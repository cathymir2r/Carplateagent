from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from realtime_audio_demo.config import EASYTURN_ACK_TEXT, PLATE_REPLY_INCLUDE_CONFIRMATION
from realtime_audio_demo.services.plate_agent_rules import clean_plate_text, describe_plate_char, with_relative_confusion_reasons
from realtime_audio_demo.services.plate_agent_types import PlateAgentState, PlateEditCommand




def _tag_easyturn_ack_text(plain_text: str) -> str:
    return plain_text.replace("嗯，", "嗯[pause]", 1)


@dataclass(frozen=True, slots=True)
class MessageTemplate:
    message_id: str
    plain_template: str
    tagged_template: str


class ReplyText(str):
    """Plain reply string carrying its synchronized tagged TTS text."""

    def __new__(cls, plain: str, tagged: str, message_id: str = "") -> "ReplyText":
        value = super().__new__(cls, plain)
        value.tagged = tagged
        value.message_id = message_id
        return value

    @property
    def plain(self) -> str:
        return str(self)

    @property
    def display_text(self) -> str:
        return str(self)

    @property
    def tts_text(self) -> str:
        return self.tagged

    def __add__(self, other: object) -> "ReplyText":
        if isinstance(other, ReplyText):
            tagged = self.tagged + other.tagged
            plain = str(self) + str(other)
        else:
            tagged = self.tagged + str(other)
            plain = str(self) + str(other)
        return ReplyText(plain, tagged)

    def __radd__(self, other: object) -> "ReplyText":
        if isinstance(other, ReplyText):
            return other + self
        text = str(other)
        return ReplyText(text + str(self), text + self.tagged)


# Every fixed reply has one stable id and two synchronized templates. Keep runtime
# placeholders identical in both templates so values are formatted independently.
MESSAGE_TEMPLATES: dict[str, MessageTemplate] = {
    "session_opening": MessageTemplate(
        "session_opening",
        "您好，麻烦您说一下您的车牌号。",
        "您好[pause]麻烦您说一下您的车牌号。",
    ),
    "ack_initial": MessageTemplate(
        "ack_initial",
        "好的您稍等一下，我帮您查一下车牌。",
        "好的[pause]您稍等一下，我帮您查一下车牌。",
    ),
    "ack_update": MessageTemplate(
        "ack_update",
        "好的您稍等一下，我按您刚才说的更新一下车牌。",
        "好的[pause]您稍等一下，我按您刚才说的更新一下车牌。",
    ),
    "no_plate": MessageTemplate(
        "no_plate",
        "我这边刚才没听到完整的车牌号，麻烦您再说一遍车牌。",
        "[elong]呃[elong]我这边刚才没听到完整的车牌号[pause]麻烦您再说一遍车牌。",
    ),
    "invalid_plate": MessageTemplate(
        "invalid_plate",
        "我这边看了一下，您刚才这个车牌号格式好像不太对，麻烦您重新说一下。",
        "[elong]呃[elong]我这边看了一下，您刚才这个车牌号格式好像不太对[pause]麻烦您重新说一下。",
    ),
    "edit_unclear": MessageTemplate(
        "edit_unclear",
        "我知道您是想改车牌，不过我还不太确定具体要改哪一位。您再告诉我是替换、插入还是删除哪一位，可以吗？",
        "[elong]呃[elong]我知道您是想改车牌[pause]不过我还不太确定具体要改哪一位。您再告诉我是替换、插入还是删除哪一位，可以吗？",
    ),
    "edit_invalid": MessageTemplate(
        "edit_invalid",
        "我按您刚才说的改了一下，不过修改后的车牌格式还是不太符合要求。当前先保留车牌{plate}。您再说明一下要改哪一处。",
        "[elong]呃[elong]我按您刚才说的改了一下，不过修改后的车牌格式还是不太符合要求[pause]当前先保留车牌{plate}。您再说明一下要改哪一处。",
    ),
    "edit_multi_step_partial": MessageTemplate(
        "edit_multi_step_partial",
        "这次修改包含多步内容，我先处理到目前能确定的位置，请您继续确认或说明剩余要改的部分。",
        "这次修改包含多步内容，我先处理到目前能确定的位置，请您继续确认或说明剩余要改的部分。",
    ),
    "edit_keep_current_plate": MessageTemplate(
        "edit_keep_current_plate",
        "好的，那当前先保留原来的车牌{plate}。您再确认一下是否正确。",
        "好的[pause]那当前先保留原来的车牌{plate}。您再确认一下是否正确。",
    ),
    "edit_char_not_found": MessageTemplate(
        "edit_char_not_found",
        "当前车牌里没有{char}，所以没有修改。当前仍保留原来的车牌。",
        "当前车牌里没有{char}，所以没有修改。当前仍保留原来的车牌。",
    ),
    "edit_duplicate_char": MessageTemplate(
        "edit_duplicate_char",
        "当前车牌里有多个{char}，请您说明要改前面的还是后面的。",
        "当前车牌里有多个{char}，请您说明要改前面的还是后面的。",
    ),
    "normalized_short_plate": MessageTemplate(
        "normalized_short_plate",
        "我这边听到的车牌位数好像不太对，麻烦您重新说一下完整车牌。",
        "[elong]呃[elong]我这边听到的车牌位数好像不太对[pause]麻烦您重新说一下完整车牌。",
    ),
    "normalized_invalid_plate": MessageTemplate(
        "normalized_invalid_plate",
        "这个车牌格式好像还不太对，麻烦您重新说一下。",
        "这个车牌格式好像还不太对[pause]麻烦您重新说一下。",
    ),
    "initial_success": MessageTemplate(
        "initial_success",
        "嗯，我这边识别到的车牌是{plate}，您帮我确认一下是不是这个。。",
        "嗯[pause]我这边识别到的车牌是{plate}，您帮我确认一下是不是这个。",
    ),
    "update_success": MessageTemplate(
        "update_success",
        "好的，我已经按您刚才说的改好了，现在车牌是{plate}。您再帮我确认一下是否正确。",
        "好的[pause]我已经按您刚才说的改好了，现在车牌是{plate}。您再帮我确认一下是否正确。",
    ),
    "partial_confirmation": MessageTemplate(
        "partial_confirmation",
        "好的，您刚才确认的内容我已经记下来了。",
        "好的[pause]您刚才确认的内容我已经记下来了。",
    ),
    "confirmed": MessageTemplate(
        "confirmed",
        "好的已经确认，您的车牌是{plate}。",
        "好的[pause]已经确认，您的车牌是{plate}",
    ),
    "pending_confirmation": MessageTemplate(
        "pending_confirmation",
        "嗯，还有地方我想再跟您确认一下：{pending}。",
        "嗯[pause]还有地方我想再跟您确认一下：{pending}。",
    ),
    "no_pending_confirmation": MessageTemplate(
        "no_pending_confirmation",
        "好的，您再帮我确认一下，{plate}是否正确。",
        "好的[pause]您再帮我确认一下，{plate}是否正确。",
    ),
    "new_energy_confirmation": MessageTemplate(
        "new_energy_confirmation",
        "另外我再确认一下，这是新能源号牌吗？",
        "另外我再确认一下[pause]这是新能源号牌吗？",
    ),
    "pending_action_confirm": MessageTemplate(
        "pending_action_confirm",
        "好的，我这边识别到您是想{action_desc}，要现在先执行这一步吗？",
        "好的[pause]我这边识别到您是想{action_desc}，要现在先执行这一步吗？",
    ),
    "pending_action_applied": MessageTemplate(
        "pending_action_applied",
        "好的，我先按您刚才说的改好了。现在车牌是{plate}，您再帮我确认一下是否正确。",
        "好的[pause]我先按您刚才说的改好了。现在车牌是{plate}，您再帮我确认一下是否正确。",
    ),
    "pending_action_discarded": MessageTemplate(
        "pending_action_discarded",
        "好的，那这一步我先不改，当前还是保留原来的车牌{plate}。您可以继续确认，或者告诉我还要怎么修改。",
        "好的[pause]那这一步我先不改，当前还是保留原来的车牌{plate}。您可以继续确认，或者告诉我还要怎么修改。",
    ),
    "easyturn_ack": MessageTemplate(
        "easyturn_ack",
        EASYTURN_ACK_TEXT,
        _tag_easyturn_ack_text(EASYTURN_ACK_TEXT),
    ),
}


def reply_for_message(message_id: str, **values: Any) -> ReplyText:
    template = MESSAGE_TEMPLATES[message_id]
    return ReplyText(
        plain=template.plain_template.format(**values),
        tagged=template.tagged_template.format(**values),
        message_id=message_id,
    )


def tagged_message_text(message_id: str) -> str:
    return MESSAGE_TEMPLATES[message_id].tagged_template


def fixed_message(message_id: str) -> ReplyText:
    return reply_for_message(message_id)


# Only placeholder-free templates can be recovered from their plain text. Templates
# carrying runtime values are excluded on purpose so no reply is ever matched by a
# sentence that embeds a plate, a pending char list or an action description.
_STATIC_TAGGED_BY_PLAIN: dict[str, str] = {
    template.plain_template: template.tagged_template
    for template in MESSAGE_TEMPLATES.values()
    if "{" not in template.plain_template
}


def tagged_for_plain(plain_text: str) -> str:
    """Tagged TTS text for a placeholder-free fixed reply, else the plain text itself."""
    text = str(plain_text or "").strip()
    return _STATIC_TAGGED_BY_PLAIN.get(text, text)


SESSION_OPENING_TEXT = fixed_message("session_opening")
SESSION_OPENING_TTS_TEXT = SESSION_OPENING_TEXT.tts_text
ACK_MESSAGE_IDS_BY_SCENE = {"initial": "ack_initial", "update": "ack_update"}
ACK_MESSAGES_BY_SCENE = {
    scene: (fixed_message(message_id),)
    for scene, message_id in ACK_MESSAGE_IDS_BY_SCENE.items()
}
ACK_TTS_MESSAGES_BY_SCENE = {
    scene: tuple(item.tts_text for item in messages)
    for scene, messages in ACK_MESSAGES_BY_SCENE.items()
}

NO_PLATE_REPLY = fixed_message("no_plate")
NO_PLATE_TTS_TEXT = NO_PLATE_REPLY.tts_text
INVALID_PLATE_REPLY = fixed_message("invalid_plate")
INVALID_PLATE_TTS_TEXT = INVALID_PLATE_REPLY.tts_text
EDIT_UNCLEAR_REPLY = fixed_message("edit_unclear")
EDIT_UNCLEAR_TTS_TEXT = EDIT_UNCLEAR_REPLY.tts_text
EDIT_INVALID_REPLY = MESSAGE_TEMPLATES["edit_invalid"].plain_template
EDIT_MULTI_STEP_PARTIAL_REPLY = fixed_message("edit_multi_step_partial")
EDIT_KEEP_CURRENT_PLATE_TEMPLATE = MESSAGE_TEMPLATES["edit_keep_current_plate"].plain_template
EDIT_CHAR_NOT_FOUND_TEMPLATE = MESSAGE_TEMPLATES["edit_char_not_found"].plain_template
EDIT_DUPLICATE_CHAR_TEMPLATE = MESSAGE_TEMPLATES["edit_duplicate_char"].plain_template
NORMALIZED_SHORT_PLATE_REPLY = fixed_message("normalized_short_plate")
NORMALIZED_INVALID_PLATE_REPLY = fixed_message("normalized_invalid_plate")
INITIAL_SUCCESS_TEMPLATE = MESSAGE_TEMPLATES["initial_success"].plain_template
UPDATE_SUCCESS_TEMPLATE = MESSAGE_TEMPLATES["update_success"].plain_template
PARTIAL_CONFIRMATION_TEMPLATE = MESSAGE_TEMPLATES["partial_confirmation"].plain_template
CONFIRMED_REPLY_TEMPLATE = MESSAGE_TEMPLATES["confirmed"].plain_template
PENDING_CONFIRMATION_TEMPLATE = MESSAGE_TEMPLATES["pending_confirmation"].plain_template
NO_PENDING_CONFIRMATION_TEMPLATE = MESSAGE_TEMPLATES["no_pending_confirmation"].plain_template
NEW_ENERGY_CONFIRMATION_TEXT = fixed_message("new_energy_confirmation")
PENDING_ACTION_CONFIRM_TEMPLATE = MESSAGE_TEMPLATES["pending_action_confirm"].plain_template
PENDING_ACTION_APPLIED_TEMPLATE = MESSAGE_TEMPLATES["pending_action_applied"].plain_template
PENDING_ACTION_DISCARDED_TEMPLATE = MESSAGE_TEMPLATES["pending_action_discarded"].plain_template


def describe_edit_command(cmd: PlateEditCommand) -> str:
    if cmd.action == "replace_position" and cmd.position and cmd.value:
        return f"将第{cmd.position}位替换为{cmd.value}"
    if cmd.action == "replace_char" and cmd.old_value and cmd.value:
        return f"将{cmd.old_value}替换为{cmd.value}"
    if cmd.action == "insert_position" and cmd.position and cmd.value:
        relation = "之后" if cmd.relation == "after" else "之前"
        return f"在第{cmd.position}位{relation}插入{cmd.value}"
    if cmd.action == "delete_position" and cmd.position:
        return f"删除第{cmd.position}位"
    if cmd.action == "replace_substring" and cmd.old_value and cmd.new_value:
        return f"将{cmd.old_value}改为{cmd.new_value}"
    return ""


def describe_edit_commands(commands: list[PlateEditCommand]) -> str:
    parts = [describe_edit_command(cmd) for cmd in commands if cmd.action not in ("none", "unknown")]
    return "，".join(p for p in parts if p)


def build_pending_action_confirm_reply(old_plate: str, commands: list[PlateEditCommand], new_plate: str) -> ReplyText:  # noqa: ARG001
    action_desc = describe_edit_commands(commands)
    return reply_for_message("pending_action_confirm", action_desc=action_desc)


def build_pending_action_applied_reply(state: PlateAgentState) -> ReplyText:
    return with_pending_confirmation(reply_for_message("pending_action_applied", plate=display_plate(state)), state)


def build_pending_action_discarded_reply(plate: str) -> ReplyText:
    return reply_for_message("pending_action_discarded", plate=clean_plate_text(plate))


def build_initial_success_reply(state: PlateAgentState) -> ReplyText:
    return with_pending_confirmation(reply_for_message("initial_success", plate=display_plate(state)), state)


def build_update_success_reply(state: PlateAgentState) -> ReplyText:
    return with_pending_confirmation(reply_for_message("update_success", plate=display_plate(state)), state)


def build_partial_confirmation_reply(state: PlateAgentState) -> ReplyText:
    return with_pending_confirmation(reply_for_message("partial_confirmation"), state)


def build_confirmed_reply(plate: str) -> ReplyText:
    return reply_for_message("confirmed", plate=clean_plate_text(plate))


def build_edit_invalid_reply(state: PlateAgentState) -> ReplyText:
    return with_pending_confirmation(reply_for_message("edit_invalid", plate=display_plate(state)), state)


def build_edit_unclear_reply(state: PlateAgentState, base_reply: str | ReplyText | None = None) -> ReplyText:
    if base_reply is None:
        base = reply_for_message("edit_unclear")
    elif isinstance(base_reply, ReplyText):
        base = base_reply
    else:
        base = ReplyText(plain=str(base_reply), tagged=str(base_reply))
    return with_pending_confirmation(base, state)


def build_keep_current_plate_reply(plate: str) -> ReplyText:
    return reply_for_message("edit_keep_current_plate", plate=clean_plate_text(plate))


def build_char_not_found_reply(value: str) -> ReplyText:
    return reply_for_message("edit_char_not_found", char=describe_plate_char(value))


def build_duplicate_char_reply(value: str) -> ReplyText:
    return reply_for_message("edit_duplicate_char", char=describe_plate_char(value))


def build_fixed_reply(state: PlateAgentState, *, changed: bool, scene: str = "") -> ReplyText:
    if scene == "initial_success":
        return build_initial_success_reply(state)
    if scene == "partial_confirmation":
        return build_partial_confirmation_reply(state)
    if scene == "update_success" or changed:
        return build_update_success_reply(state)
    return build_edit_unclear_reply(state)


def with_pending_confirmation(base_reply: ReplyText, state: PlateAgentState) -> ReplyText:
    reply = ReplyText(ensure_sentence(base_reply.plain), ensure_sentence(base_reply.tagged))
    if not PLATE_REPLY_INCLUDE_CONFIRMATION:
        return reply
    pending = pending_confirmation_text(state)
    if pending:
        reply += reply_for_message("pending_confirmation", pending=pending)
    else:
        reply += reply_for_message("no_pending_confirmation", plate=display_plate(state))
    if state.vehicle_type == "new_energy":
        reply += reply_for_message("new_energy_confirmation")
    return reply


def pending_confirmation_text(state: PlateAgentState) -> str:
    descriptions = pending_confirmation_descriptions(state)
    return "，".join(descriptions)


def pending_confirmation_descriptions(state: PlateAgentState) -> list[str]:
    descriptions: list[str] = []
    items = state.need_confirm_chars or with_relative_confusion_reasons(state.car_plate, state.confusions)
    seen_positions: set[int] = set()
    for item in items:
        position = int(getattr(item, "position", 0) or 0)
        value = str(getattr(item, "value", "") or "").strip()
        if position <= 0 or not value or position in seen_positions:
            continue
        seen_positions.add(position)
        descriptions.append(f"第{position}位是{describe_plate_char(value)}")
    return descriptions


def display_plate(state: PlateAgentState | Any) -> str:
    if isinstance(state, PlateAgentState):
        return clean_plate_text(state.car_plate) or "当前车牌"
    return clean_plate_text(state) or "当前车牌"


def ensure_sentence(value: str) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    if not text.endswith(("。", "！", "？", ".", "!", "?")):
        text += "。"
    return text
