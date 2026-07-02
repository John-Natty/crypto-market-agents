from contextlib import redirect_stdout
from io import StringIO
from pathlib import Path
import sys
import unittest


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


class RecordingFactory:
    def __init__(self):
        self.instances = []

    def __call__(self, **kwargs):
        instance = FakeOrchestrator(kwargs)
        self.instances.append(instance)
        return instance


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
