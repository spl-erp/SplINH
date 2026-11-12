"""Lead Merge endpoints (2026-11-05): the server side of Lead list > Actions > Merge Leads.

Two whitelisted methods; the rules live in custom/lead_merge.py.

  get_merge_preview(names)   read-only: the comparison the dialog shows (every offered field with each
                             Lead's value), what is connected to each Lead, and a `modified` stamp per Lead.
  merge_leads(names, main, choices, stamps)
                             the merge, in ONE transaction (any failure rolls everything back):
                               1. write permission on every Lead (the same check as editing it), lock them
                                  and refuse if one changed since the dialog was opened;
                               2. write a Lead Merge Log row with a full copy of every Lead that goes away;
                               3. Frappe's own merge (frappe.model.rename_doc, merge=True) moves every link,
                                  Dynamic Link (Contacts, Addresses, Call Logs), comment, ToDo and file of a
                                  merged-away Lead to the main Lead and deletes it;
                               4. the chosen / appended values and the combined child rows are saved on the
                                  main Lead, as the user, so Lead's own validate and hooks run (Phone Lookup,
                                  SFA). This comes AFTER the merge: Lead.validate refuses an email_id that
                                  another Lead still has.

`rebuild_search=False` below is essential: the default would queue a global-search rebuild of all Leads.
"""

import json

import frappe
from frappe import _
from frappe.model.rename_doc import get_link_fields, rename_doc
from frappe.utils import cstr, now_datetime

from splinh.custom import lead_merge as lm

LEAD = "Lead"
LOG = "Lead Merge Log"


def _as_json(value, default):
	if value in (None, ""):
		return default
	if isinstance(value, str):
		try:
			return json.loads(value)
		except ValueError:
			frappe.throw(_("Invalid request."))
	return value


def _fail(error):
	"""A MergeError as a normal validation message (HTTP 417), naming the row when there is one."""
	frappe.throw(_(error.message), title=_("Merge Leads"))


def _check_permissions(names):
	for n in names:
		frappe.has_permission(LEAD, "write", doc=n, throw=True)


def _meta_parts():
	"""(offered fields, {table fieldname: value columns}) for the Lead the current user may write."""
	meta = frappe.get_meta(LEAD)
	offered = lm.offered_fields(meta.fields, write_levels=meta.get_permlevel_access("write"))
	tables = {
		df.fieldname: lm.child_columns(frappe.get_meta(df.options).fields)
		for df in meta.get_table_fields()
	}
	return offered, tables


def _load(names):
	docs = {n: frappe.get_doc(LEAD, n) for n in names}
	return docs, {n: d.as_dict() for n, d in docs.items()}


def _counts(name):
	"""What is connected to one Lead (for the dialog badges)."""
	dynamic = lambda parenttype: frappe.db.count(  # noqa: E731
		"Dynamic Link", {"parenttype": parenttype, "link_doctype": LEAD, "link_name": name}
	)
	return {
		"calls": dynamic("Call Log"),
		"contacts": dynamic("Contact"),
		"addresses": dynamic("Address"),
		"customers": frappe.get_all("Customer", filters={"lead_name": name}, pluck="name"),
	}


def _link_counts(name):
	"""{doctype.field: rows pointing at this Lead} over every Link field, plus Dynamic Links - for the log."""
	out = {}
	for lf in get_link_fields(LEAD):
		if lf.get("issingle"):
			continue
		try:
			count = frappe.db.count(lf["parent"], {lf["fieldname"]: name})
		except Exception:
			count = None  # a table that cannot be counted must not stop the merge; the log shows null
		if count != 0:
			out["{0}.{1}".format(lf["parent"], lf["fieldname"])] = count
	for parenttype, count in frappe.db.sql(
		"select parenttype, count(*) from `tabDynamic Link` where link_doctype=%s and link_name=%s group by parenttype",
		(LEAD, name),
	):
		out["Dynamic Link in {0}".format(parenttype)] = count
	return out


@frappe.whitelist(methods=["POST"])
def get_merge_preview(names=None):
	names = [cstr(n) for n in _as_json(names, [])]
	try:
		lm.validate_selection(names, names[0] if names else "")
	except lm.MergeError as e:
		_fail(e)
	_check_permissions(names)

	offered, _tables = _meta_parts()
	docs, dicts = _load(names)
	rows = lm.build_rows(offered, dicts, names)

	leads = []
	for n in names:
		d, counts = docs[n], _counts(n)
		leads.append(
			{
				"name": n,
				"title": d.company_name or d.lead_name or n,
				"status": d.status,
				"owner": d.lead_owner or d.owner,
				"creation": d.creation,
				"modified": cstr(d.modified),
				"notes": len(d.get("notes") or []),
				**counts,
				"weight": counts["calls"] + counts["contacts"] + counts["addresses"] + 50 * len(counts["customers"]),
			}
		)
	return {
		"names": names,
		"leads": leads,
		"rows": rows,
		"stamps": {x["name"]: x["modified"] for x in leads},
		"suggested_main": lm.suggest_main(leads),
	}


@frappe.whitelist(methods=["POST"])
def merge_leads(names=None, main=None, choices=None, stamps=None):
	names = [cstr(n) for n in _as_json(names, [])]
	choices = _as_json(choices, {})
	stamps = _as_json(stamps, {})
	main = cstr(main)
	try:
		lm.validate_selection(names, main)
	except lm.MergeError as e:
		_fail(e)
	others = [n for n in names if n != main]
	_check_permissions(names)

	# Lock in a fixed order (call-insert jobs and SFA saves touch Leads too), then require the Leads to be
	# exactly what the dialog showed.
	locked = frappe.db.sql(
		"select name, modified from `tabLead` where name in %(names)s order by name for update",
		{"names": names},
		as_dict=True,
	)
	try:
		lm.check_stamps(names, stamps, {r.name: cstr(r.modified) for r in locked})
		offered, tables = _meta_parts()
		docs, dicts = _load(names)
		updates = lm.compute_updates(offered, dicts, names, main, choices)
		child_rows = lm.combine_child_rows(tables, dicts, names, main)
	except lm.MergeError as e:
		_fail(e)

	changes = {
		"fields": {f: {"before": dicts[main].get(f), "after": v} for f, v in updates.items()},
		"child_rows_added": child_rows,
	}
	log = frappe.get_doc(
		{
			"doctype": LOG,
			"main_lead": main,
			"merged_leads": ", ".join(others),
			"merged_by": frappe.session.user,
			"merged_on": now_datetime(),
			"changes": frappe.as_json(changes),
			"field_choices": frappe.as_json(choices),
			"link_counts": frappe.as_json({n: _link_counts(n) for n in others}),
			"snapshot": frappe.as_json({n: dicts[n] for n in others}),
		}
	).insert(ignore_permissions=True)

	for other in others:
		rename_doc(
			LEAD,
			other,
			main,
			merge=True,
			ignore_permissions=True,  # write permission on both Leads was checked above
			show_alert=False,
			rebuild_search=False,
		)

	main_doc = frappe.get_doc(LEAD, main)
	main_doc.update(updates)
	for table, rows in child_rows.items():
		for row in rows:
			main_doc.append(table, row)
	main_doc.save()

	# Lead's own rules run on that save (e.g. a Lead that now has a Customer becomes "Converted" whatever
	# status was chosen). Record what was really stored, and tell the dialog which fields differ from the
	# choice, so nobody is surprised.
	overridden = {}
	for fieldname, chosen in updates.items():
		stored = main_doc.get(fieldname)
		changes["fields"][fieldname]["chosen"] = chosen
		changes["fields"][fieldname]["after"] = stored
		if lm.norm(stored) != lm.norm(chosen):
			overridden[fieldname] = stored
	frappe.db.set_value(LOG, log.name, "changes", frappe.as_json(changes), update_modified=False)

	return {
		"main": main,
		"merged": others,
		"changed_fields": sorted(updates),
		"overridden": overridden,
		"child_rows_added": {t: len(r) for t, r in child_rows.items()},
		"log": log.name,
	}
