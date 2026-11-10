"""Resumable, bounded orchestration of the read-only research and paper cycle."""

from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timezone
import fcntl
import hashlib
import json
import os
from pathlib import Path
import time
import uuid
from typing import Any, Callable, Iterator, Sequence

from crypto_market_agents.http_utils import http_request_budget
from .multi_asset import MultiAssetPaperStore

AUTO_SCHEMA = "automatic-paper-cycle.v1"
STAGES = ("research", "review", "proposal", "execution")


class AutomaticCycleError(RuntimeError):
    """The automatic cycle cannot safely continue or resume."""


def _canonical_hash(value: Any) -> str:
    encoded = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()
    return hashlib.sha256(encoded).hexdigest()


def _validate_budget(value: dict[str, int], expected: set[str]) -> None:
    if set(value) != expected or any(type(item) is not int or item < 1 for item in value.values()):
        raise ValueError("cycle_budget_invalid")


def _write_new_json(path: Path, value: Any) -> str:
    path.parent.mkdir(parents=True, exist_ok=True)
    raw = (json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode()
    descriptor = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY | os.O_NOFOLLOW, 0o600)
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(raw)
            handle.flush()
            os.fsync(handle.fileno())
        directory_fd = os.open(path.parent, os.O_DIRECTORY)
        try:
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)
    except Exception:
        try:
            path.unlink()
        except OSError:
            pass
        raise
    return hashlib.sha256(raw).hexdigest()


def _read_json(path: Path) -> tuple[dict[str, Any], str]:
    raw = path.read_bytes()
    if len(raw) > 20 * 1024 * 1024:
        raise AutomaticCycleError("cycle_artifact_too_large")
    value = json.loads(raw)
    if not isinstance(value, dict):
        raise AutomaticCycleError("cycle_artifact_invalid")
    return value, hashlib.sha256(raw).hexdigest()


@contextmanager
def _cycle_lock(state: Path) -> Iterator[None]:
    lock_path = state.with_name(state.name + ".automatic-cycle.lock")
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(lock_path, os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
    try:
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise AutomaticCycleError("cycle_already_running_for_portfolio") from exc
        yield
    finally:
        try:
            fcntl.flock(fd, fcntl.LOCK_UN)
        finally:
            os.close(fd)


class DailyBudget:
    """Conservative, atomically persisted daily usage, serialized per portfolio."""

    def __init__(self, path: Path, limits: dict[str, int]):
        self.path = path
        self.limits = limits

    @contextmanager
    def _lock(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        fd = os.open(
            self.path.with_suffix(self.path.suffix + ".lock"),
            os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW,
            0o600,
        )
        try:
            fcntl.flock(fd, fcntl.LOCK_EX)
            yield
        finally:
            fcntl.flock(fd, fcntl.LOCK_UN)
            os.close(fd)

    def consume(self, usage: dict[str, int], day: str) -> dict[str, Any]:
        with self._lock():
            try:
                fd = os.open(self.path, os.O_RDONLY | os.O_NOFOLLOW)
                try:
                    with os.fdopen(fd, "r", encoding="utf-8") as handle:
                        raw = json.load(handle)
                except Exception:
                    try:
                        os.close(fd)
                    except OSError:
                        pass
                    raise
            except FileNotFoundError:
                raw = {"schema": "automatic-cycle-daily-budget.v1", "days": {}}
            if raw.get("schema") != "automatic-cycle-daily-budget.v1" or not isinstance(
                raw.get("days"), dict
            ):
                raise AutomaticCycleError("daily_budget_ledger_invalid")
            current = raw["days"].get(day, {key: 0 for key in self.limits})
            updated = {
                key: int(current.get(key, 0)) + int(usage.get(key, 0)) for key in self.limits
            }
            if any(value < 0 for value in updated.values()):
                raise AutomaticCycleError("daily_budget_reconciliation_underflow")
            exceeded = [key for key, value in updated.items() if value > self.limits[key]]
            if exceeded:
                return {
                    "accepted": False,
                    "exceeded": exceeded,
                    "used": current,
                    "limits": self.limits,
                }
            raw["days"][day] = updated
            temporary = self.path.with_name(f".{self.path.name}.{uuid.uuid4().hex}.tmp")
            descriptor = os.open(
                temporary,
                os.O_CREAT | os.O_EXCL | os.O_WRONLY | os.O_NOFOLLOW,
                0o600,
            )
            with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
                json.dump(raw, handle, indent=2, sort_keys=True)
                handle.write("\n")
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, self.path)
            directory_fd = os.open(self.path.parent, os.O_DIRECTORY)
            try:
                os.fsync(directory_fd)
            finally:
                os.close(directory_fd)
            return {"accepted": True, "used": updated, "limits": self.limits}


class AutomaticCycleRunner:
    """Runs existing CLI stages; its only financial stage is explicit paper execution."""

    def __init__(
        self,
        *,
        state: str | Path,
        reports_dir: str | Path,
        invoke: Callable[[Sequence[str]], int],
        model: str = "qwen3:8b",
        objective: str,
        enable_paper_execution: bool = False,
        enable_gnews: bool = False,
        confirm_gnews_free_plan: bool = False,
        cycle_timeout_seconds: int = 600,
        cycle_limits: dict[str, int] | None = None,
        daily_limits: dict[str, int] | None = None,
        daily_budget_path: str | Path | None = None,
        research_limits: dict[str, int] | None = None,
        review_limits: dict[str, int] | None = None,
        proposal_limits: dict[str, int] | None = None,
        now: Callable[[], datetime] = lambda: datetime.now(timezone.utc),
        monotonic: Callable[[], float] = time.monotonic,
    ):
        self.state = Path(state).resolve()
        self.reports_dir = Path(reports_dir).resolve()
        self.invoke = invoke
        self.model = model
        self.objective = objective
        self.enable_paper_execution = enable_paper_execution
        self.enable_gnews = enable_gnews
        self.confirm_gnews_free_plan = confirm_gnews_free_plan
        self.cycle_timeout_seconds = cycle_timeout_seconds
        self.cycle_limits = cycle_limits or {
            "llm_calls": 20,
            "generation_tokens": 12000,
            "external_tool_calls": 25,
            "http_requests": 100,
        }
        self.daily_limits = daily_limits or {
            "duration_seconds": 3600,
            "llm_calls": 60,
            "generation_tokens": 30000,
            "external_tool_calls": 100,
            "http_requests": 500,
        }
        self.daily_budget_path = (
            Path(daily_budget_path)
            if daily_budget_path
            else (
                Path.home()
                / ".local"
                / "state"
                / "crypto-market-agents"
                / "automatic-cycle-daily.json"
            )
        )
        self.research_limits = research_limits or {
            "timeout": 300,
            "llm_timeout": 90,
            "num_predict": 1200,
            "total_num_predict": 6000,
            "conclusion_num_predict": 600,
            "conclusion_timeout": 45,
            "max_tool_calls": 10,
            "max_candidates": 5,
        }
        self.review_limits = review_limits or {"timeout": 60, "num_predict": 900}
        self.proposal_limits = proposal_limits or {
            "timeout": 60,
            "num_predict": 800,
            "total_num_predict": 4000,
        }
        self.now = now
        self.monotonic = monotonic
        if not objective.strip() or len(objective) > 1000:
            raise ValueError("cycle_objective_invalid")
        if not 1 <= cycle_timeout_seconds <= 900:
            raise ValueError("cycle_timeout_out_of_range")
        _validate_budget(
            self.daily_limits,
            {
                "duration_seconds",
                "llm_calls",
                "generation_tokens",
                "external_tool_calls",
                "http_requests",
            },
        )
        _validate_budget(
            self.cycle_limits,
            {"llm_calls", "generation_tokens", "external_tool_calls", "http_requests"},
        )
        if not (
            1 <= self.research_limits["max_candidates"] <= 5
            and 1 <= self.research_limits["max_tool_calls"] <= 10
            and 1 <= self.research_limits["timeout"] <= 300
            and 1 <= self.research_limits["llm_timeout"] <= 180
            and 128 <= self.research_limits["num_predict"] <= 2048
            and 128 <= self.research_limits["conclusion_num_predict"] <= 2048
            and 1 <= self.research_limits["conclusion_timeout"] <= 120
            and self.research_limits["conclusion_timeout"] + 3 <= self.research_limits["timeout"]
            and self.research_limits["num_predict"] + self.research_limits["conclusion_num_predict"]
            <= self.research_limits["total_num_predict"]
            <= 8192
        ):
            raise ValueError("research_budget_out_of_range")
        if not (
            1 <= self.review_limits["timeout"] <= 120
            and 128 <= self.review_limits["num_predict"] <= 2048
        ):
            raise ValueError("review_budget_out_of_range")
        if not (
            1 <= self.proposal_limits["timeout"] <= 90
            and 128 <= self.proposal_limits["num_predict"] <= 1200
            and self.proposal_limits["num_predict"]
            <= self.proposal_limits["total_num_predict"]
            <= 8192
        ):
            raise ValueError("proposal_budget_out_of_range")
        minimum_cycle = {
            "llm_calls": self.research_limits["max_tool_calls"] + 2 + 1 + 5,
            "generation_tokens": (
                self.research_limits["total_num_predict"]
                + self.review_limits["num_predict"]
                + self.proposal_limits["total_num_predict"]
            ),
            "external_tool_calls": self.research_limits["max_tool_calls"] + 5,
            "http_requests": 90,
        }
        insufficient = [
            key for key, required in minimum_cycle.items() if self.cycle_limits[key] < required
        ]
        required_seconds = (
            self.research_limits["timeout"]
            + self.review_limits["timeout"]
            + 180
            + (10 if enable_paper_execution else 0)
        )
        if insufficient or self.cycle_timeout_seconds < required_seconds:
            details = ",".join(insufficient) if insufficient else "duration_seconds"
            raise ValueError(f"cycle_budget_cannot_fund_configured_stages:{details}")
        if enable_gnews and not confirm_gnews_free_plan:
            raise ValueError("gnews_free_plan_confirmation_required")

    def run(
        self,
        *,
        resume: str | Path | None = None,
        stop_requested: Callable[[], bool] = lambda: False,
    ) -> dict[str, Any]:
        with _cycle_lock(self.state):
            started = self.monotonic()
            cycle_dir, manifest = self._load_or_create(resume)
            ledger = DailyBudget(
                self.daily_budget_path,
                self.daily_limits,
            )
            day = self.now().astimezone(timezone.utc).date().isoformat()
            manifest["status"] = "running"
            self._save_manifest(cycle_dir, manifest)
            current_stage = None
            try:
                for stage in STAGES:
                    if stage == "execution" and not self.enable_paper_execution:
                        manifest["stages"][stage] = {
                            "status": "disabled_by_default",
                            "reason": "explicit_activation_required",
                        }
                        manifest["status"] = "proposal_ready_execution_disabled"
                        manifest["stop_reason"] = "paper_execution_not_explicitly_enabled"
                        self._save_manifest(cycle_dir, manifest)
                        break
                    existing = manifest["stages"].get(stage, {})
                    if existing.get("status") == "completed":
                        self._verify_saved_stage(cycle_dir, stage, existing)
                        outcome = existing.get("outcome", {})
                        if stage in {"review", "proposal"} and outcome.get("terminal"):
                            manifest["status"] = outcome.get("status", "partial")
                            manifest["stop_reason"] = outcome.get("reason")
                            break
                        if stage == "execution":
                            report, _ = _read_json(cycle_dir / existing["artifact"])
                            manifest["status"] = (
                                "paper_executed"
                                if report.get("status") == "committed"
                                else "plan_stale_rejected"
                                if report.get("status") == "rejected"
                                else "completed_without_transaction"
                            )
                            break
                        continue
                    if stop_requested():
                        manifest["status"] = "stopped"
                        manifest["stop_reason"] = "stop_requested_before_next_stage"
                        break
                    if (
                        stage in {"proposal", "execution"}
                        and self._current_portfolio_revision()
                        != manifest["initial_portfolio_revision"]
                    ):
                        receipt = (
                            self._find_receipt_entry(manifest.get("proposal_cycle_id"))
                            if stage == "execution"
                            else None
                        )
                        if receipt is None:
                            manifest["status"] = "state_changed"
                            manifest["stop_reason"] = "portfolio_changed_since_cycle_start"
                            break
                        manifest["execution_receipt_observed"] = True
                    remaining = self.cycle_timeout_seconds - int(self.monotonic() - started)
                    if remaining <= 0:
                        raise AutomaticCycleError("cycle_duration_budget_exhausted")
                    if (
                        stage == "research"
                        and min(remaining, self.research_limits["timeout"])
                        < self.research_limits["conclusion_timeout"] + 3
                    ):
                        manifest["status"] = "budget_exhausted"
                        manifest["stop_reason"] = "insufficient_time_for_bounded_research_stage"
                        break
                    if stage == "proposal" and remaining < 180:
                        manifest["status"] = "budget_exhausted"
                        manifest["stop_reason"] = "insufficient_time_for_bounded_proposal_stage"
                        break
                    path = cycle_dir / self._artifact_name(stage)
                    recovered_artifact = path.is_file()
                    previous_stage = manifest["stages"].get(stage, {})
                    usage_reserve = (
                        previous_stage.get("reservation", {})
                        if recovered_artifact
                        else self._stage_reserve(
                            stage,
                            remaining,
                            max(
                                0,
                                self.cycle_limits["http_requests"]
                                - manifest["usage"]["http_requests"],
                            ),
                        )
                    )
                    budget_day = (
                        previous_stage.get("budget_day", day) if recovered_artifact else day
                    )
                    if recovered_artifact:
                        budget_status = {"accepted": True, "recovered_artifact": True}
                    else:
                        budget_status = ledger.consume(usage_reserve, day)
                    manifest["daily_budget_before_stage"][stage] = budget_status
                    if not budget_status["accepted"]:
                        manifest["status"] = "budget_exhausted"
                        manifest["stop_reason"] = "daily_budget_exhausted:" + ",".join(
                            budget_status["exceeded"]
                        )
                        break
                    argv = self._stage_argv(stage, cycle_dir, path, remaining)
                    manifest["stages"][stage] = {
                        **previous_stage,
                        "status": "running",
                        "artifact": path.name,
                        "started_at": self.now().isoformat(),
                        "budget_day": budget_day,
                        "reservation": usage_reserve,
                        "budget_granted": self._stage_budget_record(
                            stage, remaining, usage_reserve
                        ),
                    }
                    current_stage = stage
                    self._save_manifest(cycle_dir, manifest)
                    stage_started = self.monotonic()
                    http_usage = {"used": 0}
                    try:
                        with http_request_budget(
                            usage_reserve.get("http_requests", 0)
                        ) as http_usage:
                            code = 0 if path.is_file() else self.invoke(argv)
                    except BaseException:
                        self._record_http_attempts(
                            ledger,
                            manifest,
                            cycle_dir,
                            stage,
                            http_usage,
                            usage_reserve,
                            budget_day,
                            recovered_artifact,
                        )
                        raise
                    self._record_http_attempts(
                        ledger,
                        manifest,
                        cycle_dir,
                        stage,
                        http_usage,
                        usage_reserve,
                        budget_day,
                        recovered_artifact,
                    )
                    if not path.is_file():
                        if stage == "execution":
                            receipt_entry = self._find_receipt_entry(
                                manifest.get("proposal_cycle_id")
                            )
                            receipt = receipt_entry.get("result") if receipt_entry else None
                            if receipt is not None:
                                _write_new_json(path, receipt)
                        if not path.is_file():
                            raise AutomaticCycleError(f"{stage}_failed_without_report_exit_{code}")
                    report, digest = _read_json(path)
                    llm, tokens, external = self._usage(stage, report)
                    actual_duration = int(max(0, self.monotonic() - stage_started))
                    manifest["stages"][stage]["duration_seconds"] = actual_duration
                    manifest["stages"][stage]["usage"] = {
                        "llm_calls": llm,
                        "generation_tokens": tokens,
                        "external_tool_calls": external,
                        "http_requests": manifest["stages"][stage].get("http_requests_used"),
                        "http_request_upper_bound": manifest["stages"][stage].get(
                            "http_request_upper_bound"
                        ),
                    }
                    outcome = self._validate_stage(stage, report, manifest, cycle_dir)
                    if (
                        stage == "proposal"
                        and self._current_portfolio_revision()
                        != manifest["initial_portfolio_revision"]
                    ):
                        outcome = {
                            "status": "technical_error",
                            "reason": "portfolio_changed_during_proposal",
                        }
                    if outcome["status"] == "technical_error":
                        manifest["stages"][stage] = {
                            **manifest["stages"][stage],
                            "status": "technical_error",
                            "artifact": path.name,
                            "sha256": digest,
                            "details": outcome,
                        }
                        manifest["status"] = "technical_error"
                        manifest["stop_reason"] = outcome.get("reason", "stage_failed")
                        break
                    manifest["stages"][stage] = {
                        **manifest["stages"][stage],
                        "status": "completed",
                        "artifact": path.name,
                        "sha256": digest,
                        "outcome": outcome,
                    }
                    actual_usage = {
                        "duration_seconds": actual_duration,
                        "llm_calls": llm,
                        "generation_tokens": tokens,
                        "external_tool_calls": external,
                        "http_requests": (
                            usage_reserve.get("http_requests", 0)
                            if recovered_artifact
                            or manifest["stages"][stage].get("http_usage_recorded")
                            else http_usage["used"]
                        ),
                    }
                    reconciliation = ledger.consume(
                        {key: actual_usage[key] - usage_reserve[key] for key in usage_reserve},
                        budget_day,
                    )
                    manifest["usage"]["duration_seconds"] += actual_duration
                    manifest["usage"]["llm_calls"] += llm
                    manifest["usage"]["generation_tokens"] += tokens
                    manifest["usage"]["external_tool_calls"] += external
                    if any(
                        manifest["usage"][key] > self.cycle_limits[key] for key in self.cycle_limits
                    ):
                        manifest["status"] = "budget_exhausted"
                        manifest["stop_reason"] = "cycle_usage_budget_exceeded"
                        self._save_manifest(cycle_dir, manifest)
                        break
                    if not reconciliation["accepted"]:
                        manifest["status"] = "budget_exhausted"
                        manifest["stop_reason"] = "daily_budget_exceeded_after_stage"
                        self._save_manifest(cycle_dir, manifest)
                        break
                    if stage == "proposal":
                        manifest["proposal_cycle_id"] = report.get("cycle_id")
                    self._save_manifest(cycle_dir, manifest)
                    if stage in {"review", "proposal"} and outcome.get("terminal"):
                        manifest["status"] = outcome["status"]
                        manifest["stop_reason"] = outcome.get("reason")
                        break
                    if stage == "execution":
                        manifest["status"] = (
                            "paper_executed"
                            if report.get("status") == "committed"
                            else "plan_stale_rejected"
                            if report.get("status") == "rejected"
                            else "completed_without_transaction"
                        )
                        break
                    current_stage = None
                else:
                    manifest["status"] = "completed_without_execution"
                manifest["elapsed_seconds"] = round(self.monotonic() - started, 3)
            except KeyboardInterrupt:
                receipt = self._find_receipt_entry(manifest.get("proposal_cycle_id"))
                result = receipt.get("result") if receipt else None
                manifest["status"] = (
                    "paper_executed"
                    if result and result.get("status") == "committed"
                    else "interrupted"
                )
                manifest["stop_reason"] = "interrupt_received; no positions liquidated"
                if receipt:
                    manifest["execution_receipt"] = result
                if (
                    current_stage
                    and manifest["stages"].get(current_stage, {}).get("status") == "running"
                ):
                    manifest["stages"][current_stage]["status"] = (
                        "commit_confirmed_by_receipt"
                        if result and result.get("status") == "committed"
                        else "interrupted"
                    )
            except Exception as exc:
                manifest["status"] = "technical_error"
                manifest["error"] = f"{type(exc).__name__}:{exc}"
                if (
                    current_stage
                    and manifest["stages"].get(current_stage, {}).get("status") == "running"
                ):
                    manifest["stages"][current_stage]["status"] = "technical_error"
                    manifest["stages"][current_stage]["error"] = str(exc)[:180]
            finally:
                manifest["elapsed_seconds"] = round(self.monotonic() - started, 3)
                self._save_manifest(cycle_dir, manifest)
            return manifest

    def _load_or_create(self, resume: str | Path | None):
        if resume is not None:
            directory = Path(resume).resolve()
            manifest, _ = _read_json(directory / "cycle.json")
            allowed_parent = (self.reports_dir / "automatic-cycles").resolve()
            if directory.parent != allowed_parent or directory.name != manifest.get("cycle_id"):
                raise AutomaticCycleError("resume_cycle_directory_invalid")
            if manifest.get("schema_version") != AUTO_SCHEMA or manifest.get("state_path") != str(
                self.state
            ):
                raise AutomaticCycleError("resume_manifest_mismatch")
            expected_budgets = {
                "cycle_duration_seconds": self.cycle_timeout_seconds,
                "cycle": self.cycle_limits,
                "daily": self.daily_limits,
                "research": self.research_limits,
                "review": self.review_limits,
                "proposal": self.proposal_limits,
            }
            if (
                manifest.get("model") != self.model
                or manifest.get("objective") != self.objective
                or manifest.get("execution_explicitly_enabled") is not self.enable_paper_execution
                or manifest.get("gnews_enabled") is not self.enable_gnews
                or manifest.get("budgets") != expected_budgets
                or manifest.get("daily_budget_ledger_path") != str(self.daily_budget_path.resolve())
            ):
                raise AutomaticCycleError("resume_configuration_mismatch")
            return directory, manifest
        if not self.state.is_file():
            raise AutomaticCycleError("portfolio_state_missing")
        portfolio = MultiAssetPaperStore(self.state).load()
        if self.enable_paper_execution and portfolio.format_version != 2:
            raise AutomaticCycleError("automatic_execution_requires_portfolio_v2")
        cycle_id = uuid.uuid4().hex
        parent = self.reports_dir / "automatic-cycles"
        parent.mkdir(parents=True, exist_ok=True)
        directory = parent / cycle_id
        directory.mkdir(mode=0o700, exist_ok=False)
        manifest = {
            "schema_version": AUTO_SCHEMA,
            "cycle_id": cycle_id,
            "created_at": self.now().isoformat(),
            "state_path": str(self.state),
            "daily_budget_ledger_path": str(self.daily_budget_path.resolve()),
            "initial_portfolio_revision": _canonical_hash(portfolio.to_dict()),
            "model": self.model,
            "objective": self.objective,
            "execution_explicitly_enabled": self.enable_paper_execution,
            "gnews_enabled": self.enable_gnews,
            "budgets": {
                "cycle_duration_seconds": self.cycle_timeout_seconds,
                "cycle": self.cycle_limits,
                "daily": self.daily_limits,
                "research": self.research_limits,
                "review": self.review_limits,
                "proposal": self.proposal_limits,
            },
            "stages": {},
            "usage": {
                "duration_seconds": 0,
                "llm_calls": 0,
                "generation_tokens": 0,
                "external_tool_calls": 0,
                "http_requests": 0,
            },
            "daily_budget_before_stage": {},
            "status": "created",
            "stop_reason": None,
        }
        self._save_manifest(directory, manifest)
        return directory, manifest

    def _current_portfolio_revision(self):
        portfolio = MultiAssetPaperStore(self.state).load()
        return _canonical_hash(portfolio.to_dict())

    def _save_manifest(self, directory: Path, manifest: dict[str, Any]):
        temporary = directory / f".cycle.{uuid.uuid4().hex}.tmp"
        descriptor = os.open(
            temporary,
            os.O_CREAT | os.O_EXCL | os.O_WRONLY | os.O_NOFOLLOW,
            0o600,
        )
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            json.dump(manifest, handle, ensure_ascii=False, indent=2, sort_keys=True)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, directory / "cycle.json")
        directory_fd = os.open(directory, os.O_DIRECTORY)
        try:
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)

    def _verify_saved_stage(self, directory, stage, record):
        report, digest = _read_json(directory / record["artifact"])
        if digest != record.get("sha256"):
            raise AutomaticCycleError(f"resume_{stage}_artifact_hash_mismatch")
        if (
            self._validate_stage(stage, report, {"stages": {}}, directory).get("status")
            == "technical_error"
        ):
            raise AutomaticCycleError(f"resume_{stage}_artifact_invalid")

    @staticmethod
    def _artifact_name(stage):
        return {
            "research": "research.json",
            "review": "review.json",
            "proposal": "proposal.json",
            "execution": "execution.json",
        }[stage]

    def _stage_argv(self, stage, directory, output, remaining):
        if stage == "research":
            args = [
                "research",
                "--objective",
                self.objective,
                "--model",
                self.model,
                "--json",
                str(output),
                "--timeout",
                str(min(self.research_limits["timeout"], remaining)),
                "--llm-timeout",
                str(self.research_limits["llm_timeout"]),
                "--num-predict",
                str(self.research_limits["num_predict"]),
                "--total-num-predict",
                str(self.research_limits["total_num_predict"]),
                "--conclusion-num-predict",
                str(self.research_limits["conclusion_num_predict"]),
                "--conclusion-timeout",
                str(self.research_limits["conclusion_timeout"]),
                "--max-tool-calls",
                str(self.research_limits["max_tool_calls"]),
                "--max-candidates",
                str(self.research_limits["max_candidates"]),
            ]
            if self.enable_gnews:
                args += ["--enable-gnews", "--confirm-gnews-free-plan"]
            return args
        if stage == "review":
            return [
                "review-research",
                "--input-json",
                str(directory / "research.json"),
                "--json",
                str(output),
                "--model",
                self.model,
                "--timeout",
                str(min(self.review_limits["timeout"], remaining)),
                "--num-predict",
                str(self.review_limits["num_predict"]),
            ]
        if stage == "proposal":
            return [
                "proposal-cycle",
                "--research-json",
                str(directory / "research.json"),
                "--review-json",
                str(directory / "review.json"),
                "--state",
                str(self.state),
                "--json",
                str(output),
                "--model",
                self.model,
                "--timeout",
                str(min(self.proposal_limits["timeout"], remaining)),
                "--num-predict",
                str(self.proposal_limits["num_predict"]),
                "--total-num-predict",
                str(self.proposal_limits["total_num_predict"]),
            ]
        return [
            "execute-proposal-cycle",
            "--plan-json",
            str(directory / "proposal.json"),
            "--research-json",
            str(directory / "research.json"),
            "--review-json",
            str(directory / "review.json"),
            "--state",
            str(self.state),
            "--json",
            str(output),
        ]

    def _validate_stage(self, stage, report, manifest, directory):
        if stage == "research":
            if (
                not str(report.get("schema_version", "")).startswith("autonomous-research.")
                or report.get("read_only") is not True
            ):
                return {"status": "technical_error", "reason": "research_report_contract_invalid"}
            return {
                "status": "partial" if report.get("status") != "completed" else "completed",
                "research_status": report.get("research_status"),
                "reason": report.get("stop_reason"),
            }
        if stage == "review":
            source, source_hash = _read_json(directory / "research.json")
            if (
                not str(report.get("schema_version", "")).startswith(
                    "research-contradiction-review."
                )
                or report.get("source_report_sha256") != source_hash
            ):
                return {"status": "technical_error", "reason": "review_source_or_contract_mismatch"}
            if report.get("status") in {"unavailable", "error"}:
                return {
                    "status": "review_unavailable",
                    "terminal": True,
                    "reason": report.get("status"),
                }
            return {
                "status": report.get("status", "partial"),
                "review_status": report.get("status"),
            }
        if stage == "proposal":
            if report.get("schema_version") != "paper-proposal-cycle.v2" or not isinstance(
                report.get("proposals"), list
            ):
                return {"status": "technical_error", "reason": "proposal_contract_invalid"}
            research, research_hash = _read_json(directory / "research.json")
            review, review_hash = _read_json(directory / "review.json")
            source_reports = report.get("source_reports", {})
            if (
                source_reports.get("research", {}).get("sha256") != research_hash
                or source_reports.get("review", {}).get("sha256") != review_hash
            ):
                return {"status": "technical_error", "reason": "proposal_source_hash_mismatch"}
            if report.get("technical_status") not in {"completed", "partial"}:
                return {
                    "status": "proposal_rejected",
                    "terminal": True,
                    "reason": report.get("decision_status"),
                }
            return {
                "status": report.get("decision_status"),
                "decision_status": report.get("decision_status"),
                "accepted_plans": sum(
                    row.get("status") == "accepted" for row in report["proposals"]
                ),
                "terminal": not any(
                    row.get("status") == "accepted" and row.get("action") in {"buy", "reduce"}
                    for row in report["proposals"]
                ),
                "reason": report.get("decision_status"),
            }
        if report.get("status") not in {
            "committed",
            "no_accepted_operations",
            "no_transactions",
            "rejected",
        }:
            return {"status": "technical_error", "reason": "execution_result_invalid"}
        status = (
            "plan_stale_rejected" if report.get("status") == "rejected" else report.get("status")
        )
        return {
            "status": status,
            "terminal": status == "plan_stale_rejected",
            "reason": report.get("reason"),
            "transaction_count": report.get("transaction_count", 0),
            "replayed": report.get("replayed", False),
        }

    @staticmethod
    def _usage(stage, report):
        if stage == "research":
            budget = report.get("budget", {})
            return (
                budget.get("ollama_turns", 0),
                budget.get("generation_tokens_reported", 0),
                budget.get("tool_calls_used", 0),
            )
        if stage == "review":
            generation = report.get("generation", {})
            return 1, generation.get("eval_count", 0), 0
        if stage == "proposal":
            generation = report.get("generation", {})
            return (
                len(generation.get("calls", [])),
                generation.get("generated_tokens", 0),
                len(report.get("quote_observations", [])),
            )
        return 0, 0, 0

    def _stage_reserve(self, stage, remaining, remaining_http_requests):
        return {
            "duration_seconds": min(
                remaining,
                {
                    "research": self.research_limits["timeout"],
                    "review": self.review_limits["timeout"],
                    "proposal": 180,
                    "execution": 10,
                }[stage],
            ),
            "llm_calls": {
                "research": self.research_limits["max_tool_calls"] + 2,
                "review": 1,
                "proposal": 5,
                "execution": 0,
            }[stage],
            "generation_tokens": {
                "research": self.research_limits["total_num_predict"],
                "review": self.review_limits["num_predict"],
                "proposal": self.proposal_limits["total_num_predict"],
                "execution": 0,
            }[stage],
            "external_tool_calls": {
                "research": self.research_limits["max_tool_calls"],
                "review": 0,
                "proposal": 5,
                "execution": 0,
            }[stage],
            "http_requests": min(
                remaining_http_requests,
                {"research": 80, "review": 0, "proposal": 10, "execution": 0}[stage],
            ),
        }

    def _stage_budget_record(self, stage, remaining, reservation):
        generation = {
            "research": {
                "per_call_tokens": self.research_limits["num_predict"],
                "total_tokens_including_conclusion": self.research_limits["total_num_predict"],
                "conclusion_tokens": self.research_limits["conclusion_num_predict"],
            },
            "review": {
                "per_call_tokens": self.review_limits["num_predict"],
                "total_tokens": self.review_limits["num_predict"],
            },
            "proposal": {
                "per_market_tokens": self.proposal_limits["num_predict"],
                "total_tokens": self.proposal_limits["total_num_predict"],
            },
            "execution": {"total_tokens": 0},
        }[stage]
        timeouts = {
            "research": {
                "stage_seconds": min(self.research_limits["timeout"], remaining),
                "per_llm_call_seconds": self.research_limits["llm_timeout"],
                "conclusion_seconds": self.research_limits["conclusion_timeout"],
            },
            "review": {"stage_seconds": min(self.review_limits["timeout"], remaining)},
            "proposal": {
                "stage_seconds": 180,
                "requested_per_call_seconds": self.proposal_limits["timeout"],
                "effective_per_call_seconds": "dynamically divided after quote reserve",
            },
            "execution": {"stage_seconds": reservation["duration_seconds"]},
        }[stage]
        return {
            "llm_calls": reservation["llm_calls"],
            "generation": generation,
            "external_tool_calls": reservation["external_tool_calls"],
            "http_requests": reservation["http_requests"],
            "duration": timeouts,
        }

    def _record_http_attempts(
        self,
        ledger,
        manifest,
        cycle_dir,
        stage,
        counter,
        reservation,
        budget_day,
        recovered_artifact,
    ):
        stage_record = manifest["stages"][stage]
        if recovered_artifact:
            if stage_record.get("http_usage_recorded"):
                return
            used = reservation.get("http_requests", 0)
            stage_record["http_requests_used"] = None
            stage_record["http_request_upper_bound"] = used
            stage_record["http_usage_recorded"] = True
            manifest["usage"]["http_requests"] += used
            manifest["usage"]["http_request_count_unknown_after_recovery"] = True
            self._save_manifest(cycle_dir, manifest)
            return

        used = counter.get("used", 0)
        stage_record["http_requests_used"] = used
        stage_record["http_usage_recorded"] = True
        manifest["usage"]["http_requests"] += used
        reconciliation = ledger.consume(
            {"http_requests": used - reservation.get("http_requests", 0)},
            budget_day,
        )
        manifest["daily_budget_before_stage"].setdefault(stage, {})["http_reconciliation"] = (
            reconciliation
        )
        if not reconciliation["accepted"]:
            manifest["status"] = "budget_exhausted"
            manifest["stop_reason"] = "daily_http_budget_reconciliation_failed"
        if manifest["usage"]["http_requests"] > self.cycle_limits["http_requests"]:
            manifest["status"] = "budget_exhausted"
            manifest["stop_reason"] = "cycle_http_budget_exceeded"
        self._save_manifest(cycle_dir, manifest)

    def _find_receipt_entry(self, cycle_id):
        if not cycle_id or not self.state.is_file():
            return None
        portfolio = MultiAssetPaperStore(self.state).load()
        for receipt in portfolio.cycle_receipts:
            if receipt.get("cycle_id") == cycle_id:
                return receipt
        return None


def run_repeating(
    runner: AutomaticCycleRunner,
    *,
    interval_seconds: int,
    stop_requested: Callable[[], bool],
    sleep: Callable[[float], None] = time.sleep,
) -> list[dict[str, Any]]:
    """Foreground repeat mode; a stop signal prevents starting the next cycle."""
    if interval_seconds < 1:
        raise ValueError("repeat_interval_must_be_positive")
    results = []
    while not stop_requested():
        result = runner.run(stop_requested=stop_requested)
        results.append(result)
        if result.get("status") in {"interrupted", "technical_error", "budget_exhausted"}:
            return results
        for _ in range(interval_seconds):
            if stop_requested():
                return results
            sleep(1)
    return results
