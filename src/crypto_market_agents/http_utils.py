"""Small HTTP robustness helpers for read-only API clients."""

from __future__ import annotations

import os
import socket
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request

from crypto_market_agents.logging_utils import get_logger
from crypto_market_agents.security import redact_url


TRANSIENT_HTTP_STATUS_CODES = {429, 500, 502, 503, 504}


@dataclass(frozen=True, slots=True)
class HTTPClientSettings:
    """Retry and cache settings shared by read-only HTTP clients."""

    max_retries: int = 2
    backoff_seconds: float = 0.5
    cache_ttl_seconds: int = 60
    cache_enabled: bool = True

    def __post_init__(self) -> None:
        if self.max_retries < 0:
            raise ValueError("max_retries must be greater than or equal to 0.")
        if self.backoff_seconds < 0:
            raise ValueError("backoff_seconds must be greater than or equal to 0.")
        if self.cache_ttl_seconds < 0:
            raise ValueError("cache_ttl_seconds must be greater than or equal to 0.")

    @classmethod
    def from_env(cls) -> HTTPClientSettings:
        """Build settings from environment variables."""

        return cls(
            max_retries=_env_int("HTTP_MAX_RETRIES", 2, minimum=0, maximum=10),
            backoff_seconds=_env_float("HTTP_BACKOFF_SECONDS", 0.5, minimum=0.0),
            cache_ttl_seconds=_env_int(
                "HTTP_CACHE_TTL_SECONDS",
                _env_int("CACHE_TTL_SECONDS", 60, minimum=0, maximum=86400),
                minimum=0,
                maximum=86400,
            ),
            cache_enabled=_env_bool("HTTP_CACHE_ENABLED", True),
        )


@dataclass(frozen=True, slots=True)
class HTTPResponse:
    """Text HTTP response returned by the retry helper."""

    status_code: int
    body: str
    from_cache: bool = False


class InMemoryTTLCache:
    """Tiny in-memory TTL cache used by read-only GET clients."""

    def __init__(self, *, time_provider: Callable[[], float] | None = None) -> None:
        self._time_provider = time_provider or time.monotonic
        self._items: dict[str, tuple[float, HTTPResponse]] = {}

    def get(self, key: str) -> HTTPResponse | None:
        """Return a cached response when it exists and has not expired."""

        item = self._items.get(key)
        if item is None:
            return None

        expires_at, response = item
        if expires_at <= self._time_provider():
            self._items.pop(key, None)
            return None

        return HTTPResponse(
            status_code=response.status_code,
            body=response.body,
            from_cache=True,
        )

    def set(self, key: str, response: HTTPResponse, ttl_seconds: int) -> None:
        """Store a response for a positive TTL."""

        if ttl_seconds <= 0:
            return

        self._items[key] = (
            self._time_provider() + ttl_seconds,
            HTTPResponse(status_code=response.status_code, body=response.body),
        )


def send_request_with_retries(
    request: Request,
    *,
    opener: Callable[..., Any],
    timeout_seconds: int,
    response_status: Callable[[Any], int],
    read_text: Callable[[Any], str],
    settings: HTTPClientSettings | None = None,
    cache: InMemoryTTLCache | None = None,
    sleep: Callable[[float], None] = time.sleep,
    logger_name: str = __name__,
) -> HTTPResponse:
    """Send an HTTP request with limited retry, backoff, and optional GET cache."""

    selected_settings = settings or HTTPClientSettings.from_env()
    logger = get_logger(logger_name)
    cache_key = _cache_key(request)
    cacheable = _is_cacheable(request, selected_settings, cache)

    if cacheable and cache_key is not None:
        cached_response = cache.get(cache_key) if cache else None
        if cached_response:
            logger.debug("HTTP cache hit for %s", redact_url(request.full_url))
            return cached_response
        logger.debug("HTTP cache miss for %s", redact_url(request.full_url))

    last_response: HTTPResponse | None = None
    for attempt in range(selected_settings.max_retries + 1):
        try:
            with opener(request, timeout=timeout_seconds) as response:
                status_code = response_status(response)
                body = read_text(response)
        except HTTPError as exc:
            if _can_retry_http_status(exc.code, attempt, selected_settings.max_retries):
                _sleep_before_retry(
                    sleep=sleep,
                    settings=selected_settings,
                    attempt=attempt,
                    logger_name=logger_name,
                    reason=f"HTTP {exc.code}",
                    url=request.full_url,
                )
                continue
            raise
        except (TimeoutError, socket.timeout) as exc:
            if _can_retry_attempt(attempt, selected_settings.max_retries):
                _sleep_before_retry(
                    sleep=sleep,
                    settings=selected_settings,
                    attempt=attempt,
                    logger_name=logger_name,
                    reason=type(exc).__name__,
                    url=request.full_url,
                )
                continue
            raise
        except URLError:
            if _can_retry_attempt(attempt, selected_settings.max_retries):
                _sleep_before_retry(
                    sleep=sleep,
                    settings=selected_settings,
                    attempt=attempt,
                    logger_name=logger_name,
                    reason="network error",
                    url=request.full_url,
                )
                continue
            raise

        last_response = HTTPResponse(status_code=status_code, body=body)
        if _can_retry_http_status(status_code, attempt, selected_settings.max_retries):
            _sleep_before_retry(
                sleep=sleep,
                settings=selected_settings,
                attempt=attempt,
                logger_name=logger_name,
                reason=f"HTTP {status_code}",
                url=request.full_url,
            )
            continue
        break

    if last_response is None:
        raise RuntimeError("HTTP request did not produce a response.")

    if cacheable and cache_key is not None and last_response.status_code == 200 and cache:
        cache.set(cache_key, last_response, selected_settings.cache_ttl_seconds)

    return last_response


def should_retry_http_status(status_code: int) -> bool:
    """Return True when an HTTP status is considered temporary."""

    return status_code in TRANSIENT_HTTP_STATUS_CODES


def backoff_delay(base_seconds: float, attempt: int) -> float:
    """Return exponential backoff delay for a retry attempt."""

    return base_seconds * (2**attempt)


def _cache_key(request: Request) -> str | None:
    if request.get_method().upper() != "GET":
        return None

    return request.full_url


def _is_cacheable(
    request: Request,
    settings: HTTPClientSettings,
    cache: InMemoryTTLCache | None,
) -> bool:
    return (
        cache is not None
        and settings.cache_enabled
        and settings.cache_ttl_seconds > 0
        and request.get_method().upper() == "GET"
    )


def _can_retry_http_status(status_code: int, attempt: int, max_retries: int) -> bool:
    return should_retry_http_status(status_code) and _can_retry_attempt(attempt, max_retries)


def _can_retry_attempt(attempt: int, max_retries: int) -> bool:
    return attempt < max_retries


def _sleep_before_retry(
    *,
    sleep: Callable[[float], None],
    settings: HTTPClientSettings,
    attempt: int,
    logger_name: str,
    reason: str,
    url: str,
) -> None:
    delay = backoff_delay(settings.backoff_seconds, attempt)
    get_logger(logger_name).info(
        "Temporary HTTP failure (%s), retry %s/%s after %.2fs for %s",
        reason,
        attempt + 1,
        settings.max_retries,
        delay,
        redact_url(url),
    )
    if delay > 0:
        sleep(delay)


def _env_bool(key: str, default: bool) -> bool:
    value = os.getenv(key)
    if value is None:
        return default

    normalized = value.strip().lower()
    if normalized in {"1", "true", "yes", "y", "on"}:
        return True
    if normalized in {"0", "false", "no", "n", "off"}:
        return False

    return default


def _env_int(
    key: str,
    default: int,
    *,
    minimum: int,
    maximum: int,
) -> int:
    value = os.getenv(key)
    if value is None:
        return default

    try:
        parsed = int(value)
    except ValueError:
        return default

    if parsed < minimum or parsed > maximum:
        return default

    return parsed


def _env_float(key: str, default: float, *, minimum: float) -> float:
    value = os.getenv(key)
    if value is None:
        return default

    try:
        parsed = float(value)
    except ValueError:
        return default

    if parsed < minimum:
        return default

    return parsed
