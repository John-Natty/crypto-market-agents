from contextlib import redirect_stdout
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from io import StringIO
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from crypto_market_agents.cli import main
from crypto_market_agents.trading.deep_analysis import DeepMarketAnalyzer
from crypto_market_agents.trading.history_analysis import calculate_metrics, normalize_candles
from crypto_market_agents.trading.history_client import BitvavoCandleClient, CandleClientError


NOW = datetime(2026, 1, 10, 12, tzinfo=timezone.utc)
HOUR = 3_600_000
BASE = int(NOW.timestamp() * 1000) - 60 * HOUR


def series(n=60):
    return [
        [
            BASE + index * HOUR,
            str(100 + index),
            str(101 + index),
            str(99 + index),
            str(100 + index),
            str(index + 1),
        ]
        for index in range(n)
    ]


class HistoryMetricTests(unittest.TestCase):
    def test_reference_returns_sma_and_volume_ratio(self):
        candles, quality = normalize_candles(series(), interval="1h", now=NOW)
        result = calculate_metrics(candles, interval="1h", now=NOW)
        # 24h comparison uses the close 24 intervals before current (index -24).
        self.assertEqual(
            result["metrics"]["returns_pct"]["24h"],
            str(((Decimal(159) / Decimal(135) - 1) * 100).quantize(Decimal("0.0001"))),
        )
        self.assertEqual(Decimal(result["metrics"]["sma50_close"]), Decimal("134.5"))
        self.assertEqual(result["metrics"]["latest_volume_vs_previous_20_mean"], "1.2121")
        self.assertTrue(quality["fresh"])

    def test_irregular_close_intervals_are_not_treated_as_one_hour_returns(self):
        rows = series(8)
        rows = [row for index, row in enumerate(rows) if index != 3]
        candles, _ = normalize_candles(rows, interval="1h", now=NOW)
        result = calculate_metrics(candles, interval="1h", now=NOW)
        metrics = result["metrics"]
        contiguous = [
            float(candles[i].close / candles[i - 1].close - 1)
            for i in range(1, len(candles))
            if candles[i].timestamp - candles[i - 1].timestamp == timedelta(hours=1)
        ]
        self.assertEqual(metrics["contiguous_interval_returns_used"], len(contiguous))
        self.assertEqual(len(contiguous), 5)
        # The two-hour price move across the omitted timestamp is not counted as an hourly return.
        expected_hourly = contiguous
        mean = sum(expected_hourly) / len(expected_hourly)
        expected_volatility = (
            sum((value - mean) ** 2 for value in expected_hourly) / (len(expected_hourly) - 1)
        ) ** 0.5 * 100
        self.assertEqual(
            Decimal(metrics["return_volatility_pct_per_interval"]),
            Decimal(str(expected_volatility)).quantize(Decimal("0.0001")),
        )

    def test_time_horizons_and_sma_require_exact_contiguous_windows(self):
        rows = [row for index, row in enumerate(series(60)) if index not in {35, 50}]
        candles, _ = normalize_candles(rows, interval="1h", now=NOW)
        result = calculate_metrics(candles, interval="1h", now=NOW)
        metrics = result["metrics"]
        # The exact 24h baseline (latest timestamp minus 24 hours) is absent.
        self.assertIsNone(metrics["returns_pct"]["24h"])
        # The gap at index 50 breaks the trailing 20-hour moving-average window.
        self.assertIsNone(metrics["sma20_close"])
        self.assertFalse(metrics["sma20_window_contiguous"])

    def test_old_gap_does_not_invalidate_recent_contiguous_metrics(self):
        rows = [row for index, row in enumerate(series(80)) if index != 5]
        candles, quality = normalize_candles(rows, interval="1h", now=NOW)
        result = calculate_metrics(candles, interval="1h", now=NOW)
        self.assertTrue(quality["gaps"])
        self.assertTrue(result["metric_quality"]["sma20_close"]["available"])
        self.assertTrue(result["metric_quality"]["sma20_vs_sma50_pct"]["available"])
        self.assertTrue(result["metric_quality"]["returns_pct.24h"]["available"])
        self.assertFalse(result["metric_quality"]["returns_pct.7d"]["available"])
        self.assertIsNotNone(result["metric_quality"]["returns_pct.7d"]["reason"])

    def test_each_metric_quality_exposes_window_freshness_and_unavailability_reason(self):
        candles, _ = normalize_candles(series(60), interval="1h", now=NOW)
        result = calculate_metrics(candles, interval="1h", now=NOW)
        quality = result["metric_quality"]
        self.assertTrue(quality["returns_pct.24h"]["available"])
        self.assertEqual(quality["returns_pct.24h"]["observation_count"], 2)
        self.assertEqual(
            quality["returns_pct.24h"]["window"]["start_at"], candles[-25].timestamp.isoformat()
        )
        self.assertTrue(quality["returns_pct.24h"]["freshness"]["fresh"])
        self.assertFalse(quality["returns_pct.30d"]["available"])
        self.assertIsNotNone(quality["returns_pct.30d"]["reason"])

    def test_720_hourly_candles_cannot_span_two_closes_thirty_days_apart(self):
        start = int(NOW.timestamp() * 1000) - 721 * HOUR
        seven_twenty = [
            [
                start + index * HOUR,
                str(index + 1),
                str(index + 1),
                str(index + 1),
                str(index + 1),
                "1",
            ]
            for index in range(720)
        ]
        candles, _ = normalize_candles(seven_twenty, interval="1h", now=NOW)
        self.assertIsNone(
            calculate_metrics(candles, interval="1h", now=NOW)["metrics"]["returns_pct"]["30d"]
        )
        no_reference = calculate_metrics(candles, interval="1h", now=NOW)
        self.assertTrue(
            any(
                "historique insuffisant: aucune donnée jusqu'à la cible" in reason
                for reason in no_reference["reasons"]
            )
        )

        seven_twenty_one = seven_twenty + [[start + 720 * HOUR, "721", "721", "721", "721", "1"]]
        candles, _ = normalize_candles(seven_twenty_one, interval="1h", now=NOW)
        value = calculate_metrics(candles, interval="1h", now=NOW)["metrics"]["returns_pct"]["30d"]
        self.assertEqual(value, "72000.0000")

    def test_open_candle_excluded_and_duplicate_gap_reported(self):
        rows = series(30)
        rows.append([int(NOW.timestamp() * 1000) - HOUR // 2, "1", "1", "1", "1", "1"])
        candles, quality = normalize_candles(rows, interval="1h", now=NOW)
        self.assertLessEqual(candles[-1].timestamp + timedelta(hours=1), NOW)
        self.assertGreater(quality["open_excluded"], 0)
        duplicate_rows = series(4) + [series(4)[2]]
        _, duplicate_quality = normalize_candles(duplicate_rows, interval="1h", now=NOW)
        self.assertEqual(duplicate_quality["duplicates_removed"], 1)
        self.assertTrue(duplicate_quality["gaps"])

    def test_insufficient_invalid_and_nonfinite_data_are_explicit(self):
        rows = series(5) + [[BASE + 5 * HOUR, "NaN", "1", "1", "1", "0"]]
        candles, quality = normalize_candles(rows, interval="1h", now=NOW)
        result = calculate_metrics(candles, interval="1h", now=NOW)
        self.assertFalse(result["data_sufficiency"]["sufficient"])
        self.assertIsNone(result["metrics"]["sma50_close"])
        self.assertIsNone(result["metrics"]["returns_pct"]["30d"])
        self.assertTrue(quality["invalid_rows"])
        self.assertIn("insuffisant", " ".join(result["reasons"]))


class ClientAndOrchestrationTests(unittest.TestCase):
    def test_stale_missing_and_future_scan_json_do_not_call_api(self):
        class Client:
            def get_candles(self, *_args, **_kwargs):
                raise AssertionError("API must not be called for stale scan input")

        for collected_at in (
            None,
            (NOW - timedelta(hours=2)).isoformat(),
            (NOW + timedelta(minutes=1)).isoformat(),
        ):
            scan = {"collected_at": collected_at, "candidates": [{"market": "AAA-EUR"}]}
            result = DeepMarketAnalyzer(Client(), now_provider=lambda: NOW).analyze(scan)
            self.assertIn("scan_error", result["candidates"][0]["data_quality"])

    def test_client_builds_public_limited_candles_request(self):
        captured = {}

        class Response:
            def __enter__(self):
                return self

            def __exit__(self, *args):
                pass

            def read(self, _size):
                return b"[]"

        def opener(request, timeout):
            captured["url"] = request.full_url
            captured["timeout"] = timeout
            return Response()

        self.assertEqual(
            BitvavoCandleClient(opener=opener).get_candles("BTC-EUR", interval="1h", limit=500), []
        )
        self.assertIn("/v2/BTC-EUR/candles?", captured["url"])
        self.assertIn("limit=500", captured["url"])

    def test_candidate_error_does_not_stop_following_candidate(self):
        class Client:
            def get_candles(self, market, **kwargs):
                if market == "AAA-EUR":
                    raise CandleClientError("HTTP 429")
                return series(60)

        scan = {
            "collected_at": NOW.isoformat(),
            "candidates": [{"market": "AAA-EUR"}, {"market": "BBB-EUR"}, {"market": "CCC-EUR"}],
        }
        result = DeepMarketAnalyzer(Client(), now_provider=lambda: NOW).analyze(scan, count=2)
        self.assertEqual(result["selection"]["selected"], 2)
        self.assertIn("error", result["candidates"][0]["data_quality"])
        self.assertEqual(result["candidates"][1]["market"], "BBB-EUR")
        self.assertEqual(len(result["candidates"]), 2)

    def test_cli_selects_count_and_exports_json(self):
        scan_data = {
            "collected_at": NOW.isoformat(),
            "candidates": [{"market": f"COIN{i}-EUR"} for i in range(7)],
        }
        with tempfile.TemporaryDirectory() as directory:
            scan_path, output_path = (
                Path(directory) / "scan.json",
                Path(directory) / "analysis.json",
            )
            scan_path.write_text(json.dumps(scan_data))
            with patch("crypto_market_agents.cli.BitvavoCandleClient") as client:
                client.return_value.get_candles.side_effect = lambda market, **_kwargs: []
                output = StringIO()
                with redirect_stdout(output):
                    code = main(
                        [
                            "analyze",
                            "--scan-json",
                            str(scan_path),
                            "--count",
                            "2",
                            "--json",
                            str(output_path),
                        ],
                        now_provider=lambda: NOW,
                    )
            self.assertEqual(code, 0)
            payload = json.loads(output_path.read_text())
            self.assertEqual(payload["selection"]["selected"], 2)
            self.assertIn("lecture seule", output.getvalue())
            self.assertEqual(client.return_value.get_candles.call_count, 2)
            self.assertTrue(
                all(
                    call.kwargs["limit"] == 1440
                    for call in client.return_value.get_candles.call_args_list
                )
            )


if __name__ == "__main__":
    unittest.main()
