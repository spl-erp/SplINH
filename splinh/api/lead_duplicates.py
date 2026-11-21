"""Possible duplicates of one Lead (2026-11-13): the banner on the Lead form that offers Merge Leads.

One whitelisted, read-only method. It lists the OTHER Leads that share a phone number (Phone Lookup, the same index the Duplicate
Check uses), the email address or the company name with the Lead being looked at. Only Leads the user may read are returned, so
SFA team visibility still applies. Nothing is written. The rules are in custom/lead_merge.py."""

import frappe
from frappe import _
from frappe.utils import cstr

from splinh.custom import lead_merge as rules
from splinh.custom import phone_lookup

PHONE_FIELDS = ("phone", "mobile_no", "whatsapp_no")


@frappe.whitelist(methods=["GET", "POST"])
def similar_leads(lead=None):
	lead = cstr(lead).strip()
	if not lead:
		frappe.throw(_("No Lead was given."))
	frappe.has_permission("Lead", "read", doc=lead, throw=True)
	doc = frappe.db.get_value("Lead", lead, ["company_name", "email_id", *PHONE_FIELDS], as_dict=True)
	if not doc:
		frappe.throw(_("Lead {0} does not exist.").format(lead), frappe.DoesNotExistError)

	keys = []
	for field in PHONE_FIELDS:  # the same number in two fields is looked up once
		key = phone_lookup._normalize(doc.get(field)) if doc.get(field) else None
		if key and key not in keys:
			keys.append(key)
	by_phone = [row["name"] for key in keys for row in phone_lookup.find_all_by_key(key) if row["doctype"] == "Lead"]
	by_email = []
	email = cstr(doc.email_id).strip().lower()
	if email:
		by_email = frappe.get_all("Lead", filters={"email_id": email, "name": ["!=", lead]}, pluck="name", limit_page_length=rules.SIMILAR_LIMIT)
	by_company = _company_hits(doc.company_name, lead)

	found = rules.similar_leads(lead, by_phone, by_email, by_company, limit=rules.SIMILAR_LIMIT * 2)
	rows = []
	for item in found:  # the Lead must be readable by this user (SFA visibility)
		if not frappe.has_permission("Lead", "read", doc=item["name"]):
			continue
		row = frappe.db.get_value("Lead", item["name"], ["company_name", "lead_name", "status", "lead_owner", "owner"], as_dict=True)
		rows.append(
			{
				"name": item["name"],
				"title": row.company_name or row.lead_name or item["name"],
				"status": row.status,
				"owner": row.lead_owner or row.owner,
				"matched_on": item["matched_on"],
			}
		)
	return {"lead": lead, "matches": rows[: rules.SIMILAR_LIMIT]}


def _company_hits(company_name, lead):
	"""Other Leads with the same company name, through Duplicate Guard's own name index (the one the Duplicate Check uses, the
	same normalisation). Lead.company_name has no database index, so a direct lookup would scan every Lead; when the guard is not
	installed this reason is simply left out."""
	try:
		from duplicate_guard.core.normalizer import normalize_name
	except ImportError:
		return []
	key = normalize_name(company_name) if company_name else None
	if not key:
		return []
	return frappe.db.sql_list(
		"select reference_name from `tabDuplicate Index` where value_type = 'Name' and normalized_value = %s "
		"and reference_doctype = 'Lead' and reference_name != %s limit %s",
		(key, lead, rules.SIMILAR_LIMIT * 3),
	)
