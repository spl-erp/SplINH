"""Offline tests for custom/call_ai_quota.py and its use in custom/call_ai.py - no site, no network.

A fake cache and a fake clock stand in for Redis and time, so the minute windows, the daily
limit (Pacific-time day), pacing and the 429 handling can be checked in microseconds.

Run:  cd apps/splinh && ../../env/bin/python -m unittest splinh.tests.test_call_ai_quota
"""

import unittest
from contextlib import ExitStack
from datetime import datetime, timezone
from unittest import mock

import frappe

from splinh.custom import call_ai, call_ai_quota as q

NOON_PACIFIC = datetime(2026, 10, 31, 19, 0, tzinfo=timezone.utc).timestamp()  # 12:00 PDT, half way through the day


class FakeCache:
	def __init__(self):
		self.data = {}

	def get_value(self, key, **kwargs):
		return self.data.get(key)

	def set_value(self, key, value, **kwargs):
		self.data[key] = value

	def delete_value(self, key, **kwargs):
		self.data.pop(key, None)


class Env:
	"""A cache, a clock that sleep() advances, and the settings screen."""

	def __init__(self, now=NOON_PACIFIC, **screen):
		self.cache = FakeCache()
		self.now = now
		self.slept = []
		self.screen = screen

	def _sleep(self, seconds):
		self.slept.append(seconds)
		self.now += seconds

	def __enter__(self):
		doc = mock.MagicMock()
		doc.get.side_effect = lambda key, default=None: self.screen.get(key, default)
		self.stack = ExitStack()
		for p in (
			mock.patch.object(frappe, "cache", self.cache, create=True),
			mock.patch.object(q, "_clock", lambda: self.now),
			mock.patch.object(q, "_sleep", self._sleep),
			mock.patch.object(call_ai, "_load_settings", lambda: doc),
		):
			self.stack.enter_context(p)
		return self

	def __exit__(self, *exc):
		self.stack.close()


class TestLimits(unittest.TestCase):
	def test_defaults_are_tier_one_with_a_safety_margin(self):
		with Env():
			lim = q.limits()
		self.assertEqual((lim["rpm"], lim["tpm"], lim["rpd"], lim["rpd_nominal"]), (9, 9000, 97, 100))
		self.assertTrue(lim["spread"])

	def test_the_screen_overrides_so_a_higher_tier_needs_no_code(self):
		with Env(speech_rpm=1000, speech_tpm=1_000_000, speech_rpd=10_000, speech_spread=0):
			lim = q.limits()
		self.assertEqual((lim["rpm"], lim["tpm"], lim["rpd"]), (900, 900_000, 9997))
		self.assertFalse(lim["spread"])

	def test_estimate(self):
		self.assertEqual(q.estimate_tokens(78), 2048)  # 78 s x 25 x 1.05
		self.assertEqual(q.estimate_tokens(0), q.estimate_tokens(60))  # no duration -> a minute


class TestPacificDay(unittest.TestCase):
	def test_day_boundaries(self):
		day, start, reset, fraction = q._pacific_day(NOON_PACIFIC)
		self.assertEqual(day, "2026-10-31")
		self.assertAlmostEqual(fraction, 0.5, places=3)
		self.assertEqual(reset - start, 86400)

	def test_the_day_rolls_over_at_pacific_midnight(self):
		_, _, reset, _ = q._pacific_day(NOON_PACIFIC)
		self.assertEqual(q._pacific_day(reset - 1)[0], "2026-10-31")
		self.assertEqual(q._pacific_day(reset + 1)[0], "2026-11-01")

	def test_the_day_the_clocks_change_has_25_hours(self):
		after_change = datetime(2026, 11, 1, 20, 0, tzinfo=timezone.utc).timestamp()  # 12:00 PST on 1 Nov
		_, start, reset, fraction = q._pacific_day(after_change)
		self.assertEqual(reset - start, 25 * 3600)
		self.assertTrue(0 < fraction < 1)


class TestReserve(unittest.TestCase):
	def test_requests_per_minute_wait_for_a_slot(self):
		with Env(speech_spread=0) as env:
			for _ in range(9):
				q.reserve(100)
			self.assertEqual(env.slept, [])
			q.reserve(100)  # the 10th inside one minute: waits for the first to age out
		self.assertEqual(len(env.slept), 1)
		self.assertAlmostEqual(sum(env.slept), 60.5, places=1)

	def test_tokens_per_minute_wait_for_room(self):
		with Env(speech_spread=0) as env:
			q.reserve(5000)
			q.reserve(3500)  # 8,500 of 9,000
			self.assertEqual(env.slept, [])
			q.reserve(2000)  # would be 10,500: waits
		self.assertTrue(env.slept)

	def test_a_call_that_can_never_fit_is_too_long(self):
		with Env():
			with self.assertRaises(q.TooLong):
				q.reserve(9001)
			q.reserve(9000)  # exactly the limit is fine

	def test_daily_limit_throttles_without_waiting(self):
		with Env(speech_spread=0) as env:
			env.cache.data[q.DAY_KEY.format("2026-10-31")] = 97
			with self.assertRaises(q.Throttled) as ctx:
				q.reserve(100)
			self.assertIn("Daily quota used (97 of 100)", str(ctx.exception))
			self.assertEqual(env.slept, [])

	def test_a_new_pacific_day_starts_at_zero(self):
		with Env(speech_spread=0) as env:
			env.cache.data[q.DAY_KEY.format("2026-10-31")] = 97
			_, _, reset, _ = q._pacific_day(env.now)
			env.now = reset + 5
			q.reserve(100)  # no Throttled

	def test_reserve_counts_the_day(self):
		with Env(speech_spread=0) as env:
			q.reserve(100)
			q.reserve(100)
			self.assertEqual(env.cache.data[q.DAY_KEY.format("2026-10-31")], 2)

	def test_pacing_spreads_the_day(self):
		with Env() as env:  # half way through the day: 97 x 0.5 -> 49 + a burst of 5 = 54 allowed
			env.cache.data[q.DAY_KEY.format("2026-10-31")] = 53
			q.reserve(100)  # the 54th: allowed
			with self.assertRaises(q.Throttled) as ctx:
				q.reserve(100)  # the 55th: wait for later in the day
			self.assertIn("Pacing", str(ctx.exception))

	def test_pacing_opens_up_as_the_day_goes_on(self):
		with Env() as env:
			env.cache.data[q.DAY_KEY.format("2026-10-31")] = 54
			with self.assertRaises(q.Throttled):
				q.reserve(100)
			env.now += 3 * 3600
			q.reserve(100)

	def test_burst_is_available_right_after_the_reset(self):
		with Env() as env:
			_, _, reset, _ = q._pacific_day(env.now)
			env.now = reset + 60  # a minute into the day: 1 call of pace + the burst of 5
			for _ in range(6):
				q.reserve(100)
			with self.assertRaises(q.Throttled):
				q.reserve(100)

	def test_google_said_daily_limit_blocks_until_the_reset(self):
		with Env(speech_spread=0) as env:
			reset = q.block_until_reset()
			self.assertEqual(reset, q._pacific_day(env.now)[2])
			self.assertIn("Google's daily limit", q.blocker())
			env.now = reset + 1
			self.assertIsNone(q.blocker())

	def test_a_manual_click_is_not_paced_but_still_bound_by_the_daily_limit(self):
		with Env() as env:
			env.cache.data[q.DAY_KEY.format("2026-10-31")] = 60  # ahead of the even pace
			with self.assertRaises(q.Throttled):
				q.reserve(100)
			q.reserve(100, paced=False)  # a manager's click goes through
			env.cache.data[q.DAY_KEY.format("2026-10-31")] = 97
			with self.assertRaises(q.Throttled):
				q.reserve(100, paced=False)

	def test_settle_replaces_the_estimate_with_the_real_count(self):
		with Env(speech_spread=0) as env:
			rid = q.reserve(5000)
			q.settle(rid, 1800)
			self.assertEqual([e[1] for e in env.cache.data[q.WINDOW_KEY]], [1800])
			q.settle(None, 10)  # harmless without a reservation

	def test_status_text(self):
		with Env(speech_spread=0):
			q.reserve(1000)
			text = q.status_text(queue=12)
		self.assertIn("Today 1/100 requests", text)
		self.assertIn("1/9 requests, 1000/9000 tokens", text)
		self.assertIn("12 calls waiting", text)


class Test429(unittest.TestCase):
	MINUTE = 'Gemini API 429: {"error":{"message":"Rate limit exceeded for model gemini-3.5-transcribe (limit: 10000 input tokens per minute on Tier 1). Please retry in 26s or upgrade your tier"}}'
	DAILY = 'Gemini API 429: {"error":{"message":"Rate limit exceeded (limit: 100 requests per day on Tier 1). Please retry in 5h"}}'

	def test_parse(self):
		self.assertEqual(q.parse_429(self.MINUTE), (False, 26.0))
		self.assertEqual(q.parse_429(self.DAILY)[0], True)
		self.assertEqual(q.parse_429(""), (False, None))

	def test_only_short_minute_waits_are_retried(self):
		def error(daily, after):
			e = call_ai.AIError("x", 429)
			e.daily, e.retry_after = daily, after
			return e

		self.assertTrue(call_ai._retryable(error(False, 26)))
		self.assertTrue(call_ai._retryable(error(False, None)))
		self.assertFalse(call_ai._retryable(error(True, 26)))  # daily: waiting a minute is pointless
		self.assertFalse(call_ai._retryable(error(False, 300)))
		self.assertTrue(call_ai._retryable(call_ai.AIError("x", 503)))
		self.assertFalse(call_ai._retryable(call_ai.AIError("x", 400)))

	def test_the_wait_follows_googles_retry_in(self):
		e = call_ai.AIError("x", 429)
		e.retry_after = 26
		state = mock.Mock()
		state.outcome.exception.return_value = e
		self.assertEqual(call_ai._wait(state), 27)
		e.retry_after = 59.5
		self.assertEqual(call_ai._wait(state), 60)

	def test_request_records_the_429_details(self):
		response = mock.Mock(status_code=429, text=self.MINUTE)
		with mock.patch.object(call_ai.requests, "request", return_value=response), mock.patch.object(
			call_ai, "api_key", lambda: "k"
		), mock.patch.object(call_ai._request.retry, "wait", lambda state: 0):
			with self.assertRaises(call_ai.AIError) as ctx:
				call_ai._request("POST", "https://x", timeout=1)
		self.assertEqual((ctx.exception.status, ctx.exception.daily, ctx.exception.retry_after), (429, False, 26.0))

	def test_a_speech_429_becomes_throttled_never_a_failure(self):
		with Env(speech_spread=0) as env:
			minute = call_ai.AIError("Gemini API 429: x", 429)
			minute.retry_after = 26
			with self.assertRaises(q.Throttled):
				raise call_ai._throttled(minute)
			self.assertIsNone(env.cache.data.get(q.BLOCK_KEY))
			daily = call_ai.AIError("Gemini API 429: x", 429)
			daily.daily = True
			with self.assertRaises(q.Throttled):
				raise call_ai._throttled(daily)
			self.assertTrue(env.cache.data[q.BLOCK_KEY] > env.now)  # blocked until Pacific midnight


class TestRunCallAi(unittest.TestCase):
	def run_it(self, error, requeue):
		status = {}
		with mock.patch.object(call_ai, "process_call", mock.Mock(side_effect=error)), mock.patch.object(
			call_ai, "_set_status", lambda name, st, err: status.update(status=st, error=err)
		), mock.patch.object(call_ai, "save_result"), mock.patch.object(call_ai.frappe, "db", mock.Mock()), mock.patch.object(
			call_ai.frappe, "cache", mock.Mock(), create=True
		), mock.patch.object(call_ai.frappe, "publish_realtime"):
			return call_ai.run_call_ai("CL-1", "u", requeue=requeue), status

	def test_throttled_goes_back_to_the_queue_for_the_scheduler(self):
		outcome, status = self.run_it(q.Throttled("Daily quota used (97 of 100)"), requeue=True)
		self.assertEqual(outcome, "Throttled")
		self.assertEqual(status["status"], "")  # no status = picked up again later

	def test_throttled_on_a_manual_click_says_so(self):
		outcome, status = self.run_it(q.Throttled("Pacing"), requeue=False)
		self.assertEqual(outcome, "Failed")
		self.assertIn("Waiting for the API quota (Pacing)", status["error"])

	def test_too_long_is_skipped_not_failed_or_requeued(self):
		outcome, status = self.run_it(call_ai.Skipped("Longer than the Google tier allows."), requeue=True)
		self.assertEqual((outcome, status["status"]), ("Skipped", "Skipped"))

	def test_other_errors_still_fail(self):
		outcome, status = self.run_it(call_ai.AIError("boom"), requeue=True)
		self.assertEqual((outcome, status["status"], status["error"]), ("Failed", "Failed", "boom"))

	def test_success(self):
		outcome, _ = self.run_it(None, requeue=True)
		self.assertEqual(outcome, "Completed")


class TestProcessCall(unittest.TestCase):
	def call(self, duration, reserve=None):
		patches = dict(
			api_key=lambda: "k",
			latest_audio_file=lambda n: mock.Mock(file_name="a.m4a", name="F1"),
			_call_context=lambda n: ({}, duration),
			_upload_audio=mock.Mock(side_effect=AssertionError("must not upload before a slot is reserved")),
		)
		fake_file = mock.Mock()
		fake_file.get_content.return_value = b"x"
		with mock.patch.multiple(call_ai, **patches), mock.patch.object(
			call_ai.frappe, "get_doc", return_value=fake_file
		), mock.patch.object(call_ai.frappe, "local", mock.Mock(site="s")), mock.patch.object(
			call_ai.call_ai_quota, "reserve", reserve or (lambda est, paced=False: "rid")
		):
			return call_ai.process_call("CL-1")

	def test_a_call_too_long_for_the_tier_is_skipped_before_any_upload(self):
		def reserve(est, paced=False):
			raise q.TooLong("too long")

		with self.assertRaises(call_ai.Skipped):
			self.call(1411, reserve)

	def test_no_quota_means_no_upload(self):
		def reserve(est, paced=False):
			raise q.Throttled("Daily quota used")

		with self.assertRaises(q.Throttled):
			self.call(78, reserve)

	def test_the_estimate_comes_from_the_call_duration(self):
		asked = []

		def reserve(est, paced=False):
			asked.append(est)
			raise q.Throttled("stop here")

		with self.assertRaises(q.Throttled):
			self.call(78, reserve)
		self.assertEqual(asked, [2048])


if __name__ == "__main__":
	unittest.main()
