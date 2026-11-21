"""Lead Merge (2026-11-05): the rules, kept free of the database so they can be tested offline.

The Lead list's "Merge Leads" action (custom/lead_merge_list.js, api/lead_merge.py) merges 2 to 4 Leads
into one chosen "main" Lead. For every field of the Lead the user picks which Lead's value ends up in
the main Lead ("use"), and for free-text fields can also append the other Leads' texts to it
(Lead 1 "hi" + Lead 2 "hey" -> "hi hey"). A field the user does not touch keeps the main Lead's value,
even when it is blank. The rows of the Leads' child tables (Notes, tags, ...) are combined into the
main Lead, identical rows once. Everything that points at the merged-away Leads is moved by Frappe's
own merge (api/lead_merge.py).

This module only decides WHAT the main Lead will hold; nothing here reads or writes the database.
"""

MIN_LEADS = 2
MAX_LEADS = 4
DATA_MAX = 140  # a Data field is a varchar(140)

# Never offered as a choice: they identify the record and must stay the main Lead's own.
NEVER_OFFER = {"name", "naming_series", "custom_record_number", "owner", "creation"}
# Layout and display-only fieldtypes, and tables (tables are combined, see combine_child_rows).
NOT_A_VALUE = {
	"Section Break",
	"Column Break",
	"Tab Break",
	"HTML",
	"Heading",
	"Button",
	"Fold",
	"Table",
	"Table MultiSelect",
	"Image",
}

# Append is for free text only. These hold ONE identifying value; "a@x.com b@y.com" or two GSTINs
# would be invalid data, so they can only be overwritten.
APPEND_NEVER = {
	"email_id",
	"fax",
	"custom_gstin",
	"custom_pincode",
	"website",
	"custom_geotag",
	"custom_record_number",
}
MULTI_LINE_TYPES = {"Small Text", "Text", "Long Text", "Markdown Editor"}
HTML_TYPES = {"Text Editor"}
SEPARATOR = {"single": " ", "multi": "\n", "html": "<div><br></div>"}

# Standard columns of a child row: never compared or copied.
CHILD_STANDARD = {
	"name",
	"owner",
	"creation",
	"modified",
	"modified_by",
	"docstatus",
	"idx",
	"parent",
	"parentfield",
	"parenttype",
	"doctype",
}


class MergeError(Exception):
	"""A merge request that cannot be carried out; `field` names the row to blame when there is one."""

	def __init__(self, message, field=None):
		super().__init__(message)
		self.message = message
		self.field = field


def norm(value):
	"""Comparable text: None, "" and 0 all mean 'empty'."""
	if value is None or value in ("", 0, 0.0, False):
		return ""
	return " ".join(str(value).split())


def appendable_kind(field):
	"""'single' | 'multi' | 'html' for a free-text field, else None."""
	name = field.get("fieldname") or ""
	fieldtype = field.get("fieldtype")
	if name in APPEND_NEVER or name.endswith("_owner"):
		return None
	if fieldtype == "Data":
		# Data with options is Email / Phone / URL / Name ...: one identifying value, not free text
		return None if field.get("options") else "single"
	if fieldtype in MULTI_LINE_TYPES:
		return "multi"
	if fieldtype in HTML_TYPES:
		return "html"
	return None


def offered_fields(fields, write_levels=(0,)):
	"""The Lead fields a user may choose between, in form order, each with its section heading.

	Left out: layout, tables, read-only and hidden fields (read-only ones are rebuilt by the Lead's own
	validate, e.g. lead_name from first/last name), the record identifiers in NEVER_OFFER, and fields on a
	permission level the user cannot write."""
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
				"appendable": appendable_kind(f),
			}
		)
	return offered


def build_rows(offered, docs, names):
	"""One row per offered field: each Lead's value and whether the Leads differ."""
	rows = []
	for f in offered:
		values = {n: docs[n].get(f["fieldname"]) for n in names}
		differs = len({norm(v) for v in values.values()}) > 1
		rows.append({**f, "values": values, "differs": differs})
	return rows


def append_values(base, extras, kind):
	"""base + each non-empty extra, joined with the separator for the field kind.

	An extra that equals (ignoring case and spacing) the base or an already appended text is skipped, so
	appending the same word twice does not repeat it."""
	sep = SEPARATOR[kind]
	text = "" if base is None else str(base)
	seen = [norm(text).lower()]
	for extra in extras:
		piece = "" if extra is None else str(extra)
		if not piece.strip():
			continue
		key = norm(piece).lower()
		if key in seen:
			continue
		seen.append(key)
		text = piece.strip() if not text.strip() else text.rstrip() + sep + piece.strip()
	return text


def compute_updates(offered, docs, names, main, choices):
	"""The values to write on the main Lead: {fieldname: new value}, only where it changes.

	choices: {fieldname: {"use": <lead name>, "append": [<lead names>]}}. `use` defaults to the main
	Lead (keep as is); `append` is allowed only on free-text fields and never on the Lead that is used."""
	by_field = {f["fieldname"]: f for f in offered}
	updates = {}
	for fieldname, choice in (choices or {}).items():
		field = by_field.get(fieldname)
		if not field:
			raise MergeError("This field cannot be merged: {0}".format(fieldname), fieldname)
		choice = choice or {}
		use = choice.get("use") or main
		if use not in names:
			raise MergeError("{0}: value taken from a Lead that is not selected".format(field["label"]), fieldname)
		wanted = choice.get("append") or []
		if wanted and not field["appendable"]:
			raise MergeError("{0}: append is not allowed for this field".format(field["label"]), fieldname)
		for n in wanted:
			if n not in names or n == use:
				raise MergeError("{0}: cannot append from {1}".format(field["label"], n), fieldname)
		appended = [n for n in names if n in set(wanted)]  # dialog column order

		value = docs[use].get(fieldname)
		if appended:
			value = append_values(value, [docs[n].get(fieldname) for n in appended], field["appendable"])
		if field["appendable"] == "single" and len(norm(value)) > (field["length"] or DATA_MAX):
			raise MergeError(
				"{0}: the combined text is longer than {1} characters".format(field["label"], field["length"] or DATA_MAX),
				fieldname,
			)
		if norm(value) != norm(docs[main].get(fieldname)):
			updates[fieldname] = value if value is not None else ""
	return updates


def child_columns(child_fields):
	"""The value columns of a child table: its own fields without layout and standard columns."""
	return [
		f.get("fieldname")
		for f in child_fields
		if f.get("fieldtype") not in NOT_A_VALUE and f.get("fieldname") not in CHILD_STANDARD
	]


def row_key(row, columns):
	return tuple(norm(row.get(c)) for c in columns)


def combine_child_rows(tables, docs, names, main):
	"""Rows to ADD to the main Lead, per child table: {table fieldname: [row dict, ...]}.

	tables: {table fieldname: [value columns]}. A row identical (on its value columns) to one the main
	Lead already has, or to one already taken from an earlier Lead, is not added again; empty rows are
	ignored. Rows keep their own author and time (CRM Note.added_by / added_on)."""
	added = {}
	for table, columns in tables.items():
		seen = {row_key(r, columns) for r in (docs[main].get(table) or [])}
		rows = []
		for n in names:
			if n == main:
				continue
			for r in docs[n].get(table) or []:
				key = row_key(r, columns)
				if not any(key) or key in seen:
					continue
				seen.add(key)
				rows.append({c: r.get(c) for c in columns})
		if rows:
			added[table] = rows
	return added


def validate_selection(names, main):
	"""2 to 4 different Leads including the main one; returns the names as a list."""
	names = [str(n) for n in (names or [])]
	if len(set(names)) != len(names):
		raise MergeError("Select each Lead only once.")
	if not MIN_LEADS <= len(names) <= MAX_LEADS:
		raise MergeError("Select between {0} and {1} Leads to merge.".format(MIN_LEADS, MAX_LEADS))
	if main not in names:
		raise MergeError("The main Lead must be one of the selected Leads.")
	return names


def check_stamps(names, stamps, current):
	"""The Leads must be unchanged since the dialog was opened.

	stamps: {name: modified as the dialog saw it}; current: {name: modified now}."""
	stamps = stamps or {}
	for n in names:
		if n not in current:
			raise MergeError("Lead {0} no longer exists. Close this window and select the Leads again.".format(n))
		if str(stamps.get(n)) != str(current[n]):
			raise MergeError(
				"Lead {0} was changed after this window was opened. Close it and open Merge Leads again.".format(n)
			)


def suggest_main(leads):
	"""The Lead that carries most history; the oldest on a tie. leads: [{name, creation, weight}]."""
	return sorted(leads, key=lambda x: (-x["weight"], str(x["creation"]), x["name"]))[0]["name"]


# ---------------------------------------------------------------------------------------------------------------
# What the merge must also keep right outside Frappe's own link moves (custom/lead_merge_links.py applies these).


def rename_args(args):
	"""(old, new, merge) from the arguments Frappe passes to a rename hook (it may put the method name first), or None."""
	items = list(args or [])
	if items and items[0] in ("before_rename", "after_rename"):
		items = items[1:]
	if len(items) < 2 or not items[0] or not items[1]:
		return None
	return str(items[0]), str(items[1]), bool(items[2]) if len(items) > 2 else False


def member_plan(rows, old, new):
	"""For one locked Recipient Set holding `old` (and maybe already `new`): (names of rows to point at `new`, names to remove).
	A person stays in the Set once, so when `new` is already there the `old` row goes away."""
	old_rows = [r["name"] for r in rows if r.get("party") == old]
	if not old_rows:
		return [], []
	if any(r.get("party") == new for r in rows):
		return [], old_rows
	return old_rows[:1], old_rows[1:]


def todo_note(old, todos):
	"""The text kept on the main Lead for tasks Frappe drops in a merge (when the same person is assigned on both Leads only one
	task per person survives). Everything typed is escaped."""
	from html import escape

	lines = []
	for t in todos:
		parts = [escape(str(t.get("description") or "").strip() or "(no text)")]
		meta = [x for x in (t.get("allocated_to"), t.get("status"), str(t["date"]) if t.get("date") else None) if x]
		if meta:
			parts.append("(" + escape(", ".join(str(m) for m in meta)) + ")")
		lines.append("<li>" + " ".join(parts) + "</li>")
	return (
		"<b>Tasks of the merged Lead " + escape(str(old)) + "</b> (kept here because the same person is already assigned on this Lead)<ul>"
		+ "".join(lines)
		+ "</ul>"
	)


# ---------------------------------------------------------------------------------------------------------------
# Possible duplicates of one Lead (the banner on the Lead form, api/lead_duplicates.py).

SIMILAR_LIMIT = 10
SIMILAR_REASONS = ("phone", "email", "company")  # the order a reason is shown in


def similar_leads(own, by_phone, by_email, by_company, limit=SIMILAR_LIMIT):
	"""[{"name", "matched_on": [...]}] for the other Leads that share a phone, an email or the company name with `own`.

	Each argument after `own` is the list of Lead names found for that reason (in the order they were found). The Lead itself is
	never listed; a Lead found for several reasons is listed once with all of them and comes first; ties keep the order found."""
	reasons, order = {}, []
	for reason, names in (("phone", by_phone), ("email", by_email), ("company", by_company)):
		for name in names or []:
			if not name or name == own:
				continue
			if name not in reasons:
				reasons[name] = []
				order.append(name)
			if reason not in reasons[name]:
				reasons[name].append(reason)
	order.sort(key=lambda n: -len(reasons[n]))  # stable: equal counts keep the order found
	return [{"name": n, "matched_on": sorted(reasons[n], key=SIMILAR_REASONS.index)} for n in order[: max(1, int(limit))]]

