"""Data models for Viber chat transfer.

Defines the core dataclasses used throughout the application to represent
users, messages, attachments, and conversations.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import List, Optional


@dataclass
class User:
    """Represents a Viber user / contact.

    Attributes:
        user_id: Internal unique identifier for the user.
        phone_number: Canonical phone number (e.g. "+12025551234").
        display_name: Human-readable name shown in chat.
        viber_id: Optional Viber-specific identifier (may be None).
    """

    user_id: str
    phone_number: str
    display_name: str
    viber_id: Optional[str] = None


@dataclass
class Attachment:
    """Represents a file attachment associated with a message.

    Attributes:
        attachment_id: Unique identifier for the attachment.
        file_path: Absolute or relative path to the file on-device.
        mime_type: MIME type string (e.g. "image/jpeg").
        file_size: Size in bytes.
        file_name: Original filename (basename).
    """

    attachment_id: str
    file_path: str
    mime_type: str
    file_size: int
    file_name: str


@dataclass
class Message:
    """Represents a single Viber chat message.

    Attributes:
        message_id: Unique identifier for this message.
        conversation_id: ID of the parent conversation.
        sender: The :class:`User` who sent the message.
        timestamp: UTC datetime of when the message was sent.
        text: Plain-text body of the message (may be empty for media).
        message_type: One of "text", "photo", "video", "sticker", "system",
            "audio", "file", or "location".
        attachments: List of :class:`Attachment` objects associated with the message.
        is_outgoing: ``True`` if the message was sent by the local user.
    """

    message_id: str
    conversation_id: str
    sender: User
    timestamp: datetime
    text: str
    message_type: str  # text, photo, video, sticker, system, audio, file, location
    attachments: List[Attachment] = field(default_factory=list)
    is_outgoing: bool = False


@dataclass
class Conversation:
    """Represents a Viber conversation (1-on-1 or group).

    Attributes:
        conversation_id: Unique identifier for this conversation.
        participants: List of :class:`User` objects in the conversation.
        messages: Ordered list of :class:`Message` objects.
        is_group: ``True`` for group conversations.
        group_name: Display name for group chats (``None`` for 1-on-1).
        created_at: Optional creation timestamp.
    """

    conversation_id: str
    participants: List[User]
    messages: List[Message]
    is_group: bool
    group_name: Optional[str] = None
    created_at: Optional[datetime] = None
