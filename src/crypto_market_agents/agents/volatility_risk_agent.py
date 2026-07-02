"""Volatility and market risk analysis agent."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any, Protocol

from crypto_market_agents.clients.coingecko_client import CoinGeckoError
from crypto_market_agents.schemas import (
    AgentReport,
    AgentStatus,
    Finding,
    ImpactDirection,
    RiskLevel,
    Source,
)


class MarketDataClient(Protocol):
    """Read-only market data client expected by VolatilityRiskAgent."""

    base_url: str

    def get_coin_markets(
        self,
        coin_ids: str | Sequence[str] | None = None,
        *,
        vs_currency: str = "usd",
        order: str = "market_cap_desc",
        per_page: int | None = None,
        page: int = 1,
        sparkline: bool = False,
        price_change_percentage: str | Sequence[str] | None = ("1h", "24h", "7d"),
    ) -> list[dict[str, Any]]:
        """Return read-only market data."""


@dataclass(frozen=True, slots=True)
class VolatilityRiskThresholds:
    """Simple thresholds for volatility and risk analysis."""

    notable_1h_change: float = 2.0
    high_1h_change: float = 5.0
    notable_24h_change: float = 5.0
    high_24h_change: float = 10.0
    extreme_24h_change: float = 25.0
    high_7d_change: float = 20.0
    extreme_7d_change: float = 60.0
    high_24h_amplitude: float = 8.0
    high_volume_to_market_cap: float = 0.15


class VolatilityRiskAgent:
    """Analyze market volatility and risk signals from CoinGecko data."""

    agent_name = "volatility_risk_agent"

    important_fields = (
        "current_price",
        "high_24h",
        "low_24h",
        "price_change_percentage_1h_in_currency",
        "price_change_percentage_24h",
        "price_change_percentage_7d_in_currency",
        "total_volume",
        "market_cap",
        "last_updated",
    )

    def __init__(
        self,
        client: MarketDataClient,
        *,
        thresholds: VolatilityRiskThresholds | None = None,
    ) -> None:
        self.client = client
        self.thresholds = thresholds or VolatilityRiskThresholds()

    def analyze(
        self,
        coin_ids: list[str] | Sequence[str],
        vs_currency: str = "usd",
    ) -> AgentReport:
        """Fetch market data and return a volatility-oriented report."""

        clean_coin_ids = _normalize_coin_ids(coin_ids)
        source = self._source()

        try:
            market_data = self.client.get_coin_markets(
                clean_coin_ids,
                vs_currency=vs_currency,
                price_change_percentage=("1h", "24h", "7d"),
            )
        except CoinGeckoError as exc:
            return self._failed_report(str(exc), source)

        if not market_data:
            return self._failed_report("CoinGecko returned no market data.", source)

        missing_fields = _missing_fields_by_asset(market_data, self.important_fields)
        returned_ids = {
            str(asset.get("id", "")).strip().lower()
            for asset in market_data
            if asset.get("id")
        }
        missing_coin_ids = [
            coin_id for coin_id in clean_coin_ids if coin_id not in returned_ids
        ]

        asset_metrics = [self._asset_metrics(asset) for asset in market_data]
        findings: list[Finding] = []
        for asset, metrics in zip(market_data, asset_metrics, strict=True):
            findings.extend(self._findings_for_asset(asset, metrics))

        if missing_fields:
            findings.extend(self._missing_data_findings(missing_fields))

        risk_level = self._global_risk_level(asset_metrics)
        confidence = self._confidence(
            market_data,
            requested_count=len(clean_coin_ids),
            missing_fields=missing_fields,
        )
        errors = _build_partial_errors(missing_coin_ids, missing_fields)
        status = AgentStatus.PARTIAL if errors else AgentStatus.SUCCESS

        return AgentReport(
            agent_name=self.agent_name,
            status=status,
            summary=self._summary(
                asset_count=len(market_data),
                finding_count=len(findings),
                risk_level=risk_level,
                status=status,
            ),
            risk_level=risk_level,
            confidence=confidence,
            findings=tuple(findings),
            sources=(source,),
            errors=tuple(errors),
            data={
                "vs_currency": vs_currency.lower(),
                "requested_coin_ids": list(clean_coin_ids),
                "missing_coin_ids": missing_coin_ids,
                "missing_fields": missing_fields,
                "asset_metrics": asset_metrics,
            },
        )

    def _asset_metrics(self, asset: dict[str, Any]) -> dict[str, Any]:
        current_price = _number(asset.get("current_price"))
        high_24h = _number(asset.get("high_24h"))
        low_24h = _number(asset.get("low_24h"))
        volume = _number(asset.get("total_volume"))
        market_cap = _number(asset.get("market_cap"))

        amplitude_24h_pct = None
        if (
            current_price is not None
            and high_24h is not None
            and low_24h is not None
            and current_price > 0
            and high_24h >= low_24h
        ):
            amplitude_24h_pct = ((high_24h - low_24h) / current_price) * 100

        volume_to_market_cap = None
        if volume is not None and market_cap is not None and market_cap > 0:
            volume_to_market_cap = volume / market_cap

        change_1h_abs = abs(
            _number(asset.get("price_change_percentage_1h_in_currency")) or 0
        )
        change_24h_abs = abs(_number(asset.get("price_change_percentage_24h")) or 0)
        change_7d_abs = abs(
            _number(asset.get("price_change_percentage_7d_in_currency")) or 0
        )

        return {
            "id": asset.get("id"),
            "symbol": _symbol(asset),
            "name": asset.get("name"),
            "current_price": current_price,
            "amplitude_24h_pct": amplitude_24h_pct,
            "change_1h_abs": change_1h_abs,
            "change_24h_abs": change_24h_abs,
            "change_7d_abs": change_7d_abs,
            "volume_to_market_cap": volume_to_market_cap,
            "risk_score": self._asset_risk_score(
                amplitude_24h_pct=amplitude_24h_pct,
                change_1h_abs=change_1h_abs,
                change_24h_abs=change_24h_abs,
                change_7d_abs=change_7d_abs,
                volume_to_market_cap=volume_to_market_cap,
            ),
        }

    def _findings_for_asset(
        self,
        asset: dict[str, Any],
        metrics: dict[str, Any],
    ) -> list[Finding]:
        findings: list[Finding] = []
        symbol = _symbol(asset)
        name = _asset_label(asset)

        amplitude = _number(metrics.get("amplitude_24h_pct"))
        if amplitude is not None and amplitude >= self.thresholds.high_24h_amplitude:
            findings.append(
                Finding(
                    title="Volatilite 24h elevee",
                    description=f"{name} affiche une amplitude 24h de {_fmt(amplitude)}%.",
                    impact=ImpactDirection.MIXED,
                    symbols=(symbol,),
                    confidence_score=0.78,
                    data={"metric": "amplitude_24h_pct", "value": amplitude},
                )
            )

        change_1h = _number(metrics.get("change_1h_abs")) or 0
        if change_1h >= self.thresholds.high_1h_change:
            findings.append(
                Finding(
                    title="Mouvement brutal 1h",
                    description=f"{name} varie de {_fmt(change_1h)}% en valeur absolue sur 1h.",
                    impact=ImpactDirection.MIXED,
                    symbols=(symbol,),
                    confidence_score=0.78,
                    data={"metric": "change_1h_abs", "value": change_1h},
                )
            )
        elif change_1h >= self.thresholds.notable_1h_change:
            findings.append(
                Finding(
                    title="Mouvement notable 1h",
                    description=f"{name} varie de {_fmt(change_1h)}% en valeur absolue sur 1h.",
                    impact=ImpactDirection.MIXED,
                    symbols=(symbol,),
                    confidence_score=0.66,
                    data={"metric": "change_1h_abs", "value": change_1h},
                )
            )

        change_24h = _number(metrics.get("change_24h_abs")) or 0
        if change_24h >= self.thresholds.high_24h_change:
            findings.append(
                Finding(
                    title="Forte variation 24h",
                    description=f"{name} varie de {_fmt(change_24h)}% en valeur absolue sur 24h.",
                    impact=ImpactDirection.MIXED,
                    symbols=(symbol,),
                    confidence_score=0.77,
                    data={"metric": "change_24h_abs", "value": change_24h},
                )
            )
        elif change_24h >= self.thresholds.notable_24h_change:
            findings.append(
                Finding(
                    title="Variation notable 24h",
                    description=f"{name} varie de {_fmt(change_24h)}% en valeur absolue sur 24h.",
                    impact=ImpactDirection.MIXED,
                    symbols=(symbol,),
                    confidence_score=0.65,
                    data={"metric": "change_24h_abs", "value": change_24h},
                )
            )

        change_7d = _number(metrics.get("change_7d_abs")) or 0
        if change_7d >= self.thresholds.high_7d_change:
            title = (
                "Variation extreme 7j"
                if change_7d >= self.thresholds.extreme_7d_change
                else "Forte variation 7j"
            )
            findings.append(
                Finding(
                    title=title,
                    description=f"{name} varie de {_fmt(change_7d)}% en valeur absolue sur 7j.",
                    impact=ImpactDirection.MIXED,
                    symbols=(symbol,),
                    confidence_score=0.76,
                    data={"metric": "change_7d_abs", "value": change_7d},
                )
            )

        volume_ratio = _number(metrics.get("volume_to_market_cap"))
        if (
            volume_ratio is not None
            and volume_ratio >= self.thresholds.high_volume_to_market_cap
        ):
            findings.append(
                Finding(
                    title="Volume anormalement eleve",
                    description=(
                        f"{name} affiche un ratio volume/capitalisation "
                        f"de {_fmt(volume_ratio * 100)}%."
                    ),
                    impact=ImpactDirection.MIXED,
                    symbols=(symbol,),
                    confidence_score=0.70,
                    data={"metric": "volume_to_market_cap", "value": volume_ratio},
                )
            )

        return findings

    def _missing_data_findings(self, missing_fields: dict[str, list[str]]) -> list[Finding]:
        findings: list[Finding] = []
        for asset_id, fields in missing_fields.items():
            findings.append(
                Finding(
                    title="Donnees insuffisantes pour evaluer le risque",
                    description=(
                        f"{asset_id} manque de donnees importantes: "
                        f"{', '.join(fields)}."
                    ),
                    impact=ImpactDirection.UNKNOWN,
                    symbols=(asset_id,),
                    confidence_score=0.40,
                    data={"missing_fields": fields},
                )
            )

        return findings

    def _asset_risk_score(
        self,
        *,
        amplitude_24h_pct: float | None,
        change_1h_abs: float,
        change_24h_abs: float,
        change_7d_abs: float,
        volume_to_market_cap: float | None,
    ) -> int:
        score = 0

        if change_1h_abs >= self.thresholds.high_1h_change:
            score += 2
        elif change_1h_abs >= self.thresholds.notable_1h_change:
            score += 1

        if change_24h_abs >= self.thresholds.extreme_24h_change:
            score += 6
        elif change_24h_abs >= self.thresholds.high_24h_change:
            score += 2
        elif change_24h_abs >= self.thresholds.notable_24h_change:
            score += 1

        if change_7d_abs >= self.thresholds.extreme_7d_change:
            score += 6
        elif change_7d_abs >= self.thresholds.high_7d_change:
            score += 2

        if (
            amplitude_24h_pct is not None
            and amplitude_24h_pct >= self.thresholds.high_24h_amplitude
        ):
            score += 2

        if (
            volume_to_market_cap is not None
            and volume_to_market_cap >= self.thresholds.high_volume_to_market_cap
        ):
            score += 1

        return score

    def _global_risk_level(self, asset_metrics: list[dict[str, Any]]) -> RiskLevel:
        scores = [int(metrics.get("risk_score") or 0) for metrics in asset_metrics]
        if not scores:
            return RiskLevel.MEDIUM

        critical_count = sum(score >= 6 for score in scores)
        high_count = sum(3 <= score < 6 for score in scores)
        medium_count = sum(1 <= score < 3 for score in scores)

        if critical_count or sum(scores) >= 10:
            return RiskLevel.CRITICAL
        if high_count or max(scores) >= 4:
            return RiskLevel.HIGH
        if high_count or medium_count:
            return RiskLevel.MEDIUM

        return RiskLevel.LOW

    def _confidence(
        self,
        market_data: list[dict[str, Any]],
        *,
        requested_count: int,
        missing_fields: dict[str, list[str]],
    ) -> float:
        if not market_data or requested_count <= 0:
            return 0.0

        expected_total = len(market_data) * len(self.important_fields)
        missing_total = sum(len(fields) for fields in missing_fields.values())
        completeness = (expected_total - missing_total) / expected_total
        coverage = min(len(market_data) / requested_count, 1)
        confidence = 0.2 + (0.75 * completeness * coverage)

        return round(max(0.05, min(0.95, confidence)), 2)

    def _summary(
        self,
        *,
        asset_count: int,
        finding_count: int,
        risk_level: RiskLevel,
        status: AgentStatus,
    ) -> str:
        prefix = "Analyse partielle" if status is AgentStatus.PARTIAL else "Analyse complete"
        return (
            f"{prefix} Volatilite & Risque pour {asset_count} actif(s). "
            f"Risque global {risk_level.value}. "
            f"{finding_count} signal(aux) de risque detecte(s)."
        )

    def _source(self) -> Source:
        base_url = getattr(self.client, "base_url", "https://api.coingecko.com/api/v3")
        return Source(
            name="CoinGecko coins markets",
            provider="coingecko",
            url=f"{str(base_url).rstrip('/')}/coins/markets",
        )

    def _failed_report(self, error: str, source: Source) -> AgentReport:
        return AgentReport(
            agent_name=self.agent_name,
            status=AgentStatus.FAILED,
            summary="Analyse Volatilite & Risque echouee.",
            risk_level=RiskLevel.MEDIUM,
            confidence=0.0,
            findings=(),
            sources=(source,),
            errors=(error,),
            data={"asset_metrics": []},
        )


def _normalize_coin_ids(coin_ids: Sequence[str]) -> tuple[str, ...]:
    if isinstance(coin_ids, str) or not isinstance(coin_ids, Sequence):
        raise ValueError("coin_ids must be a sequence of CoinGecko IDs.")

    normalized: list[str] = []
    seen: set[str] = set()
    for coin_id in coin_ids:
        cleaned = str(coin_id).strip().lower()
        if cleaned and cleaned not in seen:
            seen.add(cleaned)
            normalized.append(cleaned)

    if not normalized:
        raise ValueError("coin_ids must contain at least one CoinGecko ID.")

    return tuple(normalized)


def _missing_fields_by_asset(
    market_data: list[dict[str, Any]],
    important_fields: Sequence[str],
) -> dict[str, list[str]]:
    missing: dict[str, list[str]] = {}
    for asset in market_data:
        asset_id = str(asset.get("id") or asset.get("symbol") or "unknown")
        missing_fields = [
            field_name
            for field_name in important_fields
            if asset.get(field_name) is None
        ]
        if missing_fields:
            missing[asset_id] = missing_fields

    return missing


def _build_partial_errors(
    missing_coin_ids: Sequence[str],
    missing_fields: dict[str, list[str]],
) -> list[str]:
    errors: list[str] = []
    if missing_coin_ids:
        errors.append("Missing market data for: " + ", ".join(missing_coin_ids))

    for asset_id, fields in missing_fields.items():
        errors.append(f"Missing fields for {asset_id}: {', '.join(fields)}")

    return errors


def _asset_label(asset: dict[str, Any]) -> str:
    name = str(asset.get("name") or "").strip()
    symbol = _symbol(asset).upper()
    return f"{name} ({symbol})" if name else symbol


def _symbol(asset: dict[str, Any]) -> str:
    symbol = str(asset.get("symbol") or asset.get("id") or "unknown").strip().lower()
    return symbol or "unknown"


def _number(value: Any) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, int | float):
        return float(value)

    try:
        return float(str(value).strip())
    except ValueError:
        return None


def _fmt(value: float) -> str:
    return f"{value:.2f}".rstrip("0").rstrip(".")
