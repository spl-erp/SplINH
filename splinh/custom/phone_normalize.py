"""Canonical phone key for Call Log -> Lead/Contact matching: "+" then the digits.

	"+91-9225144953"     -> "+919225144953"
	" +91 7391066704"    -> "+917391066704"
	"+1767-4484544"      -> "+17674484544"    (dash sits after the US area code)
	"+91-8048372666x573" -> "+918048372666"   (167 stored numbers carry an extension)
	"9225144953"         -> "+919225144953"   (no country code: 173 of 459 call numbers)

Text in, text out - no database, no Frappe - so the index builder, the Lead/Contact
hooks and the incoming-call lookup can never disagree on the key. None means "not
usable as a phone number": nothing is ever guessed.
"""

import re

DEFAULT_COUNTRY_CODE = "91"
DEFAULT_NATIONAL_LENGTH = 10
# E.164's limit. Longer is data-entry junk (4 such values exist) and would not fit
# the indexed phone_key column, where a silent truncation would be a wrong key.
MAX_DIGITS = 15


def normalize_phone_key(
	raw, default_country_code=DEFAULT_COUNTRY_CODE, default_national_length=DEFAULT_NATIONAL_LENGTH
):
	"""Return the "+<digits>" key for `raw`, or None if it is not a usable number."""
	if not raw:
		return None

	text = re.sub(r"(?i)(?:ext\.?|[x#;])\s*\d.*$", "", str(raw).strip())
	digits = re.sub(r"\D", "", text)
	if not digits or len(digits) > MAX_DIGITS:
		return None

	if text.startswith("+"):
		return "+" + digits

	# No country code (older app versions, a few hand-typed Contacts). Accept a plain
	# national number for the default country, with or without its trunk "0"; refuse
	# anything else rather than guess which country it belongs to.
	code = (default_country_code or "").lstrip("+")
	national = digits[1:] if digits.startswith("0") else digits
	if not code:
		return None
	if len(national) == default_national_length:
		return "+" + code + national
	if national.startswith(code) and len(national) == len(code) + default_national_length:
		return "+" + national
	return None
