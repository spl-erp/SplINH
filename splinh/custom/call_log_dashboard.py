"""
Custom whitelisted aggregation for the "Call Monitoring" workspace
(splinh_setup.py's ensure_call_monitoring_dashboard()).

Why this exists: both native Number Cards and native "Group By" Dashboard Charts
operate on a single AND-list of [doctype, fieldname, operator, value] conditions.
There is no way to express "customer is set OR custom_lead is set" (an OR across
two different fields) through that filter format - confirmed by reading
frappe/desk/doctype/number_card/number_card.py's get_result(), which only ever
forwards `filters` (the AND list) to frappe.get_list, never `or_filters` - and
Number Card's own doctype (frappe/desk/doctype/number_card/number_card.json) has
no `or_filters_json` field for a person to fill in from the UI at all.

So "Company calls" is the one card built as type="Custom" with this whitelisted
method, per the resolution hierarchy in splinh_setup.py's module docstring:
native mechanisms first, a custom method only where a native filter genuinely
can't express the logic.
"""

import frappe


@frappe.whitelist()
def get_company_calls_count(filters=None):
	"""Count of Call Log rows where `customer` OR `custom_lead` is set.

	`filters` arrives from the Number Card widget (frappe/public/js/frappe/widgets/
	number_card_widget.js get_settings()) as whatever
	frappe.dashboard_utils.get_all_filters() built from this card's own
	filters_json + dynamic_filters_json (here: just the "this week" bound on
	start_time) - every entry in it is ANDed. The customer/custom_lead OR is
	expressed with frappe.get_list's separate `or_filters` argument, which the
	query engine groups into its own bracketed OR clause and ANDs against
	everything else (confirmed live: frappe/database/operator_map.py's func_is
	turns ["Call Log", "customer", "is", "set"] into `customer != ''`, and
	frappe/model/qb_query.py's DatabaseQuery.execute() takes `or_filters` as a
	first-class, separately-ANDed argument).
	"""
	filters = frappe.parse_json(filters) if isinstance(filters, str) else (filters or [])
	result = frappe.get_list(
		"Call Log",
		filters=filters,
		or_filters=[
			["Call Log", "customer", "is", "set"],
			["Call Log", "custom_lead", "is", "set"],
		],
		# NOT the raw-string field "count(name) as total" - this Frappe version's
		# query builder (frappe/database/query.py _validate_select_field) rejects
		# any SQL function written as a plain string and throws ValidationError,
		# confirmed live. The dict form is the same aggregate-with-alias shape
		# frappe/desk/doctype/dashboard_chart/dashboard_chart.py's own
		# get_group_by_chart_config() uses for a native Group By chart.
		fields=[{"COUNT": "name", "as": "total"}],
	)
	return result[0]["total"] if result else 0
