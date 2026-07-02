"""Final synthesis agent for already-produced agent reports."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
import re
from typing import Any

from crypto_market_agents.schemas import (
    AgentFinding,
    AgentReport,
    AgentStatus,
    FinalReport,
    ImpactDirection,
    RiskLevel,
    RiskSignal,
    SentimentLabel,
    risk_level_at_or_above,
)


EXPECTED_AGENT_NAMES = (
    "price_market_agent",
    "volatility_risk_agent",
    "news_sentiment_agent",
    "onchain_fundamental_agent",
)

MAX_KEY_FINDINGS = 12
MAX_ASSETS_TO_WATCH = 12
LOW_CONFIDENCE_THRESHOLD = 0.45

KNOWN_ASSET_NAMES = {
    "bitcoin": "bitcoin",
    "ethereum": "ethereum",
    "solana": "solana",
    "cardano": "cardano",
    "polkadot": "polkadot",
    "chainlink": "chainlink",
    "avalanche": "avalanche",
    "polygon": "polygon",
    "arbitrum": "arbitrum",
    "optimism": "optimism",
    "uniswap": "uniswap",
    "aave": "aave",
    "lido": "lido",
    "curve": "curve",
    "maker": "maker",
}

ASSET_DATA_KEYS = {
    "asset",
    "assets",
    "asset_id",
    "asset_ids",
    "coin_id",
    "coin_ids",
    "protocol",
    "protocol_slug",
    "related_assets",
    "slug",
    "symbol",
    "symbols",
}

IGNORED_ASSET_TOKENS = {
    "api",
    "btc?",  # defensive cleanup fallback for odd titles
    "high",
    "low",
    "market",
    "news",
    "tvl",
    "usd",
    "usdc",
    "usdt",
}

UPPERCASE_TOKEN_RE = re.compile(r"\b[A-Z0-9]{2,10}\b")


class FinalSynthesisAgent:
    """Combine specialized AgentReport objects into one final report."""

    agent_name = "final_synthesis_agent"

    def __init__(
        self,
        expected_agent_names: Sequence[str] = EXPECTED_AGENT_NAMES,
    ) -> None:
        self.expected_agent_names = tuple(
            str(name).strip().lower() for name in expected_agent_names if str(name).strip()
        )

    def synthesize(self, agent_reports: list[AgentReport] | Sequence[AgentReport]) -> FinalReport:
        """Return a final synthesis from existing agent reports only."""

        reports = _normalize_reports(agent_reports)
        reports_by_name = {report.agent_name: report for report in reports}

        global_risk_level = self._global_risk_level(reports)
        confidence = self._global_confidence(reports)
        key_findings = self._key_findings(reports)
        assets_to_watch = self._assets_to_watch(reports)
        contradictions = self._contradictions(reports_by_name, reports)
        important_risks = self._important_risks(reports)
        warnings = self._warnings(reports, global_risk_level, contradictions)
        sentiment = self._sentiment(reports_by_name, reports)

        return FinalReport(
            title="Crypto Market Agents - Rapport final",
            market_summary=self._market_summary(
                reports=reports,
                global_risk_level=global_risk_level,
                confidence=confidence,
                contradiction_count=len(contradictions),
            ),
            cryptos_to_watch=assets_to_watch,
            important_risks=important_risks,
            confidence_score=confidence,
            conclusion="Analyse informative uniquement, pas un conseil financier.",
            contradictions=tuple(contradictions),
            sentiment=sentiment,
            global_risk_level=global_risk_level,
            confidence=confidence,
            key_findings=key_findings,
            assets_to_watch=assets_to_watch,
            warnings=tuple(warnings),
            agent_reports=reports,
        )

    def _global_risk_level(self, reports: tuple[AgentReport, ...]) -> RiskLevel:
        active_reports = tuple(
            report for report in reports if report.status is not AgentStatus.FAILED
        )
        if not active_reports:
            return RiskLevel.MEDIUM

        risk_levels = [report.risk_level for report in active_reports]
        if any(level is RiskLevel.CRITICAL for level in risk_levels):
            return RiskLevel.CRITICAL

        high_count = sum(level is RiskLevel.HIGH for level in risk_levels)
        medium_count = sum(level is RiskLevel.MEDIUM for level in risk_levels)

        if high_count >= 2:
            return RiskLevel.CRITICAL
        if high_count == 1 or medium_count >= 2:
            return RiskLevel.HIGH
        if medium_count == 1:
            return RiskLevel.MEDIUM

        return RiskLevel.LOW

    def _global_confidence(self, reports: tuple[AgentReport, ...]) -> float:
        if not reports:
            return 0.0

        average_confidence = sum(report.confidence for report in reports) / len(reports)
        expected_count = max(len(self.expected_agent_names), 1)
        available_count = len({report.agent_name for report in reports})
        coverage = min(available_count / expected_count, 1.0)

        confidence = average_confidence * (0.70 + (0.30 * coverage))

        failed_count = sum(report.status is AgentStatus.FAILED for report in reports)
        partial_count = sum(report.status is AgentStatus.PARTIAL for report in reports)
        low_confidence_count = sum(
            report.confidence < LOW_CONFIDENCE_THRESHOLD for report in reports
        )

        confidence -= failed_count * 0.15
        if partial_count >= 2:
            confidence -= 0.12
        elif partial_count == 1:
            confidence -= 0.04
        if low_confidence_count >= 2:
            confidence -= 0.08

        return round(max(0.0, min(0.95, confidence)), 2)

    def _key_findings(self, reports: tuple[AgentReport, ...]) -> tuple[AgentFinding, ...]:
        prioritized: list[tuple[float, AgentFinding]] = []
        for report in _ordered_reports(reports, self.expected_agent_names):
            for finding in report.findings:
                priority = (
                    finding.confidence_score
                    + (_risk_rank(report.risk_level) * 0.08)
                    + _impact_weight(finding.impact)
                )
                prioritized.append((priority, finding))

        prioritized.sort(key=lambda item: item[0], reverse=True)
        return tuple(finding for _, finding in prioritized[:MAX_KEY_FINDINGS])

    def _assets_to_watch(self, reports: tuple[AgentReport, ...]) -> tuple[str, ...]:
        assets: list[str] = []
        seen: set[str] = set()

        for report in _ordered_reports(reports, self.expected_agent_names):
            for asset in _extract_assets_from_report(report):
                if asset not in seen:
                    seen.add(asset)
                    assets.append(asset)
                if len(assets) >= MAX_ASSETS_TO_WATCH:
                    return tuple(assets)

        return tuple(assets)

    def _important_risks(self, reports: tuple[AgentReport, ...]) -> tuple[RiskSignal, ...]:
        risks: list[RiskSignal] = []
        for report in reports:
            if report.status is AgentStatus.FAILED:
                continue
            if not risk_level_at_or_above(report.risk_level, RiskLevel.MEDIUM):
                continue

            risks.append(
                RiskSignal(
                    title=f"Risque {report.risk_level.value} - {report.agent_name}",
                    description=report.summary,
                    level=report.risk_level,
                    symbols=_extract_assets_from_report(report),
                    data={
                        "agent_name": report.agent_name,
                        "status": report.status.value,
                        "confidence": report.confidence,
                    },
                )
            )

        return tuple(risks)

    def _warnings(
        self,
        reports: tuple[AgentReport, ...],
        global_risk_level: RiskLevel,
        contradictions: list[str],
    ) -> list[str]:
        warnings: list[str] = []
        missing_agents = sorted(
            set(self.expected_agent_names) - {report.agent_name for report in reports}
        )
        if missing_agents:
            warnings.append("Rapports manquants: " + ", ".join(missing_agents) + ".")

        for report in reports:
            if report.status is AgentStatus.FAILED:
                reason = f" {report.errors[0]}" if report.errors else ""
                warnings.append(f"Agent en echec: {report.agent_name}.{reason}")
            elif report.status is AgentStatus.PARTIAL:
                warnings.append(f"Analyse partielle: {report.agent_name}.")

        if risk_level_at_or_above(global_risk_level, RiskLevel.HIGH):
            warnings.append(f"Risque global {global_risk_level.value} detecte.")
        if contradictions:
            warnings.append("Signaux contradictoires detectes entre agents.")

        warnings.append("Analyse informative uniquement, pas un conseil financier.")
        return _dedupe_text(warnings)

    def _contradictions(
        self,
        reports_by_name: dict[str, AgentReport],
        reports: tuple[AgentReport, ...],
    ) -> list[str]:
        contradictions: list[str] = []
        price_report = reports_by_name.get("price_market_agent")
        volatility_report = reports_by_name.get("volatility_risk_agent")
        news_report = reports_by_name.get("news_sentiment_agent")
        onchain_report = reports_by_name.get("onchain_fundamental_agent")

        if (
            price_report
            and news_report
            and _has_positive_price_signal(price_report)
            and _has_negative_news_signal(news_report)
        ):
            contradictions.append("Marche prix positif mais actualites negatives.")

        if (
            onchain_report
            and volatility_report
            and onchain_report.risk_level is RiskLevel.LOW
            and risk_level_at_or_above(volatility_report.risk_level, RiskLevel.HIGH)
        ):
            contradictions.append("Fondamentaux solides mais volatilite elevee.")

        if (
            price_report
            and news_report
            and price_report.risk_level is RiskLevel.LOW
            and risk_level_at_or_above(news_report.risk_level, RiskLevel.HIGH)
        ):
            contradictions.append("Risque prix faible mais risque news eleve.")

        low_confidence_reports = [
            report.agent_name
            for report in reports
            if report.confidence < LOW_CONFIDENCE_THRESHOLD
        ]
        if len(low_confidence_reports) >= 2:
            contradictions.append(
                "Plusieurs agents ont une confiance faible: "
                + ", ".join(low_confidence_reports)
                + "."
            )

        return _dedupe_text(contradictions)

    def _sentiment(
        self,
        reports_by_name: dict[str, AgentReport],
        reports: tuple[AgentReport, ...],
    ) -> SentimentLabel:
        news_report = reports_by_name.get("news_sentiment_agent")
        if news_report:
            try:
                return SentimentLabel(str(news_report.data.get("sentiment", "")).lower())
            except ValueError:
                pass

        bullish = any(
            finding.impact is ImpactDirection.BULLISH
            for report in reports
            for finding in report.findings
        )
        bearish = any(
            finding.impact is ImpactDirection.BEARISH
            for report in reports
            for finding in report.findings
        )

        if bullish and bearish:
            return SentimentLabel.MIXED
        if bullish:
            return SentimentLabel.POSITIVE
        if bearish:
            return SentimentLabel.NEGATIVE
        if reports:
            return SentimentLabel.NEUTRAL

        return SentimentLabel.UNKNOWN

    def _market_summary(
        self,
        *,
        reports: tuple[AgentReport, ...],
        global_risk_level: RiskLevel,
        confidence: float,
        contradiction_count: int,
    ) -> str:
        if not reports:
            return (
                "Aucun rapport agent disponible. Le niveau de risque reste incertain "
                "et la confiance globale est nulle."
            )

        failed_count = sum(report.status is AgentStatus.FAILED for report in reports)
        partial_count = sum(report.status is AgentStatus.PARTIAL for report in reports)
        high_or_more = sum(
            risk_level_at_or_above(report.risk_level, RiskLevel.HIGH)
            and report.status is not AgentStatus.FAILED
            for report in reports
        )

        return (
            f"Synthese de {len(reports)} rapport(s) agent. "
            f"Risque global {global_risk_level.value}, confiance {confidence:.2f}. "
            f"{high_or_more} agent(s) signalent un risque high ou critical. "
            f"{partial_count} rapport(s) partiel(s), {failed_count} echec(s). "
            f"{contradiction_count} contradiction(s) simple(s) detectee(s)."
        )


def _normalize_reports(agent_reports: Sequence[AgentReport]) -> tuple[AgentReport, ...]:
    if not agent_reports:
        return ()
    if isinstance(agent_reports, str):
        raise TypeError("agent_reports must be a sequence of AgentReport objects.")

    reports: list[AgentReport] = []
    for report in agent_reports:
        if isinstance(report, AgentReport):
            reports.append(report)
        elif isinstance(report, Mapping):
            reports.append(AgentReport.from_dict(report))
        else:
            raise TypeError("agent_reports must contain AgentReport objects.")

    return tuple(reports)


def _ordered_reports(
    reports: tuple[AgentReport, ...],
    expected_agent_names: tuple[str, ...],
) -> tuple[AgentReport, ...]:
    order = {name: index for index, name in enumerate(expected_agent_names)}
    return tuple(
        sorted(
            reports,
            key=lambda report: (order.get(report.agent_name, len(order)), report.agent_name),
        )
    )


def _risk_rank(level: RiskLevel) -> int:
    return {
        RiskLevel.LOW: 0,
        RiskLevel.MEDIUM: 1,
        RiskLevel.HIGH: 2,
        RiskLevel.CRITICAL: 3,
    }[level]


def _impact_weight(impact: ImpactDirection) -> float:
    if impact in {ImpactDirection.BEARISH, ImpactDirection.MIXED}:
        return 0.12
    if impact is ImpactDirection.BULLISH:
        return 0.08
    return 0.0


def _extract_assets_from_report(report: AgentReport) -> tuple[str, ...]:
    assets: list[str] = []
    seen: set[str] = set()

    def add(value: Any) -> None:
        for asset in _asset_candidates(value):
            if asset not in seen:
                seen.add(asset)
                assets.append(asset)

    for finding in report.findings:
        add(finding.symbols)
        for key, value in finding.data.items():
            if str(key).lower() in ASSET_DATA_KEYS:
                add(value)
        add(_assets_from_text(f"{finding.title} {finding.description}"))

    protocols = report.data.get("protocols")
    if isinstance(protocols, Sequence) and not isinstance(protocols, str):
        for protocol in protocols:
            if isinstance(protocol, Mapping):
                add(protocol.get("slug"))
                add(protocol.get("name"))

    return tuple(assets)


def _asset_candidates(value: Any) -> tuple[str, ...]:
    if value is None:
        return ()
    if isinstance(value, str):
        candidate = _clean_asset_token(value)
        return (candidate,) if candidate else ()
    if isinstance(value, Mapping):
        candidates: list[str] = []
        for key in ASSET_DATA_KEYS:
            candidates.extend(_asset_candidates(value.get(key)))
        return tuple(candidates)
    if isinstance(value, Sequence):
        candidates = []
        for item in value:
            candidates.extend(_asset_candidates(item))
        return tuple(candidates)

    candidate = _clean_asset_token(str(value))
    return (candidate,) if candidate else ()


def _assets_from_text(text: str) -> tuple[str, ...]:
    assets: list[str] = []
    lower_text = text.lower()

    for name, slug in KNOWN_ASSET_NAMES.items():
        if name in lower_text:
            assets.append(slug)

    for token in UPPERCASE_TOKEN_RE.findall(text):
        candidate = _clean_asset_token(token)
        if candidate:
            assets.append(candidate)

    return tuple(assets)


def _clean_asset_token(value: str) -> str | None:
    cleaned = value.strip().strip(".,;:()[]{}").lower()
    if not cleaned:
        return None
    if cleaned in IGNORED_ASSET_TOKENS:
        return None
    if cleaned.startswith(("http://", "https://")):
        return None
    if not re.fullmatch(r"[a-z0-9][a-z0-9._-]{1,31}", cleaned):
        return None

    return cleaned


def _has_positive_price_signal(report: AgentReport) -> bool:
    positive_words = ("hausse", "positif", "high_24h", "volume eleve")
    return any(
        finding.impact is ImpactDirection.BULLISH
        or any(word in f"{finding.title} {finding.description}".lower() for word in positive_words)
        for finding in report.findings
    )


def _has_negative_news_signal(report: AgentReport) -> bool:
    sentiment = str(report.data.get("sentiment", "")).lower()
    if sentiment == SentimentLabel.NEGATIVE.value:
        return True
    if risk_level_at_or_above(report.risk_level, RiskLevel.HIGH):
        return True

    return any(finding.impact is ImpactDirection.BEARISH for finding in report.findings)


def _dedupe_text(values: Sequence[str]) -> list[str]:
    cleaned_values: list[str] = []
    seen: set[str] = set()
    for value in values:
        cleaned = str(value).strip()
        if cleaned and cleaned not in seen:
            seen.add(cleaned)
            cleaned_values.append(cleaned)

    return cleaned_values

