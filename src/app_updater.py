from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import time
import urllib.error
from dataclasses import dataclass
from pathlib import Path

from paths import APP_DIR, CONFIG_DIR, DATA_DIR, RESOURCE_DIR, is_frozen
from db_updater import download_file, fetch_text


CONFIG_PATH = CONFIG_DIR / "remote_db.json"
APP_VERSION_PATH = DATA_DIR / "app_version.txt"
BUNDLED_APP_VERSION_PATH = RESOURCE_DIR / "data" / "app_version.txt"
DEFAULT_APP_VERSION_URL = "https://raw.githubusercontent.com/madarling1/mabiDB/refs/heads/main/data/app_version.txt"
DEFAULT_APP_DOWNLOAD_URL = "https://github.com/madarling1/mabiDB/releases/latest/download/mabiDB.exe"
APPLY_UPDATE_COMMAND = "--apply-app-update"
UPDATE_DONE_MARKER = "app_update_done.txt"


@dataclass(frozen=True)
class AppUpdateConfig:
    app_version_url: str = DEFAULT_APP_VERSION_URL
    app_download_url: str = DEFAULT_APP_DOWNLOAD_URL
    timeout_seconds: int = 15


@dataclass(frozen=True)
class AppUpdateResult:
    status: str
    message: str = ""


def load_app_update_config() -> AppUpdateConfig:
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
            )
            if path.exists()
        ),
        None,
    )
    if config_path:
        values = json.loads(config_path.read_text(encoding="utf-8-sig"))

    timeout_raw = os.environ.get("MOBIDB_UPDATE_TIMEOUT") or values.get("timeout_seconds") or 15
    try:
        timeout_seconds = int(timeout_raw)
    except (TypeError, ValueError):
        timeout_seconds = 15

    return AppUpdateConfig(
        app_version_url=str(
            os.environ.get("MOBIDB_APP_VERSION_URL")
            or values.get("app_version_url")
            or DEFAULT_APP_VERSION_URL
        ).strip(),
        app_download_url=str(
            os.environ.get("MOBIDB_APP_DOWNLOAD_URL")
            or values.get("app_download_url")
            or DEFAULT_APP_DOWNLOAD_URL
        ).strip(),
        timeout_seconds=max(1, timeout_seconds),
    )


def ensure_local_app_version() -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    if not APP_VERSION_PATH.exists() and BUNDLED_APP_VERSION_PATH.exists():
        shutil.copy2(BUNDLED_APP_VERSION_PATH, APP_VERSION_PATH)


def read_local_app_version() -> str:
    ensure_local_app_version()
    return APP_VERSION_PATH.read_text(encoding="utf-8").strip() if APP_VERSION_PATH.exists() else ""


def is_remote_newer(local_version: str, remote_version: str) -> bool:
    local = local_version.strip()
    remote = remote_version.strip()
    return bool(remote and remote != local and remote > local)


def validate_downloaded_exe(path: Path) -> None:
    with path.open("rb") as file:
        signature = file.read(2)
    if signature != b"MZ":
        raise OSError("downloaded file is not a Windows exe")


def update_dir_for_app() -> Path:
    return APP_DIR / ".update"


def update_done_marker_path() -> Path:
    return update_dir_for_app() / UPDATE_DONE_MARKER


def read_completed_app_update() -> AppUpdateResult | None:
    if not is_frozen():
        return None

    marker_path = update_done_marker_path()
    if not marker_path.exists():
        cleanup_update_dir()
        return None

    version = marker_path.read_text(encoding="utf-8").strip()
    marker_path.unlink(missing_ok=True)
    cleanup_update_dir()
    return AppUpdateResult("updated", version)


def cleanup_update_dir() -> None:
    update_dir = update_dir_for_app()
    if not update_dir.exists():
        return

    for name in ("mabiDB.download", "mabiDB.new.exe", "mabiDB.old.exe", "mabiDB.helper.exe"):
        path = update_dir / name
        for _ in range(20):
            try:
                path.unlink(missing_ok=True)
                break
            except OSError:
                time.sleep(0.1)

    try:
        update_dir.rmdir()
    except OSError:
        pass


def update_app_from_remote() -> AppUpdateResult:
    ensure_local_app_version()
    if not is_frozen():
        return AppUpdateResult("skipped", "source run")

    config = load_app_update_config()
    if not config.app_version_url or not config.app_download_url:
        return AppUpdateResult("skipped", "app update URL is not configured")

    try:
        local_version = read_local_app_version()
        remote_version = fetch_text(config.app_version_url, config.timeout_seconds)
        if not is_remote_newer(local_version, remote_version):
            return AppUpdateResult("unchanged", remote_version)

        exe_path = Path(sys.executable).resolve()
        update_dir = update_dir_for_app()
        update_dir.mkdir(parents=True, exist_ok=True)
        new_path = update_dir / "mabiDB.new.exe"
        helper_path = update_dir / "mabiDB.helper.exe"
        old_path = update_dir / "mabiDB.old.exe"
        download_path = update_dir / "mabiDB.download"

        download_file(config.app_download_url, download_path, config.timeout_seconds)
        validate_downloaded_exe(download_path)
        os.replace(download_path, new_path)
        shutil.copy2(exe_path, helper_path)

        subprocess.Popen(
            [
                str(helper_path),
                APPLY_UPDATE_COMMAND,
                str(exe_path),
                str(new_path),
                str(old_path),
                remote_version,
                str(os.getpid()),
            ],
            cwd=str(exe_path.parent),
            close_fds=True,
        )
        return AppUpdateResult("restarting", remote_version)
    except (OSError, ValueError, urllib.error.URLError, urllib.error.HTTPError) as exc:
        return AppUpdateResult("failed", str(exc))


def handle_app_update_args(args: list[str]) -> bool:
    if not args or args[0] != APPLY_UPDATE_COMMAND:
        return False
    exit_code = apply_app_update(args[1:])
    raise SystemExit(exit_code)


def apply_app_update(args: list[str]) -> int:
    if len(args) != 5:
        return 2

    target_path = Path(args[0])
    new_path = Path(args[1])
    old_path = Path(args[2])
    remote_version = args[3]
    parent_pid = int(args[4])

    wait_for_process_exit(parent_pid)

    if not replace_app_exe(target_path, new_path, old_path):
        return 1

    try:
        APP_VERSION_PATH.parent.mkdir(parents=True, exist_ok=True)
        APP_VERSION_PATH.write_text(remote_version + "\n", encoding="utf-8")
        update_done_marker_path().write_text(remote_version + "\n", encoding="utf-8")
    except OSError as exc:
        print(f"앱 업데이트 상태 저장 실패: {exc}")

    try:
        subprocess.Popen([str(target_path)], cwd=str(target_path.parent), close_fds=True)
    except OSError as exc:
        print(f"앱 재실행 실패: {exc}")
        return 1

    return 0


def replace_app_exe(target_path: Path, new_path: Path, old_path: Path) -> bool:
    last_error: OSError | None = None
    for _ in range(80):
        try:
            if target_path.exists():
                old_path.unlink(missing_ok=True)
                os.replace(target_path, old_path)
            os.replace(new_path, target_path)
            return True
        except OSError as exc:
            last_error = exc
            restore_old_exe(target_path, old_path)
            time.sleep(0.25)

    if last_error:
        print(f"앱 업데이트 실패: {last_error}")
    return False


def restore_old_exe(target_path: Path, old_path: Path) -> None:
    if target_path.exists() or not old_path.exists():
        return
    try:
        os.replace(old_path, target_path)
    except OSError:
        pass


def wait_for_process_exit(pid: int) -> None:
    if os.name != "nt":
        time.sleep(1)
        return

    import ctypes

    synchronize = 0x00100000
    timeout_ms = 30_000
    kernel32 = ctypes.windll.kernel32
    handle = kernel32.OpenProcess(synchronize, False, pid)
    if not handle:
        time.sleep(1)
        return
    try:
        kernel32.WaitForSingleObject(handle, timeout_ms)
    finally:
        kernel32.CloseHandle(handle)
