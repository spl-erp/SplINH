"""Per-user "Record Calls" toggle: pause and resume recording uploads.

The toggle is the User check `custom_inh_record_calls` (Splashjet INH tab, created by
splinh_setup._ensure_user_inh_fields). OFF means PAUSED for that period until it is turned ON
again. The decision is made on the CALL's start time against the user's pause periods, never on
the toggle's state at upload time, so a late upload from an offline phone or a backlog after the
resume behaves correctly:

  - a call that started before the pause may still upload (it was made while recording was ON);
  - a call that started inside a pause is never uploaded, not even after the resume.

Periods are rows of "Call Tracker Recording Pause", opened and closed by `track_recording_toggle`
(User on_update hook; it acts only when this one field changes). The app is told through
api/app_status.py (get_my_app_config, heartbeat) and receive_call_recording refuses a recording of
a paused call (api/call_tracking.py). The phone's own call recorder is not ours to control: only
the upload is.

Pure rules first (no database), then the few functions that touch Frappe.
"""

import frappe
from frappe.utils import add_to_date, cint, get_datetime, now_datetime

TOGGLE_FIELD = "custom_inh_record_calls"
PAUSE_DOCTYPE = "Call Tracker Recording Pause"
CONFIG_WINDOW_DAYS = 45
_TIME_FORMAT = "%Y-%m-%d %H:%M:%S"


# ------------------------------------------------------------------ pure rules


def is_recording_on(value):
	"""A missing value counts as ON, so a bad column default can never pause anyone."""
	return value is None or bool(cint(value))


def toggle_change(before, after):
	""""pause" (ON -> OFF), "resume" (OFF -> ON) or None. `before` is None for a brand-new user."""
	was_on = True if before is None else is_recording_on(before)
	now_on = is_recording_on(after)
	if was_on and not now_on:
		return "pause"
	if not was_on and now_on:
		return "resume"
	return None


def in_paused_period(periods, call_start):
	"""True when `call_start` falls in [paused_from, resumed_on) of any period; an open period
	(no resumed_on) extends to now. A call with no start time is never treated as paused."""
	if not call_start:
		return False
	start = get_datetime(call_start)
	for period in periods:
		begin = period.get("paused_from")
		end = period.get("resumed_on")
		if begin and get_datetime(begin) <= start and (not end or start < get_datetime(end)):
			return True
	return False


def periods_for_config(periods, now, days=CONFIG_WINDOW_DAYS):
	"""The periods the app needs: still open, or closed within the last `days`. Times as text."""
	cutoff = add_to_date(now, days=-days)
	out = []
	for period in periods:
		end = period.get("resumed_on")
		if end and get_datetime(end) < cutoff:
			continue
		out.append(
			{
				"from": get_datetime(period["paused_from"]).strftime(_TIME_FORMAT),
				"to": get_datetime(end).strftime(_TIME_FORMAT) if end else None,
			}
		)
	return out


# --------------------------------------------------------------- Frappe access


def user_periods(user):
	return frappe.get_all(
		PAUSE_DOCTYPE,
		filters={"user": user},
		fields=["paused_from", "resumed_on"],
		order_by="paused_from asc",
	)


def recording_paused_for(user, call_start):
	"""True when `user`'s call that started at `call_start` fell inside a pause."""
	if not user or not call_start:
		return False
	return frappe.db.sql(
		f"""SELECT 1 FROM `tab{PAUSE_DOCTYPE}`
		WHERE user = %s AND paused_from <= %s AND (resumed_on IS NULL OR resumed_on > %s)
		LIMIT 1""",
		(user, get_datetime(call_start), get_datetime(call_start)),
	) != ()


def app_config(user):
	"""What the app needs to know about recording: the current switch and the recent pauses."""
	value = frappe.db.get_value("User", user, TOGGLE_FIELD) if frappe.db.has_column("User", TOGGLE_FIELD) else None
	now = now_datetime()
	return {
		"record_calls": is_recording_on(value),
		"paused_periods": periods_for_config(user_periods(user), now),
		"server_time": now.isoformat(timespec="seconds"),
	}


def safe_app_config(user):
	"""app_config for the heartbeat answer: never lets a problem here break the heartbeat."""
	try:
		return app_config(user)
	except Exception:
		frappe.log_error(title="call_recording_pause: app_config failed", message=frappe.get_traceback())
		return {}


def track_recording_toggle(doc, method=None):
	"""User on_update hook: open a pause period when the toggle goes OFF, close it when it goes ON.

	Returns at once unless THIS field changed, and never blocks saving a user."""
	try:
		before = None if doc.is_new() else doc.get_doc_before_save()
		before_value = before.get(TOGGLE_FIELD) if before else None
		change = toggle_change(before_value, doc.get(TOGGLE_FIELD))
		if not change:
			return
		now = now_datetime()
		if change == "pause":
			if frappe.db.exists(PAUSE_DOCTYPE, {"user": doc.name, "resumed_on": ("is", "not set")}):
				return  # already paused
			frappe.get_doc(
				{
					"doctype": PAUSE_DOCTYPE,
					"user": doc.name,
					"paused_from": now,
					"paused_by": frappe.session.user,
				}
			).insert(ignore_permissions=True)
		else:
			for name in frappe.get_all(
				PAUSE_DOCTYPE, filters={"user": doc.name, "resumed_on": ("is", "not set")}, pluck="name"
			):
				frappe.db.set_value(
					PAUSE_DOCTYPE,
					name,
					{"resumed_on": now, "resumed_by": frappe.session.user},
					update_modified=False,
				)
	except Exception:
		frappe.log_error(title="call_recording_pause: could not record toggle change", message=frappe.get_traceback())
