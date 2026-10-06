  """Transcript + sales-call summary for a Call Log recording, via the Gemini API.

Triggered by the "Transcribe & Summarize" button on the Call Log form
(api/call_ai.py) - on demand, never automatically at insert time. Automating it
later means calling `run_call_ai()` from receive_call_recording behind a flag.

Pipeline (two_step, the default):
  1. upload the recording to the Gemini Files API
  2. transcribe it with a dedicated speech model (gemini-3.5-transcribe)
  3. if the transcript came back in Devanagari, convert it to Roman letters
     (Hinglish) with the cheap text model - the transcribe model documents no
     output-script option, so this is done explicitly
  4. summarise + extract structured insights with a cheap text model
     (gemini-3.5-flash-lite) using a JSON schema
  5. delete the uploaded copy from Google, write everything to the Call Log
`mode="one_pass"` (bake-off only) does 2-4 in a single call on the audio.

Privacy: every Interactions call sends `"store": false` (otherwise Google keeps
each request and response for 55 days on the paid tier) and the uploaded audio
is deleted as soon as the run ends. Needs a BILLED Google project - the free
tier lets Google use the content for training.

Config lives on the "SplINH AI Settings" screen in ERPNext (System Manager only; the
API key is an encrypted Password field; "SplINH Settings" is the app-shipped fallback). conf()/models()/api_key() below read it
first and fall back to site_config.json, then to a built-in default, so nothing
breaks before the screen has been filled in:
  gemini_api_key                required (Password field / site_config)
  models                        transcribe + summary model ids (site_config: splinh_ai_models)
  splinh_ai_language_codes      BCP-47 hints, default [] = auto-detect (handles Hindi/English mixing)
  splinh_ai_diarization         default FALSE: live test showed the speaker turns come back glued together
                                with no labels (words run on: "...sir?Yeah, Prashant here"), so the plain
                                transcript reads better at the same cost. Calls up to 30 min only (API limit)
  splinh_ai_romanize            default true
  splinh_ai_thinking_level      default "low"
  splinh_ai_glossary            product / brand words to spell correctly
  splinh_ai_min_seconds / max_seconds / daily_limit / enabled   guards, see api/call_ai.py

Prices below are USD per 1M tokens, Standard tier, from
https://ai.google.dev/gemini-api/docs/pricing (2026-10-01). Costs written to the
Call Log are ESTIMATES from the token counts Google returns.
"""

import json
import os
import re
import time

import frappe
import requests
from frappe.utils import get_bench_path
from tenacity import retry, retry_if_exception, stop_after_attempt, wait_exponential

BASE_URL = "https://generativelanguage.googleapis.com"

DEFAULT_MODELS = {"transcribe": "gemini-3.5-transcribe", "summary": "gemini-3.5-flash-lite"}

PRICES = {
	"gemini-3.5-transcribe": {"audio_in": 2.00, "text_in": 2.00, "out": 12.00},
	"gemini-3.5-flash-lite": {"audio_in": 0.30, "text_in": 0.30, "out": 2.50},
	"gemini-3.1-flash-lite": {"audio_in": 0.50, "text_in": 0.25, "out": 1.50},
}

AUDIO_MIME = {
	".m4a": ("audio/mp4", "audio/m4a"),  # tried in order; the first one Google accepts wins
	".mp4": ("audio/mp4",),
	".mp3": ("audio/mp3",),
	".wav": ("audio/wav",),
	".aac": ("audio/aac",),
	".ogg": ("audio/ogg",),
	".opus": ("audio/ogg",),
	".flac": ("audio/flac",),
}

DIARIZATION_MAX_SECONDS = 30 * 60  # documented limit when diarization is on
_DEVANAGARI = re.compile(r"[ऀ-ॿ]")
ROMANIZE_CHUNK_CHARS = 12000

INTEREST_LEVELS = ("Hot", "Warm", "Cold", "None")

INSIGHTS_SCHEMA = {
	"type": "object",
	"properties": {
		"summary": {"type": "string", "description": "3-5 sentence summary of the call, in English."},
		"customer_requirements": {"type": "array", "items": {"type": "string"}},
		"products_discussed": {"type": "array", "items": {"type": "string"}},
		"customer_questions_concerns": {"type": "array", "items": {"type": "string"}},
		"price_quotation_discussed": {
			"type": "string",
			"description": "Any price, discount, quantity or quotation talked about, with the numbers as spoken. 'Not discussed' if none.",
		},
		"follow_up_actions": {"type": "array", "items": {"type": "string"}},
		"interest_level": {"type": "string", "enum": list(INTEREST_LEVELS)},
		"interest_reason": {"type": "string"},
		"next_steps": {"type": "array", "items": {"type": "string"}},
		"language": {"type": "string", "description": "e.g. Hindi, English, Hinglish"},
	},
	"required": [
		"summary",
		"customer_requirements",
		"products_discussed",
		"customer_questions_concerns",
		"price_quotation_discussed",
		"follow_up_actions",
		"interest_level",
		"interest_reason",
		"next_steps",
		"language",
	],
}

SUMMARY_SYSTEM = """You analyse sales phone calls for an ink and printing-supplies company in India.
The conversation may be Hindi, English or Hinglish. Write all fields in English, but keep product names, brand names, people and places exactly as spoken.
Use ONLY what is said in the call. Never invent prices, quantities, dates or commitments. If something was not discussed, return an empty list (or "Not discussed" for the price field).
interest_level: Hot = clear buying intent or asks for a quote/delivery; Warm = interested but undecided or comparing; Cold = polite or no interest; None = not a sales conversation (wrong number, internal, silence)."""

ROMANIZE_SYSTEM = """Convert the text to Roman letters (Hinglish): write every Hindi word in Roman letters the way people type Hindi on a phone (e.g. "aap ka quotation bhej dijiye"). Leave English words, numbers, names, and any speaker labels or timestamps exactly as they are. Keep the same line breaks and order. Do not translate, summarise, add or remove anything. Output only the converted text."""


class AIError(Exception):
	"""A failure to show the user as is (missing key, no recording, Gemini error...)."""

	def __init__(self, message, status=None):
		super().__init__(message)
		self.status = status


# ---------------------------------------------------------------------- config


# The screen the user built in the ERPNext UI comes first; the one shipped in the app
# ("SplINH Settings") is the fallback. The first of these that has been saved is used.
SETTINGS_DOCTYPES = ("SplINH AI Settings", "SplINH Settings")
_UNSET = object()

# site_config key -> (field on the SplINH Settings screen, kind). Checkboxes are
# authoritative once the screen has been saved (a checkbox cannot be "empty");
# every other kind falls through to site_config / the built-in default when blank.
SETTINGS_MAP = {
	"splinh_ai_enabled": ("enabled", "check"),
	"splinh_ai_diarization": ("diarization", "check"),
	"splinh_ai_romanize": ("romanize", "check"),
	"splinh_ai_thinking_level": ("thinking_level", "text"),
	"splinh_ai_glossary": ("glossary", "text"),
	"splinh_ai_language_codes": ("language_codes", "list"),
	"splinh_ai_min_seconds": ("min_seconds", "int"),
	"splinh_ai_max_seconds": ("max_seconds", "int"),
	"splinh_ai_daily_limit": ("daily_limit", "int"),
}


def _site_conf(key):
	return frappe.conf.get(key)


def _load_settings():
	"""The saved settings document (first of SETTINGS_DOCTYPES that has been saved),
	or None if no screen exists yet or none has been saved. Read once per request/job.

	Never raises: call jobs import this module the moment the code is on disk, which
	can be before a settings table exists, and a settings problem must not break
	anything that worked from site_config alone.
	"""
	cached = getattr(frappe.local, "splinh_ai_settings", _UNSET)
	if cached is not _UNSET:
		return cached
	doc = None
	for doctype in SETTINGS_DOCTYPES:
		try:
			# A Single's `modified` only exists once it has been saved at least once.
			if frappe.db.get_single_value(doctype, "modified"):
				doc = frappe.get_doc(doctype)
				break
		except Exception:
			continue
	frappe.local.splinh_ai_settings = doc
	return doc


def conf(key, default=None):
	"""One setting: the SplINH Settings screen first, then site_config.json, then `default`."""
	settings = _load_settings()
	if settings is not None and key in SETTINGS_MAP:
		field, kind = SETTINGS_MAP[key]
		value = settings.get(field)
		if kind == "check":
			return bool(value)
		if kind == "int" and value:
			return int(value)
		if kind == "list" and value:
			return [code.strip() for code in str(value).split(",") if code.strip()]
		if kind == "text" and value and str(value).strip():
			return str(value).strip()
	value = _site_conf(key)
	return default if value is None else value


def models():
	chosen = dict(DEFAULT_MODELS)
	chosen.update(_site_conf("splinh_ai_models") or {})
	settings = _load_settings()
	if settings is not None:
		for role, field in (("transcribe", "transcribe_model"), ("summary", "summary_model")):
			if (settings.get(field) or "").strip():
				chosen[role] = settings.get(field).strip()
	return chosen


def api_key():
	"""The Gemini key: SplINH Settings (encrypted Password field) first, then site_config."""
	key = None
	settings = _load_settings()
	if settings is not None:
		try:
			key = settings.get_password("gemini_api_key", raise_exception=False)
		except Exception:
			key = None
	key = key or _site_conf("gemini_api_key")
	# A key pasted into a form often carries a trailing space or newline, and a screen
	# built in the UI has no server code to trim it.
	key = key.strip() if isinstance(key, str) else key
	if not key:
		raise AIError("The Gemini API key is not set. An administrator can add it in SplINH Settings.")
	return key


def check_key(key):
	"""Is this Gemini key accepted by Google? Uses the free model-list call, never a
	paid model. Returns (ok, message); the key itself is never echoed back."""
	if not key:
		return False, "No API key to test."
	try:
		response = requests.get(
			f"{BASE_URL}/v1beta/models", params={"pageSize": 1}, headers={"x-goog-api-key": key}, timeout=20
		)
	except requests.RequestException as e:
		return False, f"Could not reach Google: {type(e).__name__}"
	if response.status_code == 200:
		return True, "Google accepted the key."
	try:
		detail = response.json().get("error", {}).get("message") or ""
	except ValueError:
		detail = ""
	return False, f"Google rejected the key (HTTP {response.status_code}). {detail}".strip()


# ------------------------------------------------------------------------ HTTP


def _retryable(exc):
	if isinstance(exc, (requests.ConnectionError, requests.Timeout)):
		return True
	return isinstance(exc, AIError) and exc.status in (429, 500, 502, 503, 504)


@retry(
	retry=retry_if_exception(_retryable),
	stop=stop_after_attempt(3),
	wait=wait_exponential(multiplier=2, min=2, max=20),
	reraise=True,
)
def _request(method, url, *, timeout, auth=True, headers=None, **kwargs):
	headers = dict(headers or {})
	if auth:
		headers["x-goog-api-key"] = api_key()
	response = requests.request(method, url, headers=headers, timeout=timeout, **kwargs)
	if response.status_code >= 400:
		raise AIError(f"Gemini API {response.status_code}: {response.text[:500]}", response.status_code)
	return response


def _upload_audio(content, mime, display_name):
	"""Resumable upload to the Files API. Returns the file dict (name, uri, state)."""
	start = _request(
		"POST",
		f"{BASE_URL}/upload/v1beta/files",
		timeout=60,
		headers={
			"X-Goog-Upload-Protocol": "resumable",
			"X-Goog-Upload-Command": "start",
			"X-Goog-Upload-Header-Content-Length": str(len(content)),
			"X-Goog-Upload-Header-Content-Type": mime,
			"Content-Type": "application/json",
		},
		data=json.dumps({"file": {"display_name": display_name}}),
	)
	upload_url = start.headers.get("x-goog-upload-url")
	if not upload_url:
		raise AIError("Gemini Files API did not return an upload URL.")

	done = _request(
		"POST",
		upload_url,
		timeout=300,
		auth=False,
		headers={
			"Content-Length": str(len(content)),
			"X-Goog-Upload-Offset": "0",
			"X-Goog-Upload-Command": "upload, finalize",
		},
		data=content,
	)
	file = done.json().get("file") or {}
	deadline = time.time() + 60
	while file.get("state") == "PROCESSING" and time.time() < deadline:
		time.sleep(2)
		file = _request("GET", f"{BASE_URL}/v1beta/{file['name']}", timeout=30).json()
	if file.get("state") not in (None, "ACTIVE"):
		raise AIError(f"Gemini could not process the audio file (state {file.get('state')}).")
	if not file.get("uri"):
		raise AIError("Gemini Files API did not return a file URI.")
	return file


def _delete_file(file):
	try:
		if file and file.get("name"):
			_request("DELETE", f"{BASE_URL}/v1beta/{file['name']}", timeout=30)
	except Exception:
		frappe.log_error(title="splinh call_ai: could not delete the uploaded Gemini file", message=frappe.get_traceback())


def _interact(body, timeout):
	"""One Interactions API call. store=false: Google keeps nothing of it."""
	return _request("POST", f"{BASE_URL}/v1beta/interactions", timeout=timeout, json={**body, "store": False}).json()


def _output_text(interaction):
	if interaction.get("status") not in (None, "completed"):
		raise AIError(f"Gemini call did not complete (status {interaction.get('status')}).")
	parts = []
	for step in interaction.get("steps") or []:
		if step.get("type") == "model_output":
			parts.extend(c["text"] for c in step.get("content") or [] if c.get("text"))
	return ("".join(parts) or interaction.get("output_text") or "").strip()


# ----------------------------------------------------------------- usage / cost


def _record_usage(usage_log, model, interaction):
	usage = interaction.get("usage") or {}
	audio = sum(
		m.get("tokens", 0) for m in usage.get("input_tokens_by_modality") or [] if m.get("modality") == "audio"
	)
	total_in = usage.get("total_input_tokens", 0)
	output = usage.get("total_output_tokens", 0) + usage.get("total_thought_tokens", 0)
	if not output:
		# gemini-3.5-transcribe leaves total_output_tokens at 0 and reports what it wrote
		# only per invocation (seen live 2026-10-04) - without this its output is free.
		output = sum(
			d.get("tokens", 0)
			for invocation in usage.get("model_invocation_token_counts") or []
			for d in invocation.get("candidates_tokens_details") or []
		)
	price = PRICES.get(model)
	cost = None
	if price:
		cost = (
			audio * price["audio_in"] + max(total_in - audio, 0) * price["text_in"] + output * price["out"]
		) / 1_000_000
	usage_log.append(
		{
			"model": model,
			"input_tokens": total_in,
			"audio_tokens": audio,
			"output_tokens": output,
			"cost_usd": None if cost is None else round(cost, 6),
		}
	)


def _total_cost(usage_log):
	costs = [u["cost_usd"] for u in usage_log if u["cost_usd"] is not None]
	return round(sum(costs), 6) if costs else None


# ------------------------------------------------------------------ the steps


def _text_call(model, system, text, usage_log, *, schema=None, max_tokens=8192, timeout=180):
	body = {
		"model": model,
		"system_instruction": system,
		"input": text,
		"generation_config": {"max_output_tokens": max_tokens},
	}
	level = conf("splinh_ai_thinking_level", "low")
	if level:
		body["generation_config"]["thinking_level"] = level
	if schema:
		body["response_format"] = {"type": "text", "mime_type": "application/json", "schema": schema}
	try:
		interaction = _interact(body, timeout)
	except AIError as e:
		if e.status == 400 and level and "think" in str(e).lower():
			body["generation_config"].pop("thinking_level", None)  # this model has no such setting
			interaction = _interact(body, timeout)
		else:
			raise
	_record_usage(usage_log, model, interaction)
	return _output_text(interaction)


def _transcribe(file, mimes, diarize, usage_log):
	mode = {"type": "verbatim"}
	if diarize:
		mode["diarization_mode"] = "speaker"
	last_error = None
	for mime in mimes:
		body = {
			"model": models()["transcribe"],
			"input": [{"type": "audio", "uri": file["uri"], "mime_type": mime}],
			"generation_config": {
				"transcription_config": {"language_codes": conf("splinh_ai_language_codes") or [], "mode": mode}
			},
		}
		try:
			interaction = _interact(body, timeout=600)
		except AIError as e:
			if e.status == 400 and "mime" in str(e).lower():
				last_error = e
				continue
			raise
		_record_usage(usage_log, models()["transcribe"], interaction)
		return _output_text(interaction)
	raise last_error


def _chunk(text, size):
	"""Split on line breaks into pieces of at most ~size characters."""
	chunks, current = [], ""
	for line in text.splitlines(keepends=True):
		if current and len(current) + len(line) > size:
			chunks.append(current)
			current = ""
		current += line
	if current:
		chunks.append(current)
	return chunks


def _romanize(text, usage_log):
	if not conf("splinh_ai_romanize", True) or not _DEVANAGARI.search(text):
		return text
	return "\n".join(
		_text_call(models()["summary"], ROMANIZE_SYSTEM, chunk, usage_log, max_tokens=32768, timeout=300).strip("\n")
		for chunk in _chunk(text, ROMANIZE_CHUNK_CHARS)
	)


def _summary_prompt(transcript, context):
	lines = [f"{k}: {v}" for k, v in context.items() if v]
	glossary = conf("splinh_ai_glossary")
	if glossary:
		lines.append(f"Product / brand words to spell correctly: {glossary}")
	return "Call details:\n" + "\n".join(lines) + "\n\nTRANSCRIPT:\n" + transcript


def parse_insights(text):
	"""Parse and sanitise the model's JSON. Raises AIError if it is not usable."""
	try:
		data = json.loads(text)
	except ValueError:
		raise AIError("The summary model did not return valid JSON.")
	if not isinstance(data, dict):
		raise AIError("The summary model returned an unexpected shape.")

	def lines(key):
		value = data.get(key) or []
		return [str(v).strip() for v in value if str(v).strip()] if isinstance(value, list) else [str(value)]

	level = str(data.get("interest_level") or "").strip().title()
	return {
		"summary": str(data.get("summary") or "").strip(),
		"customer_requirements": lines("customer_requirements"),
		"products_discussed": lines("products_discussed"),
		"customer_questions_concerns": lines("customer_questions_concerns"),
		"price_quotation_discussed": str(data.get("price_quotation_discussed") or "Not discussed").strip(),
		"follow_up_actions": lines("follow_up_actions"),
		"interest_level": level if level in INTEREST_LEVELS else "None",
		"interest_reason": str(data.get("interest_reason") or "").strip(),
		"next_steps": lines("next_steps"),
		"language": str(data.get("language") or "").strip(),
	}


def format_insights(data):
	"""Readable text for the Key Insights field."""

	def block(title, items):
		return f"{title}\n" + ("\n".join(f"  - {i}" for i in items) if items else "  - (none)")

	return "\n\n".join(
		[
			block("Customer requirements", data["customer_requirements"]),
			block("Products / services discussed", data["products_discussed"]),
			block("Questions & concerns", data["customer_questions_concerns"]),
			f"Price / quotation discussed\n  {data['price_quotation_discussed']}",
			block("Follow-up actions", data["follow_up_actions"]),
			f"Interest level: {data['interest_level']}" + (f" - {data['interest_reason']}" if data["interest_reason"] else ""),
			block("Next steps", data["next_steps"]),
		]
	)


# ------------------------------------------------------------------ the recording


def latest_audio_file(call_log_name):
	"""Newest attached File with an audio extension, or None."""
	files = frappe.get_all(
		"File",
		filters={"attached_to_doctype": "Call Log", "attached_to_name": call_log_name},
		fields=["name", "file_name", "file_size"],
		order_by="creation desc",
	)
	for f in files:
		if os.path.splitext(f.file_name or "")[1].lower() in AUDIO_MIME:
			return f
	return None


def _call_context(call_log_name):
	row = frappe.db.get_value(
		"Call Log",
		call_log_name,
		["type", "duration", "custom_linked_party", "custom_contact_name", "start_time"],
		as_dict=True,
	)
	return {
		"Direction": row.type,
		"Duration (seconds)": int(row.duration or 0),
		"Contact name on the phone": row.custom_contact_name,
		"Linked in ERP to": row.custom_linked_party,
		"Call time": row.start_time,
	}, int(row.duration or 0)


def process_call(call_log_name, *, mode="two_step", diarize=None):
	"""Run the pipeline and return the result. Writes nothing to the database.

	Raises AIError for anything the user should see. The uploaded Google copy is
	always deleted before returning.
	"""
	api_key()  # fail early, before any work
	audio = latest_audio_file(call_log_name)
	if not audio:
		raise AIError("This call has no audio recording attached.")

	# Same CWD guard as receive_call_recording: a respawned worker can have a relative site path.
	frappe.local.site_path = os.path.join(get_bench_path(), "sites", frappe.local.site)
	content = frappe.get_doc("File", audio.name).get_content()
	if isinstance(content, str):
		content = content.encode()

	context, duration = _call_context(call_log_name)
	if diarize is None:
		diarize = bool(conf("splinh_ai_diarization", False)) and 0 < duration <= DIARIZATION_MAX_SECONDS
	mimes = AUDIO_MIME[os.path.splitext(audio.file_name)[1].lower()]
	usage_log = []
	started = time.time()

	file = None
	last_error = None
	try:
		for mime in mimes:
			try:
				file = _upload_audio(content, mime, call_log_name)
				break
			except AIError as e:
				last_error = e
				if not (e.status == 400 and "mime" in str(e).lower()):
					raise
		if not file:
			raise last_error
		upload_mime = file.get("mimeType") or mimes[0]

		if mode == "one_pass":
			result_text = _one_pass(file, upload_mime, context, usage_log)
			try:
				data_json = json.loads(result_text)
			except ValueError:
				raise AIError("The model did not return valid JSON.")
			if not isinstance(data_json, dict):
				raise AIError("The model returned an unexpected shape.")
			transcript = str(data_json.pop("transcript", "")).strip()
			if not transcript:
				raise AIError("The model returned an empty transcript (silence or unusable audio).")
			insights = parse_insights(json.dumps(data_json))
		else:
			transcript = _transcribe(file, (upload_mime,) + tuple(m for m in mimes if m != upload_mime), diarize, usage_log)
			if not transcript:
				raise AIError("The speech model returned an empty transcript (silence or unusable audio).")
			transcript = _romanize(transcript, usage_log)
			insights = parse_insights(
				_text_call(
					models()["summary"],
					SUMMARY_SYSTEM,
					_summary_prompt(transcript, context),
					usage_log,
					schema=INSIGHTS_SCHEMA,
					max_tokens=4096,
				)
			)
	finally:
		_delete_file(file)

	return {
		"transcript": transcript,
		"insights": insights,
		"insights_text": format_insights(insights),
		"language": insights["language"],
		"models": " + ".join(dict.fromkeys(u["model"] for u in usage_log)),
		"usage": usage_log,
		"cost_usd": _total_cost(usage_log),
		"seconds": round(time.time() - started, 1),
		"mode": mode,
		"diarized": diarize and mode == "two_step",
	}


def _one_pass(file, mime, context, usage_log):
	"""Bake-off only: transcript + insights from one call on the audio."""
	schema = json.loads(json.dumps(INSIGHTS_SCHEMA))
	schema["properties"]["transcript"] = {
		"type": "string",
		"description": "Full verbatim transcript, one line per speaker turn. Hindi written in Roman letters (Hinglish), English as is.",
	}
	schema["required"].append("transcript")
	prompt = _summary_prompt("(listen to the attached audio)", context)
	body = {
		"model": models()["summary"],
		"system_instruction": SUMMARY_SYSTEM,
		"input": [{"type": "text", "text": prompt}, {"type": "audio", "uri": file["uri"], "mime_type": mime}],
		"generation_config": {"max_output_tokens": 65536},
		"response_format": {"type": "text", "mime_type": "application/json", "schema": schema},
	}
	interaction = _interact(body, timeout=600)
	_record_usage(usage_log, models()["summary"], interaction)
	return _output_text(interaction)


# --------------------------------------------------------------- saving + the job


def save_result(call_log_name, result):
	"""Write a finished run to the Call Log (raw set_value: system-computed data,
	same precedent as the rest of this app - managers have no permlevel-0 write)."""
	insights = result["insights"]
	frappe.db.set_value(
		"Call Log",
		call_log_name,
		{
			"custom_transcript": result["transcript"],
			"custom_transcript_language": result["language"][:140],
			"custom_transcript_model": result["models"][:140],
			"custom_transcript_status": "Completed",
			"custom_transcript_error": "",
			"custom_ai_summary": insights["summary"],
			"custom_interest_level": insights["interest_level"],
			"custom_next_step": "\n".join(insights["next_steps"]),
			"custom_ai_insights": result["insights_text"],
			"custom_ai_insights_json": json.dumps(insights, ensure_ascii=False, indent=1),
			"custom_ai_usage": json.dumps(
				{"cost_usd_estimate": result["cost_usd"], "seconds": result["seconds"], "calls": result["usage"]}
			),
		},
		update_modified=False,
	)


def run_call_ai(call_log_name, user):
	"""Background job behind the button. Always ends in Completed or Failed and
	always tells the browser, so the button never stays stuck on Processing."""
	try:
		result = process_call(call_log_name)
		save_result(call_log_name, result)
	except Exception as e:
		message = str(e) if isinstance(e, AIError) else f"Unexpected error: {e}"
		frappe.db.rollback()
		frappe.db.set_value(
			"Call Log",
			call_log_name,
			{"custom_transcript_status": "Failed", "custom_transcript_error": message[:1000]},
			update_modified=False,
		)
		if not isinstance(e, AIError):
			frappe.log_error(title="splinh call_ai: unexpected failure", message=f"{call_log_name}\n{frappe.get_traceback()}")
	finally:
		frappe.cache.delete_value(f"splinh_ai_running::{call_log_name}")
		frappe.db.commit()
		frappe.publish_realtime("splinh_call_ai_done", {"name": call_log_name}, user=user)
