# Copyright (c) 2026, Splashjet Ink and contributors
# For license information, please see license.txt

from frappe.model.document import Document


class PhoneLookup(Document):
	# Rows are written by raw upserts in splinh/custom/phone_lookup.py (the table
	# is a derived index of Lead/Contact phones, rebuilt from them at any time) -
	# never edited by hand.
	pass
