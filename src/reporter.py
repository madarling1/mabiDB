from __future__ import annotations

import json
import os
import secrets
import urllib.error
import urllib.request
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path

from paths import APP_DIR, CONFIG_DIR, DATA_DIR, RESOURCE_DIR, USER_DATA_DIR, is_frozen


CONFIG_PATH = CONFIG_DIR / "remote_db.json"
DB_VERSION_PATH = DATA_DIR / "db_version.txt"
APP_VERSION_PATH = DATA_DIR / "app_version.txt"
FEEDBACK_IDENTITY_PATH = USER_DATA_DIR / "feedback_identity.json"
FEEDBACK_SEEN_REPLIES_PATH = USER_DATA_DIR / "feedback_seen_replies.json"
REQUEST_HEADERS = {"User-Agent": "mabiDB"}
DISCORD_CONTENT_LIMIT = 1900
KST = timezone(timedelta(hours=9))
SECTION_DIVIDER = "----------------------------------------------"


@dataclass(frozen=True)
class ReportConfig:
    feedback_url: str = ""
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
    request_id: str = ""


@dataclass(frozen=True)
class FeedbackReply:
    reply_id: str
    request_id: str
    message: str
    created_at: str


@dataclass(frozen=True)
class FeedbackThread:
    request_id: str
    created_at: str
    message: str
    search_scope: str
    search_query: str
    status: str
    replies: list[FeedbackReply]


@dataclass(frozen=True)
class FeedbackThreadsResult:
    status: str
    threads: list[FeedbackThread]
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
    feedback_url = str(
        os.environ.get("MOBIDB_FEEDBACK_URL")
        or values.get("feedback_url")
        or ""
    ).strip()

    return ReportConfig(
        feedback_url=feedback_url,
        webhook_url=webhook_url,
        timeout_seconds=max(1, timeout_seconds),
    )


def read_text(path: Path) -> str:
    return path.read_text(encoding="utf-8").strip() if path.exists() else ""


def feedback_reply_token() -> str:
    try:
        values = json.loads(FEEDBACK_IDENTITY_PATH.read_text(encoding="utf-8"))
        token = str(values.get("replyToken", "")).strip()
        if token:
            return token
    except (OSError, json.JSONDecodeError):
        pass

    token = secrets.token_urlsafe(32)
    FEEDBACK_IDENTITY_PATH.parent.mkdir(parents=True, exist_ok=True)
    FEEDBACK_IDENTITY_PATH.write_text(
        json.dumps({"replyToken": token}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return token


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
    if not config.feedback_url and not config.webhook_url:
        return ReportResult("failed", "feedback URL is not configured")

    try:
        if config.feedback_url:
            request_id = send_feedback_request(config.feedback_url, report, config.timeout_seconds)
            return ReportResult("sent", request_id=request_id)
        send_discord_webhook(config.webhook_url, report, config.timeout_seconds)
        return ReportResult("sent")
    except (OSError, urllib.error.URLError, urllib.error.HTTPError, ValueError, json.JSONDecodeError) as exc:
        return ReportResult("failed", str(exc))


def send_feedback_request(feedback_url: str, report: RevisionRequest, timeout_seconds: int) -> str:
    recent_results = []
    for item in report.recent_results:
        recent_results.append(
            {
                "name": item.get("name") or "",
                "type": item.get("type") or "",
                "typeLabel": item.get("type_label") or item.get("type") or "",
            }
        )
    payload = json.dumps(
        {
            "platform": "desktop",
            "replyToken": feedback_reply_token(),
            "message": report.message,
            "searchScope": report.scope_label,
            "searchScopeKey": report.scope,
            "searchQuery": report.keyword,
            "recentResults": recent_results,
            "dbVersion": report.db_version,
            "appVersion": report.app_version,
            "runMode": report.run_mode,
        },
        ensure_ascii=False,
    ).encode("utf-8")
    request = urllib.request.Request(
        feedback_url,
        data=payload,
        headers={
            **REQUEST_HEADERS,
            "Content-Type": "application/json; charset=utf-8",
        },
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=timeout_seconds) as response:
        if response.status >= 400:
            raise ValueError(f"feedback server failed: HTTP {response.status}")
        body = response.read().decode("utf-8")
    data = json.loads(body) if body.strip() else {}
    if data.get("ok") is False:
        raise ValueError(str(data.get("error") or "feedback server failed"))
    return str(data.get("requestId", "")).strip()


def feedback_replies_url(feedback_url: str) -> str:
    if feedback_url.endswith("/replies"):
        return feedback_url
    return feedback_url.rstrip("/") + "/replies"


def fetch_revision_replies() -> FeedbackThreadsResult:
    config = load_report_config()
    if not config.feedback_url:
        return FeedbackThreadsResult("unavailable", [], "feedback URL is not configured")

    payload = json.dumps(
        {"replyToken": feedback_reply_token()},
        ensure_ascii=False,
    ).encode("utf-8")
    request = urllib.request.Request(
        feedback_replies_url(config.feedback_url),
        data=payload,
        headers={
            **REQUEST_HEADERS,
            "Content-Type": "application/json; charset=utf-8",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=config.timeout_seconds) as response:
            if response.status >= 400:
                raise ValueError(f"feedback server failed: HTTP {response.status}")
            body = response.read().decode("utf-8")
        data = json.loads(body) if body.strip() else {}
        if data.get("ok") is False:
            return FeedbackThreadsResult("failed", [], str(data.get("error") or "feedback server failed"))
        return FeedbackThreadsResult("ok", parse_feedback_threads(data.get("threads")))
    except (OSError, urllib.error.URLError, urllib.error.HTTPError, ValueError, json.JSONDecodeError) as exc:
        return FeedbackThreadsResult("failed", [], str(exc))


def parse_feedback_threads(value) -> list[FeedbackThread]:
    if not isinstance(value, list):
        return []

    threads = []
    for item in value:
        if not isinstance(item, dict):
            continue
        request_id = str(item.get("requestId", "")).strip()
        replies = []
        raw_replies = item.get("replies")
        if isinstance(raw_replies, list):
            for reply in raw_replies:
                if not isinstance(reply, dict):
                    continue
                reply_id = str(reply.get("replyId", "")).strip()
                replies.append(
                    FeedbackReply(
                        reply_id=reply_id,
                        request_id=request_id,
                        message=str(reply.get("message", "")).strip(),
                        created_at=str(reply.get("createdAt", "")).strip(),
                    )
                )
        threads.append(
            FeedbackThread(
                request_id=request_id,
                created_at=str(item.get("createdAt", "")).strip(),
                message=str(item.get("message", "")).strip(),
                search_scope=str(item.get("searchScope", "")).strip(),
                search_query=str(item.get("searchQuery", "")).strip(),
                status=str(item.get("status", "")).strip(),
                replies=replies,
            )
        )
    return threads


def feedback_reply_ids(threads: list[FeedbackThread]) -> set[str]:
    return {reply.reply_id for thread in threads for reply in thread.replies if reply.reply_id}


def read_seen_feedback_reply_ids() -> set[str]:
    try:
        values = json.loads(FEEDBACK_SEEN_REPLIES_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return set()
    if not isinstance(values, list):
        return set()
    return {str(value) for value in values if str(value).strip()}


def unread_feedback_reply_count(threads: list[FeedbackThread]) -> int:
    return len(feedback_reply_ids(threads) - read_seen_feedback_reply_ids())


def mark_feedback_replies_seen(threads: list[FeedbackThread]) -> None:
    reply_ids = feedback_reply_ids(threads)
    if not reply_ids:
        return
    seen = read_seen_feedback_reply_ids() | reply_ids
    FEEDBACK_SEEN_REPLIES_PATH.parent.mkdir(parents=True, exist_ok=True)
    FEEDBACK_SEEN_REPLIES_PATH.write_text(
        json.dumps(sorted(seen), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def clear_feedback_history() -> None:
    for path in (FEEDBACK_IDENTITY_PATH, FEEDBACK_SEEN_REPLIES_PATH):
        try:
            path.unlink()
        except FileNotFoundError:
            pass

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
