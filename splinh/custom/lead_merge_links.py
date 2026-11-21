"""Lead Merge: keep the records Frappe does not move (2026-11-13). Called from the Lead's rename hooks (hooks.py), so it runs for
Merge Leads (api/lead_merge.py uses Frappe's own merge) and for any Lead rename.

Frappe moves Link fields and the Dynamic Links it knows about. It does not move:
  - tasks (ToDo): when the same person is assigned on both Leads it keeps ONE task per person and deletes the others with their
    text. before_rename copies those texts onto the main Lead as a comment first, so nothing written in a task is lost;
  - the Waba WhatsApp records, when the Waba app is installed (production may not have it): a conversation's `party`, the people
    in a locked Recipient Set and a campaign's recipient rows (plain text), and a notification's reference.
A failure here is logged and never stops the merge."""

import frappe
from frappe.utils import cstr

from splinh.custom import lead_merge as rules


def before_rename(doc, *args, **kwargs):
	try:
		parsed = rules.rename_args(args)
		if doc.doctype != "Lead" or not parsed or not parsed[2]:
			return
		old, new, _merge = parsed
		assigned = lambda name: set(frappe.parse_json(frappe.db.get_value("Lead", name, "_assign")) or [])  # noqa: E731
		common = assigned(old) & assigned(new)
		if not common:
			return
		todos = frappe.get_all(
			"ToDo",
			filters={"owner": ["in", list(common)], "reference_type": "Lead", "reference_name": old},
			fields=["description", "allocated_to", "status", "date"],
		)
		if todos:
			frappe.get_doc("Lead", new).add_comment("Info", rules.todo_note(old, todos))
	except Exception:
		frappe.log_error(title="splinh lead_merge: keeping tasks failed", message=frappe.get_traceback())


def after_rename(doc, *args, **kwargs):
	try:
		parsed = rules.rename_args(args)
		if doc.doctype != "Lead" or not parsed or parsed[0] == parsed[1]:
			return
		old, new, _merge = parsed
		if frappe.db.table_exists("Waba Conversation"):
			_waba(old, new)
	except Exception:
		frappe.log_error(title="splinh lead_merge: following a Lead rename failed", message=frappe.get_traceback())


def _waba(old, new, doctype="Lead"):
	"""Point the Waba records that name `old` (a Lead by default; Contact Merge passes "Contact") at `new`."""
	frappe.db.sql("update `tabWaba Conversation` set party=%s where party_doctype=%s and party=%s", (new, doctype, old))
	for set_name in set(frappe.get_all("Waba Recipient Set Member", filters={"party": old}, pluck="recipient_set")):
		if frappe.db.get_value("Waba Recipient Set", set_name, "party_doctype") != doctype:
			continue
		rows = frappe.get_all("Waba Recipient Set Member", filters={"recipient_set": set_name, "party": ["in", [old, new]]}, fields=["name", "party"])
		repoint, remove = rules.member_plan(rows, old, new)
		for name in repoint:
			frappe.db.set_value("Waba Recipient Set Member", name, "party", new, update_modified=False)
		for name in remove:
			frappe.db.delete("Waba Recipient Set Member", {"name": name})
		if remove:  # the Set's own count of people follows the rows
			included = int(cstr(frappe.db.get_value("Waba Recipient Set", set_name, "included_count") or 0))
			frappe.db.set_value("Waba Recipient Set", set_name, "included_count", max(0, included - len(remove)), update_modified=False)
	campaigns = frappe.get_all("Waba Campaign", filters={"party_doctype": doctype}, pluck="name")
	if campaigns:
		frappe.db.sql("update `tabWaba Campaign Recipient` set party=%s where party=%s and campaign in %s", (new, old, tuple(campaigns)))
	frappe.db.sql("update `tabWaba Notification Log` set reference_name=%s where reference_doctype=%s and reference_name=%s", (new, doctype, old))
	if frappe.db.table_exists("Waba Identity"):  # a stored customer identity (plain text party) follows the record too
		frappe.db.sql("update `tabWaba Identity` set party=%s where party_doctype=%s and party=%s", (new, doctype, old))
