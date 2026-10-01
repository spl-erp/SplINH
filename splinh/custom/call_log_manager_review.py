"""Manager review fields on Call Log (2026-09-30). See
custom/splinh_setup.py:_ensure_call_manager_review_fields() for the fields
themselves and their permlevel=1 restriction to Call Tracker Manager / System
Manager - this module only holds the one computed-flag hook.
"""

import frappe


def sync_manager_remarked_flag(doc, method=None):
	"""custom_manager_remarked is never hand-edited (read_only=1) - it turns on by
	itself the moment Manager Feedback has any real content, so it can never be
	forgotten or left inconsistent with whether feedback was actually given.
	"""
	doc.custom_manager_remarked = 1 if (doc.custom_manager_feedback or "").strip() else 0
