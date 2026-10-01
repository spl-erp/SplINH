"""
Runtime query logic for the Issue pipeline tracker.

Deliberately separate from `splinh_setup.py`, which is config-application only
(Workflow/DocType/Property Setter setup, re-applied by `after_migrate`). This module
is called at request time, by the Issue Pipeline Tracker page.

No new schema: Issue already has `track_changes = 1`, so every save already writes a
`Version` document with a field-level diff. Once the Workflow (see
`_ensure_issue_workflow` in splinh_setup.py) drives `status` through
`workflow_action`, each transition is a save, so each transition is already a Version
row - this just reads that history back out in a usable shape.
"""

import json

import frappe


@frappe.whitelist()
def get_issue_pipeline_trace(issue_name):
	"""Current state + full status-transition history of one Issue.

	Returns:
	    {
	        "issue": <name>,
	        "current_status": <str>,
	        "owner": <user>,
	        "raised_by": <email>,
	        "solution_provider": <Solution Provider name or None>,
	        "modules": [<System Module name>, ...],
	        "trace": [
	            {"timestamp": <datetime>, "changed_by": <user>,
	             "from_state": <str>, "to_state": <str>},
	            ...
	        ],  # chronological, oldest first
	    }
	"""
	if not frappe.db.exists("Issue", issue_name):
		frappe.throw(frappe._("Issue {0} does not exist").format(issue_name))

	doc = frappe.get_doc("Issue", issue_name)
	# Read permission on the specific doc - honours the `All` role's `if_owner` grant,
	# so a reporter can trace their own issue but not someone else's.
	doc.check_permission("read")

	versions = frappe.get_all(
		"Version",
		filters={"ref_doctype": "Issue", "docname": issue_name},
		fields=["name", "owner", "creation", "data"],
		order_by="creation asc",
	)

	trace = []
	for version in versions:
		try:
			data = json.loads(version.data or "{}")
		except (TypeError, ValueError):
			continue
		for change in data.get("changed") or []:
			# Each entry is [fieldname, old_value, new_value].
			if len(change) >= 3 and change[0] == "status":
				trace.append(
					{
						"timestamp": version.creation,
						"changed_by": version.owner,
						"from_state": change[1],
						"to_state": change[2],
					}
				)

	return {
		"issue": doc.name,
		"current_status": doc.status,
		"owner": doc.owner,
		"raised_by": doc.raised_by,
		"solution_provider": doc.get("custom_solution_provider"),
		"modules": [row.system_module for row in (doc.get("custom_modules") or []) if row.system_module],
		"trace": trace,
	}
