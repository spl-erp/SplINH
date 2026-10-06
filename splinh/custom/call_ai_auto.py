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
                yet, long enough to contain speech; claim each atomically; run the
                same pipeline as the button (call_ai.run_call_ai)
Safety stops: daily cap, time budget (scheduler jobs time out at 300 s), a circuit
breaker after repeated failures (bad key, Google outage), and a lock so two runs can
never overlap. A failed call is left Failed for a manager to retry with the button -
never retried automatically, so a persistent problem cannot burn money in a loop.
"""

import time

import frappe
from frappe.utils import cint, now_datetime, today

from splinh.custom import call_ai

SINCE_KEY = "splinh_ai_auto_since"  # DefaultValue: only recordings created after this are considered
LOCK_KEY = "splinh_ai_auto_lock"
RUNNING_KEY = "splinh_ai_running::{}"  # shared with the button (api/call_ai.py)
DAILY_KEY = "splinh_ai_auto_daily::{}"

LOCK_SECONDS = 280  # a run is killed at 300 s, so a crashed run frees its lock before the next real one
RUNNING_SECONDS = 20 * 60
TIME_BUDGET_SECONDS = 200
BATCH_SIZE = 10
MAX_CONSECUTIVE_FAILURES = 3
DEFAULT_DAILY_LIMIT = 200
DEFAULT_MIN_SECONDS = 10
AUDIO_EXTENSIONS = r"\.(m4a|mp3|wav|aac|ogg|opus|flac|mp4)$"


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


def _candidates(since, min_seconds, limit):
	"""Calls with an audio recording attached after `since`, no status yet, and long
	enough to contain a conversation. Oldest first. SELECT only."""
	rows = frappe.db.sql(
		"""
		SELECT c.name
		FROM `tabCall Log` c
		JOIN `tabFile` f ON f.attached_to_doctype = 'Call Log' AND f.attached_to_name = c.name
		WHERE f.creation >= %(since)s
		  AND c.duration >= %(min_seconds)s
		  AND (c.custom_transcript_status IS NULL OR c.custom_transcript_status IN ('', 'Pending'))
		  AND f.file_name REGEXP %(extensions)s
		GROUP BY c.name
		ORDER BY MIN(f.creation)
		LIMIT %(limit)s
		""",
		{"since": since, "min_seconds": min_seconds, "extensions": AUDIO_EXTENSIONS, "limit": int(limit)},
	)
	return [r[0] for r in rows]


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


def process_pending(dry_run=False):
	"""One scheduled run. Returns a small report (also what `dry_run` shows: the calls
	it WOULD process, nothing written)."""
	report = {"auto_enabled": auto_enabled(), "reason": None, "would_process": [], "processed": [], "failed": [], "recovered": []}

	if not dry_run:
		report["recovered"] = recover_interrupted()

	if not report["auto_enabled"]:
		if not dry_run:
			_set_marker(now_datetime())  # keep the start marker at "now" while it is off
		report["reason"] = "Auto-transcribe is switched off."
		return report
	if not call_ai.conf("splinh_ai_enabled", True):
		report["reason"] = "AI call analysis is switched off in SplINH AI Settings."
		return report
	try:
		call_ai.api_key()
	except call_ai.AIError as e:
		report["reason"] = str(e)
		return report

	since = _get_marker()
	if not since:
		if not dry_run:
			_set_marker(now_datetime())
		report["reason"] = "First run: starting from now, older recordings are left alone."
		return report

	min_seconds = cint(call_ai.conf("splinh_ai_min_seconds", DEFAULT_MIN_SECONDS))
	if dry_run:
		report["would_process"] = _candidates(since, min_seconds, BATCH_SIZE)
		report["since"] = str(since)
		return report

	if frappe.cache.get_value(LOCK_KEY):
		report["reason"] = "Another run is still in progress."
		return report

	daily_key = DAILY_KEY.format(today())
	used = cint(frappe.cache.get_value(daily_key))
	limit = daily_limit()
	if used >= limit:
		report["reason"] = f"Daily limit of {limit} automatic analyses reached."
		return report

	frappe.cache.set_value(LOCK_KEY, 1, expires_in_sec=LOCK_SECONDS)
	started = time.time()
	failures_in_a_row = 0
	try:
		for name in _candidates(since, min_seconds, BATCH_SIZE):
			if time.time() - started > TIME_BUDGET_SECONDS:
				report["reason"] = "Time budget reached; the rest waits for the next run."
				break
			if used >= limit:
				report["reason"] = f"Daily limit of {limit} automatic analyses reached."
				break
			if failures_in_a_row >= MAX_CONSECUTIVE_FAILURES:
				report["reason"] = f"Stopped after {MAX_CONSECUTIVE_FAILURES} failures in a row (check the key and Google's status)."
				break

			frappe.cache.set_value(RUNNING_KEY.format(name), 1, expires_in_sec=RUNNING_SECONDS)
			if not _claim(name):
				frappe.cache.delete_value(RUNNING_KEY.format(name))
				continue  # someone else (a manager's click) took it

			used += 1
			frappe.cache.set_value(daily_key, used, expires_in_sec=26 * 3600)
			call_ai.run_call_ai(name, "Administrator")  # always ends Completed or Failed
			if _status(name) == "Failed":
				report["failed"].append(name)
				failures_in_a_row += 1
			else:
				report["processed"].append(name)
				failures_in_a_row = 0
	finally:
		frappe.cache.delete_value(LOCK_KEY)
	return report
