"""Contact Merge (2026-11-19): the rules, kept free of the database so they can be tested offline.

The Contact list's "Merge Contacts" action (custom/contact_merge_list.js, api/contact_merge.py) merges 2 to 4 duplicate Contacts into one
chosen "main" Contact, the same way Lead Merge does for Leads (custom/lead_merge.py, whose generic rules are used here).

What is different for a Contact:
  - ONLY real duplicates merge: every selected Contact must share a phone number or an email with another one of the group
    (duplicate_check). Name alone is not enough.
  - A Contact belongs to parties (Lead, Customer, Supplier, Prospect...) through its `links` rows. Contacts of different parties may merge:
    the main Contact then links to all of them (cross_party_note warns about it).
  - Frappe's own merge deletes the merged-away Contact WITH its phone, email and link rows, so these are combined here and saved on the main
    Contact after the merge: identical numbers / emails once, at most ONE primary email, ONE primary phone and ONE primary mobile
    (Contact.validate throws otherwise), and the main Contact's own primaries always win.
  - The Waba consent fields are read-only; the more restrictive consent is kept automatically (restrictive_consent).

Nothing here reads or writes the database."""

import re
from html import escape

from splinh.custom import lead_merge as lm
from splinh.custom.lead_merge import MergeError, check_stamps as _check_stamps, norm, row_key  # noqa: F401  (re-exported)

MIN_CONTACTS = 2
MAX_CONTACTS = 4

# Fields never offered as a choice: they identify the record, belong to the Google sync, or are rebuilt by Contact's own validate.
NEVER_OFFER = {
	"name",
	"naming_series",
	"owner",
	"creation",
	"google_contacts",
	"google_contacts_id",
	"sync_with_google_contacts",
	"pulled_from_google_contacts",
}
NOT_A_VALUE = lm.NOT_A_VALUE


def offered_fields(fields, write_levels=(0,)):
	"""The Contact fields a user may choose between, in form order, each with its section heading.

	Left out: layout, tables (combined, see combine_*), read-only and hidden fields (full_name, email_id, phone, mobile_no and the Waba
	consent fields are rebuilt or merged by rule), NEVER_OFFER, and fields on a permission level the user cannot write. Contact has no
	free-text field worth appending, so nothing is appendable."""
	offered = []
	section = ""
	for f in fields:
		fieldtype = f.get("fieldtype")
		if fieldtype in ("Section Break", "Tab Break"):
			section = f.get("label") or section
			continue
		if fieldtype in NOT_A_VALUE:
			continue
		name = f.get("fieldname")
		if not name or name in NEVER_OFFER:
			continue
		if f.get("read_only") or f.get("hidden"):
			continue
		if int(f.get("permlevel") or 0) not in write_levels:
			continue
		offered.append(
			{
				"fieldname": name,
				"label": f.get("label") or name,
				"fieldtype": fieldtype,
				"options": f.get("options") or "",
				"length": int(f.get("length") or 0),
				"section": section,
				"appendable": None,
			}
		)
	return offered


# build_rows / compute_updates / child_columns / combine_child_rows are generic: used as they are.
build_rows = lm.build_rows
compute_updates = lm.compute_updates
child_columns = lm.child_columns
suggest_main = lm.suggest_main
rename_args = lm.rename_args
member_plan = lm.member_plan


def validate_selection(names, main):
	"""2 to 4 different Contacts including the main one; returns the names as a list."""
	names = [str(n) for n in (names or [])]
	if len(set(names)) != len(names):
		raise MergeError("Select each Contact only once.")
	if not MIN_CONTACTS <= len(names) <= MAX_CONTACTS:
		raise MergeError("Select between {0} and {1} Contacts to merge.".format(MIN_CONTACTS, MAX_CONTACTS))
	if main not in names:
		raise MergeError("The main Contact must be one of the selected Contacts.")
	return names


def check_stamps(names, stamps, current):
	"""The Contacts must be unchanged since the dialog was opened (same rule as Lead Merge, Contact wording)."""
	try:
		_check_stamps(names, stamps, current)
	except MergeError as e:
		raise MergeError(e.message.replace("Lead", "Contact").replace("Merge Leads", "Merge Contacts"))


# ---------------------------------------------------------------------------------------------------------------
# The duplicate gate: only Contacts that share a phone number or an email may merge.


def phone_key(raw, normalize=None):
	"""Comparable phone: Frappe-side callers pass splinh's country-aware normalizer; without one, the digits (last 10 when longer)."""
	if not raw:
		return None
	if normalize:
		return normalize(raw)
	digits = re.sub(r"\D", "", re.sub(r"(?i)(?:ext\.?|[x#;])\s*\d.*$", "", str(raw)))
	return ("+" + digits[-10:]) if len(digits) >= 7 else None


def email_key(raw):
	text = str(raw or "").strip().lower()
	return text if "@" in text else None


def keys_of(contact, normalize=None):
	"""{"phones": {keys}, "emails": {keys}} of one Contact: {"phones": [raw...], "emails": [raw...]}."""
	return {
		"phones": {k for k in (phone_key(p, normalize) for p in contact.get("phones") or []) if k},
		"emails": {k for k in (email_key(e) for e in contact.get("emails") or []) if k},
	}


def duplicate_check(contacts, normalize=None):
	"""Are these Contacts duplicates of each other? contacts: {name: {"phones": [raw...], "emails": [raw...]}}.

	They are when the group is connected by shared phones or emails: every Contact matches at least one other (A-B by phone and B-C by
	email is one group). Returns {"ok", "reason", "pairs": [{"a", "b", "on": ["phone", "email"]}], "unmatched": [names]}; `reason` is a
	plain sentence for the person when ok is False."""
	names = list(contacts)
	keys = {n: keys_of(contacts[n], normalize) for n in names}
	pairs = []
	parent = {n: n for n in names}

	def find(x):
		while parent[x] != x:
			parent[x] = parent[parent[x]]
			x = parent[x]
		return x

	for i, a in enumerate(names):
		for b in names[i + 1 :]:
			on = []
			if keys[a]["phones"] & keys[b]["phones"]:
				on.append("phone")
			if keys[a]["emails"] & keys[b]["emails"]:
				on.append("email")
			if on:
				pairs.append({"a": a, "b": b, "on": on})
				parent[find(a)] = find(b)
	roots = {find(n) for n in names}
	matched = {p["a"] for p in pairs} | {p["b"] for p in pairs}
	unmatched = [n for n in names if n not in matched]
	if len(roots) == 1 and len(names) > 1:
		return {"ok": True, "reason": "", "pairs": pairs, "unmatched": []}
	if not pairs:
		reason = "These Contacts are not duplicates: they share no phone number and no email. Only Contacts with a phone number or an email in common can be merged."
	elif unmatched:
		reason = "These are not all duplicates of each other. No phone number or email is shared with: {0}. Only Contacts with a phone number or an email in common can be merged.".format(
			", ".join(unmatched)
		)
	else:
		reason = "These Contacts form separate groups of duplicates (they are not connected by a shared phone number or email). Merge one group at a time."
	return {"ok": False, "reason": reason, "pairs": pairs, "unmatched": unmatched}


# ---------------------------------------------------------------------------------------------------------------
# Parties, portal logins, consent.


def parties_of(links):
	"""['Customer C0099', 'Lead CRM-LEAD-2026-00001'] from a Contact's link rows [{"link_doctype", "link_name"}], in order, once each."""
	seen, out = set(), []
	for row in links or []:
		dt, name = row.get("link_doctype"), row.get("link_name")
		if dt and name and (dt, name) not in seen:
			seen.add((dt, name))
			out.append("{0} {1}".format(dt, name))
	return out


def cross_party_note(parties_by_contact):
	"""{"different": bool, "all": [...], "note": str}. `different` is True when the Contacts do not all belong to exactly the same parties;
	the note names each Contact's parties and says the main Contact will link to all of them."""
	sets = {n: frozenset(p) for n, p in parties_by_contact.items()}
	everyone = []
	for parties in parties_by_contact.values():
		for p in parties:
			if p not in everyone:
				everyone.append(p)
	different = len(set(sets.values())) > 1
	if not different:
		return {"different": False, "all": everyone, "note": ""}
	lines = "; ".join("{0}: {1}".format(n, ", ".join(p) if p else "no party") for n, p in parties_by_contact.items())
	return {
		"different": True,
		"all": everyone,
		"note": "These Contacts belong to different parties ({0}). After the merge the main Contact will be linked to all of them ({1}). Merge only if they are the same person.".format(
			lines, ", ".join(everyone)
		),
	}


def user_conflict(users):
	"""The distinct portal logins among the Contacts ({name: user or None}); a conflict when there are two or more different ones."""
	distinct = sorted({u for u in users.values() if u})
	return {"conflict": len(distinct) > 1, "users": distinct}


CONSENT_ORDER = {"No": 0, "Unknown": 1, "Yes": 2}  # the lower the more restrictive


def restrictive_consent(entries):
	"""The consent record to keep: the most restrictive value (No beats Unknown beats Yes); on a tie the one with the newest date.
	entries: [{"value": "Yes"|"No"|"Unknown"|None, "on": date or None, "source": text}]. Returns the winning entry (a copy), or None."""
	entries = [e for e in entries if e is not None]
	if not entries:
		return None
	best = min(entries, key=lambda e: (CONSENT_ORDER.get(e.get("value") or "Unknown", 1), -(e.get("on").toordinal() if e.get("on") else 0)))
	return dict(best)


# ---------------------------------------------------------------------------------------------------------------
# Combining phones, emails and party links onto the main Contact.


def _flag(row, name):
	return 1 if row.get(name) else 0


def combine_phones(main_rows, other_rows, normalize=None, fmt=None):
	"""Rows to ADD to the main Contact's phone_nos: [{phone, is_primary_phone, is_primary_mobile_no, ...}].

	main_rows: the main Contact's rows; other_rows: the other Contacts' rows in the order of the merge. A number the main Contact already
	holds (or an earlier row), compared by normalized digits, is not added again. The main Contact's own primary flags always stay; a flag
	the main Contact does not use is taken from the first added row that has it, and never more than one of each ends up set.
	fmt(raw, key) -> the text to store for an added number (the Contact's phone field only accepts "+91-9876543210" or "+91 9876543210",
	so the caller gives numbers without a country code, or in compact form, their "+CC-number" shape); the dedupe is always on `key`."""
	seen = {phone_key(r.get("phone"), normalize) or ("raw:" + norm(r.get("phone"))) for r in main_rows or [] if norm(r.get("phone"))}
	has_primary = any(_flag(r, "is_primary_phone") for r in main_rows or [])
	has_mobile = any(_flag(r, "is_primary_mobile_no") for r in main_rows or [])
	added = []
	for r in other_rows or []:
		raw = r.get("phone")
		if not norm(raw):
			continue
		key = phone_key(raw, normalize) or ("raw:" + norm(raw))
		if key in seen:
			continue
		seen.add(key)
		row = {k: v for k, v in r.items() if k not in lm.CHILD_STANDARD}
		if fmt:
			row["phone"] = fmt(raw, key) or raw
		row["is_primary_phone"] = 1 if (not has_primary and _flag(r, "is_primary_phone")) else 0
		row["is_primary_mobile_no"] = 1 if (not has_mobile and _flag(r, "is_primary_mobile_no")) else 0
		has_primary = has_primary or bool(row["is_primary_phone"])
		has_mobile = has_mobile or bool(row["is_primary_mobile_no"])
		added.append(row)
	return added


def combine_emails(main_rows, other_rows):
	"""Rows to ADD to the main Contact's email_ids: [{email_id, is_primary}]. Same email (any case) once; the main Contact's primary stays;
	if it has none, the first added row that was primary becomes the one primary."""
	seen = {email_key(r.get("email_id")) for r in main_rows or [] if email_key(r.get("email_id"))}
	has_primary = any(_flag(r, "is_primary") for r in main_rows or [])
	added = []
	for r in other_rows or []:
		key = email_key(r.get("email_id"))
		if not key or key in seen:
			continue
		seen.add(key)
		row = {k: v for k, v in r.items() if k not in lm.CHILD_STANDARD}
		row["is_primary"] = 1 if (not has_primary and _flag(r, "is_primary")) else 0
		has_primary = has_primary or bool(row["is_primary"])
		added.append(row)
	return added


def combine_links(main_rows, other_rows):
	"""Party links to ADD to the main Contact: [{link_doctype, link_name}], each (doctype, name) once, in the order found."""
	seen = {(r.get("link_doctype"), r.get("link_name")) for r in main_rows or []}
	added = []
	for r in other_rows or []:
		key = (r.get("link_doctype"), r.get("link_name"))
		if not key[0] or not key[1] or key in seen:
			continue
		seen.add(key)
		added.append({"link_doctype": key[0], "link_name": key[1]})
	return added


def contact_name(full_name, first_link_name):
	"""What Contact.autoname would call the record: 'Full Name-FirstParty' (the party suffix only when there is a party)."""
	name = str(full_name or "").strip()
	link = str(first_link_name or "").strip()
	return name + "-" + link if link else name


# ---------------------------------------------------------------------------------------------------------------
# After the merge: the copies other documents keep of the primary Contact, and what Frappe drops.

COPY_FIELDS = ("mobile_no", "email_id", "first_name", "last_name")  # Customer / Supplier fetch these from their primary Contact


def copy_changes(current, contact):
	"""{field: new value} for the fields of a Customer / Supplier (`current`) that differ from the Contact they copy from."""
	out = {}
	for f in COPY_FIELDS:
		new = contact.get(f)
		if norm(current.get(f)) != norm(new):
			out[f] = new if new is not None else ""
	return out


def todo_note(old, todos):
	"""The text kept on the main Contact for tasks Frappe drops in a merge (when the same person is assigned on both Contacts only one
	task per person survives). Everything typed is escaped."""
	lines = []
	for t in todos:
		parts = [escape(str(t.get("description") or "").strip() or "(no text)")]
		meta = [x for x in (t.get("allocated_to"), t.get("status"), str(t["date"]) if t.get("date") else None) if x]
		if meta:
			parts.append("(" + escape(", ".join(str(m) for m in meta)) + ")")
		lines.append("<li>" + " ".join(parts) + "</li>")
	return (
		"<b>Tasks of the merged Contact " + escape(str(old)) + "</b> (kept here because the same person is already assigned on this Contact)<ul>"
		+ "".join(lines)
		+ "</ul>"
	)


# ---------------------------------------------------------------------------------------------------------------
# Possible duplicates of one Contact (the banner on the Contact form, api/contact_merge.py).

SIMILAR_LIMIT = 10
SIMILAR_REASONS = ("phone", "email")


def similar_contacts(own, by_phone, by_email, same_party=(), limit=SIMILAR_LIMIT):
	"""[{"name", "matched_on": [...], "same_party": bool}] for the other Contacts that share a phone or an email with `own`.

	A Contact found for both reasons comes first, then those that share a party with `own`; ties keep the order found. The Contact itself is
	never listed. `same_party` is the set of names that share a party with `own`."""
	reasons, order = {}, []
	for reason, names in (("phone", by_phone), ("email", by_email)):
		for name in names or []:
			if not name or name == own:
				continue
			if name not in reasons:
				reasons[name] = []
				order.append(name)
			if reason not in reasons[name]:
				reasons[name].append(reason)
	same = set(same_party or ())
	order.sort(key=lambda n: (-len(reasons[n]), 0 if n in same else 1))
	return [{"name": n, "matched_on": sorted(reasons[n], key=SIMILAR_REASONS.index), "same_party": n in same} for n in order[: max(1, int(limit))]]
