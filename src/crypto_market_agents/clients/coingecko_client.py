"""Read-only CoinGecko API client."""

from __future__ import annotations

import json
import os
import socket
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode, urlsplit
from urllib.request import Request, urlopen

from crypto_market_agents.config import CoinGeckoConfig, HTTPConfig
from crypto_market_agents.http_utils import (
    HTTPClientSettings,
    InMemoryTTLCache,
    send_request_with_retries,
)
from crypto_market_agents.security import redact_text


class CoinGeckoError(RuntimeError):
    """Base error raised by the CoinGecko client."""


class CoinGeckoNetworkError(CoinGeckoError):
    """Raised when the client cannot reach CoinGecko."""


class CoinGeckoTimeoutError(CoinGeckoNetworkError):
    """Raised when a CoinGecko request times out."""


class CoinGeckoResponseError(CoinGeckoError):
    """Raised when CoinGecko returns malformed or unexpected data."""


class CoinGeckoAPIError(CoinGeckoError):
    """Raised when CoinGecko returns a non-success HTTP response."""

    def __init__(self, *, endpoint: str, status_code: int, body: str) -> None:
        self.endpoint = redact_text(endpoint)
        self.status_code = status_code
        self.body = redact_text(body)
        super().__init__(
            f"CoinGecko request to {self.endpoint} failed with HTTP "
            f"{status_code}: {_truncate(self.body)}"
        )


@dataclass(frozen=True, slots=True)
class CoinGeckoRequest:
    """Small trace object useful for tests and debugging."""

    endpoint: str
    params: Mapping[str, str]


class CoinGeckoClient:
    """Small read-only client for public CoinGecko market data.

    The client never performs trading, wallet, transfer, withdrawal, or exchange
    actions. It only sends GET requests to CoinGecko market-data endpoints.
    """

    def __init__(
        self,
        *,
        base_url: str = "https://api.coingecko.com/api/v3",
        api_key: str | None = None,
        timeout_seconds: int = 20,
        opener: Callable[..., Any] | None = None,
        http_settings: HTTPClientSettings | None = None,
        cache: InMemoryTTLCache | None = None,
        sleep: Callable[[float], None] | None = None,
    ) -> None:
        self.base_url = _clean_base_url(base_url)
        self.api_key = _clean_optional_text(api_key)
        self.timeout_seconds = _validate_timeout(timeout_seconds)
        self._opener = opener or urlopen
        self.http_settings = http_settings or HTTPClientSettings.from_env()
        self._cache = cache or InMemoryTTLCache()
        self._sleep = sleep or _sleep_noop_if_zero

    @classmethod
    def from_config(
        cls,
        config: CoinGeckoConfig,
        http_config: HTTPConfig | None = None,
    ) -> CoinGeckoClient:
        """Create a client from the validated application config."""

        return cls(
            base_url=config.base_url,
            api_key=config.api_key,
            timeout_seconds=config.timeout_seconds,
            http_settings=_settings_from_config(http_config),
        )

    @classmethod
    def from_env(cls) -> CoinGeckoClient:
        """Create a client directly from CoinGecko environment variables."""

        timeout = os.getenv("COINGECKO_TIMEOUT") or os.getenv("REQUEST_TIMEOUT_SECONDS")
        return cls(
            base_url=os.getenv(
                "COINGECKO_BASE_URL",
                "https://api.coingecko.com/api/v3",
            ),
            api_key=os.getenv("COINGECKO_API_KEY"),
            timeout_seconds=int(timeout or "20"),
        )

    def ping(self) -> dict[str, Any]:
        """Check CoinGecko API availability."""

        payload = self._request_json("ping")
        if not isinstance(payload, dict):
            raise CoinGeckoResponseError("CoinGecko ping response must be an object.")

        return payload

    def get_simple_prices(
        self,
        coin_ids: str | Sequence[str],
        *,
        vs_currencies: str | Sequence[str] = "usd",
        include_market_cap: bool = True,
        include_24hr_vol: bool = True,
        include_24hr_change: bool = True,
        include_last_updated_at: bool = True,
        precision: str | int | None = None,
    ) -> dict[str, Any]:
        """Fetch simple prices for one or more CoinGecko coin IDs."""

        params: dict[str, str] = {
            "ids": _csv(coin_ids, "coin_ids"),
            "vs_currencies": _csv(vs_currencies, "vs_currencies"),
            "include_market_cap": _bool_param(include_market_cap),
            "include_24hr_vol": _bool_param(include_24hr_vol),
            "include_24hr_change": _bool_param(include_24hr_change),
            "include_last_updated_at": _bool_param(include_last_updated_at),
        }
        if precision is not None:
            params["precision"] = str(precision).strip()

        payload = self._request_json("simple/price", params)
        if not isinstance(payload, dict):
            raise CoinGeckoResponseError("CoinGecko simple price response must be an object.")

        return payload

    def get_coin_markets(
        self,
        coin_ids: str | Sequence[str] | None = None,
        *,
        vs_currency: str = "usd",
        order: str = "market_cap_desc",
        per_page: int | None = None,
        page: int = 1,
        sparkline: bool = False,
        price_change_percentage: str | Sequence[str] | None = ("1h", "24h", "7d"),
    ) -> list[dict[str, Any]]:
        """Fetch market data such as price, volume, market cap, and changes."""

        clean_ids = _csv(coin_ids, "coin_ids") if coin_ids is not None else None
        selected_per_page = per_page
        if selected_per_page is None:
            selected_per_page = min(max(len(clean_ids.split(",")), 1), 250) if clean_ids else 100

        params: dict[str, str] = {
            "vs_currency": _required_text(vs_currency, "vs_currency").lower(),
            "order": _required_text(order, "order"),
            "per_page": str(_validate_page_size(selected_per_page)),
            "page": str(_validate_positive_int(page, "page")),
            "sparkline": _bool_param(sparkline),
        }
        if clean_ids:
            params["ids"] = clean_ids
        if price_change_percentage is not None:
            params["price_change_percentage"] = _csv(
                price_change_percentage,
                "price_change_percentage",
            )

        payload = self._request_json("coins/markets", params)
        if not isinstance(payload, list):
            raise CoinGeckoResponseError("CoinGecko markets response must be a list.")
        if not all(isinstance(item, dict) for item in payload):
            raise CoinGeckoResponseError("CoinGecko markets response must contain objects.")

        return payload

    def _request_json(
        self,
        endpoint: str,
        params: Mapping[str, str] | None = None,
    ) -> Any:
        request = self._build_request(endpoint, params or {})

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
            raise CoinGeckoAPIError(
                endpoint=redact_text(endpoint, secrets=(self.api_key,)),
                status_code=exc.code,
                body=redact_text(_read_text(exc), secrets=(self.api_key,)),
            ) from exc
        except (TimeoutError, socket.timeout) as exc:
            raise CoinGeckoTimeoutError(f"CoinGecko request to {endpoint} timed out.") from exc
        except URLError as exc:
            if _is_timeout_reason(exc.reason):
                raise CoinGeckoTimeoutError(f"CoinGecko request to {endpoint} timed out.") from exc
            reason = redact_text(str(exc.reason), secrets=(self.api_key,))
            raise CoinGeckoNetworkError(
                f"CoinGecko request to {redact_text(endpoint, secrets=(self.api_key,))} "
                f"failed: {reason}"
            ) from exc

        if status_code != 200:
            raise CoinGeckoAPIError(
                endpoint=redact_text(endpoint, secrets=(self.api_key,)),
                status_code=status_code,
                body=redact_text(body, secrets=(self.api_key,)),
            )

        try:
            return json.loads(body)
        except json.JSONDecodeError as exc:
            raise CoinGeckoResponseError(
                f"CoinGecko returned invalid JSON for {endpoint}."
            ) from exc

    def _build_request(self, endpoint: str, params: Mapping[str, str]) -> Request:
        clean_endpoint = _required_text(endpoint, "endpoint").lstrip("/")
        query = urlencode(dict(params))
        url = f"{self.base_url}/{clean_endpoint}"
        if query:
            url = f"{url}?{query}"

        return Request(url, headers=self._headers(), method="GET")

    def _headers(self) -> dict[str, str]:
        headers = {
            "Accept": "application/json",
            "User-Agent": "crypto-market-agents/0.1",
        }
        if self.api_key:
            headers[_api_key_header_name(self.base_url)] = self.api_key

        return headers


def _api_key_header_name(base_url: str) -> str:
    host = (urlsplit(base_url).hostname or "").lower()
    if host == "pro-api.coingecko.com":
        return "x-cg-pro-api-key"

    return "x-cg-demo-api-key"


def _settings_from_config(http_config: HTTPConfig | None) -> HTTPClientSettings | None:
    if http_config is None:
        return None

    return HTTPClientSettings(
        max_retries=http_config.max_retries,
        backoff_seconds=http_config.backoff_seconds,
        cache_ttl_seconds=http_config.cache_ttl_seconds,
        cache_enabled=http_config.cache_enabled,
    )


def _sleep_noop_if_zero(seconds: float) -> None:
    if seconds <= 0:
        return

    import time

    time.sleep(seconds)


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


def _csv(values: str | Sequence[str], field_name: str) -> str:
    if isinstance(values, str):
        raw_values = values.split(",")
    elif isinstance(values, Sequence):
        raw_values = list(values)
    else:
        raise ValueError(f"{field_name} must be a string or sequence of strings.")

    clean_values: list[str] = []
    seen: set[str] = set()
    for value in raw_values:
        cleaned = _required_text(value, field_name).lower()
        if cleaned not in seen:
            seen.add(cleaned)
            clean_values.append(cleaned)

    if not clean_values:
        raise ValueError(f"{field_name} must contain at least one value.")

    return ",".join(clean_values)


def _bool_param(value: bool) -> str:
    return "true" if bool(value) else "false"


def _validate_timeout(value: int) -> int:
    return _validate_positive_int(value, "timeout_seconds", maximum=120)


def _validate_page_size(value: int) -> int:
    return _validate_positive_int(value, "per_page", maximum=250)


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
        raise CoinGeckoResponseError("CoinGecko response has no HTTP status code.")

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
