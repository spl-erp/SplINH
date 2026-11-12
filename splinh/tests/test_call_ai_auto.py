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
from splinh.custom import call_ai, call_ai_auto, call_ai_quota


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
		self.untils, self.saved_bulk = [], []
		self.throttled, self.blocked = set(), None  # calls that hit the quota; why quota blocks everything
		self.screen, self.shown, self.bulk = {}, {}, None  # schedule fields, status writes, bulk job
		self._processing = list(processing)

	def run_call_ai(self, name, user, requeue=False):
		self.calls.append((name, user))
		if name in self.throttled:  # no API quota: the real function puts the call back with no status
			self.status[name] = ""
			return "Throttled"
		self.status[name] = self.outcomes.get(name, "Completed")
		return self.status[name]

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
			mock.patch.object(call_ai_auto, "_setting", lambda *names: self.screen.get(names[0])),
			mock.patch.object(call_ai_auto, "_show", lambda **values: self.shown.update(values)),
			mock.patch.object(call_ai_auto, "bulk_job", lambda: self.bulk),
			mock.patch.object(call_ai_quota, "blocker", lambda: self.blocked),
			mock.patch.object(call_ai_quota, "last_reason", lambda: "Quota reason"),
			mock.patch.object(call_ai_auto, "_save_bulk", lambda job, note="": self.saved_bulk.append((dict(job), note))),
			mock.patch.object(call_ai_auto, "daily_limit", lambda: getattr(self, "limit", 200)),
			mock.patch.object(call_ai_auto, "_get_marker", lambda: self.marker),
			mock.patch.object(call_ai_auto, "_set_marker", lambda v: self.marker_sets.append(str(v))),
			mock.patch.object(call_ai_auto, "_candidates", lambda since, mn, limit, until=None: (self.candidates_asked.append((since, mn, limit)), self.untils.append(until), list(self._candidates))[2]),
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
		self.assertEqual(h.candidates_asked[0][1:], (30, call_ai_auto.BATCH_SIZE))  # minimum seconds (fixed at 30 for auto), batch size


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

		def spy(name, user, requeue=False):
			seen["marker_during_run"] = h.cache.get_value(call_ai_auto.RUNNING_KEY.format(name))
			return original(name, user, requeue)

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


class TestCandidateRules(unittest.TestCase):
	"""The SQL itself needs a database; here we check it carries the three selection rules."""

	def run_query(self):
		fake = mock.Mock()
		fake.db.sql.return_value = [("CALL-1",)]
		with mock.patch.object(call_ai_auto, "frappe", fake):
			names = call_ai_auto._candidates("2026-10-30 00:00:00", call_ai_auto.AUTO_MIN_SECONDS, 10)
		sql, params = fake.db.sql.call_args[0]
		return names, " ".join(sql.split()), params

	def test_longer_than_30_seconds(self):
		names, sql, params = self.run_query()
		self.assertEqual(names, ["CALL-1"])
		self.assertIn("c.duration > %(min_seconds)s", sql)
		self.assertEqual(params["min_seconds"], 30)

	def test_reviewed_and_not_no_two_way(self):
		_, sql, params = self.run_query()
		self.assertIn("IFNULL(c.custom_call_drop_stage, '') != ''", sql)
		self.assertIn("c.custom_call_drop_stage NOT IN %(excluded_stages)s", sql)
		self.assertIn("0. No 2-Way Communication", params["excluded_stages"])

	def test_not_an_internal_call(self):
		_, sql, params = self.run_query()
		self.assertIn("IFNULL(c.custom_call_category, '') != %(excluded_category)s", sql)
		self.assertEqual(params["excluded_category"], "Internal Call")


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


class TestSchedule(unittest.TestCase):
	def test_window_normal_overnight_and_blank(self):
		w = call_ai_auto._in_window
		self.assertTrue(w("2026-10-30 12:00:00", "09:00:00", "21:00:00"))
		self.assertFalse(w("2026-10-30 22:00:00", "09:00:00", "21:00:00"))
		self.assertFalse(w("2026-10-30 21:00:00", "09:00:00", "21:00:00"))  # end is exclusive
		self.assertTrue(w("2026-10-30 23:30:00", "22:00:00", "06:00:00"))  # passes midnight
		self.assertTrue(w("2026-10-30 03:00:00", "22:00:00", "06:00:00"))
		self.assertFalse(w("2026-10-30 12:00:00", "22:00:00", "06:00:00"))
		self.assertTrue(w("2026-10-30 12:00:00", None, None))
		self.assertTrue(w("2026-10-30 12:00:00", "09:00:00", None))

	def test_interval(self):
		def reason(minutes, last, now="2026-10-30 12:00:00"):
			screen = {"auto_run_every_minutes": minutes, "auto_last_run": last}
			with mock.patch.object(call_ai_auto, "_setting", lambda *names: screen.get(names[0])):
				return call_ai_auto._interval_reason(now)

		self.assertIsNone(reason(0, "2026-10-30 11:59:00"))  # blank/0 = every tick
		self.assertIsNone(reason(2, "2026-10-30 11:59:00"))
		self.assertIsNone(reason(10, None))
		self.assertIsNotNone(reason(10, "2026-10-30 11:55:00"))
		self.assertIsNone(reason(10, "2026-10-30 11:50:30"))  # 9.5 min is within the scheduler's slack
		self.assertIsNone(reason(10, "2026-10-30 11:40:00"))

	def test_outside_the_window_nothing_runs_and_the_reason_is_shown(self):
		h = Harness(candidates=["A"])
		h.screen = {"auto_window_from": "13:00:00", "auto_window_to": "20:00:00"}
		with mock.patch.object(call_ai_auto, "_setting", lambda *names: h.screen.get(names[0])):
			r = h.run()
		self.assertEqual(h.calls, [])
		self.assertIn("Outside the active hours", r["reason"])
		self.assertIn("Outside the active hours", h.shown["auto_status"])

	def test_a_real_run_stamps_last_run(self):
		h = Harness(candidates=["A"])
		h.run()
		self.assertEqual(str(h.shown["auto_last_run"]), "2026-10-05 12:00:00")


class TestBulk(unittest.TestCase):
	def job(self, **over):
		job = {"from": "2026-10-01 00:00:00", "to": "2026-10-02 00:00:00", "max": 5, "done": 0, "failed": 0, "state": "Running"}
		job.update(over)
		return job

	def test_not_running_does_not_touch_bulk(self):
		for bulk in (None, self.job(state="Stopped"), self.job(state="Done")):
			h = Harness(candidates=[], auto=False)
			h.bulk = bulk
			h.run()
			self.assertEqual(h.untils, [])  # no bulk query at all
			self.assertEqual(h.saved_bulk, [])

	def test_runs_the_range_even_when_auto_is_off(self):
		h = Harness(candidates=["A", "B"], auto=False)
		h.bulk = self.job()
		h.run()
		self.assertEqual([c[0] for c in h.calls], ["A", "B"])
		self.assertEqual(h.untils, ["2026-10-02 00:00:00"])
		self.assertEqual(h.candidates_asked[-1][0], "2026-10-01 00:00:00")  # the range start, not the marker
		job, _ = h.saved_bulk[-1]
		self.assertEqual((job["done"], job["failed"], job["state"]), (2, 0, "Running"))

	def test_does_not_count_against_the_daily_auto_limit(self):
		h = Harness(candidates=["A"], auto=False)
		h.bulk = self.job()
		h.run()
		self.assertNotIn("splinh_ai_auto_daily::" + TODAY, h.cache.data)

	def test_call_limit_caps_the_batch_and_finishes(self):
		h = Harness(candidates=["A", "B", "C"], auto=False)
		h.bulk = self.job(max=5, done=3, failed=0)  # 2 left
		h.run()
		self.assertEqual([c[0] for c in h.calls], ["A", "B"])
		self.assertEqual(h.candidates_asked[-1][2], 2)  # asked for at most what is left

	def test_limit_reached_marks_done_without_calling_gemini(self):
		h = Harness(candidates=["A"], auto=False)
		h.bulk = self.job(max=3, done=2, failed=1)
		h.run()
		self.assertEqual(h.calls, [])
		self.assertEqual(h.saved_bulk[-1][0]["state"], "Done")

	def test_nothing_left_marks_done(self):
		h = Harness(candidates=[], auto=False)
		h.bulk = self.job()
		h.run()
		self.assertEqual(h.saved_bulk[-1][0]["state"], "Done")

	def test_three_failures_in_a_row_stop_the_bulk(self):
		h = Harness(candidates=list("ABCDE"), auto=False, outcomes={c: "Failed" for c in "ABCDE"})
		h.bulk = self.job(max=50)
		h.run()
		self.assertEqual(len(h.calls), 3)
		job, _ = h.saved_bulk[-1]
		self.assertEqual((job["failed"], job["state"]), (3, "Stopped"))

	def test_dry_run_lists_and_writes_nothing(self):
		h = Harness(candidates=["A"], auto=False)
		h.bulk = self.job()
		r = h.run(dry_run=True)
		self.assertEqual(r["bulk_would_process"], ["A"])
		self.assertEqual((h.calls, h.saved_bulk), ([], []))

	def test_preview_validates_the_range(self):
		with self.assertRaises(call_ai.AIError):
			call_ai_auto.bulk_preview("2026-10-02 00:00:00", "2026-10-01 00:00:00")

	def test_preview_numbers(self):
		with mock.patch.object(call_ai_auto, "_count_eligible", lambda a, b: (120, 7200.0)):
			r = call_ai_auto.bulk_preview("2026-10-01 00:00:00", "2026-10-02 00:00:00")
		self.assertEqual(r, {"count": 120, "hours": 2.0, "estimated_cost_usd": 0.66})

	def test_start_refuses_when_running_off_or_empty_and_caps_max(self):
		patches = lambda **kw: [
			mock.patch.object(call_ai_auto, "bulk_job", lambda: kw.get("job")),
			mock.patch.object(call_ai_auto, "_save_bulk", lambda job, note="": kw["saved"].append(job)),
			mock.patch.object(call_ai_auto, "bulk_preview", lambda a, b: {"count": kw.get("count", 10), "hours": 1.0, "estimated_cost_usd": 0.33}),
			mock.patch.object(call_ai, "conf", lambda key, default=None: kw.get("enabled", True)),
			mock.patch.object(call_ai, "api_key", lambda: "k"),
			mock.patch.object(call_ai_auto, "now_datetime", lambda: "2026-10-30 10:00:00"),
		]

		def start(max_calls=None, **kw):
			from contextlib import ExitStack

			kw["saved"] = []

			with ExitStack() as stack:
				for p in patches(**kw):
					stack.enter_context(p)
				out = call_ai_auto.bulk_start("2026-10-01 00:00:00", "2026-10-02 00:00:00", max_calls)
			return out, kw.get("saved")

		with self.assertRaises(call_ai.AIError):
			start(job={"state": "Running"})
		with self.assertRaises(call_ai.AIError):
			start(enabled=False)
		with self.assertRaises(call_ai.AIError):
			start(count=0)
		out, saved = start()
		self.assertEqual(out["max"], call_ai_auto.BULK_DEFAULT_MAX)
		self.assertEqual(saved[0]["state"], "Running")
		out, _ = start(max_calls=999999)
		self.assertEqual(out["max"], call_ai_auto.BULK_ABSOLUTE_MAX)

	def test_stop(self):
		saved = []
		with mock.patch.object(call_ai_auto, "bulk_job", lambda: self.job()), mock.patch.object(
			call_ai_auto, "_save_bulk", lambda job, note="": saved.append(job)
		):
			call_ai_auto.bulk_stop()
		self.assertEqual(saved[0]["state"], "Stopped")
		with mock.patch.object(call_ai_auto, "bulk_job", lambda: None), self.assertRaises(call_ai.AIError):
			call_ai_auto.bulk_stop()

	def test_buttons_are_system_manager_only_and_need_both_dates(self):
		with mock.patch.object(frappe, "only_for", mock.Mock(side_effect=frappe.PermissionError)):
			for fn, args in ((auto_ai.bulk_preview, ("a", "b")), (auto_ai.bulk_start, ("a", "b")), (auto_ai.bulk_stop, ())):
				with self.assertRaises(frappe.PermissionError):
					fn(*args)
		with mock.patch.object(frappe, "only_for", lambda role: None), mock.patch.object(
			frappe, "throw", mock.Mock(side_effect=frappe.ValidationError)
		):
			with self.assertRaises(frappe.ValidationError):
				auto_ai.bulk_preview("2026-10-01 00:00:00", None)


class TestQuotaInTheRun(unittest.TestCase):
	def test_a_throttled_call_stops_the_batch_and_is_not_a_failure(self):
		h = Harness(candidates=["A", "B", "C"])
		h.throttled = {"B"}
		r = h.run()
		self.assertEqual([c[0] for c in h.calls], ["A", "B"])  # C is not even tried
		self.assertEqual(r["processed"], ["A"])
		self.assertEqual(r["failed"], [])
		self.assertEqual(r["reason"], "Quota reason")
		self.assertEqual(h.cache.data["splinh_ai_auto_daily::" + TODAY], 1)  # the throttled one is not counted

	def test_throttled_calls_do_not_trip_the_failure_breaker(self):
		h = Harness(candidates=list("ABCDE"), outcomes={"A": "Failed", "B": "Failed"})
		h.throttled = {"C"}
		r = h.run()
		self.assertEqual(r["failed"], ["A", "B"])
		self.assertNotIn("failures in a row", r["reason"] or "")

	def test_a_skipped_call_is_neither_done_nor_failed(self):
		h = Harness(candidates=["A", "B"], outcomes={"A": "Skipped"})
		r = h.run()
		self.assertEqual(r["skipped"], ["A"])
		self.assertEqual(r["processed"], ["B"])
		self.assertEqual(r["failed"], [])

	def test_quota_used_up_sends_nothing(self):
		h = Harness(candidates=["A"])
		h.blocked = "Daily quota used (97 of 100)"
		r = h.run()
		self.assertEqual(h.calls, [])
		self.assertEqual(r["reason"], "Daily quota used (97 of 100)")

	def test_bulk_waits_when_the_quota_is_used_up(self):
		h = Harness(candidates=["A"], auto=False)
		h.bulk = {"from": "2026-10-01 00:00:00", "to": "2026-10-02 00:00:00", "max": 5, "done": 0, "failed": 0, "state": "Running"}
		h.blocked = "Pacing"
		r = h.run()
		self.assertEqual((h.calls, h.saved_bulk), ([], []))  # still Running, nothing consumed
		self.assertEqual(r["bulk_reason"], "Pacing")

	def test_bulk_throttle_keeps_it_running(self):
		h = Harness(candidates=["A", "B"], auto=False)
		h.bulk = {"from": "2026-10-01 00:00:00", "to": "2026-10-02 00:00:00", "max": 5, "done": 0, "failed": 0, "state": "Running"}
		h.throttled = {"B"}
		h.run()
		job, note = h.saved_bulk[-1]
		self.assertEqual((job["done"], job["failed"], job["state"]), (1, 0, "Running"))
		self.assertEqual(note, "Quota reason")

	def test_live_job_serves_the_newest_calls_within_the_age_cap(self):
		h = Harness(candidates=["A"], marker="2026-09-01 00:00:00")  # an old marker
		h.run()
		since = h.candidates_asked[0][0]
		self.assertEqual(str(since), "2026-10-03 12:00:00")  # now (5 Oct 12:00) minus the default 2 days

	def test_age_cap_never_goes_before_the_marker(self):
		h = Harness(candidates=["A"], marker="2026-10-05 08:00:00")
		h.run()
		self.assertEqual(str(h.candidates_asked[0][0]), "2026-10-05 08:00:00")

	def test_order_is_newest_first(self):
		fake = mock.Mock()
		fake.db.sql.return_value = []
		with mock.patch.object(call_ai_auto, "frappe", fake):
			call_ai_auto._candidates("2026-10-30 00:00:00", 30, 10)
		self.assertIn("ORDER BY MAX(f.creation) DESC", " ".join(fake.db.sql.call_args[0][0].split()))


if __name__ == "__main__":
	unittest.main()
