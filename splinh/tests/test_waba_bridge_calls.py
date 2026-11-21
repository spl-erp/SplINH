import os
import unittest
from unittest import mock

import frappe

from splinh import waba_bridge as wb
from splinh.api import call_tracking as real_ct

BASE = os.path.join(os.path.dirname(__file__), "..")


def _read(*parts):
	with open(os.path.join(BASE, *parts), encoding="utf-8") as f:
		return f.read()


class TestCleanName(unittest.TestCase):
	def test_dot_is_not_a_name(self):
		self.assertIsNone(wb._clean_name("."))
		self.assertIsNone(wb._clean_name("  "))
		self.assertIsNone(wb._clean_name(None))
		self.assertIsNone(wb._clean_name("\U0001F600"))

	def test_real_names_kept_and_tidied(self):
		self.assertEqual(wb._clean_name("  Asha   Rao "), "Asha Rao")
		self.assertEqual(wb._clean_name("A1"), "A1")


class TestCreateCallLog(unittest.TestCase):
	def _run(self, call, paused=False, exists=False):
		inserted = {}
		insert = lambda name, values, user, *a: inserted.update(values=values, user=user)
		db = mock.Mock()
		db.exists.side_effect = lambda dt, n=None: (exists if dt == "Call Log" else False) or (dt == "Call Log" and bool(inserted))
		doc = frappe._dict(call)
		with mock.patch.object(frappe, "get_doc", return_value=doc), mock.patch.object(frappe, "db", db), mock.patch.object(real_ct, "_insert_with_retry", side_effect=insert), mock.patch.object(real_ct, "_format_duration", return_value="1m 0s"), mock.patch.object(wb, "recording_paused_for", return_value=paused), mock.patch.object(wb, "_hand_to") as hand, mock.patch.object(wb, "_attach_recording") as attach, mock.patch("splinh.custom.call_employee.employee_for_user", return_value=(None, None)):
			wb.create_call_log("X")
		return inserted, hand, attach

	BASE_CALL = dict(name="X", call_id="c1", direction="Incoming", status="Completed", caller_phone="919000011122", caller_name=".", started_at="2026-11-20 10:00:00", ended_at="2026-11-20 10:01:00", duration=60, answered_by=None, started_by=None, recording_file=None)

	def test_number_has_plus_and_dot_name_dropped(self):
		inserted, _, _ = self._run(self.BASE_CALL)
		v = inserted["values"]
		self.assertEqual(v["from"], "+919000011122")
		self.assertIsNone(v["custom_contact_name"])
		self.assertIn("+919000011122", v["custom_call_summary"])

	def test_inserted_as_administrator_and_unanswered_stays_there(self):
		inserted, hand, _ = self._run(self.BASE_CALL)
		self.assertEqual(inserted["user"], "Administrator")
		hand.assert_not_called()

	def test_answered_call_is_handed_to_the_rep(self):
		inserted, hand, _ = self._run(dict(self.BASE_CALL, answered_by="rep@example.com"))
		self.assertEqual(inserted["user"], "Administrator")
		hand.assert_called_once_with("WABA-c1", "rep@example.com")

	def test_no_phone_and_no_start_time_do_not_break(self):
		inserted, _, _ = self._run(dict(self.BASE_CALL, caller_phone=None, started_at=None))
		self.assertIsNone(inserted["values"]["from"])
		self.assertIn("unknown number", inserted["values"]["custom_call_summary"])

	def test_recording_skipped_when_rep_paused(self):
		_, _, attach = self._run(dict(self.BASE_CALL, answered_by="rep@example.com", recording_file="/private/files/c1.ogg"), paused=True)
		attach.assert_not_called()

	def test_recording_attached_when_not_paused(self):
		_, _, attach = self._run(dict(self.BASE_CALL, answered_by="rep@example.com", recording_file="/private/files/c1.ogg"), paused=False)
		attach.assert_called_once()


class TestDashboardsCountApiCalls(unittest.TestCase):
	def test_every_whatsapp_count_includes_api_calls(self):
		for parts in (
			("custom", "call_monitoring_detail.py"),
			("custom", "call_monitoring_insights.py"),
			("custom", "kpi_dashboard_insights.py"),
			("custom", "kpi_dashboard_detail.py"),
			("splinh", "doctype", "sales_kpi_entry", "sales_kpi_entry.py"),
			("custom", "splinh_setup.py"),
		):
			self.assertIn("WhatsApp API", _read(*parts), "/".join(parts))

	def test_audio_types_include_webm(self):
		from splinh.custom.call_ai import AUDIO_MIME

		for ext in (".ogg", ".opus", ".webm"):
			self.assertIn(ext, AUDIO_MIME)


if __name__ == "__main__":
	unittest.main()
