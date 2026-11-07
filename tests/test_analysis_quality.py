"""Regressions for unsafe confidence, stale evidence and duplicated signals."""

from datetime import UTC, datetime, timedelta
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from crypto_market_agents.agents.final_synthesis_agent import FinalSynthesisAgent
from crypto_market_agents.agents.news_sentiment_agent import NewsSentimentAgent
from crypto_market_agents.agents.onchain_fundamental_agent import OnchainFundamentalAgent
from crypto_market_agents.agents.price_market_agent import PriceMarketAgent
from crypto_market_agents.agents.volatility_risk_agent import VolatilityRiskAgent
from crypto_market_agents.schemas import (
    AgentReport,
    AgentStatus,
    Finding,
    ImpactDirection,
    RiskLevel,
)


NOW = datetime(2026, 7, 1, 12, tzinfo=UTC)


def market_asset(asset_id="bitcoin", **overrides):
    return {
        "id": asset_id,
        "symbol": asset_id,
        "current_price": 100,
        "market_cap": 1_000_000,
        "market_cap_rank": 1,
        "total_volume": 10_000,
        "high_24h": 101,
        "low_24h": 99,
        "price_change_percentage_1h_in_currency": 0,
        "price_change_percentage_24h": 0,
        "price_change_percentage_7d_in_currency": 0,
        "last_updated": NOW.isoformat(),
        **overrides,
    }


class MarketClient:
    def __init__(self, assets):
        self.assets = assets

    def get_coin_markets(self, *args, **kwargs):
        return self.assets


class MarketQualityTests(unittest.TestCase):
    def test_unusable_prices_and_timestamps_never_report_success(self):
        for agent_class in (PriceMarketAgent, VolatilityRiskAgent):
            for override in (
                {"current_price": "invalid"},
                {"current_price": float("nan")},
                {"current_price": float("inf")},
                {"current_price": True},
                {"current_price": -1},
                {"current_price": 0},
                {"last_updated": "2000-01-01T00:00:00Z"},
                {"last_updated": "invalid"},
                {"last_updated": None},
                {"last_updated": (NOW + timedelta(hours=1)).isoformat()},
            ):
                with self.subTest(agent=agent_class.__name__, override=override):
                    result = agent_class(
                        MarketClient([market_asset(**override)]), now=lambda: NOW
                    ).analyze(["bitcoin"])
                    self.assertEqual(result.status, AgentStatus.FAILED)
                    self.assertEqual(result.confidence, 0)
                    self.assertTrue(result.errors)
                    self.assertEqual(result.findings, ())

    def test_bad_optional_metrics_lower_confidence_and_do_not_create_signals(self):
        for agent_class in (PriceMarketAgent, VolatilityRiskAgent):
            for override in (
                {"total_volume": "invalid"},
                {"market_cap": -10},
                {"price_change_percentage_24h": float("inf")},
                {"price_change_percentage_7d_in_currency": -101},
                {"high_24h": 50, "low_24h": 200},
                {"low_24h": 0},
                {"market_cap": 1e-300, "total_volume": 1e300},
            ):
                with self.subTest(agent=agent_class.__name__, override=override):
                    result = agent_class(
                        MarketClient([market_asset(**override)]), now=lambda: NOW
                    ).analyze(["bitcoin"])
                    self.assertEqual(result.status, AgentStatus.PARTIAL)
                    self.assertLessEqual(result.confidence, 0.60)
                    self.assertTrue(result.errors)
                    self.assertNotEqual(result.risk_level, RiskLevel.LOW)

    def test_stale_asset_is_excluded_from_mixed_report(self):
        result = PriceMarketAgent(
            MarketClient(
                [
                    market_asset(),
                    market_asset("ethereum", last_updated="2000-01-01T00:00:00Z"),
                ]
            ),
            now=lambda: NOW,
        ).analyze(["bitcoin", "ethereum"])
        self.assertEqual(result.status, AgentStatus.PARTIAL)
        self.assertEqual([asset["id"] for asset in result.data["assets"]], ["bitcoin"])
        self.assertEqual(result.data["missing_coin_ids"], ["ethereum"])

    def test_many_mild_assets_do_not_manufacture_critical_risk(self):
        risks = []
        for count in (1, 10, 100):
            assets = [market_asset(str(i), price_change_percentage_24h=6) for i in range(count)]
            result = VolatilityRiskAgent(MarketClient(assets), now=lambda: NOW).analyze(
                [asset["id"] for asset in assets]
            )
            risks.append(result.risk_level)
        self.assertEqual(risks, [RiskLevel.MEDIUM] * 3)

    def test_one_critical_asset_is_not_diluted_by_quiet_assets(self):
        assets = [market_asset(str(i)) for i in range(20)]
        assets[0]["price_change_percentage_24h"] = -30
        result = VolatilityRiskAgent(MarketClient(assets), now=lambda: NOW).analyze(
            [asset["id"] for asset in assets]
        )
        self.assertEqual(result.risk_level, RiskLevel.CRITICAL)


class TvlClient:
    def __init__(self, history, current=100_000_000):
        self.history, self.current = history, current

    def get_protocol(self, slug):
        return {"name": slug, "category": "Dexes", "chains": ["Ethereum"], "tvl": self.history}

    def get_current_tvl(self, slug):
        return self.current

    def get_fees_overview(self):
        return {}

    def get_stablecoins(self):
        return {}


def tvl_point(days_ago, value):
    return {"date": (NOW - timedelta(days=days_ago)).timestamp(), "totalLiquidityUSD": value}


class TvlWindowTests(unittest.TestCase):
    def test_recent_collapse_is_not_hidden_by_lifetime_growth(self):
        history = [
            tvl_point(100, 1_000_000),
            tvl_point(1, 1_000_000_000),
            tvl_point(0, 100_000_000),
        ]
        report = OnchainFundamentalAgent(TvlClient(history), now=lambda: NOW).analyze(["test"])
        self.assertEqual(report.data["protocols"][0]["tvl_change_24h_pct"], -90)
        self.assertIsNone(report.data["protocols"][0]["tvl_change_7d_pct"])
        self.assertEqual(report.risk_level, RiskLevel.HIGH)

    def test_zero_is_a_real_total_loss_and_history_is_sorted_by_date(self):
        history = [tvl_point(0, 0), tvl_point(7, 2_000_000_000), tvl_point(1, 1_000_000_000)]
        report = OnchainFundamentalAgent(TvlClient(history, current=0), now=lambda: NOW).analyze(
            ["test"]
        )
        data = report.data["protocols"][0]
        self.assertEqual(data["current_tvl"], 0)
        self.assertEqual(data["tvl_change_24h_pct"], -100)
        self.assertEqual(data["tvl_change_7d_pct"], -100)
        self.assertEqual(report.status, AgentStatus.SUCCESS)

    def test_sparse_or_stale_history_is_not_an_undated_percentage(self):
        for history in (
            [tvl_point(100, 1), tvl_point(0, 100)],
            [tvl_point(101, 1), tvl_point(100, 100)],
            [{"date": 1, "totalLiquidityUSD": 1}, {"date": 2, "totalLiquidityUSD": 100}],
        ):
            report = OnchainFundamentalAgent(TvlClient(history), now=lambda: NOW).analyze(["test"])
            self.assertIsNone(report.data["protocols"][0]["tvl_change_pct"])
            self.assertEqual(report.status, AgentStatus.PARTIAL)


class NewsClient:
    def __init__(self, articles):
        self.articles = articles

    def search_articles(self, *args, **kwargs):
        return self.articles


def article(title, *, url="https://news.example/story", date=None):
    return {
        "title": title,
        "description": "Crypto news update.",
        "content": "Details available.",
        "url": url,
        "publishedAt": date or NOW.isoformat(),
        "source": {"name": "News"},
    }


def news_report(articles):
    return NewsSentimentAgent(NewsClient(articles), now=lambda: NOW).analyze(["bitcoin"])


class NewsQualityTests(unittest.TestCase):
    def test_negated_or_denied_hack_does_not_trigger_critical(self):
        for title in (
            "Bitcoin exchange confirms no hack occurred",
            "Bitcoin exchange denies hack allegations",
            "Bitcoin hack did not occur",
            "Bitcoin hack rumors are false",
            "Bitcoin exchange rules confirmed: no security breach",
        ):
            with self.subTest(title=title):
                report = news_report([article(title)])
                self.assertEqual(report.risk_level, RiskLevel.LOW)
                self.assertEqual(report.data["article_analyses"][0]["risk_signals"], [])

    def test_affirmed_event_after_negation_still_triggers(self):
        report = news_report([article("No hack yesterday, but a bitcoin hack occurred today")])
        self.assertEqual(report.risk_level, RiskLevel.CRITICAL)

    def test_duplicate_urls_titles_and_reordered_headlines_are_single_evidence(self):
        original = article("Bitcoin exchange faces major liquidation")
        cases = [
            [original] * 6,
            [original, article(original["title"], url="https://other.example/copy")],
            [
                original,
                article(
                    "Breaking major liquidation faces Bitcoin exchange",
                    url="https://other.example/copy",
                ),
            ],
            [original, article("Another title", url="https://news.example/story?utm_source=copy")],
        ]
        baseline = news_report([original])
        for items in cases:
            with self.subTest(items=items):
                report = news_report(items)
                self.assertEqual(len(report.data["articles"]), 1)
                self.assertEqual(report.confidence, baseline.confidence)
                self.assertEqual(report.data["sentiment_score"], baseline.data["sentiment_score"])

    def test_old_missing_or_future_news_is_not_current_evidence(self):
        for date in (
            None,
            "2000-01-01T00:00:00Z",
            "invalid",
            (NOW + timedelta(days=1)).isoformat(),
        ):
            item = article("Bitcoin exchange hack")
            item["publishedAt"] = date
            report = news_report([item] * 6)
            self.assertEqual(report.status, AgentStatus.FAILED)
            self.assertEqual(report.confidence, 0)
            self.assertEqual(report.findings, ())

    def test_distinct_article_query_ids_are_not_duplicates(self):
        report = news_report(
            [
                article("Bitcoin adoption expands", url="https://news.example/article?id=1"),
                article("Ethereum upgrade announced", url="https://news.example/article?id=2"),
            ]
        )
        self.assertEqual(len(report.data["articles"]), 2)

    def test_publisher_count_controls_confidence(self):
        one_source = [
            article(f"Bitcoin adoption growth announcement {i}", url=f"https://news.example/{i}")
            for i in range(6)
        ]
        many_sources = [
            dict(item, url=f"https://publisher-{i}.example/story")
            for i, item in enumerate(one_source)
        ]
        first = news_report(one_source)
        second = news_report(many_sources)
        self.assertEqual(first.data["independent_source_count"], 1)
        self.assertEqual(second.data["independent_source_count"], 6)
        self.assertGreater(second.confidence, first.confidence)


class SynthesisEvidenceTests(unittest.TestCase):
    def test_price_and_volatility_are_one_risk_vote(self):
        for level in (RiskLevel.MEDIUM, RiskLevel.HIGH, RiskLevel.CRITICAL):
            reports = [
                AgentReport(
                    agent_name=name,
                    status=AgentStatus.SUCCESS,
                    risk_level=level,
                    confidence=0.8,
                    summary="Test",
                )
                for name in ("price_market_agent", "volatility_risk_agent")
            ]
            result = FinalSynthesisAgent().synthesize(reports)
            self.assertEqual(result.global_risk_level, level)

    def test_unrelated_assets_do_not_create_contradictions(self):
        price = AgentReport(
            agent_name="price_market_agent",
            status=AgentStatus.SUCCESS,
            risk_level=RiskLevel.LOW,
            confidence=0.9,
            summary="Test",
            findings=(
                Finding(
                    title="Hausse",
                    description="Bitcoin monte",
                    impact=ImpactDirection.BULLISH,
                    symbols=("btc",),
                ),
            ),
        )
        news = AgentReport(
            agent_name="news_sentiment_agent",
            status=AgentStatus.SUCCESS,
            risk_level=RiskLevel.HIGH,
            confidence=0.9,
            summary="Test",
            findings=(
                Finding(
                    title="Baisse",
                    description="Solana recule",
                    impact=ImpactDirection.BEARISH,
                    symbols=("sol",),
                ),
            ),
        )
        self.assertEqual(FinalSynthesisAgent().synthesize([price, news]).contradictions, ())
