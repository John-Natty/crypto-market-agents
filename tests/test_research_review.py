from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from io import StringIO
from unittest.mock import patch

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from crypto_market_agents.trading.research_review import review_research_report
from crypto_market_agents.cli import build_parser, main


def source_report():
    facts = {
        "scan:AAA-EUR:price_change_24h_pct": {
            "reference": "scan:AAA-EUR:price_change_24h_pct",
            "market": "AAA-EUR",
            "source": "bitvavo_scanner",
            "collected_at": "2026-11-09T10:01:00Z",
            "source_at": "2026-11-09T10:00:00Z",
            "value": {"value": "2.4", "unit": "%/24h"},
        },
        "history:AAA-EUR:returns_pct.7d": {
            "reference": "history:AAA-EUR:returns_pct.7d",
            "market": "AAA-EUR",
            "source": "bitvavo_historical_analysis",
            "collected_at": "2026-11-09T10:02:00Z",
            "source_at": "2026-11-09T10:00:00Z",
            "value": {
                "value": "-3.1",
                "unit": "percent",
                "horizon": "7d",
                "availability": "available",
                "freshness": {"fresh": True},
            },
        },
        "history:AAA-EUR:returns_pct.30d": {
            "reference": "history:AAA-EUR:returns_pct.30d",
            "market": "AAA-EUR",
            "source": "bitvavo_historical_analysis",
            "collected_at": "2026-11-09T10:02:00Z",
            "source_at": "2026-11-09T10:00:00Z",
            "value": {
                "value": "5.0",
                "unit": "percent",
                "horizon": "30d",
                "availability": "available",
                "freshness": {"fresh": False},
            },
        },
        "news:AAA-EUR:article:story": {
            "reference": "news:AAA-EUR:article:story",
            "market": "AAA-EUR",
            "source": "rss_feed",
            "collected_at": "2026-11-09T10:02:00Z",
            "source_at": "2026-11-08T12:00:00Z",
            "value": {
                "evidence_class": "reported_source_content",
                "title": "Publisher reports a protocol update",
                "publisher": "Example News",
                "published_at": "2026-11-08T12:00:00Z",
                "external_content_untrusted": True,
            },
        },
    }
    missing = {
        "history:AAA-EUR:sma50_close": {
            "reference": "history:AAA-EUR:sma50_close",
            "market": "AAA-EUR",
            "availability": "unavailable",
            "reason": "gap inside the required window",
            "source": "bitvavo_historical_analysis",
            "collected_at": "2026-11-09T10:02:00Z",
        }
    }
    refs = list(facts)
    return {
        "schema_version": "autonomous-research.v6",
        "generated_at": "2026-11-09T10:03:00Z",
        "objective": "Assess whether the thesis is supported by the observed evidence.",
        "read_only": True,
        "financial_action_executed": False,
        "model": "qwen3:8b",
        "conclusion": {
            "candidates": [
                {
                    "market": "AAA-EUR",
                    "status": "partial",
                    "interpretation": "The weekly fall may challenge the thesis.",
                    "conclusion": "Review the thesis after new evidence.",
                    "fact_refs": refs,
                    "missing_refs": list(missing),
                    "required_missing_refs": [],
                }
            ]
        },
        "fact_ledger": facts,
        "missing_ledger": missing,
    }


def review_payload(
    *,
    verdict="contested",
    arguments=None,
    missing=None,
    conditions=None,
):
    return {
        "reviews": [
            {
                "market": "AAA-EUR",
                "arguments": arguments or [],
                "missing_refs": missing or [],
                "invalidation_conditions": conditions or [],
                "verdict": verdict,
            }
        ]
    }


class FakeOllama:
    def __init__(self, content=None, *, done_reason="stop", error=None):
        self.content = content
        self.done_reason = done_reason
        self.error = error
        self.calls = []

    def chat_with_tools(self, messages, tools, **kwargs):
        self.calls.append((messages, tools, kwargs))
        if self.error:
            raise self.error
        return {
            "message": {"content": json.dumps(self.content), "tool_calls": []},
            "done_reason": self.done_reason,
            "generation": {"prompt_eval_count": 700, "eval_count": 350},
            "duration_ms": 4200,
            "request_bytes": 5000,
            "response_bytes": 1400,
            "think": False,
        }


class ResearchReviewTests(unittest.TestCase):
    def run_review(self, payload, *, done_reason="stop", error=None):
        report = source_report()
        raw = json.dumps(report, sort_keys=True).encode()
        model = FakeOllama(payload, done_reason=done_reason, error=error)
        result = review_research_report(
            report,
            model,
            source_sha256=hashlib.sha256(raw).hexdigest(),
            source_label="frozen-source.json",
            model="qwen3:8b",
            now_provider=lambda: datetime(2026, 11, 9, 11, tzinfo=timezone.utc),
            monotonic=iter([1.0, 5.2]).__next__,
        )
        return result, model, report, raw

    def test_supported_and_counterevidence_are_rendered_from_references(self):
        payload = review_payload(
            arguments=[
                {
                    "reference": "scan:AAA-EUR:price_change_24h_pct",
                    "role": "favorable",
                    "justification": "Momentum is positive, but this alone does not prove quality.",
                },
                {
                    "reference": "history:AAA-EUR:returns_pct.7d",
                    "role": "unfavorable",
                    "justification": "The weekly return is negative.",
                },
            ],
            missing=["history:AAA-EUR:sma50_close"],
            conditions=[
                {
                    "reference": "scan:AAA-EUR:price_change_24h_pct",
                    "condition": "value_falls_below_zero",
                },
                {
                    "reference": "news:AAA-EUR:article:story",
                    "condition": "source_claim_is_retracted",
                },
            ],
        )
        result, model, original, raw = self.run_review(payload)
        candidate = result["review"]["candidates"][0]
        favorable = candidate["favorable_arguments"][0]["rendered_observation"]
        adverse = candidate["unfavorable_arguments"][0]["rendered_observation"]
        self.assertEqual(result["status"], "completed")
        self.assertEqual(candidate["verdict"], "contested")
        self.assertEqual(candidate["favorable_arguments"][0]["argument_index"], 0)
        self.assertEqual(candidate["unfavorable_arguments"][0]["argument_index"], 1)
        self.assertEqual(favorable["value"], "2.4")
        self.assertEqual(favorable["unit"], "%/24h")
        self.assertTrue(favorable["computed_relations"]["positive"])
        self.assertTrue(favorable["computed_relations"]["greater_than_one_in_displayed_unit"])
        self.assertFalse(favorable["computed_relations"]["less_than_one_in_displayed_unit"])
        self.assertEqual(favorable["numeric_relation_contract"], "numeric-relations.v1")
        self.assertEqual(adverse["horizon"], "7d")
        self.assertEqual(candidate["information_to_obtain"][0]["availability"], "unavailable")
        self.assertIn(
            "ne prouve pas un défaut", candidate["information_to_obtain"][0]["interpretation"]
        )
        self.assertTrue(all(item["hypothesis"] for item in candidate["invalidation_conditions"]))
        self.assertEqual(model.calls[0][1], [])  # No tools, collectors, or finance operations.
        self.assertFalse(result["financial_action_executed"])
        self.assertEqual(json.dumps(original, sort_keys=True).encode(), raw)

    def test_divergence_from_first_conclusion_is_allowed_without_forcing_a_strategy(self):
        payload = review_payload(
            verdict="supported",
            arguments=[
                {
                    "reference": "scan:AAA-EUR:price_change_24h_pct",
                    "role": "favorable",
                    "justification": "Could support the thesis, but price momentum alone is limited.",
                }
            ],
        )
        result, _, _, _ = self.run_review(payload)
        self.assertEqual(result["status"], "completed")
        self.assertEqual(result["review"]["candidates"][0]["verdict"], "supported")

    def test_invalid_reference_is_retained_as_rejection_and_valid_argument_survives(self):
        payload = review_payload(
            verdict="supported",
            arguments=[
                {
                    "reference": "scan:AAA-EUR:price_change_24h_pct",
                    "role": "favorable",
                    "justification": "Positive market observation.",
                },
                {
                    "reference": "scan:AAA-EUR:invented",
                    "role": "favorable",
                    "justification": "Invented metric.",
                },
            ],
        )
        result, _, _, _ = self.run_review(payload)
        candidate = result["review"]["candidates"][0]
        self.assertEqual(result["status"], "partial")
        self.assertEqual(len(candidate["favorable_arguments"]), 1)
        self.assertEqual(
            candidate["rejected_arguments"][0]["reason"], "review_reference_not_in_source_scope"
        )
        self.assertEqual(candidate["verdict"], "insufficient")

    def test_invalid_global_verdict_does_not_discard_validated_arguments(self):
        payload = review_payload(
            verdict="supported",
            arguments=[
                {
                    "reference": "scan:AAA-EUR:price_change_24h_pct",
                    "role": "favorable",
                    "justification": "Observed price change only.",
                }
            ],
        )
        payload["reviews"][0]["verdict"] = "buy"
        result, _, _, _ = self.run_review(payload)
        candidate = result["review"]["candidates"][0]
        self.assertEqual(result["status"], "partial")
        self.assertEqual(len(candidate["favorable_arguments"]), 1)
        self.assertEqual(candidate["rejected_verdict"]["reason"], "review_verdict_invalid")
        self.assertEqual(candidate["verdict"], "insufficient")

    def test_model_receives_code_computed_relations_and_cannot_choose_them(self):
        payload = review_payload(
            verdict="supported",
            arguments=[
                {
                    "reference": "scan:AAA-EUR:price_change_24h_pct",
                    "role": "uncertain",
                    "justification": "Positive price movement is not proof of quality.",
                }
            ],
        )
        result, model, _, _ = self.run_review(payload)
        candidate = result["review"]["candidates"][0]
        request = json.loads(model.calls[0][0][1]["content"])
        observation = request["candidates"][0]["evidence"][0]["observation"]
        self.assertEqual(result["status"], "completed")
        self.assertTrue(observation["computed_relations"]["positive"])
        self.assertFalse(observation["computed_relations"]["less_than_one_in_displayed_unit"])
        self.assertNotIn(
            "relation",
            model.calls[0][2]["format_schema"]["properties"]["reviews"]["items"]["properties"][
                "arguments"
            ]["items"]["properties"],
        )
        self.assertEqual(candidate["verdict"], "insufficient")
        self.assertEqual(candidate["proposed_verdict"], "supported")

    def test_invalid_invalidation_condition_is_diagnosed_without_losing_valid_arguments(self):
        payload = review_payload(
            verdict="supported",
            arguments=[
                {
                    "reference": "scan:AAA-EUR:price_change_24h_pct",
                    "role": "favorable",
                    "justification": "Positive observed move, not proof of quality.",
                }
            ],
            conditions=[
                {
                    "reference": "scan:AAA-EUR:price_change_24h_pct",
                    "condition": "evidence_remains_unavailable",
                }
            ],
        )
        result, _, _, _ = self.run_review(payload)
        candidate = result["review"]["candidates"][0]
        self.assertEqual(result["status"], "partial")
        self.assertEqual(len(candidate["favorable_arguments"]), 1)
        self.assertEqual(
            candidate["rejected_invalidation_conditions"][0]["reason"],
            "review_missing_information_is_not_invalidation",
        )
        self.assertEqual(candidate["verdict"], "insufficient")

    def test_missing_information_is_a_research_need_not_a_thesis_invalidation(self):
        payload = review_payload(
            verdict="supported",
            arguments=[
                {
                    "reference": "scan:AAA-EUR:price_change_24h_pct",
                    "role": "favorable",
                    "justification": "Observed price move only.",
                }
            ],
            missing=["history:AAA-EUR:sma50_close"],
            conditions=[
                {
                    "reference": "history:AAA-EUR:sma50_close",
                    "condition": "metric_becomes_stale",
                }
            ],
        )
        result, _, _, _ = self.run_review(payload)
        candidate = result["review"]["candidates"][0]
        self.assertEqual(result["status"], "partial")
        self.assertEqual(
            candidate["information_to_obtain"][0]["reference"], "history:AAA-EUR:sma50_close"
        )
        self.assertEqual(
            candidate["rejected_invalidation_conditions"][0]["reason"],
            "review_missing_information_is_not_invalidation",
        )
        self.assertEqual(candidate["verdict"], "insufficient")

    def test_stale_fact_and_missing_data_are_distinguished(self):
        payload = review_payload(
            verdict="insufficient",
            arguments=[
                {
                    "reference": "history:AAA-EUR:returns_pct.30d",
                    "role": "unfavorable",
                    "justification": "This observation is stale.",
                }
            ],
            missing=["history:AAA-EUR:sma50_close"],
        )
        result, _, _, _ = self.run_review(payload)
        candidate = result["review"]["candidates"][0]
        self.assertEqual(candidate["unfavorable_arguments"], [])
        self.assertEqual(candidate["rejected_arguments"][0]["reason"], "review_observation_stale")
        self.assertEqual(candidate["information_to_obtain"][0]["availability"], "unavailable")

    def test_reported_news_is_marked_as_a_publisher_claim_not_verified_fact(self):
        payload = review_payload(
            verdict="insufficient",
            arguments=[
                {
                    "reference": "news:AAA-EUR:article:story",
                    "role": "uncertain",
                    "justification": "The publisher reports an update; it is not independently confirmed.",
                }
            ],
        )
        result, _, _, _ = self.run_review(payload)
        item = result["review"]["candidates"][0]["uncertain_arguments"][0]
        self.assertEqual(item["assessment_type"], "model_interpretation_not_certified")
        self.assertEqual(item["rendered_observation"]["publisher"], "Example News")

    def test_error_and_truncation_are_unavailable(self):
        payload = review_payload(
            verdict="supported",
            arguments=[
                {
                    "reference": "scan:AAA-EUR:price_change_24h_pct",
                    "role": "favorable",
                    "justification": "Price moved higher.",
                }
            ],
        )
        errored, _, _, _ = self.run_review(payload, error=TimeoutError())
        truncated, _, _, _ = self.run_review(payload, done_reason="length")
        self.assertEqual(errored["status"], "unavailable")
        self.assertEqual(errored["review"], None)
        self.assertEqual(truncated["status"], "unavailable")
        self.assertIn("review_generation_truncated", truncated["errors"])
        self.assertFalse(truncated["financial_action_executed"])

    def test_cli_writes_a_separate_review_and_leaves_source_unchanged(self):
        payload = review_payload(
            verdict="supported",
            arguments=[
                {
                    "reference": "scan:AAA-EUR:price_change_24h_pct",
                    "role": "favorable",
                    "justification": "Price moved higher.",
                }
            ],
        )
        model = FakeOllama(payload)
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "source.json"
            output = Path(directory) / "review.json"
            source.write_text(json.dumps(source_report()), encoding="utf-8")
            before = source.read_bytes()
            parsed = build_parser().parse_args(
                ["review-research", "--input-json", str(source), "--json", str(output)]
            )
            self.assertEqual(parsed.model, "qwen3:8b")
            with patch("crypto_market_agents.cli.OllamaCandidateEvaluator", return_value=model):
                stdout = StringIO()
                with redirect_stdout(stdout):
                    code = main(
                        ["review-research", "--input-json", str(source), "--json", str(output)]
                    )
            self.assertEqual(code, 0)
            self.assertTrue(output.exists())
            self.assertEqual(source.read_bytes(), before)
            saved = json.loads(output.read_text(encoding="utf-8"))
            self.assertEqual(saved["status"], "completed")
            self.assertFalse(saved["financial_action_executed"])


if __name__ == "__main__":
    unittest.main()
