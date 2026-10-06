"""Offline tests for how the AI options are resolved (SplINH Settings screen ->
site_config.json -> built-in default). No site, no network, no real key.

Run:  cd apps/splinh && ../../env/bin/python -m unittest splinh.tests.test_call_ai_settings
"""

import unittest
from unittest import mock

import frappe
import requests

from splinh.api import call_ai as api
from splinh.custom import call_ai
from splinh.custom.call_ai import AIError


class FakeSettings(dict):
	"""Stands in for the saved SplINH Settings document."""

	def __init__(self, key=None, key_error=False, **fields):
		super().__init__(fields)
		self._key, self._key_error = key, key_error

	def get_password(self, fieldname, raise_exception=True):
		if self._key_error:
			raise RuntimeError("decryption failed")
		return self._key


def with_config(settings=None, site=None):
	"""Patch the two places call_ai reads from."""
	site = site or {}
	return mock.patch.multiple(call_ai, _load_settings=lambda: settings, _site_conf=lambda key: site.get(key))


class TestPrecedence(unittest.TestCase):
	def test_screen_beats_site_config_beats_default(self):
		site = {"splinh_ai_thinking_level": "high"}
		with with_config(FakeSettings(thinking_level="minimal"), site):
			self.assertEqual(call_ai.conf("splinh_ai_thinking_level", "low"), "minimal")
		with with_config(FakeSettings(thinking_level=""), site):
			self.assertEqual(call_ai.conf("splinh_ai_thinking_level", "low"), "high")  # blank -> site_config
		with with_config(FakeSettings(thinking_level=""), {}):
			self.assertEqual(call_ai.conf("splinh_ai_thinking_level", "low"), "low")  # blank -> default

	def test_screen_never_saved_uses_site_config_and_defaults(self):
		with with_config(None, {"splinh_ai_glossary": "Splashjet"}):
			self.assertEqual(call_ai.conf("splinh_ai_glossary"), "Splashjet")
			self.assertTrue(call_ai.conf("splinh_ai_enabled", True))  # never-saved screen must not switch AI off

	def test_integers(self):
		with with_config(FakeSettings(min_seconds=25, max_seconds=None, daily_limit=0), {"splinh_ai_daily_limit": 7}):
			self.assertEqual(call_ai.conf("splinh_ai_min_seconds", 10), 25)
			self.assertEqual(call_ai.conf("splinh_ai_max_seconds", 0), 0)  # empty = no maximum
			self.assertEqual(call_ai.conf("splinh_ai_daily_limit", 100), 7)  # 0/blank falls through

	def test_checkboxes_are_authoritative_once_saved(self):
		with with_config(FakeSettings(romanize=0, enabled=0, diarization=1), {"splinh_ai_romanize": True}):
			self.assertFalse(call_ai.conf("splinh_ai_romanize", True))
			self.assertFalse(call_ai.conf("splinh_ai_enabled", True))
			self.assertTrue(call_ai.conf("splinh_ai_diarization", False))

	def test_language_hints_are_split(self):
		with with_config(FakeSettings(language_codes="hi-IN, en-IN ,"), {}):
			self.assertEqual(call_ai.conf("splinh_ai_language_codes"), ["hi-IN", "en-IN"])
		with with_config(FakeSettings(language_codes=""), {}):
			self.assertIsNone(call_ai.conf("splinh_ai_language_codes"))  # empty = auto-detect


class TestModels(unittest.TestCase):
	def test_defaults(self):
		with with_config(None, {}):
			self.assertEqual(call_ai.models(), call_ai.DEFAULT_MODELS)

	def test_screen_overrides_only_what_is_filled(self):
		with with_config(FakeSettings(transcribe_model="gemini-x", summary_model="  "), {}):
			chosen = call_ai.models()
		self.assertEqual(chosen["transcribe"], "gemini-x")
		self.assertEqual(chosen["summary"], call_ai.DEFAULT_MODELS["summary"])

	def test_site_config_models_still_work(self):
		with with_config(None, {"splinh_ai_models": {"summary": "gemini-3.1-flash-lite"}}):
			self.assertEqual(call_ai.models()["summary"], "gemini-3.1-flash-lite")


class TestApiKey(unittest.TestCase):
	def test_key_comes_from_the_screen_first(self):
		with with_config(FakeSettings(key="screen-key"), {"gemini_api_key": "file-key"}):
			self.assertEqual(call_ai.api_key(), "screen-key")

	def test_falls_back_to_site_config(self):
		with with_config(FakeSettings(key=None), {"gemini_api_key": "file-key"}):
			self.assertEqual(call_ai.api_key(), "file-key")
		with with_config(None, {"gemini_api_key": "file-key"}):
			self.assertEqual(call_ai.api_key(), "file-key")

	def test_decryption_problem_falls_back_instead_of_breaking(self):
		with with_config(FakeSettings(key_error=True), {"gemini_api_key": "file-key"}):
			self.assertEqual(call_ai.api_key(), "file-key")

	def test_pasted_whitespace_is_trimmed(self):
		with with_config(FakeSettings(key="  screen-key\n"), {}):
			self.assertEqual(call_ai.api_key(), "screen-key")
		with with_config(None, {"gemini_api_key": "file-key \n"}):
			self.assertEqual(call_ai.api_key(), "file-key")

	def test_whitespace_only_key_counts_as_missing(self):
		with with_config(FakeSettings(key="  \n"), {}):
			with self.assertRaises(AIError):
				call_ai.api_key()

	def test_no_key_anywhere_is_a_friendly_error(self):
		with with_config(FakeSettings(key=None), {}):
			with self.assertRaises(AIError) as ctx:
				call_ai.api_key()
		self.assertIn("SplINH Settings", str(ctx.exception))


class TestLoadSettings(unittest.TestCase):
	def setUp(self):
		for attr in ("splinh_ai_settings",):
			try:
				delattr(frappe.local, attr)
			except AttributeError:
				pass

	def tearDown(self):
		self.setUp()

	def test_missing_table_never_raises(self):
		broken_db = mock.MagicMock()
		broken_db.get_single_value.side_effect = Exception("Table 'tabSplINH Settings' doesn't exist")
		with mock.patch.object(frappe, "db", broken_db, create=True):
			self.assertIsNone(call_ai._load_settings())

	def test_the_ui_built_screen_wins_and_the_shipped_one_is_the_fallback(self):
		saved = {"SplINH AI Settings": "2026-10-04", "SplINH Settings": "2026-10-04"}
		docs = {"SplINH AI Settings": "ui-doc", "SplINH Settings": "shipped-doc"}

		def make(saved):
			db = mock.MagicMock()
			db.get_single_value.side_effect = lambda doctype, field: saved.get(doctype)
			return db

		with mock.patch.object(frappe, "db", make(saved), create=True), mock.patch.object(
			frappe, "get_doc", side_effect=lambda doctype: docs[doctype]
		):
			self.assertEqual(call_ai._load_settings(), "ui-doc")
		self.setUp()  # drop the per-request cache
		only_shipped = {"SplINH Settings": "2026-10-04"}
		with mock.patch.object(frappe, "db", make(only_shipped), create=True), mock.patch.object(
			frappe, "get_doc", side_effect=lambda doctype: docs[doctype]
		):
			self.assertEqual(call_ai._load_settings(), "shipped-doc")

	def test_never_saved_screen_is_ignored(self):
		fresh_db = mock.MagicMock()
		fresh_db.get_single_value.return_value = None  # no `modified` -> never saved
		with mock.patch.object(frappe, "db", fresh_db, create=True):
			self.assertIsNone(call_ai._load_settings())


class TestCheckKey(unittest.TestCase):
	def reply(self, status, body=None):
		response = mock.Mock(status_code=status)
		response.json.return_value = body or {}
		return response

	def test_accepted(self):
		with mock.patch.object(requests, "get", return_value=self.reply(200)):
			ok, message = call_ai.check_key("SECRET-KEY-123")
		self.assertTrue(ok)
		self.assertNotIn("SECRET-KEY-123", message)

	def test_rejected_shows_googles_reason_but_never_the_key(self):
		body = {"error": {"message": "API key not valid. Please pass a valid API key."}}
		with mock.patch.object(requests, "get", return_value=self.reply(400, body)):
			ok, message = call_ai.check_key("SECRET-KEY-123")
		self.assertFalse(ok)
		self.assertIn("HTTP 400", message)
		self.assertIn("not valid", message)
		self.assertNotIn("SECRET-KEY-123", message)

	def test_network_trouble(self):
		with mock.patch.object(requests, "get", side_effect=requests.Timeout("slow")):
			ok, message = call_ai.check_key("SECRET-KEY-123")
		self.assertFalse(ok)
		self.assertNotIn("SECRET-KEY-123", message)

	def test_empty_key(self):
		self.assertFalse(call_ai.check_key("")[0])


class TestTestKeyEndpoint(unittest.TestCase):
	def test_only_system_manager(self):
		with mock.patch.object(api.frappe, "only_for", side_effect=PermissionError("no")):
			with self.assertRaises(PermissionError):
				api.test_gemini_key("anything")

	def test_masked_placeholder_tests_the_saved_key(self):
		with mock.patch.object(api.frappe, "only_for"), mock.patch.object(
			call_ai, "api_key", return_value="stored-key"
		), mock.patch.object(call_ai, "check_key", return_value=(True, "ok")) as check:
			self.assertEqual(api.test_gemini_key("********"), {"ok": True, "message": "ok"})
			check.assert_called_once_with("stored-key")

	def test_typed_key_is_tested_as_typed(self):
		with mock.patch.object(api.frappe, "only_for"), mock.patch.object(
			call_ai, "check_key", return_value=(False, "bad")
		) as check:
			self.assertEqual(api.test_gemini_key("  typed-key \n"), {"ok": False, "message": "bad"})
			check.assert_called_once_with("typed-key")

	def test_no_saved_key_is_reported_not_raised(self):
		with mock.patch.object(api.frappe, "only_for"), mock.patch.object(
			call_ai, "api_key", side_effect=AIError("The Gemini API key is not set.")
		):
			result = api.test_gemini_key("")
		self.assertFalse(result["ok"])


if __name__ == "__main__":
	unittest.main()
