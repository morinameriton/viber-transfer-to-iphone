"""Command-line interface for the Viber transfer tool.

Provides the ``viber-transfer`` command with sub-commands for extracting,
parsing, converting, and migrating Viber chats.
"""

from __future__ import annotations

import logging
import sys
from pathlib import Path
from typing import Optional

import typer

from viber_transfer.utils import (
    get_logger,
    pretty_print_summary,
    setup_logging,
)

app = typer.Typer(
    name="viber-transfer",
    help="Migrate Viber chats from Android to iPhone.",
    add_completion=False,
)

logger = get_logger(__name__)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _resolve_output(output: Optional[Path], default_name: str) -> Path:
    """Return *output* or a default path in the current directory."""
    return output if output is not None else Path.cwd() / default_name


# ---------------------------------------------------------------------------
# extract
# ---------------------------------------------------------------------------


@app.command("extract")
def extract_command(
    output_dir: Path = typer.Option(
        Path("./android_dbs"),
        "--output-dir",
        "-o",
        help="Directory to save the pulled databases.",
    ),
    serial: Optional[str] = typer.Option(
        None,
        "--serial",
        "-s",
        help="ADB device serial (required when multiple devices are connected).",
    ),
    verbose: bool = typer.Option(False, "--verbose", "-v"),
) -> None:
    """Pull Viber databases from a connected Android device via ADB."""
    setup_logging(logging.DEBUG if verbose else logging.INFO)

    from viber_transfer.adb_extractor import extract_viber_databases

    try:
        paths = extract_viber_databases(output_dir, serial=serial)
        typer.echo(f"Extracted databases:")
        for name, path in paths.items():
            typer.echo(f"  {name}: {path}")
    except Exception as exc:
        typer.echo(f"Error: {exc}", err=True)
        raise typer.Exit(code=1) from exc


# ---------------------------------------------------------------------------
# parse
# ---------------------------------------------------------------------------


@app.command("parse")
def parse_command(
    android_db: Path = typer.Option(
        ...,
        "--android-db",
        help="Path to the viber_messages SQLite database.",
    ),
    data_db: Optional[Path] = typer.Option(
        None,
        "--data-db",
        help="Optional path to the viber_data SQLite database.",
    ),
    verbose: bool = typer.Option(False, "--verbose", "-v"),
) -> None:
    """Parse Android Viber databases and display a chat summary."""
    setup_logging(logging.DEBUG if verbose else logging.INFO)

    from viber_transfer.android_parser import parse_android_databases

    try:
        conversations = parse_android_databases(android_db, data_db_path=data_db)
        pretty_print_summary(conversations)
    except Exception as exc:
        typer.echo(f"Error: {exc}", err=True)
        raise typer.Exit(code=1) from exc


# ---------------------------------------------------------------------------
# validate-backup
# ---------------------------------------------------------------------------


@app.command("validate-backup")
def validate_backup_command(
    backup_dir: Path = typer.Argument(..., help="Path to the iPhone backup directory."),
    verbose: bool = typer.Option(False, "--verbose", "-v"),
) -> None:
    """Validate an existing iPhone backup directory."""
    setup_logging(logging.DEBUG if verbose else logging.INFO)

    from viber_transfer.ios_backup_reader import validate_backup

    try:
        validate_backup(backup_dir)
        typer.echo(f"Backup at '{backup_dir}' is valid and unencrypted.")
    except Exception as exc:
        typer.echo(f"Validation failed: {exc}", err=True)
        raise typer.Exit(code=1) from exc


# ---------------------------------------------------------------------------
# migrate (full pipeline)
# ---------------------------------------------------------------------------


@app.command("migrate")
def migrate_command(
    android_db: Path = typer.Option(
        ...,
        "--android-db",
        help="Path to the extracted viber_messages SQLite database.",
    ),
    backup_dir: Path = typer.Option(
        ...,
        "--backup-dir",
        help="Path to the unencrypted iPhone backup directory.",
    ),
    output_dir: Path = typer.Option(
        Path("./converted_backup"),
        "--output-dir",
        "-o",
        help="Destination directory for the modified backup.",
    ),
    data_db: Optional[Path] = typer.Option(
        None,
        "--data-db",
        help="Optional path to the viber_data SQLite database.",
    ),
    local_phone: str = typer.Option(
        "me",
        "--local-phone",
        help="Phone number of the device owner (used for outgoing messages).",
    ),
    verbose: bool = typer.Option(False, "--verbose", "-v"),
) -> None:
    """Run the full Android-to-iOS Viber migration pipeline.

    Steps:
    1. Parse Android Viber databases.
    2. Convert schema to iOS format.
    3. Inject converted data into the iPhone backup.
    4. Output the modified backup to --output-dir.
    """
    setup_logging(logging.DEBUG if verbose else logging.INFO)

    from viber_transfer.android_parser import parse_android_databases
    from viber_transfer.ios_backup_injector import inject_into_backup
    from viber_transfer.schema_converter import build_ios_viber_tables

    try:
        # Step 1 – Parse
        typer.echo("Step 1/4 – Parsing Android Viber database …")
        conversations = parse_android_databases(android_db, data_db_path=data_db,
                                                 local_phone_number=local_phone)
        typer.echo(f"  Found {len(conversations)} conversation(s).")

        # Step 2 – Convert
        typer.echo("Step 2/4 – Converting schema to iOS format …")
        ios_tables = build_ios_viber_tables(conversations)
        typer.echo(f"  {len(ios_tables['messages'])} message(s) converted.")

        # Step 3 – Validate backup
        typer.echo("Step 3/4 – Validating iPhone backup …")
        from viber_transfer.ios_backup_reader import validate_backup
        validate_backup(backup_dir)
        typer.echo("  Backup is valid.")

        # Step 4 – Inject
        typer.echo("Step 4/4 – Injecting messages into backup …")
        result = inject_into_backup(backup_dir, ios_tables, output_dir)
        typer.echo(f"\nDone! Modified backup written to: {result}")
        typer.echo(
            "\nTo restore, open Finder (macOS) or iTunes (Windows), "
            "connect your iPhone, and restore from backup using this folder."
        )

    except Exception as exc:
        typer.echo(f"\nMigration failed: {exc}", err=True)
        logger.exception("Migration failed")
        raise typer.Exit(code=1) from exc


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


def main() -> None:
    """Entry point for the ``viber-transfer`` CLI command."""
    app()


if __name__ == "__main__":
    main()
