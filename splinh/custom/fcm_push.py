"""Ring pushes for WhatsApp calls on the phone (Firebase Cloud Messaging, HTTP v1).

Waba fires the `waba_call_push` hook (user, data) for every call event of a person; this file turns it into one high-priority data message per
enabled device of that person. `data` carries only ids and a time (Waba builds it): no customer name, number or text ever leaves the server.
Until a Firebase key is stored in Call Tracker Settings, nothing is sent. A failure never touches the call.
"""

import json

import frappe

TOKEN_DOCTYPE = "Call Tracker Token"
SCOPE = "https://www.googleapis.com/auth/firebase.messaging"
SEND_URL = "https://fcm.googleapis.com/v1/projects/{project}/messages:send"
RING_TTL_SECONDS = 30


# ------------------------------------------------------------------ pure rules


def build_message(device_token, data):
	"""The FCM v1 message for one device. Data-only and high priority, so Android wakes the app at once; a ring dies with the call (ttl)."""
	ttl = RING_TTL_SECONDS if data.get("kind") == "ring" else 60
	return {
		"message": {
			"token": device_token,
			"data": {str(k): str(v) for k, v in data.items()},
			"android": {"priority": "HIGH", "ttl": f"{ttl}s"},
		}
	}


def is_dead_token_error(status_code, body):
	"""FCM says this device token will never work again (app uninstalled, token rotated): stop pushing to it."""
	text = body if isinstance(body, str) else json.dumps(body or {})
	return status_code in (404, 410) or "UNREGISTERED" in text or "registration-token-not-registered" in text


# ------------------------------------------------------------------ frappe side


def push_call_event(user, data):
	"""Hook `waba_call_push`: queue the sends for after the commit (the ring itself must never wait for Google)."""
	if not user or not data or not _key_present():
		return
	devices = frappe.get_all(TOKEN_DOCTYPE, filters={"user": user, "enabled": 1, "push_enabled": 1}, pluck="name")
	if not devices:
		return
	frappe.enqueue(
		"splinh.custom.fcm_push.send_to_devices", queue="short", devices=devices, data=data, enqueue_after_commit=True, job_id=None
	)


def _key_present():
	try:
		return bool(frappe.get_single("Call Tracker Settings").get_password("fcm_service_account", raise_exception=False))
	except Exception:
		return False


def _credentials():
	"""(project id, bearer token) from the stored service-account key."""
	from google.oauth2 import service_account
	import google.auth.transport.requests

	info = json.loads(frappe.get_single("Call Tracker Settings").get_password("fcm_service_account"))
	creds = service_account.Credentials.from_service_account_info(info, scopes=[SCOPE])
	creds.refresh(google.auth.transport.requests.Request())
	return info["project_id"], creds.token


def send_to_devices(devices, data, transport=None):
	"""The job: send `data` to each device (a Call Tracker Token name). `transport(url, headers, body) -> (status, text)` is injectable for
	tests; the real one posts with requests. Dead tokens are switched off."""
	sent = 0
	try:
		project, bearer = _credentials() if transport is None else ("test", "test")
	except Exception:
		frappe.log_error(title="Call push: the Firebase key could not be used")
		return 0
	post = transport or _post
	for name in devices:
		token = frappe.db.get_value(TOKEN_DOCTYPE, name, "push_token")
		if not token:
			continue
		try:
			status, text = post(SEND_URL.format(project=project), {"Authorization": f"Bearer {bearer}"}, build_message(token, data))
		except Exception:
			frappe.log_error(title="Call push: a send failed")
			continue
		if 200 <= status < 300:
			sent += 1
		elif is_dead_token_error(status, text):
			frappe.db.set_value(TOKEN_DOCTYPE, name, {"push_enabled": 0, "push_token": None}, update_modified=False)
			frappe.db.commit()
		else:
			frappe.log_error(title="Call push: Firebase refused a send", message=f"{status} {str(text)[:300]}")
	return sent


def _post(url, headers, body):
	import requests

	response = requests.post(url, headers=headers, json=body, timeout=10)
	return response.status_code, response.text
