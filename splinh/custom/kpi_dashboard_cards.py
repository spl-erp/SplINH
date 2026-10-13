"""
Custom whitelisted aggregation for the "KPI Dashboard" workspace
(splinh_setup.py's ensure_kpi_dashboard()).

Why this exists: Cold Call, Followup Call, Visit Existing and Visit New are not
real stored columns - they are the exact same derived metrics as the Sales KPI
Entry doctype's virtual fields (splinh.splinh.doctype.sales_kpi_entry.
sales_kpi_entry), computed from a LEFT JOIN against Lead.status (for Cold/
Followup) or a bucketing of Start-End Meeting.id_type (for Visit Existing/New).
A native "Document Type" Number Card can only COUNT/SUM a single real column
with a plain AND-list of filters - there is no way to express a join or a
CASE-bucketed GROUP BY through that mechanism (same reasoning as
call_log_dashboard.py's get_company_calls_count, the one precedent for this
pattern already proven live on this bench).

Every function here is deliberately a thin wrapper around the SAME constants
the doctype controller uses (LEAD_COLD_STATUSES, VISIT_EXISTING_ID_TYPES,
VISIT_NEW_ID_TYPES) - imported, not re-typed - so the Workspace card and the
per-salesperson Web Form number can never silently drift apart.
"""

import frappe

from splinh.splinh.doctype.sales_kpi_entry.sales_kpi_entry import (
	LEAD_COLD_STATUSES,
	VISIT_EXISTING_ID_TYPES,
	VISIT_NEW_ID_TYPES,
)


def _extract_bound(filters, fieldname):
	"""Pull the >= and < bounds for `fieldname` out of a Number Card's AND-list
	filters (frappe.dashboard_utils.get_all_filters() - every entry ANDed, each
	a [doctype, fieldname, operator, value] or [fieldname, operator, value]
	triple). Lets the Workspace card's own "this week" bound (set once in
	splinh_setup.py, editable there like any other Number Card filter) drive
	these custom aggregates exactly like it drives every native card.
	"""
	filters = frappe.parse_json(filters) if isinstance(filters, str) else (filters or [])
	start = end = None
	for row in filters:
		if len(row) == 4:
			_, fname, op, value = row
		elif len(row) == 3:
			fname, op, value = row
		else:
			continue
		if fname != fieldname:
			continue
		if op in (">=", ">"):
			start = value
		elif op in ("<", "<="):
			end = value
	return start, end


def _call_counts(filters):
	conditions = ["cl.type = 'Outgoing'"]
	values = []
	start, end = _extract_bound(filters, "start_time")
	if start:
		conditions.append("cl.start_time >= %s")
		values.append(start)
	if end:
		conditions.append("cl.start_time < %s")
		values.append(end)
	rows = frappe.db.sql(
		f"""
		SELECT cl.custom_lead AS lead, l.status AS lead_status
		FROM `tabCall Log` cl
		LEFT JOIN `tabLead` l ON l.name = cl.custom_lead
		WHERE {" AND ".join(conditions)}
		""",
		values,
		as_dict=True,
	)
	cold = followup = 0
	for row in rows:
		if row.lead and row.lead_status in LEAD_COLD_STATUSES:
			cold += 1
		else:
			followup += 1
	return cold, followup


@frappe.whitelist()
def get_cold_call_count(filters=None):
	return _call_counts(filters)[0]


@frappe.whitelist()
def get_followup_call_count(filters=None):
	return _call_counts(filters)[1]


def _visit_counts(filters):
	conditions = ["1=1"]
	values = []
	start, end = _extract_bound(filters, "start_meeting")
	if start:
		conditions.append("start_meeting >= %s")
		values.append(start)
	if end:
		conditions.append("start_meeting < %s")
		values.append(end)
	rows = frappe.db.sql(
		f"""SELECT id_type, COUNT(*) AS cnt FROM `tabStart-End Meeting`
		WHERE {" AND ".join(conditions)} GROUP BY id_type""",
		values,
		as_dict=True,
	)
	existing = new = 0
	for row in rows:
		if row.id_type in VISIT_EXISTING_ID_TYPES:
			existing += row.cnt
		elif row.id_type in VISIT_NEW_ID_TYPES:
			new += row.cnt
	return existing, new


@frappe.whitelist()
def get_visit_existing_count(filters=None):
	return _visit_counts(filters)[0]


@frappe.whitelist()
def get_visit_new_count(filters=None):
	return _visit_counts(filters)[1]


@frappe.whitelist()
def get_meeting_duration_minutes(filters=None):
	conditions = ["end_meeting IS NOT NULL"]
	values = []
	start, end = _extract_bound(filters, "start_meeting")
	if start:
		conditions.append("start_meeting >= %s")
		values.append(start)
	if end:
		conditions.append("start_meeting < %s")
		values.append(end)
	row = frappe.db.sql(
		f"""SELECT SUM(TIMESTAMPDIFF(SECOND, start_meeting, end_meeting))
		FROM `tabStart-End Meeting` WHERE {" AND ".join(conditions)}""",
		values,
	)
	total_seconds = row[0][0] if row and row[0][0] is not None else 0
	return round(total_seconds / 60.0, 1)
