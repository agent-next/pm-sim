"""FXMacroData release-calendar helpers for event-driven markets."""

from __future__ import annotations

from dataclasses import dataclass
import os
from typing import Any

import httpx

FXMACRODATA_BASE_URL = "https://fxmacrodata.com/api/v1"


@dataclass(frozen=True)
class MacroEvent:
    """A macroeconomic or central-bank release event."""

    release: str
    name: str
    currency: str
    date: str
    market_tier: int | None
    announcement_datetime_utc: str | None
    source: str | None
    source_url: str | None


def get_macro_events(
    currency: str = "usd",
    *,
    limit: int = 20,
    min_tier: int | None = None,
    api_key: str | None = None,
    base_url: str = FXMACRODATA_BASE_URL,
) -> list[MacroEvent]:
    """Fetch FXMacroData events that can be mapped to prediction markets."""
    limit = max(1, int(limit))
    params: dict[str, Any] = {"limit": limit}
    token = api_key or os.environ.get("FXMACRODATA_API_KEY")
    if token:
        params["api_key"] = token

    with httpx.Client(timeout=30) as client:
        response = client.get(
            f"{base_url.rstrip('/')}/calendar/{currency.lower()}",
            params=params,
        )
        response.raise_for_status()
        rows = response.json().get("data", [])

    if min_tier is not None:
        rows = [
            row
            for row in rows
            if int(row.get("market_tier") or 99) <= int(min_tier)
        ]

    return [
        MacroEvent(
            release=str(row.get("release") or ""),
            name=str(row.get("name") or ""),
            currency=str(row.get("currency") or currency).upper(),
            date=str(row.get("date") or ""),
            market_tier=row.get("market_tier"),
            announcement_datetime_utc=row.get("announcement_datetime_utc"),
            source=row.get("source"),
            source_url=row.get("source_url"),
        )
        for row in rows[:limit]
    ]
