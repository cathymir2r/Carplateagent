# Fusion changelog

Base: `Carplate_Recognition-main-无tts.zip` (optimized Agent/dialogue version).

New TTS protocol source: `reference/stts_client_async_new.py`.

## Runtime files changed

1. `realtime_audio_demo/config.py`
   - Added `TTS_BACKEND=local_lark`.
   - Added localhost:9876 Lark TTS endpoint/VID/polling settings.
   - Set streaming playback sample rate default to 8000 Hz.

2. `realtime_audio_demo/services/speech.py`
   - Added `LocalLarkTtsSpeechSynthesizer` adapter.
   - Uses async `httpx`, not blocking `requests`.
   - Starts synthesis at `/api/v1/lark/tts/synthesizeAsync`.
   - Polls `/api/v1/lark/tts/getSynthesizeResult` exactly as in the supplied reference client.
   - Forwards `206` PCM chunks immediately and the final `200` chunk to the existing streaming playback path.
   - Wraps collected PCM in WAV only for the non-streaming `/api/chatbox/speech` route.
   - Retains OpenAI-compatible and Qwen fallback backends.

3. `realtime_audio_demo/routes/pages.py`
   - Adds TTS backend details to `/health` for deployment checks.

4. `run_chatbox.sh`, `setup_uv.sh`
   - Default to the new local TTS service and expose the required environment variables.

## Intentionally unchanged

The optimized Agent/dialogue chain was kept byte-for-byte unchanged, including:

- `realtime_audio_demo/routes/audio.py`
- `realtime_audio_demo/services/chatbox_application.py`
- `realtime_audio_demo/services/model_gateway.py`
- all `realtime_audio_demo/services/plate_agent*.py` modules
- `static/realtime-audio-client.js`
- `static/chatbox.html`

This means the fusion changes only the TTS backend boundary, not the optimized Agent state machine or dialogue logic.

## Validation completed

- Python `compileall`: passed.
- FastAPI app import and critical route registration: passed.
- Mock local TTS protocol test (`204 -> 206 -> 200`): passed.
- Request field check (`vid`, `sampleRate`, `resType`, `splitType`, `bizName`, `appId`, `sceneId`): passed.
- Non-streaming PCM-to-WAV path: passed.
- `/health` and `/api/chatbox/speech` FastAPI mock end-to-end test: passed.

The real `127.0.0.1:9876` engine cannot be reached from the packaging environment, so the final deployment still needs one live-container smoke test.

## Corrected TTS reference (2026-08-18)

The corrected TTS team client `stts_client_async_new(1).py` is now the source of truth.
The result polling endpoint is `/api/v1/lark/tts/getSynthesizeResult`; the earlier mistaken polling path has been removed.
The bundled reference client and runtime configuration have both been updated accordingly.
