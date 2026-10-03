"""Call Log controller override (this app only; ERPNext itself is untouched).

Stock `CallLog.before_insert` (apps/erpnext/.../telephony/doctype/call_log/
call_log.py) runs two unindexed `LIKE '%number'` scans on every insert -
Contact Phone (~2s) and Lead (~11s on this site) - unconditionally, even if the
links are already set, and it can never match a stored "+91-9225144953" against a
"+919225144953" number anyway (the dash). The call-sync job has already resolved
the party through the indexed Phone Lookup and appended its link, so for those
inserts the scans are pure cost and are skipped.

Only inserts that opt in (doc.flags.splinh_party_resolved, set by
api/call_tracking._insert_with_retry) change behaviour; every other Call Log
insert - Exotel/Twilio integrations, manual entries - runs stock before_insert.
"""

from erpnext.telephony.doctype.call_log.call_log import CallLog


class SplinhCallLog(CallLog):
	def before_insert(self):
		if self.flags.get("splinh_party_resolved"):
			# Keep the one stock step that is not a phone scan: who received the call.
			if self.is_incoming_call():
				self.update_received_by()
			return
		super().before_insert()
