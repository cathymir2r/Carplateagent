# Local Lark TTS 9876 Integration

This merged project keeps the optimized Agent/dialogue implementation as the main code path and attaches the newly deployed TTS service only through `realtime_audio_demo/services/speech.py`.

## Runtime call chain

```text
PlateAgentResult.speech_text
  -> SpeechSynthesizer.stream_synthesize(...)
  -> POST http://127.0.0.1:9876/api/v1/lark/tts/synthesizeAsync
  -> poll /api/v1/lark/tts/getSynthesizeResult
  -> responseCode 206: forward PCM chunk immediately
  -> responseCode 200: forward final PCM chunk and finish
  -> tts_audio_delta WebSocket/SSE event
  -> browser WebAudio PCM playback
```

## Interface fields copied from the TTS team's reference client

- `vid`: `zsfb2057E` by default
- `resType`: `0` (PCM)
- `splitType`: `1`
- `bizName`: `ISPS`
- `sampleRate`: `8k`
- `appId`: `ISPS`
- `sceneId`: `ISPS`
- `requireTimeline`: `0`
- `spd`, `vol`, `pit`: `0`
- `smooth`: `2`

The browser playback rate is configured separately as integer `8000` Hz. Do not send `"8k"` to WebAudio as the playback sample rate.

## Default environment

```bash
export TTS_BACKEND=local_lark
export LOCAL_TTS_BASE_URL=http://127.0.0.1:9876
export LOCAL_TTS_VID=zsfb2057E
export LOCAL_TTS_REQUEST_SAMPLE_RATE=8k
export TTS_SAMPLE_RATE=8000
export TTS_STREAM_RESPONSE_FORMAT=pcm
```

## Start

```bash
./setup_uv.sh
./run_chatbox.sh
```

Then check:

```bash
curl http://127.0.0.1:55785/health
```

The `tts` section should show `backend: local_lark`, base URL `http://127.0.0.1:9876`, and sample rate `8000`.

## Important deployment condition

`127.0.0.1:9876` is correct only when the Agent service and the TTS service share the same container/network namespace (for example, the same container or the same Kubernetes Pod). If they are in separate Pods, change only `LOCAL_TTS_BASE_URL` to the corresponding Service address; Agent logic does not need to change.

## Legacy files

Files such as `config_tts.py`, `routes/audio_tts.py`, `routes/pages_tts.py`, `services/speech-tts.py`, and `services/text_chat_tts.py` are retained only as historical/experimental copies. The runtime imports the non-suffixed files:

- `realtime_audio_demo/config.py`
- `realtime_audio_demo/routes/audio.py`
- `realtime_audio_demo/routes/pages.py`
- `realtime_audio_demo/services/speech.py`

Do not modify the `_tts` copies for this deployment.
