import datetime
import unittest

from splinh.api import app_status as a

NOW = datetime.datetime(2026, 10, 13, 12, 0, 0)


class TestCleanText(unittest.TestCase):
	def test_trims_and_limits(self):
		self.assertEqual(a.clean_text("  SM-G991B  ", 80), "SM-G991B")
		self.assertEqual(a.clean_text("x" * 100, 40), "x" * 40)

	def test_single_line(self):
		self.assertEqual(a.clean_text("line1\nline2\tend\x00", 100), "line1 line2 end")

	def test_empty_and_wrong_types(self):
		for bad in (None, "", "   ", {}, [], ["a"], {"a": 1}):
			self.assertIsNone(a.clean_text(bad, 10))

	def test_numbers_become_text(self):
		self.assertEqual(a.clean_text(14, 10), "14")


class TestCleanFlag(unittest.TestCase):
	def test_accepted_spellings(self):
		for yes in (1, True, "1", "true", "YES", " on ", 2.0):
			self.assertEqual(a.clean_flag(yes), 1, yes)
		for no in (0, False, "0", "false", "No", "off", 0.0):
			self.assertEqual(a.clean_flag(no), 0, no)

	def test_garbage_is_skipped(self):
		for bad in (None, "", "maybe", [], {}, "2x"):
			self.assertIsNone(a.clean_flag(bad))


class TestCleanCount(unittest.TestCase):
	def test_numbers(self):
		self.assertEqual(a.clean_count(7), 7)
		self.assertEqual(a.clean_count("12"), 12)
		self.assertEqual(a.clean_count(3.9), 3)
		self.assertEqual(a.clean_count(0), 0)

	def test_range(self):
		self.assertIsNone(a.clean_count(-1))
		self.assertEqual(a.clean_count(10**12), a.MAX_COUNT)

	def test_garbage(self):
		for bad in (None, "", "abc", True, [], {}, float("nan"), float("inf")):
			self.assertIsNone(a.clean_count(bad))


class TestCleanTime(unittest.TestCase):
	def test_epoch_milliseconds_and_seconds_agree(self):
		ms = a.clean_time(1791806183705, now=NOW + datetime.timedelta(days=400))
		sec = a.clean_time(1791806183, now=NOW + datetime.timedelta(days=400))
		self.assertIsNotNone(ms)
		self.assertEqual(ms.replace(microsecond=0), sec)

	def test_numeric_string(self):
		now = NOW + datetime.timedelta(days=400)
		self.assertEqual(a.clean_time("1791806183", now=now), a.clean_time(1791806183, now=now))

	def test_iso_naive(self):
		self.assertEqual(a.clean_time("2026-10-13 09:30:00", now=NOW), datetime.datetime(2026, 10, 13, 9, 30))

	def test_iso_with_timezone_becomes_naive(self):
		parsed = a.clean_time("2026-10-13T04:00:00+00:00", now=NOW)
		self.assertIsNotNone(parsed)
		self.assertIsNone(parsed.tzinfo)

	def test_future_and_ancient_are_rejected(self):
		self.assertIsNone(a.clean_time("2026-10-20 00:00:00", now=NOW))
		self.assertIsNone(a.clean_time("2001-01-01 00:00:00", now=NOW))
		self.assertIsNone(a.clean_time(0, now=NOW))

	def test_garbage(self):
		for bad in (None, "", "not a time", True, [], {}, "99999999999999999999999999"):
			self.assertIsNone(a.clean_time(bad, now=NOW))


class TestCleanPayload(unittest.TestCase):
	def test_full_payload(self):
		raw = {
			"device_id": "54f82f836657c65b",
			"device_model": "Samsung SM-G991B",
			"android_version": "14",
			"app_version": "1.2.2",
			"last_sync_at": "2026-10-13 11:40:00",
			"perm_call_log": 1,
			"perm_audio": "true",
			"perm_notifications": 0,
			"battery_exempt": False,
			"rec_pending": 3,
			"rec_failed": "1",
			"rec_last_error": "HTTP 401",
		}
		out = a.clean_payload(raw, now=NOW)
		self.assertEqual(out["custom_inh_device_id"], "54f82f836657c65b")
		self.assertEqual(out["custom_inh_app_version"], "1.2.2")
		self.assertEqual(out["custom_inh_last_sync"], datetime.datetime(2026, 10, 13, 11, 40))
		self.assertEqual(out["custom_inh_perm_call_log"], 1)
		self.assertEqual(out["custom_inh_perm_audio"], 1)
		self.assertEqual(out["custom_inh_perm_notifications"], 0)
		self.assertEqual(out["custom_inh_battery_exempt"], 0)
		self.assertEqual(out["custom_inh_rec_pending"], 3)
		self.assertEqual(out["custom_inh_rec_failed"], 1)
		self.assertEqual(out["custom_inh_rec_last_error"], "HTTP 401")
		self.assertEqual(len(out), 12)

	def test_everything_optional(self):
		self.assertEqual(a.clean_payload({}, now=NOW), {})
		self.assertEqual(a.clean_payload(None, now=NOW), {})
		self.assertEqual(a.clean_payload("not a dict", now=NOW), {})

	def test_unknown_and_user_fields_are_ignored(self):
		raw = {"user": "Administrator", "name": "x", "owner": "y", "cmd": "z", "api_secret": "s", "enabled": 0, "restrict_ip": "1.1.1.1"}
		self.assertEqual(a.clean_payload(raw, now=NOW), {})

	def test_bad_values_are_skipped_not_fatal(self):
		raw = {"rec_pending": "lots", "perm_audio": "maybe", "last_sync_at": "yesterday-ish", "app_version": "1.2.2"}
		self.assertEqual(a.clean_payload(raw, now=NOW), {"custom_inh_app_version": "1.2.2"})

	def test_long_error_is_truncated(self):
		out = a.clean_payload({"rec_last_error": "e" * 5000}, now=NOW)
		self.assertEqual(len(out["custom_inh_rec_last_error"]), a.ERROR_MAX if hasattr(a, "ERROR_MAX") else 500)

	def test_only_expected_columns_are_ever_written(self):
		raw = {k: "1" for k in list(a.TEXT_FIELDS) + list(a.FLAG_FIELDS) + list(a.COUNT_FIELDS)}
		raw["last_sync_at"] = "2026-10-13 10:00:00"
		allowed = {a.FIELD_PREFIX + k for k in list(a.TEXT_FIELDS) + list(a.FLAG_FIELDS) + list(a.COUNT_FIELDS)} | {
			a.FIELD_PREFIX + "last_sync"
		}
		self.assertLessEqual(set(a.clean_payload(raw, now=NOW)), allowed)


if __name__ == "__main__":
	unittest.main()
