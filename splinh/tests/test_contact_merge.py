"""Run without a site:  python -m unittest splinh.tests.test_contact_merge"""

import datetime
import os
import unittest

from splinh.custom import contact_merge as m

APP = os.path.join(os.path.dirname(__file__), "..")


def read(*parts):
	with open(os.path.join(APP, *parts), encoding="utf-8") as handle:
		return handle.read()


def field(fieldname, fieldtype="Data", **kw):
	return {"fieldname": fieldname, "fieldtype": fieldtype, "label": fieldname.title(), **kw}


FIELDS = [
	{"fieldtype": "Section Break", "label": "Person"},
	field("first_name"),
	field("last_name"),
	field("full_name", read_only=1, hidden=1),
	field("email_id", options="Email", read_only=1),
	field("user", "Link", options="User"),
	field("sync_with_google_contacts", "Check"),
	field("google_contacts_id", read_only=1),
	{"fieldtype": "Section Break", "label": "Details"},
	field("status", "Select"),
	field("designation"),
	field("department"),
	field("is_billing_contact", "Check"),
	field("unsubscribed", "Check"),
	field("waba_consent", "Select", read_only=1),
	field("secret", "Data", permlevel=1),
	field("phone_nos", "Table", options="Contact Phone"),
	field("image", "Attach Image", hidden=1),
]
NAMES = ["C1", "C2", "C3"]


def docs(**per):
	return {n: dict(per.get(n, {})) for n in NAMES}


class TestOfferedFields(unittest.TestCase):
	def test_only_editable_values_are_offered_in_form_order_with_their_section(self):
		got = m.offered_fields(FIELDS)
		self.assertEqual([f["fieldname"] for f in got], ["first_name", "last_name", "user", "status", "designation", "department", "is_billing_contact", "unsubscribed"])
		self.assertEqual({f["section"] for f in got if f["fieldname"] == "first_name"}, {"Person"})

	def test_nothing_is_appendable_on_a_contact(self):
		self.assertTrue(all(f["appendable"] is None for f in m.offered_fields(FIELDS)))

	def test_google_fields_read_only_and_hidden_ones_are_left_out(self):
		names = {f["fieldname"] for f in m.offered_fields(FIELDS)}
		for gone in ("sync_with_google_contacts", "google_contacts_id", "full_name", "email_id", "waba_consent", "image", "phone_nos", "secret"):
			self.assertNotIn(gone, names)

	def test_a_higher_permission_level_is_offered_only_when_writable(self):
		self.assertIn("secret", {f["fieldname"] for f in m.offered_fields(FIELDS, write_levels=(0, 1))})


class TestChoices(unittest.TestCase):
	def test_a_field_not_touched_keeps_the_main_contacts_value(self):
		offered = m.offered_fields(FIELDS)
		self.assertEqual(m.compute_updates(offered, docs(C1={"designation": "Buyer"}, C2={"designation": "Owner"}), NAMES, "C1", {}), {})

	def test_use_takes_another_contacts_value(self):
		offered = m.offered_fields(FIELDS)
		got = m.compute_updates(offered, docs(C1={"designation": "Buyer"}, C2={"designation": "Owner"}), NAMES, "C1", {"designation": {"use": "C2"}})
		self.assertEqual(got, {"designation": "Owner"})

	def test_append_is_refused_for_every_field(self):
		offered = m.offered_fields(FIELDS)
		with self.assertRaises(m.MergeError):
			m.compute_updates(offered, docs(C1={"designation": "a"}, C2={"designation": "b"}), NAMES, "C1", {"designation": {"use": "C1", "append": ["C2"]}})

	def test_the_portal_login_is_chosen_like_any_other_field(self):
		offered = m.offered_fields(FIELDS)
		d = docs(C1={"user": "a@x"}, C2={"user": "b@x"})
		self.assertEqual(m.compute_updates(offered, d, NAMES, "C1", {"user": {"use": "C2"}}), {"user": "b@x"})
		self.assertEqual(m.compute_updates(offered, d, NAMES, "C1", {}), {})


class TestSelection(unittest.TestCase):
	def test_two_to_four_different_contacts_including_the_main(self):
		self.assertEqual(m.validate_selection(["C1", "C2"], "C1"), ["C1", "C2"])
		for bad in (["C1"], ["C1", "C1"], ["C1", "C2", "C3", "C4", "C5"], []):
			with self.assertRaises(m.MergeError):
				m.validate_selection(bad, "C1")
		with self.assertRaises(m.MergeError):
			m.validate_selection(["C1", "C2"], "C9")

	def test_messages_say_contact(self):
		with self.assertRaises(m.MergeError) as e:
			m.validate_selection(["C1"], "C1")
		self.assertIn("Contacts", e.exception.message)
		with self.assertRaises(m.MergeError) as e:
			m.check_stamps(["C1"], {"C1": "a"}, {"C1": "b"})
		self.assertIn("Contact C1 was changed", e.exception.message)
		self.assertNotIn("Lead", e.exception.message)

	def test_stamps_must_match(self):
		m.check_stamps(["C1", "C2"], {"C1": "t1", "C2": "t2"}, {"C1": "t1", "C2": "t2"})


class TestDuplicateGate(unittest.TestCase):
	def gate(self, **contacts):
		return m.duplicate_check(contacts)

	def test_a_shared_phone_in_different_formats_is_a_duplicate(self):
		got = self.gate(C1={"phones": ["+91-98765 43210"]}, C2={"phones": ["9876543210"]})
		self.assertTrue(got["ok"])
		self.assertEqual(got["pairs"], [{"a": "C1", "b": "C2", "on": ["phone"]}])

	def test_a_shared_email_in_any_case_is_a_duplicate(self):
		self.assertTrue(self.gate(C1={"emails": ["Rahul@Example.com"]}, C2={"emails": ["rahul@example.com"]})["ok"])

	def test_both_phone_and_email_are_reported(self):
		got = self.gate(C1={"phones": ["9876543210"], "emails": ["a@x.com"]}, C2={"phones": ["9876543210"], "emails": ["a@x.com"]})
		self.assertEqual(got["pairs"][0]["on"], ["phone", "email"])

	def test_a_chain_is_one_group(self):
		got = self.gate(C1={"phones": ["9876543210"]}, C2={"phones": ["9876543210"], "emails": ["b@x.com"]}, C3={"emails": ["B@x.com"]})
		self.assertTrue(got["ok"])
		self.assertEqual(len(got["pairs"]), 2)

	def test_a_contact_with_nothing_in_common_refuses_and_is_named(self):
		got = self.gate(C1={"phones": ["9876543210"]}, C2={"phones": ["9876543210"]}, C3={"phones": ["9000000001"]})
		self.assertFalse(got["ok"])
		self.assertEqual(got["unmatched"], ["C3"])
		self.assertIn("C3", got["reason"])

	def test_no_phone_and_no_email_at_all_refuses(self):
		got = self.gate(C1={}, C2={})
		self.assertFalse(got["ok"])
		self.assertIn("share no phone number and no email", got["reason"])

	def test_same_name_alone_is_not_enough(self):
		self.assertFalse(self.gate(C1={"phones": ["9111111111"]}, C2={"phones": ["9222222222"]})["ok"])

	def test_two_separate_pairs_are_two_groups_and_refuse(self):
		got = self.gate(C1={"phones": ["9111111111"]}, C2={"phones": ["9111111111"]}, C3={"emails": ["z@x.com"]}, C4={"emails": ["z@x.com"]})
		self.assertFalse(got["ok"])
		self.assertIn("separate groups", got["reason"])
		self.assertEqual(got["unmatched"], [])

	def test_junk_numbers_and_emails_do_not_match(self):
		self.assertFalse(self.gate(C1={"phones": ["123"], "emails": ["nope"]}, C2={"phones": ["123"], "emails": ["nope"]})["ok"])

	def test_a_custom_normalizer_is_used(self):
		got = m.duplicate_check({"C1": {"phones": ["a"]}, "C2": {"phones": ["b"]}}, normalize=lambda raw: "+91" if raw in ("a", "b") else None)
		self.assertTrue(got["ok"])


class TestParties(unittest.TestCase):
	def test_parties_are_listed_once(self):
		links = [{"link_doctype": "Customer", "link_name": "C1"}, {"link_doctype": "Customer", "link_name": "C1"}, {"link_doctype": "Lead", "link_name": "L9"}]
		self.assertEqual(m.parties_of(links), ["Customer C1", "Lead L9"])

	def test_the_same_parties_give_no_warning(self):
		self.assertFalse(m.cross_party_note({"A": ["Customer C1"], "B": ["Customer C1"]})["different"])

	def test_different_parties_warn_and_name_them_all(self):
		got = m.cross_party_note({"A": ["Customer C1"], "B": ["Lead L1"]})
		self.assertTrue(got["different"])
		self.assertEqual(got["all"], ["Customer C1", "Lead L1"])
		self.assertIn("linked to all of them", got["note"])
		self.assertIn("A: Customer C1", got["note"])

	def test_portal_logins(self):
		self.assertFalse(m.user_conflict({"A": "u@x", "B": "u@x", "C": None})["conflict"])
		got = m.user_conflict({"A": "u@x", "B": "v@x"})
		self.assertTrue(got["conflict"])
		self.assertEqual(got["users"], ["u@x", "v@x"])


class TestConsent(unittest.TestCase):
	def test_a_no_beats_everything_and_unknown_beats_yes(self):
		d = datetime.date
		self.assertEqual(m.restrictive_consent([{"value": "Yes", "on": d(2026, 1, 1)}, {"value": "No", "on": d(2025, 1, 1)}])["value"], "No")
		self.assertEqual(m.restrictive_consent([{"value": "Yes"}, {"value": "Unknown"}])["value"], "Unknown")
		self.assertEqual(m.restrictive_consent([{"value": "Yes"}, {"value": "Yes"}])["value"], "Yes")

	def test_on_a_tie_the_newest_date_wins(self):
		d = datetime.date
		got = m.restrictive_consent([{"value": "No", "on": d(2025, 1, 1), "source": "old"}, {"value": "No", "on": d(2026, 1, 1), "source": "new"}])
		self.assertEqual(got["source"], "new")

	def test_nothing_in_nothing_out(self):
		self.assertIsNone(m.restrictive_consent([]))
		self.assertIsNone(m.restrictive_consent([None]))


class TestCombinePhones(unittest.TestCase):
	def test_a_number_the_main_contact_has_is_not_added_again_whatever_its_format(self):
		main = [{"phone": "+91-9876543210", "is_primary_phone": 1, "is_primary_mobile_no": 1}]
		other = [{"phone": "98765 43210"}, {"phone": "9000000001"}]
		got = m.combine_phones(main, other)
		self.assertEqual([r["phone"] for r in got], ["9000000001"])

	def test_the_main_contacts_primaries_always_win(self):
		main = [{"phone": "9111111111", "is_primary_phone": 1, "is_primary_mobile_no": 1}]
		other = [{"phone": "9222222222", "is_primary_phone": 1, "is_primary_mobile_no": 1}]
		got = m.combine_phones(main, other)
		self.assertEqual((got[0]["is_primary_phone"], got[0]["is_primary_mobile_no"]), (0, 0))

	def test_without_a_primary_the_first_added_one_becomes_it_and_only_one(self):
		other = [{"phone": "9222222222", "is_primary_phone": 1, "is_primary_mobile_no": 0}, {"phone": "9333333333", "is_primary_phone": 1, "is_primary_mobile_no": 1}, {"phone": "9444444444", "is_primary_mobile_no": 1}]
		got = m.combine_phones([{"phone": "9111111111"}], other)
		self.assertEqual(sum(r["is_primary_phone"] for r in got), 1)
		self.assertEqual(sum(r["is_primary_mobile_no"] for r in got), 1)
		self.assertEqual(got[0]["is_primary_phone"], 1)
		self.assertEqual(got[1]["is_primary_mobile_no"], 1)

	def test_the_caller_can_give_an_added_number_the_shape_the_phone_field_accepts(self):
		fmt = lambda raw, key: raw if raw.startswith("+") else "+91-" + key[-10:]  # noqa: E731
		got = m.combine_phones([], [{"phone": "9000098113"}, {"phone": "+91-9000098114"}], normalize=lambda raw: "+91" + "".join(c for c in raw if c.isdigit())[-10:], fmt=fmt)
		self.assertEqual([r["phone"] for r in got], ["+91-9000098113", "+91-9000098114"])

	def test_without_a_formatter_the_text_is_kept_as_typed(self):
		self.assertEqual([r["phone"] for r in m.combine_phones([], [{"phone": "9000098113"}])], ["9000098113"])

	def test_the_same_number_on_two_other_contacts_is_added_once_and_blanks_and_extra_columns_are_handled(self):
		got = m.combine_phones([], [{"phone": "9222222222", "custom_last10": "9222222222", "name": "x", "idx": 3}, {"phone": "+91 92222 22222"}, {"phone": "  "}])
		self.assertEqual(len(got), 1)
		self.assertEqual(got[0]["custom_last10"], "9222222222")
		self.assertNotIn("name", got[0])


class TestCombineEmailsAndLinks(unittest.TestCase):
	def test_emails_are_deduplicated_in_any_case_and_have_one_primary(self):
		main = [{"email_id": "a@x.com", "is_primary": 1}]
		got = m.combine_emails(main, [{"email_id": "A@X.com", "is_primary": 1}, {"email_id": "b@x.com", "is_primary": 1}, {"email_id": "c@x.com"}, {"email_id": "not-an-email"}])
		self.assertEqual([r["email_id"] for r in got], ["b@x.com", "c@x.com"])
		self.assertEqual([r["is_primary"] for r in got], [0, 0])

	def test_without_a_primary_the_first_primary_found_wins(self):
		got = m.combine_emails([{"email_id": "a@x.com"}], [{"email_id": "b@x.com", "is_primary": 1}, {"email_id": "c@x.com", "is_primary": 1}])
		self.assertEqual([r["is_primary"] for r in got], [1, 0])

	def test_party_links_are_united_once(self):
		main = [{"link_doctype": "Lead", "link_name": "L1"}]
		other = [{"link_doctype": "Lead", "link_name": "L1"}, {"link_doctype": "Customer", "link_name": "C9", "link_title": "x"}, {"link_doctype": "Customer", "link_name": "C9"}, {"link_doctype": "", "link_name": "z"}]
		self.assertEqual(m.combine_links(main, other), [{"link_doctype": "Customer", "link_name": "C9"}])


class TestNamesAndCopies(unittest.TestCase):
	def test_the_name_follows_the_autoname_rule(self):
		self.assertEqual(m.contact_name("Rahul Sharma", "C0099"), "Rahul Sharma-C0099")
		self.assertEqual(m.contact_name("Rahul Sharma", None), "Rahul Sharma")
		self.assertEqual(m.contact_name(" Rahul ", " CRM-LEAD-1 "), "Rahul-CRM-LEAD-1")

	def test_a_customer_copy_is_refreshed_only_where_it_differs(self):
		current = {"mobile_no": "+91-111", "email_id": "a@x.com", "first_name": "Rahul", "last_name": ""}
		contact = {"mobile_no": "+91-222", "email_id": "a@x.com", "first_name": "Rahul", "last_name": None}
		self.assertEqual(m.copy_changes(current, contact), {"mobile_no": "+91-222"})
		self.assertEqual(m.copy_changes(current, current), {})

	def test_the_task_note_keeps_the_text_and_escapes_it(self):
		note = m.todo_note("C2", [{"description": "Call <b>back</b>", "allocated_to": "u@x", "status": "Open", "date": datetime.date(2026, 11, 20)}])
		self.assertIn("Tasks of the merged Contact C2", note)
		self.assertIn("Call &lt;b&gt;back&lt;/b&gt;", note)
		self.assertIn("2026-11-20", note)
		self.assertNotIn("Lead", note)


class TestSuggestMain(unittest.TestCase):
	def test_the_heaviest_then_the_oldest_wins(self):
		cs = [{"name": "A", "creation": "2026-01-02", "weight": 3}, {"name": "B", "creation": "2026-01-01", "weight": 3}, {"name": "C", "creation": "2025-01-01", "weight": 1}]
		self.assertEqual(m.suggest_main(cs), "B")


class TestSimilar(unittest.TestCase):
	def test_both_reasons_first_then_same_party_the_contact_itself_never(self):
		got = m.similar_contacts("A", ["A", "B", "C"], ["C", "D"], same_party={"B"})
		self.assertEqual([g["name"] for g in got], ["C", "B", "D"])
		self.assertEqual(got[0]["matched_on"], ["phone", "email"])
		self.assertTrue(got[1]["same_party"])

	def test_the_list_is_limited(self):
		names = ["N{0}".format(i) for i in range(30)]
		self.assertEqual(len(m.similar_contacts("X", names, [], limit=5)), 5)
		self.assertEqual(m.similar_contacts("X", [], []), [])


class TestSharedRules(unittest.TestCase):
	def test_the_generic_waba_and_rename_rules_are_the_lead_ones(self):
		from splinh.custom import lead_merge as lm

		self.assertIs(m.member_plan, lm.member_plan)
		self.assertIs(m.rename_args, lm.rename_args)
		self.assertEqual(m.rename_args(("before_rename", "A", "B", True)), ("A", "B", True))


class TestWiring(unittest.TestCase):
	API = read("api", "contact_merge.py")
	MERGE = API.split("def merge_contacts(")[1].split("\ndef _refresh_copies")[0]

	def test_the_merge_is_post_only_and_the_preview_and_banner_are_read_only(self):
		self.assertIn('@frappe.whitelist(methods=["POST"])\ndef get_merge_preview(', self.API)
		self.assertIn('@frappe.whitelist(methods=["POST"])\ndef merge_contacts(', self.API)
		self.assertIn('@frappe.whitelist(methods=["GET", "POST"])\ndef similar_contacts(', self.API)
		preview = self.API.split("def get_merge_preview(")[1].split("\ndef _consent_entry")[0]
		for write in ("insert(", ".save(", "rename_doc(", "set_value(", "db.delete"):
			self.assertNotIn(write, preview, write)

	def test_the_order_of_a_merge_is_check_lock_gate_log_merge_save_refresh_rename(self):
		order = ["_check_permissions(names)", "for update", "rules.check_stamps(", "rules.duplicate_check(", "if not gate[\"ok\"]", "\"doctype\": LOG", "rename_doc(CONTACT, other, main, merge=True", "main_doc.save()", "_refresh_copies(main_doc)", "_rename_to_match(main_doc)"]
		last = -1
		for needle in order:
			at = self.MERGE.index(needle)
			self.assertGreater(at, last, needle)
			last = at

	def test_the_merge_never_rebuilds_global_search_and_checks_write_permission_first(self):
		self.assertIn("rebuild_search=False", self.MERGE)
		self.assertIn("merge=True", self.MERGE)
		self.assertIn('frappe.has_permission(CONTACT, "write", doc=n, throw=True)', self.API)

	def test_the_other_contacts_rows_are_read_before_the_merge_and_saved_after_it(self):
		self.assertLess(self.MERGE.index("add_phones = rules.combine_phones("), self.MERGE.index("rename_doc(CONTACT, other"))
		self.assertGreater(self.MERGE.index("main_doc.append(\"phone_nos\""), self.MERGE.index("rename_doc(CONTACT, other"))
		self.assertGreater(self.MERGE.index("main_doc.append(\"links\""), self.MERGE.index("rename_doc(CONTACT, other"))

	def test_the_rename_is_best_effort_in_a_savepoint_and_never_merges_into_another_contact(self):
		body = self.API.split("def _rename_to_match(")[1].split("\n@frappe.whitelist")[0]
		self.assertIn("savepoint", body)
		self.assertIn("rollback(save_point", body)
		self.assertIn("merge=False", body)
		self.assertIn("frappe.db.exists(CONTACT, wanted)", body)

	def test_customer_and_supplier_copies_are_refreshed_without_a_full_save(self):
		body = self.API.split("def _refresh_copies(")[1].split("\ndef _rename_to_match")[0]
		self.assertIn("customer_primary_contact", body)
		self.assertIn("supplier_primary_contact", body)
		self.assertIn("frappe.db.set_value(", body)
		self.assertNotIn(".save(", body)

	def test_the_hooks_and_the_installer_are_registered(self):
		hooks = read("hooks.py")
		self.assertIn('"before_rename": "splinh.custom.contact_merge_links.before_rename"', hooks)
		self.assertIn('"after_rename": "splinh.custom.contact_merge_links.after_rename"', hooks)
		self.assertIn('"before_rename": "splinh.custom.lead_merge_links.before_rename"', hooks)  # Lead Merge is untouched
		setup = read("custom", "splinh_setup.py")
		self.assertIn("_ensure_contact_merge,", setup)
		self.assertIn('"Contact List Merge Contacts"', setup)
		self.assertIn('"Contact Form Merge Contacts"', setup)

	def test_the_waba_repair_serves_both_doctypes_and_lead_is_the_default(self):
		links = read("custom", "lead_merge_links.py")
		self.assertIn('def _waba(old, new, doctype="Lead"):', links)
		self.assertIn("_waba(old, new)\n", links)  # the Lead hook still calls it as before
		contact = read("custom", "contact_merge_links.py")
		self.assertIn('_waba(old, new, "Contact")', contact)
		self.assertIn("Phone Lookup", contact)

	def test_the_log_doctype_exists_with_the_contact_field_names(self):
		import json

		d = json.loads(read("splinh", "doctype", "contact_merge_log", "contact_merge_log.json"))
		self.assertEqual(d["name"], "Contact Merge Log")
		self.assertEqual([f["fieldname"] for f in d["fields"] if f["fieldname"] in ("main_contact", "merged_contacts", "snapshot")], ["main_contact", "merged_contacts", "snapshot"])
		self.assertEqual([p["role"] for p in d["permissions"]], ["System Manager"])

	def test_the_scripts_install_one_global_and_syntax_check(self):
		import shutil
		import subprocess

		for name in ("contact_merge_list.js", "contact_merge_form.js"):
			src = read("custom", name)
			self.assertIn("window.splinh_contact_merge", read("custom", "contact_merge_list.js"))
			self.assertNotIn("splinh_lead_merge", src)
			if shutil.which("node"):
				subprocess.run(["node", "--check", os.path.join(APP, "custom", name)], check=True)
		self.assertIn("splinh.api.contact_merge.get_merge_preview", read("custom", "contact_merge_list.js"))
		self.assertIn("splinh.api.contact_merge.merge_contacts", read("custom", "contact_merge_list.js"))
		self.assertIn("splinh.api.contact_merge.similar_contacts", read("custom", "contact_merge_form.js"))

	def test_the_dialog_refuses_what_is_not_a_duplicate_and_warns_about_different_parties(self):
		js = read("custom", "contact_merge_list.js")
		self.assertIn("if (!dup.ok)", js)
		self.assertIn('prop("disabled", true)', js)
		self.assertIn("cross_party.different", js)
		self.assertIn("preview.logins.conflict", js)
		self.assertIn("unsubscribed_differs", js)


if __name__ == "__main__":
	unittest.main()
