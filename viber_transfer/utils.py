"""Utility helpers for the Viber transfer tool.

Provides logging setup, hashing helpers, timestamp utilities, database context
managers, path validation, pretty-printing, and custom exception types.
"""

from __future__ import annotations

import hashlib
import logging
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Generator, List

# ---------------------------------------------------------------------------
# Custom exception hierarchy
# ---------------------------------------------------------------------------


class DeviceNotFoundError(Exception):
    """Raised when no Android device is detected via ADB."""


class DatabaseNotFoundError(Exception):
    """Raised when a required SQLite database file cannot be found."""


class BackupNotFoundError(Exception):
    """Raised when the specified iPhone backup directory does not exist."""


class EncryptedBackupError(Exception):
    """Raised when the iPhone backup is encrypted (not supported)."""


class SchemaError(Exception):
    """Raised when an unexpected database schema is encountered."""


class ADBPermissionError(Exception):
    """Raised when ADB lacks the required permissions to access a file."""


# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------

_LOG_FORMAT = "%(asctime)s [%(levelname)s] %(name)s – %(message)s"
_DATE_FORMAT = "%Y-%m-%dT%H:%M:%S"


def setup_logging(level: int = logging.INFO, log_file: Path | None = None) -> None:
    """Configure the root logger.

    Args:
        level: Logging level (e.g. ``logging.DEBUG``).
        log_file: Optional path to write log output to a file in addition to
            the console.
    """
    handlers: list[logging.Handler] = [logging.StreamHandler()]
    if log_file is not None:
        handlers.append(logging.FileHandler(log_file))

    logging.basicConfig(
        level=level,
        format=_LOG_FORMAT,
        datefmt=_DATE_FORMAT,
        handlers=handlers,
        force=True,
    )


def get_logger(name: str) -> logging.Logger:
    """Return a named logger.

    Args:
        name: Logger name (typically ``__name__`` of the calling module).

    Returns:
        A :class:`logging.Logger` instance.
    """
    return logging.getLogger(name)


# ---------------------------------------------------------------------------
# Hashing
# ---------------------------------------------------------------------------


def sha1_hash(data: bytes | str) -> str:
    """Compute the SHA-1 hex digest of *data*.

    Args:
        data: Raw bytes or a UTF-8 string.

    Returns:
        Lowercase hex string of the SHA-1 digest.
    """
    if isinstance(data, str):
        data = data.encode("utf-8")
    return hashlib.sha1(data).hexdigest()


def sha256_hash_file(path: Path) -> str:
    """Compute the SHA-256 hex digest of a file.

    Reads the file in 64 KiB chunks to support large files without exhausting
    memory.

    Args:
        path: Path to the file to hash.

    Returns:
        Lowercase hex string of the SHA-256 digest.

    Raises:
        FileNotFoundError: If *path* does not exist.
    """
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def sha1_hash_file(path: Path) -> str:
    """Compute the SHA-1 hex digest of a file.

    Args:
        path: Path to the file to hash.

    Returns:
        Lowercase hex string of the SHA-1 digest.

    Raises:
        FileNotFoundError: If *path* does not exist.
    """
    h = hashlib.sha1()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def compute_file_id(domain: str, relative_path: str) -> str:
    """Compute the iOS backup *fileID* for a domain/path combination.

    iOS backups identify files by the SHA-1 hash of the string
    ``"<domain>-<relativePath>"``.

    Args:
        domain: Backup domain, e.g. ``"AppDomain-com.viber"``.
        relative_path: Relative path inside the domain, e.g.
            ``"Library/Application Support/Viber/Database/ViberMessages.db"``.

    Returns:
        40-character lowercase hex SHA-1 string.
    """
    combined = f"{domain}-{relative_path}"
    return sha1_hash(combined)


# ---------------------------------------------------------------------------
# Timestamp utilities
# ---------------------------------------------------------------------------

# Apple epoch: 2001-01-01 00:00:00 UTC expressed as a Unix timestamp (seconds).
APPLE_EPOCH_OFFSET: int = 978307200


def unix_ms_to_datetime(timestamp_ms: int) -> datetime:
    """Convert an Android-style Unix millisecond timestamp to a UTC datetime.

    Args:
        timestamp_ms: Milliseconds since the Unix epoch (1970-01-01).

    Returns:
        Timezone-aware :class:`datetime` in UTC.
    """
    return datetime.fromtimestamp(timestamp_ms / 1000.0, tz=timezone.utc)


def datetime_to_unix_ms(dt: datetime) -> int:
    """Convert a :class:`datetime` to Android-style Unix milliseconds.

    Args:
        dt: A timezone-aware or naive (assumed UTC) datetime.

    Returns:
        Integer milliseconds since the Unix epoch.
    """
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return int(dt.timestamp() * 1000)


def unix_ms_to_apple_epoch(timestamp_ms: int) -> float:
    """Convert a Unix millisecond timestamp to Apple epoch seconds.

    Apple epoch is the number of seconds since 2001-01-01 00:00:00 UTC.

    Args:
        timestamp_ms: Milliseconds since the Unix epoch.

    Returns:
        Floating-point seconds since the Apple epoch.
    """
    unix_seconds = timestamp_ms / 1000.0
    return unix_seconds - APPLE_EPOCH_OFFSET


def apple_epoch_to_datetime(apple_ts: float) -> datetime:
    """Convert Apple epoch seconds to a UTC :class:`datetime`.

    Args:
        apple_ts: Seconds since 2001-01-01 00:00:00 UTC.

    Returns:
        Timezone-aware UTC datetime.
    """
    unix_ts = apple_ts + APPLE_EPOCH_OFFSET
    return datetime.fromtimestamp(unix_ts, tz=timezone.utc)


def datetime_to_apple_epoch(dt: datetime) -> float:
    """Convert a :class:`datetime` to Apple epoch seconds.

    Args:
        dt: A timezone-aware or naive (assumed UTC) datetime.

    Returns:
        Floating-point seconds since the Apple epoch.
    """
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.timestamp() - APPLE_EPOCH_OFFSET


# ---------------------------------------------------------------------------
# Database helpers
# ---------------------------------------------------------------------------


@contextmanager
def open_db(path: Path, read_only: bool = False) -> Generator[sqlite3.Connection, None, None]:
    """Open a SQLite database as a context manager.

    Args:
        path: Path to the ``*.db`` / ``*.sqlite`` file.
        read_only: If ``True``, open the database in read-only URI mode so the
            file is never modified.

    Yields:
        An open :class:`sqlite3.Connection`.

    Raises:
        DatabaseNotFoundError: If *path* does not exist and *read_only* is
            ``True``.
    """
    if read_only and not path.exists():
        raise DatabaseNotFoundError(f"Database not found: {path}")

    if read_only:
        uri = path.as_uri() + "?mode=ro"
        conn = sqlite3.connect(uri, uri=True)
    else:
        conn = sqlite3.connect(path)

    conn.row_factory = sqlite3.Row
    try:
        yield conn
    finally:
        conn.close()


def get_table_names(conn: sqlite3.Connection) -> List[str]:
    """Return a list of table names in the given database connection.

    Args:
        conn: An open :class:`sqlite3.Connection`.

    Returns:
        List of table name strings.
    """
    cursor = conn.execute("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name")
    return [row[0] for row in cursor.fetchall()]


def table_exists(conn: sqlite3.Connection, table_name: str) -> bool:
    """Check whether *table_name* exists in the database.

    Args:
        conn: An open :class:`sqlite3.Connection`.
        table_name: Name of the table to check.

    Returns:
        ``True`` if the table exists, ``False`` otherwise.
    """
    cursor = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table_name,)
    )
    return cursor.fetchone() is not None


# ---------------------------------------------------------------------------
# Path validation
# ---------------------------------------------------------------------------


def validate_path_exists(path: Path, label: str = "Path") -> None:
    """Assert that *path* exists on the filesystem.

    Args:
        path: The path to validate.
        label: Human-readable label used in the error message.

    Raises:
        FileNotFoundError: If *path* does not exist.
    """
    if not path.exists():
        raise FileNotFoundError(f"{label} does not exist: {path}")


def validate_directory(path: Path, label: str = "Directory") -> None:
    """Assert that *path* is an existing directory.

    Args:
        path: The path to validate.
        label: Human-readable label used in the error message.

    Raises:
        NotADirectoryError: If *path* is not a directory or does not exist.
    """
    if not path.is_dir():
        raise NotADirectoryError(f"{label} is not a valid directory: {path}")


def check_disk_space(path: Path, required_bytes: int) -> None:
    """Verify that sufficient free disk space is available at *path*.

    Args:
        path: Directory path on which to check available space.
        required_bytes: Minimum free bytes required.

    Raises:
        OSError: If there is insufficient disk space.
    """
    import shutil

    total, used, free = shutil.disk_usage(path)
    if free < required_bytes:
        raise OSError(
            f"Insufficient disk space at {path}: {free} bytes free, "
            f"{required_bytes} bytes required."
        )


# ---------------------------------------------------------------------------
# Pretty-printing
# ---------------------------------------------------------------------------


def pretty_print_summary(conversations: "list[Conversation]") -> None:  # type: ignore[type-arg]
    """Print a human-readable summary of parsed conversations to stdout.

    Args:
        conversations: List of :class:`~viber_transfer.models.Conversation`
            objects.
    """
    total_messages = sum(len(c.messages) for c in conversations)
    print(f"\n{'='*60}")
    print(f"  Viber Chat Summary")
    print(f"{'='*60}")
    print(f"  Conversations : {len(conversations)}")
    print(f"  Total messages: {total_messages}")
    print(f"{'='*60}")
    for conv in conversations:
        label = conv.group_name if conv.is_group else ", ".join(
            p.display_name or p.phone_number for p in conv.participants
        )
        print(f"  [{conv.conversation_id:>8}]  {label:<35} {len(conv.messages):>5} msg(s)")
    print(f"{'='*60}\n")
