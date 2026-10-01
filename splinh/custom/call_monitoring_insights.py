"""
Layer 2 of the Call Monitoring project (2026-09-26): an Insights workbook that
drills down from the Layer 1 Workspace's Number Cards/Charts
(splinh_setup.py's ensure_call_monitoring_dashboard()) into any combination
of filters, down to individual Call Log rows.

This is the first real use of the `insights` app on this bench - installed but
never configured or used anywhere before this. Confirmed live before writing
anything below (do not assume from general Insights knowledge):

  - Insights v3 already has a "Site DB" Insights Data Source v3 record with
    is_site_db=1 - it connects DIRECTLY to this site's own database, no
    external connection string, host or credentials needed. This is provisioned
    automatically (not something this module needs to create).
  - `tabCall Log` is already synced as an Insights Table v3 row against that
    Site DB source (name d67a1ace4d at the time this was written) - every table
    in the site's schema is auto-registered, so no manual "connect a table"
    step is needed either.
  - Insights v3 stores a workbook's real content as plain Frappe documents -
    Insights Workbook / Insights Query v3 / Insights Chart v3 / Insights
    Dashboard v3 - authored as described in the `insights-workbook-cli` skill
    this app ships at apps/insights/skills/insights-workbook-cli/ (read in
    full before writing this module: SKILL.md + every file under reference/).
    That skill targets a *separate* site over its own CLI (frappectl); this
    bench already has direct DB/bench access to the same site, so the
    documents below are created with plain frappe.get_doc(...).insert() calls
    instead - the exact same document shapes the skill's reference/*.md files
    describe, just over a different transport. The skill's hard rules are
    followed throughout: queries stay per-row (charts aggregate, so every
    chart here can drill down to source rows), no bare `filter` (only
    `filter_group`), no `sql`/`code` operations, dashboard filters route by
    query name and column.

Idempotent by "create once, never touch again": once "Call Monitoring" exists
as an Insights Workbook title, this function returns immediately every later
run. The skill's own dashboards.md is explicit about why: "the site owns the
layout" the moment a person can open and edit it - a user who drags a chart,
resizes a filter, or edits a title in the Insights UI owns that edit, and a
script that unconditionally rebuilds `items` on every migrate would silently
destroy it. Layer 1's Number Cards/Charts are safe to rebuild every migrate
because nothing else there is ever hand-edited outside this file; an Insights
workbook is explicitly a thing people edit by hand, so it gets the create-once
treatment instead - if a restore wipes it, the next migrate recreates a fresh
copy, and nothing here ever overwrites a copy that already exists.
"""

import json

import frappe

WORKBOOK_TITLE = "Call Monitoring"
INSIGHTS_DATA_SOURCE = "Site DB"
CALL_LOG_TABLE = "tabCall Log"
BASE_QUERY_TITLE = "Call Log"
CALL_MONITORING_WORKSPACE = "Call Monitoring"


def ensure_call_monitoring_insights():
	if frappe.db.exists("Insights Workbook", {"title": WORKBOOK_TITLE}):
		_ensure_insights_shortcut_on_workspace()
		return

	if not frappe.db.exists("Insights Data Source v3", INSIGHTS_DATA_SOURCE):
		frappe.log_error(
			title="splinh_setup: Insights Site DB source missing",
			message=f"No '{INSIGHTS_DATA_SOURCE}' Insights Data Source v3 - "
			"Call Monitoring Insights workbook not created.",
		)
		return
	if not frappe.db.exists("Insights Table v3", {"table": CALL_LOG_TABLE, "data_source": INSIGHTS_DATA_SOURCE}):
		frappe.log_error(
			title="splinh_setup: Call Log not synced into Insights",
			message=f"No Insights Table v3 row for {CALL_LOG_TABLE} against '{INSIGHTS_DATA_SOURCE}' - "
			"Call Monitoring Insights workbook not created.",
		)
		return

	_build_call_monitoring_workbook()
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


def _count_measure(measure_name="Calls"):
	return {"measure_name": measure_name, "column_name": "count", "data_type": "Integer", "aggregation": "count"}


def _expr_measure(measure_name, expression, data_type="Integer"):
	return {
		"measure_name": measure_name,
		"data_type": data_type,
		"expression": {"type": "expression", "expression": expression},
	}


EMPTY_FILTERS = {"logical_operator": "And", "filters": []}


def _build_call_monitoring_workbook():
	workbook = frappe.get_doc({"doctype": "Insights Workbook", "title": WORKBOOK_TITLE})
	workbook.insert(ignore_permissions=True)
	workbook_name = workbook.name

	# ---- base query: one row per Call Log call. Stays per-row throughout (no
	# summarize/order_by/limit) so every chart built on it can drill down to the
	# actual call - see the "Queries return per-row data" rule in
	# reference/rules.md. Two computed columns reuse this project's existing
	# conventions instead of re-deriving them:
	#   - status_bucket: the exact WhatsApp+Unknown -> "Missed" display-label
	#     convention from splinh_setup.py's CALL_LOG_STATUS_LABEL_*_JS,
	#     reimplemented here as a real per-row column (Insights has no
	#     equivalent of a client-side list-view formatter).
	#   - is_missed_call / is_company_call: the same definitions as the
	#     "Missed calls" / "Company calls" Number Cards in
	#     splinh_setup.py's CALL_MONITORING_NATIVE_CARDS /
	#     _ensure_call_monitoring_cards(), as 0/1 columns so they can be summed
	#     as measures and cut by any dashboard filter.
	# `call_received_by` (not `owner`) is used for the "employee" filter: real
	# data shows `owner` is 397/397 populated but almost entirely one shared
	# sync/service account (exports.eu@splasjetink.com, 335 of 397 rows), so it
	# does not actually distinguish who handled a call; `call_received_by` is
	# populated on 335/397 rows and is the real per-call assignee.
	operations = [
		{
			"type": "source",
			"table": {"type": "table", "data_source": INSIGHTS_DATA_SOURCE, "table_name": CALL_LOG_TABLE},
		},
		{
			"type": "select",
			"column_names": [
				"name",
				"start_time",
				"end_time",
				"duration",
				"status",
				"type",
				"custom_source",
				"customer",
				"custom_lead",
				"call_received_by",
				"owner",
				"custom_call_summary",
			],
		},
		{
			"type": "mutate",
			"new_name": "status_bucket",
			"data_type": "String",
			"expression": {
				"type": "expression",
				"expression": "if_else((custom_source == 'WhatsApp') & (status == 'Unknown'), 'Missed', status)",
			},
		},
		{
			"type": "mutate",
			"new_name": "is_missed_call",
			"data_type": "Integer",
			"expression": {
				"type": "expression",
				"expression": "one_if((status == 'No Answer') | (status == 'Busy') | (status == 'Unknown'))",
			},
		},
		{
			"type": "mutate",
			"new_name": "is_company_call",
			"data_type": "Integer",
			"expression": {"type": "expression", "expression": "one_if(is_set(customer) | is_set(custom_lead))"},
		},
	]
	query = _new_query_doc(workbook_name, BASE_QUERY_TITLE, operations)
	query_name = query.name

	# ---- charts, all built on the one base query above.
	metrics_chart = _new_chart_doc(
		workbook_name,
		"Key Metrics",
		query_name,
		"Number",
		{
			"number_columns": [
				_count_measure("Total Calls"),
				{"measure_name": "Company Calls", "column_name": "is_company_call", "data_type": "Integer", "aggregation": "sum"},
				{"measure_name": "Missed Calls", "column_name": "is_missed_call", "data_type": "Integer", "aggregation": "sum"},
				_expr_measure("Outgoing Calls", "count_if(type == 'Outgoing')"),
				_expr_measure("Incoming Calls", "count_if(type == 'Incoming')"),
				_expr_measure("WhatsApp Calls", "count_if(custom_source == 'WhatsApp')"),
				_expr_measure("Phone (SIM) Calls", "count_if(custom_source == 'SIM')"),
			],
			"number_formats": {},
			"number_column_options": [{}, {}, {}, {}, {}, {}, {}],
			"filters": EMPTY_FILTERS,
		},
	)

	incoming_outgoing_chart = _new_chart_doc(
		workbook_name,
		"Calls, Incoming vs Outgoing",
		query_name,
		"Donut",
		{
			"label_column": _col("type", "type", "String"),
			"value_column": _count_measure(),
			"max_slices": 8,
		},
	)

	source_chart = _new_chart_doc(
		workbook_name,
		"Calls, WhatsApp vs SIM",
		query_name,
		"Donut",
		{
			"label_column": _col("custom_source", "custom_source", "String"),
			"value_column": _count_measure(),
			"max_slices": 8,
		},
	)

	status_chart = _new_chart_doc(
		workbook_name,
		"Calls, By Status (Missed Merged)",
		query_name,
		"Donut",
		{
			"label_column": _col("status_bucket", "status_bucket", "String"),
			"value_column": _count_measure(),
			"max_slices": 8,
		},
	)

	trend_chart = _new_chart_doc(
		workbook_name,
		"Calls, Per Day",
		query_name,
		"Line",
		{
			"x_axis": {"dimension": _col("start_time", "start_time", "Datetime", granularity="day")},
			"y_axis": {"series": [{"measure": _count_measure()}], "show_data_labels": False},
			"order_by": [{"column": {"type": "column", "column_name": "start_time"}, "direction": "asc"}],
			"limit": 366,
			"filters": EMPTY_FILTERS,
		},
	)

	table_chart = _new_chart_doc(
		workbook_name,
		"Call Log Detail",
		query_name,
		"Table",
		{
			"rows": [
				_col("start_time", "start_time", "Datetime"),
				_col("status_bucket", "status_bucket", "String"),
				_col("type", "type", "String"),
				_col("custom_source", "custom_source", "String"),
				_col("customer", "customer", "String"),
				_col("custom_lead", "custom_lead", "String"),
				_col("call_received_by", "call_received_by", "String"),
				_col("custom_call_summary", "custom_call_summary", "String"),
				_col("name", "name", "String"),
			],
			"columns": [],
			"values": [{"measure_name": "Row Count", "column_name": "count", "data_type": "Integer", "aggregation": "count"}],
			"order_by": [{"column": {"type": "column", "column_name": "start_time"}, "direction": "desc"}],
			"limit": 1000,
			"show_row_totals": False,
			"filters": EMPTY_FILTERS,
		},
	)

	# ---- dashboard. Every filter links every chart to the same base query
	# (`query_name`), since all six charts share that one query - see
	# "routing is by query name" in reference/dashboards.md.
	chart_docs = [metrics_chart, incoming_outgoing_chart, source_chart, status_chart, trend_chart, table_chart]

	def _links_for(column):
		return {c.name: f"`{query_name}`.`{column}`" for c in chart_docs}

	items = []

	filter_specs = [
		("Date Range", "Date", "start_time", "filter-date", 0),
		("Employee", "String", "call_received_by", "filter-employee", 4),
		("Source", "String", "custom_source", "filter-source", 8),
		("Status", "String", "status", "filter-status", 12),
		("Type", "String", "type", "filter-type", 16),
	]
	for filter_name, filter_type, column, item_id, x in filter_specs:
		item = {
			"type": "filter",
			"filter_name": filter_name,
			"filter_type": filter_type,
			"links": _links_for(column),
			"layout": {"i": item_id, "x": x, "y": 0, "w": 4, "h": 2},
		}
		if filter_name == "Date Range":
			item["icon"] = "calendar"
		items.append(item)

	# Key Metrics: one cell per reading (Number charts are not one grid cell -
	# see "A Number chart is not one cell" in reference/dashboards.md).
	metric_readings = [
		"Total Calls", "Company Calls", "Missed Calls", "Outgoing Calls",
		"Incoming Calls", "WhatsApp Calls", "Phone (SIM) Calls",
	]
	for i, reading in enumerate(metric_readings):
		x = (i % 5) * 4
		y = 2 if i < 5 else 6
		items.append(
			{
				"type": "chart",
				"chart": metrics_chart.name,
				"reading": reading,
				"layout": {"i": f"kpi-{i}", "x": x, "y": y, "w": 4, "h": 4},
			}
		)

	items.append({"type": "chart", "chart": incoming_outgoing_chart.name, "layout": {"i": "chart-in-out", "x": 0, "y": 10, "w": 10, "h": 20}})
	items.append({"type": "chart", "chart": source_chart.name, "layout": {"i": "chart-source", "x": 10, "y": 10, "w": 10, "h": 20}})
	items.append({"type": "chart", "chart": status_chart.name, "layout": {"i": "chart-status", "x": 0, "y": 30, "w": 10, "h": 20}})
	items.append({"type": "chart", "chart": trend_chart.name, "layout": {"i": "chart-trend", "x": 10, "y": 30, "w": 10, "h": 20}})
	items.append({"type": "chart", "chart": table_chart.name, "layout": {"i": "chart-table", "x": 0, "y": 50, "w": 20, "h": 24}})

	dashboard = frappe.get_doc(
		{
			"doctype": "Insights Dashboard v3",
			"workbook": workbook_name,
			"title": WORKBOOK_TITLE,
			"items": json.dumps(items),
		}
	)
	dashboard.insert(ignore_permissions=True)
	return workbook_name, dashboard.name


def _ensure_insights_shortcut_on_workspace():
	"""One click from the Layer 1 Workspace to the Layer 2 Insights dashboard.

	Additive only - never rebuilds workspace.content wholesale, unlike
	_ensure_call_monitoring_workspace() in splinh_setup.py, which fully owns
	the rest of that Workspace's layout and already runs (and recommits) before
	this function on every after_migrate (see execute()'s step order).
	"""
	if not frappe.db.exists("Workspace", CALL_MONITORING_WORKSPACE):
		return
	workbook_name = frappe.db.get_value("Insights Workbook", {"title": WORKBOOK_TITLE}, "name")
	if not workbook_name:
		return
	dashboard_name = frappe.db.get_value("Insights Dashboard v3", {"workbook": workbook_name}, "name")
	if not dashboard_name:
		return

	route = f"/insights/workbook/{workbook_name}/dashboard/{dashboard_name}"
	shortcut_label = "Call Monitoring (Insights)"

	workspace = frappe.get_doc("Workspace", CALL_MONITORING_WORKSPACE)
	existing_shortcut_labels = {row.label for row in workspace.shortcuts}
	changed = False
	if shortcut_label not in existing_shortcut_labels:
		workspace.append(
			"shortcuts",
			{"type": "URL", "url": route, "label": shortcut_label, "color": "Green"},
		)
		changed = True

	content = json.loads(workspace.content or "[]")
	existing_ids = {block.get("id") for block in content}
	if "cminsights_sc" not in existing_ids:
		content.append({"id": "cminsights_sc", "type": "shortcut", "data": {"shortcut_name": shortcut_label, "col": 6}})
		workspace.content = json.dumps(content)
		changed = True

	if changed:
		workspace.save(ignore_permissions=True)
