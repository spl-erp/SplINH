"""Entry point for the "SplINH Auto Transcribe" Server Script (Scheduler Event).

The script's whole body is:

    frappe.call("splinh.api.auto_ai.process_pending_recordings")

This module is deliberately separate from api/call_ai.py so that it is imported fresh
by the scheduler job - web workers cache modules they have already loaded, and
nothing here needs a restart. The logic lives in custom/call_ai_auto.py.
"""

import frappe

from splinh.custom import call_ai, call_ai_auto


@frappe.whitelist(methods=["POST"])
def process_pending_recordings(dry_run=0):
	"""Run one auto-transcription pass. System Manager only (the scheduler runs as
	Administrator). `dry_run=1` returns the calls it WOULD process and writes nothing.
	"""
	frappe.only_for("System Manager")
	return call_ai_auto.process_pending(dry_run=bool(int(dry_run or 0)))


@frappe.whitelist(methods=["POST"])
def bulk_preview(bulk_from=None, bulk_to=None):
	"""Settings-screen button: how many calls a bulk run over this range would analyse and a
	rough cost. Reads only. System Manager only."""
	frappe.only_for("System Manager")
	return _call(call_ai_auto.bulk_preview, bulk_from, bulk_to)


@frappe.whitelist(methods=["POST"])
def bulk_start(bulk_from=None, bulk_to=None, max_calls=None):
	"""Settings-screen button: register a bulk run over this range. The scheduled poll does
	the analysing (a few calls every 2 minutes); nothing is processed in this request."""
	frappe.only_for("System Manager")
	return _call(call_ai_auto.bulk_start, bulk_from, bulk_to, max_calls)


@frappe.whitelist(methods=["POST"])
def bulk_stop():
	"""Settings-screen button: stop the running bulk run (calls already analysed stay)."""
	frappe.only_for("System Manager")
	return _call(call_ai_auto.bulk_stop)


def _call(fn, *args):
	"""Run a bulk helper; turn the module's own errors into a normal validation message."""
	if fn is not call_ai_auto.bulk_stop and not (args[0] and args[1]):
		frappe.throw("Choose both 'Recordings from' and 'Recordings to'.")
	try:
		return fn(*args)
	except call_ai.AIError as e:
		frappe.throw(str(e))
