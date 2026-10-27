import unittest
from unittest import mock

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


class TestRef(unittest.TestCase):
	def test_roundtrip(self):
		self.assertEqual(c.parse_ref("CRM-LEAD-2026-06156|7073"), ("CRM-LEAD-2026-06156", "7073"))

	def test_target_names_with_pipes_use_the_last_separator(self):
		self.assertEqual(c.parse_ref("A|B|99"), ("A|B", "99"))

	def test_empty_and_malformed(self):
		for bad in (None, "", "no-separator", "|", "x|", "|y"):
			self.assertIsNone(c.parse_ref(bad), bad)


class TestTidyName(unittest.TestCase):
	def test_spaces(self):
		self.assertEqual(c.tidy_name(" Ankit  Nandkishor   Ingle "), "Ankit Nandkishor Ingle")
		self.assertEqual(c.tidy_name(None), "")


class TestCallerName(unittest.TestCase):
	def names(self, user_name, employee_name):
		def get_value(doctype, name, field):
			return {"User": user_name, "Employee": employee_name}[doctype]

		fake = mock.MagicMock()
		fake.db.get_value.side_effect = get_value
		return mock.patch.object(c, "frappe", fake)

	def test_owner_full_name_first(self):
		with self.names("Shubham Sharma", "Shubham Ratanlal Sharma"):
			self.assertEqual(c.caller_name({"owner": "lfp3@x.com", "call_received_by": "E0052"}), "Shubham Sharma")

	def test_employee_name_when_the_user_has_no_name(self):
		with self.names("", "Ankit  Nandkishor  Ingle "):
			self.assertEqual(c.caller_name({"owner": "a@x.com", "call_received_by": "E1"}), "Ankit Nandkishor Ingle")

	def test_nothing_known(self):
		with self.names(None, None):
			self.assertEqual(c.caller_name({"owner": "a@x.com"}), "")
			self.assertEqual(c.caller_name({}), "")


class TestSummary(unittest.TestCase):
	def test_unchanged_summary_is_not_written(self):
		with mock.patch.object(c, "frappe") as fake:
			self.assertEqual(c._sync_summary({"name": "X", "summary": "Call back"}, "Call back"), "none")
			fake.db.set_value.assert_not_called()

	def test_set_and_clear(self):
		with mock.patch.object(c, "frappe") as fake:
			self.assertEqual(c._sync_summary({"name": "X", "summary": ""}, "Call back"), "set")
			self.assertEqual(c._sync_summary({"name": "X", "summary": "Call back"}, ""), "clear")
			self.assertEqual(fake.db.set_value.call_count, 2)

if __name__ == "__main__":
	unittest.main()
