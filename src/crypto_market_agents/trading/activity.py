"""Local runner activity, independent of the portfolio transaction lock and balances."""

from __future__ import annotations

from datetime import datetime, timezone
import json
import math
import os
from pathlib import Path
import stat
import threading

from crypto_market_agents.file_utils import atomic_write_text
from crypto_market_agents.security import redact_text
from .models import TradingError
from .store import fcntl

PHASES = frozenset({"starting", "market", "deciding", "executing", "waiting", "stopped", "error"})
MAX_ACTIVITY_BYTES = 32_768


def _utc_now():
    return datetime.now(timezone.utc)


def _timestamp(value):
    if not isinstance(value, str):
        raise ValueError("Invalid activity timestamp")
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise ValueError("Activity timestamps must include their timezone")
    return parsed


def _safe_error(error):
    if isinstance(error, OSError):
        # errno text is sufficient; filenames may reveal unrelated local directories.
        message = error.strerror or type(error).__name__
    else:
        message = str(error)
    return redact_text(message)[:1000]


def activity_path(state_path):
    path = Path(state_path).expanduser().absolute()
    return path.with_name(path.name + ".activity.json")


def read_activity(state_path, *, now=None, stale_after=20.0):
    """Read a bounded atomic snapshot without waiting on the paper transaction lock.

    Missing activity means no observed runner. A stale heartbeat means the last
    active session cannot be considered running; it does not change any balances.
    """
    snapshot = {
        "available": False,
        "phase": "stopped",
        "running": False,
        "stale": False,
        "updated_at": None,
        "started_at": None,
        "next_run_at": None,
        "cycle": 0,
        "provider": None,
        "model": None,
        "interval_seconds": None,
        "last_error": None,
        "last_result": None,
    }
    try:
        descriptor = os.open(activity_path(state_path), os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
        with os.fdopen(descriptor, "rb") as source:
            if not stat.S_ISREG(os.fstat(source.fileno()).st_mode):
                raise ValueError("Activity must be a regular file")
            raw = source.read(MAX_ACTIVITY_BYTES + 1)
        if len(raw) > MAX_ACTIVITY_BYTES:
            raise ValueError("Activity too large")
        payload = json.loads(raw)
        if not isinstance(payload, dict) or payload.get("version") != 1:
            raise ValueError("Invalid activity format")
        phase = payload["phase"]
        if not isinstance(phase, str) or phase not in PHASES:
            raise ValueError("Invalid phase")
        updated = _timestamp(payload["updated_at"])
        _timestamp(payload["started_at"])
        next_run = payload.get("next_run_at")
        if next_run is not None:
            _timestamp(next_run)
        cycle, interval = payload["cycle"], payload["interval_seconds"]
        if type(cycle) is not int or cycle < 0:
            raise ValueError("Invalid cycle")
        if type(interval) not in (int, float) or not math.isfinite(interval) or interval < 0:
            raise ValueError("Invalid interval")
        for key in ("provider", "model", "last_error"):
            value = payload.get(key)
            if value is not None and not isinstance(value, str):
                raise ValueError("Invalid activity text")
        last_result = payload.get("last_result")
        if last_result is not None:
            if not isinstance(last_result, dict):
                raise ValueError("Invalid activity result")
            last_result = {
                key: redact_text(str(last_result[key]))[:1000]
                for key in ("action", "status", "reason")
                if key in last_result
            } | {"duplicate": payload["last_result"].get("duplicate") is True}
        age = ((now or _utc_now()) - updated).total_seconds()
        stale = phase != "stopped" and (age > stale_after or age < -5)
        snapshot.update(
            available=True,
            phase=phase,
            running=phase != "stopped" and not stale,
            stale=stale,
            updated_at=payload["updated_at"],
            started_at=payload["started_at"],
            next_run_at=next_run,
            cycle=cycle,
            provider=redact_text(payload["provider"])[:100] if payload.get("provider") else None,
            model=redact_text(payload["model"])[:100] if payload.get("model") else None,
            interval_seconds=interval,
            last_error=redact_text(payload["last_error"])[:1000]
            if payload.get("last_error")
            else None,
            last_result=last_result,
        )
    except FileNotFoundError:
        pass
    except (OSError, ValueError, KeyError, TypeError, ArithmeticError):
        snapshot.update(phase="error", last_error="Le suivi du moteur est illisible.")
    return snapshot


class ActivityError(TradingError):
    """Runner lock or activity storage failed; never silently continue unobserved."""


class ActivitySession:
    """One runner per portfolio, with a heartbeat even during a slow model request."""

    def __init__(
        self,
        state_path,
        *,
        provider,
        model=None,
        interval_seconds=300,
        now_provider=_utc_now,
        heartbeat_seconds=5.0,
    ):
        if heartbeat_seconds <= 0:
            raise ValueError("Heartbeat interval must be positive")
        self.path = activity_path(state_path)
        self.lock_path = self.path.with_name(
            self.path.name.removesuffix(".activity.json") + ".runner.lock"
        )
        self.now_provider = now_provider
        self.heartbeat_seconds = heartbeat_seconds
        self._mutex = threading.RLock()
        self._stop = threading.Event()
        self._thread = None
        self._descriptor = None
        self._heartbeat_error = None
        self._data = {
            "version": 1,
            "phase": "starting",
            "started_at": None,
            "updated_at": None,
            "next_run_at": None,
            "cycle": 0,
            "provider": redact_text(provider)[:100],
            "model": redact_text(model)[:100] if model else None,
            "interval_seconds": interval_seconds,
            "last_error": None,
            "last_result": None,
        }

    def __enter__(self):
        if fcntl is None:
            raise ActivityError("Le suivi du moteur exige les verrous POSIX.")
        self.path.parent.mkdir(parents=True, exist_ok=True)
        descriptor = os.open(self.lock_path, os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
        try:
            try:
                fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                raise ActivityError("Un moteur utilise deja ce portefeuille.") from None
            self._descriptor = descriptor
            previous = read_activity(
                self.path.with_name(self.path.name.removesuffix(".activity.json"))
            )
            if previous["available"]:
                self._data["last_error"] = previous["last_error"]
                self._data["last_result"] = previous["last_result"]
            self._data["started_at"] = self.now_provider().isoformat()
            self.update("starting")
            self._thread = threading.Thread(
                target=self._heartbeat, daemon=True, name="paper-activity"
            )
            self._thread.start()
        except BaseException:
            os.close(descriptor)
            self._descriptor = None
            raise
        return self

    def _write_locked(self):
        self._data["updated_at"] = self.now_provider().isoformat()
        try:
            atomic_write_text(
                self.path, json.dumps(self._data, ensure_ascii=False, allow_nan=False) + "\n"
            )
        except (OSError, ValueError) as exc:
            raise ActivityError(f"Ecriture du suivi impossible: {_safe_error(exc)}") from exc

    def _heartbeat(self):
        while not self._stop.wait(self.heartbeat_seconds):
            try:
                with self._mutex:
                    self._write_locked()
            except (OSError, ValueError) as exc:
                self._heartbeat_error = exc
                return

    def update(self, phase, *, cycle=None, next_run_at=None):
        if phase not in PHASES:
            raise ValueError("Invalid runner phase")
        with self._mutex:
            if self._heartbeat_error is not None:
                raise self._heartbeat_error
            self._data["phase"] = phase
            self._data["next_run_at"] = next_run_at.isoformat() if next_run_at else None
            if cycle is not None:
                self._data["cycle"] = cycle
            self._write_locked()

    def record_result(self, result, *, error=None):
        """A committed cycle clears the previous error only if its provider succeeded."""
        with self._mutex:
            self._data["last_result"] = {
                key: redact_text(str(result[key]))[:1000]
                for key in ("action", "status", "reason")
                if key in result
            } | {"duplicate": result.get("duplicate") is True}
            self._data["last_error"] = _safe_error(error) if error else None
            self.update("error" if error else "executing")

    def record_error(self, error):
        with self._mutex:
            self._data["last_error"] = _safe_error(error)
            self.update("error")

    def __exit__(self, exc_type, exc, traceback):
        self._stop.set()
        if self._thread is not None:
            self._thread.join()
        write_error = self._heartbeat_error
        try:
            with self._mutex:
                self._data["phase"] = "stopped"
                self._data["next_run_at"] = None
                if exc is not None and not isinstance(exc, KeyboardInterrupt):
                    self._data["last_error"] = _safe_error(exc)
                try:
                    self._write_locked()
                except (OSError, ValueError) as error:
                    write_error = write_error or error
        finally:
            if self._descriptor is not None:
                fcntl.flock(self._descriptor, fcntl.LOCK_UN)
                os.close(self._descriptor)
                self._descriptor = None
        if write_error is not None:
            if exc is not None:
                exc.add_note(f"Echec du suivi: {_safe_error(write_error)}")
            else:
                raise write_error
        return False
