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
from crypto_market_agents.trading.proposal_cycle import (
    collect_position_history_report,
    load_recent_position_history,
    load_verified_entry_theses,
    run_proposal_cycle,
)
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
        "read_only": True,
        "financial_action_executed": False,
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


class FakeHistoryClient:
    def __init__(self):
        self.calls = []

    def get_candles(self, market, *, interval, limit):
        self.calls.append((market, interval, limit))
        start = NOW - timedelta(hours=801)
        return [
            [
                int((start + timedelta(hours=index)).timestamp() * 1000),
                "60000",
                "70000",
                "50000",
                str(60000 + index),
                "1",
            ]
            for index in range(800)
        ]


class ProposalCycleTests(unittest.TestCase):
    def run_cycle(
        self,
        *,
        action="buy",
        research=None,
        review=None,
        quote_client=None,
        history_client=None,
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
            history_client=history_client or FakeHistoryClient(),
        )
        return result, portfolio, before, model

    def test_position_outside_discovery_gets_bounded_history_when_research_and_review_fail(self):
        portfolio = MultiAssetPortfolio.create()
        MultiAssetPaperEngine().execute(
            portfolio,
            MultiAssetOrder("buy", "bitvavo", "ETH-EUR", "fixture position", amount_eur=D("20")),
            Quote("ETH-EUR", D("2000"), D("2001"), NOW),
            "fixture-seed-eth",
            now=NOW,
        )
        research = {
            "schema_version": "autonomous-research.v6",
            "generated_at": NOW.isoformat(),
            "status": "unavailable",
            "research_status": "unavailable",
            "read_only": True,
            "financial_action_executed": False,
            "conclusion": {"candidates": []},
            "fact_ledger": {},
            "missing_ledger": {},
        }
        research_hash = hashlib.sha256(json.dumps(research, sort_keys=True).encode()).hexdigest()
        review = {
            "schema_version": "research-contradiction-review.v3",
            "generated_at": NOW.isoformat(),
            "status": "unavailable",
            "source_report_sha256": research_hash,
            "read_only": True,
            "financial_action_executed": False,
            "review": None,
        }

        class History:
            def __init__(self):
                self.calls = []

            def get_candles(self, market, *, interval, limit):
                self.calls.append((market, interval, limit))
                return [
                    [
                        int((NOW - timedelta(hours=61 - i)).timestamp() * 1000),
                        "2000",
                        "2010",
                        "1990",
                        str(2000 + i),
                        "1",
                    ]
                    for i in range(60)
                ]

        history = History()
        result, _, _, _ = self.run_cycle(
            portfolio=portfolio,
            research=research,
            review=review,
            model_payload=proposal("abstain", references=[], market="ETH-EUR"),
            history_client=history,
        )
        tracking = result["position_tracking"]
        self.assertEqual(history.calls, [("ETH-EUR", "1h", 1440)])
        self.assertEqual(tracking["historical_analysis"]["ETH-EUR"]["status"], "collected")
        self.assertGreater(tracking["historical_analysis"]["ETH-EUR"]["available_metric_count"], 0)
        self.assertEqual(tracking["sources"]["research"], "unavailable")
        self.assertEqual(tracking["sources"]["review"], "unavailable")
        self.assertEqual(tracking["positions"][0]["status"], "incomplete")
        self.assertIn("research_unavailable", tracking["positions"][0]["reason"])
        self.assertIn("review_unavailable", tracking["positions"][0]["reason"])

    def test_fresh_history_in_report_is_reused_without_another_candle_request(self):
        portfolio = MultiAssetPortfolio.create()
        MultiAssetPaperEngine().execute(
            portfolio,
            MultiAssetOrder("buy", "bitvavo", "BTC-EUR", "fixture position", amount_eur=D("20")),
            Quote("BTC-EUR", D("60000"), D("60001"), NOW),
            "fixture-seed-btc",
            now=NOW,
        )
        research, review, _, _ = inputs()
        metrics = (
            "returns_pct.24h",
            "returns_pct.7d",
            "returns_pct.30d",
            "sma20_close",
            "sma50_close",
            "sma20_vs_sma50_pct",
        )
        for metric in metrics:
            reference = f"history:BTC-EUR:{metric}"
            research["fact_ledger"][reference] = {
                "reference": reference,
                "market": "BTC-EUR",
                "source": "bitvavo_historical_analysis",
                "collected_at": NOW.isoformat(),
                "value": {
                    "value": "1",
                    "availability": "available",
                    "freshness": {"fresh": True},
                },
            }
            research["conclusion"]["candidates"][0]["fact_refs"].append(reference)
        review["source_report_sha256"] = self.hashes(research, review)[0]

        class NoCollection:
            def get_candles(self, *_args, **_kwargs):
                raise AssertionError("fresh history must be reused")

        result, _, _, _ = self.run_cycle(
            portfolio=portfolio,
            research=research,
            review=review,
            action="hold",
            model_payload=proposal("hold", references=[REF, "portfolio:bitvavo|BTC-EUR:position"]),
            history_client=NoCollection(),
        )
        self.assertEqual(
            result["position_tracking"]["historical_analysis"]["BTC-EUR"]["status"],
            "reused_fresh_analysis",
        )
        self.assertEqual(
            result["position_tracking"]["historical_analysis"]["BTC-EUR"]["network_requests"],
            0,
        )

    def test_tracking_only_uses_fresh_history_without_discovery_review(self):
        portfolio = MultiAssetPortfolio.create()
        MultiAssetPaperEngine().execute(
            portfolio,
            MultiAssetOrder("buy", "bitvavo", "BTC-EUR", "fixture position", amount_eur=D("20")),
            Quote("BTC-EUR", D("60000"), D("60001"), NOW),
            "tracking-only-seed",
            now=NOW,
        )
        history_report = collect_position_history_report(
            portfolio,
            FakeHistoryClient(),
            reports_dir=Path(tempfile.mkdtemp()),
            now=NOW,
        )
        review = {
            "schema_version": "research-contradiction-review.v3",
            "generated_at": NOW.isoformat(),
            "source_report_sha256": hashlib.sha256(
                json.dumps(history_report, sort_keys=True).encode()
            ).hexdigest(),
            "status": "not_applicable",
            "review_scope": "position_tracking_only",
            "read_only": True,
            "financial_action_executed": False,
            "review": {"candidates": []},
        }
        research_bytes = json.dumps(history_report, sort_keys=True).encode()
        review["source_report_sha256"] = hashlib.sha256(research_bytes).hexdigest()
        model = FakeOllama(
            proposal(
                "hold",
                market="BTC-EUR",
                references=[
                    "history:BTC-EUR:returns_pct.24h",
                    "portfolio:bitvavo|BTC-EUR:position",
                ],
            )
        )
        before = portfolio.to_dict()
        result = run_proposal_cycle(
            history_report,
            review,
            portfolio,
            model,
            research_sha256=hashlib.sha256(research_bytes).hexdigest(),
            review_sha256=hashlib.sha256(json.dumps(review, sort_keys=True).encode()).hexdigest(),
            research_label="position-history.json",
            review_label="not-applicable.json",
            model="qwen3:8b",
            now_provider=lambda: NOW,
            quote_client=Quotes(),
            position_tracking_only=True,
        )
        self.assertEqual(result["cycle_type"], "position_tracking_only")
        self.assertEqual(result["position_tracking"]["positions"][0]["status"], "reviewed")
        self.assertEqual(result["position_tracking"]["positions"][0]["action"], "hold")
        self.assertEqual(result["position_tracking"]["sources"]["review"], "not_applicable")
        self.assertEqual(portfolio.to_dict(), before)
        self.assertEqual(len(model.calls), 1)

    def test_tracking_only_can_propose_reduce_or_close_without_discovery_review(self):
        for action in ("reduce", "close"):
            with self.subTest(action=action):
                portfolio = MultiAssetPortfolio.create()
                MultiAssetPaperEngine().execute(
                    portfolio,
                    MultiAssetOrder(
                        "buy", "bitvavo", "BTC-EUR", "fixture position", amount_eur=D("20")
                    ),
                    Quote("BTC-EUR", D("60000"), D("60001"), NOW),
                    f"tracking-only-seed-{action}",
                    now=NOW,
                )
                research = collect_position_history_report(
                    portfolio,
                    FakeHistoryClient(),
                    reports_dir=Path(tempfile.mkdtemp()),
                    now=NOW,
                )
                raw = json.dumps(research, sort_keys=True).encode()
                review = {
                    "schema_version": "research-contradiction-review.v3",
                    "generated_at": NOW.isoformat(),
                    "source_report_sha256": hashlib.sha256(raw).hexdigest(),
                    "status": "not_applicable",
                    "review_scope": "position_tracking_only",
                    "read_only": True,
                    "financial_action_executed": False,
                    "review": {"candidates": []},
                }
                review_raw = json.dumps(review, sort_keys=True).encode()
                model = FakeOllama(
                    proposal(
                        action,
                        market="BTC-EUR",
                        references=[
                            "history:BTC-EUR:returns_pct.24h",
                            "portfolio:bitvavo|BTC-EUR:position",
                        ],
                    )
                )
                result = run_proposal_cycle(
                    research,
                    review,
                    portfolio,
                    model,
                    research_sha256=hashlib.sha256(raw).hexdigest(),
                    review_sha256=hashlib.sha256(review_raw).hexdigest(),
                    research_label="position-history.json",
                    review_label="not-applicable.json",
                    model="qwen3:8b",
                    now_provider=lambda: NOW,
                    quote_client=Quotes(),
                    position_tracking_only=True,
                )
                self.assertEqual(result["proposals"][0]["status"], "accepted")
                self.assertEqual(result["proposals"][0]["action"], action)
                self.assertEqual(result["position_tracking"]["positions"][0]["status"], "reviewed")

    def test_position_history_network_failure_is_specific_and_incomplete(self):
        portfolio = MultiAssetPortfolio.create()
        MultiAssetPaperEngine().execute(
            portfolio,
            MultiAssetOrder("buy", "bitvavo", "BTC-EUR", "fixture position", amount_eur=D("20")),
            Quote("BTC-EUR", D("60000"), D("60001"), NOW),
            "tracking-network-failure-seed",
            now=NOW,
        )

        class Offline:
            def get_candles(self, *_args, **_kwargs):
                raise TimeoutError("bounded public request timed out")

        result = collect_position_history_report(
            portfolio,
            Offline(),
            reports_dir=Path(tempfile.mkdtemp()),
            now=NOW,
        )
        row = result["position_history"]["BTC-EUR"]
        self.assertEqual(row["status"], "incomplete")
        self.assertIn("TimeoutError", row["reason"])
        self.assertEqual(row["network_requests"], 1)

    def test_tracking_only_ollama_failure_is_incomplete_not_a_hold(self):
        portfolio = MultiAssetPortfolio.create()
        MultiAssetPaperEngine().execute(
            portfolio,
            MultiAssetOrder("buy", "bitvavo", "BTC-EUR", "fixture position", amount_eur=D("20")),
            Quote("BTC-EUR", D("60000"), D("60001"), NOW),
            "tracking-llm-failure-seed",
            now=NOW,
        )
        research = collect_position_history_report(
            portfolio,
            FakeHistoryClient(),
            reports_dir=Path(tempfile.mkdtemp()),
            now=NOW,
        )
        research_raw = json.dumps(research, sort_keys=True).encode()
        review = {
            "schema_version": "research-contradiction-review.v3",
            "generated_at": NOW.isoformat(),
            "source_report_sha256": hashlib.sha256(research_raw).hexdigest(),
            "status": "not_applicable",
            "review_scope": "position_tracking_only",
            "read_only": True,
            "financial_action_executed": False,
            "review": {"candidates": []},
        }
        model = FakeOllama(error=RuntimeError("simulated local model outage"))
        result = run_proposal_cycle(
            research,
            review,
            portfolio,
            model,
            research_sha256=hashlib.sha256(research_raw).hexdigest(),
            review_sha256=hashlib.sha256(json.dumps(review, sort_keys=True).encode()).hexdigest(),
            research_label="position-history.json",
            review_label="not-applicable.json",
            model="qwen3:8b",
            now_provider=lambda: NOW,
            quote_client=Quotes(),
            position_tracking_only=True,
        )
        row = result["position_tracking"]["positions"][0]
        self.assertEqual(row["status"], "incomplete")
        self.assertIsNone(row["action"])
        self.assertNotEqual(row["suggested_action"], "hold")
        self.assertNotEqual(result["decision_status"], "model_abstained")

    def test_verified_fresh_history_cache_is_reused_and_stale_cache_is_refetched(self):
        portfolio = MultiAssetPortfolio.create()
        MultiAssetPaperEngine().execute(
            portfolio,
            MultiAssetOrder("buy", "bitvavo", "BTC-EUR", "fixture position", amount_eur=D("20")),
            Quote("BTC-EUR", D("60000"), D("60001"), NOW),
            "tracking-cache-seed",
            now=NOW,
        )
        refs = (
            "returns_pct.24h",
            "returns_pct.7d",
            "returns_pct.30d",
            "sma20_close",
            "sma50_close",
            "sma20_vs_sma50_pct",
        )

        def install_report(root, collected_at):
            cycle_dir = root / "automatic-cycles" / ("a" * 32)
            cycle_dir.mkdir(parents=True)
            facts = {
                f"history:BTC-EUR:{metric}": {
                    "reference": f"history:BTC-EUR:{metric}",
                    "market": "BTC-EUR",
                    "source": "bitvavo_historical_analysis",
                    "collected_at": collected_at.isoformat(),
                    "value": {
                        "value": "1",
                        "availability": "available",
                        "freshness": {"fresh": True},
                    },
                }
                for metric in refs
            }
            report = {
                "schema_version": "autonomous-research.v6",
                "status": "completed",
                "read_only": True,
                "financial_action_executed": False,
                "fact_ledger": facts,
            }
            raw = (json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode()
            (cycle_dir / "research.json").write_bytes(raw)
            (cycle_dir / "cycle.json").write_text(
                json.dumps(
                    {
                        "schema_version": "automatic-paper-cycle.v1",
                        "cycle_id": "a" * 32,
                        "stages": {
                            "research": {
                                "status": "completed",
                                "artifact": "research.json",
                                "sha256": hashlib.sha256(raw).hexdigest(),
                            }
                        },
                    }
                ),
                encoding="utf-8",
            )

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            install_report(root, NOW)
            self.assertEqual(len(load_recent_position_history(root, portfolio, now=NOW)), 6)

            class History(FakeHistoryClient):
                pass

            history = History()
            result = collect_position_history_report(
                portfolio,
                history,
                reports_dir=root,
                now=NOW,
            )
            self.assertEqual(history.calls, [])
            self.assertEqual(
                result["position_history"]["BTC-EUR"]["status"], "reused_fresh_analysis"
            )

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            install_report(root, NOW - timedelta(hours=7))
            history = FakeHistoryClient()
            result = collect_position_history_report(
                portfolio,
                history,
                reports_dir=root,
                now=NOW,
            )
            self.assertEqual(history.calls, [("BTC-EUR", "1h", 1440)])
            self.assertEqual(result["position_history"]["BTC-EUR"]["status"], "collected")

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

    def test_open_position_missing_from_scanner_gets_motivated_hold_without_invented_thesis(self):
        portfolio = MultiAssetPortfolio.create()
        MultiAssetPaperEngine().execute(
            portfolio,
            MultiAssetOrder("buy", "bitvavo", "ETH-EUR", "seed", amount_eur=D("20")),
            Quote("ETH-EUR", D("2000"), D("2001"), NOW),
            "seed-eth",
            now=NOW,
        )
        held_ref = "portfolio:bitvavo|ETH-EUR:position"
        model_payload = {
            "ETH-EUR": {
                **proposal("hold", references=[held_ref], market="ETH-EUR"),
                "rationale": "La cotation est valide et aucun fait disponible ne montre une invalidation.",
            },
            "BTC-EUR": proposal("abstain", references=[], market="BTC-EUR"),
        }
        result, _, before, model = self.run_cycle(portfolio=portfolio, model_payload=model_payload)
        follow = result["position_tracking"]["positions"][0]
        context = json.loads(model.calls[0][0][1]["content"])
        self.assertEqual(context["target_market"], "ETH-EUR")
        self.assertEqual(
            context["target_position"][0]["cost_basis_eur"],
            str(portfolio.positions["bitvavo|ETH-EUR"].cost_basis_eur),
        )
        self.assertEqual(context["target_position"][0]["entry_thesis"], {"status": "not_recorded"})
        self.assertEqual(follow["status"], "incomplete")
        self.assertIsNone(follow["action"])
        self.assertEqual(follow["suggested_action"], "hold")
        self.assertIn("aucun fait disponible", follow["rationale"])
        self.assertEqual(follow["source_status"], "not_in_discovery_shortlist")
        self.assertEqual(follow["entry_thesis_status"], "not_found")
        self.assertIsNone(follow["entry_thesis"])
        self.assertEqual(portfolio.to_dict(), before)

    def test_held_scanner_candidate_consumes_tracking_not_discovery_generation_budget(self):
        portfolio = MultiAssetPortfolio.create()
        MultiAssetPaperEngine().execute(
            portfolio,
            MultiAssetOrder("buy", "bitvavo", "ETH-EUR", "seed", amount_eur=D("20")),
            Quote("ETH-EUR", D("2000"), D("2001"), NOW),
            "seed-eth",
            now=NOW,
        )
        research, review, _, _ = inputs()
        research["conclusion"]["candidates"][0]["market"] = "ETH-EUR"
        research["fact_ledger"][REF]["market"] = "ETH-EUR"
        review["review"]["candidates"][0]["market"] = "ETH-EUR"
        review["source_report_sha256"] = self.hashes(research, review)[0]
        held_ref = "portfolio:bitvavo|ETH-EUR:position"
        result, _, _, model = self.run_cycle(
            portfolio=portfolio,
            research=research,
            review=review,
            model_payload=proposal(
                "hold",
                references=[REF, held_ref],
                market="ETH-EUR",
            ),
        )
        call = result["generation"]["calls"][0]
        self.assertEqual(call["target_type"], "position_tracking")
        self.assertEqual(call["num_predict"], 384)
        self.assertEqual(result["position_tracking"]["budget"]["generated_tokens"], 80)
        self.assertEqual(len(model.calls), 1)

    def test_missing_required_source_evidence_makes_hold_suggestion_incomplete(self):
        portfolio = MultiAssetPortfolio.create()
        MultiAssetPaperEngine().execute(
            portfolio,
            MultiAssetOrder("buy", "bitvavo", "BTC-EUR", "seed", amount_eur=D("20")),
            Quote("BTC-EUR", D("60000"), D("60001"), NOW),
            "seed-btc",
            now=NOW,
        )
        research, review, _, _ = inputs(review_verdict="insufficient")
        research["status"] = "partial"
        research["conclusion"]["candidates"][0]["status"] = "partial"
        research["conclusion"]["candidates"][0]["required_missing_refs"] = [
            "missing:BTC-EUR:holder_concentration"
        ]
        research["conclusion"]["candidates"][0]["missing_refs"] = [
            "missing:BTC-EUR:holder_concentration"
        ]
        research["missing_ledger"]["missing:BTC-EUR:holder_concentration"] = {
            "market": "BTC-EUR",
            "reason": "provider_data_unavailable",
        }
        review["source_report_sha256"] = self.hashes(research, review)[0]
        held_ref = "portfolio:bitvavo|BTC-EUR:position"
        result, _, _, _ = self.run_cycle(
            portfolio=portfolio,
            research=research,
            review=review,
            model_payload={
                "BTC-EUR": {
                    **proposal("hold", references=[held_ref]),
                    "rationale": "La position est observée mais une donnée requise manque.",
                },
                "BTC-EUR-discovery": proposal("abstain"),
            },
        )
        follow = result["position_tracking"]["positions"][0]
        self.assertEqual(follow["status"], "incomplete")
        self.assertIsNone(follow["action"])
        self.assertEqual(follow["suggested_action"], "hold")
        self.assertEqual(follow["missing_references"], ["missing:BTC-EUR:holder_concentration"])

    def test_failed_research_and_unavailable_review_still_run_incomplete_position_follow_up(self):
        portfolio = MultiAssetPortfolio.create()
        MultiAssetPaperEngine().execute(
            portfolio,
            MultiAssetOrder("buy", "bitvavo", "BTC-EUR", "fixture position", amount_eur=D("20")),
            Quote("BTC-EUR", D("60000"), D("60001"), NOW),
            "fixture-seed-btc",
            now=NOW,
        )
        research = {
            "schema_version": "autonomous-research.v6",
            "generated_at": NOW.isoformat(),
            "status": "unavailable",
            "research_status": "unavailable",
            "read_only": True,
            "financial_action_executed": False,
            "conclusion": {"candidates": []},
            "fact_ledger": {},
            "missing_ledger": {},
        }
        research_hash = hashlib.sha256(json.dumps(research, sort_keys=True).encode()).hexdigest()
        review = {
            "schema_version": "research-contradiction-review.v3",
            "generated_at": NOW.isoformat(),
            "status": "unavailable",
            "source_report_sha256": research_hash,
            "read_only": True,
            "financial_action_executed": False,
            "review": None,
        }
        result, _, _, model = self.run_cycle(
            portfolio=portfolio,
            research=research,
            review=review,
            model_payload=proposal("hold", references=["portfolio:bitvavo|BTC-EUR:position"]),
        )
        follow = result["position_tracking"]["positions"][0]
        self.assertEqual(len(model.calls), 1)
        self.assertEqual(
            result["position_tracking"]["sources"],
            {
                "research": "unavailable",
                "review": "unavailable",
            },
        )
        self.assertEqual(follow["status"], "incomplete")
        self.assertIsNone(follow["action"])
        self.assertEqual(follow["suggested_action"], "hold")
        self.assertIn("research_unavailable", follow["reason"])
        self.assertIn("review_unavailable", follow["reason"])
        self.assertEqual(result["proposals"][0]["status"], "not_evaluated")
        self.assertEqual(result["technical_status"], "partial")

    def test_empty_discovery_still_calls_model_for_open_position(self):
        portfolio = MultiAssetPortfolio.create()
        MultiAssetPaperEngine().execute(
            portfolio,
            MultiAssetOrder("buy", "bitvavo", "BTC-EUR", "fixture position", amount_eur=D("20")),
            Quote("BTC-EUR", D("60000"), D("60001"), NOW),
            "fixture-seed-btc",
            now=NOW,
        )
        research, review, _, _ = inputs()
        research["conclusion"]["candidates"] = []
        review["review"]["candidates"] = []
        review["source_report_sha256"] = self.hashes(research, review)[0]
        result, _, _, model = self.run_cycle(
            portfolio=portfolio,
            research=research,
            review=review,
            model_payload=proposal("hold", references=[REF, "portfolio:bitvavo|BTC-EUR:position"]),
        )
        follow = result["position_tracking"]["positions"][0]
        context = json.loads(model.calls[0][0][1]["content"])
        self.assertEqual(len(model.calls), 1)
        self.assertEqual(context["target_market"], "BTC-EUR")
        self.assertEqual(
            context["research_review"][0]["research_status"], "not_in_discovery_shortlist"
        )
        self.assertEqual(follow["status"], "incomplete")
        self.assertEqual(follow["suggested_action"], "hold")
        self.assertIn("position_not_in_current_research_scope", follow["reason"])

    def test_optional_missing_evidence_is_visible_without_blocking_position_follow_up(self):
        portfolio = MultiAssetPortfolio.create()
        MultiAssetPaperEngine().execute(
            portfolio,
            MultiAssetOrder("buy", "bitvavo", "BTC-EUR", "seed", amount_eur=D("20")),
            Quote("BTC-EUR", D("60000"), D("60001"), NOW),
            "seed-btc",
            now=NOW,
        )
        research, review, _, _ = inputs()
        research["conclusion"]["candidates"][0]["missing_refs"] = ["missing:BTC-EUR:optional"]
        research["missing_ledger"]["missing:BTC-EUR:optional"] = {
            "market": "BTC-EUR",
            "reason": "optional_source_not_configured",
        }
        review["source_report_sha256"] = self.hashes(research, review)[0]
        result, _, _, model = self.run_cycle(
            portfolio=portfolio,
            research=research,
            review=review,
            model_payload=proposal("hold", references=[REF, "portfolio:bitvavo|BTC-EUR:position"]),
        )
        follow = result["position_tracking"]["positions"][0]
        self.assertEqual(follow["status"], "reviewed")
        self.assertEqual(follow["action"], "hold")
        self.assertEqual(follow["missing_references"], ["missing:BTC-EUR:optional"])
        self.assertIn("missing:BTC-EUR:optional", model.calls[0][0][1]["content"])

    def test_stale_position_quote_makes_follow_up_incomplete_not_hold(self):
        portfolio = MultiAssetPortfolio.create()
        MultiAssetPaperEngine().execute(
            portfolio,
            MultiAssetOrder("buy", "bitvavo", "BTC-EUR", "seed", amount_eur=D("20")),
            Quote("BTC-EUR", D("60000"), D("60001"), NOW),
            "seed-btc",
            now=NOW,
        )
        result, _, _, _ = self.run_cycle(
            portfolio=portfolio,
            model_payload=proposal("hold", references=["portfolio:bitvavo|BTC-EUR:position"]),
            quote_client=Quotes(timestamp=NOW - timedelta(seconds=500)),
        )
        follow = result["position_tracking"]["positions"][0]
        self.assertEqual(follow["status"], "incomplete")
        self.assertEqual(follow["proposal_status"], "not_evaluated")
        self.assertIn("quote", follow["reason"])

    def test_position_can_be_reduced_or_closed_with_existing_risk_limits(self):
        for action, amount, follow_bid in (
            ("reduce", D("60"), "120000"),
            ("close", D("20"), "60000"),
        ):
            with self.subTest(action=action):
                portfolio = MultiAssetPortfolio.create()
                engine = MultiAssetPaperEngine()
                engine.execute(
                    portfolio,
                    MultiAssetOrder("buy", "bitvavo", "BTC-EUR", "seed", amount_eur=amount),
                    Quote("BTC-EUR", D("60000"), D("60001"), NOW),
                    "seed-btc",
                    now=NOW,
                )
                original_quantity = portfolio.positions["bitvavo|BTC-EUR"].quantity
                result, _, before, _ = self.run_cycle(
                    portfolio=portfolio,
                    model_payload=proposal(
                        action,
                        references=[REF, "portfolio:bitvavo|BTC-EUR:position"],
                    ),
                    quote_client=Quotes(bid=follow_bid, ask=str(D(follow_bid) + 1)),
                )
                plan = result["proposals"][0]
                self.assertEqual(plan["status"], "accepted")
                self.assertEqual(plan["action"], action)
                if action == "close":
                    self.assertEqual(D(plan["quantity"]), original_quantity)
                else:
                    self.assertLess(D(plan["quantity"]), original_quantity)
                self.assertEqual(portfolio.to_dict(), before)

    def test_valid_position_reduction_survives_another_position_quote_failure(self):
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
        result, _, _, _ = self.run_cycle(
            portfolio=portfolio,
            model_payload={
                "BTC-EUR": proposal(
                    "reduce", references=[REF, "portfolio:bitvavo|BTC-EUR:position"]
                ),
                "ETH-EUR": proposal("hold", references=["portfolio:bitvavo|ETH-EUR:position"]),
            },
            quote_client=Quotes(fail=("ETH-EUR",)),
        )
        plans = {row["market"]: row for row in result["proposals"]}
        tracking = {row["market"]: row for row in result["position_tracking"]["positions"]}
        self.assertEqual(plans["BTC-EUR"]["status"], "accepted")
        self.assertEqual(plans["BTC-EUR"]["action"], "reduce")
        self.assertEqual(tracking["BTC-EUR"]["status"], "reviewed")
        self.assertEqual(tracking["ETH-EUR"]["status"], "incomplete")
        quote_rows = {row["market"]: row for row in result["quote_observations"]}
        self.assertEqual(quote_rows["BTC-EUR"]["status"], "available")
        self.assertEqual(quote_rows["ETH-EUR"]["status"], "unavailable")

    def test_position_budget_is_separate_and_overflow_is_not_a_hold(self):
        portfolio = MultiAssetPortfolio.create()
        engine = MultiAssetPaperEngine()
        for index in range(11):
            market = f"COIN{index:02d}-EUR"
            engine.execute(
                portfolio,
                MultiAssetOrder("buy", "bitvavo", market, "seed", amount_eur=D("8")),
                Quote(market, D("100"), D("100.01"), NOW),
                f"seed-{index}",
                now=NOW,
            )
        model_payload = {
            f"COIN{index:02d}-EUR": proposal(
                "hold",
                references=[f"portfolio:bitvavo|COIN{index:02d}-EUR:position"],
                market=f"COIN{index:02d}-EUR",
            )
            for index in range(10)
        }
        model_payload["BTC-EUR"] = proposal("abstain", references=[])
        result, _, _, model = self.run_cycle(portfolio=portfolio, model_payload=model_payload)
        tracking = result["position_tracking"]
        self.assertEqual(tracking["status"], "partial")
        self.assertEqual(tracking["budget"]["selected_positions"], 10)
        self.assertEqual(tracking["budget"]["omitted_positions"], 1)
        omitted = next(row for row in tracking["positions"] if row["status"] == "not_evaluated")
        self.assertEqual(omitted["reason"], "position_tracking_budget_exhausted")
        self.assertEqual(len(model.calls), 11)

    def test_entry_thesis_loaded_only_from_committed_hash_verified_purchase(self):
        portfolio = MultiAssetPortfolio.create()
        proposal_cycle_id = "a" * 24
        decision_id = f"proposal-cycle:{proposal_cycle_id}:ETH-EUR"
        MultiAssetPaperEngine().execute(
            portfolio,
            MultiAssetOrder(
                "buy", "bitvavo", "ETH-EUR", "seed", amount_eur=D("20"), provider="proposal_cycle"
            ),
            Quote("ETH-EUR", D("2000"), D("2001"), NOW),
            decision_id,
            now=NOW,
        )
        with tempfile.TemporaryDirectory() as temp_dir:
            reports = Path(temp_dir)
            cycle_dir = reports / "automatic-cycles" / ("b" * 32)
            cycle_dir.mkdir(parents=True)
            proposal_report = {
                "schema_version": "paper-proposal-cycle.v3",
                "cycle_id": proposal_cycle_id,
                "read_only": True,
                "financial_action_executed": False,
                "proposals": [
                    {
                        "market": "ETH-EUR",
                        "action": "buy",
                        "status": "accepted",
                        "rationale": "Thèse consignée à l'entrée.",
                        "references": ["scan:ETH-EUR:price_change_24h_pct"],
                        "horizon": "30 jours",
                        "invalidation_conditions": ["Fait nouveau qui contredit la thèse"],
                    }
                ],
            }
            execution_report = {
                "status": "committed",
                "transactions": [
                    {
                        "decision_id": decision_id,
                        "market": "ETH-EUR",
                        "action": "buy",
                        "status": "executed",
                    }
                ],
            }
            proposal_path = cycle_dir / "proposal.json"
            execution_path = cycle_dir / "execution.json"
            proposal_path.write_text(json.dumps(proposal_report, indent=2) + "\n")
            execution_path.write_text(json.dumps(execution_report, indent=2) + "\n")
            manifest = {
                "schema_version": "automatic-paper-cycle.v1",
                "cycle_id": "b" * 32,
                "stages": {
                    "proposal": {
                        "status": "completed",
                        "artifact": "proposal.json",
                        "sha256": hashlib.sha256(proposal_path.read_bytes()).hexdigest(),
                    },
                    "execution": {
                        "status": "completed",
                        "artifact": "execution.json",
                        "sha256": hashlib.sha256(execution_path.read_bytes()).hexdigest(),
                    },
                },
            }
            (cycle_dir / "cycle.json").write_text(json.dumps(manifest, indent=2) + "\n")
            thesis = load_verified_entry_theses(reports, portfolio)["bitvavo|ETH-EUR"]
            self.assertEqual(thesis["status"], "verified")
            self.assertEqual(thesis["rationale"], "Thèse consignée à l'entrée.")

            execution_path.write_text('{"status":"committed","transactions":[]}')
            self.assertEqual(load_verified_entry_theses(reports, portfolio), {})

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
        # A fresh market fact plus the held-position reference is enough; no new entry thesis is required.
        model_payload = proposal("reduce", references=[REF, "portfolio:bitvavo|BTC-EUR:position"])
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
        MultiAssetPaperEngine().execute(
            portfolio,
            MultiAssetOrder("buy", "bitvavo", "BTC-EUR", "seed", amount_eur=D("20")),
            Quote("BTC-EUR", D("60000"), D("60001"), NOW),
            "seed-btc",
            now=NOW,
        )
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
        self.assertEqual(result["position_tracking"]["status"], "partial")
        self.assertEqual(result["position_tracking"]["positions"][0]["status"], "incomplete")
        self.assertNotEqual(result["position_tracking"]["positions"][0]["action"], "hold")
        self.assertIsNone(result["position_tracking"]["positions"][0]["action"])
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
