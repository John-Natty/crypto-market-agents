from datetime import datetime, timedelta, timezone
from decimal import Decimal
import hashlib
from http.client import HTTPConnection
from http.server import ThreadingHTTPServer
import json
from pathlib import Path
import tempfile
from threading import Thread
import unittest

from crypto_market_agents.dashboard import make_dashboard_handler
from crypto_market_agents.trading.automatic_cycle import AUTO_SCHEMA, _cycle_lock
from crypto_market_agents.trading.multi_asset import (
    MultiAssetOrder,
    MultiAssetPaperEngine,
    MultiAssetPaperStore,
)
from crypto_market_agents.trading.multi_asset_dashboard import (
    cycle_report_bytes,
    is_multi_asset_state,
    multi_asset_dashboard_snapshot,
    render_multi_asset_dashboard,
)
from crypto_market_agents.trading.models import Quote

NOW = datetime(2026, 11, 10, 12, tzinfo=timezone.utc)


def _write(path: Path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _cycle(
    reports: Path,
    cycle_id: str,
    *,
    status="plans_rejected",
    stages=None,
    cycle_type="full_discovery",
):
    directory = reports / "automatic-cycles" / cycle_id
    _write(
        directory / "cycle.json",
        {
            "schema_version": AUTO_SCHEMA,
            "cycle_id": cycle_id,
            "created_at": NOW.isoformat(),
            "status": status,
            "cycle_type": cycle_type,
            "stop_reason": status if status != "paper_executed" else None,
            "model": "qwen3:8b",
            "elapsed_seconds": 24,
            "usage": {
                "llm_calls": 4,
                "generation_tokens": 800,
                "http_requests": 3,
            },
            "stages": stages or {},
        },
    )
    return directory


def _record(directory, stage, data, *, status="completed", reason=None):
    filename = {
        "research": "research.json",
        "review": "review.json",
        "proposal": "proposal.json",
        "execution": "execution.json",
    }[stage]
    digest = _write(directory / filename, data)
    return {
        "status": status,
        "artifact": filename,
        "sha256": digest,
        "started_at": NOW.isoformat(),
        "duration_seconds": 4,
        "http_requests_used": 1,
        "usage": {"llm_calls": 1, "generation_tokens": 200},
        "outcome": {"reason": reason} if reason else {},
    }


class MultiAssetDashboardTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.state_path = self.root / "portfolio.json"
        self.reports = self.root / "reports"
        self.store = MultiAssetPaperStore(self.state_path)
        self.store.initialize()

    def test_empty_portfolio_and_empty_cycle_history(self):
        snapshot = multi_asset_dashboard_snapshot(self.state_path, self.reports, now=NOW)
        self.assertTrue(snapshot["ok"])
        self.assertEqual(snapshot["portfolio"]["cash_eur"], "150")
        self.assertTrue(snapshot["portfolio"]["valuation"]["complete"])
        self.assertEqual(snapshot["cycles"], [])
        self.assertEqual(snapshot["reports_state"], "not_found")
        self.assertEqual(snapshot["external_requests"], 0)
        self.assertEqual(snapshot["llm_calls"], 0)
        (self.reports / "automatic-cycles").mkdir(parents=True)
        empty_reports = multi_asset_dashboard_snapshot(self.state_path, self.reports, now=NOW)
        self.assertEqual(empty_reports["reports_state"], "empty")

    def test_rejected_cycle_shows_pipeline_reasons_and_verified_references(self):
        cycle_id = "a" * 32
        directory = _cycle(self.reports, cycle_id)
        stages = {}
        stages["research"] = _record(
            directory,
            "research",
            {
                "schema_version": "autonomous-research.v6",
                "status": "partial",
                "stop_reason": "generation_budget_exhausted_during_research_turn",
                "generated_at": NOW.isoformat(),
                "events": [
                    {
                        "kind": "ollama_turn",
                        "done_reason": "length",
                        "generation": {"eval_count": 1200},
                    }
                ],
                "conclusion": {
                    "candidates": [
                        {
                            "fact_refs": ["history:BTC-EUR:returns_pct.24h"],
                            "missing_refs": ["history:BTC-EUR:sma50_contiguous"],
                        }
                    ]
                },
            },
            reason="generation_budget_exhausted_during_research_turn",
        )
        stages["review"] = _record(
            directory,
            "review",
            {
                "schema_version": "research-contradiction-review.v3",
                "status": "partial",
                "review": {
                    "candidates": [
                        {
                            "favorable_arguments": [
                                {"reference": "history:BTC-EUR:returns_pct.24h"}
                            ],
                            "rejected_invalidation_conditions": [
                                {
                                    "reference": "history:BTC-EUR:returns_pct.30d",
                                    "reason": "review_condition_not_a_source_claim",
                                }
                            ],
                        }
                    ]
                },
            },
        )
        stages["proposal"] = _record(
            directory,
            "proposal",
            {
                "schema_version": "paper-proposal-cycle.v2",
                "status": "completed",
                "decision_status": "plans_rejected",
                "proposals": [
                    {
                        "market": "BTC-EUR",
                        "status": "rejected",
                        "reasons": ["new_entry_review_not_supportive"],
                        "references": ["history:BTC-EUR:returns_pct.24h"],
                    }
                ],
            },
        )
        _cycle(self.reports, cycle_id, stages=stages)

        snapshot = multi_asset_dashboard_snapshot(self.state_path, self.reports, now=NOW)
        cycle = snapshot["latest_cycle"]
        self.assertEqual(cycle["status"], "plans_rejected")
        by_stage = {row["stage"]: row for row in cycle["stages"]}
        self.assertEqual(by_stage["research"]["report_status"], "partial")
        self.assertTrue(by_stage["research"]["truncated_output"])
        self.assertIn("generation_budget_exhausted", by_stage["research"]["reason"])
        self.assertIn("new_entry_review_not_supportive", by_stage["proposal"]["reason"])
        self.assertEqual(by_stage["execution"]["status"], "not_started")
        self.assertIn("history:BTC-EUR:returns_pct.24h", cycle["proof_refs"])
        self.assertIn("history:BTC-EUR:sma50_contiguous", cycle["missing_refs"])
        self.assertIn("review_condition_not_a_source_claim", by_stage["review"]["reason"])

    def test_corrupt_cycle_report_is_visible_without_executing_it(self):
        cycle_id = "b" * 32
        directory = _cycle(self.reports, cycle_id, status="technical_error")
        (directory / "research.json").write_text('{"status":', encoding="utf-8")
        _write(
            directory / "cycle.json",
            {
                "schema_version": AUTO_SCHEMA,
                "cycle_id": cycle_id,
                "created_at": NOW.isoformat(),
                "status": "technical_error",
                "model": "qwen3:8b",
                "elapsed_seconds": 2,
                "usage": {},
                "stages": {
                    "research": {
                        "status": "technical_error",
                        "artifact": "research.json",
                        "sha256": "0" * 64,
                        "details": {"reason": "research_artifact_invalid"},
                    }
                },
            },
        )
        snapshot = multi_asset_dashboard_snapshot(self.state_path, self.reports, now=NOW)
        row = snapshot["latest_cycle"]["stages"][0]
        self.assertEqual(snapshot["latest_cycle"]["status"], "technical_error")
        self.assertEqual(row["integrity"], "hash_mismatch")
        self.assertIn("recherche", render_multi_asset_dashboard().lower())

    def test_running_cycle_and_incomplete_artifact_are_not_reported_as_stopped(self):
        cycle_id = "f" * 32
        directory = _cycle(self.reports, cycle_id, status="running")
        (directory / "research.json").write_text('{"status":"partial"', encoding="utf-8")
        _write(
            directory / "cycle.json",
            {
                "schema_version": AUTO_SCHEMA,
                "cycle_id": cycle_id,
                "created_at": NOW.isoformat(),
                "status": "running",
                "model": "qwen3:8b",
                "usage": {},
                "stages": {
                    "research": {
                        "status": "running",
                        "artifact": "research.json",
                        "started_at": NOW.isoformat(),
                    }
                },
            },
        )
        snapshot = multi_asset_dashboard_snapshot(self.state_path, self.reports, now=NOW)
        research = snapshot["latest_cycle"]["stages"][0]
        self.assertEqual(snapshot["latest_cycle"]["status"], "running")
        self.assertEqual(research["status"], "running")
        self.assertEqual(research["integrity"], "in_progress_unverified")
        self.assertNotIn(
            "research", [link["stage"] for link in snapshot["latest_cycle"]["report_links"]]
        )
        self.assertEqual(snapshot["activity"]["status"], "unconfirmed")

    def test_corrupt_multi_asset_state_is_preserved_and_visible(self):
        original = b'{"format":"paper-multiasset","format_version":2,'
        self.state_path.write_bytes(original)
        self.assertTrue(is_multi_asset_state(self.state_path))
        snapshot = multi_asset_dashboard_snapshot(self.state_path, self.reports, now=NOW)
        self.assertFalse(snapshot["ok"])
        self.assertEqual(snapshot["error"], "state_invalid")
        self.assertEqual(self.state_path.read_bytes(), original)

    def test_stale_position_is_incomplete_and_never_substituted_with_zero(self):
        old = NOW - timedelta(minutes=10)
        with self.store.transaction() as portfolio:
            MultiAssetPaperEngine().execute(
                portfolio,
                MultiAssetOrder("buy", "mock", "BTC-EUR", "test", amount_eur=Decimal("30")),
                Quote("BTC-EUR", Decimal("100"), Decimal("100.1"), old),
                "old-quote-buy",
                now=old,
            )
        snapshot = multi_asset_dashboard_snapshot(self.state_path, self.reports, now=NOW)
        valuation = snapshot["portfolio"]["valuation"]
        position = valuation["positions"][0]
        self.assertFalse(valuation["complete"])
        self.assertIsNone(valuation["total_value_eur"])
        self.assertIsNone(position["market_value_eur"])
        self.assertFalse(position["quote_fresh"])
        self.assertEqual(position["quote_age_seconds"], 600)
        self.assertIsNotNone(position["last_known_bid_eur"])

    def test_recorded_fictional_execution_is_displayed_without_mutation(self):
        with self.store.transaction() as portfolio:
            MultiAssetPaperEngine().execute(
                portfolio,
                MultiAssetOrder("buy", "mock", "BTC-EUR", "test", amount_eur=Decimal("30")),
                Quote("BTC-EUR", Decimal("100"), Decimal("100.1"), NOW),
                "paper-fill",
                now=NOW,
            )
        before = self.state_path.read_bytes()
        cycle_id = "c" * 32
        directory = _cycle(self.reports, cycle_id, status="paper_executed")
        execution = _record(
            directory,
            "execution",
            {"status": "committed", "transaction_count": 1},
        )
        stages = {"execution": execution}
        _cycle(self.reports, cycle_id, status="paper_executed", stages=stages)

        snapshot = multi_asset_dashboard_snapshot(self.state_path, self.reports, now=NOW)
        self.assertEqual(snapshot["latest_cycle"]["status"], "paper_executed")
        self.assertEqual(snapshot["latest_cycle"]["stages"][3]["report_status"], "committed")
        self.assertEqual(self.state_path.read_bytes(), before)
        self.assertEqual(len(self.store.load().ledger), 1)

    def test_latest_position_follow_up_is_shown_per_position(self):
        cycle_id = "9" * 32
        directory = _cycle(self.reports, cycle_id, status="plans_rejected")
        proposal = _record(
            directory,
            "proposal",
            {
                "schema_version": "paper-proposal-cycle.v3",
                "status": "completed",
                "position_tracking": {
                    "status": "partial",
                    "budget": {"max_positions": 10, "per_position_tokens": 384},
                    "positions": [
                        {
                            "position_key": "mock:ETH-EUR",
                            "market": "ETH-EUR",
                            "status": "incomplete",
                            "suggested_action": "hold",
                            "rationale": "Aucun nouvel élément n'invalide la thèse observée.",
                            "reason": "review_evidence_missing",
                            "entry_thesis_status": "not_found",
                            "source_status": "partial",
                            "references": ["portfolio:mock:ETH-EUR"],
                            "missing_references": ["history:ETH-EUR:activity"],
                        }
                    ],
                },
            },
        )
        _cycle(self.reports, cycle_id, stages={"proposal": proposal})

        snapshot = multi_asset_dashboard_snapshot(self.state_path, self.reports, now=NOW)
        tracking = snapshot["latest_cycle"]["position_tracking"]
        self.assertEqual(tracking["status"], "partial")
        row = tracking["positions"][0]
        self.assertEqual(row["position_key"], "mock:ETH-EUR")
        self.assertEqual(row["suggested_action"], "hold")
        self.assertEqual(row["entry_thesis_status"], "not_found")
        self.assertIsNone(row["entry_thesis_reference"])
        self.assertEqual(row["missing_references"], ["history:ETH-EUR:activity"])
        page = render_multi_asset_dashboard()
        self.assertIn("Suivi des positions", page)
        self.assertIn("Suggestion non validée", page)
        self.assertIn("Cycle source de la thèse", page)

    def test_active_lock_is_confirmed_but_idle_lock_is_not_claimed_stopped(self):
        lock_path = self.state_path.with_name(self.state_path.name + ".automatic-cycle.lock")
        with _cycle_lock(self.state_path):
            snapshot = multi_asset_dashboard_snapshot(self.state_path, self.reports, now=NOW)
            self.assertEqual(snapshot["activity"]["status"], "running")
        snapshot = multi_asset_dashboard_snapshot(self.state_path, self.reports, now=NOW)
        self.assertEqual(snapshot["activity"]["status"], "unconfirmed")
        self.assertTrue(lock_path.is_file())

    def test_cycle_artifact_route_restricts_names_and_escapes_as_json(self):
        cycle_id = "d" * 32
        directory = _cycle(self.reports, cycle_id)
        research = _record(directory, "research", {"summary": "<script>alert(1)</script>"})
        _cycle(self.reports, cycle_id, stages={"research": research})
        body = cycle_report_bytes(self.reports, cycle_id, "research")
        self.assertIn(b"<script>", body)
        with self.assertRaises(FileNotFoundError):
            cycle_report_bytes(self.reports, "../outside", "research")
        with self.assertRaises(FileNotFoundError):
            cycle_report_bytes(self.reports, cycle_id, "../../secret")
        (directory / "research.json").write_text('{"summary":"tampered"}', encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "hash mismatch"):
            cycle_report_bytes(self.reports, cycle_id, "research")
        page = render_multi_asset_dashboard()
        self.assertIn("textContent", page)
        self.assertNotIn("innerHTML", page)
        self.assertNotIn("<button", page)
        self.assertTrue(is_multi_asset_state(self.state_path))

    def test_refresh_preserves_open_cycle_scroll_and_focus_for_three_updates(self):
        page = render_multi_asset_dashboard()
        self.assertIn("setInterval(refresh,5000)", page)
        self.assertIn("querySelectorAll('details.cycle[open]')", page)
        self.assertIn("el.open=openIds.has(el.dataset.cycleId)", page)
        self.assertIn("window.scrollTo(scrollX,scrollY)", page)
        self.assertIn("el.focus({preventScroll:true})", page)
        self.assertIn("summary.dataset.focusKey=(c.cycle_id||'')+':summary'", page)
        self.assertIn("a.dataset.focusKey=(c.cycle_id||'')+':'+s.stage", page)

        # Model the capture/restore contract over consecutive DOM rebuilds.
        open_ids = {"cycle-a"}
        scroll = (0, 760)
        focus = "cycle-a:summary"
        for _ in range(3):
            captured_open_ids = set(open_ids)
            captured_scroll = scroll
            captured_focus = focus
            rendered_ids = {"cycle-a", "cycle-b"}
            open_ids = {cycle_id for cycle_id in rendered_ids if cycle_id in captured_open_ids}
            scroll = captured_scroll
            focus = captured_focus if captured_focus.startswith("cycle-a:") else None
            self.assertIn("cycle-a", open_ids)
            self.assertEqual(scroll, (0, 760))
            self.assertEqual(focus, "cycle-a:summary")

        # A voluntary close removes the cycle from the state restored next time.
        open_ids.clear()
        open_ids = {cycle_id for cycle_id in {"cycle-a", "cycle-b"} if cycle_id in open_ids}
        self.assertNotIn("cycle-a", open_ids)

    def test_local_dashboard_routes_are_read_only_and_render_cycle_artifacts_as_json(self):
        cycle_id = "e" * 32
        directory = _cycle(self.reports, cycle_id, cycle_type="position_tracking_only")
        _record(directory, "research", {"status": "partial", "summary": "<script>text</script>"})
        _cycle(
            self.reports,
            cycle_id,
            cycle_type="position_tracking_only",
            stages={
                "research": {
                    "status": "completed",
                    "artifact": "research.json",
                    "sha256": hashlib.sha256(
                        (directory / "research.json").read_bytes()
                    ).hexdigest(),
                }
            },
        )
        before = self.state_path.read_bytes()
        server = ThreadingHTTPServer(
            ("127.0.0.1", 0),
            make_dashboard_handler(self.reports, state=self.state_path),
        )
        thread = Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            connection = HTTPConnection("127.0.0.1", server.server_port, timeout=3)
            connection.request("GET", "/")
            page = connection.getresponse()
            html_body = page.read().decode()
            self.assertEqual(page.status, 200)
            self.assertIn("Simulation — aucun argent réel", html_body)
            self.assertIn("Cycles automatiques", html_body)
            self.assertIn("Suivi seul", html_body)
            connection.close()

            connection = HTTPConnection("127.0.0.1", server.server_port, timeout=3)
            connection.request("GET", "/api/multi-asset")
            api = connection.getresponse()
            data = json.loads(api.read())
            self.assertEqual(api.status, 200)
            self.assertEqual(data["external_requests"], 0)
            self.assertEqual(data["llm_calls"], 0)
            self.assertEqual(data["cycles"][0]["cycle_type"], "position_tracking_only")
            connection.close()

            connection = HTTPConnection("127.0.0.1", server.server_port, timeout=3)
            connection.request("GET", f"/cycle-file?cycle_id={cycle_id}&stage=research")
            report = connection.getresponse()
            self.assertEqual(report.status, 200)
            self.assertIn("application/json", report.getheader("Content-Type"))
            self.assertEqual(json.loads(report.read())["summary"], "<script>text</script>")
            connection.close()
            self.assertEqual(self.state_path.read_bytes(), before)
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=2)


if __name__ == "__main__":
    unittest.main()
