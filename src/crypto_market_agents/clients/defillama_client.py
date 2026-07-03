"""Read-only DefiLlama Free API client."""

from __future__ import annotations

import json
import os
import socket
from collections.abc import Callable
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlsplit
from urllib.request import Request, urlopen

from crypto_market_agents.config import DefiLlamaConfig, HTTPConfig
from crypto_market_agents.http_utils import (
    CacheBackend,
    HTTPClientSettings,
    build_cache_backend,
    send_request_with_retries,
)
from crypto_market_agents.security import redact_text


class DefiLlamaError(RuntimeError):
    """Base error raised by the DefiLlama client."""


class DefiLlamaNetworkError(DefiLlamaError):
    """Raised when the client cannot reach DefiLlama."""


class DefiLlamaTimeoutError(DefiLlamaNetworkError):
    """Raised when a DefiLlama request times out."""


class DefiLlamaResponseError(DefiLlamaError):
    """Raised when DefiLlama returns malformed or unexpected data."""


class DefiLlamaAPIError(DefiLlamaError):
    """Raised when DefiLlama returns a non-success HTTP response."""

    def __init__(self, *, endpoint: str, status_code: int, body: str) -> None:
        self.endpoint = redact_text(endpoint)
        self.status_code = status_code
        self.body = redact_text(body)
        super().__init__(
            f"DefiLlama request to {self.endpoint} failed with HTTP "
            f"{status_code}: {_truncate(self.body)}"
        )


class DefiLlamaClient:
    """Small read-only client for DefiLlama Free API data."""

    def __init__(
        self,
        *,
        base_url: str = "https://api.llama.fi",
        timeout_seconds: int = 20,
        opener: Callable[..., Any] | None = None,
        http_settings: HTTPClientSettings | None = None,
        cache: CacheBackend | None = None,
        sleep: Callable[[float], None] | None = None,
    ) -> None:
        self.base_url = _clean_base_url(base_url)
        self.timeout_seconds = _validate_timeout(timeout_seconds)
        self._opener = opener or urlopen
        self.http_settings = http_settings or HTTPClientSettings.from_env()
        self._cache = cache if cache is not None else build_cache_backend(self.http_settings)
        self._sleep = sleep or _sleep_noop_if_zero

    @classmethod
    def from_config(
        cls,
        config: DefiLlamaConfig,
        http_config: HTTPConfig | None = None,
    ) -> DefiLlamaClient:
        """Create a client from validated application config."""

        return cls(
            base_url=config.base_url,
            timeout_seconds=config.timeout_seconds,
            http_settings=_settings_from_config(http_config),
        )

    @classmethod
    def from_env(cls) -> DefiLlamaClient:
        """Create a client directly from DefiLlama environment variables."""

        timeout = os.getenv("DEFILLAMA_TIMEOUT") or os.getenv("REQUEST_TIMEOUT_SECONDS")
        return cls(
            base_url=os.getenv("DEFILLAMA_BASE_URL", "https://api.llama.fi"),
            timeout_seconds=int(timeout or "20"),
        )

    def get_protocols(self) -> list[dict[str, Any]]:
        """Return all DefiLlama protocols."""

        payload = self._request_json("protocols")
        if not isinstance(payload, list):
            raise DefiLlamaResponseError("DefiLlama protocols response must be a list.")
        if not all(isinstance(item, dict) for item in payload):
            raise DefiLlamaResponseError("DefiLlama protocols items must be objects.")

        return payload

    def get_protocol(self, protocol_slug: str) -> dict[str, Any]:
        """Return details and historical TVL for one protocol slug."""

        slug = _required_text(protocol_slug, "protocol_slug")
        payload = self._request_json(f"protocol/{quote(slug, safe='')}")
        if not isinstance(payload, dict):
            raise DefiLlamaResponseError("DefiLlama protocol response must be an object.")
        if not payload:
            raise DefiLlamaResponseError("DefiLlama protocol response is empty.")

        return payload

    def get_chains(self) -> list[dict[str, Any]]:
        """Return current TVL by chain."""

        payload = self._request_json("v2/chains")
        if not isinstance(payload, list):
            raise DefiLlamaResponseError("DefiLlama chains response must be a list.")
        if not all(isinstance(item, dict) for item in payload):
            raise DefiLlamaResponseError("DefiLlama chains items must be objects.")

        return payload

    def get_current_tvl(self, protocol_slug: str) -> float | int | None:
        """Return simplified current TVL for one protocol slug."""

        slug = _required_text(protocol_slug, "protocol_slug")
        payload = self._request_json(f"tvl/{quote(slug, safe='')}")
        if payload is None:
            return None
        if isinstance(payload, bool) or not isinstance(payload, int | float):
            raise DefiLlamaResponseError("DefiLlama TVL response must be numeric.")

        return payload

    def get_stablecoins(self) -> dict[str, Any]:
        """Return global stablecoin data."""

        payload = self._request_json("stablecoins")
        if not isinstance(payload, dict):
            raise DefiLlamaResponseError("DefiLlama stablecoins response must be an object.")
        if not payload:
            raise DefiLlamaResponseError("DefiLlama stablecoins response is empty.")

        return payload

    def get_fees_overview(self) -> dict[str, Any]:
        """Return fees and revenue overview."""

        payload = self._request_json("overview/fees")
        if not isinstance(payload, dict):
            raise DefiLlamaResponseError("DefiLlama fees response must be an object.")
        if not payload:
            raise DefiLlamaResponseError("DefiLlama fees response is empty.")

        return payload

    def _request_json(self, endpoint: str) -> Any:
        request = self._build_request(endpoint)

        try:
            response = send_request_with_retries(
                request,
                opener=self._opener,
                timeout_seconds=self.timeout_seconds,
                response_status=_response_status,
                read_text=_read_text,
                settings=self.http_settings,
                cache=self._cache,
                sleep=self._sleep,
                logger_name=__name__,
            )
            status_code = response.status_code
            body = response.body
        except HTTPError as exc:
            raise DefiLlamaAPIError(
                endpoint=redact_text(endpoint),
                status_code=exc.code,
                body=redact_text(_read_text(exc)),
            ) from exc
        except (TimeoutError, socket.timeout) as exc:
            raise DefiLlamaTimeoutError(f"DefiLlama request to {endpoint} timed out.") from exc
        except URLError as exc:
            if _is_timeout_reason(exc.reason):
                raise DefiLlamaTimeoutError(f"DefiLlama request to {endpoint} timed out.") from exc
            reason = redact_text(str(exc.reason))
            raise DefiLlamaNetworkError(
                f"DefiLlama request to {redact_text(endpoint)} failed: {reason}"
            ) from exc

        if status_code != 200:
            raise DefiLlamaAPIError(
                endpoint=redact_text(endpoint),
                status_code=status_code,
                body=redact_text(body),
            )
        if not body.strip():
            raise DefiLlamaResponseError(f"DefiLlama returned an empty response for {endpoint}.")

        try:
            return json.loads(body)
        except json.JSONDecodeError as exc:
            raise DefiLlamaResponseError(
                f"DefiLlama returned invalid JSON for {endpoint}."
            ) from exc

    def _build_request(self, endpoint: str) -> Request:
        clean_endpoint = _required_text(endpoint, "endpoint").lstrip("/")
        url = f"{self.base_url}/{clean_endpoint}"
        return Request(url, headers=self._headers(), method="GET")

    def _headers(self) -> dict[str, str]:
        return {
            "Accept": "application/json",
            "User-Agent": "crypto-market-agents/0.1",
        }


def _clean_base_url(base_url: str) -> str:
    cleaned = _required_text(base_url, "base_url").rstrip("/")
    parsed = urlsplit(cleaned)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise ValueError("base_url must be a valid http(s) URL.")

    return cleaned


def _settings_from_config(http_config: HTTPConfig | None) -> HTTPClientSettings | None:
    if http_config is None:
        return None

    return HTTPClientSettings(
        max_retries=http_config.max_retries,
        backoff_seconds=http_config.backoff_seconds,
        cache_ttl_seconds=http_config.cache_ttl_seconds,
        cache_enabled=http_config.cache_enabled,
        cache_backend=http_config.cache_backend,
        cache_dir=str(http_config.cache_dir),
    )


def _sleep_noop_if_zero(seconds: float) -> None:
    if seconds <= 0:
        return

    import time

    time.sleep(seconds)


def _required_text(value: str, field_name: str) -> str:
    if not isinstance(value, str):
        raise ValueError(f"{field_name} must be a string.")

    cleaned = value.strip()
    if not cleaned:
        raise ValueError(f"{field_name} cannot be empty.")

    return cleaned


def _validate_timeout(value: int) -> int:
    return _validate_positive_int(value, "timeout_seconds", maximum=120)


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
        raise DefiLlamaResponseError("DefiLlama response has no HTTP status code.")

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
