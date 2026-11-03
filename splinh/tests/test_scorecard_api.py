"""Offline tests for api/scorecard.py save_my_dilo with the 1.7.0 additions (visits, the rep's own call counts, Off Day).

No site and no database: the day row, the clock and the readiness checks are replaced, and what the API writes is
captured. The rules themselves are tested in test_scorecard.py; here: what is written, what is refused, and when.

Run:  cd apps/splinh && ../../env/bin/python -m unittest splinh.tests.test_scorecard_api
"""

import datetime
import unittest
from contextlib import ExitStack
from unittest import mock

import frappe

from splinh.api import scorecard as api
from splinh.custom import scorecard as sc

NOON_PLAN_CUTOFF = datetime.time(12, 0)
MORNING = datetime.datetime(2026, 10, 24, 10, 0)  # before the cut-off
EVENING = datetime.datetime(2026, 10, 24, 18, 0)  # after the cut-off, same day


class Env:
	"""One signed-in rep with one day row; records every write."""

	def __init__(self, now=MORNING, submitted=0, stored=None, **ready):
		self.now = now
		self.writes = []
		self.stored = frappe._dict({"eod_submitted": submitted, **(stored or {})})
		self.ready = {"extras": True, "visits": True, "reported": True, "submit": True, **ready}

	def get_value(self, doctype, filters, fields=None, as_dict=False):
		if isinstance(filters, dict):
			return "ROW-1"  # the day row
		return self.stored  # the stored figures behind the Submit lock

	def set_value(self, doctype, name, values, update_modified=True):
		self.writes.append(dict(values))

	def __enter__(self):
		fake = mock.MagicMock()
		fake.session.user = "rep@example.com"
		fake.db.get_value.side_effect = self.get_value
		fake.db.set_value.side_effect = self.set_value
		fake.get_meta.return_value.has_field.return_value = True
		fake.local.response = {}  # the HTTP status the API sets lands here
		self.stack = ExitStack()
		for p in (
			mock.patch.object(api, "frappe", fake),
			mock.patch.object(api, "now_datetime", lambda: self.now),
			mock.patch.object(api, "today", lambda: str(self.now.date())),
			mock.patch.object(api, "_cutoff", lambda: NOON_PLAN_CUTOFF),
			mock.patch.object(api, "_find_scorecard", lambda user, d: "SC-1"),
			mock.patch.object(api, "_payload", lambda name, d: {"status": "ok"}),
			mock.patch.object(sc, "scorecard_ready", lambda: True),
			mock.patch.object(sc, "refresh_scorecard", lambda name: None),
			mock.patch.object(sc, "extras_ready", lambda: self.ready["extras"]),
			mock.patch.object(sc, "visits_ready", lambda: self.ready["visits"]),
			mock.patch.object(sc, "reported_ready", lambda: self.ready["reported"]),
			mock.patch.object(sc, "submit_ready", lambda: self.ready["submit"]),
			mock.patch.object(sc, "submit_has_time", lambda: False),
		):
			self.stack.enter_context(p)
		self.response = fake.local.response
		return self

	def __exit__(self, *exc):
		self.stack.close()

	def save(self, **kw):
		kw.setdefault("date", str(self.now.date()))
		return api.save_my_dilo(**kw)


class TestRepOwnedCallCounts(unittest.TestCase):
	def test_a_typed_number_is_stored_with_its_flag_and_stamps_the_end_of_day(self):
		with Env(now=EVENING) as env:
			body = env.save(new_calls_reported=12)
			self.assertEqual(body["status"], "ok")
			(write,) = env.writes
		self.assertEqual((write["new_calls_reported"], write["new_calls_reported_set"]), (12, 1))
		self.assertIn("eod_saved_at", write)
		self.assertNotIn("followup_calls_reported", write)  # an absent key changes nothing

	def test_null_clears_the_number_and_the_flag(self):
		with Env() as env:
			env.save(new_calls_reported=12, followup_calls_reported=None)
			(write,) = env.writes
		self.assertEqual((write["new_calls_reported"], write["new_calls_reported_set"]), (12, 1))
		self.assertEqual((write["followup_calls_reported"], write["followup_calls_reported_set"]), (0, 0))

	def test_empty_text_counts_as_null_and_a_typed_zero_is_a_real_zero(self):
		with Env() as env:
			env.save(new_calls_reported="", followup_calls_reported=0)
			(write,) = env.writes
		self.assertEqual((write["new_calls_reported"], write["new_calls_reported_set"]), (0, 0))
		self.assertEqual((write["followup_calls_reported"], write["followup_calls_reported_set"]), (0, 1))

	def test_ignored_until_the_desk_fields_exist(self):
		with Env(reported=False) as env:
			body = env.save(new_calls_reported=12)
			self.assertEqual(body["status"], "ok")
			self.assertEqual(env.writes, [])

	def test_limits_and_bad_values(self):
		for bad in (10000, -1, 2.5, "abc", True):
			with Env() as env:
				body = env.save(new_calls_reported=bad)
				self.assertEqual((body["status"], body["field"]), ("invalid", "new_calls_reported"), repr(bad))
				self.assertEqual(env.response["http_status_code"], 422)
				self.assertEqual(env.writes, [])
		with Env() as env:
			env.save(new_calls_reported=9999)
			self.assertEqual(env.writes[0]["new_calls_reported"], 9999)

	def test_locked_after_midnight_like_the_other_end_of_day_figures(self):
		with Env() as env:
			body = env.save(date="2026-10-23", new_calls_reported=3)
			self.assertEqual(body["status"], "locked")
			self.assertEqual(body["locked_fields"], ["new_calls_reported"])
			self.assertEqual(env.response["http_status_code"], 403)
			self.assertEqual(env.writes, [])


class TestVisits(unittest.TestCase):
	def test_plan_visits_before_the_cutoff_are_saved_and_count_as_the_plan(self):
		with Env() as env:
			body = env.save(plan_new_visits=4, plan_followup_visits=6)
			self.assertEqual(body["status"], "ok")
			(write,) = env.writes
		self.assertEqual((write["plan_new_visits"], write["plan_followup_visits"]), (4, 6))
		self.assertIn("plan_saved_at", write)
		self.assertNotIn("eod_saved_at", write)

	def test_plan_visits_after_the_cutoff_are_locked(self):
		with Env(now=EVENING) as env:
			body = env.save(plan_new_visits=4)
			self.assertEqual((body["status"], body["locked_fields"]), ("locked", ["plan_new_visits"]))
			self.assertEqual(env.writes, [])

	def test_visits_received_are_end_of_day_so_allowed_after_the_cutoff(self):
		with Env(now=EVENING) as env:
			env.save(new_visits_received=3, followup_visits_received=2)
			(write,) = env.writes
		self.assertEqual((write["new_visits_received"], write["followup_visits_received"]), (3, 2))
		self.assertIn("eod_saved_at", write)
		self.assertNotIn("plan_saved_at", write)

	def test_limit_9999(self):
		with Env() as env:
			body = env.save(plan_new_visits=10000)
			self.assertEqual((body["status"], body["field"]), ("invalid", "plan_new_visits"))
		with Env() as env:
			env.save(plan_new_visits=9999)
			self.assertEqual(env.writes[0]["plan_new_visits"], 9999)

	def test_ignored_until_the_desk_fields_exist(self):
		with Env(visits=False) as env:
			env.save(plan_new_visits=4, new_visits_received=1)
			self.assertEqual(env.writes, [])


class TestSubmittedDay(unittest.TestCase):
	def test_changing_a_visit_or_a_call_count_is_refused(self):
		stored = {"new_visits_received": 3, "new_calls_reported": 12, "new_calls_reported_set": 1}
		with Env(now=EVENING, submitted=1, stored=stored) as env:
			body = env.save(new_visits_received=4, new_calls_reported=11)
			self.assertEqual(body["status"], "locked")
			self.assertEqual(body["reason"], "End of day already submitted")
			self.assertEqual(sorted(body["locked_fields"]), ["new_calls_reported", "new_visits_received"])
			self.assertEqual(env.response["http_status_code"], 403)
			self.assertEqual(env.writes, [])

	def test_a_repeat_of_the_same_figures_is_a_retry_not_a_conflict(self):
		stored = {"new_visits_received": 3, "new_calls_reported": 12, "new_calls_reported_set": 1,
			"followup_calls_reported": 0, "followup_calls_reported_set": 0}
		with Env(now=EVENING, submitted=1, stored=stored) as env:
			body = env.save(new_visits_received=3, new_calls_reported=12, followup_calls_reported=None)
			self.assertEqual(body["status"], "ok")
			self.assertEqual(env.writes, [])

	def test_clearing_a_typed_count_after_submit_is_refused_and_typing_one_where_none_was_too(self):
		with Env(now=EVENING, submitted=1, stored={"new_calls_reported": 12, "new_calls_reported_set": 1}) as env:
			self.assertEqual(env.save(new_calls_reported=None)["locked_fields"], ["new_calls_reported"])
		with Env(now=EVENING, submitted=1, stored={"new_calls_reported": 0, "new_calls_reported_set": 0}) as env:
			self.assertEqual(env.save(new_calls_reported=0)["locked_fields"], ["new_calls_reported"])  # a typed 0 is a change

	def test_the_visit_plan_is_not_frozen_by_submit_only_by_the_cutoff(self):
		with Env(now=MORNING, submitted=1, stored={}) as env:
			self.assertEqual(env.save(plan_new_visits=5)["status"], "ok")
			self.assertEqual(env.writes[0]["plan_new_visits"], 5)


class TestOffDay(unittest.TestCase):
	def test_off_day_after_the_plan_cutoff_and_after_submit_still_works_then_undo(self):
		with Env(now=EVENING, submitted=1, stored={"orders_received": 2}) as env:
			self.assertEqual(env.save(off_day=True)["status"], "ok")
			self.assertEqual(env.save(off_day=False)["status"], "ok")
			self.assertEqual([w["no_target"] for w in env.writes], [1, 0])

	def test_off_day_on_a_day_that_already_has_a_plan_is_allowed(self):
		with Env() as env:
			env.save(plan_new_calls=15, orders_expected=2)
			env.save(off_day=True)
			self.assertEqual(env.writes[1], {"no_target": 1})  # nothing else is written: the plan stays stored

	def test_off_day_sends_nothing_else(self):
		with Env() as env:
			env.save(off_day=True)
			self.assertEqual(env.writes, [{"no_target": 1}])

	def test_a_past_day_is_refused(self):
		with Env() as env:
			body = env.save(date="2026-10-23", off_day=True)
			self.assertEqual((body["status"], body["reason"]), ("locked", "past day"))
			self.assertEqual(env.writes, [])

	def test_a_future_day_is_refused(self):
		with Env() as env:
			self.assertEqual(env.save(date="2026-10-25", off_day=True)["status"], "invalid")


if __name__ == "__main__":
	unittest.main()
