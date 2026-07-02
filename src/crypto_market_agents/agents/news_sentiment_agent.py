"""News and sentiment analysis agent."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any, Protocol

from crypto_market_agents.clients.news_client import NewsError
from crypto_market_agents.schemas import (
    AgentReport,
    AgentStatus,
    Finding,
    ImpactDirection,
    RiskLevel,
    SentimentLabel,
    Source,
)


POSITIVE_KEYWORDS = (
    "adoption",
    "partnership",
    "approval",
    "growth",
    "bullish",
    "rally",
    "surge",
    "record",
    "institutional",
    "integration",
)

NEGATIVE_KEYWORDS = (
    "hack",
    "exploit",
    "lawsuit",
    "ban",
    "bearish",
    "crash",
    "fraud",
    "investigation",
    "liquidation",
    "outflow",
    "crackdown",
)

HIGH_RISK_KEYWORDS = (
    "hack",
    "exploit",
    "security breach",
    "insolvency",
    "bankruptcy",
    "regulatory crackdown",
    "major liquidation",
)

REGULATORY_KEYWORDS = (
    "lawsuit",
    "ban",
    "investigation",
    "crackdown",
    "regulatory",
    "regulator",
)

HACK_EXPLOIT_KEYWORDS = (
    "hack",
    "exploit",
    "security breach",
)


class ArticleClient(Protocol):
    """Read-only article client expected by NewsSentimentAgent."""

    base_url: str
    default_query: str
    max_articles: int

    def search_articles(
        self,
        query: str,
        language: str = "en",
        page_size: int = 10,
    ) -> list[dict[str, Any]]:
        """Return recent articles."""


@dataclass(frozen=True, slots=True)
class ArticleSignal:
    """Simple keyword signal extracted from one article."""

    positive_count: int
    negative_count: int
    high_risk_count: int
    regulatory_count: int
    hack_exploit_count: int


class NewsSentimentAgent:
    """Analyze recent crypto news with simple keyword-based rules."""

    agent_name = "news_sentiment_agent"

    def __init__(
        self,
        client: ArticleClient,
        *,
        default_query: str | None = None,
        max_articles: int | None = None,
    ) -> None:
        self.client = client
        self.default_query = default_query or getattr(
            client,
            "default_query",
            "crypto OR bitcoin OR ethereum OR blockchain",
        )
        self.max_articles = max_articles or int(getattr(client, "max_articles", 10))

    def analyze(
        self,
        coin_ids: list[str] | Sequence[str] | None = None,
        query: str | None = None,
        language: str = "en",
    ) -> AgentReport:
        """Fetch recent articles and return a sentiment-oriented report."""

        selected_query = _build_query(query, coin_ids, self.default_query)
        source = self._source()

        try:
            articles = self.client.search_articles(
                selected_query,
                language=language,
                page_size=self.max_articles,
            )
        except NewsError as exc:
            return self._failed_report(str(exc), source, selected_query)

        if not articles:
            finding = Finding(
                title="Absence d'actualite significative",
                description="Aucun article exploitable n'a ete retourne pour la requete.",
                impact=ImpactDirection.NEUTRAL,
                symbols=_normalize_symbols(coin_ids),
                confidence_score=0.30,
                data={"query": selected_query},
            )
            return AgentReport(
                agent_name=self.agent_name,
                status=AgentStatus.PARTIAL,
                summary="Analyse News & Sentiment partielle: aucun article exploitable.",
                risk_level=RiskLevel.LOW,
                confidence=0.20,
                findings=(finding,),
                sources=(source,),
                errors=(f"No articles returned for query: {selected_query}",),
                data={
                    "query": selected_query,
                    "language": language.lower(),
                    "articles": [],
                    "sentiment": SentimentLabel.NEUTRAL.value,
                },
            )

        signals = [self._article_signal(article) for article in articles]
        findings = self._build_findings(articles, signals, coin_ids)
        sentiment = self._global_sentiment(signals)
        risk_level = self._risk_level(signals)
        confidence = self._confidence(articles)

        return AgentReport(
            agent_name=self.agent_name,
            status=AgentStatus.SUCCESS,
            summary=self._summary(
                article_count=len(articles),
                finding_count=len(findings),
                sentiment=sentiment,
                risk_level=risk_level,
            ),
            risk_level=risk_level,
            confidence=confidence,
            findings=tuple(findings),
            sources=(source,),
            errors=(),
            data={
                "query": selected_query,
                "language": language.lower(),
                "sentiment": sentiment.value,
                "articles": [_clean_article_for_report(article) for article in articles],
            },
        )

    def _article_signal(self, article: dict[str, Any]) -> ArticleSignal:
        text = _article_text(article)
        return ArticleSignal(
            positive_count=_keyword_count(text, POSITIVE_KEYWORDS),
            negative_count=_keyword_count(text, NEGATIVE_KEYWORDS),
            high_risk_count=_keyword_count(text, HIGH_RISK_KEYWORDS),
            regulatory_count=_keyword_count(text, REGULATORY_KEYWORDS),
            hack_exploit_count=_keyword_count(text, HACK_EXPLOIT_KEYWORDS),
        )

    def _build_findings(
        self,
        articles: list[dict[str, Any]],
        signals: list[ArticleSignal],
        coin_ids: list[str] | Sequence[str] | None,
    ) -> list[Finding]:
        findings: list[Finding] = []
        symbols = _normalize_symbols(coin_ids)

        for article, signal in zip(articles, signals, strict=True):
            article_data = _finding_article_data(article)
            title = _article_title(article)

            if signal.hack_exploit_count:
                findings.append(
                    Finding(
                        title="Actualite liee a un hack ou exploit",
                        description=f"Signal de securite detecte dans: {title}",
                        impact=ImpactDirection.BEARISH,
                        symbols=symbols,
                        confidence_score=0.82,
                        data=article_data,
                    )
                )
                continue

            if signal.high_risk_count:
                findings.append(
                    Finding(
                        title="Actualite a risque eleve",
                        description=f"Signal de risque eleve detecte dans: {title}",
                        impact=ImpactDirection.BEARISH,
                        symbols=symbols,
                        confidence_score=0.80,
                        data=article_data,
                    )
                )
                continue

            if signal.regulatory_count:
                findings.append(
                    Finding(
                        title="Actualite liee a un risque reglementaire",
                        description=f"Signal reglementaire detecte dans: {title}",
                        impact=ImpactDirection.BEARISH,
                        symbols=symbols,
                        confidence_score=0.72,
                        data=article_data,
                    )
                )
                continue

            if signal.negative_count > signal.positive_count:
                findings.append(
                    Finding(
                        title="Actualite negative importante",
                        description=f"Signal negatif detecte dans: {title}",
                        impact=ImpactDirection.BEARISH,
                        symbols=symbols,
                        confidence_score=0.68,
                        data=article_data,
                    )
                )
                continue

            if signal.positive_count > signal.negative_count:
                findings.append(
                    Finding(
                        title="Actualite positive importante",
                        description=f"Signal positif detecte dans: {title}",
                        impact=ImpactDirection.BULLISH,
                        symbols=symbols,
                        confidence_score=0.66,
                        data=article_data,
                    )
                )

        if not findings:
            findings.append(
                Finding(
                    title="Absence d'actualite significative",
                    description="Les articles recuperes ne contiennent pas de signal clair.",
                    impact=ImpactDirection.NEUTRAL,
                    symbols=symbols,
                    confidence_score=0.45,
                    data={"article_count": len(articles)},
                )
            )

        return findings

    def _global_sentiment(self, signals: list[ArticleSignal]) -> SentimentLabel:
        positive = sum(signal.positive_count for signal in signals)
        negative = sum(signal.negative_count for signal in signals)

        if positive and negative:
            return SentimentLabel.MIXED
        if negative:
            return SentimentLabel.NEGATIVE
        if positive:
            return SentimentLabel.POSITIVE

        return SentimentLabel.NEUTRAL

    def _risk_level(self, signals: list[ArticleSignal]) -> RiskLevel:
        high_risk = sum(signal.high_risk_count for signal in signals)
        regulatory = sum(signal.regulatory_count for signal in signals)
        negative_articles = sum(signal.negative_count > 0 for signal in signals)

        if high_risk:
            return RiskLevel.CRITICAL
        if negative_articles >= 3 or regulatory >= 2:
            return RiskLevel.HIGH
        if negative_articles or regulatory:
            return RiskLevel.MEDIUM

        return RiskLevel.LOW

    def _confidence(self, articles: list[dict[str, Any]]) -> float:
        if not articles:
            return 0.0

        article_volume = min(len(articles) / max(self.max_articles, 1), 1)
        content_scores = [_content_quality(article) for article in articles]
        content_quality = sum(content_scores) / len(content_scores)
        confidence = 0.2 + (0.45 * article_volume) + (0.30 * content_quality)

        return round(max(0.05, min(0.95, confidence)), 2)

    def _summary(
        self,
        *,
        article_count: int,
        finding_count: int,
        sentiment: SentimentLabel,
        risk_level: RiskLevel,
    ) -> str:
        return (
            f"Analyse News & Sentiment sur {article_count} article(s). "
            f"Sentiment global {sentiment.value}. "
            f"Risque global {risk_level.value}. "
            f"{finding_count} signal(aux) d'actualite detecte(s)."
        )

    def _source(self) -> Source:
        base_url = getattr(self.client, "base_url", "https://newsapi.org/v2")
        return Source(
            name="NewsAPI everything",
            provider="newsapi",
            url=f"{str(base_url).rstrip('/')}/everything",
        )

    def _failed_report(self, error: str, source: Source, query: str) -> AgentReport:
        return AgentReport(
            agent_name=self.agent_name,
            status=AgentStatus.FAILED,
            summary="Analyse News & Sentiment echouee.",
            risk_level=RiskLevel.MEDIUM,
            confidence=0.0,
            findings=(),
            sources=(source,),
            errors=(error,),
            data={"query": query, "articles": [], "sentiment": SentimentLabel.UNKNOWN.value},
        )


def _build_query(
    query: str | None,
    coin_ids: list[str] | Sequence[str] | None,
    default_query: str,
) -> str:
    if query and query.strip():
        return query.strip()

    symbols = _normalize_symbols(coin_ids)
    if symbols:
        return " OR ".join(symbols)

    return default_query


def _normalize_symbols(coin_ids: list[str] | Sequence[str] | None) -> tuple[str, ...]:
    if not coin_ids:
        return ()

    symbols: list[str] = []
    seen: set[str] = set()
    for coin_id in coin_ids:
        cleaned = str(coin_id).strip().lower()
        if cleaned and cleaned not in seen:
            seen.add(cleaned)
            symbols.append(cleaned)

    return tuple(symbols)


def _article_signal_text(value: Any) -> str:
    return str(value or "").strip()


def _article_text(article: dict[str, Any]) -> str:
    return " ".join(
        part
        for part in (
            _article_signal_text(article.get("title")),
            _article_signal_text(article.get("description")),
            _article_signal_text(article.get("content")),
        )
        if part
    ).lower()


def _keyword_count(text: str, keywords: Sequence[str]) -> int:
    return sum(1 for keyword in keywords if keyword in text)


def _article_title(article: dict[str, Any]) -> str:
    return _article_signal_text(article.get("title")) or "article sans titre"


def _finding_article_data(article: dict[str, Any]) -> dict[str, Any]:
    source = article.get("source") if isinstance(article.get("source"), dict) else {}
    return {
        "title": article.get("title"),
        "source": source.get("name"),
        "url": article.get("url"),
        "publishedAt": article.get("publishedAt"),
    }


def _clean_article_for_report(article: dict[str, Any]) -> dict[str, Any]:
    source = article.get("source") if isinstance(article.get("source"), dict) else {}
    return {
        "title": article.get("title"),
        "description": article.get("description"),
        "source": source.get("name"),
        "url": article.get("url"),
        "publishedAt": article.get("publishedAt"),
    }


def _content_quality(article: dict[str, Any]) -> float:
    fields = (
        article.get("title"),
        article.get("description"),
        article.get("content"),
        article.get("url"),
        article.get("publishedAt"),
    )
    present = sum(1 for field in fields if _article_signal_text(field))
    return present / len(fields)

