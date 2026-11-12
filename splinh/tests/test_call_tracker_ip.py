"""Offline tests for custom/call_tracker_ip.py: Call Tracker token requests skip the restrict_ip list, nothing else does.

No site and no database: the request, the token lookup and the frappe.auth module are replaced.

Run:  cd apps/splinh && ../../env/bin/python -m unittest splinh.tests.test_call_tracker_ip
"""

import types
import unittest
from unittest import mock

from splinh.custom import call_tracker_ip as ip

PUSH = "/api/method/splinh.api.call_tracking.push_calls"
SOURCE = "Call Tracker Token"


class Blocked(Exception):
	pass


def original(user):
	raise Blocked(user)  # the real check refusing an outside IP


class Env:
	"""One request: headers, path, cmd, the key in the Authorization header and the user of the enabled token with it."""

	def __init__(self, source=SOURCE, path=PUSH, cmd=None, key="k1", token_user="rep@x.com", has_request=True):
		self.source, self.path, self.cmd, self.key, self.token_user = source, path, cmd, key, token_user
		self.has_request = has_request
		self.lookups = []

	def __enter__(self):
		headers = {} if self.source is None else {ip.SOURCE_HEADER: self.source}
		request = types.SimpleNamespace(headers=headers, path=self.path) if self.has_request else None
		fake = mock.MagicMock()
		fake.local = types.SimpleNamespace(request=request)
		fake.form_dict = {"cmd": self.cmd} if self.cmd else {}

		def get_value(doctype, filters, fieldname):
			self.lookups.append((doctype, filters, fieldname))
			return self.token_user

		fake.db.get_value.side_effect = get_value
		self.patches = [mock.patch.object(ip, "frappe", fake), mock.patch.object(ip, "request_api_key", lambda: self.key)]
		for p in self.patches:
			p.start()
		return self

	def __exit__(self, *exc):
		for p in self.patches:
			p.stop()


def patched(previous=original):
	module = types.SimpleNamespace(validate_ip_address=previous)
	ip.patch_validate_ip_address(module)
	return module


class TestDecision(unittest.TestCase):
	def test_a_token_request_of_the_same_user_on_an_allowed_path_skips_the_check(self):
		self.assertTrue(ip.bypasses_ip_check(SOURCE, PUSH, None, "k", "a@x.com", "a@x.com"))

	def test_every_other_case_does_not(self):
		cases = {
			"other source": (None, PUSH, None, "k", "a@x.com", "a@x.com"),
			"wrong source": ("User", PUSH, None, "k", "a@x.com", "a@x.com"),
			"other path": (SOURCE, "/api/method/frappe.client.get_list", None, "k", "a@x.com", "a@x.com"),
			"cmd set": (SOURCE, PUSH, "frappe.client.get_list", "k", "a@x.com", "a@x.com"),
			"no key": (SOURCE, PUSH, None, None, "a@x.com", "a@x.com"),
			"no token": (SOURCE, PUSH, None, "k", None, "a@x.com"),
			"token of another user": (SOURCE, PUSH, None, "k", "b@x.com", "a@x.com"),
			"no user": (SOURCE, PUSH, None, "k", None, None),
		}
		for name, args in cases.items():
			self.assertFalse(ip.bypasses_ip_check(*args), name)


class TestWrapper(unittest.TestCase):
	def test_a_valid_token_request_from_any_ip_is_let_through_without_the_previous_check(self):
		module = patched()
		with Env():
			self.assertIsNone(module.validate_ip_address("rep@x.com"))

	def test_the_token_lookup_only_asks_for_an_enabled_token_by_key(self):
		module = patched()
		with Env() as env:
			module.validate_ip_address("rep@x.com")
		self.assertEqual(env.lookups, [("Call Tracker Token", {"api_key": "k1", "enabled": 1}, "user")])

	def test_everything_else_reaches_the_previous_check(self):
		cases = {
			"no Source header (plain API key or cookie)": dict(source=None),
			"another Source": dict(source="User"),
			"a path outside the allowlist": dict(path="/api/method/frappe.client.get_list"),
			"a ?cmd= override": dict(cmd="frappe.client.get_list"),
			"no key": dict(key=None),
			"unknown or disabled token": dict(token_user=None),
			"a token of another user": dict(token_user="other@x.com"),
			"no request at all (a job or a console)": dict(has_request=False),
		}
		for name, kwargs in cases.items():
			module = patched()
			with Env(**kwargs):
				with self.assertRaises(Blocked, msg=name):
					module.validate_ip_address("rep@x.com")

	def test_the_lookup_is_skipped_when_the_source_header_is_not_ours(self):
		module = patched()
		with Env(source=None) as env:
			with self.assertRaises(Blocked):
				module.validate_ip_address("rep@x.com")
		self.assertEqual(env.lookups, [])  # ordinary traffic pays nothing

	def test_an_error_while_deciding_falls_back_to_the_previous_check(self):
		module = patched()
		with Env() as env:
			ip.frappe.db.get_value.side_effect = RuntimeError("db down")
			with self.assertRaises(Blocked):
				module.validate_ip_address("rep@x.com")

	def test_guest_and_empty_users_go_to_the_previous_check(self):
		for user in ("Guest", "", None):
			module = patched()
			with Env():
				with self.assertRaises(Blocked):
					module.validate_ip_address(user)

	def test_the_previous_result_is_passed_through(self):
		module = patched(lambda user: "previous:" + user)
		with Env(source=None):
			self.assertEqual(module.validate_ip_address("a@x.com"), "previous:a@x.com")


class TestInstall(unittest.TestCase):
	def test_installing_twice_wraps_once(self):
		module = types.SimpleNamespace(validate_ip_address=original)
		ip.patch_validate_ip_address(module)
		first = module.validate_ip_address
		ip.patch_validate_ip_address(module)
		self.assertIs(module.validate_ip_address, first)

	def test_it_stacks_with_another_wrapper_installed_later(self):
		calls = []

		def vendor(previous):
			def wrapped(user):
				calls.append("vendor")
				return previous(user)

			return wrapped

		module = types.SimpleNamespace(validate_ip_address=original)
		ip.patch_validate_ip_address(module)
		module.validate_ip_address = vendor(module.validate_ip_address)  # a vendor wrapper on top of ours
		ip.patch_validate_ip_address(module)  # installing again must not add a second layer
		with Env():
			self.assertIsNone(module.validate_ip_address("rep@x.com"))  # a token request still passes
		self.assertEqual(calls, ["vendor"])
		with Env(source=None):
			with self.assertRaises(Blocked):
				module.validate_ip_address("rep@x.com")

	def test_it_stacks_with_another_wrapper_installed_earlier(self):
		def vendor(user):
			if user == "mobile@x.com":
				return True  # their own bypass
			return original(user)

		module = types.SimpleNamespace(validate_ip_address=vendor)
		ip.patch_validate_ip_address(module)
		with Env(source=None):
			self.assertTrue(module.validate_ip_address("mobile@x.com"))  # their bypass still works
			with self.assertRaises(Blocked):
				module.validate_ip_address("rep@x.com")  # ours does not widen it

	def test_nothing_to_wrap_is_not_an_error(self):
		ip.patch_validate_ip_address(types.SimpleNamespace())


if __name__ == "__main__":
	unittest.main()
