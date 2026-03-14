"""ADB-based extractor for Android Viber databases.

Wraps ``adb`` command-line calls to detect connected Android devices and pull
the Viber SQLite databases from the device.
"""

from __future__ import annotations

import logging
import shutil
import subprocess
from pathlib import Path
from typing import List, Optional

from viber_transfer.utils import (
    ADBPermissionError,
    DatabaseNotFoundError,
    DeviceNotFoundError,
    get_logger,
    validate_directory,
)

logger = get_logger(__name__)

# Paths on the Android device for the two Viber databases.
VIBER_MESSAGES_DB_PATH = "/data/data/com.viber.voip/databases/viber_messages"
VIBER_DATA_DB_PATH = "/data/data/com.viber.voip/databases/viber_data"

ADB_BINARY = "adb"


def _run_adb(*args: str, timeout: int = 30) -> subprocess.CompletedProcess[str]:
    """Execute an adb command and return the completed process.

    Args:
        *args: Arguments passed to ``adb`` after the binary name.
        timeout: Maximum seconds to wait for the command to complete.

    Returns:
        The completed :class:`subprocess.CompletedProcess`.

    Raises:
        DeviceNotFoundError: If ``adb`` is not found on PATH.
    """
    adb = shutil.which(ADB_BINARY)
    if adb is None:
        raise DeviceNotFoundError(
            "ADB binary not found on PATH. "
            "Install Android SDK Platform-Tools and ensure 'adb' is accessible."
        )
    cmd = [adb, *args]
    logger.debug("Running ADB command: %s", " ".join(cmd))
    return subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        timeout=timeout,
    )


def get_connected_devices() -> List[str]:
    """Return a list of serial numbers of currently connected Android devices.

    Returns:
        List of device serial strings (may be empty if no devices are connected).

    Raises:
        DeviceNotFoundError: If ``adb`` is not found on PATH.
    """
    result = _run_adb("devices")
    lines = result.stdout.strip().splitlines()
    devices: List[str] = []
    for line in lines[1:]:  # Skip "List of devices attached" header
        line = line.strip()
        if line and "\t" in line:
            serial, state = line.split("\t", 1)
            if state.strip() == "device":
                devices.append(serial.strip())
    logger.info("Connected devices: %s", devices)
    return devices


def assert_single_device(serial: Optional[str] = None) -> str:
    """Ensure exactly one device is connected (or return the requested serial).

    Args:
        serial: Optional device serial to target.  When provided the function
            verifies that the device is online; when ``None`` it requires that
            exactly one device is attached.

    Returns:
        The device serial string.

    Raises:
        DeviceNotFoundError: When no or multiple devices are found and no
            *serial* was specified.
    """
    devices = get_connected_devices()
    if serial is not None:
        if serial not in devices:
            raise DeviceNotFoundError(
                f"Device '{serial}' is not connected. "
                f"Connected devices: {devices}"
            )
        return serial

    if not devices:
        raise DeviceNotFoundError(
            "No Android devices found. "
            "Connect a device and ensure USB debugging is enabled."
        )
    if len(devices) > 1:
        raise DeviceNotFoundError(
            f"Multiple devices connected: {devices}. "
            "Specify the target device serial with --serial."
        )
    return devices[0]


def _pull_file(
    remote_path: str,
    local_dir: Path,
    serial: Optional[str] = None,
) -> Path:
    """Pull a single file from the device to *local_dir*.

    Args:
        remote_path: Absolute path on the Android device.
        local_dir: Local directory to save the file into.
        serial: Optional device serial.

    Returns:
        :class:`Path` to the pulled local file.

    Raises:
        ADBPermissionError: If ADB reports a permission denied error.
        DatabaseNotFoundError: If the file does not exist on the device.
        RuntimeError: If the ``adb pull`` command fails for another reason.
    """
    local_dir.mkdir(parents=True, exist_ok=True)
    file_name = Path(remote_path).name
    local_path = local_dir / file_name

    adb_args = []
    if serial:
        adb_args = ["-s", serial]
    adb_args += ["pull", remote_path, str(local_path)]

    result = _run_adb(*adb_args, timeout=120)

    if result.returncode != 0:
        stderr = (result.stderr or "").lower()
        if "permission denied" in stderr:
            raise ADBPermissionError(
                f"Permission denied when pulling '{remote_path}'. "
                "The device may need to be rooted, or Viber must be backed up "
                "through ADB backup first."
            )
        if "no such file" in stderr or "does not exist" in stderr:
            raise DatabaseNotFoundError(
                f"File not found on device: {remote_path}"
            )
        raise RuntimeError(
            f"ADB pull failed for '{remote_path}': {result.stderr.strip()}"
        )

    logger.info("Pulled '%s' → '%s'", remote_path, local_path)
    return local_path


def validate_database_file(path: Path) -> bool:
    """Verify that *path* is a valid (non-corrupt) SQLite database.

    Reads the 16-byte SQLite magic header from the file.

    Args:
        path: Path to the SQLite file to validate.

    Returns:
        ``True`` if the file begins with the SQLite magic bytes.

    Raises:
        DatabaseNotFoundError: If the file does not exist.
    """
    if not path.exists():
        raise DatabaseNotFoundError(f"Database file not found: {path}")
    with open(path, "rb") as fh:
        magic = fh.read(16)
    is_valid = magic == b"SQLite format 3\x00"
    if not is_valid:
        logger.warning("File does not appear to be a valid SQLite database: %s", path)
    return is_valid


def extract_viber_databases(
    output_dir: Path,
    serial: Optional[str] = None,
) -> dict[str, Path]:
    """Pull both Viber databases from a connected Android device.

    Automatically detects a connected device (or uses *serial*), then pulls
    ``viber_messages`` and ``viber_data`` databases to *output_dir*.

    Args:
        output_dir: Local directory to write pulled databases into.
        serial: Optional ADB device serial.  When ``None`` the function
            requires exactly one device to be connected.

    Returns:
        Mapping of ``"viber_messages"`` and ``"viber_data"`` keys to the
        corresponding local :class:`Path` objects.

    Raises:
        DeviceNotFoundError: If no suitable device is found.
        ADBPermissionError: If ADB cannot read the database files.
        DatabaseNotFoundError: If the database files are absent on the device.
    """
    device_serial = assert_single_device(serial)
    logger.info("Targeting device: %s", device_serial)

    paths: dict[str, Path] = {}

    for db_name, remote_path in [
        ("viber_messages", VIBER_MESSAGES_DB_PATH),
        ("viber_data", VIBER_DATA_DB_PATH),
    ]:
        local_path = _pull_file(remote_path, output_dir, serial=device_serial)
        validate_database_file(local_path)
        paths[db_name] = local_path

    logger.info("Databases extracted to %s", output_dir)
    return paths
