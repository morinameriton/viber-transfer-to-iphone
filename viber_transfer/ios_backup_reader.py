"""iOS backup reader.

Parses the structure of an unencrypted iPhone backup directory to locate the
Viber app sandbox, resolve hashed file paths, and extract existing databases.
"""

from __future__ import annotations

import logging
import plistlib
import sqlite3
from pathlib import Path
from typing import Dict, List, Optional

from viber_transfer.utils import (
    BackupNotFoundError,
    DatabaseNotFoundError,
    EncryptedBackupError,
    SchemaError,
    compute_file_id,
    get_logger,
    open_db,
    table_exists,
)

logger = get_logger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

VIBER_DOMAIN = "AppDomain-com.viber"
VIBER_DB_RELATIVE_PATH = (
    "Library/Application Support/Viber/Database/ViberMessages.db"
)
MANIFEST_DB_NAME = "Manifest.db"
MANIFEST_PLIST_NAME = "Manifest.plist"
INFO_PLIST_NAME = "Info.plist"
STATUS_PLIST_NAME = "Status.plist"


# ---------------------------------------------------------------------------
# Plist helpers
# ---------------------------------------------------------------------------


def _read_plist(path: Path) -> dict:  # type: ignore[type-arg]
    """Read and return the contents of a plist file.

    Args:
        path: Path to the ``.plist`` file.

    Returns:
        Parsed plist data as a Python dict.

    Raises:
        FileNotFoundError: If *path* does not exist.
    """
    if not path.exists():
        raise FileNotFoundError(f"Plist file not found: {path}")
    with open(path, "rb") as fh:
        return plistlib.load(fh)


# ---------------------------------------------------------------------------
# Backup validation
# ---------------------------------------------------------------------------


def validate_backup(backup_dir: Path) -> None:
    """Check that *backup_dir* looks like a valid, unencrypted iPhone backup.

    Args:
        backup_dir: Path to the iPhone backup directory.

    Raises:
        BackupNotFoundError: If *backup_dir* does not exist.
        EncryptedBackupError: If the backup is encrypted.
        FileNotFoundError: If ``Manifest.db`` or ``Info.plist`` is absent.
    """
    if not backup_dir.exists():
        raise BackupNotFoundError(f"Backup directory not found: {backup_dir}")

    manifest_plist_path = backup_dir / MANIFEST_PLIST_NAME
    if manifest_plist_path.exists():
        try:
            manifest_data = _read_plist(manifest_plist_path)
            if manifest_data.get("IsEncrypted", False):
                raise EncryptedBackupError(
                    "The backup is encrypted. Please create an unencrypted backup "
                    "via Finder (macOS) or iTunes (Windows) before running this tool."
                )
        except plistlib.InvalidFileException as exc:
            raise SchemaError(f"Manifest.plist is corrupt: {exc}") from exc

    manifest_db_path = backup_dir / MANIFEST_DB_NAME
    if not manifest_db_path.exists():
        raise FileNotFoundError(
            f"Manifest.db not found in backup directory: {backup_dir}"
        )


# ---------------------------------------------------------------------------
# Manifest.db access
# ---------------------------------------------------------------------------


def _open_manifest_db(backup_dir: Path) -> sqlite3.Connection:
    """Open the ``Manifest.db`` file from *backup_dir*.

    Args:
        backup_dir: Path to the backup directory.

    Returns:
        An open :class:`sqlite3.Connection` to ``Manifest.db``.

    Raises:
        DatabaseNotFoundError: If ``Manifest.db`` is absent.
    """
    path = backup_dir / MANIFEST_DB_NAME
    if not path.exists():
        raise DatabaseNotFoundError(f"Manifest.db not found: {path}")
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    return conn


def list_viber_files(backup_dir: Path) -> List[Dict[str, object]]:
    """List all files in the Viber app domain inside the backup.

    Args:
        backup_dir: Path to the iPhone backup directory.

    Returns:
        List of dicts with keys ``fileID``, ``domain``, ``relativePath``,
        ``flags``, and ``file`` (raw plist blob bytes).
    """
    validate_backup(backup_dir)
    conn = _open_manifest_db(backup_dir)
    try:
        if not table_exists(conn, "Files"):
            raise SchemaError("'Files' table missing from Manifest.db")
        cursor = conn.execute(
            "SELECT fileID, domain, relativePath, flags, file "
            "FROM Files WHERE domain=? ORDER BY relativePath",
            (VIBER_DOMAIN,),
        )
        rows = [dict(row) for row in cursor.fetchall()]
    finally:
        conn.close()

    logger.info("Found %d files in Viber domain", len(rows))
    return rows


# ---------------------------------------------------------------------------
# File ID resolution
# ---------------------------------------------------------------------------


def resolve_file_path(backup_dir: Path, file_id: str) -> Path:
    """Resolve a hashed fileID to its physical path inside the backup.

    iOS backups store files in subdirectories named by the first two characters
    of the fileID.

    Args:
        backup_dir: Path to the backup directory.
        file_id: 40-character SHA-1 hex string (the fileID).

    Returns:
        Full :class:`Path` to the file.
    """
    subdir = file_id[:2]
    return backup_dir / subdir / file_id


def get_viber_db_file_id() -> str:
    """Return the fileID for the main Viber messages database.

    Returns:
        SHA-1 hex string of ``"AppDomain-com.viber-{VIBER_DB_RELATIVE_PATH}"``.
    """
    return compute_file_id(VIBER_DOMAIN, VIBER_DB_RELATIVE_PATH)


# ---------------------------------------------------------------------------
# Plist readers
# ---------------------------------------------------------------------------


def read_manifest_plist(backup_dir: Path) -> dict:  # type: ignore[type-arg]
    """Read ``Manifest.plist`` from *backup_dir*.

    Args:
        backup_dir: Path to the backup directory.

    Returns:
        Parsed plist data as a dict.
    """
    return _read_plist(backup_dir / MANIFEST_PLIST_NAME)


def read_info_plist(backup_dir: Path) -> dict:  # type: ignore[type-arg]
    """Read ``Info.plist`` from *backup_dir*.

    Args:
        backup_dir: Path to the backup directory.

    Returns:
        Parsed plist data as a dict.
    """
    return _read_plist(backup_dir / INFO_PLIST_NAME)


def read_status_plist(backup_dir: Path) -> dict:  # type: ignore[type-arg]
    """Read ``Status.plist`` from *backup_dir*.

    Args:
        backup_dir: Path to the backup directory.

    Returns:
        Parsed plist data as a dict.
    """
    return _read_plist(backup_dir / STATUS_PLIST_NAME)


# ---------------------------------------------------------------------------
# Viber database extraction
# ---------------------------------------------------------------------------


def extract_viber_db(backup_dir: Path, output_path: Path) -> Optional[Path]:
    """Extract the Viber messages database from an iPhone backup.

    Looks up the Viber messages DB file in ``Manifest.db``, then copies it to
    *output_path*.

    Args:
        backup_dir: Path to the iPhone backup directory.
        output_path: Destination path for the extracted database file.

    Returns:
        *output_path* if extraction succeeded, or ``None`` if the file was not
        found in the backup.

    Raises:
        BackupNotFoundError: If *backup_dir* is not a valid backup.
    """
    validate_backup(backup_dir)
    file_id = get_viber_db_file_id()
    source = resolve_file_path(backup_dir, file_id)

    if not source.exists():
        logger.warning(
            "Viber database not found in backup (expected fileID %s at %s)",
            file_id,
            source,
        )
        return None

    output_path.parent.mkdir(parents=True, exist_ok=True)
    import shutil

    shutil.copy2(source, output_path)
    logger.info("Extracted Viber DB to %s", output_path)
    return output_path
