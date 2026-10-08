"""Offline tests for custom/call_ai.py - no site, no network, no API key.

Run:  cd apps/splinh && ../../env/bin/python -m unittest splinh.tests.test_call_ai
"""

import json
import unittest
from unittest import mock

from splinh.custom import call_ai
from splinh.custom.call_ai import AIError


def no_conf(key, default=None):
	return default


def quiet_settings():
	"""models() / api_key() consult the Settings screen and site_config; make both empty."""
	return mock.patch.multiple(call_ai, _site_conf=lambda key: None, _load_settings=lambda: None)


class TestOutputText(unittest.TestCase):
	def test_reads_model_output_steps(self):
		interaction = {
			"status": "completed",
			"steps": [
				{"type": "user_input", "content": [{"text": "ignored"}]},
				{"type": "model_output", "content": [{"text": "hello "}, {"text": "world"}]},
			],
		}
		self.assertEqual(call_ai._output_text(interaction), "hello world")

	def test_falls_back_to_output_text(self):
		self.assertEqual(call_ai._output_text({"output_text": " hi "}), "hi")

	def test_incomplete_status_is_an_error(self):
		with self.assertRaises(AIError):
			call_ai._output_text({"status": "failed", "steps": []})


class TestParseInsights(unittest.TestCase):
	def test_valid_and_normalised(self):
		data = call_ai.parse_insights(
			json.dumps(
				{
					"summary": " Customer wants 20 litres of ink. ",
					"customer_requirements": ["20 L black ink", " "],
					"interest_level": "hot",
					"next_steps": ["Send quotation"],
					"language": "Hinglish",
				}
			)
		)
		self.assertEqual(data["summary"], "Customer wants 20 litres of ink.")
		self.assertEqual(data["customer_requirements"], ["20 L black ink"])  # blank dropped
		self.assertEqual(data["interest_level"], "Hot")  # case fixed
		self.assertEqual(data["products_discussed"], [])  # missing -> empty list
		self.assertEqual(data["price_quotation_discussed"], "Not discussed")

	def test_unknown_interest_level_becomes_none(self):
		self.assertEqual(call_ai.parse_insights('{"interest_level": "maybe"}')["interest_level"], "None")

	def test_not_json_is_an_error(self):
		with self.assertRaises(AIError):
			call_ai.parse_insights("sorry, here is a summary")
		with self.assertRaises(AIError):
			call_ai.parse_insights("[1, 2]")

	def test_format_insights_lists_every_section(self):
		text = call_ai.format_insights(call_ai.parse_insights('{"next_steps": ["Call back Friday"]}'))
		for heading in ("Customer requirements", "Questions & concerns", "Price / quotation", "Follow-up actions", "Next steps"):
			self.assertIn(heading, text)
		self.assertIn("Call back Friday", text)


class TestCost(unittest.TestCase):
	def test_flash_lite_cost(self):
		log = []
		call_ai._record_usage(
			log,
			"gemini-3.5-flash-lite",
			{
				"usage": {
					"total_input_tokens": 1200,
					"input_tokens_by_modality": [{"modality": "audio", "tokens": 1000}],
					"total_output_tokens": 500,
					"total_thought_tokens": 100,
				}
			},
		)
		# 1000 audio x $0.30 + 200 text x $0.30 + 600 output x $2.50, per million
		self.assertAlmostEqual(log[0]["cost_usd"], 0.00186, places=6)
		self.assertEqual(log[0]["output_tokens"], 600)  # thinking tokens are billed as output

	def test_transcribe_model_output_is_read_from_the_nested_counts(self):
		# Real shape seen 2026-10-04: total_output_tokens is 0, the text is only in
		# model_invocation_token_counts[].candidates_tokens_details.
		log = []
		call_ai._record_usage(
			log,
			"gemini-3.5-transcribe",
			{
				"usage": {
					"total_input_tokens": 2596,
					"input_tokens_by_modality": [{"modality": "audio", "tokens": 2595}, {"modality": "text", "tokens": 1}],
					"total_output_tokens": 0,
					"model_invocation_token_counts": [
						{"candidates_tokens_details": [{"modality": "text", "tokens": 306}]}
					],
				}
			},
		)
		self.assertEqual(log[0]["output_tokens"], 306)
		# 2595 audio x $2 + 1 text x $2 + 306 out x $12, per million = $0.008864
		self.assertAlmostEqual(log[0]["cost_usd"], 0.008864, places=6)

	def test_unknown_model_has_no_cost(self):
		log = []
		call_ai._record_usage(log, "some-future-model", {"usage": {"total_input_tokens": 10}})
		self.assertIsNone(log[0]["cost_usd"])
		self.assertIsNone(call_ai._total_cost(log))


class TestHelpers(unittest.TestCase):
	def test_chunk_respects_size_and_keeps_every_line(self):
		text = "".join(f"line {i}\n" for i in range(100))
		chunks = call_ai._chunk(text, 200)
		self.assertTrue(all(len(c) <= 200 + 10 for c in chunks))
		self.assertEqual("".join(chunks), text)

	def test_m4a_is_a_known_audio_type(self):
		self.assertIn(".m4a", call_ai.AUDIO_MIME)

	def test_romanize_skips_text_that_is_already_roman(self):
		with quiet_settings(), mock.patch.object(call_ai, "conf", no_conf), mock.patch.object(
			call_ai, "_text_call", side_effect=AssertionError("must not call the model")
		):
			self.assertEqual(call_ai._romanize("aap ka quotation bhej dijiye", []), "aap ka quotation bhej dijiye")

	def test_romanize_calls_the_model_for_devanagari(self):
		with quiet_settings(), mock.patch.object(call_ai, "conf", no_conf), mock.patch.object(
			call_ai, "_text_call", return_value="aap kaise hain"
		) as call:
			self.assertEqual(call_ai._romanize("आप कैसे हैं", []), "aap kaise hain")
			call.assert_called_once()


class TestFallbacks(unittest.TestCase):
	def test_thinking_level_rejected_is_retried_without_it(self):
		bodies = []

		def fake_interact(body, timeout):
			bodies.append(json.loads(json.dumps(body)))
			if "thinking_level" in body["generation_config"]:
				raise AIError("Gemini API 400: thinking_level is not supported by this model", 400)
			return {"status": "completed", "steps": [{"type": "model_output", "content": [{"text": "ok"}]}]}

		with quiet_settings(), mock.patch.object(call_ai, "conf", lambda k, d=None: "low" if k == "splinh_ai_thinking_level" else d), mock.patch.object(
			call_ai, "_interact", fake_interact
		):
			self.assertEqual(call_ai._text_call("m", "sys", "text", []), "ok")
		self.assertEqual(len(bodies), 2)
		self.assertNotIn("thinking_level", bodies[1]["generation_config"])

	def test_transcribe_tries_the_next_mime_type(self):
		seen = []

		def fake_interact(body, timeout):
			mime = body["input"][0]["mime_type"]
			seen.append(mime)
			if mime == "audio/mp4":
				raise AIError("Gemini API 400: Unsupported MIME type: audio/mp4", 400)
			return {"status": "completed", "steps": [{"type": "model_output", "content": [{"text": "namaste"}]}]}

		with quiet_settings(), mock.patch.object(call_ai, "conf", no_conf), mock.patch.object(call_ai, "_interact", fake_interact):
			text = call_ai._transcribe({"uri": "u"}, ("audio/mp4", "audio/m4a"), False, [])
		self.assertEqual(text, "namaste")
		self.assertEqual(seen, ["audio/mp4", "audio/m4a"])

	def test_other_errors_are_not_swallowed(self):
		with quiet_settings(), mock.patch.object(call_ai, "conf", no_conf), mock.patch.object(
			call_ai, "_interact", side_effect=AIError("Gemini API 403: denied", 403)
		):
			with self.assertRaises(AIError):
				call_ai._transcribe({"uri": "u"}, ("audio/mp4",), False, [])


class TestSpeechModelFallback(unittest.TestCase):
	def test_only_the_thinking_fault_triggers_it(self):
		thinking = call_ai.AIError('Gemini API 400: {"message":"Thinking is not enabled for this model"}', 400)
		self.assertTrue(call_ai._speech_model_unavailable(thinking))
		self.assertFalse(call_ai._speech_model_unavailable(call_ai.AIError("Gemini API 400: bad mime", 400)))
		self.assertFalse(call_ai._speech_model_unavailable(call_ai.AIError("Thinking is not enabled", 500)))
		self.assertFalse(call_ai._speech_model_unavailable(call_ai.AIError("quota", 429)))

	def test_process_call_falls_back_to_one_pass(self):
		insights = {"summary": "s"}
		thinking = call_ai.AIError("Gemini API 400: Thinking is not enabled for this model", 400)
		patches = dict(
			api_key=lambda: "k",
			latest_audio_file=lambda n: mock.Mock(file_name="a.m4a", name="F1"),
			_call_context=lambda n: ({}, 60),
			_upload_audio=lambda c, m, d: {"uri": "u", "name": "files/x", "mimeType": "audio/mp4"},
			_delete_file=lambda f: None,
			_transcribe=mock.Mock(side_effect=thinking),
			_run_one_pass=lambda *a: ("hello", insights),
			format_insights=lambda i: "text",
			conf=lambda k, d=None: d,
		)
		fake_file = mock.Mock()
		fake_file.get_content.return_value = b"x"
		with mock.patch.multiple(call_ai, **patches), mock.patch.object(call_ai.frappe, "get_doc", return_value=fake_file), mock.patch.object(
			call_ai.frappe, "local", mock.Mock(site="s")
		):
			insights_result = dict(insights, language="en", interest_level="Low")
			with mock.patch.object(call_ai, "_run_one_pass", lambda *a: ("hello", insights_result)):
				result = call_ai.process_call("CL-1")
		self.assertEqual(result["mode"], "one_pass")
		self.assertEqual(result["transcript"], "hello")
		self.assertFalse(result["diarized"])


if __name__ == "__main__":
	unittest.main()
