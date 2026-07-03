from datetime import datetime
from pathlib import Path
import json
import sys
import tempfile
import unittest


PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from crypto_market_agents.agents.news_sentiment_agent import NewsSentimentAgent
from crypto_market_agents.agents.onchain_fundamental_agent import OnchainFundamentalAgent
from crypto_market_agents.agents.price_market_agent import PriceMarketAgent
from crypto_market_agents.agents.volatility_risk_agent import VolatilityRiskAgent
from crypto_market_agents.config import load_config
from crypto_market_agents.orchestrator import CryptoMarketOrchestrator
from crypto_market_agents.schemas import (
    AgentReport,
    AgentStatus,
    Finding,
    ImpactDirection,
    RiskLevel,
)


class OrchestratorTests(unittest.TestCase):
    def test_orchestrator_with_mocked_agents_saves_reports(self):
        output_dir = self.temp_path()
        orchestrator = CryptoMarketOrchestrator(
            config=self.make_config(),
            price_market_agent=StaticAgent("price_market_agent", RiskLevel.LOW),
            volatility_risk_agent=StaticAgent("volatility_risk_agent", RiskLevel.HIGH),
            news_sentiment_agent=StaticAgent("news_sentiment_agent", RiskLevel.LOW),
            onchain_fundamental_agent=StaticAgent(
                "onchain_fundamental_agent",
                RiskLevel.LOW,
            ),
            now_provider=lambda: datetime(2026, 7, 1, 12, 34),
        )

        final_report = orchestrator.run_full_analysis(
            coin_ids=["bitcoin", "ethereum"],
            protocol_slugs=["uniswap", "aave"],
            output_dir=output_dir,
        )

        run = orchestrator.last_run
        self.assertIsNotNone(run)
        self.assertEqual(final_report.global_risk_level, RiskLevel.HIGH)
        self.assertEqual(run.markdown_path.name, "report_2026-07-01_1234.md")
        self.assertEqual(run.json_path.name, "report_2026-07-01_1234.json")
        self.assertEqual(run.html_path.name, "report_2026-07-01_1234.html")
        self.assertTrue(run.markdown_path.exists())
        self.assertTrue(run.json_path.exists())
        self.assertTrue(run.html_path.exists())
        self.assertEqual(run.whatsapp_summary["status"], "disabled")
        self.assertEqual(json.loads(run.json_path.read_text())["global_risk_level"], "high")
        self.assertIn("Risque high", run.html_path.read_text(encoding="utf-8"))

    def test_orchestrator_real_agents_with_fake_clients_never_touch_external_apis(self):
        output_dir = self.temp_path()
        market_client = FakeMarketClient()
        news_client = FakeNewsClient()
        fundamental_client = FakeFundamentalClient()
        orchestrator = CryptoMarketOrchestrator(
            config=self.make_config(),
            price_market_agent=PriceMarketAgent(market_client),
            volatility_risk_agent=VolatilityRiskAgent(market_client),
            news_sentiment_agent=NewsSentimentAgent(news_client),
            onchain_fundamental_agent=OnchainFundamentalAgent(fundamental_client),
            now_provider=lambda: datetime(2026, 7, 1, 12, 34),
        )

        final_report = orchestrator.run_full_analysis(
            coin_ids=["bitcoin", "ethereum"],
            news_query="bitcoin OR ethereum",
            protocol_slugs=["uniswap", "aave"],
            output_dir=output_dir,
            notify_whatsapp=False,
        )

        self.assertEqual(len(orchestrator.last_run.agent_reports), 4)
        self.assertTrue(
            all(
                report.status is AgentStatus.SUCCESS
                for report in orchestrator.last_run.agent_reports
            )
        )
        self.assertGreater(final_report.confidence, 0)
        self.assertGreaterEqual(len(market_client.calls), 2)
        self.assertEqual(news_client.calls[0]["query"], "bitcoin OR ethereum")
        self.assertEqual(fundamental_client.protocol_calls, ["uniswap", "aave"])
        self.assertEqual(orchestrator.last_run.whatsapp_summary["status"], "skipped")

    def test_agent_exception_becomes_failed_report_and_flow_continues(self):
        output_dir = self.temp_path()
        orchestrator = CryptoMarketOrchestrator(
            config=self.make_config(),
            price_market_agent=RaisingAgent("boom"),
            volatility_risk_agent=StaticAgent("volatility_risk_agent", RiskLevel.LOW),
            news_sentiment_agent=StaticAgent("news_sentiment_agent", RiskLevel.LOW),
            onchain_fundamental_agent=StaticAgent("onchain_fundamental_agent", RiskLevel.LOW),
            now_provider=lambda: datetime(2026, 7, 1, 12, 34),
        )

        final_report = orchestrator.run_full_analysis(output_dir=output_dir)

        reports = orchestrator.last_run.agent_reports
        self.assertEqual(len(reports), 4)
        self.assertEqual(reports[0].agent_name, "price_market_agent")
        self.assertEqual(reports[0].status, AgentStatus.FAILED)
        self.assertIn("boom", reports[0].errors[0])
        self.assertTrue(orchestrator.last_run.json_path.exists())
        self.assertTrue(orchestrator.last_run.html_path.exists())
        self.assertGreaterEqual(final_report.confidence, 0)

    def test_whatsapp_disabled_does_not_call_notifier(self):
        orchestrator = CryptoMarketOrchestrator(
            config=self.make_config(whatsapp_enabled=False),
            price_market_agent=StaticAgent("price_market_agent", RiskLevel.LOW),
            volatility_risk_agent=StaticAgent("volatility_risk_agent", RiskLevel.LOW),
            news_sentiment_agent=StaticAgent("news_sentiment_agent", RiskLevel.LOW),
            onchain_fundamental_agent=StaticAgent("onchain_fundamental_agent", RiskLevel.LOW),
            whatsapp_notifier=FailingNotifier(),
        )

        orchestrator.run_full_analysis(output_dir=self.temp_path())

        self.assertEqual(orchestrator.last_run.whatsapp_summary["status"], "disabled")
        self.assertEqual(orchestrator.last_run.whatsapp_alert["status"], "disabled")

    def test_whatsapp_enabled_uses_mocked_notifier(self):
        notifier = RecordingNotifier()
        orchestrator = CryptoMarketOrchestrator(
            config=self.make_config(whatsapp_enabled=True),
            price_market_agent=StaticAgent("price_market_agent", RiskLevel.LOW),
            volatility_risk_agent=StaticAgent("volatility_risk_agent", RiskLevel.HIGH),
            news_sentiment_agent=StaticAgent("news_sentiment_agent", RiskLevel.LOW),
            onchain_fundamental_agent=StaticAgent("onchain_fundamental_agent", RiskLevel.LOW),
            whatsapp_notifier=notifier,
        )

        orchestrator.run_full_analysis(output_dir=self.temp_path())

        self.assertEqual(notifier.summary_calls, 1)
        self.assertEqual(notifier.alert_calls, 1)
        self.assertEqual(orchestrator.last_run.whatsapp_summary["status"], "sent")
        self.assertEqual(orchestrator.last_run.whatsapp_alert["status"], "sent")

    def make_config(self, *, whatsapp_enabled=False):
        temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(temp_dir.cleanup)
        env_path = Path(temp_dir.name) / ".env"
        if whatsapp_enabled:
            env_path.write_text(
                "\n".join(
                    [
                        "WHATSAPP_ENABLED=true",
                        "WHATSAPP_ACCESS_TOKEN=test-token",
                        "WHATSAPP_PHONE_NUMBER_ID=123",
                        "WHATSAPP_TO_NUMBER=33600000000",
                    ]
                ),
                encoding="utf-8",
            )
        else:
            env_path.write_text("WHATSAPP_ENABLED=false\n", encoding="utf-8")

        return load_config(env_path, include_os_environ=False)

    def temp_path(self) -> Path:
        temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(temp_dir.cleanup)
        return Path(temp_dir.name)


class StaticAgent:
    def __init__(self, agent_name, risk_level):
        self.agent_name = agent_name
        self.risk_level = risk_level
        self.calls = []

    def analyze(self, *args, **kwargs):
        self.calls.append({"args": args, "kwargs": kwargs})
        return AgentReport(
            agent_name=self.agent_name,
            status=AgentStatus.SUCCESS,
            summary=f"Rapport mock pour {self.agent_name}.",
            risk_level=self.risk_level,
            confidence=0.82,
            findings=(
                Finding(
                    title="Signal mock",
                    description="Signal de test pour BTC.",
                    impact=ImpactDirection.MIXED,
                    symbols=("btc",),
                ),
            ),
        )


class RaisingAgent:
    def __init__(self, message):
        self.message = message

    def analyze(self, *args, **kwargs):
        raise RuntimeError(self.message)


class RecordingNotifier:
    def __init__(self):
        self.summary_calls = 0
        self.alert_calls = 0

    def send_final_report_summary(self, final_report):
        self.summary_calls += 1
        return {
            "sent": True,
            "channel": "whatsapp",
            "status": "sent",
            "message": "summary sent",
            "error": None,
            "data": {},
        }

    def send_high_risk_alert(self, final_report):
        self.alert_calls += 1
        return {
            "sent": True,
            "channel": "whatsapp",
            "status": "sent",
            "message": "alert sent",
            "error": None,
            "data": {},
        }


class FailingNotifier:
    def send_final_report_summary(self, final_report):
        raise AssertionError("Notifier should not be called when WhatsApp is disabled.")

    def send_high_risk_alert(self, final_report):
        raise AssertionError("Notifier should not be called when WhatsApp is disabled.")


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
    unittest.main()
