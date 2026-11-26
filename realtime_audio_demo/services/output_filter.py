import json
import re
from dataclasses import dataclass
from typing import Any

from realtime_audio_demo.services.plate_agent_messages import (
    NORMALIZED_INVALID_PLATE_REPLY,
    NORMALIZED_SHORT_PLATE_REPLY,
    ReplyText,
)


_FIXED_REPLY_BY_PLAIN: dict[str, ReplyText] = {
    str(NORMALIZED_SHORT_PLATE_REPLY): NORMALIZED_SHORT_PLATE_REPLY,
    str(NORMALIZED_INVALID_PLATE_REPLY): NORMALIZED_INVALID_PLATE_REPLY,
}
_FIXED_REPLY_BY_MESSAGE_ID: dict[str, ReplyText] = {
    reply.message_id: reply for reply in _FIXED_REPLY_BY_PLAIN.values()
}


@dataclass
class AssistantOutput:
    raw_text: str
    display_text: str
    history_text: str
    speech_text: str
    is_json: bool
    tts_text: str = ""
    message_id: str = ""

    def __post_init__(self) -> None:
        if not self.tts_text:
            self.tts_text = str(getattr(self.speech_text, "tts_text", self.speech_text) or "")


def _fixed_message_id_for_json(value: dict[str, Any]) -> str:
    task_status = str(value.get("task_status") or "").strip().lower()
    if "normalized" not in value:
        return ""
    normalized = re.sub(r"\s+", "", str(value.get("normalized") or ""))
    if task_status == "need_more_info" and len(normalized) < 7:
        return "normalized_short_plate"
    if task_status == "invalid" and len(normalized) > 8:
        return "normalized_invalid_plate"
    return ""


def normalize_assistant_output(text: str | None) -> AssistantOutput:
    reply = _FIXED_REPLY_BY_PLAIN.get(str(text or "").strip())
    if reply is not None:
        plain = str(reply)
        return AssistantOutput(
            raw_text=plain,
            display_text=plain,
            history_text=plain,
            speech_text=plain,
            is_json=False,
            tts_text=reply.tts_text,
        )

    raw_text = (text or "").strip()
    if not raw_text:
        return AssistantOutput(
            raw_text="",
            display_text="",
            history_text="",
            speech_text="",
            is_json=False,
        )

    candidate = extract_json_candidate(raw_text)
    try:
        value = json.loads(candidate)
    except json.JSONDecodeError:
        return AssistantOutput(
            raw_text=raw_text,
            display_text=raw_text,
            history_text=raw_text,
            speech_text=raw_text,
            is_json=False,
        )

    if not isinstance(value, dict):
        display_text = format_json_value(value)
        return AssistantOutput(
            raw_text=raw_text,
            display_text=display_text,
            history_text=display_text,
            speech_text="",
            is_json=True,
        )

    display_text = format_json_value(value)
    message_id = _fixed_message_id_for_json(value)
    fixed_reply = _FIXED_REPLY_BY_MESSAGE_ID.get(message_id)
    speech_text = str(value.get("assistant_reply") or "").strip()

    return AssistantOutput(
        raw_text=raw_text,
        display_text=display_text,
        history_text=display_text,
        speech_text=speech_text,
        is_json=True,
        tts_text=fixed_reply.tts_text if fixed_reply is not None else speech_text,
        message_id=message_id,
    )


def extract_json_candidate(text: str, *, prefer_object: bool = False) -> str:
    stripped = text.strip()
    return extract_last_balanced_json_candidate(stripped, prefer_object=prefer_object) or stripped


def extract_last_balanced_json_candidate(text: str, *, prefer_object: bool = False) -> str:
    candidates: list[tuple[str, str]] = []
    stack: list[tuple[str, int]] = []
    closing_to_opening = {"}": "{", "]": "["}
    in_string = False
    escaped = False

    for index, char in enumerate(text):
        if in_string:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == '"':
                in_string = False
            continue

        if char == '"' and stack:
            in_string = True
            continue

        if char in {"{", "["}:
            stack.append((char, index))
            continue

        expected_opening = closing_to_opening.get(char)
        if not expected_opening:
            continue

        match_index = -1
        for stack_index in range(len(stack) - 1, -1, -1):
            if stack[stack_index][0] == expected_opening:
                match_index = stack_index
                break
        if match_index < 0:
            stack.clear()
            continue

        opening_char, start = stack[match_index]
        del stack[match_index:]
        candidate = text[start : index + 1].strip()
        try:
            json.loads(candidate)
        except json.JSONDecodeError:
            continue
        candidates.append((candidate, opening_char))

    if prefer_object:
        for candidate, opening_char in reversed(candidates):
            if opening_char == "{":
                return candidate
    if candidates:
        return candidates[-1][0]
    return ""


def parse_assistant_json(text: str | None) -> dict[str, Any] | None:
    candidate = extract_json_candidate((text or "").strip())
    if not candidate:
        return None
    try:
        value = json.loads(candidate)
    except json.JSONDecodeError:
        return None
    return value if isinstance(value, dict) else None


def validate_current_normalized_plate(text: str | None) -> str | None:
    data = parse_assistant_json(text)
    if not data or "normalized" not in data:
        return text

    normalized = re.sub(r"\s+", "", str(data.get("normalized") or ""))
    data["normalized"] = normalized
    plate_length = len(normalized)
    if plate_length in {7, 8}:
        return format_json_value(data)

    if plate_length < 7:
        data["task_status"] = "need_more_info"
        data["assistant_reply"] = NORMALIZED_SHORT_PLATE_REPLY
    else:
        data["task_status"] = "invalid"
        data["assistant_reply"] = NORMALIZED_INVALID_PLATE_REPLY

    return format_json_value(data)


def format_json_value(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, indent=2)
