"""Android Viber database parser.

Reads the SQLite databases extracted from an Android Viber installation and
converts them into the internal :mod:`~viber_transfer.models` dataclasses used
by the rest of the pipeline.
"""

from __future__ import annotations

import logging
import sqlite3
from pathlib import Path
from typing import Dict, List, Optional

from viber_transfer.models import Attachment, Conversation, Message, User
from viber_transfer.utils import (
    DatabaseNotFoundError,
    SchemaError,
    get_logger,
    open_db,
    table_exists,
    unix_ms_to_datetime,
)

logger = get_logger(__name__)

# ---------------------------------------------------------------------------
# Required tables in each database
# ---------------------------------------------------------------------------

REQUIRED_MESSAGES_TABLES = {"messages", "conversations"}
REQUIRED_DATA_TABLES = {"participants_info"}

# Android Viber send_type: 1 = incoming, 2 = outgoing (and variants ≥ 10).
_OUTGOING_SEND_TYPES = {2, 10, 11, 12, 13, 14, 15, 16, 17, 18, 19, 20}

# Android Viber msg_type values.
_MSG_TYPE_MAP: Dict[int, str] = {
    1: "text",
    2: "sticker",
    3: "photo",
    4: "video",
    5: "audio",
    6: "file",
    7: "location",
    8: "contact",
    9: "system",
    15: "system",
}


def _safe_str(value: object) -> str:
    """Return *value* as a stripped string or an empty string if None.

    Args:
        value: Any value from a SQLite row.

    Returns:
        Stripped string.
    """
    if value is None:
        return ""
    return str(value).strip()


def _map_message_type(msg_type: Optional[int]) -> str:
    """Convert an Android Viber message-type integer to a canonical string.

    Args:
        msg_type: Raw integer from the ``messages.msg_type`` column.

    Returns:
        One of ``"text"``, ``"sticker"``, ``"photo"``, ``"video"``,
        ``"audio"``, ``"file"``, ``"location"``, ``"contact"``, or
        ``"system"``.
    """
    if msg_type is None:
        return "text"
    return _MSG_TYPE_MAP.get(int(msg_type), "text")


def _validate_schema(conn: sqlite3.Connection, required: set[str], db_label: str) -> None:
    """Assert that all *required* tables exist in *conn*.

    Args:
        conn: Open database connection.
        required: Set of table names that must be present.
        db_label: Human-readable label used in error messages.

    Raises:
        SchemaError: If any required table is missing.
    """
    for table in required:
        if not table_exists(conn, table):
            raise SchemaError(
                f"Required table '{table}' is missing from {db_label}. "
                "Is this a valid Viber Android database?"
            )


# ---------------------------------------------------------------------------
# Participant / contact loading
# ---------------------------------------------------------------------------


def _load_participants_info(
    data_conn: Optional[sqlite3.Connection],
) -> Dict[str, User]:
    """Load phone-number → User mappings from the viber_data database.

    Args:
        data_conn: Open connection to ``viber_data``, or ``None`` if the
            database is unavailable (participants will still be created but
            with minimal info).

    Returns:
        Dict mapping canonical phone numbers to :class:`User` objects.
    """
    users: Dict[str, User] = {}
    if data_conn is None:
        return users

    if not table_exists(data_conn, "participants_info"):
        logger.warning("Table 'participants_info' not found in viber_data – skipping contacts.")
        return users

    cursor = data_conn.execute(
        "SELECT _id, number, display_name, viber_id FROM participants_info"
    )
    for row in cursor.fetchall():
        phone = _safe_str(row["number"])
        uid = str(row["_id"]) if row["_id"] is not None else phone
        display = _safe_str(row["display_name"]) or phone
        viber_id = _safe_str(row["viber_id"]) or None
        user = User(
            user_id=uid,
            phone_number=phone,
            display_name=display,
            viber_id=viber_id,
        )
        if phone:
            users[phone] = user
        # Also index by _id for cross-referencing
        users[uid] = user

    logger.debug("Loaded %d participant_info records", len(users))
    return users


def _load_conversation_participants(
    msg_conn: sqlite3.Connection,
) -> Dict[str, List[str]]:
    """Load conversation → list of phone numbers from ``participants`` table.

    Falls back gracefully when the ``participants`` table is absent.

    Args:
        msg_conn: Open connection to ``viber_messages``.

    Returns:
        Dict mapping conversation_id strings to lists of phone number strings.
    """
    result: Dict[str, List[str]] = {}
    if not table_exists(msg_conn, "participants"):
        logger.debug("'participants' table absent – participant data unavailable.")
        return result

    cursor = msg_conn.execute(
        "SELECT conversation_id, number FROM participants"
    )
    for row in cursor.fetchall():
        conv_id = str(row["conversation_id"])
        number = _safe_str(row["number"])
        result.setdefault(conv_id, []).append(number)
    return result


# ---------------------------------------------------------------------------
# Attachment helpers
# ---------------------------------------------------------------------------


def _load_attachment(
    msg_conn: sqlite3.Connection,
    message_id: str,
) -> Optional[Attachment]:
    """Attempt to load attachment metadata for *message_id* from ``messages_extra``.

    Args:
        msg_conn: Open connection to ``viber_messages``.
        message_id: The ``_id`` of the parent message.

    Returns:
        An :class:`Attachment` if found, otherwise ``None``.
    """
    if not table_exists(msg_conn, "messages_extra"):
        return None

    cursor = msg_conn.execute(
        "SELECT _id, uri, mime_type, size, file_name "
        "FROM messages_extra WHERE message_id=? LIMIT 1",
        (message_id,),
    )
    row = cursor.fetchone()
    if row is None:
        return None

    return Attachment(
        attachment_id=str(row["_id"]),
        file_path=_safe_str(row["uri"]),
        mime_type=_safe_str(row["mime_type"]) or "application/octet-stream",
        file_size=int(row["size"]) if row["size"] is not None else 0,
        file_name=_safe_str(row["file_name"]),
    )


# ---------------------------------------------------------------------------
# Message parsing
# ---------------------------------------------------------------------------


def _parse_messages(
    msg_conn: sqlite3.Connection,
    conversation_id: str,
    users_by_key: Dict[str, User],
    local_user: User,
) -> List[Message]:
    """Parse all messages for a given conversation from the database.

    Args:
        msg_conn: Open connection to ``viber_messages``.
        conversation_id: The conversation to filter by.
        users_by_key: Dict of users indexed by phone number or user_id.
        local_user: A placeholder :class:`User` representing the device owner.

    Returns:
        List of :class:`Message` objects ordered by timestamp.
    """
    cursor = msg_conn.execute(
        "SELECT _id, conversation_id, address, date, body, send_type, msg_type "
        "FROM messages WHERE conversation_id=? ORDER BY date ASC",
        (conversation_id,),
    )
    messages: List[Message] = []
    for row in cursor.fetchall():
        raw_id = str(row["_id"])
        address = _safe_str(row["address"])
        date_ms = int(row["date"]) if row["date"] is not None else 0
        body = _safe_str(row["body"])
        send_type = int(row["send_type"]) if row["send_type"] is not None else 1
        msg_type = row["msg_type"]

        is_outgoing = send_type in _OUTGOING_SEND_TYPES
        sender: User
        if is_outgoing:
            sender = local_user
        else:
            sender = users_by_key.get(address) or User(
                user_id=address or raw_id,
                phone_number=address,
                display_name=address,
            )

        attachment = _load_attachment(msg_conn, raw_id)
        attachments = [attachment] if attachment else []

        messages.append(
            Message(
                message_id=raw_id,
                conversation_id=conversation_id,
                sender=sender,
                timestamp=unix_ms_to_datetime(date_ms),
                text=body,
                message_type=_map_message_type(msg_type),
                attachments=attachments,
                is_outgoing=is_outgoing,
            )
        )
    return messages


# ---------------------------------------------------------------------------
# Conversation parsing
# ---------------------------------------------------------------------------


def _parse_conversations(
    msg_conn: sqlite3.Connection,
    users_by_key: Dict[str, User],
    conv_participants: Dict[str, List[str]],
    local_user: User,
) -> List[Conversation]:
    """Parse all conversations and their messages from the database.

    Args:
        msg_conn: Open connection to ``viber_messages``.
        users_by_key: Dict of users indexed by phone number or user_id.
        conv_participants: Dict of conversation_id → list of phone numbers.
        local_user: Placeholder :class:`User` for the device owner.

    Returns:
        List of :class:`Conversation` objects.
    """
    cursor = msg_conn.execute(
        "SELECT _id, date, group_type, group_name FROM conversations"
    )
    conversations: List[Conversation] = []
    for row in cursor.fetchall():
        conv_id = str(row["_id"])
        date_ms = int(row["date"]) if row["date"] is not None else 0
        group_type = int(row["group_type"]) if row["group_type"] is not None else 0
        group_name = _safe_str(row["group_name"]) or None

        is_group = group_type > 0

        # Build participant list
        phone_numbers = conv_participants.get(conv_id, [])
        participants: List[User] = []
        for phone in phone_numbers:
            user = users_by_key.get(phone) or User(
                user_id=phone,
                phone_number=phone,
                display_name=phone,
            )
            participants.append(user)
        # Always include local user
        if local_user not in participants:
            participants.append(local_user)

        messages = _parse_messages(msg_conn, conv_id, users_by_key, local_user)

        created_at = unix_ms_to_datetime(date_ms) if date_ms else None

        conversations.append(
            Conversation(
                conversation_id=conv_id,
                participants=participants,
                messages=messages,
                is_group=is_group,
                group_name=group_name,
                created_at=created_at,
            )
        )

    logger.info("Parsed %d conversations", len(conversations))
    return conversations


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def parse_android_databases(
    messages_db_path: Path,
    data_db_path: Optional[Path] = None,
    local_phone_number: str = "me",
    local_display_name: str = "Me",
) -> List[Conversation]:
    """Parse Viber chat data from Android SQLite databases.

    Args:
        messages_db_path: Path to the ``viber_messages`` SQLite file.
        data_db_path: Optional path to ``viber_data`` for contact enrichment.
        local_phone_number: Phone number of the device owner (used to
            construct the local-user placeholder).
        local_display_name: Display name for the device owner.

    Returns:
        List of :class:`~viber_transfer.models.Conversation` objects
        representing all parsed conversations.

    Raises:
        DatabaseNotFoundError: If *messages_db_path* does not exist.
        SchemaError: If a required table is missing from the messages database.
    """
    if not messages_db_path.exists():
        raise DatabaseNotFoundError(f"Messages database not found: {messages_db_path}")

    local_user = User(
        user_id="local_user",
        phone_number=local_phone_number,
        display_name=local_display_name,
    )

    # --- Load contact data (optional) ---
    use_data_db = data_db_path is not None and data_db_path.exists()

    if use_data_db:
        with open_db(data_db_path, read_only=True) as data_conn:  # type: ignore[arg-type]
            users_by_key = _load_participants_info(data_conn)
            with open_db(messages_db_path, read_only=True) as msg_conn:
                _validate_schema(msg_conn, REQUIRED_MESSAGES_TABLES, "viber_messages")
                conv_participants = _load_conversation_participants(msg_conn)
                conversations = _parse_conversations(
                    msg_conn, users_by_key, conv_participants, local_user
                )
    else:
        users_by_key = _load_participants_info(None)
        with open_db(messages_db_path, read_only=True) as msg_conn:
            _validate_schema(msg_conn, REQUIRED_MESSAGES_TABLES, "viber_messages")
            conv_participants = _load_conversation_participants(msg_conn)
            conversations = _parse_conversations(
                msg_conn, users_by_key, conv_participants, local_user
            )

    return conversations
