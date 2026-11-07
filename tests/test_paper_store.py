from datetime import datetime, timezone
from decimal import Decimal as D
import json
import multiprocessing
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from crypto_market_agents.trading import Decision, PaperEngine, PaperStore, Quote, TradingError

NOW = datetime(2026, 10, 5, 12, tzinfo=timezone.utc)
BUY = Decision("buy", "BTC-EUR", D("10"), "test", "test")
QUOTE = Quote("BTC-EUR", D("100"), D("100"), NOW)


def concurrent_buy(path, start, result):
    start.wait(5)
    try:
        with PaperStore(path).transaction() as portfolio:
            event = PaperEngine().step(portfolio, BUY, QUOTE, "same-id", now=NOW)
        result.put(event["duplicate"])
    except Exception as exc:
        result.put(str(exc))


class PaperStoreTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.path = Path(self.temporary.name) / "paper.json"
        self.store = PaperStore(self.path)
        self.store.initialize(market="BTC-EUR", market_source="mock")

    def buy(self, identifier="buy-1"):
        with self.store.transaction() as portfolio:
            return PaperEngine().step(portfolio, BUY, QUOTE, identifier, now=NOW)

    def test_roundtrip_restart_keeps_balances_and_idempotent_ids(self):
        self.buy()
        prior = self.store.load().to_dict()
        restarted = PaperStore(self.path)
        with restarted.transaction() as portfolio:
            event = PaperEngine().step(portfolio, BUY, QUOTE, "buy-1", now=NOW)
        self.assertTrue(event["duplicate"])
        self.assertEqual(restarted.load().to_dict(), prior)
        self.assertEqual(self.path.stat().st_mode & 0o777, 0o600)

    def test_initializer_never_resets_existing_or_corrupt_state(self):
        self.buy()
        with self.assertRaisesRegex(TradingError, "already exists"):
            self.store.initialize(market="BTC-EUR", market_source="mock")
        self.path.write_text("broken")
        with self.assertRaisesRegex(TradingError, "already exists"):
            self.store.initialize(market="BTC-EUR", market_source="mock")
        self.assertEqual(self.path.read_text(), "broken")

    def test_missing_state_requires_explicit_initialization(self):
        path = self.path.with_name("missing.json")
        with self.assertRaisesRegex(TradingError, "initialize"):
            PaperStore(path).load()
        self.assertFalse(path.exists())

    def test_expected_source_and_market_prevent_contamination(self):
        with self.assertRaisesRegex(TradingError, "source does not match"):
            self.store.load(expected_source="bitvavo")
        with self.assertRaisesRegex(TradingError, "market does not match"):
            with self.store.transaction(expected_market="ETH-EUR"):
                pass
        self.store.load(expected_market="BTC-EUR", expected_source="mock")

    def test_exception_rolls_back_entire_transaction(self):
        before = self.path.read_bytes()
        with self.assertRaisesRegex(RuntimeError, "simulated crash"):
            with self.store.transaction() as portfolio:
                PaperEngine().step(portfolio, BUY, QUOTE, "id", now=NOW)
                raise RuntimeError("simulated crash")
        self.assertEqual(self.path.read_bytes(), before)

    def test_replace_failure_preserves_state_and_cleans_temporary(self):
        before = self.path.read_bytes()
        with patch(
            "crypto_market_agents.trading.store.os.replace", side_effect=OSError("disk failure")
        ):
            with self.assertRaises(OSError):
                self.buy()
        self.assertEqual(self.path.read_bytes(), before)
        self.assertFalse(list(self.path.parent.glob("*.tmp")))
        self.assertFalse(list(self.path.parent.glob(".*.tmp")))
        self.store.load()

    def test_invalid_transaction_cannot_change_identity_or_balances(self):
        for name, value in (
            ("market_source", "bitvavo"),
            ("initial_cash", D("40")),
            ("cash", D("100")),
        ):
            before = self.path.read_bytes()
            with self.subTest(name=name), self.assertRaises(TradingError):
                with self.store.transaction() as portfolio:
                    setattr(portfolio, name, value)
            self.assertEqual(self.path.read_bytes(), before)

    def test_corrupt_json_and_duplicate_keys_fail_closed(self):
        for text in ('{"cash":', '{"version":1,"version":1}', "[]", "null", "\ufffd"):
            self.path.write_text(text)
            with self.subTest(text=text), self.assertRaises(TradingError):
                self.store.load()
            self.assertEqual(self.path.read_text(), text)

    def test_invalid_version_unknown_fields_and_money_types_fail_closed(self):
        original = json.loads(self.path.read_text())
        for change in (
            {"version": 99},
            {"version": True},
            {"cash": 50},
            {"cash": "NaN"},
            {"cash": "-1"},
            {"unexpected": "field"},
            {"created_at": "2026-10-05"},
            {"buying_paused": "false"},
            {"peak_equity": "0"},
            {"history": [{}]},
            {"first_quote": {}},
            {"last_bid": "0"},
        ):
            self.path.write_text(json.dumps({**original, **change}))
            with self.subTest(change=change), self.assertRaises(TradingError):
                self.store.load()
        self.path.write_text(json.dumps(original))
        raw = dict(original)
        del raw["ledger"]
        self.path.write_text(json.dumps(raw))
        with self.assertRaises(TradingError):
            self.store.load()

    def test_ledger_replay_detects_tampered_fees_balances_and_duplicates(self):
        self.buy()
        original = json.loads(self.path.read_text())
        for field, value in (
            ("fee_eur", "0"),
            ("notional_eur", "0"),
            ("quantity_after", "0"),
            ("status", "held"),
        ):
            raw = json.loads(json.dumps(original))
            raw["ledger"][0][field] = value
            self.path.write_text(json.dumps(raw))
            with self.subTest(field=field), self.assertRaises(TradingError):
                self.store.load()
        raw = json.loads(json.dumps(original))
        raw["ledger"].append(raw["ledger"][0].copy())
        self.path.write_text(json.dumps(raw))
        with self.assertRaises(TradingError):
            self.store.load()

    def test_symlink_state_is_refused(self):
        link = self.path.with_name("link.json")
        link.symlink_to(self.path)
        with self.assertRaisesRegex(TradingError, "symbolic link"):
            PaperStore(link).load()
        with self.assertRaises(TradingError):
            PaperStore(link).initialize(market="BTC-EUR", market_source="mock")

    def test_no_fcntl_is_explicit_failure(self):
        with patch("crypto_market_agents.trading.store.fcntl", None):
            with self.assertRaisesRegex(TradingError, "fcntl"):
                self.store.load()

    def test_oversized_file_fails_closed(self):
        with patch("crypto_market_agents.trading.store.MAX_STATE_BYTES", 10):
            with self.assertRaisesRegex(TradingError, "maximum supported size"):
                self.store.load()

    def test_process_lock_makes_same_id_concurrent_buy_exactly_once(self):
        context = multiprocessing.get_context("fork")
        start = context.Event()
        results = context.Queue()
        children = [
            context.Process(target=concurrent_buy, args=(str(self.path), start, results))
            for _ in range(4)
        ]
        for child in children:
            child.start()
            self.addCleanup(lambda child=child: child.terminate() if child.is_alive() else None)
        start.set()
        for child in children:
            child.join(8)
            self.assertEqual(child.exitcode, 0)
        returned = [results.get(timeout=2) for _ in children]
        self.assertEqual(returned.count(False), 1, returned)
        self.assertEqual(returned.count(True), 3, returned)
        portfolio = self.store.load()
        self.assertEqual(len(portfolio.ledger), 1)
        self.assertGreater(portfolio.cash, D("39"))
        self.assertLess(portfolio.cash, D("41"))


if __name__ == "__main__":
    unittest.main()
