import datetime
import unittest

from splinh.custom import call_tracker_auth as c

NOW = datetime.datetime(2026, 10, 22, 12, 0, 0)
KEY = "secret-key"


class TestRequestAllowed(unittest.TestCase):
	def test_every_listed_method_is_allowed(self):
		for method in c.ALLOWED_METHODS:
			self.assertTrue(c.is_request_allowed("/api/method/" + method), method)

	def test_trailing_slash_is_fine(self):
		self.assertTrue(c.is_request_allowed("/api/method/splinh.api.call_tracking.push_calls/"))

	def test_other_methods_and_routes_are_refused(self):
		for path in (
			"/api/method/frappe.client.get_list",
			"/api/method/frappe.handler.run_doc_method",
			"/api/resource/Lead",
			"/api/v2/method/splinh.api.call_tracking.push_calls",
			"/api/method/splinh.api.call_tracking.get_my_api_credentials",
			"/api/method/splinh.api.call_tracker_auth.app_login_start",
			"/app/lead",
			"/desk",
			"/",
			"",
			None,
		):
			self.assertFalse(c.is_request_allowed(path), path)

	def test_cmd_override_is_refused_even_on_an_allowed_path(self):
		self.assertFalse(c.is_request_allowed("/api/method/splinh.api.call_tracking.push_calls", "frappe.client.get_list"))


class TestDeviceId(unittest.TestCase):
	def test_valid(self):
		self.assertEqual(c.clean_device_id(" 8a862a795f7083f6 "), "8a862a795f7083f6")

	def test_invalid(self):
		for bad in (None, "", "short", "has space in it", "x" * 65, 12345678, ["abcdefgh"], "bad;chars!"):
			self.assertIsNone(c.clean_device_id(bad), bad)


class TestEmails(unittest.TestCase):
	def test_parse(self):
		text = "Prashant@Splashjetink.com, a@b.co;\nprashant@splashjetink.com  not-an-email\nx@y.in"
		self.assertEqual(c.parse_emails(text), ["prashant@splashjetink.com", "a@b.co", "x@y.in"])

	def test_empty(self):
		self.assertEqual(c.parse_emails(None), [])
		self.assertEqual(c.parse_emails(""), [])

	def test_mask(self):
		self.assertEqual(c.mask_email("prashant@splashjetink.com"), "p***@splashjetink.com")
		self.assertEqual(c.mask_email("nonsense"), "***")


class TestMergeRecipients(unittest.TestCase):
	def test_administrator_first_then_extras_without_duplicates(self):
		out = c.merge_recipients("Prashant@SplashjetInk.com", "extra@x.in, prashant@splashjetink.com", ["role@x.in", "extra@x.in"])
		self.assertEqual(out, ["prashant@splashjetink.com", "extra@x.in", "role@x.in"])

	def test_only_the_administrator(self):
		self.assertEqual(c.merge_recipients("admin@x.in", "", None, []), ["admin@x.in"])

	def test_administrator_without_email_and_nothing_else(self):
		self.assertEqual(c.merge_recipients(None, "", None, []), [])
		self.assertEqual(c.merge_recipients("", "  ", ["not-an-email"]), [])

	def test_follows_whatever_the_email_is_now(self):
		self.assertEqual(c.merge_recipients("new.address@company.com"), ["new.address@company.com"])


class TestIpRule(unittest.TestCase):
	OFFICE = ["183.177.126.223", "117.247.52.23", "122.169.40.250"]

	def test_inside_and_outside(self):
		self.assertTrue(c.ip_in_list("122.169.40.250", self.OFFICE))
		self.assertFalse(c.ip_in_list("49.36.42.10", self.OFFICE))

	def test_prefix_rule_like_frappe(self):
		self.assertTrue(c.ip_in_list("10.1.2.3", ["10.1."]))

	def test_no_list_means_unrestricted(self):
		self.assertTrue(c.ip_in_list("1.2.3.4", []))
		self.assertTrue(c.ip_in_list("1.2.3.4", None))

	def test_missing_request_ip_is_outside(self):
		self.assertFalse(c.ip_in_list(None, self.OFFICE))


class TestOtp(unittest.TestCase):
	def _state(self, otp="123456", attempts=0, minutes=10, cid="cid1"):
		return {
			"otp_hash": c.hash_otp(otp, cid, KEY),
			"attempts": attempts,
			"expires_at": str(NOW + datetime.timedelta(minutes=minutes)),
		}

	def test_new_otp_is_six_digits(self):
		for _ in range(50):
			code = c.new_otp()
			self.assertEqual(len(code), 6)
			self.assertTrue(code.isdigit())

	def test_hash_depends_on_code_challenge_and_key(self):
		self.assertNotEqual(c.hash_otp("111111", "a", KEY), c.hash_otp("111112", "a", KEY))
		self.assertNotEqual(c.hash_otp("111111", "a", KEY), c.hash_otp("111111", "b", KEY))
		self.assertNotEqual(c.hash_otp("111111", "a", KEY), c.hash_otp("111111", "a", "other"))

	def test_correct_code(self):
		self.assertEqual(c.evaluate_otp(self._state(), "123456", "cid1", NOW, 5, KEY)[0], "ok")

	def test_code_with_spaces_is_accepted(self):
		self.assertEqual(c.evaluate_otp(self._state(), " 123456 ", "cid1", NOW, 5, KEY)[0], "ok")

	def test_wrong_code_counts_an_attempt(self):
		result, state = c.evaluate_otp(self._state(), "000000", "cid1", NOW, 5, KEY)
		self.assertEqual(result, "wrong")
		self.assertEqual(state["attempts"], 1)

	def test_locks_at_the_limit(self):
		result, _ = c.evaluate_otp(self._state(attempts=4), "000000", "cid1", NOW, 5, KEY)
		self.assertEqual(result, "locked")

	def test_already_locked_refuses_even_the_right_code(self):
		self.assertEqual(c.evaluate_otp(self._state(attempts=5), "123456", "cid1", NOW, 5, KEY)[0], "locked")

	def test_expired(self):
		self.assertEqual(c.evaluate_otp(self._state(minutes=-1), "123456", "cid1", NOW, 5, KEY)[0], "expired")

	def test_unknown_challenge(self):
		self.assertEqual(c.evaluate_otp(None, "123456", "cid1", NOW, 5, KEY)[0], "unknown")

	def test_code_for_another_challenge_fails(self):
		self.assertEqual(c.evaluate_otp(self._state(cid="other"), "123456", "cid1", NOW, 5, KEY)[0], "wrong")

	def test_non_text_code_is_wrong_not_a_crash(self):
		self.assertEqual(c.evaluate_otp(self._state(), None, "cid1", NOW, 5, KEY)[0], "wrong")


class TestPlanDevice(unittest.TestCase):
	T = [{"name": "t1", "device_id": "dev-aaaa"}, {"name": "t2", "device_id": "dev-bbbb"}]

	def test_reuse_same_device(self):
		self.assertEqual(c.plan_device(self.T, "dev-bbbb", 1), ("reuse", "t2", []))

	def test_first_device(self):
		self.assertEqual(c.plan_device([], "dev-new1", 1), ("issue", None, []))

	def test_new_device_at_the_limit_replaces_the_oldest(self):
		self.assertEqual(c.plan_device([self.T[0]], "dev-new1", 1), ("issue", None, ["t1"]))

	def test_limit_two_with_two_active_drops_the_oldest(self):
		self.assertEqual(c.plan_device(self.T, "dev-new1", 2), ("issue", None, ["t1"]))

	def test_room_left_keeps_everything(self):
		self.assertEqual(c.plan_device([self.T[0]], "dev-new1", 3), ("issue", None, []))

	def test_limit_lowered_below_current_count(self):
		self.assertEqual(c.plan_device(self.T, "dev-new1", 1), ("issue", None, ["t1", "t2"]))

	def test_zero_or_missing_limit_means_one(self):
		self.assertEqual(c.plan_device([self.T[0]], "dev-new1", 0), ("issue", None, ["t1"]))
		self.assertEqual(c.plan_device([self.T[0]], "dev-new1", None), ("issue", None, ["t1"]))


class TestInactivity(unittest.TestCase):
	def test_used_recently(self):
		self.assertFalse(c.is_unused_too_long(NOW - datetime.timedelta(days=5), NOW - datetime.timedelta(days=90), NOW, 30))

	def test_unused_too_long(self):
		self.assertTrue(c.is_unused_too_long(NOW - datetime.timedelta(days=31), NOW - datetime.timedelta(days=90), NOW, 30))

	def test_never_used_falls_back_to_creation(self):
		self.assertTrue(c.is_unused_too_long(None, NOW - datetime.timedelta(days=40), NOW, 30))
		self.assertFalse(c.is_unused_too_long(None, NOW - datetime.timedelta(days=2), NOW, 30))

	def test_zero_days_disables_the_rule(self):
		self.assertFalse(c.is_unused_too_long(NOW - datetime.timedelta(days=999), None, NOW, 0))

	def test_nothing_to_compare(self):
		self.assertFalse(c.is_unused_too_long(None, None, NOW, 30))


class TestFingerprint(unittest.TestCase):
	def test_changes_when_the_hash_changes(self):
		self.assertNotEqual(c.fingerprint("$pbkdf2-sha256$a"), c.fingerprint("$pbkdf2-sha256$b"))

	def test_stable_and_short(self):
		self.assertEqual(c.fingerprint("x"), c.fingerprint("x"))
		self.assertEqual(len(c.fingerprint("x")), 32)

	def test_missing_hash(self):
		self.assertEqual(c.fingerprint(None), "")
		self.assertEqual(c.fingerprint(""), "")


class TestCleanText(unittest.TestCase):
	def test_trim_cap_and_single_line(self):
		self.assertEqual(c.clean_text("  a\nb\x00c  ", 10), "a b c")
		self.assertEqual(len(c.clean_text("x" * 99, 10)), 10)

	def test_non_text(self):
		for bad in (None, {}, [], ("a",)):
			self.assertEqual(c.clean_text(bad, 10), "")


if __name__ == "__main__":
	unittest.main()
