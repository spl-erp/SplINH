"""Call Tracker device tokens: sign-in from outside the office, approved by an OTP.

Why this exists. The Call Tracker app used to depend on the user's own API key/secret
(`User.api_secret`) and on a password login. The vendor sweep (sigzensfa) deletes
`User.api_secret` of every user without the SFA flag each hour, and Frappe refuses a password
login from outside the user's `restrict_ip` list. This module gives the app its own credential,
the "Call Tracker Token" doctype:

  - issued by api/call_tracker_auth.py (password check without a desk session; from outside the
    office only after an OTP that is emailed to the approver(s), never to the rep);
  - sent by the app as `Authorization: token key:secret` plus `Frappe-Authorization-Source:
    Call Tracker Token`, which core Frappe validates against that doctype (auth.py
    validate_api_key_secret), so the sweep never sees it;
  - limited to the Call Tracker endpoints by `enforce_token_scope` (an `auth_hooks` entry), so a
    stolen token can never read or change ERP data and never opens the desk.

The desk login, `restrict_ip`, Sigzen code and the old app flow are untouched.

This file holds the pure rules (no database) so they can be unit tested offline, then the few
functions that touch Frappe.
"""

import hashlib
import hmac
import json
import re
import secrets

import frappe
from frappe.utils import add_to_date, cint, get_datetime, now_datetime

TOKEN_DOCTYPE = "Call Tracker Token"
SETTINGS_DOCTYPE = "Call Tracker Settings"
EVENT_DOCTYPE = "Call Tracker Login Event"
SOURCE_HEADER = "Frappe-Authorization-Source"
APP_FLAG_FIELD = "custom_inh_app_user"

# The only methods a Call Tracker token may call. Everything else is a 403.
_API = "/api/method/"
ALLOWED_METHODS = (
	"splinh.api.call_tracking.push_calls",
	"splinh.api.call_tracking.ingest_whatsapp_call",
	"splinh.api.call_tracking.receive_call_recording",
	"splinh.api.call_tracking.submit_call_review",
	"splinh.api.call_tracking.get_call_review_options",
	"splinh.api.call_tracking.get_my_call_reviews",
	"splinh.api.app_status.heartbeat",
	"splinh.api.app_status.get_my_app_config",
	"splinh.api.call_tracker_auth.app_logout",
)
ALLOWED_PATHS = frozenset(_API + method for method in ALLOWED_METHODS)

DEVICE_ID = re.compile(r"[A-Za-z0-9._:-]{6,64}")
EMAIL = re.compile(r"[^@\s,;<>]+@[^@\s,;<>]+\.[^@\s,;<>]+")

OTP_REQUESTS_PER_WINDOW = 3
OTP_REQUEST_WINDOW_SECONDS = 15 * 60
LAST_USED_THROTTLE_SECONDS = 10 * 60


# ------------------------------------------------------------------ pure rules


def is_request_allowed(path, cmd=None):
	"""A request made with a Call Tracker token may only call an allowlisted method.

	`cmd` must be empty: frappe.app handles the deprecated `?cmd=` form BEFORE the /api/ route,
	so an allowlisted path with `cmd=frappe.client.get_list` would otherwise run another method."""
	if cmd:
		return False
	return (path or "").rstrip("/") in ALLOWED_PATHS


def clean_device_id(value):
	"""The app's device id (Android ID), or None when unusable."""
	if not isinstance(value, str):
		return None
	text = value.strip()
	return text if DEVICE_ID.fullmatch(text) else None


def clean_text(value, limit):
	if value is None or isinstance(value, (dict, list, tuple, set)):
		return ""
	return re.sub(r"[\x00-\x1f\x7f]+", " ", str(value)).strip()[:limit]


def parse_emails(text):
	"""Distinct valid addresses from a comma / semicolon / line separated text, lower-cased."""
	found = []
	for address in EMAIL.findall(text or ""):
		address = address.strip().lower()
		if address not in found:
			found.append(address)
	return found


def merge_recipients(*groups):
	"""One distinct, lower-cased list of valid addresses from several text/list sources, in order.

	The Administrator account's own email comes first (see api/call_tracker_auth._approver_emails),
	so nothing needs to be typed anywhere for the OTP to reach it."""
	out = []
	for group in groups:
		text = ", ".join(group) if isinstance(group, (list, tuple)) else (group or "")
		for address in parse_emails(text):
			if address not in out:
				out.append(address)
	return out


def mask_email(address):
	name, _, domain = (address or "").partition("@")
	return f"{name[:1]}***@{domain}" if name and domain else "***"


def ip_in_list(request_ip, allowed_ips):
	"""Frappe's own rule (auth.py validate_ip_address): prefix match against the user's list;
	an empty list means the user is not restricted."""
	if not allowed_ips:
		return True
	return any((request_ip or "").startswith(ip) for ip in allowed_ips)


def new_otp():
	return f"{secrets.randbelow(10**6):06d}"


def hash_otp(otp, challenge_id, key):
	message = f"{challenge_id}:{otp}".encode()
	return hmac.new((key or "").encode(), message, hashlib.sha256).hexdigest()


def fingerprint(password_hash):
	"""Non-reversible marker of the user's current password hash, stored on the token so that any
	password change (self, reset or admin) is noticed and the token stops working."""
	if not password_hash:
		return ""
	return hashlib.sha256(str(password_hash).encode()).hexdigest()[:32]


def evaluate_otp(state, otp, challenge_id, now, max_attempts, key):
	"""-> (result, new_state). result: ok / wrong / locked / expired / unknown.

	`state` is the cached challenge: otp_hash, attempts, expires_at (ISO text). A wrong code
	counts an attempt; at `max_attempts` the challenge is locked (a new code must be requested)."""
	if not state:
		return "unknown", None
	if now > get_datetime(state["expires_at"]):
		return "expired", None
	attempts = cint(state.get("attempts"))
	if attempts >= max_attempts:
		return "locked", None
	supplied = hash_otp(str(otp or "").strip(), challenge_id, key)
	if hmac.compare_digest(supplied, state.get("otp_hash") or ""):
		return "ok", None
	state = {**state, "attempts": attempts + 1}
	return ("locked" if attempts + 1 >= max_attempts else "wrong"), state


def plan_device(active_tokens, device_id, max_devices):
	"""Decide what an approved sign-in does with the user's active tokens (oldest first).

	-> ("reuse", token_name, []) when this device already has one, else
	   ("issue", None, [names to revoke so the new one fits within max_devices])."""
	for token in active_tokens:
		if token["device_id"] == device_id:
			return "reuse", token["name"], []
	room = max(1, cint(max_devices) or 1)
	overflow = len(active_tokens) - room + 1
	return "issue", None, [t["name"] for t in active_tokens[: max(0, overflow)]]


def is_unused_too_long(last_used, created, now, inactivity_days):
	"""True when the token has not been used for more than `inactivity_days`."""
	days = cint(inactivity_days)
	if days <= 0:
		return False
	reference = last_used or created
	if not reference:
		return False
	return get_datetime(reference) < add_to_date(now, days=-days)


# --------------------------------------------------------------- Frappe access


def get_settings():
	return frappe.get_cached_doc(SETTINGS_DOCTYPE)


def log_event(user, event, device_id=None, ip=None, detail=None):
	"""One row in the Call Tracker Login Event list. Committed at once: most callers answer with an
	error right after, and an error would roll the row back."""
	try:
		frappe.get_doc(
			{
				"doctype": EVENT_DOCTYPE,
				"user": clean_text(user, 140),
				"event": event,
				"device_id": clean_text(device_id, 64),
				"ip": clean_text(ip, 45),
				"detail": clean_text(detail, 500),
			}
		).insert(ignore_permissions=True)
		frappe.db.commit()  # nosemgrep
	except Exception:
		frappe.log_error(title="call_tracker_auth: could not log event", message=frappe.get_traceback())


def user_gate(user, settings=None):
	"""(ok, reason). The user must be enabled, hold the required role AND have the INH tick box."""
	settings = settings or get_settings()
	row = frappe.db.get_value("User", user, ["enabled"], as_dict=True)
	if not row or not row.enabled:
		return False, "user disabled"
	required_role = (settings.required_role or "Call Tracker User").strip()
	if required_role not in frappe.get_roles(user):
		return False, f"missing role {required_role}"
	if not frappe.db.has_column("User", APP_FLAG_FIELD):
		return False, f"User field {APP_FLAG_FIELD} not created"
	if not cint(frappe.db.get_value("User", user, APP_FLAG_FIELD)):
		return False, "Call Tracker App User not ticked"
	return True, ""


def current_fingerprint(user):
	rows = frappe.db.sql(
		"select `password` from `__Auth` where doctype='User' and name=%s and fieldname='password'",
		(user,),
	)
	return fingerprint(rows[0][0]) if rows else ""


def user_restricted_ips(user):
	return frappe.get_cached_doc("User", user).get_restricted_ip_list()


def active_tokens(user):
	return frappe.get_all(
		TOKEN_DOCTYPE,
		filters={"user": user, "enabled": 1},
		fields=["name", "device_id", "creation", "password_fingerprint", "api_key"],
		order_by="creation asc",
	)


def revoke_token(name, reason, user=None, ip=None):
	frappe.db.set_value(
		TOKEN_DOCTYPE,
		name,
		{"enabled": 0, "revoked_on": now_datetime(), "revoke_reason": clean_text(reason, 140)},
		update_modified=False,
	)
	log_event(user or frappe.db.get_value(TOKEN_DOCTYPE, name, "user"), "token_revoked", None, ip, reason)


def issue_token(user, device_id, device_model, app_version, signed_in_via, ip):
	"""A new enabled token for (user, device). Returns (name, api_key, api_secret)."""
	api_key = frappe.generate_hash(length=15)
	api_secret = frappe.generate_hash(length=32)
	doc = frappe.get_doc(
		{
			"doctype": TOKEN_DOCTYPE,
			"user": user,
			"device_id": device_id,
			"device_model": clean_text(device_model, 80),
			"app_version": clean_text(app_version, 40),
			"api_key": api_key,
			"api_secret": api_secret,
			"enabled": 1,
			"signed_in_via": signed_in_via,
			"password_fingerprint": current_fingerprint(user),
			"last_used": now_datetime(),
			"last_ip": clean_text(ip, 45),
		}
	).insert(ignore_permissions=True)
	log_event(user, "token_issued", device_id, ip, f"via {signed_in_via}")
	return doc.name, api_key, api_secret


def token_credentials(name):
	"""(api_key, api_secret) of an existing token, for a device that signs in again."""
	from frappe.utils.password import get_decrypted_password

	return (
		frappe.db.get_value(TOKEN_DOCTYPE, name, "api_key"),
		get_decrypted_password(TOKEN_DOCTYPE, name, fieldname="api_secret", raise_exception=False),
	)


def request_api_key():
	"""The key part of the request's `Authorization: token key:secret` header, or None."""
	parts = (frappe.get_request_header("Authorization") or "").split(" ")
	if len(parts) == 2 and parts[0].lower() == "token" and ":" in parts[1]:
		return parts[1].split(":", 1)[0]
	return None


def enforce_token_scope():
	"""`auth_hooks` entry: runs on every request right after core has validated an API key.

	Does nothing unless the request names the Call Tracker Token source. Then:
	  - only an allowlisted method (and no `?cmd=`) may be called: else 403;
	  - the token's user must still be enabled, hold the role and have the tick box: else 403;
	  - a password change since the token was issued ends the token: 401;
	  - `last_used` / `last_ip` are refreshed at most every 10 minutes.
	"""
	request = getattr(frappe.local, "request", None)
	if request is None or request.headers.get(SOURCE_HEADER) != TOKEN_DOCTYPE:
		return

	if not is_request_allowed(request.path, frappe.form_dict.get("cmd")):
		frappe.throw("This credential cannot be used for that request.", frappe.PermissionError)

	user = frappe.session.user
	if not user or user == "Guest":
		return  # core raises the 401; there is no user to check

	ok, reason = user_gate(user)
	if not ok:
		frappe.throw("Call Tracker access is not available for this account.", frappe.PermissionError)

	key = request_api_key()
	token = (
		frappe.db.get_value(
			TOKEN_DOCTYPE, {"api_key": key, "enabled": 1}, ["name", "password_fingerprint"], as_dict=True
		)
		if key
		else None
	)
	if not token:
		return
	if token.password_fingerprint and token.password_fingerprint != current_fingerprint(user):
		revoke_token(token.name, "password changed", user, frappe.local.request_ip)
		frappe.db.commit()  # nosemgrep
		raise frappe.AuthenticationError

	cache_key = f"ct_last_used:{token.name}"
	if not frappe.cache.get_value(cache_key):
		try:
			frappe.db.set_value(
				TOKEN_DOCTYPE,
				token.name,
				{"last_used": now_datetime(), "last_ip": clean_text(frappe.local.request_ip, 45)},
				update_modified=False,
			)
			frappe.cache.set_value(cache_key, 1, expires_in_sec=LAST_USED_THROTTLE_SECONDS)
		except Exception:
			pass


def expire_unused_tokens():
	"""Daily job: disable tokens not used for `token_inactivity_days` (default 30)."""
	settings = get_settings()
	days = cint(settings.token_inactivity_days)
	now = now_datetime()
	for token in frappe.get_all(
		TOKEN_DOCTYPE, filters={"enabled": 1}, fields=["name", "user", "last_used", "creation"]
	):
		if is_unused_too_long(token.last_used, token.creation, now, days):
			revoke_token(token.name, f"unused for more than {days} days", token.user)
	frappe.db.commit()  # nosemgrep


def challenge_state(user, device_id, device_model, app_version, otp_hash, ttl_seconds, ip):
	return {
		"user": user,
		"device_id": device_id,
		"device_model": device_model,
		"app_version": app_version,
		"otp_hash": otp_hash,
		"attempts": 0,
		"expires_at": str(add_to_date(now_datetime(), seconds=ttl_seconds)),
		"ip": ip,
	}


def encode_state(state):
	return json.dumps(state)


def decode_state(raw):
	if not raw:
		return None
	return raw if isinstance(raw, dict) else json.loads(raw)
