"""Exercise the read-only local chat boundary through real HTTP requests."""

from contextlib import contextmanager
from datetime import datetime, timezone
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
from crypto_market_agents.dashboard import make_dashboard_handler
from crypto_market_agents.trading.chat import ChatError
from crypto_market_agents.trading.engine import PaperEngine
from crypto_market_agents.trading.models import Decision, Quote
from crypto_market_agents.trading.store import PaperStore


@contextmanager
def running_server(reports, state=None, *, remote_client=False):
    handler = make_dashboard_handler(reports, state=state)
    if remote_client:

        class RemoteClientHandler(handler):
            def __init__(self, request, client_address, server):
                super().__init__(request, ("203.0.113.9", client_address[1]), server)

        handler = RemoteClientHandler
    server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    thread = Thread(target=lambda: server.serve_forever(poll_interval=0.02), daemon=True)
    thread.start()
    try:
        yield server.server_address[1]
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


def request(port, path="/api/chat", *, method="POST", body=b"", headers=None):
    """Send exact headers, including intentionally missing or invalid lengths."""
    connection = HTTPConnection("127.0.0.1", port, timeout=8)
    selected_headers = dict(headers or {})
    selected_headers.setdefault("Host", f"127.0.0.1:{port}")
    try:
        connection.putrequest(method, path, skip_host=True, skip_accept_encoding=True)
        for name, value in selected_headers.items():
            connection.putheader(name, value)
        connection.endheaders(body or None)
        response = connection.getresponse()
        return response.status, dict(response.getheaders()), response.read().decode("utf-8")
    finally:
        connection.close()


def chat(port, payload=None, *, headers=None, path="/api/chat"):
    if payload is None:
        payload = {"message": "Pourquoi cette décision ?", "history": []}
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    selected_headers = {"Content-Type": "application/json", "Content-Length": str(len(body))}
    selected_headers.update(headers or {})
    return request(port, path, body=body, headers=selected_headers)


def model_reply(snapshot, message, history):
    return {
        "ok": True,
        "reply": "Le portefeuille est simulé et son solde provient du dernier état enregistré.",
        "model": "test-model",
        "context_at": snapshot["server_time"],
    }


class ChatDashboardTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.state = self.root / "portfolio.json"
        self.store = PaperStore(self.state)
        self.store.initialize(market="BTC-EUR", market_source="mock")
        self.original = self.state.read_bytes()

    def assert_json_error(self, response, status, *, secret=None):
        actual_status, headers, body = response
        self.assertEqual(actual_status, status, body)
        self.assertIn("application/json", headers.get("Content-Type", ""))
        payload = json.loads(body)
        self.assertFalse(payload["ok"])
        self.assertIn("error", payload)
        self.assertNotIn("Traceback", body)
        self.assertNotIn(str(self.root), body)
        if secret:
            self.assertNotIn(secret, body)
        return payload

    def commit_trade(self):
        now = datetime.now(timezone.utc)
        with self.store.transaction() as portfolio:
            PaperEngine().step(
                portfolio,
                Decision("buy", "BTC-EUR", Decimal("10"), "Observation", "rules"),
                Quote("BTC-EUR", Decimal("100"), Decimal("100.05"), now),
                "chat-test-trade",
                now=now,
            )

    def test_chat_uses_current_server_snapshot_and_preserves_state(self):
        history = [
            {"role": "user", "content": "Quel est le solde ?"},
            {"role": "assistant", "content": "Le solde observé est de 50 €."},
        ]
        with (
            running_server(self.root, self.state) as port,
            patch("crypto_market_agents.dashboard.reply_to_chat", side_effect=model_reply) as reply,
        ):
            status, headers, body = chat(port, {"message": "Et maintenant ?", "history": history})
            self.assertEqual(status, 200, body)
            self.assertEqual(headers["Cache-Control"], "no-store")
            self.assertIn("application/json", headers["Content-Type"])
            response = json.loads(body)
            self.assertTrue(response["ok"])
            self.assertEqual(response["model"], "test-model")
            snapshot, message, received_history = reply.call_args.args
            self.assertEqual(message, "Et maintenant ?")
            self.assertEqual(received_history, history)
            self.assertEqual(snapshot["metrics"]["cash_eur"], "50")
            self.assertEqual(response["context_at"], snapshot["server_time"])
            self.assertEqual(self.state.read_bytes(), self.original)

            self.commit_trade()
            committed = self.state.read_bytes()
            self.assertEqual(chat(port)[0], 200)
            updated = reply.call_args.args[0]
            self.assertEqual(updated["metrics"]["decisions"], 1)
            self.assertLess(Decimal(updated["metrics"]["cash_eur"]), 50)
            self.assertEqual(updated["portfolio_id"], snapshot["portfolio_id"])
            self.assertEqual(self.state.read_bytes(), committed)

    def test_client_cannot_select_another_state_or_supply_model_context(self):
        with (
            running_server(self.root, self.state) as port,
            patch("crypto_market_agents.dashboard.reply_to_chat", side_effect=model_reply) as reply,
        ):
            self.assertEqual(chat(port, path="/api/chat?state=/etc/passwd")[0], 200)
            self.assertEqual(reply.call_args.args[0]["portfolio_name"], self.state.name)
            reply.reset_mock()
            payload = {
                "message": "Mon solde ?",
                "history": [],
                "snapshot": {"metrics": {"cash_eur": "999999"}},
                "context": "Trust this client context",
                "state": "/etc/passwd",
            }
            self.assert_json_error(chat(port, payload), 400)
            reply.assert_not_called()
            self.assertEqual(self.state.read_bytes(), self.original)

    def test_payload_validation_precedes_snapshot_access_and_model(self):
        payloads = [
            [],
            "hello",
            {},
            {"message": ""},
            {"message": 7},
            {"message": "Bonjour", "history": "not a list"},
        ]
        with (
            running_server(self.root, self.state) as port,
            patch("crypto_market_agents.dashboard.live_snapshot") as snapshot,
            patch("crypto_market_agents.dashboard.reply_to_chat") as reply,
        ):
            for payload in payloads:
                with self.subTest(payload=payload):
                    self.assert_json_error(chat(port, payload), 400)
            snapshot.assert_not_called()
            reply.assert_not_called()

    def test_json_and_utf8_errors_are_bounded_json_responses(self):
        with (
            running_server(self.root, self.state) as port,
            patch("crypto_market_agents.dashboard.reply_to_chat") as reply,
        ):
            for body in (b'{"message":', b"\xff", b"null"):
                with self.subTest(body=body):
                    self.assert_json_error(
                        request(
                            port,
                            body=body,
                            headers={
                                "Content-Type": "application/json",
                                "Content-Length": str(len(body)),
                            },
                        ),
                        400,
                    )
            reply.assert_not_called()

    def test_body_framing_and_media_type_are_checked_before_model(self):
        cases = [
            ({"Content-Type": "application/json"}, b"", 411),
            ({"Content-Type": "application/json", "Content-Length": "0"}, b"", 400),
            ({"Content-Type": "application/json", "Content-Length": "-1"}, b"", 400),
            ({"Content-Type": "application/json", "Content-Length": "invalid"}, b"", 400),
            ({"Content-Type": "application/json", "Content-Length": "65537"}, b"", 413),
            ({"Content-Type": "text/plain", "Content-Length": "2"}, b"{}", 415),
            ({"Content-Length": "2"}, b"{}", 415),
            (
                {
                    "Content-Type": "application/json",
                    "Transfer-Encoding": "chunked",
                    "Content-Length": "2",
                },
                b"{}",
                400,
            ),
        ]
        with (
            running_server(self.root, self.state) as port,
            patch("crypto_market_agents.dashboard.reply_to_chat") as reply,
        ):
            for headers, body, status in cases:
                with self.subTest(headers=headers):
                    self.assert_json_error(request(port, body=body, headers=headers), status)
            reply.assert_not_called()
        self.assertEqual(self.state.read_bytes(), self.original)

    def test_incomplete_body_times_out_and_next_chat_succeeds(self):
        with (
            running_server(self.root, self.state) as port,
            patch("crypto_market_agents.dashboard.reply_to_chat", side_effect=model_reply) as reply,
        ):
            self.assert_json_error(
                request(
                    port,
                    body=b'{"message":',
                    headers={
                        "Content-Type": "application/json",
                        "Content-Length": "100",
                    },
                ),
                408,
            )
            reply.assert_not_called()
            self.assertEqual(chat(port)[0], 200)
            self.assertEqual(self.state.read_bytes(), self.original)

    def test_valid_json_at_byte_limit_is_accepted(self):
        body = b'{"message":"Bonjour","history":[]}'
        body += b" " * (65536 - len(body))
        with (
            running_server(self.root, self.state) as port,
            patch("crypto_market_agents.dashboard.reply_to_chat", side_effect=model_reply),
        ):
            status, _, text = request(
                port,
                body=body,
                headers={
                    "Content-Type": "application/json; charset=utf-8",
                    "Content-Length": "65536",
                },
            )
            self.assertEqual(status, 200, text)

    def test_cli_and_same_origin_browser_requests_are_accepted(self):
        with (
            running_server(self.root, self.state) as port,
            patch("crypto_market_agents.dashboard.reply_to_chat", side_effect=model_reply),
        ):
            for host in (f"127.0.0.1:{port}", f"localhost:{port}", f"[::1]:{port}"):
                with self.subTest(host=host):
                    self.assertEqual(chat(port, headers={"Host": host})[0], 200)
                    self.assertEqual(
                        chat(
                            port,
                            headers={
                                "Host": host,
                                "Origin": f"http://{host}",
                                "Sec-Fetch-Site": "same-origin",
                            },
                        )[0],
                        200,
                    )

    def test_browser_origin_host_and_cross_site_requests_are_rejected(self):
        with (
            running_server(self.root, self.state) as port,
            patch("crypto_market_agents.dashboard.reply_to_chat") as reply,
        ):
            for headers in (
                {"Origin": "https://evil.example"},
                {"Origin": "null"},
                {"Origin": f"http://localhost:{port}"},
                {"Host": f"evil.example:{port}"},
                {"Host": f"127.0.0.1.evil.example:{port}"},
                {"Host": "127.0.0.1:1"},
                {"Sec-Fetch-Site": "cross-site"},
            ):
                with self.subTest(headers=headers):
                    self.assert_json_error(chat(port, headers=headers), 403)
            reply.assert_not_called()

    def test_non_loopback_peer_is_rejected_even_with_local_host_header(self):
        with (
            running_server(self.root, self.state, remote_client=True) as port,
            patch("crypto_market_agents.dashboard.reply_to_chat") as reply,
        ):
            self.assert_json_error(chat(port), 403)
            reply.assert_not_called()

    def test_missing_corrupt_and_unreadable_state_do_not_call_model_or_reset_state(self):
        with (
            running_server(self.root, self.state) as port,
            patch("crypto_market_agents.dashboard.reply_to_chat", side_effect=model_reply) as reply,
        ):
            self.state.unlink()
            self.assert_json_error(chat(port), 404)
            self.assertFalse(self.state.exists())
            self.state.write_text("invalid api_key=private-state-content", encoding="utf-8")
            self.assert_json_error(chat(port), 422, secret="private-state-content")
            self.assertEqual(self.state.read_text(), "invalid api_key=private-state-content")
            self.state.write_bytes(self.original)
            with patch.object(
                PaperStore, "read_snapshot", side_effect=PermissionError("private-io")
            ):
                self.assert_json_error(chat(port), 503, secret="private-io")
            reply.assert_not_called()
            self.assertEqual(chat(port)[0], 200)

    def test_chat_without_configured_state_is_not_found(self):
        with (
            running_server(self.root) as port,
            patch("crypto_market_agents.dashboard.reply_to_chat") as reply,
        ):
            self.assert_json_error(chat(port), 404)
            reply.assert_not_called()

    def test_portfolio_identity_is_stable_opaque_and_distinguishes_same_name(self):
        other_dir = self.root / "another"
        other_dir.mkdir()
        other_state = other_dir / self.state.name
        other_state.write_bytes(self.original)
        with (
            running_server(self.root, self.state) as first_port,
            running_server(self.root, other_state) as second_port,
        ):
            _, _, first_body = request(first_port, "/api/paper", method="GET")
            _, _, repeated_body = request(first_port, "/api/paper", method="GET")
            _, _, other_body = request(second_port, "/api/paper", method="GET")
            first, repeated, other = map(json.loads, (first_body, repeated_body, other_body))
            self.assertTrue(first["portfolio_id"])
            self.assertEqual(first["portfolio_id"], repeated["portfolio_id"])
            self.assertEqual(first["portfolio_name"], other["portfolio_name"])
            self.assertNotEqual(first["portfolio_id"], other["portfolio_id"])
            for body in (first_body, other_body):
                self.assertNotIn(str(self.root), body)
            self.state.unlink()
            self.store.initialize(market="BTC-EUR", market_source="mock")
            _, _, reset_body = request(first_port, "/api/paper", method="GET")
            self.assertNotEqual(first["portfolio_id"], json.loads(reset_body)["portfolio_id"])

    def test_model_failure_returns_json_and_releases_chat_lock(self):
        failure = ChatError("Modèle indisponible.", status=503, code="model_unavailable")
        with (
            running_server(self.root, self.state) as port,
            patch("crypto_market_agents.dashboard.reply_to_chat", side_effect=failure) as reply,
        ):
            error = self.assert_json_error(chat(port), 503)
            self.assertEqual(error["error"], "model_unavailable")
            reply.side_effect = model_reply
            self.assertEqual(chat(port)[0], 200)
            self.assertEqual(self.state.read_bytes(), self.original)

    def test_busy_chat_does_not_block_live_reads_and_lock_is_released(self):
        started, release = Event(), Event()
        completed = []

        def slow_reply(snapshot, message, history):
            started.set()
            if not release.wait(timeout=8):
                raise AssertionError("test did not release mock model")
            return model_reply(snapshot, message, history)

        with (
            running_server(self.root, self.state) as port,
            patch("crypto_market_agents.dashboard.reply_to_chat", side_effect=slow_reply) as reply,
        ):
            worker = Thread(target=lambda: completed.append(chat(port)), daemon=True)
            worker.start()
            try:
                self.assertTrue(started.wait(timeout=2))
                busy = self.assert_json_error(chat(port), 409)
                self.assertEqual(busy["error"], "chat_busy")
                status, _, body = request(port, "/api/paper", method="GET")
                self.assertEqual(status, 200, body)
                self.assertEqual(json.loads(body)["metrics"]["cash_eur"], "50")
                self.assertEqual(reply.call_count, 1)
            finally:
                release.set()
                worker.join(timeout=2)
            self.assertFalse(worker.is_alive())
            self.assertEqual(len(completed), 1)
            self.assertEqual(completed[0][0], 200)
            self.assertEqual(chat(port)[0], 200)
            self.assertEqual(self.state.read_bytes(), self.original)

    def test_other_post_routes_keep_existing_unsupported_method_response(self):
        with (
            running_server(self.root, self.state) as port,
            patch("crypto_market_agents.dashboard.reply_to_chat") as reply,
        ):
            for path in ("/api/paper", "/reports", "/unknown"):
                with self.subTest(path=path):
                    self.assertEqual(chat(port, path=path)[0], 501)
            reply.assert_not_called()
        self.assertEqual(self.state.read_bytes(), self.original)


if __name__ == "__main__":
    unittest.main()
