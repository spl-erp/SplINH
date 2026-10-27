"""Run without a site:  python -m unittest splinh.tests.test_call_review_base_fields

The Call Review fields that production has by hand are created by splinh_setup only when missing. The code reads them by
fieldname and checks values against their live option lists, so the defaults must agree with what the code expects.
"""

import unittest

from splinh.custom import call_review as cr
from splinh.custom import scorecard as sc
from splinh.custom import splinh_setup as setup


def by_name():
	return {f["fieldname"]: f for f in setup.CALL_REVIEW_BASE_FIELDS}


class TestCallReviewBaseFields(unittest.TestCase):
	def test_covers_every_review_field_except_the_manager_one(self):
		# custom_call_drop_stage is created by the manager-review step; these are the other three
		self.assertEqual(set(by_name()), set(cr.REVIEW_FIELDS) - {"custom_call_drop_stage"})

	def test_call_category_options_are_what_the_scorecard_counts(self):
		options = cr.parse_options(by_name()["custom_call_category"]["options"])
		self.assertEqual(options, [sc.CALL_NEW, sc.CALL_FOLLOWUP])

	def test_selects_start_with_a_blank_option(self):
		# without it Frappe fills the first option into every new Call Log
		for name in ("custom_next_stage", "custom_call_category"):
			self.assertTrue(by_name()[name]["options"].startswith("\n"), name)
		self.assertTrue(setup.DROP_STAGE_OPTIONS.startswith("\n"))

	def test_next_stage_default_list(self):
		self.assertEqual(
			cr.parse_options(by_name()["custom_next_stage"]["options"]),
			["Meeting", "Follow up", "Proposal / Quotation", "Sample"],
		)

	def test_anchor_chain_starts_at_an_existing_field(self):
		chain = [f["insert_after"] for f in setup.CALL_REVIEW_BASE_FIELDS]
		self.assertEqual(chain[0], "custom_call_drop_stage")
		self.assertEqual(chain[1:], [f["fieldname"] for f in setup.CALL_REVIEW_BASE_FIELDS][:-1])
		# custom_reviewed_at is anchored on the last one, so this step must run before _ensure_call_review_fields
		self.assertEqual(setup.CALL_REVIEW_BASE_FIELDS[-1]["fieldname"], "custom_detailed_action")

	def test_step_runs_before_the_one_that_needs_its_anchor(self):
		import inspect

		source = inspect.getsource(setup.execute)
		self.assertLess(source.index("_ensure_call_review_base_fields"), source.index("_ensure_call_review_fields"))
		self.assertLess(source.index("_ensure_call_manager_review_fields"), source.index("_ensure_call_review_base_fields"))


if __name__ == "__main__":
	unittest.main()
