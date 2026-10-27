"""DILO (the daily scorecard) for the Call Tracker app. Rules and data access: custom/scorecard.py.

THE REP SETS THE PLAN each morning (new calls, follow-up calls, orders expected, payments expected); the day is
measured against it.

  GET  get_my_dilo?date=YYYY-MM-DD   the caller's day: plan, live actual New / Follow-up calls (from the Call Logs by
                                     Call Category), Ach % against the plan, what they typed, the off-day flag, the
                                     locks, and the month so far with every day of the month. Creates this month's
                                     scorecard on first use.
  POST save_my_dilo                  today only: the plan (until the manager's cut-off time, no late plan), the
                                     end-of-day figures and the off-day tick (until midnight).

Always the caller's own data. Answers carry a `status` word and the HTTP code the app acts on:
200 ok, 403 locked, 404 no_scorecard (a past month with no record), 422 invalid, 503 not_ready.

The old `target` / `is_target_day` aliases are gone (the app no longer reads them); the manager's real targets come
later as a separate layer. Ach % of orders, order amount and payments is `null` until the end-of-day figures of that
day are saved (the app then shows "Not entered" instead of a red 0 %).
"""

import frappe
from frappe.utils import cint, flt, getdate, now_datetime, today

from splinh.custom import scorecard as sc


def _respond(http_status, status, message=None, **extra):
	frappe.local.response["http_status_code"] = http_status
	body = {"status": status, **extra}
	if message:
		body["message"] = message
	return body


def _cutoff():
	try:
		return sc.parse_cutoff(frappe.db.get_single_value("Call Tracker Settings", "dilo_plan_cutoff_time"))
	except Exception:
		return sc.DEFAULT_CUTOFF


def _pct(value):
	return None if value is None else round(flt(value), 2)


def _find_scorecard(user, the_date):
	"""Name of the scorecard of the month of `the_date`, creating THIS month's for any signed-in user (a user outside
	the Sales Person tree simply gets no sales person / manager on it). Other months are only returned if they exist."""
	start = sc.month_start(the_date)
	name = sc.scorecard_name(user, start)
	if frappe.db.exists(sc.DOCTYPE, name):
		return sc.ensure_scorecard(user, start) if start == sc.month_start(today()) else name
	if start == sc.month_start(today()):
		return sc.ensure_scorecard(user, start)
	return None


def _row_view(row, submit=False):
	off = bool(cint(row.no_target))
	planned = sc.has_plan(row)
	eod = bool(row.eod_saved_at)
	return {
		"date": str(row.date),
		"day": row.day or getdate(row.date).strftime("%A"),
		"is_off_day": off,
		"has_plan": planned,
		"plan_new": cint(row.plan_new),
		"plan_followup": cint(row.plan_followup),
		"actual_new": cint(row.actual_new),
		"ach_new": sc.ach(row.actual_new, row.plan_new) if planned else None,
		"actual_followup": cint(row.actual_followup),
		"ach_followup": sc.ach(row.actual_followup, row.plan_followup) if planned else None,
		"orders_expected": cint(row.orders_expected),
		"orders_received": cint(row.orders_received),
		"orders_ach": sc.ach(row.orders_received, row.orders_expected) if eod else None,
		"order_amount_expected": flt(row.order_amount_expected),
		"order_amount_received": flt(row.order_amount_received),
		"order_amount_ach": sc.ach(row.order_amount_received, row.order_amount_expected) if eod else None,
		"payments_expected": flt(row.payments_expected),
		"payments_received": flt(row.payments_received),
		"payments_ach": sc.ach(row.payments_received, row.payments_expected) if eod else None,
		"remarks": row.remarks or "",
		**({"eod_submitted": bool(cint(row.get("eod_submitted")))} if submit else {}),
		**_extra_row_view(row, planned),
	}


def _extra_num(kind, value):
	return flt(value) if kind == "amount" else cint(value)


def _extra_row_view(row, planned):
	"""Opportunities / Quotations of one day: plan (_expected), counted actual (_created) and Ach %. Zero / None until the
	fields exist on the scorecard doctypes."""
	out = {}
	for name, kind in sc.EXTRAS:
		expected, created = row.get(f"{name}_expected"), row.get(f"{name}_created")
		out[f"{name}_expected"] = _extra_num(kind, expected)
		out[f"{name}_created"] = _extra_num(kind, created)
		out[f"{name}_ach"] = sc.ach(created, expected) if planned else None
	return out


def _activity_row(activity, the_date):
	"""One day's updated counts and talk-time numbers (all null when the stats could not be computed)."""
	day = (activity or {}).get(getdate(the_date), {}) if activity is not None else None
	return {key: (None if day is None else cint(day.get(key))) for key in sc.ACTIVITY_KEYS}


def _talk_block(values, prefix):
	"""{"seconds", "calls", "avg_seconds"} from a dict holding <prefix>_talk_seconds / _talk_calls; null if unknown."""
	if values.get(f"{prefix}_talk_seconds") is None:
		return None
	return sc.talk_view(values[f"{prefix}_talk_seconds"], values[f"{prefix}_talk_calls"])


def _submitted_view(row):
	"""eod.submitted / eod.submitted_at (null when there is no time field or the day is not submitted)."""
	at = row.get("eod_submitted_at") if row else None
	return {
		"submitted": bool(row and cint(row.get("eod_submitted"))),
		"submitted_at": at.isoformat(timespec="seconds") if at else None,
	}


def _payload(name, the_date):
	parent = frappe.db.get_value(sc.DOCTYPE, name, "*", as_dict=True)
	rows = frappe.get_all(sc.CHILD, filters={"parent": name}, fields=["*"], order_by="date asc")
	activity = sc.month_activity(parent.user, parent.month_start)  # None when it cannot be computed
	submit = sc.submit_ready()
	days = [{**_row_view(r, submit), **_activity_row(activity, r.date)} for r in rows]
	the_date = getdate(the_date)
	row = next((r for r in rows if getdate(r.date) == the_date), None)
	now = now_datetime()
	lock = sc.locks(now, the_date, _cutoff())
	v = _row_view(row, submit) if row else None
	plan_new = v["plan_new"] if v else 0
	plan_fu = v["plan_followup"] if v else 0
	today_activity = _activity_row(activity, the_date)
	month_activity = (
		None if activity is None else {key: sum(day[key] for day in activity.values()) for key in sc.ACTIVITY_KEYS}
	)
	month_total = month_activity or {key: None for key in sc.ACTIVITY_KEYS}
	month_plan = {
		"new_calls": cint(parent.new_calls_plan_total),
		"followup_calls": cint(parent.followup_calls_plan_total),
		**{name: _extra_num(kind, parent.get(f"{name}_expected_total")) for name, kind in sc.EXTRAS},
	}
	return {
		"status": "ok",
		"date": str(the_date),
		"day": the_date.strftime("%A"),
		"is_off_day": v["is_off_day"] if v else False,
		"plan": {
			"new_calls": plan_new,
			"followup_calls": plan_fu,
			"orders_expected": v["orders_expected"] if v else 0,
			"order_amount_expected": v["order_amount_expected"] if v else 0.0,
			"payments_expected": v["payments_expected"] if v else 0.0,
			"saved": bool(row and row.plan_saved_at),
			**{name: (v[f"{name}_expected"] if v else _extra_num(kind, 0)) for name, kind in sc.EXTRAS},
		},
		"actual": {
			"new_calls": v["actual_new"] if v else 0,
			"followup_calls": v["actual_followup"] if v else 0,
			**{name: (v[f"{name}_created"] if v else _extra_num(kind, 0)) for name, kind in sc.EXTRAS},
			"opportunities_updated": today_activity["opportunities_updated"],
			"quotations_updated": today_activity["quotations_updated"],
		},
		"talk": {
			"new_calls": _talk_block(today_activity, "new"),
			"followup_calls": _talk_block(today_activity, "followup"),
		},
		"ach": {
			"new_calls": v["ach_new"] if v else None,
			"followup_calls": v["ach_followup"] if v else None,
			"orders": v["orders_ach"] if v else None,
			"order_amount": v["order_amount_ach"] if v else None,
			"payments": v["payments_ach"] if v else None,
			**{name: (v[f"{name}_ach"] if v else None) for name, _ in sc.EXTRAS},
		},
		"eod": {
			"orders_received": v["orders_received"] if v else 0,
			"order_amount_received": v["order_amount_received"] if v else 0.0,
			"payments_received": v["payments_received"] if v else 0.0,
			"remarks": v["remarks"] if v else "",
			"saved": bool(row and row.eod_saved_at),
			**(_submitted_view(row) if submit else {}),
		},
		"locks": {
			"plan_editable": lock["plan_editable"],
			"eod_editable": lock["eod_editable"],
			"plan_cutoff": str(_cutoff()),
		},
		"month": {
			"month_start": str(parent.month_start),
			"plan": month_plan,
			"actual": {
				"new_calls": cint(parent.new_calls_actual_total),
				"followup_calls": cint(parent.followup_calls_actual_total),
				**{name: _extra_num(kind, parent.get(f"{name}_created_total")) for name, kind in sc.EXTRAS},
				"opportunities_updated": month_total["opportunities_updated"],
				"quotations_updated": month_total["quotations_updated"],
			},
			"talk": {
				"new_calls": _talk_block(month_total, "new"),
				"followup_calls": _talk_block(month_total, "followup"),
			},
			"ach": {
				# stored by the engine (actual on days with a plan / month plan); n/a while there is no plan
				"new_calls": _pct(parent.new_calls_ach) if month_plan["new_calls"] > 0 else None,
				"followup_calls": _pct(parent.followup_calls_ach) if month_plan["followup_calls"] > 0 else None,
				"orders": sc.ach(parent.orders_received_total, parent.orders_expected_total),
				"order_amount": sc.ach(parent.order_amount_received_total, parent.order_amount_expected_total),
				"payments": sc.ach(parent.payments_received_total, parent.payments_expected_total),
				**{
					name: (_pct(parent.get(f"{name}_ach")) if month_plan[name] > 0 else None)
					for name, _ in sc.EXTRAS
				},
			},
			"orders": {"expected": cint(parent.orders_expected_total), "received": cint(parent.orders_received_total)},
			"order_amount": {
				"expected": flt(parent.order_amount_expected_total),
				"received": flt(parent.order_amount_received_total),
			},
			"payments": {"expected": flt(parent.payments_expected_total), "received": flt(parent.payments_received_total)},
			"days": days,
		},
		"server_time": now.isoformat(timespec="seconds"),
	}


def _user_or_throw():
	user = frappe.session.user
	if not user or user == "Guest":
		frappe.throw("Not permitted", frappe.PermissionError)
	return user


@frappe.whitelist(methods=["GET"])
def get_my_dilo(date=None):
	"""The caller's DILO for `date` (default today) with the month so far."""
	user = _user_or_throw()
	if not sc.scorecard_ready():
		return _respond(503, "not_ready", "The scorecard is not set up yet.")
	try:
		the_date = getdate(date) if date else getdate(today())
	except Exception:
		return _respond(422, "invalid", "date must be YYYY-MM-DD.", field="date")
	name = _find_scorecard(user, the_date)
	if not name:
		return _respond(404, "no_scorecard", "There is no scorecard for you for this month.")
	sc.refresh_scorecard(name)
	frappe.local.flags.commit = True  # a GET writes the refreshed numbers; keep them
	return _payload(name, the_date)


def _truthy(value):
	if isinstance(value, str):
		return value.strip().lower() in ("1", "true", "yes", "y", "on")
	return bool(value)


@frappe.whitelist(methods=["POST"])
def save_my_dilo(
	date=None,
	plan_new_calls=None,
	plan_followup_calls=None,
	orders_expected=None,
	order_amount_expected=None,
	payments_expected=None,
	opportunities_expected=None,
	opportunity_amount_expected=None,
	quotations_expected=None,
	quotation_amount_expected=None,
	off_day=None,
	orders_received=None,
	order_amount_received=None,
	payments_received=None,
	remarks=None,
	submit_eod=None,
	**ignored,
):
	"""Save today's plan (until the cut-off, no late plan) and the end-of-day figures / off-day tick (until midnight).
	`submit_eod: true` saves the figures sent and submits the day: the End of day figures are then locked (an ERP user
	can reopen it). Only the keys sent change; unknown keys are ignored."""
	user = _user_or_throw()
	if not sc.scorecard_ready():
		return _respond(503, "not_ready", "The scorecard is not set up yet.")
	try:
		the_date = getdate(date) if date else getdate(today())
	except Exception:
		return _respond(422, "invalid", "date must be YYYY-MM-DD.", field="date")
	if the_date > getdate(today()):
		return _respond(422, "invalid", "A future day cannot be saved.", field="date")

	try:
		values = {
			"plan_new_calls": sc.clean_count(plan_new_calls, "plan_new_calls"),
			"plan_followup_calls": sc.clean_count(plan_followup_calls, "plan_followup_calls"),
			"orders_expected": sc.clean_count(orders_expected, "orders_expected"),
			"order_amount_expected": sc.clean_amount(order_amount_expected, "order_amount_expected"),
			"payments_expected": sc.clean_amount(payments_expected, "payments_expected"),
			"orders_received": sc.clean_count(orders_received, "orders_received"),
			"order_amount_received": sc.clean_amount(order_amount_received, "order_amount_received"),
			"payments_received": sc.clean_amount(payments_received, "payments_received"),
			"remarks": sc.clean_remarks(remarks),
		}
		extra_plan = {
			"opportunities_expected": opportunities_expected,
			"opportunity_amount_expected": opportunity_amount_expected,
			"quotations_expected": quotations_expected,
			"quotation_amount_expected": quotation_amount_expected,
		}
		for name, kind in sc.EXTRAS:
			key = f"{name}_expected"
			clean = sc.clean_count if kind == "count" else sc.clean_amount
			values[key] = clean(extra_plan[key], key)
	except sc.InvalidDilo as e:
		return _respond(422, "invalid", str(e), field=e.field)
	values = {k: v for k, v in values.items() if v is not None}
	if not sc.extras_ready():  # the Opportunity / Quotation fields are not on the doctypes yet: ignore those keys
		values = {k: v for k, v in values.items() if k not in sc.EXTRA_PLAN_FIELDS}
	if off_day is not None:
		values["off_day"] = 1 if _truthy(off_day) else 0

	submit = _truthy(submit_eod) and sc.submit_ready()  # ignored until the tick exists on the day rows
	lock = sc.locks(now_datetime(), the_date, _cutoff())
	locked = [
		k for k in values
		if (k in sc.SOD_FIELDS and not lock["plan_editable"]) or (k in sc.EOD_FIELDS and not lock["eod_editable"])
	]
	if submit and not lock["eod_editable"]:
		locked.append("submit_eod")
	if locked:
		reason = "past day" if the_date != getdate(today()) else "after the cut-off time"
		return _respond(403, "locked", f"These fields are locked ({reason}).", locked_fields=locked, reason=reason)

	name = _find_scorecard(user, the_date)
	if not name:
		return _respond(404, "no_scorecard", "There is no scorecard for you for this month.")
	row = frappe.db.get_value(sc.CHILD, {"parent": name, "date": the_date}, "name")
	if not row:
		return _respond(404, "no_scorecard", "There is no row for this day.")

	already_submitted = False
	if sc.submit_ready():
		stored = frappe.db.get_value(sc.CHILD, row, ["eod_submitted", *sc.EOD_FIGURE_FIELDS], as_dict=True)
		already_submitted = bool(cint(stored.eod_submitted))
		conflicts = sc.eod_submit_conflicts(already_submitted, values, stored)
		if conflicts:
			return _respond(
				403, "locked", "End of day already submitted.", locked_fields=conflicts, reason="End of day already submitted"
			)
		if already_submitted:  # a repeat of the same figures (app retry): nothing to write
			values = {k: v for k, v in values.items() if k not in sc.EOD_FIGURE_FIELDS}

	if values or (submit and not already_submitted):
		now = now_datetime()
		write = {}
		for key, value in values.items():
			if key == "off_day":
				write["no_target"] = value
			else:
				write[sc.ROW_FIELD.get(key, key)] = value
		if any(k in sc.SOD_FIELDS for k in values):
			write["plan_saved_at"] = now
		if any(k in ("orders_received", "order_amount_received", "payments_received", "remarks") for k in values):
			write["eod_saved_at"] = now
		if submit and not already_submitted:
			write["eod_submitted"] = 1
			write["eod_saved_at"] = now  # submitting finalises the End of day, so its Ach % become real numbers
			if sc.submit_has_time():
				write["eod_submitted_at"] = now
		frappe.db.set_value(sc.CHILD, row, write, update_modified=False)
	sc.refresh_scorecard(name)
	return _payload(name, the_date)
