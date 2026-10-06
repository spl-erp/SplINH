# Copyright (c) 2026, Splashjet Ink and contributors
# For license information, please see license.txt

import frappe
from frappe import _
from frappe.model.document import Document


# Frappe finds the controller by the doctype name with spaces removed and capitals KEPT
# ("SplINH Settings" -> SplINHSettings); any other spelling is "ImportError: SplINH Settings".
class SplINHSettings(Document):
	"""Gemini API key and options for the "Transcribe & Summarize" button.

	Read through splinh.custom.call_ai.conf()/api_key()/models(), which fall back to
	site_config.json for anything left blank. System Manager only: the key spends money.
	"""

	def validate(self):
		# Pasted keys often arrive with a trailing space or newline. A saved key shows as
		# a row of asterisks; leave that placeholder alone so saving does not overwrite it.
		key = self.gemini_api_key
		if key and not self.is_dummy_password(key):
			self.gemini_api_key = key.strip()

		if self.language_codes:
			self.language_codes = ", ".join(c.strip() for c in self.language_codes.split(",") if c.strip())

		if self.min_seconds and self.min_seconds < 0:
			frappe.throw(_("Minimum call length cannot be negative."))
		if self.max_seconds and self.min_seconds and self.max_seconds < self.min_seconds:
			frappe.throw(_("Maximum call length cannot be shorter than the minimum."))
