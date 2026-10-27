"""Run without a site:  python -m unittest splinh.tests.test_scorecard_schema

custom/scorecard_schema.py is the written-down contract of the two Sales Scorecard doctypes (built by hand in the
production Desk, so no app file described them). The scorecard code reads and writes them by FIELDNAME, so a name
missing here would only show up as a broken DILO on a site built from this file. Check every name the code uses.
"""

import unittest

from splinh.custom import scorecard as sc
from splinh.custom import scorecard_schema as schema

# fields refresh_scorecard / the API read or write on the parent (kept in step with custom/scorecard.py)
PARENT_USED = {
	"user", "month_start", "employee_name", "sales_person", "reporting_manager", "daily",
	"new_calls_plan_total", "new_calls_actual_total", "new_calls_ach",
	"followup_calls_plan_total", "followup_calls_actual_total", "followup_calls_ach",
	"orders_expected_total", "orders_received_total", "orders_ach",
	"order_amount_expected_total", "order_amount_received_total", "order_amount_ach",
	"payments_expected_total", "payments_received_total", "payments_ach",
}
CHILD_USED = {
	"date", "day", "no_target", "plan_new", "plan_followup", "plan_saved_at", "actual_new", "actual_followup",
	"ach_new", "ach_followup", "orders_expected", "orders_received", "orders_ach",
	"order_amount_expected", "order_amount_received", "order_amount_ach",
	"payments_expected", "payments_received", "payments_ach", "remarks", "eod_saved_at",
	"eod_submitted", "eod_submitted_at",
}


def by_name(fields):
	return {f["fieldname"]: f for f in fields}


class TestScorecardSchema(unittest.TestCase):
	def test_names_match_the_code(self):
		self.assertEqual((schema.DOCTYPE, schema.CHILD), (sc.DOCTYPE, sc.CHILD))

	def test_fieldnames_are_unique(self):
		for fields in (schema.CHILD_FIELDS, schema.PARENT_FIELDS):
			names = [f["fieldname"] for f in fields]
			self.assertEqual(len(names), len(set(names)))

	def test_child_has_every_field_the_code_uses(self):
		have = by_name(schema.CHILD_FIELDS)
		needed = CHILD_USED | set(sc.ACH_FIELDS) | set(sc.extra_row_fields())
		self.assertEqual(sorted(needed - set(have)), [])

	def test_parent_has_every_field_the_code_uses(self):
		have = by_name(schema.PARENT_FIELDS)
		needed = PARENT_USED | set(sc.ACH_TOTALS) | set(sc.extra_parent_fields())
		self.assertEqual(sorted(needed - set(have)), [])
		self.assertEqual((have["daily"]["fieldtype"], have["daily"]["options"]), ("Table", schema.CHILD))

	def test_automatic_fields_are_read_only_and_typed_for_what_the_code_stores(self):
		child = by_name(schema.CHILD_FIELDS)
		for name in ("actual_new", "actual_followup", "plan_saved_at", "eod_saved_at") + tuple(sc.ACH_FIELDS):
			self.assertEqual(child[name].get("read_only"), 1, name)
		for name in sc.ACH_FIELDS:
			self.assertEqual(child[name]["fieldtype"], "Percent", name)  # stored() writes 0 for "not applicable"
		for name in ("plan_new", "plan_followup", "orders_expected", "orders_received", "no_target", "remarks"):
			self.assertFalse(child[name].get("read_only"), name)  # the rep (and the ERP form) edit these
		for name, kind in sc.EXTRAS:
			want = "Int" if kind == "count" else "Currency"
			self.assertEqual(child[f"{name}_expected"]["fieldtype"], want)
			self.assertEqual(child[f"{name}_created"]["fieldtype"], want)

	def test_child_grid_stays_within_ten_columns(self):
		listed = [f for f in schema.CHILD_FIELDS if f.get("in_list_view")]
		self.assertEqual(len(listed), 10)
		self.assertEqual(sum(f["columns"] for f in listed), 10)  # a grid shows at most 10 column widths

	def test_parent_naming_matches_scorecard_name(self):
		# ensure_scorecard() and the API find a record by f"{user}-{month_start}"
		self.assertEqual(sc.scorecard_name("a@b.com", "2026-10-01"), "a@b.com-2026-10-01")

	def test_settings_field_is_the_one_the_api_reads(self):
		names = [f["fieldname"] for f in schema.SETTINGS_FIELDS]
		self.assertIn("dilo_plan_cutoff_time", names)  # api/scorecard.py _cutoff()


if __name__ == "__main__":
	unittest.main()
