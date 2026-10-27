"""Schema of the Sales Scorecard (DILO) doctypes, created by the config engine when missing (2026-10-27).

Why this exists. The two doctypes - "Sales Scorecard" (one record per person per month) and its child table
"Sales Scorecard Day" - were first built by hand in the Desk UI of production as Custom doctypes (see the
Chrome_Prompt_Sales_Scorecard_* documents in Dump/), so no app file describes them and a migrate on any other
site never creates them. custom/scorecard.py and api/scorecard.py read and write them BY FIELDNAME, so that
list of fieldnames is the contract; this file is that contract written down as data.

`ensure_scorecard_schema()` creates a doctype ONLY IF it does not exist. Production already has both, so there it
changes nothing (never edits, never converts); UAT and any new site get them on the next migrate. Custom doctype
(custom = 1) because that is what production has, and what Frappe allows without developer mode.

The test (tests/test_scorecard_schema.py) checks that every fieldname the code uses is defined here.
"""

import frappe

DOCTYPE = "Sales Scorecard"
CHILD = "Sales Scorecard Day"
MODULE = "SplINH"
SETTINGS = "Call Tracker Settings"


def _f(fieldname, fieldtype, label=None, **extra):
	return {"fieldname": fieldname, "fieldtype": fieldtype, "label": label, **extra}


def _listed(field):
	"""A column of the child grid. A grid shows at most 10 column widths, so each of these is 1 wide."""
	return {**field, "in_list_view": 1, "columns": 1}


# ------------------------------------------------------------------ child table: one row per day

# The Opportunity / Quotation block: (fieldname prefix, label, kind). Plan typed by the rep (_expected), counted by
# the server (_created), Ach % worked out (_ach). Same names as custom/scorecard.py EXTRAS.
_EXTRAS = (
	("opportunities", "Opportunities", "Int"),
	("opportunity_amount", "Opportunity Amount", "Currency"),
	("quotations", "Quotations", "Int"),
	("quotation_amount", "Quotation Amount", "Currency"),
)


def _child_extras():
	out = []
	for name, label, kind in _EXTRAS:
		out += [
			_f(f"{name}_expected", kind, f"{label} Expected"),
			_f(f"{name}_created", kind, f"{label} Created", read_only=1),
			_f(f"{name}_ach", "Percent", f"{label} Ach %", read_only=1),
		]
	return out


CHILD_FIELDS = [
	_listed(_f("date", "Date", "Date", read_only=1, reqd=1)),
	_listed(_f("day", "Data", "Day", read_only=1)),
	_f("no_target", "Check", "Off Day"),
	_listed(_f("plan_new", "Int", "New Calls Plan")),
	_listed(_f("actual_new", "Int", "New Calls Actual", read_only=1)),
	_listed(_f("ach_new", "Percent", "New Calls Ach %", read_only=1)),
	_listed(_f("plan_followup", "Int", "Follow-up Plan")),
	_listed(_f("actual_followup", "Int", "Follow-up Actual", read_only=1)),
	_listed(_f("ach_followup", "Percent", "Follow-up Ach %", read_only=1)),
	_listed(_f("orders_expected", "Int", "Orders Expected")),
	_listed(_f("order_amount_expected", "Currency", "Order Amount Expected")),
	_f("orders_received", "Int", "Orders Received"),
	_f("order_amount_received", "Currency", "Order Amount Received"),
	_f("orders_ach", "Percent", "Orders Ach %", read_only=1),
	_f("order_amount_ach", "Percent", "Order Amount Ach %", read_only=1),
	_f("payments_expected", "Currency", "Payments Expected"),
	_f("payments_received", "Currency", "Payments Received"),
	_f("payments_ach", "Percent", "Payments Ach %", read_only=1),
	*_child_extras(),
	_f("remarks", "Small Text", "Remarks"),
	_f("plan_saved_at", "Datetime", "Plan Saved At", read_only=1),
	_f("eod_saved_at", "Datetime", "EOD Saved At", read_only=1),
	_f("eod_submitted", "Check", "End of Day Submitted"),
	_f("eod_submitted_at", "Datetime", "End of Day Submitted At", read_only=1),
	# the manager's daily target: kept, hidden and unused until real targets are designed
	_f("target_new", "Int", "New Calls Target", read_only=1, hidden=1),
	_f("target_followup", "Int", "Follow-up Target", read_only=1, hidden=1),
]


# ------------------------------------------------------------------ parent: one record per person per month

_MONTH_BLOCK_EXTRAS = []
for _name, _label, _kind in _EXTRAS:
	_MONTH_BLOCK_EXTRAS += [
		_f(f"{_name}_expected_total", _kind, f"{_label} Expected (Month)", read_only=1),
		_f(f"{_name}_created_total", _kind, f"{_label} Created (Month)", read_only=1),
		_f(f"{_name}_ach", "Percent", f"{_label} Ach %", read_only=1),
	]

PARENT_FIELDS = [
	_f("summary_tab", "Tab Break", "Summary"),
	_f("user", "Link", "User", options="User", reqd=1, in_list_view=1, in_standard_filter=1),
	_f("month_start", "Date", "Month", reqd=1, in_list_view=1, in_standard_filter=1, description="First day of the month"),
	_f("employee_name", "Data", "Employee Name", read_only=1),
	_f("column_break_sc1", "Column Break"),
	_f("sales_person", "Link", "Sales Person", options="Sales Person", read_only=1),
	_f("reporting_manager", "Link", "Reporting Manager", options="User", read_only=1),
	# the manager's daily target: kept, hidden and unused until real targets are designed
	_f("targets_section", "Section Break", "Daily targets (set by the manager)", hidden=1),
	_f("daily_new_calls_target", "Int", "Daily New Calls Target", hidden=1),
	_f("column_break_sc2", "Column Break"),
	_f("daily_followup_calls_target", "Int", "Daily Follow-up Calls Target", hidden=1),
	_f("month_section", "Section Break", "Month so far (automatic)"),
	_f("new_calls_plan_total", "Int", "New Calls Plan (Month)", read_only=1),
	_f("new_calls_target_total", "Int", "New Calls Target (Month)", read_only=1, hidden=1),
	_f("new_calls_actual_total", "Int", "New Calls Actual (Month)", read_only=1),
	_f("new_calls_ach", "Percent", "New Calls Ach %", read_only=1),
	_f("followup_calls_plan_total", "Int", "Follow-up Plan (Month)", read_only=1),
	_f("followup_calls_target_total", "Int", "Follow-up Calls Target (Month)", read_only=1, hidden=1),
	_f("followup_calls_actual_total", "Int", "Follow-up Calls Actual (Month)", read_only=1),
	_f("followup_calls_ach", "Percent", "Follow-up Calls Ach %", read_only=1),
	_f("column_break_sc3", "Column Break"),
	_f("orders_expected_total", "Int", "Orders Expected (Month)", read_only=1),
	_f("orders_received_total", "Int", "Orders Received (Month)", read_only=1),
	_f("orders_ach", "Percent", "Orders Ach %", read_only=1),
	_f("order_amount_expected_total", "Currency", "Order Amount Expected (Month)", read_only=1),
	_f("order_amount_received_total", "Currency", "Order Amount Received (Month)", read_only=1),
	_f("order_amount_ach", "Percent", "Order Amount Ach %", read_only=1),
	_f("payments_expected_total", "Currency", "Payments Expected (Month)", read_only=1),
	_f("payments_received_total", "Currency", "Payments Received (Month)", read_only=1),
	_f("payments_ach", "Percent", "Payments Ach %", read_only=1),
	*_MONTH_BLOCK_EXTRAS,
	_f("dilo_tab", "Tab Break", "DILO"),
	_f("daily", "Table", "Daily", options=CHILD),
	_f("wilo_tab", "Tab Break", "WILO"),
	_f("wilo_note", "HTML", "WILO Note", options="<p>Weekly scorecard: coming in the next phase.</p>"),
	_f("milo_tab", "Tab Break", "MILO"),
	_f("milo_note", "HTML", "MILO Note", options="<p>Monthly scorecard: coming in the next phase.</p>"),
]

_ALL_RIGHTS = {
	"role": "System Manager",
	"permlevel": 0,
	"read": 1,
	"write": 1,
	"create": 1,
	"delete": 1,
	"report": 1,
	"export": 1,
	"print": 1,
	"email": 1,
	"share": 1,
}

# Call Tracker Settings gets one section and one field (a Custom Field on a standard doctype).
SETTINGS_FIELDS = [
	{
		"fieldname": "dilo_section",
		"fieldtype": "Section Break",
		"label": "DILO (daily scorecard)",
		"insert_after": "token_inactivity_days",
	},
	{
		"fieldname": "dilo_plan_cutoff_time",
		"fieldtype": "Time",
		"label": "DILO plan cut-off time",
		"default": "12:00:00",
		"insert_after": "dilo_section",
		"description": (
			"Until this time of day a rep can edit the day's plan in the app; the end-of-day figures "
			"stay editable until midnight."
		),
	},
]


# ------------------------------------------------------------------ creation


def _doctype_values(name, fields, **extra):
	return {
		"doctype": "DocType",
		"name": name,
		"module": MODULE,
		"custom": 1,
		"engine": "InnoDB",
		"fields": fields,
		**extra,
	}


def ensure_scorecard_schema():
	"""Create the two doctypes (child first) and the Settings field if they are missing. Existing ones are never touched."""
	if not frappe.db.exists("DocType", CHILD):
		frappe.get_doc(_doctype_values(CHILD, CHILD_FIELDS, istable=1, editable_grid=1, permissions=[])).insert(
			ignore_permissions=True
		)
	if not frappe.db.exists("DocType", DOCTYPE):
		frappe.get_doc(
			_doctype_values(
				DOCTYPE,
				PARENT_FIELDS,
				naming_rule="Expression",
				autoname="format:{user}-{month_start}",
				title_field="employee_name",
				sort_field="month_start",
				sort_order="DESC",
				track_changes=1,
				permissions=[dict(_ALL_RIGHTS)],
			)
		).insert(ignore_permissions=True)

	if frappe.db.exists("DocType", SETTINGS):
		for field in SETTINGS_FIELDS:
			if not frappe.db.exists("Custom Field", f"{SETTINGS}-{field['fieldname']}"):
				frappe.get_doc({"doctype": "Custom Field", "dt": SETTINGS, "is_system_generated": 0, **field}).insert(
					ignore_permissions=True
				)
