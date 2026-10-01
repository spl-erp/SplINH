"""
Runtime query logic for the "Call Monitoring - Detailed" Desk page
(splinh.splinh.page.call_mon_detail, route /app/call-mon-detail).

Deliberately separate from `splinh_setup.py` (config-application only,
re-applied by `after_migrate`) - same split as `issue_tracking.py` for the
Issue Pipeline Tracker page.

SECURITY - read this before changing anything here. This page/API was built
after a separate reference page on this bench (`/salesperson-expense-report`,
a Web Page) was audited and found to have NO real server-side access control:
its JS calls the generic `frappe.client.get_list` API directly, and its
"access control" was client-side-only UX filtering. That pattern is not
replicated here. This module is called through ONE whitelisted method
(`get_dashboard_data`), and the very first thing it does - before touching a
single Call Log row - is an explicit role check via `frappe.only_for`:

  - `frappe.only_for` (frappe/utils/__init__.py, read in full before use)
    raises `frappe.PermissionError` whenever `set(roles).isdisjoint(get_roles())`,
    i.e. whenever the current session holds NONE of the given roles. A Guest
    session's only role is "Guest", which is disjoint from
    {"Call Tracker Manager", "System Manager"} - so a Guest is rejected by the
    exact same check as a wrong-role logged-in user, with no special-casing
    needed. Its one bypass is `local.session.user == "Administrator"`, which is
    intentional and already how every other System-Manager-gated feature on
    this bench behaves (Administrator is the bench superuser regardless of
    assigned roles).
  - Everything after that check uses `frappe.get_list` (not raw SQL run with
    ignore_permissions) for the row-level drill-down table, so Call Log's own
    DocPerms/User Permissions are still respected as a second layer - belt and
    braces, not the only gate. The aggregate COUNT/GROUP BY queries use plain
    parameterised `frappe.db.sql` (matching the existing
    "Call Log Status Breakdown" Query Report in splinh_setup.py, which the
    same two roles already have `report` access to) - safe here specifically
    because the role check above has already run unconditionally first, on
    every single call into this module, with no code path that skips it.

Metric/bucket definitions are NOT re-derived here - they reuse the exact
definitions already agreed and built into the Layer 1 Workspace
(splinh_setup.py's `_ensure_call_monitoring_cards` /
`_ensure_call_monitoring_status_report`) and the Layer 2 Insights workbook
(`call_monitoring_insights.py`):
  - Company calls: `customer` is set OR `custom_lead` is set (OR, not AND).
  - Missed calls (the summary metric): status IN ('No Answer', 'Busy', 'Unknown') -
    the real stored values a "Missed" call takes, regardless of source.
  - Status Breakdown (the chart / grouped bucket): only a WhatsApp row stored
    as status='Unknown' is relabelled 'Missed'; real 'No Answer'/'Busy' rows
    stay their own separate slices - same CASE expression as the Query Report.
    A user filtering explicitly by status="Missed" gets the broader summary
    definition above (No Answer/Busy/Unknown), which is the intuitive "show me
    every missed call" filter; the chart's own grouping is unaffected by which
    filter is active except that active filters narrow every group's input the
    same way.

COMPANY SCOPING (added after first live review of the page) - the "Company
calls" definition above is now also the page's fixed base lens:

  - `summary.total_calls` (the "Total calls" tile) is the ONLY number on this
    page computed from the raw togglable-filter WHERE clause with no company
    condition added - the true grand total, matching what a Guest-of-context
    "every Call Log row" count would be.
  - Every other number this method returns - the other 6 summary fields, all
    4 chart datasets, `total_rows`, and the drill-down `rows` themselves - has
    `COMPANY_CONDITION` ANDed onto the WHERE clause UNDERNEATH whatever
    togglable filters are active. This is not one of the togglable filters
    (there is no UI control that turns it off) - it is a permanent second
    layer, same spirit as the role check in `_check_access`: applied
    unconditionally, on every code path, before the numbers are computed.
  - Verified live (see CHANGELOG / handback report for the exact query
    output): whatsapp+phone accounts for all 123 company-scoped rows - that
    reconciliation is a coincidence of `custom_source` covering every row
    today, not a rule this code assumes (SUM of CASE, not a partition
    assumed exhaustive).
  - Outgoing/Incoming ALSO exclude the Missed status group (No Answer/Busy/
    Unknown), on top of the company scope above - see `_MISSED_STATUS_SQL_LIST`.
    A call that never connected isn't meaningfully "outgoing" or "incoming"
    traffic for this pair of tiles, so outgoing+incoming no longer sums to
    company_calls; it sums to company_calls - missed_calls, MINUS the small
    remainder of Missed calls whose `type` is NULL/Unknown (which don't
    belong to either tile in the first place). The "Incoming vs Outgoing"
    donut chart applies the identical exclusion (same SQL fragment) so its
    displayed numbers always agree with the two tiles exactly - this was a
    real bug (chart and tiles disagreeing after only one side was rescoped)
    on an earlier pass over this same page; see CHANGELOG.
  - The drill-down table's own `rows` AND its `total_rows` (the "X-Y of N"
    footer count, also driving Prev/Next paging) apply this SAME conditional
    exclusion whenever Direction is Incoming/Outgoing - see
    `_direction_filter_excludes_missed`, the one guard both call sites share.
    An earlier pass fixed `rows` alone and left `total_rows` reading straight
    from `summary.company_calls` (no exclusion at all); that let the table's
    visible rows be correct while its footer still claimed the larger,
    Missed-inclusive count - a real bug found via live browser check (not
    reproducible from `bench console` alone, since a fresh console process
    happily re-imports the already-fixed `rows` path and never touches
    whichever stale worker a real browser request was hitting); see
    CHANGELOG.
"""

import frappe
from frappe import _
from frappe.utils import cint, get_datetime

CALL_TRACKING_MANAGER_ROLE = "Call Tracker Manager"
ALLOWED_ROLES = ["Call Tracker Manager", "System Manager"]

# Same literal set as the "Missed calls" Number Card / Insights is_missed_call
# column - see module docstring. Not the narrower per-row status_bucket rule
# used for the breakdown chart (WhatsApp+Unknown only).
MISSED_STATUS_VALUES = ["No Answer", "Busy", "Unknown"]

# Same set, pre-rendered as a SQL literal list for the Outgoing/Incoming tiles
# and the "Incoming vs Outgoing" chart below - both now defined as an explicit
# EXCLUSION of the Missed group (status NOT IN (...)) rather than a hardcoded
# inclusion of 'Completed' alone, so a future non-missed status value doesn't
# silently fall out of both tiles/chart. Derived from MISSED_STATUS_VALUES so
# the two never drift apart.
_MISSED_STATUS_SQL_LIST = ", ".join(f"'{v}'" for v in MISSED_STATUS_VALUES)


def _direction_filter_excludes_missed(direction):
	"""True exactly when the active Direction filter is Incoming or Outgoing -
	the one condition that must be applied IDENTICALLY everywhere a reading
	needs to agree with the Outgoing/Incoming tiles under that filter: the
	drill-down rows themselves (`_filter_rows_get_list`) AND the row count
	used for that same table's footer/pagination (`total_rows` below).

	Added 2026-09-26 after a live discrepancy: `_filter_rows_get_list` already
	excluded Missed-status rows under this condition (see its own comment),
	but `total_rows` was still being taken straight from `summary.company_calls`
	(no exclusion at all) - so the table's own rows were correctly filtered
	down to, say, 74 while its footer still read "of 109", a silent 35-row
	(exactly the Missed count) mismatch between what the table showed and
	what it claimed. Both call sites now go through this one guard so the
	same drift can't reopen by only one of them being updated next time.
	"""
	return direction in ("Incoming", "Outgoing")


# The page's fixed, non-togglable base lens - see "COMPANY SCOPING" in the
# module docstring. No bind params (both sides are column checks), so it can
# be appended to any WHERE clause with plain string concatenation.
COMPANY_CONDITION = "(IFNULL(customer, '') != '' OR IFNULL(custom_lead, '') != '')"

# Same condition, expressed as frappe.get_list `or_filters` (OR'd together,
# then ANDed with the `filters` group) for `_filter_rows_get_list` below.
# "is"/"set" compiles to `IS NOT NULL`; confirmed live that this table never
# stores '' for these two columns (only NULL or a real value), so this is
# exactly equivalent to COMPANY_CONDITION's IFNULL(...) != '' checks, not an
# approximation of it.
COMPANY_OR_FILTERS = [
	["Call Log", "customer", "is", "set"],
	["Call Log", "custom_lead", "is", "set"],
]

# Bucketed status labels (post WhatsApp+Unknown->Missed merge - see module
# docstring) in a sensible display order. `_status_options()` filters this
# down to whichever of these are actually present in the company-scoped data
# rather than showing the full stock Call Log status enum, and appends any
# further label not anticipated here (future-proofing) at the end.
CANONICAL_STATUS_ORDER = [
	"Completed", "No Answer", "Busy", "Missed", "Failed",
	"Cancelled", "Queued", "Ringing", "In Progress", "Unspecified",
]

MAX_PAGE_LENGTH = 200
DEFAULT_PAGE_LENGTH = 50


def _check_access():
	"""First line of every entry point below. See module docstring."""
	frappe.only_for(ALLOWED_ROLES, message=True)


def _build_conditions(filters):
	"""Turns the filter dict from the page into a parameterised SQL WHERE clause.

	Every condition here is ANDed. `filters` is already-parsed (dict), not the
	raw JSON string - callers of this helper parse it first.
	"""
	conditions = ["1=1"]
	values = []

	from_date = filters.get("from_date")
	to_date = filters.get("to_date")
	if from_date:
		conditions.append("start_time >= %s")
		values.append(get_datetime(from_date))
	if to_date:
		# Inclusive of the whole end day.
		conditions.append("start_time <= %s")
		values.append(get_datetime(f"{to_date} 23:59:59"))

	employee = filters.get("employee")
	if employee:
		conditions.append("call_received_by = %s")
		values.append(employee)

	source = filters.get("source")
	if source in ("SIM", "WhatsApp"):
		conditions.append("custom_source = %s")
		values.append(source)

	direction = filters.get("direction")
	if direction in ("Incoming", "Outgoing", "Unknown"):
		conditions.append("type = %s")
		values.append(direction)

	status = filters.get("status")
	if status == "Missed":
		# Broad "Missed" definition - see module docstring. Deliberately NOT the
		# narrower WhatsApp+Unknown-only bucket used by the breakdown chart.
		placeholders = ", ".join(["%s"] * len(MISSED_STATUS_VALUES))
		conditions.append(f"status IN ({placeholders})")
		values.extend(MISSED_STATUS_VALUES)
	elif status == "Unspecified":
		# The status dropdown is now populated from real distinct values
		# (see `_status_options`), and "Unspecified" is the bucket label used
		# for a NULL `status` column - not a literal stored value, so it needs
		# its own branch rather than falling into `status = %s` below.
		conditions.append("status IS NULL")
	elif status:
		conditions.append("status = %s")
		values.append(status)

	return " AND ".join(conditions), values


@frappe.whitelist()
def get_dashboard_data(filters=None, start=0, page_length=DEFAULT_PAGE_LENGTH):
	"""Everything the Call Monitoring - Detailed page needs for one filter
	combination: the 7 summary numbers, the 4 breakdown-chart datasets, a page
	of matching Call Log rows for the drill-down table, and the option lists
	for the filter row itself.

	`filters` is the page's combined TOGGLABLE filter state (date range,
	employee, source, status, direction) - see `_build_conditions` for the
	exact keys. Every reading below is computed against the same togglable
	WHERE clause, so the summary numbers, the charts and the drill-down table
	are always for the one filter combination in effect, never three
	different scopes - EXCEPT `summary.total_calls`, which is the one number
	deliberately computed without the company-scoping base condition also
	applied here. See "COMPANY SCOPING" in the module docstring.
	"""
	_check_access()

	filters = frappe.parse_json(filters) if isinstance(filters, str) else (filters or {})
	start = cint(start)
	page_length = min(cint(page_length) or DEFAULT_PAGE_LENGTH, MAX_PAGE_LENGTH)

	where_clause, values = _build_conditions(filters)
	# The fixed base lens (see module docstring) - every reading below except
	# `total_calls` uses this, not `where_clause`, as its WHERE clause.
	company_where_clause = f"{where_clause} AND {COMPANY_CONDITION}"

	total_calls = cint(
		frappe.db.sql(
			f"SELECT COUNT(*) FROM `tabCall Log` WHERE {where_clause}", values
		)[0][0]
	)

	summary_row = frappe.db.sql(
		f"""SELECT
			COUNT(*) AS company_calls,
			SUM(CASE WHEN status IN ('No Answer', 'Busy', 'Unknown')
				THEN 1 ELSE 0 END) AS missed_calls,
			SUM(CASE WHEN type = 'Outgoing' AND status NOT IN ({_MISSED_STATUS_SQL_LIST})
				THEN 1 ELSE 0 END) AS outgoing_calls,
			SUM(CASE WHEN type = 'Incoming' AND status NOT IN ({_MISSED_STATUS_SQL_LIST})
				THEN 1 ELSE 0 END) AS incoming_calls,
			SUM(CASE WHEN custom_source = 'WhatsApp' THEN 1 ELSE 0 END) AS whatsapp_calls,
			SUM(CASE WHEN custom_source = 'SIM' THEN 1 ELSE 0 END) AS phone_calls
		FROM `tabCall Log`
		WHERE {company_where_clause}""",
		values,
		as_dict=True,
	)[0]
	summary = {"total_calls": total_calls}
	summary.update({k: cint(v) for k, v in summary_row.items()})

	# "Incoming vs Outgoing" chart - same Missed-exclusion as the Outgoing/
	# Incoming tiles above (not just the same-named tiles computed differently
	# from the chart they sit beside - see module docstring's COMPANY SCOPING
	# section for why that kind of tile/display mismatch is treated as a bug
	# on this page). A row with a Missed status is dropped from this chart's
	# total entirely, matching how it contributes 0 to both tiles' sums.
	direction_rows = frappe.db.sql(
		f"""SELECT IFNULL(type, 'Unspecified') AS label, COUNT(*) AS value
		FROM `tabCall Log` WHERE {company_where_clause}
			AND status NOT IN ({_MISSED_STATUS_SQL_LIST})
		GROUP BY label ORDER BY value DESC""",
		values,
		as_dict=True,
	)

	source_rows = frappe.db.sql(
		f"""SELECT IFNULL(custom_source, 'Unspecified') AS label, COUNT(*) AS value
		FROM `tabCall Log` WHERE {company_where_clause}
		GROUP BY label ORDER BY value DESC""",
		values,
		as_dict=True,
	)

	# Same CASE expression as CALL_MONITORING_STATUS_REPORT in splinh_setup.py
	# and call_monitoring_insights.py's status_bucket column - see module docstring.
	status_rows = frappe.db.sql(
		f"""SELECT
			CASE WHEN custom_source = 'WhatsApp' AND status = 'Unknown' THEN 'Missed'
				ELSE IFNULL(status, 'Unspecified') END AS label,
			COUNT(*) AS value
		FROM `tabCall Log` WHERE {company_where_clause}
		GROUP BY label ORDER BY value DESC""",
		values,
		as_dict=True,
	)

	trend_rows = frappe.db.sql(
		f"""SELECT DATE(start_time) AS day, COUNT(*) AS value
		FROM `tabCall Log` WHERE {company_where_clause} AND start_time IS NOT NULL
		GROUP BY day ORDER BY day ASC""",
		values,
		as_dict=True,
	)

	# The drill-down table goes through frappe.get_list (permission-checked)
	# rather than the raw SQL used for the aggregates above - same filter
	# semantics as `company_where_clause`, re-expressed as get_list filters in
	# `_filter_rows_get_list` (belt and braces: see module docstring).
	rows = _filter_rows_get_list(filters, start, page_length)

	# The row count backing the table's own "X-Y of N" footer AND its
	# Prev/Next paging logic - deliberately NOT `summary["company_calls"]`
	# (see the 2026-09-26 fix note on `_direction_filter_excludes_missed`):
	# that aggregate has no Missed-exclusion, so under a Direction=Incoming/
	# Outgoing filter it counts 35+ more rows than `_filter_rows_get_list`
	# actually returns, and the footer would silently disagree with both the
	# rows on screen and the tile above them. Same WHERE clause as `rows`,
	# with the identical conditional exclusion bolted on.
	total_rows_where = company_where_clause
	total_rows_values = list(values)
	if _direction_filter_excludes_missed(filters.get("direction")):
		total_rows_where += f" AND status NOT IN ({_MISSED_STATUS_SQL_LIST})"
	total_rows = cint(
		frappe.db.sql(
			f"SELECT COUNT(*) FROM `tabCall Log` WHERE {total_rows_where}",
			total_rows_values,
		)[0][0]
	)

	employees = frappe.db.sql(
		"""SELECT DISTINCT cl.call_received_by AS employee, e.employee_name AS employee_name
		FROM `tabCall Log` cl
		LEFT JOIN `tabEmployee` e ON e.name = cl.call_received_by
		WHERE IFNULL(cl.call_received_by, '') != ''
		ORDER BY employee_name""",
		as_dict=True,
	)

	return {
		"summary": summary,
		"charts": {
			"direction": direction_rows,
			"source": source_rows,
			"status": status_rows,
			"trend": trend_rows,
		},
		"rows": rows,
		# Company-scoped AND (when Direction is Incoming/Outgoing) Missed-
		# excluded, matching `rows`/`_filter_rows_get_list` exactly - NOT
		# `summary["company_calls"]` (see the fix note above `total_rows`
		# and on `_direction_filter_excludes_missed`), and NOT `total_calls`,
		# which is the one unscoped number on the page.
		"total_rows": total_rows,
		"filter_options": {
			"employees": employees,
			"sources": ["SIM", "WhatsApp"],
			"directions": ["Incoming", "Outgoing", "Unknown"],
			"statuses": _status_options(),
		},
	}


def _status_options():
	"""Real, present bucketed status labels within the company-scoped subset -
	see module docstring - rather than the full stock Call Log status enum
	(most of which never actually occurs on this bench). Deliberately
	independent of the currently active togglable filters (same reasoning as
	the `employees` list above): narrowing this by, say, the active employee
	filter would make options disappear from the dropdown while a user still
	has it open, which reads as broken rather than helpful.
	"""
	present = {
		row.label
		for row in frappe.db.sql(
			f"""SELECT DISTINCT
				CASE WHEN custom_source = 'WhatsApp' AND status = 'Unknown' THEN 'Missed'
					ELSE IFNULL(status, 'Unspecified') END AS label
			FROM `tabCall Log` WHERE {COMPANY_CONDITION}""",
			as_dict=True,
		)
	}
	ordered = [s for s in CANONICAL_STATUS_ORDER if s in present]
	ordered += sorted(present - set(ordered))
	return ordered


def _filter_rows_get_list(filters, start, page_length):
	"""Same filter semantics as `company_where_clause` in `get_dashboard_data`
	(togglable filters AND company-scoping), expressed as `frappe.get_list`
	filter/or_filters arguments so the drill-down table goes through Frappe's
	own permission-checked list API rather than only the raw SQL used for the
	aggregate counts above.
	"""
	and_filters = []
	from_date = filters.get("from_date")
	to_date = filters.get("to_date")
	if from_date:
		and_filters.append(["Call Log", "start_time", ">=", get_datetime(from_date)])
	if to_date:
		and_filters.append(["Call Log", "start_time", "<=", get_datetime(f"{to_date} 23:59:59")])
	employee = filters.get("employee")
	if employee:
		and_filters.append(["Call Log", "call_received_by", "=", employee])
	source = filters.get("source")
	if source in ("SIM", "WhatsApp"):
		and_filters.append(["Call Log", "custom_source", "=", source])
	direction = filters.get("direction")
	if direction in ("Incoming", "Outgoing", "Unknown"):
		and_filters.append(["Call Log", "type", "=", direction])
		if _direction_filter_excludes_missed(direction):
			# Same Missed-exclusion as the Outgoing/Incoming tiles and the
			# "Incoming vs Outgoing" chart (see module docstring and
			# `_MISSED_STATUS_SQL_LIST`'s comment) - a call that never
			# connected isn't meaningfully "outgoing" or "incoming" traffic,
			# so the drill-down table under either of those two direction
			# filters must agree with what the tile/chart above it show.
			# Deliberately NOT applied when direction is "Unknown" or unset -
			# there is no equivalent "Missed-excluded Unknown-direction" tile,
			# so the table doesn't invent one either; unfiltered-by-direction
			# and Unknown-direction rows keep today's unchanged behaviour.
			and_filters.append(["Call Log", "status", "not in", MISSED_STATUS_VALUES])
	status = filters.get("status")
	if status == "Missed":
		and_filters.append(["Call Log", "status", "in", MISSED_STATUS_VALUES])
	elif status == "Unspecified":
		and_filters.append(["Call Log", "status", "is", "not set"])
	elif status:
		and_filters.append(["Call Log", "status", "=", status])

	return frappe.get_list(
		"Call Log",
		filters=and_filters,
		# ANDed with `filters` above, OR'd internally with each other - the
		# company-scoping base condition (see module docstring), not one of
		# the togglable filters, so it isn't built from `filters` at all.
		or_filters=COMPANY_OR_FILTERS,
		fields=[
			"name", "start_time", "end_time", "duration", "type", "status",
			"custom_source", "custom_call_summary", "customer", "custom_lead",
			"call_received_by",
		],
		order_by="start_time desc",
		start=start,
		page_length=page_length,
	)
