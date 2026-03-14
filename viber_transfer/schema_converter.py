"""Schema converter: Android Viber → iOS Viber.

Transforms parsed Android :class:`~viber_transfer.models.Conversation` /
:class:`~viber_transfer.models.Message` objects into the dict structures used
by the iOS Viber SQLite schema.
"""

from __future__ import annotations

import logging
from typing import Dict, List

from viber_transfer.models import Conversation, Message
from viber_transfer.utils import (
    datetime_to_apple_epoch,
    get_logger,
    unix_ms_to_apple_epoch,
)

logger = get_logger(__name__)

# ---------------------------------------------------------------------------
# iOS Viber message type codes
# ---------------------------------------------------------------------------

# Mapping from canonical type string to iOS Viber integer type code.
_IOS_TYPE_MAP: Dict[str, int] = {
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

# iOS Viber sender_id: 0 = incoming, 1 = outgoing.
_IOS_SENDER_OUTGOING = 1
_IOS_SENDER_INCOMING = 0


def unix_ms_to_apple_epoch_func(timestamp_ms: int) -> float:
    """Convert an Android Unix millisecond timestamp to Apple epoch seconds.

    This is a thin re-export of :func:`~viber_transfer.utils.unix_ms_to_apple_epoch`
    kept here so that schema_converter has a single named conversion function
    as specified in the requirements.

    Args:
        timestamp_ms: Milliseconds since 1970-01-01 (Unix epoch).

    Returns:
        Floating-point seconds since 2001-01-01 (Apple epoch).
    """
    return unix_ms_to_apple_epoch(timestamp_ms)


def convert_message(android_msg: Message) -> dict:  # type: ignore[type-arg]
    """Convert a single :class:`~viber_transfer.models.Message` to an iOS-style dict.

    The returned dict has keys that correspond to iOS Viber database columns:

    - ``message_id`` – original Android message ID (string)
    - ``chat_id`` – conversation ID
    - ``sender_id`` – 1 for outgoing, 0 for incoming
    - ``phone_number`` – sender phone number
    - ``display_name`` – sender display name
    - ``text`` – message body
    - ``timestamp`` – Apple epoch float
    - ``message_type`` – iOS type integer
    - ``attachments`` – list of attachment dicts

    Args:
        android_msg: A parsed Android :class:`~viber_transfer.models.Message`.

    Returns:
        Dict representing the iOS-formatted message row.
    """
    apple_ts = datetime_to_apple_epoch(android_msg.timestamp)
    ios_type = _IOS_TYPE_MAP.get(android_msg.message_type, 1)
    sender_id = _IOS_SENDER_OUTGOING if android_msg.is_outgoing else _IOS_SENDER_INCOMING

    attachments = [
        {
            "attachment_id": att.attachment_id,
            "file_path": att.file_path,
            "mime_type": att.mime_type,
            "file_size": att.file_size,
            "file_name": att.file_name,
        }
        for att in android_msg.attachments
    ]

    return {
        "message_id": android_msg.message_id,
        "chat_id": android_msg.conversation_id,
        "sender_id": sender_id,
        "phone_number": android_msg.sender.phone_number,
        "display_name": android_msg.sender.display_name,
        "text": android_msg.text,
        "timestamp": apple_ts,
        "message_type": ios_type,
        "attachments": attachments,
    }


def convert_conversation(android_conv: Conversation) -> dict:  # type: ignore[type-arg]
    """Convert a :class:`~viber_transfer.models.Conversation` to an iOS-style dict.

    The returned dict contains:

    - ``chat_id`` – conversation ID
    - ``is_group`` – boolean
    - ``group_name`` – group display name or ``None``
    - ``created_at`` – Apple epoch float or ``None``
    - ``participants`` – list of participant dicts
    - ``messages`` – list of converted message dicts (via :func:`convert_message`)

    Args:
        android_conv: A parsed Android :class:`~viber_transfer.models.Conversation`.

    Returns:
        Dict representing the iOS-formatted conversation.
    """
    created_at = (
        datetime_to_apple_epoch(android_conv.created_at)
        if android_conv.created_at is not None
        else None
    )

    participants = [
        {
            "user_id": p.user_id,
            "phone_number": p.phone_number,
            "display_name": p.display_name,
            "viber_id": p.viber_id,
        }
        for p in android_conv.participants
    ]

    messages = [convert_message(msg) for msg in android_conv.messages]

    return {
        "chat_id": android_conv.conversation_id,
        "is_group": android_conv.is_group,
        "group_name": android_conv.group_name,
        "created_at": created_at,
        "participants": participants,
        "messages": messages,
    }


def build_ios_viber_tables(conversations: List[Conversation]) -> dict:  # type: ignore[type-arg]
    """Build all iOS Viber table data from a list of conversations.

    This is the top-level conversion function that produces the data structures
    needed by :mod:`~viber_transfer.ios_backup_injector` to populate the iOS
    Viber SQLite database.

    The returned dict has the following top-level keys, each mapping to a list
    of row dicts ready to be inserted into the corresponding iOS table:

    - ``"chats"`` – one entry per conversation
    - ``"messages"`` – all messages across all conversations
    - ``"participants"`` – all participants across all conversations (de-duped
      at conversation level)
    - ``"contacts"`` – unique contacts across all conversations

    Args:
        conversations: List of Android :class:`~viber_transfer.models.Conversation`
            objects (the output of the Android parser).

    Returns:
        Dict with keys ``"chats"``, ``"messages"``, ``"participants"``,
        ``"contacts"``.
    """
    chats: list = []
    all_messages: list = []
    all_participants: list = []
    contacts_seen: Dict[str, dict] = {}  # phone → contact dict

    for conv in conversations:
        ios_conv = convert_conversation(conv)
        chats.append(
            {
                "chat_id": ios_conv["chat_id"],
                "is_group": int(ios_conv["is_group"]),
                "group_name": ios_conv["group_name"],
                "created_at": ios_conv["created_at"],
            }
        )

        for msg in ios_conv["messages"]:
            all_messages.append(msg)

        for participant in ios_conv["participants"]:
            all_participants.append(
                {
                    "chat_id": ios_conv["chat_id"],
                    **participant,
                }
            )
            phone = participant["phone_number"]
            if phone and phone not in contacts_seen:
                contacts_seen[phone] = {
                    "user_id": participant["user_id"],
                    "phone_number": phone,
                    "display_name": participant["display_name"],
                    "viber_id": participant["viber_id"],
                }

    logger.info(
        "Converted %d chats, %d messages, %d participants, %d contacts",
        len(chats),
        len(all_messages),
        len(all_participants),
        len(contacts_seen),
    )

    return {
        "chats": chats,
        "messages": all_messages,
        "participants": all_participants,
        "contacts": list(contacts_seen.values()),
    }
