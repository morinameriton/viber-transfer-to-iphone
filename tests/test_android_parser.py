"""Tests for viber_transfer.android_parser."""

from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from pathlib import Path

import pytest

from viber_transfer.android_parser import (
    _map_message_type,
    parse_android_databases,
)
from viber_transfer.utils import SchemaError


# ---------------------------------------------------------------------------
# Fixtures: mock databases
# ---------------------------------------------------------------------------


def _create_messages_db(path: Path, include_participants: bool = True) -> None:
    """Create a minimal viber_messages SQLite database for testing."""
    conn = sqlite3.connect(path)
    conn.execute(
        """CREATE TABLE conversations (
            _id         INTEGER PRIMARY KEY,
            date        INTEGER,
            group_type  INTEGER DEFAULT 0,
            group_name  TEXT
        )"""
    )
    conn.execute(
        """CREATE TABLE messages (
            _id             INTEGER PRIMARY KEY,
            conversation_id INTEGER,
            address         TEXT,
            date            INTEGER,
            body            TEXT,
            send_type       INTEGER DEFAULT 1,
            msg_type        INTEGER DEFAULT 1
        )"""
    )
    if include_participants:
        conn.execute(
            """CREATE TABLE participants (
                _id             INTEGER PRIMARY KEY,
                conversation_id INTEGER,
                number          TEXT
            )"""
        )

    # Seed data: one 1-on-1 conversation and one group conversation.
    conn.execute(
        "INSERT INTO conversations (_id, date, group_type, group_name) VALUES (1, 1686825600000, 0, NULL)"
    )
    conn.execute(
        "INSERT INTO conversations (_id, date, group_type, group_name) VALUES (2, 1686825600000, 1, 'My Group')"
    )

    # Messages for conv 1
    conn.execute(
        "INSERT INTO messages (_id, conversation_id, address, date, body, send_type, msg_type) "
        "VALUES (1, 1, '+19998887777', 1686825600000, 'Hello', 1, 1)"
    )
    conn.execute(
        "INSERT INTO messages (_id, conversation_id, address, date, body, send_type, msg_type) "
        "VALUES (2, 1, NULL, 1686825601000, 'Hi back!', 2, 1)"
    )
    # Photo message in conv 1
    conn.execute(
        "INSERT INTO messages (_id, conversation_id, address, date, body, send_type, msg_type) "
        "VALUES (3, 1, '+19998887777', 1686825602000, '', 1, 3)"
    )

    # Messages for conv 2 (group)
    conn.execute(
        "INSERT INTO messages (_id, conversation_id, address, date, body, send_type, msg_type) "
        "VALUES (4, 2, '+11112223333', 1686825700000, 'Hey group!', 1, 1)"
    )

    if include_participants:
        conn.execute(
            "INSERT INTO participants (_id, conversation_id, number) VALUES (1, 1, '+19998887777')"
        )
        conn.execute(
            "INSERT INTO participants (_id, conversation_id, number) VALUES (2, 2, '+11112223333')"
        )
        conn.execute(
            "INSERT INTO participants (_id, conversation_id, number) VALUES (3, 2, '+44445556666')"
        )

    conn.commit()
    conn.close()


def _create_data_db(path: Path) -> None:
    """Create a minimal viber_data SQLite database for testing."""
    conn = sqlite3.connect(path)
    conn.execute(
        """CREATE TABLE participants_info (
            _id           INTEGER PRIMARY KEY,
            number        TEXT,
            display_name  TEXT,
            viber_id      TEXT
        )"""
    )
    conn.execute(
        "INSERT INTO participants_info (_id, number, display_name, viber_id) "
        "VALUES (10, '+19998887777', 'Alice Smith', 'alice_viber')"
    )
    conn.execute(
        "INSERT INTO participants_info (_id, number, display_name, viber_id) "
        "VALUES (20, '+11112223333', 'Bob Jones', NULL)"
    )
    conn.commit()
    conn.close()


# ---------------------------------------------------------------------------
# Tests: _map_message_type
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "msg_type,expected",
    [
        (1, "text"),
        (2, "sticker"),
        (3, "photo"),
        (4, "video"),
        (5, "audio"),
        (6, "file"),
        (7, "location"),
        (8, "contact"),
        (9, "system"),
        (15, "system"),
        (None, "text"),
        (999, "text"),  # Unknown → default "text"
    ],
)
def test_map_message_type(msg_type: object, expected: str) -> None:
    assert _map_message_type(msg_type) == expected  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# Tests: parse_android_databases
# ---------------------------------------------------------------------------


def test_parse_basic(tmp_path: Path) -> None:
    msg_db = tmp_path / "viber_messages"
    _create_messages_db(msg_db)

    conversations = parse_android_databases(msg_db)
    assert len(conversations) == 2


def test_parse_with_data_db(tmp_path: Path) -> None:
    msg_db = tmp_path / "viber_messages"
    data_db = tmp_path / "viber_data"
    _create_messages_db(msg_db)
    _create_data_db(data_db)

    conversations = parse_android_databases(msg_db, data_db_path=data_db)
    assert len(conversations) == 2

    # Find the 1-on-1 conversation
    one_on_one = next(c for c in conversations if not c.is_group)
    assert len(one_on_one.messages) == 3

    # Verify contact enrichment
    incoming_msg = one_on_one.messages[0]
    assert incoming_msg.sender.display_name == "Alice Smith"
    assert incoming_msg.sender.viber_id == "alice_viber"


def test_parse_outgoing_message(tmp_path: Path) -> None:
    msg_db = tmp_path / "viber_messages"
    _create_messages_db(msg_db)
    conversations = parse_android_databases(msg_db)
    one_on_one = next(c for c in conversations if not c.is_group)
    # Message id=2 has send_type=2 (outgoing)
    outgoing = next(m for m in one_on_one.messages if m.message_id == "2")
    assert outgoing.is_outgoing is True


def test_parse_incoming_message(tmp_path: Path) -> None:
    msg_db = tmp_path / "viber_messages"
    _create_messages_db(msg_db)
    conversations = parse_android_databases(msg_db)
    one_on_one = next(c for c in conversations if not c.is_group)
    incoming = next(m for m in one_on_one.messages if m.message_id == "1")
    assert incoming.is_outgoing is False


def test_parse_group_conversation(tmp_path: Path) -> None:
    msg_db = tmp_path / "viber_messages"
    _create_messages_db(msg_db)
    conversations = parse_android_databases(msg_db)
    group = next(c for c in conversations if c.is_group)
    assert group.group_name == "My Group"
    assert len(group.messages) == 1


def test_parse_timestamps(tmp_path: Path) -> None:
    msg_db = tmp_path / "viber_messages"
    _create_messages_db(msg_db)
    conversations = parse_android_databases(msg_db)
    one_on_one = next(c for c in conversations if not c.is_group)
    msg = one_on_one.messages[0]
    assert msg.timestamp.tzinfo == timezone.utc
    assert msg.timestamp == datetime(2023, 6, 15, 10, 40, 0, tzinfo=timezone.utc)


def test_parse_missing_database(tmp_path: Path) -> None:
    from viber_transfer.utils import DatabaseNotFoundError

    with pytest.raises(DatabaseNotFoundError):
        parse_android_databases(tmp_path / "nonexistent_db")


def test_parse_missing_conversations_table(tmp_path: Path) -> None:
    msg_db = tmp_path / "bad_db"
    conn = sqlite3.connect(msg_db)
    conn.execute("CREATE TABLE messages (_id INTEGER PRIMARY KEY)")
    conn.commit()
    conn.close()

    with pytest.raises(SchemaError):
        parse_android_databases(msg_db)


def test_parse_without_participants_table(tmp_path: Path) -> None:
    msg_db = tmp_path / "viber_messages"
    _create_messages_db(msg_db, include_participants=False)
    # Should not raise even without participants table
    conversations = parse_android_databases(msg_db)
    assert len(conversations) == 2


def test_parse_photo_message_type(tmp_path: Path) -> None:
    msg_db = tmp_path / "viber_messages"
    _create_messages_db(msg_db)
    conversations = parse_android_databases(msg_db)
    one_on_one = next(c for c in conversations if not c.is_group)
    photo = next(m for m in one_on_one.messages if m.message_id == "3")
    assert photo.message_type == "photo"


def test_parse_null_body(tmp_path: Path) -> None:
    msg_db = tmp_path / "viber_messages"
    conn = sqlite3.connect(msg_db)
    conn.execute(
        """CREATE TABLE conversations (
            _id INTEGER PRIMARY KEY, date INTEGER, group_type INTEGER DEFAULT 0, group_name TEXT
        )"""
    )
    conn.execute(
        """CREATE TABLE messages (
            _id INTEGER PRIMARY KEY, conversation_id INTEGER,
            address TEXT, date INTEGER, body TEXT, send_type INTEGER, msg_type INTEGER
        )"""
    )
    conn.execute("INSERT INTO conversations VALUES (1, 1000, 0, NULL)")
    conn.execute("INSERT INTO messages VALUES (1, 1, '+1', 1000, NULL, 1, 1)")
    conn.commit()
    conn.close()

    conversations = parse_android_databases(msg_db)
    assert conversations[0].messages[0].text == ""
