import unittest

from splinh.custom import call_remark_sync as c


class TestCleanRemark(unittest.TestCase):
	def test_trims_and_keeps_line_breaks(self):
		self.assertEqual(c.clean_remark("  Call again\r\nwith price \n"), "Call again\nwith price")

	def test_empty_and_wrong_types(self):
		for bad in (None, "", "   ", "\n\n", {}, [], ("a",)):
			self.assertEqual(c.clean_remark(bad), "", bad)

	def test_control_characters_are_removed(self):
		self.assertEqual(c.clean_remark("a\x00b\x07c"), "abc")

	def test_capped(self):
		self.assertEqual(len(c.clean_remark("x" * 5000)), c.MAX_REMARK)

	def test_numbers_become_text(self):
		self.assertEqual(c.clean_remark(14), "14")


class TestRemarkHtml(unittest.TestCase):
	def test_same_markup_as_the_text_editor(self):
		self.assertEqual(c.remark_html("Follow up"), '<div class="ql-editor read-mode"><p>Follow up</p></div>')

	def test_html_is_escaped(self):
		out = c.remark_html("<script>alert(1)</script> & more")
		self.assertNotIn("<script>", out)
		self.assertIn("&lt;script&gt;", out)
		self.assertIn("&amp;", out)

	def test_line_breaks_become_br(self):
		self.assertIn("line1<br>line2", c.remark_html("line1\nline2"))


class TestRef(unittest.TestCase):
	def test_roundtrip(self):
		self.assertEqual(c.parse_ref(c.make_ref("CRM-LEAD-2026-06156", "7073")), ("CRM-LEAD-2026-06156", "7073"))

	def test_target_names_with_pipes_use_the_last_separator(self):
		self.assertEqual(c.parse_ref("A|B|99"), ("A|B", "99"))

	def test_empty_and_malformed(self):
		for bad in (None, "", "no-separator", "|", "x|", "|y"):
			self.assertIsNone(c.parse_ref(bad), bad)


class TestPlanSync(unittest.TestCase):
	REF = ("LEAD-1", "5")

	def test_nothing_to_do(self):
		self.assertEqual(c.plan_sync(None, None, ""), "none")
		self.assertEqual(c.plan_sync(None, "LEAD-1", ""), "none")
		self.assertEqual(c.plan_sync(None, None, "text"), "none")

	def test_create(self):
		self.assertEqual(c.plan_sync(None, "LEAD-1", "text"), "create")

	def test_update_same_target(self):
		self.assertEqual(c.plan_sync(self.REF, "LEAD-1", "new text"), "update")

	def test_clearing_the_remark_deletes(self):
		self.assertEqual(c.plan_sync(self.REF, "LEAD-1", ""), "delete")

	def test_no_target_any_more_deletes(self):
		self.assertEqual(c.plan_sync(self.REF, None, "text"), "delete")

	def test_target_changed_moves(self):
		self.assertEqual(c.plan_sync(self.REF, "LEAD-2", "text"), "move")


if __name__ == "__main__":
	unittest.main()
