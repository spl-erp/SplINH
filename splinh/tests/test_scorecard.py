import datetime
import unittest

from splinh.custom import scorecard as sc

D = datetime.date
TODAY = D(2026, 10, 24)


def row(day, **kw):
	base = {"date": D(2026, 10, day), "no_target": 0, "plan_new": 0, "plan_followup": 0, "plan_saved_at": None,
		"actual_new": 0, "actual_followup": 0, "orders_expected": 0, "orders_received": 0,
		"order_amount_expected": 0, "order_amount_received": 0, "payments_expected": 0, "payments_received": 0}
	base.update(kw)
	return sc._Row(base)


def planned(day, plan_new=5, plan_followup=8, **kw):
	"""A row whose plan the rep has saved."""
	return row(day, plan_new=plan_new, plan_followup=plan_followup, plan_saved_at=datetime.datetime(2026, 10, day, 9, 30), **kw)


def parent():
	return sc._Row({})


class TestDates(unittest.TestCase):
	def test_month_start_and_days(self):
		self.assertEqual(sc.month_start("2026-10-24"), D(2026, 10, 1))
		days = sc.days_of_month(D(2026, 10, 1))
		self.assertEqual((len(days), days[0], days[-1]), (31, D(2026, 10, 1), D(2026, 10, 31)))
		self.assertEqual(len(sc.days_of_month(D(2026, 2, 1))), 28)
		self.assertEqual(len(sc.days_of_month(D(2028, 2, 10))), 29)


class TestAch(unittest.TestCase):
	def test_ratio(self):
		self.assertEqual(sc.ach(14, 15), 93.33)
		self.assertEqual(sc.ach(17, 20), 85.0)
		self.assertEqual(sc.ach(30, 15), 200.0)

	def test_no_target_gives_none(self):
		for target in (0, None, -1, "0"):
			self.assertIsNone(sc.ach(5, target))

	def test_zero_actual(self):
		self.assertEqual(sc.ach(0, 15), 0.0)
		self.assertEqual(sc.ach(None, 15), 0.0)


class TestCleaning(unittest.TestCase):
	def test_counts(self):
		self.assertEqual(sc.clean_count("3", "x"), 3)
		self.assertEqual(sc.clean_count(2.0, "x"), 2)
		self.assertIsNone(sc.clean_count(None, "x"))
		self.assertIsNone(sc.clean_count("  ", "x"))

	def test_bad_counts(self):
		for bad in (-1, 2.5, "abc", True, 10**9, [1], {}):
			with self.assertRaises(sc.InvalidDilo):
				sc.clean_count(bad, "orders_expected")

	def test_amounts(self):
		self.assertEqual(sc.clean_amount("150000.456", "x"), 150000.46)
		self.assertEqual(sc.clean_amount(0, "x"), 0.0)
		self.assertIsNone(sc.clean_amount(None, "x"))

	def test_bad_amounts(self):
		for bad in (-5, "x", True, float("nan"), 10**15):
			with self.assertRaises(sc.InvalidDilo):
				sc.clean_amount(bad, "payments_expected")

	def test_remarks(self):
		self.assertEqual(sc.clean_remarks("  1 order slipped\x00 \n PO awaited "), "1 order slipped\nPO awaited")
		self.assertEqual(len(sc.clean_remarks("x" * 900)), sc.REMARKS_MAX)
		self.assertIsNone(sc.clean_remarks(None))
		self.assertEqual(sc.clean_remarks("   "), "")
		with self.assertRaises(sc.InvalidDilo):
			sc.clean_remarks(["x"])


class TestCutoff(unittest.TestCase):
	def test_values(self):
		self.assertEqual(sc.parse_cutoff(datetime.timedelta(hours=11, minutes=30)), datetime.time(11, 30))
		self.assertEqual(sc.parse_cutoff("10:15:00"), datetime.time(10, 15))
		self.assertEqual(sc.parse_cutoff(datetime.time(9, 0)), datetime.time(9, 0))

	def test_missing_or_zero_means_default_noon(self):
		for empty in (None, "", datetime.timedelta(0), "00:00:00", "garbage", datetime.time(0, 0)):
			self.assertEqual(sc.parse_cutoff(empty), sc.DEFAULT_CUTOFF, empty)


class TestLocks(unittest.TestCase):
	CUT = datetime.time(12, 0)

	def at(self, hour, minute=0):
		return datetime.datetime(2026, 10, 24, hour, minute)

	def test_before_cutoff_today(self):
		self.assertEqual(sc.locks(self.at(9), D(2026, 10, 24), self.CUT), {"plan_editable": True, "eod_editable": True})

	def test_after_cutoff_today(self):
		self.assertEqual(sc.locks(self.at(12, 0), D(2026, 10, 24), self.CUT), {"plan_editable": False, "eod_editable": True})
		self.assertEqual(sc.locks(self.at(18), D(2026, 10, 24), self.CUT), {"plan_editable": False, "eod_editable": True})

	def test_just_before_cutoff(self):
		self.assertTrue(sc.locks(self.at(11, 59), D(2026, 10, 24), self.CUT)["plan_editable"])

	def test_past_and_future_days_are_locked(self):
		for day in (D(2026, 10, 23), D(2026, 10, 25)):
			self.assertEqual(sc.locks(self.at(9), day, self.CUT), {"plan_editable": False, "eod_editable": False})


class TestApplyRules(unittest.TestCase):
	def run_rules(self, rows):
		p = parent()
		sc.apply_rules(p, rows, TODAY)
		return p

	def test_ach_is_actual_over_the_saved_plan(self):
		r = planned(24, plan_new=15, plan_followup=20, actual_new=14, actual_followup=17)
		self.run_rules([r])
		self.assertEqual((r["ach_new"], r["ach_followup"]), (93.33, 85.0))

	def test_ach_is_uncapped(self):
		r = planned(24, plan_new=5, actual_new=8)
		self.run_rules([r])
		self.assertEqual(r["ach_new"], 160.0)

	def test_day_without_a_saved_plan_has_no_ach(self):
		r = row(24, plan_new=5, actual_new=8)           # numbers present but never saved by the rep
		self.run_rules([r])
		self.assertIsNone(r["ach_new"])
		self.assertIsNone(r["ach_followup"])

	def test_off_day_has_no_ach_even_with_a_saved_plan(self):
		r = planned(24, actual_new=3, no_target=1)
		self.run_rules([r])
		self.assertIsNone(r["ach_new"])

	def test_has_plan(self):
		self.assertTrue(sc.has_plan(planned(24)))
		self.assertFalse(sc.has_plan(row(24)))
		self.assertFalse(sc.has_plan(planned(24, no_target=1)))

	def test_zero_plan_gives_no_ach(self):
		r = planned(24, plan_new=0, plan_followup=0, actual_new=5)
		self.run_rules([r])
		self.assertIsNone(r["ach_new"])

	def test_month_so_far_plan_and_ach_use_only_days_with_a_plan(self):
		rows = [planned(22, actual_new=10, actual_followup=15), planned(23, actual_new=4, actual_followup=8),
			row(24, actual_new=30, actual_followup=30),        # no plan: left out of plan and Ach, still in the actual
			planned(25), planned(26)]                           # future days
		p = self.run_rules(rows)
		self.assertEqual(p["new_calls_plan_total"], 10)         # two planned days x 5
		self.assertEqual(p["new_calls_actual_total"], 44)       # every call counts in the month actual
		self.assertEqual(p["new_calls_ach"], 140.0)             # (10 + 4) / 10: only days with a plan
		self.assertEqual(p["followup_calls_plan_total"], 16)
		self.assertEqual(p["followup_calls_actual_total"], 53)
		self.assertEqual(p["followup_calls_ach"], 143.75)       # (15 + 8) / 16

	def test_off_days_are_left_out_of_the_month_plan(self):
		rows = [planned(22, actual_new=5), planned(23, actual_new=5, no_target=1)]
		p = self.run_rules(rows)
		self.assertEqual(p["new_calls_plan_total"], 5)
		self.assertEqual(p["new_calls_actual_total"], 10)       # calls on the off day still count as actual
		self.assertEqual(p["new_calls_ach"], 100.0)             # only the planned day: 5 of 5

	def test_future_days_are_not_in_the_month_so_far(self):
		p = self.run_rules([planned(24), planned(27, plan_new=50)])
		self.assertEqual(p["new_calls_plan_total"], 5)

	def test_orders_and_payments(self):
		r = planned(24, orders_expected=3, orders_received=2, payments_expected=150000, payments_received=75000)
		p = self.run_rules([r])
		self.assertEqual((r["orders_ach"], r["payments_ach"]), (66.67, 50.0))
		self.assertEqual((p["orders_expected_total"], p["orders_received_total"]), (3, 2))
		self.assertEqual((p["payments_expected_total"], p["payments_received_total"]), (150000.0, 75000.0))
		self.assertEqual((p["orders_ach"], p["payments_ach"]), (66.67, 50.0))

	def test_nothing_expected_gives_no_ach(self):
		r = planned(24, orders_received=2)
		self.run_rules([r])
		self.assertIsNone(r["orders_ach"])

	def test_empty_month(self):
		p = self.run_rules([])
		self.assertEqual(p["new_calls_plan_total"], 0)
		self.assertIsNone(p["new_calls_ach"])

	def test_the_managers_target_fields_are_ignored(self):
		r = planned(24, plan_new=5, actual_new=5)
		r["target_new"] = 99
		self.run_rules([r])
		self.assertEqual(r["ach_new"], 100.0)
		self.assertEqual(r["target_new"], 99)

	def test_rerun_is_stable(self):
		r = planned(24, actual_new=4)
		self.run_rules([r])
		first = dict(r)
		self.run_rules([r])
		self.assertEqual(first, dict(r))


class TestOrderAmount(unittest.TestCase):
	def test_row_ach_and_month_totals(self):
		rows = [
			planned(20, order_amount_expected=100000, order_amount_received=50000),
			planned(21, order_amount_expected=50000, order_amount_received=100000),
			row(22, order_amount_expected=999, order_amount_received=1),  # no plan saved: left out of the month totals
			planned(30, order_amount_expected=777),  # future day: not in the month so far
		]
		p = parent()
		v = sc.apply_rules(p, rows, TODAY)
		self.assertEqual(rows[0]["order_amount_ach"], 50.0)
		self.assertEqual(rows[1]["order_amount_ach"], 200.0)  # uncapped
		self.assertEqual(v["order_amount_expected_total"], 150000.0)
		self.assertEqual(v["order_amount_received_total"], 150000.0)
		self.assertEqual(p["order_amount_ach"], 100.0)

	def test_nothing_expected_gives_none(self):
		rows = [planned(20, order_amount_received=5)]
		sc.apply_rules(parent(), rows, TODAY)
		self.assertIsNone(rows[0]["order_amount_ach"])

	def test_amount_cleaning(self):
		self.assertEqual(sc.clean_amount("85000", "order_amount_expected"), 85000.0)
		with self.assertRaises(sc.InvalidDilo):
			sc.clean_amount(-1, "order_amount_expected")

	def test_ach_fields_listed(self):
		self.assertIn("order_amount_ach", sc.ACH_FIELDS)
		self.assertIn("order_amount_ach", sc.ACH_TOTALS)


class TestOpportunitiesAndQuotations(unittest.TestCase):
	def test_row_ach_is_created_over_the_saved_plan(self):
		r = planned(
			24, opportunities_expected=4, opportunities_created=3, opportunity_amount_expected=200000,
			opportunity_amount_created=50000, quotations_expected=2, quotations_created=5,
			quotation_amount_expected=100000, quotation_amount_created=80000,
		)
		sc.apply_rules(parent(), [r], TODAY)
		self.assertEqual(
			(r["opportunities_ach"], r["opportunity_amount_ach"], r["quotations_ach"], r["quotation_amount_ach"]),
			(75.0, 25.0, 250.0, 80.0),
		)

	def test_no_plan_or_off_day_gives_no_ach(self):
		for r in (row(24, opportunities_expected=4, opportunities_created=3), planned(24, no_target=1, quotations_expected=2, quotations_created=1)):
			sc.apply_rules(parent(), [r], TODAY)
			self.assertIsNone(r["opportunities_ach"])
			self.assertIsNone(r["quotations_ach"])

	def test_zero_plan_gives_no_ach(self):
		r = planned(24, opportunities_created=2)
		sc.apply_rules(parent(), [r], TODAY)
		self.assertIsNone(r["opportunities_ach"])

	def test_month_totals_actual_counts_every_day_ach_only_planned_days(self):
		rows = [
			planned(22, opportunities_expected=2, opportunities_created=1, quotation_amount_expected=1000, quotation_amount_created=500),
			planned(23, opportunities_expected=2, opportunities_created=3, quotation_amount_expected=1000, quotation_amount_created=1500),
			row(24, opportunities_created=10, quotation_amount_created=250.5),  # no plan: in the actual, not in plan or Ach
			planned(27, opportunities_expected=50),  # future day
		]
		p = parent()
		v = sc.apply_rules(p, rows, TODAY)
		self.assertEqual((v["opportunities_expected_total"], v["opportunities_created_total"]), (4, 14))
		self.assertEqual(v["opportunities_ach"], 100.0)  # (1 + 3) / 4
		self.assertEqual((v["quotation_amount_expected_total"], v["quotation_amount_created_total"]), (2000.0, 2250.5))
		self.assertEqual(v["quotation_amount_ach"], 100.0)  # (500 + 1500) / 2000
		self.assertIsNone(v["quotations_ach"])
		self.assertEqual(p["opportunities_created_total"], 14)

	def test_ach_fields_listed_and_planned_in_the_lock_group(self):
		for name, _ in sc.EXTRAS:
			self.assertIn(f"{name}_ach", sc.ACH_FIELDS)
			self.assertIn(f"{name}_ach", sc.ACH_TOTALS)
			self.assertIn(f"{name}_expected", sc.SOD_FIELDS)

	def test_kinds(self):
		self.assertEqual(dict(sc.EXTRAS), {"opportunities": "count", "opportunity_amount": "amount", "quotations": "count", "quotation_amount": "amount"})


class TestTalkView(unittest.TestCase):
	def test_average_is_whole_seconds(self):
		self.assertEqual(sc.talk_view(1380, 6), {"seconds": 1380, "calls": 6, "avg_seconds": 230})
		self.assertEqual(sc.talk_view(100, 3), {"seconds": 100, "calls": 3, "avg_seconds": 33})
		self.assertEqual(sc.talk_view(101, 2)["avg_seconds"], 50)

	def test_no_calls_gives_zeros(self):
		self.assertEqual(sc.talk_view(0, 0), {"seconds": 0, "calls": 0, "avg_seconds": 0})
		self.assertEqual(sc.talk_view(None, None), {"seconds": 0, "calls": 0, "avg_seconds": 0})

	def test_never_negative(self):
		self.assertEqual(sc.talk_view(-5, -1), {"seconds": 0, "calls": 0, "avg_seconds": 0})

	def test_month_average_comes_from_the_totals(self):
		days = [(100, 1), (900, 9)]  # daily averages 100 and 100, but 10 calls of 100s
		self.assertEqual(sc.talk_view(sum(d[0] for d in days), sum(d[1] for d in days))["avg_seconds"], 100)
		days = [(60, 1), (60, 3)]  # daily averages 60 and 20: the month average is 120 / 4 = 30, not 40
		self.assertEqual(sc.talk_view(sum(d[0] for d in days), sum(d[1] for d in days))["avg_seconds"], 30)


class TestSubmitDay(unittest.TestCase):
	STORED = {"orders_received": 4, "order_amount_received": 150000.0, "payments_received": 75000.0, "remarks": "ok"}

	def test_not_submitted_never_conflicts(self):
		self.assertEqual(sc.eod_submit_conflicts(False, {"orders_received": 9, "remarks": "x"}, self.STORED), [])

	def test_same_figures_are_a_retry_not_a_conflict(self):
		sent = {"orders_received": 4, "order_amount_received": 150000, "payments_received": 75000.0, "remarks": "ok"}
		self.assertEqual(sc.eod_submit_conflicts(True, sent, self.STORED), [])

	def test_changed_figures_conflict(self):
		sent = {"orders_received": 5, "remarks": "changed", "payments_received": 75000}
		self.assertEqual(sc.eod_submit_conflicts(True, sent, self.STORED), ["orders_received", "remarks"])

	def test_plan_and_other_keys_are_not_locked(self):
		self.assertEqual(sc.eod_submit_conflicts(True, {"plan_new_calls": 9, "orders_expected": 1, "off_day": 1}, self.STORED), [])

	def test_empty_remarks_equal_missing(self):
		self.assertEqual(sc.eod_submit_conflicts(True, {"remarks": ""}, {"remarks": None}), [])

	def test_stamp_tick_untick(self):
		now = datetime.datetime(2026, 10, 27, 9, 57, 12)
		ticked = sc._Row({"eod_submitted": 1})
		sc.stamp_submit(ticked, now)
		self.assertEqual(ticked["eod_submitted_at"], now)
		sc.stamp_submit(ticked, datetime.datetime(2026, 10, 27, 12, 0))  # already stamped: unchanged
		self.assertEqual(ticked["eod_submitted_at"], now)
		ticked["eod_submitted"] = 0  # reopened in the ERP
		sc.stamp_submit(ticked, now)
		self.assertIsNone(ticked["eod_submitted_at"])
		idle = sc._Row({"eod_submitted": 0})
		sc.stamp_submit(idle, now)
		self.assertNotIn("eod_submitted_at", idle)

	def test_figure_fields_match_the_eod_group(self):
		self.assertEqual(set(sc.EOD_FIGURE_FIELDS), set(sc.EOD_FIELDS) - {"off_day"})


class TestLockGroups(unittest.TestCase):
	def test_plan_group_and_eod_group(self):
		self.assertEqual(
			set(sc.SOD_FIELDS),
			{"plan_new_calls", "plan_followup_calls", "orders_expected", "order_amount_expected", "payments_expected",
				"opportunities_expected", "opportunity_amount_expected", "quotations_expected", "quotation_amount_expected"},
		)
		self.assertEqual(set(sc.EOD_FIELDS), {"orders_received", "order_amount_received", "payments_received", "remarks", "off_day"})
		self.assertEqual(sc.ROW_FIELD, {"plan_new_calls": "plan_new", "plan_followup_calls": "plan_followup"})
		self.assertFalse(set(sc.SOD_FIELDS) & set(sc.EOD_FIELDS))


if __name__ == "__main__":
	unittest.main()
