"""Run without a site:  python -m unittest splinh.tests.test_sales_kpi_other_activity

The Other Activity block of Sales KPI Entry is a child table (2026-10-13). Checks the
three definitions that have to agree - the child doctype, the parent's Table field and
the Web Form's - because a mismatch only shows up as a broken form in the browser.
"""

import json
import os
import unittest

BASE = os.path.join(os.path.dirname(__file__), "..", "splinh")
CHILD = "Sales KPI Other Activity"
OLD_FIELDS = {
	"other_activity_type",
	"other_activity_count",
	"other_activity_column",
	"other_activity_duration",
	"other_activity_summary",
}


def load(*parts):
	with open(os.path.join(BASE, *parts), encoding="utf-8") as f:
		return json.load(f)


class TestOtherActivityChildTable(unittest.TestCase):
	def test_child_doctype_is_a_table_with_visible_columns(self):
		child = load("doctype", "sales_kpi_other_activity", "sales_kpi_other_activity.json")
		self.assertEqual(child["name"], CHILD)
		self.assertEqual(child["istable"], 1)
		self.assertEqual(child["module"], "SplINH")
		# A Web Form grid only shows in_list_view fields, so every field must be one.
		for field in child["fields"]:
			self.assertEqual(field.get("in_list_view"), 1, field["fieldname"])
		self.assertEqual([f["fieldname"] for f in child["fields"]], child["field_order"])
		self.assertEqual(child["fields"][0].get("reqd"), 1)  # a row needs its Type

	def test_parent_points_at_the_child_and_dropped_the_old_fields(self):
		parent = load("doctype", "sales_kpi_entry", "sales_kpi_entry.json")
		names = {f["fieldname"] for f in parent["fields"]}
		self.assertFalse(names & OLD_FIELDS)
		self.assertFalse(set(parent["field_order"]) & OLD_FIELDS)
		table = next(f for f in parent["fields"] if f["fieldname"] == "other_activities")
		self.assertEqual((table["fieldtype"], table["options"]), ("Table", CHILD))
		self.assertIn("other_activities", parent["field_order"])
		self.assertEqual(parent["field_order"].index("other_activity_section") + 1, parent["field_order"].index("other_activities"))

	def test_web_form_uses_the_table(self):
		form = load("web_form", "sales_kpi_entry", "sales_kpi_entry.json")
		names = {f["fieldname"] for f in form["web_form_fields"]}
		self.assertFalse(names & OLD_FIELDS)
		table = next(f for f in form["web_form_fields"] if f["fieldname"] == "other_activities")
		self.assertEqual((table["fieldtype"], table["options"]), ("Table", CHILD))
		parent = {f["fieldname"] for f in load("doctype", "sales_kpi_entry", "sales_kpi_entry.json")["fields"]}
		for field in form["web_form_fields"]:  # every form field must exist on the doctype
			self.assertIn(field["fieldname"], parent)

	def test_patch_is_registered_after_doctype_sync(self):
		with open(os.path.join(BASE, "..", "patches.txt"), encoding="utf-8") as f:
			text = f.read()
		self.assertLess(text.index("[post_model_sync]"), text.index("move_other_activity_to_child_table"))


if __name__ == "__main__":
	unittest.main()
