import datetime
import unittest

from splinh.custom import call_recording_pause as p

NOW = datetime.datetime(2026, 10, 22, 18, 0, 0)


def t(hour, day=22):
	return datetime.datetime(2026, 10, day, hour, 0, 0)


class TestToggleMeaning(unittest.TestCase):
	def test_missing_value_counts_as_on(self):
		self.assertTrue(p.is_recording_on(None))

	def test_values(self):
		for on in (1, "1", True, 2):
			self.assertTrue(p.is_recording_on(on), on)
		for off in (0, "0", False):
			self.assertFalse(p.is_recording_on(off), off)


class TestToggleChange(unittest.TestCase):
	def test_pause(self):
		self.assertEqual(p.toggle_change(1, 0), "pause")

	def test_resume(self):
		self.assertEqual(p.toggle_change(0, 1), "resume")

	def test_no_change(self):
		self.assertIsNone(p.toggle_change(1, 1))
		self.assertIsNone(p.toggle_change(0, 0))

	def test_missing_before_is_on(self):
		self.assertEqual(p.toggle_change(None, 0), "pause")
		self.assertIsNone(p.toggle_change(None, 1))

	def test_new_user_created_off_opens_a_pause(self):
		self.assertEqual(p.toggle_change(None, 0), "pause")


class TestInPausedPeriod(unittest.TestCase):
	CLOSED = [{"paused_from": t(10), "resumed_on": t(14)}]
	OPEN = [{"paused_from": t(10), "resumed_on": None}]

	def test_before_inside_after(self):
		self.assertFalse(p.in_paused_period(self.CLOSED, t(9)))
		self.assertTrue(p.in_paused_period(self.CLOSED, t(12)))
		self.assertFalse(p.in_paused_period(self.CLOSED, t(15)))

	def test_boundaries(self):
		self.assertTrue(p.in_paused_period(self.CLOSED, t(10)))  # paused_from is inside
		self.assertFalse(p.in_paused_period(self.CLOSED, t(14)))  # resumed_on is outside

	def test_open_period_runs_to_now(self):
		self.assertFalse(p.in_paused_period(self.OPEN, t(9)))
		self.assertTrue(p.in_paused_period(self.OPEN, t(23)))

	def test_a_call_made_in_the_pause_stays_paused_after_the_resume(self):
		# the point of the design: the verdict depends on when the CALL started
		self.assertTrue(p.in_paused_period(self.CLOSED, t(11)))

	def test_several_periods(self):
		periods = [{"paused_from": t(8), "resumed_on": t(9)}, {"paused_from": t(12), "resumed_on": t(13)}]
		self.assertTrue(p.in_paused_period(periods, t(12)))
		self.assertFalse(p.in_paused_period(periods, t(10)))

	def test_no_periods_or_no_start(self):
		self.assertFalse(p.in_paused_period([], t(12)))
		self.assertFalse(p.in_paused_period(self.OPEN, None))

	def test_text_times(self):
		self.assertTrue(p.in_paused_period(self.CLOSED, "2026-10-22 12:30:00"))


class TestPeriodsForConfig(unittest.TestCase):
	def test_open_and_recent_closed_are_listed(self):
		periods = [
			{"paused_from": NOW - datetime.timedelta(days=100), "resumed_on": NOW - datetime.timedelta(days=90)},
			{"paused_from": NOW - datetime.timedelta(days=10), "resumed_on": NOW - datetime.timedelta(days=9)},
			{"paused_from": NOW - datetime.timedelta(days=1), "resumed_on": None},
		]
		out = p.periods_for_config(periods, NOW)
		self.assertEqual(len(out), 2)
		self.assertIsNone(out[1]["to"])
		self.assertEqual(out[1]["from"], "2026-10-21 18:00:00")
		self.assertEqual(out[0]["to"], "2026-10-13 18:00:00")

	def test_empty(self):
		self.assertEqual(p.periods_for_config([], NOW), [])


if __name__ == "__main__":
	unittest.main()
