from pathlib import Path
import json
import sys
import tempfile
import unittest


PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from crypto_market_agents.agents.final_synthesis_agent import FinalSynthesisAgent
from crypto_market_agents.agents.news_sentiment_agent import NewsSentimentAgent
from crypto_market_agents.agents.onchain_fundamental_agent import OnchainFundamentalAgent
from crypto_market_agents.agents.price_market_agent import PriceMarketAgent
from crypto_market_agents.agents.volatility_risk_agent import VolatilityRiskAgent
from crypto_market_agents.config import load_config
from crypto_market_agents.notifications.whatsapp_client import WhatsAppClient
from crypto_market_agents.notifications.whatsapp_notifier import WhatsAppNotifier
from crypto_market_agents.reporting.report_renderer import (
    render_final_report_html,
    render_final_report_json,
    render_final_report_markdown,
    save_html_report,
    save_json_report,
    save_markdown_report,
)
from crypto_market_agents.schemas import AgentStatus, RiskLevel


class FullAnalysisFlowTests(unittest.TestCase):
    def test_full_analysis_flow_with_mocks_and_no_external_calls(self):
        market_client = FakeMarketClient()
        news_client = FakeNewsClient()
        fundamental_client = FakeFundamentalClient()

        price_report = PriceMarketAgent(market_client).analyze(["bitcoin", "ethereum"])
        volatility_report = VolatilityRiskAgent(market_client).analyze(["bitcoin", "ethereum"])
        news_report = NewsSentimentAgent(news_client).analyze(["bitcoin", "ethereum"])
        onchain_report = OnchainFundamentalAgent(fundamental_client).analyze(["uniswap", "aave"])

        reports = (
            price_report,
            volatility_report,
            news_report,
            onchain_report,
        )
        final_report = FinalSynthesisAgent().synthesize(reports)
        markdown = render_final_report_markdown(final_report)
        html = render_final_report_html(final_report)
        json_payload = json.loads(render_final_report_json(final_report))

        self.assertTrue(all(report.status is AgentStatus.SUCCESS for report in reports))
        self.assertEqual(len(final_report.agent_reports), 4)
        self.assertIn(final_report.global_risk_level, set(RiskLevel))
        self.assertGreater(final_report.confidence, 0)
        self.assertIn("Analyse informative uniquement", markdown)
        self.assertIn("Analyse informative uniquement", html)
        self.assertIn("Risque", html)
        self.assertEqual(json_payload["global_risk_level"], final_report.global_risk_level.value)
        self.assertIn("confidence", json_payload)
        self.assertIn("warnings", json_payload)
        self.assertIn("agent_reports", json_payload)
        self.assertEqual(
            len(final_report.assets_to_watch),
            len(set(final_report.assets_to_watch)),
        )
        self.assertIn("btc", final_report.assets_to_watch)
        self.assertIn("uniswap", final_report.assets_to_watch)
        self.assertGreaterEqual(len(market_client.calls), 2)
        self.assertEqual(news_client.calls[0]["query"], "bitcoin OR ethereum")
        self.assertIn("uniswap", fundamental_client.protocol_calls)

        with tempfile.TemporaryDirectory() as temp_dir:
            markdown_path = Path(temp_dir) / "final.md"
            json_path = Path(temp_dir) / "final.json"
            html_path = Path(temp_dir) / "final.html"
            save_markdown_report(final_report, str(markdown_path))
            save_json_report(final_report, str(json_path))
            save_html_report(final_report, str(html_path))

            self.assertTrue(markdown_path.exists())
            self.assertTrue(json_path.exists())
            self.assertTrue(html_path.exists())

        with tempfile.TemporaryDirectory() as temp_dir:
            env_path = Path(temp_dir) / ".env"
            env_path.write_text("", encoding="utf-8")
            config = load_config(env_path, include_os_environ=False)

        calls = []
        whatsapp_client = WhatsAppClient(
            enabled=config.whatsapp.enabled,
            access_token=config.whatsapp.access_token,
            phone_number_id=config.whatsapp.phone_number_id,
            to_number=config.whatsapp.to_number,
            graph_api_version=config.whatsapp.graph_api_version,
            timeout_seconds=config.whatsapp.timeout_seconds,
            opener=forbidden_opener(calls),
        )
        notification = WhatsAppNotifier(whatsapp_client).send_final_report_summary(final_report)

        self.assertFalse(config.whatsapp.enabled)
        self.assertFalse(notification["sent"])
        self.assertEqual(notification["status"], "disabled")
        self.assertEqual(calls, [])


class FakeMarketClient:
    base_url = "https://api.coingecko.com/api/v3"

    def __init__(self):
        self.calls = []

    def get_coin_markets(self, coin_ids, **kwargs):
        self.calls.append({"coin_ids": tuple(coin_ids), "kwargs": kwargs})
        return [
            {
                "id": "bitcoin",
                "symbol": "btc",
                "name": "Bitcoin",
                "current_price": 100000,
                "market_cap": 2_000_000_000_000,
                "market_cap_rank": 1,
                "total_volume": 180_000_000_000,
                "price_change_percentage_1h_in_currency": 1.2,
                "price_change_percentage_24h": 6.5,
                "price_change_percentage_7d_in_currency": 14.0,
                "high_24h": 102000,
                "low_24h": 96000,
                "last_updated": "2026-07-01T12:00:00Z",
            },
            {
                "id": "ethereum",
                "symbol": "eth",
                "name": "Ethereum",
                "current_price": 5000,
                "market_cap": 600_000_000_000,
                "market_cap_rank": 2,
                "total_volume": 80_000_000_000,
                "price_change_percentage_1h_in_currency": -0.8,
                "price_change_percentage_24h": 3.0,
                "price_change_percentage_7d_in_currency": 9.5,
                "high_24h": 5100,
                "low_24h": 4800,
                "last_updated": "2026-07-01T12:00:00Z",
            },
        ]


class FakeNewsClient:
    base_url = "https://newsapi.org/v2"
    default_query = "crypto OR bitcoin OR ethereum OR blockchain"
    max_articles = 10

    def __init__(self):
        self.calls = []

    def search_articles(self, query, language="en", page_size=10):
        self.calls.append({"query": query, "language": language, "page_size": page_size})
        return [
            {
                "title": "Bitcoin adoption grows with institutional integration",
                "description": "Institutional adoption keeps growing.",
                "content": "Bitcoin adoption and integration accelerate.",
                "source": {"name": "Mock News"},
                "url": "https://example.test/bitcoin-adoption",
                "publishedAt": "2026-07-01T08:00:00Z",
            },
            {
                "title": "Crypto liquidation risk remains monitored",
                "description": "Market desks watch liquidation risk.",
                "content": "No exploit reported, but liquidation remains a risk.",
                "source": {"name": "Mock News"},
                "url": "https://example.test/liquidation-risk",
                "publishedAt": "2026-07-01T09:00:00Z",
            },
        ]


class FakeFundamentalClient:
    base_url = "https://api.llama.fi"

    def __init__(self):
        self.protocol_calls = []

    def get_protocol(self, protocol_slug):
        self.protocol_calls.append(protocol_slug)
        return {
            "name": protocol_slug.title(),
            "slug": protocol_slug,
            "category": "Dexes",
            "chains": ["Ethereum", "Arbitrum", "Optimism"],
            "tvl": [
                {"date": 1, "totalLiquidityUSD": 1_000_000_000},
                {"date": 2, "totalLiquidityUSD": 1_200_000_000},
            ],
        }

    def get_current_tvl(self, protocol_slug):
        values = {"uniswap": 1_200_000_000, "aave": 900_000_000}
        return values.get(protocol_slug, 100_000_000)

    def get_chains(self):
        return [{"name": "Ethereum", "tvl": 50_000_000_000}]

    def get_stablecoins(self):
        return {"peggedAssets": [{"name": "USDC"}], "totalCirculating": {"peggedUSD": 1}}

    def get_fees_overview(self):
        return {
            "protocols": [
                {"slug": "uniswap", "name": "Uniswap", "total24h": 200_000},
                {"slug": "aave", "name": "Aave", "total24h": 100_000},
            ]
        }


def forbidden_opener(calls):
    def opener(request, timeout):
        calls.append(request)
        raise AssertionError("External WhatsApp call must not happen in tests.")

    return opener


if __name__ == "__main__":
    unittest.main()
