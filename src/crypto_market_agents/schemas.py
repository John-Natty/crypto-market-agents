"""Shared schemas returned by analysis agents."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import StrEnum
from typing import Any


class SchemaError(ValueError):
    """Raised when schema data is invalid."""


class AgentName(StrEnum):
    """Known analysis agent identifiers."""

    PRICE_MARKET_AGENT = "price_market_agent"
    PRICE_MARKET = "price_market"
    VOLATILITY_RISK = "volatility_risk"
    NEWS_SENTIMENT = "news_sentiment"
    FUNDAMENTALS = "fundamentals"
    FINAL_SYNTHESIS = "final_synthesis"


class AgentStatus(StrEnum):
    """Normalized execution status for agent reports."""

    SUCCESS = "success"
    PARTIAL = "partial"
    FAILED = "failed"


class RiskLevel(StrEnum):
    """Normalized risk levels used across agents."""

    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


class ImpactDirection(StrEnum):
    """Simple market impact direction."""

    BULLISH = "bullish"
    BEARISH = "bearish"
    NEUTRAL = "neutral"
    MIXED = "mixed"
    UNKNOWN = "unknown"


class SentimentLabel(StrEnum):
    """Normalized sentiment label for news and synthesis."""

    POSITIVE = "positive"
    NEGATIVE = "negative"
    NEUTRAL = "neutral"
    MIXED = "mixed"
    UNKNOWN = "unknown"


RISK_LEVEL_RANK = {
    RiskLevel.LOW: 1,
    RiskLevel.MEDIUM: 2,
    RiskLevel.HIGH: 3,
    RiskLevel.CRITICAL: 4,
}


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


@dataclass(frozen=True, slots=True)
class SourceReference:
    """Reference to an API, article, dataset, or computed source."""

    name: str
    url: str | None = None
    provider: str | None = None
    retrieved_at: datetime = field(default_factory=_utc_now)

    def __post_init__(self) -> None:
        object.__setattr__(self, "name", _required_text(self.name, "name"))
        object.__setattr__(self, "url", _optional_text(self.url))
        object.__setattr__(self, "provider", _optional_text(self.provider))
        object.__setattr__(
            self,
            "retrieved_at",
            _normalize_datetime(self.retrieved_at, "retrieved_at"),
        )

    def to_dict(self) -> dict[str, Any]:
        """Return a JSON-friendly representation."""

        return {
            "name": self.name,
            "url": self.url,
            "provider": self.provider,
            "retrieved_at": self.retrieved_at.isoformat(),
        }

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> SourceReference:
        """Build a source reference from a mapping."""

        return cls(
            name=payload.get("name", ""),
            url=payload.get("url"),
            provider=payload.get("provider"),
            retrieved_at=_normalize_datetime(
                payload.get("retrieved_at", _utc_now()),
                "retrieved_at",
            ),
        )


@dataclass(frozen=True, slots=True)
class AgentFinding:
    """A concise observation produced by an analysis agent."""

    title: str
    description: str
    impact: ImpactDirection = ImpactDirection.UNKNOWN
    symbols: tuple[str, ...] = ()
    confidence_score: float = 0.5
    data: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(self, "title", _required_text(self.title, "title"))
        object.__setattr__(
            self,
            "description",
            _required_text(self.description, "description"),
        )
        object.__setattr__(
            self,
            "impact",
            _coerce_enum(ImpactDirection, self.impact, "impact"),
        )
        object.__setattr__(self, "symbols", _normalize_text_tuple(self.symbols))
        object.__setattr__(
            self,
            "confidence_score",
            _probability(self.confidence_score, "confidence_score"),
        )
        object.__setattr__(self, "data", _normalize_data(self.data, "data"))

    def to_dict(self) -> dict[str, Any]:
        """Return a JSON-friendly representation."""

        return {
            "title": self.title,
            "description": self.description,
            "impact": self.impact.value,
            "symbols": list(self.symbols),
            "confidence_score": self.confidence_score,
            "data": to_jsonable(self.data),
        }

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> AgentFinding:
        """Build an agent finding from a mapping."""

        return cls(
            title=payload.get("title", ""),
            description=payload.get("description", ""),
            impact=payload.get("impact", ImpactDirection.UNKNOWN),
            symbols=_sequence_or_empty(payload.get("symbols")),
            confidence_score=payload.get("confidence_score", 0.5),
            data=dict(payload.get("data") or {}),
        )


@dataclass(frozen=True, slots=True)
class RiskSignal:
    """A normalized risk signal detected by an agent."""

    title: str
    description: str
    level: RiskLevel
    symbols: tuple[str, ...] = ()
    metric_name: str | None = None
    metric_value: float | None = None
    threshold: float | None = None
    data: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(self, "title", _required_text(self.title, "title"))
        object.__setattr__(
            self,
            "description",
            _required_text(self.description, "description"),
        )
        object.__setattr__(self, "level", _coerce_enum(RiskLevel, self.level, "level"))
        object.__setattr__(self, "symbols", _normalize_text_tuple(self.symbols))
        object.__setattr__(self, "metric_name", _optional_text(self.metric_name))
        object.__setattr__(
            self,
            "metric_value",
            _optional_number(self.metric_value, "metric_value"),
        )
        object.__setattr__(
            self,
            "threshold",
            _optional_number(self.threshold, "threshold"),
        )
        object.__setattr__(self, "data", _normalize_data(self.data, "data"))

    def to_dict(self) -> dict[str, Any]:
        """Return a JSON-friendly representation."""

        return {
            "title": self.title,
            "description": self.description,
            "level": self.level.value,
            "symbols": list(self.symbols),
            "metric_name": self.metric_name,
            "metric_value": self.metric_value,
            "threshold": self.threshold,
            "data": to_jsonable(self.data),
        }

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> RiskSignal:
        """Build a risk signal from a mapping."""

        return cls(
            title=payload.get("title", ""),
            description=payload.get("description", ""),
            level=payload.get("level", RiskLevel.MEDIUM),
            symbols=_sequence_or_empty(payload.get("symbols")),
            metric_name=payload.get("metric_name"),
            metric_value=payload.get("metric_value"),
            threshold=payload.get("threshold"),
            data=dict(payload.get("data") or {}),
        )


@dataclass(frozen=True, slots=True)
class MarketAssetSnapshot:
    """Market data snapshot for one crypto asset."""

    asset_id: str
    symbol: str
    name: str
    current_price: float | None = None
    market_cap: float | None = None
    total_volume: float | None = None
    price_change_percentage_24h: float | None = None
    price_change_percentage_7d: float | None = None
    last_updated: datetime | None = None
    data: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(self, "asset_id", _required_text(self.asset_id, "asset_id"))
        object.__setattr__(
            self,
            "symbol",
            _required_text(self.symbol, "symbol").lower(),
        )
        object.__setattr__(self, "name", _required_text(self.name, "name"))
        object.__setattr__(
            self,
            "current_price",
            _optional_number(self.current_price, "current_price"),
        )
        object.__setattr__(
            self,
            "market_cap",
            _optional_number(self.market_cap, "market_cap"),
        )
        object.__setattr__(
            self,
            "total_volume",
            _optional_number(self.total_volume, "total_volume"),
        )
        object.__setattr__(
            self,
            "price_change_percentage_24h",
            _optional_number(
                self.price_change_percentage_24h,
                "price_change_percentage_24h",
            ),
        )
        object.__setattr__(
            self,
            "price_change_percentage_7d",
            _optional_number(
                self.price_change_percentage_7d,
                "price_change_percentage_7d",
            ),
        )
        if self.last_updated is not None:
            object.__setattr__(
                self,
                "last_updated",
                _normalize_datetime(self.last_updated, "last_updated"),
            )
        object.__setattr__(self, "data", _normalize_data(self.data, "data"))

    def to_dict(self) -> dict[str, Any]:
        """Return a JSON-friendly representation."""

        return {
            "asset_id": self.asset_id,
            "symbol": self.symbol,
            "name": self.name,
            "current_price": self.current_price,
            "market_cap": self.market_cap,
            "total_volume": self.total_volume,
            "price_change_percentage_24h": self.price_change_percentage_24h,
            "price_change_percentage_7d": self.price_change_percentage_7d,
            "last_updated": (
                self.last_updated.isoformat() if self.last_updated else None
            ),
            "data": to_jsonable(self.data),
        }

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> MarketAssetSnapshot:
        """Build a market asset snapshot from a mapping."""

        last_updated = payload.get("last_updated")
        return cls(
            asset_id=payload.get("asset_id", ""),
            symbol=payload.get("symbol", ""),
            name=payload.get("name", ""),
            current_price=payload.get("current_price"),
            market_cap=payload.get("market_cap"),
            total_volume=payload.get("total_volume"),
            price_change_percentage_24h=payload.get("price_change_percentage_24h"),
            price_change_percentage_7d=payload.get("price_change_percentage_7d"),
            last_updated=(
                _normalize_datetime(last_updated, "last_updated")
                if last_updated is not None
                else None
            ),
            data=dict(payload.get("data") or {}),
        )


@dataclass(frozen=True, slots=True)
class AgentResult:
    """Standard result envelope returned by every analysis agent."""

    agent_name: AgentName
    summary: str
    key_findings: tuple[AgentFinding, ...] = ()
    risks: tuple[RiskSignal, ...] = ()
    confidence_score: float = 0.5
    sources: tuple[SourceReference, ...] = ()
    generated_at: datetime = field(default_factory=_utc_now)
    data: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "agent_name",
            _coerce_enum(AgentName, self.agent_name, "agent_name"),
        )
        object.__setattr__(self, "summary", _required_text(self.summary, "summary"))
        object.__setattr__(
            self,
            "key_findings",
            _normalize_object_tuple(
                self.key_findings,
                AgentFinding,
                "key_findings",
            ),
        )
        object.__setattr__(
            self,
            "risks",
            _normalize_object_tuple(self.risks, RiskSignal, "risks"),
        )
        object.__setattr__(
            self,
            "confidence_score",
            _probability(self.confidence_score, "confidence_score"),
        )
        object.__setattr__(
            self,
            "sources",
            _normalize_object_tuple(self.sources, SourceReference, "sources"),
        )
        object.__setattr__(
            self,
            "generated_at",
            _normalize_datetime(self.generated_at, "generated_at"),
        )
        object.__setattr__(self, "data", _normalize_data(self.data, "data"))

    @property
    def highest_risk_level(self) -> RiskLevel | None:
        """Return the highest risk level detected by this agent."""

        if not self.risks:
            return None

        return max(self.risks, key=lambda risk: RISK_LEVEL_RANK[risk.level]).level

    def has_risk_at_or_above(self, threshold: RiskLevel | str) -> bool:
        """Return True when at least one risk reaches the requested threshold."""

        normalized_threshold = _coerce_enum(RiskLevel, threshold, "threshold")
        return any(
            risk_level_at_or_above(risk.level, normalized_threshold)
            for risk in self.risks
        )

    def to_dict(self) -> dict[str, Any]:
        """Return a JSON-friendly representation."""

        return {
            "agent_name": self.agent_name.value,
            "summary": self.summary,
            "key_findings": [finding.to_dict() for finding in self.key_findings],
            "risks": [risk.to_dict() for risk in self.risks],
            "confidence_score": self.confidence_score,
            "sources": [source.to_dict() for source in self.sources],
            "generated_at": self.generated_at.isoformat(),
            "data": to_jsonable(self.data),
        }

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> AgentResult:
        """Build an agent result from a mapping."""

        return cls(
            agent_name=payload.get("agent_name", ""),
            summary=payload.get("summary", ""),
            key_findings=tuple(
                AgentFinding.from_dict(finding)
                for finding in payload.get("key_findings", ())
            ),
            risks=tuple(
                RiskSignal.from_dict(risk)
                for risk in payload.get("risks", ())
            ),
            confidence_score=payload.get("confidence_score", 0.5),
            sources=tuple(
                SourceReference.from_dict(source)
                for source in payload.get("sources", ())
            ),
            generated_at=_normalize_datetime(
                payload.get("generated_at", _utc_now()),
                "generated_at",
            ),
            data=dict(payload.get("data") or {}),
        )


@dataclass(frozen=True, slots=True)
class AgentReport:
    """Step-oriented agent report envelope with explicit status and errors."""

    agent_name: str
    status: AgentStatus
    summary: str
    risk_level: RiskLevel
    confidence: float
    findings: tuple[AgentFinding, ...] = ()
    sources: tuple[SourceReference, ...] = ()
    errors: tuple[str, ...] = ()
    generated_at: datetime = field(default_factory=_utc_now)
    data: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "agent_name",
            _required_text(self.agent_name, "agent_name").lower(),
        )
        object.__setattr__(
            self,
            "status",
            _coerce_enum(AgentStatus, self.status, "status"),
        )
        object.__setattr__(self, "summary", _required_text(self.summary, "summary"))
        object.__setattr__(
            self,
            "risk_level",
            _coerce_enum(RiskLevel, self.risk_level, "risk_level"),
        )
        object.__setattr__(
            self,
            "confidence",
            _probability(self.confidence, "confidence"),
        )
        object.__setattr__(
            self,
            "findings",
            _normalize_object_tuple(self.findings, AgentFinding, "findings"),
        )
        object.__setattr__(
            self,
            "sources",
            _normalize_object_tuple(self.sources, SourceReference, "sources"),
        )
        object.__setattr__(
            self,
            "errors",
            _normalize_text_tuple(self.errors, lowercase=False),
        )
        object.__setattr__(
            self,
            "generated_at",
            _normalize_datetime(self.generated_at, "generated_at"),
        )
        object.__setattr__(self, "data", _normalize_data(self.data, "data"))

    def to_dict(self) -> dict[str, Any]:
        """Return a JSON-friendly representation."""

        return {
            "agent_name": self.agent_name,
            "status": self.status.value,
            "summary": self.summary,
            "risk_level": self.risk_level.value,
            "confidence": self.confidence,
            "findings": [finding.to_dict() for finding in self.findings],
            "sources": [source.to_dict() for source in self.sources],
            "errors": list(self.errors),
            "generated_at": self.generated_at.isoformat(),
            "data": to_jsonable(self.data),
        }

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> AgentReport:
        """Build an agent report from a mapping."""

        return cls(
            agent_name=payload.get("agent_name", ""),
            status=payload.get("status", AgentStatus.FAILED),
            summary=payload.get("summary", ""),
            risk_level=payload.get("risk_level", RiskLevel.MEDIUM),
            confidence=payload.get("confidence", 0),
            findings=tuple(
                AgentFinding.from_dict(finding)
                for finding in payload.get("findings", ())
            ),
            sources=tuple(
                SourceReference.from_dict(source)
                for source in payload.get("sources", ())
            ),
            errors=_sequence_or_empty(payload.get("errors")),
            generated_at=_normalize_datetime(
                payload.get("generated_at", _utc_now()),
                "generated_at",
            ),
            data=dict(payload.get("data") or {}),
        )


@dataclass(frozen=True, slots=True)
class FinalReport:
    """Final synthesis report assembled from agent results."""

    title: str
    market_summary: str
    cryptos_to_watch: tuple[str, ...]
    important_risks: tuple[RiskSignal, ...]
    confidence_score: float
    conclusion: str
    agent_results: tuple[AgentResult, ...] = ()
    contradictions: tuple[str, ...] = ()
    sentiment: SentimentLabel = SentimentLabel.UNKNOWN
    generated_at: datetime = field(default_factory=_utc_now)
    language: str = "fr"
    global_risk_level: RiskLevel = RiskLevel.LOW
    confidence: float | None = None
    key_findings: tuple[AgentFinding, ...] = ()
    assets_to_watch: tuple[str, ...] = ()
    warnings: tuple[str, ...] = ()
    agent_reports: tuple[AgentReport, ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(self, "title", _required_text(self.title, "title"))
        object.__setattr__(
            self,
            "market_summary",
            _required_text(self.market_summary, "market_summary"),
        )
        object.__setattr__(
            self,
            "cryptos_to_watch",
            _normalize_text_tuple(self.cryptos_to_watch),
        )
        object.__setattr__(
            self,
            "important_risks",
            _normalize_object_tuple(
                self.important_risks,
                RiskSignal,
                "important_risks",
            ),
        )
        object.__setattr__(
            self,
            "confidence_score",
            _probability(self.confidence_score, "confidence_score"),
        )
        object.__setattr__(
            self,
            "conclusion",
            _required_text(self.conclusion, "conclusion"),
        )
        object.__setattr__(
            self,
            "agent_results",
            _normalize_object_tuple(
                self.agent_results,
                AgentResult,
                "agent_results",
            ),
        )
        object.__setattr__(
            self,
            "contradictions",
            _normalize_text_tuple(self.contradictions, lowercase=False),
        )
        object.__setattr__(
            self,
            "sentiment",
            _coerce_enum(SentimentLabel, self.sentiment, "sentiment"),
        )
        object.__setattr__(
            self,
            "generated_at",
            _normalize_datetime(self.generated_at, "generated_at"),
        )
        object.__setattr__(self, "language", _required_text(self.language, "language"))
        global_risk_level = _coerce_enum(
            RiskLevel,
            self.global_risk_level,
            "global_risk_level",
        )
        if self.important_risks:
            highest_risk = max(
                (risk.level for risk in self.important_risks),
                key=lambda level: RISK_LEVEL_RANK[level],
            )
            if RISK_LEVEL_RANK[highest_risk] > RISK_LEVEL_RANK[global_risk_level]:
                global_risk_level = highest_risk
        object.__setattr__(self, "global_risk_level", global_risk_level)
        confidence = self.confidence_score if self.confidence is None else self.confidence
        object.__setattr__(self, "confidence", _probability(confidence, "confidence"))
        object.__setattr__(
            self,
            "key_findings",
            _normalize_object_tuple(self.key_findings, AgentFinding, "key_findings"),
        )
        object.__setattr__(
            self,
            "assets_to_watch",
            _normalize_text_tuple(self.assets_to_watch or self.cryptos_to_watch),
        )
        object.__setattr__(
            self,
            "warnings",
            _normalize_text_tuple(self.warnings, lowercase=False),
        )
        object.__setattr__(
            self,
            "agent_reports",
            _normalize_object_tuple(self.agent_reports, AgentReport, "agent_reports"),
        )

    def has_risk_at_or_above(self, threshold: RiskLevel | str) -> bool:
        """Return True when the final report contains a high enough risk."""

        normalized_threshold = _coerce_enum(RiskLevel, threshold, "threshold")
        if risk_level_at_or_above(self.global_risk_level, normalized_threshold):
            return True

        return any(
            risk_level_at_or_above(risk.level, normalized_threshold)
            for risk in self.important_risks
        )

    def to_dict(self) -> dict[str, Any]:
        """Return a JSON-friendly representation."""

        return {
            "title": self.title,
            "market_summary": self.market_summary,
            "cryptos_to_watch": list(self.cryptos_to_watch),
            "important_risks": [risk.to_dict() for risk in self.important_risks],
            "confidence_score": self.confidence_score,
            "conclusion": self.conclusion,
            "agent_results": [result.to_dict() for result in self.agent_results],
            "contradictions": list(self.contradictions),
            "sentiment": self.sentiment.value,
            "generated_at": self.generated_at.isoformat(),
            "language": self.language,
            "global_risk_level": self.global_risk_level.value,
            "confidence": self.confidence,
            "key_findings": [finding.to_dict() for finding in self.key_findings],
            "assets_to_watch": list(self.assets_to_watch),
            "warnings": list(self.warnings),
            "agent_reports": [report.to_dict() for report in self.agent_reports],
        }

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> FinalReport:
        """Build a final report from a mapping."""

        return cls(
            title=payload.get("title", ""),
            market_summary=payload.get("market_summary", ""),
            cryptos_to_watch=_sequence_or_empty(
                payload.get("cryptos_to_watch", payload.get("assets_to_watch")),
            ),
            important_risks=tuple(
                RiskSignal.from_dict(risk)
                for risk in payload.get("important_risks", ())
            ),
            confidence_score=payload.get(
                "confidence_score",
                payload.get("confidence", 0.5),
            ),
            conclusion=payload.get("conclusion", ""),
            agent_results=tuple(
                AgentResult.from_dict(result)
                for result in payload.get("agent_results", ())
            ),
            contradictions=_sequence_or_empty(payload.get("contradictions")),
            sentiment=payload.get("sentiment", SentimentLabel.UNKNOWN),
            generated_at=_normalize_datetime(
                payload.get("generated_at", _utc_now()),
                "generated_at",
            ),
            language=payload.get("language", "fr"),
            global_risk_level=payload.get("global_risk_level", RiskLevel.LOW),
            confidence=payload.get("confidence"),
            key_findings=tuple(
                AgentFinding.from_dict(finding)
                for finding in payload.get("key_findings", ())
            ),
            assets_to_watch=_sequence_or_empty(payload.get("assets_to_watch")),
            warnings=_sequence_or_empty(payload.get("warnings")),
            agent_reports=tuple(
                AgentReport.from_dict(report)
                for report in payload.get("agent_reports", ())
            ),
        )


def risk_level_at_or_above(level: RiskLevel | str, threshold: RiskLevel | str) -> bool:
    """Return True when level is equal to or more severe than threshold."""

    normalized_level = _coerce_enum(RiskLevel, level, "level")
    normalized_threshold = _coerce_enum(RiskLevel, threshold, "threshold")
    return RISK_LEVEL_RANK[normalized_level] >= RISK_LEVEL_RANK[normalized_threshold]


def to_jsonable(value: Any) -> Any:
    """Convert schema values to objects accepted by json.dumps."""

    if isinstance(value, StrEnum):
        return value.value
    if isinstance(value, datetime):
        return _normalize_datetime(value, "value").isoformat()
    if hasattr(value, "to_dict") and callable(value.to_dict):
        return value.to_dict()
    if isinstance(value, Mapping):
        return {str(key): to_jsonable(item) for key, item in value.items()}
    if isinstance(value, tuple | list | set):
        return [to_jsonable(item) for item in value]

    return value


def _required_text(value: Any, field_name: str) -> str:
    if not isinstance(value, str):
        raise SchemaError(f"{field_name} must be a string.")

    cleaned = value.strip()
    if not cleaned:
        raise SchemaError(f"{field_name} cannot be empty.")

    return cleaned


def _optional_text(value: Any) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise SchemaError("Optional text values must be strings or None.")

    cleaned = value.strip()
    return cleaned or None


def _probability(value: Any, field_name: str) -> float:
    number = _required_number(value, field_name)
    if number < 0 or number > 1:
        raise SchemaError(f"{field_name} must be between 0 and 1.")

    return number


def _required_number(value: Any, field_name: str) -> float:
    if isinstance(value, bool) or not isinstance(value, int | float):
        raise SchemaError(f"{field_name} must be a number.")

    return float(value)


def _optional_number(value: Any, field_name: str) -> float | None:
    if value is None:
        return None

    return _required_number(value, field_name)


def _normalize_data(value: Any, field_name: str) -> dict[str, Any]:
    if value is None:
        return {}
    if not isinstance(value, Mapping):
        raise SchemaError(f"{field_name} must be a mapping.")

    return dict(value)


def _normalize_text_tuple(value: Any, *, lowercase: bool = True) -> tuple[str, ...]:
    if value is None:
        return ()
    if isinstance(value, str) or not isinstance(value, Sequence):
        raise SchemaError("Expected a sequence of strings.")

    items: list[str] = []
    seen: set[str] = set()
    for item in value:
        cleaned = _required_text(item, "sequence item")
        if lowercase:
            cleaned = cleaned.lower()
        if cleaned not in seen:
            seen.add(cleaned)
            items.append(cleaned)

    return tuple(items)


def _normalize_object_tuple(
    value: Any,
    expected_type: type[Any],
    field_name: str,
) -> tuple[Any, ...]:
    if value is None:
        return ()
    if isinstance(value, expected_type):
        return (value,)
    if isinstance(value, str) or not isinstance(value, Sequence):
        raise SchemaError(f"{field_name} must be a sequence.")

    items: list[Any] = []
    for item in value:
        if isinstance(item, expected_type):
            items.append(item)
        elif isinstance(item, Mapping) and hasattr(expected_type, "from_dict"):
            items.append(expected_type.from_dict(item))
        else:
            raise SchemaError(
                f"{field_name} items must be {expected_type.__name__} instances."
            )

    return tuple(items)


def _coerce_enum(enum_type: type[StrEnum], value: Any, field_name: str) -> Any:
    if isinstance(value, enum_type):
        return value
    if isinstance(value, str):
        try:
            return enum_type(value.strip().lower())
        except ValueError as exc:
            allowed = ", ".join(item.value for item in enum_type)
            raise SchemaError(f"{field_name} must be one of: {allowed}.") from exc

    raise SchemaError(f"{field_name} must be a string or {enum_type.__name__}.")


def _normalize_datetime(value: Any, field_name: str) -> datetime:
    if isinstance(value, datetime):
        parsed = value
    elif isinstance(value, str):
        cleaned = value.strip()
        if cleaned.endswith("Z"):
            cleaned = f"{cleaned[:-1]}+00:00"
        try:
            parsed = datetime.fromisoformat(cleaned)
        except ValueError as exc:
            raise SchemaError(f"{field_name} must be an ISO datetime.") from exc
    else:
        raise SchemaError(f"{field_name} must be a datetime or ISO datetime string.")

    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)

    return parsed.astimezone(timezone.utc)


def _sequence_or_empty(value: Any) -> Sequence[Any]:
    if value is None:
        return ()
    if isinstance(value, str) or not isinstance(value, Sequence):
        raise SchemaError("Expected a sequence.")

    return value


Finding = AgentFinding
Source = SourceReference
