"""Call Tracker app sign-in that works from outside the office (see custom/call_tracker_auth.py).

  app_login_start   password + device -> a token (office IP / already-approved device) or an
                    OTP challenge (outside the office, new device). The OTP is emailed ONLY to the
                    approver(s) in Call Tracker Settings; the rep gets it from them.
  app_login_verify  challenge id + OTP -> the token.
  app_logout        disables the caller's own token (the rep logged out on purpose).

None of these creates a desk session or a cookie and none runs the IP check of the desk login,
so ERP desk access from outside the office is exactly as before. Answers carry a `status` word and
an HTTP code the app can act on: 200 ok / otp_required, 401 bad password or code, 403 not allowed,
410 code expired, 422 bad input, 429 too many tries, 503 switched off or no approver / mail down.
"""

import json

import frappe
from frappe import _
from frappe.rate_limiter import rate_limit
from frappe.utils import add_to_date, cint, get_datetime, now_datetime

from splinh.custom.call_tracker_auth import (
	OTP_REQUEST_WINDOW_SECONDS,
	OTP_REQUESTS_PER_WINDOW,
	SOURCE_HEADER,
	TOKEN_DOCTYPE,
	active_tokens,
	challenge_state,
	clean_device_id,
	clean_text,
	current_fingerprint,
	decode_state,
	encode_state,
	evaluate_otp,
	get_settings,
	hash_otp,
	ip_in_list,
	issue_token,
	log_event,
	mask_email,
	merge_recipients,
	new_otp,
	parse_emails,
	plan_device,
	request_api_key,
	revoke_token,
	token_credentials,
	user_gate,
	user_restricted_ips,
)

CHALLENGE_PREFIX = "ct_otp:"
REQUEST_COUNT_PREFIX = "ct_otp_req:"


def _respond(http_status, status, message=None, **extra):
	frappe.local.response["http_status_code"] = http_status
	body = {"status": status, **extra}
	if message:
		body["message"] = message
	return body


def _key():
	return frappe.conf.get("encryption_key") or ""


def _credentials(user, name, api_key, api_secret, device_id, source, settings):
	return {
		"status": "ok",
		"user": user,
		"full_name": frappe.db.get_value("User", user, "full_name") or user,
		"device_id": device_id,
		"api_key": api_key,
		"api_secret": api_secret,
		"signed_in_via": source,
		"unused_token_expires_after_days": cint(settings.token_inactivity_days),
	}


def _sign_in_device(user, device_id, device_model, app_version, source, settings, ip):
	"""Reuse this device's token, or issue one (disabling the oldest if over the device limit)."""
	tokens = active_tokens(user)
	action, name, to_revoke = plan_device(tokens, device_id, settings.max_devices_per_user)
	if action == "reuse":
		token = next(t for t in tokens if t["name"] == name)
		if token.password_fingerprint and token.password_fingerprint != current_fingerprint(user):
			revoke_token(name, "password changed", user, ip)  # a stale token: sign in anew below
			tokens = active_tokens(user)
			action, name, to_revoke = plan_device(tokens, device_id, settings.max_devices_per_user)
		else:
			api_key, api_secret = token_credentials(name)
			if api_key and api_secret:
				log_event(user, "token_reused", device_id, ip, f"via {source}")
				return _credentials(user, name, api_key, api_secret, device_id, "existing", settings)
			revoke_token(name, "credential unreadable", user, ip)
			tokens = active_tokens(user)
			action, name, to_revoke = plan_device(tokens, device_id, settings.max_devices_per_user)
	for old in to_revoke:
		revoke_token(old, "replaced by a newer device", user, ip)
	name, api_key, api_secret = issue_token(user, device_id, device_model, app_version, source, ip)
	return _credentials(user, name, api_key, api_secret, device_id, source, settings)


def _approver_emails(settings):
	"""Who receives the OTP: the Administrator account's own email, whatever it currently is (nothing
	is hardcoded), plus any extra addresses / role holders configured in Call Tracker Settings."""
	admin_email = frappe.db.get_value("User", "Administrator", "email")
	role_emails = []
	if settings.approver_role:
		for row in frappe.get_all(
			"Has Role",
			filters={"role": settings.approver_role, "parenttype": "User"},
			fields=["parent"],
		):
			enabled, email = frappe.db.get_value("User", row.parent, ["enabled", "email"]) or (0, None)
			if enabled and email:
				role_emails.append(email)
	return merge_recipients(admin_email, settings.otp_approver_emails, role_emails)


def _send_otp_mail(recipients, user, device_id, device_model, app_version, ip, otp, minutes):
	full_name = frappe.db.get_value("User", user, "full_name") or user
	employee = frappe.db.get_value("Employee", {"user_id": user}, "name") or "-"
	message = _(
		"<p>A Call Tracker app sign-in from <b>outside the office</b> needs approval.</p>"
		"<p><b>Code: {0}</b> (valid for {1} minutes)</p>"
		"<table>"
		"<tr><td>Person</td><td>{2} ({3})</td></tr>"
		"<tr><td>Employee</td><td>{4}</td></tr>"
		"<tr><td>Device</td><td>{5} ({6})</td></tr>"
		"<tr><td>App version</td><td>{7}</td></tr>"
		"<tr><td>From IP</td><td>{8}</td></tr>"
		"<tr><td>Time</td><td>{9}</td></tr>"
		"</table>"
		"<p>Give the code to the rep only if you expect this sign-in. Otherwise ignore this email.</p>"
	).format(
		otp,
		minutes,
		frappe.utils.escape_html(full_name),
		user,
		employee,
		frappe.utils.escape_html(device_model or "unknown"),
		frappe.utils.escape_html(device_id),
		frappe.utils.escape_html(app_version or "-"),
		frappe.utils.escape_html(ip or "-"),
		now_datetime().strftime("%d-%m-%Y %H:%M:%S"),
	)
	frappe.sendmail(
		recipients=recipients,
		subject=_("Call Tracker sign-in code for {0}").format(full_name),
		message=message,
		now=True,
	)


@frappe.whitelist(allow_guest=True, methods=["POST"])
@rate_limit(limit=30, seconds=600)
def app_login_start(usr=None, pwd=None, device_id=None, device_model=None, app_version=None):
	"""Step 1. Password + device. Returns the token, or {"status": "otp_required", "challenge_id"}."""
	settings = get_settings()
	ip = frappe.local.request_ip
	if not cint(settings.enabled):
		return _respond(503, "disabled", _("Remote sign-in is not enabled."))

	device = clean_device_id(device_id)
	if not device:
		return _respond(422, "invalid_device", _("A valid device_id is required."))

	login_manager = frappe.local.login_manager
	try:
		login_manager.authenticate(usr, pwd)  # lockout trackers + Activity Log, no session, no IP check
	except frappe.AuthenticationError:
		log_event(usr, "password_failed", device, ip)
		raise
	user = login_manager.user

	ok, reason = user_gate(user, settings)
	if not ok:
		log_event(user, "blocked", device, ip, reason)
		return _respond(403, "not_allowed", _("This account is not allowed to use the Call Tracker app."))

	# Already approved device, or sign-in from an allowed (office) IP: no OTP.
	existing = [t for t in active_tokens(user) if t["device_id"] == device]
	inside = ip_in_list(ip, user_restricted_ips(user))
	if existing or inside:
		return _sign_in_device(user, device, device_model, app_version, "office" if inside else "otp", settings, ip)

	# Outside the office with a new device: an OTP goes to the approver(s) only.
	recipients = _approver_emails(settings)
	if not recipients:
		log_event(user, "blocked", device, ip, "no approver configured")
		return _respond(503, "no_approver", _("No approver is configured. Ask the administrator."))

	count_key = REQUEST_COUNT_PREFIX + user
	count = cint(frappe.cache.get_value(count_key))
	if count >= OTP_REQUESTS_PER_WINDOW:
		log_event(user, "blocked", device, ip, "too many OTP requests")
		return _respond(429, "too_many_requests", _("Too many code requests. Try again in a few minutes."))
	frappe.cache.set_value(count_key, count + 1, expires_in_sec=OTP_REQUEST_WINDOW_SECONDS)

	minutes = max(1, cint(settings.otp_valid_minutes) or 10)
	challenge_id = frappe.generate_hash(length=24)
	otp = new_otp()
	state = challenge_state(user, device, clean_text(device_model, 80), clean_text(app_version, 40),
		hash_otp(otp, challenge_id, _key()), minutes * 60, ip)
	try:
		_send_otp_mail(recipients, user, device, device_model, app_version, ip, otp, minutes)
	except Exception:
		frappe.log_error(title="call_tracker_auth: OTP email failed", message=frappe.get_traceback())
		log_event(user, "blocked", device, ip, "OTP email could not be sent")
		return _respond(503, "mail_failed", _("The approval code could not be sent. Try again later."))
	frappe.cache.set_value(CHALLENGE_PREFIX + challenge_id, encode_state(state), expires_in_sec=minutes * 60 + 60)
	log_event(user, "otp_requested", device, ip, f"sent to {len(recipients)} approver(s)")
	return _respond(
		200,
		"otp_required",
		_("Ask the administrator for the code and enter it here."),
		challenge_id=challenge_id,
		expires_in=minutes * 60,
		sent_to_approvers=len(recipients),
	)


@frappe.whitelist(allow_guest=True, methods=["POST"])
@rate_limit(limit=40, seconds=600)
def app_login_verify(challenge_id=None, otp=None, device_id=None):
	"""Step 2. The code the approver read out. Returns the token."""
	settings = get_settings()
	ip = frappe.local.request_ip
	if not cint(settings.enabled):
		return _respond(503, "disabled", _("Remote sign-in is not enabled."))

	device = clean_device_id(device_id)
	challenge_id = clean_text(challenge_id, 64)
	if not device or not challenge_id:
		return _respond(422, "invalid_request", _("challenge_id and device_id are required."))

	cache_key = CHALLENGE_PREFIX + challenge_id
	state = decode_state(frappe.cache.get_value(cache_key))
	if state and state.get("device_id") != device:
		return _respond(403, "device_mismatch", _("This code was requested for another device."))

	max_attempts = max(1, cint(settings.max_otp_attempts) or 5)
	result, new_state = evaluate_otp(state, otp, challenge_id, now_datetime(), max_attempts, _key())
	user = (state or {}).get("user")

	if result == "ok":
		frappe.cache.delete_value(cache_key)
		ok, reason = user_gate(user, settings)
		if not ok:
			log_event(user, "blocked", device, ip, reason)
			return _respond(403, "not_allowed", _("This account is not allowed to use the Call Tracker app."))
		log_event(user, "otp_ok", device, ip)
		return _sign_in_device(user, device, state.get("device_model"), state.get("app_version"), "otp", settings, ip)

	if result == "wrong":
		remaining = int((get_datetime(new_state["expires_at"]) - now_datetime()).total_seconds())
		frappe.cache.set_value(cache_key, encode_state(new_state), expires_in_sec=max(remaining, 1) + 60)
		log_event(user, "otp_failed", device, ip, f"attempt {new_state['attempts']} of {max_attempts}")
		return _respond(401, "otp_invalid", _("That code is not correct."), attempts_left=max_attempts - new_state["attempts"])
	if result == "locked":
		frappe.cache.delete_value(cache_key)
		log_event(user, "otp_failed", device, ip, "locked after too many wrong codes")
		return _respond(429, "otp_locked", _("Too many wrong codes. Sign in again to get a new code."))
	# expired / unknown
	frappe.cache.delete_value(cache_key)
	if user:
		log_event(user, "otp_expired", device, ip)
	return _respond(410, "otp_expired", _("That code has expired. Sign in again to get a new code."))


@frappe.whitelist(methods=["POST"])
def app_logout():
	"""The rep logged out on purpose: disable the token this request was made with."""
	if frappe.get_request_header(SOURCE_HEADER) != TOKEN_DOCTYPE:
		return _respond(403, "not_allowed", _("Use the Call Tracker token."))
	key = request_api_key()
	name = frappe.db.get_value(TOKEN_DOCTYPE, {"api_key": key, "enabled": 1}, "name") if key else None
	if not name:
		return _respond(404, "not_found", _("No active token."))
	user = frappe.session.user
	revoke_token(name, "logged out by the user", user, frappe.local.request_ip)
	log_event(user, "logout", None, frappe.local.request_ip)
	return {"status": "ok"}
