"""Tests for viber_transfer.utils."""

from __future__ import annotations

import hashlib
from datetime import datetime, timezone
from pathlib import Path

import pytest

from viber_transfer.utils import (
    APPLE_EPOCH_OFFSET,
    ADBPermissionError,
    BackupNotFoundError,
    DatabaseNotFoundError,
    DeviceNotFoundError,
    EncryptedBackupError,
    SchemaError,
    apple_epoch_to_datetime,
    compute_file_id,
    datetime_to_apple_epoch,
    datetime_to_unix_ms,
    get_table_names,
    open_db,
    sha1_hash,
    sha1_hash_file,
    sha256_hash_file,
    table_exists,
    unix_ms_to_apple_epoch,
    unix_ms_to_datetime,
    validate_directory,
    validate_path_exists,
)


# ---------------------------------------------------------------------------
# Custom exceptions
# ---------------------------------------------------------------------------


def test_exception_hierarchy() -> None:
    for exc_cls in (
        DeviceNotFoundError,
        DatabaseNotFoundError,
        BackupNotFoundError,
        EncryptedBackupError,
        SchemaError,
        ADBPermissionError,
    ):
        exc = exc_cls("test message")
        assert isinstance(exc, Exception)
        assert str(exc) == "test message"


# ---------------------------------------------------------------------------
# SHA-1 hashing
# ---------------------------------------------------------------------------


def test_sha1_hash_bytes() -> None:
    data = b"hello"
    expected = hashlib.sha1(b"hello").hexdigest()
    assert sha1_hash(data) == expected


def test_sha1_hash_string() -> None:
    data = "hello"
    expected = hashlib.sha1(b"hello").hexdigest()
    assert sha1_hash(data) == expected


def test_sha1_hash_file(tmp_path: Path) -> None:
    f = tmp_path / "test.txt"
    f.write_bytes(b"hello world")
    expected = hashlib.sha1(b"hello world").hexdigest()
    assert sha1_hash_file(f) == expected


def test_sha256_hash_file(tmp_path: Path) -> None:
    f = tmp_path / "test.bin"
    f.write_bytes(b"abc")
    expected = hashlib.sha256(b"abc").hexdigest()
    assert sha256_hash_file(f) == expected


# ---------------------------------------------------------------------------
# File ID (domain-relativePath SHA-1)
# ---------------------------------------------------------------------------


def test_compute_file_id() -> None:
    domain = "AppDomain-com.viber"
    rel = "Library/Application Support/Viber/Database/ViberMessages.db"
    combined = f"{domain}-{rel}".encode("utf-8")
    expected = hashlib.sha1(combined).hexdigest()
    assert compute_file_id(domain, rel) == expected


def test_compute_file_id_known_value() -> None:
    # Pre-computed known value for regression testing.
    result = compute_file_id("AppDomain-com.example", "some/path/file.db")
    assert len(result) == 40
    assert result == hashlib.sha1(b"AppDomain-com.example-some/path/file.db").hexdigest()


# ---------------------------------------------------------------------------
# Timestamp utilities
# ---------------------------------------------------------------------------


def test_unix_ms_to_datetime_zero() -> None:
    dt = unix_ms_to_datetime(0)
    assert dt == datetime(1970, 1, 1, tzinfo=timezone.utc)


def test_unix_ms_to_datetime_known() -> None:
    # 1_000_000_000_000 ms = 2001-09-09T01:46:40Z
    dt = unix_ms_to_datetime(1_000_000_000_000)
    assert dt == datetime(2001, 9, 9, 1, 46, 40, tzinfo=timezone.utc)


def test_datetime_to_unix_ms_roundtrip() -> None:
    dt = datetime(2023, 6, 15, 12, 30, 0, tzinfo=timezone.utc)
    ms = datetime_to_unix_ms(dt)
    assert unix_ms_to_datetime(ms) == dt


def test_unix_ms_to_apple_epoch() -> None:
    # Apple epoch offset: 978307200 s
    # 978307200 * 1000 ms → should map to apple epoch 0.0
    ms = APPLE_EPOCH_OFFSET * 1000
    assert unix_ms_to_apple_epoch(ms) == pytest.approx(0.0)


def test_apple_epoch_to_datetime_zero() -> None:
    dt = apple_epoch_to_datetime(0.0)
    assert dt == datetime(2001, 1, 1, tzinfo=timezone.utc)


def test_datetime_to_apple_epoch_roundtrip() -> None:
    dt = datetime(2023, 6, 15, tzinfo=timezone.utc)
    apple_ts = datetime_to_apple_epoch(dt)
    assert apple_epoch_to_datetime(apple_ts) == dt


def test_unix_ms_to_apple_epoch_positive() -> None:
    # A timestamp after the Apple epoch.
    ms = (APPLE_EPOCH_OFFSET + 3600) * 1000  # 1 hour after Apple epoch
    apple_ts = unix_ms_to_apple_epoch(ms)
    assert apple_ts == pytest.approx(3600.0)


# ---------------------------------------------------------------------------
# Path validation
# ---------------------------------------------------------------------------


def test_validate_path_exists_ok(tmp_path: Path) -> None:
    f = tmp_path / "file.txt"
    f.write_text("x")
    validate_path_exists(f)  # Should not raise


def test_validate_path_exists_missing(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError):
        validate_path_exists(tmp_path / "no_such_file.txt")


def test_validate_directory_ok(tmp_path: Path) -> None:
    validate_directory(tmp_path)  # Should not raise


def test_validate_directory_file(tmp_path: Path) -> None:
    f = tmp_path / "file.txt"
    f.write_text("x")
    with pytest.raises(NotADirectoryError):
        validate_directory(f)


# ---------------------------------------------------------------------------
# SQLite helpers
# ---------------------------------------------------------------------------


def test_open_db_and_table_exists(tmp_path: Path) -> None:
    db = tmp_path / "test.db"
    with open_db(db) as conn:
        conn.execute("CREATE TABLE foo (id INTEGER PRIMARY KEY)")
        conn.commit()
        assert table_exists(conn, "foo")
        assert not table_exists(conn, "bar")


def test_get_table_names(tmp_path: Path) -> None:
    db = tmp_path / "tables.db"
    with open_db(db) as conn:
        conn.execute("CREATE TABLE alpha (id INTEGER PRIMARY KEY)")
        conn.execute("CREATE TABLE beta (id INTEGER PRIMARY KEY)")
        conn.commit()
        names = get_table_names(conn)
    assert "alpha" in names
    assert "beta" in names


def test_open_db_read_only_missing(tmp_path: Path) -> None:
    from viber_transfer.utils import DatabaseNotFoundError

    with pytest.raises(DatabaseNotFoundError):
        with open_db(tmp_path / "missing.db", read_only=True):
            pass
