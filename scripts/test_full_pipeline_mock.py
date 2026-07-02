"""Local smoke test for the full mock analysis pipeline."""

from pathlib import Path
import tempfile
import sys


PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from crypto_market_agents.agents.final_synthesis_agent import FinalSynthesisAgent
from crypto_market_agents.agents.news_sentiment_agent import NewsSentimentAgent
from crypto_market_agents.agents.onchain_fundamental_agent import OnchainFundamentalAgent
from crypto_market_agents.agents.price_market_agent import PriceMarketAgent
from crypto_market_agents.agents.volatility_risk_agent import VolatilityRiskAgent
from crypto_market_agents.reporting.report_renderer import (
    save_json_report,
    save_markdown_report,
)


def main() -> None:
    market_client = FakeMarketClient()
    news_client = FakeNewsClient()
    fundamental_client = FakeFundamentalClient()

    reports = [
        PriceMarketAgent(market_client).analyze(["bitcoin", "ethereum"]),
        VolatilityRiskAgent(market_client).analyze(["bitcoin", "ethereum"]),
        NewsSentimentAgent(news_client).analyze(["bitcoin", "ethereum"]),
        OnchainFundamentalAgent(fundamental_client).analyze(["uniswap", "aave"]),
    ]
    final_report = FinalSynthesisAgent().synthesize(reports)

    output_dir = Path(tempfile.mkdtemp(prefix="crypto-market-agents-"))
    markdown_path = output_dir / "final_report.md"
    json_path = output_dir / "final_report.json"
    save_markdown_report(final_report, str(markdown_path))
    save_json_report(final_report, str(json_path))

    print("Resume final:")
    print(final_report.market_summary)
    print(f"Risque global: {final_report.global_risk_level.value}")
    print(f"Confidence: {final_report.confidence:.2f}")
    print(f"Markdown: {markdown_path}")
    print(f"JSON: {json_path}")


class FakeMarketClient:
    base_url = "https://api.coingecko.com/api/v3"

    def get_coin_markets(self, coin_ids, **kwargs):
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

    def search_articles(self, query, language="en", page_size=10):
        return [
            {
                "title": "Bitcoin adoption grows",
                "description": "Institutional adoption keeps growing.",
                "content": "Integration and adoption remain visible.",
                "source": {"name": "Mock News"},
                "url": "https://example.test/bitcoin",
                "publishedAt": "2026-07-01T08:00:00Z",
            }
        ]


class FakeFundamentalClient:
    base_url = "https://api.llama.fi"

    def get_protocol(self, protocol_slug):
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
        return {"uniswap": 1_200_000_000, "aave": 900_000_000}.get(protocol_slug)

    def get_chains(self):
        return [{"name": "Ethereum", "tvl": 50_000_000_000}]

    def get_stablecoins(self):
        return {"peggedAssets": [{"name": "USDC"}]}

    def get_fees_overview(self):
        return {
            "protocols": [
                {"slug": "uniswap", "name": "Uniswap", "total24h": 200_000},
                {"slug": "aave", "name": "Aave", "total24h": 100_000},
            ]
        }


if __name__ == "__main__":
    main()
