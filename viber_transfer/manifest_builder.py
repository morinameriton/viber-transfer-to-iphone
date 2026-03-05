"""Manifest builder for iOS backup metadata.

Handles creation and update of ``Manifest.db`` entries after injecting a
modified Viber database into an iPhone backup.  File metadata blobs are
encoded as binary plists compatible with NSKeyedArchiver expectations.
"""

from __future__ import annotations

import logging
import plistlib
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional

from viber_transfer.utils import (
    compute_file_id,
    get_logger,
    sha256_hash_file,
    sha1_hash_file,
)

logger = get_logger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

VIBER_DOMAIN = "AppDomain-com.viber"

# Flag values used by iOS backups.
FLAGS_FILE = 1
FLAGS_DIRECTORY = 2

# Manifest.db schema used when creating a fresh database.
_MANIFEST_DB_SCHEMA = """
CREATE TABLE IF NOT EXISTS Files (
    fileID        TEXT PRIMARY KEY,
    domain        TEXT,
    relativePath  TEXT,
    flags         INTEGER,
    file          BLOB
);
CREATE TABLE IF NOT EXISTS Properties (
    key   TEXT PRIMARY KEY,
    value BLOB
);
"""


# ---------------------------------------------------------------------------
# Metadata blob serialisation
# ---------------------------------------------------------------------------


def _build_file_metadata_blob(
    relative_path: str,
    domain: str,
    file_size: int,
    sha256_hash: str,
    modification_date: datetime,
    flags: int = FLAGS_FILE,
) -> bytes:
    """Encode per-file metadata as a binary plist blob.

    The blob follows a simplified NSKeyedArchiver-compatible structure that is
    sufficient for iTunes / Finder to accept the backup without errors.

    Args:
        relative_path: Relative path of the file within the app domain.
        domain: Backup domain string (e.g. ``"AppDomain-com.viber"``).
        file_size: File size in bytes.
        sha256_hash: Lowercase hex SHA-256 digest of the file.
        modification_date: UTC datetime of last modification.
        flags: 1 for a regular file, 2 for a directory.

    Returns:
        Binary plist-encoded bytes for the ``file`` column of ``Manifest.db``.
    """
    # Ensure modification_date is UTC-aware then convert to naive UTC for plistlib,
    # which requires naive datetimes and internally treats them as UTC.
    if modification_date.tzinfo is None:
        modification_date = modification_date.replace(tzinfo=timezone.utc)
    naive_date = modification_date.replace(tzinfo=None)

    data: Dict[str, object] = {
        "$version": 100000,
        "$archiver": "NSKeyedArchiver",
        "$top": {"root": plistlib.UID(1)},
        "$objects": [
            "$null",
            {
                "$class": plistlib.UID(2),
                "Birth": naive_date,
                "Checksum": bytes.fromhex(sha256_hash),
                "Domain": domain,
                "Flags": flags,
                "GroupID": 0,
                "InodeNumber": 0,
                "LastModified": naive_date,
                "LastStatusChange": naive_date,
                "Mode": 33188 if flags == FLAGS_FILE else 16877,
                "Path": relative_path,
                "ProtectionClass": 0,
                "RelativePath": relative_path,
                "Size": file_size,
                "UserID": 0,
            },
            {
                "$classname": "MBFile",
                "$classes": ["MBFile", "NSObject"],
            },
        ],
    }
    return plistlib.dumps(data, fmt=plistlib.FMT_BINARY)


def _build_directory_metadata_blob(
    relative_path: str,
    domain: str,
    modification_date: datetime,
) -> bytes:
    """Build a metadata blob for a directory entry.

    Args:
        relative_path: Directory path relative to the app domain root.
        domain: Backup domain string.
        modification_date: UTC datetime of last modification.

    Returns:
        Binary plist-encoded bytes.
    """
    return _build_file_metadata_blob(
        relative_path=relative_path,
        domain=domain,
        file_size=0,
        sha256_hash="0" * 64,
        modification_date=modification_date,
        flags=FLAGS_DIRECTORY,
    )


# ---------------------------------------------------------------------------
# Manifest.db operations
# ---------------------------------------------------------------------------


def create_manifest_db(db_path: Path) -> None:
    """Create a fresh ``Manifest.db`` with the standard iOS schema.

    Args:
        db_path: Path where the new ``Manifest.db`` should be created.
    """
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path)
    try:
        conn.executescript(_MANIFEST_DB_SCHEMA)
        conn.commit()
    finally:
        conn.close()
    logger.debug("Created Manifest.db at %s", db_path)


def upsert_file_entry(
    conn: sqlite3.Connection,
    file_id: str,
    domain: str,
    relative_path: str,
    flags: int,
    file_blob: bytes,
) -> None:
    """Insert or replace a single file entry in ``Manifest.db``.

    Args:
        conn: Open writable :class:`sqlite3.Connection` to ``Manifest.db``.
        file_id: SHA-1 hex file identifier.
        domain: Backup domain string.
        relative_path: File path relative to the domain root.
        flags: 1 for file, 2 for directory.
        file_blob: Serialised metadata plist blob.
    """
    conn.execute(
        "INSERT OR REPLACE INTO Files (fileID, domain, relativePath, flags, file) "
        "VALUES (?, ?, ?, ?, ?)",
        (file_id, domain, relative_path, flags, file_blob),
    )


def batch_upsert_entries(
    manifest_db_path: Path,
    entries: List[Dict[str, object]],
) -> None:
    """Upsert multiple file entries into ``Manifest.db`` in a single transaction.

    Args:
        manifest_db_path: Path to the ``Manifest.db`` file.
        entries: List of entry dicts with keys matching the ``Files`` columns:
            ``fileID``, ``domain``, ``relativePath``, ``flags``, ``file``.
    """
    conn = sqlite3.connect(manifest_db_path)
    try:
        for entry in entries:
            conn.execute(
                "INSERT OR REPLACE INTO Files (fileID, domain, relativePath, flags, file) "
                "VALUES (:fileID, :domain, :relativePath, :flags, :file)",
                entry,
            )
        conn.commit()
        logger.debug("Upserted %d entries into Manifest.db", len(entries))
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# High-level manifest building
# ---------------------------------------------------------------------------


def build_manifest_entry(
    file_path: Path,
    domain: str,
    relative_path: str,
    modification_date: Optional[datetime] = None,
) -> Dict[str, object]:
    """Build a complete ``Files`` row dict for a single file.

    Computes the fileID (SHA-1 of domain–relativePath), SHA-256 hash of the
    file, and serialises the metadata blob.

    Args:
        file_path: Path to the file on the local filesystem.
        domain: Backup domain string.
        relative_path: Relative path inside the domain.
        modification_date: Modification timestamp.  Defaults to *now* if
            ``None``.

    Returns:
        Dict with keys ``fileID``, ``domain``, ``relativePath``, ``flags``,
        ``file``.
    """
    if modification_date is None:
        modification_date = datetime.now(tz=timezone.utc)

    file_id = compute_file_id(domain, relative_path)
    file_size = file_path.stat().st_size
    sha256 = sha256_hash_file(file_path)

    blob = _build_file_metadata_blob(
        relative_path=relative_path,
        domain=domain,
        file_size=file_size,
        sha256_hash=sha256,
        modification_date=modification_date,
    )

    return {
        "fileID": file_id,
        "domain": domain,
        "relativePath": relative_path,
        "flags": FLAGS_FILE,
        "file": blob,
    }


def build_directory_entry(
    domain: str,
    relative_path: str,
    modification_date: Optional[datetime] = None,
) -> Dict[str, object]:
    """Build a ``Files`` row dict for a directory entry.

    Args:
        domain: Backup domain string.
        relative_path: Relative directory path inside the domain.
        modification_date: Modification timestamp.  Defaults to *now*.

    Returns:
        Dict with keys ``fileID``, ``domain``, ``relativePath``, ``flags``,
        ``file``.
    """
    if modification_date is None:
        modification_date = datetime.now(tz=timezone.utc)

    file_id = compute_file_id(domain, relative_path)
    blob = _build_directory_metadata_blob(relative_path, domain, modification_date)

    return {
        "fileID": file_id,
        "domain": domain,
        "relativePath": relative_path,
        "flags": FLAGS_DIRECTORY,
        "file": blob,
    }


def rebuild_viber_manifest_entries(
    viber_db_path: Path,
    domain: str = VIBER_DOMAIN,
    db_relative_path: str = "Library/Application Support/Viber/Database/ViberMessages.db",
    modification_date: Optional[datetime] = None,
) -> List[Dict[str, object]]:
    """Produce the complete list of ``Manifest.db`` entries for the Viber domain.

    Creates entries for the required ancestor directories as well as the
    database file itself.

    Args:
        viber_db_path: Local path to the (modified) Viber SQLite database.
        domain: Backup domain string.
        db_relative_path: Relative path within the domain for the DB file.
        modification_date: Timestamp to use; defaults to *now*.

    Returns:
        List of entry dicts suitable for :func:`batch_upsert_entries`.
    """
    if modification_date is None:
        modification_date = datetime.now(tz=timezone.utc)

    entries: List[Dict[str, object]] = []

    # Add directory entries for each path component.
    parts = Path(db_relative_path).parts
    for i in range(1, len(parts)):
        dir_rel = "/".join(parts[:i])
        entries.append(
            build_directory_entry(domain, dir_rel, modification_date)
        )

    # Add the database file entry.
    entries.append(
        build_manifest_entry(viber_db_path, domain, db_relative_path, modification_date)
    )

    logger.debug(
        "Built %d manifest entries for domain '%s'", len(entries), domain
    )
    return entries
