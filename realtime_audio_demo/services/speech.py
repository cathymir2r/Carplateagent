from __future__ import annotations

import asyncio
import base64
from io import BytesIO
import logging
import time
from typing import Any, Callable
import uuid
import wave

import httpx

from realtime_audio_demo.config import (
    FINAL_MAX_TOKENS,
    LOCAL_TTS_APP_ID,
    LOCAL_TTS_BASE_URL,
    LOCAL_TTS_BIZ_NAME,
    LOCAL_TTS_CONNECT_TIMEOUT,
    LOCAL_TTS_POLL_INTERVAL,
    LOCAL_TTS_POLL_TIMEOUT,
    LOCAL_TTS_REQUEST_SAMPLE_RATE,
    LOCAL_TTS_REQUEST_TIMEOUT,
    LOCAL_TTS_RESULT_PATH,
    LOCAL_TTS_SCENE_ID,
    LOCAL_TTS_SYNTHESIZE_PATH,
    LOCAL_TTS_VID,
    TTS_API_BASE,
    TTS_BACKEND,
    TTS_MODEL,
    TTS_REF_AUDIO,
    TTS_REF_TEXT,
    TTS_RESPONSE_FORMAT,
    TTS_SAMPLE_RATE,
    TTS_STREAM_FORMAT,
    TTS_STREAM_RESPONSE_FORMAT,
    TTS_TASK_TYPE,
    TTS_VOICE,
)
from realtime_audio_demo.services.interfaces import ChatModel, SpeechSynthesizer
from realtime_audio_demo.services.model_gateway import model_gateway

logger = logging.getLogger("uvicorn.error")


def _join_url(base_url: str, path: str) -> str:
    return f"{base_url.rstrip('/')}/{path.lstrip('/')}"


def _response_code(response: httpx.Response) -> str:
    return str(response.headers.get("responseCode") or "").strip()


def _response_message(response: httpx.Response) -> str:
    return str(response.headers.get("responseMsg") or "").strip()


def _pcm16le_to_wav_bytes(pcm_bytes: bytes, sample_rate: int) -> bytes:
    """Wrap mono 16-bit little-endian PCM in a WAV container for non-stream playback."""
    buf = BytesIO()
    with wave.open(buf, "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(sample_rate)
        wf.writeframes(pcm_bytes)
    return buf.getvalue()


def build_tts_payload(*, model: str, text: str, response_format: str, stream: bool = False) -> dict[str, object]:
    """Payload for the legacy OpenAI-compatible TTS fallback."""
    payload: dict[str, object] = {
        "model": model,
        "input": text,
        "response_format": response_format,
    }
    if TTS_VOICE:
        payload["voice"] = TTS_VOICE
    if TTS_TASK_TYPE:
        payload["task_type"] = TTS_TASK_TYPE
    if TTS_REF_AUDIO:
        payload["ref_audio"] = TTS_REF_AUDIO
        payload.setdefault("task_type", "Base")
    if TTS_REF_TEXT:
        payload["ref_text"] = TTS_REF_TEXT
    if stream:
        payload["stream"] = True
        payload["stream_format"] = TTS_STREAM_FORMAT
    return payload


class LocalLarkTtsSpeechSynthesizer(SpeechSynthesizer):
    """Adapter for the new local Lark TTS service deployed at localhost:9876.

    Protocol source: stts_client_async_new.py supplied by the TTS team.

    1. POST /api/v1/lark/tts/synthesizeAsync to create the synthesis task.
    2. POST the returned JSON body to /api/v1/lark/tts/getSynthesizeResult.
    3. responseCode=204 means not ready; 206 carries an intermediate PCM chunk;
       200 carries the final PCM chunk and completes the request.

    The returned audio is treated as 8 kHz mono signed PCM16 little-endian, matching
    the supplied demo's len(audioBytes) / 16 millisecond calculation at 8 kHz.
    """

    def __init__(
        self,
        *,
        base_url: str,
        vid: str,
        request_sample_rate: str,
        playback_sample_rate: int,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.vid = vid.strip()
        self.request_sample_rate = request_sample_rate.strip()
        self.playback_sample_rate = int(playback_sample_rate)
        self.synthesize_url = _join_url(self.base_url, LOCAL_TTS_SYNTHESIZE_PATH)
        self.result_url = _join_url(self.base_url, LOCAL_TTS_RESULT_PATH)

    def _build_request(self, *, text: str, session_id: str) -> dict[str, object]:
        # Keep field names and values aligned with the TTS team's reference client.
        return {
            "text": text,
            "vid": self.vid,
            "spd": 0,
            "vol": 0,
            "pit": 0,
            "smooth": 2,
            "sessionId": session_id,
            "resType": 0,  # PCM
            "splitType": 1,
            "bizName": LOCAL_TTS_BIZ_NAME,
            "sampleRate": self.request_sample_rate,
            "appId": LOCAL_TTS_APP_ID,
            "sceneId": LOCAL_TTS_SCENE_ID,
            "requireTimeline": 0,
        }

    async def synthesize(self, *, model: str, text: str) -> str | None:
        """Return a WAV data URL for the non-streaming /api/chatbox/speech path."""
        chunks: list[bytes] = []

        async def collect(chunk: bytes) -> None:
            chunks.append(chunk)

        ok = await self.stream_synthesize(model=model, text=text, on_audio_chunk=collect)
        if not ok or not chunks:
            return None
        wav_bytes = _pcm16le_to_wav_bytes(b"".join(chunks), self.playback_sample_rate)
        audio_b64 = base64.b64encode(wav_bytes).decode("ascii")
        return f"data:audio/wav;base64,{audio_b64}"

    async def stream_synthesize(
        self,
        *,
        model: str,
        text: str,
        on_audio_chunk: Callable[[bytes], Any],
    ) -> bool:
        speech_text = text.strip()
        if not speech_text:
            return False
        if not self.base_url or not self.vid:
            logger.error("local Lark TTS is not configured: base_url=%r vid=%r", self.base_url, self.vid)
            return False

        session_id = f"carplate_{uuid.uuid4().hex}"
        request_data = self._build_request(text=speech_text, session_id=session_id)
        timeout = httpx.Timeout(LOCAL_TTS_REQUEST_TIMEOUT, connect=LOCAL_TTS_CONNECT_TIMEOUT)
        sent_audio = False
        started_at = time.monotonic()

        try:
            # localhost must not be accidentally routed through a corporate HTTP proxy.
            async with httpx.AsyncClient(timeout=timeout, trust_env=False) as client:
                response = await client.post(self.synthesize_url, json=request_data)
                if response.status_code >= 400:
                    logger.error(
                        "local Lark TTS synthesize HTTP error: session=%s status=%d body=%r",
                        session_id,
                        response.status_code,
                        response.content[:500],
                    )
                    return False

                code = _response_code(response)
                if code != "200":
                    logger.error(
                        "local Lark TTS synthesize failed: session=%s responseCode=%r responseMsg=%r",
                        session_id,
                        code,
                        _response_message(response),
                    )
                    return False

                try:
                    poll_request = response.json()
                except Exception as exc:
                    logger.error("local Lark TTS returned invalid task JSON: session=%s error=%s", session_id, exc)
                    return False

                logger.info(
                    "local Lark TTS started: session=%s vid=%s sample_rate=%s",
                    session_id,
                    self.vid,
                    self.request_sample_rate,
                )

                while True:
                    if time.monotonic() - started_at > LOCAL_TTS_POLL_TIMEOUT:
                        logger.error(
                            "local Lark TTS polling timeout: session=%s timeout=%.2fs",
                            session_id,
                            LOCAL_TTS_POLL_TIMEOUT,
                        )
                        return False

                    poll_response = await client.post(self.result_url, json=poll_request)
                    if poll_response.status_code >= 400:
                        logger.error(
                            "local Lark TTS poll HTTP error: session=%s status=%d body=%r",
                            session_id,
                            poll_response.status_code,
                            poll_response.content[:500],
                        )
                        return False

                    code = _response_code(poll_response)
                    if code == "204":
                        if LOCAL_TTS_POLL_INTERVAL > 0:
                            await asyncio.sleep(LOCAL_TTS_POLL_INTERVAL)
                        continue

                    if code in {"206", "200"}:
                        audio_bytes = poll_response.content
                        if audio_bytes:
                            await on_audio_chunk(audio_bytes)
                            sent_audio = True
                        if code == "200":
                            logger.info(
                                "local Lark TTS finished: session=%s elapsed_ms=%d sent_audio=%s",
                                session_id,
                                int((time.monotonic() - started_at) * 1000),
                                sent_audio,
                            )
                            return sent_audio
                        continue

                    logger.error(
                        "local Lark TTS poll failed: session=%s responseCode=%r responseMsg=%r",
                        session_id,
                        code,
                        _response_message(poll_response),
                    )
                    return False
        except Exception as exc:
            logger.error("local Lark TTS request failed: session=%s error=%s", session_id, exc)
            return False


class TtsApiSpeechSynthesizer(SpeechSynthesizer):
    """Legacy OpenAI-compatible /v1/audio/speech fallback."""

    def __init__(self, api_base: str) -> None:
        self.api_base = api_base.rstrip("/")

    async def synthesize(self, *, model: str, text: str) -> str | None:
        speech_text = text.strip()
        if not speech_text or not self.api_base:
            return None
        url = f"{self.api_base}/v1/audio/speech"
        payload = build_tts_payload(model=TTS_MODEL, text=speech_text, response_format=TTS_RESPONSE_FORMAT)
        try:
            async with httpx.AsyncClient(timeout=httpx.Timeout(30.0, connect=10.0)) as client:
                response = await client.post(url, json=payload)
        except Exception as exc:
            logger.error("TTS request failed: %s", exc)
            return None
        if response.status_code >= 400 or not response.content:
            logger.error("TTS error: status=%d body=%r", response.status_code, response.content[:500])
            return None
        audio_b64 = base64.b64encode(response.content).decode("ascii")
        return f"data:audio/{TTS_RESPONSE_FORMAT};base64,{audio_b64}"

    async def stream_synthesize(
        self,
        *,
        model: str,
        text: str,
        on_audio_chunk: Callable[[bytes], Any],
    ) -> bool:
        speech_text = text.strip()
        if not speech_text or not self.api_base:
            return False
        url = f"{self.api_base}/v1/audio/speech"
        payload = build_tts_payload(
            model=TTS_MODEL,
            text=speech_text,
            response_format=TTS_STREAM_RESPONSE_FORMAT,
            stream=True,
        )
        sent = False
        try:
            async with httpx.AsyncClient(timeout=httpx.Timeout(60.0, connect=10.0)) as client:
                async with client.stream("POST", url, json=payload) as response:
                    if response.status_code >= 400:
                        body = await response.aread()
                        logger.error("streaming TTS error: status=%d body=%r", response.status_code, body[:500])
                        return False
                    async for chunk in response.aiter_bytes():
                        if not chunk:
                            continue
                        await on_audio_chunk(chunk)
                        sent = True
        except Exception as exc:
            logger.error("streaming TTS request failed: %s", exc)
            return False
        return sent


class ModelSpeechSynthesizer(SpeechSynthesizer):
    """Fallback: use Qwen model output_audio."""

    def __init__(self, model_client: ChatModel) -> None:
        self.model_client = model_client

    async def synthesize(self, *, model: str, text: str) -> str | None:
        speech_text = text.strip()
        if not speech_text:
            return None
        result, status_code = await self.model_client.complete_text(
            model=model,
            text=speech_text,
            prompt="请把用户输入作为语音播报文本。只按原文朗读，不要解释、不要改写、不要补充任何内容。",
            history=[],
            max_tokens=FINAL_MAX_TOKENS,
            output_audio=True,
        )
        if status_code >= 400:
            return None
        audio_data_url = result.get("audio_data_url")
        return str(audio_data_url) if audio_data_url else None

    async def stream_synthesize(
        self,
        *,
        model: str,
        text: str,
        on_audio_chunk: Callable[[bytes], Any],
    ) -> bool:
        return False


def speech_backend_status() -> dict[str, object]:
    backend = TTS_BACKEND or "local_lark"
    status: dict[str, object] = {
        "backend": backend,
        "stream_format": TTS_STREAM_RESPONSE_FORMAT,
        "sample_rate": TTS_SAMPLE_RATE,
    }
    if backend in {"local_lark", "lark", "local_9876"}:
        status.update(
            {
                "base_url": LOCAL_TTS_BASE_URL,
                "synthesize_path": LOCAL_TTS_SYNTHESIZE_PATH,
                "result_path": LOCAL_TTS_RESULT_PATH,
                "vid": LOCAL_TTS_VID,
                "request_sample_rate": LOCAL_TTS_REQUEST_SAMPLE_RATE,
            }
        )
    elif backend in {"openai", "openai_compatible", "external"}:
        status["api_base"] = TTS_API_BASE or None
    return status


def create_speech_synthesizer() -> SpeechSynthesizer:
    backend = (TTS_BACKEND or "local_lark").strip().lower()

    if backend in {"local_lark", "lark", "local_9876"}:
        logger.info(
            "using local Lark TTS: base=%s vid=%s request_sr=%s playback_sr=%d",
            LOCAL_TTS_BASE_URL,
            LOCAL_TTS_VID,
            LOCAL_TTS_REQUEST_SAMPLE_RATE,
            TTS_SAMPLE_RATE,
        )
        return LocalLarkTtsSpeechSynthesizer(
            base_url=LOCAL_TTS_BASE_URL,
            vid=LOCAL_TTS_VID,
            request_sample_rate=LOCAL_TTS_REQUEST_SAMPLE_RATE,
            playback_sample_rate=TTS_SAMPLE_RATE,
        )

    if backend in {"openai", "openai_compatible", "external"}:
        if not TTS_API_BASE:
            logger.warning("TTS_BACKEND=%s but TTS_API_BASE is empty; falling back to Qwen output_audio", backend)
            return ModelSpeechSynthesizer(model_gateway)
        logger.info("using external OpenAI-compatible TTS API: %s", TTS_API_BASE)
        return TtsApiSpeechSynthesizer(TTS_API_BASE)

    if backend in {"qwen", "qwen_talker", "model"}:
        logger.info("using Qwen model-based TTS fallback")
        return ModelSpeechSynthesizer(model_gateway)

    logger.warning("unknown TTS_BACKEND=%r; using local Lark TTS", backend)
    return LocalLarkTtsSpeechSynthesizer(
        base_url=LOCAL_TTS_BASE_URL,
        vid=LOCAL_TTS_VID,
        request_sample_rate=LOCAL_TTS_REQUEST_SAMPLE_RATE,
        playback_sample_rate=TTS_SAMPLE_RATE,
    )


speech_synthesizer = create_speech_synthesizer()
