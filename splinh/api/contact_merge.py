"""Contact Merge endpoints (2026-11-19): the server side of Contact list > Actions > Merge Contacts and the Similar Contacts banner.

Three whitelisted methods; the rules live in custom/contact_merge.py (the generic ones are Lead Merge's).

  get_merge_preview(names)   read-only: the comparison the dialog shows (every offered field with each Contact's value), the duplicate
                             check (Contacts that are not duplicates are refused with a plain reason), the parties of each Contact
                             and the warning when they differ, the portal logins, the phones / emails / party links that will be
                             combined, what is connected to each Contact, and a `modified` stamp per Contact.
  merge_contacts(names, main, choices, stamps)
                             the merge, in ONE transaction (any failure rolls everything back):
                               1. write permission on every Contact; lock them; refuse if one changed since the dialog opened;
                               2. the duplicate check again, on the locked data;
                               3. a Contact Merge Log row with a full copy of every Contact that goes away (child rows included);
                               4. Frappe's own merge (rename_doc, merge=True) moves every link, Dynamic Link, comment, ToDo, file
                                  and tag of a merged-away Contact to the main Contact and deletes it WITH its phone, email and
                                  party-link rows, which is why those were read in step 3;
                               5. the chosen values and the combined phones / emails / links are saved on the main Contact, as the
                                  user, so Contact's own validate and hooks run (Phone Lookup, Duplicate Guard). This comes AFTER the
                                  merge: Duplicate Guard refuses a number another Contact still holds;
                               6. the Customers / Suppliers that use the main Contact as their primary Contact get their copies of
                                  mobile, email and first / last name refreshed;
                               7. the main Contact is renamed to match its name and first party (in a savepoint: a failed rename
                                  keeps the merge).
  similar_contacts(contact)  read-only: the other Contacts that share a phone number or an email with this one (banner).

`rebuild_search=False` below is essential: the default would queue a global-search rebuild of all Contacts."""

import json
import re

import frappe
from frappe import _
from frappe.model.rename_doc import get_link_fields, rename_doc
from frappe.utils import cstr, now_datetime

from splinh.custom import contact_merge as rules
from splinh.custom import phone_lookup

CONTACT = "Contact"
LOG = "Contact Merge Log"
CONSENT_FIELDS = ("waba_consent", "waba_marketing_consent")  # each with its _on and _source


_CODED = re.compile(r"^\+\d{1,3}[- ]\S")


def phone_format(raw, key):
	"""The text to store for a phone number added to the main Contact. The Contact's phone field accepts '+91-9876543210' or '+91 9876543210'
	(a compact '+919876543210' or a number without a country code is refused or blanked on save), so such numbers are written as
	'+<country code>-<national number>' using the country-aware key."""
	text = cstr(raw).strip()
	if _CODED.match(text) or not key:
		return text
	try:
		import phonenumbers

		parsed = phonenumbers.parse(key)
		return "+{0}-{1}".format(parsed.country_code, parsed.national_number)
	except Exception:
		return text


def _as_json(value, default):
	if value in (None, ""):
		return default
	if isinstance(value, str):
		try:
			return json.loads(value)
		except ValueError:
			frappe.throw(_("Invalid request."))
	return value


def _fail(error):
	frappe.throw(_(error.message), title=_("Merge Contacts"))


def _check_permissions(names):
	for n in names:
		frappe.has_permission(CONTACT, "write", doc=n, throw=True)


def _meta_parts():
	meta = frappe.get_meta(CONTACT)
	offered = rules.offered_fields(meta.fields, write_levels=meta.get_permlevel_access("write"))
	tables = {df.fieldname: rules.child_columns(frappe.get_meta(df.options).fields) for df in meta.get_table_fields()}
	return offered, tables


def _load(names):
	docs = {n: frappe.get_doc(CONTACT, n) for n in names}
	return docs, {n: d.as_dict() for n, d in docs.items()}


def _plain(rows):
	return [{k: v for k, v in r.items() if k not in ("name", "owner", "creation", "modified", "modified_by", "docstatus", "idx", "parent", "parentfield", "parenttype", "doctype")} for r in rows or []]


def _gate_input(dicts):
	return {n: {"phones": [r.get("phone") for r in d.get("phone_nos") or []], "emails": [r.get("email_id") for r in d.get("email_ids") or []] + ([d.get("email_id")] if d.get("email_id") else [])} for n, d in dicts.items()}


def _counts(name):
	"""What is connected to one Contact (for the dialog badges)."""
	dynamic = lambda parenttype: frappe.db.count("Dynamic Link", {"parenttype": parenttype, "link_doctype": CONTACT, "link_name": name})  # noqa: E731
	primary = frappe.get_all("Customer", filters={"customer_primary_contact": name}, pluck="name") + frappe.get_all("Supplier", filters={"supplier_primary_contact": name}, pluck="name")
	return {"calls": dynamic("Call Log"), "primary_for": primary, "documents": _document_count(name)}


def _document_count(name):
	"""Documents that name the Contact (Quotation, Sales Order, Invoice, Opportunity, Issue ... any Link field), counted cheaply."""
	total = 0
	for lf in get_link_fields(CONTACT):
		if lf.get("issingle") or lf["parent"] in ("Customer", "Supplier"):
			continue
		try:
			total += frappe.db.count(lf["parent"], {lf["fieldname"]: name})
		except Exception:
			pass
	return total


def _link_counts(name):
	"""{doctype.field: rows pointing at this Contact}, plus Dynamic Links - for the log."""
	out = {}
	for lf in get_link_fields(CONTACT):
		if lf.get("issingle"):
			continue
		try:
			count = frappe.db.count(lf["parent"], {lf["fieldname"]: name})
		except Exception:
			count = None
		if count != 0:
			out["{0}.{1}".format(lf["parent"], lf["fieldname"])] = count
	for parenttype, count in frappe.db.sql("select parenttype, count(*) from `tabDynamic Link` where link_doctype=%s and link_name=%s group by parenttype", (CONTACT, name)):
		out["Dynamic Link in {0}".format(parenttype)] = count
	return out


def _weight(counts, links):
	return counts["calls"] + counts["documents"] + 5 * len(links) + 50 * len(counts["primary_for"])


@frappe.whitelist(methods=["POST"])
def get_merge_preview(names=None):
	names = [cstr(n) for n in _as_json(names, [])]
	try:
		rules.validate_selection(names, names[0] if names else "")
	except rules.MergeError as e:
		_fail(e)
	_check_permissions(names)

	offered, _tables = _meta_parts()
	docs, dicts = _load(names)
	gate = rules.duplicate_check(_gate_input(dicts), phone_lookup._normalize)
	parties = {n: rules.parties_of(_plain(dicts[n].get("links"))) for n in names}
	cross = rules.cross_party_note(parties)
	logins = rules.user_conflict({n: dicts[n].get("user") for n in names})
	rows = rules.build_rows(offered, dicts, names)

	contacts = []
	for n in names:
		d, counts = docs[n], _counts(n)
		contacts.append(
			{
				"name": n,
				"title": d.full_name or n,
				"status": d.status,
				"owner": d.owner,
				"creation": d.creation,
				"modified": cstr(d.modified),
				"parties": parties[n],
				"phones": [r.phone for r in d.phone_nos],
				"emails": [r.email_id for r in d.email_ids],
				"user": d.user,
				"unsubscribed": int(d.unsubscribed or 0),
				**counts,
				"weight": _weight(counts, d.links),
			}
		)
	suggested = rules.suggest_main(contacts)
	main = dicts[suggested]
	combined = {
		"phones": [r["phone"] for r in rules.combine_phones(_plain(main.get("phone_nos")), [r for n in names if n != suggested for r in _plain(dicts[n].get("phone_nos"))], phone_lookup._normalize, phone_format)],
		"emails": [r["email_id"] for r in rules.combine_emails(_plain(main.get("email_ids")), [r for n in names if n != suggested for r in _plain(dicts[n].get("email_ids"))])],
		"links": ["{0} {1}".format(r["link_doctype"], r["link_name"]) for r in rules.combine_links(_plain(main.get("links")), [r for n in names if n != suggested for r in _plain(dicts[n].get("links"))])],
	}
	return {
		"names": names,
		"contacts": contacts,
		"rows": rows,
		"stamps": {x["name"]: x["modified"] for x in contacts},
		"suggested_main": suggested,
		"duplicate": gate,
		"cross_party": cross,
		"logins": logins,
		"combined_if_main_is_suggested": combined,
		"unsubscribed_differs": len({x["unsubscribed"] for x in contacts}) > 1,
	}


def _consent_entry(d, field):
	return {"value": d.get(field), "on": d.get(field + "_on"), "source": d.get(field.replace("consent", "consent_source"))}


@frappe.whitelist(methods=["POST"])
def merge_contacts(names=None, main=None, choices=None, stamps=None):
	names = [cstr(n) for n in _as_json(names, [])]
	choices = _as_json(choices, {})
	stamps = _as_json(stamps, {})
	main = cstr(main)
	try:
		rules.validate_selection(names, main)
	except rules.MergeError as e:
		_fail(e)
	others = [n for n in names if n != main]
	_check_permissions(names)

	# Lock in a fixed order, then require the Contacts to be exactly what the dialog showed.
	locked = frappe.db.sql("select name, modified from `tabContact` where name in %(names)s order by name for update", {"names": names}, as_dict=True)
	try:
		rules.check_stamps(names, stamps, {r.name: cstr(r.modified) for r in locked})
		offered, _tables = _meta_parts()
		docs, dicts = _load(names)
		gate = rules.duplicate_check(_gate_input(dicts), phone_lookup._normalize)
		if not gate["ok"]:
			raise rules.MergeError(gate["reason"])
		updates = rules.compute_updates(offered, dicts, names, main, choices)
	except rules.MergeError as e:
		_fail(e)

	main_d = dicts[main]
	other_phones = [r for n in others for r in _plain(dicts[n].get("phone_nos"))]
	other_emails = [r for n in others for r in _plain(dicts[n].get("email_ids"))]
	other_links = [r for n in others for r in _plain(dicts[n].get("links"))]
	add_phones = rules.combine_phones(_plain(main_d.get("phone_nos")), other_phones, phone_lookup._normalize, phone_format)
	add_emails = rules.combine_emails(_plain(main_d.get("email_ids")), other_emails)
	add_links = rules.combine_links(_plain(main_d.get("links")), other_links)
	consent = {f: rules.restrictive_consent([_consent_entry(dicts[n], f) for n in names]) for f in CONSENT_FIELDS}

	changes = {
		"fields": {f: {"before": main_d.get(f), "after": v} for f, v in updates.items()},
		"phones_added": [r["phone"] for r in add_phones],
		"emails_added": [r["email_id"] for r in add_emails],
		"links_added": ["{0} {1}".format(r["link_doctype"], r["link_name"]) for r in add_links],
	}
	log = frappe.get_doc(
		{
			"doctype": LOG,
			"main_contact": main,
			"merged_contacts": ", ".join(others),
			"merged_by": frappe.session.user,
			"merged_on": now_datetime(),
			"changes": frappe.as_json(changes),
			"field_choices": frappe.as_json(choices),
			"link_counts": frappe.as_json({n: _link_counts(n) for n in others}),
			"snapshot": frappe.as_json({n: dicts[n] for n in others}),
		}
	).insert(ignore_permissions=True)

	for other in others:
		rename_doc(CONTACT, other, main, merge=True, ignore_permissions=True, show_alert=False, rebuild_search=False)

	main_doc = frappe.get_doc(CONTACT, main)
	main_doc.update(updates)
	for f, entry in consent.items():  # the read-only Waba consent: the most restrictive record is kept
		if entry and main_doc.meta.has_field(f):
			main_doc.set(f, entry.get("value") or "Unknown")
			main_doc.set(f + "_on", entry.get("on"))
			main_doc.set(f.replace("consent", "consent_source"), entry.get("source"))
	for r in add_phones:
		main_doc.append("phone_nos", r)
	for r in add_emails:
		main_doc.append("email_ids", r)
	for r in add_links:
		main_doc.append("links", r)
	main_doc.save()

	overridden = {}
	for fieldname, chosen in updates.items():
		stored = main_doc.get(fieldname)
		changes["fields"][fieldname]["chosen"] = chosen
		changes["fields"][fieldname]["after"] = stored
		if rules.norm(stored) != rules.norm(chosen):
			overridden[fieldname] = stored

	refreshed = _refresh_copies(main_doc)
	final = _rename_to_match(main_doc)
	frappe.db.set_value(LOG, log.name, {"changes": frappe.as_json({**changes, "customers_refreshed": refreshed, "renamed_to": final if final != main else None}), "main_contact": final}, update_modified=False)

	return {
		"main": final,
		"merged": others,
		"changed_fields": sorted(updates),
		"overridden": overridden,
		"added": {"phones": len(add_phones), "emails": len(add_emails), "links": len(add_links)},
		"customers_refreshed": refreshed,
		"renamed": final != main,
		"log": log.name,
	}


def _refresh_copies(contact):
	"""The Customers / Suppliers whose primary Contact is `contact` copy its mobile, email and name when they are saved. Bring those
	copies up to date now, directly (no full save: it would run other apps' hooks). Returns ["Customer C0099", ...] of those changed."""
	changed = []
	values = {f: contact.get(f) for f in rules.COPY_FIELDS}
	for doctype, link_field in (("Customer", "customer_primary_contact"), ("Supplier", "supplier_primary_contact")):
		meta = frappe.get_meta(doctype)
		fields = [f for f in rules.COPY_FIELDS if meta.has_field(f)]
		if not fields:
			continue
		for row in frappe.get_all(doctype, filters={link_field: contact.name}, fields=["name", *fields]):
			diff = rules.copy_changes(row, {f: values[f] for f in fields})
			if diff:
				frappe.db.set_value(doctype, row.name, diff, update_modified=False)
				changed.append("{0} {1}".format(doctype, row.name))
	return changed


def _rename_to_match(contact):
	"""Rename the surviving Contact to 'Full Name-FirstParty' (Contact.autoname's rule) when that differs. In a savepoint: if the rename fails
	the merge stays and the old name is kept. Returns the final name."""
	first = contact.links[0].link_name if contact.links else None
	wanted = rules.contact_name(contact._get_full_name(), first)
	if not wanted or wanted == contact.name:
		return contact.name
	if frappe.db.exists(CONTACT, wanted):
		return contact.name  # that name is taken (another person): never merge into it by accident
	frappe.db.savepoint("contact_merge_rename")
	try:
		rename_doc(CONTACT, contact.name, wanted, merge=False, ignore_permissions=True, show_alert=False, rebuild_search=False)
		return wanted
	except Exception:
		frappe.db.rollback(save_point="contact_merge_rename")
		frappe.log_error(title="splinh contact_merge: rename after a merge failed", message=frappe.get_traceback())
		return contact.name


@frappe.whitelist(methods=["GET", "POST"])
def similar_contacts(contact=None):
	"""Contacts that share a phone number or an email with this one (the Similar Contacts banner). Only Contacts the user may read."""
	contact = cstr(contact).strip()
	if not contact:
		frappe.throw(_("No Contact was given."))
	frappe.has_permission(CONTACT, "read", doc=contact, throw=True)
	doc = frappe.get_doc(CONTACT, contact)
	by_phone = []
	for key in {k for k in (phone_lookup._normalize(r.phone) for r in doc.phone_nos) if k}:
		by_phone += [row["name"] for row in phone_lookup.find_all_by_key(key) if row["doctype"] == CONTACT]
	emails = sorted({e.strip().lower() for e in [r.email_id for r in doc.email_ids] + ([doc.email_id] if doc.email_id else []) if e and "@" in e})
	by_email = frappe.db.sql_list("select distinct parent from `tabContact Email` where parenttype = 'Contact' and lower(email_id) in %(e)s and parent != %(c)s limit 30", {"e": emails, "c": contact}) if emails else []
	mine = {(r.link_doctype, r.link_name) for r in doc.links}
	found = rules.similar_contacts(contact, by_phone, by_email, limit=rules.SIMILAR_LIMIT * 3)
	rows = []
	for item in found:
		name = item["name"]
		if not frappe.has_permission(CONTACT, "read", doc=name):
			continue
		d = frappe.db.get_value(CONTACT, name, ["full_name", "status", "modified"], as_dict=True) or {}
		parties = frappe.get_all("Dynamic Link", filters={"parent": name, "parenttype": "Contact"}, fields=["link_doctype", "link_name"])
		item["same_party"] = any((p.link_doctype, p.link_name) in mine for p in parties)
		rows.append({**item, "title": d.get("full_name") or name, "status": d.get("status"), "parties": rules.parties_of(parties)})
	rows.sort(key=lambda r: (-len(r["matched_on"]), 0 if r["same_party"] else 1))
	return {"contact": contact, "matches": rows[: rules.SIMILAR_LIMIT]}
