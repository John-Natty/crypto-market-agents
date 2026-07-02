from pathlib import Path
import sys
import unittest


PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from crypto_market_agents.agents.price_market_agent import PriceMarketAgent
from crypto_market_agents.clients.coingecko_client import CoinGeckoNetworkError
from crypto_market_agents.schemas import AgentStatus, RiskLevel


class FakeCoinGeckoClient:
    base_url = "https://api.coingecko.com/api/v3"

    def __init__(self, market_data=None, error=None):
        self.market_data = market_data or []
        self.error = error
        self.calls = []

    def get_coin_markets(self, coin_ids, **kwargs):
        self.calls.append({"coin_ids": coin_ids, "kwargs": kwargs})
        if self.error:
            raise self.error
        return self.market_data


class PriceMarketAgentTests(unittest.TestCase):
    def test_analyze_normal_bitcoin_ethereum(self):
        client = FakeCoinGeckoClient(
            [
                market_asset(
                    "bitcoin",
                    "btc",
                    "Bitcoin",
                    rank=1,
                    current_price=100000,
                    change_24h=1.2,
                    change_7d=3.4,
                ),
                market_asset(
                    "ethereum",
                    "eth",
                    "Ethereum",
                    rank=2,
                    current_price=5000,
                    change_24h=-1.0,
                    change_7d=2.0,
                ),
            ]
        )

        report = PriceMarketAgent(client).analyze(["bitcoin", "ethereum"])

        self.assertEqual(report.agent_name, "price_market_agent")
        self.assertEqual(report.status, AgentStatus.SUCCESS)
        self.assertEqual(report.risk_level, RiskLevel.LOW)
        self.assertEqual(report.confidence, 0.95)
        self.assertTrue(any("market cap" in finding.title for finding in report.findings))
        self.assertEqual(client.calls[0]["coin_ids"], ("bitcoin", "ethereum"))

    def test_analyze_detects_strong_24h_rise(self):
        client = FakeCoinGeckoClient(
            [
                market_asset(
                    "bitcoin",
                    "btc",
                    "Bitcoin",
                    rank=1,
                    current_price=100000,
                    change_24h=12,
                    change_7d=16,
                ),
                market_asset(
                    "ethereum",
                    "eth",
                    "Ethereum",
                    rank=2,
                    current_price=5000,
                    change_24h=9,
                    change_7d=17,
                ),
            ]
        )

        report = PriceMarketAgent(client).analyze(["bitcoin", "ethereum"])

        self.assertEqual(report.risk_level, RiskLevel.HIGH)
        self.assertTrue(any(finding.title == "Forte hausse 24h" for finding in report.findings))

    def test_analyze_detects_strong_24h_drop(self):
        client = FakeCoinGeckoClient(
            [
                market_asset(
                    "solana",
                    "sol",
                    "Solana",
                    rank=6,
                    current_price=120,
                    change_24h=-12,
                    change_7d=-8,
                ),
                market_asset(
                    "ethereum",
                    "eth",
                    "Ethereum",
                    rank=2,
                    current_price=5000,
                    change_24h=-1,
                    change_7d=-2,
                ),
            ]
        )

        report = PriceMarketAgent(client).analyze(["solana", "ethereum"])

        self.assertEqual(report.risk_level, RiskLevel.MEDIUM)
        self.assertTrue(any(finding.title == "Forte baisse 24h" for finding in report.findings))

    def test_analyze_returns_partial_when_fields_are_missing(self):
        client = FakeCoinGeckoClient(
            [
                {
                    "id": "bitcoin",
                    "symbol": "btc",
                    "name": "Bitcoin",
                    "current_price": 100000,
                    "price_change_percentage_24h": 2,
                }
            ]
        )

        report = PriceMarketAgent(client).analyze(["bitcoin", "ethereum"])

        self.assertEqual(report.status, AgentStatus.PARTIAL)
        self.assertLess(report.confidence, 0.95)
        self.assertTrue(report.errors)
        self.assertIn("ethereum", report.data["missing_coin_ids"])
        self.assertIn("bitcoin", report.data["missing_fields"])

    def test_analyze_returns_failed_when_client_errors(self):
        client = FakeCoinGeckoClient(error=CoinGeckoNetworkError("network down"))

        report = PriceMarketAgent(client).analyze(["bitcoin", "ethereum"])

        self.assertEqual(report.status, AgentStatus.FAILED)
        self.assertEqual(report.confidence, 0.0)
        self.assertEqual(report.findings, ())
        self.assertTrue(report.errors)


def market_asset(
    asset_id,
    symbol,
    name,
    *,
    rank,
    current_price,
    change_24h,
    change_7d,
):
    return {
        "id": asset_id,
        "symbol": symbol,
        "name": name,
        "current_price": current_price,
        "market_cap": current_price * 20_000_000,
        "market_cap_rank": rank,
        "total_volume": current_price * 1_000_000,
        "price_change_percentage_1h_in_currency": 0.5,
        "price_change_percentage_24h": change_24h,
        "price_change_percentage_7d_in_currency": change_7d,
        "high_24h": current_price * 1.05,
        "low_24h": current_price * 0.95,
        "last_updated": "2026-06-30T10:00:00Z",
    }


if __name__ == "__main__":
    unittest.main()
