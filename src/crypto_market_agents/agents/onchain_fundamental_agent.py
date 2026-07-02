"""On-chain and fundamental analysis agent."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any, Protocol

from crypto_market_agents.clients.defillama_client import (
    DefiLlamaAPIError,
    DefiLlamaError,
)
from crypto_market_agents.schemas import (
    AgentReport,
    AgentStatus,
    Finding,
    ImpactDirection,
    RiskLevel,
    Source,
)


DEFAULT_PROTOCOL_SLUGS = ("uniswap", "aave", "lido", "curve-dex")


class FundamentalDataClient(Protocol):
    """Read-only DefiLlama client expected by OnchainFundamentalAgent."""

    base_url: str

    def get_protocol(self, protocol_slug: str) -> dict[str, Any]:
        """Return protocol details."""

    def get_current_tvl(self, protocol_slug: str) -> float | int | None:
        """Return current TVL for one protocol."""

    def get_chains(self) -> list[dict[str, Any]]:
        """Return chain TVL data."""

    def get_stablecoins(self) -> dict[str, Any]:
        """Return stablecoin data."""

    def get_fees_overview(self) -> dict[str, Any]:
        """Return fees and revenue overview."""


@dataclass(frozen=True, slots=True)
class FundamentalThresholds:
    """Simple thresholds for fundamental analysis."""

    high_tvl_usd: float = 1_000_000_000
    medium_tvl_usd: float = 100_000_000
    low_tvl_usd: float = 50_000_000
    notable_tvl_drop_pct: float = -10.0
    strong_tvl_drop_pct: float = -25.0
    significant_chain_count: int = 3


class OnchainFundamentalAgent:
    """Analyze simple DeFi fundamentals from DefiLlama Free API data."""

    agent_name = "onchain_fundamental_agent"

    def __init__(
        self,
        client: FundamentalDataClient,
        *,
        thresholds: FundamentalThresholds | None = None,
    ) -> None:
        self.client = client
        self.thresholds = thresholds or FundamentalThresholds()

    def analyze(
        self,
        protocol_slugs: list[str] | Sequence[str] | None = None,
        chains: list[str] | Sequence[str] | None = None,
    ) -> AgentReport:
        """Fetch and analyze simple on-chain/fundamental data."""

        selected_protocols = _normalize_items(protocol_slugs) or DEFAULT_PROTOCOL_SLUGS
        selected_chains = _normalize_items(chains)
        sources = self._sources(include_chains=bool(selected_chains))

        analyses: list[dict[str, Any]] = []
        findings: list[Finding] = []
        errors: list[str] = []

        for slug in selected_protocols:
            try:
                protocol = self.client.get_protocol(slug)
                current_tvl = self.client.get_current_tvl(slug)
            except DefiLlamaAPIError as exc:
                if exc.status_code == 404:
                    errors.append(f"Protocol not found: {slug}")
                    findings.append(self._not_found_finding(slug))
                    analyses.append({"slug": slug, "found": False})
                    continue
                return self._failed_report(str(exc), sources)
            except DefiLlamaError as exc:
                return self._failed_report(str(exc), sources)

            analysis = self._analyze_protocol(slug, protocol, current_tvl)
            analyses.append(analysis)
            findings.extend(self._findings_for_protocol(analysis))
            if analysis["missing_fields"]:
                errors.append(
                    f"Missing fields for {slug}: "
                    f"{', '.join(analysis['missing_fields'])}"
                )

        fees_overview = self._optional_call("fees overview", self.client.get_fees_overview, errors)
        stablecoins = self._optional_call("stablecoins", self.client.get_stablecoins, errors)
        chain_data = (
            self._selected_chain_data(selected_chains, errors) if selected_chains else []
        )

        if fees_overview:
            fee_findings = self._fees_findings(analyses, fees_overview)
            findings.extend(fee_findings)

        risk_level = self._risk_level(analyses)
        confidence = self._confidence(analyses, requested_count=len(selected_protocols))
        status = AgentStatus.PARTIAL if errors else AgentStatus.SUCCESS

        return AgentReport(
            agent_name=self.agent_name,
            status=status,
            summary=self._summary(
                protocol_count=len(selected_protocols),
                found_count=sum(1 for analysis in analyses if analysis.get("found")),
                finding_count=len(findings),
                risk_level=risk_level,
                status=status,
            ),
            risk_level=risk_level,
            confidence=confidence,
            findings=tuple(findings),
            sources=sources,
            errors=tuple(errors),
            data={
                "protocols": analyses,
                "chains": chain_data,
                "stablecoins": _stablecoin_summary(stablecoins),
            },
        )

    def _analyze_protocol(
        self,
        slug: str,
        protocol: dict[str, Any],
        current_tvl: float | int | None,
    ) -> dict[str, Any]:
        chains = _string_tuple(protocol.get("chains"))
        tvl_change_pct = _tvl_change_pct(protocol.get("tvl"))
        category = _optional_text(protocol.get("category"))
        fees_slug = _optional_text(protocol.get("slug")) or slug
        tvl = _number(current_tvl)

        missing_fields = []
        if tvl is None:
            missing_fields.append("current_tvl")
        if category is None:
            missing_fields.append("category")
        if not chains:
            missing_fields.append("chains")

        return {
            "slug": slug,
            "fees_slug": fees_slug,
            "name": _optional_text(protocol.get("name")) or slug,
            "found": True,
            "current_tvl": tvl,
            "tvl_change_pct": tvl_change_pct,
            "category": category,
            "chains": list(chains),
            "missing_fields": missing_fields,
        }

    def _findings_for_protocol(self, analysis: dict[str, Any]) -> list[Finding]:
        findings: list[Finding] = []
        slug = str(analysis["slug"])
        name = str(analysis["name"])
        tvl = _number(analysis.get("current_tvl"))
        chains = analysis.get("chains") or []
        tvl_change_pct = _number(analysis.get("tvl_change_pct"))

        if tvl is not None and tvl >= self.thresholds.high_tvl_usd:
            findings.append(
                Finding(
                    title="TVL elevee",
                    description=f"{name} affiche une TVL d'environ {_usd(tvl)}.",
                    impact=ImpactDirection.NEUTRAL,
                    symbols=(slug,),
                    confidence_score=0.80,
                    data={"current_tvl": tvl},
                )
            )
        elif tvl is not None and tvl < self.thresholds.low_tvl_usd:
            findings.append(
                Finding(
                    title="TVL faible",
                    description=(
                        f"{name} affiche une TVL inferieure a "
                        f"{_usd(self.thresholds.low_tvl_usd)}."
                    ),
                    impact=ImpactDirection.BEARISH,
                    symbols=(slug,),
                    confidence_score=0.74,
                    data={"current_tvl": tvl},
                )
            )

        if tvl_change_pct is not None and tvl_change_pct <= self.thresholds.strong_tvl_drop_pct:
            findings.append(
                Finding(
                    title="Baisse importante de TVL",
                    description=(
                        f"{name} recule de {_fmt(abs(tvl_change_pct))}% "
                        "sur la periode disponible."
                    ),
                    impact=ImpactDirection.BEARISH,
                    symbols=(slug,),
                    confidence_score=0.76,
                    data={"tvl_change_pct": tvl_change_pct},
                )
            )
        elif tvl_change_pct is not None and tvl_change_pct <= self.thresholds.notable_tvl_drop_pct:
            findings.append(
                Finding(
                    title="Baisse notable de TVL",
                    description=(
                        f"{name} recule de {_fmt(abs(tvl_change_pct))}% "
                        "sur la periode disponible."
                    ),
                    impact=ImpactDirection.BEARISH,
                    symbols=(slug,),
                    confidence_score=0.66,
                    data={"tvl_change_pct": tvl_change_pct},
                )
            )

        if len(chains) >= self.thresholds.significant_chain_count:
            findings.append(
                Finding(
                    title="Protocole present sur plusieurs chaines",
                    description=f"{name} est deploye sur {len(chains)} chaines.",
                    impact=ImpactDirection.NEUTRAL,
                    symbols=(slug,),
                    confidence_score=0.70,
                    data={"chains": chains},
                )
            )

        if analysis.get("missing_fields"):
            findings.append(
                Finding(
                    title="Donnees insuffisantes",
                    description=f"{name} manque de donnees fondamentales importantes.",
                    impact=ImpactDirection.UNKNOWN,
                    symbols=(slug,),
                    confidence_score=0.42,
                    data={"missing_fields": analysis["missing_fields"]},
                )
            )

        return findings

    def _fees_findings(
        self,
        analyses: list[dict[str, Any]],
        fees_overview: dict[str, Any],
    ) -> list[Finding]:
        findings: list[Finding] = []
        fee_items = fees_overview.get("protocols")
        if not isinstance(fee_items, list):
            return findings

        for analysis in analyses:
            if not analysis.get("found"):
                continue
            item = _find_fee_item(analysis, fee_items)
            fee_value = _fee_value(item) if item else None
            if fee_value is None or fee_value <= 0:
                continue

            slug = str(analysis["slug"])
            findings.append(
                Finding(
                    title="Activite fees/revenue significative",
                    description=f"{analysis['name']} affiche une activite fees/revenue positive.",
                    impact=ImpactDirection.NEUTRAL,
                    symbols=(slug,),
                    confidence_score=0.68,
                    data={"fees_or_revenue": fee_value},
                )
            )

        return findings

    def _selected_chain_data(
        self,
        selected_chains: tuple[str, ...],
        errors: list[str],
    ) -> list[dict[str, Any]]:
        chain_payload = self._optional_call("chains", self.client.get_chains, errors)
        if not chain_payload:
            return []

        selected = {chain.lower() for chain in selected_chains}
        results = []
        for item in chain_payload:
            name = str(item.get("name") or item.get("chain") or "").lower()
            if name in selected:
                results.append(
                    {
                        "name": item.get("name") or item.get("chain"),
                        "tvl": item.get("tvl"),
                    }
                )

        missing = selected - {
            str(item.get("name") or "").strip().lower() for item in results
        }
        if missing:
            errors.append("Missing chain data for: " + ", ".join(sorted(missing)))

        return results

    def _optional_call(self, label: str, callback, errors: list[str]) -> Any:
        try:
            return callback()
        except DefiLlamaError as exc:
            errors.append(f"Optional DefiLlama {label} unavailable: {exc}")
            return None

    def _not_found_finding(self, slug: str) -> Finding:
        return Finding(
            title="Protocole introuvable",
            description=f"DefiLlama ne retourne pas de donnees pour {slug}.",
            impact=ImpactDirection.UNKNOWN,
            symbols=(slug,),
            confidence_score=0.35,
            data={"slug": slug},
        )

    def _risk_level(self, analyses: list[dict[str, Any]]) -> RiskLevel:
        found = [analysis for analysis in analyses if analysis.get("found")]
        if not found:
            return RiskLevel.CRITICAL

        not_found_count = len(analyses) - len(found)
        low_tvl_count = sum(
            (_number(analysis.get("current_tvl")) or 0) < self.thresholds.low_tvl_usd
            for analysis in found
            if analysis.get("current_tvl") is not None
        )
        strong_drop_count = sum(
            (_number(analysis.get("tvl_change_pct")) or 0)
            <= self.thresholds.strong_tvl_drop_pct
            for analysis in found
            if analysis.get("tvl_change_pct") is not None
        )
        missing_count = sum(bool(analysis.get("missing_fields")) for analysis in found)

        if strong_drop_count >= 2 or missing_count == len(found):
            return RiskLevel.CRITICAL
        if low_tvl_count or strong_drop_count or not_found_count:
            return RiskLevel.HIGH
        if missing_count:
            return RiskLevel.MEDIUM

        return RiskLevel.LOW

    def _confidence(self, analyses: list[dict[str, Any]], *, requested_count: int) -> float:
        if not analyses or requested_count <= 0:
            return 0.0

        found = [analysis for analysis in analyses if analysis.get("found")]
        coverage = len(found) / requested_count
        if not found:
            return 0.10

        required_fields = ("current_tvl", "category", "chains")
        present_total = 0
        expected_total = len(found) * len(required_fields)
        for analysis in found:
            present_total += 1 if analysis.get("current_tvl") is not None else 0
            present_total += 1 if analysis.get("category") else 0
            present_total += 1 if analysis.get("chains") else 0

        completeness = present_total / expected_total if expected_total else 0
        confidence = 0.15 + (0.55 * coverage) + (0.25 * completeness)

        return round(max(0.05, min(0.95, confidence)), 2)

    def _summary(
        self,
        *,
        protocol_count: int,
        found_count: int,
        finding_count: int,
        risk_level: RiskLevel,
        status: AgentStatus,
    ) -> str:
        prefix = "Analyse partielle" if status is AgentStatus.PARTIAL else "Analyse complete"
        return (
            f"{prefix} On-chain/Fondamental pour {found_count}/{protocol_count} "
            f"protocole(s). Risque global {risk_level.value}. "
            f"{finding_count} signal(aux) fondamental(aux) detecte(s)."
        )

    def _sources(self, *, include_chains: bool) -> tuple[Source, ...]:
        base_url = getattr(self.client, "base_url", "https://api.llama.fi")
        sources = [
            Source(
                name="DefiLlama protocol",
                provider="defillama",
                url=f"{str(base_url).rstrip('/')}/protocol/{{protocol}}",
            ),
            Source(
                name="DefiLlama current TVL",
                provider="defillama",
                url=f"{str(base_url).rstrip('/')}/tvl/{{protocol}}",
            ),
            Source(
                name="DefiLlama stablecoins",
                provider="defillama",
                url=f"{str(base_url).rstrip('/')}/stablecoins",
            ),
            Source(
                name="DefiLlama fees overview",
                provider="defillama",
                url=f"{str(base_url).rstrip('/')}/overview/fees",
            ),
        ]
        if include_chains:
            sources.append(
                Source(
                    name="DefiLlama chains",
                    provider="defillama",
                    url=f"{str(base_url).rstrip('/')}/v2/chains",
                )
            )

        return tuple(sources)

    def _failed_report(self, error: str, sources: tuple[Source, ...]) -> AgentReport:
        return AgentReport(
            agent_name=self.agent_name,
            status=AgentStatus.FAILED,
            summary="Analyse On-chain/Fondamental echouee.",
            risk_level=RiskLevel.CRITICAL,
            confidence=0.0,
            findings=(),
            sources=sources,
            errors=(error,),
            data={"protocols": [], "chains": [], "stablecoins": {}},
        )


def _normalize_items(values: list[str] | Sequence[str] | None) -> tuple[str, ...]:
    if not values:
        return ()

    normalized: list[str] = []
    seen: set[str] = set()
    for value in values:
        cleaned = str(value).strip().lower()
        if cleaned and cleaned not in seen:
            seen.add(cleaned)
            normalized.append(cleaned)

    return tuple(normalized)


def _string_tuple(value: Any) -> tuple[str, ...]:
    if value is None:
        return ()
    if isinstance(value, str):
        return (value.strip(),) if value.strip() else ()
    if not isinstance(value, Sequence):
        return ()

    return tuple(str(item).strip() for item in value if str(item).strip())


def _tvl_change_pct(tvl_history: Any) -> float | None:
    if not isinstance(tvl_history, Sequence) or len(tvl_history) < 2:
        return None

    first = _history_tvl_value(tvl_history[0])
    last = _history_tvl_value(tvl_history[-1])
    if first is None or last is None or first <= 0:
        return None

    return ((last - first) / first) * 100


def _history_tvl_value(item: Any) -> float | None:
    if isinstance(item, dict):
        return _number(item.get("totalLiquidityUSD") or item.get("tvl"))

    return _number(item)


def _find_fee_item(analysis: dict[str, Any], fee_items: list[Any]) -> dict[str, Any] | None:
    expected = {
        str(analysis.get("slug", "")).lower(),
        str(analysis.get("fees_slug", "")).lower(),
        str(analysis.get("name", "")).lower(),
    }
    for item in fee_items:
        if not isinstance(item, dict):
            continue
        candidates = {
            str(item.get("slug", "")).lower(),
            str(item.get("name", "")).lower(),
            str(item.get("displayName", "")).lower(),
            str(item.get("module", "")).lower(),
        }
        if expected & candidates:
            return item

    return None


def _fee_value(item: dict[str, Any]) -> float | None:
    keys = (
        "total24h",
        "dailyFees",
        "dailyRevenue",
        "dailyUserFees",
        "dailyHoldersRevenue",
        "fees",
        "revenue",
    )
    for key in keys:
        value = _number(item.get(key))
        if value is not None and value > 0:
            return value

    return None


def _stablecoin_summary(payload: Any) -> dict[str, Any]:
    if not isinstance(payload, dict):
        return {}

    total_circulating = payload.get("totalCirculating")
    if isinstance(total_circulating, dict):
        total_circulating = total_circulating.get("peggedUSD")

    return {
        "pegged_asset_count": len(payload.get("peggedAssets") or []),
        "total_circulating_usd": total_circulating,
    }


def _optional_text(value: Any) -> str | None:
    if value is None:
        return None

    cleaned = str(value).strip()
    return cleaned or None


def _number(value: Any) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, int | float):
        return float(value)

    try:
        return float(str(value).strip())
    except ValueError:
        return None


def _usd(value: float) -> str:
    if value >= 1_000_000_000:
        return f"${value / 1_000_000_000:.2f}B"
    if value >= 1_000_000:
        return f"${value / 1_000_000:.2f}M"

    return f"${value:,.0f}"


def _fmt(value: float) -> str:
    return f"{value:.2f}".rstrip("0").rstrip(".")
