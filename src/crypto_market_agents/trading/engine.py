"""Deterministic risk checks and fee-aware simulated spot fills. Never sends orders."""

from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal, ROUND_CEILING, ROUND_FLOOR, localcontext

from .models import (
    ZERO,
    Decision,
    Portfolio,
    Quote,
    TradingError,
    checked_decimal,
    checked_market,
    checked_time,
)

MONEY_STEP = Decimal("0.000000000001")


def _money(value: Decimal, rounding: str) -> Decimal:
    return value.quantize(MONEY_STEP, rounding=rounding)


def _lot(value: Decimal, step: Decimal) -> Decimal:
    return (value / step).to_integral_value(rounding=ROUND_FLOOR) * step


class PaperEngine:
    """Mutates a validated portfolio; use inside PaperStore.transaction for persistence."""

    def step(
        self,
        portfolio: Portfolio,
        decision: Decision,
        quote: Quote,
        decision_id: str,
        *,
        now: datetime | None = None,
    ) -> dict:
        portfolio.validate()
        timestamp = checked_time(now if now is not None else datetime.now(timezone.utc), "now")
        if not isinstance(decision_id, str) or not decision_id or len(decision_id) > 200:
            raise TradingError("decision_id must be a nonempty string of at most 200 characters")
        if not isinstance(decision, Decision):
            raise TradingError("Expected a Decision")
        for name in ("action", "market", "reason", "provider"):
            value = getattr(decision, name)
            if not isinstance(value, str) or len(value) > 4000:
                raise TradingError(f"Invalid decision {name}")
        requested = str(decision.amount_eur)
        if len(requested) > 80:
            raise TradingError("Request amount is too long")
        for event in portfolio.ledger:
            if event["decision_id"] == decision_id:
                if any(
                    event[key] != value
                    for key, value in (
                        ("action", decision.action),
                        ("market", decision.market),
                        ("provider", decision.provider),
                        ("decision_reason", decision.reason),
                        ("requested_amount_eur", requested),
                    )
                ):
                    raise TradingError("decision_id already belongs to a different decision")
                return {**event, "duplicate": True}
        event = {
            "decision_id": decision_id,
            "timestamp": timestamp.isoformat(),
            "status": "rejected",
            "action": decision.action,
            "market": decision.market,
            "provider": decision.provider,
            "reason": "",
            "requested_amount_eur": requested,
            "decision_reason": decision.reason,
            "execution_price": "0",
            "quantity": "0",
            "notional_eur": "0",
            "fee_eur": "0",
            "cash_after": str(portfolio.cash),
            "quantity_after": str(portfolio.quantity),
            "equity_after": str(portfolio.equity),
        }
        with localcontext() as context:
            context.prec = 60
            try:
                self._validate_quote(portfolio, quote, timestamp)
                portfolio.last_bid = quote.bid
                if portfolio.first_quote is None:
                    portfolio.first_quote = {
                        name: str(getattr(quote, name))
                        for name in ("bid", "ask", "min_order_eur", "min_quantity", "quantity_step")
                    }
                    portfolio.first_quote["timestamp"] = quote.timestamp.isoformat()
                self._mark_risk(portfolio)
                point = {"timestamp": quote.timestamp.isoformat(), "bid": str(quote.bid)}
                if not portfolio.history or portfolio.history[-1] != point:
                    portfolio.history.append(point)
                    portfolio.history = portfolio.history[-1000:]
                self._validate_decision(portfolio, decision)
                if decision.action == "hold":
                    event.update(
                        status="held", reason=decision.reason or "No transaction requested"
                    )
                else:
                    self._fill(portfolio, decision, quote, event)
            except TradingError as exc:
                event["reason"] = str(exc)
            self._mark_risk(portfolio)
            event.update(
                cash_after=str(portfolio.cash),
                quantity_after=str(portfolio.quantity),
                equity_after=str(portfolio.equity),
            )
            portfolio.ledger.append(event)
            portfolio.validate()
        return {**event, "duplicate": False}

    @staticmethod
    def _mark_risk(portfolio: Portfolio) -> None:
        portfolio.peak_equity = max(portfolio.peak_equity, portfolio.equity)
        portfolio.max_drawdown_eur = max(
            portfolio.max_drawdown_eur, portfolio.peak_equity - portfolio.equity
        )
        if portfolio.max_drawdown_eur >= portfolio.config.max_loss_eur:
            portfolio.buying_paused = True

    @staticmethod
    def _validate_quote(portfolio: Portfolio, quote: Quote, now: datetime) -> None:
        if not isinstance(quote, Quote):
            raise TradingError("Expected a Quote")
        checked_market(quote.market)
        if quote.market != portfolio.market:
            raise TradingError("Quote market differs from the portfolio market")
        for name in ("bid", "ask", "min_order_eur", "quantity_step", "min_quantity"):
            checked_decimal(getattr(quote, name), name)
        if quote.bid <= ZERO or quote.ask < quote.bid:
            raise TradingError("Quote requires 0 < bid <= ask")
        if quote.quantity_step <= ZERO or quote.min_order_eur <= ZERO:
            raise TradingError("Quote lot size and minimum order must be positive")
        if quote.quantity_step < Decimal("1e-18"):
            raise TradingError("Quote quantity step has unsupported precision")
        checked_time(quote.timestamp)
        age = (now - quote.timestamp).total_seconds()
        if age < 0 or age > portfolio.config.max_quote_age_seconds:
            raise TradingError("Quote is stale or from the future")
        if portfolio.history:
            previous = datetime.fromisoformat(portfolio.history[-1]["timestamp"])
            if quote.timestamp < previous:
                raise TradingError("Quote is older than the most recent observed quote")
        spread_bps = (quote.ask - quote.bid) / quote.bid * Decimal("10000")
        if spread_bps > portfolio.config.max_spread_bps:
            raise TradingError("Quote spread exceeds the configured limit")

    @staticmethod
    def _validate_decision(portfolio: Portfolio, decision: Decision) -> None:
        checked_market(decision.market)
        if decision.market != portfolio.market:
            raise TradingError("Decision market differs from the portfolio market")
        if decision.action not in {"buy", "sell", "hold"}:
            raise TradingError("Decision action must be buy, sell or hold")
        checked_decimal(decision.amount_eur, "amount_eur")
        if decision.action == "hold":
            if decision.amount_eur != ZERO:
                raise TradingError("A hold decision must request zero EUR")
        elif decision.amount_eur <= ZERO:
            raise TradingError("Trade amount must be greater than zero")
        elif decision.amount_eur > portfolio.config.max_trade_eur:
            raise TradingError("Trade exceeds max_trade_eur")

    @staticmethod
    def _fill(portfolio: Portfolio, decision: Decision, quote: Quote, event: dict) -> None:
        cfg = portfolio.config
        slip = cfg.slippage_bps / Decimal("10000")
        if decision.action == "buy":
            if portfolio.buying_paused:
                raise TradingError("Loss limit reached: new purchases are blocked")
            if decision.amount_eur > portfolio.cash:
                raise TradingError("Insufficient cash for the requested purchase")
            price = _money(quote.ask * (1 + slip), ROUND_CEILING)
            quantity = _lot(decision.amount_eur / (price * (1 + cfg.fee_rate)), quote.quantity_step)
            notional = _money(quantity * price, ROUND_CEILING)
            fee = _money(notional * cfg.fee_rate, ROUND_CEILING)
            # Upward monetary rounding must never spend more than the requested total budget.
            if notional + fee > decision.amount_eur:
                excess = notional + fee - decision.amount_eur
                lots = (
                    (excess + MONEY_STEP * 2) / (price * (1 + cfg.fee_rate)) / quote.quantity_step
                ).to_integral_value(rounding=ROUND_CEILING)
                quantity = max(ZERO, quantity - lots * quote.quantity_step)
                notional = _money(quantity * price, ROUND_CEILING)
                fee = _money(notional * cfg.fee_rate, ROUND_CEILING)
            PaperEngine._validate_size(quantity, quote, notional)
            if (portfolio.quantity + quantity) * quote.ask > cfg.max_position_eur:
                raise TradingError("Purchase exceeds max_position_eur")
            total = notional + fee
            if total > portfolio.cash or total > decision.amount_eur:
                raise TradingError("Rounded purchase exceeds available cash or budget")
            portfolio.cash -= total
            portfolio.quantity += quantity
            portfolio.cost_basis += total
        else:
            if portfolio.quantity <= ZERO:
                raise TradingError("No position to sell; short selling is disabled")
            price = _money(quote.bid * (1 - slip), ROUND_FLOOR)
            quantity = _lot(
                min(portfolio.quantity, decision.amount_eur / quote.bid), quote.quantity_step
            )
            notional = _money(quantity * price, ROUND_FLOOR)
            fee = _money(notional * cfg.fee_rate, ROUND_CEILING)
            PaperEngine._validate_size(quantity, quote, notional)
            proceeds = notional - fee
            if proceeds < ZERO or price <= ZERO:
                raise TradingError("Rounded sale proceeds must be nonnegative")
            if quantity == portfolio.quantity:
                removed_basis = portfolio.cost_basis
            else:
                removed_basis = _money(
                    portfolio.cost_basis * quantity / portfolio.quantity, ROUND_FLOOR
                )
            portfolio.cash += proceeds
            portfolio.quantity -= quantity
            portfolio.cost_basis -= removed_basis
            portfolio.realized_pnl += proceeds - removed_basis
        portfolio.fees_paid += fee
        event.update(
            status="executed",
            reason=decision.reason or "Paper order accepted by risk controls",
            execution_price=str(price),
            quantity=str(quantity),
            notional_eur=str(notional),
            fee_eur=str(fee),
        )

    @staticmethod
    def _validate_size(quantity: Decimal, quote: Quote, notional: Decimal) -> None:
        if quantity <= ZERO or quantity < quote.min_quantity:
            raise TradingError("Rounded quantity is below the market minimum")
        if notional < quote.min_order_eur:
            raise TradingError("Order notional after lot rounding is below the market minimum")
