"""News and sentiment analysis agent."""

from __future__ import annotations

import re
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


SIGNAL_CATEGORIES = (
    "adoption",
    "institutional",
    "regulation",
    "security",
    "market_stress",
    "legal",
    "technical",
    "macro",
    "neutral",
)

POSITIVE_KEYWORDS: dict[str, dict[str, int]] = {
    "adoption": {
        "adoption": 3,
        "partnership": 2,
        "integration": 2,
        "growth": 2,
        "expansion": 2,
        "merchant adoption": 3,
    },
    "institutional": {
        "approval": 3,
        "institutional": 3,
        "inflow": 2,
        "etf inflow": 2,
        "fund inflow": 2,
    },
    "market_stress": {
        "bullish": 2,
        "rally": 2,
        "surge": 2,
        "record": 1,
        "rebound": 1,
    },
    "technical": {
        "upgrade": 2,
        "scaling": 2,
        "mainnet": 2,
        "developer activity": 2,
    },
    "macro": {
        "rate cut": 2,
        "liquidity improves": 2,
    },
}

NEGATIVE_KEYWORDS: dict[str, dict[str, int]] = {
    "security": {
        "hack": -5,
        "exploit": -5,
        "security breach": -5,
        "breach": -4,
        "stolen funds": -5,
    },
    "regulation": {
        "ban": -4,
        "crackdown": -4,
        "regulatory crackdown": -5,
        "enforcement action": -3,
        "regulatory pressure": -3,
    },
    "legal": {
        "lawsuit": -3,
        "investigation": -3,
        "fraud": -5,
        "bankruptcy": -5,
        "insolvency": -5,
    },
    "market_stress": {
        "bearish": -2,
        "crash": -4,
        "liquidation": -4,
        "major liquidation": -5,
        "outflow": -2,
        "selloff": -3,
        "market stress": -3,
    },
    "technical": {
        "outage": -3,
        "bug": -2,
        "network halt": -4,
    },
    "macro": {
        "rate hike": -2,
        "recession": -2,
        "liquidity crunch": -3,
    },
}

STRONG_INTENSITY_KEYWORDS = (
    "major",
    "massive",
    "severe",
    "emergency",
    "critical",
    "record-breaking",
)

WEAK_INTENSITY_KEYWORDS = (
    "minor",
    "limited",
    "partial",
    "moderate",
)

CRITICAL_RISK_KEYWORDS = (
    "hack",
    "exploit",
    "security breach",
    "stolen funds",
    "bankruptcy",
    "insolvency",
)

ASSET_ALIASES: dict[str, tuple[str, ...]] = {
    "bitcoin": ("bitcoin", "btc"),
    "ethereum": ("ethereum", "eth"),
    "solana": ("solana", "sol"),
    "cardano": ("cardano", "ada"),
    "ripple": ("ripple", "xrp"),
    "binance": ("binance", "bnb"),
    "dogecoin": ("dogecoin", "doge"),
    "polygon": ("polygon", "matic"),
    "avalanche": ("avalanche", "avax"),
    "uniswap": ("uniswap", "uni"),
    "aave": ("aave",),
    "lido": ("lido",),
    "curve": ("curve",),
}

MAX_FINDINGS = 8
POSITIVE_THRESHOLD = 2.0
NEGATIVE_THRESHOLD = -2.0


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
class ArticleAnalysis:
    """Explainable sentiment result for one article."""

    title: str
    url: str | None
    sentiment_score: float
    positive_score: float
    negative_score: float
    sentiment_label: SentimentLabel
    categories: tuple[str, ...]
    risk_signals: tuple[str, ...]
    matched_keywords: tuple[str, ...]
    intensity_keywords: tuple[str, ...]
    related_assets: tuple[str, ...]
    risk_level: RiskLevel
    confidence: float
    content_quality: float

    @property
    def has_signal(self) -> bool:
        """Return True when the article contains at least one weighted signal."""

        return bool(self.matched_keywords)

    def to_dict(self) -> dict[str, Any]:
        """Return a JSON-friendly explanation of the article analysis."""

        return {
            "title": self.title,
            "url": self.url,
            "sentiment_score": self.sentiment_score,
            "positive_score": self.positive_score,
            "negative_score": self.negative_score,
            "sentiment_label": self.sentiment_label.value,
            "categories": list(self.categories),
            "risk_signals": list(self.risk_signals),
            "matched_keywords": list(self.matched_keywords),
            "intensity_keywords": list(self.intensity_keywords),
            "related_assets": list(self.related_assets),
            "risk_level": self.risk_level.value,
            "confidence": self.confidence,
            "content_quality": self.content_quality,
        }


class NewsSentimentAgent:
    """Analyze recent crypto news with weighted, explainable sentiment rules."""

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
                    "article_analyses": [],
                    "sentiment": SentimentLabel.NEUTRAL.value,
                    "sentiment_score": 0.0,
                },
            )

        analyses = [self._analyze_article(article) for article in articles]
        findings = self._build_findings(articles, analyses, coin_ids)
        sentiment = self._global_sentiment(analyses)
        risk_level = self._risk_level(analyses)
        confidence = self._confidence(articles, analyses)

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
                "sentiment_score": round(sum(item.sentiment_score for item in analyses), 2),
                "categories": list(_unique_category_list(analyses)),
                "articles": [_clean_article_for_report(article) for article in articles],
                "article_analyses": [analysis.to_dict() for analysis in analyses],
            },
        )

    def _analyze_article(self, article: dict[str, Any]) -> ArticleAnalysis:
        text = _article_text(article)
        content_quality = _content_quality(article)
        positive_score, negative_score, categories, matched_keywords = _weighted_scores(text)
        intensity_keywords = _matched_intensity_keywords(text)
        multiplier = _intensity_multiplier(intensity_keywords)
        adjusted_positive = round(positive_score * multiplier, 2)
        adjusted_negative = round(negative_score * multiplier, 2)
        sentiment_score = round(adjusted_positive + adjusted_negative, 2)
        risk_signals = _risk_signals(text, negative_score)
        sentiment_label = _article_sentiment_label(adjusted_positive, adjusted_negative)
        risk_level = _article_risk_level(
            text=text,
            negative_score=adjusted_negative,
            risk_signals=risk_signals,
            categories=categories,
            intensity_keywords=intensity_keywords,
        )
        confidence = _article_confidence(
            content_quality=content_quality,
            signal_count=len(matched_keywords),
            sentiment_score=sentiment_score,
        )

        return ArticleAnalysis(
            title=_article_title(article),
            url=_optional_article_text(article.get("url")),
            sentiment_score=sentiment_score,
            positive_score=adjusted_positive,
            negative_score=adjusted_negative,
            sentiment_label=sentiment_label,
            categories=tuple(sorted(categories)) or ("neutral",),
            risk_signals=tuple(sorted(risk_signals)),
            matched_keywords=tuple(sorted(matched_keywords)),
            intensity_keywords=tuple(sorted(intensity_keywords)),
            related_assets=_extract_assets(text),
            risk_level=risk_level,
            confidence=confidence,
            content_quality=round(content_quality, 2),
        )

    def _build_findings(
        self,
        articles: list[dict[str, Any]],
        analyses: list[ArticleAnalysis],
        coin_ids: list[str] | Sequence[str] | None,
    ) -> list[Finding]:
        symbols_from_query = _normalize_symbols(coin_ids)
        ranked = sorted(
            ((index, analysis) for index, analysis in enumerate(analyses) if analysis.has_signal),
            key=lambda item: _finding_importance(item[1]),
            reverse=True,
        )

        findings: list[Finding] = []
        for index, analysis in ranked[:MAX_FINDINGS]:
            article = articles[index]
            symbols = analysis.related_assets or symbols_from_query
            findings.append(
                Finding(
                    title=_finding_title(analysis),
                    description=_finding_description(analysis),
                    impact=_finding_impact(analysis.sentiment_label),
                    symbols=symbols,
                    confidence_score=analysis.confidence,
                    data={
                        "title": analysis.title,
                        "url": analysis.url,
                        "sentiment_score": analysis.sentiment_score,
                        "sentiment_label": analysis.sentiment_label.value,
                        "risk_level": analysis.risk_level.value,
                        "categories": list(analysis.categories),
                        "risk_signals": list(analysis.risk_signals),
                        "matched_keywords": list(analysis.matched_keywords),
                        "intensity_keywords": list(analysis.intensity_keywords),
                        "related_assets": list(analysis.related_assets),
                        "source": _article_source_name(article),
                        "publishedAt": article.get("publishedAt"),
                    },
                )
            )

        if not findings:
            findings.append(
                Finding(
                    title="Absence d'actualite significative",
                    description="Les articles recuperes ne contiennent pas de signal clair.",
                    impact=ImpactDirection.NEUTRAL,
                    symbols=symbols_from_query,
                    confidence_score=0.45,
                    data={"article_count": len(articles)},
                )
            )

        return findings

    def _global_sentiment(self, analyses: list[ArticleAnalysis]) -> SentimentLabel:
        positive_articles = sum(
            analysis.positive_score >= POSITIVE_THRESHOLD for analysis in analyses
        )
        negative_articles = sum(
            analysis.negative_score <= NEGATIVE_THRESHOLD for analysis in analyses
        )
        total_score = sum(analysis.sentiment_score for analysis in analyses)
        strong_positive = sum(max(analysis.positive_score, 0.0) for analysis in analyses)
        strong_negative = sum(abs(min(analysis.negative_score, 0.0)) for analysis in analyses)

        if positive_articles and negative_articles:
            balanced = abs(strong_positive - strong_negative) <= max(
                2.0,
                0.50 * max(strong_positive, strong_negative),
            )
            if balanced or abs(total_score) <= 3.0:
                return SentimentLabel.MIXED

        if total_score >= 3.0:
            return SentimentLabel.POSITIVE
        if total_score <= -3.0:
            return SentimentLabel.NEGATIVE
        if positive_articles and negative_articles:
            return SentimentLabel.MIXED

        return SentimentLabel.NEUTRAL

    def _risk_level(self, analyses: list[ArticleAnalysis]) -> RiskLevel:
        critical_count = sum(analysis.risk_level == RiskLevel.CRITICAL for analysis in analyses)
        high_count = sum(analysis.risk_level == RiskLevel.HIGH for analysis in analyses)
        medium_count = sum(analysis.risk_level == RiskLevel.MEDIUM for analysis in analyses)
        negative_count = sum(analysis.negative_score <= NEGATIVE_THRESHOLD for analysis in analyses)

        if critical_count:
            return RiskLevel.CRITICAL
        if high_count >= 1 or negative_count >= 3:
            return RiskLevel.HIGH
        if medium_count or negative_count:
            return RiskLevel.MEDIUM

        return RiskLevel.LOW

    def _confidence(
        self,
        articles: list[dict[str, Any]],
        analyses: list[ArticleAnalysis],
    ) -> float:
        if not articles:
            return 0.0

        article_volume = min(len(articles) / max(min(self.max_articles, 6), 1), 1.0)
        content_quality = sum(analysis.content_quality for analysis in analyses) / len(analyses)
        signal_ratio = sum(analysis.has_signal for analysis in analyses) / len(analyses)
        signal_strength = min(
            sum(abs(analysis.sentiment_score) for analysis in analyses) / (len(analyses) * 6),
            1.0,
        )
        low_signal_conflict = (
            self._global_sentiment(analyses) == SentimentLabel.MIXED and signal_strength < 0.45
        )
        confidence = (
            0.18
            + (0.25 * article_volume)
            + (0.32 * content_quality)
            + (0.16 * signal_ratio)
            + (0.09 * signal_strength)
        )
        if len(articles) == 1:
            confidence -= 0.10
        if low_signal_conflict:
            confidence -= 0.07

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


def _optional_article_text(value: Any) -> str | None:
    cleaned = str(value or "").strip()
    return cleaned or None


def _article_text(article: dict[str, Any]) -> str:
    """Return normalized article text built from title, description, and content."""

    text = " ".join(
        part
        for part in (
            _optional_article_text(article.get("title")),
            _optional_article_text(article.get("description")),
            _optional_article_text(article.get("content")),
        )
        if part
    )
    return _normalize_text(text)


def _normalize_text(text: Any) -> str:
    lowered = str(text or "").lower()
    return re.sub(r"\s+", " ", lowered).strip()


def _keyword_count(text: str, keyword: str) -> int:
    if not text:
        return 0

    normalized_keyword = _normalize_text(keyword)
    escaped = re.escape(normalized_keyword).replace(r"\ ", r"[\s-]+")
    pattern = rf"(?<![a-z0-9]){escaped}(?![a-z0-9])"
    return len(re.findall(pattern, text))


def _weighted_scores(text: str) -> tuple[float, float, set[str], set[str]]:
    positive_score = 0.0
    negative_score = 0.0
    categories: set[str] = set()
    matched_keywords: set[str] = set()

    for category, keywords in POSITIVE_KEYWORDS.items():
        for keyword, weight in keywords.items():
            count = min(_keyword_count(text, keyword), 3)
            if count:
                positive_score += weight * count
                categories.add(category)
                matched_keywords.add(keyword)

    for category, keywords in NEGATIVE_KEYWORDS.items():
        for keyword, weight in keywords.items():
            count = min(_keyword_count(text, keyword), 3)
            if count:
                negative_score += weight * count
                categories.add(category)
                matched_keywords.add(keyword)

    return positive_score, negative_score, categories, matched_keywords


def _matched_intensity_keywords(text: str) -> set[str]:
    matched = set()
    for keyword in (*STRONG_INTENSITY_KEYWORDS, *WEAK_INTENSITY_KEYWORDS):
        if _keyword_count(text, keyword):
            matched.add(keyword)

    return matched


def _intensity_multiplier(intensity_keywords: set[str]) -> float:
    strong = sum(keyword in intensity_keywords for keyword in STRONG_INTENSITY_KEYWORDS)
    weak = sum(keyword in intensity_keywords for keyword in WEAK_INTENSITY_KEYWORDS)
    multiplier = 1.0 + min(strong, 2) * 0.25 - min(weak, 2) * 0.20
    return max(0.60, min(1.50, multiplier))


def _risk_signals(text: str, negative_score: float) -> set[str]:
    signals: set[str] = set()
    if negative_score >= 0:
        return signals

    for category, keywords in NEGATIVE_KEYWORDS.items():
        for keyword, weight in keywords.items():
            if weight <= -3 and _keyword_count(text, keyword):
                signals.add(f"{category}:{keyword}")

    return signals


def _article_sentiment_label(
    positive_score: float,
    negative_score: float,
) -> SentimentLabel:
    has_positive = positive_score >= POSITIVE_THRESHOLD
    has_negative = negative_score <= NEGATIVE_THRESHOLD
    total = positive_score + negative_score

    if has_positive and has_negative:
        return SentimentLabel.MIXED
    if total >= POSITIVE_THRESHOLD:
        return SentimentLabel.POSITIVE
    if total <= NEGATIVE_THRESHOLD:
        return SentimentLabel.NEGATIVE

    return SentimentLabel.NEUTRAL


def _article_risk_level(
    *,
    text: str,
    negative_score: float,
    risk_signals: set[str],
    categories: set[str],
    intensity_keywords: set[str],
) -> RiskLevel:
    strong_intensity = any(keyword in intensity_keywords for keyword in STRONG_INTENSITY_KEYWORDS)
    weak_intensity = any(keyword in intensity_keywords for keyword in WEAK_INTENSITY_KEYWORDS)
    has_critical_security = any(_keyword_count(text, keyword) for keyword in CRITICAL_RISK_KEYWORDS)
    has_major_liquidation = _keyword_count(text, "major liquidation") or (
        _keyword_count(text, "liquidation") and strong_intensity
    )
    has_major_crackdown = _keyword_count(text, "regulatory crackdown") and strong_intensity

    if has_critical_security or has_major_liquidation or has_major_crackdown:
        return RiskLevel.CRITICAL
    if not risk_signals:
        return RiskLevel.LOW
    if weak_intensity and abs(negative_score) < 4.0:
        return RiskLevel.MEDIUM
    if abs(negative_score) >= 12.0:
        return RiskLevel.HIGH
    if "regulation" in categories and abs(negative_score) >= 4.0:
        return RiskLevel.HIGH
    if "market_stress" in categories and abs(negative_score) >= 12.0:
        return RiskLevel.HIGH
    if abs(negative_score) >= 3.0:
        return RiskLevel.MEDIUM

    return RiskLevel.LOW


def _article_confidence(
    *,
    content_quality: float,
    signal_count: int,
    sentiment_score: float,
) -> float:
    signal_component = min(signal_count / 5, 1.0)
    strength_component = min(abs(sentiment_score) / 8, 1.0)
    confidence = 0.20 + (0.45 * content_quality) + (0.20 * signal_component)
    confidence += 0.10 * strength_component

    return round(max(0.10, min(0.95, confidence)), 2)


def _finding_importance(analysis: ArticleAnalysis) -> float:
    risk_weight = {
        RiskLevel.LOW: 0,
        RiskLevel.MEDIUM: 2,
        RiskLevel.HIGH: 4,
        RiskLevel.CRITICAL: 7,
    }[analysis.risk_level]
    return abs(analysis.sentiment_score) + risk_weight + analysis.confidence


def _finding_title(analysis: ArticleAnalysis) -> str:
    if _has_security_signal(analysis):
        return "Actualite liee a un hack ou exploit"
    if "regulation" in analysis.categories and analysis.risk_level in {
        RiskLevel.HIGH,
        RiskLevel.CRITICAL,
    }:
        return "Actualite liee a un risque reglementaire"
    if analysis.risk_level in {RiskLevel.HIGH, RiskLevel.CRITICAL}:
        return "Actualite a risque eleve"
    if analysis.sentiment_label == SentimentLabel.MIXED:
        return "Actualite mixte importante"
    if analysis.sentiment_label == SentimentLabel.NEGATIVE:
        return "Actualite negative importante"
    if analysis.sentiment_label == SentimentLabel.POSITIVE:
        return "Actualite positive importante"

    return "Actualite neutre"


def _finding_description(analysis: ArticleAnalysis) -> str:
    categories = ", ".join(analysis.categories)
    signals = ", ".join(analysis.risk_signals or analysis.matched_keywords)
    if not signals:
        signals = "aucun signal sensible"

    return (
        f"Signal {analysis.sentiment_label.value} detecte dans: {analysis.title}. "
        f"Categories: {categories}. "
        f"Score {analysis.sentiment_score:+.2f}; risque {analysis.risk_level.value}; "
        f"signaux: {signals}."
    )


def _finding_impact(sentiment: SentimentLabel) -> ImpactDirection:
    if sentiment == SentimentLabel.POSITIVE:
        return ImpactDirection.BULLISH
    if sentiment == SentimentLabel.NEGATIVE:
        return ImpactDirection.BEARISH
    if sentiment == SentimentLabel.MIXED:
        return ImpactDirection.MIXED

    return ImpactDirection.NEUTRAL


def _has_security_signal(analysis: ArticleAnalysis) -> bool:
    return any(signal.startswith("security:") for signal in analysis.risk_signals)


def _extract_assets(text: str) -> tuple[str, ...]:
    assets: list[str] = []
    for asset, aliases in ASSET_ALIASES.items():
        if any(_keyword_count(text, alias) for alias in aliases):
            assets.append(asset)

    return tuple(assets)


def _unique_category_list(analyses: list[ArticleAnalysis]) -> tuple[str, ...]:
    categories: list[str] = []
    seen: set[str] = set()
    for analysis in analyses:
        for category in analysis.categories:
            if category not in seen:
                seen.add(category)
                categories.append(category)

    return tuple(categories)


def _article_title(article: dict[str, Any]) -> str:
    return _optional_article_text(article.get("title")) or "article sans titre"


def _article_source_name(article: dict[str, Any]) -> str | None:
    source = article.get("source") if isinstance(article.get("source"), dict) else {}
    return _optional_article_text(source.get("name"))


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
    present = sum(1 for field in fields if _optional_article_text(field))
    return present / len(fields)
