"""Splashjet's side of the Waba (WhatsApp) app: how a customer's phone number finds their Lead or Contact.

Waba never imports splinh. It calls the functions listed under `waba_party_matchers` in hooks.py. This one answers
with the indexed Phone Lookup table, so a WhatsApp sender is matched with one point query (sub-millisecond), the same
rule and priority the call tracker uses (Contact before Lead, then field order, then newest).
"""

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
