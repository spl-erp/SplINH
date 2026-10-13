"""One-time move of the old single "Other Activity" set (Type / No. / Duration /
Summary, four columns on Sales KPI Entry) into the new child table
"Sales KPI Other Activity" (2026-10-13), so nothing already entered is lost.

Runs after the doctypes are synced ([post_model_sync] in patches.txt). Frappe leaves
the old columns in the database when a field is removed from a doctype, so they are
still readable here; they are left in place (harmless; `bench trim-tables` would drop
them). A site that never had those columns (a fresh install, e.g. production) has
nothing to move and this does nothing. Re-running is safe: an entry that already has
child rows is skipped.
"""

import frappe

OLD_COLUMNS = ("other_activity_type", "other_activity_count", "other_activity_duration", "other_activity_summary")


def execute():
	if not frappe.db.table_exists("Sales KPI Entry") or not all(
		frappe.db.has_column("Sales KPI Entry", column) for column in OLD_COLUMNS
	):
		return
	if not frappe.db.table_exists("Sales KPI Other Activity"):
		return

	moved = 0
	for entry in frappe.db.sql(
		"""
		SELECT name, owner, creation, modified, modified_by, other_activity_type, other_activity_count,
			other_activity_duration, other_activity_summary
		FROM `tabSales KPI Entry`
		WHERE IFNULL(other_activity_type, '') != '' OR IFNULL(other_activity_count, 0) != 0
			OR IFNULL(other_activity_duration, 0) != 0 OR IFNULL(other_activity_summary, '') != ''
		""",
		as_dict=True,
	):
		if frappe.db.exists("Sales KPI Other Activity", {"parent": entry.name, "parenttype": "Sales KPI Entry"}):
			continue
		row = frappe.get_doc(
			{
				"doctype": "Sales KPI Other Activity",
				"name": frappe.generate_hash(length=10),
				"parent": entry.name,
				"parenttype": "Sales KPI Entry",
				"parentfield": "other_activities",
				"idx": 1,
				"activity_type": entry.other_activity_type or "Other",  # Type is required on a row
				"activity_count": entry.other_activity_count or 0,
				"activity_duration": entry.other_activity_duration or 0,
				"activity_summary": entry.other_activity_summary,
				"owner": entry.owner,
				"creation": entry.creation,
				"modified": entry.modified,
				"modified_by": entry.modified_by,
			}
		)
		# db_insert: no parent save, so no validate hooks, no Version row, and the parent's
		# `modified` is not bumped.
		row.db_insert()
		moved += 1
	frappe.db.commit()
	print(f"move_other_activity_to_child_table: {moved} entr{'y' if moved == 1 else 'ies'} moved")
