"""Cross-component regressions for malformed upstream data and stale report context."""

from datetime import datetime, timedelta, timezone
from decimal import Decimal
from io import BytesIO
import json
from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from crypto_market_agents.trading.cli import _analysis_context
from crypto_market_agents.trading.decision import parse_decision
from crypto_market_agents.trading.market import BitvavoMarketData, MarketDataError
from crypto_market_agents.trading.models import TradingError

NOW = datetime(2026, 10, 5, 12, tzinfo=timezone.utc)


def agent_report(name, *, generated_at=NOW):
    return {
        "agent_name": name,
        "generated_at": generated_at.isoformat(),
        "status": "success",
        "risk_level": "low",
        "confidence": 0.9,
    }


class PaperIntegrationReviewTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.report = Path(self.temp.name) / "analysis.json"
        self.payload = {
            "generated_at": NOW.isoformat(),
            "global_risk_level": "low",
            "confidence": 0.9,
            "warnings": [],
            "agent_reports": [
                agent_report(name)
                for name in (
                    "price_market_agent",
                    "volatility_risk_agent",
                    "news_sentiment_agent",
                    "onchain_fundamental_agent",
                )
            ],
        }

    def assert_report_cannot_authorize_buy(self, payload):
        self.report.write_text(json.dumps(payload))
        try:
            _, risk_off = _analysis_context(self.report, NOW)
        except TradingError:
            return
        self.assertTrue(risk_off, "Incomplete/stale analysis must not authorize purchases")

    def test_non_string_report_timestamp_fails_with_controlled_error(self):
        for value in (None, 123, [], {}):
            self.report.write_text(json.dumps({**self.payload, "generated_at": value}))
            with self.subTest(value=value), self.assertRaises(TradingError):
                _analysis_context(self.report, NOW)

    def test_invalid_warnings_fail_with_controlled_error(self):
        for value in (None, 123, {}):
            self.report.write_text(json.dumps({**self.payload, "warnings": value}))
            with self.subTest(value=value), self.assertRaises(TradingError):
                _analysis_context(self.report, NOW)

    def test_incomplete_anonymous_agent_report_cannot_authorize_buy(self):
        self.assert_report_cannot_authorize_buy(
            {**self.payload, "agent_reports": [{"status": "success"}]}
        )

    def test_fresh_synthesis_cannot_hide_stale_agent_reports(self):
        old = NOW - timedelta(days=365)
        payload = {
            **self.payload,
            "agent_reports": [
                agent_report(name, generated_at=old)
                for name in (
                    "price_market_agent",
                    "volatility_risk_agent",
                    "news_sentiment_agent",
                    "onchain_fundamental_agent",
                )
            ],
        }
        self.assert_report_cannot_authorize_buy(payload)

    def test_wrong_type_order_types_produces_market_error(self):
        for value in (None, 123, {}, "market"):
            rules = {
                "market": "BTC-EUR",
                "status": "trading",
                "quote": "EUR",
                "orderTypes": value,
                "quantityDecimals": 8,
                "minOrderInBaseAsset": "0.00001",
                "minOrderInQuoteAsset": "5",
            }
            responses = [rules, {"market": "BTC-EUR", "bid": "100", "ask": "100.1"}]

            def opener(request, timeout):
                return BytesIO(json.dumps(responses.pop(0)).encode())

            with self.subTest(value=value), self.assertRaises(MarketDataError):
                BitvavoMarketData(opener=opener, now_provider=lambda: NOW).get_quote("BTC-EUR")

    def test_eighteen_decimal_lots_roundtrip_without_overspending(self):
        from crypto_market_agents.trading import Portfolio, Quote, Decision, PaperEngine

        portfolio = Portfolio.create("TEST-EUR", "mock")
        quote = Quote(
            "TEST-EUR",
            Decimal("0.123456789012345678"),
            Decimal("0.123456789012345679"),
            NOW,
            quantity_step=Decimal("1e-18"),
        )
        result = PaperEngine().step(
            portfolio,
            Decision("buy", "TEST-EUR", Decimal("10"), "precision check", "test"),
            quote,
            "precise",
            now=NOW,
        )
        self.assertEqual(result["status"], "executed", result["reason"])
        self.assertGreaterEqual(portfolio.cash, Decimal("40"))
        restored = Portfolio.from_dict(portfolio.to_dict())
        self.assertEqual(restored.equity, portfolio.equity)
        self.assertEqual(restored.quantity, portfolio.quantity)

    def test_model_amount_must_match_advertised_decimal_schema(self):
        base = {"action": "buy", "market": "BTC-EUR", "amount_eur": "10", "reason": "simulation"}
        for value in ("1_0", "1e1", "+10", " 10", ".5"):
            with self.subTest(value=value), self.assertRaises(ValueError):
                parse_decision({**base, "amount_eur": value}, market="BTC-EUR", provider="ollama")
        self.assertEqual(
            parse_decision(base, market="BTC-EUR", provider="ollama").amount_eur, Decimal("10")
        )


if __name__ == "__main__":
    unittest.main()
