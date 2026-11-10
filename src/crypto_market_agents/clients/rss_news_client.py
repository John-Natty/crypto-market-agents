"""Bounded RSS/Atom reader for explicitly configured public feeds."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from dataclasses import replace
from email.utils import parsedate_to_datetime
from html.parser import HTMLParser
import hashlib
import ipaddress
import json
import re
import socket
import time
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener
import xml.etree.ElementTree as ET

from crypto_market_agents.clients.news_client import (
    NewsNetworkError,
    NewsResponseError,
    NewsTimeoutError,
    _response_status,
    _validate_timeout,
)
from crypto_market_agents.http_utils import (
    CacheBackend,
    HTTPClientSettings,
    _consume_http_request_budget,
    build_cache_backend,
    send_request_with_retries,
)


MAX_FEEDS = 10
MAX_FEED_BYTES = 256_000
MAX_FEED_ITEMS = 40
MAX_ARTICLES_RETURNED = 30
MAX_TITLE_CHARS = 500
MAX_EXCERPT_CHARS = 1_000
MAX_URL_CHARS = 2_000
ALLOWED_SOURCE_TYPES = {
    "news_article",
    "official_announcement",
    "press_release",
    "promotional_content",
}
ALLOWED_COVERAGE_DOMAINS = {"macro", "regulatory", "market_general", "project", "platform"}
TRACKING_QUERY_KEYS = {"fbclid", "gclid", "ref", "source"}


class RSSNewsClientError(RuntimeError):
    """RSS or Atom fetching/parsing error."""


class RSSNewsClient:
    """Fetch configured HTTPS feeds; never fetch article links from feed entries."""

    def __init__(
        self,
        feeds: list[dict[str, Any]],
        *,
        timeout_seconds: int = 10,
        max_items_per_feed: int = 20,
        max_feed_bytes: int = MAX_FEED_BYTES,
        max_articles: int = 20,
        opener=None,
        http_settings: HTTPClientSettings | None = None,
        cache: CacheBackend | None = None,
        sleep=None,
        now_provider=None,
    ):
        self.feeds = _validate_feeds(feeds)
        self.timeout_seconds = _validate_timeout(timeout_seconds)
        if isinstance(max_items_per_feed, bool) or not 1 <= max_items_per_feed <= MAX_FEED_ITEMS:
            raise ValueError("max_items_per_feed must be between 1 and 40")
        if isinstance(max_feed_bytes, bool) or not 4_096 <= max_feed_bytes <= MAX_FEED_BYTES:
            raise ValueError("max_feed_bytes must be between 4096 and 256000")
        if isinstance(max_articles, bool) or not 1 <= max_articles <= MAX_ARTICLES_RETURNED:
            raise ValueError("max_articles must be between 1 and 30")
        self.max_items_per_feed = max_items_per_feed
        self.max_feed_bytes = max_feed_bytes
        self.max_articles = max_articles
        self._opener = opener or _same_host_opener()
        self.http_settings = http_settings or HTTPClientSettings.from_env()
        self._cache = cache if cache is not None else build_cache_backend(self.http_settings)
        self._sleep = sleep or time.sleep
        self._now = now_provider or (lambda: datetime.now(timezone.utc))

    @classmethod
    def from_config_file(cls, path, **kwargs):
        with open(path, encoding="utf-8") as handle:
            payload = json.load(handle)
        if not isinstance(payload, dict) or payload.get("schema_version") != 1:
            raise ValueError("RSS source config must use schema_version 1")
        return cls(payload.get("feeds"), **kwargs)

    def search_market(
        self,
        *,
        source_ids=None,
        budget_seconds: float | None = None,
        window_days: int = 30,
    ) -> dict[str, Any]:
        return self._search(
            scope="market",
            source_ids=source_ids,
            budget_seconds=budget_seconds,
            window_days=window_days,
        )

    def search_asset(
        self,
        *,
        identity: dict[str, Any],
        source_ids=None,
        budget_seconds: float | None = None,
        window_days: int = 30,
    ) -> dict[str, Any]:
        _validate_window_days(window_days)
        if not isinstance(identity, dict) or identity.get("status") not in {
            "resolved_explicit",
            "resolved_cross_referenced",
        }:
            return {
                "scope": "asset",
                "identity_status": identity.get("status") if isinstance(identity, dict) else None,
                "identity_reason": "verified_identity_required",
                "articles": [],
                "coverage_groups": [],
                "source_results": [],
                "collected_at": self._collected_at(),
                "search_scope": "verified_asset_search",
                "asset_attribution": "not_established",
                "search_window": _search_window(self._collected_at(), window_days),
                "coverage": {
                    "status": "not_searched_identity_unresolved",
                    "applicable_sources": 0,
                    "failed_sources": [],
                    "not_executed_sources": [],
                },
                "search_result_status": "not_searched_identity_unresolved",
                "event_absence_established": False,
            }
        aliases = _verified_aliases(identity)
        if not aliases:
            return {
                "scope": "asset",
                "identity_status": identity.get("status"),
                "identity_reason": "no_verified_search_alias",
                "articles": [],
                "coverage_groups": [],
                "source_results": [],
                "collected_at": self._collected_at(),
                "search_scope": "verified_asset_search",
                "asset_attribution": "not_established",
                "search_window": _search_window(self._collected_at(), window_days),
                "coverage": {
                    "status": "not_searched_no_verified_alias",
                    "applicable_sources": 0,
                    "failed_sources": [],
                    "not_executed_sources": [],
                },
                "search_result_status": "not_searched_no_verified_alias",
                "event_absence_established": False,
            }
        result = self._search(
            scope="asset",
            source_ids=source_ids,
            budget_seconds=budget_seconds,
            provider_id=identity.get("provider_id"),
            window_days=window_days,
        )
        matched = []
        for article in result["articles"]:
            found_aliases = _matching_aliases(article, aliases)
            official_feed = article.get("configured_asset_id_match") is True
            if found_aliases or official_feed:
                item = dict(article)
                item["matched_identity_aliases"] = found_aliases
                item["attribution_status"] = (
                    "verified_official_asset_feed"
                    if official_feed
                    else "possible_exact_verified_name_match"
                )
                item["relevance_status"] = (
                    "verified_asset_feed_source"
                    if official_feed
                    else "possible_exact_name_match_unconfirmed"
                )
                matched.append(item)
        result["articles"] = matched[: self.max_articles]
        result["identity_status"] = identity["status"]
        result["identity_reason"] = None
        result["search_scope"] = "verified_asset_search"
        has_verified_feed_match = any(
            article.get("attribution_status") == "verified_official_asset_feed"
            for article in matched
        )
        has_possible_name_match = any(
            article.get("attribution_status") == "possible_exact_verified_name_match"
            for article in matched
        )
        result["asset_attribution"] = (
            "mixed_verified_and_possible_matches"
            if has_verified_feed_match and has_possible_name_match
            else "verified_official_feed_matches"
            if has_verified_feed_match
            else "possible_exact_name_matches_unconfirmed"
            if has_possible_name_match
            else "no_asset_attribution_result"
        )
        result["coverage_groups"] = _coverage_groups(result["articles"])
        _refresh_news_coverage(result, window_days=window_days)
        return result

    def _search(
        self, *, scope: str, source_ids=None, budget_seconds=None, provider_id=None, window_days=30
    ):
        if budget_seconds is not None and (
            isinstance(budget_seconds, bool)
            or not isinstance(budget_seconds, int | float)
            or budget_seconds <= 0
            or budget_seconds > 300
        ):
            raise ValueError("budget_seconds must be between 0 and 300 seconds")
        _validate_window_days(window_days)
        if source_ids is not None and (
            not isinstance(source_ids, list | tuple)
            or not source_ids
            or not all(isinstance(value, str) for value in source_ids)
        ):
            raise ValueError("source_ids must be a non-empty list of feed IDs")
        selected_ids = None if source_ids is None else set(source_ids)
        known_ids = {feed["id"] for feed in self.feeds}
        if selected_ids is not None and (not selected_ids or not selected_ids <= known_ids):
            raise ValueError("source_ids must be a non-empty subset of configured feed IDs")
        articles = []
        source_results = []
        searched_at = self._collected_at()
        deadline = time.monotonic() + budget_seconds if budget_seconds is not None else None
        for feed in self.feeds:
            if scope not in feed["scopes"] or (
                selected_ids is not None and feed["id"] not in selected_ids
            ):
                continue
            configured_asset_match = (
                scope == "asset" and provider_id is not None and provider_id in feed["asset_ids"]
            )
            if scope == "asset" and feed["asset_ids"] and not configured_asset_match:
                source_results.append(
                    {
                        "source_id": feed["id"],
                        "publisher": feed["publisher"],
                        "coverage_domain": feed.get("coverage_domain", "market_general"),
                        "status": "not_applicable",
                        "article_count": 0,
                        "error": "feed_asset_id_not_matched",
                        "collected_at": searched_at,
                    }
                )
                continue
            collected_at = self._collected_at()
            if deadline is not None and deadline - time.monotonic() <= 1.0:
                source_results.append(
                    {
                        "source_id": feed["id"],
                        "publisher": feed["publisher"],
                        "coverage_domain": feed.get("coverage_domain", "market_general"),
                        "status": "not_executed",
                        "article_count": 0,
                        "error": "news_time_budget_exhausted",
                        "collected_at": collected_at,
                    }
                )
                continue
            try:
                timeout = self.timeout_seconds
                if deadline is not None:
                    timeout = max(1, min(timeout, int(deadline - time.monotonic())))
                body = self._request_feed(feed, timeout_seconds=timeout)
                parsed = _parse_feed(
                    body,
                    feed=feed,
                    collected_at=collected_at,
                    limit=self.max_items_per_feed,
                )
                for article in parsed:
                    article["configured_asset_id_match"] = configured_asset_match
                articles.extend(parsed)
                source_results.append(
                    {
                        "source_id": feed["id"],
                        "publisher": feed["publisher"],
                        "coverage_domain": feed.get("coverage_domain", "market_general"),
                        "status": "success",
                        "article_count": len(parsed),
                        "error": None,
                        "collected_at": collected_at,
                    }
                )
            except Exception as exc:
                source_results.append(
                    {
                        "source_id": feed["id"],
                        "publisher": feed["publisher"],
                        "coverage_domain": feed.get("coverage_domain", "market_general"),
                        "status": "error",
                        "article_count": 0,
                        "error": type(exc).__name__,
                        "collected_at": collected_at,
                    }
                )
        unique = {}
        for article in articles:
            previous = unique.get(article["id"])
            if previous is None:
                article["observed_in_sources"] = [article["source_id"]]
                article["observed_publishers"] = [article["publisher"]]
                unique[article["id"]] = article
            else:
                if article["source_id"] not in previous["observed_in_sources"]:
                    previous["observed_in_sources"].append(article["source_id"])
                if article["publisher"] not in previous["observed_publishers"]:
                    previous["observed_publishers"].append(article["publisher"])
        result_articles = list(unique.values())
        result_articles.sort(
            key=lambda article: (
                article["published_at"] is not None,
                article["published_at"] or "",
            ),
            reverse=True,
        )
        result_articles = result_articles[: self.max_articles]
        result = {
            "scope": scope,
            "search_scope": "general_market_context"
            if scope == "market"
            else "verified_asset_search",
            "asset_attribution": "not_established"
            if scope == "market"
            else "pending_identity_matching",
            "search_window": {
                "requested_days": window_days,
                "published_after": _search_window(searched_at, window_days)["published_after"],
                "evaluated_at": searched_at,
            },
            "articles": result_articles,
            "coverage_groups": _coverage_groups(result_articles),
            "source_results": source_results,
            "collected_at": searched_at,
        }
        for article in result_articles:
            article["freshness_status"] = _publication_freshness(article, result["search_window"])
            article["relevance_status"] = (
                "general_context_relevance_not_established"
                if scope == "market"
                else "awaiting_identity_match"
            )
            article["coverage_domain"] = next(
                (
                    feed.get("coverage_domain", "market_general")
                    for feed in self.feeds
                    if feed["id"] == article["source_id"]
                ),
                "market_general",
            )
            if scope == "market":
                article["attribution_status"] = "not_attributed_to_asset"
        _refresh_news_coverage(result, window_days=window_days)
        return result

    def _request_feed(self, feed: dict[str, Any], *, timeout_seconds: int) -> bytes:
        request = Request(
            feed["url"],
            headers={
                "Accept": "application/atom+xml, application/rss+xml, application/xml, text/xml",
                "User-Agent": "crypto-market-agents/0.1 (RSS reader)",
            },
            method="GET",
        )

        def read_bounded(response):
            final_url = response.geturl() if hasattr(response, "geturl") else feed["url"]
            if not _same_https_host(feed["url"], final_url):
                raise RSSNewsClientError("feed_redirect_host_not_allowed")
            raw = response.read(self.max_feed_bytes + 1)
            raw = raw.encode("utf-8") if isinstance(raw, str) else bytes(raw)
            if len(raw) > self.max_feed_bytes:
                raise RSSNewsClientError("feed_response_too_large")
            return raw.decode("utf-8-sig", errors="strict")

        try:
            response = send_request_with_retries(
                request,
                opener=self._opener,
                timeout_seconds=timeout_seconds,
                response_status=_response_status,
                read_text=read_bounded,
                settings=replace(self.http_settings, max_retries=0),
                cache=self._cache,
                sleep=self._sleep,
                logger_name=__name__,
                error_body_max_bytes=4_096,
            )
        except HTTPError as exc:
            exc.close()
            raise RSSNewsClientError(f"feed_http_{exc.code}") from None
        except (TimeoutError, socket.timeout) as exc:
            raise NewsTimeoutError("RSS feed request timed out") from exc
        except URLError as exc:
            raise NewsNetworkError("RSS feed request failed") from exc
        if response.status_code != 200:
            raise RSSNewsClientError(f"feed_http_{response.status_code}")
        return response.body.encode("utf-8")

    def _collected_at(self) -> str:
        value = self._now()
        if value.tzinfo is None:
            value = value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc).isoformat()


def _validate_feeds(feeds):
    if not isinstance(feeds, list) or not 1 <= len(feeds) <= MAX_FEEDS:
        raise ValueError("feeds must contain between 1 and 10 configured feeds")
    output = []
    seen = set()
    for raw in feeds:
        if not isinstance(raw, dict) or set(raw) - {
            "id",
            "publisher",
            "url",
            "source_type",
            "scopes",
            "asset_ids",
            "coverage_domain",
        }:
            raise ValueError("feed config has invalid fields")
        feed_id = raw.get("id")
        publisher = raw.get("publisher")
        url = raw.get("url")
        source_type = raw.get("source_type")
        coverage_domain = raw.get("coverage_domain", "market_general")
        scopes = raw.get("scopes")
        if (
            not isinstance(feed_id, str)
            or not re.fullmatch(r"[a-z0-9][a-z0-9_-]{1,63}", feed_id)
            or feed_id in seen
            or not isinstance(publisher, str)
            or not publisher.strip()
            or not isinstance(source_type, str)
            or source_type not in ALLOWED_SOURCE_TYPES
            or not isinstance(coverage_domain, str)
            or coverage_domain not in ALLOWED_COVERAGE_DOMAINS
            or not isinstance(scopes, list)
            or not scopes
            or not all(isinstance(scope, str) for scope in scopes)
            or not set(scopes) <= {"market", "asset"}
            or len(scopes) != len(set(scopes))
        ):
            raise ValueError("feed config contains invalid metadata")
        parsed = urlsplit(url) if isinstance(url, str) else None
        if (
            parsed is None
            or parsed.scheme != "https"
            or not parsed.hostname
            or parsed.username
            or parsed.password
            or parsed.fragment
            or not _public_host(parsed.hostname)
            or len(url) > MAX_URL_CHARS
        ):
            raise ValueError("feed URL must be a public HTTPS URL without credentials or fragment")
        asset_ids = raw.get("asset_ids", [])
        if not isinstance(asset_ids, list) or not all(
            isinstance(value, str) and 1 <= len(value) <= 100 for value in asset_ids
        ):
            raise ValueError("asset_ids must be a list of provider IDs")
        seen.add(feed_id)
        output.append(
            {
                "id": feed_id,
                "publisher": publisher.strip()[:150],
                "url": url,
                "source_type": source_type,
                "coverage_domain": coverage_domain,
                "scopes": list(scopes),
                "asset_ids": list(asset_ids),
            }
        )
    return output


def _public_host(host):
    normalized = host.rstrip(".").casefold()
    if normalized in {"localhost", "localhost.localdomain"} or normalized.endswith(".localhost"):
        return False
    try:
        address = ipaddress.ip_address(normalized)
        return address.is_global
    except ValueError:
        try:
            return bool(normalized) and not any(
                not label or len(label) > 63 or label.startswith("-") or label.endswith("-")
                for label in normalized.split(".")
            )
        except AttributeError:
            return False


class _SameHostRedirect(HTTPRedirectHandler):
    def redirect_request(self, request, fp, code, message, headers, new_url):
        if not _same_https_host(request.full_url, new_url):
            return None
        _consume_http_request_budget()
        return super().redirect_request(request, fp, code, message, headers, new_url)


def _same_host_opener():
    return build_opener(_SameHostRedirect()).open


def _same_https_host(original, final):
    first, last = urlsplit(original), urlsplit(final)
    return (
        last.scheme == "https"
        and first.hostname is not None
        and first.hostname.casefold() == (last.hostname or "").casefold()
        and (last.port or 443) == (first.port or 443)
    )


def _parse_feed(body: bytes, *, feed, collected_at: str, limit: int):
    if len(body) > MAX_FEED_BYTES:
        raise RSSNewsClientError("feed_response_too_large")
    if re.search(rb"<!\s*(?:DOCTYPE|ENTITY)\b", body, re.IGNORECASE):
        raise RSSNewsClientError("xml_dtd_or_entity_forbidden")
    try:
        root = ET.fromstring(body)
    except ET.ParseError as exc:
        raise NewsResponseError("RSS/Atom feed is malformed") from exc
    local_root = _local_name(root.tag)
    if local_root not in {"rss", "feed", "rdf"}:
        raise NewsResponseError("XML root is not a supported RSS/Atom feed")
    entries = [element for element in root.iter() if _local_name(element.tag) in {"item", "entry"}]
    articles = []
    for entry in entries[:limit]:
        fields = _entry_fields(entry)
        title = _bounded_text(fields.get("title"), MAX_TITLE_CHARS)
        article_url = _clean_article_url(fields.get("url"))
        if not title or not article_url:
            continue
        excerpt = _bounded_text(fields.get("excerpt"), MAX_EXCERPT_CHARS)
        published_at, date_status = _parse_date(fields.get("published"))
        stable_key = (
            _canonical_url(article_url) or fields.get("guid") or f"{title}|{published_at or ''}"
        )
        article_id = hashlib.sha256(stable_key.encode("utf-8")).hexdigest()[:24]
        item_type = _item_type(
            feed["source_type"], fields.get("categories", []), fields.get("title", "")
        )
        articles.append(
            {
                "id": article_id,
                "title": title,
                "publisher": feed["publisher"],
                "source_id": feed["id"],
                "source_type": item_type,
                "content_category": _content_category(
                    item_type,
                    fields.get("categories", []),
                    fields.get("title", ""),
                    feed.get("coverage_domain", "market_general"),
                ),
                "content_category_basis": "configured_source_and_title_keywords",
                "url": article_url,
                "excerpt": excerpt,
                "excerpt_source": fields.get("excerpt_source") if excerpt else None,
                "is_full_article": False,
                "published_at": published_at,
                "published_at_raw": _bounded_text(fields.get("published"), 200),
                "publication_date_status": date_status,
                "collected_at": collected_at,
                "external_content_untrusted": True,
                "coverage_group_id": None,
            }
        )
    return articles


def _entry_fields(entry):
    result = {"categories": []}
    for child in list(entry):
        name = _local_name(child.tag)
        text_value = "".join(child.itertext()).strip()
        if name == "title":
            result["title"] = text_value
        elif name in {"description", "summary", "content", "encoded"} and text_value:
            if "excerpt" not in result or name in {"summary", "description"}:
                result["excerpt"] = text_value
                result["excerpt_source"] = name
        elif name in {"pubdate", "published", "updated", "date"} and text_value:
            result.setdefault("published", text_value)
        elif name in {"guid", "id"} and text_value:
            result["guid"] = text_value[:500]
        elif name == "link":
            href = child.attrib.get("href")
            if href and child.attrib.get("rel", "alternate") == "alternate":
                result.setdefault("url", href)
            elif text_value:
                result.setdefault("url", text_value)
        elif name == "category":
            term = child.attrib.get("term") or text_value
            if term:
                result["categories"].append(term[:100])
    return result


def _local_name(tag):
    return tag.rsplit("}", 1)[-1].casefold()


def _bounded_text(value, limit):
    if not isinstance(value, str):
        return None
    parser = _TextOnlyParser()
    try:
        parser.feed(value)
        parser.close()
    except Exception:
        return None
    cleaned = " ".join(parser.parts).strip()
    cleaned = re.sub(r"\s+", " ", cleaned)
    return cleaned[:limit] or None


class _TextOnlyParser(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.parts = []

    def handle_data(self, data):
        if len(self.parts) < 2_000:
            self.parts.append(data)


def _clean_article_url(value):
    if not isinstance(value, str) or len(value) > MAX_URL_CHARS:
        return None
    parsed = urlsplit(value.strip())
    if parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password:
        return None
    if not _public_host(parsed.hostname):
        return None
    return urlunsplit(("https", parsed.netloc, parsed.path[:1500], parsed.query[:500], ""))


def _canonical_url(value):
    parsed = urlsplit(value)
    query = urlencode(
        [
            (key, val)
            for key, val in parse_qsl(parsed.query, keep_blank_values=True)
            if not key.casefold().startswith("utm_") and key.casefold() not in TRACKING_QUERY_KEYS
        ]
    )
    return urlunsplit(
        (parsed.scheme.casefold(), parsed.netloc.casefold(), parsed.path.rstrip("/"), query, "")
    )


def _parse_date(value):
    if not isinstance(value, str) or not value.strip():
        return None, "unknown"
    raw = value.strip()
    try:
        parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        try:
            parsed = parsedate_to_datetime(raw)
        except (TypeError, ValueError, OverflowError):
            return None, "invalid"
    if parsed.tzinfo is None:
        return None, "invalid"
    return parsed.astimezone(timezone.utc).isoformat(), "available"


def _item_type(source_type, categories, title):
    labels = " ".join(categories).casefold()
    title_folded = title.casefold()
    if any(
        term in labels or term in title_folded
        for term in ("sponsored", "advertisement", "paid promotion")
    ):
        return "promotional_content"
    if "press release" in labels or "press release" in title_folded:
        return "press_release"
    return source_type


def _content_category(source_type, categories, title, coverage_domain):
    """Heuristic label for item type; it never verifies the item's claims."""
    labels = " ".join(categories).casefold()
    title = title.casefold()
    text = f"{labels} {title}"
    if source_type == "promotional_content" or any(
        term in text for term in ("sponsored", "advertisement", "paid promotion", "sweepstakes")
    ):
        return "promotional_content"
    if any(
        term in text
        for term in (
            "security incident",
            "exploit",
            "hack",
            "breach",
            "outage",
            "service disruption",
        )
    ):
        return "incident"
    if any(
        term in text
        for term in (
            "listing",
            "listed on",
            "now available for trading",
            "new trading pair",
            "new market",
        )
    ):
        return "listing_announcement"
    if coverage_domain == "platform":
        return "platform_update"
    if coverage_domain == "project":
        return "project_update"
    return "unclassified"


def _verified_aliases(identity):
    aliases = []
    symbol = identity.get("symbol")
    symbol_key = symbol.casefold().strip() if isinstance(symbol, str) else None
    for key in ("verified_names", "verified_aliases"):
        values = identity.get(key, [])
        if isinstance(values, list):
            aliases.extend(v.strip() for v in values if isinstance(v, str) and len(v.strip()) >= 3)
    for key in ("coingecko_name", "bitvavo_name"):
        value = identity.get(key)
        if isinstance(value, str) and len(value.strip()) >= 3:
            aliases.append(value.strip())
    return list(dict.fromkeys(alias for alias in aliases if alias.casefold().strip() != symbol_key))


def _matching_aliases(article, aliases):
    haystack = f"{article.get('title') or ''} {article.get('excerpt') or ''}".casefold()
    found = []
    for alias in aliases:
        normalized = alias.casefold().strip()
        if not normalized or len(normalized) < 3:
            continue
        if re.search(rf"(?<!\w){re.escape(normalized)}(?!\w)", haystack):
            found.append(alias)
    return found


def _coverage_groups(articles):
    groups = []
    for article in articles:
        words = _title_words(article.get("title") or "")
        matched = None
        for group in groups:
            overlap = len(words & group["_words"]) / max(1, len(words | group["_words"]))
            article_date = _datetime_value(article.get("published_at"))
            group_date = group.get("_published_at")
            within_time = (
                article_date is None
                or group_date is None
                or abs((article_date - group_date).total_seconds()) <= 72 * 60 * 60
            )
            if len(words) >= 4 and overlap >= 0.8 and within_time:
                matched = group
                break
        if matched is None:
            group_id = hashlib.sha256(" ".join(sorted(words)).encode()).hexdigest()[:20]
            matched = {
                "id": group_id,
                "article_refs": [],
                "publisher_count": 0,
                "repeated_coverage_is_not_independent_confirmation": False,
                "_publishers": set(),
                "_words": words,
                "_published_at": _datetime_value(article.get("published_at")),
                "_source_observations": 0,
            }
            groups.append(matched)
        matched["article_refs"].append(article["id"])
        matched["_publishers"].update(article.get("observed_publishers") or [article["publisher"]])
        matched["_source_observations"] += len(
            article.get("observed_in_sources") or [article["source_id"]]
        )
        article["coverage_group_id"] = matched["id"]
    output = []
    for group in groups:
        output.append(
            {
                "id": group["id"],
                "article_refs": group["article_refs"],
                "publisher_count": len(group["_publishers"]),
                "source_observations_count": group["_source_observations"],
                "repeated_coverage_is_not_independent_confirmation": (
                    len(group["article_refs"]) > 1 or group["_source_observations"] > 1
                ),
            }
        )
    return output


def _datetime_value(value):
    if not isinstance(value, str):
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo else None


def _search_window(evaluated_at, window_days):
    timestamp = _datetime_value(evaluated_at)
    if timestamp is None:
        timestamp = datetime.now(timezone.utc)
    return {
        "requested_days": window_days,
        "published_after": (timestamp - timedelta(days=window_days))
        .astimezone(timezone.utc)
        .isoformat(),
        "evaluated_at": timestamp.astimezone(timezone.utc).isoformat(),
    }


def _validate_window_days(window_days):
    if (
        isinstance(window_days, bool)
        or not isinstance(window_days, int)
        or not 1 <= window_days <= 365
    ):
        raise ValueError("window_days must be between 1 and 365")


def _publication_freshness(article, window):
    if article.get("publication_date_status") != "available":
        return "unknown_or_invalid_publication_date"
    published = _datetime_value(article.get("published_at"))
    cutoff = _datetime_value(window.get("published_after"))
    evaluated_at = _datetime_value(window.get("evaluated_at"))
    if published is None or cutoff is None or evaluated_at is None:
        return "unknown_or_invalid_publication_date"
    if published > evaluated_at + timedelta(hours=2):
        return "future_publication_date"
    if published < cutoff:
        return "older_than_requested_window"
    return "within_requested_window"


def _refresh_news_coverage(result, *, window_days):
    window = result.get("search_window") or _search_window(result.get("collected_at"), window_days)
    result["search_window"] = window
    articles = result.get("articles", [])
    for article in articles:
        article["freshness_status"] = _publication_freshness(article, window)
    known_dates = [article["published_at"] for article in articles if article.get("published_at")]
    applicable = [
        row for row in result.get("source_results", []) if row.get("status") != "not_applicable"
    ]
    failed = [row.get("source_id") for row in applicable if row.get("status") == "error"]
    not_executed = [
        row.get("source_id") for row in applicable if row.get("status") == "not_executed"
    ]
    successful = [row for row in applicable if row.get("status") == "success"]
    in_window = [
        article
        for article in articles
        if article.get("freshness_status") == "within_requested_window"
        and article.get("relevance_status") not in {"not_relevant", "awaiting_identity_match"}
    ]
    if failed or not_executed:
        coverage_status = "partial_configured_source_coverage"
    elif not applicable:
        coverage_status = "no_configured_source_covered_scope"
    else:
        coverage_status = "configured_sources_checked"
    result["coverage"] = {
        "status": coverage_status,
        "applicable_sources": len(applicable),
        "successful_sources": len(successful),
        "failed_sources": failed,
        "not_executed_sources": not_executed,
        "not_applicable_sources": [
            row.get("source_id")
            for row in result.get("source_results", [])
            if row.get("status") == "not_applicable"
        ],
        "articles_observed": len(articles),
        "articles_within_requested_window": len(in_window),
        "coverage_missing": failed + not_executed,
    }
    result["dates_obtained"] = {
        "oldest_publication_at": min(known_dates) if known_dates else None,
        "newest_publication_at": max(known_dates) if known_dates else None,
        "unknown_or_invalid_count": sum(
            article.get("freshness_status") == "unknown_or_invalid_publication_date"
            for article in articles
        ),
        "older_than_window_count": sum(
            article.get("freshness_status") == "older_than_requested_window" for article in articles
        ),
    }
    if result.get("search_scope") == "general_market_context":
        result["search_result_status"] = (
            "general_items_observed_within_window_relevance_not_established"
            if in_window
            else "no_feed_items_observed_within_requested_window"
        )
        result["relevance_status"] = "general_context_relevance_not_established"
    else:
        result["search_result_status"] = (
            "possible_asset_matches_observed_within_window"
            if in_window
            else "no_possible_asset_matches_observed_within_window"
        )
        result["relevance_status"] = "possible_identity_match_only"
    result["event_absence_established"] = False


def _title_words(value):
    return {
        word
        for word in re.findall(r"[a-z0-9]{3,}", value.casefold())
        if word not in {"the", "and", "for", "with", "from", "that", "this", "says", "after"}
    }
