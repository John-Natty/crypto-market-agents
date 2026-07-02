from pathlib import Path
import sys
import unittest


PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from crypto_market_agents.agents.volatility_risk_agent import VolatilityRiskAgent
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


class VolatilityRiskAgentTests(unittest.TestCase):
    def test_analyze_stable_market(self):
        client = FakeCoinGeckoClient(
            [
                market_asset(
                    "bitcoin",
                    "btc",
                    "Bitcoin",
                    current_price=100000,
                    high_24h=101000,
                    low_24h=99000,
                    change_1h=0.3,
                    change_24h=1.2,
                    change_7d=3.0,
                    volume_to_market_cap=0.05,
                ),
                market_asset(
                    "ethereum",
                    "eth",
                    "Ethereum",
                    current_price=5000,
                    high_24h=5050,
                    low_24h=4950,
                    change_1h=-0.4,
                    change_24h=-1.1,
                    change_7d=2.0,
                    volume_to_market_cap=0.04,
                ),
            ]
        )

        report = VolatilityRiskAgent(client).analyze(["bitcoin", "ethereum"])

        self.assertEqual(report.agent_name, "volatility_risk_agent")
        self.assertEqual(report.status, AgentStatus.SUCCESS)
        self.assertEqual(report.risk_level, RiskLevel.LOW)
        self.assertEqual(report.confidence, 0.95)
        self.assertEqual(report.findings, ())
        self.assertEqual(client.calls[0]["coin_ids"], ("bitcoin", "ethereum"))

    def test_analyze_detects_strong_1h_move(self):
        client = FakeCoinGeckoClient(
            [
                market_asset(
                    "bitcoin",
                    "btc",
                    "Bitcoin",
                    current_price=100000,
                    high_24h=102000,
                    low_24h=98000,
                    change_1h=5.5,
                    change_24h=6,
                    change_7d=8,
                    volume_to_market_cap=0.08,
                )
            ]
        )

        report = VolatilityRiskAgent(client).analyze(["bitcoin"])

        self.assertEqual(report.risk_level, RiskLevel.HIGH)
        self.assertTrue(
            any(finding.title == "Mouvement brutal 1h" for finding in report.findings)
        )

    def test_analyze_detects_high_24h_amplitude(self):
        client = FakeCoinGeckoClient(
            [
                market_asset(
                    "solana",
                    "sol",
                    "Solana",
                    current_price=100,
                    high_24h=110,
                    low_24h=98,
                    change_1h=1,
                    change_24h=4,
                    change_7d=8,
                    volume_to_market_cap=0.08,
                )
            ]
        )

        report = VolatilityRiskAgent(client).analyze(["solana"])

        self.assertEqual(report.risk_level, RiskLevel.MEDIUM)
        self.assertTrue(
            any(
                finding.title == "Volatilite 24h elevee"
                for finding in report.findings
            )
        )

    def test_analyze_detects_extreme_7d_variation(self):
        client = FakeCoinGeckoClient(
            [
                market_asset(
                    "ethereum",
                    "eth",
                    "Ethereum",
                    current_price=5000,
                    high_24h=5100,
                    low_24h=4900,
                    change_1h=1,
                    change_24h=3,
                    change_7d=-65,
                    volume_to_market_cap=0.06,
                )
            ]
        )

        report = VolatilityRiskAgent(client).analyze(["ethereum"])

        self.assertEqual(report.risk_level, RiskLevel.CRITICAL)
        self.assertTrue(
            any(finding.title == "Variation extreme 7j" for finding in report.findings)
        )

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

        report = VolatilityRiskAgent(client).analyze(["bitcoin", "ethereum"])

        self.assertEqual(report.status, AgentStatus.PARTIAL)
        self.assertLess(report.confidence, 0.95)
        self.assertIn("ethereum", report.data["missing_coin_ids"])
        self.assertIn("bitcoin", report.data["missing_fields"])
        self.assertTrue(
            any(
                finding.title == "Donnees insuffisantes pour evaluer le risque"
                for finding in report.findings
            )
        )

    def test_analyze_returns_failed_when_client_errors(self):
        client = FakeCoinGeckoClient(error=CoinGeckoNetworkError("network down"))

        report = VolatilityRiskAgent(client).analyze(["bitcoin", "ethereum"])

        self.assertEqual(report.status, AgentStatus.FAILED)
        self.assertEqual(report.confidence, 0.0)
        self.assertEqual(report.findings, ())
        self.assertTrue(report.errors)


def market_asset(
    asset_id,
    symbol,
    name,
    *,
    current_price,
    high_24h,
    low_24h,
    change_1h,
    change_24h,
    change_7d,
    volume_to_market_cap,
):
    market_cap = current_price * 20_000_000
    return {
        "id": asset_id,
        "symbol": symbol,
        "name": name,
        "current_price": current_price,
        "high_24h": high_24h,
        "low_24h": low_24h,
        "price_change_percentage_1h_in_currency": change_1h,
        "price_change_percentage_24h": change_24h,
        "price_change_percentage_7d_in_currency": change_7d,
        "total_volume": market_cap * volume_to_market_cap,
        "market_cap": market_cap,
        "last_updated": "2026-07-01T10:00:00Z",
    }


if __name__ == "__main__":
    unittest.main()

