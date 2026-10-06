"""The "Transcribe & Summarize" button on Call Log.

One whitelisted method. It validates, claims the call, queues the real work
(custom/call_ai.py:run_call_ai) and returns at once - the Gemini calls take
30-60+ seconds and must never run inside a web request.

Every click costs a little money, so the guards are server side, not just the
hidden button: only Call Tracker Manager / System Manager, a recording must
exist, the call must be at least 10 seconds (enough to have speech), one run per
call at a time, and a daily cap per user. There is no maximum call length.
"""

import frappe
from frappe import _
from frappe.utils import cint, today

from splinh.custom import call_ai

ALLOWED_ROLES = {"Call Tracker Manager", "System Manager"}
RUNNING_TTL_SECONDS = 20 * 60  # a run that never reported back is considered dead after this
DEFAULT_MIN_SECONDS = 10
DEFAULT_DAILY_LIMIT = 100


@frappe.whitelist(methods=["POST"])
def transcribe_and_summarize(call_log_name):
	if not ALLOWED_ROLES & set(frappe.get_roles()):
		frappe.throw(_("Only Call Tracker Managers can run AI analysis on a call."), frappe.PermissionError)
	if not frappe.db.exists("Call Log", call_log_name):
		frappe.throw(_("Call Log {0} not found").format(call_log_name), frappe.DoesNotExistError)
	if not frappe.has_permission("Call Log", ptype="read", doc=call_log_name):
		raise frappe.PermissionError

	if not call_ai.conf("splinh_ai_enabled", True):
		frappe.throw(_("AI call analysis is switched off in SplINH Settings."))
	try:
		call_ai.api_key()
	except call_ai.AIError as e:
		frappe.throw(_(str(e)))
	if not call_ai.latest_audio_file(call_log_name):
		frappe.throw(_("This call has no audio recording attached."))

	duration = cint(frappe.db.get_value("Call Log", call_log_name, "duration"))
	minimum = cint(call_ai.conf("splinh_ai_min_seconds", DEFAULT_MIN_SECONDS))
	if duration < minimum:
		frappe.throw(_("This call is only {0} seconds long - too short to analyse.").format(duration))
	# No maximum length on purpose. Google's speech model takes up to 1 hour per request,
	# so a longer call simply comes back as Failed with Google's message. An optional
	# splinh_ai_max_seconds in site_config can still cap it if cost ever needs a ceiling.
	maximum = cint(call_ai.conf("splinh_ai_max_seconds", 0))
	if maximum and duration > maximum:
		frappe.throw(_("This call is {0} minutes long; the limit is {1} minutes.").format(duration // 60, maximum // 60))

	running_key = f"splinh_ai_running::{call_log_name}"
	if frappe.cache.get_value(running_key):
		frappe.throw(_("This call is already being analysed. Please wait for it to finish."))

	daily_key = f"splinh_ai_daily::{frappe.session.user}::{today()}"
	used = cint(frappe.cache.get_value(daily_key))
	limit = cint(call_ai.conf("splinh_ai_daily_limit", DEFAULT_DAILY_LIMIT))
	if used >= limit:
		frappe.throw(_("Daily limit of {0} AI analyses reached.").format(limit))

	frappe.cache.set_value(running_key, 1, expires_in_sec=RUNNING_TTL_SECONDS)
	frappe.cache.set_value(daily_key, used + 1, expires_in_sec=24 * 3600)
	frappe.db.set_value(
		"Call Log",
		call_log_name,
		{"custom_transcript_status": "Processing", "custom_transcript_error": ""},
		update_modified=False,
	)

	try:
		frappe.enqueue(
			"splinh.custom.call_ai.run_call_ai",
			queue="default",
			timeout=900,
			enqueue_after_commit=True,
			call_log_name=call_log_name,
			user=frappe.session.user,
		)
	except Exception:
		# The queue refused the job (e.g. too many queued jobs): do not leave the call
		# showing "Processing" for 20 minutes with nothing running.
		frappe.cache.delete_value(running_key)
		frappe.cache.set_value(daily_key, used, expires_in_sec=24 * 3600)
		frappe.db.set_value("Call Log", call_log_name, "custom_transcript_status", "", update_modified=False)
		raise
	return {"status": "Processing"}


@frappe.whitelist(methods=["POST"])
def test_gemini_key(api_key=None):
	"""The "Test connection" button on SplINH Settings (System Manager only).

	Tries a key against Google's free model-list call - never a paid model - and
	reports whether it was accepted. A key typed on the form is tested as typed; an
	empty box or the masked placeholder of an already-saved key tests the stored one.
	The key is never returned or logged.
	"""
	frappe.only_for("System Manager")
	key = (api_key or "").strip()
	if not key or set(key) == {"*"}:
		try:
			key = call_ai.api_key()
		except call_ai.AIError as e:
			return {"ok": False, "message": str(e)}
	ok, message = call_ai.check_key(key)
	return {"ok": ok, "message": message}
