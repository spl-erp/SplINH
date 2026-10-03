"""Normalized, indexed phone matching for Call Log's Lead/Customer auto-link.

Background (2026-09-26): stock ERPNext's `Call Log.before_insert()`
(apps/erpnext/erpnext/telephony/doctype/call_log/call_log.py - core, never
edited) matches the caller's number against Lead/Contact with an unindexable
`LIKE '%number'` against the RAW stored phone value - dashes and all. Two real
bugs came out of that:

  1. Performance: `LIKE '%number'` cannot use any index (confirmed via EXPLAIN:
     type=ALL over the full Lead table, ~30s for a non-matching number on this
     bench's ~35k Leads). See apps/splinh/splinh/api/call_tracking.py's
     module docstring for the full story and why the actual insert was already
     moved to a background job over this.
  2. Correctness: because the stored value is compared WITHOUT stripping
     non-digit characters, a stored number like "+91-9225144953" matches an
     incoming bare 10-digit "9225144953" (the dash sits outside the 10-char
     comparison window) but NOT the same real number written with a country
     code and no separator, "919225144953" (the dash now sits exactly where
     the 12-char window's 2nd character should be, so the suffix comparison
     fails). Confirmed live: Contact Phone "DUMMY-DUMMY" stores
     "+91-9225144953"; Call Log rows with to="9225144953" matched Customer
     DUMMY, the three rows with to="919225144953" did not.

Fix built here, in OUR app, not core: a normalized "last 10 digits, non-digit
characters stripped" exact match, computed at QUERY TIME against the raw
phone-ish fields that already exist (Lead.phone/mobile_no/whatsapp_no,
Contact Phone.phone) via `_normalize_sql_expr()` below - no stored or virtual
column of any kind (2026-10: an earlier version of this fix stored the
normalized value in dedicated indexed Custom Fields; abandoned because
`tabLead` is already too wide for another fixed-width column - adding one
more hit MariaDB's 65535-byte max row size and silently failed on every
`bench migrate`, which is what let this bug reach production undetected).
See `find_party_by_last10()` below, and `api/call_tracking.py`'s
push_calls/ingest_whatsapp_call for where the pre-insert lookup happens.

IMPORTANT (confirmed live, 2026-09-26): Call Log's own before_insert is
UNCONDITIONAL - it does not check whether self.customer or self.links are
already populated before running its own slow/buggy match. Pre-setting
doc.links before doc.insert() therefore does NOT skip or speed up core's own
query - core's query still runs, at the same cost, every time. What
pre-setting DOES do is guarantee the CORRECT result is what actually survives:
core's before_insert only ever appends a link when it finds a match of its
own; it never clears or overwrites an existing links row. So our normalized
match, computed and applied via db_set AFTER insert() completes (in
_resolve_linked_party, which already reads doc.links post-insert), is the
right and sufficient place to apply this fix. The performance problem (core's
own query still costing ~30s per insert) is unchanged by this fix and remains
the separate, already-deferred-to-a-background-job, not-yet-scoped follow-up
documented in call_tracking.py.
"""

import re

import frappe

_DIGITS_RE = re.compile(r"\D+")


def last10(number):
	"""Strip every non-digit character, then return the last 10 digits.

	Returns None for anything with fewer than 10 digits left after stripping -
	deliberately conservative: an ambiguous short number (extension, landline
	fragment, etc.) should never produce a false-positive exact match. This
	mirrors the same design used for the incoming number in
	api/call_tracking.py's pre-insert lookup.
	"""
	if not number:
		return None
	digits = _DIGITS_RE.sub("", number)
	if len(digits) < 10:
		return None
	return digits[-10:]


# Lead has three separate raw phone-ish fields (unlike Contact, which has a
# child table with one row per number) - or_filters across all three in stock
# get_lead_with_phone_number. Priority order matches the old stored-column
# design: phone, then mobile_no, then whatsapp_no.
LEAD_PHONE_FIELDNAMES = ("phone", "mobile_no", "whatsapp_no")


def _normalize_sql_expr(column):
	"""SQL expression computing the same value as `last10()` above, directly
	against a raw column - no stored/virtual column involved. Used by
	find_party_by_last10() below as a query-time WHERE condition."""
	stripped = f"REGEXP_REPLACE({column}, '[^0-9]', '')"
	return f"IF(CHAR_LENGTH({stripped}) >= 10, RIGHT({stripped}, 10), NULL)"


def find_party_by_last10(number):
	"""Exact-match lookup against the SAME raw phone columns Lead/Contact
	Phone already have - normalized at query time via `_normalize_sql_expr`,
	never read from a separately stored column (2026-10: superseded the
	stored-Custom-Field design - see the module docstring above for why).

	Returns a dict {"doctype": "Lead"|"Contact", "name": ...} for the first
	match found (Contact checked first, same priority stock's before_insert
	effectively ends up with since Contact -> Customer is the most-resolved
	party _resolve_linked_party can report), or None if nothing matches.

	`key` is always passed as a bind parameter, never string-interpolated -
	only the fixed, hardcoded column name is interpolated into the SQL text
	via `_normalize_sql_expr`.

	Performance note: this can no longer use a B-tree index - each call is a
	full scan of the relevant table with REGEXP_REPLACE computed per row,
	instead of an indexed exact match. Acceptable here because every caller
	runs off the HTTP hot path: the live insert path already runs in a
	background job (frappe.enqueue(..., queue="long", timeout=300) - see
	api/call_tracking.py's own docstring, which already accounts for a
	15-33s/call cost from stock's own before_insert match), and the other
	caller (rematch_call_logs) runs from an explicit bench CLI command with
	no timeout.
	"""
	key = last10(number)
	if not key:
		return None

	contact = frappe.db.sql(
		f"""
		SELECT parent FROM `tabContact Phone`
		WHERE {_normalize_sql_expr("phone")} = %(key)s
		ORDER BY creation DESC
		LIMIT 1
		""",
		{"key": key},
	)
	if contact:
		return {"doctype": "Contact", "name": contact[0][0]}

	for fieldname in LEAD_PHONE_FIELDNAMES:
		lead = frappe.db.sql(
			f"""
			SELECT name FROM `tabLead`
			WHERE {_normalize_sql_expr(fieldname)} = %(key)s
			ORDER BY creation DESC
			LIMIT 1
			""",
			{"key": key},
		)
		if lead:
			return {"doctype": "Lead", "name": lead[0][0]}

	return None


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
	     fresh `find_party_by_last10` lookup (indexed exact match, never the
	     old unindexed LIKE) and, if a real match exists, the link is
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
				match = find_party_by_last10(number)
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
