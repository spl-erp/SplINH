"""Offline tests for custom/call_ai_auto.py - no site, no network, no database.

The database steps are small helpers in the module, replaced here, so these tests
cover the decisions: when a run does nothing, what it picks, and every stop condition.
The SQL itself is checked against real data with the module's dry run.

Run:  cd apps/splinh && ../../env/bin/python -m unittest splinh.tests.test_call_ai_auto
"""

import unittest
from unittest import mock

import frappe

from splinh.api import auto_ai
from splinh.custom import call_ai, call_ai_auto


TODAY = "2026-10-05"


class FakeCache:
	def __init__(self):
		self.data = {}

	def get_value(self, key, **kwargs):
		return self.data.get(key)

	def set_value(self, key, value, **kwargs):
		self.data[key] = value

	def delete_value(self, key, **kwargs):
		self.data.pop(key, None)


class Harness:
	"""Everything a run touches, replaced; records what happened."""

	def __init__(self, candidates=(), marker="2026-10-05 10:00:00", auto=True, enabled=True, key=True, claims=None, outcomes=None, processing=()):
		self.cache = FakeCache()
		self.calls = []  # run_call_ai(name)
		self.marker_sets = []
		self.interrupted = []
		self.candidates_asked = []
		self.claims = claims or {}
		self.outcomes = outcomes or {}  # name -> "Completed" / "Failed"
		self.status = {}
		self.marker = marker
		self.auto, self.enabled, self.key = auto, enabled, key
		self._candidates = list(candidates)
		self._processing = list(processing)

	def run_call_ai(self, name, user):
		self.calls.append((name, user))
		self.status[name] = self.outcomes.get(name, "Completed")

	def patches(self):
		def api_key():
			if not self.key:
				raise call_ai.AIError("The Gemini API key is not set.")
			return "k"

		def conf(key, default=None):
			return {"splinh_ai_enabled": self.enabled, "splinh_ai_min_seconds": 10}.get(key, default)

		return [
			mock.patch.object(call_ai_auto, "now_datetime", lambda: "2026-10-05 12:00:00"),  # these read System Settings
			mock.patch.object(call_ai_auto, "today", lambda: TODAY),
			mock.patch.object(frappe, "cache", self.cache, create=True),
			mock.patch.object(call_ai_auto, "auto_enabled", lambda: self.auto),
			mock.patch.object(call_ai_auto, "daily_limit", lambda: getattr(self, "limit", 200)),
			mock.patch.object(call_ai_auto, "_get_marker", lambda: self.marker),
			mock.patch.object(call_ai_auto, "_set_marker", lambda v: self.marker_sets.append(str(v))),
			mock.patch.object(call_ai_auto, "_candidates", lambda since, mn, limit: (self.candidates_asked.append((since, mn, limit)), list(self._candidates))[1]),
			mock.patch.object(call_ai_auto, "_claim", lambda name: self.claims.get(name, True)),
			mock.patch.object(call_ai_auto, "_status", lambda name: self.status.get(name)),
			mock.patch.object(call_ai_auto, "_processing_calls", lambda limit=50: list(self._processing)),
			mock.patch.object(call_ai_auto, "_mark_interrupted", lambda name: self.interrupted.append(name)),
			mock.patch.object(call_ai, "api_key", api_key),
			mock.patch.object(call_ai, "conf", conf),
			mock.patch.object(call_ai, "run_call_ai", self.run_call_ai),
		]

	def run(self, **kwargs):
		from contextlib import ExitStack

		with ExitStack() as stack:
			for p in self.patches():
				stack.enter_context(p)
			return call_ai_auto.process_pending(**kwargs)


class TestSwitches(unittest.TestCase):
	def test_off_does_nothing_but_keep_the_marker_at_now(self):
		h = Harness(candidates=["A"], auto=False)
		report = h.run()
		self.assertEqual(h.calls, [])
		self.assertEqual(len(h.marker_sets), 1)
		self.assertIn("switched off", report["reason"])

	def test_off_dry_run_does_not_move_the_marker(self):
		h = Harness(auto=False)
		h.run(dry_run=True)
		self.assertEqual(h.marker_sets, [])

	def test_master_switch_off(self):
		h = Harness(candidates=["A"], enabled=False)
		report = h.run()
		self.assertEqual(h.calls, [])
		self.assertIn("switched off", report["reason"])

	def test_no_key(self):
		h = Harness(candidates=["A"], key=False)
		report = h.run()
		self.assertEqual(h.calls, [])
		self.assertIn("key", report["reason"])

	def test_first_run_only_sets_the_start_marker(self):
		h = Harness(candidates=["A"], marker=None)
		report = h.run()
		self.assertEqual(h.calls, [])
		self.assertEqual(len(h.marker_sets), 1)
		self.assertIn("older recordings are left alone", report["reason"])


class TestDryRun(unittest.TestCase):
	def test_lists_candidates_and_writes_nothing(self):
		h = Harness(candidates=["A", "B"])
		report = h.run(dry_run=True)
		self.assertEqual(report["would_process"], ["A", "B"])
		self.assertEqual(h.calls, [])
		self.assertEqual(h.marker_sets, [])
		self.assertEqual(h.interrupted, [])  # not even recovery
		self.assertNotIn(call_ai_auto.LOCK_KEY, h.cache.data)
		self.assertEqual(h.candidates_asked[0][1:], (10, call_ai_auto.BATCH_SIZE))  # minimum seconds, batch size


class TestProcessing(unittest.TestCase):
	def test_processes_in_order_as_administrator_and_releases_the_lock(self):
		h = Harness(candidates=["A", "B", "C"])
		report = h.run()
		self.assertEqual(h.calls, [("A", "Administrator"), ("B", "Administrator"), ("C", "Administrator")])
		self.assertEqual(report["processed"], ["A", "B", "C"])
		self.assertNotIn(call_ai_auto.LOCK_KEY, h.cache.data)

	def test_running_marker_is_set_so_a_manual_click_is_refused(self):
		h = Harness(candidates=["A"])
		seen = {}
		original = h.run_call_ai

		def spy(name, user):
			seen["marker_during_run"] = h.cache.get_value(call_ai_auto.RUNNING_KEY.format(name))
			original(name, user)

		h.run_call_ai = spy
		h.run()
		self.assertTrue(seen["marker_during_run"])

	def test_a_call_someone_else_claimed_is_skipped(self):
		h = Harness(candidates=["A", "B"], claims={"A": False})
		report = h.run()
		self.assertEqual([c[0] for c in h.calls], ["B"])
		self.assertEqual(report["processed"], ["B"])
		self.assertIsNone(h.cache.get_value(call_ai_auto.RUNNING_KEY.format("A")))

	def test_an_overlapping_run_does_nothing(self):
		h = Harness(candidates=["A"])
		h.cache.set_value(call_ai_auto.LOCK_KEY, 1)
		report = h.run()
		self.assertEqual(h.calls, [])
		self.assertIn("in progress", report["reason"])
		self.assertEqual(h.cache.get_value(call_ai_auto.LOCK_KEY), 1)  # not stolen or released


class TestStopConditions(unittest.TestCase):
	def test_daily_limit_already_reached(self):
		h = Harness(candidates=["A"])
		h.limit = 2
		h.cache.set_value(call_ai_auto.DAILY_KEY.format(TODAY), 2)
		report = h.run()
		self.assertEqual(h.calls, [])
		self.assertIn("Daily limit", report["reason"])

	def test_daily_limit_reached_in_the_middle(self):
		h = Harness(candidates=["A", "B", "C"])
		h.limit = 2
		report = h.run()
		self.assertEqual([c[0] for c in h.calls], ["A", "B"])
		self.assertIn("Daily limit", report["reason"])

	def test_circuit_breaker_after_three_failures_in_a_row(self):
		names = ["A", "B", "C", "D", "E"]
		h = Harness(candidates=names, outcomes={n: "Failed" for n in names})
		report = h.run()
		self.assertEqual([c[0] for c in h.calls], ["A", "B", "C"])
		self.assertEqual(report["failed"], ["A", "B", "C"])
		self.assertIn("failures in a row", report["reason"])

	def test_a_success_resets_the_failure_count(self):
		names = ["A", "B", "C", "D", "E", "F"]
		h = Harness(candidates=names, outcomes={"A": "Failed", "B": "Failed", "C": "Completed", "D": "Failed", "E": "Failed", "F": "Completed"})
		report = h.run()
		self.assertEqual([c[0] for c in h.calls], names)
		self.assertEqual(report["processed"], ["C", "F"])

	def test_time_budget(self):
		h = Harness(candidates=["A", "B", "C"])
		clock = iter([0, 0, 10_000, 10_000, 10_000])  # started, check before A, check before B (over budget)
		with mock.patch.object(call_ai_auto.time, "time", lambda: next(clock)):
			report = h.run()
		self.assertEqual([c[0] for c in h.calls], ["A"])
		self.assertIn("Time budget", report["reason"])

	def test_nothing_to_do(self):
		h = Harness(candidates=[])
		report = h.run()
		self.assertEqual(h.calls, [])
		self.assertEqual(report["processed"], [])


class TestRecovery(unittest.TestCase):
	def test_processing_without_a_running_marker_is_marked_failed(self):
		h = Harness(processing=["DEAD", "ALIVE"])
		h.cache.set_value(call_ai_auto.RUNNING_KEY.format("ALIVE"), 1)
		report = h.run()
		self.assertEqual(h.interrupted, ["DEAD"])
		self.assertEqual(report["recovered"], ["DEAD"])

	def test_recovery_runs_even_when_auto_is_off(self):
		h = Harness(processing=["DEAD"], auto=False)
		h.run()
		self.assertEqual(h.interrupted, ["DEAD"])


class TestSettings(unittest.TestCase):
	def settings(self, **fields):
		doc = mock.MagicMock()
		doc.get.side_effect = lambda key, default=None: fields.get(key, default)
		return doc

	def test_checkbox_on_off_and_missing_field(self):
		with mock.patch.object(call_ai, "_load_settings", lambda: self.settings(auto_transcribe=1)):
			self.assertTrue(call_ai_auto.auto_enabled())
		with mock.patch.object(call_ai, "_load_settings", lambda: self.settings(auto_transcribe=0)), mock.patch.object(
			call_ai, "_site_conf", lambda key: True
		):
			self.assertFalse(call_ai_auto.auto_enabled())  # an explicit 0 on the screen wins over site_config
		with mock.patch.object(call_ai, "_load_settings", lambda: self.settings()), mock.patch.object(
			call_ai, "_site_conf", lambda key: None
		):
			self.assertFalse(call_ai_auto.auto_enabled())  # field not added yet -> off
		with mock.patch.object(call_ai, "_load_settings", lambda: None), mock.patch.object(
			call_ai, "_site_conf", lambda key: True if key == "splinh_ai_auto" else None
		):
			self.assertTrue(call_ai_auto.auto_enabled())  # no screen saved -> site_config

	def test_daily_limit_accepts_the_ui_generated_fieldname(self):
		with mock.patch.object(call_ai, "_load_settings", lambda: self.settings(daily_auto_limit=75)):
			self.assertEqual(call_ai_auto.daily_limit(), 75)
		with mock.patch.object(call_ai, "_load_settings", lambda: self.settings(auto_daily_limit=50, daily_auto_limit=75)):
			self.assertEqual(call_ai_auto.daily_limit(), 50)  # the documented name wins if both exist

	def test_daily_limit_fallbacks(self):
		with mock.patch.object(call_ai, "_load_settings", lambda: self.settings(auto_daily_limit=50)):
			self.assertEqual(call_ai_auto.daily_limit(), 50)
		with mock.patch.object(call_ai, "_load_settings", lambda: self.settings()), mock.patch.object(
			call_ai, "_site_conf", lambda key: None
		):
			self.assertEqual(call_ai_auto.daily_limit(), call_ai_auto.DEFAULT_DAILY_LIMIT)


class TestEntryPoint(unittest.TestCase):
	def test_only_system_manager_and_dry_run_flag(self):
		with mock.patch.object(auto_ai.frappe, "only_for", side_effect=PermissionError("no")):
			with self.assertRaises(PermissionError):
				auto_ai.process_pending_recordings()
		with mock.patch.object(auto_ai.frappe, "only_for"), mock.patch.object(
			call_ai_auto, "process_pending", return_value={"ok": 1}
		) as run:
			self.assertEqual(auto_ai.process_pending_recordings("1"), {"ok": 1})
			run.assert_called_once_with(dry_run=True)
			run.reset_mock()
			auto_ai.process_pending_recordings()
			run.assert_called_once_with(dry_run=False)


if __name__ == "__main__":
	unittest.main()
