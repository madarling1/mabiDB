from __future__ import annotations

import json
import os
import sqlite3
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path

from app_paths import APP_DIR, CONFIG_DIR, DATA_DIR, RESOURCE_DIR


CONFIG_PATH = CONFIG_DIR / "remote_db.json"
VERSION_PATH = DATA_DIR / "db_version.txt"
REQUEST_HEADERS = {"User-Agent": "MobiDB"}
REQUIRED_TABLES = {"entries", "rune_details", "search_synonyms"}


@dataclass(frozen=True)
class RemoteDbConfig:
    database_url: str = ""
    version_url: str = ""
    timeout_seconds: int = 15


@dataclass(frozen=True)
class RemoteDbUpdateResult:
    status: str
    message: str = ""


def load_remote_db_config() -> RemoteDbConfig:
    values: dict[str, object] = {}
    config_path = next(
        (
            path
            for path in (
                CONFIG_PATH,
                APP_DIR / "remote_db.json",
                RESOURCE_DIR / "remote_db.json",
                RESOURCE_DIR / "remote_db.example.json",
            )
            if path.exists()
        ),
        None,
    )
    if config_path:
        values = json.loads(config_path.read_text(encoding="utf-8"))

    database_url = str(os.environ.get("MOBIDB_SQLITE_URL") or values.get("database_url") or "")
    version_url = str(os.environ.get("MOBIDB_VERSION_URL") or values.get("version_url") or "")
    timeout_raw = os.environ.get("MOBIDB_UPDATE_TIMEOUT") or values.get("timeout_seconds") or 15

    try:
        timeout_seconds = int(timeout_raw)
    except (TypeError, ValueError):
        timeout_seconds = 15

    return RemoteDbConfig(
        database_url=database_url.strip(),
        version_url=version_url.strip(),
        timeout_seconds=max(1, timeout_seconds),
    )


def request_url(url: str, timeout_seconds: int):
    request = urllib.request.Request(url, headers=REQUEST_HEADERS)
    return urllib.request.urlopen(request, timeout=timeout_seconds)


def fetch_text(url: str, timeout_seconds: int) -> str:
    with request_url(url, timeout_seconds) as response:
        return response.read().decode("utf-8").strip()


def download_file(url: str, target_path, timeout_seconds: int) -> None:
    with request_url(url, timeout_seconds) as response:
        with target_path.open("wb") as file:
            while True:
                chunk = response.read(1024 * 128)
                if not chunk:
                    break
                file.write(chunk)


def validate_sqlite(db_path: Path) -> None:
    conn = sqlite3.connect(db_path)
    try:
        quick_check = conn.execute("PRAGMA quick_check").fetchone()
        if quick_check is None or quick_check[0] != "ok":
            raise sqlite3.DatabaseError(f"SQLite quick_check failed: {quick_check!r}")

        rows = conn.execute(
            """
            SELECT name
            FROM sqlite_master
            WHERE type = 'table'
            """
        ).fetchall()
        table_names = {row[0] for row in rows}
        missing = REQUIRED_TABLES - table_names
        if missing:
            raise sqlite3.DatabaseError(f"Missing required tables: {', '.join(sorted(missing))}")
    finally:
        conn.close()


def update_database_from_remote(db_path: Path) -> RemoteDbUpdateResult:
    config = load_remote_db_config()
    if not config.database_url:
        return RemoteDbUpdateResult("skipped", "remote DB URL is not configured")

    db_path.parent.mkdir(parents=True, exist_ok=True)

    try:
        remote_version = fetch_text(config.version_url, config.timeout_seconds) if config.version_url else ""
        if remote_version:
            local_version = VERSION_PATH.read_text(encoding="utf-8").strip() if VERSION_PATH.exists() else ""
            if db_path.exists() and local_version == remote_version:
                return RemoteDbUpdateResult("unchanged", remote_version)

        temp_path = db_path.with_suffix(".sqlite.download")
        download_file(config.database_url, temp_path, config.timeout_seconds)
        validate_sqlite(temp_path)
        os.replace(temp_path, db_path)

        if remote_version:
            VERSION_PATH.write_text(remote_version + "\n", encoding="utf-8")

        return RemoteDbUpdateResult("updated", remote_version)
    except (OSError, sqlite3.DatabaseError, urllib.error.URLError, urllib.error.HTTPError) as exc:
        try:
            db_path.with_suffix(".sqlite.download").unlink(missing_ok=True)
        except OSError:
            pass
        return RemoteDbUpdateResult("failed", str(exc))
