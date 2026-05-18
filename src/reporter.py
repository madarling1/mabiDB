from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path

from paths import APP_DIR, CONFIG_DIR, DATA_DIR, RESOURCE_DIR, is_frozen


CONFIG_PATH = CONFIG_DIR / "remote_db.json"
DB_VERSION_PATH = DATA_DIR / "db_version.txt"
APP_VERSION_PATH = DATA_DIR / "app_version.txt"
REQUEST_HEADERS = {"User-Agent": "mabiDB"}
DISCORD_CONTENT_LIMIT = 1900
KST = timezone(timedelta(hours=9))
SECTION_DIVIDER = "----------------------------------------------"


@dataclass(frozen=True)
class ReportConfig:
    webhook_url: str = ""
    timeout_seconds: int = 15


@dataclass(frozen=True)
class RevisionRequest:
    scope: str
    scope_label: str
    keyword: str
    message: str
    recent_results: list[dict[str, object]]
    db_version: str
    app_version: str
    run_mode: str
    created_at: str


@dataclass(frozen=True)
class ReportResult:
    status: str
    message: str = ""


def load_report_config() -> ReportConfig:
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

    timeout_raw = os.environ.get("MOBIDB_REPORT_TIMEOUT") or values.get("timeout_seconds") or 15
    try:
        timeout_seconds = int(timeout_raw)
    except (TypeError, ValueError):
        timeout_seconds = 15

    webhook_url = str(
        os.environ.get("MOBIDB_REPORT_WEBHOOK_URL")
        or values.get("report_webhook_url")
        or values.get("discord_webhook_url")
        or ""
    ).strip()

    return ReportConfig(webhook_url=webhook_url, timeout_seconds=max(1, timeout_seconds))


def read_text(path: Path) -> str:
    return path.read_text(encoding="utf-8").strip() if path.exists() else ""


def build_revision_request(
    *,
    scope: str,
    scope_label: str,
    keyword: str,
    message: str,
    recent_results: list[dict[str, object]],
) -> RevisionRequest:
    return RevisionRequest(
        scope=scope,
        scope_label=scope_label,
        keyword=keyword,
        message=message,
        recent_results=recent_results,
        db_version=read_text(DB_VERSION_PATH),
        app_version=read_text(APP_VERSION_PATH),
        run_mode="exe" if is_frozen() else "source",
        created_at=datetime.now(KST).strftime("%Y-%m-%d %H:%M:%S KST"),
    )


def submit_revision_request(report: RevisionRequest) -> ReportResult:
    config = load_report_config()
    if not config.webhook_url:
        return ReportResult("failed", "webhook URL is not configured")

    try:
        send_discord_webhook(config.webhook_url, report, config.timeout_seconds)
        return ReportResult("sent")
    except (OSError, urllib.error.URLError, urllib.error.HTTPError, ValueError) as exc:
        return ReportResult("failed", str(exc))


def send_discord_webhook(webhook_url: str, report: RevisionRequest, timeout_seconds: int) -> None:
    payload = json.dumps(
        {"content": build_discord_content(report)},
        ensure_ascii=False,
    ).encode("utf-8")
    request = urllib.request.Request(
        webhook_url,
        data=payload,
        headers={
            **REQUEST_HEADERS,
            "Content-Type": "application/json; charset=utf-8",
        },
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=timeout_seconds) as response:
        if response.status >= 400:
            raise ValueError(f"Discord webhook failed: HTTP {response.status}")


def build_discord_content(report: RevisionRequest) -> str:
    lines = [
        SECTION_DIVIDER,
        "최근 검색 결과",
    ]

    if report.recent_results:
        for index, item in enumerate(report.recent_results, 1):
            name = item.get("name") or "-"
            item_type = item.get("type_label") or item.get("type") or "-"
            lines.append(f"{index}. {name} ({item_type})")
    else:
        lines.append("검색 결과 없음")

    lines.extend(
        [
            "",
            f"제보 시각 : {report.created_at}",
            f"검색범위 : {report.scope_label}",
            f"검색어 : {report.keyword}",
            f"앱 버전 : {report.app_version or '-'}",
            f"DB 버전: {report.db_version or '-'}",
            "",
            "요청내용:",
            "",
            report.message,
            SECTION_DIVIDER,
        ]
    )
    content = "\n".join(lines)
    if len(content) <= DISCORD_CONTENT_LIMIT:
        return content
    return content[: DISCORD_CONTENT_LIMIT - 20].rstrip() + "\n...(truncated)"
