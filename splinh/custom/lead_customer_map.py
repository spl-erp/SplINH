"""Map existing Leads to the Customers they became (from the business's ID / Org_Code file).

The file has two columns: `ID` (the Lead's name - the legacy CRM id; 294k of 298k Leads
have a numeric name) and `Org_Code` (the Customer Code = `Customer.name`). The link ERPNext
itself uses is `Customer.lead_name` ("From Lead"); a Lead's status is `Converted` exactly
when a Customer points at it (`Lead.set_status` -> `has_customer`), so setting only the
status would be undone on the next Lead save.

Three steps, each its own bench command (commands.py):
  report  - read the file, classify every row, write a CSV. Never writes to the database.
  apply   - from that CSV, for the approved classes only, set Customer.lead_name and the
            Lead status with the same raw write stock `Customer.update_lead_status` uses (no
            full save, so the duplicate / GST / assignment hooks do not fire). The records'
            modified time is updated so the change is visible and traceable. Idempotent;
            a log of every change (with the previous Lead status) is written for revert, and
            everything not applied (skips, failures, review rows) goes to a separate
            `*_pending.csv` sheet to resolve later.
  revert  - from that log, put Customer.lead_name and the Lead status back.

Classes (report):
  CONFIRMED          Lead and Customer exist and share a phone number
  ID_ONLY_NAME_OK    no phone to compare (a side has none) but the names agree
  REVIEW             phones present but different, or no phone and the names differ, or a
                     duplicated code that cannot be decided - for a person to look at
  PHONE_ONLY         the ID gave no usable Lead, but the Customer's phone points to exactly
                     one Lead that points back only to this Customer
  ALREADY_DONE       Customer already points at this Lead and the Lead is Converted
  SKIP               not mappable (reason given): customer / lead not found, already linked
                     elsewhere, the other row of a duplicated code
  EXTRA_PHONE_LEAD   information only: another Lead with the Customer's phone (a Customer
                     can point at one Lead)
Only CONFIRMED and ID_ONLY_NAME_OK are applied by default.
"""

import csv
import re
from collections import defaultdict

import frappe

APPLY_BY_DEFAULT = ("CONFIRMED", "ID_ONLY_NAME_OK")
CHUNK = 1500
REPORT_FIELDS = [
	"row",
	"file_id",
	"file_code",
	"customer",
	"lead",
	"class",
	"reason",
	"lead_status",
	"customer_disabled",
	"lead_name",
	"customer_name",
	"name_match",
	"phone_overlap",
]
PENDING_FIELDS = REPORT_FIELDS + ["stage"]
LOG_FIELDS = ["customer", "lead", "previous_lead_status", "previous_customer_lead_name", "applied_at"]


# --------------------------------------------------------------------- pure helpers


def normalise_code(raw):
	"""' c010207 ' / 'C 010937' -> 'C010207' / 'C010937': strip all whitespace, upper-case."""
	return re.sub(r"\s+", "", str(raw if raw is not None else "")).upper()


def normalise_id(raw):
	"""12345 / 12345.0 / ' 12345 ' -> '12345' (the Lead's name); '' if not numeric."""
	try:
		return str(int(float(str(raw).strip())))
	except (TypeError, ValueError):
		return ""


def _squash(text):
	return re.sub(r"[^a-z0-9]", "", (text or "").lower())


def name_match(lead_names, customer_names):
	"""'exact' (one name equal after removing punctuation/case), 'partial' (one contains the
	other, both >= 5 characters) or 'different'."""
	a = {x for x in (_squash(n) for n in lead_names if n) if x}
	b = {x for x in (_squash(n) for n in customer_names if n) if x}
	if a & b:
		return "exact"
	if any(x in y or y in x for x in a for y in b if len(x) >= 5 and len(y) >= 5):
		return "partial"
	return "different"


def classify_pair(lead, customer, lead_phones, customer_phones):
	"""-> (class, reason, name_match, phone_overlap) for one existing Lead + existing Customer."""
	nm = name_match((lead["lead_name"], lead["company_name"], lead["title"]), (customer["customer_name"], customer["custom_customer_name_city"]))
	if lead_phones and customer_phones:
		if lead_phones & customer_phones:
			return "CONFIRMED", "phone matches", nm, "yes"
		return "REVIEW", "both have phones but none match", nm, "no"
	if nm in ("exact", "partial"):
		return "ID_ONLY_NAME_OK", "no phone to compare; names agree", nm, "n/a"
	return "REVIEW", "no phone to compare and names differ", nm, "n/a"


_RANK = {"CONFIRMED": 0, "ID_ONLY_NAME_OK": 1}


def decide_duplicates(rows):
	"""Several file rows (different Leads) for one Customer. Keep the best-evidence row (CONFIRMED
	beats ID_ONLY_NAME_OK); anything else, or a tie, goes to REVIEW. Mutates the row dicts."""
	good = [r for r in rows if r["class"] in _RANK]
	best = min((_RANK[r["class"]] for r in good), default=None)
	winners = [r for r in good if _RANK[r["class"]] == best]
	for r in rows:
		if len(winners) == 1 and r is winners[0]:
			continue
		if len(winners) == 1:
			r["class"], r["reason"] = "SKIP", f"duplicate code: Lead {winners[0]['lead']} kept"
		else:
			r["class"], r["reason"] = "REVIEW", "duplicate code: cannot choose between the Leads"


# --------------------------------------------------------------------- data access


def _chunks(items, size=CHUNK):
	items = list(items)
	for i in range(0, len(items), size):
		yield tuple(items[i : i + size])


def read_file(path):
	import openpyxl

	workbook = openpyxl.load_workbook(path, read_only=True, data_only=True)
	rows = []
	for number, (raw_id, raw_code) in enumerate(workbook.worksheets[0].iter_rows(min_row=2, max_col=2, values_only=True), start=2):
		if raw_id in (None, "") and raw_code in (None, ""):
			continue
		rows.append({"row": number, "file_id": normalise_id(raw_id), "file_code": str(raw_code or "").strip(), "code": normalise_code(raw_code)})
	return rows


def _load_customers(codes):
	out = {}
	for chunk in _chunks(codes):
		for r in frappe.db.sql(
			"""SELECT name, customer_name, custom_customer_name_city, disabled, lead_name
			FROM `tabCustomer` WHERE name IN %s""",
			(chunk,),
			as_dict=True,
		):
			out[r.name] = r
	return out


def _load_leads(ids):
	out = {}
	for chunk in _chunks(ids):
		for r in frappe.db.sql(
			"SELECT name, lead_name, company_name, title, status FROM `tabLead` WHERE name IN %s", (chunk,), as_dict=True
		):
			out[r.name] = r
	return out


def _customer_phones(codes):
	"""Phone keys of the Contacts linked to each Customer (indexed Phone Lookup table)."""
	out = defaultdict(set)
	for chunk in _chunks(codes):
		for customer, key in frappe.db.sql(
			"""SELECT dl.link_name, pl.phone_key FROM `tabDynamic Link` dl
			JOIN `tabPhone Lookup` pl ON pl.source_doctype = 'Contact' AND pl.source_name = dl.parent
			WHERE dl.parenttype = 'Contact' AND dl.link_doctype = 'Customer' AND dl.link_name IN %s""",
			(chunk,),
		):
			out[customer].add(key)
	return out


def _lead_phones(ids):
	out = defaultdict(set)
	for chunk in _chunks(ids):
		for lead, key in frappe.db.sql(
			"SELECT source_name, phone_key FROM `tabPhone Lookup` WHERE source_doctype = 'Lead' AND source_name IN %s", (chunk,)
		):
			out[lead].add(key)
	return out


def _leads_for_keys(keys):
	out = defaultdict(set)
	for chunk in _chunks(keys, 3000):
		for key, lead in frappe.db.sql(
			"SELECT phone_key, source_name FROM `tabPhone Lookup` WHERE source_doctype = 'Lead' AND phone_key IN %s", (chunk,)
		):
			out[key].add(lead)
	return out


def _lead_owner_map(lead_names):
	"""lead -> [customers that already point at it via lead_name]"""
	out = defaultdict(list)
	for chunk in _chunks(lead_names):
		for customer, lead in frappe.db.sql("SELECT name, lead_name FROM `tabCustomer` WHERE lead_name IN %s", (chunk,)):
			out[lead].append(customer)
	return out


# --------------------------------------------------------------------- the report


def build_report(path, log=print):
	"""Classify every file row. Reads only. Returns (report_rows, summary_counter)."""
	file_rows = read_file(path)
	codes = sorted({r["code"] for r in file_rows if r["code"]})
	ids = sorted({r["file_id"] for r in file_rows if r["file_id"]})
	log(f"file rows: {len(file_rows)} | distinct codes: {len(codes)} | distinct ids: {len(ids)}")

	customers = _load_customers(codes)
	leads = _load_leads(ids)
	cphones = _customer_phones(customers)
	lphones = _lead_phones(leads)
	owners = _lead_owner_map(leads)

	report = []
	for r in file_rows:
		row = {f: "" for f in REPORT_FIELDS}
		row.update(row=r["row"], file_id=r["file_id"], file_code=r["file_code"], customer=r["code"], lead=r["file_id"])
		customer, lead = customers.get(r["code"]), leads.get(r["file_id"])
		if not r["file_id"]:
			row.update({"class": "SKIP", "reason": "ID is not a number"})
		elif customer is None:
			row.update({"class": "SKIP", "reason": "code is not a Customer"})
		elif lead is None:
			row.update({"class": "SKIP", "reason": "ID is not a Lead"})
		else:
			row.update(
				lead_status=lead["status"],
				customer_disabled=customer["disabled"],
				lead_name=lead["lead_name"] or lead["company_name"],
				customer_name=customer["customer_name"],
			)
			if customer["lead_name"] == lead["name"] and lead["status"] == "Converted":
				row.update({"class": "ALREADY_DONE", "reason": "already linked and Converted"})
			elif customer["lead_name"] and customer["lead_name"] != lead["name"]:
				row.update({"class": "SKIP", "reason": f"customer already linked to Lead {customer['lead_name']}"})
			elif [c for c in owners.get(lead["name"], []) if c != customer["name"]]:
				row.update({"class": "SKIP", "reason": f"Lead already linked to Customer {owners[lead['name']][0]}"})
			else:
				cls, reason, nm, overlap = classify_pair(lead, customer, lphones.get(lead["name"], set()), cphones.get(customer["name"], set()))
				row.update({"class": cls, "reason": reason, "name_match": nm, "phone_overlap": overlap})
		report.append(row)

	by_customer = defaultdict(list)
	for row in report:
		if row["class"] in _RANK:
			by_customer[row["customer"]].append(row)
	for rows in by_customer.values():
		if len(rows) > 1:
			decide_duplicates(rows)

	report.extend(_phone_only_rows(report, customers, cphones, owners))
	counts = defaultdict(int)
	for row in report:
		counts[row["class"]] += 1
	return report, counts


def _phone_only_rows(report, customers, cphones, owners):
	"""Customers in the file with no usable ID Lead, whose phone points at exactly one Lead."""
	handled = {row["customer"] for row in report if row["class"] in (*_RANK, "ALREADY_DONE", "REVIEW")}
	file_customers = [c for c in customers if cphones.get(c)]
	keys = {k for c in file_customers for k in cphones[c]}
	key_leads = _leads_for_keys(keys)
	customer_leads = {c: set().union(*[key_leads.get(k, set()) for k in cphones[c]]) for c in file_customers}
	lead_customers = defaultdict(set)
	for c, ls in customer_leads.items():
		for lead in ls:
			lead_customers[lead].add(c)

	mapped_lead = {row["customer"]: row["lead"] for row in report if row["class"] in (*_RANK, "ALREADY_DONE")}
	all_leads = sorted({lead for ls in customer_leads.values() for lead in ls})
	leads = _load_leads(all_leads)
	extra = []
	for c in file_customers:
		found = customer_leads[c]
		if c in mapped_lead:
			for lead in sorted(found - {mapped_lead[c]}):
				extra.append(_info_row(c, lead, leads, customers, "EXTRA_PHONE_LEAD", "another Lead has this Customer's phone"))
			continue
		if c in handled or customers[c]["lead_name"] or not found:
			continue
		if len(found) == 1:
			(lead,) = found
			if lead_customers[lead] == {c} and not owners.get(lead) and lead in leads:
				extra.append(_info_row(c, lead, leads, customers, "PHONE_ONLY", "unique phone match, no ID Lead", "yes"))
				continue
		for lead in sorted(found):
			extra.append(_info_row(c, lead, leads, customers, "REVIEW", "phone matches several Leads/Customers; no ID Lead", "yes"))
	return extra


def _info_row(customer, lead, leads, customers, cls, reason, overlap="yes"):
	row = {f: "" for f in REPORT_FIELDS}
	lead_doc = leads.get(lead) or {}
	row.update(
		customer=customer,
		lead=lead,
		**{"class": cls},
		reason=reason,
		lead_status=lead_doc.get("status", ""),
		customer_disabled=customers[customer]["disabled"],
		lead_name=lead_doc.get("lead_name") or lead_doc.get("company_name", ""),
		customer_name=customers[customer]["customer_name"],
		phone_overlap=overlap,
	)
	return row


def pending_path_for(report_path):
	return report_path[:-4] + "_pending.csv" if report_path.endswith(".csv") else report_path + "_pending.csv"


def write_report(path, report):
	"""The full report, plus the separate sheet of everything that will NOT be applied by
	default (to resolve later)."""
	with open(path, "w", newline="", encoding="utf-8") as handle:
		writer = csv.DictWriter(handle, fieldnames=REPORT_FIELDS)
		writer.writeheader()
		writer.writerows(report)
	pending = [dict(r, stage="report") for r in report if r["class"] not in (*APPLY_BY_DEFAULT, "ALREADY_DONE")]
	with open(pending_path_for(path), "w", newline="", encoding="utf-8") as handle:
		writer = csv.DictWriter(handle, fieldnames=PENDING_FIELDS)
		writer.writeheader()
		writer.writerows(pending)
	return len(pending)


# --------------------------------------------------------------------- apply / revert


def read_report(path):
	with open(path, newline="", encoding="utf-8") as handle:
		return list(csv.DictReader(handle))


def _append_pending(path, rows):
	if not rows:
		return
	import os

	new = not os.path.exists(path)
	with open(path, "a", newline="", encoding="utf-8") as handle:
		writer = csv.DictWriter(handle, fieldnames=PENDING_FIELDS)
		if new:
			writer.writeheader()
		writer.writerows(rows)


def apply_report(report_path, log_path, classes=APPLY_BY_DEFAULT, limit=0, fix=False, log=print, batch=200, touch_modified=True):
	"""Apply approved report rows. Every row is re-checked against the live data first, so a
	stale report cannot overwrite something that changed since; a row that cannot be applied
	(changed since, or an error) is left alone and written to the pending sheet. fix=False
	only counts. touch_modified=True updates the records' modified time (traceability)."""
	rows = [r for r in read_report(report_path) if r["class"] in classes]
	stats = {"selected": len(rows), "applied": 0, "skipped": 0, "failed": 0}
	pending = []
	pending_path = pending_path_for(report_path)
	skipped = []

	def hold(row, stage, reason):
		stats["failed" if stage == "failed" else "skipped"] += 1
		skipped.append(f"{row['customer']}/{row['lead']}: {reason}")
		pending.append(dict(row, **{"class": "SKIP" if stage == "skipped" else "FAILED"}, reason=reason, stage=stage))

	log_handle = writer = None
	if fix:
		log_handle = open(log_path, "a", newline="", encoding="utf-8")
		writer = csv.DictWriter(log_handle, fieldnames=LOG_FIELDS)
		if log_handle.tell() == 0:
			writer.writeheader()
	try:
		for row in rows:
			if limit and stats["applied"] >= limit:
				break
			customer, lead = row["customer"], row["lead"]
			current = frappe.db.get_value("Customer", customer, ["lead_name"], as_dict=True)
			lead_status = frappe.db.get_value("Lead", lead, "status")
			other = frappe.db.get_value("Customer", {"lead_name": lead, "name": ["!=", customer]}, "name")
			if current is None or lead_status is None:
				hold(row, "skipped", "no longer exists")
				continue
			if current.lead_name and current.lead_name != lead:
				hold(row, "skipped", f"customer now linked to Lead {current.lead_name}")
				continue
			if other:
				hold(row, "skipped", f"Lead now linked to Customer {other}")
				continue
			if current.lead_name == lead and lead_status == "Converted":
				stats["already_done"] = stats.get("already_done", 0) + 1  # a re-run: nothing to do, nothing to log
				continue
			if not fix:
				stats["applied"] += 1
				continue
			try:
				frappe.db.set_value("Customer", customer, "lead_name", lead, update_modified=touch_modified)
				frappe.db.set_value("Lead", lead, "status", "Converted", update_modified=touch_modified)
				writer.writerow(
					{
						"customer": customer,
						"lead": lead,
						"previous_lead_status": lead_status,
						"previous_customer_lead_name": current.lead_name or "",
						"applied_at": str(frappe.utils.now_datetime()),
					}
				)
				log_handle.flush()
				stats["applied"] += 1
				if stats["applied"] % batch == 0:
					frappe.db.commit()
					log(f"  applied {stats['applied']}")
			except Exception as e:
				frappe.db.rollback()
				hold(row, "failed", f"{type(e).__name__}: {e}"[:300])
		if fix:
			frappe.db.commit()
	finally:
		if log_handle:
			log_handle.close()
		if fix:
			_append_pending(pending_path, pending)
	stats["skipped_detail"] = skipped
	stats["pending_path"] = pending_path
	return stats


def revert_log(log_path, fix=False, log=print, batch=200, touch_modified=True):
	"""Undo applied rows: Customer.lead_name back to its previous value, Lead status back to
	what it was. Only when the Customer still points at that Lead (never undoes a later edit)."""
	stats = {"selected": 0, "reverted": 0, "skipped": 0}
	skipped = []
	for row in read_report(log_path):
		stats["selected"] += 1
		customer, lead = row["customer"], row["lead"]
		if frappe.db.get_value("Customer", customer, "lead_name") != lead:
			stats["skipped"] += 1
			skipped.append(f"{customer}/{lead}: Customer no longer points at this Lead")
			continue
		stats["reverted"] += 1
		if fix:
			frappe.db.set_value("Customer", customer, "lead_name", row["previous_customer_lead_name"] or None, update_modified=touch_modified)
			frappe.db.set_value("Lead", lead, "status", row["previous_lead_status"], update_modified=touch_modified)
			if stats["reverted"] % batch == 0:
				frappe.db.commit()
				log(f"  reverted {stats['reverted']}")
	if fix:
		frappe.db.commit()
	stats["skipped_detail"] = skipped
	return stats
