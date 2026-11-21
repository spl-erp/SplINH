"""Splashjet's side of the Waba (WhatsApp) app: how a customer's phone number finds their Lead or Contact.

Waba never imports splinh. It calls the functions listed under `waba_party_matchers` in hooks.py. This one answers
with the indexed Phone Lookup table, so a WhatsApp sender is matched with one point query (sub-millisecond), the same
rule and priority the call tracker uses (Contact before Lead, then field order, then newest).
"""

from splinh.custom.call_recording_pause import recording_paused_for
from splinh.custom.phone_lookup import find_party_by_phone


def match_party(phone):
	"""{"doctype": "Contact"|"Lead", "name": ...} for an E.164 phone ("+919876543210"), or None."""
	return find_party_by_phone(phone)


def record_owner(party_doctype, party):
	"""{"owner": a user, "language": "Hindi"} for the customer's record; either part may be missing.

	Waba uses the owner to send a conversation to the person who already looks after that customer, and the language to
	pick an agent who speaks it. A Lead's owner is its Lead Owner; a Customer's is its Account Manager; a Contact has no
	owner of its own, so the Customer it belongs to answers."""
	import frappe

	if party_doctype == "Contact":
		link = frappe.db.get_value(
			"Dynamic Link",
			{"parenttype": "Contact", "parent": party, "link_doctype": "Customer"},
			"link_name",
		)
		if not link:
			return {}
		party_doctype, party = "Customer", link
	field = {"Lead": "lead_owner", "Customer": "account_manager"}.get(party_doctype)
	if not field:
		return {}
	owner, language = frappe.db.get_value(party_doctype, party, [field, "language"]) or (None, None)
	enabled = owner and frappe.db.get_value("User", owner, "enabled")
	return {
		"owner": owner if enabled else "",
		"language": (frappe.db.get_value("Language", language, "language_name") if language else "") or "",
	}


def opt_out(party_doctype, party):
	"""True when the record already says "do not contact", so WhatsApp respects what email and calls already respect:
	a Lead's Unsubscribed and Do Not Disturb flags, a Contact's Unsubscribed flag."""
	import frappe

	fields = {"Lead": ("unsubscribed", "custom_unsubscribe_do_not_disturb"), "Contact": ("unsubscribed",)}.get(party_doctype)
	if not fields:
		return False
	meta = frappe.get_meta(party_doctype)
	wanted = [f for f in fields if meta.has_field(f)]
	if not wanted:
		return False
	values = frappe.db.get_value(party_doctype, party, wanted, as_dict=True) or {}
	return any(values.get(f) for f in wanted)


def match_all_parties(phone):
	"""Every Lead and Contact holding this phone number: [{"doctype", "name"}, ...] (Contact first). A person is often both a
	Lead and a Contact, so a STOP must be recorded on all of them, not only on the one a message happened to match."""
	from splinh.custom.phone_lookup import _normalize, find_all_by_key

	return [{"doctype": row["doctype"], "name": row["name"]} for row in find_all_by_key(_normalize(phone))]


# --------------------------------------------------------------------------------------------- WhatsApp voice calls (Waba Call -> Call Log)

CALL_LOG_STATUS = {"Completed": "Completed", "Missed": "No Answer", "No answer": "No Answer", "Cancelled": "No Answer", "Rejected": "Busy", "Failed": "Failed"}


def call_ended(call):
	"""Waba says a WhatsApp voice call finished (or its recording arrived). Queued, because a Call Log insert runs the slow party match and
	retries with rollbacks, which must never share Waba's webhook transaction. Idempotent: the Call Log is named WABA-<call id>."""
	import frappe

	if call.status in ("Ringing", "Answered"):
		return  # not finished yet
	frappe.enqueue(
		"splinh.waba_bridge.create_call_log",
		queue="short",
		call=call.name,
		enqueue_after_commit=True,
		job_id=f"waba-calllog-{call.name}-{1 if call.recording_file else 0}",
		deduplicate=True,
	)


def create_call_log(call):
	"""The job: make the Call Log for a Waba Call (source "WhatsApp API", linked to the Lead / Contact / Customer by the same phone lookup the
	call tracker uses), or attach the recording to the one that exists."""
	import frappe

	from splinh.api import call_tracking as ct

	doc = frappe.get_doc("Waba Call", call)
	name = "WABA-" + doc.call_id
	outgoing = doc.direction == "Outgoing"
	rep = doc.answered_by or doc.started_by or None  # nobody answered (missed / declined): stays with Administrator
	if not frappe.db.exists("Call Log", name):
		digits = (doc.caller_phone or "").lstrip("+")
		phone = f"+{digits}" if digits else None  # same shape as the phone-detected WhatsApp rows
		status = CALL_LOG_STATUS.get(doc.status, "Completed")
		shown = _clean_name(doc.caller_name)
		values = {
			"doctype": "Call Log",
			"id": name,
			"from": None if outgoing else phone,
			"to": phone if outgoing else None,
			"type": "Outgoing" if outgoing else "Incoming",
			"status": status,
			"duration": doc.duration or 0,
			"start_time": doc.started_at,
			"end_time": doc.ended_at,
			"custom_device_id": "waba-api",
			"custom_contact_name": shown,
			"custom_source": "WhatsApp API",
			"custom_call_type": "Voice",
			"custom_detector_confidence": "High",
			"custom_call_summary": f"WhatsApp Voice · {'Outgoing to' if outgoing else 'Incoming from'} {shown or phone or 'unknown number'}" + (f" ({ct._format_duration(doc.duration)})" if status == "Completed" else f" - {status}"),
		}
		if rep:
			# The Employee of the person who took the call (the insert below runs as Administrator, so it cannot find it itself).
			from splinh.custom.call_employee import employee_for_user

			employee, employee_user = employee_for_user(rep)
			if employee:
				values["call_received_by"] = employee
				if employee_user:
					values["employee_user_id"] = employee_user
		# Insert as Administrator so a rep without the Call Tracker User role never loses the call, then hand the row to the rep.
		ct._insert_with_retry(name, values, "Administrator", "waba-api", doc.call_id, {"waba_call": doc.name})
		if frappe.db.exists("Call Log", name):
			frappe.db.set_value("Waba Call", doc.name, "call_log", name, update_modified=False)
			if rep:
				_hand_to(name, rep)
	if doc.recording_file and frappe.db.exists("Call Log", name):
		if recording_paused_for(rep, doc.started_at):
			return  # the rep had "Record Calls" paused when this call began: no audio on the Call Log (Waba keeps its own copy for managers)
		_attach_recording(doc, name)


def _clean_name(value):
	"""A profile name worth showing: one with a letter or digit ('.' or a lone emoji is not a name)."""
	value = " ".join(str(value or "").split())
	return value if any(ch.isalnum() for ch in value) else None


def _hand_to(call_log, user):
	"""Make the rep the Call Log's owner (so their own list and the 'my calls' views show it) and refresh the caller name."""
	import frappe

	from splinh.custom.call_remark_sync import caller_name

	frappe.db.set_value("Call Log", call_log, "owner", user, update_modified=False)
	row = frappe.db.get_value("Call Log", call_log, ["owner", "call_received_by"], as_dict=True)
	name = caller_name(row)
	if name:
		frappe.db.set_value("Call Log", call_log, "custom_caller_name", name, update_modified=False)


def _attach_recording(doc, call_log):
	"""A private copy of the recording on the Call Log (free playback on its form), once."""
	import frappe

	if frappe.db.exists("File", {"attached_to_doctype": "Call Log", "attached_to_name": call_log, "file_name": ["like", f"{doc.call_id}%"]}):
		return
	source = frappe.get_doc("File", {"file_url": doc.recording_file, "attached_to_name": doc.name})
	frappe.get_doc(
		{"doctype": "File", "file_name": source.file_name, "attached_to_doctype": "Call Log", "attached_to_name": call_log, "content": source.get_content(), "is_private": 1}
	).insert(ignore_permissions=True)
