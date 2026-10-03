"""Run without a site:  python -m unittest splinh.tests.test_phone_normalize"""

import unittest

from splinh.custom.phone_normalize import normalize_phone_key as n


class TestNormalizePhoneKey(unittest.TestCase):
	def test_stored_formats_seen_in_production(self):
		cases = {
			"+91-9225144953": "+919225144953",
			"+919225144953": "+919225144953",
			" +91 7391066704": "+917391066704",
			"+91- 94155 17920": "+919415517920",
			"+49-731  7908 2275": "+4973179082275",
			"+420-775 975 529 ": "+420775975529",
			"+1767-4484544": "+17674484544",
			"+91 (92251) 44953": "+919225144953",
			"+977-23455884": "+97723455884",  # 8 digits: the old last-10 rule dropped these
			"+91-4024611432": "+914024611432",
		}
		for raw, want in cases.items():
			self.assertEqual(n(raw), want, raw)

	def test_extension_is_dropped(self):
		# 167 stored numbers look like this; without stripping, the key would be
		# "+918048372666573" and could never match a call from +918048372666.
		self.assertEqual(n("+91-8048372666x573"), "+918048372666")
		self.assertEqual(n("+91-8048372666 ext. 12"), "+918048372666")
		self.assertEqual(n("+91-8048372666#4"), "+918048372666")

	def test_bare_numbers_use_default_country(self):
		self.assertEqual(n("9225144953"), "+919225144953")
		self.assertEqual(n("09225144953"), "+919225144953")
		self.assertEqual(n("919225144953"), "+919225144953")
		self.assertEqual(n("92251 44953"), "+919225144953")
		self.assertEqual(n("9225144953", default_country_code="44"), "+449225144953")

	def test_bare_numbers_never_guess(self):
		self.assertIsNone(n("12345"))
		self.assertIsNone(n("922514495"))  # 9 digits
		self.assertIsNone(n("92251449531"))  # 11 digits, no trunk 0, not +91
		self.assertIsNone(n("9225144953", default_country_code=""))

	def test_unusable_input(self):
		for raw in (None, "", "   ", "abc", "+", "-", "+" + "9" * 16):
			self.assertIsNone(n(raw), repr(raw))

	def test_short_numbers_with_a_country_code_are_kept(self):
		# No minimum length: the data has 86 real 7-to-9-digit numbers.
		self.assertEqual(n("+977-2345588"), "+9772345588")

	def test_idempotent(self):
		for raw in ("+91-9225144953", "9225144953", "+1767-4484544"):
			self.assertEqual(n(n(raw)), n(raw))

	def test_same_digits_different_country_stay_distinct(self):
		self.assertNotEqual(n("+1-9225144953"), n("+91-9225144953"))


if __name__ == "__main__":
	unittest.main()
