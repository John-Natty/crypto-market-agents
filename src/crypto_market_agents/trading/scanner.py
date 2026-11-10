"""Neutral Bitvavo market discovery and the optional deterministic scanner."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
import json
from urllib.error import HTTPError, URLError
from urllib.request import Request

from crypto_market_agents.http_utils import _consume_http_request_budget, budgeted_urlopen


def _decimal(value, field):
    try:
        if isinstance(value, bool):
            raise ValueError
        result = Decimal(str(value))
        if not result.is_finite() or result <= 0:
            raise ValueError
        return result
    except (ValueError, InvalidOperation, TypeError):
        raise ValueError(f"{field} absent ou invalide") from None


def _utc_ms(value):
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise ValueError("timestamp source absent ou invalide")
    return datetime.fromtimestamp(value / 1000, timezone.utc)


@dataclass(frozen=True)
class ScanConfig:
    min_volume_eur: Decimal = Decimal("100000")
    max_spread_pct: Decimal = Decimal("1")
    require_trading: bool = True


@dataclass(frozen=True)
class Candidate:
    market: str
    status: str
    base_asset: str
    quote_asset: str
    volume_base_24h: str | None
    volume_quote_24h_eur: str | None
    price_change_24h_pct: str | None
    bid_eur: str | None
    ask_eur: str | None
    spread_pct: str | None
    min_order_base: str | None
    min_order_quote_eur: str | None
    collected_at: str
    source_at: str | None
    score: str | None
    reasons: tuple[str, ...]


class BitvavoScanner:
    """Three grouped, unauthenticated GETs; no trade or LLM endpoint is called."""

    def __init__(self, *, opener=None, now_provider=None, timeout=10):
        self.opener = opener or budgeted_urlopen
        self.now_provider = now_provider or (lambda: datetime.now(timezone.utc))
        self.timeout = timeout

    def _get(self, endpoint):
        req = Request(
            "https://api.bitvavo.com/v2/" + endpoint, headers={"Accept": "application/json"}
        )
        try:
            _consume_http_request_budget()
            with self.opener(req, timeout=self.timeout) as response:
                raw = response.read(5_000_001)
                if len(raw) > 5_000_000:
                    raise ValueError("réponse Bitvavo trop volumineuse")
                return json.loads(raw)
        except HTTPError as exc:
            status_code = exc.code
            exc.close()
            if status_code == 429:
                raise RuntimeError("Limite API Bitvavo (HTTP 429); réessayer plus tard.") from None
            raise RuntimeError(f"API Bitvavo indisponible (HTTP {status_code}).") from None
        except (OSError, URLError, TimeoutError, json.JSONDecodeError) as exc:
            raise RuntimeError(f"API Bitvavo indisponible ({type(exc).__name__}).") from None

    @staticmethod
    def _indexed(payload):
        if not isinstance(payload, list):
            return {}
        return {
            x["market"]: x
            for x in payload
            if isinstance(x, dict) and isinstance(x.get("market"), str)
        }

    def scan(self, config=ScanConfig()):
        markets = self._get("markets")
        tickers = self._get("ticker/24h")
        books = self._get("ticker/book")
        if not isinstance(markets, list):
            raise RuntimeError("Réponse markets invalide.")
        tickers, books = self._indexed(tickers), self._indexed(books)
        collected = self.now_provider().astimezone(timezone.utc).isoformat()
        accepted, rejected = [], []
        for market in sorted(
            (m for m in markets if isinstance(m, dict)), key=lambda x: str(x.get("market", ""))
        ):
            name = market.get("market", "")
            if market.get("quote") != "EUR" or not isinstance(market.get("base"), str):
                continue
            status = str(market.get("status", "unknown"))
            reasons = []
            market_eligible = status == "trading"
            if config.require_trading and status != "trading":
                reasons.append(f"statut {status}, marché non négociable")
            ticker, book = tickers.get(name, {}), books.get(name, {})
            volbase = volquote = change = bid = ask = spread = minbase = minquote = source_at = None
            try:
                open_price = _decimal(ticker.get("open"), "open")
                last = _decimal(ticker.get("last"), "last")
                volbase = str(_decimal(ticker.get("volume"), "volume base"))
                volquote = str(_decimal(ticker.get("volumeQuote"), "volume quote EUR"))
                change = str(((last - open_price) / open_price * 100).quantize(Decimal("0.0001")))
                source_at = _utc_ms(ticker.get("timestamp")).isoformat()
                bid, ask = _decimal(book.get("bid"), "bid"), _decimal(book.get("ask"), "ask")
                if bid > ask:
                    raise ValueError("bid supérieur à ask")
                spread = str(((ask - bid) / ((ask + bid) / 2) * 100).quantize(Decimal("0.0001")))
                minbase = str(_decimal(market.get("minOrderInBaseAsset"), "minimum base"))
                minquote = str(_decimal(market.get("minOrderInQuoteAsset"), "minimum quote EUR"))
            except (ValueError, TypeError, OverflowError):
                reasons.append("données manquantes, non finies ou incohérentes")
            if volquote is not None and Decimal(volquote) < config.min_volume_eur:
                reasons.append(
                    f"volume quote {volquote} EUR inférieur au minimum {config.min_volume_eur} EUR"
                )
            if spread is not None and Decimal(spread) > config.max_spread_pct:
                reasons.append(f"spread {spread}% supérieur au maximum {config.max_spread_pct}%")
            # Rule score: volume threshold passed (40), spread <= half cap (30), positive change (up to 30).
            invalid_data = any("données" in r for r in reasons)
            filter_failed = any("inférieur" in r or "supérieur" in r for r in reasons)
            score = None
            if not invalid_data and not filter_failed and market_eligible:
                score = Decimal(40)
                if Decimal(spread) <= config.max_spread_pct / 2:
                    score += 30
                score += min(Decimal(30), max(Decimal(0), Decimal(change)))
                reasons.append(
                    "score: volume admissible (40), spread (30 si <= moitié du seuil), variation passée plafonnée à 30"
                )
            obj = Candidate(
                name,
                status,
                market["base"],
                "EUR",
                volbase,
                volquote,
                change,
                str(bid) if bid is not None else None,
                str(ask) if ask is not None else None,
                spread,
                minbase,
                minquote,
                collected,
                source_at,
                str(score) if score is not None else None,
                tuple(reasons),
            )
            (accepted if score is not None else rejected).append(obj)
        accepted.sort(
            key=lambda x: (-Decimal(x.score), -Decimal(x.volume_quote_24h_eur or "0"), x.market)
        )
        return {
            "collected_at": collected,
            "quote_currency": "EUR",
            "ranking_note": "Présélection par règles, pas une prédiction de rendement.",
            "candidates": [asdict(x) for x in accepted],
            "rejected": [asdict(x) for x in rejected],
            "selection_order": "score décroissant, volume quote 24h EUR décroissant, marché alphabétique",
        }


class BitvavoMarketDiscovery:
    """Unranked, paginated observations for all Bitvavo EUR-quoted markets."""

    def __init__(self, *, opener=None, now_provider=None, timeout=10, page_size=10):
        if (
            isinstance(page_size, bool)
            or not isinstance(page_size, int)
            or not 1 <= page_size <= 25
        ):
            raise ValueError("page_size must be between 1 and 25")
        self.scanner = BitvavoScanner(opener=opener, now_provider=now_provider, timeout=timeout)
        self.page_size = page_size

    def snapshot(self):
        """Fetch the public EUR market universe without scoring or eligibility thresholds."""
        markets = self.scanner._get("markets")
        tickers = self.scanner._get("ticker/24h")
        books = self.scanner._get("ticker/book")
        if not isinstance(markets, list):
            raise RuntimeError("Réponse markets Bitvavo invalide.")
        tickers = self.scanner._indexed(tickers)
        books = self.scanner._indexed(books)
        collected_at = self.scanner.now_provider().astimezone(timezone.utc).isoformat()
        rows = []
        for item in markets:
            if (
                not isinstance(item, dict)
                or item.get("quote") != "EUR"
                or not isinstance(item.get("market"), str)
                or not isinstance(item.get("base"), str)
            ):
                continue
            market = item["market"]
            ticker = tickers.get(market, {})
            book = books.get(market, {})
            ticker_time = _catalog_source_time(ticker.get("timestamp"))
            observations = {
                "base_asset": _catalog_observation(
                    item.get("base"),
                    "asset identifier",
                    collected_at,
                    None,
                    "market_base_asset_absent",
                ),
                "quote_asset": _catalog_observation(
                    item.get("quote"),
                    "asset identifier",
                    collected_at,
                    None,
                    "market_quote_asset_absent",
                ),
                "market_status": _catalog_observation(
                    item.get("status"),
                    "status",
                    collected_at,
                    None,
                    "market_status_absent",
                ),
                "last": _catalog_observation(
                    _catalog_positive(ticker.get("last")),
                    "EUR per base asset",
                    collected_at,
                    ticker_time,
                    "ticker_last_absent_or_invalid",
                ),
                "open_24h": _catalog_observation(
                    _catalog_positive(ticker.get("open")),
                    "EUR per base asset",
                    collected_at,
                    ticker_time,
                    "ticker_open_absent_or_invalid",
                ),
                "price_change_24h_pct": _catalog_observation(
                    _catalog_change_pct(ticker.get("open"), ticker.get("last")),
                    "% over previous 24h",
                    collected_at,
                    ticker_time,
                    "ticker_open_or_last_absent_or_invalid",
                ),
                "volume_base_24h": _catalog_observation(
                    _catalog_nonnegative(ticker.get("volume")),
                    item["base"],
                    collected_at,
                    ticker_time,
                    "ticker_base_volume_absent_or_invalid",
                ),
                "volume_quote_24h": _catalog_observation(
                    _catalog_nonnegative(ticker.get("volumeQuote")),
                    "EUR",
                    collected_at,
                    ticker_time,
                    "ticker_quote_volume_absent_or_invalid",
                ),
                "bid": _catalog_observation(
                    _catalog_positive(book.get("bid")),
                    "EUR per base asset",
                    collected_at,
                    None,
                    "public_orderbook_bid_absent_or_invalid",
                ),
                "ask": _catalog_observation(
                    _catalog_positive(book.get("ask")),
                    "EUR per base asset",
                    collected_at,
                    None,
                    "public_orderbook_ask_absent_or_invalid",
                ),
                "min_order_base": _catalog_observation(
                    _catalog_positive(item.get("minOrderInBaseAsset")),
                    item["base"],
                    collected_at,
                    None,
                    "minimum_base_order_absent_or_invalid",
                ),
                "min_order_quote": _catalog_observation(
                    _catalog_positive(item.get("minOrderInQuoteAsset")),
                    "EUR",
                    collected_at,
                    None,
                    "minimum_quote_order_absent_or_invalid",
                ),
            }
            bid = observations["bid"]["value"]
            ask = observations["ask"]["value"]
            spread = None
            spread_reason = "public_orderbook_bid_or_ask_unavailable"
            if bid is not None and ask is not None:
                bid_value, ask_value = Decimal(bid), Decimal(ask)
                if bid_value <= ask_value:
                    spread = str(
                        ((ask_value - bid_value) / ((ask_value + bid_value) / 2) * 100).quantize(
                            Decimal("0.0001")
                        )
                    )
                    spread_reason = "public_orderbook_crossed"
            observations["spread_pct"] = _catalog_observation(
                spread,
                "%",
                collected_at,
                None,
                spread_reason,
            )
            rows.append(
                {
                    "market": market,
                    "base_asset": item["base"],
                    "quote_asset": "EUR",
                    "status": item.get("status"),
                    "collected_at": collected_at,
                    "source_at": ticker_time,
                    "observations": observations,
                }
            )

        # Lexical market ID is a stable pagination key, not an investment ranking.
        rows.sort(key=lambda row: row["market"])
        pages = [
            {
                "page": page,
                "items": rows[start : start + self.page_size],
            }
            for page, start in enumerate(range(0, len(rows), self.page_size))
        ]
        return {
            "source": "Bitvavo public API",
            "quote_currency": "EUR",
            "collected_at": collected_at,
            "ordering": "market identifier ascending; stable pagination only, no ranking",
            "page_size": self.page_size,
            "total_markets": len(rows),
            "total_pages": len(pages),
            "pages": pages,
        }


def _catalog_source_time(value):
    try:
        return _utc_ms(value).isoformat()
    except (ValueError, OverflowError, OSError):
        return None


def _catalog_positive(value):
    try:
        return str(_decimal(value, "market observation"))
    except ValueError:
        return None


def _catalog_nonnegative(value):
    try:
        if isinstance(value, bool):
            return None
        parsed = Decimal(str(value))
        if not parsed.is_finite() or parsed < 0:
            return None
        return str(parsed)
    except (InvalidOperation, ValueError, TypeError):
        return None


def _catalog_change_pct(open_value, last_value):
    opened, last = _catalog_positive(open_value), _catalog_positive(last_value)
    if opened is None or last is None:
        return None
    change = (Decimal(last) - Decimal(opened)) / Decimal(opened) * 100
    return str(change.quantize(Decimal("0.0001")))


def _catalog_observation(value, unit, collected_at, source_at, reason):
    return {
        "value": value,
        "unit": unit,
        "availability": "available" if value is not None else "unavailable",
        "reason": None if value is not None else reason,
        "collected_at": collected_at,
        "source_at": source_at,
        "source_timestamp_available": source_at is not None,
    }
