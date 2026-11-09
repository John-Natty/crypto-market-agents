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
    parsed, invalid_data, notes = [], [], []
    if not isinstance(rows, list):
        rows = []
        invalid_data.append("réponse de bougies invalide")
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
            invalid_data.append(f"bougie {index}: valeur ou format invalide")
    counts = {}
    for candle in parsed:
        counts[candle.timestamp] = counts.get(candle.timestamp, 0) + 1
    duplicates = {stamp for stamp, count in counts.items() if count > 1}
    if duplicates:
        parsed = [c for c in parsed if c.timestamp not in duplicates]
        invalid_data.append(f"{len(duplicates)} horodatage(s) dupliqué(s), retirés")
    original_order = [c.timestamp for c in parsed]
    sorted_rows = sorted(parsed, key=lambda c: c.timestamp)
    if original_order != [c.timestamp for c in sorted_rows]:
        notes.append("bougies reçues hors ordre chronologique, triées en UTC")
    now = now.astimezone(timezone.utc)
    seconds = intervals[interval]
    closed = [c for c in sorted_rows if c.timestamp + timedelta(seconds=seconds) <= now]
    open_count = len(sorted_rows) - len(closed)
    if open_count:
        notes.append(f"{open_count} bougie(s) encore ouverte(s) exclue(s)")
    gaps = []
    for before, after in zip(closed, closed[1:]):
        delta = int((after.timestamp - before.timestamp).total_seconds())
        if delta > seconds:
            gaps.append(
                {
                    "from": before.timestamp.isoformat(),
                    "to": after.timestamp.isoformat(),
                    "missing_intervals": delta // seconds - 1,
                    "cause": "unknown",
                }
            )
    age = (
        (now - (closed[-1].timestamp + timedelta(seconds=seconds))).total_seconds()
        if closed
        else None
    )
    fresh = age is not None and 0 <= age <= seconds * 2
    if closed and not fresh:
        notes.append("dernière bougie clôturée ancienne (fraîcheur > 2 intervalles)")
    return closed, {
        "received": len(rows),
        "valid": len(parsed),
        "closed": len(closed),
        "open_excluded": open_count,
        "duplicates_removed": len(duplicates),
        "invalid_rows": invalid_data,
        "invalid_data": invalid_data,
        "normalization_notes": notes,
        "gaps": gaps,
        "latest_closed_at": closed[-1].timestamp.isoformat() if closed else None,
        "latest_age_seconds": age,
        "fresh": fresh,
    }


def calculate_metrics(candles, *, interval: str, now: datetime, min_history=30):
    """Calculate metrics only across observations with defensible time spacing."""
    closes = [c.close for c in candles]
    volumes = [c.volume_base for c in candles]
    intervals = {"1h": 3600, "4h": 14400, "1d": 86400}
    seconds = intervals[interval]
    stamps = [int(c.timestamp.timestamp()) for c in candles]
    by_stamp = {stamp: close for stamp, close in zip(stamps, closes)}
    index_by_stamp = {stamp: index for index, stamp in enumerate(stamps)}
    horizon_bars = {
        "1d": {"24h": 1, "7d": 7, "30d": 30},
        "1h": {"24h": 24, "7d": 168, "30d": 720},
        "4h": {"24h": 6, "7d": 42, "30d": 180},
    }[interval]
    now = now.astimezone(timezone.utc)
    changes = {}
    metric_quality = {}

    def freshness_for(end_index):
        if end_index is None or end_index < 0:
            return {"fresh": False, "age_seconds": None}
        end = candles[end_index].timestamp + timedelta(seconds=seconds)
        age = (now - end).total_seconds()
        return {"fresh": 0 <= age <= seconds * 2, "age_seconds": age}

    def quality_record(*, available, timestamps_used, reason=None, target_start=None):
        end_index = None
        if timestamps_used:
            end_index = max(
                index_by_stamp[int(datetime.fromisoformat(ts).timestamp())]
                for ts in timestamps_used
            )
        freshness = freshness_for(end_index)
        used_stamps = [int(datetime.fromisoformat(ts).timestamp()) for ts in timestamps_used]
        freshness["oldest_observation_age_seconds"] = (
            (now - datetime.fromtimestamp(min(used_stamps), timezone.utc)).total_seconds()
            if used_stamps
            else None
        )
        effective_reason = reason
        if available and not freshness["fresh"] and effective_reason is None:
            effective_reason = "observations trop anciennes pour être considérées fraîches"
        starts = target_start or (timestamps_used[0] if timestamps_used else None)
        ends = timestamps_used[-1] if timestamps_used else None
        return {
            "available": bool(available),
            "window": {"start_at": starts, "end_at": ends, "target_start_at": target_start},
            "timestamps_used": timestamps_used,
            "observation_count": len(timestamps_used),
            "freshness": freshness,
            "reason": effective_reason,
        }

    for label, bars in horizon_bars.items():
        target = stamps[-1] - bars * seconds if stamps else None
        baseline = by_stamp.get(target)
        changes[label] = ((closes[-1] / baseline - 1) * 100) if baseline is not None else None
        target_iso = (
            datetime.fromtimestamp(target, timezone.utc).isoformat() if target is not None else None
        )
        used = (
            [target_iso, candles[-1].timestamp.isoformat()]
            if baseline is not None
            else [candles[-1].timestamp.isoformat()]
            if candles
            else []
        )
        metric_quality[f"returns_pct.{label}"] = quality_record(
            available=baseline is not None,
            timestamps_used=used,
            target_start=target_iso,
            reason=(
                None
                if baseline is not None
                else f"historique insuffisant: aucune donnée jusqu'à la cible {target_iso}"
                if target is not None and stamps and target < stamps[0]
                else "horodatage exact de référence absent; aucune clôture substituée"
            ),
        )
    returns = [
        float(closes[i] / closes[i - 1] - 1)
        for i in range(1, len(closes))
        if stamps[i] - stamps[i - 1] == seconds
    ]
    return_pairs = [
        (i - 1, i) for i in range(1, len(closes)) if stamps[i] - stamps[i - 1] == seconds
    ]
    volatility = None
    if len(returns) >= 2:
        mean_return = sum(returns) / len(returns)
        volatility = Decimal(
            str(math.sqrt(sum((r - mean_return) ** 2 for r in returns) / (len(returns) - 1)) * 100)
        )
    volatility_indices = sorted({index for pair in return_pairs for index in pair})
    volatility_timestamps = [candles[index].timestamp.isoformat() for index in volatility_indices]
    metric_quality["return_volatility_pct_per_interval"] = quality_record(
        available=volatility is not None,
        timestamps_used=volatility_timestamps,
        reason=None
        if volatility is not None
        else f"historique insuffisant: {len(returns)}/2 rendements contigus requis",
    )

    def contiguous_window(size):
        if len(closes) < size:
            return None
        window_stamps = stamps[-size:]
        if any(b - a != seconds for a, b in zip(window_stamps, window_stamps[1:])):
            return None
        return closes[-size:]

    fast_window = contiguous_window(20)
    slow_window = contiguous_window(50)
    sma_fast = sum(fast_window) / 20 if fast_window is not None else None
    sma_slow = sum(slow_window) / 50 if slow_window is not None else None
    volume_ratio = None
    if len(volumes) >= 21 and all(b - a == seconds for a, b in zip(stamps[-21:], stamps[-20:])):
        historical_avg = sum(volumes[-21:-1]) / 20
        if historical_avg > 0:
            volume_ratio = volumes[-1] / historical_avg

    def window_timestamps(size, window):
        if window is not None:
            return [c.timestamp.isoformat() for c in candles[-size:]]
        if len(candles) >= size:
            return [c.timestamp.isoformat() for c in candles[-size:]]
        return [c.timestamp.isoformat() for c in candles]

    metric_quality["sma20_close"] = quality_record(
        available=fast_window is not None,
        timestamps_used=window_timestamps(20, fast_window),
        reason=None if fast_window is not None else _window_reason(candles, 20, seconds),
    )
    metric_quality["sma50_close"] = quality_record(
        available=slow_window is not None,
        timestamps_used=window_timestamps(50, slow_window),
        reason=None if slow_window is not None else _window_reason(candles, 50, seconds),
    )
    metric_quality["close_vs_sma20_pct"] = quality_record(
        available=fast_window is not None and bool(candles),
        timestamps_used=window_timestamps(20, fast_window),
        reason=None
        if fast_window is not None and candles
        else _window_reason(candles, 20, seconds),
    )
    metric_quality["sma20_vs_sma50_pct"] = quality_record(
        available=fast_window is not None and slow_window is not None,
        timestamps_used=(
            window_timestamps(50, slow_window)
            if slow_window is not None
            else window_timestamps(20, fast_window)
        ),
        reason=None
        if fast_window is not None and slow_window is not None
        else "; ".join(
            f"{name}: {metric_quality[name]['reason']}"
            for name, window in (("sma20_close", fast_window), ("sma50_close", slow_window))
            if window is None
        ),
    )
    volume_timestamps = window_timestamps(21, volume_ratio is not None)
    metric_quality["latest_volume_vs_previous_20_mean"] = quality_record(
        available=volume_ratio is not None,
        timestamps_used=volume_timestamps,
        reason=(
            None
            if volume_ratio is not None
            else "moyenne des 20 volumes précédents nulle"
            if len(volumes) >= 21
            and all(b - a == seconds for a, b in zip(stamps[-21:], stamps[-20:]))
            else _window_reason(candles, 21, seconds)
        ),
    )
    metric_quality["latest_volume_base"] = quality_record(
        available=bool(volumes),
        timestamps_used=[candles[-1].timestamp.isoformat()] if candles else [],
        reason=None if volumes else "aucune clôture disponible",
    )
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
        "contiguous_interval_returns_used": len(returns),
        "sma20_window_contiguous": fast_window is not None,
        "sma50_window_contiguous": slow_window is not None,
    }
    sufficient = len(candles) >= min_history
    favorable, risks, reasons = [], [], []
    for horizon, value in changes.items():
        if value is None:
            reasons.append(
                f"variation {horizon} indisponible: "
                f"{metric_quality[f'returns_pct.{horizon}']['reason']}"
            )
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
        reasons.append(
            "tendance SMA20/SMA50 indisponible: "
            + "; ".join(
                f"{name}: {metric_quality[name]['reason']}"
                for name, value in (("sma20_close", sma_fast), ("sma50_close", sma_slow))
                if value is None
            )
        )
    if volume_ratio is None:
        reasons.append(
            "comparaison du volume indisponible: "
            + metric_quality["latest_volume_vs_previous_20_mean"]["reason"]
        )
    elif volume_ratio > Decimal("1.5"):
        favorable.append("volume de la dernière bougie > 1,5 fois la moyenne des 20 précédentes")
    elif volume_ratio < Decimal("0.5"):
        risks.append("volume de la dernière bougie < moitié de la moyenne des 20 précédentes")
    if volatility is None:
        reasons.append(
            "volatilité indisponible: "
            + metric_quality["return_volatility_pct_per_interval"]["reason"]
        )
    else:
        risks.append(
            f"volatilité des rendements par intervalle: {volatility.quantize(Decimal('0.01'))}%"
        )
    if not sufficient:
        reasons.append(f"historique insuffisant: {len(candles)} clôtures, {min_history} requises")
    available_metric_count = sum(
        status["available"] and status["freshness"]["fresh"] for status in metric_quality.values()
    )
    decision = (
        "analyse complète pour les métriques calculées"
        if available_metric_count == len(metric_quality)
        else "analyse partielle: disponibilité évaluée par métrique et fenêtre"
    )
    return {
        "metrics": measures,
        "metric_quality": metric_quality,
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


def _window_reason(candles, size, seconds):
    if len(candles) < size:
        return f"historique insuffisant: {len(candles)}/{size} clôtures requises"
    window = candles[-size:]
    for before, after in zip(window, window[1:]):
        if (after.timestamp - before.timestamp).total_seconds() != seconds:
            return (
                "lacune dans la fenêtre requise entre "
                f"{before.timestamp.isoformat()} et {after.timestamp.isoformat()}"
            )
    return "mesure indisponible"
