"""WhatsApp calls on the phone: which methods a token may call, and the ring push (sent through a fake transport)."""

import json
import os
import unittest
from unittest import mock

import frappe

from splinh.custom import call_tracker_auth as auth
from splinh.custom import fcm_push as fcm

BASE = os.path.join(os.path.dirname(__file__), "..")


def read(*parts):
	with open(os.path.join(BASE, *parts), encoding="utf-8") as f:
		return f.read()


CALL = "/api/method/waba.api.calls.answer"
REGISTER = "/api/method/splinh.api.call_tracker_auth.register_push_token"
MESSAGES = "/api/method/waba.api.inbox.conversations"


class TestGate(unittest.TestCase):
	def test_call_methods_need_the_calls_switch(self):
		self.assertFalse(auth.is_request_allowed(CALL))
		self.assertFalse(auth.is_request_allowed(CALL, phone_inbox=True))
		self.assertTrue(auth.is_request_allowed(CALL, phone_inbox=True, phone_calls=True))
		self.assertTrue(auth.is_request_allowed(REGISTER, phone_calls=True))

	def test_calls_switch_does_not_open_the_inbox(self):
		self.assertFalse(auth.is_request_allowed(MESSAGES, phone_calls=True))

	def test_all_nine_call_methods_and_both_push_methods_are_listed(self):
		for name in ("my_calls", "offer", "answer", "decline", "hangup", "can_call", "request_permission", "place", "answer_sdp", "call_status"):
			self.assertIn(f"waba.api.calls.{name}", auth.WABA_CALL_METHODS)
		self.assertIn("splinh.api.call_tracker_auth.register_push_token", auth.WABA_CALL_METHODS)
		self.assertIn("splinh.api.call_tracker_auth.unregister_push_token", auth.WABA_CALL_METHODS)

	def test_cmd_form_is_still_refused(self):
		self.assertFalse(auth.is_request_allowed(CALL, cmd="frappe.client.get_list", phone_inbox=True, phone_calls=True))

	def test_path_kinds(self):
		self.assertTrue(auth.is_calls_path(CALL))
		self.assertFalse(auth.is_calls_path(MESSAGES))
		self.assertFalse(auth.is_inbox_path(CALL))

	def test_ip_bypass_follows_the_same_gate(self):
		from splinh.custom.call_tracker_ip import bypasses_ip_check

		args = ("Call Tracker Token", CALL, None, "key", "u@x.com", "u@x.com")
		self.assertFalse(bypasses_ip_check(*args, phone_inbox=True))
		self.assertTrue(bypasses_ip_check(*args, phone_inbox=True, phone_calls=True))


class TestMessage(unittest.TestCase):
	DATA = {"type": "waba_call", "kind": "ring", "call": "abc", "ring_expires_at": "2026-11-20 12:00:30"}

	def test_data_only_high_priority_with_short_life(self):
		m = fcm.build_message("tok", self.DATA)["message"]
		self.assertEqual(m["token"], "tok")
		self.assertEqual(m["android"], {"priority": "HIGH", "ttl": "30s"})
		self.assertNotIn("notification", m)
		self.assertTrue(all(isinstance(v, str) for v in m["data"].values()))

	def test_non_ring_events_live_longer(self):
		self.assertEqual(fcm.build_message("tok", dict(self.DATA, kind="ended"))["message"]["android"]["ttl"], "60s")

	def test_dead_token_detection(self):
		self.assertTrue(fcm.is_dead_token_error(404, ""))
		self.assertTrue(fcm.is_dead_token_error(400, '{"error":{"details":[{"errorCode":"UNREGISTERED"}]}}'))
		self.assertFalse(fcm.is_dead_token_error(500, "server busy"))


class TestSend(unittest.TestCase):
	DATA = TestMessage.DATA

	def _run(self, replies, tokens):
		sent, writes = [], []
		queue = list(replies)

		def transport(url, headers, body):
			sent.append(body["message"]["token"])
			return queue.pop(0)

		db = mock.Mock()
		db.get_value.side_effect = lambda dt, name, field: tokens.get(name)
		db.set_value.side_effect = lambda *a, **k: writes.append(a)
		with mock.patch.object(frappe, "db", db), mock.patch.object(frappe, "log_error"):
			count = fcm.send_to_devices(list(tokens), self.DATA, transport=transport)
		return count, sent, writes

	def test_each_device_gets_one_message(self):
		count, sent, _ = self._run([(200, "{}"), (200, "{}")], {"d1": "tok1", "d2": "tok2"})
		self.assertEqual((count, sent), (2, ["tok1", "tok2"]))

	def test_dead_device_is_switched_off_and_others_still_get_it(self):
		count, sent, writes = self._run([(404, "UNREGISTERED"), (200, "{}")], {"d1": "tok1", "d2": "tok2"})
		self.assertEqual((count, sent), (1, ["tok1", "tok2"]))
		self.assertEqual(writes[0][0:2], ("Call Tracker Token", "d1"))
		self.assertEqual(writes[0][2], {"push_enabled": 0, "push_token": None})

	def test_device_without_token_is_skipped(self):
		count, sent, _ = self._run([], {"d1": None})
		self.assertEqual((count, sent), (0, []))

	def test_no_key_means_nothing_is_queued(self):
		with mock.patch.object(fcm, "_key_present", return_value=False), mock.patch.object(frappe, "enqueue") as enqueue:
			fcm.push_call_event("u@x.com", TestMessage.DATA)
		enqueue.assert_not_called()

	def test_key_present_queues_for_enabled_devices_only(self):
		with mock.patch.object(fcm, "_key_present", return_value=True), mock.patch.object(frappe, "get_all", return_value=["d1"]) as get_all, mock.patch.object(frappe, "enqueue") as enqueue:
			fcm.push_call_event("u@x.com", TestMessage.DATA)
		self.assertEqual(get_all.call_args.kwargs["filters"], {"user": "u@x.com", "enabled": 1, "push_enabled": 1})
		self.assertEqual(enqueue.call_args.kwargs["devices"], ["d1"])
		self.assertTrue(enqueue.call_args.kwargs["enqueue_after_commit"])


class TestWiring(unittest.TestCase):
	def test_hook_is_registered_and_fields_exist(self):
		self.assertIn("splinh.custom.fcm_push.push_call_event", read("hooks.py"))
		token = json.loads(read("splinh", "doctype", "call_tracker_token", "call_tracker_token.json"))
		names = {f["fieldname"] for f in token["fields"]}
		self.assertTrue({"push_token", "push_enabled", "push_platform", "push_updated"} <= names)
		settings = json.loads(read("splinh", "doctype", "call_tracker_settings", "call_tracker_settings.json"))
		self.assertIn("fcm_service_account", {f["fieldname"] for f in settings["fields"]})

	def test_revoking_a_token_drops_its_push_token(self):
		src = read("custom", "call_tracker_auth.py")
		self.assertIn('"push_token": None, "push_enabled": 0', src)

	def test_register_methods_are_post_only(self):
		src = read("api", "call_tracker_auth.py")
		self.assertIn('@frappe.whitelist(methods=["POST"])\ndef register_push_token', src)
		self.assertIn('@frappe.whitelist(methods=["POST"])\ndef unregister_push_token', src)


if __name__ == "__main__":
	unittest.main()
