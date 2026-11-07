from contextlib import redirect_stdout
from datetime import datetime
from io import StringIO
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from crypto_market_agents.cli import main
from crypto_market_agents.config import load_config
from crypto_market_agents.dashboard import discover_reports, read_report_file
from crypto_market_agents.file_utils import atomic_write_text, reserve_report_paths
from crypto_market_agents.orchestrator import CryptoMarketOrchestrator
from crypto_market_agents.schemas import AgentReport, AgentStatus, RiskLevel


class RecordingAgent:
    def __init__(self, name):
        self.name = name
        self.calls = []

    def analyze(self, *args, **kwargs):
        self.calls.append(kwargs)
        return AgentReport(
            agent_name=self.name,
            status=AgentStatus.SUCCESS,
            summary="Test",
            risk_level=RiskLevel.LOW,
            confidence=0.8,
        )


class RuntimeReliabilityTests(unittest.TestCase):
    def test_orchestrator_honors_config_and_preserves_same_minute_reports(self):
        with tempfile.TemporaryDirectory() as tmp:
            output = Path(tmp) / "configured"
            env = Path(tmp) / "config.txt"
            env.write_text(
                f"BASE_CURRENCY=eur\nNEWS_LANGUAGE=fr\nREPORT_OUTPUT_DIR={output}\nWHATSAPP_ENABLED=false\n"
            )
            agents = {
                name: RecordingAgent(name)
                for name in (
                    "price_market_agent",
                    "volatility_risk_agent",
                    "news_sentiment_agent",
                    "onchain_fundamental_agent",
                )
            }
            orchestrator = CryptoMarketOrchestrator(
                config=load_config(env, include_os_environ=False),
                now_provider=lambda: datetime(2026, 10, 5, 12, 34),
                **agents,
            )
            orchestrator.run_full_analysis(notify_whatsapp=False)
            first = orchestrator.last_run.json_path
            contents = first.read_bytes()
            orchestrator.run_full_analysis(notify_whatsapp=False)
            self.assertNotEqual(first, orchestrator.last_run.json_path)
            self.assertEqual(first.read_bytes(), contents)
            self.assertEqual(len(list(output.glob("*.json"))), 2)
            self.assertEqual(agents["price_market_agent"].calls[0]["vs_currency"], "eur")
            self.assertEqual(agents["news_sentiment_agent"].calls[0]["language"], "fr")

    def test_cli_omitted_values_allow_configuration_fallback(self):
        from test_cli import RecordingFactory

        factory = RecordingFactory()
        with redirect_stdout(StringIO()):
            main(["report", "--no-whatsapp"], orchestrator_factory=factory)
        kwargs = factory.instances[0].run_kwargs
        self.assertIsNone(kwargs["vs_currency"])
        self.assertIsNone(kwargs["news_language"])
        self.assertIsNone(kwargs["output_dir"])

    def test_corrupt_report_and_external_symlink_do_not_hide_valid_report(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            reports = root / "reports"
            reports.mkdir()
            (reports / "good.json").write_text('{"title":"Good"}')
            (reports / "broken.json").write_text("{")
            (root / "outside.json").write_text('{"title":"Outside"}')
            (reports / "link.json").symlink_to(root / "outside.json")
            with self.assertLogs("crypto_market_agents.dashboard", level="WARNING"):
                summaries = discover_reports(reports)
            self.assertEqual([s.display_name for s in summaries], ["Good"])

    def test_redaction_preserves_json_structure(self):
        with tempfile.TemporaryDirectory() as tmp:
            payload = {
                "market_summary": "api_key=synthetic-example",
                "nested": {"token": "secret-example"},
            }
            (Path(tmp) / "report.json").write_text(json.dumps(payload))
            _, content = read_report_file(tmp, "report.json")
            clean = json.loads(content)
            self.assertIn("market_summary", clean)
            self.assertNotIn("synthetic-example", content.decode())
            self.assertNotIn("secret-example", content.decode())

    def test_interrupted_atomic_write_preserves_old_content(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "report.json"
            path.write_text("old")
            with patch(
                "crypto_market_agents.file_utils.os.replace", side_effect=OSError("disk error")
            ):
                with self.assertRaises(OSError):
                    atomic_write_text(path, "new")
            self.assertEqual(path.read_text(), "old")
            self.assertEqual(list(Path(tmp).iterdir()), [path])

    def test_reserved_bundle_is_not_reused_before_write(self):
        with tempfile.TemporaryDirectory() as tmp:
            with reserve_report_paths(tmp, "report") as first:
                with reserve_report_paths(tmp, "report") as second:
                    self.assertNotEqual(first, second)
            self.assertEqual(list(Path(tmp).iterdir()), [])

    def test_scheduler_retries_next_run_after_export_error(self):
        from crypto_market_agents.cli import _generate_report

        calls = 0

        def fail_once(*args):
            nonlocal calls
            calls += 1
            if calls == 1:
                raise OSError("temporary storage failure")
            return _generate_report(*args)

        with (
            tempfile.TemporaryDirectory() as tmp,
            patch("crypto_market_agents.cli._generate_report", side_effect=fail_once),
            redirect_stdout(StringIO()),
        ):
            code = main(
                [
                    "schedule",
                    "--mock",
                    "--runs",
                    "2",
                    "--interval-minutes",
                    "1",
                    "--output-dir",
                    tmp,
                ],
                sleep_func=lambda seconds: None,
            )
            self.assertEqual(len(list(Path(tmp).glob("*.json"))), 1)
        self.assertEqual(code, 1)
        self.assertEqual(calls, 2)


if __name__ == "__main__":
    unittest.main()
