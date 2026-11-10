"""Public Bitvavo quote refresh and separate atomic quote-observation storage."""

from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timezone
import fcntl
import json
import os
from pathlib import Path
import tempfile
from typing import Iterator

from .market import BitvavoMarketData, MarketDataError
from .models import TradingError
from .multi_asset import (
    MAX_STATE_BYTES,
    MultiAssetPaperStore,
    _quote_reason,
    _quote_snapshot,
    _timestamp,
)

QUOTE_STATE_VERSION = 1


class MultiAssetQuoteStore:
    """Quote sidecar. It never edits the portfolio balances or transaction ledger."""

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

    def load_for(self, portfolio) -> dict:
        with self._lock():
            return self._load_for(portfolio)

    def record_refresh(
        self, portfolio, outcomes: dict, attempted_at: datetime, valuation: dict
    ) -> dict:
        with self._lock():
            state = self._load_for(portfolio, allow_missing=True)
            observations = state["observations"]
            for key, position in portfolio.positions.items():
                if position.quantity <= 0:
                    continue
                outcome = outcomes.get(key)
                previous = observations.get(key, {})
                last_quote = previous.get("last_quote") or position.last_quote
                source = previous.get("source") or ("trade_input" if last_quote else None)
                if isinstance(outcome, dict) and outcome.get("status") == "available":
                    last_quote = _quote_snapshot(outcome["quote"])
                    source = "bitvavo_public"
                    status, error = "available", None
                else:
                    status = "unavailable"
                    error = (
                        outcome.get("error", "quote_not_requested_during_refresh")
                        if isinstance(outcome, dict)
                        else "quote_not_requested_during_refresh"
                    )
                observations[key] = {
                    "platform": position.platform,
                    "market": position.market,
                    "status": status,
                    "last_attempt_at": attempted_at.isoformat(),
                    "last_quote": last_quote,
                    "source": source,
                    "error": error,
                }
            state["last_valuation"] = valuation
            self._write(state)
            return state

    def _empty(self, portfolio) -> dict:
        return {
            "format": "paper-multiasset-quotes",
            "version": QUOTE_STATE_VERSION,
            "portfolio_created_at": portfolio.created_at.isoformat(),
            "portfolio_format_version": portfolio.format_version,
            "observations": {},
            "last_valuation": None,
        }

    def _load_for(self, portfolio, *, allow_missing=False) -> dict:
        if self.path.is_symlink():
            raise TradingError("Quote state must not be a symbolic link")
        if not self.path.exists():
            if allow_missing:
                return self._empty(portfolio)
            return self._empty(portfolio)
        try:
            with self.path.open("rb") as handle:
                raw = handle.read(MAX_STATE_BYTES + 1)
            if len(raw) > MAX_STATE_BYTES:
                raise TradingError("Quote state exceeds maximum size")
            state = json.loads(raw, object_pairs_hook=_unique_object)
        except (OSError, ValueError, TypeError) as exc:
            raise TradingError(
                f"Invalid quote sidecar; refusing to reset: {type(exc).__name__}"
            ) from None
        required = {
            "format",
            "version",
            "portfolio_created_at",
            "portfolio_format_version",
            "observations",
            "last_valuation",
        }
        if (
            not isinstance(state, dict)
            or set(state) != required
            or state["format"] != "paper-multiasset-quotes"
            or type(state["version"]) is not int
            or state["version"] != QUOTE_STATE_VERSION
        ):
            raise TradingError("Unsupported multi-asset quote sidecar format")
        if (
            state["portfolio_created_at"] != portfolio.created_at.isoformat()
            or state["portfolio_format_version"] != portfolio.format_version
        ):
            raise TradingError("Quote sidecar belongs to a different portfolio instance")
        if not isinstance(state["observations"], dict):
            raise TradingError("Invalid quote observations map")
        if state["last_valuation"] is not None and not isinstance(state["last_valuation"], dict):
            raise TradingError("Invalid stored valuation")
        for key, observation in state["observations"].items():
            self._validate_observation(key, observation)
        return state

    @staticmethod
    def _validate_observation(key, observation):
        required = {
            "platform",
            "market",
            "status",
            "last_attempt_at",
            "last_quote",
            "source",
            "error",
        }
        if not isinstance(observation, dict) or set(observation) != required:
            raise TradingError("Invalid quote observation fields")
        if key != f"{observation['platform']}|{observation['market']}":
            raise TradingError("Quote observation identity mismatch")
        if observation["status"] not in {"available", "unavailable"}:
            raise TradingError("Invalid quote observation status")
        _timestamp(observation["last_attempt_at"], "quote attempt time")
        if observation["last_quote"] is not None:
            from .multi_asset import _validate_quote_snapshot

            _validate_quote_snapshot(observation["last_quote"])
        elif observation["status"] == "available":
            raise TradingError("Available quote observation has no quote")
        if observation["source"] is not None and not isinstance(observation["source"], str):
            raise TradingError("Invalid quote source")
        if observation["error"] is not None and not isinstance(observation["error"], str):
            raise TradingError("Invalid quote refresh error")

    def _write(self, state):
        payload = (json.dumps(state, ensure_ascii=False, allow_nan=False, indent=2) + "\n").encode()
        if len(payload) > MAX_STATE_BYTES:
            raise TradingError("Quote state exceeds maximum size")
        descriptor, temporary = tempfile.mkstemp(
            prefix=f".{self.path.name}.", suffix=".tmp", dir=self.path.parent
        )
        try:
            with os.fdopen(descriptor, "wb") as handle:
                handle.write(payload)
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


class MultiAssetQuoteRefresher:
    """Fetches public quotes per open position, preserving successful peers and old values."""

    def __init__(self, *, client=None, now_provider=lambda: datetime.now(timezone.utc)):
        self.client = client or BitvavoMarketData(now_provider=now_provider)
        self.now_provider = now_provider

    def refresh(
        self, portfolio_store: MultiAssetPaperStore, quote_store: MultiAssetQuoteStore
    ) -> dict:
        initial = portfolio_store.load()
        attempted_at = self.now_provider()
        outcomes, diagnostics = {}, []
        for key, position in sorted(initial.positions.items()):
            if position.quantity <= 0:
                continue
            if position.platform != "bitvavo":
                outcome = {"status": "unavailable", "error": "unsupported_platform"}
            else:
                try:
                    quote = self.client.get_quote(position.market)
                    invalid_reason = _quote_reason(
                        quote,
                        position.platform,
                        position.market,
                        self.now_provider(),
                        initial.config,
                    )
                    if invalid_reason:
                        outcome = {"status": "unavailable", "error": invalid_reason}
                    else:
                        outcome = {"status": "available", "quote": quote}
                except MarketDataError as exc:
                    outcome = {
                        "status": "unavailable",
                        "error": f"public_quote_failed:{type(exc).__name__}",
                    }
                except Exception as exc:
                    outcome = {
                        "status": "unavailable",
                        "error": f"public_quote_failed:{type(exc).__name__}",
                    }
            outcomes[key] = outcome
            diagnostics.append(
                {
                    "key": key,
                    "status": outcome["status"],
                    "reason": outcome.get("error"),
                }
            )

        current = portfolio_store.load()
        provisional = quote_store.load_for(current)
        merged = dict(provisional["observations"])
        for key, position in current.positions.items():
            if position.quantity > 0 and key not in outcomes:
                merged[key] = {
                    "platform": position.platform,
                    "market": position.market,
                    "status": "unavailable",
                    "last_attempt_at": attempted_at.isoformat(),
                    "last_quote": merged.get(key, {}).get("last_quote") or position.last_quote,
                    "source": merged.get(key, {}).get("source")
                    or ("trade_input" if position.last_quote else None),
                    "error": "position_changed_during_refresh",
                }
        # Evaluate current positions with both new observations and cached last-known quotes.
        for key, position in current.positions.items():
            if position.quantity <= 0:
                continue
            outcome = outcomes.get(key)
            previous = merged.get(key, {})
            if isinstance(outcome, dict) and outcome.get("status") == "available":
                merged[key] = {
                    "platform": position.platform,
                    "market": position.market,
                    "status": "available",
                    "last_attempt_at": attempted_at.isoformat(),
                    "last_quote": _quote_snapshot(outcome["quote"]),
                    "source": "bitvavo_public",
                    "error": None,
                }
            elif outcome is not None:
                merged[key] = {
                    "platform": position.platform,
                    "market": position.market,
                    "status": "unavailable",
                    "last_attempt_at": attempted_at.isoformat(),
                    "last_quote": previous.get("last_quote") or position.last_quote,
                    "source": previous.get("source")
                    or ("trade_input" if position.last_quote else None),
                    "error": outcome.get("error"),
                }
        valuation = current.valuation(
            quote_observations=merged,
            now=self.now_provider(),
        )
        state = quote_store.record_refresh(current, outcomes, attempted_at, valuation)
        is_complete = valuation["complete"]
        has_open_positions = any(position.quantity > 0 for position in current.positions.values())
        return {
            "status": "no_open_positions"
            if not has_open_positions
            else ("success" if is_complete else "partial"),
            "read_only": True,
            "transactions_before": len(initial.ledger),
            "transactions_after": len(portfolio_store.load().ledger),
            "refreshed_at": attempted_at.isoformat(),
            "positions": diagnostics,
            "valuation": state["last_valuation"],
            "quote_state_path": str(quote_store.path),
        }


def _unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise TradingError(f"Duplicate quote state key: {key}")
        result[key] = value
    return result
