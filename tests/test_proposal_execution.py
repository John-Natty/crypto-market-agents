from copy import deepcopy
from datetime import datetime, timedelta, timezone
from decimal import Decimal as D
import hashlib
import json
import multiprocessing
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from crypto_market_agents.trading.models import Quote
from crypto_market_agents.trading.multi_asset import (
    MultiAssetOrder,
    MultiAssetPaperEngine,
    MultiAssetPaperStore,
    MultiAssetPortfolio,
)
from crypto_market_agents.trading.proposal_cycle import run_proposal_cycle
from crypto_market_agents.trading.proposal_execution import execute_proposal_cycle
from crypto_market_agents.cli import main

NOW = datetime.now(timezone.utc)


def _fixture(markets=("BTC-EUR",), *, action="buy", portfolio=None, review_verdict="supported"):
    refs = {market: f"scan:{market}:price_change_24h_pct" for market in markets}
    research = {
        "schema_version": "autonomous-research.v6",
        "status": "completed",
        "generated_at": NOW.isoformat(),
        "read_only": True,
        "financial_action_executed": False,
        "conclusion": {"candidates": []},
        "fact_ledger": {},
        "missing_ledger": {},
    }
    review_rows = []
    for market in markets:
        ref = refs[market]
        research["conclusion"]["candidates"].append(
            {
                "market": market,
                "status": "complete",
                "fact_refs": [ref],
                "missing_refs": [],
                "required_missing_refs": [],
            }
        )
        research["fact_ledger"][ref] = {
            "reference": ref,
            "market": market,
            "source": "bitvavo_scanner",
            "collected_at": NOW.isoformat(),
            "value": {
                "value": "2.5",
                "unit": "%/24h",
                "availability": "available",
                "freshness": {"fresh": True},
            },
        }
        review_rows.append(
            {
                "market": market,
                "verdict": review_verdict,
                "favorable_arguments": [{"reference": ref, "role": "favorable"}],
                "unfavorable_arguments": [],
                "uncertain_arguments": [],
                "rejected_arguments": [],
                "rejected_invalidation_conditions": [],
            }
        )
    research_sha = hashlib.sha256(json.dumps(research, sort_keys=True).encode()).hexdigest()
    review = {
        "schema_version": "research-contradiction-review.v3",
        "generated_at": NOW.isoformat(),
        "source_report_sha256": research_sha,
        "status": "completed",
        "read_only": True,
        "financial_action_executed": False,
        "review": {"candidates": review_rows},
    }
    review_sha = hashlib.sha256(json.dumps(review).encode()).hexdigest()

    class Model:
        def chat_with_tools(self, messages, tools, **kwargs):
            target = json.loads(messages[1]["content"])["target_market"]
            ref = refs.get(target)
            if action in {"reduce", "close", "hold"}:
                refs_for_action = [
                    item
                    for item in (ref, f"portfolio:bitvavo|{target}:position")
                    if item is not None
                ]
            else:
                refs_for_action = [ref] if ref and action != "abstain" else []
            payload = {
                "market": target,
                "action": action,
                "references": refs_for_action,
                "rationale": "Proposition étayée par les observations fournies.",
                "horizon": "7 jours" if action == "buy" else "",
                "invalidation_conditions": ["Réexaminer les preuves"] if action == "buy" else [],
            }
            return {
                "message": {"content": json.dumps(payload), "tool_calls": []},
                "done_reason": "stop",
                "think": False,
                "request_bytes": 1000,
                "response_bytes": 400,
                "duration_ms": 20,
                "generation": {"prompt_eval_count": 200, "eval_count": 100},
            }

    class QuoteClient:
        def get_quote(self, market):
            bid = D("110") if action == "reduce" else D("100")
            return Quote(market, bid, bid + D("0.1"), NOW, D("5"), D("0.0001"), D("0"))

    class HistoryClient:
        def get_candles(self, market, *, interval, limit):
            start = NOW - timedelta(hours=801)
            return [
                [
                    int((start + timedelta(hours=index)).timestamp() * 1000),
                    "100",
                    "1000",
                    "1",
                    str(100 + index),
                    "1",
                ]
                for index in range(800)
            ]

    portfolio = portfolio or MultiAssetPortfolio.create()
    plan = run_proposal_cycle(
        research,
        review,
        portfolio,
        Model(),
        research_sha256=research_sha,
        review_sha256=review_sha,
        research_label="research.json",
        review_label="review.json",
        model="qwen3:8b",
        now_provider=lambda: NOW,
        quote_client=QuoteClient(),
        history_client=HistoryClient(),
    )
    return plan, research, review, research_sha, review_sha


def _concurrent_execute(
    state_path, plan, research, review, research_sha, review_sha, barrier, queue
):
    barrier.wait(5)
    result = execute_proposal_cycle(
        plan,
        research,
        review,
        MultiAssetPaperStore(state_path),
        research_sha256=research_sha,
        review_sha256=review_sha,
        now=NOW,
    )
    queue.put(result)


class ProposalExecutionTests(unittest.TestCase):
    def test_accepted_plan_executes_atomically_and_replay_is_idempotent(self):
        with tempfile.TemporaryDirectory() as directory:
            store = MultiAssetPaperStore(Path(directory) / "portfolio.json")
            portfolio = store.initialize()
            plan, research, review, research_sha, review_sha = _fixture(portfolio=portfolio)
            self.assertEqual(plan["proposals"][0]["status"], "accepted")
            result = execute_proposal_cycle(
                plan,
                research,
                review,
                store,
                research_sha256=research_sha,
                review_sha256=review_sha,
                now=NOW,
            )
            self.assertEqual(result["status"], "committed")
            self.assertTrue(result["financial_action_executed"])
            self.assertEqual(result["transaction_count"], 1)
            committed = store.load()
            self.assertEqual(len(committed.ledger), 1)
            self.assertEqual(len(committed.cycle_receipts), 1)
            again = execute_proposal_cycle(
                plan,
                research,
                review,
                store,
                research_sha256=research_sha,
                review_sha256=review_sha,
                now=NOW,
            )
            self.assertTrue(again["replayed"])
            self.assertEqual(again["transactions"], result["transactions"])
            self.assertEqual(len(store.load().ledger), 1)

    def test_two_accepted_buys_share_cash_and_global_exposure(self):
        with tempfile.TemporaryDirectory() as directory:
            store = MultiAssetPaperStore(Path(directory) / "portfolio.json")
            portfolio = store.initialize()
            plan, research, review, research_sha, review_sha = _fixture(
                ("BTC-EUR", "ETH-EUR"), portfolio=portfolio
            )
            self.assertTrue(all(row["status"] == "accepted" for row in plan["proposals"]))
            result = execute_proposal_cycle(
                plan,
                research,
                review,
                store,
                research_sha256=research_sha,
                review_sha256=review_sha,
                now=NOW,
            )
            current = store.load()
            self.assertEqual(result["transaction_count"], 2)
            self.assertGreater(current.cash_eur, D("0"))
            self.assertLess(current.cash_eur, D("150"))
            self.assertLessEqual(
                current.invested_cost_basis_eur, current.config.max_total_exposure_eur
            )
            self.assertEqual(
                [row["market"] for row in result["transactions"]], ["BTC-EUR", "ETH-EUR"]
            )

    def test_partial_reduction_reuses_engine_and_accounts_for_fees(self):
        with tempfile.TemporaryDirectory() as directory:
            store = MultiAssetPaperStore(Path(directory) / "portfolio.json")
            store.initialize()
            with store.transaction() as portfolio:
                seeded = MultiAssetPaperEngine().execute(
                    portfolio,
                    MultiAssetOrder("buy", "bitvavo", "BTC-EUR", "seed", amount_eur=D("60")),
                    Quote("BTC-EUR", D("100"), D("100.1"), NOW, D("5"), D("0.0001")),
                    "seed-buy",
                    now=NOW,
                )
                self.assertEqual(seeded["status"], "executed")
            before = store.load().positions["bitvavo|BTC-EUR"]
            plan, research, review, research_sha, review_sha = _fixture(
                action="reduce", portfolio=store.load()
            )
            result = execute_proposal_cycle(
                plan,
                research,
                review,
                store,
                research_sha256=research_sha,
                review_sha256=review_sha,
                now=NOW,
            )
            after_portfolio = store.load()
            after = after_portfolio.positions["bitvavo|BTC-EUR"]
            self.assertEqual(result["status"], "committed")
            self.assertLess(after.quantity, before.quantity)
            self.assertGreater(after_portfolio.realized_pnl_eur, D("0"))
            self.assertGreater(after_portfolio.fees_paid_eur, D("0"))

    def test_close_executes_full_position_once_with_existing_trade_limit(self):
        with tempfile.TemporaryDirectory() as directory:
            store = MultiAssetPaperStore(Path(directory) / "portfolio.json")
            store.initialize()
            with store.transaction() as portfolio:
                MultiAssetPaperEngine().execute(
                    portfolio,
                    MultiAssetOrder("buy", "bitvavo", "BTC-EUR", "seed", amount_eur=D("50")),
                    Quote("BTC-EUR", D("100"), D("100.1"), NOW, D("5"), D("0.0001")),
                    "seed-buy",
                    now=NOW,
                )
            quantity = store.load().positions["bitvavo|BTC-EUR"].quantity
            plan, research, review, research_sha, review_sha = _fixture(
                action="close", portfolio=store.load()
            )
            self.assertEqual(plan["proposals"][0]["status"], "accepted")
            self.assertEqual(D(plan["proposals"][0]["quantity"]), quantity)
            result = execute_proposal_cycle(
                plan,
                research,
                review,
                store,
                research_sha256=research_sha,
                review_sha256=review_sha,
                now=NOW,
            )
            self.assertEqual(result["status"], "committed")
            self.assertEqual(result["transaction_count"], 1)
            self.assertEqual(result["transactions"][0]["action"], "close")
            self.assertEqual(store.load().positions["bitvavo|BTC-EUR"].quantity, D("0"))
            replay = execute_proposal_cycle(
                plan,
                research,
                review,
                store,
                research_sha256=research_sha,
                review_sha256=review_sha,
                now=NOW,
            )
            self.assertTrue(replay["replayed"])
            self.assertEqual(len(store.load().ledger), 2)  # one seed and one close

    def test_concurrent_launches_commit_once(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "portfolio.json"
            store = MultiAssetPaperStore(path)
            portfolio = store.initialize()
            plan, research, review, research_sha, review_sha = _fixture(portfolio=portfolio)
            context = multiprocessing.get_context("fork")
            queue = context.Queue()
            barrier = context.Barrier(2)
            args = (str(path), plan, research, review, research_sha, review_sha, barrier, queue)
            workers = [context.Process(target=_concurrent_execute, args=args) for _ in range(2)]
            for worker in workers:
                worker.start()
            results = [queue.get(timeout=15) for _ in workers]
            for worker in workers:
                worker.join(15)
                self.assertEqual(worker.exitcode, 0)
            self.assertEqual(sum(row.get("replayed", False) for row in results), 1)
            self.assertEqual(sum(not row.get("replayed", False) for row in results), 1)
            self.assertEqual(len(store.load().ledger), 1)

    def test_retry_after_uncertain_post_replace_commit_returns_receipt(self):
        with tempfile.TemporaryDirectory() as directory:
            store = MultiAssetPaperStore(Path(directory) / "portfolio.json")
            portfolio = store.initialize()
            plan, research, review, research_sha, review_sha = _fixture(portfolio=portfolio)
            write = store._write

            def write_then_lose_ack(state):
                write(state)
                raise OSError("simulated acknowledgement loss after atomic replace")

            store._write = write_then_lose_ack
            with self.assertRaises(OSError):
                execute_proposal_cycle(
                    plan,
                    research,
                    review,
                    store,
                    research_sha256=research_sha,
                    review_sha256=review_sha,
                    now=NOW,
                )
            store._write = write
            replay = execute_proposal_cycle(
                plan,
                research,
                review,
                store,
                research_sha256=research_sha,
                review_sha256=review_sha,
                now=NOW,
            )
            self.assertTrue(replay["replayed"])
            self.assertEqual(len(store.load().ledger), 1)

    def test_portfolio_change_since_plan_rejects_without_execution(self):
        with tempfile.TemporaryDirectory() as directory:
            store = MultiAssetPaperStore(Path(directory) / "portfolio.json")
            store.initialize()
            plan, research, review, research_sha, review_sha = _fixture(portfolio=store.load())
            with store.transaction() as portfolio:
                MultiAssetPaperEngine().execute(
                    portfolio,
                    MultiAssetOrder(
                        "buy", "bitvavo", "ETH-EUR", "manual change", amount_eur=D("20")
                    ),
                    Quote("ETH-EUR", D("100"), D("100.1"), NOW),
                    "manual-change",
                    now=NOW,
                )
            before = store.load().to_dict()
            result = execute_proposal_cycle(
                plan,
                research,
                review,
                store,
                research_sha256=research_sha,
                review_sha256=review_sha,
                now=NOW,
            )
            self.assertEqual(result["reason"], "execution_portfolio_changed_since_proposal")
            self.assertFalse(result["financial_action_executed"])
            self.assertEqual(store.load().to_dict(), before)

    def test_expired_cycle_quote_rejects_plan(self):
        with tempfile.TemporaryDirectory() as directory:
            store = MultiAssetPaperStore(Path(directory) / "portfolio.json")
            portfolio = store.initialize()
            plan, research, review, research_sha, review_sha = _fixture(portfolio=portfolio)
            before = store.load().to_dict()
            result = execute_proposal_cycle(
                plan,
                research,
                review,
                store,
                research_sha256=research_sha,
                review_sha256=review_sha,
                now=NOW + timedelta(seconds=121),
            )
            self.assertEqual(result["reason"], "execution_quote_expired_or_invalid")
            self.assertEqual(store.load().to_dict(), before)

    def test_error_before_commit_leaves_no_partial_operation(self):
        with tempfile.TemporaryDirectory() as directory:
            store = MultiAssetPaperStore(Path(directory) / "portfolio.json")
            portfolio = store.initialize()
            plan, research, review, research_sha, review_sha = _fixture(
                ("BTC-EUR", "ETH-EUR"), portfolio=portfolio
            )
            before = store.path.read_bytes()
            original_execute = MultiAssetPaperEngine.execute
            calls = 0

            def fail_second(self, *args, **kwargs):
                nonlocal calls
                calls += 1
                if calls == 2:
                    return {"status": "rejected", "reason": "injected failure"}
                return original_execute(self, *args, **kwargs)

            with patch.object(MultiAssetPaperEngine, "execute", fail_second):
                result = execute_proposal_cycle(
                    plan,
                    research,
                    review,
                    store,
                    research_sha256=research_sha,
                    review_sha256=review_sha,
                    now=NOW,
                )
            self.assertEqual(result["reason"], "execution_paper_engine_revalidation_failed")
            self.assertEqual(store.path.read_bytes(), before)
            self.assertEqual(store.load().ledger, [])

    def test_same_cycle_id_with_different_content_is_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            store = MultiAssetPaperStore(Path(directory) / "portfolio.json")
            portfolio = store.initialize()
            plan, research, review, research_sha, review_sha = _fixture(portfolio=portfolio)
            first = execute_proposal_cycle(
                plan,
                research,
                review,
                store,
                research_sha256=research_sha,
                review_sha256=review_sha,
                now=NOW,
            )
            changed = deepcopy(plan)
            changed["proposals"][0]["rationale"] = "Contenu changé sous le même cycle"
            replay = execute_proposal_cycle(
                changed,
                research,
                review,
                store,
                research_sha256=research_sha,
                review_sha256=review_sha,
                now=NOW,
            )
            self.assertEqual(first["status"], "committed")
            self.assertEqual(replay["reason"], "execution_cycle_id_content_mismatch")
            self.assertEqual(len(store.load().ledger), 1)

    def test_no_accepted_plan_records_no_transaction(self):
        with tempfile.TemporaryDirectory() as directory:
            store = MultiAssetPaperStore(Path(directory) / "portfolio.json")
            portfolio = store.initialize()
            plan, research, review, research_sha, review_sha = _fixture(
                action="abstain", portfolio=portfolio
            )
            before_cash = store.load().cash_eur
            result = execute_proposal_cycle(
                plan,
                research,
                review,
                store,
                research_sha256=research_sha,
                review_sha256=review_sha,
                now=NOW,
            )
            after = store.load()
            self.assertEqual(result["status"], "no_accepted_operations")
            self.assertFalse(result["financial_action_executed"])
            self.assertEqual(result["transaction_count"], 0)
            self.assertEqual(after.ledger, [])
            self.assertEqual(after.cash_eur, before_cash)
            self.assertEqual(len(after.cycle_receipts), 1)

    def test_insufficient_review_keeps_new_entry_rejected_and_executes_nothing(self):
        with tempfile.TemporaryDirectory() as directory:
            store = MultiAssetPaperStore(Path(directory) / "portfolio.json")
            portfolio = store.initialize()
            plan, research, review, research_sha, review_sha = _fixture(
                portfolio=portfolio, review_verdict="insufficient"
            )
            self.assertEqual(plan["proposals"][0]["status"], "rejected")
            self.assertIn("new_entry_review_not_supportive", plan["proposals"][0]["reasons"])
            result = execute_proposal_cycle(
                plan,
                research,
                review,
                store,
                research_sha256=research_sha,
                review_sha256=review_sha,
                now=NOW,
            )
            self.assertEqual(result["status"], "no_accepted_operations")
            self.assertFalse(result["financial_action_executed"])
            self.assertEqual(store.load().ledger, [])

    def test_v1_upgrade_is_explicit_and_keeps_exact_backup(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "portfolio.json"
            portfolio = MultiAssetPortfolio.create()
            portfolio.format_version = 1
            old_bytes = (json.dumps(portfolio.to_dict(), indent=2) + "\n").encode()
            path.write_bytes(old_bytes)
            store = MultiAssetPaperStore(path)
            self.assertEqual(store.load().format_version, 1)
            upgraded = store.upgrade_for_cycle_execution()
            self.assertEqual(upgraded.format_version, 2)
            self.assertEqual(path.with_name(path.name + ".v1-backup").read_bytes(), old_bytes)
            self.assertEqual(store.load().format_version, 2)

    def test_cli_executes_frozen_report_without_model_or_network_and_writes_separate_report(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            state_path = root / "portfolio.json"
            store = MultiAssetPaperStore(state_path)
            portfolio = store.initialize()
            plan, research, review, research_sha, review_sha = _fixture(portfolio=portfolio)
            research_bytes = json.dumps(research, sort_keys=True).encode()
            review_bytes = json.dumps(review).encode()
            self.assertEqual(hashlib.sha256(research_bytes).hexdigest(), research_sha)
            self.assertEqual(hashlib.sha256(review_bytes).hexdigest(), review_sha)
            plan_path, research_path, review_path, output_path = (
                root / "plan.json",
                root / "research.json",
                root / "review.json",
                root / "execution.json",
            )
            plan_path.write_text(json.dumps(plan), encoding="utf-8")
            research_path.write_bytes(research_bytes)
            review_path.write_bytes(review_bytes)
            result_code = main(
                [
                    "execute-proposal-cycle",
                    "--plan-json",
                    str(plan_path),
                    "--research-json",
                    str(research_path),
                    "--review-json",
                    str(review_path),
                    "--state",
                    str(state_path),
                    "--json",
                    str(output_path),
                ]
            )
            self.assertEqual(result_code, 0)
            execution = json.loads(output_path.read_text())
            self.assertEqual(execution["status"], "committed")
            self.assertEqual(len(store.load().ledger), 1)


if __name__ == "__main__":
    unittest.main()
