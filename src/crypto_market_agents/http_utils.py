"""Small HTTP robustness helpers for read-only API clients."""

from __future__ import annotations

import os
import socket
import time
from collections.abc import Callable
from dataclasses import dataclass
import hashlib
import json
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request

from crypto_market_agents.logging_utils import get_logger
from crypto_market_agents.security import redact_text, redact_url


TRANSIENT_HTTP_STATUS_CODES = {429, 500, 502, 503, 504}
DEFAULT_CACHE_BACKEND = "memory"
DEFAULT_CACHE_DIR = ".cache/crypto-market-agents"
SUPPORTED_CACHE_BACKENDS = {"memory", "file"}


@dataclass(frozen=True, slots=True)
class HTTPClientSettings:
    """Retry and cache settings shared by read-only HTTP clients."""

    max_retries: int = 2
    backoff_seconds: float = 0.5
    cache_ttl_seconds: int = 60
    cache_enabled: bool = True
    cache_backend: str = DEFAULT_CACHE_BACKEND
    cache_dir: str = DEFAULT_CACHE_DIR

    def __post_init__(self) -> None:
        if self.max_retries < 0:
            raise ValueError("max_retries must be greater than or equal to 0.")
        if self.backoff_seconds < 0:
            raise ValueError("backoff_seconds must be greater than or equal to 0.")
        if self.cache_ttl_seconds < 0:
            raise ValueError("cache_ttl_seconds must be greater than or equal to 0.")
        cache_backend = self.cache_backend.strip().lower()
        if cache_backend not in SUPPORTED_CACHE_BACKENDS:
            allowed = ", ".join(sorted(SUPPORTED_CACHE_BACKENDS))
            raise ValueError(f"cache_backend must be one of: {allowed}.")
        if not str(self.cache_dir).strip():
            raise ValueError("cache_dir cannot be empty.")

        object.__setattr__(self, "cache_backend", cache_backend)
        object.__setattr__(self, "cache_dir", str(self.cache_dir).strip())

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
            cache_backend=_env_choice(
                "HTTP_CACHE_BACKEND",
                DEFAULT_CACHE_BACKEND,
                SUPPORTED_CACHE_BACKENDS,
            ),
            cache_dir=os.getenv("HTTP_CACHE_DIR", DEFAULT_CACHE_DIR),
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


class FileCacheBackend:
    """Simple JSON file cache backend for successful read-only GET responses."""

    def __init__(
        self,
        cache_dir: str | Path = DEFAULT_CACHE_DIR,
        *,
        time_provider: Callable[[], float] | None = None,
        logger_name: str = __name__,
    ) -> None:
        self.cache_dir = Path(cache_dir)
        self._time_provider = time_provider or time.time
        self._logger_name = logger_name
        self._available = True

    def get(self, key: str) -> HTTPResponse | None:
        """Return a cached file response when present, valid, and not expired."""

        if not self._ensure_cache_dir():
            return None

        path = self.cache_path(key)
        try:
            raw_payload = path.read_text(encoding="utf-8")
        except FileNotFoundError:
            return None
        except OSError as exc:
            self._warn(
                "Unable to read HTTP file cache entry %s: %s",
                path.name,
                redact_text(str(exc)),
            )
            return None

        try:
            payload = json.loads(raw_payload)
            created_at = _number(payload.get("created_at"))
            ttl_seconds = _number(payload.get("ttl_seconds"))
            status_code = payload.get("status_code")
            body = payload.get("payload")
            if not isinstance(status_code, int) or not isinstance(body, str):
                raise ValueError("cache payload has invalid fields")
        except (json.JSONDecodeError, TypeError, ValueError) as exc:
            self._warn("Ignoring corrupted HTTP file cache entry %s: %s", path.name, exc)
            _unlink_safely(path)
            return None

        if created_at + ttl_seconds <= self._time_provider():
            self._warn("Ignoring expired HTTP file cache entry %s", path.name)
            _unlink_safely(path)
            return None

        return HTTPResponse(status_code=status_code, body=body, from_cache=True)

    def set(self, key: str, response: HTTPResponse, ttl_seconds: int) -> None:
        """Store a successful response in a JSON file for a positive TTL."""

        if ttl_seconds <= 0 or response.status_code != 200:
            return
        if not self._ensure_cache_dir():
            return

        path = self.cache_path(key)
        payload = {
            "created_at": self._time_provider(),
            "ttl_seconds": ttl_seconds,
            "status_code": response.status_code,
            "payload": response.body,
        }
        try:
            path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
        except OSError as exc:
            self._warn(
                "Unable to write HTTP file cache entry %s: %s",
                path.name,
                redact_text(str(exc)),
            )

    def cache_path(self, key: str) -> Path:
        """Return the safe hashed file path for a cache key."""

        return self.cache_dir / f"{_cache_key_digest(key)}.json"

    def _ensure_cache_dir(self) -> bool:
        if not self._available:
            return False

        try:
            self.cache_dir.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            self._available = False
            self._warn(
                "HTTP file cache unavailable at %s: %s",
                redact_text(str(self.cache_dir)),
                redact_text(str(exc)),
            )
            return False

        return True

    def _warn(self, message: str, *args: Any) -> None:
        get_logger(self._logger_name).warning(message, *args)


CacheBackend = InMemoryTTLCache | FileCacheBackend


def send_request_with_retries(
    request: Request,
    *,
    opener: Callable[..., Any],
    timeout_seconds: int,
    response_status: Callable[[Any], int],
    read_text: Callable[[Any], str],
    settings: HTTPClientSettings | None = None,
    cache: CacheBackend | None = None,
    sleep: Callable[[float], None] = time.sleep,
    logger_name: str = __name__,
) -> HTTPResponse:
    """Send an HTTP request with limited retry, backoff, and optional GET cache."""

    selected_settings = settings or HTTPClientSettings.from_env()
    logger = get_logger(logger_name)
    cache_key = build_cache_key(request)
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


def build_cache_key(request: Request) -> str | None:
    """Return the cache key for a GET request, or None for non-cacheable methods."""

    if request.get_method().upper() != "GET":
        return None

    return request.full_url


def build_cache_backend(settings: HTTPClientSettings) -> CacheBackend | None:
    """Build the configured cache backend."""

    if not settings.cache_enabled or settings.cache_ttl_seconds <= 0:
        return None
    if settings.cache_backend == "file":
        return FileCacheBackend(settings.cache_dir)

    return InMemoryTTLCache()


def _is_cacheable(
    request: Request,
    settings: HTTPClientSettings,
    cache: CacheBackend | None,
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


def _env_choice(key: str, default: str, choices: set[str]) -> str:
    value = os.getenv(key)
    if value is None:
        return default

    normalized = value.strip().lower()
    if normalized in choices:
        return normalized

    allowed = ", ".join(sorted(choices))
    get_logger(__name__).warning(
        "Unknown %s value %s; using %s. Allowed values: %s.",
        key,
        redact_text(value),
        default,
        allowed,
    )
    return default


def _cache_key_digest(key: str) -> str:
    return hashlib.sha256(key.encode("utf-8")).hexdigest()


def _number(value: Any) -> float:
    if isinstance(value, bool) or not isinstance(value, int | float):
        raise ValueError("expected number")

    return float(value)


def _unlink_safely(path: Path) -> None:
    try:
        path.unlink()
    except FileNotFoundError:
        return
    except OSError:
        return
