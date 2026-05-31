from __future__ import annotations

import sqlite3
import shutil
from pathlib import Path

from paths import APP_DIR, DATA_DIR, RESOURCE_DIR
from db_updater import RemoteDbUpdateResult, update_database_from_remote, validate_sqlite


ROOT_DIR = APP_DIR
DB_PATH = DATA_DIR / "mabidb.sqlite"
LEGACY_DB_PATH = DATA_DIR / "mobidb.sqlite"
SCHEMA_CANDIDATES = (
    APP_DIR / "resources" / "schema.sql",
    APP_DIR / "schema.sql",
    RESOURCE_DIR / "resources" / "schema.sql",
    RESOURCE_DIR / "schema.sql",
)
BUNDLED_DB_PATH = RESOURCE_DIR / "data" / "mabidb.sqlite"
BUNDLED_VERSION_PATH = RESOURCE_DIR / "data" / "db_version.txt"
BUNDLED_APP_VERSION_PATH = RESOURCE_DIR / "data" / "app_version.txt"
VERSION_PATH = DATA_DIR / "db_version.txt"
APP_VERSION_PATH = DATA_DIR / "app_version.txt"


def connect(db_path: Path = DB_PATH) -> sqlite3.Connection:
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def ensure_local_version_files(db_path: Path = DB_PATH) -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    if db_path == DB_PATH and not VERSION_PATH.exists() and BUNDLED_VERSION_PATH.exists():
        shutil.copy2(BUNDLED_VERSION_PATH, VERSION_PATH)
    if db_path == DB_PATH and not APP_VERSION_PATH.exists() and BUNDLED_APP_VERSION_PATH.exists():
        shutil.copy2(BUNDLED_APP_VERSION_PATH, APP_VERSION_PATH)


def cleanup_legacy_database_file(db_path: Path = DB_PATH) -> None:
    if db_path != DB_PATH or not DB_PATH.exists() or not LEGACY_DB_PATH.exists():
        return
    try:
        LEGACY_DB_PATH.unlink()
    except OSError:
        pass


def find_schema_path() -> Path | None:
    return next((path for path in SCHEMA_CANDIDATES if path.exists()), None)


def initialize(db_path: Path = DB_PATH, *, update_remote: bool = False) -> RemoteDbUpdateResult | None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    if db_path == DB_PATH and not db_path.exists() and BUNDLED_DB_PATH.exists():
        shutil.copy2(BUNDLED_DB_PATH, db_path)
    ensure_local_version_files(db_path)
    update_result = None
    if update_remote and db_path == DB_PATH:
        update_result = update_database_from_remote(db_path)
    schema_path = find_schema_path()
    if schema_path is None:
        validate_sqlite(db_path)
    else:
        schema = schema_path.read_text(encoding="utf-8")
        with connect(db_path) as conn:
            conn.executescript(schema)
    cleanup_legacy_database_file(db_path)
    return update_result


def upsert_entry(
    conn: sqlite3.Connection,
    *,
    entry_type: str,
    name: str,
    summary: str = "",
    description: str = "",
    source: str = "",
    aliases: list[str] | None = None,
    tags: list[str] | None = None,
    attributes: dict[str, str] | None = None,
    replace_tags: bool = False,
) -> int:
    conn.execute(
        """
        INSERT INTO entries(type, name, summary, description, source)
        VALUES (?, ?, ?, ?, ?)
        ON CONFLICT(type, name) DO UPDATE SET
            summary = excluded.summary,
            description = excluded.description,
            source = excluded.source,
            updated_at = CURRENT_TIMESTAMP
        """,
        (entry_type, name, summary, description, source),
    )
    entry_id = conn.execute(
        "SELECT id FROM entries WHERE type = ? AND name = ?",
        (entry_type, name),
    ).fetchone()["id"]

    for alias in aliases or []:
        conn.execute(
            "INSERT OR IGNORE INTO aliases(entry_id, alias) VALUES (?, ?)",
            (entry_id, alias),
        )

    if replace_tags:
        conn.execute("DELETE FROM entry_tags WHERE entry_id = ?", (entry_id,))

    for tag in tags or []:
        conn.execute("INSERT OR IGNORE INTO tags(name) VALUES (?)", (tag,))
        tag_id = conn.execute("SELECT id FROM tags WHERE name = ?", (tag,)).fetchone()["id"]
        conn.execute(
            "INSERT OR IGNORE INTO entry_tags(entry_id, tag_id) VALUES (?, ?)",
            (entry_id, tag_id),
        )

    if attributes is not None:
        conn.execute("DELETE FROM attributes WHERE entry_id = ?", (entry_id,))
        conn.executemany(
            "INSERT INTO attributes(entry_id, key, value) VALUES (?, ?, ?)",
            [(entry_id, key, value) for key, value in attributes.items()],
        )

    return entry_id
