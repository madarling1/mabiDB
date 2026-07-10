from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from dataclasses import dataclass

from reporter import load_report_config


REQUEST_HEADERS = {"User-Agent": "mabiDB"}
DEFAULT_TIMEOUT_SECONDS = 3


@dataclass(frozen=True)
class MaterialPrice:
    name: str
    min_price: int | None
    total_count: int
    sold_out: bool
    last_version: str
    status: str


@dataclass(frozen=True)
class ResistMarketPrices:
    ok: bool
    updated_at: str
    cache_updated_at: str
    items: dict[str, MaterialPrice]
    error: str = ""


def empty_resist_market_prices(error: str = "") -> ResistMarketPrices:
    return ResistMarketPrices(False, "", "", {}, error)


def market_prices_url_from_feedback_url(feedback_url: str) -> str:
    url = feedback_url.strip()
    if not url:
        return ""
    marker = "/api/feedback"
    if marker in url:
        return url.split(marker, 1)[0].rstrip("/") + "/api/market/resist-prices"
    return url.rstrip("/") + "/api/market/resist-prices"


def configured_market_prices_url() -> str:
    override = os.environ.get("MOBIDB_MARKET_PRICES_URL", "").strip()
    if override:
        return override
    return market_prices_url_from_feedback_url(load_report_config().feedback_url)


def market_timeout_seconds() -> int:
    raw = os.environ.get("MOBIDB_MARKET_TIMEOUT", "").strip()
    if not raw:
        return DEFAULT_TIMEOUT_SECONDS
    try:
        return max(1, int(raw))
    except ValueError:
        return DEFAULT_TIMEOUT_SECONDS


def parse_material_price(name: str, value) -> MaterialPrice | None:
    if not isinstance(value, dict):
        return None

    min_price = value.get("minPrice")
    try:
        min_price = int(min_price) if min_price is not None else None
    except (TypeError, ValueError):
        min_price = None

    try:
        total_count = int(value.get("totalCount") or 0)
    except (TypeError, ValueError):
        total_count = 0

    return MaterialPrice(
        name=str(value.get("name") or name),
        min_price=min_price,
        total_count=total_count,
        sold_out=bool(value.get("soldOut")),
        last_version=str(value.get("lastVersion") or ""),
        status=str(value.get("status") or "exact"),
    )


def parse_resist_market_prices(payload) -> ResistMarketPrices:
    if not isinstance(payload, dict):
        return empty_resist_market_prices("invalid response")

    items = {}
    raw_items = payload.get("items")
    if isinstance(raw_items, dict):
        for name, value in raw_items.items():
            price = parse_material_price(str(name), value)
            if price is not None:
                items[str(name)] = price

    return ResistMarketPrices(
        ok=bool(payload.get("ok")) and bool(items),
        updated_at=str(payload.get("updatedAt") or ""),
        cache_updated_at=str(payload.get("cacheUpdatedAt") or ""),
        items=items,
        error=str(payload.get("error") or ""),
    )


def fetch_resist_market_prices() -> ResistMarketPrices:
    url = configured_market_prices_url()
    if not url:
        return empty_resist_market_prices("market prices URL is not configured")

    request = urllib.request.Request(url, headers=REQUEST_HEADERS)
    try:
        with urllib.request.urlopen(request, timeout=market_timeout_seconds()) as response:
            payload = json.loads(response.read().decode("utf-8"))
        return parse_resist_market_prices(payload)
    except (
        OSError,
        TimeoutError,
        urllib.error.URLError,
        urllib.error.HTTPError,
        json.JSONDecodeError,
        UnicodeDecodeError,
    ) as error:
        return empty_resist_market_prices(str(error))
