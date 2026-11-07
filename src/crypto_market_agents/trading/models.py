"""Exact monetary types and validated state for the isolated paper portfolio."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from decimal import Decimal, ROUND_CEILING, ROUND_FLOOR, localcontext
import re

ZERO = Decimal("0")
MAX_CAPITAL = Decimal("50")
STATE_VERSION = 1


class TradingError(ValueError):
    """Invalid input or inconsistent paper-trading state; no order should run."""


def checked_decimal(value: object, name: str, *, nonnegative: bool = True) -> Decimal:
    if not isinstance(value, Decimal) or not value.is_finite():
        raise TradingError(f"{name} must be a finite Decimal")
    if value.copy_abs() > Decimal("1e18") or value.as_tuple().exponent < -28:
        raise TradingError(f"{name} exceeds supported precision or magnitude")
    if nonnegative and value < ZERO:
        raise TradingError(f"{name} must be nonnegative")
    return value


def checked_market(market: object) -> str:
    if not isinstance(market, str) or not re.fullmatch(r"[A-Z0-9]{2,20}-EUR", market):
        raise TradingError("market must be a single EUR spot market, for example BTC-EUR")
    return market


def checked_time(value: object, name: str = "timestamp") -> datetime:
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise TradingError(f"{name} must be a timezone-aware datetime")
    return value


@dataclass(frozen=True)
class Quote:
    market: str
    bid: Decimal
    ask: Decimal
    timestamp: datetime
    min_order_eur: Decimal = Decimal("5")
    quantity_step: Decimal = Decimal("0.00000001")
    min_quantity: Decimal = ZERO


@dataclass(frozen=True)
class Decision:
    action: str
    market: str
    amount_eur: Decimal
    reason: str
    provider: str


@dataclass(frozen=True)
class RiskConfig:
    max_trade_eur: Decimal = Decimal("10")
    max_position_eur: Decimal = Decimal("25")
    max_loss_eur: Decimal = Decimal("5")
    fee_rate: Decimal = Decimal("0.0025")
    slippage_bps: Decimal = Decimal("10")
    max_quote_age_seconds: int = 120
    max_spread_bps: Decimal = Decimal("100")

    def __post_init__(self) -> None:
        for name in ("max_trade_eur", "max_position_eur", "max_loss_eur"):
            value = checked_decimal(getattr(self, name), name)
            if not ZERO < value <= MAX_CAPITAL:
                raise TradingError(f"{name} must be greater than zero and at most 50 EUR")
        spread = checked_decimal(self.max_spread_bps, "max_spread_bps")
        if not ZERO < spread <= Decimal("1000"):
            raise TradingError("max_spread_bps must be > 0 and <= 1000")
        fee = checked_decimal(self.fee_rate, "fee_rate")
        slippage = checked_decimal(self.slippage_bps, "slippage_bps")
        if fee > Decimal("0.1") or slippage > Decimal("1000"):
            raise TradingError("fee_rate must be <= 0.1 and slippage_bps <= 1000")
        if (
            type(self.max_quote_age_seconds) is not int
            or not 1 <= self.max_quote_age_seconds <= 3600
        ):
            raise TradingError("max_quote_age_seconds must be an integer from 1 to 3600")


@dataclass
class Portfolio:
    market: str
    market_source: str
    initial_cash: Decimal = Decimal("50")
    cash: Decimal = Decimal("50")
    quantity: Decimal = ZERO
    cost_basis: Decimal = ZERO
    fees_paid: Decimal = ZERO
    realized_pnl: Decimal = ZERO
    last_bid: Decimal | None = None
    peak_equity: Decimal = Decimal("50")
    max_drawdown_eur: Decimal = ZERO
    buying_paused: bool = False
    first_quote: dict | None = None
    history: list[dict] = field(default_factory=list)
    ledger: list[dict] = field(default_factory=list)
    config: RiskConfig = field(default_factory=RiskConfig)
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    version: int = STATE_VERSION

    @classmethod
    def create(
        cls,
        market: str,
        market_source: str,
        initial_cash: Decimal = Decimal("50"),
        config: RiskConfig | None = None,
    ) -> Portfolio:
        result = cls(
            market=market,
            market_source=market_source,
            initial_cash=initial_cash,
            cash=initial_cash,
            peak_equity=initial_cash,
            config=config or RiskConfig(),
        )
        result.validate()
        return result

    def to_dict(self) -> dict:
        self.validate()
        result = asdict(self)
        for name in (
            "initial_cash",
            "cash",
            "quantity",
            "cost_basis",
            "fees_paid",
            "realized_pnl",
            "peak_equity",
            "max_drawdown_eur",
        ):
            result[name] = str(result[name])
        result["last_bid"] = str(self.last_bid) if self.last_bid is not None else None
        result["created_at"] = self.created_at.isoformat()
        result["config"] = {
            key: str(value) if isinstance(value, Decimal) else value
            for key, value in result["config"].items()
        }
        return result

    @classmethod
    def from_dict(cls, raw: object) -> Portfolio:
        expected = {
            "version",
            "market",
            "market_source",
            "initial_cash",
            "cash",
            "quantity",
            "cost_basis",
            "fees_paid",
            "realized_pnl",
            "last_bid",
            "history",
            "ledger",
            "config",
            "created_at",
            "peak_equity",
            "max_drawdown_eur",
            "buying_paused",
            "first_quote",
        }
        if not isinstance(raw, dict) or set(raw) != expected:
            raise TradingError("Missing or unknown portfolio fields")
        values = raw.copy()
        for name in (
            "initial_cash",
            "cash",
            "quantity",
            "cost_basis",
            "fees_paid",
            "realized_pnl",
            "peak_equity",
            "max_drawdown_eur",
        ):
            values[name] = parse_decimal(raw[name], name, nonnegative=name != "realized_pnl")
        values["last_bid"] = (
            parse_decimal(raw["last_bid"], "last_bid") if raw["last_bid"] is not None else None
        )
        values["created_at"] = parse_time(raw["created_at"])
        cfg = raw["config"]
        if not isinstance(cfg, dict) or set(cfg) != set(RiskConfig.__dataclass_fields__):
            raise TradingError("Missing or unknown risk configuration fields")
        values["config"] = RiskConfig(
            **{
                key: value if key == "max_quote_age_seconds" else parse_decimal(value, key)
                for key, value in cfg.items()
            }
        )
        result = cls(**values)
        result.validate()
        return result

    @property
    def equity(self) -> Decimal:
        with localcontext() as context:
            context.prec = 60
            return (self.cash + self.quantity * (self.last_bid or ZERO)).quantize(
                Decimal("1e-12"), rounding=ROUND_FLOOR
            )

    @property
    def unrealized_pnl(self) -> Decimal:
        return self.equity - self.cash - self.cost_basis

    def validate(self) -> None:
        if type(self.version) is not int or self.version != STATE_VERSION:
            raise TradingError("Unsupported paper state version")
        checked_market(self.market)
        if self.market_source not in {"mock", "bitvavo"}:
            raise TradingError("market_source must be mock or bitvavo")
        checked_time(self.created_at, "created_at")
        if not isinstance(self.config, RiskConfig):
            raise TradingError("Invalid risk configuration")
        self.config.__post_init__()
        for name in ("initial_cash", "cash", "quantity", "cost_basis", "fees_paid"):
            checked_decimal(getattr(self, name), name)
        checked_decimal(self.realized_pnl, "realized_pnl", nonnegative=False)
        if not ZERO < self.initial_cash <= MAX_CAPITAL:
            raise TradingError("Initial paper capital must be greater than zero and <= 50 EUR")
        if self.last_bid is not None and checked_decimal(self.last_bid, "last_bid") <= ZERO:
            raise TradingError("last_bid must be positive")
        checked_decimal(self.peak_equity, "peak_equity")
        checked_decimal(self.max_drawdown_eur, "max_drawdown_eur")
        if self.peak_equity < self.initial_cash or self.peak_equity < self.equity:
            raise TradingError("Peak equity cannot be lower than initial or current equity")
        if self.max_drawdown_eur < self.peak_equity - self.equity:
            raise TradingError("Maximum drawdown is inconsistent")
        if type(self.buying_paused) is not bool:
            raise TradingError("buying_paused must be a boolean")
        if self.max_drawdown_eur >= self.config.max_loss_eur and not self.buying_paused:
            raise TradingError("The loss limit must latch the purchase pause")
        if self.first_quote is not None:
            expected_quote = {
                "bid",
                "ask",
                "min_order_eur",
                "min_quantity",
                "quantity_step",
                "timestamp",
            }
            if not isinstance(self.first_quote, dict) or set(self.first_quote) != expected_quote:
                raise TradingError("Invalid first quote")
            parse_time(self.first_quote["timestamp"])
            for name in expected_quote - {"timestamp"}:
                value = parse_decimal(self.first_quote[name], name)
                if name != "min_quantity" and value <= ZERO:
                    raise TradingError("First quote fields must be positive")
            if Decimal(self.first_quote["bid"]) > Decimal(self.first_quote["ask"]):
                raise TradingError("Invalid first quote spread")
        if self.quantity > ZERO and self.last_bid is None:
            raise TradingError("A held position requires a valid last bid")
        if self.quantity == ZERO and self.cost_basis != ZERO:
            raise TradingError("An empty position cannot retain cost basis")
        if abs(self.cash + self.cost_basis - self.initial_cash - self.realized_pnl) > Decimal(
            "0.000000000001"
        ):
            raise TradingError("Paper portfolio accounting does not balance")
        if not isinstance(self.history, list) or len(self.history) > 1000:
            raise TradingError("Invalid price history")
        previous_time = None
        for point in self.history:
            if not isinstance(point, dict) or set(point) != {"timestamp", "bid"}:
                raise TradingError("Invalid price history entry")
            timestamp = parse_time(point["timestamp"])
            if previous_time is not None and timestamp < previous_time:
                raise TradingError("Price history timestamps must be ordered")
            previous_time = timestamp
            if parse_decimal(point["bid"], "history bid") <= ZERO:
                raise TradingError("History bid must be positive")
        if self.history and parse_decimal(self.history[-1]["bid"], "history bid") != self.last_bid:
            raise TradingError("History and last bid disagree")
        if not isinstance(self.ledger, list):
            raise TradingError("Invalid paper ledger")
        identifiers = set()
        for event in self.ledger:
            validate_event(event)
            if event["decision_id"] in identifiers:
                raise TradingError("Duplicate decision ID in paper ledger")
            identifiers.add(event["decision_id"])
        self._validate_ledger_accounting()
        if self.ledger:
            last = self.ledger[-1]
            if (
                Decimal(last["cash_after"]) != self.cash
                or Decimal(last["quantity_after"]) != self.quantity
            ):
                raise TradingError("Ledger and balances disagree")
            if Decimal(last["equity_after"]) != self.equity:
                raise TradingError("Ledger and current equity disagree")

    def _validate_ledger_accounting(self) -> None:
        cash, quantity, basis, fees, pnl = self.initial_cash, ZERO, ZERO, ZERO, ZERO
        money_step = Decimal("1e-12")
        with localcontext() as context:
            context.prec = 60
            for event in self.ledger:
                qty = Decimal(event["quantity"])
                price = Decimal(event["execution_price"])
                notional = Decimal(event["notional_eur"])
                fee = Decimal(event["fee_eur"])
                if event["status"] == "executed":
                    if event["market"] != self.market or min(qty, price, notional) <= ZERO:
                        raise TradingError("Invalid executed order in ledger")
                    rounding = ROUND_CEILING if event["action"] == "buy" else ROUND_FLOOR
                    if (qty * price).quantize(money_step, rounding=rounding) != notional:
                        raise TradingError("Ledger notional does not match its fill")
                    if (notional * self.config.fee_rate).quantize(
                        money_step, rounding=ROUND_CEILING
                    ) != fee:
                        raise TradingError("Ledger fee does not match risk configuration")
                    if event["action"] == "buy":
                        cash -= notional + fee
                        quantity += qty
                        basis += notional + fee
                    else:
                        if qty > quantity:
                            raise TradingError("Ledger sells more than available holdings")
                        removed = (
                            basis
                            if qty == quantity
                            else (basis * qty / quantity).quantize(money_step, rounding=ROUND_FLOOR)
                        )
                        cash += notional - fee
                        quantity -= qty
                        basis -= removed
                        pnl += notional - fee - removed
                    fees += fee
                elif any(value != ZERO for value in (qty, price, notional, fee)):
                    raise TradingError("Unexecuted ledger event has a fill")
                if (
                    cash < ZERO
                    or Decimal(event["cash_after"]) != cash
                    or Decimal(event["quantity_after"]) != quantity
                ):
                    raise TradingError("Ledger running balances do not agree")
            if (cash, quantity, basis, fees, pnl) != (
                self.cash,
                self.quantity,
                self.cost_basis,
                self.fees_paid,
                self.realized_pnl,
            ):
                raise TradingError("Portfolio balances do not match the replayed ledger")


def parse_decimal(value: object, name: str, *, nonnegative: bool = True) -> Decimal:
    if not isinstance(value, str) or len(value) > 80:
        raise TradingError(f"{name} must be an exact decimal string")
    try:
        decimal = Decimal(value)
    except ArithmeticError as exc:
        raise TradingError(f"Invalid decimal for {name}") from exc
    return checked_decimal(decimal, name, nonnegative=nonnegative)


def parse_time(value: object) -> datetime:
    if not isinstance(value, str):
        raise TradingError("Timestamp must be an ISO 8601 string")
    try:
        return checked_time(datetime.fromisoformat(value))
    except ValueError as exc:
        raise TradingError("Invalid timestamp") from exc


EVENT_KEYS = {
    "decision_id",
    "timestamp",
    "status",
    "action",
    "market",
    "provider",
    "reason",
    "requested_amount_eur",
    "decision_reason",
    "execution_price",
    "quantity",
    "notional_eur",
    "fee_eur",
    "cash_after",
    "quantity_after",
    "equity_after",
}


def validate_event(event: object) -> None:
    if not isinstance(event, dict) or set(event) != EVENT_KEYS:
        raise TradingError("Invalid ledger event fields")
    for key in ("decision_id", "action", "market", "provider", "reason", "decision_reason"):
        if not isinstance(event[key], str) or len(event[key]) > 4000:
            raise TradingError(f"Invalid ledger {key}")
    if not event["decision_id"] or len(event["decision_id"]) > 200:
        raise TradingError("Invalid decision ID")
    if event["status"] not in {"executed", "held", "rejected"}:
        raise TradingError("Invalid event status")
    if event["status"] == "executed" and event["action"] not in {"buy", "sell"}:
        raise TradingError("Invalid executed action")
    parse_time(event["timestamp"])
    for key in (
        "execution_price",
        "quantity",
        "notional_eur",
        "fee_eur",
        "cash_after",
        "quantity_after",
        "equity_after",
    ):
        parse_decimal(event[key], key)
    # Rejections may contain invalid request values, retained solely as untrusted audit text.
    if (
        not isinstance(event["requested_amount_eur"], str)
        or len(event["requested_amount_eur"]) > 80
    ):
        raise TradingError("Invalid request amount audit field")
