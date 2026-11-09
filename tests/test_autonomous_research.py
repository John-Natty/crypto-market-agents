from datetime import datetime, timezone
from pathlib import Path
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from io import StringIO
from unittest.mock import patch

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from crypto_market_agents.trading.autonomous_research import (
    ALLOWED_TOOLS,
    AutonomousResearchRunner,
    ResearchLimits,
    _budgeted_research_context,
    _conclusion_schema,
)
from crypto_market_agents.trading.evaluation import OllamaCandidateEvaluator
from crypto_market_agents.cli import main


def scan(score=80):
    return {
        "collected_at": "2026-11-09T10:00:00+00:00",
        "candidates": [
            {
                "market": "AAA-EUR",
                "base_asset": "AAA",
                "quote_asset": "EUR",
                "score": str(score),
                "volume_quote_24h_eur": "123456",
                "price_change_24h_pct": "2.0",
                "collected_at": "2026-11-09T10:00:00+00:00",
                "source_at": "2026-11-09T09:59:00+00:00",
            },
            {
                "market": "BBB-EUR",
                "base_asset": "BBB",
                "quote_asset": "EUR",
                "score": "60",
                "volume_quote_24h_eur": "200000",
                "price_change_24h_pct": "-2.0",
                "collected_at": "2026-11-09T10:00:00+00:00",
                "source_at": "2026-11-09T09:59:00+00:00",
            },
        ],
        "rejected": [],
    }


def history(market):
    return {
        "generated_at": "2026-11-09T10:01:00+00:00",
        "candidates": [
            {
                "market": market,
                "interval": "1h",
                "metrics": {"returns_pct": {"24h": "3.1"}},
                "metric_quality": {
                    "returns_pct.24h": {
                        "available": True,
                        "freshness": {"fresh": True},
                        "window": {
                            "start_at": "2026-11-08T10:00:00+00:00",
                            "end_at": "2026-11-09T10:00:00+00:00",
                        },
                        "observation_count": 24,
                        "reason": None,
                    }
                },
                "analysis_quality": {
                    "status": "partial",
                    "unavailable_metrics": ["returns_pct.7d"],
                    "observed_gaps": [
                        {"from": f"gap-{index}", "cause": "unknown"} for index in range(20)
                    ],
                },
                "reasons": ["one historical metric unavailable"],
            }
        ],
    }


def fundamentals(market, identity="resolved_explicit"):
    cg = (
        {
            "provider_id": "verified-id",
            "name": "Asset",
            "network": "ethereum",
            "contract_address": "0xverified",
        }
        if identity == "resolved_explicit"
        else {"provider_id": None, "name": None, "network": None, "contract_address": None}
    )
    return {
        "candidates": [
            {
                "market": market,
                "identity_status": identity,
                "identity_reason": None
                if identity == "resolved_explicit"
                else "no_explicit_verified_mapping",
                "bitvavo_identity": {
                    "name": "Asset",
                    "symbol": market.split("-")[0],
                    "provider_id": market.split("-")[0],
                },
                "coingecko_identity": cg,
                "symbol_candidates": [
                    {"provider_id": "unverified-candidate", "name": "Asset clone"}
                ],
                "observations": {
                    "market_cap": {
                        "value": 1_234_000 if identity == "resolved_explicit" else None,
                        "unit": "EUR",
                        "source": "coingecko",
                        "availability": "available"
                        if identity == "resolved_explicit"
                        else "unavailable",
                        "reason": None
                        if identity == "resolved_explicit"
                        else "identity_unresolved",
                        "collected_at": "2026-11-09T10:02:00+00:00",
                    },
                    "total_supply": {
                        "value": None,
                        "unit": "token",
                        "source": "coingecko",
                        "availability": "unavailable",
                        "reason": "source_field_absent",
                        "collected_at": "2026-11-09T10:02:00+00:00",
                    },
                },
                "protocol": {"availability": "unavailable", "reason": "no_verified_protocol_link"},
                "provider_errors": {},
            }
        ]
    }


class FakeOllama:
    def __init__(
        self,
        handlers,
        *,
        before_response=None,
        timeout=30,
        conclusion_response=None,
        before_conclusion=None,
    ):
        self.handlers = list(handlers)
        self.before_response = before_response
        self.conclusion_response = conclusion_response
        self.before_conclusion = before_conclusion
        self.timeout = timeout
        self.requests = []

    def chat_with_tools(self, messages, tools, **kwargs):
        self.requests.append((messages, tools, kwargs))
        if not tools:
            if self.before_conclusion:
                self.before_conclusion()
            if self.conclusion_response is not None:
                return self.conclusion_response
            payload = __import__("json").loads(messages[-1]["content"])
            rows = [
                {
                    "market": candidate["market"],
                    "status": "partial" if candidate.get("required_missing_refs") else "complete",
                    "interpretation": "Interprétation courte.",
                    "conclusion": "Bilan limité aux preuves référencées.",
                }
                for candidate in payload["candidate_scope"]
            ]
            return {
                "message": {
                    "content": __import__("json").dumps({"candidates": rows}),
                    "tool_calls": [],
                },
                "generation": {"eval_count": 32},
                "duration_ms": 10,
                "think": False,
                "done_reason": "stop",
                "request_bytes": 100,
                "response_bytes": 200,
                "timeout_seconds": kwargs.get("timeout"),
                "num_ctx": 8192,
            }
        if self.before_response:
            self.before_response()
        return {
            "message": {"content": "", "tool_calls": [self.handlers.pop(0)]},
            "generation": {"eval_count": 42},
            "duration_ms": 10,
            "think": False,
            "done_reason": "stop",
            "request_bytes": 100,
            "response_bytes": 200,
            "timeout_seconds": kwargs.get("timeout"),
            "num_ctx": 8192,
        }


def call(name, args):
    if name == "finish_research":
        args = {
            "candidates": [
                {
                    key: candidate.get(key, [])
                    for key in (
                        "market",
                        "fact_refs",
                        "missing_refs",
                        "required_missing_refs",
                    )
                }
                for candidate in args.get("candidates", [])
            ]
        }
    return {"type": "function", "function": {"name": name, "arguments": args}}


class FakeClock:
    def __init__(self):
        self.value = 0.0

    def monotonic(self):
        return self.value

    def sleep(self, seconds):
        self.value += seconds


class AutonomousResearchTests(unittest.TestCase):
    def make_runner(
        self,
        model,
        *,
        scan_data=None,
        limits=None,
        clock=None,
        analyze_fn=None,
        fundamentals_fn=None,
    ):
        clock = clock or FakeClock()
        return AutonomousResearchRunner(
            model="qwen3:8b",
            ollama=model,
            scan=lambda remaining: scan_data or scan(),
            analyze=analyze_fn
            or (lambda market, candidate, scan_result, remaining: history(market)),
            fundamentals=fundamentals_fn
            or (lambda market, candidate, remaining: fundamentals(market)),
            limits=limits or ResearchLimits(total_timeout_seconds=60),
            now_provider=lambda: datetime(2026, 11, 9, tzinfo=timezone.utc),
            monotonic=clock.monotonic,
            sleep=clock.sleep,
        ), clock

    def test_model_chooses_tools_differently_from_scanner_observations(self):
        selected = []
        for value in (90, 10):
            tool = "analyze_candidate" if value >= 50 else "get_fundamentals"
            refs = ["scan:AAA-EUR:score"]
            fake = FakeOllama(
                [
                    call(tool, {"market": "AAA-EUR"}),
                    call(
                        "finish_research",
                        {
                            "candidates": [
                                {
                                    "market": "AAA-EUR",
                                    "fact_refs": refs,
                                    "missing_refs": [],
                                    "interpretation": "Lecture qualitative.",
                                    "conclusion": "Recherche suffisante.",
                                }
                            ]
                        },
                    ),
                ]
            )
            runner, _ = self.make_runner(fake, scan_data=scan(value))
            result = runner.run("Comparer les preuves récentes.")
            selected.append(
                next(
                    event["request"]["tool"]
                    for event in result["events"]
                    if event["kind"] == "tool_call"
                )
            )
            self.assertEqual(result["status"], "completed")
        self.assertEqual(selected, ["analyze_candidate", "get_fundamentals"])

    def test_forbidden_tool_and_invalid_arguments_never_execute(self):
        for bad_call, expected in (
            (call("shell", {"command": "id"}), "forbidden_tool_requested"),
            (
                call("analyze_candidate", {"market": "AAA-EUR", "limit": 9999}),
                "invalid_tool_arguments",
            ),
        ):
            executed = []
            fake = FakeOllama([bad_call])
            runner, _ = self.make_runner(
                fake,
                analyze_fn=lambda *args: executed.append("history"),
                fundamentals_fn=lambda *args: executed.append("fundamentals"),
            )
            result = runner.run("Recherche en lecture seule.")
            self.assertEqual(result["stop_reason"], expected)
            self.assertEqual(executed, [])
            self.assertFalse(result["financial_action_executed"])
            exposed = {tool["function"]["name"] for tool in fake.requests[0][1]}
            self.assertEqual(exposed, ALLOWED_TOOLS)

    def test_parallel_tool_requests_execute_only_first_and_preserve_adaptive_loop(self):
        class MultiCallOllama(FakeOllama):
            def __init__(self):
                super().__init__([])
                self.responses = [
                    [
                        call("get_fundamentals", {"market": "AAA-EUR"}),
                        call("get_fundamentals", {"market": "BBB-EUR"}),
                    ],
                    [
                        call(
                            "finish_research",
                            {
                                "candidates": [
                                    {
                                        "market": "AAA-EUR",
                                        "fact_refs": ["scan:AAA-EUR:score"],
                                        "missing_refs": [],
                                        "interpretation": "Données consultées.",
                                        "conclusion": "Bilan terminé.",
                                    }
                                ]
                            },
                        )
                    ],
                ]

            def chat_with_tools(self, messages, tools, **kwargs):
                self.requests.append((messages, tools, kwargs))
                if not tools:
                    payload = __import__("json").loads(messages[-1]["content"])
                    rows = [
                        {
                            "market": row["market"],
                            "status": "partial" if row.get("required_missing_refs") else "complete",
                            "interpretation": "Données consultées.",
                            "conclusion": "Bilan terminé.",
                        }
                        for row in payload["candidate_scope"]
                    ]
                    return {
                        "message": {
                            "content": __import__("json").dumps({"candidates": rows}),
                            "tool_calls": [],
                        },
                        "generation": {"eval_count": 20},
                        "duration_ms": 10,
                        "done_reason": "stop",
                        "request_bytes": 100,
                        "response_bytes": 200,
                        "timeout_seconds": kwargs.get("timeout"),
                        "num_ctx": 8192,
                    }
                return {
                    "message": {
                        "content": "",
                        "tool_calls": self.responses.pop(0),
                    },
                    "generation": {"eval_count": 20},
                    "duration_ms": 10,
                    "done_reason": "stop",
                    "request_bytes": 100,
                    "response_bytes": 200,
                }

        executed = []
        fake = MultiCallOllama()
        runner, _ = self.make_runner(
            fake,
            fundamentals_fn=lambda market, *args: executed.append(market) or fundamentals(market),
        )
        result = runner.run("Recherche factuelle.")
        self.assertEqual(result["status"], "completed")
        self.assertEqual(executed, ["AAA-EUR"])
        request_event = next(event for event in result["events"] if event["kind"] == "ollama_turn")
        self.assertEqual(len(request_event["requested_tool_calls"]), 2)
        self.assertEqual(
            request_event["requested_tool_calls"][0]["arguments"], {"market": "AAA-EUR"}
        )
        self.assertTrue(
            any(event.get("error") == "parallel_call_not_executed" for event in result["events"])
        )
        self.assertEqual(result["budget"]["tool_requests_reported"], 3)

    def test_unresolved_identity_does_not_turn_candidate_id_into_fact(self):
        fake = FakeOllama(
            [
                call("get_fundamentals", {"market": "AAA-EUR"}),
                call(
                    "finish_research",
                    {
                        "candidates": [
                            {
                                "market": "AAA-EUR",
                                "fact_refs": ["fundamentals:AAA-EUR:identity_status"],
                                "missing_refs": ["fundamentals:AAA-EUR:market_cap"],
                                "required_missing_refs": ["fundamentals:AAA-EUR:market_cap"],
                                "interpretation": "Identité non résolue.",
                                "conclusion": "Données bloquées.",
                            }
                        ]
                    },
                ),
            ]
        )
        runner, _ = self.make_runner(
            fake,
            fundamentals_fn=lambda market, candidate, remaining: fundamentals(
                market, identity="unresolved"
            ),
        )
        result = runner.run("Évaluer l'identité factuelle.")
        self.assertEqual(result["status"], "completed")
        self.assertEqual(result["candidates"][0]["facts"][0]["value"]["value"], "unresolved")
        self.assertNotIn("fundamentals:AAA-EUR:market_cap", result["fact_ledger"])
        self.assertIsNone(
            result["fact_ledger"]["fundamentals:AAA-EUR:identity_status"]["value"][
                "coingecko_identity"
            ]["provider_id"]
        )

    def test_identical_repeated_call_is_rejected_and_previous_data_remains(self):
        fake = FakeOllama(
            [
                call("analyze_candidate", {"market": "AAA-EUR"}),
                call("analyze_candidate", {"market": "AAA-EUR"}),
                call(
                    "finish_research",
                    {
                        "candidates": [
                            {
                                "market": "AAA-EUR",
                                "fact_refs": ["history:AAA-EUR:returns_pct.24h"],
                                "missing_refs": [],
                                "interpretation": "Historique examiné.",
                                "conclusion": "Les mesures restent descriptives.",
                            }
                        ]
                    },
                ),
            ]
        )
        runner, _ = self.make_runner(fake)
        result = runner.run("Vérifier une variation historique.")
        self.assertEqual(result["status"], "completed")
        events = [event for event in result["events"] if event["kind"] == "tool_call"]
        self.assertEqual(events[1]["error"], "identical_call_not_permitted")
        self.assertIn("history:AAA-EUR:returns_pct.24h", result["fact_ledger"])
        self.assertEqual(result["budget"]["repeated_tool_requests"], 1)

    def test_history_remains_available_after_unresolved_fundamentals(self):
        fake = FakeOllama(
            [
                call("get_fundamentals", {"market": "AAA-EUR"}),
                call("analyze_candidate", {"market": "AAA-EUR"}),
                call(
                    "finish_research",
                    {
                        "candidates": [
                            {
                                "market": "AAA-EUR",
                                "fact_refs": ["history:AAA-EUR:returns_pct.24h"],
                                "missing_refs": [],
                            }
                        ]
                    },
                ),
            ]
        )
        runner, _ = self.make_runner(
            fake,
            fundamentals_fn=lambda market, candidate, remaining: fundamentals(
                market, identity="unresolved"
            ),
        )
        result = runner.run("Examiner le marché Bitvavo et ses preuves disponibles.")
        tool_events = [event for event in result["events"] if event.get("kind") == "tool_call"]
        self.assertEqual(
            [event["request"]["tool"] for event in tool_events],
            [
                "get_fundamentals",
                "analyze_candidate",
                "finish_research",
            ],
        )
        self.assertEqual(tool_events[0]["status"], "success")
        self.assertEqual(tool_events[0]["identity_status"], "unresolved")
        self.assertEqual(tool_events[0]["information_result"], "facts_obtained")
        self.assertIn("history:AAA-EUR:returns_pct.24h", result["fact_ledger"])
        second_context = __import__("json").loads(fake.requests[1][0][-1]["content"])
        prior = second_context["previous_tool_results"][0]
        self.assertEqual(prior["execution_status"], "success")
        self.assertEqual(prior["identity_status"], "unresolved")
        self.assertEqual(prior["information_result"], "facts_obtained")
        self.assertNotIn("symbol_candidates_unresolved", str(second_context))
        for turn in result["events"]:
            if turn.get("kind") == "ollama_turn":
                self.assertLessEqual(
                    turn["estimated_prompt_tokens"]
                    + turn["num_predict"]
                    + turn["context_safety_margin_tokens"],
                    turn["num_ctx"],
                )

    def test_no_tool_text_gets_one_bounded_format_recovery_without_replaying_text(self):
        class OneNoToolThenFake(FakeOllama):
            def __init__(self, handlers):
                super().__init__(handlers)
                self.returned_free_text = False

            def chat_with_tools(self, messages, tools, **kwargs):
                if tools and not self.returned_free_text:
                    self.returned_free_text = True
                    self.requests.append((messages, tools, kwargs))
                    return {
                        "message": {
                            "content": "UNTRUSTED_FREE_TEXT_MUST_NOT_REPLAY",
                            "tool_calls": [],
                        },
                        "generation": {"eval_count": 20},
                        "duration_ms": 5,
                        "think": False,
                        "done_reason": "stop",
                        "request_bytes": 100,
                        "response_bytes": 100,
                        "timeout_seconds": kwargs.get("timeout"),
                        "num_ctx": 8192,
                    }
                return super().chat_with_tools(messages, tools, **kwargs)

        fake = OneNoToolThenFake(
            [
                call("analyze_candidate", {"market": "AAA-EUR"}),
                call(
                    "finish_research",
                    {
                        "candidates": [
                            {
                                "market": "AAA-EUR",
                                "fact_refs": ["history:AAA-EUR:returns_pct.24h"],
                                "missing_refs": [],
                            }
                        ]
                    },
                ),
            ]
        )
        runner, _ = self.make_runner(fake)
        result = runner.run("Lire l'historique.")
        recovery = next(
            event for event in result["events"] if event["kind"] == "model_turn_recovery"
        )
        self.assertEqual(recovery["recovery_attempt"], 1)
        self.assertFalse(recovery["raw_content_retained"])
        second_context = __import__("json").loads(fake.requests[1][0][-1]["content"])
        self.assertIn("aucun appel d'outil", second_context["code_feedback"])
        self.assertNotIn("UNTRUSTED_FREE_TEXT_MUST_NOT_REPLAY", str(fake.requests[1][0]))
        self.assertEqual(result["status"], "completed")

    def test_budget_expires_before_and_during_operations(self):
        clock = FakeClock()
        fake = FakeOllama(
            [call("analyze_candidate", {"market": "AAA-EUR"})],
            before_response=lambda: setattr(clock, "value", 2.95),
        )
        ran = []
        runner, _ = self.make_runner(
            fake,
            limits=ResearchLimits(total_timeout_seconds=4, conclusion_timeout_seconds=1),
            clock=clock,
            analyze_fn=lambda *args: ran.append(True),
        )
        result = runner.run("Budget expiré avant l'outil.")
        self.assertEqual(result["stop_reason"], "budget_exhausted_before_tool")
        self.assertEqual(ran, [])

        clock = FakeClock()
        fake = FakeOllama([call("analyze_candidate", {"market": "AAA-EUR"})])

        def slow_analysis(market, candidate, scan_result, remaining):
            clock.value = 5.0
            return history(market)

        runner, _ = self.make_runner(
            fake,
            limits=ResearchLimits(total_timeout_seconds=4, conclusion_timeout_seconds=1),
            clock=clock,
            analyze_fn=slow_analysis,
        )
        result = runner.run("Budget expiré pendant l'outil.")
        self.assertEqual(result["research_status"], "partial")
        self.assertEqual(result["stop_reason"], "conclusion_time_unavailable")
        self.assertEqual(result["research_stop_reason"], "budget_exhausted_during_tool")
        self.assertIn("history:AAA-EUR:returns_pct.24h", result["fact_ledger"])

        clock = FakeClock()
        fake = FakeOllama(
            [call("analyze_candidate", {"market": "AAA-EUR"})],
            before_response=lambda: setattr(clock, "value", 3.1),
        )
        runner, _ = self.make_runner(
            fake,
            limits=ResearchLimits(total_timeout_seconds=4, conclusion_timeout_seconds=1),
            clock=clock,
        )
        result = runner.run("Budget expiré pendant Ollama.")
        self.assertEqual(result["stop_reason"], "budget_exhausted_during_model_turn")
        self.assertEqual(result["budget"]["tool_calls_used"], 0)

    def test_research_stops_at_generation_reserve_and_concludes_inside_total(self):
        fake = FakeOllama([call("analyze_candidate", {"market": "AAA-EUR"})])
        limits = ResearchLimits(
            total_timeout_seconds=10,
            conclusion_timeout_seconds=3,
            num_predict=128,
            conclusion_num_predict=128,
            total_num_predict=256,
        )
        runner, _ = self.make_runner(fake, limits=limits)
        result = runner.run("Bilan sous budget.")
        self.assertEqual(result["stop_reason"], "generation_budget_reserved_for_conclusion")
        self.assertEqual(result["conclusion"]["mode"], "model_structured")
        self.assertEqual(result["budget"]["research_generation_tokens_reserved"], 128)
        self.assertEqual(result["budget"]["conclusion_generation_tokens_reserved"], 128)
        self.assertEqual(result["budget"]["generation_tokens_reserved"], 256)
        self.assertLessEqual(
            result["budget"]["generation_tokens_reserved"], limits.total_num_predict
        )
        self.assertEqual(fake.requests[-1][1], [])
        self.assertIsNotNone(fake.requests[-1][2].get("format_schema"))

    def test_research_time_stops_early_and_leaves_conclusion_time(self):
        clock = FakeClock()
        fake = FakeOllama([call("analyze_candidate", {"market": "AAA-EUR"})])

        def slow_history(market, candidate, scan_result, remaining):
            clock.value = 7.0
            return history(market)

        limits = ResearchLimits(
            total_timeout_seconds=10,
            conclusion_timeout_seconds=3,
            num_predict=128,
            conclusion_num_predict=128,
            total_num_predict=512,
        )
        runner, _ = self.make_runner(fake, limits=limits, clock=clock, analyze_fn=slow_history)
        result = runner.run("Bilan avec réserve temporelle.")
        self.assertEqual(result["stop_reason"], "research_time_reserved_for_conclusion")
        self.assertEqual(result["conclusion"]["mode"], "model_structured")
        self.assertEqual(fake.requests[-1][2]["timeout"], 3)
        self.assertLessEqual(result["budget"]["elapsed_seconds"], 10)

    def test_conclusion_timeout_and_truncation_use_deterministic_fallback(self):
        finish = call(
            "finish_research",
            {
                "candidates": [
                    {
                        "market": "AAA-EUR",
                        "fact_refs": ["scan:AAA-EUR:score"],
                        "missing_refs": [],
                        "interpretation": "ignored by scope transition",
                        "conclusion": "ignored by scope transition",
                    }
                ]
            },
        )
        limits = ResearchLimits(
            total_timeout_seconds=10,
            conclusion_timeout_seconds=3,
            num_predict=128,
            conclusion_num_predict=128,
            total_num_predict=256,
        )

        clock = FakeClock()
        expired = FakeOllama([finish], before_conclusion=lambda: setattr(clock, "value", 10.1))
        runner, _ = self.make_runner(expired, limits=limits, clock=clock)
        result = runner.run("Conclusion expirée.")
        self.assertEqual(result["conclusion"]["mode"], "deterministic_fallback")
        self.assertEqual(result["conclusion"]["reason"], "budget_exhausted_during_conclusion")
        self.assertIsNone(result["conclusion"]["classification"])
        self.assertTrue(
            all("classification" not in row for row in result["conclusion"]["candidates"])
        )

        truncated = FakeOllama(
            [finish],
            conclusion_response={
                "message": {"content": "{", "tool_calls": []},
                "generation": {"eval_count": 128},
                "done_reason": "length",
                "duration_ms": 10,
                "request_bytes": 100,
                "response_bytes": 100,
                "timeout_seconds": 3,
                "num_ctx": 8192,
            },
        )
        runner, _ = self.make_runner(truncated, limits=limits)
        result = runner.run("Conclusion tronquée.")
        self.assertEqual(result["conclusion"]["mode"], "deterministic_fallback")
        self.assertEqual(result["conclusion"]["reason"], "conclusion_generation_truncated")
        self.assertEqual(result["budget"]["conclusion_generation_tokens_reserved"], 128)
        conclusion_event = next(
            event for event in result["events"] if event.get("phase") == "conclusion"
        )
        self.assertEqual(conclusion_event["status"], "error")

    def test_conclusion_keeps_missing_scope_without_reference_echo(self):
        fake = FakeOllama(
            [
                call("get_fundamentals", {"market": "AAA-EUR"}),
                call(
                    "finish_research",
                    {
                        "candidates": [
                            {
                                "market": "AAA-EUR",
                                "fact_refs": ["fundamentals:AAA-EUR:identity_status"],
                                "missing_refs": ["fundamentals:AAA-EUR:market_cap"],
                            }
                        ]
                    },
                ),
            ]
        )
        runner, _ = self.make_runner(
            fake,
            fundamentals_fn=lambda market, candidate, remaining: fundamentals(
                market, identity="unresolved"
            ),
        )
        result = runner.run("Garder les limites dans le bilan.")
        self.assertEqual(result["conclusion"]["mode"], "model_structured")
        self.assertIn("fundamentals:AAA-EUR:market_cap", result["missing_ledger"])
        self.assertIn(
            "fundamentals:AAA-EUR:market_cap",
            result["candidates"][0]["missing_references"],
        )
        self.assertIn(
            "fundamentals:AAA-EUR:market_cap",
            result["conclusion"]["candidates"][0]["missing_refs"],
        )
        self.assertEqual(result["conclusion"]["candidates"][0]["status"], "complete")
        self.assertEqual(
            result["candidates"][0]["optional_missing_references"],
            ["fundamentals:AAA-EUR:market_cap"],
        )

    def test_context_budget_traces_shortlist_reduction_and_long_value_compaction(self):
        markets = [f"ASSET-{index:02d}-" + ("X" * 80) for index in range(20)]
        rows = {
            market: {
                "market": market,
                "base_asset": market.split("-")[0],
                "score": "1",
                "volume_quote_24h_eur": "100",
                "price_change_24h_pct": "0",
                "spread_pct": "0.1",
                "collected_at": "2026-11-09T10:00:00Z",
                "source_at": "2026-11-09T10:00:00Z",
            }
            for market in markets
        }
        facts = {
            "fundamentals:ASSET-00-long:description": {
                "market": markets[0],
                "value": {"value": "long external text " * 100},
                "source": "coingecko",
                "collected_at": "2026-11-09T10:00:00Z",
            }
        }
        context = _budgeted_research_context(
            {"role": "system", "content": "Read only."},
            "Research objective",
            markets,
            rows,
            {markets[0]},
            {"max_candidates": 5},
            facts,
            {},
            {},
            feedback=None,
            num_ctx=8192,
            num_predict=1200,
            safety_margin=4096,
        )
        self.assertIsNotNone(context)
        self.assertTrue(context["reduced_markets"])
        self.assertNotIn(markets[0], context["reduced_markets"])
        self.assertIn("fundamentals:ASSET-00-long:description", context["compacted_refs"])
        self.assertLessEqual(context["estimated_prompt_tokens"] + 1200 + 4096, 8192)

    def test_truncated_model_turn_never_executes_partial_tool_request(self):
        class TruncatedOllama(FakeOllama):
            def chat_with_tools(self, messages, tools, **kwargs):
                self.requests.append((messages, tools, kwargs))
                return {
                    "message": {
                        "content": "",
                        "tool_calls": [call("analyze_candidate", {"market": "AAA-EUR"})],
                    },
                    "generation": {"eval_count": 128},
                    "done_reason": "length",
                    "duration_ms": 10,
                    "request_bytes": 100,
                    "response_bytes": 100,
                }

        executed = []
        runner, _ = self.make_runner(
            TruncatedOllama([]), analyze_fn=lambda *args: executed.append(True)
        )
        result = runner.run("Analyse bornée.")
        self.assertEqual(result["stop_reason"], "generation_budget_exhausted_during_research_turn")
        self.assertEqual(executed, [])

    def test_partial_tool_error_keeps_previous_results(self):
        fake = FakeOllama(
            [
                call("analyze_candidate", {"market": "AAA-EUR"}),
                call("get_fundamentals", {"market": "BBB-EUR"}),
                call(
                    "finish_research",
                    {
                        "candidates": [
                            {
                                "market": "BBB-EUR",
                                "fact_refs": ["scan:BBB-EUR:score"],
                                "missing_refs": [],
                                "interpretation": "Une source est en erreur.",
                                "conclusion": "Bilan partiel.",
                            }
                        ]
                    },
                ),
            ]
        )

        def fundamentals_error(*args):
            raise RuntimeError("provider unavailable")

        runner, _ = self.make_runner(fake, fundamentals_fn=fundamentals_error)
        result = runner.run("Comparer plusieurs candidats.")
        self.assertEqual(result["status"], "partial")
        self.assertIn("history:AAA-EUR:returns_pct.24h", result["fact_ledger"])
        self.assertTrue(any(event.get("error") == "RuntimeError" for event in result["events"]))

    def test_tool_observations_return_to_model_with_values_but_bounded(self):
        fake = FakeOllama(
            [
                call("analyze_candidate", {"market": "AAA-EUR"}),
                call(
                    "finish_research",
                    {
                        "candidates": [
                            {
                                "market": "AAA-EUR",
                                "fact_refs": ["history:AAA-EUR:returns_pct.24h"],
                                "missing_refs": [],
                                "interpretation": "Mesure observée.",
                                "conclusion": "Bilan descriptif.",
                            }
                        ]
                    },
                ),
            ]
        )
        runner, _ = self.make_runner(fake)
        result = runner.run("Lire les données historiques.")
        content = __import__("json").loads(fake.requests[1][0][-1]["content"])
        fact = next(
            row
            for row in content["verified_observation_registry"]
            if row["reference"] == "history:AAA-EUR:returns_pct.24h"
        )
        self.assertEqual(fact["value"]["value"], "3.1")
        self.assertNotIn("observed_gaps", content["verified_observation_registry"])
        self.assertNotIn("analysis_quality", content)
        self.assertEqual(content["previous_tool_results"][0]["execution_status"], "success")
        self.assertEqual(
            content["previous_tool_results"][0]["information_result"], "facts_obtained"
        )
        self.assertIn("history:AAA-EUR:returns_pct.24h", result["fact_ledger"])

    def test_context_sends_scanner_measures_once_with_refs_not_diagnostics(self):
        from crypto_market_agents.trading.autonomous_research import _put_observation

        row = scan()["candidates"][0]
        facts = {}
        missing = {}
        for key, value in row.items():
            _put_observation(
                facts,
                missing,
                f"scan:AAA-EUR:{key}",
                value,
                source="bitvavo_scanner",
                collected_at=row["collected_at"],
                source_at=row["source_at"],
                unit=None,
                reason="scanner_field_absent",
            )
        _put_observation(
            facts,
            missing,
            "scan:AAA-EUR:diagnostics",
            {"verbose": "x" * 5000},
            source="bitvavo_scanner",
            collected_at=row["collected_at"],
            source_at=row["source_at"],
            unit=None,
            reason="scanner_field_absent",
        )
        context = _budgeted_research_context(
            {"role": "system", "content": "rules"},
            "objective",
            ["AAA-EUR"],
            {"AAA-EUR": row},
            set(),
            {"max_candidates": 5},
            facts,
            missing,
            {},
            feedback=None,
            num_ctx=8192,
            num_predict=512,
            safety_margin=512,
        )
        payload = __import__("json").loads(context["messages"][-1]["content"])
        shortlist = payload["scanner_shortlist"][0]
        refs = shortlist["fact_references"]
        self.assertEqual(refs["score"], "scan:AAA-EUR:score")
        self.assertEqual(shortlist["score"], row["score"])
        registry_refs = [item["reference"] for item in payload["verified_observation_registry"]]
        self.assertNotIn("scan:AAA-EUR:score", registry_refs)
        self.assertNotIn("scan:AAA-EUR:diagnostics", registry_refs)
        self.assertIn(
            "diagnostics scanner détaillés restent dans le rapport", payload["context_policy"]
        )

    def test_ollama_tool_turn_sends_think_false_and_hides_thinking(self):
        class Response:
            def __enter__(self):
                return self

            def __exit__(self, *args):
                return None

            def read(self, size):
                return (
                    __import__("json")
                    .dumps(
                        {
                            "done": True,
                            "done_reason": "stop",
                            "thinking": "internal detail must not escape",
                            "message": {
                                "role": "assistant",
                                "content": "",
                                "thinking": "internal detail must not escape",
                                "tool_calls": [
                                    {
                                        "function": {
                                            "name": "analyze_candidate",
                                            "arguments": {"market": "AAA-EUR"},
                                        }
                                    }
                                ],
                            },
                            "eval_count": 8,
                        }
                    )
                    .encode()
                )

        requests = []

        def opener(request, timeout):
            requests.append((request, timeout))
            return Response()

        ollama = OllamaCandidateEvaluator("qwen3:8b", opener=opener, timeout=10)
        result = ollama.chat_with_tools(
            [{"role": "user", "content": "Research"}],
            [{"type": "function", "function": {"name": "analyze_candidate"}}],
            num_predict=128,
            timeout=5,
            max_response_bytes=1024,
        )
        body = __import__("json").loads(requests[0][0].data)
        self.assertFalse(body["think"])
        self.assertEqual(body["options"]["num_predict"], 128)
        self.assertEqual(requests[0][0].full_url, "http://127.0.0.1:11434/api/chat")
        self.assertNotIn("thinking", result)
        self.assertNotIn("thinking", result["message"])
        self.assertEqual(result["generation"]["eval_count"], 8)
        self.assertEqual(result["timeout_seconds"], 5)
        self.assertEqual(result["num_ctx"], ollama.num_ctx)

        schema = {"type": "object", "required": ["candidates"]}
        ollama.chat_with_tools(
            [{"role": "user", "content": "Conclude"}],
            [],
            num_predict=128,
            timeout=4,
            max_response_bytes=1024,
            format_schema=schema,
        )
        conclusion_body = __import__("json").loads(requests[1][0].data)
        self.assertEqual(conclusion_body["tools"], [])
        self.assertEqual(conclusion_body["format"], schema)
        self.assertFalse(conclusion_body["think"])

    def test_cli_writes_new_report_without_financial_execution(self):
        class FakeRunner:
            def __init__(self, **kwargs):
                self.kwargs = kwargs

            def run(self, objective):
                return {
                    "schema_version": "autonomous-research.v3",
                    "status": "partial",
                    "stop_reason": "ollama_error",
                    "objective": objective,
                    "financial_action_executed": False,
                    "events": [],
                }

        with tempfile.TemporaryDirectory() as temp_dir:
            mapping_path = Path(temp_dir) / "mapping.json"
            mapping_path.write_text("{}", encoding="utf-8")
            report_path = Path(temp_dir) / "research.json"
            patches = (
                patch("crypto_market_agents.cli.OllamaCandidateEvaluator"),
                patch(
                    "crypto_market_agents.cli.BitvavoAssetClient.from_env", return_value=object()
                ),
                patch(
                    "crypto_market_agents.cli.CoinGeckoClient.from_free_public_api",
                    return_value=object(),
                ),
                patch("crypto_market_agents.cli.DefiLlamaClient.from_env", return_value=object()),
                patch("crypto_market_agents.cli.AutonomousResearchRunner", side_effect=FakeRunner),
            )
            with patches[0], patches[1], patches[2], patches[3], patches[4]:
                with redirect_stdout(StringIO()):
                    code = main(
                        [
                            "research",
                            "--objective",
                            "Dossier factuel",
                            "--mapping",
                            str(mapping_path),
                            "--json",
                            str(report_path),
                        ]
                    )
            self.assertEqual(code, 0)
            report = __import__("json").loads(report_path.read_text(encoding="utf-8"))
            self.assertFalse(report["financial_action_executed"])
            with redirect_stdout(StringIO()):
                code = main(
                    [
                        "research",
                        "--objective",
                        "Dossier factuel",
                        "--mapping",
                        str(mapping_path),
                        "--json",
                        str(report_path),
                    ]
                )
            self.assertEqual(code, 1)

    def test_cli_fundamentals_callback_uses_candidate_timestamp(self):
        from crypto_market_agents.http_utils import HTTPClientSettings

        class Client:
            timeout_seconds = 10
            http_settings = HTTPClientSettings(max_retries=0, backoff_seconds=0)

        candidate = {
            "market": "AAA-EUR",
            "base_asset": "AAA",
            "quote_asset": "EUR",
            "score": "70",
            "volume_quote_24h_eur": "12345",
            "price_change_24h_pct": "1",
            "collected_at": "2026-11-09T10:00:00+00:00",
            "source_at": "2026-11-09T09:59:00+00:00",
        }

        class Scanner:
            def __init__(self, **kwargs):
                pass

            def scan(self):
                return {"collected_at": candidate["collected_at"], "candidates": [candidate]}

        class Ollama:
            timeout = 90

            def __init__(self, *args, **kwargs):
                self.turn = 0

            def chat_with_tools(self, messages, tools, **kwargs):
                self.turn += 1
                if not tools:
                    payload = __import__("json").loads(messages[-1]["content"])
                    rows = [
                        {
                            "market": row["market"],
                            "status": "partial",
                            "interpretation": "Identité vérifiée par le code.",
                            "conclusion": "Dossier consulté.",
                        }
                        for row in payload["candidate_scope"]
                    ]
                    return {
                        "message": {
                            "content": __import__("json").dumps({"candidates": rows}),
                            "tool_calls": [],
                        },
                        "generation": {"eval_count": 20},
                        "done_reason": "stop",
                        "duration_ms": 1,
                        "request_bytes": 1,
                        "response_bytes": 1,
                        "timeout_seconds": kwargs.get("timeout"),
                        "num_ctx": 8192,
                    }
                if self.turn == 1:
                    requested = call("get_fundamentals", {"market": "AAA-EUR"})
                else:
                    requested = call(
                        "finish_research",
                        {
                            "candidates": [
                                {
                                    "market": "AAA-EUR",
                                    "fact_refs": ["fundamentals:AAA-EUR:identity_status"],
                                    "missing_refs": [],
                                    "interpretation": "Identité vérifiée par le code.",
                                    "conclusion": "Dossier consulté.",
                                }
                            ]
                        },
                    )
                return {
                    "message": {"content": "", "tool_calls": [requested]},
                    "generation": {"eval_count": 20},
                    "done_reason": "stop",
                    "duration_ms": 1,
                    "request_bytes": 1,
                    "response_bytes": 1,
                }

        dossier = {
            "candidates": [
                {
                    "market": "AAA-EUR",
                    "identity_status": "unresolved",
                    "identity_reason": "no_explicit_verified_mapping",
                    "bitvavo_identity": {"symbol": "AAA"},
                    "coingecko_identity": {"provider_id": None},
                    "symbol_candidates": [],
                    "observations": {},
                    "protocol": {
                        "availability": "unavailable",
                        "reason": "no_verified_protocol_link",
                    },
                    "provider_errors": {},
                }
            ]
        }
        with tempfile.TemporaryDirectory() as temp_dir:
            mapping_path = Path(temp_dir) / "mapping.json"
            mapping_path.write_text("{}", encoding="utf-8")
            report_path = Path(temp_dir) / "research.json"
            with (
                patch("crypto_market_agents.cli.BitvavoScanner", Scanner),
                patch("crypto_market_agents.cli.OllamaCandidateEvaluator", Ollama),
                patch(
                    "crypto_market_agents.cli.BitvavoAssetClient.from_env", return_value=Client()
                ),
                patch(
                    "crypto_market_agents.cli.CoinGeckoClient.from_free_public_api",
                    return_value=Client(),
                ),
                patch("crypto_market_agents.cli.DefiLlamaClient.from_env", return_value=Client()),
                patch(
                    "crypto_market_agents.cli.enrich_analysis_report", return_value=dossier
                ) as enrich,
                redirect_stdout(StringIO()),
            ):
                code = main(
                    [
                        "research",
                        "--objective",
                        "Dossier fondamental",
                        "--mapping",
                        str(mapping_path),
                        "--json",
                        str(report_path),
                    ]
                )
            self.assertEqual(code, 0)
            self.assertEqual(enrich.call_args.args[0]["generated_at"], candidate["collected_at"])

    def test_budget_validation_and_empty_scan(self):
        with self.assertRaises(ValueError):
            ResearchLimits(max_tool_calls=11)
        with self.assertRaises(ValueError):
            ResearchLimits(num_predict=1200, conclusion_num_predict=1200, total_num_predict=2000)
        with self.assertRaises(ValueError):
            ResearchLimits(total_timeout_seconds=30, conclusion_timeout_seconds=30)
        with self.assertRaises(ValueError):
            ResearchLimits(context_safety_margin_tokens=127)
        fake = FakeOllama([])
        runner, _ = self.make_runner(fake, scan_data={"collected_at": "x", "candidates": []})
        result = runner.run("Aucun candidat.")
        self.assertEqual(result["status"], "completed")
        self.assertEqual(result["stop_reason"], "no_scanner_candidates")
        self.assertEqual(fake.requests, [])

    def test_complete_conclusion_is_rejected_when_scope_has_missing_information(self):
        class OverclaimingOllama(FakeOllama):
            def chat_with_tools(self, messages, tools, **kwargs):
                if tools:
                    return super().chat_with_tools(messages, tools, **kwargs)
                self.requests.append((messages, tools, kwargs))
                payload = __import__("json").loads(messages[-1]["content"])
                rows = [
                    {
                        "market": row["market"],
                        "status": "complete",
                        "interpretation": "Interprétation courte.",
                        "conclusion": "Bilan complet.",
                    }
                    for row in payload["candidate_scope"]
                ]
                return {
                    "message": {
                        "content": __import__("json").dumps({"candidates": rows}),
                        "tool_calls": [],
                    },
                    "generation": {"eval_count": 32},
                    "duration_ms": 10,
                    "think": False,
                    "done_reason": "stop",
                    "request_bytes": 100,
                    "response_bytes": 200,
                    "timeout_seconds": kwargs.get("timeout"),
                    "num_ctx": 8192,
                }

        fake = OverclaimingOllama(
            [
                call("get_fundamentals", {"market": "AAA-EUR"}),
                call(
                    "finish_research",
                    {
                        "candidates": [
                            {
                                "market": "AAA-EUR",
                                "fact_refs": ["fundamentals:AAA-EUR:identity_status"],
                                "missing_refs": ["fundamentals:AAA-EUR:market_cap"],
                                "required_missing_refs": ["fundamentals:AAA-EUR:market_cap"],
                            }
                        ]
                    },
                ),
            ]
        )
        runner, _ = self.make_runner(
            fake,
            fundamentals_fn=lambda market, candidate, remaining: fundamentals(
                market, identity="unresolved"
            ),
        )
        result = runner.run("Évaluer les preuves disponibles.")
        self.assertEqual(result["conclusion"]["mode"], "deterministic_fallback")
        self.assertEqual(
            result["conclusion"]["reason"],
            "invalid_conclusion:complete_conclusion_with_missing_information",
        )
        self.assertTrue(result["conclusion"]["candidates"])
        candidate = result["conclusion"]["candidates"][0]
        self.assertTrue(candidate["missing_refs"])
        self.assertIsNone(result["conclusion"]["classification"])

    def test_conclusion_schema_disallows_complete_only_for_blocking_missing_information(self):
        scope = [
            {"market": "AAA-EUR", "fact_refs": ["scan:AAA-EUR:score"], "missing_refs": []},
            {
                "market": "BBB-EUR",
                "fact_refs": ["scan:BBB-EUR:score"],
                "missing_refs": ["fundamentals:BBB-EUR:market_cap"],
                "required_missing_refs": ["fundamentals:BBB-EUR:market_cap"],
            },
        ]
        schema = _conclusion_schema(scope, max_candidates=5)
        status_enum = schema["properties"]["candidates"]["items"]["properties"]["status"]["enum"]
        self.assertEqual(status_enum, ["partial", "abstain"])
        optional_schema = _conclusion_schema(
            [
                {
                    "market": "AAA-EUR",
                    "fact_refs": ["scan:AAA-EUR:score"],
                    "missing_refs": ["fundamentals:AAA-EUR:market_cap"],
                    "required_missing_refs": [],
                }
            ],
            max_candidates=5,
        )
        self.assertEqual(
            optional_schema["properties"]["candidates"]["items"]["properties"]["status"]["enum"],
            ["complete", "partial", "abstain"],
        )


if __name__ == "__main__":
    unittest.main()
