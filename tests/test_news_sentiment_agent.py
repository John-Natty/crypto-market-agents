from pathlib import Path
import sys
import unittest


PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from crypto_market_agents.agents.news_sentiment_agent import NewsSentimentAgent
from crypto_market_agents.clients.news_client import NewsNetworkError
from crypto_market_agents.schemas import AgentStatus, RiskLevel, SentimentLabel


class FakeNewsClient:
    base_url = "https://newsapi.org/v2"
    default_query = "crypto OR bitcoin"
    max_articles = 10

    def __init__(self, articles=None, error=None):
        self.articles = articles or []
        self.error = error
        self.calls = []

    def search_articles(self, query, language="en", page_size=10):
        self.calls.append(
            {
                "query": query,
                "language": language,
                "page_size": page_size,
            }
        )
        if self.error:
            raise self.error
        return self.articles


class NewsSentimentAgentTests(unittest.TestCase):
    def test_analyze_positive_articles(self):
        client = FakeNewsClient(
            [
                article(
                    title="Bitcoin adoption reaches record level",
                    description="Institutional integration and partnership growth continue.",
                    content="A bullish rally follows approval news.",
                )
            ]
        )

        report = NewsSentimentAgent(client).analyze(["bitcoin"], language="en")

        self.assertEqual(report.agent_name, "news_sentiment_agent")
        self.assertEqual(report.status, AgentStatus.SUCCESS)
        self.assertEqual(report.risk_level, RiskLevel.LOW)
        self.assertEqual(report.data["sentiment"], SentimentLabel.POSITIVE.value)
        self.assertIn("adoption", report.data["article_analyses"][0]["categories"])
        self.assertIn("institutional", report.data["article_analyses"][0]["categories"])
        self.assertTrue(
            any(finding.title == "Actualite positive importante" for finding in report.findings)
        )
        self.assertEqual(client.calls[0]["query"], "bitcoin")

    def test_analyze_negative_articles(self):
        client = FakeNewsClient(
            [
                article(
                    title="Crypto market turns bearish",
                    description="A market outflow follows bearish momentum.",
                    content="The article mentions a crash risk.",
                )
            ]
        )

        report = NewsSentimentAgent(client).analyze(query="crypto lawsuit")

        self.assertEqual(report.risk_level, RiskLevel.MEDIUM)
        self.assertEqual(report.data["sentiment"], SentimentLabel.NEGATIVE.value)
        self.assertTrue(
            any(finding.title == "Actualite negative importante" for finding in report.findings)
        )

    def test_analyze_hack_or_exploit_articles(self):
        client = FakeNewsClient(
            [
                article(
                    title="Major exchange hack reported",
                    description="A security breach and exploit affect users.",
                    content="The hack investigation is ongoing.",
                )
            ]
        )

        report = NewsSentimentAgent(client).analyze(["ethereum"])

        self.assertEqual(report.risk_level, RiskLevel.CRITICAL)
        self.assertEqual(report.data["sentiment"], SentimentLabel.NEGATIVE.value)
        self.assertTrue(
            any(
                finding.title == "Actualite liee a un hack ou exploit"
                for finding in report.findings
            )
        )
        self.assertIn("security:hack", report.data["article_analyses"][0]["risk_signals"])

    def test_analyze_regulatory_crackdown_or_ban_articles(self):
        client = FakeNewsClient(
            [
                article(
                    title="Regulator announces crypto ban",
                    description="A regulatory crackdown creates pressure on exchanges.",
                    content="The enforcement action remains under review.",
                )
            ]
        )

        report = NewsSentimentAgent(client).analyze(["bitcoin"])

        self.assertEqual(report.risk_level, RiskLevel.HIGH)
        self.assertEqual(report.data["sentiment"], SentimentLabel.NEGATIVE.value)
        self.assertTrue(
            any(
                finding.title == "Actualite liee a un risque reglementaire"
                for finding in report.findings
            )
        )
        self.assertIn("regulation", report.data["article_analyses"][0]["categories"])

    def test_analyze_mixed_sentiment_when_positive_and_negative_signals_coexist(self):
        client = FakeNewsClient(
            [
                article(
                    title="Ethereum adoption grows after institutional approval",
                    description="Partnership and integration signals improve sentiment.",
                    content="Fund inflow continues.",
                ),
                article(
                    title="Crypto lawsuit triggers investigation",
                    description="Legal pressure weighs on market confidence.",
                    content="The lawsuit remains active.",
                ),
            ]
        )

        report = NewsSentimentAgent(client).analyze(["ethereum"])

        self.assertEqual(report.data["sentiment"], SentimentLabel.MIXED.value)
        self.assertEqual(report.risk_level, RiskLevel.MEDIUM)
        self.assertTrue(
            any(finding.title == "Actualite positive importante" for finding in report.findings)
        )
        self.assertTrue(
            any(finding.title == "Actualite negative importante" for finding in report.findings)
        )

    def test_analyze_neutral_articles(self):
        client = FakeNewsClient(
            [
                article(
                    title="Crypto conference schedule published",
                    description="The event agenda lists panels and workshops.",
                    content="No market-sensitive announcement is included.",
                )
            ]
        )

        report = NewsSentimentAgent(client).analyze(query="crypto conference")

        self.assertEqual(report.risk_level, RiskLevel.LOW)
        self.assertEqual(report.data["sentiment"], SentimentLabel.NEUTRAL.value)
        self.assertTrue(
            any(finding.title == "Absence d'actualite significative" for finding in report.findings)
        )

    def test_major_or_massive_intensity_increases_risk(self):
        client = FakeNewsClient(
            [
                article(
                    title="Major massive liquidation event hits crypto market",
                    description="A liquidation wave creates severe market stress.",
                    content="Analysts describe the event as critical.",
                )
            ]
        )

        report = NewsSentimentAgent(client).analyze(["bitcoin"])
        analysis = report.data["article_analyses"][0]

        self.assertEqual(report.risk_level, RiskLevel.CRITICAL)
        self.assertLess(analysis["sentiment_score"], -8)
        self.assertIn("major", analysis["intensity_keywords"])

    def test_minor_or_limited_intensity_reduces_impact(self):
        base_report = NewsSentimentAgent(
            FakeNewsClient(
                [
                    article(
                        title="Liquidation reported in crypto market",
                        description="A liquidation event affects traders.",
                        content="Market stress remains contained.",
                    )
                ]
            )
        ).analyze(["bitcoin"])
        limited_report = NewsSentimentAgent(
            FakeNewsClient(
                [
                    article(
                        title="Minor limited liquidation reported in crypto market",
                        description="A partial liquidation event affects traders.",
                        content="The effect is moderate and contained.",
                    )
                ]
            )
        ).analyze(["bitcoin"])

        base_score = base_report.data["article_analyses"][0]["sentiment_score"]
        limited_score = limited_report.data["article_analyses"][0]["sentiment_score"]

        self.assertLess(abs(limited_score), abs(base_score))
        self.assertIn(
            limited_report.risk_level,
            {RiskLevel.LOW, RiskLevel.MEDIUM},
        )

    def test_extracts_btc_eth_and_sol_assets_cleanly(self):
        client = FakeNewsClient(
            [
                article(
                    title="BTC and ETH rally while SOL integration grows",
                    description="Bitcoin, Ethereum and Solana see adoption signals.",
                    content="Institutional inflow supports the move.",
                )
            ]
        )

        report = NewsSentimentAgent(client).analyze(query="crypto")
        assets = report.data["article_analyses"][0]["related_assets"]

        self.assertIn("bitcoin", assets)
        self.assertIn("ethereum", assets)
        self.assertIn("solana", assets)
        self.assertTrue(any("solana" in finding.symbols for finding in report.findings))

    def test_does_not_detect_sol_inside_solution(self):
        client = FakeNewsClient(
            [
                article(
                    title="Developer solution improves crypto tooling",
                    description="The solution focuses on documentation.",
                    content="No network ticker is mentioned.",
                )
            ]
        )

        report = NewsSentimentAgent(client).analyze(query="developer solution")

        self.assertNotIn("solana", report.data["article_analyses"][0]["related_assets"])

    def test_limits_findings_to_most_important_articles(self):
        client = FakeNewsClient(
            [
                article(
                    title=f"Bitcoin adoption record article {index}",
                    description="Institutional partnership and integration continue.",
                    content="Bullish growth follows approval.",
                    url=f"https://example.com/article-{index}",
                )
                for index in range(12)
            ]
        )

        report = NewsSentimentAgent(client).analyze(["bitcoin"])

        self.assertEqual(len(report.findings), 8)

    def test_confidence_is_lower_with_few_incomplete_articles(self):
        client = FakeNewsClient([article(title="Crypto update", description=None, content=None)])

        report = NewsSentimentAgent(client).analyze(query="crypto")

        self.assertLess(report.confidence, 0.5)

    def test_confidence_is_higher_with_multiple_complete_articles(self):
        client = FakeNewsClient(
            [
                article(
                    title=f"Ethereum adoption growth article {index}",
                    description="Institutional integration and partnership improve confidence.",
                    content="Approval and inflow signals are described with context.",
                    url=f"https://example.com/complete-{index}",
                )
                for index in range(5)
            ]
        )

        report = NewsSentimentAgent(client).analyze(["ethereum"])

        self.assertGreater(report.confidence, 0.75)

    def test_analyze_no_articles_returns_partial_report(self):
        client = FakeNewsClient([])

        report = NewsSentimentAgent(client).analyze(query="quiet crypto topic")

        self.assertEqual(report.status, AgentStatus.PARTIAL)
        self.assertEqual(report.risk_level, RiskLevel.LOW)
        self.assertEqual(report.confidence, 0.20)
        self.assertTrue(report.errors)
        self.assertTrue(
            any(finding.title == "Absence d'actualite significative" for finding in report.findings)
        )

    def test_analyze_returns_failed_when_client_errors(self):
        client = FakeNewsClient(error=NewsNetworkError("network down"))

        report = NewsSentimentAgent(client).analyze(["bitcoin"])

        self.assertEqual(report.status, AgentStatus.FAILED)
        self.assertEqual(report.confidence, 0.0)
        self.assertEqual(report.findings, ())
        self.assertTrue(report.errors)


def article(
    title,
    description="",
    content="",
    *,
    url="https://example.com/article",
    published_at="2026-07-01T09:00:00Z",
):
    return {
        "title": title,
        "description": description,
        "content": content,
        "source": {"id": "test", "name": "Test News"},
        "url": url,
        "publishedAt": published_at,
    }


if __name__ == "__main__":
    unittest.main()
