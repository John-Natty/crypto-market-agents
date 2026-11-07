"""Pure historical candle validation, metrics, and explainable observations."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
import math


@dataclass(frozen=True)
class Candle:
    timestamp: datetime
    open: Decimal
    high: Decimal
    low: Decimal
    close: Decimal
    volume_base: Decimal


def _positive(value, label, allow_zero=False):
    try:
        result = Decimal(str(value))
        if not result.is_finite() or result < 0 or (not allow_zero and result == 0):
            raise ValueError
        return result
    except (InvalidOperation, TypeError, ValueError):
        raise ValueError(f"{label} invalide") from None


def normalize_candles(rows, *, interval: str, now: datetime):
    """Validate API rows, reject duplicate timestamps, sort and exclude open candles."""
    intervals = {"1h": 3600, "4h": 14400, "1d": 86400}
    if interval not in intervals:
        raise ValueError("intervalle non pris en charge")
    parsed, invalid = [], []
    if not isinstance(rows, list):
        rows = []
        invalid.append("réponse de bougies invalide")
    for index, row in enumerate(rows):
        try:
            if not isinstance(row, (list, tuple)) or len(row) != 6:
                raise ValueError("format de bougie invalide")
            raw_ts = row[0]
            if isinstance(raw_ts, bool):
                raise ValueError("timestamp invalide")
            ts = int(raw_ts)
            stamp = datetime.fromtimestamp(ts / 1000, timezone.utc)
            opened, high, low, close = (_positive(row[i], "prix") for i in (1, 2, 3, 4))
            volume = _positive(row[5], "volume", allow_zero=True)
            if high < max(opened, close, low) or low > min(opened, close, high):
                raise ValueError("OHLC incohérent")
            parsed.append(Candle(stamp, opened, high, low, close, volume))
        except (ValueError, TypeError, OverflowError, OSError):
            invalid.append(f"bougie {index}: valeur ou format invalide")
    counts = {}
    for candle in parsed:
        counts[candle.timestamp] = counts.get(candle.timestamp, 0) + 1
    duplicates = {stamp for stamp, count in counts.items() if count > 1}
    if duplicates:
        parsed = [c for c in parsed if c.timestamp not in duplicates]
        invalid.append(f"{len(duplicates)} horodatage(s) dupliqué(s), retirés")
    original_order = [c.timestamp for c in parsed]
    sorted_rows = sorted(parsed, key=lambda c: c.timestamp)
    if original_order != [c.timestamp for c in sorted_rows]:
        invalid.append("bougies reçues hors ordre chronologique, triées en UTC")
    now = now.astimezone(timezone.utc)
    seconds = intervals[interval]
    closed = [c for c in sorted_rows if c.timestamp + timedelta(seconds=seconds) <= now]
    open_count = len(sorted_rows) - len(closed)
    if open_count:
        invalid.append(f"{open_count} bougie(s) encore ouverte(s) exclue(s)")
    gaps = []
    for before, after in zip(closed, closed[1:]):
        delta = int((after.timestamp - before.timestamp).total_seconds())
        if delta > seconds:
            gaps.append(
                {
                    "from": before.timestamp.isoformat(),
                    "to": after.timestamp.isoformat(),
                    "missing_intervals": delta // seconds - 1,
                }
            )
    if gaps:
        invalid.append(f"{len(gaps)} trou(s) temporel(s) détecté(s)")
    age = (
        (now - (closed[-1].timestamp + timedelta(seconds=seconds))).total_seconds()
        if closed
        else None
    )
    fresh = age is not None and 0 <= age <= seconds * 2
    if closed and not fresh:
        invalid.append("dernière bougie clôturée ancienne (fraîcheur > 2 intervalles)")
    return closed, {
        "received": len(rows),
        "valid": len(parsed),
        "closed": len(closed),
        "open_excluded": open_count,
        "duplicates_removed": len(duplicates),
        "invalid_rows": invalid,
        "gaps": gaps,
        "latest_closed_at": closed[-1].timestamp.isoformat() if closed else None,
        "latest_age_seconds": age,
        "fresh": fresh,
    }


def calculate_metrics(candles, *, interval: str, now: datetime, min_history=30):
    """Calculate simple returns, sample volatility, SMA relation and volume ratio."""
    closes = [c.close for c in candles]
    volumes = [c.volume_base for c in candles]
    horizon_bars = {
        "1d": {"24h": 1, "7d": 7, "30d": 30},
        "1h": {"24h": 24, "7d": 168, "30d": 720},
        "4h": {"24h": 6, "7d": 42, "30d": 180},
    }[interval]
    changes = {}
    for label, bars in horizon_bars.items():
        changes[label] = (
            ((closes[-1] / closes[-1 - bars] - 1) * 100) if len(closes) > bars else None
        )
    returns = [float(closes[i] / closes[i - 1] - 1) for i in range(1, len(closes))]
    volatility = None
    if len(returns) >= 2:
        mean_return = sum(returns) / len(returns)
        volatility = Decimal(
            str(math.sqrt(sum((r - mean_return) ** 2 for r in returns) / (len(returns) - 1)) * 100)
        )
    sma_fast = sum(closes[-20:]) / 20 if len(closes) >= 20 else None
    sma_slow = sum(closes[-50:]) / 50 if len(closes) >= 50 else None
    volume_ratio = None
    if len(volumes) >= 21:
        historical_avg = sum(volumes[-21:-1]) / 20
        if historical_avg > 0:
            volume_ratio = volumes[-1] / historical_avg
    measures = {
        "returns_pct": {
            k: str(v.quantize(Decimal("0.0001"))) if v is not None else None
            for k, v in changes.items()
        },
        "return_volatility_pct_per_interval": str(volatility.quantize(Decimal("0.0001")))
        if volatility is not None
        else None,
        "sma20_close": str(sma_fast) if sma_fast is not None else None,
        "sma50_close": str(sma_slow) if sma_slow is not None else None,
        "close_vs_sma20_pct": str(((closes[-1] / sma_fast - 1) * 100).quantize(Decimal("0.0001")))
        if sma_fast is not None
        else None,
        "sma20_vs_sma50_pct": str(((sma_fast / sma_slow - 1) * 100).quantize(Decimal("0.0001")))
        if sma_fast is not None and sma_slow is not None
        else None,
        "latest_volume_base": str(volumes[-1]) if volumes else None,
        "latest_volume_vs_previous_20_mean": str(volume_ratio.quantize(Decimal("0.0001")))
        if volume_ratio is not None
        else None,
    }
    sufficient = len(candles) >= min_history
    favorable, risks, reasons = [], [], []
    for horizon, value in changes.items():
        if value is None:
            reasons.append(f"variation {horizon} indisponible: historique insuffisant")
        elif value > 0:
            favorable.append(f"variation passée {horizon}: +{value.quantize(Decimal('0.01'))}%")
        elif value < 0:
            risks.append(f"variation passée {horizon}: {value.quantize(Decimal('0.01'))}%")
    if sma_fast is not None and sma_slow is not None:
        if closes[-1] > sma_fast > sma_slow:
            favorable.append("clôture > SMA20 > SMA50")
        elif closes[-1] < sma_fast < sma_slow:
            risks.append("clôture < SMA20 < SMA50")
        else:
            risks.append("moyennes mobiles en configuration mixte")
    else:
        reasons.append("tendance SMA20/SMA50 insuffisante: 50 clôtures requises")
    if volume_ratio is None:
        reasons.append(
            "comparaison du volume insuffisante: 21 clôtures requises et moyenne historique non nulle"
        )
    elif volume_ratio > Decimal("1.5"):
        favorable.append("volume de la dernière bougie > 1,5 fois la moyenne des 20 précédentes")
    elif volume_ratio < Decimal("0.5"):
        risks.append("volume de la dernière bougie < moitié de la moyenne des 20 précédentes")
    if volatility is None:
        reasons.append("volatilité insuffisante: au moins 3 clôtures requises")
    else:
        risks.append(
            f"volatilité des rendements par intervalle: {volatility.quantize(Decimal('0.01'))}%"
        )
    if not sufficient:
        reasons.append(f"historique insuffisant: {len(candles)} clôtures, {min_history} requises")
    decision = (
        "surveiller pour analyse complémentaire"
        if sufficient and not reasons
        else "écarter pour l'instant: données insuffisantes ou incomplètes"
    )
    return {
        "metrics": measures,
        "favorable_signals": favorable,
        "risks_and_contradictions": risks,
        "data_sufficiency": {
            "sufficient": sufficient,
            "closed_candles": len(candles),
            "minimum_required": min_history,
        },
        "reasons": reasons,
        "disposition": decision,
    }
