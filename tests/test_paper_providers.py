from datetime import datetime, timezone
from decimal import Decimal
from io import BytesIO
import json
from pathlib import Path
import sys
import unittest
from urllib.error import URLError

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from crypto_market_agents.trading.decision import (
    OllamaDecisionAgent,
    ReferenceDecisionAgent,
    parse_decision,
)
from crypto_market_agents.trading.market import BitvavoMarketData, MarketDataError, MockMarketData
from crypto_market_agents.trading.models import Portfolio
from crypto_market_agents.trading.cli import make_context

NOW = datetime(2026, 10, 5, tzinfo=timezone.utc)
RULES = {
    "market": "BTC-EUR",
    "status": "trading",
    "quote": "EUR",
    "orderTypes": ["market"],
    "quantityDecimals": 8,
    "minOrderInBaseAsset": "0.00001",
    "minOrderInQuoteAsset": "5",
}
BOOK = {"market": "BTC-EUR", "bid": "100", "ask": "100.1"}


class ProviderTests(unittest.TestCase):
    def fake_opener(self, payloads):
        calls = []

        def opener(request, timeout):
            calls.append(request)
            return BytesIO(json.dumps(payloads.pop(0)).encode())

        return calls, opener

    def test_bitvavo_get_only_with_market_rules_and_receipt_time(self):
        calls, opener = self.fake_opener([[RULES], BOOK])
        quote = BitvavoMarketData(opener=opener, now_provider=lambda: NOW).get_quote("BTC-EUR")
        self.assertEqual(quote.min_quantity, Decimal("0.00001"))
        self.assertEqual(quote.quantity_step, Decimal("0.00000001"))
        self.assertEqual(quote.timestamp, NOW)
        self.assertEqual([r.get_method() for r in calls], ["GET", "GET"])
        self.assertIn("/ticker/book?market=BTC-EUR", calls[-1].full_url)
        self.assertTrue(all(not r.has_header("Authorization") for r in calls))

    def test_bitvavo_rejects_bad_market_status_and_rules(self):
        for field, value in [
            ("status", "halted"),
            ("quote", "USD"),
            ("quantityDecimals", -1),
            ("quantityDecimals", True),
            ("quantityDecimals", 19),
            ("minOrderInQuoteAsset", "NaN"),
            ("minOrderInBaseAsset", "0"),
            ("orderTypes", []),
        ]:
            with self.subTest(field=field):
                _, opener = self.fake_opener([{**RULES, field: value}, BOOK])
                with self.assertRaises(MarketDataError):
                    BitvavoMarketData(opener=opener).get_quote("BTC-EUR")
        with self.assertRaises(MarketDataError):
            BitvavoMarketData().get_quote("BTC-USDT")

    def test_bitvavo_rejects_crossed_nan_and_missing_books(self):
        for book in (
            {**BOOK, "bid": "102"},
            {**BOOK, "ask": "NaN"},
            {**BOOK, "ask": True},
            {},
            [BOOK, BOOK],
        ):
            _, opener = self.fake_opener([RULES, book])
            with self.assertRaises(MarketDataError):
                BitvavoMarketData(opener=opener).get_quote("BTC-EUR")

    def test_network_failure_reports_no_raw_secret(self):
        def opener(*args, **kwargs):
            raise URLError("api_key=secret-placeholder")

        with self.assertRaises(MarketDataError) as error:
            BitvavoMarketData(opener=opener).get_quote("BTC-EUR")
        self.assertNotIn("secret-placeholder", str(error.exception))

    def test_reference_is_explicitly_not_ai_and_first_observation_holds(self):
        portfolio = Portfolio.create("BTC-EUR", "mock")
        quote = MockMarketData(now_provider=lambda: NOW).get_quote("BTC-EUR")
        decision = ReferenceDecisionAgent().decide(make_context(portfolio, quote))
        self.assertEqual((decision.provider, decision.action), ("rules", "hold"))

    def test_ollama_validates_schema_and_calls_only_localhost(self):
        proposal = {
            "action": "buy",
            "market": "BTC-EUR",
            "amount_eur": "10",
            "reason": "Test fictif",
        }
        calls, opener = self.fake_opener(
            [{"done": True, "message": {"content": json.dumps(proposal)}}]
        )
        decision = OllamaDecisionAgent("local-test", opener=opener).decide({"market": "BTC-EUR"})
        self.assertEqual(decision.amount_eur, Decimal("10"))
        self.assertEqual(calls[0].full_url, "http://127.0.0.1:11434/api/chat")
        payload = json.loads(calls[0].data)
        self.assertFalse(payload["stream"])
        self.assertNotIn("tools", payload)

    def test_ollama_unavailable_malformed_or_tool_request_means_hold(self):
        for response in (
            {},
            {"done": False},
            {"done": True, "message": {"content": "not-json"}},
            {"done": True, "message": {"tool_calls": [{"name": "buy"}]}},
            {"done": True, "message": {"content": "null"}},
        ):
            _, opener = self.fake_opener([response])
            self.assertEqual(
                OllamaDecisionAgent("local-test", opener=opener)
                .decide({"market": "BTC-EUR"})
                .action,
                "hold",
            )

        def offline(*args, **kwargs):
            raise URLError("offline")

        self.assertEqual(
            OllamaDecisionAgent("local-test", opener=offline).decide({"market": "BTC-EUR"}).action,
            "hold",
        )

    def test_interrupted_http_response_is_a_controlled_failure(self):
        from http.client import IncompleteRead

        def interrupted(*args, **kwargs):
            raise IncompleteRead(b"partial")

        with self.assertRaises(MarketDataError):
            BitvavoMarketData(opener=interrupted).get_quote("BTC-EUR")
        agent = OllamaDecisionAgent("local-test", opener=interrupted)
        decision = agent.decide({"market": "BTC-EUR"})
        self.assertEqual(decision.action, "hold")
        self.assertEqual(agent.last_error, "IncompleteRead")

    def test_cloud_model_names_are_not_accepted(self):
        with self.assertRaises(ValueError):
            OllamaDecisionAgent("test:cloud")

    def test_model_cannot_change_policy_market_or_exceed_capital(self):
        valid = {"action": "buy", "market": "BTC-EUR", "amount_eur": "10", "reason": "Test fictif"}
        for changes in (
            {"market": "ETH-EUR"},
            {"action": "withdraw"},
            {"amount_eur": "NaN"},
            {"amount_eur": "Infinity"},
            {"amount_eur": "51"},
            {"amount_eur": "-1"},
            {"amount_eur": 10},
            {"max_loss": "50"},
            {"action": "hold"},
        ):
            with self.subTest(changes=changes), self.assertRaises((ValueError, ArithmeticError)):
                parse_decision({**valid, **changes}, market="BTC-EUR", provider="ollama")


if __name__ == "__main__":
    unittest.main()
