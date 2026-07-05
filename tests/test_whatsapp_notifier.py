from pathlib import Path
import sys
import unittest


PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from crypto_market_agents.agents.final_synthesis_agent import FinalSynthesisAgent
from crypto_market_agents.notifications.whatsapp_notifier import (
    DISCLAIMER,
    ReportPaths,
    TRUNCATION_NOTICE,
    WhatsAppNotifier,
)
from crypto_market_agents.schemas import (
    AgentReport,
    AgentStatus,
    Finding,
    ImpactDirection,
    RiskLevel,
)


class FakeWhatsAppClient:
    def __init__(self):
        self.messages = []

    def send_text_message(self, message, to_number=None):
        self.messages.append({"message": message, "to_number": to_number})
        return {
            "sent": True,
            "channel": "whatsapp",
            "status": "sent",
            "message": "fake sent",
            "error": None,
            "data": {},
        }


class WhatsAppNotifierTests(unittest.TestCase):
    def test_summary_message_is_short_and_readable(self):
        notifier = WhatsAppNotifier(FakeWhatsAppClient())
        final_report = make_final_report(RiskLevel.HIGH, finding_count=4)

        message = notifier.format_final_report_summary(final_report)

        self.assertIn("Crypto Market Agents", message)
        self.assertIn("Risque global: high", message)
        self.assertIn("Confiance:", message)
        self.assertIn("Top findings:", message)
        self.assertIn("A surveiller: btc", message)
        self.assertIn("Warnings:", message)
        self.assertIn("pas un conseil financier", message)
        self.assertIn("Finding 1", message)
        self.assertIn("Finding 3", message)
        self.assertNotIn("Finding 4", message)

    def test_summary_message_includes_html_report_path(self):
        notifier = WhatsAppNotifier(FakeWhatsAppClient())
        final_report = make_final_report(RiskLevel.MEDIUM)
        paths = ReportPaths(html_path="reports/report_2026-07-01_1234.html")

        message = notifier.build_final_report_summary_message(final_report, paths)

        self.assertIn("Rapport HTML: reports/report_2026-07-01_1234.html", message)

    def test_send_final_report_summary_uses_client(self):
        client = FakeWhatsAppClient()
        notifier = WhatsAppNotifier(client)
        final_report = make_final_report(RiskLevel.MEDIUM)

        result = notifier.send_final_report_summary(final_report)

        self.assertTrue(result["sent"])
        self.assertEqual(len(client.messages), 1)
        self.assertIn("Risque global: medium", client.messages[0]["message"])

    def test_preview_final_report_summary_does_not_send(self):
        client = FakeWhatsAppClient()
        notifier = WhatsAppNotifier(client)
        final_report = make_final_report(RiskLevel.MEDIUM)

        message = notifier.preview_final_report_summary(final_report)

        self.assertIn("Risque global: medium", message)
        self.assertEqual(client.messages, [])

    def test_high_risk_alert_sent_when_high(self):
        client = FakeWhatsAppClient()
        notifier = WhatsAppNotifier(client)
        final_report = make_final_report(RiskLevel.HIGH)

        result = notifier.send_high_risk_alert(final_report)

        self.assertTrue(result["sent"])
        self.assertEqual(len(client.messages), 1)
        self.assertIn("[ALERTE RISQUE]", client.messages[0]["message"])
        self.assertIn("Findings prioritaires:", client.messages[0]["message"])
        self.assertIn("Assets/protocoles concernes:", client.messages[0]["message"])

    def test_high_risk_alert_sent_when_critical(self):
        client = FakeWhatsAppClient()
        notifier = WhatsAppNotifier(client)
        final_report = make_final_report(RiskLevel.CRITICAL)

        result = notifier.send_high_risk_alert(final_report)

        self.assertTrue(result["sent"])
        self.assertEqual(len(client.messages), 1)
        self.assertIn("Risque global: critical", client.messages[0]["message"])
        self.assertIn("[ALERTE RISQUE]", client.messages[0]["message"])

    def test_preview_high_risk_alert_does_not_send(self):
        client = FakeWhatsAppClient()
        notifier = WhatsAppNotifier(client)
        final_report = make_final_report(RiskLevel.HIGH)

        message = notifier.preview_high_risk_alert(final_report)

        self.assertIn("[ALERTE RISQUE]", message)
        self.assertEqual(client.messages, [])

    def test_high_risk_alert_not_sent_when_low(self):
        client = FakeWhatsAppClient()
        notifier = WhatsAppNotifier(client)
        final_report = make_final_report(RiskLevel.LOW)

        result = notifier.send_high_risk_alert(final_report)

        self.assertFalse(result["sent"])
        self.assertEqual(result["status"], "skipped")
        self.assertEqual(client.messages, [])

    def test_preview_high_risk_alert_reports_skip_for_medium(self):
        notifier = WhatsAppNotifier(FakeWhatsAppClient())
        final_report = make_final_report(RiskLevel.MEDIUM)

        message = notifier.preview_high_risk_alert(final_report)

        self.assertIn("Aucune alerte WhatsApp high/critical", message)

    def test_message_length_is_limited_and_truncated_cleanly(self):
        notifier = WhatsAppNotifier(FakeWhatsAppClient(), max_message_chars=260)
        final_report = make_final_report(RiskLevel.HIGH, finding_count=8, long_text=True)

        message = notifier.build_final_report_summary_message(final_report)

        self.assertLessEqual(len(message), 260)
        self.assertIn("Crypto Market Agents", message)
        self.assertIn("Risque global: high", message)
        self.assertIn("Confiance:", message)
        self.assertIn(TRUNCATION_NOTICE, message)
        self.assertTrue(message.endswith(DISCLAIMER))

    def test_secrets_are_redacted_from_messages(self):
        notifier = WhatsAppNotifier(FakeWhatsAppClient())
        final_report = make_final_report(
            RiskLevel.HIGH,
            secret_text="https://example.test/report?api_key=super-secret-token",
        )

        message = notifier.build_final_report_summary_message(final_report)

        self.assertNotIn("super-secret-token", message)
        self.assertIn("[REDACTED]", message)

    def test_high_risk_alert_not_sent_when_medium(self):
        client = FakeWhatsAppClient()
        notifier = WhatsAppNotifier(client)
        final_report = make_final_report(RiskLevel.MEDIUM)

        result = notifier.send_high_risk_alert(final_report)

        self.assertFalse(result["sent"])
        self.assertEqual(result["status"], "skipped")
        self.assertEqual(client.messages, [])


def make_final_report(
    risk_level,
    *,
    finding_count=1,
    long_text=False,
    secret_text="",
):
    extra = " " + ("Tres long signal de volatilite. " * 40 if long_text else secret_text)
    findings = tuple(
        Finding(
            title=f"Finding {index}",
            description=f"Signal de test {index} pour BTC.{extra}",
            impact=ImpactDirection.MIXED,
            symbols=("btc",),
        )
        for index in range(1, finding_count + 1)
    )
    report = AgentReport(
        agent_name="volatility_risk_agent",
        status=AgentStatus.SUCCESS,
        summary="Rapport factice de volatilite.",
        risk_level=risk_level,
        confidence=0.82,
        findings=findings,
    )

    return FinalSynthesisAgent().synthesize([report])


if __name__ == "__main__":
    unittest.main()
