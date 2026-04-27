from __future__ import annotations

import sqlite3
from pathlib import Path

from app_paths import APP_DIR, DATA_DIR, RESOURCE_DIR
from remote_db import update_database_from_remote


ROOT_DIR = APP_DIR
DB_PATH = DATA_DIR / "mobidb.sqlite"
SCHEMA_PATH = APP_DIR / "schema.sql"
BUNDLED_SCHEMA_PATH = RESOURCE_DIR / "schema.sql"


def connect(db_path: Path = DB_PATH) -> sqlite3.Connection:
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def initialize(db_path: Path = DB_PATH, *, update_remote: bool = False) -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    if update_remote and db_path == DB_PATH:
        update_database_from_remote(db_path)
    schema_path = SCHEMA_PATH if SCHEMA_PATH.exists() else BUNDLED_SCHEMA_PATH
    schema = schema_path.read_text(encoding="utf-8")
    with connect(db_path) as conn:
        conn.executescript(schema)


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
