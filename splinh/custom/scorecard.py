"""Sales Scorecard: management's DILO / WILO / MILO workbook as one record per person per month.

The doctypes were designed in the Desk UI (Sales Scorecard + the child table Sales Scorecard Day; the
fieldnames below are the contract). This module holds the rules and the data access. DILO (daily) is
live; the WILO and MILO tabs are added later.

THE REP SETS THE PLAN. Each morning the rep saves a plan (SOD) and the day is measured against it:
  plan_new / plan_followup       New and Follow-up calls the rep plans (counts)
  orders_expected                Orders the rep expects (a number)
  order_amount_expected          Value of those orders (an amount in rupees)
  payments_expected              Payments the rep expects (an amount in rupees)
  plan_saved_at                  set when the plan is saved: a day "has a plan" only then (and if it is not an off day)
  no_target                      the "Off Day" tick (leave, holiday): no plan is expected that day
  actual_new / actual_followup   counted from the person's Call Logs of that day by Call Category
                                 ("New Call" / "Follow Up Call"); calls without a category count as nothing
  ach_new / ach_followup         actual / plan x 100, uncapped; blank without a saved plan or on an off day
  opportunities / opportunity_amount / quotations / quotation_amount   (EXTRAS) the same way as the calls: the rep plans
                                 <x>_expected, the server counts <x>_created from the Opportunities / Quotations the person
                                 created that day (owner), <x>_ach = created / expected x 100 (blank without a saved plan)
  orders_received / order_amount_received / payments_received, remarks    typed by the rep at the end of the day
  orders_ach / order_amount_ach / payments_ach    received / expected x 100
The manager's daily target fields (target_new, target_followup, daily_*_target, *_target_total) are kept but
hidden and UNUSED until real targets are designed. An approval step for plans is on hold.

Month so far (days up to today): month plan, orders, order amount and payments = the days that have a plan; Ach % =
actual on those days / month plan; days without a plan are simply left out (calls made on them still count in the
month actual).

Auto fields are read-only in the UI and written here with the DB API (never a parent save), so no Version
rows and no hooks fire. Locks (app only, server clock, today only): the plan group (plan and expected fields)
until the manager's cut-off time, no late plan; the end-of-day group (received, remarks) and the off-day tick
until midnight.

Pure rules first (no database), then the Frappe access.
"""

import datetime

import frappe
from frappe.utils import add_days, add_months, flt, get_first_day, getdate, now_datetime, today

DOCTYPE = "Sales Scorecard"
CHILD = "Sales Scorecard Day"
CALL_NEW = "New Call"
CALL_FOLLOWUP = "Follow Up Call"
DEFAULT_CUTOFF = datetime.time(12, 0, 0)
# API names of the two lock groups (row fields: plan_new_calls -> plan_new, plan_followup_calls -> plan_followup)
# Opportunities and Quotations created by the person: (name, kind). Row fields <name>_expected / _created / _ach,
# parent fields <name>_expected_total / _created_total / _ach.
EXTRAS = (
	("opportunities", "count"),
	("opportunity_amount", "amount"),
	("quotations", "count"),
	("quotation_amount", "amount"),
)
EXTRA_PLAN_FIELDS = tuple(f"{name}_expected" for name, _ in EXTRAS)
SOD_FIELDS = (
	"plan_new_calls", "plan_followup_calls", "orders_expected", "order_amount_expected", "payments_expected",
) + EXTRA_PLAN_FIELDS
EOD_FIELDS = ("orders_received", "order_amount_received", "payments_received", "remarks", "off_day")
# the figures a submitted day freezes ("Submit day": the rep's End of day is final; an ERP user can untick it)
EOD_FIGURE_FIELDS = ("orders_received", "order_amount_received", "payments_received", "remarks")
ROW_FIELD = {"plan_new_calls": "plan_new", "plan_followup_calls": "plan_followup"}
REMARKS_MAX = 500


class InvalidDilo(frappe.ValidationError):
	http_status_code = 422

	def __init__(self, message, field=None):
		super().__init__(message)
		self.field = field


# ------------------------------------------------------------------ pure rules


def month_start(value):
	return get_first_day(getdate(value))


def days_of_month(start):
	"""Every date of the month that `start` falls in, oldest first."""
	start = get_first_day(getdate(start))
	days, d = [], start
	while d.month == start.month:
		days.append(d)
		d = d + datetime.timedelta(days=1)
	return days


def ach(numerator, denominator):
	"""numerator / denominator x 100, two decimals; None without a positive denominator."""
	denominator = flt(denominator)
	if denominator <= 0:
		return None
	return round(flt(numerator) / denominator * 100, 2)


def clean_count(value, field):
	if value is None or (isinstance(value, str) and not value.strip()):
		return None
	if isinstance(value, bool):
		raise InvalidDilo(f"{field} must be a whole number.", field)
	try:
		number = float(value)
	except (TypeError, ValueError):
		raise InvalidDilo(f"{field} must be a whole number.", field)
	if number != int(number) or number < 0 or number > 10**7:
		raise InvalidDilo(f"{field} must be a whole number of 0 or more.", field)
	return int(number)


def clean_amount(value, field):
	if value is None or (isinstance(value, str) and not value.strip()):
		return None
	if isinstance(value, bool):
		raise InvalidDilo(f"{field} must be an amount.", field)
	try:
		number = float(value)
	except (TypeError, ValueError):
		raise InvalidDilo(f"{field} must be an amount.", field)
	if number != number or number < 0 or number > 10**12:
		raise InvalidDilo(f"{field} must be an amount of 0 or more.", field)
	return round(number, 2)


def clean_remarks(value):
	"""Text up to 500 characters, control characters removed, line breaks kept; None leaves it unchanged."""
	if value is None:
		return None
	if not isinstance(value, str):
		raise InvalidDilo("remarks must be text.", "remarks")
	cleaned = "".join(ch for ch in value.replace("\r\n", "\n") if ch == "\n" or ch >= " " and ch != "\x7f")
	return "\n".join(line.strip() for line in cleaned.split("\n")).strip()[:REMARKS_MAX]


def parse_cutoff(value):
	"""A Time value (timedelta, time or text) as datetime.time; missing or 00:00:00 means the default."""
	if isinstance(value, datetime.timedelta):
		seconds = int(value.total_seconds())
		value = datetime.time(seconds // 3600 % 24, seconds % 3600 // 60, seconds % 60) if seconds else None
	elif isinstance(value, str):
		parts = value.strip().split(":") if value.strip() else []
		try:
			value = datetime.time(*[int(float(p)) for p in parts[:3]]) if parts else None
		except (ValueError, TypeError):
			value = None
	if not value or value == datetime.time(0, 0, 0):
		return DEFAULT_CUTOFF
	return value


def locks(now, the_date, cutoff):
	"""Which groups the app may edit: only for today; SOD until the cut-off, EOD until midnight."""
	is_today = getdate(the_date) == getdate(now)
	return {
		"plan_editable": bool(is_today and now.time() < cutoff),
		"eod_editable": bool(is_today),
	}


def has_plan(row):
	"""A day has a plan when the rep saved it and it is not an off day."""
	return bool(row.get("plan_saved_at")) and not row.get("no_target")


def apply_rules(parent, rows, today_date):
	"""Recompute Ach % and the month-so-far totals from the plan.

	`parent` and each row support .get()/.set() like a Frappe document (or a _dict wrapper). Actuals are read as
	stored (refresh_scorecard fills them). The manager's target fields are not used."""
	today_date = getdate(today_date)
	totals = {"pn": 0, "pf": 0, "an": 0, "af": 0, "an_p": 0, "af_p": 0, "oe": 0, "or": 0, "ae": 0.0, "ar": 0.0, "pe": 0.0, "pr": 0.0}
	extra = {name: {"exp": 0.0, "created": 0.0, "created_p": 0.0} for name, _ in EXTRAS}
	for row in rows:
		d = getdate(row.get("date"))
		planned = has_plan(row)
		row.set("ach_new", ach(row.get("actual_new"), row.get("plan_new")) if planned else None)
		row.set("ach_followup", ach(row.get("actual_followup"), row.get("plan_followup")) if planned else None)
		row.set("orders_ach", ach(row.get("orders_received"), row.get("orders_expected")))
		row.set("order_amount_ach", ach(row.get("order_amount_received"), row.get("order_amount_expected")))
		row.set("payments_ach", ach(row.get("payments_received"), row.get("payments_expected")))
		for name, _ in EXTRAS:
			row.set(f"{name}_ach", ach(row.get(f"{name}_created"), row.get(f"{name}_expected")) if planned else None)
		if d <= today_date:  # month so far
			for name, _ in EXTRAS:
				created = flt(row.get(f"{name}_created"))
				extra[name]["created"] += created
				if planned:
					extra[name]["exp"] += flt(row.get(f"{name}_expected"))
					extra[name]["created_p"] += created
			totals["an"] += int(row.get("actual_new") or 0)
			totals["af"] += int(row.get("actual_followup") or 0)
			if planned:
				totals["pn"] += int(row.get("plan_new") or 0)
				totals["pf"] += int(row.get("plan_followup") or 0)
				totals["an_p"] += int(row.get("actual_new") or 0)
				totals["af_p"] += int(row.get("actual_followup") or 0)
				totals["oe"] += int(row.get("orders_expected") or 0)
				totals["or"] += int(row.get("orders_received") or 0)
				totals["ae"] += flt(row.get("order_amount_expected"))
				totals["ar"] += flt(row.get("order_amount_received"))
				totals["pe"] += flt(row.get("payments_expected"))
				totals["pr"] += flt(row.get("payments_received"))
	values = {
		"new_calls_plan_total": totals["pn"],
		"new_calls_actual_total": totals["an"],
		"new_calls_ach": ach(totals["an_p"], totals["pn"]),
		"followup_calls_plan_total": totals["pf"],
		"followup_calls_actual_total": totals["af"],
		"followup_calls_ach": ach(totals["af_p"], totals["pf"]),
		"orders_expected_total": totals["oe"],
		"orders_received_total": totals["or"],
		"orders_ach": ach(totals["or"], totals["oe"]),
		"order_amount_expected_total": round(totals["ae"], 2),
		"order_amount_received_total": round(totals["ar"], 2),
		"order_amount_ach": ach(totals["ar"], totals["ae"]),
		"payments_expected_total": round(totals["pe"], 2),
		"payments_received_total": round(totals["pr"], 2),
		"payments_ach": ach(totals["pr"], totals["pe"]),
	}
	for name, kind in EXTRAS:
		e = extra[name]
		digits = 0 if kind == "count" else 2
		values[f"{name}_expected_total"] = round(e["exp"], digits) if digits else int(e["exp"])
		values[f"{name}_created_total"] = round(e["created"], digits) if digits else int(e["created"])
		values[f"{name}_ach"] = ach(e["created_p"], e["exp"])
	for key, value in values.items():
		parent.set(key, value)
	return values


ACH_FIELDS = ("ach_new", "ach_followup", "orders_ach", "order_amount_ach", "payments_ach") + tuple(
	f"{name}_ach" for name, _ in EXTRAS
)
ACH_TOTALS = ("new_calls_ach", "followup_calls_ach", "orders_ach", "order_amount_ach", "payments_ach") + tuple(
	f"{name}_ach" for name, _ in EXTRAS
)


def stored(value):
	"""Percent columns are NOT NULL: "not applicable" (None) is stored as 0 and recomputed as None by the API."""
	return 0 if value is None else value


class _Row(dict):
	"""A dict with the .get()/.set() pair apply_rules needs (used for plain data and in tests)."""

	def set(self, key, value):
		self[key] = value


# --------------------------------------------------------------- Frappe access


def scorecard_ready():
	return bool(frappe.db.table_exists(DOCTYPE) and frappe.db.table_exists(CHILD))


def scorecard_name(user, start):
	return f"{user}-{getdate(start)}"


def sales_person_of(user):
	"""The enabled Sales Person whose Employee is this user, or None."""
	rows = frappe.db.sql(
		"""SELECT sp.name, sp.parent_sales_person FROM `tabSales Person` sp
		JOIN `tabEmployee` e ON e.name = sp.employee
		WHERE e.user_id = %s AND sp.enabled = 1 LIMIT 1""",
		(user,),
		as_dict=True,
	)
	return rows[0] if rows else None


def reporting_manager_of(parent_sales_person):
	"""The user of the parent Sales Person's Employee (the person's manager), or None."""
	if not parent_sales_person:
		return None
	rows = frappe.db.sql(
		"""SELECT e.user_id FROM `tabSales Person` sp JOIN `tabEmployee` e ON e.name = sp.employee
		WHERE sp.name = %s AND IFNULL(e.user_id, '') <> '' LIMIT 1""",
		(parent_sales_person,),
	)
	return rows[0][0] if rows else None


def eligible_users(require_call_tracker_role=True):
	"""Enabled users in the Sales Person tree (with the Call Tracker User role for the automatic jobs)."""
	role_join = (
		"JOIN `tabHas Role` h ON h.parent = u.name AND h.parenttype = 'User' AND h.role = 'Call Tracker User'"
		if require_call_tracker_role
		else ""
	)
	return frappe.db.sql(
		f"""SELECT DISTINCT u.name FROM `tabSales Person` sp
		JOIN `tabEmployee` e ON e.name = sp.employee
		JOIN `tabUser` u ON u.name = e.user_id AND u.enabled = 1
		{role_join}
		WHERE sp.enabled = 1 AND IFNULL(e.user_id, '') <> ''""",
		pluck="name",
	)


def _insert_missing_days(name, start):
	have = set(frappe.get_all(CHILD, filters={"parent": name}, pluck="date"))
	idx = frappe.db.sql("SELECT COALESCE(MAX(idx), 0) FROM `tabSales Scorecard Day` WHERE parent=%s", (name,))[0][0]
	for d in days_of_month(start):
		if d in have:
			continue
		idx += 1
		frappe.get_doc(
			{
				"doctype": CHILD,
				"parent": name,
				"parenttype": DOCTYPE,
				"parentfield": "daily",
				"idx": idx,
				"date": d,
				"day": d.strftime("%A"),
				"no_target": 0,
			}
		).db_insert()


def ensure_scorecard(user, start):
	"""The month's scorecard for `user`, created if needed (copying last month's targets) and with one row
	per day of the month. Returns its name."""
	start = month_start(start)
	name = scorecard_name(user, start)
	if frappe.db.exists(DOCTYPE, name):
		_insert_missing_days(name, start)
		return name
	person = sales_person_of(user)
	doc = frappe.get_doc(
		{
			"doctype": DOCTYPE,
			"user": user,
			"month_start": start,
			"employee_name": frappe.db.get_value("User", user, "full_name") or user,
			"sales_person": person.name if person else None,
			"reporting_manager": reporting_manager_of(person.parent_sales_person) if person else None,
		}
	)
	for d in days_of_month(start):
		doc.append("daily", {"date": d, "day": d.strftime("%A"), "no_target": 0})
	doc.insert(ignore_permissions=True)
	return doc.name


def month_call_counts(user, start):
	"""{date: {"new": n, "followup": n}} for the user's Call Logs of the month, by Call Category."""
	start = getdate(start)
	end = add_months(start, 1)
	rows = frappe.db.sql(
		"""SELECT DATE(start_time) AS d, custom_call_category AS c, COUNT(*) AS n
		FROM `tabCall Log`
		WHERE owner = %s AND start_time >= %s AND start_time < %s
		  AND custom_call_category IN (%s, %s)
		GROUP BY DATE(start_time), custom_call_category""",
		(user, start, end, CALL_NEW, CALL_FOLLOWUP),
		as_dict=True,
	)
	out = {}
	for r in rows:
		slot = out.setdefault(getdate(r.d), {"new": 0, "followup": 0})
		slot["new" if r.c == CALL_NEW else "followup"] += r.n
	return out


def submit_ready():
	"""True once the "End of Day Submitted" tick (eod_submitted) exists on the day rows (added in the Desk). Until then
	the app gets no `eod.submitted` and "Submit day" does nothing."""
	return bool(frappe.get_meta(CHILD).has_field("eod_submitted"))


def submit_has_time():
	return bool(frappe.get_meta(CHILD).has_field("eod_submitted_at"))


def _same_figure(sent, stored):
	if isinstance(sent, str) or sent is None:
		return (sent or "") == (stored or "")
	return flt(sent) == flt(stored)


def eod_submit_conflicts(submitted, values, stored):
	"""End of day figures that may not change because the day is submitted: the ones in `values` (already cleaned)
	that differ from the `stored` row. A repeat of the same figures (an app retry) is no conflict."""
	if not submitted:
		return []
	return [k for k in EOD_FIGURE_FIELDS if k in values and not _same_figure(values[k], stored.get(k))]


def stamp_submit(row, now):
	"""A tick set in the ERP form gets its time; an untick (reopening the day) clears it."""
	if row.get("eod_submitted"):
		if not row.get("eod_submitted_at"):
			row.set("eod_submitted_at", now)
	elif row.get("eod_submitted_at"):
		row.set("eod_submitted_at", None)


def extras_ready():
	"""True once the Opportunity / Quotation fields exist on both doctypes (they are added in the Desk). Until then
	the scorecard works exactly as before and the new keys are ignored."""
	last = EXTRAS[-1][0]
	return bool(
		frappe.get_meta(CHILD).has_field(f"{last}_ach") and frappe.get_meta(DOCTYPE).has_field(f"{last}_created_total")
	)


def extra_row_fields():
	return [f"{name}_{part}" for name, _ in EXTRAS for part in ("expected", "created", "ach")]


def extra_parent_fields():
	return [f"{name}_{part}" for name, _ in EXTRAS for part in ("expected_total", "created_total", "ach")]


def month_created_counts(user, start):
	"""{date: {"opportunities": n, "opportunity_amount": x, "quotations": n, "quotation_amount": x}} for what the user
	created in the month (owner). Opportunity amount = opportunity_amount; Quotations count while draft or submitted
	(not cancelled) and their amount is rounded_total."""
	start = getdate(start)
	end = add_months(start, 1)
	out = {}
	queries = (
		("opportunities", "opportunity_amount", "SELECT DATE(creation) AS d, COUNT(*) AS n, COALESCE(SUM(opportunity_amount), 0) AS a FROM `tabOpportunity` WHERE owner = %s AND creation >= %s AND creation < %s GROUP BY DATE(creation)"),
		("quotations", "quotation_amount", "SELECT DATE(creation) AS d, COUNT(*) AS n, COALESCE(SUM(rounded_total), 0) AS a FROM `tabQuotation` WHERE owner = %s AND docstatus < 2 AND creation >= %s AND creation < %s GROUP BY DATE(creation)"),
	)
	for count_key, amount_key, sql in queries:
		for r in frappe.db.sql(sql, (user, start, end), as_dict=True):
			slot = out.setdefault(getdate(r.d), {})
			slot[count_key] = int(r.n)
			slot[amount_key] = round(flt(r.a), 2)
	return out


ACTIVITY_KEYS = (
	"opportunities_updated",
	"quotations_updated",
	"new_talk_seconds",
	"new_talk_calls",
	"followup_talk_seconds",
	"followup_talk_calls",
)


def talk_view(seconds, calls):
	"""{"seconds", "calls", "avg_seconds"} of a talk-time total; the average is whole seconds, 0 without calls."""
	seconds, calls = max(int(seconds or 0), 0), max(int(calls or 0), 0)
	return {"seconds": seconds, "calls": calls, "avg_seconds": int(round(seconds / calls)) if calls else 0}


def month_updated_counts(user, start):
	"""{date: {"opportunities_updated": n, "quotations_updated": n}}: the Opportunities / Quotations the user saved a
	change on that day (Track Changes writes one Version row per save, so a record saved 100 times counts once),
	leaving out records created that same day and cancelled Quotations."""
	start = getdate(start)
	end = add_months(start, 1)
	out = {}
	for doctype, key, extra in (
		("Opportunity", "opportunities_updated", ""),
		("Quotation", "quotations_updated", "AND d.docstatus < 2"),
	):
		rows = frappe.db.sql(
			f"""SELECT DATE(v.creation) AS day, COUNT(DISTINCT v.docname) AS n
			FROM `tabVersion` v JOIN `tab{doctype}` d ON d.name = v.docname
			WHERE v.ref_doctype = %s AND v.owner = %s AND v.creation >= %s AND v.creation < %s
			  AND DATE(d.creation) <> DATE(v.creation) {extra}
			GROUP BY DATE(v.creation)""",
			(doctype, user, start, end),
			as_dict=True,
		)
		for r in rows:
			out.setdefault(getdate(r.day), {})[key] = int(r.n)
	return out


def month_talk_stats(user, start):
	"""{date: {"new_talk_seconds", "new_talk_calls", "followup_talk_seconds", "followup_talk_calls"}} from the user's
	answered Call Logs (duration > 0) by Call Category; Internal and uncategorised calls count in neither."""
	start = getdate(start)
	end = add_months(start, 1)
	rows = frappe.db.sql(
		"""SELECT DATE(start_time) AS day, custom_call_category AS c, COUNT(*) AS calls, COALESCE(SUM(duration), 0) AS secs
		FROM `tabCall Log`
		WHERE owner = %s AND start_time >= %s AND start_time < %s AND duration > 0
		  AND custom_call_category IN (%s, %s)
		GROUP BY DATE(start_time), custom_call_category""",
		(user, start, end, CALL_NEW, CALL_FOLLOWUP),
		as_dict=True,
	)
	out = {}
	for r in rows:
		prefix = "new" if r.c == CALL_NEW else "followup"
		slot = out.setdefault(getdate(r.day), {})
		slot[f"{prefix}_talk_seconds"] = int(r.secs)
		slot[f"{prefix}_talk_calls"] = int(r.calls)
	return out


def month_activity(user, start):
	"""{date: {every ACTIVITY_KEYS entry}} for the month (0 where nothing happened), or None when it cannot be
	computed (the answer then carries null for these keys; nothing else is affected)."""
	try:
		updated, talk = month_updated_counts(user, start), month_talk_stats(user, start)
		days = {}
		for d in days_of_month(start):
			merged = {**updated.get(d, {}), **talk.get(d, {})}
			days[d] = {key: int(merged.get(key, 0)) for key in ACTIVITY_KEYS}
		return days
	except Exception:
		frappe.log_error(title="scorecard: activity stats failed", message=frappe.get_traceback())
		return None


def _row_wrapper(row):
	wrapper = _Row(row)
	return wrapper


def refresh_scorecard(name):
	"""Rewrite the read-only fields of one scorecard (actuals, Ach %, month totals) with the DB API. Never saves the
	parent: no Version rows, no hooks."""
	parent_fields = [
		"user", "month_start",
		"new_calls_plan_total", "new_calls_actual_total", "new_calls_ach",
		"followup_calls_plan_total", "followup_calls_actual_total", "followup_calls_ach",
		"orders_expected_total", "orders_received_total", "orders_ach",
		"order_amount_expected_total", "order_amount_received_total", "order_amount_ach",
		"payments_expected_total", "payments_received_total", "payments_ach",
	]
	ready = extras_ready()
	if ready:
		parent_fields += extra_parent_fields()
	parent = frappe.db.get_value(DOCTYPE, name, parent_fields, as_dict=True)
	if not parent:
		return None
	counts = month_call_counts(parent.user, parent.month_start)
	created = month_created_counts(parent.user, parent.month_start) if ready else {}
	fields = ["name", "date", "no_target", "plan_new", "plan_followup", "plan_saved_at", "actual_new",
		"actual_followup", "ach_new", "ach_followup", "orders_expected", "orders_received", "orders_ach",
		"order_amount_expected", "order_amount_received", "order_amount_ach", "payments_expected", "payments_received", "payments_ach"]
	if ready:
		fields += extra_row_fields()
	rows = frappe.get_all(CHILD, filters={"parent": name}, fields=fields, order_by="date asc")
	wrapped = []
	for r in rows:
		w = _row_wrapper(r)
		c = counts.get(getdate(r["date"]), {"new": 0, "followup": 0})
		w["actual_new"], w["actual_followup"] = c["new"], c["followup"]
		if ready:
			day = created.get(getdate(r["date"]), {})
			for key, kind in EXTRAS:
				w[f"{key}_created"] = day.get(key, 0 if kind == "count" else 0.0)
		wrapped.append(w)
	parent_w = _row_wrapper(parent)
	values = apply_rules(parent_w, wrapped, today())
	row_keys = ["actual_new", "actual_followup", "ach_new", "ach_followup", "orders_ach", "order_amount_ach", "payments_ach"]
	if ready:
		row_keys += [f"{key}_{part}" for key, _ in EXTRAS for part in ("created", "ach")]
	for w, original in zip(wrapped, rows):
		changed = {k: stored(w[k]) for k in row_keys if stored(w.get(k)) != original.get(k)}
		if changed:
			frappe.db.set_value(CHILD, w["name"], changed, update_modified=False)
	changed_parent = {k: stored(v) for k, v in values.items() if k in parent and stored(v) != parent.get(k)}
	if changed_parent:
		frappe.db.set_value(DOCTYPE, name, changed_parent, update_modified=False)
	return name


def on_validate(doc, method=None):
	"""Sales Scorecard validate hook (a manager saving in the ERP): targets for today onwards follow the parent
	target and the "No Target" ticks, Ach % and totals are recomputed from the stored actuals."""
	rows = doc.get("daily") or []
	now = now_datetime()
	for row in rows:
		# a plan entered in the ERP form (by a System Manager) counts as a saved plan
		if not row.get("plan_saved_at") and any(
			flt(row.get(k)) > 0
			for k in ("plan_new", "plan_followup", "orders_expected", "order_amount_expected", "payments_expected") + EXTRA_PLAN_FIELDS
		):
			row.set("plan_saved_at", now)
	if submit_ready() and submit_has_time():
		for row in rows:
			stamp_submit(row, now)
	apply_rules(doc, rows, today())
	for row in rows:
		for key in ACH_FIELDS:
			row.set(key, stored(row.get(key)))
	for key in ACH_TOTALS:
		doc.set(key, stored(doc.get(key)))


def on_load(doc, method=None):
	"""Sales Scorecard onload hook: show live actuals when the form opens (in memory only, nothing is written)."""
	try:
		counts = month_call_counts(doc.user, doc.month_start)
		created = month_created_counts(doc.user, doc.month_start) if extras_ready() else None
		rows = doc.get("daily") or []
		for row in rows:
			c = counts.get(getdate(row.get("date")), {"new": 0, "followup": 0})
			row.actual_new, row.actual_followup = c["new"], c["followup"]
			if created is not None:
				day = created.get(getdate(row.get("date")), {})
				for name, kind in EXTRAS:
					row.set(f"{name}_created", day.get(name, 0 if kind == "count" else 0.0))
		apply_rules(doc, rows, today())
		for row in rows:
			for key in ACH_FIELDS:
				row.set(key, stored(row.get(key)))
		for key in ACH_TOTALS:
			doc.set(key, stored(doc.get(key)))
	except Exception:
		frappe.log_error(title="scorecard: onload refresh failed", message=frappe.get_traceback())


def refresh_current_month_scorecards():
	"""Hourly job: refresh this month's scorecards."""
	if not scorecard_ready():
		return
	start = month_start(today())
	for name in frappe.get_all(DOCTYPE, filters={"month_start": start}, pluck="name"):
		try:
			refresh_scorecard(name)
		except Exception:
			frappe.log_error(title="scorecard: hourly refresh failed", message=f"{name}\n{frappe.get_traceback()}")
	frappe.db.commit()  # nosemgrep


def ensure_current_month_scorecards():
	"""Daily job: make sure everyone eligible has this month's scorecard (the month rollover copies targets)."""
	if not scorecard_ready():
		return
	start = month_start(today())
	for user in eligible_users():
		try:
			ensure_scorecard(user, start)
		except Exception:
			frappe.log_error(title="scorecard: ensure failed", message=f"{user}\n{frappe.get_traceback()}")
	frappe.db.commit()  # nosemgrep
