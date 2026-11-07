from dataclasses import replace
from datetime import datetime, timedelta, timezone
from decimal import Decimal as D
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from crypto_market_agents.trading import (
    Decision,
    PaperEngine,
    Portfolio,
    Quote,
    RiskConfig,
    TradingError,
)

NOW = datetime(2026, 10, 5, 12, tzinfo=timezone.utc)


class PaperEngineTests(unittest.TestCase):
    def setUp(self):
        self.portfolio = Portfolio.create("BTC-EUR", "mock")
        self.engine = PaperEngine()
        self.quote = Quote("BTC-EUR", D("100"), D("100"), NOW)
        self.sequence = 0

    def step(self, action="buy", amount="10", quote=None, portfolio=None, decision_id=None):
        self.sequence += 1
        decision = Decision(action, "BTC-EUR", D(amount), "test", "test")
        return self.engine.step(
            portfolio or self.portfolio,
            decision,
            quote or self.quote,
            decision_id or str(self.sequence),
            now=NOW,
        )

    def test_buy_then_sell_reconciles_fees_slippage_and_cost_basis(self):
        buy = self.step()
        self.assertEqual(buy["status"], "executed")
        self.assertLessEqual(D(buy["notional_eur"]) + D(buy["fee_eur"]), D("10"))
        self.assertGreater(D(buy["execution_price"]), self.quote.ask)
        self.assertEqual(self.portfolio.cash + self.portfolio.cost_basis, D("50"))
        sell = self.step("sell")
        self.assertEqual(sell["status"], "executed")
        self.assertLess(D(sell["execution_price"]), self.quote.bid)
        self.assertEqual(self.portfolio.quantity, 0)
        self.assertEqual(self.portfolio.cost_basis, 0)
        self.assertEqual(self.portfolio.realized_pnl, self.portfolio.cash - D("50"))
        self.assertEqual(self.portfolio.fees_paid, D(buy["fee_eur"]) + D(sell["fee_eur"]))
        self.assertLess(self.portfolio.cash, D("50"))
        self.assertEqual(Portfolio.from_dict(self.portfolio.to_dict()), self.portfolio)

    def test_partial_sale_preserves_remaining_cost_basis(self):
        cfg = RiskConfig(fee_rate=D("0"), slippage_bps=D("0"))
        p = Portfolio.create("BTC-EUR", "mock", config=cfg)
        self.step(portfolio=p)
        sell = self.step("sell", "5", portfolio=p)
        self.assertEqual(sell["status"], "executed")
        self.assertEqual(p.cost_basis, D("5"))
        self.assertEqual(p.quantity, D("0.05"))
        self.assertEqual(p.cash, D("45"))
        self.assertEqual(p.realized_pnl, 0)

    def test_sell_caps_quantity_at_holdings(self):
        self.step(amount="7")
        held = self.portfolio.quantity
        sale = self.step("sell", "10")
        self.assertEqual(D(sale["quantity"]), held)
        self.assertEqual(self.portfolio.quantity, 0)

    def test_cannot_short_or_exceed_max_trade(self):
        self.assertEqual(self.step("sell")["status"], "rejected")
        self.assertIn("max_trade", self.step(amount="10.01")["reason"])
        self.assertEqual(self.portfolio.cash, D("50"))

    def test_position_limit_blocks_third_purchase(self):
        self.step()
        self.step()
        third = self.step()
        self.assertEqual(third["status"], "rejected")
        self.assertIn("max_position", third["reason"])
        self.assertLessEqual(self.portfolio.quantity * self.quote.ask, D("25"))

    def test_insufficient_cash_includes_fees(self):
        p = Portfolio.create("BTC-EUR", "mock", initial_cash=D("8"))
        result = self.step(portfolio=p)
        self.assertEqual(result["status"], "rejected")
        self.assertIn("Insufficient cash", result["reason"])
        self.assertEqual(p.cash, D("8"))

    def test_amount_and_quantity_minimums_apply_after_rounding(self):
        for quote, amount in (
            (self.quote, "5"),
            (replace(self.quote, min_quantity=D("0.11")), "10"),
            (replace(self.quote, quantity_step=D("0.1")), "10"),
        ):
            with self.subTest(quote=quote, amount=amount):
                self.assertEqual(self.step(amount=amount, quote=quote)["status"], "rejected")
        self.assertEqual(self.portfolio.cash, D("50"))

    def test_lots_round_down_and_total_spend_never_exceeds_request(self):
        result = self.step(quote=replace(self.quote, quantity_step=D("0.03")))
        self.assertEqual(result["status"], "executed")
        self.assertEqual(D(result["quantity"]) % D("0.03"), 0)
        self.assertLessEqual(D("50") - self.portfolio.cash, D("10"))

    def test_nonfinite_negative_and_wrong_type_decisions_rejected(self):
        for amount in (D("NaN"), D("Infinity"), D("-1"), D("0"), D("1e100"), 10, "10", True):
            with self.subTest(amount=amount):
                self.sequence += 1
                result = self.engine.step(
                    self.portfolio,
                    Decision("buy", "BTC-EUR", amount, "test", "test"),
                    self.quote,
                    str(self.sequence),
                    now=NOW,
                )
                self.assertEqual(result["status"], "rejected")
        self.assertEqual(self.portfolio.cash, D("50"))
        self.portfolio.validate()

    def test_invalid_action_and_market_and_nonzero_hold_rejected(self):
        for decision in (
            Decision("withdraw", "BTC-EUR", D("10"), "test", "test"),
            Decision("buy", "ETH-EUR", D("10"), "test", "test"),
            Decision("hold", "BTC-EUR", D("10"), "test", "test"),
            Decision("buy", "BTC-USD", D("10"), "test", "test"),
        ):
            self.sequence += 1
            result = self.engine.step(
                self.portfolio, decision, self.quote, str(self.sequence), now=NOW
            )
            self.assertEqual(result["status"], "rejected")

    def test_stale_future_naive_and_out_of_order_quotes_rejected(self):
        self.step("hold", "0")
        history = self.portfolio.history.copy()
        for timestamp in (
            NOW - timedelta(seconds=121),
            NOW + timedelta(microseconds=1),
            NOW.replace(tzinfo=None),
            NOW - timedelta(seconds=1),
        ):
            with self.subTest(timestamp=timestamp):
                result = self.step(quote=replace(self.quote, timestamp=timestamp))
                self.assertEqual(result["status"], "rejected")
        self.assertEqual(self.portfolio.history, history)
        self.assertEqual(self.portfolio.cash, D("50"))

    def test_invalid_market_numbers_spread_and_lot_rejected(self):
        for changes in (
            {"market": "ETH-EUR"},
            {"bid": D("NaN")},
            {"ask": D("Infinity")},
            {"bid": D("0")},
            {"ask": D("99")},
            {"ask": D("102")},
            {"quantity_step": D("0")},
            {"quantity_step": D("1e-19")},
            {"min_order_eur": D("0")},
            {"min_quantity": D("-1")},
        ):
            with self.subTest(changes=changes):
                self.assertEqual(
                    self.step(quote=replace(self.quote, **changes))["status"], "rejected"
                )
        self.assertIsNone(self.portfolio.last_bid)
        self.assertIsNone(self.portfolio.first_quote)

    def test_loss_limit_latches_buys_but_allows_sells(self):
        self.step()
        self.step()
        crash = replace(self.quote, bid=D("70"), ask=D("70"))
        result = self.step(quote=crash)
        self.assertEqual(result["status"], "rejected")
        self.assertTrue(self.portfolio.buying_paused)
        self.assertGreater(self.portfolio.max_drawdown_eur, D("5"))
        self.assertEqual(self.step(quote=self.quote)["status"], "rejected")
        self.assertEqual(self.step("sell", quote=self.quote)["status"], "executed")
        self.assertTrue(self.portfolio.buying_paused)

    def test_profit_then_drawdown_latches_even_above_initial_balance(self):
        self.step()
        gain = replace(self.quote, bid=D("300"), ask=D("300"))
        self.step("hold", "0", quote=gain)
        self.assertGreater(self.portfolio.peak_equity, D("69"))
        pullback = replace(self.quote, bid=D("240"), ask=D("240"))
        self.step("hold", "0", quote=pullback)
        self.assertGreater(self.portfolio.equity, self.portfolio.initial_cash)
        self.assertTrue(self.portfolio.buying_paused)

    def test_duplicate_id_is_noop_even_when_quote_changed(self):
        first = self.step(decision_id="same")
        before = self.portfolio.to_dict()
        replay = self.step(decision_id="same", quote=replace(self.quote, bid=D("90"), ask=D("90")))
        self.assertTrue(replay["duplicate"])
        self.assertEqual(first["quantity"], replay["quantity"])
        self.assertEqual(self.portfolio.to_dict(), before)

    def test_rejected_ids_are_also_idempotent(self):
        self.step(amount="100", decision_id="rejection")
        before = self.portfolio.to_dict()
        self.assertTrue(self.step(amount="100", decision_id="rejection")["duplicate"])
        self.assertEqual(before, self.portfolio.to_dict())

    def test_same_id_cannot_be_reused_for_another_decision(self):
        self.step(decision_id="same")
        with self.assertRaisesRegex(TradingError, "different decision"):
            self.step("sell", decision_id="same")
        self.assertEqual(len(self.portfolio.ledger), 1)

    def test_invalid_ids_or_metadata_raise_without_mutation(self):
        for identifier in ("", "x" * 201, None):
            with self.assertRaises(TradingError):
                self.engine.step(
                    self.portfolio,
                    Decision("hold", "BTC-EUR", D("0"), "", "test"),
                    self.quote,
                    identifier,
                    now=NOW,
                )
        with self.assertRaises(TradingError):
            self.engine.step(
                self.portfolio,
                Decision("buy", "BTC-EUR", D("10"), None, "test"),
                self.quote,
                "id",
                now=NOW,
            )
        self.assertEqual(self.portfolio.ledger, [])

    def test_first_quote_is_preserved_and_price_history_is_bounded(self):
        self.step("hold", "0")
        first = self.portfolio.first_quote.copy()
        for i in range(1, 1005):
            now = NOW + timedelta(seconds=i)
            self.engine.step(
                self.portfolio,
                Decision("hold", "BTC-EUR", D("0"), "", "test"),
                replace(self.quote, bid=D("100.1"), ask=D("100.1"), timestamp=now),
                f"tick-{i}",
                now=now,
            )
        self.assertEqual(len(self.portfolio.history), 1000)
        self.assertEqual(self.portfolio.first_quote, first)
        self.assertEqual(len(self.portfolio.ledger), 1005)

    def test_risk_configuration_and_capital_fail_closed(self):
        for args in (
            {"fee_rate": D("NaN")},
            {"fee_rate": D("0.11")},
            {"slippage_bps": D("1001")},
            {"max_loss_eur": D("0")},
            {"max_trade_eur": D("51")},
            {"max_position_eur": 25},
            {"max_quote_age_seconds": True},
            {"max_quote_age_seconds": 0},
            {"max_spread_bps": D("0")},
        ):
            with self.subTest(args=args), self.assertRaises(TradingError):
                RiskConfig(**args)
        for amount in (D("0"), D("51"), D("Infinity"), 50):
            with self.assertRaises(TradingError):
                Portfolio.create("BTC-EUR", "mock", initial_cash=amount)
        with self.assertRaises(TradingError):
            Portfolio.create("BTC-USD", "mock")
        with self.assertRaises(TradingError):
            Portfolio.create("BTC-EUR", "real")


if __name__ == "__main__":
    unittest.main()
