"""iOS backup injector.

Writes converted Viber messages into the SQLite database inside an iPhone
backup and updates ``Manifest.db`` to reflect the new file hashes and sizes.
"""

from __future__ import annotations

import logging
import shutil
import sqlite3
from pathlib import Path
from typing import Dict, List, Optional

from viber_transfer.ios_backup_reader import (
    VIBER_DB_RELATIVE_PATH,
    VIBER_DOMAIN,
    get_viber_db_file_id,
    resolve_file_path,
    validate_backup,
)
from viber_transfer.manifest_builder import (
    batch_upsert_entries,
    rebuild_viber_manifest_entries,
)
from viber_transfer.utils import (
    get_logger,
    sha256_hash_file,
)

logger = get_logger(__name__)

# ---------------------------------------------------------------------------
# iOS Viber SQLite schema
# ---------------------------------------------------------------------------

_IOS_VIBER_SCHEMA = """
CREATE TABLE IF NOT EXISTS ZVIBERCHAT (
    Z_PK          INTEGER PRIMARY KEY AUTOINCREMENT,
    ZCHATID       TEXT    UNIQUE,
    ZISGROUP      INTEGER DEFAULT 0,
    ZGROUPNAME    TEXT,
    ZCREATEDAT    REAL
);

CREATE TABLE IF NOT EXISTS ZVIBERMESSAGE (
    Z_PK          INTEGER PRIMARY KEY AUTOINCREMENT,
    ZMESSAGEID    TEXT    UNIQUE,
    ZCHATID       TEXT,
    ZSENDERID     INTEGER DEFAULT 0,
    ZPHONENUMBER  TEXT,
    ZDISPLAYNAME  TEXT,
    ZTEXT         TEXT,
    ZTIMESTAMP    REAL,
    ZMESSAGETYPE  INTEGER DEFAULT 1,
    FOREIGN KEY (ZCHATID) REFERENCES ZVIBERCHAT(ZCHATID)
);

CREATE TABLE IF NOT EXISTS ZVIBERPARTICIPANT (
    Z_PK          INTEGER PRIMARY KEY AUTOINCREMENT,
    ZCHATID       TEXT,
    ZUSERID       TEXT,
    ZPHONENUMBER  TEXT,
    ZDISPLAYNAME  TEXT,
    ZVIBERID      TEXT
);

CREATE TABLE IF NOT EXISTS ZVIBERCONTACT (
    Z_PK          INTEGER PRIMARY KEY AUTOINCREMENT,
    ZUSERID       TEXT    UNIQUE,
    ZPHONENUMBER  TEXT,
    ZDISPLAYNAME  TEXT,
    ZVIBERID      TEXT
);

CREATE TABLE IF NOT EXISTS ZVIBERATTACHMENT (
    Z_PK           INTEGER PRIMARY KEY AUTOINCREMENT,
    ZATTACHMENTID  TEXT    UNIQUE,
    ZMESSAGEID     TEXT,
    ZFILEPATH      TEXT,
    ZMIMETYPE      TEXT,
    ZFILESIZE      INTEGER DEFAULT 0,
    ZFILENAME      TEXT
);
"""


# ---------------------------------------------------------------------------
# Database initialisation
# ---------------------------------------------------------------------------


def _ensure_ios_schema(conn: sqlite3.Connection) -> None:
    """Create iOS Viber tables if they do not already exist.

    Args:
        conn: Open writable :class:`sqlite3.Connection` to the Viber DB.
    """
    conn.executescript(_IOS_VIBER_SCHEMA)
    conn.commit()


# ---------------------------------------------------------------------------
# Row insertion helpers
# ---------------------------------------------------------------------------


def _insert_chats(conn: sqlite3.Connection, chats: List[dict]) -> None:  # type: ignore[type-arg]
    """Insert chat rows into ``ZVIBERCHAT``.

    Args:
        conn: Open writable connection.
        chats: List of chat dicts from :func:`~viber_transfer.schema_converter.build_ios_viber_tables`.
    """
    for chat in chats:
        conn.execute(
            "INSERT OR IGNORE INTO ZVIBERCHAT (ZCHATID, ZISGROUP, ZGROUPNAME, ZCREATEDAT) "
            "VALUES (?, ?, ?, ?)",
            (chat["chat_id"], chat["is_group"], chat["group_name"], chat["created_at"]),
        )


def _insert_messages(conn: sqlite3.Connection, messages: List[dict]) -> None:  # type: ignore[type-arg]
    """Insert message rows and their attachments.

    Args:
        conn: Open writable connection.
        messages: List of message dicts.
    """
    for msg in messages:
        conn.execute(
            "INSERT OR IGNORE INTO ZVIBERMESSAGE "
            "(ZMESSAGEID, ZCHATID, ZSENDERID, ZPHONENUMBER, ZDISPLAYNAME, "
            " ZTEXT, ZTIMESTAMP, ZMESSAGETYPE) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (
                msg["message_id"],
                msg["chat_id"],
                msg["sender_id"],
                msg["phone_number"],
                msg["display_name"],
                msg["text"],
                msg["timestamp"],
                msg["message_type"],
            ),
        )
        for att in msg.get("attachments", []):
            conn.execute(
                "INSERT OR IGNORE INTO ZVIBERATTACHMENT "
                "(ZATTACHMENTID, ZMESSAGEID, ZFILEPATH, ZMIMETYPE, ZFILESIZE, ZFILENAME) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (
                    att["attachment_id"],
                    msg["message_id"],
                    att["file_path"],
                    att["mime_type"],
                    att["file_size"],
                    att["file_name"],
                ),
            )


def _insert_participants(conn: sqlite3.Connection, participants: List[dict]) -> None:  # type: ignore[type-arg]
    """Insert participant rows.

    Args:
        conn: Open writable connection.
        participants: List of participant dicts.
    """
    for p in participants:
        conn.execute(
            "INSERT INTO ZVIBERPARTICIPANT "
            "(ZCHATID, ZUSERID, ZPHONENUMBER, ZDISPLAYNAME, ZVIBERID) "
            "VALUES (?, ?, ?, ?, ?)",
            (
                p["chat_id"],
                p["user_id"],
                p["phone_number"],
                p["display_name"],
                p.get("viber_id"),
            ),
        )


def _insert_contacts(conn: sqlite3.Connection, contacts: List[dict]) -> None:  # type: ignore[type-arg]
    """Insert or update contact rows.

    Args:
        conn: Open writable connection.
        contacts: List of contact dicts.
    """
    for c in contacts:
        conn.execute(
            "INSERT OR IGNORE INTO ZVIBERCONTACT "
            "(ZUSERID, ZPHONENUMBER, ZDISPLAYNAME, ZVIBERID) "
            "VALUES (?, ?, ?, ?)",
            (c["user_id"], c["phone_number"], c["display_name"], c.get("viber_id")),
        )


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def write_messages_to_db(
    db_path: Path,
    ios_tables: dict,  # type: ignore[type-arg]
) -> None:
    """Write converted iOS Viber tables into a SQLite database file.

    Creates the file if it does not exist, initialises the iOS Viber schema,
    then inserts all rows.

    Args:
        db_path: Path to the target SQLite database (will be created if absent).
        ios_tables: Dict returned by
            :func:`~viber_transfer.schema_converter.build_ios_viber_tables`
            with keys ``"chats"``, ``"messages"``, ``"participants"``,
            ``"contacts"``.
    """
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path)
    try:
        _ensure_ios_schema(conn)
        _insert_chats(conn, ios_tables.get("chats", []))
        _insert_messages(conn, ios_tables.get("messages", []))
        _insert_participants(conn, ios_tables.get("participants", []))
        _insert_contacts(conn, ios_tables.get("contacts", []))
        conn.commit()
        logger.info("Wrote converted data to %s", db_path)
    finally:
        conn.close()


def inject_into_backup(
    backup_dir: Path,
    ios_tables: dict,  # type: ignore[type-arg]
    output_dir: Path,
) -> Path:
    """Inject converted Viber messages into an iPhone backup copy.

    Workflow:
    1. Validate the source backup.
    2. Copy the entire backup to *output_dir*.
    3. Locate (or create) the Viber DB inside the copy.
    4. Write converted messages into the DB.
    5. Update ``Manifest.db`` with new hashes and sizes.

    Args:
        backup_dir: Path to the source unencrypted iPhone backup directory.
        ios_tables: Converted data from
            :func:`~viber_transfer.schema_converter.build_ios_viber_tables`.
        output_dir: Destination directory for the modified backup.

    Returns:
        Path to the modified backup (i.e. *output_dir*).

    Raises:
        BackupNotFoundError: If *backup_dir* is invalid.
        EncryptedBackupError: If the backup is encrypted.
    """
    validate_backup(backup_dir)

    # --- Copy backup to output ---
    if output_dir.exists():
        shutil.rmtree(output_dir)
    shutil.copytree(backup_dir, output_dir)
    logger.info("Copied backup to %s", output_dir)

    # --- Locate / create the Viber DB inside the output backup ---
    file_id = get_viber_db_file_id()
    db_dest = resolve_file_path(output_dir, file_id)
    db_dest.parent.mkdir(parents=True, exist_ok=True)

    # --- Write converted messages ---
    write_messages_to_db(db_dest, ios_tables)

    # --- Update Manifest.db ---
    manifest_db_path = output_dir / "Manifest.db"
    entries = rebuild_viber_manifest_entries(
        viber_db_path=db_dest,
        domain=VIBER_DOMAIN,
        db_relative_path=VIBER_DB_RELATIVE_PATH,
    )
    batch_upsert_entries(manifest_db_path, entries)
    logger.info("Updated Manifest.db with new file entries")

    return output_dir
