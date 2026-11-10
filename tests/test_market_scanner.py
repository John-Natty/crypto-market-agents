from contextlib import redirect_stdout
from datetime import datetime, timezone
from decimal import Decimal
from io import StringIO
import json
import tempfile
import sys
from pathlib import Path
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from crypto_market_agents.cli import main
from crypto_market_agents.trading.scanner import (
    BitvavoMarketDiscovery,
    BitvavoScanner,
    ScanConfig,
)


def rows():
    return [
        {
            "market": "AAA-EUR",
            "status": "trading",
            "base": "AAA",
            "quote": "EUR",
            "minOrderInBaseAsset": "1",
            "minOrderInQuoteAsset": "5",
        },
        {
            "market": "BAD-EUR",
            "status": "halted",
            "base": "BAD",
            "quote": "EUR",
            "minOrderInBaseAsset": "1",
            "minOrderInQuoteAsset": "5",
        },
        {"market": "USD-USDT", "status": "trading", "base": "USD", "quote": "USDT"},
    ]


def ticker(market="AAA-EUR", **kw):
    return {
        "market": market,
        "open": "10",
        "last": "11",
        "volume": "200",
        "volumeQuote": "2200",
        "timestamp": 1794000000000,
        **kw,
    }


def book(market="AAA-EUR", **kw):
    return {"market": market, "bid": "10.9", "ask": "11.1", **kw}


class ScannerTests(unittest.TestCase):
    def scan(self, markets=None, tickers=None, books=None, config=ScanConfig()):
        payloads = iter(
            [
                rows() if markets is None else markets,
                [ticker()] if tickers is None else tickers,
                [book()] if books is None else books,
            ]
        )

        class Response:
            def __init__(self, value):
                self.value = json.dumps(value).encode()

            def __enter__(self):
                return self

            def __exit__(self, *args):
                pass

            def read(self, n):
                return self.value

        scanner = BitvavoScanner(
            opener=lambda *_a, **_kw: Response(next(payloads)),
            now_provider=lambda: datetime(2026, 1, 1, tzinfo=timezone.utc),
        )
        return scanner.scan(config)

    def test_discovery_excludes_non_eur_and_unavailable_by_default(self):
        result = self.scan(
            tickers=[ticker(), ticker("BAD-EUR")],
            books=[book(), book("BAD-EUR")],
            config=ScanConfig(Decimal("1000"), Decimal("3")),
        )
        self.assertEqual([x["market"] for x in result["candidates"]], ["AAA-EUR"])
        self.assertIn("statut halted", result["rejected"][0]["reasons"][0])

    def test_change_spread_and_source_time(self):
        item = self.scan(config=ScanConfig(Decimal("1000"), Decimal("3")))["candidates"][0]
        self.assertEqual(item["price_change_24h_pct"], "10.0000")
        self.assertEqual(item["spread_pct"], "1.8182")
        self.assertTrue(item["source_at"].endswith("+00:00"))
        self.assertNotEqual(item["source_at"], item["collected_at"])

    def test_bad_and_incoherent_fields_are_retained_as_rejected(self):
        result = self.scan(
            tickers=[ticker(open="NaN")],
            books=[book(bid="12", ask="11")],
            config=ScanConfig(Decimal("1000"), Decimal("3")),
        )
        self.assertEqual(len(result["rejected"]), 2)
        self.assertTrue(
            any("données manquantes" in reason for reason in result["rejected"][0]["reasons"])
        )

    def test_filters_and_deterministic_ranking(self):
        result = self.scan(config=ScanConfig(Decimal("3000"), Decimal("1")))
        self.assertEqual(result["candidates"], [])
        self.assertIn("volume quote", result["rejected"][0]["reasons"][0])

    def test_api_429_is_reported(self):
        from urllib.error import HTTPError

        scanner = BitvavoScanner(
            opener=lambda *_a, **_kw: (_ for _ in ()).throw(HTTPError("url", 429, "rate", {}, None))
        )
        with self.assertRaisesRegex(RuntimeError, "429"):
            scanner.scan()

    def test_cli_exports_json_and_prints_read_only(self):
        result = {
            "candidates": [],
            "rejected": [],
            "collected_at": "now",
            "quote_currency": "EUR",
            "ranking_note": "note",
        }
        with tempfile.TemporaryDirectory() as directory:
            target = str(Path(directory) / "scan.json")
            with patch("crypto_market_agents.cli.BitvavoScanner") as scanner:
                scanner.return_value.scan.return_value = result
                output = StringIO()
                with redirect_stdout(output):
                    self.assertEqual(main(["scan", "--json", target]), 0)
                self.assertIn("lecture seule", output.getvalue())
                self.assertEqual(json.loads(Path(target).read_text()), result)

    def test_paginated_market_discovery_is_unranked_and_reports_unavailable_observations(self):
        payloads = iter([rows(), [ticker()], [book()]])

        class Response:
            def __init__(self, value):
                self.value = json.dumps(value).encode()

            def __enter__(self):
                return self

            def __exit__(self, *args):
                pass

            def read(self, n):
                return self.value

        discovery = BitvavoMarketDiscovery(
            opener=lambda *_args, **_kwargs: Response(next(payloads)),
            now_provider=lambda: datetime(2026, 1, 1, tzinfo=timezone.utc),
            page_size=1,
        )
        snapshot = discovery.snapshot()
        self.assertEqual(snapshot["total_markets"], 2)
        self.assertEqual(snapshot["total_pages"], 2)
        self.assertIn("no ranking", snapshot["ordering"])
        first = snapshot["pages"][0]["items"][0]
        second = snapshot["pages"][1]["items"][0]
        self.assertEqual([first["market"], second["market"]], ["AAA-EUR", "BAD-EUR"])
        self.assertNotIn("score", first)
        self.assertEqual(first["observations"]["volume_quote_24h"]["value"], "2200")
        self.assertEqual(first["observations"]["volume_quote_24h"]["unit"], "EUR")
        self.assertTrue(first["observations"]["volume_quote_24h"]["source_at"])
        self.assertEqual(second["status"], "halted")
        self.assertEqual(second["observations"]["bid"]["availability"], "unavailable")
        self.assertEqual(
            second["observations"]["bid"]["reason"],
            "public_orderbook_bid_absent_or_invalid",
        )


if __name__ == "__main__":
    unittest.main()
