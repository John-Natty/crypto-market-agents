from contextlib import redirect_stdout
from datetime import datetime, timezone
from io import StringIO
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from crypto_market_agents.cli import main
from crypto_market_agents.trading.evaluation import (
    DeterministicEvaluator,
    OllamaCandidateEvaluator,
    build_snapshot,
    compare_evaluators,
    validate_evaluation,
)


NOW = datetime(2026, 11, 7, 12, tzinfo=timezone.utc)


def analysis_report():
    return {
        "generated_at": NOW.isoformat(),
        "sources": [{"name": "Bitvavo REST candles", "retrieved_at": NOW.isoformat()}],
        "interval": "1h",
        "limitations": ["historical only"],
        "candidates": [
            {
                "market": "AAA-EUR",
                "observations": [{"timestamp": NOW.isoformat(), "close": "10", "volume_base": "2"}],
                "metrics": {
                    "returns_pct": {"24h": "2", "7d": "3", "30d": "-1"},
                    "sma20_close": "0.3045",
                    "sma50_close": "0.2871",
                    "close_vs_sma20_pct": "2.4",
                    "sma20_vs_sma50_pct": "1",
                    "latest_volume_base": "1200",
                    "latest_volume_vs_previous_20_mean": "0.8",
                    "return_volatility_pct_per_interval": "1.2",
                },
                "interval": "1h",
                "scanner_candidate": {
                    "market": "AAA-EUR",
                    "base_asset": "AAA",
                    "quote_asset": "EUR",
                    "source_at": NOW.isoformat(),
                },
                "data_sufficiency": {"sufficient": True},
                "data_quality": {"fresh": True, "gaps": [], "invalid_rows": []},
                "metric_quality": {
                    **{
                        f"returns_pct.{horizon}": {
                            "available": True,
                            "freshness": {"fresh": True},
                            "window": {"start_at": NOW.isoformat(), "end_at": NOW.isoformat()},
                            "timestamps_used": [NOW.isoformat()],
                            "observation_count": 1,
                            "reason": None,
                        }
                        for horizon in ("24h", "7d", "30d")
                    },
                    "sma20_vs_sma50_pct": {
                        "available": True,
                        "freshness": {"fresh": True},
                        "window": {"start_at": NOW.isoformat(), "end_at": NOW.isoformat()},
                        "timestamps_used": [NOW.isoformat()],
                        "observation_count": 1,
                        "reason": None,
                    },
                    **{
                        name: {
                            "available": True,
                            "freshness": {"fresh": True, "age_seconds": 10},
                            "window": {
                                "start_at": NOW.isoformat(),
                                "end_at": NOW.isoformat(),
                                "target_start_at": None,
                            },
                            "timestamps_used": [NOW.isoformat()],
                            "observation_count": 1,
                            "reason": None,
                        }
                        for name in (
                            "sma20_close",
                            "sma50_close",
                            "close_vs_sma20_pct",
                            "latest_volume_base",
                            "latest_volume_vs_previous_20_mean",
                            "return_volatility_pct_per_interval",
                        )
                    },
                },
                "favorable_signals": ["24h positive"],
                "risks_and_contradictions": [],
                "reasons": [],
            }
        ],
    }


def model_row(
    market="AAA-EUR",
    classification="watch",
    *,
    metric="returns_pct.24h",
    relation="positive",
    role="favorable",
    summary="Interprétation qualitative non certifiée.",
):
    return {
        "market": market,
        "classification": classification,
        "arguments": [
            {
                "metric_id": metric,
                "relation": relation,
                "reference": f"{market}.metrics#{metric}",
                "role": role,
            }
        ],
        "summary": summary,
    }


class SnapshotAndValidationTests(unittest.TestCase):
    def test_audited_markets_render_metric_specific_facts(self):
        from crypto_market_agents.trading.evaluation import (
            build_candidate_limits,
            build_candidate_observations,
            verify_snapshot,
        )

        path = (
            Path(__file__).resolve().parents[1]
            / "reports/metric-window-v2-release/analysis-snapshot.json"
        )
        snapshot = json.loads(path.read_text(encoding="utf-8"))
        self.assertTrue(verify_snapshot(snapshot))
        self.assertEqual(
            snapshot["content_sha256"],
            "3034e178250966ffc40b2f78b629381350c87ce61b086bd7f7d9736d48dada30",
        )
        candidates = {item["market"].split("-")[0]: item for item in snapshot["candidates"]}

        def facts(symbol):
            return {row["metric"]: row for row in build_candidate_observations(candidates[symbol])}

        rsr = facts("RSR")
        self.assertEqual(rsr["latest_volume_base"]["value"], "50464677.30400001")
        self.assertEqual(rsr["latest_volume_base"]["unit"], "RSR (base asset units)")
        self.assertEqual(rsr["latest_volume_vs_previous_20_mean"]["availability"], "unavailable")
        self.assertEqual(rsr["sma20_vs_sma50_pct"]["availability"], "unavailable")

        icnt = facts("ICNT")
        self.assertEqual(icnt["sma20_vs_sma50_pct"]["value"], "6.0528")
        self.assertEqual(icnt["latest_volume_vs_previous_20_mean"]["value"], "0.3242")
        self.assertEqual(icnt["latest_volume_base"]["unit"], "ICNT (base asset units)")

        o = facts("O")
        self.assertEqual(o["returns_pct.24h"]["availability"], "available")
        self.assertEqual(o["returns_pct.7d"]["availability"], "unavailable")
        self.assertIn("horodatage exact", o["returns_pct.7d"]["reason"])
        self.assertEqual(o["returns_pct.30d"]["availability"], "available")

        hmstr = facts("HMSTR")
        self.assertEqual(hmstr["sma20_close"]["availability"], "available")
        self.assertEqual(hmstr["sma50_close"]["availability"], "unavailable")
        self.assertEqual(hmstr["sma20_vs_sma50_pct"]["availability"], "unavailable")

        pepe = facts("PEPE")
        self.assertEqual(pepe["returns_pct.24h"]["value"], "3.3393")
        self.assertEqual(pepe["sma20_vs_sma50_pct"]["value"], "-0.2582")
        self.assertEqual(pepe["close_vs_sma20_pct"]["value"], "2.3817")
        self.assertEqual(pepe["latest_volume_base"]["value"], "31017111911")
        self.assertEqual(pepe["latest_volume_vs_previous_20_mean"]["value"], "2.0307")
        pepe_limits = build_candidate_limits(candidates["PEPE"])
        self.assertEqual(pepe_limits["sma20_sma50_relationship"]["state"], "mixed")
        self.assertEqual(pepe_limits["sma20_slope"]["status"], "not_measured")

    def test_fact_rendering_preserves_units_windows_and_quality_states(self):
        from crypto_market_agents.trading.evaluation import (
            build_candidate_limits,
            build_candidate_observations,
        )

        candidate = analysis_report()["candidates"][0]
        candidate["metric_quality"]["returns_pct.24h"]["window"] = {
            "start_at": "2026-11-06T12:00:00+00:00",
            "end_at": "2026-11-07T12:00:00+00:00",
            "target_start_at": "2026-11-06T12:00:00+00:00",
        }
        candidate["metric_quality"]["sma20_vs_sma50_pct"].update(
            available=False, reason="fenêtre contiguë interrompue"
        )
        candidate["metrics"]["sma20_vs_sma50_pct"] = None
        candidate["metric_quality"]["close_vs_sma20_pct"]["freshness"]["fresh"] = False
        candidate["metrics"]["latest_volume_base"] = "NaN"
        candidate["data_quality"]["invalid_data"] = ["bougie rejetée"]
        candidate["data_quality"]["gaps"] = [
            {
                "from": "2026-11-07T00:00:00+00:00",
                "to": "2026-11-07T02:00:00+00:00",
                "missing_intervals": 1,
                "cause": "unknown",
            }
        ]

        facts = {item["metric"]: item for item in build_candidate_observations(candidate)}
        self.assertEqual(facts["returns_pct.24h"]["value"], "2")
        self.assertEqual(facts["returns_pct.24h"]["unit"], "percent")
        self.assertEqual(facts["returns_pct.24h"]["horizon"], "24h")
        self.assertEqual(
            facts["returns_pct.24h"]["window"]["start_at"], "2026-11-06T12:00:00+00:00"
        )
        self.assertEqual(facts["sma20_close"]["unit"], "EUR per AAA")
        self.assertEqual(facts["latest_volume_base"]["unit"], "AAA (base asset units)")
        self.assertEqual(
            facts["latest_volume_vs_previous_20_mean"]["unit"],
            "multiple of the previous 20 candle mean",
        )
        self.assertEqual(facts["sma20_vs_sma50_pct"]["availability"], "unavailable")
        self.assertEqual(facts["close_vs_sma20_pct"]["availability"], "stale")
        self.assertEqual(facts["latest_volume_base"]["availability"], "invalid")

        limits = build_candidate_limits(candidate)
        self.assertEqual(limits["observed_gaps"]["cause"], "unknown")
        self.assertIn(
            "AAA-EUR.metrics#sma20_vs_sma50_pct",
            limits["observed_gaps"]["affected_metric_refs"],
        )
        self.assertEqual(limits["invalid_observations"], ["bougie rejetée"])
        self.assertEqual(limits["sma20_slope"]["status"], "not_measured")

    def test_pepe_mixed_moving_average_position_does_not_claim_a_slope(self):
        from crypto_market_agents.trading.evaluation import build_candidate_limits

        candidate = analysis_report()["candidates"][0]
        candidate["metrics"]["sma20_vs_sma50_pct"] = "-0.2582"
        candidate["metrics"]["close_vs_sma20_pct"] = "2.3817"
        limits = build_candidate_limits(candidate)
        self.assertEqual(limits["sma20_sma50_relationship"]["state"], "mixed")
        self.assertEqual(limits["sma20_slope"]["status"], "not_measured")

    def test_snapshot_hash_stable_and_content_sensitive(self):
        first = build_snapshot(analysis_report(), snapshot_id="fixed", created_at=NOW)
        second = build_snapshot(analysis_report(), snapshot_id="fixed", created_at=NOW)
        self.assertEqual(first["content_sha256"], second["content_sha256"])
        changed = analysis_report()
        changed["candidates"][0]["metrics"]["returns_pct"]["24h"] = "3"
        third = build_snapshot(changed, snapshot_id="fixed", created_at=NOW)
        self.assertNotEqual(first["content_sha256"], third["content_sha256"])
        self.assertNotIn("content_sha256", json.loads(json.dumps(first["candidates"][0])))

    def test_strict_categories_and_snapshot_references(self):
        snapshot = build_snapshot(analysis_report(), snapshot_id="fixed", created_at=NOW)
        valid = {"candidates": [model_row()]}
        self.assertEqual(
            validate_evaluation(valid, snapshot)["candidates"][0]["classification"], "watch"
        )
        for bad in (
            {"candidates": [{**valid["candidates"][0], "classification": "buy"}]},
            {"candidates": [{**valid["candidates"][0], "market": "BBB-EUR"}]},
            {
                "candidates": [
                    {
                        **valid["candidates"][0],
                        "arguments": [
                            {
                                "metric_id": "returns_pct.24h",
                                "relation": "positive",
                                "reference": "BBB-EUR.metrics#returns_pct.24h",
                                "role": "favorable",
                            }
                        ],
                    }
                ]
            },
            {"candidates": [{**valid["candidates"][0], "extra": "ignore"}]},
        ):
            with self.assertRaises(ValueError):
                validate_evaluation(bad, snapshot)

    def test_structured_arguments_carry_snapshot_data_and_summary_is_uncertified(self):
        snapshot = build_snapshot(analysis_report(), snapshot_id="fixed", created_at=NOW)
        summary = (
            "SMA20 est au-dessus de SMA50; returns_pct.24h et 7d positifs, "
            "sma20_vs_sma50_pct disponible."
        )
        validated = validate_evaluation({"candidates": [model_row(summary=summary)]}, snapshot)
        self.assertEqual(validated["candidates"][0]["summary"], summary)
        rendered = DeterministicEvaluator().evaluate(snapshot).results[0]
        self.assertEqual(rendered["summary_status"], "uncertified_model_text")
        self.assertEqual(rendered["arguments"][0]["observation"]["value"], "2")
        self.assertEqual(rendered["arguments"][0]["observation"]["unit"], "percent")
        self.assertEqual(rendered["arguments"][0]["observation"]["horizon"], "24h")
        for summary in (
            "SMA20 is above SMA50, with a 13.3077 percent return.",
            "The volume is 31017111911 base units.",
        ):
            with (
                self.subTest(summary=summary),
                self.assertRaisesRegex(ValueError, "valeur numérique"),
            ):
                validate_evaluation({"candidates": [model_row(summary=summary)]}, snapshot)

    def test_structured_argument_rejects_unsupported_relation_and_mismatched_ref(self):
        snapshot = build_snapshot(analysis_report(), snapshot_id="fixed", created_at=NOW)
        wrong_relation = model_row(relation="negative")
        wrong_ref = model_row()
        wrong_ref["arguments"][0]["reference"] = "AAA-EUR.metrics#returns_pct.7d"
        for row in (wrong_relation, wrong_ref):
            with self.assertRaises(ValueError):
                validate_evaluation({"candidates": [row]}, snapshot)

    def test_limitation_role_is_required_only_for_unusable_metric_quality(self):
        report = analysis_report()
        candidate = report["candidates"][0]
        candidate["metric_quality"]["sma20_vs_sma50_pct"]["available"] = False
        candidate["metrics"]["sma20_vs_sma50_pct"] = None
        snapshot = build_snapshot(report, snapshot_id="missing-sma", created_at=NOW)
        limited = model_row(
            classification="abstain",
            metric="sma20_vs_sma50_pct",
            relation="unavailable",
            role="limitation",
        )
        result = validate_evaluation({"candidates": [limited]}, snapshot)
        self.assertEqual(result["candidates"][0]["arguments"][0]["relation"], "unavailable")
        invalid_role = json.loads(json.dumps(limited))
        invalid_role["arguments"][0]["role"] = "unfavorable"
        with self.assertRaisesRegex(ValueError, "rôle limite"):
            validate_evaluation({"candidates": [invalid_role]}, snapshot)

    def test_pepe_sma_argument_must_match_snapshot_sign_not_a_strategy_label(self):
        from crypto_market_agents.trading.evaluation import verify_snapshot

        snapshot = json.loads(
            (
                Path(__file__).resolve().parents[1]
                / "reports/metric-window-v2-release/analysis-snapshot.json"
            ).read_text(encoding="utf-8")
        )
        self.assertTrue(verify_snapshot(snapshot))
        row = {
            "market": "PEPE-EUR",
            "classification": "abstain",
            "arguments": [
                {
                    "metric_id": "sma20_vs_sma50_pct",
                    "relation": "positive",
                    "reference": "PEPE-EUR.metrics#sma20_vs_sma50_pct",
                    "role": "favorable",
                }
            ],
            "summary": "Moyennes mobiles en position mixte.",
        }
        with self.assertRaisesRegex(ValueError, "non étayée"):
            validate_evaluation({"candidates": [row]}, snapshot, markets=["PEPE-EUR"])
        row["arguments"][0]["relation"] = "negative"
        accepted = validate_evaluation({"candidates": [row]}, snapshot, markets=["PEPE-EUR"])
        self.assertEqual(accepted["candidates"][0]["classification"], "abstain")

    def test_deterministic_result_renders_snapshot_observations_separately(self):
        snapshot = build_snapshot(analysis_report(), snapshot_id="fixed", created_at=NOW)
        row = DeterministicEvaluator().evaluate(snapshot).results[0]
        self.assertEqual(row["classification"], row["label"])
        self.assertEqual(row["evaluation_status"], "success")
        self.assertTrue(row["summary"])
        self.assertTrue(row["arguments"])
        self.assertEqual(row["summary_status"], "uncertified_model_text")
        self.assertIn("sma20_vs_sma50_pct", {item["metric"] for item in row["observations"]})
        self.assertIn("limitations", row)

    def test_watch_label_is_rejected_when_rule_metrics_are_missing(self):
        report = analysis_report()
        report["candidates"][0]["metric_quality"]["sma20_vs_sma50_pct"]["available"] = False
        snapshot = build_snapshot(report, snapshot_id="fixed", created_at=NOW)
        output = {"candidates": [model_row()]}
        with self.assertRaisesRegex(ValueError, "métriques disponibles"):
            validate_evaluation(output, snapshot)

    def test_both_evaluators_receive_same_canonical_snapshot(self):
        snapshot = build_snapshot(analysis_report(), snapshot_id="fixed", created_at=NOW)
        received = []

        class Capture:
            def evaluate(self, value):
                received.append(json.dumps(value, sort_keys=True))
                return DeterministicEvaluator().evaluate(value)

        compare_evaluators(snapshot, Capture(), Capture())
        self.assertEqual(received[0], received[1])
        self.assertEqual(json.loads(received[0])["content_sha256"], snapshot["content_sha256"])

    def test_snapshot_v2_is_required_for_metric_window_contract(self):
        snapshot = build_snapshot(analysis_report(), snapshot_id="fixed", created_at=NOW)
        self.assertEqual(snapshot["schema_version"], "market-analysis-snapshot.v2")

    def test_deterministic_rule_uses_usable_metrics_despite_old_gap(self):
        report = analysis_report()
        candidate = report["candidates"][0]
        candidate["data_quality"]["gaps"] = [{"from": "old", "to": "old", "cause": "unknown"}]
        candidate["data_sufficiency"]["sufficient"] = False
        snapshot = build_snapshot(report, snapshot_id="fixed", created_at=NOW)
        result = DeterministicEvaluator().evaluate(snapshot)
        self.assertEqual(result.results[0]["classification"], "watch")
        self.assertEqual(result.results[0]["summary_status"], "uncertified_model_text")
        self.assertIn("returns_pct", result.to_dict()["rule_requirements"]["watch"])

    def test_deterministic_rule_abstains_when_required_metric_is_unavailable(self):
        report = analysis_report()
        candidate = report["candidates"][0]
        candidate["metric_quality"]["sma20_vs_sma50_pct"]["available"] = False
        candidate["metric_quality"]["sma20_vs_sma50_pct"]["reason"] = "fenêtre SMA50 interrompue"
        snapshot = build_snapshot(report, snapshot_id="fixed", created_at=NOW)
        result = DeterministicEvaluator().evaluate(snapshot)
        self.assertEqual(result.results[0]["classification"], "abstain")
        self.assertEqual(result.results[0]["abstention_reason"], "required_data_unavailable")

    def test_deterministic_rule_abstains_with_fewer_than_two_usable_returns(self):
        report = analysis_report()
        candidate = report["candidates"][0]
        for horizon in ("7d", "30d"):
            candidate["metric_quality"][f"returns_pct.{horizon}"]["available"] = False
            candidate["metric_quality"][f"returns_pct.{horizon}"]["reason"] = "exact target absent"
        snapshot = build_snapshot(report, snapshot_id="fixed", created_at=NOW)
        result = DeterministicEvaluator().evaluate(snapshot)
        self.assertEqual(result.results[0]["classification"], "abstain")
        self.assertEqual(result.results[0]["abstention_reason"], "required_data_unavailable")

    def test_abstention_reason_distinguishes_missing_data_from_judgment(self):
        complete = build_snapshot(analysis_report(), snapshot_id="complete", created_at=NOW)
        judgment = model_row(classification="abstain", role="favorable")
        self.assertEqual(
            validate_evaluation({"candidates": [judgment]}, complete)["candidates"][0][
                "abstention_reason"
            ],
            "judgment",
        )
        report = analysis_report()
        candidate = report["candidates"][0]
        candidate["metric_quality"]["sma20_vs_sma50_pct"]["available"] = False
        candidate["metrics"]["sma20_vs_sma50_pct"] = None
        missing = build_snapshot(report, snapshot_id="missing", created_at=NOW)
        forced = model_row(
            classification="abstain",
            metric="sma20_vs_sma50_pct",
            relation="unavailable",
            role="limitation",
        )
        self.assertEqual(
            validate_evaluation({"candidates": [forced]}, missing)["candidates"][0][
                "abstention_reason"
            ],
            "required_data_unavailable",
        )
        invented = {**judgment, "abstention_reason": "required_data_unavailable"}
        with self.assertRaisesRegex(ValueError, "Format de candidat"):
            validate_evaluation({"candidates": [invented]}, complete)
        wrong_not_applicable = {**judgment, "abstention_reason": "not_applicable"}
        with self.assertRaisesRegex(ValueError, "Format de candidat"):
            validate_evaluation({"candidates": [wrong_not_applicable]}, complete)


class OllamaAndCliTests(unittest.TestCase):
    def test_timeout_invalid_response_and_unavailable_are_abstentions(self):
        snapshot = build_snapshot(analysis_report(), snapshot_id="fixed", created_at=NOW)
        for failure in (TimeoutError(), OSError("missing")):
            evaluator = OllamaCandidateEvaluator(
                "local-model", opener=lambda *_a, **_k: (_ for _ in ()).throw(failure), timeout=1
            )
            result = evaluator.evaluate(snapshot)
            self.assertEqual(result.status, "error")
            self.assertIsNone(result.results[0]["classification"])
            self.assertEqual(result.results[0]["evaluation_status"], "error")
            self.assertIsNotNone(result.error)

        class Response:
            def __enter__(self):
                return self

            def __exit__(self, *args):
                pass

            def read(self, _limit):
                return json.dumps({"done": True, "message": {"content": "not json"}}).encode()

        result = OllamaCandidateEvaluator(
            "local-model", opener=lambda *_a, **_k: Response()
        ).evaluate(snapshot)
        self.assertIsNone(result.results[0]["classification"])
        self.assertEqual(result.results[0]["evaluation_status"], "error")
        self.assertEqual(result.error, "invalid_model_json")

    def test_failure_diagnostics_distinguish_json_validation_and_truncation(self):
        snapshot = build_snapshot(analysis_report(), snapshot_id="fixed", created_at=NOW)

        class Response:
            def __init__(self, payload):
                self.payload = payload

            def __enter__(self):
                return self

            def __exit__(self, *args):
                pass

            def read(self, _limit):
                return self.payload

        def run(payload):
            return OllamaCandidateEvaluator(
                "local-model", opener=lambda *_a, **_k: Response(payload)
            ).evaluate(snapshot)

        invalid_outer_json = run(b"{")
        self.assertEqual(invalid_outer_json.error, "invalid_response_json")
        invalid_content = run(json.dumps({"done": True, "message": {"content": "{"}}).encode())
        self.assertEqual(invalid_content.error, "invalid_model_json")
        self.assertGreater(invalid_content.to_dict()["diagnostics"]["response_chars"], 0)
        self.assertIsNone(invalid_content.to_dict()["diagnostics"]["done_reason"])
        bad_refs = {"candidates": [model_row()]}
        bad_refs["candidates"][0]["arguments"][0]["reference"] = (
            "SECRET-EUR.metrics#returns_pct.24h"
        )
        rejected = run(
            json.dumps({"done": True, "message": {"content": json.dumps(bad_refs)}}).encode()
        )
        self.assertEqual(rejected.error, "invalid_structured_argument")
        too_large = run(b" " * 200_001)
        self.assertEqual(too_large.error, "response_too_large")

        truncated = run(
            json.dumps(
                {"done": True, "done_reason": "length", "message": {"content": "{"}}
            ).encode()
        )
        self.assertEqual(truncated.error, "truncated_model_json")

    def test_prompt_omits_raw_observations_and_sends_all_candidates(self):
        snapshot = build_snapshot(analysis_report(), snapshot_id="fixed", created_at=NOW)
        captured = {}

        class Response:
            def __enter__(self):
                return self

            def __exit__(self, *args):
                pass

            def read(self, _limit):
                return json.dumps({"done": True, "message": {"content": "{}"}}).encode()

        def opener(request, **_kwargs):
            captured.update(json.loads(request.data))
            return Response()

        OllamaCandidateEvaluator("local-model", opener=opener).evaluate(snapshot)
        user_payload = json.loads(captured["messages"][1]["content"])
        self.assertEqual(len(user_payload["candidates"]), 1)
        self.assertIn("observations", user_payload["candidates"][0])
        self.assertNotIn("scanner_candidate", user_payload["candidates"][0])
        self.assertIn("observed_gaps", user_payload["candidates"][0]["limitations"])
        self.assertIn("availability", json.dumps(user_payload["candidates"][0]))
        self.assertIn("admissibility_requirements", user_payload)
        self.assertIs(captured["think"], False)
        self.assertEqual(captured["options"]["num_predict"], 1200)

    def test_generation_budget_is_bounded_and_metadata_is_sanitized(self):
        for invalid in (127, 8193, True, 2400.0):
            with self.subTest(invalid=invalid), self.assertRaises(ValueError):
                OllamaCandidateEvaluator("local-model", num_predict=invalid)

        snapshot = build_snapshot(analysis_report(), snapshot_id="fixed", created_at=NOW)
        captured = {}

        class Response:
            def __enter__(self):
                return self

            def __exit__(self, *args):
                pass

            def read(self, _limit):
                return json.dumps(
                    {
                        "done": True,
                        "done_reason": "stop",
                        "total_duration": 10,
                        "prompt_eval_count": 12,
                        "eval_count": 34,
                        "message": {
                            "content": json.dumps(
                                {
                                    "candidates": [
                                        {
                                            "market": "AAA-EUR",
                                            "classification": "abstain",
                                            "arguments": [
                                                {
                                                    "metric_id": "returns_pct.24h",
                                                    "relation": "positive",
                                                    "reference": "AAA-EUR.metrics#returns_pct.24h",
                                                    "role": "favorable",
                                                }
                                            ],
                                            "summary": "Observed uncertainty.",
                                        }
                                    ]
                                }
                            ),
                            "thinking": "SECRET_REASONING_MUST_NOT_BE_STORED",
                        },
                    }
                ).encode()

        def opener(request, **_kwargs):
            captured.update(json.loads(request.data))
            return Response()

        result = OllamaCandidateEvaluator("local-model", opener=opener, num_predict=3000).evaluate(
            snapshot
        )
        self.assertEqual(result.status, "success")
        self.assertEqual(result.parameters["num_predict"], 3000)
        self.assertEqual(result.diagnostics["generation"]["eval_count"], 34)
        self.assertNotIn("thinking", json.dumps(result.to_dict()))

    def test_errors_and_unrequested_evaluators_are_not_comparable(self):
        snapshot = build_snapshot(analysis_report(), snapshot_id="fixed", created_at=NOW)
        deterministic = DeterministicEvaluator()

        class Failed:
            def evaluate(self, value):
                from crypto_market_agents.trading.evaluation import EvaluationResult

                return EvaluationResult("ollama", "v1", "error", tuple(), 0, error="timeout")

        failed = compare_evaluators(snapshot, deterministic, Failed())
        self.assertEqual(failed["summary"]["agreements"], 0)
        self.assertEqual(failed["summary"]["divergences"], 0)
        self.assertEqual(failed["summary"]["non_comparable"], 1)
        self.assertEqual(failed["comparison"][0]["status"], "non_comparable")
        self.assertEqual(failed["comparison"][0]["reason"], "ollama: timeout")

        class Unrequested:
            def evaluate(self, value):
                from crypto_market_agents.trading.evaluation import EvaluationResult

                return EvaluationResult("ollama", "v1", "not_requested", tuple(), 0)

        skipped = compare_evaluators(snapshot, deterministic, Unrequested())
        self.assertEqual(skipped["summary"]["agreements"], 0)
        self.assertEqual(skipped["comparison"][0]["status"], "non_comparable")
        self.assertEqual(skipped["comparison"][0]["reason"], "ollama: évaluateur non exécuté")

    def test_cli_deterministic_mode_exports_without_constructing_ollama(self):
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "analysis.json"
            target = Path(directory) / "nested" / "result.json"
            source.write_text(json.dumps(analysis_report()))
            with patch(
                "crypto_market_agents.cli.OllamaCandidateEvaluator",
                side_effect=AssertionError("must not construct"),
            ):
                output = StringIO()
                with redirect_stdout(output):
                    code = main(
                        ["compare", "--analysis-json", str(source), "--json", str(target)],
                        now_provider=lambda: NOW,
                    )
            self.assertEqual(code, 0)
            payload = json.loads(target.read_text())
            self.assertEqual(
                payload["evaluators"]["deterministic"]["results"][0]["classification"], "watch"
            )
            self.assertEqual(payload["evaluators"]["ollama"]["status"], "not_requested")
            self.assertEqual(payload["summary"]["non_comparable"], 1)
            self.assertEqual(payload["comparison"][0]["status"], "non_comparable")
            self.assertIsNone(payload["summary"]["winner"])
            snapshot_path = Path(payload["snapshot_path"])
            self.assertTrue(snapshot_path.exists())
            original_snapshot = snapshot_path.read_bytes()
            with redirect_stdout(StringIO()):
                self.assertEqual(
                    main(
                        ["compare", "--analysis-json", str(source), "--json", str(target)],
                        now_provider=lambda: NOW,
                    ),
                    0,
                )
            self.assertEqual(snapshot_path.read_bytes(), original_snapshot)
            self.assertIn("aucun ordre", output.getvalue())

    def test_cli_does_not_overwrite_legacy_snapshot(self):
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "analysis.json"
            snapshot_path = Path(directory) / "analysis-snapshot.json"
            source.write_text(json.dumps(analysis_report()))
            legacy = {"schema_version": "market-analysis-snapshot.v1", "sentinel": "preserve"}
            original = json.dumps(legacy).encode()
            snapshot_path.write_bytes(original)
            with redirect_stdout(StringIO()):
                code = main(
                    [
                        "compare",
                        "--analysis-json",
                        str(source),
                        "--json",
                        str(Path(directory) / "out.json"),
                    ],
                    now_provider=lambda: NOW,
                )
            self.assertEqual(code, 1)
            self.assertEqual(snapshot_path.read_bytes(), original)


if __name__ == "__main__":
    unittest.main()
