# viber-transfer-to-iphone

> Open-source Python desktop tool to migrate Viber chat history from Android to iPhone by converting Android SQLite databases into iOS backup-compatible format.

```
  ┌─────────────┐        ┌──────────────────┐        ┌──────────────────┐
  │  Android    │  ADB   │  viber-transfer  │  copy  │  iPhone Backup   │
  │  Viber DB   │──────▶│  (Python tool)   │──────▶│  (modified)      │
  │  (SQLite)   │        │                  │        │  Manifest.db     │
  └─────────────┘        └──────────────────┘        │  ViberMessages   │
                              │         │             └──────────────────┘
                         parse &    convert                    │
                         extract    schema              Finder / iTunes
                              │         │                      │
                         ┌────▼─────────▼────┐          ┌─────▼──────┐
                         │  conversations,   │          │  iPhone    │
                         │  messages,        │          │  Restore   │
                         │  participants     │          └────────────┘
                         └───────────────────┘
```

## Features

- **ADB extraction** – pull Viber databases directly from a connected Android device
- **Full chat parsing** – messages, participants, attachments, groups, timestamps
- **Schema conversion** – Android format → iOS Viber database schema with Apple epoch timestamps
- **Backup injection** – writes converted messages into a copy of your iPhone backup
- **Manifest rebuilding** – updates `Manifest.db` with correct SHA-1/SHA-256 hashes
- **CLI** – single `viber-transfer migrate` command runs the full pipeline
- **Comprehensive tests** – pytest suite with mock databases and `tmp_path` fixtures

---

## Project Structure

```
viber_transfer/
├── __init__.py
├── adb_extractor.py        # ADB device detection and database pulling
├── android_parser.py       # Parses Android Viber SQLite databases
├── ios_backup_reader.py    # Reads and validates iPhone backup directories
├── ios_backup_injector.py  # Injects messages into iPhone backup copy
├── schema_converter.py     # Android → iOS schema transformation
├── manifest_builder.py     # Rebuilds Manifest.db after injection
├── cli.py                  # Typer-based CLI entry point
├── models.py               # Core dataclasses (User, Message, Conversation, …)
└── utils.py                # Shared helpers, exceptions, logging, hashing

tests/
├── __init__.py
├── test_models.py
├── test_android_parser.py
├── test_schema_converter.py
├── test_ios_backup_reader.py
├── test_manifest_builder.py
└── test_utils.py

pyproject.toml
requirements.txt
README.md
LICENSE
```

---

## Requirements

- Python 3.11+
- [Android SDK Platform-Tools](https://developer.android.com/tools/releases/platform-tools) (`adb` on PATH)
- A rooted Android device **or** Viber backup extracted via ADB backup
- An **unencrypted** iPhone backup (created via Finder/iTunes)

---

## Installation

### From source

```bash
git clone https://github.com/morinameriton/viber-transfer-to-iphone.git
cd viber-transfer-to-iphone
pip install -e .
```

### With development dependencies

```bash
pip install -e ".[dev]"
# or
pip install -r requirements.txt
```

---

## Android Preparation

### Enable USB Debugging

1. Open **Settings → About Phone**.
2. Tap **Build Number** seven times to enable Developer Options.
3. Open **Settings → Developer Options**.
4. Enable **USB Debugging**.
5. Connect your Android device via USB and accept the RSA key prompt on the device.

### Root Requirements

Viber stores its databases in a protected directory (`/data/data/com.viber.voip/`).  
To pull them directly you need **root access** on the Android device.

If the device is not rooted, you may be able to use:

```bash
adb backup -noapk com.viber.voip
```

and then extract the databases from the resulting `backup.ab` file using `android-backup-extractor`.

### Verify ADB connection

```bash
adb devices
# Should list your device as "device" (not "unauthorized")
```

---

## iPhone Backup Preparation

1. Connect your iPhone to your Mac/PC.
2. Open **Finder** (macOS Ventura+) or **iTunes** (Windows / older macOS).
3. Select your device.
4. Choose **"Back Up Now"** — make sure **"Encrypt local backup"** is **unchecked**.
5. Wait for the backup to complete.
6. Note the backup directory path (see below).

### Backup location

| Platform | Default path |
|----------|-------------|
| macOS    | `~/Library/Application Support/MobileSync/Backup/<UUID>/` |
| Windows  | `%APPDATA%\Apple Computer\MobileSync\Backup\<UUID>\` |

---

## Usage

### Full migration pipeline

```bash
viber-transfer migrate \
  --android-db  ./android_dbs/viber_messages \
  --backup-dir  ~/Library/Application\ Support/MobileSync/Backup/<UUID> \
  --output-dir  ./converted_backup
```

The tool will:
1. Parse the Android Viber databases.
2. Convert all messages and conversations to iOS Viber schema.
3. Copy the iPhone backup to `./converted_backup/`.
4. Inject the converted messages into the backup database.
5. Rebuild `Manifest.db` with updated file hashes.

### Extract databases from Android device

```bash
viber-transfer extract --output-dir ./android_dbs
```

### Parse and summarise Android chats

```bash
viber-transfer parse \
  --android-db ./android_dbs/viber_messages \
  --data-db    ./android_dbs/viber_data
```

### Validate an iPhone backup

```bash
viber-transfer validate-backup ~/Library/Application\ Support/MobileSync/Backup/<UUID>
```

### All options

```
Usage: viber-transfer [OPTIONS] COMMAND [ARGS]...

Commands:
  migrate          Full Android-to-iOS migration pipeline
  extract          Pull Viber databases from Android via ADB
  parse            Parse Android DB and show a chat summary
  validate-backup  Validate an iPhone backup directory

Options:
  --help  Show help and exit.
```

---

## Restore Instructions

After the tool completes:

1. **macOS (Finder)**
   - Connect your iPhone.
   - Open Finder and select your device.
   - Click **"Restore Backup…"**.
   - Navigate to and select the `converted_backup/` folder.
   - Click **Restore**.

2. **Windows (iTunes)**
   - Connect your iPhone.
   - Open iTunes and select your device.
   - Click **"Restore Backup…"**.
   - Browse to the `converted_backup/` folder.
   - Click **Restore**.

> **Note:** Restoring a backup will overwrite data on the iPhone. Always keep the original backup safe.

---

## Running Tests

```bash
# Install development dependencies
pip install -e ".[dev]"

# Run the full test suite
pytest

# With coverage report
pytest --cov=viber_transfer --cov-report=term-missing
```

---

## Troubleshooting

| Problem | Solution |
|---------|----------|
| `adb: command not found` | Install [Android Platform-Tools](https://developer.android.com/tools/releases/platform-tools) and add to PATH |
| `Permission denied` on ADB pull | Device must be rooted or use `adb backup` method |
| `EncryptedBackupError` | Disable "Encrypt local backup" in Finder/iTunes before creating backup |
| `Manifest.db not found` | Verify you selected the correct backup UUID directory |
| `SchemaError` on Android DB | Ensure you are providing the `viber_messages` file (not `viber_data`) |
| Multiple devices connected | Use `--serial <device_serial>` to specify the target device |
| Backup not restorable | Only unencrypted backups are supported; re-create backup without encryption |

---

## Limitations & Known Issues

- **Encrypted backups** are not supported and will never be.
- **Media files** (images, videos) are listed in the converted DB but the actual files are not transferred. Transfer media files manually if needed.
- **Stickers** are converted as sticker-type messages but custom sticker packs may not render correctly on iOS.
- **Root is required** to extract Viber databases directly via ADB.
- Only tested against Viber for Android version 20.x. Older schema versions may differ.
- The iOS backup format may change across iOS versions. This tool targets iOS 16/17 backup structures.

---

## Contributing

Pull requests are welcome. For major changes, please open an issue first to discuss what you would like to change.

1. Fork the repository.
2. Create a feature branch: `git checkout -b feature/my-feature`
3. Write tests for your changes.
4. Run the test suite: `pytest`
5. Submit a pull request.

---

## License

[MIT](./LICENSE) © 2026 Meriton Morina
