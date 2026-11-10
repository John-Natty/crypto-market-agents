from copy import deepcopy
from datetime import datetime, timedelta, timezone
from decimal import Decimal as D
import hashlib
import json
from pathlib import Path
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from io import StringIO
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
from crypto_market_agents.cli import main


NOW = datetime.now(timezone.utc)
REF = "scan:BTC-EUR:price_change_24h_pct"


def inputs(*, collected_at=None, review_verdict="supported", rejected=None):
    collected_at = collected_at or NOW.isoformat()
    research = {
        "schema_version": "autonomous-research.v6",
        "status": "completed",
        "generated_at": NOW.isoformat(),
        "read_only": True,
        "financial_action_executed": False,
        "conclusion": {
            "candidates": [
                {
                    "market": "BTC-EUR",
                    "status": "complete",
                    "fact_refs": [REF],
                    "missing_refs": [],
                    "required_missing_refs": [],
                }
            ]
        },
        "fact_ledger": {
            REF: {
                "reference": REF,
                "market": "BTC-EUR",
                "source": "bitvavo_scanner",
                "collected_at": collected_at,
                "source_at": collected_at,
                "value": {
                    "value": "2.5",
                    "unit": "%/24h",
                    "availability": "available",
                    "freshness": {"fresh": True},
                },
            }
        },
        "missing_ledger": {},
    }
    raw = json.dumps(research, sort_keys=True).encode()
    review = {
        "schema_version": "research-contradiction-review.v3",
        "generated_at": NOW.isoformat(),
        "source_report_sha256": hashlib.sha256(raw).hexdigest(),
        "status": "partial" if rejected else "completed",
        "review": {
            "candidates": [
                {
                    "market": "BTC-EUR",
                    "verdict": review_verdict,
                    "favorable_arguments": [{"reference": REF, "role": "favorable"}],
                    "unfavorable_arguments": [],
                    "uncertain_arguments": [],
                    "rejected_arguments": rejected or [],
                    "rejected_invalidation_conditions": [],
                }
            ]
        },
    }
    return (
        research,
        review,
        hashlib.sha256(raw).hexdigest(),
        hashlib.sha256(json.dumps(review).encode()).hexdigest(),
    )


def proposal(action="buy", *, references=None, market="BTC-EUR"):
    return {
        "market": market,
        "action": action,
        "references": references
        if references is not None
        else ([REF] if action != "abstain" else []),
        "rationale": "La variation observée mérite une proposition surveillée.",
        "horizon": "7 jours" if action == "buy" else "",
        "invalidation_conditions": ["Réexaminer si la thèse est contredite"]
        if action == "buy"
        else [],
    }


class FakeOllama:
    def __init__(self, payload=None, *, done_reason="stop", error=None):
        self.payload, self.done_reason, self.error = payload, done_reason, error
        self.calls = []

    def chat_with_tools(self, messages, tools, **kwargs):
        self.calls.append((messages, tools, kwargs))
        if self.error:
            raise self.error
        payload = self.payload
        target = json.loads(messages[1]["content"])["target_market"]
        if isinstance(payload, dict) and target in payload and "action" not in payload:
            payload = payload[target]
        return {
            "message": {"content": json.dumps(payload), "tool_calls": []},
            "done_reason": self.done_reason,
            "think": False,
            "request_bytes": 1200,
            "response_bytes": 600,
            "duration_ms": 200,
            "generation": {"prompt_eval_count": 500, "eval_count": 80},
        }


class Quotes:
    def __init__(self, *, fail=(), bid="60000", ask="60001", minimum="5", timestamp=NOW):
        self.fail, self.bid, self.ask, self.minimum, self.timestamp = (
            set(fail),
            bid,
            ask,
            minimum,
            timestamp,
        )
        self.calls = []

    def get_quote(self, market):
        self.calls.append(market)
        if market in self.fail:
            raise OSError("private response details are not retained")
        return Quote(
            market,
            D(self.bid),
            D(self.ask),
            self.timestamp,
            D(self.minimum),
            D("0.00000001"),
            D("0.000001"),
        )


class ProposalCycleTests(unittest.TestCase):
    def run_cycle(
        self,
        *,
        action="buy",
        research=None,
        review=None,
        quote_client=None,
        model_payload=None,
        portfolio=None,
    ):
        research, review, research_hash, review_hash = (
            inputs() if research is None else (research, review, *self.hashes(research, review))
        )
        model = FakeOllama(model_payload or proposal(action))
        portfolio = portfolio or MultiAssetPortfolio.create()
        before = portfolio.to_dict()
        result = run_proposal_cycle(
            research,
            review,
            portfolio,
            model,
            research_sha256=research_hash,
            review_sha256=review_hash,
            research_label="research.json",
            review_label="review.json",
            model="qwen3:8b",
            now_provider=lambda: NOW,
            quote_client=quote_client or Quotes(),
        )
        return result, portfolio, before, model

    @staticmethod
    def hashes(research, review):
        raw = json.dumps(research, sort_keys=True).encode()
        research_hash = hashlib.sha256(raw).hexdigest()
        review = deepcopy(review)
        review["source_report_sha256"] = research_hash
        return research_hash, hashlib.sha256(json.dumps(review).encode()).hexdigest()

    def test_valid_proposal_uses_engine_preview_and_does_not_mutate_portfolio(self):
        result, portfolio, before, model = self.run_cycle()
        self.assertEqual(result["technical_status"], "completed")
        self.assertEqual(result["model_response_status"], "valid")
        self.assertEqual(result["plan_admissibility"], "accepted")
        self.assertEqual(result["decision_status"], "plan_accepted")
        self.assertEqual(result["proposals"][0]["status"], "accepted")
        self.assertGreater(D(result["proposals"][0]["amount_eur"]), 0)
        self.assertLessEqual(
            D(result["proposals"][0]["amount_eur"]), portfolio.config.max_trade_eur
        )
        self.assertEqual(portfolio.to_dict(), before)
        self.assertEqual(model.calls[0][1], [])
        self.assertTrue(result["read_only"])
        self.assertFalse(result["financial_action_executed"])

    def test_end_to_end_evidence_review_single_proposal_quote_and_acceptance(self):
        result, _, before, model = self.run_cycle()
        self.assertEqual(result["proposals"][0]["references"], [REF])
        self.assertEqual(result["proposals"][0]["status"], "accepted")
        self.assertEqual(result["quote_observations"][0]["status"], "available")
        self.assertEqual(json.loads(model.calls[0][0][1]["content"])["target_market"], "BTC-EUR")
        self.assertEqual(
            model.calls[0][2]["format_schema"]["properties"]["market"]["enum"], ["BTC-EUR"]
        )
        self.assertEqual(
            result["portfolio_snapshot"]["sha256"],
            hashlib.sha256(
                json.dumps(
                    before, ensure_ascii=False, sort_keys=True, separators=(",", ":")
                ).encode()
            ).hexdigest(),
        )

    def test_multiple_unique_markets_reserve_shared_cash_and_exposure(self):
        research, review, _, _ = inputs()
        second_ref = "scan:ETH-EUR:price_change_24h_pct"
        second_candidate = deepcopy(research["conclusion"]["candidates"][0])
        second_candidate.update(market="ETH-EUR", fact_refs=[second_ref])
        research["conclusion"]["candidates"].append(second_candidate)
        research["fact_ledger"][second_ref] = {
            **deepcopy(research["fact_ledger"][REF]),
            "reference": second_ref,
            "market": "ETH-EUR",
        }
        second_review = deepcopy(review["review"]["candidates"][0])
        second_review.update(
            market="ETH-EUR", favorable_arguments=[{"reference": second_ref, "role": "favorable"}]
        )
        review["review"]["candidates"].append(second_review)
        research_hash, _ = self.hashes(research, review)
        review["source_report_sha256"] = research_hash
        review_hash = hashlib.sha256(json.dumps(review).encode()).hexdigest()
        model = FakeOllama(
            {
                "BTC-EUR": proposal(market="BTC-EUR"),
                "ETH-EUR": proposal(market="ETH-EUR", references=[second_ref]),
            }
        )
        portfolio = MultiAssetPortfolio.create()
        before = portfolio.to_dict()
        result = run_proposal_cycle(
            research,
            review,
            portfolio,
            model,
            research_sha256=research_hash,
            review_sha256=review_hash,
            research_label="r",
            review_label="v",
            model="qwen3:8b",
            now_provider=lambda: NOW,
            quote_client=Quotes(bid="100", ask="101"),
        )
        self.assertEqual(len(model.calls), 2)
        self.assertTrue(all(row["status"] == "accepted" for row in result["proposals"]))
        self.assertEqual(
            [row["market"] for row in result["plan_evaluation_order"]], ["BTC-EUR", "ETH-EUR"]
        )
        reserved = sum(D(row["budget_reserved_eur"]) for row in result["proposals"])
        self.assertLessEqual(reserved, portfolio.initial_capital_eur)
        self.assertLessEqual(
            sum(D(row["amount_eur"]) for row in result["proposals"]),
            portfolio.config.max_total_exposure_eur,
        )
        self.assertLess(
            D(result["proposals"][1]["budget_reserved_eur"]),
            D(result["proposals"][0]["budget_reserved_eur"]),
        )
        self.assertEqual(portfolio.to_dict(), before)

    def test_minimum_incompatible_rejected_with_engine_reason(self):
        result, _, _, _ = self.run_cycle(quote_client=Quotes(minimum="1000"))
        self.assertEqual(result["proposals"][0]["status"], "rejected")
        self.assertTrue(
            any(
                "multi_asset_engine_rejected" in reason
                for reason in result["proposals"][0]["reasons"]
            )
        )

    def test_quote_received_after_cycle_start_is_not_misclassified_as_future(self):
        research, review, research_hash, review_hash = inputs()
        clock = iter([NOW, NOW + timedelta(seconds=1), NOW + timedelta(seconds=2)]).__next__
        quote_client = Quotes(timestamp=NOW + timedelta(milliseconds=500))
        result = run_proposal_cycle(
            research,
            review,
            MultiAssetPortfolio.create(),
            FakeOllama(proposal()),
            research_sha256=research_hash,
            review_sha256=review_hash,
            research_label="research.json",
            review_label="review.json",
            model="qwen3:8b",
            now_provider=clock,
            quote_client=quote_client,
        )
        self.assertEqual(result["quote_observations"][0]["status"], "available")
        self.assertEqual(result["proposals"][0]["status"], "accepted")

    def test_stale_public_quote_rejects_plan(self):
        result, _, _, _ = self.run_cycle(
            quote_client=Quotes(timestamp=NOW - timedelta(seconds=121))
        )
        self.assertEqual(result["proposals"][0]["status"], "rejected")
        self.assertEqual(result["quote_observations"][0]["reason"], "quote_stale_or_future")

    def test_stale_evidence_and_insufficient_review_block_new_entry(self):
        research, review, _, _ = inputs(collected_at=(NOW - timedelta(hours=2)).isoformat())
        review["source_report_sha256"] = hashlib.sha256(
            json.dumps(research, sort_keys=True).encode()
        ).hexdigest()
        result, _, _, _ = self.run_cycle(research=research, review=review)
        self.assertEqual(result["proposals"][0]["status"], "rejected")
        self.assertTrue(
            any(
                "proposal_evidence_not_fresh" in reason
                for reason in result["proposals"][0]["reasons"]
            )
        )

    def test_partial_research_can_support_independent_fresh_dependencies(self):
        research, review, _, _ = inputs()
        research["status"] = "partial"
        research["conclusion"]["candidates"][0]["status"] = "partial"
        review["source_report_sha256"] = hashlib.sha256(
            json.dumps(research, sort_keys=True).encode()
        ).hexdigest()
        result, _, _, _ = self.run_cycle(research=research, review=review)
        self.assertEqual(result["proposals"][0]["status"], "accepted")

        research, review, _, _ = inputs(review_verdict="insufficient")
        result, _, _, _ = self.run_cycle(research=research, review=review)
        self.assertTrue(
            any(
                "new_entry_review_not_supportive" in reason
                for reason in result["proposals"][0]["reasons"]
            )
        )

    def test_invented_reference_is_retained_as_rejected_plan(self):
        result, _, _, _ = self.run_cycle(model_payload=proposal(references=["fact:invented"]))
        self.assertEqual(result["proposals"][0]["status"], "rejected")
        self.assertIn("proposal_reference_not_in_source_scope", result["proposals"][0]["reasons"])

    def test_legacy_multiple_proposals_are_invalid_not_arbitrarily_selected(self):
        duplicated = {"proposals": [proposal(), proposal("hold")]}
        result, _, _, _ = self.run_cycle(model_payload=duplicated)
        self.assertEqual(result["model_response_status"], "invalid")
        self.assertEqual(result["decision_status"], "response_rejected")
        self.assertEqual(len(result["proposals"]), 1)
        self.assertEqual(result["proposals"][0]["status"], "not_evaluated")
        self.assertIn("proposal_single_object_required", result["proposals"][0]["reasons"])

    def test_model_abstention_is_reported_separately_from_response_rejection(self):
        result, _, _, _ = self.run_cycle(action="abstain")
        self.assertEqual(result["technical_status"], "completed")
        self.assertEqual(result["model_response_status"], "valid")
        self.assertEqual(result["decision_status"], "model_abstained")
        self.assertEqual(result["proposals"][0]["status"], "model_abstained")
        self.assertEqual(result["proposals"][0]["reasons"], ["model_abstained"])

    def test_thinking_must_be_confirmed_disabled(self):
        research, review, research_hash, review_hash = inputs()

        class ThinkingOllama(FakeOllama):
            def chat_with_tools(self, *args, **kwargs):
                result = super().chat_with_tools(*args, **kwargs)
                result["think"] = True
                return result

        result = run_proposal_cycle(
            research,
            review,
            MultiAssetPortfolio.create(),
            ThinkingOllama(proposal()),
            research_sha256=research_hash,
            review_sha256=review_hash,
            research_label="research.json",
            review_label="review.json",
            model="qwen3:8b",
            now_provider=lambda: NOW,
            quote_client=Quotes(),
        )
        self.assertEqual(result["decision_status"], "response_rejected")
        self.assertEqual(result["model_response_status"], "invalid")
        self.assertTrue(
            any(
                row.get("reason") == "ollama_thinking_disable_not_confirmed"
                for row in result["errors"]
            )
        )

    def test_incomplete_global_valuation_blocks_buy(self):
        portfolio = MultiAssetPortfolio.create()
        engine = MultiAssetPaperEngine()
        engine.execute(
            portfolio,
            MultiAssetOrder("buy", "bitvavo", "ETH-EUR", "seed", amount_eur=D("20")),
            Quote("ETH-EUR", D("2000"), D("2001"), NOW),
            "seed-eth",
            now=NOW,
        )
        client = Quotes(fail={"ETH-EUR"})
        result, _, before, _ = self.run_cycle(portfolio=portfolio, quote_client=client)
        self.assertEqual(result["proposals"][0]["status"], "rejected")
        self.assertIn(
            "global_valuation_incomplete_new_entry_blocked", result["proposals"][0]["reasons"]
        )
        self.assertTrue(
            any(
                observation["market"] == "ETH-EUR" and observation["status"] == "unavailable"
                for observation in result["quote_observations"]
            )
        )
        self.assertEqual(portfolio.to_dict(), before)

    def test_reduction_with_valid_quote_works_when_another_holding_quote_fails(self):
        portfolio = MultiAssetPortfolio.create()
        engine = MultiAssetPaperEngine()
        for market, price in (("BTC-EUR", "60000"), ("ETH-EUR", "2000")):
            engine.execute(
                portfolio,
                MultiAssetOrder("buy", "bitvavo", market, "seed", amount_eur=D("20")),
                Quote(market, D(price), D(price) + 1, NOW),
                f"seed-{market}",
                now=NOW,
            )
        # A held-position reference is enough to reduce without requesting a new purchase thesis.
        model_payload = proposal("reduce", references=["portfolio:bitvavo|BTC-EUR:position"])
        result, _, before, _ = self.run_cycle(
            portfolio=portfolio, quote_client=Quotes(fail={"ETH-EUR"}), model_payload=model_payload
        )
        self.assertEqual(result["proposals"][0]["status"], "accepted")
        self.assertTrue(
            any(
                row["market"] == "ETH-EUR" and row["status"] == "unavailable"
                for row in result["quote_observations"]
            )
        )
        self.assertEqual(portfolio.to_dict(), before)

    def test_ollama_failure_is_not_voluntary_abstention(self):
        research, review, rh, vh = inputs()
        model = FakeOllama(error=TimeoutError())
        client = Quotes()
        portfolio = MultiAssetPortfolio.create()
        before = portfolio.to_dict()
        result = run_proposal_cycle(
            research,
            review,
            portfolio,
            model,
            research_sha256=rh,
            review_sha256=vh,
            research_label="r",
            review_label="v",
            model="qwen3:8b",
            now_provider=lambda: NOW,
            quote_client=client,
        )
        self.assertEqual(result["decision_status"], "response_rejected")
        self.assertNotEqual(result["decision_status"], "model_abstained")
        self.assertEqual(result["model_response_status"], "invalid")
        self.assertEqual(client.calls, [])
        self.assertEqual(portfolio.to_dict(), before)

    def test_cli_writes_a_separate_report_without_touching_portfolio(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            state_path = root / "paper.json"
            store = MultiAssetPaperStore(state_path)
            store.initialize()
            before = state_path.read_bytes()
            research_path, review_path, output_path = (
                root / "research.json",
                root / "review.json",
                root / "cycle.json",
            )
            research_path.write_text("{}")
            review_path.write_text("{}")
            stub = {
                "status": "abstained",
                "decision": "abstain",
                "technical_status": "completed",
                "model_response_status": "valid",
                "plan_admissibility": "not_evaluated",
                "decision_status": "model_abstained",
                "proposals": [],
                "read_only": True,
                "financial_action_executed": False,
            }
            with (
                patch("crypto_market_agents.cli.OllamaCandidateEvaluator"),
                patch("crypto_market_agents.cli.run_proposal_cycle", return_value=stub),
                redirect_stdout(StringIO()),
            ):
                code = main(
                    [
                        "proposal-cycle",
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
            self.assertEqual(code, 0)
            self.assertTrue(output_path.is_file())
            self.assertEqual(state_path.read_bytes(), before)


if __name__ == "__main__":
    unittest.main()
