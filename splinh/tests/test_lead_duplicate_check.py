"""Run without a site:  python -m unittest splinh.tests.test_lead_duplicate_check"""

import unittest

from splinh.api import lead_duplicate_check as m


def guard_row(doctype, name, value_type, value, field="mobile_no"):
	return {
		"value_type": value_type,
		"value": value,
		"matched_doctype": doctype,
		"matched_name": name,
		"matched_field": field,
	}


def resolver(mapping=None, gone=()):
	"""Fake _resolve: `mapping` sends (doctype, name) to (doctype, name, via)."""
	mapping = mapping or {}

	def resolve(doctype, name):
		if (doctype, name) in gone:
			return None
		return mapping.get((doctype, name), (doctype, name, None))

	return resolve


class TestHits(unittest.TestCase):
	def test_guard_rows(self):
		hits = m.hits_from_guard([guard_row("Lead", "L1", "Phone", "+918103858929"), {"value_type": "Name"}])
		self.assertEqual(
			hits,
			[{"doctype": "Lead", "name": "L1", "matched_on": "Phone", "value": "+918103858929", "source": "Duplicate Guard"}],
		)
		self.assertEqual(m.hits_from_guard(None), [])

	def test_phone_lookup_rows(self):
		hits = m.hits_from_phone_lookup([{"doctype": "Contact", "name": "C1", "field": "phone"}], "+91810")
		self.assertEqual(hits[0]["matched_on"], "Phone")
		self.assertEqual(hits[0]["source"], "Phone Lookup")
		self.assertEqual(hits[0]["value"], "+91810")


class TestMergeHits(unittest.TestCase):
	def test_same_record_on_several_keys_is_one_row(self):
		hits = m.hits_from_guard(
			[
				guard_row("Lead", "L1", "Email", "a@b.com", "email_id"),
				guard_row("Lead", "L1", "Phone", "+918103858929"),
				guard_row("Lead", "L1", "Name", "acme", "company_name"),
			]
		) + m.hits_from_phone_lookup([{"doctype": "Lead", "name": "L1"}], "+918103858929")
		rows = m.merge_hits(hits, resolver())
		self.assertEqual(len(rows), 1)
		self.assertEqual(rows[0]["matched_on"], ["Name", "Phone", "Email"])
		self.assertEqual(rows[0]["sources"], ["Duplicate Guard", "Phone Lookup"])
		self.assertEqual(rows[0]["values"], ["a@b.com", "+918103858929", "acme"])

	def test_contact_and_its_customer_fold_together(self):
		hits = m.hits_from_guard([guard_row("Customer", "CUST1", "Name", "acme", "customer_name")])
		hits += m.hits_from_phone_lookup([{"doctype": "Contact", "name": "CON1"}], "+918103858929")
		resolve = resolver({("Contact", "CON1"): ("Customer", "CUST1", "via Contact CON1")})
		rows = m.merge_hits(hits, resolve)
		self.assertEqual(len(rows), 1)
		self.assertEqual((rows[0]["doctype"], rows[0]["name"]), ("Customer", "CUST1"))
		self.assertEqual(rows[0]["matched_on"], ["Name", "Phone"])
		self.assertEqual(rows[0]["via"], ["via Contact CON1"])

	def test_converted_lead_shows_as_customer(self):
		hits = m.hits_from_guard([guard_row("Lead", "L9", "Phone", "+911")])
		resolve = resolver({("Lead", "L9"): ("Customer", "CUST9", "converted from Lead L9")})
		rows = m.merge_hits(hits, resolve)
		self.assertEqual((rows[0]["doctype"], rows[0]["name"]), ("Customer", "CUST9"))

	def test_missing_records_are_dropped(self):
		hits = m.hits_from_guard([guard_row("Lead", "GONE", "Phone", "+911"), guard_row("Lead", "L2", "Phone", "+911")])
		rows = m.merge_hits(hits, resolver(gone={("Lead", "GONE")}))
		self.assertEqual([r["name"] for r in rows], ["L2"])

	def test_nothing_is_clean(self):
		self.assertEqual(m.merge_hits([], resolver()), [])

	def test_distinct_records_keep_first_hit_order(self):
		hits = m.hits_from_guard([guard_row("Lead", "L2", "Phone", "+911"), guard_row("Lead", "L1", "Email", "x@y.z")])
		self.assertEqual([r["name"] for r in m.merge_hits(hits, resolver())], ["L2", "L1"])


class TestPhoneKeyForOtherCountries(unittest.TestCase):
	# India / no country / "+..." go through phone_lookup._normalize (needs the site
	# config) and are covered by test_phone_normalize; only the explicit-country
	# branch is pure.
	def test_bare_number_uses_country_code(self):
		self.assertEqual(m.phone_key_for("515 021 333", "Poland", isd="+48"), "+48515021333")
		self.assertEqual(m.phone_key_for("(770) 664-4800", "United States", isd="+1"), "+17706644800")
		self.assertEqual(m.phone_key_for("0515021333", "Poland", isd="+48"), "+48515021333")

	def test_unusable_numbers(self):
		self.assertIsNone(m.phone_key_for("123", "Poland", isd="+48"))
		self.assertIsNone(m.phone_key_for("515021333", "Nowhere", isd=""))


class TestOptionalInputs(unittest.TestCase):
	"""Any one of Organisation / Phone / Email is enough; blank ones are not checked."""

	def test_provided_labels_skip_blanks(self):
		self.assertEqual(m.provided_labels("Acme", "8103858929", ""), ["Organisation", "Phone"])
		self.assertEqual(m.provided_labels("", "", "a@b.com"), ["Email"])
		self.assertEqual(m.provided_labels("  ", None, None), [])
		self.assertEqual(m.provided_labels("Acme", "81", "a@b.com"), ["Organisation", "Phone", "Email"])

	def test_provided_types_map_to_guard_types(self):
		self.assertEqual(m.provided_types("Acme", "8103858929", ""), ["Name", "Phone"])
		self.assertEqual(m.provided_types("", "", "a@b.com"), ["Email"])

	def test_guard_values_only_filled_fields(self):
		self.assertEqual(
			m.guard_values("Acme", "8103858929", "", "India"),
			{"company_name": "Acme", "mobile_no": "8103858929", "country": "India"},
		)
		self.assertEqual(m.guard_values("", "", "a@b.com"), {"email_id": "a@b.com"})
		self.assertEqual(m.guard_values(" Acme ", None, None), {"company_name": "Acme"})

	def test_nothing_filled_means_nothing_to_probe(self):
		self.assertEqual(m.guard_values("", "", "", "India"), {})
		self.assertEqual(m.guard_values(None, None, None), {})


if __name__ == "__main__":
	unittest.main()
