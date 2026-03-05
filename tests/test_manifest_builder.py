"""Tests for viber_transfer.manifest_builder."""

from __future__ import annotations

import plistlib
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

import pytest

from viber_transfer.manifest_builder import (
    FLAGS_DIRECTORY,
    FLAGS_FILE,
    _build_directory_metadata_blob,
    _build_file_metadata_blob,
    batch_upsert_entries,
    build_directory_entry,
    build_manifest_entry,
    create_manifest_db,
    rebuild_viber_manifest_entries,
    upsert_file_entry,
)
from viber_transfer.utils import compute_file_id


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_file(path: Path, content: bytes = b"hello world") -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(content)
    return path


# ---------------------------------------------------------------------------
# Tests: metadata blob serialisation
# ---------------------------------------------------------------------------


def test_build_file_metadata_blob_parseable() -> None:
    blob = _build_file_metadata_blob(
        relative_path="Library/file.db",
        domain="AppDomain-com.viber",
        file_size=1024,
        sha256_hash="a" * 64,
        modification_date=datetime(2023, 1, 1, tzinfo=timezone.utc),
    )
    assert isinstance(blob, bytes)
    # Must be valid binary plist
    data = plistlib.loads(blob)
    assert "$version" in data
    objects = data["$objects"]
    # Second object contains the metadata
    metadata = objects[1]
    assert metadata["Size"] == 1024
    assert metadata["Flags"] == FLAGS_FILE


def test_build_directory_metadata_blob() -> None:
    blob = _build_directory_metadata_blob(
        relative_path="Library",
        domain="AppDomain-com.viber",
        modification_date=datetime(2023, 1, 1, tzinfo=timezone.utc),
    )
    data = plistlib.loads(blob)
    metadata = data["$objects"][1]
    assert metadata["Flags"] == FLAGS_DIRECTORY
    assert metadata["Size"] == 0


def test_file_metadata_blob_checksum() -> None:
    sha256 = "abcdef" + "0" * 58
    blob = _build_file_metadata_blob(
        relative_path="f.db",
        domain="AppDomain-com.test",
        file_size=0,
        sha256_hash=sha256,
        modification_date=datetime(2024, 1, 1, tzinfo=timezone.utc),
    )
    data = plistlib.loads(blob)
    metadata = data["$objects"][1]
    assert metadata["Checksum"] == bytes.fromhex(sha256)


# ---------------------------------------------------------------------------
# Tests: create_manifest_db
# ---------------------------------------------------------------------------


def test_create_manifest_db(tmp_path: Path) -> None:
    db_path = tmp_path / "Manifest.db"
    create_manifest_db(db_path)
    assert db_path.exists()
    conn = sqlite3.connect(db_path)
    cursor = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
    )
    tables = {row[0] for row in cursor.fetchall()}
    conn.close()
    assert "Files" in tables
    assert "Properties" in tables


# ---------------------------------------------------------------------------
# Tests: upsert_file_entry
# ---------------------------------------------------------------------------


def test_upsert_file_entry(tmp_path: Path) -> None:
    db_path = tmp_path / "Manifest.db"
    create_manifest_db(db_path)
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    upsert_file_entry(
        conn,
        file_id="aabbcc" + "0" * 34,
        domain="AppDomain-com.viber",
        relative_path="Library/file.db",
        flags=FLAGS_FILE,
        file_blob=b"blob_data",
    )
    conn.commit()
    cursor = conn.execute("SELECT * FROM Files")
    rows = cursor.fetchall()
    conn.close()
    assert len(rows) == 1
    assert rows[0]["domain"] == "AppDomain-com.viber"


# ---------------------------------------------------------------------------
# Tests: batch_upsert_entries
# ---------------------------------------------------------------------------


def test_batch_upsert_entries(tmp_path: Path) -> None:
    db_path = tmp_path / "Manifest.db"
    create_manifest_db(db_path)

    entries = [
        {
            "fileID": "a" * 40,
            "domain": "AppDomain-com.viber",
            "relativePath": "Library/f1.db",
            "flags": FLAGS_FILE,
            "file": b"blob1",
        },
        {
            "fileID": "b" * 40,
            "domain": "AppDomain-com.viber",
            "relativePath": "Library/f2.db",
            "flags": FLAGS_FILE,
            "file": b"blob2",
        },
    ]
    batch_upsert_entries(db_path, entries)

    conn = sqlite3.connect(db_path)
    cursor = conn.execute("SELECT COUNT(*) FROM Files")
    count = cursor.fetchone()[0]
    conn.close()
    assert count == 2


def test_batch_upsert_idempotent(tmp_path: Path) -> None:
    db_path = tmp_path / "Manifest.db"
    create_manifest_db(db_path)
    entry = {
        "fileID": "c" * 40,
        "domain": "AppDomain-com.viber",
        "relativePath": "Library/f.db",
        "flags": FLAGS_FILE,
        "file": b"old_blob",
    }
    batch_upsert_entries(db_path, [entry])
    # Upsert again with updated blob
    entry["file"] = b"new_blob"
    batch_upsert_entries(db_path, [entry])

    conn = sqlite3.connect(db_path)
    cursor = conn.execute("SELECT file FROM Files WHERE fileID=?", ("c" * 40,))
    row = cursor.fetchone()
    conn.close()
    assert row[0] == b"new_blob"


# ---------------------------------------------------------------------------
# Tests: build_manifest_entry
# ---------------------------------------------------------------------------


def test_build_manifest_entry(tmp_path: Path) -> None:
    f = _make_file(tmp_path / "test.db", b"test content")
    entry = build_manifest_entry(
        file_path=f,
        domain="AppDomain-com.viber",
        relative_path="Library/test.db",
    )
    assert entry["flags"] == FLAGS_FILE
    assert entry["domain"] == "AppDomain-com.viber"
    assert entry["relativePath"] == "Library/test.db"
    expected_id = compute_file_id("AppDomain-com.viber", "Library/test.db")
    assert entry["fileID"] == expected_id
    assert isinstance(entry["file"], bytes)


# ---------------------------------------------------------------------------
# Tests: build_directory_entry
# ---------------------------------------------------------------------------


def test_build_directory_entry() -> None:
    entry = build_directory_entry(
        domain="AppDomain-com.viber",
        relative_path="Library",
    )
    assert entry["flags"] == FLAGS_DIRECTORY
    expected_id = compute_file_id("AppDomain-com.viber", "Library")
    assert entry["fileID"] == expected_id


# ---------------------------------------------------------------------------
# Tests: rebuild_viber_manifest_entries
# ---------------------------------------------------------------------------


def test_rebuild_viber_manifest_entries(tmp_path: Path) -> None:
    db_file = _make_file(tmp_path / "ViberMessages.db", b"viber_data")
    entries = rebuild_viber_manifest_entries(
        viber_db_path=db_file,
        domain="AppDomain-com.viber",
        db_relative_path="Library/Application Support/Viber/Database/ViberMessages.db",
    )
    # Expect directory entries for each path component + the file itself.
    assert len(entries) >= 2
    # Last entry should be the file.
    file_entry = entries[-1]
    assert file_entry["flags"] == FLAGS_FILE
    # All directory entries should have flag 2.
    for dir_entry in entries[:-1]:
        assert dir_entry["flags"] == FLAGS_DIRECTORY


def test_rebuild_viber_manifest_entries_file_id(tmp_path: Path) -> None:
    db_file = _make_file(tmp_path / "ViberMessages.db", b"x")
    rel_path = "Library/Application Support/Viber/Database/ViberMessages.db"
    entries = rebuild_viber_manifest_entries(
        viber_db_path=db_file,
        domain="AppDomain-com.viber",
        db_relative_path=rel_path,
    )
    file_entry = entries[-1]
    expected_id = compute_file_id("AppDomain-com.viber", rel_path)
    assert file_entry["fileID"] == expected_id
