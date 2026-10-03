"""Mobile call-tracking integration on stock `Call Log`.

Two whitelisted endpoints for the mobile app:
  - push_calls: uploads a batch of Android call-log entries as Call Log records.
  - get_my_api_credentials: self-service API key/secret issuance for the logged-in
    user, so the mobile app can authenticate its own subsequent calls.

See the approved plan (the-issue-is-theres-tingly-pebble.md) and
splinh.custom.splinh_setup.ensure_call_tracking for the Roles/Custom DocPerm
rows that actually govern who push_calls is allowed to insert for.

--- Why the actual insert is deferred to a background job (2026-09-24) ---
`Call Log.before_insert()` - stock ERPNext, not our code - matches the caller's
phone number against Lead and Contact with a leading-wildcard `LIKE '%number'`,
which cannot use any index (confirmed via EXPLAIN: type=ALL over the full Lead
table). A single insert took 15-33s in testing. The mobile client's own HTTP
timeout is far shorter than that, so every real request from the phone was
timing out client-side (nginx logged these as 499 - "client closed the
connection" - not a server rejection) even though the request eventually
succeeded server-side. Ruled out first, from source and from a live replay
before touching anything: CSRF (frappe/auth.py's validate_csrf_token returns
immediately whenever there is no session-stored token, which token-auth
requests never have) and auth (credentials and permissions both checked out).

Deferring the insert removes the HTTP timeout entirely - push_calls now
answers as soon as the job is durably queued, not once the slow matching
finishes. That trade means a queued call that ultimately fails is no longer
visible to the phone as a timeout, so the retry-then-record-failure path below
is not optional - without it, a failure would go from "loud but confusing" to
silently lost, which is a worse failure mode than the one being fixed.

The underlying slow query itself (the unindexable LIKE match) is a SEPARATE,
not-yet-scoped follow-up - deliberately not touched here.

--- List-view readability and the Lead/Customer connection (2026-09-24) ---
Two more fields are populated here (custom_call_summary, and the party-link
trio in _resolve_linked_party) beyond the original push_calls scope, because
the list view showed the raw sync id and the Lead/Contact match Call Log's own
before_insert already makes was invisible outside the record itself. `customer`
(stock field) and `custom_lead` (new) exist specifically so Frappe's dashboard
"transactions" mechanism - which finds a connection by matching a Link field
on Call Log against the doctype being viewed - can show a "Calls" entry on a
Lead or Customer's own Connections tab; see
splinh.override.lead_dashboard/customer_dashboard and
splinh.custom.splinh_setup.ensure_call_tracking_display.
"""

import json
import os
import time
import traceback
from urllib.parse import unquote

import frappe
import requests
from frappe.utils import cint, get_bench_path, get_datetime
from frappe.utils.file_manager import save_file

from splinh.custom.call_log_phone_matching import ensure_dynamic_link
from splinh.custom.phone_lookup import find_party_by_phone

CALL_LOG_INSERT_MAX_ATTEMPTS = 3
CALL_LOG_INSERT_RETRY_DELAY_SECONDS = 5

# (direction, android_call_type sub-label) -> (Call Log `type`, Call Log `status`).
# Exactly the table in the approved plan. `type` (Incoming/Outgoing) is its OWN
# field on the request payload - android_call_type carries only the sub-label
# ("answered"/"missed"/"rejected" for Incoming; any value for Outgoing, since
# Outgoing is classified by duration, not by android_call_type at all).
#
# Found and fixed during independent verification: an earlier version of this
# function tried to parse the direction back out of android_call_type itself
# (expecting a combined "Incoming answered" string), which silently rejected
# every request built against the plan's actual contract - it returned an empty
# accepted list with no error, no Error Log entry, nothing visible at all. Caught
# by testing with the plan's literal payload shape rather than the shape used to
# write the code, which had (wrongly) come to embed direction in both fields.
ANDROID_CALL_TYPE_MAP = {
	("Incoming", "answered"): ("Incoming", "Completed"),
	("Incoming", "missed"): ("Incoming", "No Answer"),
	("Incoming", "rejected"): ("Incoming", "Busy"),
	# Outgoing depends on duration, not a fixed lookup - handled separately below.
}


def _map_call_type(call_type, android_call_type, duration):
	"""Return (type, status) for a single call entry, or (None, None) if unrecognised.

	`call_type` is the request's own `type` field ("Incoming"/"Outgoing"), the
	authoritative source of direction. `android_call_type` supplies only the
	sub-label for Incoming calls.
	"""
	direction = (call_type or "").strip().lower()
	if direction == "incoming":
		sub = (android_call_type or "").strip().lower()
		return ANDROID_CALL_TYPE_MAP.get(("Incoming", sub), (None, None))
	if direction == "outgoing":
		if cint(duration) > 0:
			return "Outgoing", "Completed"
		return "Outgoing", "No Answer"
	return None, None


def _format_duration(seconds):
	seconds = cint(seconds)
	minutes, secs = divmod(seconds, 60)
	return f"{minutes}m {secs}s" if minutes else f"{secs}s"


def _call_summary(number, call_type, status, duration):
	"""Human-readable title for a Call Log, e.g. "Incoming from +91 98450 11223
	(2m 12s)" or "Outgoing to +91 90210 44110 - No Answer". Set as the doctype's
	title_field (2026-09-24) so this, not the raw sync id, is what a person
	actually reads in the list view and everywhere Call Log is shown as a link.
	"""
	preposition = "from" if call_type == "Incoming" else "to"
	if status == "Completed":
		return f"{call_type} {preposition} {number} ({_format_duration(duration)})"
	return f"{call_type} {preposition} {number} - {status}"


def _build_call_log_values(name, device_id, entry, call_type, status):
	number = entry.get("from") or entry.get("from_number") if call_type == "Incoming" else (
		entry.get("to") or entry.get("to_number")
	)
	duration = entry.get("duration") or 0
	return {
		"doctype": "Call Log",
		"id": name,
		"from": entry.get("from") or entry.get("from_number"),
		"to": entry.get("to") or entry.get("to_number"),
		"type": call_type,
		"status": status,
		"duration": duration,
		"start_time": entry.get("start_time"),
		"summary": entry.get("summary"),
		"custom_device_id": device_id,
		"custom_sim_slot": entry.get("sim_slot"),
		"custom_contact_name": entry.get("contact_name"),
		"custom_call_summary": _call_summary(number, call_type, status, duration),
		# Explicit, never left to a doctype default: custom_source is the field
		# that keeps measured (SIM) vs inferred (WhatsApp) rows distinguishable
		# forever, so it has no default by design - see ensure_call_tracking_source.
		"custom_source": "SIM",
	}


def _resolve_linked_party(doc):
	"""Read back doc.links (populated in-memory by Call Log's own before_insert -
	confirmed live, no reload needed) and set the three party fields a person or
	a dashboard actually needs: a human-readable summary, and the two Link
	fields ("customer" - stock, currently unpopulated by anything; "custom_lead"
	- new) that make the Lead/Customer Connections-tab dashboard entries work.

	Priority: Customer first (most resolved), then Lead, then a bare Contact
	match with neither behind it (party fields left blank - nothing to link).
	At most two extra queries (Contact -> its linked Customer, then -> its
	linked Lead if no Customer was found), not a query per link row.

	BUG FOUND 2026-09-26 (real test case: CRM-LEAD-2026-02789 / Contact
	"Danish"): a Lead's auto-created Contact is Dynamic-Linked to the LEAD,
	not a Customer - this is the standard, common case (ERPNext creates a
	Contact for every new Lead). find_party_by_phone matches Contact Phone
	before it ever reaches Lead's own normalized fields, so for any Lead with
	its auto-created Contact, the match always comes back as a bare Contact.
	The Contact->Customer resolution below then finds nothing (there is no
	Customer behind it) and used to stop there, silently dropping the Lead
	link entirely - even though the Lead is one Dynamic Link hop away, same
	as the Customer case. Confirmed live: ALL 14 Call Log rows linked to
	Contact "Danish" (CRM-LEAD-2026-02789's auto-created contact) had
	customer AND custom_lead both NULL before this fix.
	"""
	customer = lead = contact = None
	for link in doc.links or []:
		if link.link_doctype == "Customer" and not customer:
			customer = link.link_name
		elif link.link_doctype == "Lead" and not lead:
			lead = link.link_name
		elif link.link_doctype == "Contact" and not contact:
			contact = link.link_name

	if not customer and contact:
		customer = frappe.db.get_value(
			"Dynamic Link",
			{"parenttype": "Contact", "parent": contact, "link_doctype": "Customer"},
			"link_name",
		)

	if not customer and not lead and contact:
		lead = frappe.db.get_value(
			"Dynamic Link",
			{"parenttype": "Contact", "parent": contact, "link_doctype": "Lead"},
			"link_name",
		)

	if customer:
		name_ = frappe.db.get_value("Customer", customer, "customer_name") or customer
		return {"customer": customer, "custom_lead": None, "custom_linked_party": f"Customer: {name_}"}
	if lead:
		name_ = frappe.db.get_value("Lead", lead, "lead_name") or lead
		return {"customer": None, "custom_lead": lead, "custom_linked_party": f"Lead: {name_}"}
	return {"customer": None, "custom_lead": None, "custom_linked_party": ""}


def _insert_call_log_job(name, device_id, device_call_id, user, entry):
	"""Background job for a SIM call (push_calls). Signature deliberately left
	unchanged when WhatsApp ingestion was added (2026-09-25), so a SIM job
	already sitting in the queue under this signature still runs correctly
	across a worker restart. The shared work lives in _insert_with_retry.
	"""
	values = _build_call_log_values(name, device_id, entry, entry["_call_type"], entry["_status"])
	_insert_with_retry(name, values, user, device_id, device_call_id, entry)


def _insert_whatsapp_call_log_job(name, device_id, correlation_id, user, values, payload):
	"""Background job for a WhatsApp call (ingest_whatsapp_call). Values are
	built synchronously in the endpoint (cheap); only the slow insert - stock
	before_insert's unindexed phone matching, the same 15-33s cost push_calls
	was moved off the request for - happens here.
	"""
	_insert_with_retry(name, values, user, device_id, correlation_id, payload)


def _pre_match_number(values):
	"""Normalized, indexed exact-match lookup against the SAME number stock
	Call Log's before_insert will itself use (mirrors its own
	`from if is_incoming_call() else to` choice - "Unknown" direction, like
	Outgoing, uses `to`, matching _build_whatsapp_call_log_values's comment on
	why the number always goes in `to` for that case).

	Returns a match dict ({"doctype": "Contact"|"Lead", "name": ...}) or None.
	See custom/call_log_phone_matching.py for why this exists: stock's own
	match is an unindexed, dash-sensitive `LIKE '%number'` that misses numbers
	whose stored form has a separator character sitting inside the compared
	suffix window (confirmed live: Contact Phone "+91-9225144953" matches
	incoming "9225144953" but not "919225144953" - same real number, two
	formats).
	"""
	number = values.get("from") if values.get("type") == "Incoming" else values.get("to")
	return find_party_by_phone(number)


def _insert_with_retry(name, values, user, device_id, device_call_id, payload):
	"""The actual slow insert, shared by both SIM and WhatsApp jobs, off the
	request/response path entirely - see the module docstring for why.

	Runs as `user` (frappe.set_user), not Administrator, so the SAME real
	Call Tracker User `if_owner` permission governs this insert as would have
	applied synchronously - deferring the work does not widen who it runs as.

	Retries a small, fixed number of times for genuinely transient failures
	(the Error Log already shows real lock-wait-timeout/deadlock errors on this
	bench). Exhausting retries writes a `Call Sync Failure` record AND the
	existing frappe.log_error convention this module already uses - not just
	one or the other - so a lost call is always both queryable and visible in
	the place error investigation already looks on this project. For WhatsApp
	rows, `device_call_id` on that record holds the correlation_id; the "WA-"
	prefix on call_log_name makes the source unambiguous.

	--- Normalized match, pre-insert (2026-09-26) ---
	Confirmed live: stock Call Log's own before_insert is UNCONDITIONAL - it
	does not check whether self.links is already populated before running its
	own (unindexed, dash-sensitive) match, so pre-setting this does NOT skip or
	speed up that query; the ~15-33s cost documented above still happens every
	time, regardless. What pre-setting DOES guarantee is that the CORRECT
	result is what actually survives: before_insert only ever APPENDS a link it
	finds - it never clears or replaces an existing links row - so our
	normalized match, appended before .insert(), is never overwritten by
	whatever (possibly nothing, per the dash bug) core's own query finds
	afterward. _resolve_linked_party (below) already reads doc.links generically
	post-insert, so no other change is needed for this to be picked up.
	"""
	frappe.set_user(user)
	last_error = None
	try:
		for attempt in range(1, CALL_LOG_INSERT_MAX_ATTEMPTS + 1):
			if frappe.db.exists("Call Log", name):
				return  # a concurrent/duplicate job already landed this one
			try:
				match = _pre_match_number(values)
				doc = frappe.get_doc(dict(values))
				if match:
					doc.append("links", {"link_doctype": match["doctype"], "link_name": match["name"]})
				# Party already resolved above: SplinhCallLog (override/call_log.py)
				# skips stock before_insert's two unindexed LIKE scans.
				doc.flags.splinh_party_resolved = True
				doc.insert(ignore_permissions=False)
				# doc.links is already populated in-memory at this point (our own
				# pre-set link above, plus whatever Call Log's own before_insert
				# additionally found during .insert()) - confirmed live, no reload
				# needed. db_set rather than a second .save() so this is a targeted
				# update, not a full re-validate/re-run of controller hooks.
				party = _resolve_linked_party(doc)
				for fieldname, value in party.items():
					doc.db_set(fieldname, value, update_modified=False)
				# Make the RESOLVED party (Customer/Lead), not just the
				# directly-matched Contact already in doc.links, its own
				# Dynamic Link row - this is what stock's get_linked_call_logs
				# (Activity timeline, apps/erpnext, unmodified) actually reads;
				# it never looks at the customer/custom_lead fields set above.
				# See ensure_dynamic_link's docstring for why this is a direct
				# child-row insert, not a doc.append()+save().
				if party.get("customer"):
					ensure_dynamic_link(doc, "Customer", party["customer"])
				elif party.get("custom_lead"):
					ensure_dynamic_link(doc, "Lead", party["custom_lead"])
				return
			except Exception as e:
				last_error = e
				frappe.db.rollback()
				if attempt < CALL_LOG_INSERT_MAX_ATTEMPTS:
					time.sleep(CALL_LOG_INSERT_RETRY_DELAY_SECONDS)

		# All attempts exhausted - record it durably, don't just log and forget.
		frappe.set_user("Administrator")
		frappe.get_doc(
			{
				"doctype": "Call Sync Failure",
				"device_id": device_id,
				"device_call_id": device_call_id,
				"call_log_name": name,
				"user": user,
				"attempts": CALL_LOG_INSERT_MAX_ATTEMPTS,
				"last_attempt": frappe.utils.now_datetime(),
				"reason": str(last_error)[:1000],
				"payload": json.dumps(payload, default=str),
			}
		).insert(ignore_permissions=True)
		frappe.db.commit()
		frappe.log_error(
			title="splinh call_tracking: Call Log insert failed after retries",
			message=f"device_id={device_id} device_call_id={device_call_id} name={name} "
			f"attempts={CALL_LOG_INSERT_MAX_ATTEMPTS} last_error={last_error}",
		)
	finally:
		frappe.set_user("Administrator")


@frappe.whitelist(methods=["POST"])
def push_calls(device_id, calls):
	"""Accept a batch of Android call-log entries and queue them as Call Log
	records. Returns as soon as each entry is either already-synced or durably
	queued - the actual insert (slow; see module docstring) happens in a
	background job, so this endpoint is no longer bound by how long that takes.

	`calls` is a JSON array (or already-parsed list) of dicts, each with:
	  device_call_id, type, android_call_type, from (or `from_number`), to
	  (or `to_number`), start_time, duration, sim_slot, contact_name, summary

	`user` is always `frappe.session.user` - never accepted from the payload -
	and is carried into the background job so the insert still runs under the
	real caller's own permissions, not a blanket bypass.

	Returns the list of `device_call_id` values that were accepted this call or
	already existed from an earlier push (so the mobile app knows what it can now
	mark synced - dedup is a true no-op on resend, keyed on the Call Log name).
	A queued-but-not-yet-inserted entry IS in this list; if its background
	insert ultimately fails after retries, it lands in `Call Sync Failure`
	rather than disappearing - check there, not just this response, for the
	authoritative record of what actually made it into Call Log.
	"""
	if isinstance(calls, str):
		calls = json.loads(calls)

	user = frappe.session.user
	accepted = []

	for entry in calls:
		device_call_id = entry.get("device_call_id")
		start_time = entry.get("start_time")
		duration = entry.get("duration") or 0
		# device_call_id is already "{android_raw_id}_{timestamp_ms}" on the phone's
		# own side - a trailing epoch here was a second, redundant copy of the same
		# timestamp (2026-09-25, confirmed against live data before removing it).
		name = f"M-{device_id}-{device_call_id}"

		if frappe.db.exists("Call Log", name):
			accepted.append(device_call_id)
			continue

		call_type, status = _map_call_type(entry.get("type"), entry.get("android_call_type"), duration)
		if not call_type:
			frappe.log_error(
				title="splinh call_tracking: unrecognised android_call_type",
				message=f"device_id={device_id} device_call_id={device_call_id} "
				f"android_call_type={entry.get('android_call_type')!r}",
			)
			continue

		entry = dict(entry, _call_type=call_type, _status=status)
		frappe.enqueue(
			_insert_call_log_job,
			queue="long",
			timeout=300,
			enqueue_after_commit=True,
			name=name,
			device_id=device_id,
			device_call_id=device_call_id,
			user=user,
			entry=entry,
		)
		accepted.append(device_call_id)

	return accepted


# --- WhatsApp call ingestion (2026-09-25) --------------------------------------
# Same Call Log doctype as SIM calls (a deliberate decision, reversing an earlier
# "separate doctype" plan). What keeps the two honest side by side is
# custom_source: SIM rows are MEASURED (Android's own call log), WhatsApp rows are
# INFERRED (a notification-based detector). Device testing proved WhatsApp can
# never tell rejected/failed apart from an ordinary completed/missed call, so
# genuine ambiguity is stored as "Unknown" - never bucketed into Completed or No
# Answer, which would present a guess as a fact.

WHATSAPP_DIRECTION_MAP = {"incoming": "Incoming", "outgoing": "Outgoing", "unknown": "Unknown"}
WHATSAPP_STATUS_MAP = {"completed": "Completed", "missed": "No Answer", "unknown": "Unknown"}
WHATSAPP_CALL_TYPE_MAP = {"voice": "Voice", "video": "Video", "unknown": "Unknown"}
WHATSAPP_CONFIDENCE_MAP = {"high": "High", "medium": "Medium", "low": "Low"}


def _naive_datetime(value):
	"""ISO 8601, phone-local (per the contract) -> naive datetime, offset dropped
	rather than converted, matching how SIM start_time is already stored."""
	if not value:
		return None
	return get_datetime(value).replace(tzinfo=None)


def _whatsapp_summary(call_type, direction, number, status, duration):
	"""Title for a WhatsApp row, source-prefixed so it's obvious in the list view
	which rows are inferred - e.g. "WhatsApp Voice · Incoming from +91... (2m 12s)"."""
	head = f"WhatsApp {call_type or 'Unknown'} ·"
	if direction == "Incoming":
		body = f"Incoming from {number}"
	elif direction == "Outgoing":
		body = f"Outgoing to {number}"
	else:
		body = f"Unknown direction, {number}"
	if status == "Completed":
		return f"{head} {body} ({_format_duration(duration)})"
	return f"{head} {body} - {status}"


def _build_whatsapp_call_log_values(name, device_id, entry):
	"""Map one WhatsApp payload entry onto Call Log fields. Returns (values,
	unexpected) where `unexpected` lists any raw values that weren't in the
	contract, so the caller can log them - they still land (as Unknown), but a
	value outside the contract is a real mobile-side signal that must be visible.
	"""
	unexpected = []

	raw_direction = (entry.get("direction") or "").strip().lower()
	direction = WHATSAPP_DIRECTION_MAP.get(raw_direction)
	if not direction:
		unexpected.append(f"direction={entry.get('direction')!r}")
		direction = "Unknown"

	raw_status = (entry.get("status") or "").strip().lower()
	status = WHATSAPP_STATUS_MAP.get(raw_status)
	if not status:
		# Includes "rejected" and "failed": the contract lists them historically,
		# but the fixed detector never sends them - WhatsApp cannot distinguish
		# them. If one arrives anyway, that's a mobile-side bug worth seeing.
		unexpected.append(f"status={entry.get('status')!r}")
		status = "Unknown"

	raw_call_type = (entry.get("call_type") or "").strip().lower()
	call_type = WHATSAPP_CALL_TYPE_MAP.get(raw_call_type)
	if not call_type:
		if raw_call_type:
			unexpected.append(f"call_type={entry.get('call_type')!r}")
		call_type = "Unknown"

	confidence = WHATSAPP_CONFIDENCE_MAP.get((entry.get("detector_confidence") or "").strip().lower())

	started = _naive_datetime(entry.get("started_at"))
	ended = _naive_datetime(entry.get("ended_at"))
	# Computed server-side, never taken from the client (per the contract).
	duration = max(int((ended - started).total_seconds()), 0) if started and ended else 0

	phone = entry.get("phone_number")
	contact_name = entry.get("contact_name")
	display = phone or contact_name or "unknown number"

	values = {
		"doctype": "Call Log",
		"id": name,
		# Stock before_insert matches `from` only when type is Incoming and `to`
		# otherwise, so the number goes in `to` for Outgoing AND Unknown - that's
		# what keeps an unknown-direction call linkable to a Lead through the
		# UNMODIFIED stock matcher. Documented in API-CONTRACT.md.
		"from": phone if direction == "Incoming" else None,
		"to": phone if direction != "Incoming" else None,
		"type": direction,
		"status": status,
		"duration": duration,
		"start_time": started,
		"end_time": ended,
		"custom_device_id": device_id,
		"custom_contact_name": contact_name,
		"custom_source": "WhatsApp",
		"custom_call_type": call_type,
		"custom_detector_confidence": confidence,
		"custom_whatsapp_app_version": entry.get("app_version"),
		"custom_call_summary": _whatsapp_summary(call_type, direction, display, status, duration),
	}
	return values, unexpected


@frappe.whitelist(methods=["POST"])
def ingest_whatsapp_call(device_id, calls):
	"""Accept a batch of WhatsApp call events and queue them as Call Log rows.
	Mirrors push_calls exactly: same doctype, same deterministic-name dedup
	(`WA-{device_id}-{correlation_id}`), same background insert with retry and
	Call Sync Failure recording, same real-caller permissions.

	`calls` is a JSON array (or already-parsed list) of dicts, each with:
	  correlation_id, direction, call_type, status, started_at, ended_at,
	  contact_name, phone_number, detector_confidence, app_version

	Returns the list of `correlation_id`s accepted this call or already present
	from an earlier push. custom_source is not part of the payload - this
	endpoint sets it to "WhatsApp" itself.
	"""
	if isinstance(calls, str):
		calls = json.loads(calls)

	user = frappe.session.user
	accepted = []

	for entry in calls:
		correlation_id = entry.get("correlation_id")
		if not correlation_id:
			frappe.log_error(
				title="splinh call_tracking: WhatsApp call without correlation_id",
				message=f"device_id={device_id} entry={entry!r}",
			)
			continue

		name = f"WA-{device_id}-{correlation_id}"
		if frappe.db.exists("Call Log", name):
			accepted.append(correlation_id)
			continue

		values, unexpected = _build_whatsapp_call_log_values(name, device_id, entry)
		if unexpected:
			frappe.log_error(
				title="splinh call_tracking: unexpected WhatsApp value(s), stored as Unknown",
				message=f"device_id={device_id} correlation_id={correlation_id} " + ", ".join(unexpected),
			)

		frappe.enqueue(
			_insert_whatsapp_call_log_job,
			queue="long",
			timeout=300,
			enqueue_after_commit=True,
			name=name,
			device_id=device_id,
			correlation_id=correlation_id,
			user=user,
			values=values,
			payload=entry,
		)
		accepted.append(correlation_id)

	return accepted


@frappe.whitelist()
def get_my_api_credentials():
	"""Self-service API key/secret for the logged-in user only.

	Deliberately NOT Frappe's core `generate_keys` (frappe.only_for("System
	Manager")) - that is unusable for a mobile app doing this for itself. Gated
	only to "must be logged in", reusing the same hash-generation approach as
	sigzensfa's generate_keys() helper. Always acts on frappe.session.user - no
	other user id is ever accepted from the request.
	"""
	if frappe.session.user == "Guest":
		frappe.throw("You must be logged in to get API credentials.", frappe.PermissionError)

	user = frappe.session.user
	user_doc = frappe.get_doc("User", user)

	api_secret = frappe.generate_hash(length=15)
	if not user_doc.api_key:
		user_doc.api_key = frappe.generate_hash(length=15)
	user_doc.api_secret = api_secret
	user_doc.save(ignore_permissions=True)

	return {"api_key": user_doc.api_key, "api_secret": api_secret}


# --- Call transcription (2026-09-29) -------------------------------------------
# Same shape as push_calls/ingest_whatsapp_call, and for the same reason: the local
# transcription service (127.0.0.1:8100, standalone - see calltranscribe/, not this
# app) takes 1.5-3+ minutes per call. A phone-facing endpoint that waits on that
# synchronously would tie up a gunicorn worker for that long and time out
# client-side exactly like the pre-2026-09-24 push_calls did - so this endpoint only
# ever accepts the recording, queues the transcription, and returns immediately.
#
# Deliberately no separate "Call Transcription Failure" doctype (unlike Call Sync
# Failure): that doctype exists because a failed push_calls insert means the Call
# Log row itself was never created - there. Here the Call Log already exists (it's
# created by push_calls/ingest_whatsapp_call well before a recording arrives), so
# there's always a real record to carry the status/error directly - a second
# doctype would just be an unnecessary indirection.

TRANSCRIBE_SERVICE_URL = "http://127.0.0.1:8100/transcribe"
TRANSCRIBE_TIMEOUT_SECONDS = 600  # matches calltranscribe's own gunicorn -t 600
TRANSCRIBE_MODEL_LABEL = "ai4bharat/indic-conformer-600m-multilingual@e9b71b369c04"


@frappe.whitelist(methods=["POST"])
def receive_call_recording(call_log_name):
	"""Phone uploads a call recording here (multipart field `audio_file`), same
	token auth as push_calls. Attaches the recording as a private File on the Call
	Log (durable source of truth for the background job, and gives free playback
	in the Call Log form - not asked for, but a natural side effect of using
	Frappe's own save_file rather than passing raw bytes through the queue), sets
	custom_transcript_status to Pending, and queues the actual transcription.
	Returns immediately - never waits on the transcription itself.

	Permission check is `frappe.has_permission` (not a raw exists() check) so the
	existing if_owner rule on Call Tracker User (only your own synced calls) is
	respected here exactly as it already is for read/create - this is deliberately
	NOT bypassed just because the write itself uses db_set below.

	Calls _ensure_absolute_site_path() first too, not just the background job:
	confirmed live (2026-09-29) that this is NOT purely a background-job issue -
	a real, already-recycled gunicorn web worker (`ps`/`/proc/<pid>/cwd` caught
	it mid-respawn) had the same wrong (bench-root) CWD, matching multiple real
	`receive_call_recording` failures logged in frappe.log at the exact same
	timestamps as nginx's 500 responses. Gunicorn recycles workers periodically
	(`--max-requests` in config/supervisor.conf) - the long-lived original
	workers all have the correct CWD, but at least one respawned replacement
	did not. Root cause of WHY a respawned worker's CWD would differ isn't
	pinned down yet, but the fix is the same defensive one either way - see
	_ensure_absolute_site_path()'s own docstring for the full mechanism.
	"""
	_ensure_absolute_site_path()

	if not frappe.db.exists("Call Log", call_log_name):
		frappe.throw(f"Call Log {call_log_name} not found", frappe.DoesNotExistError)
	if not frappe.has_permission("Call Log", ptype="read", doc=call_log_name):
		raise frappe.PermissionError

	uploaded = frappe.request.files.get("audio_file")
	if not uploaded:
		frappe.throw("Missing 'audio_file' in the upload.")

	user = frappe.session.user
	content = uploaded.read()

	# REAL ROOT CAUSE, confirmed live (2026-09-29) via a captured traceback (see
	# _ensure_absolute_site_path()'s own history - three prior fix attempts
	# targeted the wrong theory entirely; this was never a relative-path issue):
	# the phone sends `filename` already percent-encoded, e.g.
	# "Call%20Danish_260929_155824.m4a" (confirmed from the real captured
	# exception's own `filename=` value). save_file() writes the physical file
	# under that RAW (still-encoded) name. But core Frappe's own
	# `File.before_insert()` unconditionally does
	# `self.file_url = unquote(self.file_url)` as its very first line - AFTER
	# the file was already written - so it then looks for the DECODED name
	# ("Call Danish_260929_155824.m4a", a real space) via get_content(), which
	# doesn't exist under that name. Confirmed on disk: the orphaned file sits
	# there as literal "...%20..." — write and read were simply using two
	# different filenames. Unquoting here, before save_file() ever runs, makes
	# the written filename match what core will look for later - safe even if
	# a filename was never encoded to begin with (unquoting plain text is a
	# no-op).
	try:
		file_doc = save_file(
			unquote(uploaded.filename) if uploaded.filename else f"{call_log_name}.m4a",
			content,
			"Call Log",
			call_log_name,
			is_private=1,
		)
	except Exception:
		frappe.logger("calltranscribe_diag").error(
			f"save_file failed for call_log_name={call_log_name!r} filename={uploaded.filename!r}\n"
			+ traceback.format_exc()
		)
		raise

	# Raw db.set_value, not a permission-checked save() - same precedent as the
	# rest of this module (e.g. _insert_with_retry's db_set calls): Call Tracker
	# User only has create+if_owner-read on Call Log, no write, and this is a
	# system-tracked status, not user-authored content.
	frappe.db.set_value("Call Log", call_log_name, "custom_transcript_status", "Pending", update_modified=False)

	frappe.enqueue(
		_transcribe_call_recording_job,
		queue="long",
		timeout=TRANSCRIBE_TIMEOUT_SECONDS + 60,
		enqueue_after_commit=True,
		call_log_name=call_log_name,
		file_name=file_doc.name,
		user=user,
	)

	return {"status": "queued", "call_log_name": call_log_name}


def _ensure_absolute_site_path():
	"""Correct frappe.local.site_path to an absolute path before any File API call
	- in BOTH receive_call_recording (web request) and
	_transcribe_call_recording_job (background job). Call it first thing in any
	new code path in this app that touches File content too.

	Mechanism: frappe.init()'s default sites_path="." resolves relative to the
	process's CWD *at init time* (frappe/__init__.py). Every
	frappe.utils.get_site_path()/get_files_path() call - including
	File.get_full_path()/get_content() - is built directly on
	frappe.local.site_path (get_site_base_path() just returns it, read fresh
	every call), with no absolute-path fallback anywhere in that chain.

	Investigated in depth (2026-09-29), and the full picture is messier than a
	single clean root cause:
	  - The RQ long-worker process's real, live CWD was checked directly (via a
	    diagnostic job run for real, and /proc/<pid>/cwd) and is CORRECT
	    (sites/) - contrary to an earlier theory that blamed
	    bench-uat-frappe-long-worker's supervisor `directory=` (the bench root).
	    `bench worker`/`bench execute`/`bench console` all appear to normalize
	    their own CWD to sites/ internally, regardless of supervisor's
	    `directory=` or the invoking shell's cwd.
	  - The REAL confirmed culprit instead: gunicorn recycles web workers
	    periodically (`--max-requests` in config/supervisor.conf). At least one
	    already-recycled worker process was directly caught via /proc/<pid>/cwd
	    with the WRONG cwd (bench root), at the same moment its 5 long-lived
	    siblings all had the correct one - and that matches, timestamp for
	    timestamp, real logged `receive_call_recording` exceptions in
	    logs/frappe.log. Exactly why a respawned worker ends up with a
	    different CWD than its siblings is NOT pinned down - flagged as a real
	    open question, not asserted with false confidence.
	  - Separately, NOT fixed here (out of scope, pre-existing, unrelated to
	    this app): logs/backup.log shows the identical relative-path
	    FileNotFoundError shape going back to 2026-09-23 in this site's
	    scheduled backup job - confirms this general class of bug is real
	    elsewhere on this bench too, independent of anything built today.

	Fixed here, not in frappe core: get_bench_path() is genuinely CWD-independent
	(derived from frappe.__file__'s own on-disk location, not the process's
	working directory), so this correction is safe to make entirely within our
	own app, applied defensively at both call sites rather than relying on any
	one process's CWD being trustworthy.

	TEMPORARY DIAGNOSTIC (2026-09-29): the fix above this docstring shipped
	twice and failed twice for reasons that don't add up from source alone -
	get_bench_path() returning something falsy has no explanation in its own
	code (no monkey-patch found anywhere in this bench, no FRAPPE_BENCH_ROOT
	override, frappe isn't a symlinked/editable install). Rather than guess a
	third time, log the real, live values from whichever worker actually
	answers the next real request, to a dedicated log file - remove this
	block once the real fix is confirmed via a real retry, not before.
	"""
	_diag = frappe.logger("calltranscribe_diag")
	_diag.setLevel("INFO")  # default resolved level here is ERROR - .info() would be silently dropped otherwise
	_diag.info(
		f"pid={os.getpid()} cwd={os.getcwd()!r} frappe.__file__={frappe.__file__!r} "
		f"get_bench_path()={get_bench_path()!r} frappe.local.site={frappe.local.site!r} "
		f"site_path_before={getattr(frappe.local, 'site_path', None)!r}"
	)
	frappe.local.site_path = os.path.join(get_bench_path(), "sites", frappe.local.site)
	_diag.info(f"site_path_after={frappe.local.site_path!r}")


def _transcribe_call_recording_job(call_log_name, file_name, user):
	"""Background job: POST the already-saved recording to the local transcription
	service, then write the result straight onto the Call Log - no second
	round-trip through the phone needed, unlike an earlier attach_call_transcript
	sketch that would have required the phone to fetch and re-submit the text.

	Runs as `user` for reading the File (real caller's own permissions, matching
	_insert_with_retry's convention), but switches to Administrator - same as that
	function's Call Sync Failure write - for the final status/transcript write,
	since this is system-computed output, not something scoped to the caller's own
	permissions.

	Single attempt, no retry loop: unlike the transient DB lock-wait errors
	_insert_with_retry retries for, a failure here (service down, decode error,
	timeout) is not expected to resolve itself moments later, and each attempt
	already costs minutes - a blind retry would just as likely double a genuine
	failure's cost as fix it.
	"""
	frappe.set_user(user)
	try:
		_ensure_absolute_site_path()
		file_doc = frappe.get_doc("File", file_name)
		content = file_doc.get_content()

		response = requests.post(
			TRANSCRIBE_SERVICE_URL,
			files={"audio": (file_doc.file_name, content)},
			data={"lang": "hi", "mode": "ctc"},
			timeout=TRANSCRIBE_TIMEOUT_SECONDS,
		)
		response.raise_for_status()
		result = response.json()
		if "transcript" not in result:
			frappe.throw(f"Unexpected response from transcription service: {result!r}")

		frappe.set_user("Administrator")
		frappe.db.set_value(
			"Call Log",
			call_log_name,
			{
				"custom_transcript": result["transcript"],
				"custom_transcript_status": "Completed",
				"custom_transcript_language": result.get("lang", "hi"),
				"custom_transcript_model": TRANSCRIBE_MODEL_LABEL,
			},
			update_modified=False,
		)
		frappe.db.commit()
	except Exception as e:
		frappe.set_user("Administrator")
		frappe.db.set_value(
			"Call Log",
			call_log_name,
			{"custom_transcript_status": "Failed", "custom_transcript_error": str(e)[:1000]},
			update_modified=False,
		)
		frappe.db.commit()
		frappe.log_error(
			title="splinh call_tracking: transcription failed",
			message=f"call_log_name={call_log_name} file_name={file_name} error={e}",
		)
	finally:
		frappe.set_user("Administrator")
