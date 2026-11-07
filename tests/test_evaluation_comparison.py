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
                "scanner_candidate": {"market": "AAA-EUR", "source_at": NOW.isoformat()},
                "observations": [{"timestamp": NOW.isoformat(), "close": "10", "volume_base": "2"}],
                "metrics": {
                    "returns_pct": {"24h": "2", "7d": "3", "30d": "-1"},
                    "sma20_vs_sma50_pct": "1",
                },
                "data_sufficiency": {"sufficient": True},
                "data_quality": {"fresh": True, "gaps": [], "invalid_rows": []},
                "favorable_signals": ["24h positive"],
                "risks_and_contradictions": [],
                "reasons": [],
            }
        ],
    }


class SnapshotAndValidationTests(unittest.TestCase):
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
        valid = {
            "candidates": [
                {
                    "market": "AAA-EUR",
                    "label": "watch",
                    "justification": "Observed facts.",
                    "evidence_refs": ["AAA-EUR.metrics"],
                }
            ]
        }
        self.assertEqual(validate_evaluation(valid, snapshot)["candidates"][0]["label"], "watch")
        for bad in (
            {"candidates": [{**valid["candidates"][0], "label": "buy"}]},
            {"candidates": [{**valid["candidates"][0], "market": "BBB-EUR"}]},
            {"candidates": [{**valid["candidates"][0], "evidence_refs": ["BBB-EUR.metrics"]}]},
            {"candidates": [{**valid["candidates"][0], "extra": "ignore"}]},
        ):
            with self.assertRaises(ValueError):
                validate_evaluation(bad, snapshot)

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


class OllamaAndCliTests(unittest.TestCase):
    def test_timeout_invalid_response_and_unavailable_are_abstentions(self):
        snapshot = build_snapshot(analysis_report(), snapshot_id="fixed", created_at=NOW)
        for failure in (TimeoutError(), OSError("missing")):
            evaluator = OllamaCandidateEvaluator(
                "local-model", opener=lambda *_a, **_k: (_ for _ in ()).throw(failure), timeout=1
            )
            result = evaluator.evaluate(snapshot)
            self.assertEqual(result.status, "error")
            self.assertEqual(result.results[0]["label"], "abstain")
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
        self.assertEqual(result.results[0]["label"], "abstain")
        self.assertEqual(result.error, "JSONDecodeError")

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
            self.assertEqual(payload["evaluators"]["deterministic"]["results"][0]["label"], "watch")
            self.assertEqual(payload["evaluators"]["ollama"]["status"], "not_requested")
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


if __name__ == "__main__":
    unittest.main()
