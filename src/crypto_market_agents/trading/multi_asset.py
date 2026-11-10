"""Versioned, offline multi-asset paper portfolio with a shared EUR cash balance."""

from __future__ import annotations

from contextlib import contextmanager
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation, ROUND_CEILING, ROUND_FLOOR, localcontext
import fcntl
import json
import os
from pathlib import Path
import re
import tempfile
from typing import Iterator

from .engine import MONEY_STEP, _lot, _money
from .models import Quote, TradingError, checked_decimal, checked_market, checked_time

FORMAT_NAME = "paper-multiasset"
LEGACY_FORMAT_VERSION = 1
FORMAT_VERSION = 2
RISK_POLICY_VERSION = 1
CAPITAL_EUR = Decimal("150")
ZERO = Decimal("0")
MAX_STATE_BYTES = 20 * 1024 * 1024


def _decimal(value: object, name: str, *, nonnegative: bool = True) -> Decimal:
    if not isinstance(value, str) or len(value) > 80:
        raise TradingError(f"{name} must be an exact decimal string")
    try:
        result = Decimal(value)
    except (InvalidOperation, ValueError) as exc:
        raise TradingError(f"Invalid decimal for {name}") from exc
    return checked_decimal(result, name, nonnegative=nonnegative)


def _timestamp(value: object, name: str = "timestamp") -> datetime:
    if not isinstance(value, str):
        raise TradingError(f"{name} must be an ISO timestamp")
    try:
        result = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise TradingError(f"Invalid {name}") from exc
    return checked_time(result, name)


def _platform(value: object) -> str:
    if not isinstance(value, str) or not re.fullmatch(r"[a-z0-9][a-z0-9._-]{0,39}", value):
        raise TradingError("platform must be a lowercase identifier")
    return value


def position_key(platform: str, market: str) -> str:
    return f"{_platform(platform)}|{checked_market(market)}"


@dataclass(frozen=True)
class MultiAssetRiskConfig:
    max_trade_eur: Decimal = Decimal("75")
    max_total_exposure_eur: Decimal = Decimal("135")
    max_position_eur: Decimal = Decimal("75")
    fee_rate: Decimal = Decimal("0.0025")
    slippage_bps: Decimal = Decimal("10")
    max_quote_age_seconds: int = 120
    max_spread_bps: Decimal = Decimal("100")
    version: int = RISK_POLICY_VERSION

    def validate(self) -> None:
        if type(self.version) is not int or self.version != RISK_POLICY_VERSION:
            raise TradingError("Unsupported multi-asset risk policy version")
        for name in ("max_trade_eur", "max_total_exposure_eur", "max_position_eur"):
            value = checked_decimal(getattr(self, name), name)
            if not ZERO < value <= CAPITAL_EUR:
                raise TradingError(f"{name} must be greater than zero and at most 150 EUR")
        if self.max_position_eur > self.max_total_exposure_eur:
            raise TradingError("Per-position limit cannot exceed global exposure limit")
        fee = checked_decimal(self.fee_rate, "fee_rate")
        slip = checked_decimal(self.slippage_bps, "slippage_bps")
        spread = checked_decimal(self.max_spread_bps, "max_spread_bps")
        if fee > Decimal("0.1") or slip > Decimal("1000"):
            raise TradingError("fee_rate or slippage_bps exceeds its supported bound")
        if not ZERO < spread <= Decimal("1000"):
            raise TradingError("max_spread_bps must be greater than zero and at most 1000")
        if (
            type(self.max_quote_age_seconds) is not int
            or not 1 <= self.max_quote_age_seconds <= 3600
        ):
            raise TradingError("max_quote_age_seconds must be an integer from 1 to 3600")

    def to_dict(self) -> dict:
        return {
            key: str(value) if isinstance(value, Decimal) else value
            for key, value in asdict(self).items()
        }

    @classmethod
    def from_dict(cls, raw: object) -> MultiAssetRiskConfig:
        expected = set(cls.__dataclass_fields__)
        if not isinstance(raw, dict) or set(raw) != expected:
            raise TradingError("Missing or unknown multi-asset risk configuration fields")
        values = {
            key: raw[key]
            if key in {"version", "max_quote_age_seconds"}
            else _decimal(raw[key], key)
            for key in expected
        }
        result = cls(**values)
        result.validate()
        return result


@dataclass
class MultiAssetPosition:
    platform: str
    market: str
    quantity: Decimal = ZERO
    cost_basis_eur: Decimal = ZERO
    realized_pnl_eur: Decimal = ZERO
    fees_paid_eur: Decimal = ZERO
    last_quote: dict | None = None

    @property
    def key(self) -> str:
        return position_key(self.platform, self.market)

    def to_dict(self) -> dict:
        return {
            "platform": self.platform,
            "market": self.market,
            "quantity": str(self.quantity),
            "cost_basis_eur": str(self.cost_basis_eur),
            "realized_pnl_eur": str(self.realized_pnl_eur),
            "fees_paid_eur": str(self.fees_paid_eur),
            "last_quote": self.last_quote,
        }

    @classmethod
    def from_dict(cls, raw: object) -> MultiAssetPosition:
        keys = {
            "platform",
            "market",
            "quantity",
            "cost_basis_eur",
            "realized_pnl_eur",
            "fees_paid_eur",
            "last_quote",
        }
        if not isinstance(raw, dict) or set(raw) != keys:
            raise TradingError("Invalid multi-asset position fields")
        result = cls(
            _platform(raw["platform"]),
            checked_market(raw["market"]),
            _decimal(raw["quantity"], "quantity"),
            _decimal(raw["cost_basis_eur"], "cost_basis_eur"),
            _decimal(raw["realized_pnl_eur"], "realized_pnl_eur", nonnegative=False),
            _decimal(raw["fees_paid_eur"], "fees_paid_eur"),
            raw["last_quote"],
        )
        if (result.quantity == ZERO) != (result.cost_basis_eur == ZERO):
            raise TradingError("Position quantity and cost basis disagree")
        if result.last_quote is not None:
            _validate_quote_snapshot(result.last_quote)
        return result


@dataclass
class MultiAssetPortfolio:
    cash_eur: Decimal = CAPITAL_EUR
    fees_paid_eur: Decimal = ZERO
    realized_pnl_eur: Decimal = ZERO
    positions: dict[str, MultiAssetPosition] = field(default_factory=dict)
    ledger: list[dict] = field(default_factory=list)
    config: MultiAssetRiskConfig = field(default_factory=MultiAssetRiskConfig)
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    format_version: int = FORMAT_VERSION
    initial_capital_eur: Decimal = CAPITAL_EUR
    cycle_receipts: list[dict] = field(default_factory=list)

    @classmethod
    def create(cls, config: MultiAssetRiskConfig | None = None) -> MultiAssetPortfolio:
        result = cls(config=config or MultiAssetRiskConfig())
        result.validate()
        return result

    def to_dict(self) -> dict:
        self.validate()
        result = {
            "format": FORMAT_NAME,
            "format_version": self.format_version,
            "initial_capital_eur": str(self.initial_capital_eur),
            "cash_eur": str(self.cash_eur),
            "fees_paid_eur": str(self.fees_paid_eur),
            "realized_pnl_eur": str(self.realized_pnl_eur),
            "positions": {key: pos.to_dict() for key, pos in sorted(self.positions.items())},
            "ledger": self.ledger,
            "risk_policy": self.config.to_dict(),
            "created_at": self.created_at.isoformat(),
        }
        if self.format_version >= 2:
            result["cycle_receipts"] = self.cycle_receipts
        return result

    @classmethod
    def from_dict(cls, raw: object) -> MultiAssetPortfolio:
        base_keys = {
            "format",
            "format_version",
            "initial_capital_eur",
            "cash_eur",
            "fees_paid_eur",
            "realized_pnl_eur",
            "positions",
            "ledger",
            "risk_policy",
            "created_at",
        }
        if not isinstance(raw, dict) or raw.get("format") != FORMAT_NAME:
            raise TradingError("Not a multi-asset paper state; no migration is performed")
        version = raw.get("format_version")
        if type(version) is not int:
            raise TradingError("Invalid multi-asset portfolio format version")
        keys = base_keys | ({"cycle_receipts"} if version == 2 else set())
        if set(raw) != keys or version not in {LEGACY_FORMAT_VERSION, FORMAT_VERSION}:
            raise TradingError("Not a supported multi-asset paper state; no migration is performed")
        positions_raw = raw["positions"]
        if not isinstance(positions_raw, dict):
            raise TradingError("Invalid multi-asset positions map")
        positions = {
            key: MultiAssetPosition.from_dict(value) for key, value in positions_raw.items()
        }
        if any(key != pos.key for key, pos in positions.items()) or len(positions) != len(
            set(positions)
        ):
            raise TradingError("Multi-asset position identity mismatch")
        ledger = raw["ledger"]
        if not isinstance(ledger, list) or len(ledger) > 100_000:
            raise TradingError("Invalid multi-asset ledger")
        result = cls(
            cash_eur=_decimal(raw["cash_eur"], "cash_eur"),
            fees_paid_eur=_decimal(raw["fees_paid_eur"], "fees_paid_eur"),
            realized_pnl_eur=_decimal(
                raw["realized_pnl_eur"], "realized_pnl_eur", nonnegative=False
            ),
            positions=positions,
            ledger=ledger,
            config=MultiAssetRiskConfig.from_dict(raw["risk_policy"]),
            created_at=_timestamp(raw["created_at"], "created_at"),
            format_version=raw["format_version"],
            initial_capital_eur=_decimal(raw["initial_capital_eur"], "initial_capital_eur"),
            cycle_receipts=raw.get("cycle_receipts", []),
        )
        result.validate()
        return result

    @property
    def invested_cost_basis_eur(self) -> Decimal:
        return sum((p.cost_basis_eur for p in self.positions.values()), ZERO)

    def validate(self) -> None:
        if type(self.format_version) is not int or self.format_version not in {
            LEGACY_FORMAT_VERSION,
            FORMAT_VERSION,
        }:
            raise TradingError("Unsupported multi-asset portfolio format version")
        if self.initial_capital_eur != CAPITAL_EUR:
            raise TradingError("The shared initial capital is fixed at 150 EUR")
        checked_decimal(self.cash_eur, "cash_eur")
        checked_decimal(self.fees_paid_eur, "fees_paid_eur")
        checked_decimal(self.realized_pnl_eur, "realized_pnl_eur", nonnegative=False)
        checked_time(self.created_at, "created_at")
        self.config.validate()
        if (
            self.cash_eur + self.invested_cost_basis_eur - self.initial_capital_eur
            != self.realized_pnl_eur
        ):
            if (
                abs(
                    self.cash_eur
                    + self.invested_cost_basis_eur
                    - self.initial_capital_eur
                    - self.realized_pnl_eur
                )
                > MONEY_STEP
            ):
                raise TradingError("Multi-asset cash, basis and realized result do not balance")
        if self.invested_cost_basis_eur > self.config.max_total_exposure_eur:
            raise TradingError("Current investment exceeds the configured global exposure limit")
        ids = set()
        for key, position in self.positions.items():
            if key != position.key or key in ids:
                raise TradingError("Duplicate or invalid multi-asset position identity")
            ids.add(key)
            checked_decimal(position.quantity, "position quantity")
            checked_decimal(position.cost_basis_eur, "position cost basis")
            checked_decimal(
                position.realized_pnl_eur, "position realized result", nonnegative=False
            )
            checked_decimal(position.fees_paid_eur, "position fees")
            if position.quantity == ZERO and position.cost_basis_eur != ZERO:
                raise TradingError("Closed position cannot keep a cost basis")
            if position.cost_basis_eur > self.config.max_position_eur:
                raise TradingError("Position exceeds its configured exposure limit")
            if position.last_quote is not None:
                _validate_quote_snapshot(position.last_quote)
        _validate_multi_ledger(self)
        if self.format_version == 2:
            _validate_cycle_receipts(self.cycle_receipts, self.config.version)
        elif self.cycle_receipts:
            raise TradingError("Legacy portfolio cannot contain proposal execution receipts")

    def valuation(
        self,
        quotes: dict[str, Quote] | None = None,
        *,
        quote_observations: dict[str, dict] | None = None,
        now: datetime | None = None,
    ) -> dict:
        timestamp = checked_time(now or datetime.now(timezone.utc), "valuation time")
        supplied, observations = quotes or {}, quote_observations or {}
        results, known_value = [], ZERO
        complete = True
        for key, position in sorted(self.positions.items()):
            observation = observations.get(key)
            if position.quantity == ZERO:
                results.append(
                    self._valuation_row(key, position, None, "closed", None, observation)
                )
                continue
            quote = supplied.get(key)
            if quote is None and observation is not None:
                if observation.get("status") != "available":
                    complete = False
                    results.append(
                        self._valuation_row(
                            key,
                            position,
                            None,
                            "quote_refresh_unavailable",
                            observation.get("error") or "latest public refresh failed",
                            observation,
                        )
                    )
                    continue
                snapshot = observation.get("last_quote")
                if isinstance(snapshot, dict):
                    quote = _quote_from_snapshot(position.market, snapshot)
            if quote is None and position.last_quote is not None:
                quote = _quote_from_snapshot(position.market, position.last_quote)
            reason = _quote_reason(
                quote, position.platform, position.market, timestamp, self.config
            )
            if reason:
                complete = False
                results.append(
                    self._valuation_row(key, position, None, reason, reason, observation, quote)
                )
                continue
            value = _money(position.quantity * quote.bid, ROUND_FLOOR)
            known_value += value
            results.append(
                self._valuation_row(key, position, value, "valued", None, observation, quote)
            )
        equity = self.cash_eur + known_value if complete else None
        unrealized = known_value - self.invested_cost_basis_eur if complete else None
        return {
            "as_of": timestamp.isoformat(),
            "complete": complete,
            "cash_eur": str(self.cash_eur),
            "budget_engaged_eur": str(self.invested_cost_basis_eur),
            "known_position_value_eur": str(known_value),
            "current_market_exposure_eur": str(known_value) if complete else None,
            "total_value_eur": str(equity) if equity is not None else None,
            "realized_pnl_eur": str(self.realized_pnl_eur),
            "unrealized_pnl_eur": str(unrealized) if unrealized is not None else None,
            "total_pnl_eur": str(self.realized_pnl_eur + unrealized)
            if unrealized is not None
            else None,
            "fees_paid_eur": str(self.fees_paid_eur),
            "positions": results,
        }

    @staticmethod
    def _valuation_row(key, position, market_value, status, reason, observation=None, quote=None):
        snapshot = observation.get("last_quote") if isinstance(observation, dict) else None
        if snapshot is None:
            snapshot = position.last_quote
        if quote is not None:
            snapshot = _quote_snapshot(quote)
        return {
            "key": key,
            "quantity": str(position.quantity),
            "cost_basis_eur": str(position.cost_basis_eur),
            "market_value_eur": str(market_value) if market_value is not None else None,
            "unrealized_pnl_eur": (
                str(market_value - position.cost_basis_eur)
                if market_value is not None
                else ("0" if position.quantity == ZERO else None)
            ),
            "realized_pnl_eur": str(position.realized_pnl_eur),
            "status": status,
            "reason": reason,
            "quote_timestamp": snapshot.get("timestamp") if isinstance(snapshot, dict) else None,
            "quote_source": observation.get("source")
            if isinstance(observation, dict)
            else ("trade_input" if snapshot else None),
            "last_known_bid_eur": snapshot.get("bid") if isinstance(snapshot, dict) else None,
            "quote_error": observation.get("error") if isinstance(observation, dict) else None,
        }


@dataclass(frozen=True)
class MultiAssetOrder:
    action: str
    platform: str
    market: str
    reason: str
    provider: str = "manual"
    amount_eur: Decimal | None = None
    quantity: Decimal | None = None


class MultiAssetPaperEngine:
    """Deterministic simulation. It has no exchange client and cannot send orders."""

    def execute(
        self,
        portfolio: MultiAssetPortfolio,
        order: MultiAssetOrder,
        quote: Quote,
        decision_id: str,
        *,
        quote_observations: dict[str, dict] | None = None,
        now: datetime | None = None,
    ) -> dict:
        portfolio.validate()
        timestamp = checked_time(now or datetime.now(timezone.utc), "now")
        if not isinstance(order, MultiAssetOrder):
            raise TradingError("Expected MultiAssetOrder")
        platform, market = _platform(order.platform), checked_market(order.market)
        if not isinstance(decision_id, str) or not decision_id or len(decision_id) > 200:
            raise TradingError("decision_id must be 1–200 characters")
        for text_name in ("action", "reason", "provider"):
            value = getattr(order, text_name)
            if not isinstance(value, str) or len(value) > 1000:
                raise TradingError(f"Invalid order {text_name}")
        requested_amount = str(order.amount_eur) if order.amount_eur is not None else None
        requested_quantity = str(order.quantity) if order.quantity is not None else None
        for event in portfolio.ledger:
            if event["decision_id"] == decision_id:
                identity = (
                    event["action"],
                    event["platform"],
                    event["market"],
                    event["requested_amount_eur"],
                    event["requested_quantity"],
                    event["reason"],
                    event["provider"],
                )
                current = (
                    order.action,
                    platform,
                    market,
                    requested_amount,
                    requested_quantity,
                    order.reason,
                    order.provider,
                )
                if identity != current:
                    raise TradingError("decision_id already belongs to a different simulated order")
                return {**event, "duplicate": True}
        key = position_key(platform, market)
        position = portfolio.positions.get(key)
        is_new_position = position is None
        if position is None:
            position = MultiAssetPosition(platform, market)
        event = {
            "decision_id": decision_id,
            "timestamp": timestamp.isoformat(),
            "status": "rejected",
            "action": order.action,
            "platform": platform,
            "market": market,
            "provider": order.provider,
            "reason": "",
            "requested_amount_eur": requested_amount,
            "requested_quantity": requested_quantity,
            "quote_snapshot": None,
            "execution_price": "0",
            "quantity": "0",
            "notional_eur": "0",
            "fee_eur": "0",
            "cash_after": str(portfolio.cash_eur),
            "position_quantity_after": str(position.quantity if position else ZERO),
            "position_basis_after_eur": str(position.cost_basis_eur if position else ZERO),
            "realized_pnl_after_eur": str(portfolio.realized_pnl_eur),
        }
        try:
            _validate_order_shape(order)
            reason = _quote_reason(quote, platform, market, timestamp, portfolio.config)
            if reason:
                raise TradingError(reason)
            event["quote_snapshot"] = _quote_snapshot(quote)
            with localcontext() as context:
                context.prec = 60
                if order.action == "buy":
                    mark = portfolio.valuation(
                        {key: quote}, quote_observations=quote_observations, now=timestamp
                    )
                    if not mark["complete"]:
                        raise TradingError(
                            "Current global market exposure cannot be verified; refresh quotes first"
                        )
                    current_exposure = Decimal(mark["current_market_exposure_eur"])
                    current_position_value = next(
                        (
                            Decimal(item["market_value_eur"])
                            for item in mark["positions"]
                            if item["key"] == key and item["market_value_eur"] is not None
                        ),
                        ZERO,
                    )
                    self._buy(
                        portfolio,
                        position,
                        order,
                        quote,
                        event,
                        current_exposure=current_exposure,
                        current_position_value=current_position_value,
                    )
                elif order.action == "sell":
                    self._sell(portfolio, position, order, quote, event)
                else:
                    raise TradingError("action must be buy or sell")
                position.last_quote = _quote_snapshot(quote)
                if is_new_position:
                    portfolio.positions[key] = position
        except TradingError as exc:
            event["reason"] = str(exc)
        event.update(
            cash_after=str(portfolio.cash_eur),
            position_quantity_after=str(position.quantity if position else ZERO),
            position_basis_after_eur=str(position.cost_basis_eur if position else ZERO),
            realized_pnl_after_eur=str(portfolio.realized_pnl_eur),
        )
        portfolio.ledger.append(event)
        portfolio.validate()
        return {**event, "duplicate": False}

    @staticmethod
    def _buy(
        portfolio: MultiAssetPortfolio,
        position: MultiAssetPosition,
        order: MultiAssetOrder,
        quote: Quote,
        event: dict,
        *,
        current_exposure: Decimal,
        current_position_value: Decimal,
    ) -> None:
        cfg = portfolio.config
        amount = checked_decimal(order.amount_eur, "amount_eur")
        if amount <= ZERO or amount > cfg.max_trade_eur:
            raise TradingError("Buy budget must be positive and within max_trade_eur")
        if amount > portfolio.cash_eur:
            raise TradingError("Insufficient shared cash")
        price = _money(
            quote.ask * (Decimal("1") + cfg.slippage_bps / Decimal("10000")), ROUND_CEILING
        )
        quantity = _lot(amount / (price * (Decimal("1") + cfg.fee_rate)), quote.quantity_step)
        notional = _money(quantity * price, ROUND_CEILING)
        fee = _money(notional * cfg.fee_rate, ROUND_CEILING)
        if quantity <= ZERO or notional < quote.min_order_eur or quantity < quote.min_quantity:
            raise TradingError("Buy is below the market minimum")
        total = notional + fee
        if total > amount or total > portfolio.cash_eur:
            raise TradingError("Buy exceeds its requested budget or shared cash")
        if position.cost_basis_eur + total > cfg.max_position_eur:
            raise TradingError("Buy exceeds max_position_eur")
        if portfolio.invested_cost_basis_eur + total > cfg.max_total_exposure_eur:
            raise TradingError("Buy exceeds max_total_exposure_eur")
        buy_market_value = _money(quantity * quote.bid, ROUND_FLOOR)
        if current_position_value + buy_market_value > cfg.max_position_eur:
            raise TradingError("Buy exceeds max_position_eur at current market value")
        if current_exposure + buy_market_value > cfg.max_total_exposure_eur:
            raise TradingError("Buy exceeds max_total_exposure_eur at current market value")
        portfolio.cash_eur -= total
        position.quantity += quantity
        position.cost_basis_eur += total
        position.fees_paid_eur += fee
        portfolio.fees_paid_eur += fee
        event.update(
            status="executed",
            reason=order.reason or "Simulated buy accepted",
            execution_price=str(price),
            quantity=str(quantity),
            notional_eur=str(notional),
            fee_eur=str(fee),
        )

    @staticmethod
    def _sell(
        portfolio: MultiAssetPortfolio,
        position: MultiAssetPosition,
        order: MultiAssetOrder,
        quote: Quote,
        event: dict,
    ) -> None:
        cfg = portfolio.config
        requested = checked_decimal(order.quantity, "quantity")
        if requested <= ZERO or requested > position.quantity:
            raise TradingError(
                "Sell quantity must be positive and no greater than the held position"
            )
        quantity = _lot(requested, quote.quantity_step)
        if quantity <= ZERO or quantity < quote.min_quantity:
            raise TradingError("Sell quantity is below the market minimum")
        price = _money(
            quote.bid * (Decimal("1") - cfg.slippage_bps / Decimal("10000")), ROUND_FLOOR
        )
        notional = _money(quantity * price, ROUND_FLOOR)
        fee = _money(notional * cfg.fee_rate, ROUND_CEILING)
        if notional > cfg.max_trade_eur:
            raise TradingError("Sell exceeds max_trade_eur")
        if price <= ZERO or notional < quote.min_order_eur or notional < fee:
            raise TradingError("Sell proceeds are below the market minimum")
        removed_basis = (
            position.cost_basis_eur
            if quantity == position.quantity
            else _money(position.cost_basis_eur * quantity / position.quantity, ROUND_FLOOR)
        )
        proceeds = notional - fee
        position.quantity -= quantity
        position.cost_basis_eur -= removed_basis
        position.realized_pnl_eur += proceeds - removed_basis
        position.fees_paid_eur += fee
        portfolio.cash_eur += proceeds
        portfolio.realized_pnl_eur += proceeds - removed_basis
        portfolio.fees_paid_eur += fee
        event.update(
            status="executed",
            reason=order.reason or "Simulated sell accepted",
            execution_price=str(price),
            quantity=str(quantity),
            notional_eur=str(notional),
            fee_eur=str(fee),
        )


class MultiAssetPaperStore:
    """Locked, atomic JSON persistence; refuses old/symlink/corrupt states."""

    def __init__(self, path: str | Path):
        self.path = Path(path).expanduser().absolute()
        self.lock_path = self.path.with_name(self.path.name + ".lock")

    @contextmanager
    def _lock(self) -> Iterator[None]:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        descriptor = os.open(self.lock_path, os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
        try:
            fcntl.flock(descriptor, fcntl.LOCK_EX)
            yield
        finally:
            fcntl.flock(descriptor, fcntl.LOCK_UN)
            os.close(descriptor)

    def initialize(self, config: MultiAssetRiskConfig | None = None) -> MultiAssetPortfolio:
        with self._lock():
            if self.path.exists() or self.path.is_symlink():
                raise TradingError(
                    "State already exists; choose a new path; no overwrite or migration"
                )
            portfolio = MultiAssetPortfolio.create(config)
            self._write(portfolio)
            return portfolio

    def upgrade_for_cycle_execution(self) -> MultiAssetPortfolio:
        """Explicitly migrate v1 and preserve its exact bytes in an exclusive backup."""
        with self._lock():
            portfolio = self._load()
            if portfolio.format_version == FORMAT_VERSION:
                return portfolio
            source_descriptor = os.open(self.path, os.O_RDONLY | os.O_NOFOLLOW)
            try:
                with os.fdopen(source_descriptor, "rb") as source:
                    current_bytes = source.read(MAX_STATE_BYTES + 1)
            finally:
                # fdopen closes the descriptor on success; close only if open remains live.
                try:
                    os.close(source_descriptor)
                except OSError:
                    pass
            if len(current_bytes) > MAX_STATE_BYTES:
                raise TradingError("State exceeds maximum size")
            backup = self.path.with_name(self.path.name + ".v1-backup")
            descriptor = os.open(
                backup,
                os.O_CREAT | os.O_EXCL | os.O_WRONLY | os.O_NOFOLLOW,
                0o600,
            )
            try:
                with os.fdopen(descriptor, "wb") as handle:
                    handle.write(current_bytes)
                    handle.flush()
                    os.fsync(handle.fileno())
                directory = os.open(self.path.parent, os.O_DIRECTORY)
                try:
                    os.fsync(directory)
                finally:
                    os.close(directory)
                portfolio.format_version = FORMAT_VERSION
                portfolio.cycle_receipts = []
                portfolio.validate()
                self._write(portfolio)
                directory = os.open(self.path.parent, os.O_DIRECTORY)
                try:
                    os.fsync(directory)
                finally:
                    os.close(directory)
            except Exception:
                # The v1 backup is intentionally retained for recovery.
                raise
            return portfolio

    def load(self) -> MultiAssetPortfolio:
        with self._lock():
            return self._load()

    @contextmanager
    def transaction(self) -> Iterator[MultiAssetPortfolio]:
        with self._lock():
            portfolio = self._load()
            original_content = portfolio.to_dict()
            original = (
                portfolio.initial_capital_eur,
                portfolio.created_at,
                portfolio.config,
                portfolio.format_version,
            )
            yield portfolio
            if original != (
                portfolio.initial_capital_eur,
                portfolio.created_at,
                portfolio.config,
                portfolio.format_version,
            ):
                raise TradingError(
                    "Initial capital, identity, format and risk policy are immutable"
                )
            portfolio.validate()
            if portfolio.to_dict() != original_content:
                self._write(portfolio)

    def _load(self) -> MultiAssetPortfolio:
        if self.path.is_symlink():
            raise TradingError("State must be a regular file, not a symbolic link")
        try:
            with self.path.open("rb") as handle:
                content = handle.read(MAX_STATE_BYTES + 1)
            if len(content) > MAX_STATE_BYTES:
                raise TradingError("State exceeds maximum size")
            return MultiAssetPortfolio.from_dict(
                json.loads(content, object_pairs_hook=_unique_object)
            )
        except FileNotFoundError as exc:
            raise TradingError(
                "State does not exist; initialize a new multi-asset state explicitly"
            ) from exc
        except (ValueError, TypeError, KeyError, ArithmeticError, UnicodeError) as exc:
            raise TradingError(f"Invalid multi-asset state; refusing reset: {exc}") from exc

    def _write(self, portfolio: MultiAssetPortfolio) -> None:
        encoded = (
            json.dumps(portfolio.to_dict(), ensure_ascii=False, allow_nan=False, indent=2) + "\n"
        ).encode()
        if len(encoded) > MAX_STATE_BYTES:
            raise TradingError("State exceeds maximum size")
        descriptor, temporary = tempfile.mkstemp(
            prefix=f".{self.path.name}.", suffix=".tmp", dir=self.path.parent
        )
        try:
            with os.fdopen(descriptor, "wb") as handle:
                handle.write(encoded)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, self.path)
            directory = os.open(self.path.parent, os.O_DIRECTORY)
            try:
                os.fsync(directory)
            finally:
                os.close(directory)
        finally:
            if os.path.exists(temporary):
                os.unlink(temporary)


def _validate_order_shape(order: MultiAssetOrder) -> None:
    if order.action == "buy":
        if order.amount_eur is None or order.quantity is not None:
            raise TradingError("Buy requires amount_eur only")
        checked_decimal(order.amount_eur, "amount_eur")
    elif order.action == "sell":
        if order.quantity is None or order.amount_eur is not None:
            raise TradingError("Sell requires quantity only")
        checked_decimal(order.quantity, "quantity")
    else:
        raise TradingError("action must be buy or sell")


def _quote_reason(
    quote: Quote | None, platform: str, market: str, now: datetime, config: MultiAssetRiskConfig
) -> str | None:
    if not isinstance(quote, Quote):
        return "quote_missing"
    try:
        if quote.market != market:
            return "quote_market_mismatch"
        for name in ("bid", "ask", "min_order_eur", "quantity_step", "min_quantity"):
            checked_decimal(getattr(quote, name), name)
        if (
            quote.bid <= ZERO
            or quote.ask < quote.bid
            or quote.quantity_step <= ZERO
            or quote.min_order_eur <= ZERO
        ):
            return "quote_invalid"
        if quote.quantity_step < Decimal("1e-18"):
            return "quote_quantity_step_unsupported"
        checked_time(quote.timestamp)
        age = (now - quote.timestamp).total_seconds()
        if age < 0 or age > config.max_quote_age_seconds:
            return "quote_stale_or_future"
        spread = (quote.ask - quote.bid) / quote.bid * Decimal("10000")
        if spread > config.max_spread_bps:
            return "quote_spread_exceeds_limit"
    except TradingError:
        return "quote_invalid"
    return None


def _quote_snapshot(quote: Quote) -> dict:
    return {
        "bid": str(quote.bid),
        "ask": str(quote.ask),
        "timestamp": quote.timestamp.isoformat(),
        "min_order_eur": str(quote.min_order_eur),
        "quantity_step": str(quote.quantity_step),
        "min_quantity": str(quote.min_quantity),
    }


def _validate_quote_snapshot(raw: object) -> None:
    keys = {"bid", "ask", "timestamp", "min_order_eur", "quantity_step", "min_quantity"}
    if not isinstance(raw, dict) or set(raw) != keys:
        raise TradingError("Invalid stored quote")
    bid, ask = _decimal(raw["bid"], "bid"), _decimal(raw["ask"], "ask")
    if bid <= ZERO or ask < bid:
        raise TradingError("Invalid stored quote prices")
    for key in ("min_order_eur", "quantity_step"):
        if _decimal(raw[key], key) <= ZERO:
            raise TradingError("Invalid stored quote minimum")
    _decimal(raw["min_quantity"], "min_quantity")
    _timestamp(raw["timestamp"])


def _quote_from_snapshot(market: str, raw: dict) -> Quote:
    return Quote(
        market,
        _decimal(raw["bid"], "bid"),
        _decimal(raw["ask"], "ask"),
        _timestamp(raw["timestamp"]),
        _decimal(raw["min_order_eur"], "min_order_eur"),
        _decimal(raw["quantity_step"], "quantity_step"),
        _decimal(raw["min_quantity"], "min_quantity"),
    )


EVENT_KEYS = {
    "decision_id",
    "timestamp",
    "status",
    "action",
    "platform",
    "market",
    "provider",
    "reason",
    "requested_amount_eur",
    "requested_quantity",
    "quote_snapshot",
    "execution_price",
    "quantity",
    "notional_eur",
    "fee_eur",
    "cash_after",
    "position_quantity_after",
    "position_basis_after_eur",
    "realized_pnl_after_eur",
}


def _validate_multi_ledger(portfolio: MultiAssetPortfolio) -> None:
    cash, total_fees, total_realized = CAPITAL_EUR, ZERO, ZERO
    positions: dict[str, dict[str, Decimal]] = {}
    identifiers = set()
    for event in portfolio.ledger:
        if not isinstance(event, dict) or set(event) != EVENT_KEYS:
            raise TradingError("Invalid multi-asset ledger event fields")
        identifier = event["decision_id"]
        if not isinstance(identifier, str) or not identifier or identifier in identifiers:
            raise TradingError("Invalid or duplicate multi-asset decision id")
        identifiers.add(identifier)
        platform, market = _platform(event["platform"]), checked_market(event["market"])
        if event["action"] not in {"buy", "sell"} or event["status"] not in {
            "executed",
            "rejected",
        }:
            raise TradingError("Invalid multi-asset ledger action or status")
        for text_key in ("provider", "reason"):
            if not isinstance(event[text_key], str) or len(event[text_key]) > 1000:
                raise TradingError("Invalid multi-asset event text")
        if len(identifier) > 200:
            raise TradingError("Invalid multi-asset decision id")
        _timestamp(event["timestamp"])
        if event["quote_snapshot"] is not None:
            _validate_quote_snapshot(event["quote_snapshot"])
        key = position_key(platform, market)
        empty_position = {"quantity": ZERO, "basis": ZERO, "realized": ZERO, "fees": ZERO}
        pos = positions.get(key, empty_position)
        quantity = _decimal(event["quantity"], "ledger quantity")
        price = _decimal(event["execution_price"], "ledger price")
        notional = _decimal(event["notional_eur"], "ledger notional")
        fee = _decimal(event["fee_eur"], "ledger fee")
        if event["status"] == "executed":
            if key not in positions:
                pos = positions.setdefault(key, empty_position.copy())
            if (
                min(quantity, price, notional) <= ZERO
                or _money(
                    quantity * price, ROUND_CEILING if event["action"] == "buy" else ROUND_FLOOR
                )
                != notional
            ):
                raise TradingError("Multi-asset ledger fill is inconsistent")
            if _money(notional * portfolio.config.fee_rate, ROUND_CEILING) != fee:
                raise TradingError("Multi-asset ledger fee is inconsistent")
            if event["quote_snapshot"] is None:
                raise TradingError("Executed ledger event is missing its quote")
            quote = _quote_from_snapshot(market, event["quote_snapshot"])
            if _quote_reason(
                quote, platform, market, _timestamp(event["timestamp"]), portfolio.config
            ):
                raise TradingError("Executed ledger event used an invalid or stale quote")
            slip = portfolio.config.slippage_bps / Decimal("10000")
            if event["action"] == "buy":
                requested_amount = _decimal(event["requested_amount_eur"], "requested buy amount")
                if (
                    event["requested_quantity"] is not None
                    or requested_amount <= ZERO
                    or requested_amount > portfolio.config.max_trade_eur
                ):
                    raise TradingError("Invalid executed buy request")
                expected_price = _money(quote.ask * (Decimal("1") + slip), ROUND_CEILING)
                expected_quantity = _lot(
                    requested_amount
                    / (expected_price * (Decimal("1") + portfolio.config.fee_rate)),
                    quote.quantity_step,
                )
                if (
                    price != expected_price
                    or quantity != expected_quantity
                    or notional + fee > requested_amount
                ):
                    raise TradingError("Executed buy does not match its requested budget")
                if notional < quote.min_order_eur or quantity < quote.min_quantity:
                    raise TradingError("Executed buy violates quote minimums")
                if pos["basis"] + notional + fee > portfolio.config.max_position_eur:
                    raise TradingError("Executed buy violates per-position limit")
                if (
                    sum((item["basis"] for item in positions.values()), ZERO) + notional + fee
                    > portfolio.config.max_total_exposure_eur
                ):
                    raise TradingError("Executed buy violates global exposure limit")
                cash -= notional + fee
                pos["quantity"] += quantity
                pos["basis"] += notional + fee
            else:
                requested_quantity = _decimal(
                    event["requested_quantity"], "requested sell quantity"
                )
                if (
                    event["requested_amount_eur"] is not None
                    or requested_quantity <= ZERO
                    or quantity != _lot(requested_quantity, quote.quantity_step)
                ):
                    raise TradingError("Invalid executed sell request")
                expected_price = _money(quote.bid * (Decimal("1") - slip), ROUND_FLOOR)
                if (
                    price != expected_price
                    or quantity > pos["quantity"]
                    or notional > portfolio.config.max_trade_eur
                ):
                    raise TradingError("Executed sell violates quantity or trade limits")
                if notional < quote.min_order_eur or quantity < quote.min_quantity:
                    raise TradingError("Executed sell violates quote minimums")
                if quantity > pos["quantity"]:
                    raise TradingError("Multi-asset ledger sells more than held quantity")
                removed = (
                    pos["basis"]
                    if quantity == pos["quantity"]
                    else _money(pos["basis"] * quantity / pos["quantity"], ROUND_FLOOR)
                )
                cash += notional - fee
                pos["quantity"] -= quantity
                pos["basis"] -= removed
                gain = notional - fee - removed
                pos["realized"] += gain
                total_realized += gain
            pos["fees"] += fee
            total_fees += fee
        else:
            if any(item != ZERO for item in (quantity, price, notional, fee)):
                raise TradingError("Rejected multi-asset ledger row has a fill")
            for requested_name in ("requested_amount_eur", "requested_quantity"):
                requested_value = event[requested_name]
                if requested_value is not None and (
                    not isinstance(requested_value, str) or len(requested_value) > 80
                ):
                    raise TradingError("Invalid rejected order audit amount")
        if cash < ZERO:
            raise TradingError("Multi-asset ledger creates a negative cash balance")
        if (
            _decimal(event["cash_after"], "cash_after") != cash
            or _decimal(event["position_quantity_after"], "position_quantity_after")
            != pos["quantity"]
            or _decimal(event["position_basis_after_eur"], "position_basis_after_eur")
            != pos["basis"]
            or _decimal(
                event["realized_pnl_after_eur"], "realized_pnl_after_eur", nonnegative=False
            )
            != total_realized
        ):
            raise TradingError("Multi-asset ledger balances do not replay")
    if (
        cash != portfolio.cash_eur
        or total_fees != portfolio.fees_paid_eur
        or total_realized != portfolio.realized_pnl_eur
    ):
        raise TradingError("Multi-asset state does not match replayed ledger")
    for key, values in positions.items():
        position = portfolio.positions.get(key)
        if position is None or (
            position.quantity,
            position.cost_basis_eur,
            position.realized_pnl_eur,
            position.fees_paid_eur,
        ) != (values["quantity"], values["basis"], values["realized"], values["fees"]):
            raise TradingError("Multi-asset position does not match replayed ledger")
    for key, position in portfolio.positions.items():
        if key not in positions:
            raise TradingError("Unledgered multi-asset position")


def _validate_cycle_receipts(receipts: object, risk_policy_version: int) -> None:
    if not isinstance(receipts, list) or len(receipts) > 10_000:
        raise TradingError("Invalid proposal execution receipt list")
    seen: set[str] = set()
    keys = {
        "cycle_id",
        "content_sha256",
        "plan_sha256",
        "source_reports",
        "risk_policy_version",
        "sizing_policy",
        "portfolio_revision_before",
        "portfolio_revision_after",
        "committed_at",
        "status",
        "result",
    }
    digest_re = re.compile(r"[0-9a-f]{64}")
    for receipt in receipts:
        if not isinstance(receipt, dict) or set(receipt) != keys:
            raise TradingError("Invalid proposal execution receipt fields")
        cycle_id = receipt["cycle_id"]
        if (
            not isinstance(cycle_id, str)
            or not re.fullmatch(r"[0-9a-f]{24}", cycle_id)
            or cycle_id in seen
        ):
            raise TradingError("Invalid or duplicate proposal execution cycle id")
        seen.add(cycle_id)
        for name in (
            "content_sha256",
            "plan_sha256",
            "portfolio_revision_before",
            "portfolio_revision_after",
        ):
            if not isinstance(receipt[name], str) or not digest_re.fullmatch(receipt[name]):
                raise TradingError(f"Invalid proposal execution {name}")
        source_reports = receipt["source_reports"]
        if (
            not isinstance(source_reports, dict)
            or set(source_reports) != {"research_sha256", "review_sha256"}
            or any(
                not isinstance(value, str) or not digest_re.fullmatch(value)
                for value in source_reports.values()
            )
        ):
            raise TradingError("Invalid proposal execution source fingerprints")
        if type(receipt["risk_policy_version"]) is not int or receipt["risk_policy_version"] < 1:
            raise TradingError("Invalid proposal execution risk policy version")
        if receipt["risk_policy_version"] != risk_policy_version:
            raise TradingError("Proposal execution receipt has a different risk policy version")
        if not isinstance(receipt["sizing_policy"], str) or len(receipt["sizing_policy"]) > 100:
            raise TradingError("Invalid proposal execution sizing policy")
        _timestamp(receipt["committed_at"], "proposal execution commit time")
        if receipt["status"] not in {
            "committed",
            "no_accepted_operations",
            "no_transactions",
        }:
            raise TradingError("Invalid proposal execution receipt status")
        result = receipt["result"]
        if not isinstance(result, dict) or any(
            result.get(key) != value
            for key, value in (
                ("cycle_id", cycle_id),
                ("content_sha256", receipt["content_sha256"]),
                ("plan_sha256", receipt["plan_sha256"]),
                ("status", receipt["status"]),
                ("risk_policy_version", receipt["risk_policy_version"]),
                ("sizing_policy", receipt["sizing_policy"]),
                ("portfolio_revision_before", receipt["portfolio_revision_before"]),
                (
                    "portfolio_revision_after_financial_operations",
                    receipt["portfolio_revision_after"],
                ),
            )
        ):
            raise TradingError("Invalid proposal execution receipt result")


def _unique_object(pairs: list[tuple[str, object]]) -> dict:
    result = {}
    for key, value in pairs:
        if key in result:
            raise TradingError(f"Duplicate JSON key: {key}")
        result[key] = value
    return result
