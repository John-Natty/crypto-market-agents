"""Read-only NewsAPI client."""

from __future__ import annotations

import json
import os
import socket
from collections.abc import Callable, Mapping
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode, urlsplit
from urllib.request import Request, urlopen

from crypto_market_agents.config import NewsConfig
from crypto_market_agents.security import redact_text


class NewsError(RuntimeError):
    """Base error raised by the news client."""


class NewsAPIKeyMissingError(NewsError):
    """Raised when NewsAPI is used without an API key."""


class NewsNetworkError(NewsError):
    """Raised when the client cannot reach the news API."""


class NewsTimeoutError(NewsNetworkError):
    """Raised when a news request times out."""


class NewsResponseError(NewsError):
    """Raised when the news API returns malformed data."""


class NewsAPIHTTPError(NewsError):
    """Raised when the news API returns a non-success HTTP response."""

    def __init__(self, *, endpoint: str, status_code: int, body: str) -> None:
        self.endpoint = redact_text(endpoint)
        self.status_code = status_code
        self.body = redact_text(body)
        super().__init__(
            f"News API request to {self.endpoint} failed with HTTP "
            f"{status_code}: {_truncate(self.body)}"
        )


class NewsAPIStatusError(NewsError):
    """Raised when NewsAPI returns a JSON status of error."""


class NewsClient:
    """Small read-only client for article discovery via NewsAPI."""

    def __init__(
        self,
        *,
        base_url: str = "https://newsapi.org/v2",
        api_key: str | None = None,
        timeout_seconds: int = 20,
        default_query: str = "crypto OR bitcoin OR ethereum OR blockchain",
        max_articles: int = 10,
        opener: Callable[..., Any] | None = None,
    ) -> None:
        self.base_url = _clean_base_url(base_url)
        self.api_key = _clean_optional_text(api_key)
        self.timeout_seconds = _validate_timeout(timeout_seconds)
        self.default_query = _required_text(default_query, "default_query")
        self.max_articles = _validate_page_size(max_articles)
        self._opener = opener or urlopen

    @classmethod
    def from_config(cls, config: NewsConfig) -> NewsClient:
        """Create a client from the validated application config."""

        return cls(
            base_url=config.base_url,
            api_key=config.api_key,
            timeout_seconds=config.timeout_seconds,
            default_query=config.default_query,
            max_articles=config.max_articles,
        )

    @classmethod
    def from_env(cls) -> NewsClient:
        """Create a client directly from NewsAPI environment variables."""

        timeout = os.getenv("NEWS_TIMEOUT") or os.getenv("REQUEST_TIMEOUT_SECONDS")
        return cls(
            base_url=os.getenv("NEWS_BASE_URL", "https://newsapi.org/v2"),
            api_key=os.getenv("NEWS_API_KEY"),
            timeout_seconds=int(timeout or "20"),
            default_query=os.getenv(
                "NEWS_DEFAULT_QUERY",
                "crypto OR bitcoin OR ethereum OR blockchain",
            ),
            max_articles=int(os.getenv("NEWS_MAX_ARTICLES", "10")),
        )

    def search_articles(
        self,
        query: str,
        language: str = "en",
        page_size: int = 10,
    ) -> list[dict[str, Any]]:
        """Search recent articles with NewsAPI's read-only Everything endpoint."""

        if not self.api_key:
            raise NewsAPIKeyMissingError(
                "NEWS_API_KEY is missing. Define it in .env to use NewsAPI."
            )

        clean_query = _required_text(query, "query")
        if len(clean_query) > 500:
            raise ValueError("query must be 500 characters or fewer for NewsAPI.")

        selected_page_size = min(_validate_page_size(page_size), self.max_articles)
        payload = self._request_json(
            "everything",
            {
                "q": clean_query,
                "language": _required_text(language, "language").lower(),
                "pageSize": str(selected_page_size),
                "sortBy": "publishedAt",
            },
        )

        if not isinstance(payload, dict):
            raise NewsResponseError("NewsAPI response must be an object.")

        if payload.get("status") == "error":
            code = payload.get("code", "unknown")
            message = redact_text(
                payload.get("message", "Unknown NewsAPI error."),
                secrets=(self.api_key,),
            )
            raise NewsAPIStatusError(f"NewsAPI returned {code}: {message}")

        articles = payload.get("articles", [])
        if not isinstance(articles, list):
            raise NewsResponseError("NewsAPI articles response must be a list.")
        if not all(isinstance(article, dict) for article in articles):
            raise NewsResponseError("NewsAPI articles must be objects.")

        return [_clean_article(article) for article in articles]

    def _request_json(
        self,
        endpoint: str,
        params: Mapping[str, str],
    ) -> Any:
        request = self._build_request(endpoint, params)

        try:
            with self._opener(request, timeout=self.timeout_seconds) as response:
                status_code = _response_status(response)
                body = _read_text(response)
        except HTTPError as exc:
            raise NewsAPIHTTPError(
                endpoint=redact_text(endpoint, secrets=(self.api_key,)),
                status_code=exc.code,
                body=redact_text(_read_text(exc), secrets=(self.api_key,)),
            ) from exc
        except (TimeoutError, socket.timeout) as exc:
            raise NewsTimeoutError(f"News API request to {endpoint} timed out.") from exc
        except URLError as exc:
            if _is_timeout_reason(exc.reason):
                raise NewsTimeoutError(f"News API request to {endpoint} timed out.") from exc
            reason = redact_text(str(exc.reason), secrets=(self.api_key,))
            safe_endpoint = redact_text(endpoint, secrets=(self.api_key,))
            raise NewsNetworkError(f"News API request to {safe_endpoint} failed: {reason}") from exc

        if status_code != 200:
            raise NewsAPIHTTPError(
                endpoint=redact_text(endpoint, secrets=(self.api_key,)),
                status_code=status_code,
                body=redact_text(body, secrets=(self.api_key,)),
            )

        try:
            return json.loads(body)
        except json.JSONDecodeError as exc:
            raise NewsResponseError(f"News API returned invalid JSON for {endpoint}.") from exc

    def _build_request(self, endpoint: str, params: Mapping[str, str]) -> Request:
        clean_endpoint = _required_text(endpoint, "endpoint").lstrip("/")
        query = urlencode(dict(params))
        url = f"{self.base_url}/{clean_endpoint}?{query}"
        return Request(url, headers=self._headers(), method="GET")

    def _headers(self) -> dict[str, str]:
        headers = {
            "Accept": "application/json",
            "User-Agent": "crypto-market-agents/0.1",
        }
        if self.api_key:
            headers["X-Api-Key"] = self.api_key

        return headers


def _clean_article(article: dict[str, Any]) -> dict[str, Any]:
    source = article.get("source") if isinstance(article.get("source"), dict) else {}
    return {
        "title": article.get("title"),
        "description": article.get("description"),
        "content": article.get("content"),
        "source": {
            "id": source.get("id"),
            "name": source.get("name"),
        },
        "url": article.get("url"),
        "publishedAt": article.get("publishedAt"),
    }


def _clean_base_url(base_url: str) -> str:
    cleaned = _required_text(base_url, "base_url").rstrip("/")
    parsed = urlsplit(cleaned)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise ValueError("base_url must be a valid http(s) URL.")

    return cleaned


def _clean_optional_text(value: str | None) -> str | None:
    if value is None:
        return None

    cleaned = value.strip()
    return cleaned or None


def _required_text(value: str, field_name: str) -> str:
    if not isinstance(value, str):
        raise ValueError(f"{field_name} must be a string.")

    cleaned = value.strip()
    if not cleaned:
        raise ValueError(f"{field_name} cannot be empty.")

    return cleaned


def _validate_timeout(value: int) -> int:
    return _validate_positive_int(value, "timeout_seconds", maximum=120)


def _validate_page_size(value: int) -> int:
    return _validate_positive_int(value, "page_size", maximum=100)


def _validate_positive_int(
    value: int,
    field_name: str,
    *,
    maximum: int | None = None,
) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError(f"{field_name} must be an integer.")
    if value < 1:
        raise ValueError(f"{field_name} must be greater than 0.")
    if maximum is not None and value > maximum:
        raise ValueError(f"{field_name} must be lower than or equal to {maximum}.")

    return value


def _response_status(response: Any) -> int:
    status = getattr(response, "status", None)
    if status is None and hasattr(response, "getcode"):
        status = response.getcode()

    if not isinstance(status, int):
        raise NewsResponseError("News API response has no HTTP status code.")

    return status


def _read_text(response: Any) -> str:
    raw_body = response.read()
    if isinstance(raw_body, str):
        return raw_body

    return bytes(raw_body).decode("utf-8", errors="replace")


def _is_timeout_reason(reason: Any) -> bool:
    return isinstance(reason, TimeoutError | socket.timeout) or "timed out" in str(reason).lower()


def _truncate(value: str, limit: int = 500) -> str:
    if len(value) <= limit:
        return value

    return f"{value[:limit]}..."
