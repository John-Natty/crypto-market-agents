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
    _budgeted_conclusion_context,
    _budgeted_research_context,
    _conclusion_schema,
)
import crypto_market_agents.trading.autonomous_research as research_module
from crypto_market_agents.trading.evaluation import OllamaCandidateEvaluator
from crypto_market_agents.cli import build_parser, main


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
        news_fn=None,
        gnews_fn=None,
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
            news=news_fn,
            gnews=gnews_fn,
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

    def test_model_can_choose_general_news_and_conclusion_labels_reported_claims(self):
        news_calls = []

        def news_fn(scope, market, identity, remaining):
            news_calls.append((scope, market, identity, remaining))
            return {
                "scope": "market",
                "search_scope": "general_market_context",
                "tool_invocation_market": market,
                "asset_attribution": "not_established",
                "search_window": {
                    "requested_days": 30,
                    "published_after": "2026-10-10T00:00:00+00:00",
                },
                "dates_obtained": {"oldest_publication_at": None, "newest_publication_at": None},
                "search_result_status": "relevant_items_observed_within_window",
                "coverage": {
                    "status": "partial_configured_source_coverage",
                    "coverage_missing": ["feed-two"],
                },
                "articles": [
                    {
                        "id": "article-1",
                        "title": "Source reports a market event",
                        "publisher": "Publisher",
                        "source_id": "feed-one",
                        "source_type": "news_article",
                        "url": "https://news.example/story",
                        "excerpt": "A reported claim.",
                        "excerpt_source": "description",
                        "is_full_article": False,
                        "published_at": None,
                        "publication_date_status": "unknown",
                        "collected_at": "2026-11-09T10:00:00+00:00",
                        "coverage_group_id": "group-1",
                        "relevance_status": "general_macro_regulatory_context",
                        "freshness_status": "within_requested_window",
                        "external_content_untrusted": True,
                    }
                ],
                "coverage_groups": [
                    {
                        "id": "group-1",
                        "article_refs": ["article-1"],
                        "publisher_count": 1,
                        "repeated_coverage_is_not_independent_confirmation": False,
                    }
                ],
                "source_results": [
                    {
                        "source_id": "feed-one",
                        "publisher": "Publisher",
                        "status": "success",
                        "article_count": 1,
                    },
                    {
                        "source_id": "feed-two",
                        "publisher": "Publisher Two",
                        "status": "error",
                        "error": "TimeoutError",
                    },
                ],
            }

        fake = FakeOllama(
            [
                call("get_market_news", {"market": "AAA-EUR"}),
                call(
                    "finish_research",
                    {
                        "candidates": [
                            {
                                "market": "AAA-EUR",
                                "fact_refs": ["news:general:AAA-EUR:article:article-1"],
                                "missing_refs": ["news:general:AAA-EUR:source:feed-two"],
                            }
                        ]
                    },
                ),
            ]
        )
        runner, _ = self.make_runner(fake, news_fn=news_fn)
        result = runner.run("Consulter les nouvelles générales du marché.")
        self.assertEqual(news_calls[0][0:3], ("market", "AAA-EUR", None))
        self.assertEqual(result["status"], "partial")
        self.assertEqual(result["research_status"], "partial")
        self.assertEqual(result["conclusion"]["candidates"][0]["status"], "partial")
        self.assertEqual(
            result["conclusion"]["candidates"][0]["status_adjustment_reason"],
            "execution_or_source_coverage_incomplete",
        )
        self.assertIn("news:general:AAA-EUR:article:article-1", result["fact_ledger"])
        self.assertIn("news:general:AAA-EUR:source:feed-two", result["missing_ledger"])
        self.assertIn(
            "news:general:AAA-EUR:article:article-1",
            result["conclusion"]["candidates"][0]["evidence_classes"]["reported_source_claims"],
        )
        self.assertEqual(
            result["conclusion"]["candidates"][0]["evidence_classes"]["market_observations"], []
        )
        article_fact = result["fact_ledger"]["news:general:AAA-EUR:article:article-1"]["value"]
        self.assertEqual(article_fact["search_scope"], "general_market_context")
        self.assertEqual(article_fact["asset_attribution"], "not_established")
        self.assertFalse(result["financial_action_executed"])

    def test_gnews_is_opt_in_and_runner_executes_only_requested_read_only_tool(self):
        calls = []

        def gnews_fn(scope, market, identity, remaining):
            calls.append((scope, market, identity))
            return {
                "provider": "gnews_api",
                "scope": scope,
                "query_id": "gnews-query-01",
                "search_result_status": "no_results_in_requested_window",
                "articles": [],
                "excluded_articles": [],
                "duplicate_groups": [],
                "coverage": {"status": "partial_single_provider"},
                "limits": {"free_plan_delay_hours": 12, "article_pages_fetched": False},
                "quota": {"research_requests_used": 1, "daily_requests_used": 1},
                "collected_at": "2026-11-09T10:00:00Z",
            }

        fake = FakeOllama(
            [
                call("get_gnews_market_news", {"market": "AAA-EUR"}),
                call(
                    "finish_research",
                    {
                        "candidates": [
                            {
                                "market": "AAA-EUR",
                                "fact_refs": ["scan:AAA-EUR:score"],
                                "missing_refs": [],
                            }
                        ]
                    },
                ),
            ]
        )
        runner, _ = self.make_runner(fake, scan_data=scan(), gnews_fn=gnews_fn)
        result = runner.run("Examiner le contexte crypto public.")
        self.assertEqual(calls, [("market", "AAA-EUR", None)])
        first_turn_tools = {item["function"]["name"] for item in fake.requests[0][1]}
        self.assertIn("get_gnews_market_news", first_turn_tools)
        self.assertNotIn("get_gnews_market_news", ALLOWED_TOOLS)
        self.assertIn("news:gnews:AAA-EUR:coverage", result["missing_ledger"])
        self.assertFalse(result["financial_action_executed"])

    def test_gnews_cli_requires_explicit_free_plan_confirmation(self):
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "research.json"
            args = build_parser().parse_args(
                ["research", "--objective", "Lire le contexte.", "--json", str(output)]
            )
            self.assertFalse(args.enable_gnews)
            self.assertFalse(args.confirm_gnews_free_plan)
            stdout = StringIO()
            with redirect_stdout(stdout):
                exit_code = main(
                    [
                        "research",
                        "--objective",
                        "Lire le contexte.",
                        "--json",
                        str(output),
                        "--enable-gnews",
                    ]
                )
            self.assertEqual(exit_code, 1)
            self.assertIn("--confirm-gnews-free-plan", stdout.getvalue())
            self.assertFalse(output.exists())

    def test_context_exhaustion_overrides_model_complete_and_hides_exhaustive_claim(self):
        complete_response = {
            "message": {
                "content": __import__("json").dumps(
                    {
                        "candidates": [
                            {
                                "market": "AAA-EUR",
                                "status": "complete",
                                "interpretation": "Synthèse non certifiée.",
                                "conclusion": "A comprehensive and complete review.",
                            }
                        ]
                    }
                ),
                "tool_calls": [],
            },
            "generation": {"eval_count": 20},
            "duration_ms": 10,
            "think": False,
            "done_reason": "stop",
            "request_bytes": 100,
            "response_bytes": 200,
            "timeout_seconds": 30,
            "num_ctx": 8192,
        }
        fake = FakeOllama(
            [call("get_fundamentals", {"market": "AAA-EUR"})],
            conclusion_response=complete_response,
        )
        original = research_module._budgeted_research_context
        calls = 0

        def exhaust_after_one_turn(*args, **kwargs):
            nonlocal calls
            calls += 1
            return None if calls > 1 else original(*args, **kwargs)

        runner, _ = self.make_runner(fake)
        with patch(
            "crypto_market_agents.trading.autonomous_research._budgeted_research_context",
            side_effect=exhaust_after_one_turn,
        ):
            result = runner.run("Recherche avec conclusion partielle.")
        candidate = result["conclusion"]["candidates"][0]
        self.assertEqual(result["stop_reason"], "context_budget_exhausted")
        self.assertEqual(result["status"], "partial")
        self.assertTrue(result["conclusion"]["partial"])
        self.assertEqual(candidate["status"], "partial")
        self.assertEqual(candidate["model_reported_status"], "complete")
        self.assertEqual(
            candidate["status_adjustment_reason"], "research_stop:context_budget_exhausted"
        )
        self.assertIn("Bilan partiel", candidate["conclusion"])
        self.assertIn("comprehensive", candidate["model_generated_conclusion_unverified"])
        displayed = result["candidates"][0]
        self.assertEqual(displayed["conclusion_status"], "partial")
        self.assertIn("Bilan partiel", displayed["research_conclusion"])
        self.assertIn("comprehensive", displayed["model_generated_conclusion_unverified"])

    def test_empty_news_search_records_limited_observation_not_event_absence(self):
        def no_news(scope, market, identity, remaining):
            return {
                "scope": "market",
                "search_scope": "general_market_context",
                "tool_invocation_market": market,
                "asset_attribution": "not_established",
                "search_window": {"requested_days": 30, "published_after": "2026-10-10T00:00:00Z"},
                "dates_obtained": {"oldest_publication_at": None, "newest_publication_at": None},
                "search_result_status": "no_feed_items_observed_within_requested_window",
                "event_absence_established": False,
                "coverage": {
                    "status": "configured_sources_checked",
                    "coverage_missing": [],
                },
                "articles": [],
                "coverage_groups": [],
                "source_results": [
                    {
                        "source_id": "feed-one",
                        "publisher": "Publisher",
                        "status": "success",
                        "article_count": 0,
                    },
                    {
                        "source_id": "feed-two",
                        "publisher": "Publisher Two",
                        "status": "success",
                        "article_count": 0,
                    },
                ],
                "collected_at": "2026-11-09T10:00:00+00:00",
            }

        fake = FakeOllama(
            [
                call("get_market_news", {"market": "AAA-EUR"}),
                call(
                    "finish_research",
                    {
                        "candidates": [
                            {
                                "market": "AAA-EUR",
                                "fact_refs": [],
                                "missing_refs": ["news:general:AAA-EUR:coverage"],
                                "required_missing_refs": [],
                            }
                        ]
                    },
                ),
            ]
        )
        runner, _ = self.make_runner(fake, news_fn=no_news)
        result = runner.run("Chercher du contexte général récent.")
        missing = result["missing_ledger"]["news:general:AAA-EUR:coverage"]
        self.assertEqual(missing["reason"], "no_feed_items_observed_within_requested_window")
        event = next(event for event in result["events"] if event.get("kind") == "tool_call")
        self.assertFalse(event["result_summary"]["event_absence_established"])
        self.assertEqual(
            event["result_summary"]["relevance_status"],
            "no_feed_items_observed_within_requested_window",
        )

    def test_asset_news_identity_gate_and_history_remain_available(self):
        requested_identity = []
        history_calls = []

        def news_fn(scope, market, identity, remaining):
            requested_identity.append(identity)
            self.assertEqual(scope, "asset")
            return {
                "scope": "asset",
                "identity_status": identity["status"],
                "identity_reason": "verified_identity_required",
                "articles": [],
                "coverage_groups": [],
                "source_results": [],
                "collected_at": "2026-11-09T10:00:00+00:00",
                "identity_check_execution": "fundamentals_executed_for_identity",
            }

        fake = FakeOllama(
            [
                call("get_asset_news", {"market": "AAA-EUR"}),
                call("analyze_candidate", {"market": "AAA-EUR"}),
                call(
                    "finish_research",
                    {
                        "candidates": [
                            {
                                "market": "AAA-EUR",
                                "fact_refs": ["history:AAA-EUR:returns_pct.24h"],
                                "missing_refs": ["news:asset:AAA-EUR:identity"],
                            }
                        ]
                    },
                ),
            ]
        )
        runner, _ = self.make_runner(
            fake,
            news_fn=news_fn,
            fundamentals_fn=lambda market, candidate, remaining: fundamentals(
                market, identity="unresolved"
            ),
            analyze_fn=lambda *args: history_calls.append(args[0]) or history(args[0]),
        )
        result = runner.run("Chercher des informations sur cet actif.")
        self.assertEqual(requested_identity[0]["status"], "unresolved")
        self.assertEqual(history_calls, ["AAA-EUR"])
        news_event = next(
            event
            for event in result["events"]
            if event.get("kind") == "tool_call"
            and event.get("request", {}).get("tool") == "get_asset_news"
        )
        self.assertEqual(news_event["identity_status"], "unresolved")
        self.assertIn("news:asset:AAA-EUR:identity", result["missing_ledger"])
        self.assertIn("history:AAA-EUR:returns_pct.24h", result["fact_ledger"])

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

    def test_news_context_uses_compact_reference_index_and_omits_full_excerpt(self):
        ref = "news:general:AAA-EUR:article:story-1"
        facts = {
            ref: {
                "market": "AAA-EUR",
                "source": "rss_feed",
                "collected_at": "2026-11-09T10:00:00+00:00",
                "source_at": "2026-11-08T10:00:00+00:00",
                "value": {
                    "evidence_class": "reported_source_content",
                    "search_scope": "general_market_context",
                    "asset_attribution": "not_established",
                    "relevance_status": "general_context_relevance_not_established",
                    "freshness_status": "within_requested_window",
                    "title": "Regulatory update",
                    "publisher": "Publisher",
                    "source_id": "feed-one",
                    "source_type": "official_announcement",
                    "published_at": "2026-11-08T10:00:00+00:00",
                    "publication_date_status": "available",
                    "collected_at": "2026-11-09T10:00:00+00:00",
                    "excerpt": "secret-full-excerpt-" + ("x" * 1000),
                    "url": "https://news.example/story",
                    "coverage_group_id": "group-1",
                },
            }
        }
        context = _budgeted_research_context(
            {"role": "system", "content": "read-only"},
            "Check recent general news.",
            ["AAA-EUR"],
            {"AAA-EUR": scan()["candidates"][0]},
            set(),
            {"max_candidates": 5},
            facts,
            {},
            {},
            feedback=None,
            num_ctx=8192,
            num_predict=512,
            safety_margin=512,
        )
        payload = __import__("json").loads(context["messages"][-1]["content"])
        item = next(
            row for row in payload["verified_observation_registry"] if row["reference"] == ref
        )
        self.assertEqual(item["value"]["search_scope"], "general_market_context")
        self.assertEqual(item["value"]["asset_attribution"], "not_established")
        self.assertLessEqual(len(item["value"]["excerpt"]), 120)
        self.assertNotEqual(item["value"]["excerpt"], facts[ref]["value"]["excerpt"])
        self.assertIn(ref, context["compacted_refs"])

    def test_research_context_drops_old_and_singleton_news_without_losing_report_refs(self):
        current_ref = "news:general:AAA-EUR:article:current"
        old_ref = "news:general:AAA-EUR:article:old"
        group_ref = "news:general:AAA-EUR:coverage:singleton"
        facts = {
            current_ref: {
                "market": "AAA-EUR",
                "source": "rss_feed",
                "value": {
                    "freshness_status": "within_requested_window",
                    "search_scope": "general_market_context",
                    "asset_attribution": "not_attributed_to_asset",
                    "title": "Current macro headline",
                    "publisher": "SEC",
                    "published_at": "2026-11-08T10:00:00+00:00",
                    "excerpt": "short",
                },
            },
            old_ref: {
                "market": "AAA-EUR",
                "source": "rss_feed",
                "value": {
                    "freshness_status": "outside_requested_window",
                    "title": "Old headline",
                    "publisher": "Fed",
                    "published_at": "2026-10-01T10:00:00+00:00",
                },
            },
            group_ref: {
                "market": "AAA-EUR",
                "source": "rss_title_similarity_grouping",
                "value": {
                    "article_refs": ["old"],
                    "repeated_coverage_is_not_independent_confirmation": False,
                },
            },
        }
        prior = {
            "execution_status": "success",
            "facts_obtained_count": 2,
            "unavailable_field_count": 0,
            "search_scope": "general_market_context",
            "current_article_count": 1,
            "outside_window_article_count": 1,
            "asset_specific_coverage_status": "no_asset_specific_source_configured_or_consulted",
            "available_fact_refs": [current_ref, old_ref],
            "missing_refs": [],
        }
        context = _budgeted_research_context(
            {"role": "system", "content": "rules"},
            "Review recent market context.",
            ["AAA-EUR"],
            {"AAA-EUR": scan()["candidates"][0]},
            {"AAA-EUR"},
            {"max_candidates": 5},
            facts,
            {},
            {"AAA-EUR": {"get_market_news": prior}},
            feedback=None,
            num_ctx=8192,
            num_predict=1200,
            safety_margin=512,
        )
        payload = __import__("json").loads(context["messages"][-1]["content"])
        refs = [row["reference"] for row in payload["verified_observation_registry"]]
        operation = payload["previous_tool_results"][0]
        self.assertIn(current_ref, refs)
        self.assertNotIn(old_ref, refs)
        self.assertNotIn(group_ref, refs)
        self.assertIn(old_ref, context["omitted_refs"])
        self.assertIn(group_ref, context["omitted_refs"])
        self.assertNotIn("available_fact_refs", operation)
        self.assertNotIn("missing_refs", operation)
        self.assertEqual(
            operation["asset_specific_coverage_status"],
            "no_asset_specific_source_configured_or_consulted",
        )
        decomposition = context["context_decomposition"]
        self.assertEqual(decomposition["chat_history_messages_retained"], 0)
        self.assertGreater(decomposition["component_bytes"]["tool_schemas"], 0)
        self.assertGreater(decomposition["component_bytes"]["news"], 0)
        self.assertTrue(decomposition["estimate_is_local_not_tokenizer_count"])

    def test_news_conclusion_index_fits_reserved_context_without_raw_urls(self):
        facts = {}
        refs = []
        for index in range(20):
            ref = f"news:general:AAA-EUR:article:story-{index}"
            refs.append(ref)
            facts[ref] = {
                "market": "AAA-EUR",
                "source": "rss_feed",
                "collected_at": "2026-11-09T10:00:00+00:00",
                "source_at": "2026-11-08T10:00:00+00:00",
                "value": {
                    "evidence_class": "reported_source_content",
                    "search_scope": "general_market_context",
                    "asset_attribution": "not_established",
                    "relevance_status": "general_context_relevance_not_established",
                    "freshness_status": "within_requested_window",
                    "title": f"Regulatory headline {index} " + ("headline " * 15),
                    "publisher": "Publisher",
                    "source_id": "feed-one",
                    "source_type": "official_announcement",
                    "coverage_domain": "regulatory",
                    "published_at": "2026-11-08T10:00:00+00:00",
                    "publication_date_status": "available",
                    "collected_at": "2026-11-09T10:00:00+00:00",
                    "excerpt": "A long article description " * 30,
                    "url": f"https://news.example/story-{index}",
                },
            }
        context = _budgeted_conclusion_context(
            "Evaluate recent macro context.",
            [
                {
                    "market": "AAA-EUR",
                    "fact_refs": refs,
                    "missing_refs": [],
                    "required_missing_refs": [],
                }
            ],
            facts,
            {},
            max_candidates=5,
            num_ctx=8192,
            num_predict=600,
            safety_margin=512,
        )
        self.assertIsNotNone(context)
        item = context["messages"][-1]["content"]
        self.assertNotIn("https://news.example/story-", item)
        self.assertIn("omitted_from_context", item)
        self.assertLessEqual(context["estimated_prompt_tokens"] + 600 + 512, 8192)

    def test_conclusion_separates_out_of_window_news_and_reports_source_scope(self):
        refs = [
            "news:general:AAA-EUR:article:current",
            "news:general:AAA-EUR:article:old",
            "news:general:AAA-EUR:coverage:singleton",
            "news:general:AAA-EUR:article:undated",
        ]
        facts = {
            refs[0]: {
                "market": "AAA-EUR",
                "source": "rss_feed",
                "value": {"freshness_status": "within_requested_window", "title": "Recent"},
            },
            refs[1]: {
                "market": "AAA-EUR",
                "source": "rss_feed",
                "value": {"freshness_status": "older_than_requested_window", "title": "Old"},
            },
            refs[2]: {
                "market": "AAA-EUR",
                "source": "rss_title_similarity_grouping",
                "value": {"repeated_coverage_is_not_independent_confirmation": False},
            },
            refs[3]: {
                "market": "AAA-EUR",
                "source": "rss_feed",
                "value": {
                    "freshness_status": "unknown_or_invalid_publication_date",
                    "title": "Undated",
                },
            },
        }
        context = research_module._budgeted_conclusion_context(
            "Summarize.",
            [{"market": "AAA-EUR", "fact_refs": refs, "missing_refs": []}],
            facts,
            {},
            max_candidates=5,
            num_ctx=8192,
            num_predict=600,
            safety_margin=512,
        )
        payload = __import__("json").loads(context["messages"][-1]["content"])
        candidate = payload["verified_evidence_registry"][0]
        fact_refs = [item["reference"] for item in candidate["facts"]]
        self.assertEqual(fact_refs, [refs[0]])
        self.assertEqual(context["omitted_refs"], sorted(refs[1:]))
        self.assertEqual(candidate["news_coverage"]["current_window_article_count"], 1)
        self.assertEqual(
            candidate["news_coverage"]["outside_window_articles_separated_from_current"], 1
        )
        self.assertEqual(
            candidate["news_coverage"]["unknown_publication_date_articles_not_treated_as_current"],
            1,
        )
        self.assertEqual(
            candidate["news_coverage"]["asset_specific_source_coverage"],
            "no_asset_specific_source_configured_or_consulted",
        )

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
                    "schema_version": "autonomous-research.v6",
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
