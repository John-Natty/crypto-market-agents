import argparse
from contextlib import redirect_stdout
from datetime import datetime, timedelta, timezone
from io import StringIO
import json
import multiprocessing
import os
from pathlib import Path
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from crypto_market_agents.trading.activity import (
    ActivityError,
    ActivitySession,
    activity_path,
    read_activity,
)
from crypto_market_agents.trading.cli import add_paper_parser, run_paper
from crypto_market_agents.trading.decision import ReferenceDecisionAgent
from crypto_market_agents.trading.market import MarketDataError, MockMarketData
from crypto_market_agents.trading.store import PaperStore


def _competing_runner(state, results):
    try:
        with ActivitySession(state, provider="rules"):
            results.put("acquired")
    except ActivityError as exc:
        results.put(str(exc))


class ActivityTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.state = Path(self.temp.name) / "portfolio.json"
        self.now = datetime(2026, 10, 5, 8, tzinfo=timezone.utc)

    def test_missing_corrupt_and_oversized_snapshots_are_safe(self):
        snapshot = read_activity(self.state)
        self.assertFalse(snapshot["available"])
        self.assertFalse(snapshot["running"])
        for content in ("{", '{"version":99}', "x" * 33_000):
            activity_path(self.state).write_text(content)
            snapshot = read_activity(self.state)
            self.assertFalse(snapshot["available"])
            self.assertEqual(snapshot["phase"], "error")
            self.assertNotIn(str(self.state), json.dumps(snapshot))

    def test_special_file_does_not_block_activity_read(self):
        os.mkfifo(activity_path(self.state))
        start = time.monotonic()
        snapshot = read_activity(self.state)
        self.assertLess(time.monotonic() - start, 1)
        self.assertFalse(snapshot["available"])
        self.assertEqual(snapshot["phase"], "error")

    def test_heartbeat_keeps_slow_model_live_then_stops_cleanly(self):
        clock = [self.now]
        heartbeat_written = threading.Event()
        from crypto_market_agents.trading.activity import atomic_write_text

        def observe_write(path, data):
            atomic_write_text(path, data)
            if threading.current_thread().name == "paper-activity":
                heartbeat_written.set()

        with patch("crypto_market_agents.trading.activity.atomic_write_text", observe_write):
            with ActivitySession(
                self.state,
                provider="ollama",
                model="gemma3:latest",
                heartbeat_seconds=0.02,
                now_provider=lambda: clock[0],
            ) as activity:
                activity.update("deciding", cycle=1)
                clock[0] += timedelta(seconds=30)
                self.assertTrue(heartbeat_written.wait(2))
                snapshot = read_activity(self.state, now=clock[0])
                self.assertTrue(snapshot["running"])
                self.assertFalse(snapshot["stale"])
                self.assertEqual(snapshot["phase"], "deciding")
                self.assertEqual(snapshot["updated_at"], clock[0].isoformat())
                self.assertFalse(self.state.exists(), "Activity must never create a portfolio")
        snapshot = read_activity(self.state, now=clock[0] + timedelta(days=1))
        self.assertEqual(snapshot["phase"], "stopped")
        self.assertFalse(snapshot["running"])
        self.assertFalse(snapshot["stale"])

    def test_stale_and_future_heartbeat_are_not_reported_as_running(self):
        with ActivitySession(self.state, provider="rules", now_provider=lambda: self.now):
            for instant in (self.now + timedelta(seconds=21), self.now - timedelta(seconds=6)):
                snapshot = read_activity(self.state, now=instant)
                self.assertTrue(snapshot["stale"])
                self.assertFalse(snapshot["running"])
            self.assertTrue(read_activity(self.state, now=self.now)["running"])
            self.assertFalse(
                read_activity(self.state, now=self.now + timedelta(seconds=21), stale_after=30)[
                    "stale"
                ]
            )

    def test_error_is_redacted_and_persists_across_wait_and_restart_until_success(self):
        with ActivitySession(self.state, provider="rules") as activity:
            activity.record_error(ValueError("api_key=supersecret"))
            activity.update("waiting", next_run_at=self.now + timedelta(seconds=30))
            snapshot = read_activity(self.state)
            self.assertIn("[REDACTED]", snapshot["last_error"])
            self.assertNotIn("supersecret", activity_path(self.state).read_text())
            self.assertEqual(
                snapshot["next_run_at"], (self.now + timedelta(seconds=30)).isoformat()
            )
        with ActivitySession(self.state, provider="rules") as activity:
            activity.update("market", cycle=1)
            self.assertIsNotNone(read_activity(self.state)["last_error"])
            activity.record_result({"action": "hold", "status": "held", "reason": "Observation"})
            snapshot = read_activity(self.state)
            self.assertIsNone(snapshot["last_error"])
            self.assertEqual(snapshot["last_result"]["action"], "hold")

    def test_separate_process_cannot_start_or_overwrite_active_runner(self):
        ctx = multiprocessing.get_context("spawn")
        results = ctx.Queue()
        with ActivitySession(
            self.state, provider="ollama", model="local", heartbeat_seconds=60
        ) as activity:
            activity.update("deciding", cycle=3)
            before = activity_path(self.state).read_bytes()
            process = ctx.Process(target=_competing_runner, args=(str(self.state), results))
            process.start()
            process.join(5)
            if process.is_alive():
                process.terminate()
                process.join()
                self.fail("Concurrent runner did not reject promptly")
            self.assertEqual(process.exitcode, 0)
            self.assertIn("deja", results.get(timeout=1))
            self.assertEqual(activity_path(self.state).read_bytes(), before)
        with ActivitySession(self.state, provider="rules"):
            self.assertTrue(read_activity(self.state)["running"])
        results.close()

    def test_exception_or_interrupt_stops_and_releases_runner(self):
        with self.assertRaisesRegex(ValueError, "ledger failed"):
            with ActivitySession(self.state, provider="rules"):
                raise ValueError("ledger failed")
        snapshot = read_activity(self.state)
        self.assertEqual(snapshot["phase"], "stopped")
        self.assertEqual(snapshot["last_error"], "ledger failed")
        with self.assertRaises(KeyboardInterrupt):
            with ActivitySession(self.state, provider="rules"):
                raise KeyboardInterrupt
        self.assertEqual(read_activity(self.state)["phase"], "stopped")

    def test_telemetry_failure_is_not_silenced_and_does_not_mask_portfolio_failure(self):
        session = ActivitySession(self.state, provider="rules")
        with self.assertRaisesRegex(ActivityError, "suivi impossible"):
            with session:
                with patch(
                    "crypto_market_agents.trading.activity.atomic_write_text",
                    side_effect=OSError(28, "disk full", "/private/path"),
                ):
                    session.update("market")
        self.assertNotIn("/private/path", activity_path(self.state).read_text())
        final_write_failure = patch(
            "crypto_market_agents.trading.activity.atomic_write_text",
            side_effect=OSError(28, "disk full"),
        )
        try:
            with self.assertRaisesRegex(ValueError, "portfolio failed") as raised:
                with ActivitySession(self.state, provider="rules"):
                    final_write_failure.start()
                    raise ValueError("portfolio failed")
        finally:
            final_write_failure.stop()
        self.assertEqual(str(raised.exception), "portfolio failed")
        self.assertIn("Echec du suivi", raised.exception.__notes__[0])
        with patch(
            "crypto_market_agents.trading.activity.atomic_write_text",
            side_effect=OSError(28, "disk full"),
        ):
            with self.assertRaises(ActivityError):
                with ActivitySession(self.state, provider="rules"):
                    pass
        # Initial activity failure must release the runner lock.
        with ActivitySession(self.state, provider="rules"):
            pass

    def test_background_write_failure_surfaces_at_next_transition(self):
        entered = threading.Event()
        from crypto_market_agents.trading.activity import atomic_write_text

        def broken_heartbeat(path, data):
            if threading.current_thread().name == "paper-activity":
                entered.set()
                raise OSError(28, "disk full")
            atomic_write_text(path, data)

        with patch("crypto_market_agents.trading.activity.atomic_write_text", broken_heartbeat):
            with self.assertRaises(ActivityError):
                with ActivitySession(
                    self.state, provider="rules", heartbeat_seconds=0.02
                ) as activity:
                    self.assertTrue(entered.wait(2))
                    activity._thread.join(2)
                    activity.update("executing")
        self.assertFalse(read_activity(self.state)["running"])

    def args(self, *extra):
        parser = argparse.ArgumentParser()
        add_paper_parser(parser.add_subparsers(dest="command"))
        return parser.parse_args(
            [
                "paper",
                "run",
                "--state",
                str(self.state),
                "--output-dir",
                str(self.state.parent / "reports"),
                *extra,
            ]
        )

    def test_cli_exposes_model_wait_and_recovery_without_changing_trading(self):
        PaperStore(self.state).initialize(market="BTC-EUR", market_source="mock")
        phases = []

        class FlakyMarket(MockMarketData):
            calls = 0

            def get_quote(inner, market):
                phases.append(read_activity(self.state)["phase"])
                inner.calls += 1
                if inner.calls == 1:
                    raise MarketDataError("temporary outage")
                return super().get_quote(market)

        class ObservedDecider(ReferenceDecisionAgent):
            def decide(inner, context):
                phases.append(read_activity(self.state)["phase"])
                return super().decide(context)

        def observe_wait(seconds):
            snapshot = read_activity(self.state)
            phases.append(snapshot["phase"])
            self.assertEqual(snapshot["last_error"], "temporary outage")
            self.assertIsNotNone(snapshot["next_run_at"])

        with redirect_stdout(StringIO()):
            code = run_paper(
                self.args("--runs", "2", "--interval-seconds", "0"),
                market_factory=lambda p: FlakyMarket(),
                decision_factory=ObservedDecider,
                sleep_func=observe_wait,
            )
        self.assertEqual(code, 1)
        self.assertEqual(phases, ["market", "waiting", "market", "deciding"])
        snapshot = read_activity(self.state)
        self.assertEqual(snapshot["phase"], "stopped")
        self.assertIsNone(snapshot["last_error"])
        self.assertEqual(snapshot["cycle"], 2)
        self.assertEqual(len(PaperStore(self.state).load().ledger), 1)

    def test_cli_interrupt_marks_stopped_and_preserves_committed_cycle(self):
        PaperStore(self.state).initialize(market="BTC-EUR", market_source="mock")
        with redirect_stdout(StringIO()):
            code = run_paper(
                self.args("--runs", "0", "--interval-seconds", "1"),
                sleep_func=lambda seconds: (_ for _ in ()).throw(KeyboardInterrupt()),
            )
        self.assertEqual(code, 0)
        self.assertEqual(read_activity(self.state)["phase"], "stopped")
        self.assertEqual(len(PaperStore(self.state).load().ledger), 1)

    def test_cli_rejects_competing_runner_before_waiting_on_portfolio(self):
        PaperStore(self.state).initialize(market="BTC-EUR", market_source="mock")
        with ActivitySession(self.state, provider="rules"):
            before = activity_path(self.state).read_bytes()
            with patch.object(PaperStore, "load", side_effect=AssertionError("must not load")):
                with redirect_stdout(StringIO()):
                    start = time.monotonic()
                    code = run_paper(self.args())
            self.assertLess(time.monotonic() - start, 1)
            self.assertEqual(code, 1)
            self.assertEqual(activity_path(self.state).read_bytes(), before)


if __name__ == "__main__":
    unittest.main()
