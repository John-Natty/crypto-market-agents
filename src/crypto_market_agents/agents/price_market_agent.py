"""Price and market analysis agent."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any, Protocol

from crypto_market_agents.clients.coingecko_client import CoinGeckoError
from crypto_market_agents.schemas import (
    AgentFinding,
    AgentReport,
    AgentStatus,
    ImpactDirection,
    RiskLevel,
    SourceReference,
)


class MarketDataClient(Protocol):
    """Read-only market data client expected by PriceMarketAgent."""

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
class PriceMarketThresholds:
    """Simple thresholds used by the price market agent."""

    strong_24h_change: float = 8.0
    moderate_24h_change: float = 4.0
    extreme_24h_change: float = 25.0
    important_7d_change: float = 15.0
    moderate_7d_change: float = 10.0
    extreme_7d_change: float = 60.0
    high_volume_to_market_cap: float = 0.10
    top_market_cap_rank: int = 10
    near_high_low_ratio: float = 0.02


class PriceMarketAgent:
    """Analyze prices, market cap, volume, and basic market movement."""

    agent_name = "price_market_agent"

    important_fields = (
        "current_price",
        "market_cap",
        "market_cap_rank",
        "total_volume",
        "price_change_percentage_1h_in_currency",
        "price_change_percentage_24h",
        "price_change_percentage_7d_in_currency",
        "high_24h",
        "low_24h",
        "last_updated",
    )

    def __init__(
        self,
        client: MarketDataClient,
        *,
        thresholds: PriceMarketThresholds | None = None,
    ) -> None:
        self.client = client
        self.thresholds = thresholds or PriceMarketThresholds()

    def analyze(
        self,
        coin_ids: list[str] | Sequence[str],
        vs_currency: str = "usd",
    ) -> AgentReport:
        """Fetch and analyze market data for the requested CoinGecko coin IDs."""

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

        analyzed_assets = [_clean_asset(asset) for asset in market_data]
        returned_ids = {
            str(asset.get("id", "")).strip().lower()
            for asset in market_data
            if asset.get("id")
        }
        missing_coin_ids = [
            coin_id for coin_id in clean_coin_ids if coin_id not in returned_ids
        ]
        missing_fields = _missing_fields_by_asset(
            market_data,
            self.important_fields,
        )

        findings: list[AgentFinding] = []
        for asset in market_data:
            findings.extend(self._findings_for_asset(asset))

        risk_level = self._global_risk_level(market_data)
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
                "assets": analyzed_assets,
            },
        )

    def _findings_for_asset(self, asset: dict[str, Any]) -> list[AgentFinding]:
        findings: list[AgentFinding] = []
        symbol = _symbol(asset)
        name = _asset_label(asset)
        change_24h = _number(asset.get("price_change_percentage_24h"))
        change_7d = _number(asset.get("price_change_percentage_7d_in_currency"))

        if change_24h is not None and change_24h >= self.thresholds.strong_24h_change:
            findings.append(
                AgentFinding(
                    title="Forte hausse 24h",
                    description=f"{name} progresse de {_fmt(change_24h)}% sur 24h.",
                    impact=ImpactDirection.BULLISH,
                    symbols=(symbol,),
                    confidence_score=0.74,
                    data={"metric": "price_change_percentage_24h", "value": change_24h},
                )
            )

        if change_24h is not None and change_24h <= -self.thresholds.strong_24h_change:
            findings.append(
                AgentFinding(
                    title="Forte baisse 24h",
                    description=f"{name} recule de {_fmt(abs(change_24h))}% sur 24h.",
                    impact=ImpactDirection.BEARISH,
                    symbols=(symbol,),
                    confidence_score=0.74,
                    data={"metric": "price_change_percentage_24h", "value": change_24h},
                )
            )

        volume_finding = self._volume_finding(asset, symbol, name)
        if volume_finding is not None:
            findings.append(volume_finding)

        rank = _number(asset.get("market_cap_rank"))
        if rank is not None and rank <= self.thresholds.top_market_cap_rank:
            findings.append(
                AgentFinding(
                    title="Actif tres bien classe par market cap",
                    description=f"{name} est classe #{int(rank)} par capitalisation.",
                    impact=ImpactDirection.NEUTRAL,
                    symbols=(symbol,),
                    confidence_score=0.78,
                    data={"metric": "market_cap_rank", "value": rank},
                )
            )

        if (
            change_7d is not None
            and abs(change_7d) >= self.thresholds.important_7d_change
        ):
            impact = (
                ImpactDirection.BULLISH
                if change_7d > 0
                else ImpactDirection.BEARISH
            )
            direction = "progresse" if change_7d > 0 else "recule"
            findings.append(
                AgentFinding(
                    title="Variation importante 7 jours",
                    description=f"{name} {direction} de {_fmt(abs(change_7d))}% sur 7 jours.",
                    impact=impact,
                    symbols=(symbol,),
                    confidence_score=0.70,
                    data={
                        "metric": "price_change_percentage_7d_in_currency",
                        "value": change_7d,
                    },
                )
            )

        price_position_finding = self._price_position_finding(asset, symbol, name)
        if price_position_finding is not None:
            findings.append(price_position_finding)

        return findings

    def _volume_finding(
        self,
        asset: dict[str, Any],
        symbol: str,
        name: str,
    ) -> AgentFinding | None:
        volume = _number(asset.get("total_volume"))
        market_cap = _number(asset.get("market_cap"))
        if volume is None or market_cap is None or market_cap <= 0:
            return None

        ratio = volume / market_cap
        if ratio < self.thresholds.high_volume_to_market_cap:
            return None

        return AgentFinding(
            title="Volume eleve",
            description=(
                f"{name} affiche un volume 24h eleve "
                f"({_fmt(ratio * 100)}% de sa capitalisation)."
            ),
            impact=ImpactDirection.MIXED,
            symbols=(symbol,),
            confidence_score=0.68,
            data={
                "metric": "volume_to_market_cap",
                "value": ratio,
                "total_volume": volume,
                "market_cap": market_cap,
            },
        )

    def _price_position_finding(
        self,
        asset: dict[str, Any],
        symbol: str,
        name: str,
    ) -> AgentFinding | None:
        current_price = _number(asset.get("current_price"))
        high_24h = _number(asset.get("high_24h"))
        low_24h = _number(asset.get("low_24h"))
        if (
            current_price is None
            or high_24h is None
            or low_24h is None
            or current_price <= 0
            or high_24h <= 0
            or low_24h <= 0
            or high_24h <= low_24h
        ):
            return None

        near_high = current_price >= high_24h * (1 - self.thresholds.near_high_low_ratio)
        near_low = current_price <= low_24h * (1 + self.thresholds.near_high_low_ratio)

        if near_high:
            return AgentFinding(
                title="Prix proche du high 24h",
                description=f"{name} evolue proche de son plus haut 24h.",
                impact=ImpactDirection.MIXED,
                symbols=(symbol,),
                confidence_score=0.64,
                data={
                    "current_price": current_price,
                    "high_24h": high_24h,
                    "distance_to_high_pct": (
                        (high_24h - current_price) / high_24h
                    )
                    * 100,
                },
            )

        if near_low:
            return AgentFinding(
                title="Prix proche du low 24h",
                description=f"{name} evolue proche de son plus bas 24h.",
                impact=ImpactDirection.MIXED,
                symbols=(symbol,),
                confidence_score=0.64,
                data={
                    "current_price": current_price,
                    "low_24h": low_24h,
                    "distance_to_low_pct": (
                        (current_price - low_24h) / low_24h
                    )
                    * 100,
                },
            )

        return None

    def _global_risk_level(self, market_data: list[dict[str, Any]]) -> RiskLevel:
        asset_levels = [self._asset_risk_level(asset) for asset in market_data]
        total = len(asset_levels)
        critical_count = asset_levels.count(RiskLevel.CRITICAL)
        high_count = asset_levels.count(RiskLevel.HIGH)
        medium_count = asset_levels.count(RiskLevel.MEDIUM)

        if critical_count:
            return RiskLevel.CRITICAL
        if high_count >= 2 or (total >= 3 and high_count / total >= 0.5):
            return RiskLevel.HIGH
        if high_count or medium_count:
            return RiskLevel.MEDIUM

        return RiskLevel.LOW

    def _asset_risk_level(self, asset: dict[str, Any]) -> RiskLevel:
        change_24h = abs(_number(asset.get("price_change_percentage_24h")) or 0)
        change_7d = abs(
            _number(asset.get("price_change_percentage_7d_in_currency")) or 0
        )

        if (
            change_24h >= self.thresholds.extreme_24h_change
            or change_7d >= self.thresholds.extreme_7d_change
        ):
            return RiskLevel.CRITICAL
        if (
            change_24h >= self.thresholds.strong_24h_change
            or change_7d >= self.thresholds.important_7d_change
        ):
            return RiskLevel.HIGH
        if (
            change_24h >= self.thresholds.moderate_24h_change
            or change_7d >= self.thresholds.moderate_7d_change
        ):
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
        if status is AgentStatus.PARTIAL:
            prefix = "Analyse partielle"
        else:
            prefix = "Analyse complete"

        return (
            f"{prefix} Prix & Marche pour {asset_count} actif(s). "
            f"Risque global {risk_level.value}. "
            f"{finding_count} signal(aux) de marche detecte(s), sans conseil financier."
        )

    def _source(self) -> SourceReference:
        base_url = getattr(self.client, "base_url", "https://api.coingecko.com/api/v3")
        return SourceReference(
            name="CoinGecko coins markets",
            provider="coingecko",
            url=f"{str(base_url).rstrip('/')}/coins/markets",
        )

    def _failed_report(self, error: str, source: SourceReference) -> AgentReport:
        return AgentReport(
            agent_name=self.agent_name,
            status=AgentStatus.FAILED,
            summary=(
                "Analyse Prix & Marche echouee. Aucune decision financiere "
                "ne doit etre prise sur cette base."
            ),
            risk_level=RiskLevel.MEDIUM,
            confidence=0.0,
            findings=(),
            sources=(source,),
            errors=(error,),
            data={"assets": []},
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


def _clean_asset(asset: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": asset.get("id"),
        "symbol": asset.get("symbol"),
        "name": asset.get("name"),
        "current_price": asset.get("current_price"),
        "market_cap": asset.get("market_cap"),
        "market_cap_rank": asset.get("market_cap_rank"),
        "total_volume": asset.get("total_volume"),
        "price_change_percentage_1h_in_currency": asset.get(
            "price_change_percentage_1h_in_currency"
        ),
        "price_change_percentage_24h": asset.get("price_change_percentage_24h"),
        "price_change_percentage_7d_in_currency": asset.get(
            "price_change_percentage_7d_in_currency"
        ),
        "high_24h": asset.get("high_24h"),
        "low_24h": asset.get("low_24h"),
        "last_updated": asset.get("last_updated"),
    }


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
