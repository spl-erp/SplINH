"""Call Review: the answers a rep gives about a call, sent by the Call Tracker app.

Stored on the Call Log in fields that were made by hand in the UI (the option lists are owned
there too; nothing in this app re-asserts them on migrate):
  custom_call_drop_stage   (Select)      "Call Drop Stage"
  custom_next_stage        (Select)      "Next Stage"
  custom_call_category     (Select)      "Call Category"
  custom_detailed_action   (Small Text)  "Next Action"
plus custom_reviewed_at / custom_reviewed_by / custom_review_source, created by
splinh_setup._ensure_call_review_fields.

Endpoints are in api/call_tracking.py (submit_call_review, get_call_review_options,
get_my_call_reviews). This module holds the pure rules (no database), so they can be unit
tested offline, plus the Call Log validate hook that stamps reviews made in the ERP. The
endpoints validate against the LIVE options of the Select fields, so a change of a picklist
in the UI needs no code change and no app release.
"""

import datetime
import re

import frappe
from frappe.utils import get_datetime

# Only used when the field has to be CREATED on a fresh site (the live lists are edited in the
# UI). Leading blank line on purpose: without it Frappe fills the first option into every new
# Call Log (the same reason custom_source starts with a blank line).
DROP_STAGE_OPTIONS = (
	"\nNot Connected\nNo 2-Way Connection\nNo Need\nNo Trust\nNo Money\nNo Discount\nConverted"
)
NEXT_STAGE_OPTIONS = "\nMeeting\nFollow up\nProposal / Quotation\nSample"

ACTION_MAX = 500

# The value the app sends by itself for a missed or unanswered call. Offered to the app only
# while it is really one of the live drop stages.
NOT_CONNECTED_VALUE = "Not Connected"

# The four fields that make up a review. A change to any of them from the ERP form counts as an
# ERP review.
REVIEW_FIELDS = (
	"custom_call_drop_stage",
	"custom_next_stage",
	"custom_call_category",
	"custom_detailed_action",
)
SOURCE_APP = "app"
SOURCE_ERP = "erp"
_CONTROL_CHARS = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]+")


class InvalidCallReview(frappe.ValidationError):
	"""Frappe answers ValidationError with HTTP 417, which the app treats as temporary and
	retries for days. 422 tells it the value is wrong and not to retry."""

	http_status_code = 422


def parse_options(options):
	"""Allowed values of a Select field: its option lines, trimmed, blanks dropped."""
	return [line.strip() for line in (options or "").split("\n") if line.strip()]


def clean_choice(value, allowed, label, required):
	"""A trimmed option that must be one of `allowed` ("" when optional and empty)."""
	text = value.strip() if isinstance(value, str) else ("" if value is None else None)
	if text is None:
		raise InvalidCallReview(f"{label} must be text.")
	if not text:
		if required:
			raise InvalidCallReview(f"{label} is required.")
		return ""
	if text not in allowed:
		raise InvalidCallReview(f"{label}: '{text[:60]}' is not an allowed value.")
	return text


def clean_action(value):
	"""Free text for Next Action, trimmed and capped. Line breaks are kept."""
	if value is None:
		return ""
	if not isinstance(value, str):
		raise InvalidCallReview("Next Action must be text.")
	return _CONTROL_CHARS.sub("", value).strip()[:ACTION_MAX]


def clean_reviewed_at(value, now):
	"""The phone's local time (IST, same as the server) as a naive datetime.

	An unreadable, ancient or far-future value is replaced with `now`: the review itself is
	still good, only its timestamp is unusable."""
	if not value or isinstance(value, (bool, dict, list, tuple, set)):
		return now
	try:
		parsed = get_datetime(str(value).strip())
	except (ValueError, TypeError, OverflowError):
		return now
	if parsed.tzinfo is not None:
		parsed = parsed.astimezone().replace(tzinfo=None)
	if parsed.year < 2020 or parsed > now + datetime.timedelta(days=1):
		return now
	return parsed


def clean_review(raw, allowed_drop, allowed_next, now, allowed_category=None):
	"""{field: value} to store, or raise InvalidCallReview.

	Call Category is optional: left out of the result when `allowed_category` is None (the
	field is not on the doctype) and when the app does not send it, so an older app build
	never wipes a category a manager set."""
	out = {
		"custom_call_drop_stage": clean_choice(raw.get("call_drop_stage"), allowed_drop, "Call Drop Stage", True),
		"custom_next_stage": clean_choice(raw.get("next_stage"), allowed_next, "Next Stage", False),
		"custom_detailed_action": clean_action(raw.get("next_stage_action")),
		"custom_reviewed_at": clean_reviewed_at(raw.get("reviewed_at"), now),
	}
	if allowed_category is not None and raw.get("call_category") is not None:
		out["custom_call_category"] = clean_choice(
			raw.get("call_category"), allowed_category, "Call Category", False
		)
	return out


def option_items(options):
	"""[{"value", "label"}] for the app's pickers: exact spelling and order, blanks dropped."""
	return [{"value": v, "label": v} for v in parse_options(options)]


def not_connected_value(drop_stage_options):
	"""NOT_CONNECTED_VALUE when it is one of the live drop stages, else None."""
	return NOT_CONNECTED_VALUE if NOT_CONNECTED_VALUE in parse_options(drop_stage_options) else None


def parse_day(value, today):
	"""A YYYY-MM-DD string as a date; empty means `today`. Anything else is a 422."""
	if value is None or (isinstance(value, str) and not value.strip()):
		return today
	try:
		return datetime.datetime.strptime(str(value).strip(), "%Y-%m-%d").date()
	except ValueError:
		raise InvalidCallReview("date must be YYYY-MM-DD.")


def _time_text(value):
	if not value:
		return ""
	return get_datetime(value).strftime("%Y-%m-%d %H:%M:%S")


def review_item(row):
	"""One review as the app reads it. get_my_call_reviews returns a list of these and a 409 from
	submit_call_review returns the same shape (plus "status")."""
	return {
		"call_log_name": row.get("name"),
		"call_drop_stage": row.get("custom_call_drop_stage") or "",
		"next_stage": row.get("custom_next_stage") or "",
		"next_stage_action": row.get("custom_detailed_action") or "",
		"call_category": row.get("custom_call_category") or "",
		"reviewed_at": _time_text(row.get("custom_reviewed_at")),
		"reviewed_by": row.get("custom_reviewed_by") or "",
		"review_source": row.get("custom_review_source") or "",
	}


def flag(value):
	"""True for 1 / true / yes / on in the usual spellings (JSON booleans and form strings)."""
	if isinstance(value, str):
		return value.strip().lower() in ("1", "true", "yes", "y", "on")
	return bool(value)


def decide(existing_reviewed_at, existing_source, auto, force):
	"""What submit_call_review does with a review that has passed validation:
	  "skip"      automatic review, but a real one already exists: change nothing
	  "conflict"  the existing review was made in the ERP and the app did not say force
	  "write"     store it
	"""
	if auto and existing_reviewed_at:
		return "skip"
	if existing_source == SOURCE_ERP and not force:
		return "conflict"
	return "write"


def review_changed(before, after):
	"""True when any review field differs. `before` is None for a brand-new Call Log, where any
	filled review field counts as a change."""
	for field in REVIEW_FIELDS:
		new = (after.get(field) or "").strip() if isinstance(after.get(field), str) else after.get(field)
		old = None if before is None else before.get(field)
		old = (old or "").strip() if isinstance(old, str) else old
		if (new or "") != (old or ""):
			return True
	return False


def stamp_erp_review(doc, method=None):
	"""Call Log validate hook. A review changed in the ERP form (a normal save, so this hook
	runs) is marked source "erp" with the user and the time. The app writes with
	frappe.db.set_value, which runs no hooks, so it never passes through here. Saves that do not
	touch the four review fields (call sync, transcription, manager remarks) do nothing."""
	before = None if doc.is_new() else doc.get_doc_before_save()
	if not doc.is_new() and before is None:
		return
	if not review_changed(before, doc):
		return
	doc.custom_reviewed_by = frappe.session.user
	doc.custom_review_source = SOURCE_ERP
	doc.custom_reviewed_at = frappe.utils.now_datetime()
