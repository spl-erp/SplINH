"""Call Tracker app heartbeat ("check-in").

The Android app sends one small, optional request while a user is signed in, shortly
after the call push and never inside it. The latest values are stored on the caller's
own User record, in the "Splashjet INH" tab (custom fields from
custom/splinh_setup.py:_ensure_user_inh_fields). A phone that stops checking in is the
signal: filter the User list on Last Seen.

Deliberately small and safe:
  - Written with frappe.db.set_value and update_modified=False, never doc.save(): User has
    track_changes on and many save hooks (SFA licence check and revoke, sigzenerp
    validate_user, HRMS, ERPNext); a save per heartbeat would run all of them and write a
    Version row each time. set_value runs none of them and still clears the cached User.
  - The user is always the authenticated one; any user field in the payload is ignored.
  - Every field is optional and cleaned (type, length, range); bad values are skipped, they
    never make the request fail, so the app's fire-and-forget call always gets an answer.
  - No call content and no phone numbers are expected (the app must not put any in
    `rec_last_error`); nothing is logged.
  - Does not touch api_secret, sessions, roles or restrict_ip.
"""

import datetime
import re

import frappe
from frappe.utils import get_datetime, now_datetime

from splinh.custom.call_recording_pause import app_config, safe_app_config

FIELD_PREFIX = "custom_inh_"

# payload key -> maximum length (single-line text)
TEXT_FIELDS = {
	"device_id": 40,
	"device_model": 80,
	"android_version": 20,
	"app_version": 40,
	"rec_last_error": 500,
}
FLAG_FIELDS = ("perm_call_log", "perm_audio", "perm_notifications", "battery_exempt")
COUNT_FIELDS = ("rec_pending", "rec_failed")
TIME_FIELDS = ("last_sync_at",)  # stored in custom_inh_last_sync

MAX_COUNT = 1_000_000
IP_MAX = 45
_CONTROL_CHARS = re.compile(r"[\x00-\x1f\x7f]+")
_TRUE = {"1", "true", "yes", "y", "on"}
_FALSE = {"0", "false", "no", "n", "off"}
_EARLIEST_YEAR = 2020


def clean_text(value, limit):
	"""Single-line trimmed text, or None when there is nothing usable."""
	if value is None or isinstance(value, (dict, list, tuple, set)):
		return None
	text = _CONTROL_CHARS.sub(" ", str(value)).strip()
	return text[:limit] or None


def clean_flag(value):
	"""0 / 1 from the usual spellings, or None."""
	if isinstance(value, bool):
		return int(value)
	if isinstance(value, (int, float)):
		return 1 if value else 0
	if isinstance(value, str):
		lowered = value.strip().lower()
		if lowered in _TRUE:
			return 1
		if lowered in _FALSE:
			return 0
	return None


def clean_count(value):
	"""Whole number 0..MAX_COUNT, or None."""
	if isinstance(value, bool) or value is None:
		return None
	try:
		number = int(float(str(value).strip()))
	except (TypeError, ValueError, OverflowError):
		return None
	if number < 0:
		return None
	return min(number, MAX_COUNT)


def clean_time(value, now=None):
	"""Server-local naive datetime from epoch seconds/milliseconds or an ISO string.

	None when it cannot be read, is before 2020 or is more than a day in the future."""
	if value is None or isinstance(value, (bool, dict, list, tuple, set)):
		return None
	now = now or now_datetime()
	parsed = None
	try:
		text = str(value).strip()
		if not text:
			return None
		if re.fullmatch(r"\d+(\.\d+)?", text):
			number = float(text)
			seconds = number / 1000 if number > 1e11 else number  # milliseconds vs seconds
			parsed = datetime.datetime.fromtimestamp(seconds)
		else:
			parsed = get_datetime(text)
			if parsed.tzinfo is not None:
				parsed = parsed.astimezone().replace(tzinfo=None)
	except (ValueError, OverflowError, OSError, TypeError):
		return None
	if parsed.year < _EARLIEST_YEAR or parsed > now + datetime.timedelta(days=1):
		return None
	return parsed


def clean_payload(raw, now=None):
	"""{custom_inh_<name>: value} for every usable field in `raw` (a dict). Skips the rest."""
	if not hasattr(raw, "get"):
		return {}
	values = {}
	for key, limit in TEXT_FIELDS.items():
		text = clean_text(raw.get(key), limit)
		if text is not None:
			values[FIELD_PREFIX + key] = text
	for key in FLAG_FIELDS:
		flag = clean_flag(raw.get(key))
		if flag is not None:
			values[FIELD_PREFIX + key] = flag
	for key in COUNT_FIELDS:
		count = clean_count(raw.get(key))
		if count is not None:
			values[FIELD_PREFIX + key] = count
	synced = clean_time(raw.get("last_sync_at"), now=now)
	if synced is not None:
		values[FIELD_PREFIX + "last_sync"] = synced
	return values


@frappe.whitelist(methods=["POST"])
def heartbeat():
	"""Record the calling user's latest app check-in. All request fields are optional.

	Request (JSON or form): device_id, device_model, android_version, app_version,
	last_sync_at, perm_call_log, perm_audio, perm_notifications, battery_exempt,
	rec_pending, rec_failed, rec_last_error.
	Response: {"ok": true, "server_time": "<ISO time>"}.
	"""
	user = frappe.session.user
	if not user or user == "Guest":
		frappe.throw("Not permitted", frappe.PermissionError)

	now = now_datetime()
	values = clean_payload(frappe.form_dict, now=now)
	values[FIELD_PREFIX + "last_seen"] = now
	ip = clean_text(getattr(frappe.local, "request_ip", None), IP_MAX)
	if ip:
		values[FIELD_PREFIX + "last_ip"] = ip

	frappe.db.set_value("User", user, values, update_modified=False)
	# The recording switch and recent pauses ride along (see get_my_app_config); extra keys only.
	return {"ok": True, "server_time": now.isoformat(timespec="seconds"), **safe_app_config(user)}


@frappe.whitelist(methods=["GET"])
def get_my_app_config():
	"""What the app needs to know about recording for the calling user.

	{"record_calls": bool, "paused_periods": [{"from", "to" | null}], "server_time"}. OFF means
	paused until it is turned ON again: the app must not upload the recording of a call that
	started inside a paused period (even after the resume); recordings of calls made before a
	pause may still finish uploading. The server also refuses such uploads."""
	user = frappe.session.user
	if not user or user == "Guest":
		frappe.throw("Not permitted", frappe.PermissionError)
	return app_config(user)
