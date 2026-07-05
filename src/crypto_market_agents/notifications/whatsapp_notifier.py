"""WhatsApp notification formatting for final reports."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

from crypto_market_agents.notifications.whatsapp_client import NotificationResult
from crypto_market_agents.schemas import FinalReport, Finding, RiskLevel, risk_level_at_or_above
from crypto_market_agents.security import redact_text


DEFAULT_MAX_MESSAGE_CHARS = 1500
MIN_MESSAGE_CHARS = 200
MAX_MESSAGE_CHARS = 4096
MAX_SUMMARY_FINDINGS = 3
MAX_ALERT_FINDINGS = 3
MAX_ASSETS = 6
SUMMARY_LIMIT = 260
FINDING_LIMIT = 140
REASON_LIMIT = 180
DISCLAIMER = "Info uniquement, pas un conseil financier."
TRUNCATION_NOTICE = "... message tronqué. Consultez le rapport complet."


@dataclass(frozen=True, slots=True)
class ReportPaths:
    """Optional report paths that can be referenced in WhatsApp messages."""

    markdown_path: str | Path | None = None
    json_path: str | Path | None = None
    html_path: str | Path | None = None


class TextMessageClient(Protocol):
    """Small protocol implemented by WhatsAppClient and test doubles."""

    def send_text_message(
        self,
        message: str,
        to_number: str | None = None,
    ) -> dict[str, Any]:
        """Send a text message."""


class WhatsAppNotifier:
    """Build short WhatsApp messages from FinalReport objects."""

    def __init__(
        self,
        client: TextMessageClient,
        *,
        max_message_chars: int = DEFAULT_MAX_MESSAGE_CHARS,
    ) -> None:
        self.client = client
        self.max_message_chars = _normalize_max_message_chars(max_message_chars)

    def send_final_report_summary(
        self,
        final_report: FinalReport,
        report_paths: ReportPaths | Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Send a short final report summary through WhatsAppClient."""

        return self.client.send_text_message(
            self.build_final_report_summary_message(final_report, report_paths)
        )

    def send_high_risk_alert(
        self,
        final_report: FinalReport,
        report_paths: ReportPaths | Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Send an alert only when the final report risk is high or critical."""

        if not risk_level_at_or_above(final_report.global_risk_level, RiskLevel.HIGH):
            return NotificationResult(
                sent=False,
                channel="whatsapp",
                status="skipped",
                message=(
                    "No WhatsApp risk alert sent because global risk is "
                    f"{final_report.global_risk_level.value}."
                ),
            ).to_dict()

        return self.client.send_text_message(
            self.build_high_risk_alert_message(final_report, report_paths)
        )

    def build_final_report_summary_message(
        self,
        final_report: FinalReport,
        report_paths: ReportPaths | Mapping[str, Any] | None = None,
    ) -> str:
        """Build the final report summary without sending it."""

        return build_final_report_summary_message(
            final_report,
            report_paths,
            max_message_chars=self.max_message_chars,
        )

    def build_high_risk_alert_message(
        self,
        final_report: FinalReport,
        report_paths: ReportPaths | Mapping[str, Any] | None = None,
    ) -> str:
        """Build a high/critical risk alert without sending it."""

        return build_high_risk_alert_message(
            final_report,
            report_paths,
            max_message_chars=self.max_message_chars,
        )

    def preview_final_report_summary(
        self,
        final_report: FinalReport,
        report_paths: ReportPaths | Mapping[str, Any] | None = None,
    ) -> str:
        """Return the final summary message that would be sent."""

        return self.build_final_report_summary_message(final_report, report_paths)

    def preview_high_risk_alert(
        self,
        final_report: FinalReport,
        report_paths: ReportPaths | Mapping[str, Any] | None = None,
    ) -> str:
        """Return the alert message that would be sent, or a skipped notice."""

        if not risk_level_at_or_above(final_report.global_risk_level, RiskLevel.HIGH):
            return (
                "Aucune alerte WhatsApp high/critical: risque global "
                f"{final_report.global_risk_level.value}."
            )

        return self.build_high_risk_alert_message(final_report, report_paths)

    def format_final_report_summary(self, final_report: FinalReport) -> str:
        """Backward-compatible alias for the final summary builder."""

        return self.build_final_report_summary_message(final_report)

    def format_high_risk_alert(self, final_report: FinalReport) -> str:
        """Backward-compatible alias for the high-risk alert builder."""

        return self.build_high_risk_alert_message(final_report)


def build_final_report_summary_message(
    final_report: FinalReport,
    report_paths: ReportPaths | Mapping[str, Any] | None = None,
    *,
    max_message_chars: int = DEFAULT_MAX_MESSAGE_CHARS,
) -> str:
    """Return a compact WhatsApp-ready final report summary."""

    paths = _coerce_report_paths(report_paths)
    lines = [
        "Crypto Market Agents",
        f"Risque global: {final_report.global_risk_level.value}",
        f"Confiance: {_format_percent(final_report.confidence)}",
        f"Resume marche: {_shorten(final_report.market_summary, SUMMARY_LIMIT)}",
        "Top findings:",
    ]

    if final_report.key_findings:
        for index, finding in enumerate(
            final_report.key_findings[:MAX_SUMMARY_FINDINGS],
            start=1,
        ):
            lines.append(
                f"{index}. {_shorten(f'{finding.title}: {finding.description}', FINDING_LIMIT)}"
            )
    else:
        lines.append("1. Aucun finding cle.")

    lines.append(
        "A surveiller: "
        + _format_assets(final_report.assets_to_watch or final_report.cryptos_to_watch)
    )
    lines.append(f"Warnings: {len(final_report.warnings)}")

    if paths.html_path:
        lines.append(f"Rapport HTML: {_shorten(_path_to_text(paths.html_path), FINDING_LIMIT)}")

    lines.append(DISCLAIMER)
    return _finalize_message(lines, max_message_chars)


def build_high_risk_alert_message(
    final_report: FinalReport,
    report_paths: ReportPaths | Mapping[str, Any] | None = None,
    *,
    max_message_chars: int = DEFAULT_MAX_MESSAGE_CHARS,
) -> str:
    """Return a compact WhatsApp-ready high/critical-risk alert."""

    paths = _coerce_report_paths(report_paths)
    priority_findings = _priority_risk_findings(final_report)
    lines = [
        "[ALERTE RISQUE]",
        f"Risque global: {final_report.global_risk_level.value}",
        f"Confiance: {_format_percent(final_report.confidence)}",
        f"Raison principale: {_shorten(_main_reason(final_report, priority_findings), REASON_LIMIT)}",
        "Findings prioritaires:",
    ]

    if priority_findings:
        for index, finding in enumerate(priority_findings[:MAX_ALERT_FINDINGS], start=1):
            lines.append(
                f"{index}. {_shorten(f'{finding.title}: {finding.description}', FINDING_LIMIT)}"
            )
    elif final_report.warnings:
        for index, warning in enumerate(final_report.warnings[:MAX_ALERT_FINDINGS], start=1):
            lines.append(f"{index}. {_shorten(warning, FINDING_LIMIT)}")
    else:
        lines.append("1. Aucun finding high/critical detaille.")

    lines.append(
        "Assets/protocoles concernes: "
        + _format_assets(final_report.assets_to_watch or final_report.cryptos_to_watch)
    )

    if paths.html_path:
        lines.append(f"Rapport HTML: {_shorten(_path_to_text(paths.html_path), FINDING_LIMIT)}")

    lines.append(DISCLAIMER)
    return _finalize_message(lines, max_message_chars)


def _format_assets(assets: tuple[str, ...]) -> str:
    if not assets:
        return "aucun asset/protocole specifique"

    return ", ".join(redact_text(asset) for asset in assets[:MAX_ASSETS])


def _shorten(value: str, limit: int) -> str:
    cleaned = " ".join(redact_text(value).split())
    if len(cleaned) <= limit:
        return cleaned

    return cleaned[: max(0, limit - 3)].rstrip() + "..."


def _normalize_max_message_chars(value: int) -> int:
    return max(MIN_MESSAGE_CHARS, min(int(value), MAX_MESSAGE_CHARS))


def _format_percent(value: float) -> str:
    bounded = max(0.0, min(float(value or 0.0), 1.0))
    return f"{bounded * 100:.0f}%"


def _coerce_report_paths(
    report_paths: ReportPaths | Mapping[str, Any] | None,
) -> ReportPaths:
    if report_paths is None:
        return ReportPaths()
    if isinstance(report_paths, ReportPaths):
        return report_paths

    return ReportPaths(
        markdown_path=report_paths.get("markdown_path"),
        json_path=report_paths.get("json_path"),
        html_path=report_paths.get("html_path"),
    )


def _path_to_text(value: str | Path) -> str:
    return redact_text(str(value))


def _priority_risk_findings(final_report: FinalReport) -> tuple[Finding, ...]:
    important_titles = {
        risk.title
        for risk in final_report.important_risks
        if risk.level in {RiskLevel.HIGH, RiskLevel.CRITICAL}
    }
    important_descriptions = {
        risk.description
        for risk in final_report.important_risks
        if risk.level in {RiskLevel.HIGH, RiskLevel.CRITICAL}
    }

    selected: list[Finding] = []
    for finding in final_report.key_findings:
        if finding.title in important_titles or finding.description in important_descriptions:
            selected.append(finding)

    if selected:
        return tuple(selected)

    risk_words = ("critical", "critique", "high", "eleve", "extreme", "exploit", "hack")
    for finding in final_report.key_findings:
        text = f"{finding.title} {finding.description}".lower()
        if any(word in text for word in risk_words):
            selected.append(finding)

    return tuple(selected or final_report.key_findings[:MAX_ALERT_FINDINGS])


def _main_reason(final_report: FinalReport, priority_findings: tuple[Finding, ...]) -> str:
    for risk in final_report.important_risks:
        if risk.level in {RiskLevel.HIGH, RiskLevel.CRITICAL}:
            return f"{risk.title}: {risk.description}"

    if priority_findings:
        finding = priority_findings[0]
        return f"{finding.title}: {finding.description}"

    if final_report.warnings:
        return final_report.warnings[0]

    return final_report.market_summary


def _finalize_message(lines: list[str], max_message_chars: int) -> str:
    max_chars = _normalize_max_message_chars(max_message_chars)
    safe_message = redact_text("\n".join(lines))
    if len(safe_message) <= max_chars:
        return safe_message

    split_lines = safe_message.splitlines()
    preserved_head = split_lines[:3]
    body = split_lines[3:-1]
    footer = [TRUNCATION_NOTICE, DISCLAIMER]
    preserved_text = "\n".join(preserved_head + footer)
    body_budget = max(0, max_chars - len(preserved_text) - 2)
    body_text = "\n".join(body)
    truncated_body = body_text[:body_budget].rstrip()

    final_lines = [*preserved_head]
    if truncated_body:
        final_lines.append(truncated_body)
    final_lines.extend(footer)
    truncated = "\n".join(final_lines)

    if len(truncated) <= max_chars:
        return truncated

    head_text = "\n".join(preserved_head)
    required_tail = "\n".join(footer)
    head_budget = max(0, max_chars - len(required_tail) - 1)
    return f"{head_text[:head_budget].rstrip()}\n{required_tail}"
