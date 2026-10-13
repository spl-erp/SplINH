"""One "other activity" a salesperson logs for the day (a row of Sales KPI Entry's
Other Activity table). Replaces the single Type/No./Duration/Summary set the entry had
before 2026-10-13, so several activities can be recorded per day.
"""

from frappe.model.document import Document


class SalesKPIOtherActivity(Document):
	pass
