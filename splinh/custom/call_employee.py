"""Which Employee does a call belong to?

The phone app's payload carries no employee, so `Call Log.call_received_by` used to
be filled only by Frappe's "single allowed record" Link default - which silently
stopped working for anyone with several Employee permissions, and for everyone once
"Ignore User Permissions" is ticked on the field. The caller is known (the sync job
runs as that user), so the Employee is resolved explicitly:

  1. the Employee whose `user_id` is the caller (Active first);
  2. else the caller's Employee User Permission, if there is exactly one;
  3. else None (the field stays blank - never guessed).
"""

import frappe


def employee_for_user(user):
	"""(employee_name, user_id_or_None) for a user, or (None, None)."""
	if not user or user in ("Administrator", "Guest"):
		return None, None
	rows = frappe.db.sql(
		"SELECT name FROM `tabEmployee` WHERE user_id = %s ORDER BY status = 'Active' DESC, name LIMIT 1",
		(user,),
	)
	if rows:
		return rows[0][0], user
	allowed = frappe.db.sql(
		"SELECT DISTINCT for_value FROM `tabUser Permission` WHERE user = %s AND allow = 'Employee' LIMIT 2",
		(user,),
	)
	if len(allowed) == 1:
		return allowed[0][0], None
	return None, None


def backfill(fix=False, log=print):
	"""Fill blank `call_received_by` from each Call Log's owner using the same rule.
	Touches only rows where the field is empty. Returns stats; lists every change."""
	owners = frappe.db.sql(
		"SELECT owner, COUNT(*) FROM `tabCall Log` WHERE IFNULL(call_received_by, '') = '' GROUP BY owner"
	)
	stats = {"owners": len(owners), "rows_fixed": 0, "rows_unresolvable": 0, "unresolvable": []}
	for owner, count in owners:
		employee, user_id = employee_for_user(owner)
		if not employee:
			stats["rows_unresolvable"] += count
			stats["unresolvable"].append(f"{owner} ({count})")
			continue
		log(f"{owner} -> {employee}: {count} calls")
		if fix:
			frappe.db.sql(
				"""UPDATE `tabCall Log` SET call_received_by = %s,
				employee_user_id = IF(IFNULL(employee_user_id, '') = '', %s, employee_user_id)
				WHERE owner = %s AND IFNULL(call_received_by, '') = ''""",
				(employee, user_id, owner),
			)
			frappe.db.commit()
		stats["rows_fixed"] += count
	return stats
