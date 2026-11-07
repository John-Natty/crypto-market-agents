"""Versioned JSON storage with process locking and atomic commits."""

from __future__ import annotations

from contextlib import contextmanager
from decimal import Decimal
import json
import os
from pathlib import Path
import tempfile
from typing import Iterator

from .models import Portfolio, RiskConfig, TradingError

try:
    import fcntl
except ImportError:  # pragma: no cover - the execution environment is Linux
    fcntl = None

MAX_STATE_BYTES = 20 * 1024 * 1024


class PaperStore:
    """A paper portfolio only. Missing/corrupt state never starts a fresh balance."""

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path).expanduser().absolute()
        self.lock_path = self.path.with_name(self.path.name + ".lock")

    @contextmanager
    def _lock(self) -> Iterator[None]:
        if fcntl is None:
            raise TradingError("Paper trading requires POSIX fcntl file locks on this platform")
        self.path.parent.mkdir(parents=True, exist_ok=True)
        # Keep the same lock inode across replacements of the state file.
        descriptor = os.open(self.lock_path, os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
        try:
            fcntl.flock(descriptor, fcntl.LOCK_EX)
            yield
        finally:
            fcntl.flock(descriptor, fcntl.LOCK_UN)
            os.close(descriptor)

    def initialize(
        self,
        *,
        market: str,
        market_source: str,
        initial_cash: Decimal = Decimal("50"),
        config: RiskConfig | None = None,
    ) -> Portfolio:
        with self._lock():
            if self.path.exists() or self.path.is_symlink():
                raise TradingError(
                    "Paper state already exists; choose a new path to create another portfolio"
                )
            portfolio = Portfolio.create(market, market_source, initial_cash, config)
            self._write(portfolio)
            return portfolio

    def load(
        self, *, expected_market: str | None = None, expected_source: str | None = None
    ) -> Portfolio:
        with self._lock():
            return self._load(expected_market, expected_source)

    def read_snapshot(self) -> Portfolio:
        """Read the last atomic commit without waiting for an in-flight decision.

        For observation only: trading must use the locked transaction API.
        Atomic replacement guarantees either the previous or the next whole file.
        """
        return self._load(None, None)

    @contextmanager
    def transaction(
        self, *, expected_market: str | None = None, expected_source: str | None = None
    ) -> Iterator[Portfolio]:
        with self._lock():
            portfolio = self._load(expected_market, expected_source)
            market, source = portfolio.market, portfolio.market_source
            initial, created = portfolio.initial_cash, portfolio.created_at
            config = portfolio.config
            yield portfolio
            if (
                portfolio.market != market
                or portfolio.market_source != source
                or portfolio.initial_cash != initial
                or portfolio.created_at != created
                or portfolio.config != config
            ):
                raise TradingError("Portfolio identity and initial configuration cannot change")
            self._write(portfolio)

    def _load(self, expected_market: str | None, expected_source: str | None) -> Portfolio:
        if self.path.is_symlink():
            raise TradingError("Paper state must be a regular file, not a symbolic link")
        try:
            with self.path.open("rb") as handle:
                content = handle.read(MAX_STATE_BYTES + 1)
            if len(content) > MAX_STATE_BYTES:
                raise TradingError("Paper state exceeds the maximum supported size")
            raw = json.loads(content, object_pairs_hook=_unique_object)
            portfolio = Portfolio.from_dict(raw)
        except FileNotFoundError as exc:
            raise TradingError(
                "Paper state does not exist; initialize it explicitly first"
            ) from exc
        except (ValueError, TypeError, KeyError, ArithmeticError, UnicodeError) as exc:
            raise TradingError(f"Invalid paper state; refusing to reset or trade: {exc}") from exc
        if expected_market is not None and portfolio.market != expected_market:
            raise TradingError("State market does not match the requested market")
        if expected_source is not None and portfolio.market_source != expected_source:
            raise TradingError(
                "State source does not match; mock and live-price portfolios stay separate"
            )
        return portfolio

    def _write(self, portfolio: Portfolio) -> None:
        data = json.dumps(portfolio.to_dict(), ensure_ascii=False, allow_nan=False, indent=2) + "\n"
        encoded = data.encode("utf-8")
        if len(encoded) > MAX_STATE_BYTES:
            raise TradingError("Paper state exceeds the maximum supported size")
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


def _unique_object(pairs: list[tuple[str, object]]) -> dict:
    result = {}
    for key, value in pairs:
        if key in result:
            raise TradingError(f"Duplicate JSON key: {key}")
        result[key] = value
    return result
