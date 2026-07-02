"""WhatsApp notification formatting for final reports."""

from __future__ import annotations

from typing import Any, Protocol

from crypto_market_agents.notifications.whatsapp_client import NotificationResult
from crypto_market_agents.schemas import FinalReport, RiskLevel, risk_level_at_or_above


MAX_SUMMARY_FINDINGS = 3
MAX_ASSETS = 8
SUMMARY_LIMIT = 360
FINDING_LIMIT = 140
DISCLAIMER = "Analyse informative uniquement, pas un conseil financier."


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

    def __init__(self, client: TextMessageClient) -> None:
        self.client = client

    def send_final_report_summary(self, final_report: FinalReport) -> dict[str, Any]:
        """Send a short final report summary through WhatsAppClient."""

        return self.client.send_text_message(
            self.format_final_report_summary(final_report)
        )

    def send_high_risk_alert(self, final_report: FinalReport) -> dict[str, Any]:
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

        return self.client.send_text_message(self.format_high_risk_alert(final_report))

    def format_final_report_summary(self, final_report: FinalReport) -> str:
        """Return a compact WhatsApp-ready final report summary."""

        lines = [
            final_report.title,
            f"Risque global: {final_report.global_risk_level.value}",
            f"Confiance: {final_report.confidence:.2f}",
            f"Resume: {_shorten(final_report.market_summary, SUMMARY_LIMIT)}",
        ]

        lines.append("Findings cles:")
        if final_report.key_findings:
            for finding in final_report.key_findings[:MAX_SUMMARY_FINDINGS]:
                lines.append(
                    "- "
                    + _shorten(f"{finding.title}: {finding.description}", FINDING_LIMIT)
                )
        else:
            lines.append("- Aucun finding cle.")

        lines.append(
            "A surveiller: "
            + _format_assets(final_report.assets_to_watch or final_report.cryptos_to_watch)
        )
        lines.append(DISCLAIMER)

        return "\n".join(lines)

    def format_high_risk_alert(self, final_report: FinalReport) -> str:
        """Return a compact WhatsApp-ready high-risk alert."""

        lines = [
            "Alerte risque crypto",
            f"Risque global: {final_report.global_risk_level.value}",
            f"Confiance: {final_report.confidence:.2f}",
            f"Resume: {_shorten(final_report.market_summary, SUMMARY_LIMIT)}",
        ]

        if final_report.warnings:
            lines.append("Warnings:")
            for warning in final_report.warnings[:3]:
                lines.append("- " + _shorten(warning, FINDING_LIMIT))
        elif final_report.important_risks:
            lines.append("Risques:")
            for risk in final_report.important_risks[:3]:
                lines.append("- " + _shorten(risk.description, FINDING_LIMIT))

        lines.append(
            "A surveiller: "
            + _format_assets(final_report.assets_to_watch or final_report.cryptos_to_watch)
        )
        lines.append(DISCLAIMER)

        return "\n".join(lines)


def _format_assets(assets: tuple[str, ...]) -> str:
    if not assets:
        return "aucun asset/protocole specifique"

    return ", ".join(assets[:MAX_ASSETS])


def _shorten(value: str, limit: int) -> str:
    cleaned = " ".join(str(value).split())
    if len(cleaned) <= limit:
        return cleaned

    return cleaned[: max(0, limit - 3)].rstrip() + "..."

