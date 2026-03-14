"""Tests for viber_transfer.schema_converter."""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from viber_transfer.models import Attachment, Conversation, Message, User
from viber_transfer.schema_converter import (
    build_ios_viber_tables,
    convert_conversation,
    convert_message,
    unix_ms_to_apple_epoch_func,
)
from viber_transfer.utils import APPLE_EPOCH_OFFSET


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _make_user(uid: str = "u1", phone: str = "+1") -> User:
    return User(user_id=uid, phone_number=phone, display_name=f"User {uid}")


def _make_message(
    mid: str = "m1",
    cid: str = "c1",
    text: str = "Hello",
    msg_type: str = "text",
    is_outgoing: bool = False,
) -> Message:
    return Message(
        message_id=mid,
        conversation_id=cid,
        sender=_make_user(),
        timestamp=datetime(2023, 6, 15, 12, 0, 0, tzinfo=timezone.utc),
        text=text,
        message_type=msg_type,
        is_outgoing=is_outgoing,
    )


def _make_conversation(
    cid: str = "c1",
    is_group: bool = False,
    n_messages: int = 2,
) -> Conversation:
    participants = [_make_user("u1", "+1"), _make_user("u2", "+2")]
    messages = [_make_message(str(i), cid) for i in range(n_messages)]
    return Conversation(
        conversation_id=cid,
        participants=participants,
        messages=messages,
        is_group=is_group,
        group_name="Group" if is_group else None,
        created_at=datetime(2023, 1, 1, tzinfo=timezone.utc),
    )


# ---------------------------------------------------------------------------
# unix_ms_to_apple_epoch_func
# ---------------------------------------------------------------------------


def test_unix_ms_to_apple_epoch_zero() -> None:
    # 0 ms → negative Apple epoch (before 2001)
    result = unix_ms_to_apple_epoch_func(0)
    assert result == pytest.approx(-APPLE_EPOCH_OFFSET)


def test_unix_ms_to_apple_epoch_at_apple_epoch() -> None:
    ms = APPLE_EPOCH_OFFSET * 1000
    assert unix_ms_to_apple_epoch_func(ms) == pytest.approx(0.0)


def test_unix_ms_to_apple_epoch_positive() -> None:
    ms = (APPLE_EPOCH_OFFSET + 86400) * 1000  # One day after Apple epoch
    assert unix_ms_to_apple_epoch_func(ms) == pytest.approx(86400.0)


# ---------------------------------------------------------------------------
# convert_message
# ---------------------------------------------------------------------------


def test_convert_message_text() -> None:
    msg = _make_message(text="Hi there", msg_type="text")
    result = convert_message(msg)
    assert result["text"] == "Hi there"
    assert result["message_type"] == 1  # text → 1
    assert result["sender_id"] == 0  # incoming
    assert result["chat_id"] == "c1"
    assert result["message_id"] == "m1"


def test_convert_message_outgoing() -> None:
    msg = _make_message(is_outgoing=True)
    result = convert_message(msg)
    assert result["sender_id"] == 1  # outgoing


def test_convert_message_types() -> None:
    type_map = {
        "text": 1,
        "sticker": 5,
        "photo": 2,
        "video": 4,
        "audio": 6,
        "file": 7,
        "location": 8,
        "contact": 9,
        "system": 11,
    }
    for android_type, ios_code in type_map.items():
        msg = _make_message(msg_type=android_type)
        result = convert_message(msg)
        assert result["message_type"] == ios_code, f"Failed for type {android_type}"


def test_convert_message_timestamp() -> None:
    dt = datetime(2023, 6, 15, 12, 0, 0, tzinfo=timezone.utc)
    expected_apple = dt.timestamp() - APPLE_EPOCH_OFFSET
    msg = _make_message()
    result = convert_message(msg)
    assert result["timestamp"] == pytest.approx(expected_apple)


def test_convert_message_with_attachment() -> None:
    user = _make_user()
    att = Attachment("a1", "/path/file.jpg", "image/jpeg", 1024, "file.jpg")
    msg = Message(
        message_id="m_att",
        conversation_id="c1",
        sender=user,
        timestamp=datetime(2023, 6, 15, tzinfo=timezone.utc),
        text="",
        message_type="photo",
        attachments=[att],
    )
    result = convert_message(msg)
    assert len(result["attachments"]) == 1
    att_dict = result["attachments"][0]
    assert att_dict["attachment_id"] == "a1"
    assert att_dict["mime_type"] == "image/jpeg"


def test_convert_message_unknown_type() -> None:
    msg = _make_message(msg_type="unknown_custom_type")
    result = convert_message(msg)
    assert result["message_type"] == 1  # Falls back to text (1)


# ---------------------------------------------------------------------------
# convert_conversation
# ---------------------------------------------------------------------------


def test_convert_conversation_basic() -> None:
    conv = _make_conversation()
    result = convert_conversation(conv)
    assert result["chat_id"] == "c1"
    assert result["is_group"] is False
    assert len(result["messages"]) == 2
    assert len(result["participants"]) == 2


def test_convert_conversation_group() -> None:
    conv = _make_conversation(is_group=True)
    result = convert_conversation(conv)
    assert result["is_group"] is True
    assert result["group_name"] == "Group"


def test_convert_conversation_created_at() -> None:
    conv = _make_conversation()
    result = convert_conversation(conv)
    assert result["created_at"] is not None


def test_convert_conversation_no_created_at() -> None:
    conv = _make_conversation()
    conv.created_at = None
    result = convert_conversation(conv)
    assert result["created_at"] is None


# ---------------------------------------------------------------------------
# build_ios_viber_tables
# ---------------------------------------------------------------------------


def test_build_ios_viber_tables_keys() -> None:
    conversations = [_make_conversation("c1"), _make_conversation("c2")]
    result = build_ios_viber_tables(conversations)
    assert set(result.keys()) == {"chats", "messages", "participants", "contacts"}


def test_build_ios_viber_tables_counts() -> None:
    conversations = [_make_conversation("c1", n_messages=3), _make_conversation("c2", n_messages=5)]
    result = build_ios_viber_tables(conversations)
    assert len(result["chats"]) == 2
    assert len(result["messages"]) == 8  # 3 + 5


def test_build_ios_viber_tables_contacts_deduped() -> None:
    # Both conversations share the same two participants.
    conversations = [_make_conversation("c1"), _make_conversation("c2")]
    result = build_ios_viber_tables(conversations)
    # Contacts should be de-duplicated by phone number.
    phones = {c["phone_number"] for c in result["contacts"]}
    assert len(phones) == len(result["contacts"])


def test_build_ios_viber_tables_empty() -> None:
    result = build_ios_viber_tables([])
    assert result["chats"] == []
    assert result["messages"] == []
    assert result["participants"] == []
    assert result["contacts"] == []


def test_build_ios_viber_tables_chat_fields() -> None:
    conv = _make_conversation()
    result = build_ios_viber_tables([conv])
    chat = result["chats"][0]
    assert "chat_id" in chat
    assert "is_group" in chat
    assert "group_name" in chat
    assert "created_at" in chat
