from datetime import datetime, timedelta, timezone
from decimal import Decimal as D
from pathlib import Path
import multiprocessing
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from io import StringIO

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from crypto_market_agents.trading.models import Quote, TradingError
from crypto_market_agents.trading.multi_asset import (
    CAPITAL_EUR,
    MultiAssetOrder,
    MultiAssetPaperEngine,
    MultiAssetPaperStore,
    MultiAssetPortfolio,
    MultiAssetRiskConfig,
    position_key,
)
from crypto_market_agents.trading.multi_asset_market import (
    MultiAssetQuoteRefresher,
    MultiAssetQuoteStore,
)
from crypto_market_agents.trading.market import MarketDataError
from crypto_market_agents.cli import main

NOW = datetime(2026, 11, 9, 12, tzinfo=timezone.utc)


def quote(market, bid="100", ask="100.1", when=NOW):
    return Quote(market, D(bid), D(ask), when)


def buy(market, amount="60", platform="mock"):
    return MultiAssetOrder("buy", platform, market, "test buy", amount_eur=D(amount))


def _concurrent_multi_buy(path, barrier, result_queue):
    barrier.wait(5)
    store = MultiAssetPaperStore(path)
    try:
        with store.transaction() as portfolio:
            result = MultiAssetPaperEngine().execute(
                portfolio, buy("BTC-EUR", "40"), quote("BTC-EUR"), "one-cycle", now=NOW
            )
        result_queue.put(result["duplicate"])
    except Exception as exc:
        result_queue.put(f"{type(exc).__name__}:{exc}")


class MultiAssetPaperTests(unittest.TestCase):
    def setUp(self):
        self.portfolio = MultiAssetPortfolio.create()
        self.engine = MultiAssetPaperEngine()

    def test_two_assets_share_one_fixed_150_eur_reserve(self):
        self.assertEqual(self.portfolio.cash_eur, CAPITAL_EUR)
        first = self.engine.execute(
            self.portfolio, buy("BTC-EUR"), quote("BTC-EUR"), "btc-1", now=NOW
        )
        second = self.engine.execute(
            self.portfolio, buy("ETH-EUR"), quote("ETH-EUR", "50", "50.05"), "eth-1", now=NOW
        )
        self.assertEqual((first["status"], second["status"]), ("executed", "executed"))
        self.assertEqual(set(self.portfolio.positions), {"mock|BTC-EUR", "mock|ETH-EUR"})
        self.assertLess(self.portfolio.cash_eur, D("31"))
        self.assertGreater(self.portfolio.cash_eur, D("29"))
        self.assertEqual(
            self.portfolio.cash_eur + self.portfolio.invested_cost_basis_eur, CAPITAL_EUR
        )

    def test_same_market_on_different_platforms_uses_distinct_positions(self):
        self.engine.execute(
            self.portfolio,
            buy("BTC-EUR", "20", "venue-a"),
            quote("BTC-EUR"),
            "venue-a-buy",
            now=NOW,
        )
        self.engine.execute(
            self.portfolio,
            buy("BTC-EUR", "20", "venue-b"),
            quote("BTC-EUR"),
            "venue-b-buy",
            now=NOW,
        )
        self.assertIn("venue-a|BTC-EUR", self.portfolio.positions)
        self.assertIn("venue-b|BTC-EUR", self.portfolio.positions)

    def test_purchase_exceeding_remaining_shared_cash_is_rejected(self):
        self.engine.execute(
            self.portfolio, buy("BTC-EUR", "60"), quote("BTC-EUR"), "btc-1", now=NOW
        )
        self.engine.execute(
            self.portfolio, buy("ETH-EUR", "60"), quote("ETH-EUR"), "eth-1", now=NOW
        )
        cash_before = self.portfolio.cash_eur
        result = self.engine.execute(
            self.portfolio, buy("ADA-EUR", "40"), quote("ADA-EUR"), "ada-1", now=NOW
        )
        self.assertEqual(result["status"], "rejected")
        self.assertEqual(result["reason"], "Insufficient shared cash")
        self.assertEqual(self.portfolio.cash_eur, cash_before)
        self.assertNotIn("mock|ADA-EUR", self.portfolio.positions)

    def test_global_and_per_position_exposure_limits_are_enforced(self):
        per_position = MultiAssetPortfolio.create(
            MultiAssetRiskConfig(max_position_eur=D("50"), max_total_exposure_eur=D("80"))
        )
        rejected_position = self.engine.execute(
            per_position, buy("BTC-EUR", "55"), quote("BTC-EUR"), "position-cap", now=NOW
        )
        self.assertEqual(rejected_position["status"], "rejected")
        self.assertIn("max_position_eur", rejected_position["reason"])

        global_cap = MultiAssetPortfolio.create(
            MultiAssetRiskConfig(
                max_trade_eur=D("50"), max_position_eur=D("50"), max_total_exposure_eur=D("80")
            )
        )
        self.engine.execute(global_cap, buy("BTC-EUR", "45"), quote("BTC-EUR"), "global-1", now=NOW)
        rejected_global = self.engine.execute(
            global_cap, buy("ETH-EUR", "40"), quote("ETH-EUR"), "global-2", now=NOW
        )
        self.assertEqual(rejected_global["status"], "rejected")
        self.assertIn("max_total_exposure_eur", rejected_global["reason"])

    def test_sell_without_position_and_order_below_market_minimum_are_rejected(self):
        no_short = self.engine.execute(
            self.portfolio,
            MultiAssetOrder("sell", "mock", "BTC-EUR", "no short", quantity=D("0.1")),
            quote("BTC-EUR"),
            "short-attempt",
            now=NOW,
        )
        self.assertEqual(no_short["status"], "rejected")
        self.assertIn("no greater than the held position", no_short["reason"])
        too_small = self.engine.execute(
            self.portfolio, buy("BTC-EUR", "5"), quote("BTC-EUR"), "too-small", now=NOW
        )
        self.assertEqual(too_small["status"], "rejected")
        self.assertIn("market minimum", too_small["reason"])

    def test_partial_sale_accounts_for_cost_basis_and_both_fees(self):
        self.engine.execute(
            self.portfolio, buy("BTC-EUR", "60"), quote("BTC-EUR"), "buy-1", now=NOW
        )
        position = self.portfolio.positions["mock|BTC-EUR"]
        quantity_before, basis_before = position.quantity, position.cost_basis_eur
        sell_quantity = quantity_before / 2
        sale_quote = quote("BTC-EUR", "110", "110.1")
        event = self.engine.execute(
            self.portfolio,
            MultiAssetOrder("sell", "mock", "BTC-EUR", "partial sale", quantity=sell_quantity),
            sale_quote,
            "sell-1",
            now=NOW,
        )
        self.assertEqual(event["status"], "executed", event["reason"])
        removed_basis = basis_before * D(event["quantity"]) / quantity_before
        removed_basis = removed_basis.quantize(D("1e-12"), rounding="ROUND_FLOOR")
        net_proceeds = D(event["notional_eur"]) - D(event["fee_eur"])
        self.assertEqual(self.portfolio.realized_pnl_eur, net_proceeds - removed_basis)
        self.assertEqual(position.cost_basis_eur, basis_before - removed_basis)
        self.assertEqual(
            self.portfolio.fees_paid_eur,
            D(event["fee_eur"]) + D(self.portfolio.ledger[0]["fee_eur"]),
        )

    def test_replayed_cycle_is_idempotent_even_after_restart(self):
        with tempfile.TemporaryDirectory() as directory:
            store = MultiAssetPaperStore(Path(directory) / "portfolio.json")
            store.initialize()
            with store.transaction() as portfolio:
                first = self.engine.execute(
                    portfolio, buy("BTC-EUR"), quote("BTC-EUR"), "cycle-1", now=NOW
                )
            before = store.load().to_dict()
            with store.transaction() as portfolio:
                replay = self.engine.execute(
                    portfolio,
                    buy("BTC-EUR"),
                    quote("BTC-EUR", when=NOW + timedelta(days=1)),
                    "cycle-1",
                    now=NOW + timedelta(days=1),
                )
            self.assertFalse(first["duplicate"])
            self.assertTrue(replay["duplicate"])
            self.assertEqual(store.load().to_dict(), before)

    def test_restart_preserves_shared_cash_positions_fees_and_history_across_days(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "multi.json"
            store = MultiAssetPaperStore(path)
            store.initialize()
            with store.transaction() as portfolio:
                self.engine.execute(
                    portfolio, buy("BTC-EUR"), quote("BTC-EUR"), "btc-day1", now=NOW
                )
                self.engine.execute(
                    portfolio, buy("ETH-EUR"), quote("ETH-EUR"), "eth-day1", now=NOW
                )
            reopened = MultiAssetPaperStore(path).load()
            later = reopened.created_at + timedelta(days=10)
            self.assertEqual(set(reopened.positions), {"mock|BTC-EUR", "mock|ETH-EUR"})
            self.assertEqual(reopened.cash_eur, store.load().cash_eur)
            self.assertEqual(len(reopened.ledger), 2)
            self.assertEqual(reopened.fees_paid_eur, store.load().fees_paid_eur)
            self.assertFalse(reopened.valuation(now=later)["complete"])

    def test_stale_quote_cannot_execute_and_position_value_becomes_incomplete(self):
        self.engine.execute(self.portfolio, buy("BTC-EUR"), quote("BTC-EUR"), "btc-1", now=NOW)
        cash_before = self.portfolio.cash_eur
        stale = quote("ETH-EUR", when=NOW - timedelta(seconds=121))
        rejected = self.engine.execute(self.portfolio, buy("ETH-EUR"), stale, "eth-stale", now=NOW)
        self.assertEqual(rejected["status"], "rejected")
        self.assertEqual(rejected["reason"], "quote_stale_or_future")
        self.assertEqual(self.portfolio.cash_eur, cash_before)
        self.assertNotIn("mock|ETH-EUR", self.portfolio.positions)
        valuation = self.portfolio.valuation(now=NOW + timedelta(seconds=121))
        self.assertFalse(valuation["complete"])
        self.assertIsNone(valuation["total_value_eur"])
        self.assertIsNone(valuation["positions"][0]["market_value_eur"])
        self.assertEqual(valuation["positions"][0]["status"], "quote_stale_or_future")

    def test_unknown_or_missing_quote_is_never_rendered_as_zero_value(self):
        self.engine.execute(self.portfolio, buy("BTC-EUR"), quote("BTC-EUR"), "btc-1", now=NOW)
        result = self.portfolio.valuation({}, now=NOW + timedelta(seconds=121))
        self.assertFalse(result["complete"])
        self.assertIsNone(result["total_value_eur"])
        self.assertIsNone(result["positions"][0]["market_value_eur"])

    def test_new_format_does_not_migrate_or_overwrite_existing_single_asset_state(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "legacy.json"
            path.write_text('{"version":1,"market":"BTC-EUR"}')
            before = path.read_bytes()
            with self.assertRaisesRegex(TradingError, "already exists"):
                MultiAssetPaperStore(path).initialize()
            with self.assertRaisesRegex(TradingError, "Not a multi-asset"):
                MultiAssetPaperStore(path).load()
            self.assertEqual(path.read_bytes(), before)

    def test_risk_policy_is_bounded_and_versioned(self):
        with self.assertRaisesRegex(TradingError, "at most 150"):
            MultiAssetPortfolio.create(MultiAssetRiskConfig(max_total_exposure_eur=D("151")))
        raw = self.portfolio.to_dict()
        raw["risk_policy"]["version"] = 999
        with self.assertRaisesRegex(TradingError, "risk policy version"):
            MultiAssetPortfolio.from_dict(raw)
        self.assertEqual(position_key("bitvavo", "BTC-EUR"), "bitvavo|BTC-EUR")

    def test_transaction_exception_rolls_back_multi_asset_state(self):
        with tempfile.TemporaryDirectory() as directory:
            store = MultiAssetPaperStore(Path(directory) / "portfolio.json")
            store.initialize()
            before = store.path.read_bytes()
            with self.assertRaisesRegex(RuntimeError, "crash"):
                with store.transaction() as portfolio:
                    self.engine.execute(portfolio, buy("BTC-EUR"), quote("BTC-EUR"), "buy", now=NOW)
                    raise RuntimeError("crash")
            self.assertEqual(store.path.read_bytes(), before)

    def test_process_lock_allows_same_cycle_exactly_once(self):
        context = multiprocessing.get_context("fork")
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "portfolio.json"
            MultiAssetPaperStore(path).initialize()
            barrier, result_queue = context.Event(), context.Queue()
            workers = [
                context.Process(
                    target=_concurrent_multi_buy, args=(str(path), barrier, result_queue)
                )
                for _ in range(4)
            ]
            for worker in workers:
                worker.start()
            barrier.set()
            for worker in workers:
                worker.join(8)
                self.assertEqual(worker.exitcode, 0)
            results = [result_queue.get(timeout=2) for _ in workers]
            self.assertEqual(results.count(False), 1, results)
            self.assertEqual(results.count(True), 3, results)
            loaded = MultiAssetPaperStore(path).load()
            self.assertEqual(len(loaded.ledger), 1)
            self.assertEqual(
                loaded.positions["mock|BTC-EUR"].quantity, D(loaded.ledger[0]["quantity"])
            )

    def test_atomic_replace_failure_keeps_previous_multi_asset_state(self):
        from unittest.mock import patch

        with tempfile.TemporaryDirectory() as directory:
            store = MultiAssetPaperStore(Path(directory) / "portfolio.json")
            store.initialize()
            before = store.path.read_bytes()
            with patch(
                "crypto_market_agents.trading.multi_asset.os.replace",
                side_effect=OSError("disk failure"),
            ):
                with self.assertRaises(OSError):
                    with store.transaction() as portfolio:
                        MultiAssetPaperEngine().execute(
                            portfolio,
                            buy("BTC-EUR", "20"),
                            quote("BTC-EUR"),
                            "atomic",
                            now=NOW,
                        )
            self.assertEqual(store.path.read_bytes(), before)
            self.assertEqual(list(Path(directory).glob(".*.tmp")), [])

    def test_cli_initializes_and_trades_with_manual_quotes_only(self):
        with tempfile.TemporaryDirectory() as directory:
            path = str(Path(directory) / "multi.json")
            commands = (
                ["paper-multi", "init", "--state", path],
                [
                    "paper-multi",
                    "trade",
                    "--state",
                    path,
                    "--action",
                    "buy",
                    "--platform",
                    "mock",
                    "--market",
                    "BTC-EUR",
                    "--bid",
                    "100",
                    "--ask",
                    "100.05",
                    "--amount-eur",
                    "60",
                    "--decision-id",
                    "cli-btc",
                ],
                ["paper-multi", "status", "--state", path, "--json"],
            )
            for args in commands:
                with redirect_stdout(StringIO()):
                    self.assertEqual(main(args), 0)
            state = MultiAssetPaperStore(path).load()
            self.assertEqual(state.initial_capital_eur, D("150"))
            self.assertIn("mock|BTC-EUR", state.positions)

    def test_public_quote_refresh_is_partial_read_only_and_preserves_last_quote(self):
        class FakeClient:
            def __init__(self):
                self.calls = []

            def get_quote(self, market):
                self.calls.append(market)
                if market == "ETH-EUR":
                    raise MarketDataError("private details are omitted")
                return Quote(market, D("101"), D("101.1"), NOW, D("5"), D("0.001"), D("0.01"))

        with tempfile.TemporaryDirectory() as directory:
            state_path = Path(directory) / "portfolio.json"
            store = MultiAssetPaperStore(state_path)
            store.initialize()
            with store.transaction() as portfolio:
                self.engine.execute(
                    portfolio, buy("BTC-EUR", "40", "bitvavo"), quote("BTC-EUR"), "btc", now=NOW
                )
                self.engine.execute(
                    portfolio, buy("ETH-EUR", "40", "bitvavo"), quote("ETH-EUR"), "eth", now=NOW
                )
            before = store.load().to_dict()
            quote_store = MultiAssetQuoteStore(Path(directory) / "quotes.json")
            refresher = MultiAssetQuoteRefresher(client=FakeClient(), now_provider=lambda: NOW)
            result = refresher.refresh(store, quote_store)
            after = store.load().to_dict()
            observations = quote_store.load_for(store.load())["observations"]

            self.assertEqual(result["status"], "partial")
            self.assertTrue(result["read_only"])
            self.assertEqual(result["transactions_before"], result["transactions_after"])
            self.assertEqual(before, after)
            self.assertEqual(observations["bitvavo|BTC-EUR"]["status"], "available")
            self.assertEqual(observations["bitvavo|ETH-EUR"]["status"], "unavailable")
            self.assertEqual(
                observations["bitvavo|ETH-EUR"]["last_quote"]["timestamp"], NOW.isoformat()
            )
            self.assertFalse(result["valuation"]["complete"])
            self.assertIsNone(result["valuation"]["current_market_exposure_eur"])
            expected_basis = sum(
                (D(item["cost_basis_eur"]) for item in before["positions"].values()), D("0")
            )
            self.assertEqual(D(result["valuation"]["budget_engaged_eur"]), expected_basis)

    def test_buy_requires_fresh_global_exposure_but_valid_reduction_is_allowed(self):
        with tempfile.TemporaryDirectory() as directory:
            store = MultiAssetPaperStore(Path(directory) / "portfolio.json")
            store.initialize()
            with store.transaction() as portfolio:
                self.engine.execute(
                    portfolio, buy("BTC-EUR", "40", "bitvavo"), quote("BTC-EUR"), "btc", now=NOW
                )
            self.engine.execute(
                portfolio, buy("ETH-EUR", "40", "bitvavo"), quote("ETH-EUR"), "eth", now=NOW
            )
            observation = {
                "platform": "bitvavo",
                "market": "BTC-EUR",
                "status": "available",
                "last_attempt_at": NOW.isoformat(),
                "last_quote": {
                    "bid": "100",
                    "ask": "100.1",
                    "timestamp": NOW.isoformat(),
                    "min_order_eur": "5",
                    "quantity_step": "0.00000001",
                    "min_quantity": "0",
                },
                "source": "bitvavo_public",
                "error": None,
            }
            missing_eth = {
                "bitvavo|BTC-EUR": observation,
                "bitvavo|ETH-EUR": {
                    **observation,
                    "market": "ETH-EUR",
                    "status": "unavailable",
                    "last_quote": None,
                    "error": "public_quote_failed:MarketDataError",
                },
            }
            blocked = self.engine.execute(
                portfolio,
                buy("ADA-EUR", "20", "bitvavo"),
                quote("ADA-EUR"),
                "ada",
                quote_observations=missing_eth,
                now=NOW,
            )
            self.assertEqual(blocked["status"], "rejected")
            self.assertIn("global market exposure cannot be verified", blocked["reason"])
            position = portfolio.positions["bitvavo|BTC-EUR"]
            reduced = self.engine.execute(
                portfolio,
                MultiAssetOrder(
                    "sell", "bitvavo", "BTC-EUR", "reduce", quantity=position.quantity / 2
                ),
                quote("BTC-EUR", "105", "105.1"),
                "btc-reduce",
                quote_observations=missing_eth,
                now=NOW,
            )
            self.assertEqual(reduced["status"], "executed", reduced["reason"])

    def test_quote_refresh_reports_stale_response_without_losing_last_observation(self):
        class StaleClient:
            def get_quote(self, market):
                return Quote(market, D("100"), D("100.1"), NOW - timedelta(seconds=121))

        with tempfile.TemporaryDirectory() as directory:
            store = MultiAssetPaperStore(Path(directory) / "portfolio.json")
            store.initialize()
            with store.transaction() as portfolio:
                self.engine.execute(
                    portfolio,
                    buy("BTC-EUR", "40", "bitvavo"),
                    quote("BTC-EUR"),
                    "btc",
                    now=NOW,
                )
            sidecar = MultiAssetQuoteStore(Path(directory) / "quotes.json")
            result = MultiAssetQuoteRefresher(
                client=StaleClient(), now_provider=lambda: NOW
            ).refresh(store, sidecar)
            observation = sidecar.load_for(store.load())["observations"]["bitvavo|BTC-EUR"]
            self.assertEqual(result["status"], "partial")
            self.assertEqual(observation["status"], "unavailable")
            self.assertEqual(observation["error"], "quote_stale_or_future")
            self.assertEqual(observation["last_quote"]["timestamp"], NOW.isoformat())
            self.assertFalse(result["valuation"]["complete"])

    def test_valuation_separates_decimal_cost_basis_from_current_market_value(self):
        self.engine.execute(self.portfolio, buy("BTC-EUR", "40"), quote("BTC-EUR"), "btc", now=NOW)
        before = self.portfolio.positions["mock|BTC-EUR"].cost_basis_eur
        marked = quote("BTC-EUR", "120", "120.1")
        valuation = self.portfolio.valuation({"mock|BTC-EUR": marked}, now=NOW)
        self.assertEqual(valuation["budget_engaged_eur"], str(before))
        self.assertEqual(self.portfolio.positions["mock|BTC-EUR"].cost_basis_eur, before)
        self.assertNotEqual(valuation["current_market_exposure_eur"], str(before))
        self.assertEqual(
            D(valuation["positions"][0]["market_value_eur"]),
            D(valuation["positions"][0]["quantity"]) * D("120"),
        )

    def test_refresh_cli_uses_public_client_and_does_not_add_transactions(self):
        from unittest.mock import patch

        with tempfile.TemporaryDirectory() as directory:
            state_path = Path(directory) / "portfolio.json"
            store = MultiAssetPaperStore(state_path)
            store.initialize()
            current_time = datetime.now(timezone.utc)
            with store.transaction() as portfolio:
                self.engine.execute(
                    portfolio,
                    buy("BTC-EUR", "30", "bitvavo"),
                    quote("BTC-EUR", when=current_time),
                    "btc-cli-seed",
                    now=current_time,
                )

            class FakePublicClient:
                def __init__(self, **kwargs):
                    pass

                def get_quote(self, market):
                    return Quote(
                        market,
                        D("101"),
                        D("101.1"),
                        current_time,
                        D("5"),
                        D("0.0001"),
                        D("0.001"),
                    )

            before = store.load().to_dict()
            with (
                patch(
                    "crypto_market_agents.trading.multi_asset_cli.BitvavoMarketData",
                    FakePublicClient,
                ),
                redirect_stdout(StringIO()),
            ):
                exit_code = main(["paper-multi", "refresh", "--state", str(state_path)])
            after = store.load().to_dict()
            self.assertEqual(exit_code, 0)
            self.assertEqual(before, after)
            sidecar = MultiAssetQuoteStore(f"{state_path}.quotes-v1.json")
            observation = sidecar.load_for(store.load())["observations"]["bitvavo|BTC-EUR"]
            self.assertEqual(observation["source"], "bitvavo_public")
            self.assertEqual(observation["last_quote"]["bid"], "101")


if __name__ == "__main__":
    unittest.main()
