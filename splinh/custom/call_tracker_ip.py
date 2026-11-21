"""Call Tracker tokens and the user's `restrict_ip` list (added 2026-11-03).

Why this exists. The Call Tracker app signs every request with a "Call Tracker Token"
(`Authorization: token key:secret` + `Frappe-Authorization-Source: Call Tracker Token`, see
custom/call_tracker_auth.py). Until Frappe 16.12 such a request was never checked against the user's
`restrict_ip` list, which is what lets reps use the app outside the office once an OTP approved the
device. Frappe 16.51 (commit 37cd0c21f0, "enforce IP restrictions for header-based authentication")
calls `validate_ip_address(user)` at the end of `validate_auth()` for every request authenticated by an
Authorization header, so every token request from outside the office IPs became `401 Access not allowed
from this IP Address` (sync, recordings, heartbeat, reviews, DILO).

What this does. It wraps `frappe.auth.validate_ip_address` the same way sigzensfa and splashjetsfa do
for their mobile APIs. The wrapper skips the check ONLY for a request that is a Call Tracker token
request (all of: the Source header names the Call Tracker Token, the path is one of the allowlisted
Call Tracker methods with no `?cmd=`, and the `Authorization` key belongs to an enabled Call Tracker
Token of the very user being checked). Anything else, and any error while deciding, goes to the
previous check unchanged, so desk logins, cookie sessions, other apps' endpoints and plain API keys
behave exactly as before. The token keeps its other protections (OTP approval, device binding, endpoint
allowlist, user gate, password-change revoke, inactivity expiry).

Installed from `splinh/__init__.py` at package import (hooks.py is not imported on a warm hooks cache).
The decision itself (`bypasses_ip_check`) is pure so it can be unit tested offline.
"""

import frappe

from splinh.custom.call_tracker_auth import (
	SOURCE_HEADER,
	TOKEN_DOCTYPE,
	is_calls_path,
	is_inbox_path,
	is_request_allowed,
	phone_calls_enabled,
	phone_inbox_enabled,
	request_api_key,
)

PATCH_FLAG = "__splinh_call_tracker_ip_bypass__"


def bypasses_ip_check(source, path, cmd, key, token_user, user, phone_inbox=False, phone_calls=False):
	"""True when this is a Call Tracker token request of `user` that may skip the restrict_ip list.

	source: value of the Frappe-Authorization-Source header; path / cmd: the request path and `?cmd=`;
	key: the key part of the Authorization header; token_user: the user of the enabled token with that
	key (None when there is none); user: the user whose IP is being checked; phone_inbox: the team
	inbox on the phone is switched on, so its methods count as Call Tracker methods too."""
	return bool(
		source == TOKEN_DOCTYPE
		and is_request_allowed(path, cmd, phone_inbox, phone_calls)
		and key
		and user
		and token_user == user
	)


def _is_call_tracker_request(user):
	request = getattr(frappe.local, "request", None)
	if request is None or request.headers.get(SOURCE_HEADER) != TOKEN_DOCTYPE:
		return False  # the common case: nothing else is looked at
	key = request_api_key()
	token_user = (
		frappe.db.get_value(TOKEN_DOCTYPE, {"api_key": key, "enabled": 1}, "user") if key else None
	)
	return bypasses_ip_check(
		request.headers.get(SOURCE_HEADER),
		request.path,
		frappe.form_dict.get("cmd"),
		key,
		token_user,
		user,
		phone_inbox=is_inbox_path(request.path) and phone_inbox_enabled(),
		phone_calls=is_calls_path(request.path) and phone_calls_enabled(),
	)


def patch_validate_ip_address(auth_module=None):
	"""Wrap `validate_ip_address` of frappe.auth (or `auth_module`, for tests). Idempotent."""
	if auth_module is None:
		import frappe.auth as auth_module

	current = getattr(auth_module, "validate_ip_address", None)
	# A wrapper added after ours hides our flag, so remember the install on the module as well.
	if not current or getattr(auth_module, PATCH_FLAG, False):
		return

	def wrapped_validate_ip_address(user):
		try:
			if user and user != "Guest" and _is_call_tracker_request(user):
				return None
		except Exception:
			pass  # never more permissive because of an error here: use the previous check
		return current(user)

	setattr(auth_module, PATCH_FLAG, True)
	auth_module.validate_ip_address = wrapped_validate_ip_address
