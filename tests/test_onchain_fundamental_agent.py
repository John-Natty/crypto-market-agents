from pathlib import Path
import sys
import unittest


PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from crypto_market_agents.agents.onchain_fundamental_agent import (
    OnchainFundamentalAgent,
)
from crypto_market_agents.clients.defillama_client import (
    DefiLlamaAPIError,
    DefiLlamaNetworkError,
)
from crypto_market_agents.schemas import AgentStatus, RiskLevel


class FakeDefiLlamaClient:
    base_url = "https://api.llama.fi"

    def __init__(self, protocols=None, tvls=None, error=None):
        self.protocols = protocols or {}
        self.tvls = tvls or {}
        self.error = error

    def get_protocol(self, protocol_slug):
        if self.error:
            raise self.error
        protocol = self.protocols.get(protocol_slug)
        if protocol is None:
            raise DefiLlamaAPIError(
                endpoint=f"protocol/{protocol_slug}",
                status_code=404,
                body="not found",
            )
        return protocol

    def get_current_tvl(self, protocol_slug):
        if self.error:
            raise self.error
        return self.tvls.get(protocol_slug)

    def get_chains(self):
        return [
            {"name": "Ethereum", "tvl": 50_000_000_000},
            {"name": "Arbitrum", "tvl": 3_000_000_000},
        ]

    def get_stablecoins(self):
        return {
            "peggedAssets": [{"name": "USDT"}, {"name": "USDC"}],
            "totalCirculating": {"peggedUSD": 150_000_000_000},
        }

    def get_fees_overview(self):
        return {
            "protocols": [
                {"slug": "uniswap", "name": "Uniswap", "total24h": 100_000},
                {"slug": "aave", "name": "Aave", "total24h": 50_000},
            ]
        }


class OnchainFundamentalAgentTests(unittest.TestCase):
    def test_analyze_major_protocols(self):
        client = FakeDefiLlamaClient(
            protocols={
                "uniswap": protocol("Uniswap", "uniswap", 1_200_000_000),
                "aave": protocol("Aave", "aave", 900_000_000),
            },
            tvls={"uniswap": 1_200_000_000, "aave": 900_000_000},
        )

        report = OnchainFundamentalAgent(client).analyze(["uniswap", "aave"])

        self.assertEqual(report.agent_name, "onchain_fundamental_agent")
        self.assertEqual(report.status, AgentStatus.SUCCESS)
        self.assertEqual(report.risk_level, RiskLevel.LOW)
        self.assertGreaterEqual(report.confidence, 0.90)
        self.assertTrue(any(finding.title == "TVL elevee" for finding in report.findings))
        self.assertTrue(
            any(
                finding.title == "Activite fees/revenue significative"
                for finding in report.findings
            )
        )

    def test_analyze_low_tvl_protocol(self):
        client = FakeDefiLlamaClient(
            protocols={"small": protocol("Small Protocol", "small", 20_000_000)},
            tvls={"small": 20_000_000},
        )

        report = OnchainFundamentalAgent(client).analyze(["small"])

        self.assertEqual(report.risk_level, RiskLevel.HIGH)
        self.assertTrue(any(finding.title == "TVL faible" for finding in report.findings))

    def test_analyze_protocol_not_found(self):
        client = FakeDefiLlamaClient(protocols={}, tvls={})

        report = OnchainFundamentalAgent(client).analyze(["missing"])

        self.assertEqual(report.status, AgentStatus.PARTIAL)
        self.assertEqual(report.risk_level, RiskLevel.CRITICAL)
        self.assertTrue(
            any(finding.title == "Protocole introuvable" for finding in report.findings)
        )

    def test_analyze_missing_data(self):
        client = FakeDefiLlamaClient(
            protocols={"partial": {"name": "Partial", "slug": "partial"}},
            tvls={"partial": None},
        )

        report = OnchainFundamentalAgent(client).analyze(["partial"])

        self.assertEqual(report.status, AgentStatus.PARTIAL)
        self.assertEqual(report.risk_level, RiskLevel.CRITICAL)
        self.assertLess(report.confidence, 0.95)
        self.assertTrue(
            any(finding.title == "Donnees insuffisantes" for finding in report.findings)
        )

    def test_analyze_returns_failed_when_client_errors(self):
        client = FakeDefiLlamaClient(error=DefiLlamaNetworkError("network down"))

        report = OnchainFundamentalAgent(client).analyze(["aave"])

        self.assertEqual(report.status, AgentStatus.FAILED)
        self.assertEqual(report.risk_level, RiskLevel.CRITICAL)
        self.assertEqual(report.confidence, 0.0)
        self.assertTrue(report.errors)


def protocol(name, slug, latest_tvl):
    return {
        "name": name,
        "slug": slug,
        "category": "Dexes",
        "chains": ["Ethereum", "Arbitrum", "Optimism"],
        "tvl": [
            {"date": 1, "totalLiquidityUSD": latest_tvl * 0.95},
            {"date": 2, "totalLiquidityUSD": latest_tvl},
        ],
    }


if __name__ == "__main__":
    unittest.main()
