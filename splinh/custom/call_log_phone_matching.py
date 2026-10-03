"""Call Log -> Lead/Customer auto-link helpers.

The number matching itself lives in `custom/phone_lookup.py` (an indexed
`Phone Lookup` table: one exact lookup per call instead of a scan). What is left
here is everything around it: making the resolved party a real Dynamic Link row,
and the `rematch-call-logs` command that re-runs the match over existing records.

History (2026-10): matching used to run at query time against the raw
Lead/Contact phone columns - first stock ERPNext's unindexable `LIKE '%number'`,
then our own `REGEXP_REPLACE ... RIGHT(...,10)` "last 10 digits" variant. Both
scanned every row (~38s per call on this site's 260k Leads / 392k Contact Phone
rows) and both got the country code wrong: "last 10 digits" cannot tell +1 from
+91 and silently dropped shorter numbers such as "+977-23455884". An earlier
attempt to store the normalized value in indexed Custom Fields on `tabLead`
failed too - the table is already at MariaDB's 65535-byte row limit, so the
fields were never created and the failure was silent. The lookup table replaces
all of it; no trace of the last-10 logic remains.

IMPORTANT (confirmed live, 2026-09-26): stock Call Log's `before_insert` is
UNCONDITIONAL - it never checks whether `self.links` is already populated, so
pre-setting a link does not stop core running its own slow query. That is why
`override/call_log.py` subclasses the controller to skip those scans outright
for our own inserts. Core only ever APPENDS a link it finds; it never clears
one, so a link we set before `.insert()` always survives.
"""

import frappe


def _call_log_number(doc):
	"""The number Call Log's own before_insert would have matched against -
	`from` for Incoming, `to` for everything else (mirrors
	api/call_tracking.py's _pre_match_number)."""
	return doc.get("from") if doc.type == "Incoming" else doc.get("to")


def ensure_dynamic_link(doc, link_doctype, link_name):
	"""Idempotently make sure `doc` (a Call Log) has a Dynamic Link row for
	(link_doctype, link_name) - the missing piece that makes stock ERPNext's
	`get_linked_call_logs` (apps/erpnext/.../call_log.py, unmodified - it
	queries ONLY `tabDynamic Link`) find this Call Log from a Customer's or
	Lead's own Activity timeline. No-op if link_name is falsy, or a matching
	row already exists.

	Approach chosen after a live, rolled-back test of both candidates
	(2026-09-26):

	  - `doc.append("links", {...}); doc.save(ignore_permissions=True)` DOES
	    work (correct idx, no duplicate, dedup via Call Log's own
	    `validate()` -> `deduplicate_dynamic_links` as a second safety net)
	    - but every call bumps the parent Call Log's `modified` timestamp,
	      which this task's own verification bar explicitly treats as a
	      side effect that must NOT happen for a purely additive backfill.
	    Confirmed live before_insert cannot re-fire on this path either way
	    (grep of frappe/model/document.py: `before_insert` is referenced
	    exactly once, inside `insert()`, never in `save()`/`_save()`) - that
	    was never the actual reason to avoid it, `modified` was.

	  - A direct child-row insert, `frappe.get_doc({"doctype": "Dynamic
	    Link", "parenttype": "Call Log", "parent": doc.name, "parentfield":
	    "links", "link_doctype":..., "link_name":...}).insert(...)`, leaves
	    the parent Call Log's `modified`/`status`/every other field
	    untouched (confirmed live) - genuinely just an extra row in a child
	    table, nothing more. BUT: `idx` is NOT handled automatically -
	    leaving it at its default (0) is a real, reproducible footgun:
	    frappe/model/base_document.py's `append()` treats idx via
	    `if not getattr(d, "idx", False)`, and 0 is falsy, so the NEXT time
	    anyone loads/appends to this parent's `links` table, Frappe silently
	    renumbers the idx-0 row to collide with idx 1, producing two rows
	    both reporting idx=1 in memory - reproduced live against a real Call
	    Log/Dynamic Link row, then rolled back. Fixed by computing idx
	    ourselves (max existing idx for this parent, + 1) and never leaving
	    it at the default.

	Direct child-row insert (idx computed explicitly) is what's used below:
	it is the one of the two that is actually side-effect-free on the
	parent, which is exactly what a purely-additive backfill requires.

	Returns one of:
	  "created"        - the Dynamic Link row was actually inserted.
	  "already_linked" - a matching row already existed; no-op.
	  "missing_target" - link_name was falsy, OR the resolved party doctype/
	                      name does NOT actually exist (a dangling reference
	                      - the flat field points at a Lead/Customer that was
	                      since deleted or renamed elsewhere; a genuine,
	                      pre-existing data problem, out of this function's
	                      scope to fix). Found live 2026-09-26: two Call Log
	                      rows have custom_lead='CRM-LEAD-2026-00006', but no
	                      such Lead exists in `tabLead` any more. Frappe's own
	                      `_validate_links()` (run inside `.insert()`) would
	                      otherwise raise `LinkValidationError` and abort
	                      whatever batch called this - checked defensively
	                      here instead so one bad row never takes down an
	                      entire rematch run. Callers report this, they don't
	                      silently swallow it.
	"""
	if not link_name:
		return "missing_target"

	if not frappe.db.exists(link_doctype, link_name):
		return "missing_target"

	existing = frappe.get_all(
		"Dynamic Link",
		filters={"parenttype": "Call Log", "parent": doc.name, "parentfield": "links"},
		fields=["link_doctype", "link_name", "idx"],
	)
	if any(row.link_doctype == link_doctype and row.link_name == link_name for row in existing):
		return "already_linked"

	next_idx = max((row.idx or 0 for row in existing), default=0) + 1
	frappe.get_doc(
		{
			"doctype": "Dynamic Link",
			"parenttype": "Call Log",
			"parent": doc.name,
			"parentfield": "links",
			"link_doctype": link_doctype,
			"link_name": link_name,
			"idx": next_idx,
		}
	).insert(ignore_permissions=True)
	return "created"


def rematch_call_logs(batch_size=500, dry_run=False):
	"""Step 3/4/5 (2026-09-26, `bench rematch-call-logs`): re-run the fixed
	Customer > Lead > neither matching across EVERY Call Log record synced to
	date - not just currently-unlinked ones. Supersedes the earlier, narrower
	`rematch_unlinked_call_logs` (which only ever scanned unlinked rows and
	never verified already-linked ones).

	Two passes, deliberately kept separate:

	  1. VERIFY - every row that already has `customer` or `custom_lead` set.
	     Recomputes what `_resolve_linked_party` derives from that row's OWN
	     existing `links` child table (never a fresh phone-number lookup
	     against an already-linked row - today's bugs were both
	     under-matching, never over-matching, so there is no reason to
	     re-derive a link that already resolved) and compares it against the
	     stored customer/custom_lead/custom_linked_party. Any real difference
	     is a discrepancy between the CURRENT priority function and what an
	     earlier run left in place. If even one is found, the entire run
	     stops - nothing is written, not even the correction pass below - so
	     a human reviews it first. Never silently overwritten.

	  2. CORRECT - every row with NEITHER customer NOR custom_lead set gets a
	     fresh `find_party_by_phone` lookup (one indexed exact match) and, if
	     a real match exists, the link is
	     appended and the resolved party fields applied - same mechanics
	     `rematch_unlinked_call_logs` used, just as one pass of this larger,
	     full-scope command.

	`dry_run=True` wraps the whole run in a SAVEPOINT and always rolls back
	at the end regardless of outcome - the rehearsal Step 4 of the spec asks
	for. Idempotent either way: re-running against an already-correct site
	finds 0 discrepancies and 0 further corrections.

	Always LIMIT with no OFFSET in the correction pass: as a row is corrected
	it drops out of the WHERE clause, so the "next" batch is always the
	current first N still unlinked - this must not use limit_start, which
	would skip rows.
	"""
	from splinh.api.call_tracking import _resolve_linked_party
	from splinh.custom.phone_lookup import find_party_by_phone

	def _neither_count():
		return frappe.db.sql(
			"""SELECT COUNT(*) FROM `tabCall Log`
			WHERE (customer IS NULL OR customer = '') AND (custom_lead IS NULL OR custom_lead = '')"""
		)[0][0]

	result = {
		"total": frappe.db.count("Call Log"),
		"before_customer": frappe.db.count("Call Log", {"customer": ["is", "set"]}),
		"before_lead": frappe.db.count("Call Log", {"custom_lead": ["is", "set"]}),
		"before_neither": _neither_count(),
		"verified_consistent": 0,
		"discrepancies": [],
		"dynamic_links_backfilled": 0,
		"dynamic_links_missing_target": [],
		"scanned_unlinked": 0,
		"newly_linked_customer": 0,
		"newly_linked_lead": 0,
		"dynamic_links_added_pass2": 0,
		"after_customer": None,
		"after_lead": None,
		"still_unlinked": None,
		"sample_unlinked": [],
	}

	savepoint = "rematch_call_logs"
	if dry_run:
		frappe.db.savepoint(savepoint)

	try:
		# --- Pass 1: verify already-linked rows, never re-match them -------
		linked_names = [
			r[0]
			for r in frappe.db.sql(
				"""SELECT name FROM `tabCall Log`
				WHERE (customer IS NOT NULL AND customer != '')
				   OR (custom_lead IS NOT NULL AND custom_lead != '')
				ORDER BY name"""
			)
		]
		# (doc, expected) pairs for rows confirmed consistent below - the
		# Dynamic Link backfill (after the discrepancy gate) runs over this
		# list, never over a row with an unresolved discrepancy.
		consistent = []
		for name in linked_names:
			doc = frappe.get_doc("Call Log", name)
			expected = _resolve_linked_party(doc)
			actual = {
				"customer": doc.customer or None,
				"custom_lead": doc.custom_lead or None,
				"custom_linked_party": doc.custom_linked_party or "",
			}
			if expected != actual:
				result["discrepancies"].append({"name": name, "stored": actual, "recomputed": expected})
			else:
				result["verified_consistent"] += 1
				consistent.append((doc, expected))

		if result["discrepancies"]:
			# Stop here - do not touch the unlinked rows either. Reported,
			# never silently fixed.
			return result

		# --- Pass 1b: backfill the missing Dynamic Link row (2026-09-26) ---
		# Purely additive - runs only once the ENTIRE pass above is confirmed
		# discrepancy-free (same gate as Pass 2 below), and only ever ADDS a
		# Dynamic Link row for the party the flat fields already agree on. It
		# never touches customer/custom_lead/status/modified on any row. This
		# is what makes stock ERPNext's `get_linked_call_logs` (reads ONLY
		# `tabDynamic Link`, never these flat fields) actually surface these
		# calls on the Customer's/Lead's own Activity timeline.
		for doc, expected in consistent:
			if expected.get("customer"):
				outcome = ensure_dynamic_link(doc, "Customer", expected["customer"])
				party_doctype, party_name = "Customer", expected["customer"]
			elif expected.get("custom_lead"):
				outcome = ensure_dynamic_link(doc, "Lead", expected["custom_lead"])
				party_doctype, party_name = "Lead", expected["custom_lead"]
			else:
				continue
			if outcome == "created":
				result["dynamic_links_backfilled"] += 1
			elif outcome == "missing_target":
				result["dynamic_links_missing_target"].append(
					{"name": doc.name, "link_doctype": party_doctype, "link_name": party_name}
				)

		# --- Pass 2: correct genuinely unlinked rows ------------------------
		while True:
			rows = frappe.db.sql(
				"""
				SELECT name FROM `tabCall Log`
				WHERE (customer IS NULL OR customer = '') AND (custom_lead IS NULL OR custom_lead = '')
				ORDER BY name
				LIMIT %s
				""",
				(batch_size,),
			)
			names = [r[0] for r in rows]
			if not names:
				break

			batch_progressed = False
			for name in names:
				doc = frappe.get_doc("Call Log", name)
				number = _call_log_number(doc)
				match = find_party_by_phone(number)
				result["scanned_unlinked"] += 1
				if not match:
					continue

				already_linked = any(
					link.link_doctype == match["doctype"] and link.link_name == match["name"]
					for link in doc.links
				)
				if not already_linked:
					doc.append("links", {"link_doctype": match["doctype"], "link_name": match["name"]})
					doc.save(ignore_permissions=True)

				party = _resolve_linked_party(doc)
				if any(doc.get(fieldname) != value for fieldname, value in party.items()):
					for fieldname, value in party.items():
						doc.db_set(fieldname, value, update_modified=False)
					batch_progressed = True
					if party.get("customer"):
						result["newly_linked_customer"] += 1
						outcome = ensure_dynamic_link(doc, "Customer", party["customer"])
						party_doctype, party_name = "Customer", party["customer"]
					elif party.get("custom_lead"):
						result["newly_linked_lead"] += 1
						outcome = ensure_dynamic_link(doc, "Lead", party["custom_lead"])
						party_doctype, party_name = "Lead", party["custom_lead"]
					else:
						outcome = None
					if outcome == "created":
						result["dynamic_links_added_pass2"] += 1
					elif outcome == "missing_target":
						result["dynamic_links_missing_target"].append(
							{"name": doc.name, "link_doctype": party_doctype, "link_name": party_name}
						)

			if not dry_run:
				frappe.db.commit()

			if not batch_progressed:
				# Nothing in this batch was fixable (no match found for any of
				# them) - without this, an unmatchable row would loop forever
				# since it never leaves the WHERE clause. Stop; whatever
				# remains genuinely has no match under the normalized field.
				break

		result["still_unlinked"] = _neither_count()
		result["after_customer"] = frappe.db.count("Call Log", {"customer": ["is", "set"]})
		result["after_lead"] = frappe.db.count("Call Log", {"custom_lead": ["is", "set"]})

		if result["still_unlinked"]:
			result["sample_unlinked"] = [
				{"name": r[0], "type": r[1], "number": (r[2] if r[1] == "Incoming" else r[3])}
				for r in frappe.db.sql(
					"""SELECT name, type, `from`, `to` FROM `tabCall Log`
					WHERE (customer IS NULL OR customer = '') AND (custom_lead IS NULL OR custom_lead = '')
					ORDER BY name LIMIT 5"""
				)
			]

		return result
	finally:
		if dry_run:
			frappe.db.rollback(save_point=savepoint)
