"""Manual WhatsApp notification smoke test.

The script prints the generated messages. It sends them only when
WHATSAPP_ENABLED=true in .env or the environment.
"""

from pathlib import Path
import sys


PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from crypto_market_agents.agents.final_synthesis_agent import FinalSynthesisAgent
from crypto_market_agents.config import load_config
from crypto_market_agents.notifications.whatsapp_client import WhatsAppClient
from crypto_market_agents.notifications.whatsapp_notifier import WhatsAppNotifier
from crypto_market_agents.schemas import (
    AgentReport,
    AgentStatus,
    Finding,
    ImpactDirection,
    RiskLevel,
)


def main() -> None:
    config = load_config(PROJECT_ROOT / ".env")
    final_report = _fake_final_report()
    client = WhatsAppClient.from_config(config.whatsapp)
    notifier = WhatsAppNotifier(client)

    summary_message = notifier.format_final_report_summary(final_report)
    alert_message = notifier.format_high_risk_alert(final_report)

    print("WhatsApp summary preview:")
    print(summary_message)
    print()
    print("WhatsApp high-risk alert preview:")
    print(alert_message)
    print()

    if not config.whatsapp.enabled:
        print("WHATSAPP_ENABLED=false: no live message sent.")
        return

    print("WHATSAPP_ENABLED=true: sending summary...")
    print(notifier.send_final_report_summary(final_report))
    print("Sending high-risk alert if needed...")
    print(notifier.send_high_risk_alert(final_report))


def _fake_final_report():
    reports = [
        AgentReport(
            agent_name="price_market_agent",
            status=AgentStatus.SUCCESS,
            summary="Prix en mouvement notable sur BTC.",
            risk_level=RiskLevel.MEDIUM,
            confidence=0.82,
            findings=(
                Finding(
                    title="Variation notable",
                    description="BTC montre un mouvement notable sur 24h.",
                    impact=ImpactDirection.MIXED,
                    symbols=("btc",),
                ),
            ),
        ),
        AgentReport(
            agent_name="volatility_risk_agent",
            status=AgentStatus.SUCCESS,
            summary="Volatilite elevee sur le marche observe.",
            risk_level=RiskLevel.HIGH,
            confidence=0.80,
            findings=(
                Finding(
                    title="Volatilite 24h elevee",
                    description="Amplitude importante sur BTC.",
                    impact=ImpactDirection.MIXED,
                    symbols=("btc",),
                ),
            ),
        ),
    ]

    return FinalSynthesisAgent().synthesize(reports)


if __name__ == "__main__":
    main()
