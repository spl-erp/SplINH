"""The "Duplicate Check" button on the Lead list (2026-10-13).

One whitelisted, read-only method. The salesperson types Organisation, Phone and/or
Email (any one is enough; empty ones are simply not checked, like the guard's own
blank-field rule) before filling a whole Lead form; this answers "does it already
exist?" from two sources and merges them:

  1. duplicate_guard's own probe (duplicate_guard.api.check_duplicates) - the same
     index and matching rules the guard enforces on save (organisation = company /
     customer name, any phone field, email). The probe ignores the guard's master
     `Enabled` switch, so it works while normal Lead saves stay unblocked; it is
     gated only by the guard's three "Check ..." boxes and is only as complete as
     its index (rebuild it before relying on it).
  2. splinh's Phone Lookup table (custom/phone_lookup.py) - complete and kept in
     sync on every Lead/Contact save, so a phone match is never missed because the
     guard's index is stale.

Every hit is resolved to the record a salesperson cares about: a Contact becomes
the Customer/Lead it belongs to, and a Converted Lead becomes its Customer (the
guard's index can still hold rows for Leads converted via ERPNext's raw set_value).

Nothing is ever written. When nothing matches, the client opens the normal new-Lead
form prefilled, so every existing hook and validation runs exactly as before.
"""

import re

import frappe
from frappe import _
from frappe.utils import cstr, validate_email_address

from splinh.custom import phone_lookup
from splinh.custom.phone_normalize import normalize_phone_key

GUARD_APP = "duplicate_guard"
CHECK_TYPES = ("Name", "Phone", "Email")
MAX_MATCHES = 20
DEFAULT_COUNTRY = "India"


@frappe.whitelist(methods=["POST"])
def check(organization=None, phone=None, email=None, country=None):
	frappe.has_permission("Lead", "create", throw=True)

	organization = cstr(organization).strip()
	phone = cstr(phone).strip()
	email = cstr(email).strip()
	country = cstr(country).strip() or None
	if not (organization or phone or email):
		frappe.throw(_("Enter at least one of Organisation, Phone or Email."))
	if email and not validate_email_address(email):
		frappe.throw(_("{0} is not a valid email address.").format(email))
	phone_key = None
	if phone:
		phone_key = phone_key_for(phone, country)
		if not phone_key:
			frappe.throw(
				_("Could not read {0} as one phone number. Enter a single number; add + and the country code for numbers outside {1}.").format(
					phone, DEFAULT_COUNTRY
				)
			)

	warnings = []
	hits = _guard_hits(organization, phone, email, country, warnings)
	if phone_key:
		hits += _phone_lookup_hits(phone_key, warnings)
	matches = merge_hits(hits, _resolve)
	if len(matches) > MAX_MATCHES:
		warnings.append(_("Showing the first {0} of {1} matching records.").format(MAX_MATCHES, len(matches)))
		matches = matches[:MAX_MATCHES]
	for match in matches:
		match.update(_describe(match["doctype"], match["name"]))

	provided = provided_labels(organization, phone, email)
	return {
		"status": "duplicate" if matches else "clean",
		"matches": matches,
		"normalized": {
			"phone": phone_key,
			"email": email.lower() or None,
			"organization": organization or None,
		},
		"checked": [_(label) for label in provided],
		"not_checked": [_(label) for label in INPUT_LABELS if label not in provided],
		"warnings": warnings,
	}


# --------------------------------------------------------------------- inputs
# Which of the three boxes the user filled decides what is checked. Pure helpers so the
# rule is testable without a site.

INPUT_LABELS = ("Organisation", "Phone", "Email")
_LABEL_TYPE = {"Organisation": "Name", "Phone": "Phone", "Email": "Email"}


def provided_labels(organization, phone, email):
	"""["Organisation", "Phone", "Email"] reduced to the ones that have a value."""
	given = {"Organisation": organization, "Phone": phone, "Email": email}
	return [label for label in INPUT_LABELS if cstr(given[label]).strip()]


def provided_types(organization, phone, email):
	"""The guard value types (Name / Phone / Email) the user actually gave."""
	return [_LABEL_TYPE[label] for label in provided_labels(organization, phone, email)]


def guard_values(organization, phone, email, country=None):
	"""What to hand duplicate_guard's probe: only filled fields (the guard ignores blank
	ones anyway, but an empty string must never look like a value)."""
	values = {}
	if cstr(organization).strip():
		values["company_name"] = cstr(organization).strip()
	if cstr(phone).strip():
		values["mobile_no"] = cstr(phone).strip()
	if cstr(email).strip():
		values["email_id"] = cstr(email).strip()
	if values and cstr(country).strip():
		values["country"] = cstr(country).strip()
	return values


# ------------------------------------------------------------------ phone key


def phone_key_for(phone, country=None, isd=None):
	"""The Phone Lookup key for what the user typed. A "+..." number is taken as is;
	a bare number is read as Indian (the site default, via phone_lookup) unless
	another country is chosen, in which case that country's dialling code is used.
	`isd` is only passed by tests; normally it comes from the chosen country."""
	if cstr(phone).strip().startswith("+") or not country or country == DEFAULT_COUNTRY:
		return phone_lookup._normalize(phone)

	if isd is None:
		from frappe.geo.country_info import get_country_info

		isd = get_country_info(country).get("isd")
	code = re.sub(r"\D", "", cstr(isd))
	text = re.sub(r"(?i)(?:ext\.?|[x#;])\s*\d.*$", "", cstr(phone))
	national = re.sub(r"\D", "", text).lstrip("0")
	if not code or not 6 <= len(national) <= 12:
		return None
	return normalize_phone_key("+" + code + national)


# ---------------------------------------------------------------------- hits
# A hit is {"doctype", "name", "matched_on", "value", "source"}: one reason one
# record matched. merge_hits() folds them into one row per resolved record.


def hits_from_guard(rows):
	"""duplicate_guard.api.check_duplicates rows -> hits."""
	return [
		{
			"doctype": row.get("matched_doctype"),
			"name": row.get("matched_name"),
			"matched_on": row.get("value_type"),
			"value": row.get("value"),
			"source": "Duplicate Guard",
		}
		for row in rows or []
		if row.get("matched_doctype") and row.get("matched_name")
	]


def hits_from_phone_lookup(rows, phone_key):
	"""phone_lookup.find_all_by_key rows -> hits."""
	return [
		{
			"doctype": row["doctype"],
			"name": row["name"],
			"matched_on": "Phone",
			"value": phone_key,
			"source": "Phone Lookup",
		}
		for row in rows or []
	]


def merge_hits(hits, resolve):
	"""One row per resolved record, keeping every reason it matched. `resolve(doctype,
	name)` returns (doctype, name, via) - the record to show and, if it was reached
	through another one (a Contact, a converted Lead), a note saying so - or None to
	drop the hit (the record no longer exists). Order: first hit first."""
	merged = {}
	for hit in hits:
		resolved = resolve(hit["doctype"], hit["name"])
		if not resolved:
			continue
		doctype, name, via = resolved
		row = merged.setdefault(
			(doctype, name),
			{"doctype": doctype, "name": name, "matched_on": [], "values": [], "via": [], "sources": []},
		)
		for key, value in (
			("matched_on", hit["matched_on"]),
			("values", hit["value"]),
			("via", via),
			("sources", hit["source"]),
		):
			if value and value not in row[key]:
				row[key].append(value)
	for row in merged.values():
		row["matched_on"].sort(key=lambda t: CHECK_TYPES.index(t) if t in CHECK_TYPES else len(CHECK_TYPES))
	return list(merged.values())


def _guard_hits(organization, phone, email, country, warnings):
	if GUARD_APP not in frappe.get_installed_apps():
		warnings.append(_("Duplicate Guard is not installed: only the phone number was checked."))
		return []

	from duplicate_guard.api import check_duplicates
	from duplicate_guard.core.utils import get_check_types

	# Only the types the user filled count; a Phone that the guard is not checking is not
	# worth a warning - the Phone Lookup table covers phones either way.
	enabled = get_check_types("Lead")
	off = [t for t in provided_types(organization, phone, email) if t not in enabled and t != "Phone"]
	if off:
		warnings.append(
			_("Duplicate Guard is not checking: {0}. Tick the matching boxes in Duplicate Guard Settings.").format(
				", ".join(_(t) for t in off)
			)
		)
	values = guard_values(organization, phone, email, country)
	if not values:
		return []
	try:
		return hits_from_guard(check_duplicates("Lead", values))
	except Exception:
		frappe.log_error(title="splinh Duplicate Check: duplicate_guard probe failed")
		warnings.append(_("The Duplicate Guard check failed (see Error Log): only the phone number was checked."))
		return []


def _phone_lookup_hits(phone_key, warnings):
	if not frappe.db.table_exists("Phone Lookup"):
		warnings.append(_("The Phone Lookup table does not exist yet: run bench migrate."))
		return []
	return hits_from_phone_lookup(phone_lookup.find_all_by_key(phone_key, limit=MAX_MATCHES), phone_key)


# ------------------------------------------------------------ resolve/describe


def _resolve(doctype, name):
	"""Contact -> its Customer/Lead; Converted Lead -> its Customer. None if gone."""
	if not frappe.db.exists(doctype, name):
		return None
	via = None
	if doctype == "Contact":
		party = frappe.db.sql(
			"""
			SELECT link_doctype, link_name FROM `tabDynamic Link`
			WHERE parenttype = 'Contact' AND parent = %s AND link_doctype IN ('Customer', 'Lead')
			ORDER BY link_doctype = 'Customer' DESC, idx
			LIMIT 1
			""",
			(name,),
		)
		if not party or not frappe.db.exists(party[0][0], party[0][1]):
			return doctype, name, None
		via = _("via Contact {0}").format(name)
		doctype, name = party[0]
	if doctype == "Lead" and frappe.db.get_value("Lead", name, "status") == "Converted":
		customer = frappe.db.get_value("Customer", {"lead_name": name}, "name")
		if customer:
			return "Customer", customer, via or _("converted from Lead {0}").format(name)
	return doctype, name, via


def _describe(doctype, name):
	"""Title, status and owner for the popup row."""
	if doctype == "Lead":
		row = frappe.db.get_value(
			"Lead", name, ["company_name", "lead_name", "status", "lead_owner", "owner"], as_dict=True
		)
		return {
			"title": row.company_name or row.lead_name or name,
			"record_status": row.status,
			"record_owner": row.lead_owner or row.owner,
		}
	if doctype == "Customer":
		row = frappe.db.get_value("Customer", name, ["customer_name", "disabled", "owner"], as_dict=True)
		return {
			"title": row.customer_name or name,
			"record_status": _("Disabled") if row.disabled else _("Active"),
			"record_owner": row.owner,
		}
	row = frappe.db.get_value(doctype, name, ["owner"], as_dict=True) or {}
	title = frappe.db.get_value(doctype, name, "full_name") if doctype == "Contact" else None
	return {"title": title or name, "record_status": None, "record_owner": row.get("owner")}
