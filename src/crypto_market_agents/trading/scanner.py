"""Read-only discovery and transparent ranking of existing Bitvavo EUR markets."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
import json
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


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
        self.opener = opener or urlopen
        self.now_provider = now_provider or (lambda: datetime.now(timezone.utc))
        self.timeout = timeout

    def _get(self, endpoint):
        req = Request(
            "https://api.bitvavo.com/v2/" + endpoint, headers={"Accept": "application/json"}
        )
        try:
            with self.opener(req, timeout=self.timeout) as response:
                raw = response.read(5_000_001)
                if len(raw) > 5_000_000:
                    raise ValueError("réponse Bitvavo trop volumineuse")
                return json.loads(raw)
        except HTTPError as exc:
            if exc.code == 429:
                raise RuntimeError("Limite API Bitvavo (HTTP 429); réessayer plus tard.") from None
            raise RuntimeError(f"API Bitvavo indisponible (HTTP {exc.code}).") from None
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
