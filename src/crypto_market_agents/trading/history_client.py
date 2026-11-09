"""Small public Bitvavo historical-candles client."""

from __future__ import annotations

from datetime import datetime, timezone
import json
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen


class CandleClientError(RuntimeError):
    """A bounded public candles request failed."""


class BitvavoCandleClient:
    def __init__(self, *, opener=None, now_provider=None, timeout=10):
        self.opener = opener or urlopen
        self.now_provider = now_provider or (lambda: datetime.now(timezone.utc))
        self.timeout = timeout

    def get_candles(self, market, *, interval="1h", limit=1440):
        if interval not in {"1h", "4h", "1d"}:
            raise ValueError("Intervalle pris en charge: 1h, 4h, 1d.")
        if isinstance(limit, bool) or not 1 <= limit <= 1440:
            raise ValueError("La limite doit être comprise entre 1 et 1440.")
        query = urlencode({"interval": interval, "limit": limit})
        request = Request(
            f"https://api.bitvavo.com/v2/{market}/candles?{query}",
            headers={"Accept": "application/json"},
        )
        try:
            with self.opener(request, timeout=self.timeout) as response:
                raw = response.read(2_000_001)
                if len(raw) > 2_000_000:
                    raise CandleClientError("Réponse candles trop volumineuse.")
                payload = json.loads(raw)
                if not isinstance(payload, list):
                    raise CandleClientError("Réponse candles invalide.")
                return payload
        except HTTPError as exc:
            status_code = exc.code
            exc.close()
            if status_code == 429:
                raise CandleClientError("Limite API Bitvavo (HTTP 429).") from None
            raise CandleClientError(f"Candles indisponibles (HTTP {status_code}).") from None
        except (OSError, URLError, TimeoutError, json.JSONDecodeError) as exc:
            raise CandleClientError(f"Candles indisponibles ({type(exc).__name__}).") from None
