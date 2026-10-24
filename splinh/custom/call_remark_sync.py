"""Copy a Call Log's remark ("Next Action Remarks", custom_detailed_action) to the party it is about.

Why: reps type a remark on the call, but the next time they call the same Lead or Customer they
need to see what was said. So:
  - a call linked to a LEAD  -> a row in the Lead's Notes (CRM Note child table);
  - a call linked to a CUSTOMER -> a Comment on the Customer.
One entry per call: editing the remark updates it, clearing the remark removes it, moving the call to
another Lead/Customer moves it. Only the remark text is written (the note's author and time show in
the standard UI). Nothing is written for an empty remark or a call linked to neither.

Why plain Python here (not a Server/Client Script): remarks arrive through the mobile API, and the Lead
must NOT be saved: a Lead save runs about ten hooks (SFA target engine, Duplicate Guard, Phone Lookup,
score scripts, assignments) and bumps its modified time. The CRM Note row is written directly and the
Comment through its own doctype.

The two hidden Call Log fields (custom_lead_note_ref, custom_customer_comment_ref, created by
splinh_setup._ensure_call_remark_fields) remember "<target>|<row or comment name>" so edits update the
same entry instead of duplicating it. `sync_call_remark` never raises: a failure only writes an Error Log
entry and can never fail a review.
"""

import re

import frappe
from frappe.utils import escape_html, get_datetime, now_datetime

REMARK_FIELD = "custom_detailed_action"
LEAD_REF = "custom_lead_note_ref"
CUSTOMER_REF = "custom_customer_comment_ref"
MAX_REMARK = 2000
_CONTROL = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]+")


# ------------------------------------------------------------------ pure rules


def clean_remark(value):
	"""The remark as plain text: trimmed, control characters removed, line breaks kept, capped."""
	if value is None or isinstance(value, (dict, list, tuple, set)):
		return ""
	return _CONTROL.sub("", str(value)).replace("\r\n", "\n").replace("\r", "\n").strip()[:MAX_REMARK]


def remark_html(text):
	"""The same markup Frappe's Text Editor stores: escaped text, line breaks as <br>."""
	return '<div class="ql-editor read-mode"><p>{}</p></div>'.format(escape_html(text).replace("\n", "<br>"))


def parse_ref(ref):
	"""'<target>|<name>' -> (target, name); empty or malformed -> None."""
	if not ref or "|" not in ref:
		return None
	target, _, name = str(ref).rpartition("|")
	return (target, name) if target and name else None


def make_ref(target, name):
	return f"{target}|{name}"


def plan_sync(existing_ref, target, remark):
	"""What to do for one side (Lead or Customer). `existing_ref` is the parsed ref or None.

	  none    nothing to do
	  create  no entry yet, remark and target present
	  update  entry exists for this same target
	  delete  entry exists but the remark was cleared or there is no target any more
	  move    entry exists for a DIFFERENT target: delete it, create one on the new target"""
	if not remark or not target:
		return "delete" if existing_ref else "none"
	if not existing_ref:
		return "create"
	return "update" if existing_ref[0] == target else "move"


# --------------------------------------------------------------- Frappe access


def _author_and_time(call):
	author = call.get("custom_reviewed_by") or call.get("owner") or frappe.session.user
	when = call.get("custom_reviewed_at") or now_datetime()
	return author, get_datetime(when)


def _create_lead_note(lead, html, author, when):
	next_idx = frappe.db.sql(
		"select coalesce(max(idx), 0) + 1 from `tabCRM Note` where parent=%s and parenttype='Lead' and parentfield='notes'",
		(lead,),
	)[0][0]
	note = frappe.get_doc(
		{
			"doctype": "CRM Note",
			"parent": lead,
			"parenttype": "Lead",
			"parentfield": "notes",
			"idx": next_idx,
			"note": html,
			"added_by": author,
			"added_on": when,
		}
	)
	note.db_insert()  # the Lead itself is never saved: no Lead hooks, no modified bump
	return note.name


def _sync_lead(call, remark, author, when):
	existing = parse_ref(call.get(LEAD_REF))
	target = call.get("custom_lead") or None
	if target and not frappe.db.exists("Lead", target):
		frappe.log_error(
			title="call_remark_sync: Lead missing", message=f"{call.get('name')} points at missing Lead {target}"
		)
		target = None
	action = plan_sync(existing, target, remark)
	html = remark_html(remark) if remark else ""

	if action in ("delete", "move"):
		frappe.db.delete("CRM Note", {"name": existing[1], "parent": existing[0], "parenttype": "Lead"})
		call_ref = None
	else:
		call_ref = call.get(LEAD_REF)

	if action == "update":
		row = frappe.db.get_value("CRM Note", {"name": existing[1], "parent": existing[0]}, ["note"], as_dict=True)
		if not row:
			action = "create"  # someone deleted the note by hand: put it back
		elif row.note != html:
			frappe.db.set_value(
				"CRM Note", existing[1], {"note": html, "added_by": author, "added_on": when}, update_modified=False
			)
	if action in ("create", "move"):
		call_ref = make_ref(target, _create_lead_note(target, html, author, when))
	if action in ("create", "move", "delete") and call_ref != call.get(LEAD_REF):
		frappe.db.set_value("Call Log", call["name"], LEAD_REF, call_ref or "", update_modified=False)
	return action


def _sync_customer(call, remark, author, when):
	existing = parse_ref(call.get(CUSTOMER_REF))
	target = call.get("customer") or None
	if target and not frappe.db.exists("Customer", target):
		frappe.log_error(
			title="call_remark_sync: Customer missing", message=f"{call.get('name')} points at missing Customer {target}"
		)
		target = None
	action = plan_sync(existing, target, remark)
	html = remark_html(remark) if remark else ""

	if action in ("delete", "move"):
		if frappe.db.exists("Comment", existing[1]):
			frappe.delete_doc("Comment", existing[1], ignore_permissions=True, force=True)
		call_ref = None
	else:
		call_ref = call.get(CUSTOMER_REF)

	if action == "update":
		if not frappe.db.exists("Comment", existing[1]):
			action = "create"
		else:
			comment = frappe.get_doc("Comment", existing[1])
			if comment.content != html:
				comment.content = html
				comment.save(ignore_permissions=True)
	if action in ("create", "move"):
		comment = frappe.get_doc(
			{
				"doctype": "Comment",
				"comment_type": "Comment",
				"reference_doctype": "Customer",
				"reference_name": target,
				"comment_email": author,
				"comment_by": frappe.db.get_value("User", author, "full_name") or author,
				"content": html,
			}
		).insert(ignore_permissions=True)
		call_ref = make_ref(target, comment.name)
	if action in ("create", "move", "delete") and call_ref != call.get(CUSTOMER_REF):
		frappe.db.set_value("Call Log", call["name"], CUSTOMER_REF, call_ref or "", update_modified=False)
	return action


def sync_call_remark(call_log_name):
	"""Bring the Lead note and the Customer comment of this call in line with its remark.
	Returns {"lead": action, "customer": action}; never raises."""
	result = {"lead": "none", "customer": "none"}
	try:
		call = frappe.db.get_value(
			"Call Log",
			call_log_name,
			[
				"name",
				"owner",
				REMARK_FIELD,
				"custom_lead",
				"customer",
				"custom_reviewed_by",
				"custom_reviewed_at",
				LEAD_REF,
				CUSTOMER_REF,
			],
			as_dict=True,
		)
		if not call:
			return result
		remark = clean_remark(call.get(REMARK_FIELD))
		author, when = _author_and_time(call)
		result["lead"] = _sync_lead(call, remark, author, when)
		result["customer"] = _sync_customer(call, remark, author, when)
	except Exception:
		frappe.log_error(
			title="call_remark_sync: could not sync a call remark",
			message=f"{call_log_name}\n{frappe.get_traceback()}",
		)
	return result


def on_call_log_update(doc, method=None):
	"""Call Log on_update hook (edits made in the ERP form): sync only when the remark or the
	Lead/Customer link changed. The mobile API writes with set_value (no hooks) and calls
	sync_call_remark itself."""
	try:
		before = None if doc.is_new() else doc.get_doc_before_save()
		if before is None:
			changed = bool(clean_remark(doc.get(REMARK_FIELD)))
		else:
			changed = (
				clean_remark(before.get(REMARK_FIELD)) != clean_remark(doc.get(REMARK_FIELD))
				or (before.get("custom_lead") or "") != (doc.get("custom_lead") or "")
				or (before.get("customer") or "") != (doc.get("customer") or "")
			)
		if changed:
			sync_call_remark(doc.name)
	except Exception:
		frappe.log_error(title="call_remark_sync: hook failed", message=frappe.get_traceback())


def backfill(fix=False, limit=None, log=print):
	"""Create the notes/comments for existing remarks. Only calls that have a remark, a Lead or Customer,
	and no ref yet. Oldest review first. Returns {"rows": [...], "lead": n, "customer": n}."""
	calls = frappe.db.sql(
		f"""SELECT name FROM `tabCall Log`
		WHERE IFNULL(`{REMARK_FIELD}`, '') <> ''
		  AND (
		    (IFNULL(custom_lead, '') <> '' AND IFNULL(`{LEAD_REF}`, '') = '')
		    OR (IFNULL(customer, '') <> '' AND IFNULL(`{CUSTOMER_REF}`, '') = '')
		  )
		ORDER BY COALESCE(custom_reviewed_at, modified), creation
		{"LIMIT %s" if limit else ""}""",
		(int(limit),) if limit else (),
		pluck="name",
	)
	rows, lead, customer = [], 0, 0
	for name in calls:
		call = frappe.db.get_value(
			"Call Log", name, [REMARK_FIELD, "custom_lead", "customer", "custom_reviewed_by", "owner"], as_dict=True
		)
		remark = clean_remark(call.get(REMARK_FIELD))
		for kind, target in (("lead", call.custom_lead), ("customer", call.customer)):
			if not target or not remark:
				continue
			rows.append(
				{
					"call_log": name,
					"target_type": kind,
					"target": target,
					"author": call.custom_reviewed_by or call.owner,
					"remark": remark,
				}
			)
			lead += kind == "lead"
			customer += kind == "customer"
		if fix:
			sync_call_remark(name)
			if lead + customer and (lead + customer) % 200 == 0:
				frappe.db.commit()  # nosemgrep
				log(f"  ... {lead + customer} done")
	if fix:
		frappe.db.commit()  # nosemgrep
	return {"rows": rows, "lead": lead, "customer": customer}
