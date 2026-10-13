import unittest

from splinh.custom import lead_customer_map as m


def lead(name="1", lead_name="Acme", company="Acme Pvt Ltd", title="Acme Pvt Ltd", status="Lead"):
	return {"name": name, "lead_name": lead_name, "company_name": company, "title": title, "status": status}


def customer(name="C000001", customer_name="Acme Pvt Ltd", city="", lead_name=None):
	return {"name": name, "customer_name": customer_name, "custom_customer_name_city": city, "lead_name": lead_name, "disabled": 0}


class TestNormalise(unittest.TestCase):
	def test_codes(self):
		self.assertEqual(m.normalise_code(" C009875  "), "C009875")
		self.assertEqual(m.normalise_code("c010207"), "C010207")
		self.assertEqual(m.normalise_code("C 010937"), "C010937")
		self.assertEqual(m.normalise_code(None), "")

	def test_ids(self):
		self.assertEqual(m.normalise_id(122), "122")
		self.assertEqual(m.normalise_id(122.0), "122")
		self.assertEqual(m.normalise_id(" 122 "), "122")
		self.assertEqual(m.normalise_id("abc"), "")
		self.assertEqual(m.normalise_id(None), "")


class TestNameMatch(unittest.TestCase):
	def test_levels(self):
		self.assertEqual(m.name_match(["Prasad Color Lab"], ["PRASAD  COLOR LAB"]), "exact")
		self.assertEqual(m.name_match(["Sri Gowri"], ["Sri Gowri Print House"]), "partial")
		self.assertEqual(m.name_match(["Noor SSC"], ["OTHMAN ABDULRAHMAN"]), "different")
		self.assertEqual(m.name_match([None, ""], ["Acme Pvt Ltd"]), "different")


class TestClassifyPair(unittest.TestCase):
	def test_phone_overlap_confirms_even_if_names_differ(self):
		cls = m.classify_pair(lead(company="Other"), customer(), {"+911"}, {"+911", "+912"})
		self.assertEqual(cls[0], "CONFIRMED")
		self.assertEqual(cls[3], "yes")

	def test_both_phones_but_disjoint_is_review(self):
		self.assertEqual(m.classify_pair(lead(), customer(), {"+911"}, {"+912"})[0], "REVIEW")

	def test_no_phone_names_agree(self):
		self.assertEqual(m.classify_pair(lead(), customer(), set(), {"+912"})[0], "ID_ONLY_NAME_OK")
		self.assertEqual(m.classify_pair(lead(), customer(), set(), set())[3], "n/a")

	def test_no_phone_names_differ_is_review(self):
		self.assertEqual(m.classify_pair(lead(company="Zed", lead_name="Zed", title="Zed"), customer(), set(), set())[0], "REVIEW")


class TestDuplicates(unittest.TestCase):
	def row(self, lead_id, cls):
		return {"lead": lead_id, "class": cls, "reason": ""}

	def test_confirmed_beats_id_only(self):
		a, b = self.row("1", "ID_ONLY_NAME_OK"), self.row("2", "CONFIRMED")
		m.decide_duplicates([a, b])
		self.assertEqual((a["class"], b["class"]), ("SKIP", "CONFIRMED"))

	def test_tie_goes_to_review(self):
		a, b = self.row("1", "CONFIRMED"), self.row("2", "CONFIRMED")
		m.decide_duplicates([a, b])
		self.assertEqual((a["class"], b["class"]), ("REVIEW", "REVIEW"))

	def test_no_good_row_goes_to_review(self):
		a, b = self.row("1", "REVIEW"), self.row("2", "CONFIRMED")
		m.decide_duplicates([a, b])
		self.assertEqual(b["class"], "CONFIRMED")
		self.assertEqual(a["class"], "SKIP")


class TestDefaults(unittest.TestCase):
	def test_only_safe_classes_applied_by_default(self):
		self.assertEqual(set(m.APPLY_BY_DEFAULT), {"CONFIRMED", "ID_ONLY_NAME_OK"})


if __name__ == "__main__":
	unittest.main()
