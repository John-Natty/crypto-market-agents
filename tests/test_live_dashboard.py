"""Exercise live observation through real HTTP while the simulation commits."""

from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from http.client import HTTPConnection
from http.server import ThreadingHTTPServer
import json
from pathlib import Path
import sys
import tempfile
from threading import Event, Thread
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from crypto_market_agents.cli import main
from crypto_market_agents.dashboard import make_dashboard_handler
from crypto_market_agents.trading.engine import PaperEngine
from crypto_market_agents.trading.live import live_snapshot
from crypto_market_agents.trading.models import Decision, Quote
from crypto_market_agents.trading.store import PaperStore


@contextmanager
def running_server(reports, state=None):
    server = ThreadingHTTPServer(("127.0.0.1", 0), make_dashboard_handler(reports, state=state))
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield server.server_address[1]
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


def get(port, path):
    connection = HTTPConnection("127.0.0.1", port, timeout=2)
    try:
        connection.request("GET", path)
        response = connection.getresponse()
        return response.status, dict(response.getheaders()), response.read().decode()
    finally:
        connection.close()


class LiveDashboardTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.state = self.root / "portfolio.json"
        self.store = PaperStore(self.state)
        self.store.initialize(market="BTC-EUR", market_source="mock")

    def record(self, portfolio, identifier="first", reason="Observation"):
        now = datetime.now(timezone.utc)
        return PaperEngine().step(
            portfolio,
            Decision("buy", "BTC-EUR", Decimal("10"), reason, "rules"),
            Quote("BTC-EUR", Decimal("100"), Decimal("100.05"), now),
            identifier,
            now=now,
        )

    def test_api_updates_after_commit_without_any_external_call(self):
        original = self.state.read_bytes()
        with (
            running_server(self.root, self.state) as port,
            patch("urllib.request.OpenerDirector.open", side_effect=AssertionError("network")),
        ):
            status, headers, body = get(port, "/api/paper")
            self.assertEqual(status, 200)
            self.assertEqual(headers["Cache-Control"], "no-store")
            initial = json.loads(body)
            self.assertEqual(initial["metrics"]["cash_eur"], "50")
            self.assertEqual(initial["metrics"]["decisions"], 0)
            self.assertEqual(initial["market_history"], [])
            self.assertFalse(initial["runtime"]["available"])
            self.assertTrue(initial["freshness"]["stale"])
            self.assertEqual(self.state.read_bytes(), original)
            with self.store.transaction() as portfolio:
                self.record(portfolio, reason="Texte <script>alert(1)</script> api_key=test-secret")
            status, _, body = get(port, "/api/paper?state=/etc/passwd")
            updated = json.loads(body)
            self.assertEqual(status, 200)
            self.assertEqual(updated["metrics"]["decisions"], 1)
            self.assertEqual(updated["metrics"]["fills"], 1)
            self.assertLess(Decimal(updated["metrics"]["cash_eur"]), 50)
            self.assertEqual(len(updated["history"]), 2)
            self.assertEqual(len(updated["market_history"]), 1)
            self.assertEqual(updated["market_history"][0]["bid_eur"], "100")
            self.assertEqual(
                updated["market_history"][0]["timestamp"],
                updated["metrics"]["last_observed_at"],
            )
            self.assertNotEqual(
                updated["market_history"][0]["bid_eur"],
                updated["history"][-1]["equity_eur"],
            )
            self.assertFalse(updated["freshness"]["stale"])
            self.assertNotIn("test-secret", body)
            self.assertIn("[REDACTED]", body)
            self.assertNotIn(str(self.root), body)

    def test_observer_reads_previous_commit_during_long_model_transaction(self):
        started, release = Event(), Event()

        def writer():
            with self.store.transaction() as portfolio:
                self.record(portfolio)
                started.set()
                release.wait(timeout=10)

        with running_server(self.root, self.state) as port:
            thread = Thread(target=writer, daemon=True)
            thread.start()
            try:
                self.assertTrue(started.wait(timeout=2))
                status, _, body = get(port, "/api/paper")
                self.assertEqual(status, 200)
                self.assertEqual(json.loads(body)["metrics"]["decisions"], 0)
            finally:
                release.set()
                thread.join(timeout=2)
            _, _, body = get(port, "/api/paper")
            self.assertEqual(json.loads(body)["metrics"]["decisions"], 1)

    def test_missing_corrupt_and_symlink_states_are_visible_errors_without_reset(self):
        with running_server(self.root, self.state) as port:
            self.state.unlink()
            self.assertEqual(get(port, "/api/paper")[0], 404)
            self.assertFalse(self.state.exists())
            self.state.write_text("broken api_key=secret-value")
            status, _, body = get(port, "/api/paper")
            self.assertEqual(status, 422)
            self.assertNotIn("secret-value", body)
            self.assertEqual(self.state.read_text(), "broken api_key=secret-value")
            self.state.unlink()
            other = self.root / "outside.json"
            PaperStore(other).initialize(market="BTC-EUR", market_source="mock")
            self.state.symlink_to(other)
            self.assertEqual(get(port, "/api/paper")[0], 422)

    def test_io_failure_returns_service_unavailable_and_recovers(self):
        with running_server(self.root, self.state) as port:
            with patch.object(PaperStore, "read_snapshot", side_effect=PermissionError("private")):
                status, _, body = get(port, "/api/paper")
                self.assertEqual(status, 503)
                self.assertNotIn("private", body)
            self.assertEqual(get(port, "/api/paper")[0], 200)

    def test_live_routes_reports_and_read_only_http(self):
        with running_server(self.root, self.state) as port:
            for path in ("/", "/live"):
                status, _, body = get(port, path)
                self.assertEqual(status, 200)
                self.assertIn("/api/paper", body)
                self.assertIn("Simulation — aucun argent réel", body)
                self.assertIn('lang="fr"', body)
            status, _, body = get(port, "/reports")
            self.assertEqual(status, 200)
            self.assertIn("Suivi en direct", body)
            self.assertEqual(get(port, "/file?file=../portfolio.json")[0], 403)
            connection = HTTPConnection("127.0.0.1", port, timeout=2)
            connection.request("POST", "/api/paper", body='{"action":"buy"}')
            self.assertEqual(connection.getresponse().status, 501)
            connection.close()
            self.assertEqual(self.store.load().ledger, [])

    def test_default_dashboard_keeps_existing_report_home(self):
        with running_server(self.root) as port:
            body = get(port, "/")[2]
            self.assertIn("Crypto Market Agents Dashboard", body)
            self.assertIn("Simulation — aucun argent réel", body)
            self.assertEqual(get(port, "/api/paper")[0], 404)

    def test_old_quote_is_not_presented_as_current(self):
        with self.store.transaction() as portfolio:
            self.record(portfolio)
        snapshot = live_snapshot(self.state, now=datetime.now(timezone.utc) + timedelta(minutes=5))
        self.assertTrue(snapshot["freshness"]["stale"])
        self.assertGreater(snapshot["freshness"]["age_seconds"], 120)
        self.assertEqual(snapshot["metrics"]["decisions"], 1)

    def test_dashboard_cli_selects_one_portfolio(self):
        calls = []
        self.assertEqual(
            main(
                ["dashboard", "--state", str(self.state)],
                dashboard_runner=lambda **kwargs: calls.append(kwargs),
            ),
            0,
        )
        self.assertEqual(calls[0]["state"], str(self.state))


if __name__ == "__main__":
    unittest.main()
