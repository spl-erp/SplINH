"""Run without a site:  python -m unittest splinh.tests.test_lead_merge"""

import unittest

from splinh.custom import lead_merge as m


def field(fieldname, fieldtype="Data", **kw):
	return {"fieldname": fieldname, "fieldtype": fieldtype, "label": fieldname.title(), **kw}


FIELDS = [
	field("naming_series", "Select"),
	{"fieldtype": "Section Break", "label": "Person"},
	field("company_name"),
	field("first_name"),
	field("lead_name", read_only=1),
	field("middle_name", hidden=1),
	field("email_id", options="Email"),
	field("custom_gstin"),
	field("custom_record_number", unique=1),
	field("mobile_no", "Data", options="Phone"),
	{"fieldtype": "Column Break"},
	{"fieldtype": "Section Break", "label": "Details"},
	field("custom_remark_by_bdm", "Small Text"),
	field("custom_old_crm_comments", "Markdown Editor"),
	field("custom_description", "Text Editor"),
	field("status", "Select"),
	field("territory", "Link", options="Territory"),
	field("custom_pincode"),
	field("special", "Data", permlevel=1),
	field("notes", "Table", options="CRM Note"),
	field("photo_preview", "Image"),
]
NAMES = ["L1", "L2", "L3"]


def docs(**per_lead):
	"""docs(L1={...}, L2={...}) -> {name: values}; missing leads are empty."""
	return {n: dict(per_lead.get(n, {})) for n in NAMES}


class TestOfferedFields(unittest.TestCase):
	def setUp(self):
		self.offered = m.offered_fields(FIELDS)
		self.by = {f["fieldname"]: f for f in self.offered}

	def test_skips_layout_tables_read_only_hidden_identifiers_and_higher_permlevels(self):
		for hidden in ("naming_series", "lead_name", "middle_name", "custom_record_number", "special", "notes", "photo_preview"):
			self.assertNotIn(hidden, self.by, hidden)
		self.assertEqual(
			[f["fieldname"] for f in self.offered],
			[
				"company_name",
				"first_name",
				"email_id",
				"custom_gstin",
				"mobile_no",
				"custom_remark_by_bdm",
				"custom_old_crm_comments",
				"custom_description",
				"status",
				"territory",
				"custom_pincode",
			],
		)

	def test_permlevel_is_offered_when_the_user_can_write_it(self):
		offered = m.offered_fields(FIELDS, write_levels=(0, 1))
		self.assertIn("special", [f["fieldname"] for f in offered])

	def test_section_headings_follow_the_form(self):
		self.assertEqual(self.by["company_name"]["section"], "Person")
		self.assertEqual(self.by["status"]["section"], "Details")

	def test_append_only_for_free_text(self):
		kinds = {f["fieldname"]: f["appendable"] for f in self.offered}
		self.assertEqual(kinds["company_name"], "single")
		self.assertEqual(kinds["first_name"], "single")
		self.assertEqual(kinds["custom_remark_by_bdm"], "multi")
		self.assertEqual(kinds["custom_old_crm_comments"], "multi")
		self.assertEqual(kinds["custom_description"], "html")
		for never in ("email_id", "custom_gstin", "mobile_no", "custom_pincode", "status", "territory"):
			self.assertIsNone(kinds[never], never)

	def test_owner_suffix_fields_are_never_appendable(self):
		self.assertIsNone(m.appendable_kind(field("custom_mobile_number_owner")))


class TestRows(unittest.TestCase):
	def test_differs_ignores_empty_and_spacing(self):
		offered = m.offered_fields(FIELDS)
		d = docs(
			L1={"company_name": "Acme  Ink", "first_name": None, "status": "Lead"},
			L2={"company_name": "Acme Ink", "first_name": "", "status": "Open"},
			L3={"company_name": "Acme Ink", "first_name": 0, "status": "Lead"},
		)
		by = {r["fieldname"]: r for r in m.build_rows(offered, d, NAMES)}
		self.assertFalse(by["company_name"]["differs"])
		self.assertFalse(by["first_name"]["differs"])
		self.assertTrue(by["status"]["differs"])
		self.assertEqual(by["status"]["values"], {"L1": "Lead", "L2": "Open", "L3": "Lead"})


class TestAppend(unittest.TestCase):
	def test_the_users_example_hi_hey(self):
		self.assertEqual(m.append_values("hi", ["hey"], "single"), "hi hey")

	def test_order_and_blank_base(self):
		self.assertEqual(m.append_values("a", ["b", "c"], "single"), "a b c")
		self.assertEqual(m.append_values("", ["b"], "single"), "b")
		self.assertEqual(m.append_values(None, [None, " ", "c"], "multi"), "c")

	def test_separators_by_kind(self):
		self.assertEqual(m.append_values("one", ["two"], "multi"), "one\ntwo")
		self.assertEqual(m.append_values("<p>one</p>", ["<p>two</p>"], "html"), "<p>one</p><div><br></div><p>two</p>")

	def test_same_text_is_not_repeated(self):
		self.assertEqual(m.append_values("Hi", ["hi", "HI "], "single"), "Hi")
		self.assertEqual(m.append_values("a", ["b", "b"], "single"), "a b")

	def test_a_word_inside_another_word_is_still_appended(self):
		self.assertEqual(m.append_values("Acme Printing", ["Print"], "single"), "Acme Printing Print")

	def test_trailing_space_of_base_does_not_double(self):
		self.assertEqual(m.append_values("hi  ", ["hey"], "single"), "hi hey")


class TestComputeUpdates(unittest.TestCase):
	def setUp(self):
		self.offered = m.offered_fields(FIELDS)
		self.docs = docs(
			L1={"company_name": "Acme", "first_name": "Ravi", "status": "Lead", "email_id": "a@x.com", "custom_remark_by_bdm": "call back"},
			L2={"company_name": "Acme Inks", "first_name": "", "status": "Open", "email_id": "b@x.com", "custom_remark_by_bdm": "sent price"},
			L3={"company_name": "ACME INKS PVT", "first_name": "Ravi K", "status": "Converted", "email_id": "", "custom_remark_by_bdm": ""},
		)

	def run_(self, choices, main="L1"):
		return m.compute_updates(self.offered, self.docs, NAMES, main, choices)

	def test_untouched_fields_keep_the_main_value_even_when_blank(self):
		self.assertEqual(self.run_({}), {})
		self.assertEqual(self.run_({}, main="L2"), {})  # L2's blank first_name stays blank

	def test_use_overwrites_with_the_chosen_leads_value(self):
		self.assertEqual(self.run_({"company_name": {"use": "L3"}}), {"company_name": "ACME INKS PVT"})

	def test_choosing_the_main_value_is_not_a_change(self):
		self.assertEqual(self.run_({"company_name": {"use": "L1"}}), {})

	def test_a_blank_value_can_be_chosen_to_clear_the_field(self):
		self.assertEqual(self.run_({"email_id": {"use": "L3"}}), {"email_id": ""})

	def test_append_adds_after_the_main_value(self):
		self.assertEqual(
			self.run_({"custom_remark_by_bdm": {"append": ["L2"]}}),
			{"custom_remark_by_bdm": "call back\nsent price"},
		)

	def test_append_follows_column_order_not_click_order(self):
		self.docs["L3"]["custom_remark_by_bdm"] = "third"
		out = self.run_({"custom_remark_by_bdm": {"append": ["L3", "L2"]}})
		self.assertEqual(out, {"custom_remark_by_bdm": "call back\nsent price\nthird"})

	def test_use_one_lead_and_append_another(self):
		out = self.run_({"company_name": {"use": "L2", "append": ["L3"]}})
		self.assertEqual(out, {"company_name": "Acme Inks ACME INKS PVT"})

	def test_append_of_a_blank_or_identical_value_changes_nothing(self):
		self.assertEqual(self.run_({"custom_remark_by_bdm": {"append": ["L3"]}}), {})
		self.docs["L2"]["company_name"] = "acme"
		self.assertEqual(self.run_({"company_name": {"append": ["L2"]}}), {})

	def test_append_refused_on_identifying_fields(self):
		for fn in ("email_id", "custom_gstin", "mobile_no", "status", "territory", "custom_pincode"):
			with self.assertRaises(m.MergeError) as ctx:
				self.run_({fn: {"append": ["L2"]}})
			self.assertEqual(ctx.exception.field, fn)

	def test_append_from_the_lead_that_is_used_or_unselected_is_refused(self):
		with self.assertRaises(m.MergeError):
			self.run_({"company_name": {"use": "L2", "append": ["L2"]}})
		with self.assertRaises(m.MergeError):
			self.run_({"company_name": {"append": ["L9"]}})

	def test_unknown_unselected_or_unoffered_fields_are_refused(self):
		for bad in ({"nope": {"use": "L2"}}, {"lead_name": {"use": "L2"}}, {"custom_record_number": {"use": "L2"}}, {"company_name": {"use": "L9"}}):
			with self.assertRaises(m.MergeError):
				self.run_(bad)

	def test_combined_one_line_text_over_the_limit_is_refused(self):
		self.docs["L1"]["company_name"] = "x" * 100
		self.docs["L2"]["company_name"] = "y" * 60
		with self.assertRaises(m.MergeError) as ctx:
			self.run_({"company_name": {"append": ["L2"]}})
		self.assertEqual(ctx.exception.field, "company_name")
		self.docs["L2"]["company_name"] = "y" * 39  # 100 + space + 39 = 140: allowed
		self.assertEqual(len(self.run_({"company_name": {"append": ["L2"]}})["company_name"]), 140)

	def test_main_can_be_any_lead(self):
		out = self.run_({"company_name": {"use": "L1"}}, main="L3")
		self.assertEqual(out, {"company_name": "Acme"})


class TestChildRows(unittest.TestCase):
	COLUMNS = {"notes": ["note", "added_by", "added_on"], "tags": ["tag"]}

	def test_columns_exclude_layout_and_standard(self):
		cols = m.child_columns(
			[field("note", "Text Editor"), field("added_by"), {"fieldtype": "Section Break"}, field("idx"), field("name")]
		)
		self.assertEqual(cols, ["note", "added_by"])

	def test_rows_of_the_other_leads_are_added_once(self):
		d = docs(
			L1={"notes": [{"note": "n1", "added_by": "a", "added_on": "t1", "name": "x1", "idx": 1}], "tags": [{"tag": "ink"}]},
			L2={"notes": [{"note": "n1", "added_by": "a", "added_on": "t1"}, {"note": "n2", "added_by": "b", "added_on": "t2"}], "tags": [{"tag": "ink"}, {"tag": "film"}]},
			L3={"notes": [{"note": "n2", "added_by": "b", "added_on": "t2"}, {"note": "n3", "added_by": "c", "added_on": "t3"}], "tags": []},
		)
		out = m.combine_child_rows(self.COLUMNS, d, NAMES, "L1")
		self.assertEqual(
			out["notes"],
			[{"note": "n2", "added_by": "b", "added_on": "t2"}, {"note": "n3", "added_by": "c", "added_on": "t3"}],
		)
		self.assertEqual(out["tags"], [{"tag": "film"}])

	def test_empty_rows_and_empty_tables_add_nothing(self):
		d = docs(L1={"notes": []}, L2={"notes": [{"note": "", "added_by": None}]}, L3={})
		self.assertEqual(m.combine_child_rows(self.COLUMNS, d, NAMES, "L1"), {})

	def test_main_can_be_the_one_without_rows(self):
		d = docs(L1={}, L2={"tags": [{"tag": "ink"}]}, L3={"tags": [{"tag": "ink"}]})
		self.assertEqual(m.combine_child_rows(self.COLUMNS, d, NAMES, "L1"), {"tags": [{"tag": "ink"}]})


class TestSelection(unittest.TestCase):
	def test_two_to_four_unique_with_main(self):
		self.assertEqual(m.validate_selection(["A", "B"], "A"), ["A", "B"])
		self.assertEqual(len(m.validate_selection(["A", "B", "C", "D"], "D")), 4)

	def test_refused(self):
		for names, main in ((["A"], "A"), (["A", "B", "C", "D", "E"], "A"), (["A", "A"], "A"), (["A", "B"], "Z"), ([], "A"), (None, "A")):
			with self.assertRaises(m.MergeError):
				m.validate_selection(names, main)


class TestStamps(unittest.TestCase):
	def test_unchanged_passes(self):
		m.check_stamps(["A", "B"], {"A": "t1", "B": "t2"}, {"A": "t1", "B": "t2"})

	def test_changed_missing_or_vanished_is_refused(self):
		for stamps, current in (
			({"A": "t1", "B": "t2"}, {"A": "t1", "B": "t3"}),
			({"A": "t1"}, {"A": "t1", "B": "t2"}),
			({"A": "t1", "B": "t2"}, {"A": "t1"}),
		):
			with self.assertRaises(m.MergeError):
				m.check_stamps(["A", "B"], stamps, current)


class TestSuggestMain(unittest.TestCase):
	def test_most_history_then_oldest(self):
		leads = [
			{"name": "A", "creation": "2026-02-01", "weight": 3},
			{"name": "B", "creation": "2026-03-01", "weight": 9},
			{"name": "C", "creation": "2026-01-01", "weight": 9},
		]
		self.assertEqual(m.suggest_main(leads), "C")


if __name__ == "__main__":
	unittest.main()


class TestMergeKeepsWhatFrappeDoesNotMove(unittest.TestCase):
	def test_rename_args_with_and_without_the_method_name(self):
		self.assertEqual(m.rename_args(("A", "B", True)), ("A", "B", True))
		self.assertEqual(m.rename_args(("after_rename", "A", "B", False)), ("A", "B", False))
		self.assertEqual(m.rename_args(("before_rename", "A", "B", 1)), ("A", "B", True))
		for args in ((), ("A",), ("after_rename", "A"), ("", "B"), None):
			self.assertIsNone(m.rename_args(args))

	def test_member_plan(self):
		self.assertEqual(m.member_plan([{"name": "r1", "party": "L1"}, {"name": "r2", "party": "L9"}], "L1", "L2"), (["r1"], []))
		self.assertEqual(m.member_plan([{"name": "r1", "party": "L1"}, {"name": "r2", "party": "L2"}], "L1", "L2"), ([], ["r1"]))
		self.assertEqual(m.member_plan([{"name": "r2", "party": "L2"}], "L1", "L2"), ([], []))
		self.assertEqual(m.member_plan([{"name": "r1", "party": "L1"}, {"name": "r3", "party": "L1"}], "L1", "L2"), (["r1"], ["r3"]))

	def test_todo_note_keeps_every_text_and_escapes_it(self):
		text = m.todo_note("CRM-LEAD-1", [{"description": "Call <b>Asha</b> back", "allocated_to": "a@x.com", "status": "Open", "date": "2026-11-20"}, {"description": "", "status": "Open"}])
		self.assertIn("CRM-LEAD-1", text)
		self.assertIn("Call &lt;b&gt;Asha&lt;/b&gt; back", text)
		self.assertNotIn("<b>Asha</b>", text)
		self.assertIn("a@x.com, Open, 2026-11-20", text)
		self.assertIn("(no text)", text)
		self.assertEqual(text.count("<li>"), 2)

	def test_hooks_and_module_are_wired_and_safe(self):
		import os

		base = os.path.join(os.path.dirname(__file__), "..")
		hooks = open(os.path.join(base, "hooks.py"), encoding="utf-8").read()
		self.assertIn('"before_rename": "splinh.custom.lead_merge_links.before_rename"', hooks)
		self.assertIn('"after_rename": "splinh.custom.lead_merge_links.after_rename"', hooks)
		src = open(os.path.join(base, "custom", "lead_merge_links.py"), encoding="utf-8").read()
		self.assertEqual(src.count("frappe.log_error("), 2)  # a failure is logged, never raised into the merge
		self.assertNotIn("frappe.throw", src)
		self.assertIn('frappe.db.table_exists("Waba Conversation")', src)  # production may not have Waba: every Waba step is guarded


class TestSimilarLeads(unittest.TestCase):
	def test_the_lead_itself_is_never_listed(self):
		self.assertEqual(m.similar_leads("L1", ["L1", "L2"], ["L1"], ["L1"]), [{"name": "L2", "matched_on": ["phone"]}])

	def test_a_lead_found_for_several_reasons_is_listed_once_and_first(self):
		out = m.similar_leads("L1", ["L2", "L3"], ["L3"], ["L4", "L3"])
		self.assertEqual([x["name"] for x in out], ["L3", "L2", "L4"])
		self.assertEqual(out[0]["matched_on"], ["phone", "email", "company"])
		self.assertEqual(out[2]["matched_on"], ["company"])

	def test_equal_matches_keep_the_order_found_and_the_list_is_capped(self):
		names = [f"L{i}" for i in range(2, 30)]
		out = m.similar_leads("L1", names, [], [], limit=10)
		self.assertEqual([x["name"] for x in out], names[:10])

	def test_nothing_found(self):
		self.assertEqual(m.similar_leads("L1", [], None, [""]), [])


class TestMergeFromOtherPlaces(unittest.TestCase):
	def setUp(self):
		import os

		self.base = os.path.join(os.path.dirname(__file__), "..")
		self.read = lambda *p: open(os.path.join(self.base, *p), encoding="utf-8").read()

	def test_scripts_parse(self):
		import shutil
		import subprocess

		node = shutil.which("node")
		if not node:
			self.skipTest("node is not installed")
		for name in ("lead_merge_list.js", "lead_merge_form.js"):
			result = subprocess.run([node, "--check", f"{self.base}/custom/{name}"], capture_output=True, text=True)
			self.assertEqual(result.returncode, 0, result.stderr)

	def test_the_dialog_is_one_shared_entry_point(self):
		js = self.read("custom", "lead_merge_list.js")
		self.assertIn("window.splinh_lead_merge", js)
		self.assertIn("function open_names(names, opts)", js)
		self.assertIn("opts.main && preview.names.includes(opts.main)", js)  # the Lead form suggests itself as the main Lead
		self.assertIn("splinh.api.lead_merge.get_merge_preview", js)

	def test_the_banner_uses_the_real_api_and_escapes_what_it_shows(self):
		js = self.read("custom", "lead_merge_form.js")
		self.assertIn("splinh.api.lead_duplicates.similar_leads", js)
		self.assertIn("window.splinh_lead_merge.open(names, {main: frm.doc.name})", js)
		self.assertIn("esc(m.title)", js)
		self.assertIn("frm.is_dirty()", js)  # never merges unsaved edits
		self.assertIn("!frm.perm[0].write", js)  # only for people who can edit the Lead

	def test_the_api_is_read_only_and_checks_permissions(self):
		src = self.read("api", "lead_duplicates.py")
		self.assertIn('frappe.has_permission("Lead", "read", doc=lead, throw=True)', src)
		self.assertIn('frappe.has_permission("Lead", "read", doc=item["name"])', src)  # SFA visibility per Lead
		self.assertIn("tabDuplicate Index", src)  # company names go through the guard's index: Lead.company_name is not indexed
		self.assertNotIn("lower(trim(company_name))", src)
		for word in (".insert(", ".save(", "db.set_value", "db.delete", "frappe.delete_doc"):
			self.assertNotIn(word, src)

	def test_duplicate_check_offers_merge_only_for_two_or_more_leads_and_only_when_the_dialog_exists(self):
		setup = self.read("custom", "splinh_setup.py")
		self.assertIn('lead_names.length >= 2 && window.splinh_lead_merge', setup)
		self.assertIn('m.doctype === "Lead"', setup)
		self.assertIn(".slice(0, 4)", setup)

	def test_both_client_scripts_are_created_from_the_same_dialog(self):
		setup = self.read("custom", "splinh_setup.py")
		self.assertIn('LEAD_MERGE_FORM_SCRIPT = "Lead Form Merge Leads"', setup)
		self.assertIn('"view": "Form", "enabled": 1, "script": dialog +', setup)
		self.assertIn('"view": "List", "enabled": 1, "script": dialog}', setup)
