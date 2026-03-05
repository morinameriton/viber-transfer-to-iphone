"""Tests for viber_transfer.models."""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from viber_transfer.models import Attachment, Conversation, Message, User


# ---------------------------------------------------------------------------
# User
# ---------------------------------------------------------------------------


def test_user_basic() -> None:
    user = User(
        user_id="1",
        phone_number="+12025551234",
        display_name="Alice",
    )
    assert user.user_id == "1"
    assert user.phone_number == "+12025551234"
    assert user.display_name == "Alice"
    assert user.viber_id is None


def test_user_with_viber_id() -> None:
    user = User(
        user_id="2",
        phone_number="+19998887777",
        display_name="Bob",
        viber_id="viber_bob_123",
    )
    assert user.viber_id == "viber_bob_123"


# ---------------------------------------------------------------------------
# Attachment
# ---------------------------------------------------------------------------


def test_attachment_basic() -> None:
    att = Attachment(
        attachment_id="att_1",
        file_path="/sdcard/viber/media/photo.jpg",
        mime_type="image/jpeg",
        file_size=102400,
        file_name="photo.jpg",
    )
    assert att.attachment_id == "att_1"
    assert att.mime_type == "image/jpeg"
    assert att.file_size == 102400


# ---------------------------------------------------------------------------
# Message
# ---------------------------------------------------------------------------


def test_message_defaults() -> None:
    user = User("u1", "+1234", "Alice")
    dt = datetime(2023, 6, 15, 12, 0, 0, tzinfo=timezone.utc)
    msg = Message(
        message_id="m1",
        conversation_id="c1",
        sender=user,
        timestamp=dt,
        text="Hello!",
        message_type="text",
    )
    assert msg.attachments == []
    assert msg.is_outgoing is False


def test_message_outgoing_with_attachment() -> None:
    user = User("u2", "+5678", "Bob")
    dt = datetime(2023, 6, 15, 13, 0, 0, tzinfo=timezone.utc)
    att = Attachment("a1", "/path/img.jpg", "image/jpeg", 5000, "img.jpg")
    msg = Message(
        message_id="m2",
        conversation_id="c1",
        sender=user,
        timestamp=dt,
        text="",
        message_type="photo",
        attachments=[att],
        is_outgoing=True,
    )
    assert msg.is_outgoing is True
    assert len(msg.attachments) == 1
    assert msg.attachments[0].mime_type == "image/jpeg"


# ---------------------------------------------------------------------------
# Conversation
# ---------------------------------------------------------------------------


def test_conversation_one_on_one() -> None:
    alice = User("u1", "+1111", "Alice")
    bob = User("u2", "+2222", "Bob")
    dt = datetime(2023, 1, 1, tzinfo=timezone.utc)
    msg = Message("m1", "conv1", alice, dt, "Hi!", "text")
    conv = Conversation(
        conversation_id="conv1",
        participants=[alice, bob],
        messages=[msg],
        is_group=False,
    )
    assert not conv.is_group
    assert conv.group_name is None
    assert len(conv.participants) == 2
    assert len(conv.messages) == 1


def test_conversation_group() -> None:
    users = [User(str(i), f"+{i}", f"User{i}") for i in range(3)]
    conv = Conversation(
        conversation_id="g1",
        participants=users,
        messages=[],
        is_group=True,
        group_name="Team Chat",
        created_at=datetime(2022, 12, 1, tzinfo=timezone.utc),
    )
    assert conv.is_group
    assert conv.group_name == "Team Chat"
    assert conv.created_at is not None


def test_conversation_no_messages() -> None:
    alice = User("u1", "+1", "Alice")
    conv = Conversation(
        conversation_id="empty",
        participants=[alice],
        messages=[],
        is_group=False,
    )
    assert conv.messages == []


def test_message_all_types() -> None:
    user = User("u", "+0", "Tester")
    dt = datetime(2024, 1, 1, tzinfo=timezone.utc)
    for mtype in ("text", "photo", "video", "sticker", "system", "audio", "file", "location"):
        msg = Message("id", "c", user, dt, "", mtype)
        assert msg.message_type == mtype
