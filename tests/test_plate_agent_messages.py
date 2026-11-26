import json
import re
import string
import unittest

from realtime_audio_demo.services.output_filter import normalize_assistant_output
from realtime_audio_demo.services.plate_agent_ack import ack_schedule_for_state
from realtime_audio_demo.services.plate_agent_history import build_plate_turn_summary
from realtime_audio_demo.services.plate_agent_messages import (
    MESSAGE_TEMPLATES,
    NORMALIZED_INVALID_PLATE_REPLY,
    NORMALIZED_SHORT_PLATE_REPLY,
    ReplyText,
    build_char_not_found_reply,
    build_confirmed_reply,
    build_edit_invalid_reply,
    build_initial_success_reply,
    reply_for_message,
)
from realtime_audio_demo.services.plate_agent_response import build_output_json
from realtime_audio_demo.services.plate_agent_types import PlateAgentResult, PlateAgentState


EXPECTED_MESSAGE_IDS = {
    "session_opening",
    "ack_initial",
    "ack_update",
    "no_plate",
    "invalid_plate",
    "edit_unclear",
    "edit_invalid",
    "edit_multi_step_partial",
    "edit_keep_current_plate",
    "edit_char_not_found",
    "edit_duplicate_char",
    "normalized_short_plate",
    "normalized_invalid_plate",
    "initial_success",
    "update_success",
    "partial_confirmation",
    "confirmed",
    "pending_confirmation",
    "no_pending_confirmation",
    "new_energy_confirmation",
    "pending_action_confirm",
    "pending_action_applied",
    "pending_action_discarded",
    "easyturn_ack",
}


class PlateAgentMessageTests(unittest.TestCase):
    def test_message_directory_has_stable_ids(self) -> None:
        self.assertEqual(set(MESSAGE_TEMPLATES), EXPECTED_MESSAGE_IDS)
        for message_id, template in MESSAGE_TEMPLATES.items():
            self.assertEqual(message_id, template.message_id)
            self.assertTrue(template.plain_template)
            self.assertTrue(template.tagged_template)

    def test_plain_and_tagged_templates_have_same_placeholders(self) -> None:
        for template in MESSAGE_TEMPLATES.values():
            plain = {
                field_name
                for _, field_name, _, _ in string.Formatter().parse(template.plain_template)
                if field_name
            }
            tagged = {
                field_name
                for _, field_name, _, _ in string.Formatter().parse(template.tagged_template)
                if field_name
            }
            self.assertEqual(plain, tagged, template.message_id)

    def test_tagged_templates_use_only_supported_tags(self) -> None:
        allowed = {"[pause]", "[elong]"}
        for template in MESSAGE_TEMPLATES.values():
            tags = set(re.findall(r"\[[^\]]+\]", template.tagged_template))
            self.assertTrue(tags <= allowed, template.message_id)
            self.assertNotRegex(template.tagged_template, r"\[#\d+\]|<break>|<prosody")

    def test_dynamic_values_are_formatted_into_both_variants(self) -> None:
        reply = reply_for_message(
            "pending_action_confirm",
            action_desc="将第3位替换为R",
        )
        self.assertIsInstance(reply, ReplyText)
        self.assertIn("将第3位替换为R", reply.plain)
        self.assertIn("将第3位替换为R", reply.tts_text)
        self.assertNotIn("[pause]", reply.plain)
        self.assertIn("[pause]", reply.tts_text)

        plate_reply = reply_for_message("initial_success", plate="京A12345")
        self.assertIn("京A12345", plate_reply.plain)
        self.assertIn("京A12345", plate_reply.tts_text)
        self.assertNotIn("京[pause]A12345", plate_reply.tts_text)

    def test_reply_text_keeps_plain_string_compatibility_when_concatenated(self) -> None:
        first = reply_for_message("no_plate")
        second = reply_for_message("pending_confirmation", pending="第3位是R")
        combined = first + second
        prefixed = "前缀" + second

        self.assertEqual(str(combined), first.plain + second.plain)
        self.assertEqual(combined.tts_text, first.tts_text + second.tts_text)
        self.assertEqual(str(prefixed), "前缀" + second.plain)
        self.assertEqual(prefixed.tts_text, "前缀" + second.tts_text)

    def test_agent_result_extracts_tagged_text_and_normalizes_speech_text(self) -> None:
        reply = build_confirmed_reply("京A12345")
        result = PlateAgentResult(
            text=build_output_json(
                task_status="confirmed",
                car_plate="京A12345",
                assistant_reply=reply,
                final_car_plate="京A12345",
            ),
            history_text="history",
            speech_text=reply,
            state=PlateAgentState(car_plate="京A12345", final_car_plate="京A12345"),
            latency_ms=1,
        )

        self.assertEqual(result.speech_text, str(reply))
        self.assertIs(type(result.speech_text), str)
        self.assertEqual(result.tts_text, reply.tts_text)
        self.assertNotIn("[pause]", result.speech_text)
        self.assertIn("[pause]", result.tts_text)

    def test_fixed_builders_preserve_dynamic_plain_and_tagged_text(self) -> None:
        state = PlateAgentState(car_plate="京A12345", vehicle_type="normal")
        replies = [
            build_initial_success_reply(state),
            build_edit_invalid_reply(state),
            build_char_not_found_reply("R"),
        ]
        for reply in replies:
            self.assertNotIn("[pause]", reply.plain)
            self.assertNotIn("[elong]", reply.plain)
            self.assertEqual(reply.plain, str(reply))
            self.assertTrue(reply.tts_text)

        self.assertIn("京A12345", replies[0].tts_text)
        self.assertIn("京A12345", replies[1].tts_text)
        self.assertIn("R", replies[2].tts_text)

    def test_json_and_turn_summary_remain_plain(self) -> None:
        reply = build_confirmed_reply("京A12345")
        output = build_output_json(
            task_status="confirmed",
            car_plate="京A12345",
            assistant_reply=reply,
            final_car_plate="京A12345",
        )
        payload = json.loads(output)
        self.assertNotIn("[pause]", output)
        self.assertNotIn("[elong]", output)
        self.assertEqual(payload["assistant_reply"], str(reply))

        result = PlateAgentResult(
            text=output,
            history_text=output,
            speech_text=reply,
            state=PlateAgentState(car_plate="京A12345", final_car_plate="京A12345"),
            latency_ms=1,
        )
        summary = build_plate_turn_summary(
            turn_index=1,
            before_state=PlateAgentState(),
            result=result,
        )
        self.assertNotIn("[pause]", summary)
        self.assertNotIn("[elong]", summary)
        self.assertIn(str(reply), summary)

    def test_ack_schedule_returns_plain_reply_with_tagged_tts(self) -> None:
        schedule = ack_schedule_for_state(PlateAgentState())
        self.assertEqual(len(schedule), 1)
        _, reply = schedule[0]
        self.assertIsInstance(reply, ReplyText)
        self.assertNotIn("[pause]", str(reply))
        self.assertIn("[pause]", reply.tts_text)

    def test_normalized_fixed_replies_keep_tts_text(self) -> None:
        for reply in (NORMALIZED_SHORT_PLATE_REPLY, NORMALIZED_INVALID_PLATE_REPLY):
            normalized = normalize_assistant_output(str(reply))
            self.assertEqual(normalized.speech_text, str(reply))
            self.assertEqual(normalized.tts_text, reply.tts_text)
            self.assertNotIn("[pause]", normalized.speech_text)
            self.assertIn("[pause]", normalized.tts_text)

    def test_nested_json_recovers_fixed_tts_by_business_fields(self) -> None:
        raw = json.dumps(
            {
                "task_status": "need_more_info",
                "assistant_reply": "这是一句不同的普通回复。",
                "normalized": "1 2 3",
            },
            ensure_ascii=False,
        )

        normalized = normalize_assistant_output(raw)

        self.assertTrue(normalized.is_json)
        self.assertEqual(normalized.speech_text, "这是一句不同的普通回复。")
        self.assertEqual(normalized.message_id, "normalized_short_plate")
        self.assertEqual(normalized.tts_text, NORMALIZED_SHORT_PLATE_REPLY.tts_text)
        self.assertNotIn("[pause]", normalized.display_text)
        self.assertNotIn("[elong]", normalized.display_text)
        self.assertNotIn("[pause]", normalized.history_text)
        self.assertNotIn("[elong]", normalized.history_text)

    def test_nested_json_invalid_plate_recovers_invalid_tts_template(self) -> None:
        raw = json.dumps(
            {
                "task_status": "invalid",
                "assistant_reply": "模型返回的普通文本。",
                "normalized": "ABCDEFGHI",
            },
            ensure_ascii=False,
        )

        normalized = normalize_assistant_output(raw)

        self.assertEqual(normalized.speech_text, "模型返回的普通文本。")
        self.assertEqual(normalized.message_id, "normalized_invalid_plate")
        self.assertEqual(normalized.tts_text, NORMALIZED_INVALID_PLATE_REPLY.tts_text)
        self.assertNotIn("[pause]", normalized.speech_text)
        self.assertIn("[pause]", normalized.tts_text)

    def test_regular_nested_json_speech_text_is_assistant_reply(self) -> None:
        raw = json.dumps(
            {
                "task_status": "need_confirmation",
                "assistant_reply": "请确认这个车牌。",
                "normalized": "京A12345",
            },
            ensure_ascii=False,
        )

        normalized = normalize_assistant_output(raw)

        self.assertEqual(normalized.speech_text, "请确认这个车牌。")
        self.assertEqual(normalized.tts_text, normalized.speech_text)
        self.assertEqual(normalized.message_id, "")


if __name__ == "__main__":
    unittest.main()
