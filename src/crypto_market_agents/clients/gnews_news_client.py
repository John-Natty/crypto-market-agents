"""Opt-in, bounded GNews search client for read-only research context."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from contextlib import closing
import hashlib
import json
import re
import sqlite3
import time
from pathlib import Path
from typing import Any, Callable
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode, urlsplit, urlunsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener

from crypto_market_agents.clients.news_client import (
    NewsNetworkError,
    NewsResponseError,
    NewsTimeoutError,
    _is_timeout_reason,
    _read_text,
    _response_status,
    _validate_timeout,
)
from crypto_market_agents.http_utils import (
    CacheBackend,
    HTTPClientSettings,
    _consume_http_request_budget,
    build_cache_backend,
    build_cache_key,
    send_request_with_retries,
)
from crypto_market_agents.security import redact_text


GNEWS_ENDPOINT = "https://gnews.io/api/v4/search"
GNEWS_FREE_PLAN_DELAY_HOURS = 12
GNEWS_MAX_REQUESTS_PER_RESEARCH = 5
GNEWS_MAX_REQUESTS_PER_DAY = 20
GNEWS_MAX_RESULTS = 10
GNEWS_MAX_EXCERPT_CHARS = 250
CRYPTO_CONTEXT = re.compile(
    r"\b(crypto(?:currency)?|token|blockchain|bitcoin|ethereum|altcoin|coin|defi)\b",
    re.IGNORECASE,
)
PROMOTIONAL_CONTENT = re.compile(
    r"\b(presale|pre-sale|buy now|best crypto to buy|100x|60x|300x|guaranteed|sponsored|"
    r"next crypto to explode|buyers back|investment opportunity)\b",
    re.IGNORECASE,
)
PEPE_PROMOTION_OR_HOMONYM = re.compile(
    r"\b(pepeto|pepe['’]s cofounder|pepe['’]s co-founder|pepe builder)\b", re.IGNORECASE
)


class GNewsError(RuntimeError):
    """Base class for safe GNews errors."""


class GNewsAPIKeyMissingError(GNewsError):
    """The opt-in provider was enabled without its environment key."""


class GNewsQuotaExceededError(GNewsError):
    """A local per-run or persisted daily request allowance is exhausted."""


class GNewsNewsClient:
    """Read search metadata from GNews, never fetch article pages."""

    def __init__(
        self,
        *,
        api_key: str | None = None,
        timeout_seconds: int = 10,
        max_requests_per_research: int = GNEWS_MAX_REQUESTS_PER_RESEARCH,
        max_requests_per_day: int = GNEWS_MAX_REQUESTS_PER_DAY,
        max_results: int = GNEWS_MAX_RESULTS,
        language: str = "en",
        quota_path: str | Path = ".cache/crypto-market-agents/gnews/daily-quota.sqlite3",
        http_settings: HTTPClientSettings | None = None,
        cache: CacheBackend | None = None,
        opener: Callable[..., Any] | None = None,
        now_provider: Callable[[], datetime] | None = None,
        monotonic: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        if isinstance(max_requests_per_research, bool) or not 1 <= max_requests_per_research <= 5:
            raise ValueError("max_requests_per_research must be between 1 and 5")
        if isinstance(max_requests_per_day, bool) or not 1 <= max_requests_per_day <= 20:
            raise ValueError("max_requests_per_day must be between 1 and 20")
        if isinstance(max_results, bool) or not 1 <= max_results <= 10:
            raise ValueError("max_results must be between 1 and 10")
        if not isinstance(language, str) or not re.fullmatch(r"[a-z]{2}", language):
            raise ValueError("language must be a two-letter code")
        self.api_key = api_key.strip() if isinstance(api_key, str) and api_key.strip() else None
        if self.api_key is None:
            raise GNewsAPIKeyMissingError(
                "GNEWS_API_KEY is required when GNews is explicitly enabled"
            )
        self.timeout_seconds = _validate_timeout(timeout_seconds)
        self.max_requests_per_research = max_requests_per_research
        self.max_requests_per_day = max_requests_per_day
        self.max_results = max_results
        self.language = language
        self.quota_path = Path(quota_path)
        self.http_settings = http_settings or HTTPClientSettings.from_env()
        # Never retry an external request: a retry spends another provider request.
        self.http_settings = HTTPClientSettings(
            max_retries=0,
            backoff_seconds=0,
            cache_ttl_seconds=self.http_settings.cache_ttl_seconds,
            cache_enabled=self.http_settings.cache_enabled,
            cache_backend=self.http_settings.cache_backend,
            cache_dir=self.http_settings.cache_dir,
        )
        raw_cache = cache if cache is not None else build_cache_backend(self.http_settings)
        self._cache = (
            _CredentialScopedCache(raw_cache, self.api_key) if raw_cache is not None else None
        )
        self._opener = opener or _same_host_opener()
        self._now = now_provider or (lambda: datetime.now(timezone.utc))
        self._monotonic = monotonic
        self._sleep = sleep
        self._research_requests = 0
        self._last_network_request: float | None = None
        self._seen: dict[str, dict[str, Any]] = {}
        self._query_number = 0

    def usage_summary(self):
        """Return non-secret runtime settings and counters for a research report."""
        return {
            "enabled": True,
            "credential_present": bool(self.api_key),
            "requests_per_research_used": self._research_requests,
            "requests_per_research_limit": self.max_requests_per_research,
            "requests_per_day_used_utc": self._daily_used(),
            "requests_per_day_limit": self.max_requests_per_day,
            "max_results_per_request": self.max_results,
            "window_days_max": 30,
            "excerpt_chars_max": GNEWS_MAX_EXCERPT_CHARS,
            "http_retries": 0,
            "cache_enabled": self.http_settings.cache_enabled,
        }

    def search_market(self, *, budget_seconds: float | None = None, window_days: int = 30):
        return self._search("market", budget_seconds=budget_seconds, window_days=window_days)

    def search_asset(
        self,
        *,
        identity: dict[str, Any],
        budget_seconds: float | None = None,
        window_days: int = 30,
    ):
        if not isinstance(identity, dict) or identity.get("status") not in {
            "resolved_explicit",
            "resolved_cross_referenced",
        }:
            return self._unresolved(identity, "verified_identity_required")
        name = _verified_name(identity)
        if not name:
            return self._unresolved(identity, "verified_name_unavailable")
        if len(name.strip()) <= 2:
            return self._unresolved(identity, "verified_name_not_distinctive_enough_for_search")
        return self._search(
            "asset",
            budget_seconds=budget_seconds,
            window_days=window_days,
            identity=identity,
            query=f'"{_escape_query(name)}"',
        )

    def _unresolved(self, identity, reason):
        now = self._now().astimezone(timezone.utc)
        search_status = (
            "not_searched_name_not_distinctive"
            if reason == "verified_name_not_distinctive_enough_for_search"
            else "not_searched_identity_unresolved"
        )
        return {
            "provider": "gnews_api",
            "search_scope": "verified_asset_search",
            "asset_attribution": "not_established",
            "scope": "asset",
            "identity_status": identity.get("status") if isinstance(identity, dict) else None,
            "identity_reason": reason,
            "search_result_status": search_status,
            "articles": [],
            "excluded_articles": [],
            "duplicate_groups": [],
            "collected_at": _iso(now),
            "event_absence_established": False,
            "coverage": _coverage_payload(0, 0, search_status),
        }

    def _search(self, scope, *, budget_seconds, window_days, identity=None, query=None):
        if (
            isinstance(window_days, bool)
            or not isinstance(window_days, int)
            or not 1 <= window_days <= 30
        ):
            raise ValueError("window_days must be between 1 and 30 for the free GNews plan")
        if budget_seconds is not None and (
            isinstance(budget_seconds, bool)
            or not isinstance(budget_seconds, int | float)
            or budget_seconds <= 0
            or budget_seconds > 300
        ):
            raise ValueError("budget_seconds must be between 0 and 300 seconds")
        self._research_requests += 1
        if self._research_requests > self.max_requests_per_research:
            raise GNewsQuotaExceededError("gnews_research_request_limit_reached")

        now = self._now().astimezone(timezone.utc)
        cutoff = now - timedelta(days=window_days)
        self._query_number += 1
        query_id = f"gnews-query-{self._query_number:02d}"
        selected_query = query or "crypto OR bitcoin OR ethereum OR blockchain"
        params = {
            "q": selected_query,
            "lang": self.language,
            "max": str(self.max_results),
            "sortby": "publishedAt",
            "from": _iso(cutoff),
            "to": _iso(now),
        }
        request = Request(
            f"{GNEWS_ENDPOINT}?{urlencode(params)}",
            headers={"X-Api-Key": self.api_key or "", "Accept": "application/json"},
        )
        cache_key = build_cache_key(request)
        cached = None
        if self.http_settings.cache_enabled and self._cache is not None:
            cached = self._cache.get(cache_key)
        if cached is not None:
            response_body = cached.body
            from_cache = True
        else:
            self._wait_for_rate_limit(budget_seconds)
            self._reserve_daily_request()
            self._last_network_request = self._monotonic()
            try:
                response = send_request_with_retries(
                    request,
                    opener=self._opener,
                    timeout_seconds=self.timeout_seconds,
                    response_status=_response_status,
                    read_text=_read_text,
                    settings=self.http_settings,
                    cache=self._cache,
                    sleep=lambda _: None,
                    logger_name=__name__,
                )
            except HTTPError as exc:
                status = exc.code
                # Consume and redact the provider body, but never expose it in logs or reports.
                redact_text(_read_text(exc), secrets=(self.api_key or "",))
                exc.close()
                raise GNewsError(f"gnews_http_error_{status}") from None
            except URLError as exc:
                if _is_timeout_reason(exc.reason):
                    raise NewsTimeoutError("GNews request timed out") from None
                raise NewsNetworkError("GNews request failed") from None
            except (TimeoutError, OSError) as exc:
                if isinstance(exc, TimeoutError) or getattr(exc, "errno", None) in {110, 60}:
                    raise NewsTimeoutError("GNews request timed out") from None
                raise NewsNetworkError("GNews request failed") from None
            if response.status_code != 200:
                raise GNewsError(f"gnews_http_error_{response.status_code}")
            response_body = response.body
            from_cache = response.from_cache
        try:
            payload = json.loads(response_body)
        except json.JSONDecodeError:
            raise NewsResponseError("GNews returned invalid JSON") from None
        raw_articles = payload.get("articles") if isinstance(payload, dict) else None
        if not isinstance(raw_articles, list) or not all(
            isinstance(row, dict) for row in raw_articles
        ):
            raise NewsResponseError("GNews articles must be a list of objects")

        accepted, excluded = [], []
        publishers: set[str] = set()
        seen_response_ids: set[str] = set()
        duplicate_item_count = 0
        for raw in raw_articles[: self.max_results]:
            article = self._clean_article(raw, scope, identity, query_id, now, cutoff)
            if article["id"] in seen_response_ids:
                duplicate_item_count += 1
                continue
            seen_response_ids.add(article["id"])
            publishers.add(article.get("publisher_domain") or article.get("publisher") or "unknown")
            if article["relevance_status"] == "retained_relevant_result":
                accepted.append(article)
            else:
                excluded.append(article)
        duplicate_groups = self._duplicate_groups()
        current_count = sum(
            article["freshness_status"] == "within_requested_window"
            for article in [*accepted, *excluded]
        )
        outside_count = sum(
            article["freshness_status"] == "outside_requested_window"
            for article in [*accepted, *excluded]
        )
        unknown_date_count = sum(
            article["freshness_status"] == "unknown_or_invalid_publication_date"
            for article in [*accepted, *excluded]
        )
        coverage = _coverage_payload(len(raw_articles), len(accepted), "partial_single_provider")
        coverage.update(
            {
                "current_window_article_count": current_count,
                "outside_window_article_count": outside_count,
                "unknown_publication_date_count": unknown_date_count,
            }
        )
        current = {
            "provider": "gnews_api",
            "scope": scope,
            "search_scope": "verified_asset_search"
            if scope == "asset"
            else "general_market_context",
            "asset_attribution": (
                "verified_name_article_relevance_checked"
                if scope == "asset" and accepted
                else "no_asset_attribution_result"
                if scope == "asset"
                else "not_established"
            ),
            "identity_status": identity.get("status") if identity else None,
            "identity_reason": None if identity else "general_market_search_no_asset_attribution",
            "query_id": query_id,
            "query": selected_query,
            "search_window": {"from": _iso(cutoff), "to": _iso(now), "days": window_days},
            "dates_obtained": sorted(
                {row["published_at"] for row in [*accepted, *excluded] if row.get("published_at")}
            ),
            "collected_at": _iso(now),
            "from_cache": from_cache,
            "articles": accepted,
            "excluded_articles": excluded,
            "duplicate_groups": duplicate_groups,
            "duplicate_item_count": duplicate_item_count,
            "publisher_count": len(publishers),
            "source_independence_status": (
                "multiple_publishers_observed"
                if len(publishers) > 1
                else "single_publisher_or_none"
            ),
            "search_result_status": (
                "results_observed"
                if accepted
                else "no_relevant_results_observed"
                if raw_articles
                else "no_results_in_requested_window"
            ),
            "event_absence_established": False,
            "coverage": coverage,
            "limits": {
                "free_plan_delay_hours": GNEWS_FREE_PLAN_DELAY_HOURS,
                "coverage": "partial_provider_index; no completeness guarantee",
                "content_excerpt_max_chars": GNEWS_MAX_EXCERPT_CHARS,
                "article_pages_fetched": False,
                "paid_tier_fallback": False,
            },
            "quota": {
                "research_requests_used": self._research_requests,
                "research_requests_limit": self.max_requests_per_research,
                "daily_requests_used": self._daily_used(),
                "daily_requests_limit": self.max_requests_per_day,
                "network_request_counted": not from_cache,
            },
        }
        return current

    def _clean_article(self, raw, scope, identity, query_id, now, cutoff):
        title = _clean_text(raw.get("title"), 500)
        description = _clean_text(raw.get("description"), GNEWS_MAX_EXCERPT_CHARS)
        content = _clean_text(raw.get("content"), GNEWS_MAX_EXCERPT_CHARS)
        excerpt = content or description or None
        source = raw.get("source") if isinstance(raw.get("source"), dict) else {}
        publisher = _clean_text(source.get("name"), 160) or "unknown"
        publisher_url = _clean_text(source.get("url"), 500)
        publisher_domain = _publisher_domain(publisher_url or publisher)
        url = _clean_text(raw.get("url"), 2000)
        published, date_status = _parse_date(raw.get("publishedAt"))
        freshness = (
            "unknown_or_invalid_publication_date"
            if published is None
            else "outside_requested_window"
            if published < cutoff or published > now
            else "within_requested_window"
        )
        content_for_relevance = " ".join(value for value in (title, description, excerpt) if value)
        relevance, reason, attribution = _relevance(scope, identity, title, content_for_relevance)
        identity_key = _article_identity_key(url, title, publisher)
        group_id = hashlib.sha256(identity_key.encode()).hexdigest()[:16]
        prior = self._seen.setdefault(
            identity_key,
            {
                "id": hashlib.sha256(identity_key.encode()).hexdigest()[:20],
                "group_id": group_id,
                "seen_in_queries": [],
                "publishers": set(),
                "publisher_domains": set(),
                "urls": set(),
            },
        )
        if query_id not in prior["seen_in_queries"]:
            prior["seen_in_queries"].append(query_id)
        prior["publishers"].add(publisher)
        prior["publisher_domains"].add(publisher_domain)
        if url:
            prior["urls"].add(url)
        domains = sorted(prior["publisher_domains"])
        return {
            "provider": "gnews_api",
            "id": prior["id"],
            "duplicate_group_id": group_id,
            "title": title,
            "publisher": publisher,
            "publisher_url": publisher_url,
            "publisher_domain": publisher_domain,
            "url": url,
            "excerpt": excerpt,
            "excerpt_source": "content" if content else "description" if description else None,
            "is_full_article": False,
            "published_at": _iso(published) if published is not None else None,
            "published_at_raw": _clean_text(raw.get("publishedAt"), 80),
            "publication_date_status": date_status,
            "collected_at": _iso(now),
            "freshness_status": freshness,
            "relevance_status": relevance,
            "exclusion_reason": reason,
            "attribution_status": attribution,
            "source_type": "news_article",
            "source_independence_status": "multiple_publishers_observed"
            if len(domains) > 1
            else "single_publisher",
            "observed_publishers": sorted(prior["publishers"]),
            "observed_publisher_domains": domains,
            "observed_in_queries": list(prior["seen_in_queries"]),
            "coverage_group_id": group_id,
            "external_content_untrusted": True,
        }

    def _duplicate_groups(self):
        groups = []
        for entry in self._seen.values():
            if len(entry["seen_in_queries"]) > 1:
                groups.append(
                    {
                        "id": entry["group_id"],
                        "article_id": entry["id"],
                        "query_count": len(entry["seen_in_queries"]),
                        "query_refs": list(entry["seen_in_queries"]),
                        "publisher_count": len(entry["publisher_domains"]),
                        "publisher_domains": sorted(entry["publisher_domains"]),
                        "publishers": sorted(entry["publishers"]),
                        "repeated_coverage_is_not_independent_confirmation": True,
                    }
                )
        return groups[:30]

    def _wait_for_rate_limit(self, budget_seconds):
        if self._last_network_request is None:
            return
        wait = max(0.0, 1.0 - (self._monotonic() - self._last_network_request))
        if wait <= 0:
            return
        if budget_seconds is not None and wait >= budget_seconds:
            raise TimeoutError("gnews_rate_limit_wait_exceeds_remaining_budget")
        self._sleep(wait)

    def _reserve_daily_request(self):
        self.quota_path.parent.mkdir(parents=True, exist_ok=True)
        day = self._now().astimezone(timezone.utc).date().isoformat()
        with closing(sqlite3.connect(self.quota_path, timeout=5)) as db:
            db.execute(
                "CREATE TABLE IF NOT EXISTS daily_requests (day TEXT PRIMARY KEY, count INTEGER NOT NULL)"
            )
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT count FROM daily_requests WHERE day = ?", (day,)).fetchone()
            count = row[0] if row else 0
            if count >= self.max_requests_per_day:
                db.rollback()
                raise GNewsQuotaExceededError("gnews_daily_request_limit_reached")
            db.execute(
                "INSERT INTO daily_requests(day, count) VALUES(?, 1) "
                "ON CONFLICT(day) DO UPDATE SET count = count + 1",
                (day,),
            )
            db.commit()

    def _daily_used(self):
        if not self.quota_path.exists():
            return 0
        day = self._now().astimezone(timezone.utc).date().isoformat()
        try:
            with closing(sqlite3.connect(self.quota_path, timeout=5)) as db:
                row = db.execute(
                    "SELECT count FROM daily_requests WHERE day = ?", (day,)
                ).fetchone()
        except sqlite3.OperationalError:
            return 0
        return row[0] if row else 0


def _coverage_payload(returned_count, retained_count, status):
    return {
        "status": status,
        "provider": "gnews_api",
        "sources_consulted": ["GNews indexed sources"],
        "returned_count": returned_count,
        "retained_relevant_count": retained_count,
        "coverage_missing": ["unindexed publishers", "delayed or excluded free-plan content"],
        "event_absence_established": False,
    }


def _verified_name(identity):
    for key in ("coingecko_name", "bitvavo_name"):
        value = identity.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()[:100]
    return None


def _escape_query(value):
    return value.replace('"', " ").replace("\\", " ").strip()


def _relevance(scope, identity, title, text):
    if not title:
        return "off_topic", "missing_title", "not_established"
    if PROMOTIONAL_CONTENT.search(text):
        return "promotional", "promotional_language_detected", "not_established"
    if scope == "market":
        if CRYPTO_CONTEXT.search(text):
            return "retained_relevant_result", None, "general_market_context_only"
        return "off_topic", "no_crypto_context_in_returned_text", "not_established"
    if PEPE_PROMOTION_OR_HOMONYM.search(text):
        return "ambiguous", "pepe_pepeto_or_cofounder_homonym", "not_established"
    name = _verified_name(identity or {})
    if not name or not _whole_phrase_match(name, text):
        return "off_topic", "verified_name_not_found_in_article_metadata", "not_established"
    # Common names need a token-specific cue; the ticker is not a query alias.
    if name.casefold() in {"pepe", "pepe coin"}:
        ticker_cue = re.search(r"(?<![A-Za-z0-9])PEPE(?![A-Za-z0-9])", text)
        token_context = re.search(
            r"\b(token|coin|price|rall(?:y|ies|ied)|crypto|jumps?|surges?)\b", text, re.I
        )
        if ticker_cue and token_context:
            return "retained_relevant_result", None, "verified_name_and_token_specific_cue"
        return "ambiguous", "common_name_without_token_specific_cue", "not_established"
    if not CRYPTO_CONTEXT.search(text):
        return "ambiguous", "verified_name_match_without_crypto_context", "not_established"
    return "retained_relevant_result", None, "verified_name_match_not_independently_verified"


def _whole_phrase_match(phrase, text):
    return bool(re.search(r"(?<!\w)" + re.escape(phrase) + r"(?!\w)", text, re.IGNORECASE))


def _article_identity_key(url, title, publisher):
    if url:
        parts = urlsplit(url)
        normalized = urlunsplit(
            (parts.scheme.lower(), parts.netloc.lower(), parts.path.rstrip("/"), "", "")
        )
        return "url:" + normalized
    return "title:" + " ".join((title or "").casefold().split()) + "|" + publisher.casefold()


def _publisher_domain(publisher):
    value = publisher.strip().lower()
    if "://" in value:
        return urlsplit(value).hostname or value
    return value


def _parse_date(value):
    if not isinstance(value, str) or not value.strip():
        return None, "unknown"
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None, "invalid"
    if parsed.tzinfo is None:
        return None, "invalid"
    return parsed.astimezone(timezone.utc), "valid"


def _clean_text(value, limit):
    if not isinstance(value, str):
        return None
    cleaned = " ".join(value.split())
    return cleaned[:limit]


def _iso(value):
    return value.astimezone(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def _same_host_opener():
    origin = urlsplit(GNEWS_ENDPOINT)

    class SameHostRedirectHandler(HTTPRedirectHandler):
        def redirect_request(self, request, file_pointer, code, message, headers, new_url):
            target = urlsplit(new_url)
            if target.scheme != "https" or target.hostname != origin.hostname:
                return None
            _consume_http_request_budget()
            return super().redirect_request(request, file_pointer, code, message, headers, new_url)

    opener = build_opener(SameHostRedirectHandler())
    return lambda request, timeout: opener.open(request, timeout=timeout)


class _CredentialScopedCache:
    """Keep cache entries for different GNews accounts isolated without storing keys."""

    def __init__(self, backend: CacheBackend, api_key: str) -> None:
        self.backend = backend
        self.scope = hashlib.sha256(api_key.encode("utf-8")).hexdigest()

    def get(self, key):
        return self.backend.get(f"gnews-account:{self.scope}:{key}")

    def set(self, key, response, ttl_seconds):
        self.backend.set(f"gnews-account:{self.scope}:{key}", response, ttl_seconds)
