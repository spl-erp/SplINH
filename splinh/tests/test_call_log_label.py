import unittest
from unittest import mock

import frappe

from splinh.api import call_tracking as ct


def _doc(*links):
	return frappe._dict(links=[frappe._dict(link_doctype=d, link_name=n) for d, n in links])


class TestLinkedPartyLabel(unittest.TestCase):
	def _label(self, doc, lead_row=None, customer_name=None):
		def get_value(doctype, filters, fieldname=None, **kw):
			if doctype == "Lead":
				return frappe._dict(lead_row) if lead_row is not None else None
			if doctype == "Customer":
				return customer_name
			return None

		with mock.patch.object(ct.frappe, "db", mock.Mock(get_value=get_value)):
			return ct._resolve_linked_party(doc)

	def test_lead_label_uses_company_name(self):
		r = self._label(_doc(("Lead", "L1")), {"company_name": "Mystic Aromatics", "lead_name": "Mr Nivedan"})
		self.assertEqual(r["custom_linked_party"], "Lead: Mystic Aromatics")
		self.assertEqual(r["custom_lead"], "L1")

	def test_lead_label_falls_back_to_person_then_id(self):
		self.assertEqual(
			self._label(_doc(("Lead", "L1")), {"company_name": "  ", "lead_name": "Amit"})["custom_linked_party"],
			"Lead: Amit",
		)
		self.assertEqual(
			self._label(_doc(("Lead", "L1")), {"company_name": None, "lead_name": None})["custom_linked_party"],
			"Lead: L1",
		)

	def test_customer_label_unchanged(self):
		r = self._label(_doc(("Customer", "C1")), customer_name="TECHNO INFOSYS")
		self.assertEqual(r, {"customer": "C1", "custom_lead": None, "custom_linked_party": "Customer: TECHNO INFOSYS"})
