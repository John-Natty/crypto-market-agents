"""Shared, deterministic validation for external analysis inputs."""

from __future__ import annotations

from collections.abc import Sequence
from datetime import UTC, datetime, timedelta
import math
from typing import Any


def utc_now() -> datetime:
    return datetime.now(UTC)


def finite_number(value: Any) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError, OverflowError):
        return None
    return number if math.isfinite(number) else None


def parse_timestamp(value: Any) -> datetime | None:
    """Accept ISO dates with a timezone, or Unix seconds for TVL histories."""
    try:
        if isinstance(value, int | float) and not isinstance(value, bool):
            return datetime.fromtimestamp(value, UTC)
        if isinstance(value, str):
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
            if parsed.tzinfo is not None:
                return parsed.astimezone(UTC)
    except (ValueError, OverflowError, OSError):
        pass
    return None


def is_fresh(timestamp: datetime, now: datetime, max_age: timedelta) -> bool:
    return -timedelta(minutes=2) <= now - timestamp <= max_age


def validate_market_data(
    payload: list[dict[str, Any]],
    requested_ids: Sequence[str],
    fields: Sequence[str],
    *,
    now: datetime,
    max_age: timedelta,
) -> tuple[list[dict[str, Any]], list[str]]:
    """Discard unpriceable/stale assets and null invalid optional metrics."""
    assets: list[dict[str, Any]] = []
    errors: list[str] = []
    seen: set[str] = set()
    for raw in payload:
        if not isinstance(raw, dict):
            errors.append("Invalid market data row: expected an object.")
            continue
        asset = dict(raw)
        asset_id = str(asset.get("id") or "").strip().lower()
        if asset_id not in requested_ids or asset_id in seen:
            errors.append(f"Unexpected or duplicate market asset: {asset_id or 'unknown'}")
            continue
        seen.add(asset_id)
        asset["id"] = asset_id
        timestamp = parse_timestamp(asset.get("last_updated"))
        if timestamp is None or not is_fresh(timestamp, now, max_age):
            errors.append(f"Unusable or stale last_updated for {asset_id}.")
            continue
        for field in fields:
            if field == "last_updated":
                continue
            value = finite_number(asset.get(field))
            valid = value is not None
            if valid and field.startswith("price_change_percentage"):
                valid = value >= -100
            elif valid:
                valid = value >= 0
                if field in {"current_price", "high_24h", "low_24h", "market_cap_rank"}:
                    valid = value > 0
                if field == "market_cap_rank":
                    valid = valid and value.is_integer()
            asset[field] = value if valid else None
            if not valid:
                errors.append(f"Missing or invalid {field} for {asset_id}.")
        if asset.get("current_price") is None:
            continue
        high, low, price = asset.get("high_24h"), asset.get("low_24h"), asset["current_price"]
        if high is not None and low is not None:
            if high < low or not low * 0.999 <= price <= high * 1.001:
                asset["high_24h"] = asset["low_24h"] = None
                errors.append(f"Inconsistent current_price/high_24h/low_24h for {asset_id}.")
        volume, market_cap = asset.get("total_volume"), asset.get("market_cap")
        if volume is not None and market_cap and finite_number(volume / market_cap) is None:
            asset["total_volume"] = None
            errors.append(f"Unusable volume/capitalisation ratio for {asset_id}.")
        high, low = asset.get("high_24h"), asset.get("low_24h")
        if high is not None and low is not None:
            if finite_number(((high - low) / price) * 100) is None:
                asset["high_24h"] = asset["low_24h"] = None
                errors.append(f"Unusable price range for {asset_id}.")
        assets.append(asset)
    return assets, errors
