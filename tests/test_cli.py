from contextlib import redirect_stdout
from io import StringIO
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch


PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from crypto_market_agents.agents.final_synthesis_agent import FinalSynthesisAgent
from crypto_market_agents.cli import main
from crypto_market_agents.orchestrator import OrchestrationRunResult
from crypto_market_agents.schemas import AgentReport, AgentStatus, RiskLevel


class CLITests(unittest.TestCase):
    def test_cli_report_passes_simple_arguments(self):
        factory = RecordingFactory()
        output = StringIO()

        with redirect_stdout(output):
            exit_code = main(
                [
                    "report",
                    "--coins",
                    "bitcoin",
                    "ethereum",
                    "--currency",
                    "usd",
                    "--news-query",
                    "crypto OR bitcoin",
                    "--protocols",
                    "uniswap",
                    "aave",
                    "--output-dir",
                    "reports-test",
                ],
                orchestrator_factory=factory,
            )

        self.assertEqual(exit_code, 0)
        orchestrator = factory.instances[0]
        self.assertEqual(orchestrator.init_kwargs["env_file"], None)
        self.assertEqual(orchestrator.run_kwargs["coin_ids"], ["bitcoin", "ethereum"])
        self.assertEqual(orchestrator.run_kwargs["vs_currency"], "usd")
        self.assertEqual(orchestrator.run_kwargs["news_query"], "crypto OR bitcoin")
        self.assertEqual(orchestrator.run_kwargs["protocol_slugs"], ["uniswap", "aave"])
        self.assertEqual(orchestrator.run_kwargs["output_dir"], "reports-test")
        self.assertTrue(orchestrator.run_kwargs["notify_whatsapp"])
        self.assertIn("Rapport Markdown:", output.getvalue())
        self.assertIn("Rapport HTML:", output.getvalue())
        self.assertIn("Risque global: high", output.getvalue())
        self.assertIn("WhatsApp summary: sent", output.getvalue())

    def test_cli_report_no_whatsapp_disables_notification(self):
        factory = RecordingFactory()
        output = StringIO()

        with redirect_stdout(output):
            exit_code = main(
                ["report", "--coins", "solana", "--no-whatsapp"],
                orchestrator_factory=factory,
            )

        self.assertEqual(exit_code, 0)
        orchestrator = factory.instances[0]
        self.assertFalse(orchestrator.run_kwargs["notify_whatsapp"])
        self.assertIn("WhatsApp summary: skipped", output.getvalue())

    def test_cli_report_mock_creates_reports_without_orchestrator_or_whatsapp(self):
        factory = ExplodingFactory()

        with tempfile.TemporaryDirectory() as output_dir:
            with patch.dict(os.environ, {"WHATSAPP_ENABLED": "true"}):
                exit_code, output, payload, markdown, html = self.run_mock_cli(
                    output_dir,
                    orchestrator_factory=factory,
                )

        self.assertEqual(exit_code, 0)
        self.assertEqual(factory.calls, 0)
        self.assertIn("Mode: mock", output)
        self.assertIn("Aucune API externe appelee.", output)
        self.assertIn("WhatsApp: disabled (mock mode)", output)
        self.assertIn("Rapport Markdown:", output)
        self.assertIn("Rapport JSON:", output)
        self.assertIn("Rapport HTML:", output)
        self.assertIn("Disclaimer", markdown)
        self.assertIn("Disclaimer", html)
        self.assertEqual(payload["global_risk_level"], "medium")
        self.assertEqual(len(payload["agent_reports"]), 4)

    def test_cli_report_mock_risk_level_low(self):
        with tempfile.TemporaryDirectory() as output_dir:
            _, output, payload, _, _ = self.run_mock_cli(output_dir, risk_level="low")

        self.assertIn("Risque global: low", output)
        self.assertEqual(payload["global_risk_level"], "low")

    def test_cli_report_mock_risk_level_high(self):
        with tempfile.TemporaryDirectory() as output_dir:
            _, output, payload, _, _ = self.run_mock_cli(output_dir, risk_level="high")

        self.assertIn("Risque global: high", output)
        self.assertEqual(payload["global_risk_level"], "high")

    def test_cli_report_mock_risk_level_critical(self):
        with tempfile.TemporaryDirectory() as output_dir:
            _, output, payload, _, _ = self.run_mock_cli(output_dir, risk_level="critical")

        self.assertIn("Risque global: critical", output)
        self.assertEqual(payload["global_risk_level"], "critical")

    def run_mock_cli(
        self,
        output_dir,
        *,
        risk_level=None,
        orchestrator_factory=None,
    ):
        argv = ["report", "--mock", "--output-dir", output_dir]
        if risk_level:
            argv.extend(["--mock-risk-level", risk_level])

        output = StringIO()
        with redirect_stdout(output):
            exit_code = main(
                argv,
                orchestrator_factory=orchestrator_factory or ExplodingFactory(),
            )

        output_path = Path(output_dir)
        markdown_files = sorted(output_path.glob("mock_report_*.md"))
        json_files = sorted(output_path.glob("mock_report_*.json"))
        html_files = sorted(output_path.glob("mock_report_*.html"))
        self.assertEqual(len(markdown_files), 1)
        self.assertEqual(len(json_files), 1)
        self.assertEqual(len(html_files), 1)

        payload = json.loads(json_files[0].read_text(encoding="utf-8"))
        markdown = markdown_files[0].read_text(encoding="utf-8")
        html = html_files[0].read_text(encoding="utf-8")
        return exit_code, output.getvalue(), payload, markdown, html


class RecordingFactory:
    def __init__(self):
        self.instances = []

    def __call__(self, **kwargs):
        instance = FakeOrchestrator(kwargs)
        self.instances.append(instance)
        return instance


class ExplodingFactory:
    def __init__(self):
        self.calls = 0

    def __call__(self, **kwargs):
        self.calls += 1
        raise AssertionError("orchestrator_factory must not be called in mock mode")


class FakeOrchestrator:
    def __init__(self, init_kwargs):
        self.init_kwargs = init_kwargs
        self.run_kwargs = None
        self.last_run = None

    def run_full_analysis(self, **kwargs):
        self.run_kwargs = kwargs
        report = AgentReport(
            agent_name="volatility_risk_agent",
            status=AgentStatus.SUCCESS,
            summary="Risque de test.",
            risk_level=RiskLevel.HIGH,
            confidence=0.90,
        )
        final_report = FinalSynthesisAgent().synthesize([report])
        status = "skipped" if not kwargs.get("notify_whatsapp") else "sent"
        self.last_run = OrchestrationRunResult(
            final_report=final_report,
            agent_reports=(report,),
            markdown_path=Path("reports-test/report_2026-07-01_1234.md"),
            json_path=Path("reports-test/report_2026-07-01_1234.json"),
            html_path=Path("reports-test/report_2026-07-01_1234.html"),
            whatsapp_summary={
                "sent": status == "sent",
                "channel": "whatsapp",
                "status": status,
                "message": status,
                "error": None,
                "data": {},
            },
            whatsapp_alert={
                "sent": status == "sent",
                "channel": "whatsapp",
                "status": status,
                "message": status,
                "error": None,
                "data": {},
            },
        )
        return final_report


if __name__ == "__main__":
    unittest.main()
