"""Public market observations. No authenticated endpoint or real order execution."""

from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from http.client import HTTPException
import json
import re
from urllib.parse import urlencode
from urllib.request import Request

from crypto_market_agents.http_utils import _consume_http_request_budget, budgeted_urlopen
from crypto_market_agents.trading.models import Quote


class MarketDataError(ValueError):
    """A quote cannot safely be used for simulation."""


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def positive_decimal(value: object, name: str) -> Decimal:
    try:
        if isinstance(value, bool):
            raise ValueError
        result = Decimal(str(value))
        if not result.is_finite() or result <= 0:
            raise ValueError
        return result
    except (ValueError, InvalidOperation):
        raise MarketDataError(f"Invalid positive number: {name}.") from None


def validate_market(market: str) -> str:
    if not isinstance(market, str) or not re.fullmatch(r"[A-Z0-9]{2,15}-EUR", market):
        raise MarketDataError("Only a single EUR market such as BTC-EUR is supported.")
    return market


class BitvavoMarketData:
    """Observe public bid/ask and market rules; timestamps indicate local receipt."""

    def __init__(self, *, opener=None, now_provider=utc_now, timeout=10):
        self.opener = opener or budgeted_urlopen
        self.now_provider = now_provider
        self.timeout = timeout

    def _get(self, path: str, market: str):
        url = "https://api.bitvavo.com/v2/" + path + "?" + urlencode({"market": market})
        request = Request(url, headers={"Accept": "application/json"}, method="GET")
        try:
            _consume_http_request_budget()
            with self.opener(request, timeout=self.timeout) as response:
                raw = response.read(1_000_001)
                if len(raw) > 1_000_000:
                    raise MarketDataError("Market response too large.")
                return json.loads(raw)
        except (OSError, HTTPException, ValueError) as exc:
            # Do not include arbitrary upstream response bodies or credentials in errors.
            raise MarketDataError(
                f"Public Bitvavo {path} unavailable ({type(exc).__name__})."
            ) from None

    @staticmethod
    def _item(payload, market):
        items = payload if isinstance(payload, list) else [payload]
        matches = [x for x in items if isinstance(x, dict) and x.get("market") == market]
        if len(matches) != 1:
            raise MarketDataError("Missing or ambiguous market response.")
        return matches[0]

    def get_quote(self, market: str) -> Quote:
        validate_market(market)
        rules = self._item(self._get("markets", market), market)
        if rules.get("status") != "trading" or rules.get("quote") != "EUR":
            raise MarketDataError("Market unavailable for EUR trading.")
        order_types = rules.get("orderTypes")
        if not isinstance(order_types, list) or "market" not in order_types:
            raise MarketDataError("Market orders are not supported on this market.")
        decimals = rules.get("quantityDecimals")
        if isinstance(decimals, bool) or not re.fullmatch(r"\d{1,2}", str(decimals)):
            raise MarketDataError("Invalid quantity precision.")
        decimals = int(decimals)
        if decimals > 18:
            raise MarketDataError("Unsupported quantity precision.")
        book = self._item(self._get("ticker/book", market), market)
        bid = positive_decimal(book.get("bid"), "bid")
        ask = positive_decimal(book.get("ask"), "ask")
        if bid > ask:
            raise MarketDataError("Crossed bid/ask quote.")
        # The public ticker has no exchange timestamp. Never label receipt as exchange time.
        return Quote(
            market=market,
            bid=bid,
            ask=ask,
            timestamp=self.now_provider(),
            min_order_eur=positive_decimal(rules.get("minOrderInQuoteAsset"), "minimum EUR"),
            quantity_step=Decimal(1).scaleb(-decimals),
            min_quantity=positive_decimal(rules.get("minOrderInBaseAsset"), "minimum quantity"),
        )


class MockMarketData:
    """Repeatable observations demonstrating hold, entry and a losing exit."""

    def __init__(self, *, index=0, now_provider=utc_now):
        self.index = index
        self.now_provider = now_provider

    def get_quote(self, market: str) -> Quote:
        validate_market(market)
        bids = ("100", "102", "99", "99", "101", "98")
        bid = Decimal(bids[self.index % len(bids)])
        self.index += 1
        return Quote(
            market=market,
            bid=bid,
            ask=bid + Decimal("0.05"),
            timestamp=self.now_provider(),
        )
