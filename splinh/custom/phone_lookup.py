"""Indexed phone lookup: Call Log number -> Lead/Contact in one point query.

Why this exists (2026-10-03): matching an incoming call against ~260k Leads and
~392k Contact Phone rows by scanning and cleaning every stored number took
~38s per call (plus ~13s in stock ERPNext's own LIKE '%n' match), on a single
background worker - so synced calls queued up for hours. The stored numbers
carry separators ("+91-9225144953") while the phone sends "+919225144953", so
no index on the raw columns can answer the question directly.

Fix: a small, derived table `Phone Lookup` (doctype in this app) holding ONE
row per (normalised number, Lead/Contact record), with the number normalised
once - at save time or in a batch - and indexed. A call then needs one exact,
indexed `WHERE phone_key = %s` lookup (sub-millisecond), never a scan.

The table is derived data: Lead/Contact stay the source of truth, nothing here
ever edits them, and `reconcile()` can rebuild or repair the whole table from
them at any time (also what runs after a restore from an older backup).

Keeping it in sync: doc_events (hooks.py) call sync_document / delete_document on
every Lead/Contact save and delete. Writers that skip document hooks (db_set,
raw SQL) are repaired by re-running `bench rebuild-phone-lookup`, which is safe
to run at any time.

Row identity: name = md5(phone_key|source_doctype|source_name). A record that
holds the same number in several fields (Lead.phone == Lead.mobile_no is true
for ~88% of Leads) gets ONE row, remembering the lowest-ranked field - so
upserts are idempotent through the primary key alone, no composite unique index.

Lookup order when several records share a number (3,054 numbers sit on more
than one Contact, 1,284 on more than one Lead): Contact before Lead, then
Lead field order phone < mobile_no < whatsapp_no, then newest record - the same
priority the previous matching used.
"""

import hashlib
import time

import frappe
from frappe.utils import now_datetime

from splinh.custom.phone_normalize import (
	DEFAULT_COUNTRY_CODE,
	DEFAULT_NATIONAL_LENGTH,
	normalize_phone_key,
)

TABLE = "`tabPhone Lookup`"
BATCH_SIZE = 2000

# (source doctype) -> rank. Lower wins when several records share a number.
SOURCE_RANK = {"Contact": 1, "Lead": 2}
# Lead has three raw phone-ish fields (Contact keeps its numbers in a child
# table) - field order here is the tie-break order, as before.
LEAD_PHONE_FIELDS = (("phone", 1), ("mobile_no", 2), ("whatsapp_no", 3))

CURSOR_KEY = "splinh_phone_lookup_cursor_{}"
FIND_CANDIDATES = 5


# --------------------------------------------------------------------- config


def _normalize(raw):
	return normalize_phone_key(
		raw,
		default_country_code=str(frappe.conf.get("splinh_default_country_code") or DEFAULT_COUNTRY_CODE),
		default_national_length=int(
			frappe.conf.get("splinh_default_national_length") or DEFAULT_NATIONAL_LENGTH
		),
	)


# --------------------------------------------------------------------- lookup


def find_party_by_phone(number):
	"""Resolve a call's phone number to {"doctype": "Contact"|"Lead", "name": ...}
	via one indexed lookup, or None. Same return shape as the older
	the previous matcher, so every caller (and _resolve_linked_party, which turns
	a Contact into its Customer/Lead) is unchanged.

	Up to FIND_CANDIDATES rows are read in priority order and the first whose
	source record still exists wins - a guard against a stale row (e.g. a record
	removed by raw SQL since the last reconcile).
	"""
	key = _normalize(number)
	if not key:
		return None

	rows = frappe.db.sql(
		f"""
		SELECT source_doctype, source_name FROM {TABLE}
		WHERE phone_key = %s
		ORDER BY source_rank, field_rank, source_creation DESC
		LIMIT {FIND_CANDIDATES}
		""",
		(key,),
	)
	for source_doctype, source_name in rows:
		if frappe.db.exists(source_doctype, source_name):
			return {"doctype": source_doctype, "name": source_name}
	return None


# ------------------------------------------------------------- entry building


def _row_name(phone_key, source_doctype, source_name):
	return hashlib.md5(f"{phone_key}|{source_doctype}|{source_name}".encode()).hexdigest()


def _entries_for_record(source_doctype, source_name, source_creation, numbers, stats=None):
	"""Index rows for one record. `numbers` is a list of (raw_number, source_field,
	field_rank). Same number in several fields -> one row, lowest field_rank."""
	best = {}
	for raw, source_field, field_rank in numbers:
		if raw is None or not str(raw).strip():
			continue
		key = _normalize(raw)
		if not key:
			if stats is not None:
				stats["unusable"] += 1
				if len(stats["unusable_sample"]) < 15:
					stats["unusable_sample"].append(f"{source_doctype} {source_name}: {raw!r}")
			continue
		if stats is not None and not str(raw).strip().startswith("+"):
			stats["bare"] += 1
		if key not in best or field_rank < best[key][1]:
			best[key] = (source_field, field_rank)

	return {
		_row_name(key, source_doctype, source_name): (
			key,
			source_doctype,
			source_name,
			source_field,
			SOURCE_RANK[source_doctype],
			field_rank,
			source_creation,
		)
		for key, (source_field, field_rank) in best.items()
	}


def _numbers_for_doc(doc):
	if doc.doctype == "Contact":
		return [(row.phone, "phone_nos", 1) for row in doc.get("phone_nos") or []]
	return [(doc.get(field), field, rank) for field, rank in LEAD_PHONE_FIELDS]


# ------------------------------------------------------------------ DB writes


def _upsert(entries):
	"""Bulk upsert {row_name: tuple} in chunks. Idempotent via the primary key."""
	items = list(entries.items())
	now = now_datetime()
	for start in range(0, len(items), 500):
		chunk = items[start : start + 500]
		placeholders = ", ".join(["(%s, %s, %s, 'Administrator', 'Administrator', 0, 0, %s, %s, %s, %s, %s, %s, %s)"] * len(chunk))
		values = []
		for name, (key, doctype, source_name, field, source_rank, field_rank, created) in chunk:
			values += [name, now, now, key, doctype, source_name, field, source_rank, field_rank, created]
		frappe.db.sql(
			f"""
			INSERT INTO {TABLE}
				(name, creation, modified, modified_by, owner, docstatus, idx,
				 phone_key, source_doctype, source_name, source_field, source_rank, field_rank, source_creation)
			VALUES {placeholders}
			ON DUPLICATE KEY UPDATE
				source_field = VALUES(source_field),
				source_rank = VALUES(source_rank),
				field_rank = VALUES(field_rank),
				source_creation = VALUES(source_creation),
				modified = VALUES(modified)
			""",
			values,
		)


def _delete_names(names):
	names = list(names)
	for start in range(0, len(names), 1000):
		chunk = names[start : start + 1000]
		frappe.db.sql(f"DELETE FROM {TABLE} WHERE name IN ({', '.join(['%s'] * len(chunk))})", chunk)


def _replace_record(source_doctype, source_name, expected):
	"""Make the stored rows of one record equal `expected` ({row_name: tuple})."""
	existing = {
		r[0]
		for r in frappe.db.sql(
			f"SELECT name FROM {TABLE} WHERE source_doctype = %s AND source_name = %s",
			(source_doctype, source_name),
		)
	}
	_delete_names(existing - set(expected))
	if expected:
		_upsert(expected)


# ---------------------------------------------------------------- doc hooks


def sync_document(doc, method=None):
	"""Contact / Lead `on_update`: refresh this record's rows. Never blocks the
	save - a failure is logged and the scheduled sync repairs it. Deadlocks and
	lock-wait timeouts are re-raised: MariaDB has already rolled the transaction
	back, so continuing would silently commit a half-saved document."""
	if doc.doctype not in SOURCE_RANK:
		return
	try:
		_replace_record(
			doc.doctype,
			doc.name,
			_entries_for_record(doc.doctype, doc.name, doc.creation, _numbers_for_doc(doc)),
		)
	except (frappe.QueryDeadlockError, frappe.QueryTimeoutError):
		raise
	except Exception:
		frappe.log_error(title="splinh phone_lookup: sync_document failed", message=frappe.get_traceback())


def delete_document(doc, method=None):
	"""Contact / Lead `on_trash`."""
	if doc.doctype not in SOURCE_RANK:
		return
	try:
		frappe.db.sql(
			f"DELETE FROM {TABLE} WHERE source_doctype = %s AND source_name = %s", (doc.doctype, doc.name)
		)
	except (frappe.QueryDeadlockError, frappe.QueryTimeoutError):
		raise
	except Exception:
		frappe.log_error(title="splinh phone_lookup: delete_document failed", message=frappe.get_traceback())


# --------------------------------------------------------- batch reconcile


def _new_stats():
	return {
		"records_scanned": 0,
		"rows_expected": 0,
		"rows_missing": 0,
		"rows_stale": 0,
		"rows_orphaned": 0,
		"unusable": 0,
		"unusable_sample": [],
		"bare": 0,
		"fixed": False,
	}


def _expected_for_records(doctype, records, stats):
	"""{source_name: {row_name: tuple}} for `records` = [(name, creation), ...],
	reading the current numbers from Lead / Contact Phone."""
	names = [r[0] for r in records]
	created = dict(records)
	numbers = {name: [] for name in names}
	marks = ", ".join(["%s"] * len(names))

	if doctype == "Contact":
		for parent, phone in frappe.db.sql(
			f"SELECT parent, phone FROM `tabContact Phone` WHERE parent IN ({marks}) ORDER BY idx", names
		):
			numbers[parent].append((phone, "phone_nos", 1))
	else:
		for row in frappe.db.sql(
			f"SELECT name, phone, mobile_no, whatsapp_no FROM `tabLead` WHERE name IN ({marks})", names
		):
			numbers[row[0]] = [(row[i + 1], field, rank) for i, (field, rank) in enumerate(LEAD_PHONE_FIELDS)]

	return {
		name: _entries_for_record(doctype, name, created[name], numbers[name], stats) for name in names
	}


def _reconcile_batch(doctype, records, fix, stats):
	expected = _expected_for_records(doctype, records, stats)
	names = list(expected)
	marks = ", ".join(["%s"] * len(names))

	existing = {}
	for row_name, source_name in frappe.db.sql(
		f"SELECT name, source_name FROM {TABLE} WHERE source_doctype = %s AND source_name IN ({marks})",
		[doctype, *names],
	):
		existing.setdefault(source_name, set()).add(row_name)

	to_upsert, to_delete = {}, set()
	for source_name, wanted in expected.items():
		have = existing.get(source_name, set())
		stats["rows_expected"] += len(wanted)
		missing = set(wanted) - have
		stale = have - set(wanted)
		stats["rows_missing"] += len(missing)
		stats["rows_stale"] += len(stale)
		to_delete |= stale
		# Upsert every wanted row (not only the missing ones): also refreshes a
		# changed field/rank/creation on a row whose key already exists.
		to_upsert.update(wanted)

	stats["records_scanned"] += len(names)
	if fix:
		_delete_names(to_delete)
		if to_upsert:
			_upsert(to_upsert)
		frappe.db.commit()


def _delete_orphans(fix, stats):
	"""Rows whose Lead/Contact no longer exists (deleted by raw SQL, bypassing on_trash)."""
	for doctype in SOURCE_RANK:
		orphans = [
			r[0]
			for r in frappe.db.sql(
				f"""
				SELECT pl.name FROM {TABLE} pl
				LEFT JOIN `tab{doctype}` src ON src.name = pl.source_name
				WHERE pl.source_doctype = %s AND src.name IS NULL
				""",
				(doctype,),
			)
		]
		stats["rows_orphaned"] += len(orphans)
		if fix and orphans:
			_delete_names(orphans)
			frappe.db.commit()


def reconcile(fix=True, batch_size=BATCH_SIZE, resume=False, truncate=False, log=None):
	"""Compare every Contact and Lead against Phone Lookup and (if `fix`) repair
	the differences: add missing rows, drop stale ones, refresh changed ones, drop
	orphans. This is also the initial population - run on an empty table it simply
	inserts everything - so there is exactly one code path to trust.

	Streams the sources in primary-key order, `batch_size` records at a time
	(keyset pagination, never OFFSET), and commits per batch, so memory stays flat
	and an interrupted run restarts with resume=True from the saved cursor. Safe to
	re-run at any time. `fix=False` writes nothing and returns the same report.
	`truncate=True` (with fix) empties the table first - only for a from-scratch
	rebuild while the lookup is not yet switched on.
	"""
	log = log or (lambda msg: None)
	stats = _new_stats()
	stats["fixed"] = bool(fix)
	started = time.time()

	if fix and truncate and not resume:
		frappe.db.sql(f"TRUNCATE TABLE {TABLE}")
		frappe.db.commit()
		log("Phone Lookup truncated")

	for doctype in SOURCE_RANK:
		cursor_key = CURSOR_KEY.format(doctype)
		last = (frappe.db.get_default(cursor_key) or "") if resume else ""
		while True:
			records = frappe.db.sql(
				f"SELECT name, creation FROM `tab{doctype}` WHERE name > %s ORDER BY name LIMIT %s",
				(last, int(batch_size)),
			)
			if not records:
				break
			_reconcile_batch(doctype, records, fix, stats)
			last = records[-1][0]
			if fix:
				frappe.db.set_default(cursor_key, last)
				frappe.db.commit()
			log(
				f"{doctype}: scanned {stats['records_scanned']:,} records so far "
				f"({time.time() - started:.0f}s), missing {stats['rows_missing']:,}, stale {stats['rows_stale']:,}"
			)
		if fix:
			frappe.db.set_default(cursor_key, "")
			frappe.db.commit()

	_delete_orphans(fix, stats)
	stats["table_rows"] = frappe.db.sql(f"SELECT COUNT(*) FROM {TABLE}")[0][0]
	stats["seconds"] = round(time.time() - started, 1)
	return stats
