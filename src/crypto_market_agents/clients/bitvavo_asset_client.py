"""Read-only public Bitvavo asset metadata client."""

from __future__ import annotations

import json
import os
from collections.abc import Callable
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request
from urllib.parse import urlsplit

from crypto_market_agents.http_utils import (
    HTTPClientSettings,
    budgeted_urlopen,
    build_cache_backend,
    send_request_with_retries,
)


class BitvavoAssetError(RuntimeError):
    """Raised for Bitvavo asset metadata failures."""


class BitvavoAssetClient:
    """Fetch public GET /assets metadata; it exposes no trading operations."""

    def __init__(
        self,
        *,
        base_url="https://api.bitvavo.com/v2",
        timeout_seconds=15,
        opener: Callable[..., Any] | None = None,
        http_settings=None,
        cache=None,
    ):
        self.base_url = base_url.rstrip("/")
        parsed = urlsplit(self.base_url)
        if parsed.scheme not in {"https", "http"} or not parsed.netloc:
            raise ValueError("base_url must be an HTTP URL")
        if (
            isinstance(timeout_seconds, bool)
            or not isinstance(timeout_seconds, int)
            or timeout_seconds < 1
        ):
            raise ValueError("timeout_seconds must be a positive integer")
        self.timeout_seconds = timeout_seconds
        self._opener = opener or budgeted_urlopen
        self.http_settings = http_settings or HTTPClientSettings.from_env()
        self._cache = cache if cache is not None else build_cache_backend(self.http_settings)

    @classmethod
    def from_env(cls):
        return cls(
            base_url=os.getenv("BITVAVO_BASE_URL", "https://api.bitvavo.com/v2"),
            timeout_seconds=int(os.getenv("BITVAVO_TIMEOUT", "15")),
        )

    def get_assets(self) -> list[dict[str, Any]]:
        request = Request(
            f"{self.base_url}/assets", headers={"Accept": "application/json"}, method="GET"
        )
        try:
            response = send_request_with_retries(
                request,
                opener=self._opener,
                timeout_seconds=self.timeout_seconds,
                response_status=lambda resp: getattr(resp, "status", 200),
                read_text=lambda resp: resp.read().decode("utf-8"),
                settings=self.http_settings,
                cache=self._cache,
                logger_name=__name__,
            )
        except HTTPError as exc:
            status = getattr(exc, "code", None)
            exc.close()
            raise BitvavoAssetError(
                f"Bitvavo GET /assets unavailable ({status or type(exc).__name__})."
            ) from None
        except (URLError, TimeoutError, OSError) as exc:
            raise BitvavoAssetError(
                f"Bitvavo GET /assets unavailable ({type(exc).__name__})."
            ) from None
        if response.status_code != 200:
            raise BitvavoAssetError(f"Bitvavo GET /assets failed (HTTP {response.status_code}).")
        try:
            payload = json.loads(response.body)
        except json.JSONDecodeError:
            raise BitvavoAssetError("Bitvavo GET /assets returned invalid JSON.") from None
        if not isinstance(payload, list) or not all(isinstance(row, dict) for row in payload):
            raise BitvavoAssetError("Bitvavo GET /assets response must be a list of objects.")
        return payload
