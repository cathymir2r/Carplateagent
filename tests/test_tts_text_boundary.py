"""Boundary tests: tagged TTS text must stay inside the server.

Plain text is what clients, JSON payloads, state, history and turn summaries see.
The tagged variant ([pause] / [elong]) may only reach the speech synthesizer.
"""

import asyncio
import importlib.util
import json
import re
import unittest
from pathlib import Path

from realtime_audio_demo.services.plate_agent_messages import (
    MESSAGE_TEMPLATES,
    _tag_easyturn_ack_text,
    fixed_message,
    tagged_for_plain,
)


HAS_HTTPX = importlib.util.find_spec("httpx") is not None
HAS_FASTAPI = importlib.util.find_spec("fastapi") is not None

REPO_ROOT = Path(__file__).resolve().parents[1]
TAG_PATTERN = re.compile(r"\[(?:pause|elong)\]")

# Modules that build payloads sent to browsers. None of them may emit a tts_text key.
CLIENT_FACING_SOURCES = (
    REPO_ROOT / "realtime_audio_demo" / "routes" / "audio.py",
    REPO_ROOT / "realtime_audio_demo" / "services" / "chatbox_application.py",
)


class FakeSpeechSynthesizer:
    """Records exactly what text the TTS layer was asked to speak."""

    def __init__(self) -> None:
        self.synthesize_calls: list[str] = []
        self.stream_calls: list[str] = []

    async def synthesize(self, *, model: str, text: str) -> str:
        self.synthesize_calls.append(text)
        return "data:audio/wav;base64,AAAA"

    async def stream_synthesize(self, *, model: str, text: str, on_audio_chunk) -> bool:
        self.stream_calls.append(text)
        await on_audio_chunk(b"\x00\x01")
        return True


class TaggedForPlainTests(unittest.TestCase):
    def test_placeholder_free_fixed_replies_are_recovered(self) -> None:
        for message_id, template in MESSAGE_TEMPLATES.items():
            if "{" in template.plain_template:
                continue
            with self.subTest(message_id=message_id):
                self.assertEqual(
                    tagged_for_plain(template.plain_template),
                    template.tagged_template,
                )

    def test_templates_with_runtime_values_are_never_matched(self) -> None:
        # A sentence carrying a plate / pending list / action description must not be
        # reverse matched, so lookups of a formatted reply fall back to plain text.
        formatted = fixed_message("session_opening")  # sanity: catalog is usable
        self.assertTrue(str(formatted))

        for message_id, template in MESSAGE_TEMPLATES.items():
            if "{" not in template.plain_template:
                continue
            with self.subTest(message_id=message_id):
                self.assertEqual(
                    tagged_for_plain(template.plain_template),
                    template.plain_template,
                )

    def test_unknown_text_is_returned_unchanged(self) -> None:
        self.assertEqual(tagged_for_plain("用户随便说的一句话"), "用户随便说的一句话")
        self.assertEqual(tagged_for_plain("  前后有空格  "), "前后有空格")
        self.assertEqual(tagged_for_plain(""), "")
        self.assertEqual(tagged_for_plain(None), "")


class EasyTurnAckTextTests(unittest.TestCase):
    def test_default_catalog_entry_keeps_plain_free_of_tags(self) -> None:
        reply = fixed_message("easyturn_ack")
        self.assertNotIn("[pause]", str(reply))
        self.assertNotIn("[elong]", str(reply))
        # Tagging only replaces the "，" after the leading particle, so stripping the
        # tags must give back the plain text minus that one separator.
        self.assertEqual(TAG_PATTERN.sub("", reply.tts_text), str(reply).replace("嗯，", "嗯", 1))

    def test_custom_text_starting_with_ack_particle_gets_a_pause(self) -> None:
        tagged = _tag_easyturn_ack_text("嗯，我这边还在听，您继续说。")
        self.assertEqual(tagged, "嗯[pause]我这边还在听，您继续说。")
        self.assertEqual(TAG_PATTERN.sub("", tagged), "嗯我这边还在听，您继续说。")

    def test_custom_text_without_ack_particle_is_left_untouched(self) -> None:
        plain = "请您继续说完整的车牌号。"
        self.assertEqual(_tag_easyturn_ack_text(plain), plain)

    def test_only_the_first_ack_particle_is_tagged(self) -> None:
        tagged = _tag_easyturn_ack_text("嗯，好的，嗯，您继续。")
        self.assertEqual(tagged, "嗯[pause]好的，嗯，您继续。")
        self.assertEqual(len(TAG_PATTERN.findall(tagged)), 1)

    def test_tagged_variant_uses_only_supported_tags(self) -> None:
        tagged = _tag_easyturn_ack_text("嗯，我这边还在听。")
        tags = set(re.findall(r"\[[^\]]+\]", tagged))
        self.assertTrue(tags <= {"[pause]", "[elong]"})
        self.assertNotRegex(tagged, r"\[#\d+\]|<break>|<prosody")


class ClientPayloadBoundaryTests(unittest.TestCase):
    """Regression guard: client-facing payloads must not carry a tts_text field."""

    def test_no_client_facing_module_emits_a_tts_text_key(self) -> None:
        for path in CLIENT_FACING_SOURCES:
            with self.subTest(path=path.name):
                source = path.read_text(encoding="utf-8")
                self.assertNotIn('"tts_text":', source)
                self.assertNotIn("'tts_text':", source)

    def test_frontend_never_reads_or_sends_tagged_text(self) -> None:
        static_dir = REPO_ROOT / "static"
        for path in sorted(static_dir.rglob("*")):
            if path.suffix not in {".html", ".js"}:
                continue
            with self.subTest(path=path.name):
                source = path.read_text(encoding="utf-8")
                self.assertNotIn("tts_text", source)
                self.assertNotIn("[pause]", source)
                self.assertNotIn("[elong]", source)


@unittest.skipUnless(HAS_HTTPX, "httpx is not installed")
class ChatboxApplicationBoundaryTests(unittest.TestCase):
    def setUp(self) -> None:
        from realtime_audio_demo.services.chatbox_application import ChatboxApplicationService

        self.speech = FakeSpeechSynthesizer()
        self.service = ChatboxApplicationService(model_client=_FakeChatModel(), speech=self.speech)

    def test_session_start_returns_plain_text_and_audio_only(self) -> None:
        from realtime_audio_demo.services.plate_agent_messages import (
            SESSION_OPENING_TEXT,
            SESSION_OPENING_TTS_TEXT,
        )

        payload, status = asyncio.run(self.service.start_session({"outputAudio": True}))

        self.assertEqual(status, 200)
        self.assertNotIn("tts_text", payload)
        self.assertEqual(payload["text"], str(SESSION_OPENING_TEXT))
        self.assertNotIn("[pause]", json.dumps(payload, ensure_ascii=False))
        self.assertEqual(self.speech.synthesize_calls, [SESSION_OPENING_TTS_TEXT])

    def test_text_path_synthesizes_server_side_and_returns_plain(self) -> None:
        from realtime_audio_demo.services.plate_agent_messages import NORMALIZED_SHORT_PLATE_REPLY

        payload, status = asyncio.run(
            self.service.handle_text({"text": "京A1", "outputAudio": True})
        )

        self.assertEqual(status, 200)
        self.assertNotIn("tts_text", payload)
        serialized = json.dumps(payload, ensure_ascii=False)
        self.assertNotIn("[pause]", serialized)
        self.assertNotIn("[elong]", serialized)
        self.assertTrue(payload["audio_data_url"])
        self.assertEqual(self.speech.synthesize_calls, [NORMALIZED_SHORT_PLATE_REPLY.tts_text])

    def test_text_path_skips_synthesis_when_audio_is_disabled(self) -> None:
        payload, _ = asyncio.run(self.service.handle_text({"text": "京A1", "outputAudio": False}))

        self.assertNotIn("tts_text", payload)
        self.assertEqual(self.speech.synthesize_calls, [])

    def test_speech_endpoint_retags_fixed_plain_text_server_side(self) -> None:
        from realtime_audio_demo.services.plate_agent_messages import NORMALIZED_SHORT_PLATE_REPLY

        asyncio.run(self.service.synthesize_speech({"text": str(NORMALIZED_SHORT_PLATE_REPLY)}))

        self.assertEqual(self.speech.synthesize_calls, [NORMALIZED_SHORT_PLATE_REPLY.tts_text])

    def test_speech_endpoint_ignores_client_supplied_tagged_text(self) -> None:
        asyncio.run(
            self.service.synthesize_speech(
                {"text": "普通的一句话。", "tts_text": "普通的[pause]一句话。"}
            )
        )

        self.assertEqual(self.speech.synthesize_calls, ["普通的一句话。"])


class _FakeChatModel:
    async def complete_text(self, **kwargs):
        # A short normalized plate drives validate_current_normalized_plate() into the
        # fixed normalized_short_plate reply, which has a tagged variant.
        return (
            {
                "text": json.dumps(
                    {
                        "task_status": "need_more_info",
                        "assistant_reply": "模型原始回复。",
                        "normalized": "京A1",
                    },
                    ensure_ascii=False,
                )
            },
            200,
        )


@unittest.skipUnless(HAS_FASTAPI, "fastapi is not installed")
class RouteSynthesisBoundaryTests(unittest.TestCase):
    def setUp(self) -> None:
        from realtime_audio_demo.routes import audio as audio_routes

        self.audio_routes = audio_routes
        self.speech = FakeSpeechSynthesizer()
        self._original = audio_routes.speech_synthesizer
        audio_routes.speech_synthesizer = self.speech

    def tearDown(self) -> None:
        self.audio_routes.speech_synthesizer = self._original

    def test_sse_events_carry_plain_text_while_synthesis_gets_tagged(self) -> None:
        reply = fixed_message("no_plate")

        events = asyncio.run(_collect_sse(self.audio_routes, reply))

        self.assertEqual(self.speech.stream_calls, [reply.tts_text])
        starts = [e for e in events if e["stage"] == "tts_audio_start"]
        self.assertEqual(len(starts), 1)
        self.assertEqual(starts[0]["speech_text"], str(reply))
        for event in events:
            self.assertNotIn("[pause]", json.dumps(event, ensure_ascii=False))
            self.assertNotIn("tts_text", event)

    def test_non_streaming_helper_sends_tagged_to_synthesizer(self) -> None:
        reply = fixed_message("invalid_plate")

        asyncio.run(
            self.audio_routes.synthesize_speech_audio("m", str(reply), tts_text=reply.tts_text)
        )

        self.assertEqual(self.speech.synthesize_calls, [reply.tts_text])

    def test_streaming_ws_helper_sends_tagged_and_emits_plain(self) -> None:
        reply = fixed_message("no_plate")
        session, events = _fake_ws_session(self.audio_routes)

        asyncio.run(
            self.audio_routes.stream_speech_audio_events(
                session,
                text=str(reply),
                segment="final",
                audio_index=1,
                tts_text=reply.tts_text,
            )
        )

        self.assertEqual(self.speech.stream_calls, [reply.tts_text])
        starts = [payload for name, payload in events if name == "tts_audio_start"]
        self.assertEqual(starts[0]["speech_text"], str(reply))
        for _, payload in events:
            self.assertNotIn("[pause]", json.dumps(payload, ensure_ascii=False))


@unittest.skipUnless(HAS_HTTPX, "httpx is not installed")
class LocalLarkTtsRequestTests(unittest.TestCase):
    """The Lark TTS request body must carry [pause] / [elong] verbatim."""

    def _run_synthesis(self, text: str):
        import httpx
        from unittest.mock import patch

        from realtime_audio_demo.services import speech as speech_module

        requests: list[dict] = []
        poll_states = iter(["204", "206", "200"])

        def handler(request: "httpx.Request") -> "httpx.Response":
            body = json.loads(request.content.decode("utf-8"))
            requests.append(body)
            if request.url.path.endswith(speech_module.LOCAL_TTS_SYNTHESIZE_PATH.lstrip("/")):
                return httpx.Response(
                    200,
                    headers={"responseCode": "200"},
                    json={"taskId": "task-1"},
                )
            code = next(poll_states)
            if code == "204":
                return httpx.Response(200, headers={"responseCode": "204"}, content=b"")
            return httpx.Response(
                200,
                headers={"responseCode": code},
                content=b"\x01\x02" if code == "206" else b"\x03\x04",
            )

        transport = httpx.MockTransport(handler)
        original_client = httpx.AsyncClient

        def client_factory(*args, **kwargs):
            kwargs.pop("trust_env", None)
            kwargs["transport"] = transport
            return original_client(*args, **kwargs)

        synthesizer = speech_module.LocalLarkTtsSpeechSynthesizer(
            base_url="http://127.0.0.1:9876",
            vid="test-vid",
            request_sample_rate="8000",
            playback_sample_rate=8000,
        )

        with patch.object(speech_module.httpx, "AsyncClient", client_factory), patch.object(
            speech_module, "LOCAL_TTS_POLL_INTERVAL", 0
        ):
            audio = asyncio.run(synthesizer.synthesize(model="m", text=text))
        return audio, requests

    def test_tags_reach_the_service_verbatim(self) -> None:
        reply = fixed_message("no_plate")

        audio, requests = self._run_synthesis(reply.tts_text)

        self.assertTrue(audio and audio.startswith("data:audio/wav;base64,"))
        self.assertEqual(requests[0]["text"], reply.tts_text)
        self.assertIn("[elong]", requests[0]["text"])
        self.assertIn("[pause]", requests[0]["text"])

    def test_polling_consumes_204_206_and_200(self) -> None:
        reply = fixed_message("invalid_plate")

        audio, requests = self._run_synthesis(reply.tts_text)

        # One create call plus three polls (204 -> 206 -> 200).
        self.assertEqual(len(requests), 4)
        self.assertTrue(audio)

    def test_plate_placeholders_are_not_split_by_tags(self) -> None:
        from realtime_audio_demo.services.plate_agent_messages import build_initial_success_reply
        from realtime_audio_demo.services.plate_agent_types import PlateAgentState

        reply = build_initial_success_reply(PlateAgentState(car_plate="京A12345"))
        _, requests = self._run_synthesis(reply.tts_text)

        self.assertIn("京A12345", requests[0]["text"])


async def _collect_sse(audio_routes, reply) -> list[dict]:
    events: list[dict] = []
    async for raw in audio_routes.stream_speech_sse_events(
        model="m",
        text=str(reply),
        segment="final",
        audio_index=1,
        tts_text=reply.tts_text,
    ):
        body = raw[len("data: ") :].strip()
        if body and body != "[DONE]":
            events.append(json.loads(body))
    return events


def _fake_ws_session(audio_routes):
    events: list[tuple[str, dict]] = []

    class _Session:
        model = "m"
        output_audio = True
        stream_speech_audio = True

    async def send_event(session, name, payload):  # noqa: ANN001
        events.append((name, payload))

    audio_routes.send_event = send_event
    return _Session(), events


if __name__ == "__main__":
    unittest.main()
