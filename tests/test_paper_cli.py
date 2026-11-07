from contextlib import redirect_stdout
from datetime import datetime, timezone
from io import StringIO
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from crypto_market_agents.cli import main, build_parser
from crypto_market_agents.dashboard import discover_reports
from crypto_market_agents.trading.cli import run_paper
from crypto_market_agents.trading.store import PaperStore


class PaperCLITests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.state = str(self.root / "paper.json")
        self.reports = str(self.root / "reports")

    def call(self, *args):
        output = StringIO()
        with redirect_stdout(output):
            code = main(["paper", *args, "--state", self.state])
        return code, output.getvalue()

    def test_demo_holds_buys_sells_and_restarts_without_network(self):
        with patch(
            "urllib.request.OpenerDirector.open", side_effect=AssertionError("network forbidden")
        ):
            self.assertEqual(self.call("init")[0], 0)
            code, output = self.call(
                "run",
                "--mock",
                "--runs",
                "3",
                "--interval-seconds",
                "0",
                "--output-dir",
                self.reports,
            )
        self.assertEqual(code, 0, output)
        portfolio = PaperStore(self.state).load()
        self.assertEqual([event["action"] for event in portfolio.ledger], ["hold", "buy", "sell"])
        self.assertTrue(all(event["status"] in {"held", "executed"} for event in portfolio.ledger))
        self.assertGreater(portfolio.fees_paid, 0)
        self.assertLess(portfolio.equity, portfolio.initial_cash)
        self.assertEqual(len(discover_reports(self.reports)), 1)
        code, status = self.call("status", "--json")
        self.assertEqual(code, 0)
        self.assertEqual(json.loads(status)["fills"], 2)
        self.assertEqual(self.call("init")[0], 1)
        self.assertEqual(len(PaperStore(self.state).load().ledger), 3)

    def test_retry_id_does_not_call_model_or_change_balance(self):
        self.call("init")
        argv = ["run", "--decision-id", "retry-me", "--output-dir", self.reports]
        self.assertEqual(self.call(*argv)[0], 0)
        original = Path(self.state).read_text()
        with patch(
            "crypto_market_agents.trading.market.MockMarketData.get_quote",
            side_effect=AssertionError("must not fetch"),
        ):
            code, output = self.call(*argv)
        self.assertEqual(code, 0)
        self.assertIn("deja traite", output)
        self.assertEqual(Path(self.state).read_text(), original)

    def test_no_implicit_init_or_source_switch(self):
        self.assertEqual(self.call("run", "--output-dir", self.reports)[0], 1)
        self.call("init")
        self.assertEqual(
            self.call("run", "--source", "bitvavo", "--output-dir", self.reports)[0], 1
        )
        self.assertEqual(
            self.call("run", "--provider", "ollama", "--output-dir", self.reports)[0], 1
        )

    def test_stale_report_cannot_authorize_a_fill(self):
        self.call("init")
        report = self.root / "analysis.json"
        report.write_text(
            json.dumps(
                {
                    "generated_at": "2000-01-01T00:00:00Z",
                    "confidence": 0.9,
                    "agent_reports": [{"status": "success"}],
                }
            )
        )
        code, output = self.call(
            "run", "--analysis-report", str(report), "--output-dir", self.reports
        )
        self.assertEqual(code, 1, output)
        self.assertEqual(PaperStore(self.state).load().ledger, [])

    def test_market_outage_then_recovery_continues_schedule(self):
        self.call("init")
        args = build_parser().parse_args(
            [
                "paper",
                "run",
                "--state",
                self.state,
                "--runs",
                "2",
                "--interval-seconds",
                "0",
                "--output-dir",
                self.reports,
            ]
        )
        from crypto_market_agents.trading.market import MockMarketData, MarketDataError

        class FlakyMarket(MockMarketData):
            calls = 0

            def get_quote(self, market):
                self.calls += 1
                if self.calls == 1:
                    raise MarketDataError("temporary outage")
                return super().get_quote(market)

        with redirect_stdout(StringIO()):
            code = run_paper(
                args, market_factory=lambda p: FlakyMarket(), sleep_func=lambda s: None
            )
        self.assertEqual(code, 1)
        self.assertEqual(len(PaperStore(self.state).load().ledger), 1)

    def test_unavailable_model_records_hold_and_returns_failure_status(self):
        self.call("init")
        args = build_parser().parse_args(
            [
                "paper",
                "run",
                "--state",
                self.state,
                "--provider",
                "ollama",
                "--model",
                "local-test",
                "--output-dir",
                self.reports,
            ]
        )
        from crypto_market_agents.trading.decision import OllamaDecisionAgent

        def offline(*args, **kwargs):
            raise OSError("offline")

        with redirect_stdout(StringIO()):
            code = run_paper(
                args, decision_factory=lambda: OllamaDecisionAgent("local-test", opener=offline)
            )
        self.assertEqual(code, 1)
        self.assertEqual(PaperStore(self.state).load().ledger[-1]["action"], "hold")
        self.assertEqual(PaperStore(self.state).load().cash, 50)

    def test_high_risk_report_blocks_model_buy(self):
        self.call("init")
        report = self.root / "analysis.json"
        report.write_text(
            json.dumps(
                {
                    "generated_at": datetime.now(timezone.utc).isoformat(),
                    "confidence": 0.9,
                    "agent_reports": [{"status": "success"}],
                    "global_risk_level": "critical",
                }
            )
        )
        args = build_parser().parse_args(
            [
                "paper",
                "run",
                "--state",
                self.state,
                "--analysis-report",
                str(report),
                "--output-dir",
                self.reports,
            ]
        )
        from decimal import Decimal
        from crypto_market_agents.trading.models import Decision

        class BuyAgent:
            def decide(self, context):
                return Decision("buy", "BTC-EUR", Decimal("10"), "ignored risk", "test")

        with redirect_stdout(StringIO()):
            self.assertEqual(run_paper(args, decision_factory=BuyAgent), 0)
        self.assertEqual(PaperStore(self.state).load().quantity, 0)


if __name__ == "__main__":
    unittest.main()
