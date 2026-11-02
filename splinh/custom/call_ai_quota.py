"""Keeps the speech model inside Google's rate limits instead of hitting them.

Google caps `gemini-3.5-transcribe` per project (Tier 1: 10 requests/minute, 10,000 input
tokens/minute, 100 requests/day). Sending calls back to back made most of them fail with
HTTP 429. This module hands out a "slot" BEFORE a call is uploaded or sent, so nothing is
wasted:

  - minute limits (requests and tokens, sliding 60 s window): wait a few seconds for a slot
  - daily limit (counted per Pacific-time day, when Google resets it): raise Throttled
  - pacing: the day's requests are spread over the day instead of all being used at once

Throttled never marks a call Failed: the caller puts the call back in the queue. The limits
are typed on the SplINH AI Settings screen (speech_rpm / speech_tpm / speech_rpd), so a
higher tier needs no code change. Only the speech model is limited; the text model is not.

State lives in frappe.cache (shared by the live job, bulk run and the manager's button).
If the cache is flushed the counters restart, and Google's own 429 answer is the safety net.
"""

import math
import re
import time
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import frappe
from frappe.utils import cint

PACIFIC = ZoneInfo("America/Los_Angeles")  # Google resets daily limits at midnight Pacific

AUDIO_TOKENS_PER_SECOND = 25  # measured on live data: 24-27
ESTIMATE_MARGIN = 1.05
DEFAULT_CALL_SECONDS = 60  # when a call has no duration
DEFAULT_RPM, DEFAULT_TPM, DEFAULT_RPD = 10, 10_000, 100
MINUTE_SAFETY = 0.9  # stay 10% under the per-minute limits
DAILY_RESERVE = 3  # requests kept back from the daily limit (manual clicks, a retry)
SPREAD_BURST = 5  # calls allowed ahead of the even daily pace
MAX_WAIT_SECONDS = 65  # a full minute window frees its first slot after 60.5 s
WINDOW_SECONDS = 60

WINDOW_KEY = "splinh_ai_quota_window"
DAY_KEY = "splinh_ai_quota_day::{}"
BLOCK_KEY = "splinh_ai_quota_block"
LAST_KEY = "splinh_ai_quota_last_reason"

_clock = time.time  # replaced in tests
_sleep = time.sleep


class Throttled(Exception):
	"""No quota right now. The call stays in the queue; `resume_at` is when to expect room."""

	def __init__(self, reason, resume_at=None):
		super().__init__(reason)
		self.reason = reason
		self.resume_at = resume_at  # epoch seconds or None


class TooLong(Exception):
	"""One request of this size can never fit the per-minute token limit."""


# ------------------------------------------------------------------- settings


def limits():
	"""Effective limits: the screen first (blank -> Tier 1 defaults)."""
	from splinh.custom import call_ai  # imported here: call_ai imports this module

	settings = call_ai._load_settings()

	def field(name, default):
		value = cint(settings.get(name)) if settings is not None else 0
		return value or default

	rpm, tpm, rpd = field("speech_rpm", DEFAULT_RPM), field("speech_tpm", DEFAULT_TPM), field("speech_rpd", DEFAULT_RPD)
	spread = settings.get("speech_spread") if settings is not None else None
	return {
		"rpm": max(1, math.floor(rpm * MINUTE_SAFETY)),
		"tpm": int(tpm * MINUTE_SAFETY),
		"rpd": max(1, rpd - DAILY_RESERVE),
		"rpd_nominal": rpd,
		"spread": True if spread is None else bool(cint(spread)),
	}


def estimate_tokens(duration):
	seconds = float(duration or 0) or DEFAULT_CALL_SECONDS
	return math.ceil(seconds * AUDIO_TOKENS_PER_SECOND * ESTIMATE_MARGIN)


# ------------------------------------------------------------------ the clock


def _pacific_day(now):
	"""(day key, start epoch, next reset epoch, fraction of the day elapsed)."""
	dt = datetime.fromtimestamp(now, PACIFIC)
	start = dt.replace(hour=0, minute=0, second=0, microsecond=0)
	reset = (start + timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0)
	return start.strftime("%Y-%m-%d"), start.timestamp(), reset.timestamp(), (now - start.timestamp()) / (reset.timestamp() - start.timestamp())


def clock_text(epoch):
	"""Local server time (IST here) for messages."""
	return datetime.fromtimestamp(epoch).strftime("%d %b %H:%M")


# ---------------------------------------------------------------- bookkeeping


def _window(now):
	return [e for e in (frappe.cache.get_value(WINDOW_KEY) or []) if e[0] > now - WINDOW_SECONDS]


def _used_today(day):
	return cint(frappe.cache.get_value(DAY_KEY.format(day)))


def _set_reason(reason, resume_at):
	text = reason if not resume_at else f"{reason}; expected room about {clock_text(resume_at)}"
	frappe.cache.set_value(LAST_KEY, text, expires_in_sec=3600)
	return text


def last_reason():
	return frappe.cache.get_value(LAST_KEY) or "No quota left right now."


def blocker(paced=True):
	"""Why nothing can be sent right now (daily limit, pacing, or Google said so), else None.
	Used to skip a whole scheduler run cheaply; reserves nothing. A manager's manual click
	passes paced=False: it may use the day's quota ahead of the even pace."""
	now = _clock()
	lim = limits()
	blocked = frappe.cache.get_value(BLOCK_KEY)
	if blocked and blocked > now:
		return _set_reason("Google's daily limit was reached", blocked)
	day, start, reset, fraction = _pacific_day(now)
	used = _used_today(day)
	if used >= lim["rpd"]:
		return _set_reason(f"Daily quota used ({used} of {lim['rpd_nominal']})", reset)
	if paced and lim["spread"] and used >= math.ceil(lim["rpd"] * fraction) + SPREAD_BURST:
		resume = start + 86400 * max(used - SPREAD_BURST, 0) / lim["rpd"]
		return _set_reason(f"Pacing: {used} of {lim['rpd_nominal']} used so far today", resume)
	return None


def reserve(est_tokens, paced=True):
	"""Take one speech request slot of `est_tokens`; returns a reservation id for `settle`.
	Waits (up to a minute) for the per-minute limits; raises Throttled for the daily limit,
	pacing, or a minute limit that will not clear in time; TooLong if it can never fit."""
	lim = limits()
	if est_tokens > lim["tpm"]:
		raise TooLong(
			f"About {est_tokens} tokens, more than one minute's limit ({lim['tpm']}). "
			"Longer than the Google tier allows."
		)
	deadline = _clock() + MAX_WAIT_SECONDS
	while True:
		now = _clock()
		reason = blocker(paced)
		if reason:
			raise Throttled(reason)
		window = _window(now)
		if len(window) < lim["rpm"] and sum(e[1] for e in window) + est_tokens <= lim["tpm"]:
			rid = f"{now:.6f}"
			window.append([now, est_tokens, rid])
			frappe.cache.set_value(WINDOW_KEY, window, expires_in_sec=2 * WINDOW_SECONDS)
			day = _pacific_day(now)[0]
			frappe.cache.set_value(DAY_KEY.format(day), _used_today(day) + 1, expires_in_sec=50 * 3600)
			return rid
		free_at = min(e[0] for e in window) + WINDOW_SECONDS + 0.5
		if free_at > deadline:
			raise Throttled(_set_reason("Per-minute limit", free_at), free_at)
		_sleep(max(0.5, free_at - now))


def settle(rid, actual_tokens):
	"""Replace the estimate with Google's real input-token count."""
	if not rid or not actual_tokens:
		return
	window = frappe.cache.get_value(WINDOW_KEY) or []
	for entry in window:
		if entry[2] == rid:
			entry[1] = actual_tokens
	frappe.cache.set_value(WINDOW_KEY, window, expires_in_sec=2 * WINDOW_SECONDS)


# ------------------------------------------------------------ Google's own 429


def parse_429(text):
	"""(is_daily, retry_after_seconds or None) from a Gemini 429 message."""
	lower = (text or "").lower()
	match = re.search(r"retry in ([\d.]+)\s*s", lower)
	return ("per day" in lower or "daily" in lower), (float(match.group(1)) if match else None)


def block_until_reset():
	"""Google said the daily limit is used: stop sending until the next Pacific midnight."""
	now = _clock()
	reset = _pacific_day(now)[2]
	frappe.cache.set_value(BLOCK_KEY, reset, expires_in_sec=int(reset - now) + 60)
	return reset


# ------------------------------------------------------------------ for screens


def status_text(queue=None):
	now = _clock()
	lim = limits()
	day, start, reset, fraction = _pacific_day(now)
	window = _window(now)
	text = (
		f"Today {_used_today(day)}/{lim['rpd_nominal']} requests, resets about {clock_text(reset)}; "
		f"last minute {len(window)}/{lim['rpm']} requests, {sum(e[1] for e in window)}/{lim['tpm']} tokens"
	)
	if queue is not None:
		text += f"; {queue} calls waiting"
	return text


def note(reason, resume_at=None):
	"""Remember why the last call was held back (shown on the settings screen)."""
	return _set_reason(reason, resume_at)
