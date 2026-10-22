import datetime
import unittest

from splinh.custom import call_review as c

NOW = datetime.datetime(2026, 10, 15, 12, 0, 0)
DROP = c.parse_options(c.DROP_STAGE_OPTIONS)
NEXT = c.parse_options(c.NEXT_STAGE_OPTIONS)


class TestOptions(unittest.TestCase):
	def test_parse_trims_and_drops_blanks(self):
		self.assertEqual(c.parse_options("\nNo Need \nProposal / Quatation \n"), ["No Need", "Proposal / Quatation"])
		self.assertEqual(c.parse_options(None), [])

	def test_lists_start_blank_so_no_default_is_applied(self):
		self.assertTrue(c.DROP_STAGE_OPTIONS.startswith("\n"))
		self.assertTrue(c.NEXT_STAGE_OPTIONS.startswith("\n"))

	def test_lists_match_the_app(self):
		self.assertEqual(
			DROP,
			["Not Connected", "No 2-Way Connection", "No Need", "No Trust", "No Money", "No Discount", "Converted"],
		)
		self.assertEqual(NEXT, ["Meeting", "Follow up", "Proposal / Quotation", "Sample"])


class TestChoice(unittest.TestCase):
	def test_required_value(self):
		self.assertEqual(c.clean_choice(" No Money ", DROP, "Call Drop Stage", True), "No Money")

	def test_required_missing(self):
		for bad in (None, "", "   "):
			with self.assertRaises(c.InvalidCallReview):
				c.clean_choice(bad, DROP, "Call Drop Stage", True)

	def test_optional_blank(self):
		for blank in (None, "", "  "):
			self.assertEqual(c.clean_choice(blank, NEXT, "Next Stage", False), "")

	def test_unknown_and_wrong_type(self):
		for bad in ("Maybe", "no money", 5, ["No Money"], {"a": 1}):
			with self.assertRaises(c.InvalidCallReview):
				c.clean_choice(bad, DROP, "Call Drop Stage", True)

	def test_error_is_http_422(self):
		self.assertEqual(c.InvalidCallReview.http_status_code, 422)


class TestAction(unittest.TestCase):
	def test_trim_and_cap(self):
		self.assertEqual(c.clean_action("  Call back Monday \n"), "Call back Monday")
		self.assertEqual(len(c.clean_action("x" * 900)), c.ACTION_MAX)

	def test_empty(self):
		self.assertEqual(c.clean_action(None), "")
		self.assertEqual(c.clean_action("   "), "")

	def test_keeps_line_breaks_drops_control_chars(self):
		self.assertEqual(c.clean_action("a\nb\x00c"), "a\nbc")

	def test_wrong_type(self):
		with self.assertRaises(c.InvalidCallReview):
			c.clean_action(["x"])


class TestReviewedAt(unittest.TestCase):
	def test_phone_local_time(self):
		self.assertEqual(c.clean_reviewed_at("2026-10-15 10:31:07", NOW), datetime.datetime(2026, 10, 15, 10, 31, 7))

	def test_unusable_becomes_now(self):
		for bad in (None, "", "yesterday", "2001-01-01 00:00:00", "2026-12-31 00:00:00", {}, True):
			self.assertEqual(c.clean_reviewed_at(bad, NOW), NOW, bad)


class TestCleanReview(unittest.TestCase):
	def test_full(self):
		out = c.clean_review(
			{
				"call_drop_stage": "No Money",
				"next_stage": "Follow up",
				"next_stage_action": "Call back on Monday with revised quote",
				"reviewed_at": "2026-10-15 10:31:07",
			},
			DROP,
			NEXT,
			NOW,
		)
		self.assertEqual(
			out,
			{
				"custom_call_drop_stage": "No Money",
				"custom_next_stage": "Follow up",
				"custom_detailed_action": "Call back on Monday with revised quote",
				"custom_reviewed_at": datetime.datetime(2026, 10, 15, 10, 31, 7),
			},
		)

	def test_automatic_not_connected(self):
		out = c.clean_review(
			{"call_drop_stage": "Not Connected", "next_stage": "", "next_stage_action": ""}, DROP, NEXT, NOW
		)
		self.assertEqual(out["custom_call_drop_stage"], "Not Connected")
		self.assertEqual(out["custom_next_stage"], "")
		self.assertEqual(out["custom_detailed_action"], "")
		self.assertEqual(out["custom_reviewed_at"], NOW)

	def test_missing_drop_stage_is_invalid(self):
		with self.assertRaises(c.InvalidCallReview):
			c.clean_review({"next_stage": "Meeting"}, DROP, NEXT, NOW)

	def test_bad_next_stage_is_invalid(self):
		with self.assertRaises(c.InvalidCallReview):
			c.clean_review({"call_drop_stage": "No Money", "next_stage": "Proposal / Quatation"}, DROP, NEXT, NOW)


CATEGORY = ["New Call", "Follow Up Call"]


class TestCallCategory(unittest.TestCase):
	def _review(self, **extra):
		raw = {"call_drop_stage": "No Money", **extra}
		return c.clean_review(raw, DROP, NEXT, NOW, allowed_category=CATEGORY)

	def test_valid_category_is_stored(self):
		self.assertEqual(self._review(call_category=" Follow Up Call ")["custom_call_category"], "Follow Up Call")

	def test_blank_category_clears_it(self):
		self.assertEqual(self._review(call_category="")["custom_call_category"], "")

	def test_omitted_category_is_left_alone(self):
		self.assertNotIn("custom_call_category", self._review())
		self.assertNotIn("custom_call_category", self._review(call_category=None))

	def test_unknown_category_is_422(self):
		for bad in ("Followup Call", "new call", 3, ["New Call"]):
			with self.assertRaises(c.InvalidCallReview):
				self._review(call_category=bad)

	def test_field_missing_from_doctype_is_skipped(self):
		out = c.clean_review({"call_drop_stage": "No Money", "call_category": "New Call"}, DROP, NEXT, NOW)
		self.assertNotIn("custom_call_category", out)


class TestOptionItems(unittest.TestCase):
	def test_exact_order_and_blanks_dropped(self):
		self.assertEqual(
			c.option_items("\nMeeting\n Follow up \n\nSample"),
			[
				{"value": "Meeting", "label": "Meeting"},
				{"value": "Follow up", "label": "Follow up"},
				{"value": "Sample", "label": "Sample"},
			],
		)

	def test_not_connected_value_only_when_in_the_list(self):
		self.assertEqual(c.not_connected_value("\nNot Connected\nNo Trust"), "Not Connected")
		self.assertIsNone(c.not_connected_value("\nNo Trust\nNo Need"))
		self.assertIsNone(c.not_connected_value(None))


class TestParseDay(unittest.TestCase):
	TODAY = datetime.date(2026, 10, 15)

	def test_default_is_today(self):
		for empty in (None, "", "  "):
			self.assertEqual(c.parse_day(empty, self.TODAY), self.TODAY)

	def test_valid(self):
		self.assertEqual(c.parse_day("2026-10-14", self.TODAY), datetime.date(2026, 10, 14))

	def test_bad_is_422(self):
		for bad in ("14-10-2026", "2026-13-01", "yesterday", "2026-10-14 10:00:00"):
			with self.assertRaises(c.InvalidCallReview):
				c.parse_day(bad, self.TODAY)


class TestReviewItem(unittest.TestCase):
	ROW = {
		"name": "M-abc-1",
		"custom_call_drop_stage": "No Money",
		"custom_next_stage": "Follow up",
		"custom_call_category": "Follow Up Call",
		"custom_detailed_action": "Call back",
		"custom_reviewed_at": datetime.datetime(2026, 10, 15, 10, 31, 7, 123456),
		"custom_reviewed_by": "rep@x.com",
		"custom_review_source": "erp",
	}

	def test_shape(self):
		self.assertEqual(
			c.review_item(self.ROW),
			{
				"call_log_name": "M-abc-1",
				"call_drop_stage": "No Money",
				"next_stage": "Follow up",
				"next_stage_action": "Call back",
				"call_category": "Follow Up Call",
				"reviewed_at": "2026-10-15 10:31:07",
				"reviewed_by": "rep@x.com",
				"review_source": "erp",
			},
		)

	def test_empty_values_are_empty_strings(self):
		item = c.review_item({"name": "M-abc-2"})
		self.assertEqual(item["reviewed_at"], "")
		self.assertEqual(item["call_category"], "")
		self.assertEqual(item["review_source"], "")


class TestDecide(unittest.TestCase):
	T = datetime.datetime(2026, 10, 15, 10, 0, 0)

	def test_auto_never_replaces_a_real_review(self):
		self.assertEqual(c.decide(self.T, "app", True, False), "skip")
		self.assertEqual(c.decide(self.T, "erp", True, True), "skip")

	def test_auto_on_unreviewed_call_writes(self):
		self.assertEqual(c.decide(None, "", True, False), "write")

	def test_erp_review_conflicts_without_force(self):
		self.assertEqual(c.decide(self.T, "erp", False, False), "conflict")

	def test_force_overwrites_an_erp_review(self):
		self.assertEqual(c.decide(self.T, "erp", False, True), "write")

	def test_app_review_is_overwritten_freely(self):
		self.assertEqual(c.decide(self.T, "app", False, False), "write")
		self.assertEqual(c.decide(None, "", False, False), "write")

	def test_flag_spellings(self):
		for yes in (True, 1, "true", "1", "YES", " on "):
			self.assertTrue(c.flag(yes), yes)
		for no in (None, False, 0, "", "false", "0", "no"):
			self.assertFalse(c.flag(no), no)


class TestReviewChanged(unittest.TestCase):
	BEFORE = {
		"custom_call_drop_stage": "No Money",
		"custom_next_stage": "Meeting",
		"custom_call_category": "New Call",
		"custom_detailed_action": "x",
	}

	def test_no_change(self):
		self.assertFalse(c.review_changed(self.BEFORE, dict(self.BEFORE)))

	def test_whitespace_only_is_no_change(self):
		after = dict(self.BEFORE, custom_detailed_action=" x ")
		self.assertFalse(c.review_changed(self.BEFORE, after))

	def test_each_field_counts(self):
		for field in c.REVIEW_FIELDS:
			after = dict(self.BEFORE, **{field: "changed"})
			self.assertTrue(c.review_changed(self.BEFORE, after), field)

	def test_none_and_blank_are_the_same(self):
		self.assertFalse(c.review_changed({"custom_call_drop_stage": None}, {"custom_call_drop_stage": ""}))

	def test_new_call_log_counts_only_when_a_field_is_filled(self):
		self.assertFalse(c.review_changed(None, {}))
		self.assertTrue(c.review_changed(None, {"custom_call_category": "New Call"}))


if __name__ == "__main__":
	unittest.main()
