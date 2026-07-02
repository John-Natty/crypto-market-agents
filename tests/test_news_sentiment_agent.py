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
        self.assertTrue(
            any(
                finding.title == "Actualite positive importante"
                for finding in report.findings
            )
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
            any(
                finding.title == "Actualite negative importante"
                for finding in report.findings
            )
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

    def test_analyze_no_articles_returns_partial_report(self):
        client = FakeNewsClient([])

        report = NewsSentimentAgent(client).analyze(query="quiet crypto topic")

        self.assertEqual(report.status, AgentStatus.PARTIAL)
        self.assertEqual(report.risk_level, RiskLevel.LOW)
        self.assertEqual(report.confidence, 0.20)
        self.assertTrue(report.errors)
        self.assertTrue(
            any(
                finding.title == "Absence d'actualite significative"
                for finding in report.findings
            )
        )

    def test_analyze_returns_failed_when_client_errors(self):
        client = FakeNewsClient(error=NewsNetworkError("network down"))

        report = NewsSentimentAgent(client).analyze(["bitcoin"])

        self.assertEqual(report.status, AgentStatus.FAILED)
        self.assertEqual(report.confidence, 0.0)
        self.assertEqual(report.findings, ())
        self.assertTrue(report.errors)


def article(title, description, content):
    return {
        "title": title,
        "description": description,
        "content": content,
        "source": {"id": "test", "name": "Test News"},
        "url": "https://example.com/article",
        "publishedAt": "2026-07-01T09:00:00Z",
    }


if __name__ == "__main__":
    unittest.main()
