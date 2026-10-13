"""
Layer 2 of the KPI Capturing project (2026-10-01): an Insights workbook that
drills down from the Layer 1 Workspace's Number Cards/Charts
(splinh_setup.py's ensure_kpi_dashboard()) into any combination of filters,
down to individual Lead/Call Log/Visit/manual-entry rows.

Same conventions as the proven precedent, `call_monitoring_insights.py` (read
that module's docstring first if this one is unclear) - Insights v3's "Site
DB" data source and the three source tables below are already synced as
Insights Table v3 rows (confirmed live before writing this), so this module
only authors the workbook's own documents (Insights Workbook / Query v3 /
Chart v3 / Dashboard v3), via plain frappe.get_doc(...).insert() against this
site's own DB, following the insights-workbook-cli skill's hard rules (queries
stay per-row, no bare `filter` - only `filter_group`, no `sql`/`code`
operations, dashboard filters route by query name + column).

Four base queries, one per source table, because an Insights Query v3 can
only have one `source` table per pipeline - these metrics span three
genuinely different tables, plus the Sales KPI Entry doctype's own manual
fields:
  - "Lead" - Lead Generation / Lead Score / Lead Qualification (rows 1-4).
  - "Call Log" - Cold Call vs Followup Call + WhatsApp Calling, via a LEFT JOIN
    to Lead.status (same bucketing as kpi_dashboard_cards.py / the doctype's
    own cold_call_count/followup_call_count properties - not re-derived here,
    restated in ibis expression form since a builder query can't import a
    Python tuple).
  - "Visits" - Visit Existing vs New + Meeting Duration, from Start-End
    Meeting.id_type (same bucketing as VISIT_EXISTING_ID_TYPES/
    VISIT_NEW_ID_TYPES).
  - "Manual Entries" - a thin, mutate-free pass-through of Sales KPI Entry's
    stored (non-virtual) fields, for management to browse the raw manual logs
    (Office Meeting/Training/Complaints/Reporting/Google Meet/WhatsApp Chat/
    Other Activity) ad hoc - no bucketing needed, these are already plain
    stored numbers.

Idempotent by "create once, never touch again" - same reasoning as
call_monitoring_insights.py: once "KPI Dashboard" exists as an Insights
Workbook title, this returns immediately on every later run, so a person's
hand edits in the Insights UI are never silently overwritten.
"""

import json

import frappe

WORKBOOK_TITLE = "KPI Dashboard"
INSIGHTS_DATA_SOURCE = "Site DB"
KPI_DASHBOARD_WORKSPACE = "KPI Dashboard"

REQUIRED_TABLES = ["tabLead", "tabCall Log", "tabStart-End Meeting", "tabSales KPI Entry"]


def ensure_kpi_dashboard_insights():
	if frappe.db.exists("Insights Workbook", {"title": WORKBOOK_TITLE}):
		_ensure_insights_shortcut_on_workspace()
		return

	if not frappe.db.exists("Insights Data Source v3", INSIGHTS_DATA_SOURCE):
		frappe.log_error(
			title="splinh_setup: Insights Site DB source missing",
			message=f"No '{INSIGHTS_DATA_SOURCE}' Insights Data Source v3 - "
			"KPI Dashboard Insights workbook not created.",
		)
		return
	for table in REQUIRED_TABLES:
		if not frappe.db.exists("Insights Table v3", {"table": table, "data_source": INSIGHTS_DATA_SOURCE}):
			frappe.log_error(
				title="splinh_setup: KPI source table not synced into Insights",
				message=f"No Insights Table v3 row for {table} against '{INSIGHTS_DATA_SOURCE}' - "
				"KPI Dashboard Insights workbook not created.",
			)
			return

	_build_kpi_workbook()
	_ensure_insights_shortcut_on_workspace()


def _new_query_doc(workbook_name, title, operations):
	doc = frappe.get_doc(
		{
			"doctype": "Insights Query v3",
			"workbook": workbook_name,
			"title": title,
			"use_live_connection": 1,
			"is_builder_query": 1,
			"is_native_query": 0,
			"is_script_query": 0,
			"operations": json.dumps(operations),
			"sort_order": 0,
		}
	)
	doc.insert(ignore_permissions=True)
	return doc


def _new_chart_doc(workbook_name, title, query_name, chart_type, config):
	doc = frappe.get_doc(
		{
			"doctype": "Insights Chart v3",
			"workbook": workbook_name,
			"title": title,
			"query": query_name,
			"chart_type": chart_type,
			"config": json.dumps(config),
			"sort_order": 0,
		}
	)
	doc.insert(ignore_permissions=True)
	return doc


def _col(dimension_name, column_name, data_type, granularity=None):
	d = {"dimension_name": dimension_name, "column_name": column_name, "data_type": data_type}
	if granularity:
		d["granularity"] = granularity
	return d


def _count_measure(measure_name="Count"):
	return {"measure_name": measure_name, "column_name": "count", "data_type": "Integer", "aggregation": "count"}


def _sum_measure(measure_name, column_name, data_type="Integer"):
	return {"measure_name": measure_name, "column_name": column_name, "data_type": data_type, "aggregation": "sum"}


def _expr_measure(measure_name, expression, data_type="Integer"):
	return {
		"measure_name": measure_name,
		"data_type": data_type,
		"expression": {"type": "expression", "expression": expression},
	}


EMPTY_FILTERS = {"logical_operator": "And", "filters": []}


def _build_kpi_workbook():
	workbook = frappe.get_doc({"doctype": "Insights Workbook", "title": WORKBOOK_TITLE})
	workbook.insert(ignore_permissions=True)
	workbook_name = workbook.name

	# ---- Query 1: Lead (rows 1-4 - Lead Generation / Score / Qualification) ----
	lead_ops = [
		{"type": "source", "table": {"type": "table", "data_source": INSIGHTS_DATA_SOURCE, "table_name": "tabLead"}},
		{
			"type": "select",
			"column_names": [
				"name", "lead_owner", "creation", "status",
				"custom_export_profile_percent", "custom_profile_percent", "custom_lead_score_updated_on",
			],
		},
		{
			# Same formula as sales_kpi_entry.py's lead_score property: a missing
			# half is treated as 0 (COALESCE(x, 0)), NOT substituted with the
			# other half's value - those are two very different numbers.
			# custom_profile_percent is a Data (text) Custom Field, not numeric
			# (same gotcha already hit and fixed in splinh_setup.py's
			# _ensure_lead_score_updated_on_field() stamping logic) - try_cast
			# it to float64 first, or ibis refuses to coalesce a string column
			# against a numeric literal at all (confirmed live: IbisTypeError,
			# "Cannot compute precedence for `string(140)` and `int8` types").
			"type": "mutate",
			"new_name": "lead_score",
			"data_type": "Decimal",
			"expression": {
				"type": "expression",
				"expression": "(if_null(custom_export_profile_percent.try_cast('float64'), 0) + if_null(custom_profile_percent.try_cast('float64'), 0)) / 2",
			},
		},
		{
			"type": "mutate",
			"new_name": "is_qualified",
			"data_type": "Integer",
			"expression": {"type": "expression", "expression": "one_if(is_set(custom_lead_score_updated_on))"},
		},
	]
	lead_query = _new_query_doc(workbook_name, "Lead", lead_ops)

	# ---- Query 2: Call Log, Cold vs Followup + WhatsApp (rows 5, 7, 10) ----
	# LEFT JOIN to Lead for `status` - the same cutoff as
	# kpi_dashboard_cards.py/sales_kpi_entry.py's LEAD_COLD_STATUSES, restated
	# here in ibis expression form (a builder query can't import a Python
	# tuple). Call Log's OWN `status` column (Completed/Ringing/...) is
	# deliberately left out of `select` below so there is only one `status`
	# column in this pipeline - the joined Lead's - no ambiguity to resolve.
	calls_ops = [
		{"type": "source", "table": {"type": "table", "data_source": INSIGHTS_DATA_SOURCE, "table_name": "tabCall Log"}},
		{
			"type": "select",
			"column_names": ["name", "start_time", "type", "custom_source", "custom_lead", "owner", "duration"],
		},
		{
			"type": "filter_group",
			"logical_operator": "And",
			"filters": [{"type": "filter", "column": {"type": "column", "column_name": "type"}, "operator": "=", "value": "Outgoing"}],
		},
		{
			"type": "join",
			"join_type": "left",
			"table": {"type": "table", "data_source": INSIGHTS_DATA_SOURCE, "table_name": "tabLead"},
			"select_columns": [{"type": "column", "column_name": "status"}],
			"join_condition": {
				"left_column": {"type": "column", "column_name": "custom_lead"},
				"right_column": {"type": "column", "column_name": "name"},
			},
		},
		{
			"type": "mutate",
			"new_name": "call_bucket",
			"data_type": "String",
			"expression": {
				"type": "expression",
				"expression": "if_else(is_in(status, 'Lead', 'Open', 'Replied', 'Interested'), 'Cold Call', 'Followup Call')",
			},
		},
		{
			"type": "mutate",
			"new_name": "is_whatsapp",
			"data_type": "Integer",
			"expression": {"type": "expression", "expression": "one_if(custom_source == 'WhatsApp')"},
		},
	]
	calls_query = _new_query_doc(workbook_name, "Call Log", calls_ops)

	# ---- Query 3: Visits (rows 15-17 - Visit Existing/New + Meeting Duration) ----
	visits_ops = [
		{
			"type": "source",
			"table": {"type": "table", "data_source": INSIGHTS_DATA_SOURCE, "table_name": "tabStart-End Meeting"},
		},
		{"type": "select", "column_names": ["name", "owner", "start_meeting", "end_meeting", "id_type"]},
		{
			"type": "mutate",
			"new_name": "visit_bucket",
			"data_type": "String",
			"expression": {
				"type": "expression",
				"expression": "cases((is_in(id_type, 'Customer', 'Opportunity'), 'Visit Existing'), (id_type == 'Lead', 'Visit New'), else_='Other')",
			},
		},
		{
			# NOT date_diff: it casts both sides to pure DATE (drops time-of-day
			# entirely) and doesn't even support 'minute' as a unit - confirmed
			# live this silently produced 0.0 for every row. time_diff casts to
			# TIME instead (keeps time-of-day, drops the date) and does support
			# 'minute' - correct here since a site visit never spans midnight.
			"type": "mutate",
			"new_name": "duration_minutes",
			"data_type": "Decimal",
			"expression": {
				"type": "expression",
				"expression": "if_else(is_set(end_meeting), time_diff(end_meeting, start_meeting, 'minute'), literal(0))",
			},
		},
	]
	visits_query = _new_query_doc(workbook_name, "Visits", visits_ops)

	# ---- Query 4: Manual Entries - thin pass-through, no bucketing needed ----
	manual_ops = [
		{
			"type": "source",
			"table": {"type": "table", "data_source": INSIGHTS_DATA_SOURCE, "table_name": "tabSales KPI Entry"},
		},
		{
			"type": "select",
			"column_names": [
				"user", "entry_date",
				"office_meeting_duration", "training_duration",
				"customer_complaints_count", "customer_complaints_duration",
				"reporting_duration",
				"google_meet_count", "google_meet_duration",
				"whatsapp_chat_count", "whatsapp_chat_duration",
				"other_activity_type", "other_activity_count", "other_activity_duration",
			],
		},
	]
	manual_query = _new_query_doc(workbook_name, "Manual Entries", manual_ops)

	# ---- Charts ----
	lead_metrics = _new_chart_doc(
		workbook_name, "Lead - Key Metrics", lead_query.name, "Number",
		{
			"number_columns": [
				_count_measure("Lead Generation"),
				{"measure_name": "Avg Lead Score", "data_type": "Decimal", "expression": {"type": "expression", "expression": "avg(lead_score)"}},
				_sum_measure("Lead Qualification", "is_qualified"),
				{
					"measure_name": "Avg Qualification Score", "data_type": "Decimal",
					"expression": {"type": "expression", "expression": "avg(lead_score, where=is_set(custom_lead_score_updated_on))"},
				},
			],
			"number_formats": {}, "number_column_options": [{}, {}, {}, {}], "filters": EMPTY_FILTERS,
		},
	)
	lead_trend = _new_chart_doc(
		workbook_name, "Lead Generation, Per Day", lead_query.name, "Line",
		{
			"x_axis": {"dimension": _col("creation", "creation", "Datetime", granularity="day")},
			"y_axis": {"series": [{"measure": _count_measure("Leads")}], "show_data_labels": False},
			"order_by": [{"column": {"type": "column", "column_name": "creation"}, "direction": "asc"}],
			"limit": 366, "filters": EMPTY_FILTERS,
		},
	)
	lead_table = _new_chart_doc(
		workbook_name, "Lead Detail", lead_query.name, "Table",
		{
			"rows": [
				_col("creation", "creation", "Datetime"), _col("lead_owner", "lead_owner", "String"),
				_col("status", "status", "String"), _col("lead_score", "lead_score", "Decimal"),
				_col("custom_lead_score_updated_on", "custom_lead_score_updated_on", "Datetime"),
				_col("name", "name", "String"),
			],
			"columns": [], "values": [_count_measure("Row Count")],
			"order_by": [{"column": {"type": "column", "column_name": "creation"}, "direction": "desc"}],
			"limit": 1000, "show_row_totals": False, "filters": EMPTY_FILTERS,
		},
	)

	calls_metrics = _new_chart_doc(
		workbook_name, "Calls - Key Metrics", calls_query.name, "Number",
		{
			"number_columns": [
				_expr_measure("Cold Calls", "count_if(call_bucket == 'Cold Call')"),
				_expr_measure("Followup Calls", "count_if(call_bucket == 'Followup Call')"),
				_sum_measure("WhatsApp Calls", "is_whatsapp"),
				_count_measure("Total Outgoing Calls"),
			],
			"number_formats": {}, "number_column_options": [{}, {}, {}, {}], "filters": EMPTY_FILTERS,
		},
	)
	calls_bucket_chart = _new_chart_doc(
		workbook_name, "Cold vs Followup Call", calls_query.name, "Donut",
		{"label_column": _col("call_bucket", "call_bucket", "String"), "value_column": _count_measure(), "max_slices": 8},
	)
	calls_trend = _new_chart_doc(
		workbook_name, "Outgoing Calls, Per Day", calls_query.name, "Line",
		{
			"x_axis": {"dimension": _col("start_time", "start_time", "Datetime", granularity="day")},
			"y_axis": {"series": [{"measure": _count_measure("Calls")}], "show_data_labels": False},
			"order_by": [{"column": {"type": "column", "column_name": "start_time"}, "direction": "asc"}],
			"limit": 366, "filters": EMPTY_FILTERS,
		},
	)
	calls_table = _new_chart_doc(
		workbook_name, "Call Detail", calls_query.name, "Table",
		{
			"rows": [
				_col("start_time", "start_time", "Datetime"), _col("call_bucket", "call_bucket", "String"),
				_col("custom_source", "custom_source", "String"), _col("owner", "owner", "String"),
				_col("custom_lead", "custom_lead", "String"), _col("name", "name", "String"),
			],
			"columns": [], "values": [_count_measure("Row Count")],
			"order_by": [{"column": {"type": "column", "column_name": "start_time"}, "direction": "desc"}],
			"limit": 1000, "show_row_totals": False, "filters": EMPTY_FILTERS,
		},
	)

	visits_metrics = _new_chart_doc(
		workbook_name, "Visits - Key Metrics", visits_query.name, "Number",
		{
			"number_columns": [
				_expr_measure("Visit Existing", "count_if(visit_bucket == 'Visit Existing')"),
				_expr_measure("Visit New", "count_if(visit_bucket == 'Visit New')"),
				{"measure_name": "Total Meeting Minutes", "data_type": "Decimal", "expression": {"type": "expression", "expression": "sum(duration_minutes)"}},
			],
			"number_formats": {}, "number_column_options": [{}, {}, {}], "filters": EMPTY_FILTERS,
		},
	)
	visits_bucket_chart = _new_chart_doc(
		workbook_name, "Visit Existing vs New", visits_query.name, "Donut",
		{"label_column": _col("visit_bucket", "visit_bucket", "String"), "value_column": _count_measure(), "max_slices": 8},
	)
	visits_trend = _new_chart_doc(
		workbook_name, "Visits, Per Day", visits_query.name, "Line",
		{
			"x_axis": {"dimension": _col("start_meeting", "start_meeting", "Datetime", granularity="day")},
			"y_axis": {"series": [{"measure": _count_measure("Visits")}], "show_data_labels": False},
			"order_by": [{"column": {"type": "column", "column_name": "start_meeting"}, "direction": "asc"}],
			"limit": 366, "filters": EMPTY_FILTERS,
		},
	)
	visits_table = _new_chart_doc(
		workbook_name, "Visit Detail", visits_query.name, "Table",
		{
			"rows": [
				_col("start_meeting", "start_meeting", "Datetime"), _col("visit_bucket", "visit_bucket", "String"),
				_col("owner", "owner", "String"), _col("duration_minutes", "duration_minutes", "Decimal"),
				_col("name", "name", "String"),
			],
			"columns": [], "values": [_count_measure("Row Count")],
			"order_by": [{"column": {"type": "column", "column_name": "start_meeting"}, "direction": "desc"}],
			"limit": 1000, "show_row_totals": False, "filters": EMPTY_FILTERS,
		},
	)

	manual_metrics = _new_chart_doc(
		workbook_name, "Manual Entries - Key Metrics", manual_query.name, "Number",
		{
			"number_columns": [
				_sum_measure("Office Meeting (min)", "office_meeting_duration", "Decimal"),
				_sum_measure("Training (min)", "training_duration", "Decimal"),
				_sum_measure("Google Meet (No.)", "google_meet_count"),
				_sum_measure("WhatsApp Chat (No.)", "whatsapp_chat_count"),
			],
			"number_formats": {}, "number_column_options": [{}, {}, {}, {}], "filters": EMPTY_FILTERS,
		},
	)
	manual_table = _new_chart_doc(
		workbook_name, "Manual Entry Detail", manual_query.name, "Table",
		{
			"rows": [
				_col("entry_date", "entry_date", "Date"), _col("user", "user", "String"),
				_col("office_meeting_duration", "office_meeting_duration", "Decimal"),
				_col("training_duration", "training_duration", "Decimal"),
				_col("customer_complaints_count", "customer_complaints_count", "Integer"),
				_col("google_meet_count", "google_meet_count", "Integer"),
				_col("whatsapp_chat_count", "whatsapp_chat_count", "Integer"),
			],
			"columns": [], "values": [_count_measure("Row Count")],
			"order_by": [{"column": {"type": "column", "column_name": "entry_date"}, "direction": "desc"}],
			"limit": 1000, "show_row_totals": False, "filters": EMPTY_FILTERS,
		},
	)

	# ---- Dashboard - two cross-query filters (Date Range, Employee), every
	# chart laid out below its own query's section. ----
	date_links = {
		**{c.name: f"`{lead_query.name}`.`creation`" for c in (lead_metrics, lead_trend, lead_table)},
		**{c.name: f"`{calls_query.name}`.`start_time`" for c in (calls_metrics, calls_bucket_chart, calls_trend, calls_table)},
		**{c.name: f"`{visits_query.name}`.`start_meeting`" for c in (visits_metrics, visits_bucket_chart, visits_trend, visits_table)},
		**{c.name: f"`{manual_query.name}`.`entry_date`" for c in (manual_metrics, manual_table)},
	}
	employee_links = {
		**{c.name: f"`{lead_query.name}`.`lead_owner`" for c in (lead_metrics, lead_trend, lead_table)},
		**{c.name: f"`{calls_query.name}`.`owner`" for c in (calls_metrics, calls_bucket_chart, calls_trend, calls_table)},
		**{c.name: f"`{visits_query.name}`.`owner`" for c in (visits_metrics, visits_bucket_chart, visits_trend, visits_table)},
		**{c.name: f"`{manual_query.name}`.`user`" for c in (manual_metrics, manual_table)},
	}

	items = [
		{"type": "filter", "filter_name": "Date Range", "filter_type": "Date", "links": date_links,
			"icon": "calendar", "layout": {"i": "filter-date", "x": 0, "y": 0, "w": 6, "h": 2}},
		{"type": "filter", "filter_name": "Employee", "filter_type": "String", "links": employee_links,
			"layout": {"i": "filter-employee", "x": 6, "y": 0, "w": 6, "h": 2}},
	]

	y = 2

	def _add_header(text):
		nonlocal y
		items.append({"type": "text", "text": f"## {text}", "layout": {"i": f"hdr-{text}", "x": 0, "y": y, "w": 20, "h": 2}})
		y += 2

	def _add_metrics_row(chart_doc, readings):
		nonlocal y
		for i, reading in enumerate(readings):
			items.append({"type": "chart", "chart": chart_doc.name, "reading": reading,
				"layout": {"i": f"{chart_doc.name}-{i}", "x": (i % 5) * 4, "y": y, "w": 4, "h": 4}})
		y += 4

	def _add_two_up(left, right):
		nonlocal y
		items.append({"type": "chart", "chart": left.name, "layout": {"i": f"{left.name}-c", "x": 0, "y": y, "w": 10, "h": 20}})
		items.append({"type": "chart", "chart": right.name, "layout": {"i": f"{right.name}-c", "x": 10, "y": y, "w": 10, "h": 20}})
		y += 20

	def _add_full(chart_doc, h=24):
		nonlocal y
		items.append({"type": "chart", "chart": chart_doc.name, "layout": {"i": f"{chart_doc.name}-c", "x": 0, "y": y, "w": 20, "h": h}})
		y += h

	_add_header("Lead Generation / Score / Qualification")
	_add_metrics_row(lead_metrics, ["Lead Generation", "Avg Lead Score", "Lead Qualification", "Avg Qualification Score"])
	_add_two_up(lead_trend, lead_table)

	_add_header("Cold Call / Followup Call / WhatsApp Calling")
	_add_metrics_row(calls_metrics, ["Cold Calls", "Followup Calls", "WhatsApp Calls", "Total Outgoing Calls"])
	_add_two_up(calls_bucket_chart, calls_trend)
	_add_full(calls_table)

	_add_header("Visit Existing / New / Meeting Duration")
	_add_metrics_row(visits_metrics, ["Visit Existing", "Visit New", "Total Meeting Minutes"])
	_add_two_up(visits_bucket_chart, visits_trend)
	_add_full(visits_table)

	_add_header("Manual Entries")
	_add_metrics_row(manual_metrics, ["Office Meeting (min)", "Training (min)", "Google Meet (No.)", "WhatsApp Chat (No.)"])
	_add_full(manual_table)

	dashboard = frappe.get_doc(
		{"doctype": "Insights Dashboard v3", "workbook": workbook_name, "title": WORKBOOK_TITLE, "items": json.dumps(items)}
	)
	dashboard.insert(ignore_permissions=True)
	return workbook_name, dashboard.name


def _ensure_insights_shortcut_on_workspace():
	"""One click from the Layer 1 Workspace to the Layer 2 Insights dashboard -
	same additive-only pattern as call_monitoring_insights.py's version."""
	if not frappe.db.exists("Workspace", KPI_DASHBOARD_WORKSPACE):
		return
	workbook_name = frappe.db.get_value("Insights Workbook", {"title": WORKBOOK_TITLE}, "name")
	if not workbook_name:
		return
	dashboard_name = frappe.db.get_value("Insights Dashboard v3", {"workbook": workbook_name}, "name")
	if not dashboard_name:
		return

	route = f"/insights/workbook/{workbook_name}/dashboard/{dashboard_name}"
	shortcut_label = "KPI Dashboard (Insights)"

	workspace = frappe.get_doc("Workspace", KPI_DASHBOARD_WORKSPACE)
	existing_shortcut_labels = {row.label for row in workspace.shortcuts}
	changed = False
	if shortcut_label not in existing_shortcut_labels:
		workspace.append("shortcuts", {"type": "URL", "url": route, "label": shortcut_label, "color": "Green"})
		changed = True

	content = json.loads(workspace.content or "[]")
	existing_ids = {block.get("id") for block in content}
	if "kpiinsights_sc" not in existing_ids:
		content.append({"id": "kpiinsights_sc", "type": "shortcut", "data": {"shortcut_name": shortcut_label, "col": 6}})
		workspace.content = json.dumps(content)
		changed = True

	if changed:
		workspace.save(ignore_permissions=True)
