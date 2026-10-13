"""One entry per salesperson per day. The system-generated fields are Frappe
Virtual Fields (is_virtual=1 in the doctype JSON, backed by the properties
below) - always computed fresh from live data, never stored, never editable.
Only the Manual Entry fields are real stored columns.

See CHANGELOG.md for the confirmed logic each property implements - this file
should not drift from what was actually agreed; if a definition needs to
change, update the CHANGELOG entry too.
"""

import frappe
from frappe.model.document import Document
from frappe.utils import add_to_date, get_datetime

# Cold Call = outgoing call to a Lead that hasn't reached Opportunity stage yet.
# Confirmed explicitly: Replied/Interested still count as Cold, since they are
# not converting to Opportunity - the real cutoff is Opportunity-or-beyond.
LEAD_COLD_STATUSES = ("Lead", "Open", "Replied", "Interested")

# Visit id_type -> Existing/New. Opportunity is a provisional default (only 6
# real rows site-wide at time of writing) - trivial to flip later. Retailer
# has zero real records and isn't even a defined dropdown option - not handled.
VISIT_EXISTING_ID_TYPES = ("Customer", "Opportunity")
VISIT_NEW_ID_TYPES = ("Lead",)


class SalesKPIEntry(Document):
	def autoname(self):
		# One entry per user per day - this single line both names the record
		# sensibly and enforces that uniqueness for free (a duplicate name
		# insert fails outright, no separate validation needed).
		self.name = f"{self.user}-{self.entry_date}"

	def _day_bounds(self):
		"""(start, end) datetime bounds for self.entry_date, end exclusive."""
		start = get_datetime(self.entry_date)
		end = add_to_date(start, days=1)
		return start, end

	def _ready(self):
		return bool(self.user and self.entry_date)

	# ------------------------------------------------------------ Lead

	@property
	def lead_generation_count(self):
		if not self._ready():
			return 0
		start, end = self._day_bounds()
		return frappe.db.count(
			"Lead",
			filters=[
				["lead_owner", "=", self.user],
				["creation", ">=", start],
				["creation", "<", end],
			],
		)

	@property
	def lead_score(self):
		if not self._ready():
			return 0.0
		start, end = self._day_bounds()
		row = frappe.db.sql(
			"""
			SELECT AVG((COALESCE(custom_export_profile_percent, 0) + COALESCE(custom_profile_percent, 0)) / 2)
			FROM `tabLead`
			WHERE lead_owner = %s AND creation >= %s AND creation < %s
			""",
			(self.user, start, end),
		)
		return round(row[0][0], 2) if row and row[0][0] is not None else 0.0

	@property
	def lead_qualification_count(self):
		if not self._ready():
			return 0
		start, end = self._day_bounds()
		return frappe.db.count(
			"Lead",
			filters=[
				["lead_owner", "=", self.user],
				["custom_lead_score_updated_on", ">=", start],
				["custom_lead_score_updated_on", "<", end],
			],
		)

	@property
	def lead_qualification_score(self):
		if not self._ready():
			return 0.0
		start, end = self._day_bounds()
		row = frappe.db.sql(
			"""
			SELECT AVG((COALESCE(custom_export_profile_percent, 0) + COALESCE(custom_profile_percent, 0)) / 2)
			FROM `tabLead`
			WHERE lead_owner = %s AND custom_lead_score_updated_on >= %s AND custom_lead_score_updated_on < %s
			""",
			(self.user, start, end),
		)
		return round(row[0][0], 2) if row and row[0][0] is not None else 0.0

	# -------------------------------------------------------- Call Log

	def _call_counts(self):
		"""(cold, followup) - computed together in one query since both
		properties need the same joined rows; cached on the instance so
		reading both cold_call_count and followup_call_count only queries once."""
		if getattr(self, "_call_counts_cache", None) is not None:
			return self._call_counts_cache
		if not self._ready():
			return 0, 0
		start, end = self._day_bounds()
		rows = frappe.db.sql(
			"""
			SELECT cl.custom_lead AS lead, l.status AS lead_status
			FROM `tabCall Log` cl
			LEFT JOIN `tabLead` l ON l.name = cl.custom_lead
			WHERE cl.owner = %s AND cl.type = 'Outgoing'
			  AND cl.start_time >= %s AND cl.start_time < %s
			""",
			(self.user, start, end),
			as_dict=True,
		)
		cold = followup = 0
		for row in rows:
			if row.lead and row.lead_status in LEAD_COLD_STATUSES:
				cold += 1
			else:
				followup += 1
		self._call_counts_cache = (cold, followup)
		return self._call_counts_cache

	@property
	def cold_call_count(self):
		return self._call_counts()[0]

	@property
	def followup_call_count(self):
		return self._call_counts()[1]

	@property
	def whatsapp_call_count(self):
		if not self._ready():
			return 0
		start, end = self._day_bounds()
		return frappe.db.count(
			"Call Log",
			filters=[
				["owner", "=", self.user],
				["custom_source", "=", "WhatsApp"],
				["start_time", ">=", start],
				["start_time", "<", end],
			],
		)

	# ---------------------------------------------------------- Visits

	def _visit_counts(self):
		"""(existing, new) - same one-query-cached pattern as _call_counts."""
		if getattr(self, "_visit_counts_cache", None) is not None:
			return self._visit_counts_cache
		if not self._ready():
			return 0, 0
		start, end = self._day_bounds()
		rows = frappe.db.sql(
			"""
			SELECT id_type, COUNT(*) AS cnt
			FROM `tabStart-End Meeting`
			WHERE owner = %s AND start_meeting >= %s AND start_meeting < %s
			GROUP BY id_type
			""",
			(self.user, start, end),
			as_dict=True,
		)
		existing = new = 0
		for row in rows:
			if row.id_type in VISIT_EXISTING_ID_TYPES:
				existing += row.cnt
			elif row.id_type in VISIT_NEW_ID_TYPES:
				new += row.cnt
		self._visit_counts_cache = (existing, new)
		return self._visit_counts_cache

	@property
	def visit_existing_count(self):
		return self._visit_counts()[0]

	@property
	def visit_new_count(self):
		return self._visit_counts()[1]

	@property
	def meeting_duration_minutes(self):
		if not self._ready():
			return 0.0
		start, end = self._day_bounds()
		row = frappe.db.sql(
			"""
			SELECT SUM(TIMESTAMPDIFF(SECOND, start_meeting, end_meeting))
			FROM `tabStart-End Meeting`
			WHERE owner = %s AND start_meeting >= %s AND start_meeting < %s
			  AND end_meeting IS NOT NULL
			""",
			(self.user, start, end),
		)
		total_seconds = row[0][0] if row and row[0][0] is not None else 0
		return round(total_seconds / 60.0, 1)
