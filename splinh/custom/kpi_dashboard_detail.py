"""
Runtime query logic for the "KPI Dashboard - Detailed" Desk page
(splinh.splinh.page.kpi_dashboard_detail, route /app/kpi-dashboard-detail).

Layer 3 of the KPI Capturing project (2026-10-01) - same 3-layer shape and
same security posture as the proven Call Monitoring precedent
(call_monitoring_detail.py, read its docstring first if anything here is
unclear): ONE whitelisted method (`get_dashboard_data`), and the very first
thing it does is an explicit role check via `frappe.only_for` - nothing below
that line runs for a session without System Manager or Sales Manager.

Grain: Sales KPI Entry is one row per (salesperson, day) - the drill-down
table here mirrors that exact grain, but computed directly from the SOURCE
tables (Lead, Call Log, Start-End Meeting, Sales KPI Entry) via bulk GROUP BY
SQL, not by instantiating a SalesKPIEntry doc per row. The doctype's own
virtual-field properties (splinh.splinh.doctype.sales_kpi_entry) are
correct but scoped to ONE user+date at a time (by design, for the Web Form) -
recomputing them one row at a time for a whole team over a date range would
be an N+1 query storm. The bucketing/formula definitions themselves are NOT
re-derived here: every SQL fragment below matches the doctype's properties
and kpi_dashboard_cards.py's Number Card methods exactly (LEAD_COLD_STATUSES,
VISIT_EXISTING_ID_TYPES, VISIT_NEW_ID_TYPES, imported, not re-typed).

Four independent GROUP BY queries (one per source table), merged in Python by
(user, date) key - a day where a salesperson has, say, calls but no visits
still gets one row, with the missing side's columns at 0. Manual Entry
columns come along for free from the same Sales KPI Entry row joined in (0 if
no Web Form entry exists yet for that day - a real, meaningful "not filled
in" state, not an error).
"""

import frappe
from frappe.utils import cint, flt, get_datetime

from splinh.splinh.doctype.sales_kpi_entry.sales_kpi_entry import (
	LEAD_COLD_STATUSES,
	VISIT_EXISTING_ID_TYPES,
	VISIT_NEW_ID_TYPES,
)

ALLOWED_ROLES = ["Sales Manager", "System Manager"]

MAX_PAGE_LENGTH = 200
DEFAULT_PAGE_LENGTH = 50


def _check_access():
	frappe.only_for(ALLOWED_ROLES, message=True)


def _date_bounds(filters):
	from_date = filters.get("from_date")
	to_date = filters.get("to_date")
	start = get_datetime(from_date) if from_date else None
	end = get_datetime(f"{to_date} 23:59:59") if to_date else None
	return start, end


def _date_conditions(column, start, end, values):
	conditions = []
	if start:
		conditions.append(f"{column} >= %s")
		values.append(start)
	if end:
		conditions.append(f"{column} <= %s")
		values.append(end)
	return conditions


@frappe.whitelist()
def get_dashboard_data(filters=None, start=0, page_length=DEFAULT_PAGE_LENGTH):
	"""Everything the KPI Dashboard - Detailed page needs for one filter
	combination: the summary tiles, the 3 breakdown-chart datasets, and a page
	of (user, date) drill-down rows.
	"""
	_check_access()

	filters = frappe.parse_json(filters) if isinstance(filters, str) else (filters or {})
	start = cint(start)
	page_length = min(cint(page_length) or DEFAULT_PAGE_LENGTH, MAX_PAGE_LENGTH)
	employee = filters.get("employee") or None
	date_start, date_end = _date_bounds(filters)

	lead_rows, lead_totals = _lead_data(employee, date_start, date_end)
	call_rows, call_totals = _call_data(employee, date_start, date_end)
	visit_rows, visit_totals = _visit_data(employee, date_start, date_end)
	manual_rows = _manual_data(employee, date_start, date_end)

	merged = {}

	def _get(user, date):
		key = (user, date)
		if key not in merged:
			merged[key] = {
				"user": user, "date": date,
				"lead_generation_count": 0, "lead_score_sum": 0.0, "lead_score_n": 0,
				"lead_qualification_count": 0, "lead_qualification_score_sum": 0.0, "lead_qualification_score_n": 0,
				"cold_call_count": 0, "followup_call_count": 0, "whatsapp_call_count": 0,
				"visit_existing_count": 0, "visit_new_count": 0, "meeting_duration_minutes": 0.0,
				"office_meeting_duration": 0.0, "training_duration": 0.0,
				"customer_complaints_count": 0, "customer_complaints_duration": 0.0,
				"reporting_duration": 0.0, "google_meet_count": 0, "google_meet_duration": 0.0,
				"whatsapp_chat_count": 0, "whatsapp_chat_duration": 0.0,
			}
		return merged[key]

	for row in lead_rows:
		r = _get(row.owner, row.day)
		r["lead_generation_count"] = cint(row.generated)
		r["lead_score_sum"] = flt(row.score_sum)
		r["lead_score_n"] = cint(row.score_n)
		r["lead_qualification_count"] = cint(row.get("lead_qualification_count", 0))
		r["lead_qualification_score_sum"] = flt(row.get("lead_qualification_score_sum", 0))
		r["lead_qualification_score_n"] = cint(row.get("lead_qualification_score_n", 0))

	for row in call_rows:
		r = _get(row.owner, row.day)
		r["cold_call_count"] = cint(row.cold)
		r["followup_call_count"] = cint(row.followup)
		r["whatsapp_call_count"] = cint(row.whatsapp)

	for row in visit_rows:
		r = _get(row.owner, row.day)
		r["visit_existing_count"] = cint(row.existing)
		r["visit_new_count"] = cint(row.new)
		r["meeting_duration_minutes"] = flt(row.duration_minutes)

	for row in manual_rows:
		r = _get(row.user, row.entry_date)
		r["office_meeting_duration"] = flt(row.office_meeting_duration)
		r["training_duration"] = flt(row.training_duration)
		r["customer_complaints_count"] = cint(row.customer_complaints_count)
		r["customer_complaints_duration"] = flt(row.customer_complaints_duration)
		r["reporting_duration"] = flt(row.reporting_duration)
		r["google_meet_count"] = cint(row.google_meet_count)
		r["google_meet_duration"] = flt(row.google_meet_duration)
		r["whatsapp_chat_count"] = cint(row.whatsapp_chat_count)
		r["whatsapp_chat_duration"] = flt(row.whatsapp_chat_duration)

	all_rows = sorted(merged.values(), key=lambda r: (r["date"], r["user"]), reverse=True)
	for r in all_rows:
		r["lead_score"] = round(r["lead_score_sum"] / r["lead_score_n"], 2) if r["lead_score_n"] else 0.0
		r["lead_qualification_score"] = (
			round(r["lead_qualification_score_sum"] / r["lead_qualification_score_n"], 2)
			if r["lead_qualification_score_n"] else 0.0
		)
		del r["lead_score_sum"], r["lead_score_n"], r["lead_qualification_score_sum"], r["lead_qualification_score_n"]

	total_rows = len(all_rows)
	page_rows = all_rows[start : start + page_length]

	employees = frappe.get_all(
		"Has Role", filters={"role": ["in", ["Sales User", "SFA Sales User"]], "parenttype": "User"},
		fields=["parent as user"], distinct=True, order_by="parent",
	)

	return {
		"summary": {
			"lead_generation_count": lead_totals["generated"],
			"lead_score": round(lead_totals["score_sum"] / lead_totals["score_n"], 2) if lead_totals["score_n"] else 0.0,
			"lead_qualification_count": lead_totals["qualified"],
			"lead_qualification_score": (
				round(lead_totals["qual_score_sum"] / lead_totals["qual_score_n"], 2) if lead_totals["qual_score_n"] else 0.0
			),
			"cold_call_count": call_totals["cold"],
			"followup_call_count": call_totals["followup"],
			"whatsapp_call_count": call_totals["whatsapp"],
			"visit_existing_count": visit_totals["existing"],
			"visit_new_count": visit_totals["new"],
			"meeting_duration_minutes": round(visit_totals["duration_minutes"], 1),
		},
		"charts": {
			"lead_trend": _lead_trend(employee, date_start, date_end),
			"call_bucket": [
				{"label": "Cold Call", "value": call_totals["cold"]},
				{"label": "Followup Call", "value": call_totals["followup"]},
			],
			"visit_bucket": [
				{"label": "Visit Existing", "value": visit_totals["existing"]},
				{"label": "Visit New", "value": visit_totals["new"]},
			],
		},
		"rows": page_rows,
		"total_rows": total_rows,
		"filter_options": {"employees": employees},
	}


def _lead_data(employee, start, end):
	values = []
	conditions = ["lead_owner IS NOT NULL", "lead_owner != ''"]
	if employee:
		conditions.append("lead_owner = %s")
		values.append(employee)
	gen_conditions = list(conditions) + _date_conditions("creation", start, end, values)
	rows = frappe.db.sql(
		f"""SELECT lead_owner AS owner, DATE(creation) AS day, COUNT(*) AS generated,
			SUM((IFNULL(custom_export_profile_percent, 0) + IFNULL(CAST(custom_profile_percent AS DECIMAL(10,2)), 0)) / 2) AS score_sum,
			COUNT(*) AS score_n
		FROM `tabLead` WHERE {" AND ".join(gen_conditions)}
		GROUP BY lead_owner, DATE(creation)""",
		values, as_dict=True,
	)

	values2 = []
	qual_conditions = list(conditions) + _date_conditions("custom_lead_score_updated_on", start, end, values2)
	qual_rows = frappe.db.sql(
		f"""SELECT lead_owner AS owner, DATE(custom_lead_score_updated_on) AS day, COUNT(*) AS qualified,
			SUM((IFNULL(custom_export_profile_percent, 0) + IFNULL(CAST(custom_profile_percent AS DECIMAL(10,2)), 0)) / 2) AS qual_score_sum,
			COUNT(*) AS qual_score_n
		FROM `tabLead` WHERE custom_lead_score_updated_on IS NOT NULL AND {" AND ".join(qual_conditions)}
		GROUP BY lead_owner, DATE(custom_lead_score_updated_on)""",
		values2, as_dict=True,
	)

	# Fold qualification rows into the same per-(owner, day) row list the
	# caller merges by - a day can have generation, qualification, both or
	# neither, so this is a dict merge keyed the same way _get() does above.
	by_key = {(r.owner, r.day): r for r in rows}
	for qr in qual_rows:
		key = (qr.owner, qr.day)
		if key in by_key:
			by_key[key]["lead_qualification_count"] = qr.qualified
			by_key[key]["lead_qualification_score_sum"] = qr.qual_score_sum
			by_key[key]["lead_qualification_score_n"] = qr.qual_score_n
		else:
			by_key[key] = frappe._dict(
				owner=qr.owner, day=qr.day, generated=0, score_sum=0, score_n=0,
				lead_qualification_count=qr.qualified,
				lead_qualification_score_sum=qr.qual_score_sum, lead_qualification_score_n=qr.qual_score_n,
			)
	merged_rows = list(by_key.values())
	for r in merged_rows:
		r.setdefault("lead_qualification_count", 0)
		r.setdefault("lead_qualification_score_sum", 0)
		r.setdefault("lead_qualification_score_n", 0)

	totals = {
		"generated": sum(cint(r.generated) for r in rows),
		"score_sum": sum(flt(r.score_sum) for r in rows),
		"score_n": sum(cint(r.score_n) for r in rows),
		"qualified": sum(cint(r.qualified) for r in qual_rows),
		"qual_score_sum": sum(flt(r.qual_score_sum) for r in qual_rows),
		"qual_score_n": sum(cint(r.qual_score_n) for r in qual_rows),
	}
	return merged_rows, totals


def _lead_trend(employee, start, end):
	values = []
	conditions = ["lead_owner IS NOT NULL", "lead_owner != ''"]
	if employee:
		conditions.append("lead_owner = %s")
		values.append(employee)
	conditions += _date_conditions("creation", start, end, values)
	rows = frappe.db.sql(
		f"""SELECT DATE(creation) AS day, COUNT(*) AS value FROM `tabLead`
		WHERE {" AND ".join(conditions)} GROUP BY day ORDER BY day ASC""",
		values, as_dict=True,
	)
	return rows


def _call_data(employee, start, end):
	values = ["Outgoing"]
	conditions = ["cl.type = %s"]
	if employee:
		conditions.append("cl.owner = %s")
		values.append(employee)
	conditions += _date_conditions("cl.start_time", start, end, values)
	rows = frappe.db.sql(
		f"""SELECT cl.owner AS owner, DATE(cl.start_time) AS day,
			l.status AS lead_status, cl.custom_lead AS lead, cl.custom_source AS source
		FROM `tabCall Log` cl LEFT JOIN `tabLead` l ON l.name = cl.custom_lead
		WHERE {" AND ".join(conditions)}""",
		values, as_dict=True,
	)
	by_key = {}
	totals = {"cold": 0, "followup": 0, "whatsapp": 0}
	for r in rows:
		key = (r.owner, r.day)
		agg = by_key.setdefault(key, frappe._dict(owner=r.owner, day=r.day, cold=0, followup=0, whatsapp=0))
		if r.lead and r.lead_status in LEAD_COLD_STATUSES:
			agg.cold += 1
			totals["cold"] += 1
		else:
			agg.followup += 1
			totals["followup"] += 1
		if r.source == "WhatsApp":
			agg.whatsapp += 1
			totals["whatsapp"] += 1
	return list(by_key.values()), totals


def _visit_data(employee, start, end):
	values = []
	conditions = ["1=1"]
	if employee:
		conditions.append("owner = %s")
		values.append(employee)
	conditions += _date_conditions("start_meeting", start, end, values)
	rows = frappe.db.sql(
		f"""SELECT owner, DATE(start_meeting) AS day, id_type,
			end_meeting, start_meeting
		FROM `tabStart-End Meeting` WHERE {" AND ".join(conditions)}""",
		values, as_dict=True,
	)
	by_key = {}
	totals = {"existing": 0, "new": 0, "duration_minutes": 0.0}
	for r in rows:
		key = (r.owner, r.day)
		agg = by_key.setdefault(key, frappe._dict(owner=r.owner, day=r.day, existing=0, new=0, duration_minutes=0.0))
		if r.id_type in VISIT_EXISTING_ID_TYPES:
			agg.existing += 1
			totals["existing"] += 1
		elif r.id_type in VISIT_NEW_ID_TYPES:
			agg.new += 1
			totals["new"] += 1
		if r.end_meeting:
			minutes = (get_datetime(r.end_meeting) - get_datetime(r.start_meeting)).total_seconds() / 60.0
			agg.duration_minutes += minutes
			totals["duration_minutes"] += minutes
	return list(by_key.values()), totals


def _manual_data(employee, start, end):
	values = []
	conditions = ["1=1"]
	if employee:
		conditions.append("user = %s")
		values.append(employee)
	conditions += _date_conditions("entry_date", start, end, values)
	return frappe.db.sql(
		f"""SELECT user, entry_date,
			office_meeting_duration, training_duration,
			customer_complaints_count, customer_complaints_duration,
			reporting_duration, google_meet_count, google_meet_duration,
			whatsapp_chat_count, whatsapp_chat_duration
		FROM `tabSales KPI Entry` WHERE {" AND ".join(conditions)}""",
		values, as_dict=True,
	)
