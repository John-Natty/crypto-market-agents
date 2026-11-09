"""Regressions for bounded independent evaluations of an immutable source snapshot."""

from copy import deepcopy
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from test_evaluation_comparison import NOW, analysis_report
from crypto_market_agents.trading.evaluation import (
    DeterministicEvaluator,
    OllamaCandidateEvaluator,
    build_snapshot,
    compare_evaluators,
    validate_evaluation,
    verify_snapshot,
    _read_bounded_response,
)


def snapshot(count=1):
    report = analysis_report()
    template = report["candidates"][0]
    report["candidates"] = []
    for index in range(count):
        row = deepcopy(template)
        row["market"] = f"{chr(65 + index) * 3}-EUR"
        report["candidates"].append(row)
    return build_snapshot(report, snapshot_id="source", created_at=NOW)


def output(market="AAA-EUR", label="abstain"):
    return {
        "candidates": [
            {
                "market": market,
                "classification": label,
                "arguments": [
                    {
                        "metric_id": "returns_pct.24h",
                        "relation": "positive",
                        "reference": f"{market}.metrics#returns_pct.24h",
                        "role": "unfavorable" if label == "avoid" else "favorable",
                    }
                ],
                "summary": "Preuves disponibles, incertitude explicite.",
            }
        ]
    }


class Response:
    def __init__(self, content, done_reason="stop"):
        self.raw = json.dumps(
            {
                "done": True,
                "done_reason": done_reason,
                "eval_count": 100,
                "message": {"content": content, "thinking": "PRIVATE_REASONING"},
            }
        ).encode()

    def __enter__(self):
        return self

    def __exit__(self, *args):
        pass

    def read(self, limit):
        return self.raw[:limit]


class IndependentCandidateTests(unittest.TestCase):
    def test_exactly_one_bounded_result_and_only_same_market_context(self):
        source = snapshot(3)
        before = deepcopy(source)
        requests = []

        def opener(request, **kwargs):
            data = json.loads(request.data)
            requests.append(data)
            candidates = json.loads(data["messages"][1]["content"])["candidates"]
            self.assertEqual(len(candidates), 1)
            self.assertIs(data["think"], False)
            schema = data["format"]["properties"]["candidates"]
            self.assertEqual((schema["minItems"], schema["maxItems"]), (1, 1))
            props = schema["items"]["properties"]
            self.assertEqual(props["summary"]["maxLength"], 240)
            self.assertEqual(props["arguments"]["maxItems"], 5)
            market = candidates[0]["market"]
            self.assertTrue(
                all(
                    ref.startswith(market + ".")
                    for ref in props["arguments"]["items"]["properties"]["reference"]["enum"]
                )
            )
            return Response(json.dumps(output(market)))

        result = OllamaCandidateEvaluator("local", opener=opener).evaluate(source)
        self.assertEqual(result.status, "success")
        self.assertEqual(len(result.response_validated["candidates"]), 3)
        self.assertEqual(len(requests), 3)
        self.assertEqual(result.diagnostics["attempted_count"], 3)
        self.assertEqual(result.diagnostics["validated_count"], 3)
        self.assertEqual(result.diagnostics["generated_tokens"], 300)
        self.assertEqual(result.diagnostics["reserved_generation_tokens"], 3600)
        self.assertEqual(before, source)
        self.assertTrue(verify_snapshot(source))

    def test_truncation_never_repaired_even_when_content_is_parseable(self):
        for content in ('{"candidates":[', json.dumps(output())):
            evaluator = OllamaCandidateEvaluator(
                "local", opener=lambda *_a, **_k: Response(content, "length")
            )
            result = evaluator.evaluate(snapshot())
            self.assertEqual(result.error, "truncated_model_json")
            self.assertIsNone(result.response_validated)

    def test_repetition_and_duplicate_keys_are_rejected(self):
        repeated = output()
        repeated["candidates"] *= 14
        duplicate_key = json.dumps(output()).replace(
            '"classification": "abstain"', '"classification":"avoid","classification":"abstain"'
        )
        for content, error in (
            (json.dumps(repeated), "wrong_candidate_count"),
            (duplicate_key, "invalid_model_json"),
        ):
            result = OllamaCandidateEvaluator(
                "local", opener=lambda *_a, **_k: Response(content)
            ).evaluate(snapshot())
            self.assertEqual(result.status, "error")
            self.assertEqual(result.error, error)

    def test_partial_failure_keeps_successes_and_counts_only_comparable_rows(self):
        source = snapshot(3)
        attempts = []

        def opener(request, **kwargs):
            market = json.loads(json.loads(request.data)["messages"][1]["content"])["candidates"][
                0
            ]["market"]
            attempts.append(market)
            if market == "BBB-EUR":
                return Response("{", "length")
            return Response(json.dumps(output(market, "watch" if market == "AAA-EUR" else "avoid")))

        result = compare_evaluators(
            source, DeterministicEvaluator(), OllamaCandidateEvaluator("local", opener=opener)
        )
        evaluation = result["evaluators"]["ollama"]
        self.assertEqual(evaluation["status"], "partial")
        self.assertEqual(
            [row["market"] for row in evaluation["response_validated"]["candidates"]],
            ["AAA-EUR", "CCC-EUR"],
        )
        self.assertEqual(attempts, ["AAA-EUR", "BBB-EUR", "CCC-EUR"])
        self.assertEqual(result["summary"]["agreements"], 1)
        self.assertEqual(result["summary"]["divergences"], 1)
        self.assertEqual(result["summary"]["non_comparable"], 1)
        self.assertEqual(result["comparison"][1]["reason"], "ollama: truncated_model_json")

    def test_token_cap_reserves_failed_calls_and_never_retries(self):
        requests = []

        def opener(request, **kwargs):
            requests.append(json.loads(request.data)["options"]["num_predict"])
            raise TimeoutError()

        result = OllamaCandidateEvaluator(
            "local", opener=opener, num_predict=300, total_num_predict=500
        ).evaluate(snapshot(3))
        self.assertEqual(requests, [300, 200])
        self.assertEqual(result.diagnostics["candidates"]["CCC-EUR"]["status"], "not_requested")
        self.assertEqual(
            result.diagnostics["candidates"]["CCC-EUR"]["error"], "total_budget_exhausted"
        )

    def test_total_duration_stops_scheduling_and_caps_call_timeout(self):
        clock = [0.0]
        timeouts = []

        def opener(request, **kwargs):
            timeouts.append(kwargs["timeout"])
            clock[0] += 3
            raise TimeoutError()

        with patch(
            "crypto_market_agents.trading.evaluation.time.monotonic", side_effect=lambda: clock[0]
        ):
            result = OllamaCandidateEvaluator(
                "local", opener=opener, timeout=60, total_timeout=2
            ).evaluate(snapshot(2))
        self.assertEqual(timeouts, [2])
        self.assertEqual(result.diagnostics["candidates"]["BBB-EUR"]["status"], "not_requested")

    def test_raw_capture_is_opt_in_private_unique_and_excludes_reasoning(self):
        with tempfile.TemporaryDirectory(dir=Path.cwd() / "reports") as directory:
            evaluator = OllamaCandidateEvaluator(
                "local", diagnostic_dir=directory, opener=lambda *_a, **_k: Response("{", "length")
            )
            for _ in range(2):
                result = evaluator.evaluate(snapshot())
                self.assertNotIn("PRIVATE_REASONING", json.dumps(result.to_dict()))
            captures = list(Path(directory).glob("*.content.txt"))
            self.assertEqual(len(captures), 2)
            for path in captures:
                self.assertEqual(path.read_text(), "{")
                self.assertEqual(os.stat(path).st_mode & 0o777, 0o600)
        with self.assertRaises(ValueError):
            OllamaCandidateEvaluator("local", diagnostic_dir="/tmp")

    def test_schema_disallows_inadmissible_labels_and_duplicate_reference_arrays(self):
        report = analysis_report()
        report["candidates"][0]["metric_quality"]["sma20_vs_sma50_pct"]["available"] = False
        source = build_snapshot(report)
        captured = []

        def opener(request, **kwargs):
            captured.append(json.loads(request.data))
            return Response(json.dumps(output()))

        evaluator = OllamaCandidateEvaluator("local", opener=opener)
        evaluator.evaluate(source)
        props = captured[0]["format"]["properties"]["candidates"]["items"]["properties"]
        self.assertEqual(props["classification"]["enum"], ["abstain"])
        self.assertNotIn("abstention_reason", props)
        self.assertIn(
            "returns_pct.24h", props["arguments"]["items"]["properties"]["metric_id"]["enum"]
        )

    def test_model_control_markers_in_summary_are_rejected(self):
        for text in (
            "Preuves </think>otlin</think>",
            "Reason\nsecond line",
            "Reason\x00",
            "Reason' }]}]//{",
        ):
            payload = output()
            payload["candidates"][0]["summary"] = text
            result = OllamaCandidateEvaluator(
                "local", opener=lambda *_a, **_k: Response(json.dumps(payload))
            ).evaluate(snapshot())
            self.assertEqual(result.status, "error")
            self.assertIsNone(result.response_validated)

    def test_response_read_has_a_total_deadline_and_size_limit(self):
        clock = [0.0]

        class DrippingResponse:
            def read1(self, size):
                clock[0] += 1
                return b"a" * size

        with patch(
            "crypto_market_agents.trading.evaluation.time.monotonic", side_effect=lambda: clock[0]
        ):
            with self.assertRaises(TimeoutError):
                _read_bounded_response(DrippingResponse(), deadline=2)
            raw = _read_bounded_response(DrippingResponse(), deadline=100)
            self.assertEqual(len(raw), 200001)

    def test_bounds_and_market_subset(self):
        for kwargs in (
            {"total_num_predict": True},
            {"total_num_predict": 32769},
            {"total_num_predict": 127},
            {"timeout": 181},
            {"timeout": 0},
            {"total_timeout": 601},
            {"num_ctx": 2048},
            {"num_ctx": True},
        ):
            with self.subTest(kwargs=kwargs), self.assertRaises(ValueError):
                OllamaCandidateEvaluator("local", **kwargs)
        evaluator = OllamaCandidateEvaluator(
            "local", opener=lambda *_a, **_k: Response(json.dumps(output()))
        )
        source = snapshot(2)
        self.assertEqual(evaluator.evaluate(source, markets=["AAA-EUR"]).status, "success")
        for markets in (["MISSING"], ["AAA-EUR", "AAA-EUR"], []):
            with self.assertRaises(ValueError):
                evaluator.evaluate(source, markets=markets)

    def test_validator_checks_eligibility_not_deterministic_signal_direction(self):
        source = snapshot()
        self.assertEqual(
            DeterministicEvaluator().evaluate(source).results[0]["classification"], "watch"
        )
        # Contrary judgement is permitted when the same evidence is usable and cited.
        self.assertEqual(
            validate_evaluation(output(label="avoid"), source)["candidates"][0]["classification"],
            "avoid",
        )
        for mutate in ("stale", "missing", "bad_ref", "duplicate_ref", "long_reason"):
            report = analysis_report()
            payload = output(label="avoid")
            if mutate == "stale":
                report["candidates"][0]["metric_quality"]["sma20_vs_sma50_pct"]["freshness"][
                    "fresh"
                ] = False
            elif mutate == "missing":
                report["candidates"][0]["metrics"]["sma20_vs_sma50_pct"] = None
            elif mutate == "bad_ref":
                payload["candidates"][0]["arguments"][0]["reference"] = (
                    "BBB-EUR.metrics#returns_pct.24h"
                )
            elif mutate == "duplicate_ref":
                payload["candidates"][0]["arguments"] *= 2
            else:
                payload["candidates"][0]["summary"] = "x" * 241
            with self.subTest(mutate=mutate), self.assertRaises(ValueError):
                validate_evaluation(payload, build_snapshot(report))


if __name__ == "__main__":
    unittest.main()
