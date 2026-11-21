"""Contact Merge: keep the records Frappe does not move (2026-11-19). Called from the Contact's rename hooks (hooks.py), so it runs for
Merge Contacts (api/contact_merge.py uses Frappe's own merge, then renames the survivor) and for any Contact rename.

Frappe moves Link fields and the Dynamic Links it knows about. It does not move:
  - tasks (ToDo): when the same person is assigned on both Contacts it keeps ONE task per person and deletes the others with their
    text. before_rename copies those texts onto the main Contact as a comment first, so nothing written in a task is lost;
  - the Waba WhatsApp records, when the Waba app is installed: a conversation's `party`, a stored customer identity, the people in a
    locked Recipient Set and a campaign's recipient rows (plain text), and a notification's reference (same repair as for Leads);
  - the Phone Lookup index (rows are named after the Contact): the old name's rows go and the new name's rows are rebuilt.
A failure here is logged and never stops the merge."""

import frappe

from splinh.custom import contact_merge as rules
from splinh.custom.lead_merge_links import _waba


def before_rename(doc, *args, **kwargs):
	try:
		parsed = rules.rename_args(args)
		if doc.doctype != "Contact" or not parsed or not parsed[2]:
			return
		old, new, _merge = parsed
		assigned = lambda name: set(frappe.parse_json(frappe.db.get_value("Contact", name, "_assign")) or [])  # noqa: E731
		common = assigned(old) & assigned(new)
		if not common:
			return
		todos = frappe.get_all(
			"ToDo",
			filters={"owner": ["in", list(common)], "reference_type": "Contact", "reference_name": old},
			fields=["description", "allocated_to", "status", "date"],
		)
		if todos:
			frappe.get_doc("Contact", new).add_comment("Info", rules.todo_note(old, todos))
	except Exception:
		frappe.log_error(title="splinh contact_merge: keeping tasks failed", message=frappe.get_traceback())


def after_rename(doc, *args, **kwargs):
	try:
		parsed = rules.rename_args(args)
		if doc.doctype != "Contact" or not parsed or parsed[0] == parsed[1]:
			return
		old, new, _merge = parsed
		if frappe.db.table_exists("Waba Conversation"):
			_waba(old, new, "Contact")
	except Exception:
		frappe.log_error(title="splinh contact_merge: following a Contact rename failed", message=frappe.get_traceback())
	try:
		parsed = rules.rename_args(args)
		if doc.doctype != "Contact" or not parsed or parsed[0] == parsed[1]:
			return
		old, new, _merge = parsed
		frappe.db.sql("delete from `tabPhone Lookup` where source_doctype = 'Contact' and source_name = %s", (old,))
		from splinh.custom import phone_lookup

		phone_lookup.sync_document(frappe.get_doc("Contact", new))
	except Exception:
		frappe.log_error(title="splinh contact_merge: rebuilding the phone index failed", message=frappe.get_traceback())
