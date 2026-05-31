from __future__ import annotations

import json
import os
import sqlite3
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path

from paths import APP_DIR, CONFIG_DIR, DATA_DIR, RESOURCE_DIR


CONFIG_PATH = CONFIG_DIR / "remote_db.json"
VERSION_PATH = DATA_DIR / "db_version.txt"
DECO_ASSET_BLOB_PATH = DATA_DIR / "deco_assets.blob"
DECO_ASSET_MANIFEST_PATH = DATA_DIR / "deco_assets_manifest.json"
DECO_ASSET_VERSION_PATH = DATA_DIR / "deco_asset_version.txt"
REQUEST_HEADERS = {"User-Agent": "mabiDB"}
REQUIRED_TABLES = {"entries", "rune_details", "search_synonyms"}
PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"
PROGRESS_WIDTH = 14


@dataclass(frozen=True)
class RemoteDbConfig:
    database_url: str = ""
    version_url: str = ""
    deco_assets_blob_url: str = ""
    deco_assets_manifest_url: str = ""
    deco_assets_version_url: str = ""
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
                APP_DIR / "config" / "remote_db.json",
                RESOURCE_DIR / "config" / "remote_db.json",
                RESOURCE_DIR / "remote_db.json",
                RESOURCE_DIR / "remote_db.example.json",
            )
            if path.exists()
        ),
        None,
    )
    if config_path:
        values = json.loads(config_path.read_text(encoding="utf-8-sig"))

    database_url = str(os.environ.get("MOBIDB_SQLITE_URL") or values.get("database_url") or "")
    version_url = str(os.environ.get("MOBIDB_VERSION_URL") or values.get("version_url") or "")
    deco_assets_blob_url = str(
        os.environ.get("MOBIDB_DECO_ASSETS_BLOB_URL")
        or values.get("deco_assets_blob_url")
        or ""
    )
    deco_assets_manifest_url = str(
        os.environ.get("MOBIDB_DECO_ASSETS_MANIFEST_URL")
        or values.get("deco_assets_manifest_url")
        or ""
    )
    deco_assets_version_url = str(
        os.environ.get("MOBIDB_DECO_ASSETS_VERSION_URL")
        or values.get("deco_assets_version_url")
        or ""
    )
    timeout_raw = os.environ.get("MOBIDB_UPDATE_TIMEOUT") or values.get("timeout_seconds") or 15

    try:
        timeout_seconds = int(timeout_raw)
    except (TypeError, ValueError):
        timeout_seconds = 15

    return RemoteDbConfig(
        database_url=database_url.strip(),
        version_url=version_url.strip(),
        deco_assets_blob_url=deco_assets_blob_url.strip(),
        deco_assets_manifest_url=deco_assets_manifest_url.strip(),
        deco_assets_version_url=deco_assets_version_url.strip(),
        timeout_seconds=max(1, timeout_seconds),
    )


def request_url(url: str, timeout_seconds: int):
    request = urllib.request.Request(url, headers=REQUEST_HEADERS)
    return urllib.request.urlopen(request, timeout=timeout_seconds)


def fetch_text(url: str, timeout_seconds: int) -> str:
    with request_url(url, timeout_seconds) as response:
        return response.read().decode("utf-8").strip()


def print_step(message: str) -> None:
    try:
        print(f"✔️ {message}", flush=True)
    except UnicodeEncodeError:
        print(f"[OK] {message}", flush=True)


def render_progress(downloaded: int, total: int) -> str:
    percent = min(100, int(downloaded * 100 / total)) if total > 0 else 0
    filled = min(PROGRESS_WIDTH, int(PROGRESS_WIDTH * percent / 100))
    bar = "█" * filled + "░" * (PROGRESS_WIDTH - filled)
    return f"✔️ 다운로드 중 . . . [ {bar} ] {percent}%"


def download_file(url: str, target_path, timeout_seconds: int, *, show_progress: bool = False) -> None:
    with request_url(url, timeout_seconds) as response:
        total = int(response.headers.get("Content-Length") or 0)
        downloaded = 0
        if show_progress and total <= 0:
            print_step("다운로드 중 . . .")
        with target_path.open("wb") as file:
            while True:
                chunk = response.read(1024 * 128)
                if not chunk:
                    break
                file.write(chunk)
                downloaded += len(chunk)
                if show_progress and total > 0:
                    try:
                        print("\r" + render_progress(downloaded, total), end="", flush=True)
                    except UnicodeEncodeError:
                        percent = min(100, int(downloaded * 100 / total))
                        print(f"\r[OK] 다운로드 중 . . . {percent}%", end="", flush=True)
        if show_progress and total > 0:
            print()


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


def validate_deco_assets(blob_path: Path, manifest_path: Path) -> None:
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if not isinstance(manifest, dict) or not manifest:
        raise ValueError("deco asset manifest is empty or invalid")

    blob_size = blob_path.stat().st_size
    with blob_path.open("rb") as blob_file:
        for key, entry in manifest.items():
            if not isinstance(key, str) or not isinstance(entry, dict):
                raise ValueError("deco asset manifest entry is invalid")

            offset = entry.get("offset")
            size = entry.get("size")
            if not isinstance(offset, int) or not isinstance(size, int):
                raise ValueError(f"deco asset manifest entry has invalid offset/size: {key}")
            if offset < 0 or size <= 0 or offset + size > blob_size:
                raise ValueError(f"deco asset manifest entry is out of blob range: {key}")

            blob_file.seek(offset)
            if blob_file.read(len(PNG_SIGNATURE)) != PNG_SIGNATURE:
                raise ValueError(f"deco asset is not a PNG: {key}")


def update_database_from_remote(db_path: Path) -> RemoteDbUpdateResult:
    print_step("DB 업데이트 확인 중 . . .")
    config = load_remote_db_config()
    if not config.database_url:
        return RemoteDbUpdateResult("skipped", "remote DB URL is not configured")

    db_path.parent.mkdir(parents=True, exist_ok=True)

    try:
        remote_version = fetch_text(config.version_url, config.timeout_seconds) if config.version_url else ""
        if remote_version:
            local_version = VERSION_PATH.read_text(encoding="utf-8").strip() if VERSION_PATH.exists() else ""
            if db_path.exists() and local_version == remote_version:
                print_step("DB 최신버전입니다.")
                return RemoteDbUpdateResult("unchanged", remote_version)
            print_step(f"새 DB 버전 발견 : {remote_version}")

        temp_path = db_path.with_suffix(".sqlite.download")
        download_file(config.database_url, temp_path, config.timeout_seconds, show_progress=True)
        validate_sqlite(temp_path)
        os.replace(temp_path, db_path)

        if remote_version:
            VERSION_PATH.write_text(remote_version + "\n", encoding="utf-8")

        print_step("DB 업데이트 적용 완료")
        return RemoteDbUpdateResult("updated", remote_version)
    except (OSError, sqlite3.DatabaseError, urllib.error.URLError, urllib.error.HTTPError) as exc:
        try:
            db_path.with_suffix(".sqlite.download").unlink(missing_ok=True)
        except OSError:
            pass
        return RemoteDbUpdateResult("failed", str(exc))


def update_deco_assets_from_remote() -> RemoteDbUpdateResult:
    print_step("데코 이미지 업데이트 확인 중 . . .")
    config = load_remote_db_config()
    if not (
        config.deco_assets_blob_url
        and config.deco_assets_manifest_url
        and config.deco_assets_version_url
    ):
        return RemoteDbUpdateResult("skipped", "remote deco asset URL is not configured")

    DATA_DIR.mkdir(parents=True, exist_ok=True)

    temp_blob_path = DECO_ASSET_BLOB_PATH.with_suffix(".blob.download")
    temp_manifest_path = DECO_ASSET_MANIFEST_PATH.with_suffix(".json.download")
    try:
        remote_version = fetch_text(config.deco_assets_version_url, config.timeout_seconds)
        if not remote_version:
            return RemoteDbUpdateResult("failed", "remote deco asset version is empty")

        local_version = (
            DECO_ASSET_VERSION_PATH.read_text(encoding="utf-8").strip()
            if DECO_ASSET_VERSION_PATH.exists()
            else ""
        )
        if (
            DECO_ASSET_BLOB_PATH.exists()
            and DECO_ASSET_MANIFEST_PATH.exists()
            and local_version == remote_version
        ):
            print_step("데코 이미지 최신버전입니다.")
            return RemoteDbUpdateResult("unchanged", remote_version)

        print_step(f"새 데코 이미지 버전 발견 : {remote_version}")
        download_file(config.deco_assets_blob_url, temp_blob_path, config.timeout_seconds, show_progress=True)
        download_file(config.deco_assets_manifest_url, temp_manifest_path, config.timeout_seconds)
        validate_deco_assets(temp_blob_path, temp_manifest_path)
        os.replace(temp_blob_path, DECO_ASSET_BLOB_PATH)
        os.replace(temp_manifest_path, DECO_ASSET_MANIFEST_PATH)
        DECO_ASSET_VERSION_PATH.write_text(remote_version + "\n", encoding="utf-8")

        print_step("데코 이미지 업데이트 적용 완료")
        return RemoteDbUpdateResult("updated", remote_version)
    except (OSError, ValueError, json.JSONDecodeError, urllib.error.URLError, urllib.error.HTTPError) as exc:
        try:
            temp_blob_path.unlink(missing_ok=True)
            temp_manifest_path.unlink(missing_ok=True)
        except OSError:
            pass
        return RemoteDbUpdateResult("failed", str(exc))
