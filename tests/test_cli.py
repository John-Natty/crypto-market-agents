from contextlib import redirect_stderr, redirect_stdout
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

    def test_cli_schedule_mock_runs_once_without_external_calls(self):
        factory = ExplodingFactory()

        with tempfile.TemporaryDirectory() as output_dir:
            with patch.dict(os.environ, {"WHATSAPP_ENABLED": "true"}):
                exit_code, output, payloads = self.run_schedule_mock_cli(
                    output_dir,
                    runs=1,
                    orchestrator_factory=factory,
                )

        self.assertEqual(exit_code, 0)
        self.assertEqual(factory.calls, 0)
        self.assertIn("Scheduler demarre.", output)
        self.assertIn("Mode: mock", output)
        self.assertIn("Runs prevus: 1", output)
        self.assertIn("Run 1 demarre", output)
        self.assertIn("Rapport HTML:", output)
        self.assertIn("Aucune API externe appelee.", output)
        self.assertIn("WhatsApp: disabled (mock mode)", output)
        self.assertEqual(payloads[0]["global_risk_level"], "medium")

    def test_cli_schedule_mock_runs_twice_with_sleep_mocked(self):
        sleep_calls = []

        with tempfile.TemporaryDirectory() as output_dir:
            exit_code, output, payloads = self.run_schedule_mock_cli(
                output_dir,
                runs=2,
                interval_minutes=1,
                sleep_func=sleep_calls.append,
            )

        self.assertEqual(exit_code, 0)
        self.assertEqual(sleep_calls, [60])
        self.assertIn("Run 1 demarre", output)
        self.assertIn("Run 2 demarre", output)
        self.assertIn("Prochain run prevu:", output)
        self.assertEqual(len(payloads), 2)

    def test_cli_schedule_mock_risk_level_high(self):
        with tempfile.TemporaryDirectory() as output_dir:
            _, output, payloads = self.run_schedule_mock_cli(
                output_dir,
                runs=1,
                risk_level="high",
            )

        self.assertIn("Risque global: high", output)
        self.assertEqual(payloads[0]["global_risk_level"], "high")

    def test_cli_schedule_rejects_interval_below_one_minute(self):
        output = StringIO()
        errors = StringIO()

        with redirect_stdout(output), redirect_stderr(errors), self.assertRaises(SystemExit) as cm:
            main(["schedule", "--mock", "--interval-minutes", "0"], sleep_func=lambda seconds: None)

        self.assertEqual(cm.exception.code, 2)
        self.assertIn("--interval-minutes must be at least 1", errors.getvalue())

    def test_cli_schedule_stops_cleanly_on_keyboard_interrupt(self):
        def interrupted_sleep(seconds):
            raise KeyboardInterrupt

        with tempfile.TemporaryDirectory() as output_dir:
            exit_code, output, payloads = self.run_schedule_mock_cli(
                output_dir,
                runs=None,
                interval_minutes=1,
                sleep_func=interrupted_sleep,
            )

        self.assertEqual(exit_code, 0)
        self.assertIn("Scheduler interrompu proprement.", output)
        self.assertEqual(len(payloads), 1)

    def test_cli_schedule_real_no_whatsapp_is_passed_to_orchestrator(self):
        factory = RecordingFactory()
        output = StringIO()

        with redirect_stdout(output):
            exit_code = main(
                [
                    "schedule",
                    "--runs",
                    "1",
                    "--coins",
                    "bitcoin",
                    "ethereum",
                    "--output-dir",
                    "reports-test",
                    "--no-whatsapp",
                ],
                orchestrator_factory=factory,
                sleep_func=lambda seconds: None,
            )

        self.assertEqual(exit_code, 0)
        orchestrator = factory.instances[0]
        self.assertEqual(orchestrator.run_kwargs["coin_ids"], ["bitcoin", "ethereum"])
        self.assertEqual(orchestrator.run_kwargs["output_dir"], "reports-test")
        self.assertFalse(orchestrator.run_kwargs["notify_whatsapp"])
        self.assertIn("Mode: reel", output.getvalue())
        self.assertIn("WhatsApp summary: skipped", output.getvalue())

    def test_cli_dashboard_parses_arguments_without_orchestrator(self):
        factory = ExplodingFactory()
        dashboard_runner = RecordingDashboardRunner()

        exit_code = main(
            [
                "dashboard",
                "--reports-dir",
                "reports-test",
                "--host",
                "127.0.0.2",
                "--port",
                "8123",
            ],
            orchestrator_factory=factory,
            dashboard_runner=dashboard_runner,
        )

        self.assertEqual(exit_code, 0)
        self.assertEqual(factory.calls, 0)
        self.assertEqual(
            dashboard_runner.calls,
            [
                {
                    "reports_dir": "reports-test",
                    "host": "127.0.0.2",
                    "port": 8123,
                }
            ],
        )

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

    def run_schedule_mock_cli(
        self,
        output_dir,
        *,
        runs,
        interval_minutes=1,
        risk_level=None,
        orchestrator_factory=None,
        sleep_func=None,
    ):
        argv = [
            "schedule",
            "--mock",
            "--interval-minutes",
            str(interval_minutes),
            "--output-dir",
            output_dir,
        ]
        if runs is not None:
            argv.extend(["--runs", str(runs)])
        if risk_level:
            argv.extend(["--mock-risk-level", risk_level])

        output = StringIO()
        with redirect_stdout(output):
            exit_code = main(
                argv,
                orchestrator_factory=orchestrator_factory or ExplodingFactory(),
                sleep_func=sleep_func or (lambda seconds: None),
            )

        output_path = Path(output_dir)
        markdown_files = sorted(output_path.glob("mock_report_*.md"))
        json_files = sorted(output_path.glob("mock_report_*.json"))
        html_files = sorted(output_path.glob("mock_report_*.html"))
        self.assertEqual(len(markdown_files), len(json_files))
        self.assertEqual(len(json_files), len(html_files))
        self.assertGreaterEqual(len(json_files), 1)

        payloads = [json.loads(path.read_text(encoding="utf-8")) for path in json_files]
        return exit_code, output.getvalue(), payloads


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


class RecordingDashboardRunner:
    def __init__(self):
        self.calls = []

    def __call__(self, **kwargs):
        self.calls.append(kwargs)


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
