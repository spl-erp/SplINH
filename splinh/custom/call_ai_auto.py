"""Auto-transcribe new call recordings.

Driven by a Scheduler Event Server Script created in the ERPNext UI ("SplINH Auto
Transcribe", Cron `*/2 * * * *`) whose whole body is
    frappe.call("splinh.api.auto_ai.process_pending_recordings")
so it can be switched on, off or edited without a migrate or a restart. This module
is imported fresh by each scheduler job and nothing on the call-sync or recording
upload path touches it.

Why a poll and not a trigger on the upload: the upload is the phone's request, and a
Server Script's frappe.enqueue is broken on this Frappe version (AttributeError:
request inside the job). A poll never risks an upload, also catches anything a
trigger would miss, and is at most one interval behind.

One run (all gated on "Auto-transcribe new recordings" in SplINH AI Settings):
  - off:        do nothing except keep the start marker at "now", so switching it on
                later starts from that moment and never reprocesses old recordings
  - on:         pick Call Logs with an audio File newer than the marker, no status
                yet, and worth analysing (see _candidates: reviewed, not "No 2-Way
                Communication", longer than 30 s, not an Internal Call); claim each
                atomically; run the same pipeline as the button (call_ai.run_call_ai)
Safety stops: daily cap, time budget (scheduler jobs time out at 300 s), a circuit
breaker after repeated failures (bad key, Google outage), and a lock so two runs can
never overlap. A failed call is left Failed for a manager to retry with the button -
never retried automatically, so a persistent problem cannot burn money in a loop.
"""

import json
import time

import frappe
from frappe.utils import add_to_date, cint, get_datetime, get_time, now_datetime, time_diff_in_seconds, today

from splinh.custom import call_ai, call_ai_quota

SINCE_KEY = "splinh_ai_auto_since"  # DefaultValue: only recordings created after this are considered
LOCK_KEY = "splinh_ai_auto_lock"
RUNNING_KEY = "splinh_ai_running::{}"  # shared with the button (api/call_ai.py)
DAILY_KEY = "splinh_ai_auto_daily::{}"
BULK_KEY = "splinh_ai_bulk_job"  # DefaultValue: JSON of the bulk run started from the settings screen

LOCK_SECONDS = 280  # a run is killed at 300 s, so a crashed run frees its lock before the next real one
RUNNING_SECONDS = 20 * 60
TIME_BUDGET_SECONDS = 180  # a slot may wait up to a minute for the per-minute limit, and a run is killed at 300 s
DEFAULT_MAX_AGE_DAYS = 2  # the live job ignores calls recorded longer ago than this (newest are served first)
BATCH_SIZE = 10
MAX_CONSECUTIVE_FAILURES = 3
DEFAULT_DAILY_LIMIT = 200
DEFAULT_MIN_SECONDS = 10
AUTO_MIN_SECONDS = 30  # automatic analysis only for calls LONGER than this (the button keeps the settings minimum)
EXCLUDED_DROP_STAGES = ("0. No 2-Way Communication", "No 2-Way Connection")  # the 2nd is a legacy stray value
EXCLUDED_CATEGORY = "Internal Call"
AUDIO_EXTENSIONS = r"\.(m4a|mp3|wav|aac|ogg|opus|flac|mp4)$"
BULK_BATCH_SIZE = 25  # bigger than the live batch: bulk is bounded by the time budget instead
BULK_DEFAULT_MAX = 500
BULK_ABSOLUTE_MAX = 5000
INTERVAL_SLACK_SECONDS = 45  # the scheduler fires a few seconds early or late
COST_PER_AUDIO_HOUR = 0.33  # USD, measured on the live prototype (two-step: transcribe + summary)


# ------------------------------------------------------------------- settings


def auto_enabled():
	"""The "Auto-transcribe new recordings" box. A field the screen does not have (yet)
	reads as unset and falls back to site_config, then to off."""
	settings = call_ai._load_settings()
	value = settings.get("auto_transcribe") if settings is not None else None
	if value is None:
		return bool(call_ai._site_conf("splinh_ai_auto"))
	return bool(cint(value))


def daily_limit():
	"""Cap on automatic analyses per day. Accepts the field under either spelling:
	`auto_daily_limit` (as documented) or `daily_auto_limit` (what Frappe generates from
	the label "Daily Auto Limit" when the field is built in the UI)."""
	settings = call_ai._load_settings()
	value = 0
	if settings is not None:
		value = cint(settings.get("auto_daily_limit")) or cint(settings.get("daily_auto_limit"))
	return value or cint(call_ai._site_conf("splinh_ai_auto_daily_limit")) or DEFAULT_DAILY_LIMIT


# --------------------------------------------------------------- database steps


def _get_marker():
	return frappe.db.get_default(SINCE_KEY)


def _set_marker(value):
	frappe.db.set_default(SINCE_KEY, str(value))
	frappe.db.commit()


_ELIGIBLE = """
	FROM `tabCall Log` c
	JOIN `tabFile` f ON f.attached_to_doctype = 'Call Log' AND f.attached_to_name = c.name
	WHERE f.creation >= %(since)s
	  AND (%(until)s IS NULL OR f.creation < %(until)s)
	  AND c.duration > %(min_seconds)s
	  AND IFNULL(c.custom_call_drop_stage, '') != ''
	  AND c.custom_call_drop_stage NOT IN %(excluded_stages)s
	  AND IFNULL(c.custom_call_category, '') != %(excluded_category)s
	  AND (c.custom_transcript_status IS NULL OR c.custom_transcript_status IN ('', 'Pending'))
	  AND f.file_name REGEXP %(extensions)s
"""


def _eligible_params(since, until, min_seconds):
	return {
		"since": since,
		"until": until,
		"min_seconds": min_seconds,
		"excluded_stages": EXCLUDED_DROP_STAGES,
		"excluded_category": EXCLUDED_CATEGORY,
		"extensions": AUDIO_EXTENSIONS,
	}


def _candidates(since, min_seconds, limit, until=None):
	"""Calls with an audio recording attached from `since` (before `until`, if given), no
	status yet, longer than `min_seconds`, REVIEWED (Call Drop Stage filled in by the
	caller, so a blank one waits) with a stage that is not "No 2-Way Communication", and
	not an Internal Call. NEWEST first: the API quota is smaller than the supply, so the
	freshest calls get the slots. SELECT only."""
	params = _eligible_params(since, until, min_seconds)
	params["limit"] = int(limit)
	rows = frappe.db.sql(
		"SELECT c.name " + _ELIGIBLE + " GROUP BY c.name ORDER BY MAX(f.creation) DESC LIMIT %(limit)s", params
	)
	return [r[0] for r in rows]


def _count_eligible(since, until, min_seconds=AUTO_MIN_SECONDS):
	"""(number of calls, total seconds of audio) the same rules would pick. SELECT only."""
	rows = frappe.db.sql(
		"SELECT COUNT(*), IFNULL(SUM(d), 0) FROM (SELECT c.name, MAX(c.duration) AS d "
		+ _ELIGIBLE
		+ " GROUP BY c.name) t",
		_eligible_params(since, until, min_seconds),
	)
	return cint(rows[0][0]), float(rows[0][1] or 0)


def _claim(name):
	"""Atomically move a call from "no status" to Processing. True only for the one
	caller that wins, so the same call is never processed twice."""
	frappe.db.sql(
		"""
		UPDATE `tabCall Log`
		SET custom_transcript_status = 'Processing', custom_transcript_error = ''
		WHERE name = %s AND (custom_transcript_status IS NULL OR custom_transcript_status IN ('', 'Pending'))
		""",
		(name,),
	)
	won = frappe.db.sql("SELECT ROW_COUNT()")[0][0] == 1
	frappe.db.commit()
	return won


def _processing_calls(limit=50):
	return [
		r[0]
		for r in frappe.db.sql(
			"SELECT name FROM `tabCall Log` WHERE custom_transcript_status = 'Processing' LIMIT %s", (int(limit),)
		)
	]


def _mark_interrupted(name):
	"""Processing -> Failed, only if it is STILL Processing (a run that finished a
	moment ago must not be overwritten)."""
	frappe.db.sql(
		"""
		UPDATE `tabCall Log`
		SET custom_transcript_status = 'Failed',
			custom_transcript_error = 'Interrupted: no result came back. Use AI > Retry.'
		WHERE name = %s AND custom_transcript_status = 'Processing'
		""",
		(name,),
	)
	frappe.db.commit()


def _status(name):
	return frappe.db.get_value("Call Log", name, "custom_transcript_status")


# ------------------------------------------------------------------- the run


def recover_interrupted():
	"""A call still "Processing" with no running marker means its job died (worker
	killed, timeout, restart). Without this the form would show Processing forever and
	hide the button. The marker is set by both the button and this module and
	expires after RUNNING_SECONDS."""
	fixed = []
	for name in _processing_calls():
		if not frappe.cache.get_value(RUNNING_KEY.format(name)):
			_mark_interrupted(name)
			fixed.append(name)
	return fixed


# ------------------------------------------------- schedule and status (settings screen)


def _setting(*names):
	"""First non-blank value among these fields on the saved settings screen, else None.
	Fields built in the UI may not exist yet, so a missing one just reads as unset."""
	settings = call_ai._load_settings()
	if settings is None:
		return None
	for name in names:
		value = settings.get(name)
		if value not in (None, ""):
			return value
	return None


def _show(**values):
	"""Write the read-only status fields on the settings screen (when it has them).
	Display only: nothing reads them back except `auto_last_run`, the interval gate."""
	settings = call_ai._load_settings()
	if settings is None:
		return
	try:
		meta = frappe.get_meta(settings.doctype)
		for key, value in values.items():
			if meta.has_field(key):
				frappe.db.set_single_value(settings.doctype, key, value)
		frappe.db.commit()
	except Exception:
		frappe.db.rollback()  # a status display must never break a run


def _max_age_days():
	return cint(_setting("auto_max_age_days")) or DEFAULT_MAX_AGE_DAYS


def _in_window(now, start, end):
	"""Is the clock time in [start, end)? A window that passes midnight (22:00-06:00) works."""
	if not start or not end:
		return True
	t, start, end = get_time(now), get_time(start), get_time(end)
	if start == end:
		return True
	return start <= t < end if start < end else (t >= start or t < end)


def _window_reason(now):
	start, end = _setting("auto_window_from"), _setting("auto_window_to")
	if _in_window(now, start, end):
		return None
	return f"Outside the active hours ({str(get_time(start))[:5]}-{str(get_time(end))[:5]})."


def _interval_reason(now):
	"""The scheduler ticks every 2 minutes; the screen can ask for a longer gap."""
	minutes = cint(_setting("auto_run_every_minutes"))
	last = _setting("auto_last_run")
	if minutes <= 2 or not last:
		return None
	waited = time_diff_in_seconds(now, get_datetime(last))
	if waited < minutes * 60 - INTERVAL_SLACK_SECONDS:
		return f"Next automatic run is {minutes} minutes after the last one."
	return None


# ------------------------------------------------------------------ bulk (settings screen)


def bulk_job():
	"""The bulk run started from the screen: {"from","to","max","done","failed","state",...} or None."""
	raw = frappe.db.get_default(BULK_KEY)
	try:
		return json.loads(raw) if raw else None
	except ValueError:
		return None


def _save_bulk(job, note=""):
	frappe.db.set_default(BULK_KEY, json.dumps(job))
	frappe.db.commit()
	_show(bulk_state=job["state"], bulk_progress=_progress_text(job, note))


def _progress_text(job, note=""):
	text = f"{job['done']} done, {job['failed']} failed (limit {job['max']}). Range {job['from']} to {job['to']}."
	return f"{text} {note}".strip()


def bulk_preview(bulk_from, bulk_to):
	"""How many calls a bulk run over this range would analyse, and a rough cost. Read only."""
	start, end = get_datetime(bulk_from), get_datetime(bulk_to)
	if start >= end:
		raise call_ai.AIError("'Recordings from' must be earlier than 'Recordings to'.")
	count, seconds = _count_eligible(start, end)
	hours = seconds / 3600
	return {"count": count, "hours": round(hours, 1), "estimated_cost_usd": round(hours * COST_PER_AUDIO_HOUR, 2)}


def bulk_start(bulk_from, bulk_to, max_calls=None):
	"""Register a bulk run; the 2-minute poll does the work. Returns the preview numbers."""
	existing = bulk_job()
	if existing and existing.get("state") == "Running":
		raise call_ai.AIError("A bulk run is already running. Stop it first.")
	if not call_ai.conf("splinh_ai_enabled", True):
		raise call_ai.AIError("AI call analysis is switched off in SplINH AI Settings.")
	call_ai.api_key()  # raises with a clear message when there is no key
	preview = bulk_preview(bulk_from, bulk_to)
	if not preview["count"]:
		raise call_ai.AIError("No call in that range qualifies (reviewed, not No 2-Way, over 30 s, not Internal, not transcribed yet).")
	limit = min(cint(max_calls) or BULK_DEFAULT_MAX, BULK_ABSOLUTE_MAX)
	job = {
		"from": str(get_datetime(bulk_from)),
		"to": str(get_datetime(bulk_to)),
		"max": limit,
		"done": 0,
		"failed": 0,
		"state": "Running",
		"started": str(now_datetime()),
	}
	_save_bulk(job, f"{preview['count']} qualify, about ${preview['estimated_cost_usd']}.")
	return {**preview, "max": limit}


def bulk_stop():
	job = bulk_job()
	if not job or job.get("state") != "Running":
		raise call_ai.AIError("No bulk run is running.")
	job["state"] = "Stopped"
	_save_bulk(job, "Stopped by a manager.")
	return job


# ------------------------------------------------------------------- the run


def _run_batch(names, started, report, stop_after=None, count_daily=None):
	"""Analyse these calls one after another, honouring the shared stop rules (time budget,
	3 failures in a row, optional cap). Returns (done, failed, stop_reason or None)."""
	done = failed = in_a_row = 0
	used = cint(frappe.cache.get_value(count_daily)) if count_daily else 0
	limit = daily_limit() if count_daily else None
	for name in names:
		if time.time() - started > TIME_BUDGET_SECONDS:
			return done, failed, "Time budget reached; the rest waits for the next run."
		if stop_after is not None and done + failed >= stop_after:
			return done, failed, "Call limit reached."
		if limit is not None and used >= limit:
			return done, failed, f"Daily limit of {limit} automatic analyses reached."
		if in_a_row >= MAX_CONSECUTIVE_FAILURES:
			return done, failed, f"Stopped after {MAX_CONSECUTIVE_FAILURES} failures in a row (check the key and Google's status)."

		frappe.cache.set_value(RUNNING_KEY.format(name), 1, expires_in_sec=RUNNING_SECONDS)
		if not _claim(name):
			frappe.cache.delete_value(RUNNING_KEY.format(name))
			continue  # someone else (a manager's click) took it

		if count_daily:
			used += 1
			frappe.cache.set_value(count_daily, used, expires_in_sec=26 * 3600)
		outcome = call_ai.run_call_ai(name, "Administrator", requeue=True)  # Completed / Failed / Skipped / Throttled
		if outcome == "Throttled":
			# no API quota right now: the call is back in the queue, this is not a failure
			if count_daily:
				used -= 1
				frappe.cache.set_value(count_daily, used, expires_in_sec=26 * 3600)
			return done, failed, call_ai_quota.last_reason()
		if _status(name) == "Skipped":
			report.setdefault("skipped", []).append(name)  # cannot be analysed with this setup; not a failure
			continue
		if _status(name) == "Failed":
			failed += 1
			in_a_row += 1
			report["failed"].append(name)
		else:
			done += 1
			in_a_row = 0
			report["processed"].append(name)
	return done, failed, None


def _auto_pass(report, dry_run, started, now):
	"""The live pass: new recordings from the start marker on."""
	if not report["auto_enabled"]:
		if not dry_run:
			_set_marker(now_datetime())  # keep the start marker at "now" while it is off
		report["reason"] = "Auto-transcribe is switched off."
		return
	if not call_ai.conf("splinh_ai_enabled", True):
		report["reason"] = "AI call analysis is switched off in SplINH AI Settings."
		return
	try:
		call_ai.api_key()
	except call_ai.AIError as e:
		report["reason"] = str(e)
		return

	since = _get_marker()
	if not since:
		if not dry_run:
			_set_marker(now_datetime())
		report["reason"] = "First run: starting from now, older recordings are left alone."
		return

	min_seconds = AUTO_MIN_SECONDS
	since = max(get_datetime(since), get_datetime(add_to_date(now, days=-_max_age_days())))
	if dry_run:
		report["would_process"] = _candidates(since, min_seconds, BATCH_SIZE)
		report["since"] = str(since)
		return

	reason = _window_reason(now) or _interval_reason(now)
	if reason:
		report["reason"] = reason
		return
	if frappe.cache.get_value(LOCK_KEY):
		report["reason"] = "Another run is still in progress."
		return
	blocked = call_ai_quota.blocker()
	if blocked:
		report["reason"] = blocked  # daily quota used / pacing: nothing is sent, nothing fails
		return

	daily_key = DAILY_KEY.format(today())
	if cint(frappe.cache.get_value(daily_key)) >= daily_limit():
		report["reason"] = f"Daily limit of {daily_limit()} automatic analyses reached."
		return

	frappe.cache.set_value(LOCK_KEY, 1, expires_in_sec=LOCK_SECONDS)
	_show(auto_last_run=now)
	try:
		done, failed, reason = _run_batch(_candidates(since, min_seconds, BATCH_SIZE), started, report, count_daily=daily_key)
		report["reason"] = reason
	finally:
		frappe.cache.delete_value(LOCK_KEY)


def _bulk_pass(report, dry_run, started, now):
	"""A bulk run started from the screen: the same rules over a chosen range of recordings."""
	job = bulk_job()
	if not job or job.get("state") != "Running":
		return
	if dry_run:
		report["bulk_would_process"] = _candidates(job["from"], AUTO_MIN_SECONDS, BULK_BATCH_SIZE, until=job["to"])
		return
	if _window_reason(now) or frappe.cache.get_value(LOCK_KEY):
		return
	blocked = call_ai_quota.blocker()
	if blocked:
		report["bulk_reason"] = blocked
		return
	left = job["max"] - job["done"] - job["failed"]
	if left <= 0:
		job["state"] = "Done"
		_save_bulk(job, "Call limit reached.")
		return
	if not call_ai.conf("splinh_ai_enabled", True):
		return

	names = _candidates(job["from"], AUTO_MIN_SECONDS, min(BULK_BATCH_SIZE, left), until=job["to"])
	if not names:
		job["state"] = "Done"
		_save_bulk(job, f"Finished {now}.")
		return

	frappe.cache.set_value(LOCK_KEY, 1, expires_in_sec=LOCK_SECONDS)
	try:
		done, failed, reason = _run_batch(names, started, report, stop_after=left)
	finally:
		frappe.cache.delete_value(LOCK_KEY)
	job = bulk_job() or job  # a manager may have pressed Stop meanwhile
	job["done"] += done
	job["failed"] += failed
	if reason and reason.startswith("Stopped after"):
		job["state"] = "Stopped"
	_save_bulk(job, reason or "")


def process_pending(dry_run=False):
	"""One scheduled run. Returns a small report (also what `dry_run` shows: the calls
	it WOULD process, nothing written)."""
	started = time.time()
	now = now_datetime()
	report = {"auto_enabled": auto_enabled(), "reason": None, "would_process": [], "processed": [], "failed": [], "recovered": []}

	if not dry_run:
		report["recovered"] = recover_interrupted()

	_auto_pass(report, dry_run, started, now)
	_bulk_pass(report, dry_run, started, now)

	if not dry_run:
		summary = f"{now}: {len(report['processed'])} done, {len(report['failed'])} failed."
		_show(auto_status=f"{summary} {report['reason'] or ''}".strip(), quota_status=_quota_text(report))
	return report


def _quota_text(report):
	"""One line for the settings screen: quota used, and how many calls wait in the queue."""
	try:
		since = _get_marker()
		queue = None
		if since:
			cutoff = max(get_datetime(since), get_datetime(add_to_date(now_datetime(), days=-_max_age_days())))
			queue = _count_eligible(cutoff, None)[0]
		return call_ai_quota.status_text(queue)
	except Exception:
		return ""
