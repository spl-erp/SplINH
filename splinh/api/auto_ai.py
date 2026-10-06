"""Entry point for the "SplINH Auto Transcribe" Server Script (Scheduler Event).

The script's whole body is:

    frappe.call("splinh.api.auto_ai.process_pending_recordings")

This module is deliberately separate from api/call_ai.py so that it is imported fresh
by the scheduler job - web workers cache modules they have already loaded, and
nothing here needs a restart. The logic lives in custom/call_ai_auto.py.
"""

import frappe

from splinh.custom import call_ai_auto


@frappe.whitelist(methods=["POST"])
def process_pending_recordings(dry_run=0):
	"""Run one auto-transcription pass. System Manager only (the scheduler runs as
	Administrator). `dry_run=1` returns the calls it WOULD process and writes nothing.
	"""
	frappe.only_for("System Manager")
	return call_ai_auto.process_pending(dry_run=bool(int(dry_run or 0)))
