"""A Call Log's remark ("Next Action Remarks", custom_detailed_action) and who made the call.

The Call Log is the single source of truth: the Activity tab of a Lead / Customer / Contact / Opportunity already draws
every linked call as a card (ERPNext's `get_linked_call_logs`), and the "Activity Call Card" Client Scripts
(custom/splinh_setup.py) make that card show the caller, category, stages and remark. So nothing is copied into a Lead
note or a Customer comment any more (an earlier version did; `collect_entries` / `delete_entries` remove what it made).

What is still kept in step here:
  - the standard Call Log `summary` gets the remark (the stock card of any screen without our script shows it);
  - `custom_caller_name`, the name shown on the card: the call owner's full name, else the Employee's name. It is
    stored because the browser only knows the names of users it already loaded (otherwise the card shows an email).

`sync_call_remark` never raises: a failure only writes an Error Log entry and can never fail a review.
"""

import re

import frappe

REMARK_FIELD = "custom_detailed_action"
CALLER_FIELD = "custom_caller_name"
LEAD_REF = "custom_lead_note_ref"  # legacy: "<lead>|<CRM Note row name>" of the note an earlier version created
CUSTOMER_REF = "custom_customer_comment_ref"  # legacy: "<customer>|<Comment name>"
MAX_REMARK = 2000
_CONTROL = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]+")


# ------------------------------------------------------------------ pure rules


def clean_remark(value):
	"""The remark as plain text: trimmed, control characters removed, line breaks kept, capped."""
	if value is None or isinstance(value, (dict, list, tuple, set)):
		return ""
	return _CONTROL.sub("", str(value)).replace("\r\n", "\n").replace("\r", "\n").strip()[:MAX_REMARK]


def parse_ref(ref):
	"""'<target>|<name>' -> (target, name); empty or malformed -> None."""
	if not ref or "|" not in ref:
		return None
	target, _, name = str(ref).rpartition("|")
	return (target, name) if target and name else None


def tidy_name(value):
	"""A person's name with stray spaces removed ('Ankit  Ingle ' -> 'Ankit Ingle')."""
	return " ".join(str(value or "").split())


# --------------------------------------------------------------- Frappe access


def caller_name(call):
	"""Who made the call: the call owner's full name, else the name of the Employee in `call_received_by`, else ''.
	`call` supports .get() (a Call Log document or a dict)."""
	name = ""
	if call.get("owner"):
		name = tidy_name(frappe.db.get_value("User", call.get("owner"), "full_name"))
	if not name and call.get("call_received_by"):
		name = tidy_name(frappe.db.get_value("Employee", call.get("call_received_by"), "employee_name"))
	return name


def fill_caller_name(doc, method=None):
	"""Call Log validate hook: keep `custom_caller_name` filled (new calls from the app and from the ERP)."""
	try:
		name = caller_name(doc)
		if name and doc.get(CALLER_FIELD) != name:
			doc.set(CALLER_FIELD, name)
	except Exception:
		frappe.log_error(title="call_remark_sync: caller name failed", message=frappe.get_traceback())


def _sync_summary(call, remark):
	"""The remark into the standard `summary`. A cleared remark clears it: nothing else writes it."""
	if (call.get("summary") or "") == remark:
		return "none"
	frappe.db.set_value("Call Log", call["name"], "summary", remark, update_modified=False)
	return "set" if remark else "clear"


def sync_call_remark(call_log_name):
	"""Bring the summary and the caller name of this call in line with it. Returns {"summary": action}; never raises."""
	result = {"summary": "none"}
	try:
		fields = ["name", "owner", "call_received_by", REMARK_FIELD, "summary"]
		has_caller_field = frappe.get_meta("Call Log").has_field(CALLER_FIELD)
		if has_caller_field:
			fields.append(CALLER_FIELD)
		call = frappe.db.get_value("Call Log", call_log_name, fields, as_dict=True)
		if not call:
			return result
		result["summary"] = _sync_summary(call, clean_remark(call.get(REMARK_FIELD)))
		if has_caller_field:
			name = caller_name(call)
			if name and call.get(CALLER_FIELD) != name:
				frappe.db.set_value("Call Log", call["name"], CALLER_FIELD, name, update_modified=False)
	except Exception:
		frappe.log_error(
			title="call_remark_sync: could not sync a call remark",
			message=f"{call_log_name}\n{frappe.get_traceback()}",
		)
	return result


def on_call_log_update(doc, method=None):
	"""Call Log on_update hook (edits made in the ERP form): refresh the summary when the remark changed. The mobile
	API writes with set_value (no hooks) and calls sync_call_remark itself."""
	try:
		before = None if doc.is_new() else doc.get_doc_before_save()
		before_remark = clean_remark(before.get(REMARK_FIELD)) if before else ""
		if before_remark != clean_remark(doc.get(REMARK_FIELD)):
			sync_call_remark(doc.name)
	except Exception:
		frappe.log_error(title="call_remark_sync: hook failed", message=frappe.get_traceback())


def backfill_caller_names(fix=False):
	"""Fill `custom_caller_name` on calls that have none. One UPDATE; a dry run only counts. Returns {"calls": n}."""
	where = f"IFNULL(c.`{CALLER_FIELD}`, '') = ''"
	if not fix:
		count = frappe.db.sql(f"SELECT COUNT(*) FROM `tabCall Log` c WHERE {where}")[0][0]
		return {"calls": count}
	frappe.db.sql(
		f"""UPDATE `tabCall Log` c
		LEFT JOIN `tabUser` u ON u.name = c.owner
		LEFT JOIN `tabEmployee` e ON e.name = c.call_received_by
		SET c.`{CALLER_FIELD}` = TRIM(COALESCE(NULLIF(TRIM(u.full_name), ''), NULLIF(TRIM(e.employee_name), ''), ''))
		WHERE {where}"""
	)
	return {"calls": frappe.db.sql("SELECT ROW_COUNT()")[0][0]}


# ------------------------------------------- remove what the earlier version copied


def collect_entries():
	"""Every Lead note / Customer comment an earlier version created, found by the refs stored on the Call Logs.
	Returns [{"kind", "call_log", "target", "entry", "author", "when", "html"}]; "html" is empty when the entry no
	longer exists (deleted by hand). Nothing else is ever listed, so notes written by hand are safe."""
	calls = frappe.db.sql(
		f"""SELECT name, `{LEAD_REF}` AS lead_ref, `{CUSTOMER_REF}` AS customer_ref FROM `tabCall Log`
		WHERE IFNULL(`{LEAD_REF}`, '') <> '' OR IFNULL(`{CUSTOMER_REF}`, '') <> ''
		ORDER BY creation""",
		as_dict=True,
	)
	rows = []
	for call in calls:
		lead = parse_ref(call.lead_ref)
		if lead:
			note = frappe.db.get_value(
				"CRM Note",
				{"name": lead[1], "parent": lead[0], "parenttype": "Lead"},
				["added_by", "added_on", "note"],
				as_dict=True,
			)
			rows.append(
				{
					"kind": "lead note",
					"call_log": call.name,
					"target": lead[0],
					"entry": lead[1],
					"author": note.added_by if note else "",
					"when": note.added_on if note else "",
					"html": note.note if note else "",
				}
			)
		customer = parse_ref(call.customer_ref)
		if customer:
			comment = frappe.db.get_value("Comment", customer[1], ["comment_email", "creation", "content"], as_dict=True)
			rows.append(
				{
					"kind": "customer comment",
					"call_log": call.name,
					"target": customer[0],
					"entry": customer[1],
					"author": comment.comment_email if comment else "",
					"when": comment.creation if comment else "",
					"html": comment.content if comment else "",
				}
			)
	return rows


def delete_entries(entries, log=print):
	"""Delete the entries from collect_entries() (write their backup first) and clear the refs on the Call Logs."""
	done = 0
	for row in entries:
		if row["kind"] == "lead note":
			frappe.db.delete("CRM Note", {"name": row["entry"], "parent": row["target"], "parenttype": "Lead"})
			frappe.db.set_value("Call Log", row["call_log"], LEAD_REF, "", update_modified=False)
		else:
			if frappe.db.exists("Comment", row["entry"]):
				frappe.delete_doc("Comment", row["entry"], ignore_permissions=True, force=True)
			frappe.db.set_value("Call Log", row["call_log"], CUSTOMER_REF, "", update_modified=False)
		done += 1
		if done % 200 == 0:
			frappe.db.commit()  # nosemgrep
			log(f"  ... {done} done")
	frappe.db.commit()  # nosemgrep
	return done
