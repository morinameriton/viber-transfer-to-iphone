"""Tests for viber_transfer.ios_backup_reader."""

from __future__ import annotations

import plistlib
import sqlite3
from pathlib import Path

import pytest

from viber_transfer.ios_backup_reader import (
    VIBER_DB_RELATIVE_PATH,
    VIBER_DOMAIN,
    extract_viber_db,
    get_viber_db_file_id,
    list_viber_files,
    read_info_plist,
    read_manifest_plist,
    read_status_plist,
    resolve_file_path,
    validate_backup,
)
from viber_transfer.utils import (
    BackupNotFoundError,
    EncryptedBackupError,
    compute_file_id,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _create_minimal_backup(backup_dir: Path, encrypted: bool = False) -> None:
    """Create a minimal (fake) iPhone backup directory for testing."""
    backup_dir.mkdir(parents=True, exist_ok=True)

    # Manifest.plist
    manifest_data: dict = {"IsEncrypted": encrypted, "Version": "10.0"}
    with open(backup_dir / "Manifest.plist", "wb") as fh:
        plistlib.dump(manifest_data, fh)

    # Info.plist
    info_data = {"Device Name": "Test iPhone", "GUID": "AABBCCDD"}
    with open(backup_dir / "Info.plist", "wb") as fh:
        plistlib.dump(info_data, fh)

    # Status.plist
    status_data = {"BackupState": "new", "IsFullBackup": True}
    with open(backup_dir / "Status.plist", "wb") as fh:
        plistlib.dump(status_data, fh)

    # Manifest.db
    manifest_db_path = backup_dir / "Manifest.db"
    conn = sqlite3.connect(manifest_db_path)
    conn.execute(
        """CREATE TABLE Files (
            fileID TEXT PRIMARY KEY,
            domain TEXT,
            relativePath TEXT,
            flags INTEGER,
            file BLOB
        )"""
    )
    conn.execute(
        """CREATE TABLE Properties (
            key TEXT PRIMARY KEY,
            value BLOB
        )"""
    )

    # Add a fake Viber file entry
    file_id = compute_file_id(VIBER_DOMAIN, VIBER_DB_RELATIVE_PATH)
    conn.execute(
        "INSERT INTO Files VALUES (?, ?, ?, ?, ?)",
        (file_id, VIBER_DOMAIN, VIBER_DB_RELATIVE_PATH, 1, b"fake_blob"),
    )
    conn.commit()
    conn.close()


def _create_viber_db_in_backup(backup_dir: Path) -> Path:
    """Create a fake Viber database file at the expected hashed location."""
    file_id = get_viber_db_file_id()
    subdir = backup_dir / file_id[:2]
    subdir.mkdir(parents=True, exist_ok=True)
    db_path = subdir / file_id
    # Write a minimal SQLite file header
    import struct

    # SQLite magic header (100 bytes minimum)
    header = b"SQLite format 3\x00" + b"\x00" * 84
    db_path.write_bytes(header)
    return db_path


# ---------------------------------------------------------------------------
# Tests: validate_backup
# ---------------------------------------------------------------------------


def test_validate_backup_ok(tmp_path: Path) -> None:
    backup = tmp_path / "backup"
    _create_minimal_backup(backup)
    validate_backup(backup)  # Should not raise


def test_validate_backup_missing_dir(tmp_path: Path) -> None:
    with pytest.raises(BackupNotFoundError):
        validate_backup(tmp_path / "nonexistent")


def test_validate_backup_encrypted(tmp_path: Path) -> None:
    backup = tmp_path / "enc_backup"
    _create_minimal_backup(backup, encrypted=True)
    with pytest.raises(EncryptedBackupError):
        validate_backup(backup)


def test_validate_backup_missing_manifest_db(tmp_path: Path) -> None:
    backup = tmp_path / "bad_backup"
    backup.mkdir()
    # No Manifest.db
    with pytest.raises(FileNotFoundError):
        validate_backup(backup)


# ---------------------------------------------------------------------------
# Tests: list_viber_files
# ---------------------------------------------------------------------------


def test_list_viber_files(tmp_path: Path) -> None:
    backup = tmp_path / "backup"
    _create_minimal_backup(backup)
    files = list_viber_files(backup)
    assert len(files) >= 1
    assert all(f["domain"] == VIBER_DOMAIN for f in files)


# ---------------------------------------------------------------------------
# Tests: resolve_file_path
# ---------------------------------------------------------------------------


def test_resolve_file_path() -> None:
    backup = Path("/fake/backup")
    file_id = "abcdef1234" + "0" * 30
    resolved = resolve_file_path(backup, file_id)
    assert resolved == Path(f"/fake/backup/ab/{file_id}")


def test_get_viber_db_file_id() -> None:
    file_id = get_viber_db_file_id()
    assert len(file_id) == 40
    expected = compute_file_id(VIBER_DOMAIN, VIBER_DB_RELATIVE_PATH)
    assert file_id == expected


# ---------------------------------------------------------------------------
# Tests: plist readers
# ---------------------------------------------------------------------------


def test_read_manifest_plist(tmp_path: Path) -> None:
    backup = tmp_path / "backup"
    _create_minimal_backup(backup)
    data = read_manifest_plist(backup)
    assert "IsEncrypted" in data
    assert data["IsEncrypted"] is False


def test_read_info_plist(tmp_path: Path) -> None:
    backup = tmp_path / "backup"
    _create_minimal_backup(backup)
    data = read_info_plist(backup)
    assert data["Device Name"] == "Test iPhone"


def test_read_status_plist(tmp_path: Path) -> None:
    backup = tmp_path / "backup"
    _create_minimal_backup(backup)
    data = read_status_plist(backup)
    assert data["BackupState"] == "new"


def test_read_plist_missing(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError):
        read_manifest_plist(tmp_path / "nonexistent_backup")


# ---------------------------------------------------------------------------
# Tests: extract_viber_db
# ---------------------------------------------------------------------------


def test_extract_viber_db_present(tmp_path: Path) -> None:
    backup = tmp_path / "backup"
    _create_minimal_backup(backup)
    _create_viber_db_in_backup(backup)
    output = tmp_path / "extracted.db"
    result = extract_viber_db(backup, output)
    assert result == output
    assert output.exists()


def test_extract_viber_db_absent(tmp_path: Path) -> None:
    backup = tmp_path / "backup"
    _create_minimal_backup(backup)
    # Don't create the actual DB file in the backup
    output = tmp_path / "extracted.db"
    result = extract_viber_db(backup, output)
    assert result is None
    assert not output.exists()
