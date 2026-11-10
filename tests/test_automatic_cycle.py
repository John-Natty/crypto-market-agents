import hashlib
import json
from pathlib import Path
import threading
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from decimal import Decimal

from crypto_market_agents.trading.automatic_cycle import AutomaticCycleRunner, run_repeating
from crypto_market_agents.trading.multi_asset import (
    MultiAssetOrder,
    MultiAssetPaperEngine,
    MultiAssetPaperStore,
)
from crypto_market_agents.trading.models import Quote
from crypto_market_agents.trading.proposal_cycle import run_proposal_cycle
from crypto_market_agents.trading.proposal_execution import execute_proposal_cycle
from crypto_market_agents.trading.research_review import review_research_report
from crypto_market_agents.http_utils import (
    HTTPClientSettings,
    HTTPRequestBudgetExceeded,
    InMemoryTTLCache,
    _BudgetedRedirectHandler,
    _consume_http_request_budget,
    http_request_budget,
    send_request_with_retries,
)
from crypto_market_agents.trading.scanner import BitvavoScanner
from urllib.error import URLError
from urllib.request import Request


def _dump(path: Path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2) + "\n", encoding="utf-8")


def _hash(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _runner(tmp_path, invoke, **kwargs):
    state = tmp_path / "paper.json"
    MultiAssetPaperStore(state).initialize()
    return AutomaticCycleRunner(
        state=state,
        reports_dir=tmp_path / "reports",
        invoke=invoke,
        objective="Étudier les candidats admissibles",
        enable_paper_execution=kwargs.pop("enable_paper_execution", True),
        daily_budget_path=tmp_path / "daily-budget.json",
        **kwargs,
    )


def _mock_stages(tmp_path, *, terminal=False, fail_at=None, mutate_at=None):
    calls = []
    holder = {}

    def invoke(argv):
        stage = argv[0]
        calls.append(stage)
        output = Path(argv[argv.index("--json") + 1])
        cycle_dir = output.parent
        if stage == "research":
            _dump(
                output,
                {
                    "schema_version": "autonomous-research.v6",
                    "status": "completed",
                    "research_status": "completed",
                    "read_only": True,
                    "budget": {
                        "ollama_turns": 1,
                        "generation_tokens_reported": 200,
                        "tool_calls_used": 2,
                    },
                },
            )
        elif stage == "review-research":
            _dump(
                output,
                {
                    "schema_version": "research-contradiction-review.v3",
                    "status": "completed",
                    "source_report_sha256": _hash(cycle_dir / "research.json"),
                    "read_only": True,
                    "financial_action_executed": False,
                    "generation": {"eval_count": 100},
                },
            )
        elif stage == "proposal-cycle":
            if mutate_at == stage:
                with MultiAssetPaperStore(holder["state"]).transaction() as portfolio:
                    portfolio.realized_pnl_eur += 0
            research_hash = _hash(cycle_dir / "research.json")
            review_hash = _hash(cycle_dir / "review.json")
            _dump(
                output,
                {
                    "schema_version": "paper-proposal-cycle.v3",
                    "technical_status": "completed",
                    "model_response_status": "valid",
                    "decision_status": "abstained" if terminal else "plans_ready",
                    "cycle_id": "a" * 24,
                    "source_reports": {
                        "research": {"sha256": research_hash},
                        "review": {"sha256": review_hash},
                    },
                    "proposals": [{"status": "accepted", "action": "buy"}]
                    if not terminal
                    else [{"status": "abstained", "action": "abstain"}],
                    "generation": {"calls": [{}], "generated_tokens": 120},
                    "quote_observations": [],
                },
            )
        elif stage == "execute-proposal-cycle":
            _dump(output, {"status": "committed", "transaction_count": 1, "replayed": False})
        if stage == fail_at:
            raise RuntimeError("simulated_stage_failure")
        return 0

    return invoke, calls, holder


class AutomaticCycleTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.tmp_path = Path(self.temp.name)

    def tearDown(self):
        self.temp.cleanup()

    def test_http_attempt_budget_counts_retries_before_network(self):
        with http_request_budget(1) as budget:
            _consume_http_request_budget()
            with self.assertRaises(HTTPRequestBudgetExceeded):
                _consume_http_request_budget()
        self.assertEqual(budget["used"], 1)

    def test_followed_redirect_consumes_an_additional_http_request(self):
        request = Request("https://example.invalid/start")
        handler = _BudgetedRedirectHandler()
        with http_request_budget(2) as budget:
            redirected = handler.redirect_request(
                request,
                None,
                302,
                "Found",
                {"Location": "https://example.invalid/final"},
                "https://example.invalid/final",
            )
        self.assertIsNotNone(redirected)
        self.assertEqual(budget["used"], 1)

    def test_effective_component_budgets_fit_cycle_ceiling(self):
        runner = _runner(self.tmp_path, lambda _args: 0)
        research_args = runner._stage_argv(
            "research", self.tmp_path, self.tmp_path / "research.json", 600
        )
        self.assertEqual(research_args[0], "research")
        self.assertNotIn("--scan-json", research_args)
        review_args = runner._stage_argv(
            "review", self.tmp_path, self.tmp_path / "review.json", 600
        )
        proposal_args = runner._stage_argv(
            "proposal", self.tmp_path, self.tmp_path / "proposal.json", 600
        )

        def option(args, name):
            return args[args.index(name) + 1]

        self.assertEqual(option(research_args, "--timeout"), "300")
        self.assertEqual(option(research_args, "--num-predict"), "1200")
        self.assertEqual(option(research_args, "--total-num-predict"), "6000")
        self.assertEqual(option(research_args, "--conclusion-num-predict"), "600")
        self.assertEqual(option(research_args, "--conclusion-timeout"), "45")
        self.assertEqual(option(review_args, "--timeout"), "60")
        self.assertEqual(option(review_args, "--num-predict"), "900")
        self.assertEqual(option(proposal_args, "--timeout"), "60")
        self.assertEqual(option(proposal_args, "--num-predict"), "800")
        self.assertEqual(option(proposal_args, "--tracking-num-predict"), "384")
        self.assertEqual(option(proposal_args, "--max-tracked-positions"), "10")
        self.assertEqual(option(proposal_args, "--total-num-predict"), "8192")
        self.assertEqual(runner.cycle_limits["generation_tokens"], 16000)
        self.assertEqual(runner.daily_limits["generation_tokens"], 30000)
        stage_reserves = {
            stage: runner._stage_reserve(stage, 800, 120)
            for stage in ("research", "review", "proposal", "execution")
        }
        self.assertEqual(sum(row["generation_tokens"] for row in stage_reserves.values()), 15092)
        self.assertEqual(sum(row["llm_calls"] for row in stage_reserves.values()), 28)
        self.assertEqual(sum(row["external_tool_calls"] for row in stage_reserves.values()), 25)
        self.assertEqual(sum(row["http_requests"] for row in stage_reserves.values()), 120)
        self.assertEqual(sum(row["duration_seconds"] for row in stage_reserves.values()), 770)
        worst_case = {
            "generation_tokens": 15092,
            "llm_calls": 28,
            "external_tool_calls": 25,
            "http_requests": 120,
            "duration_seconds": 770,
        }
        for key, amount in worst_case.items():
            cycle_limit_key = "duration_seconds" if key == "duration_seconds" else key
            self.assertLessEqual(amount, runner.daily_limits[cycle_limit_key])
            if key != "duration_seconds":
                self.assertLessEqual(amount, runner.cycle_limits[key])
        self.assertEqual(runner.cycle_timeout_seconds, 800)
        self.assertEqual(runner.daily_limits["duration_seconds"], 3600)
        self.assertEqual(runner.daily_limits["llm_calls"], 60)
        self.assertEqual(runner.daily_limits["external_tool_calls"], 100)
        self.assertEqual(runner.daily_limits["http_requests"], 500)
        self.assertEqual(stage_reserves["execution"]["generation_tokens"], 0)
        self.assertEqual(stage_reserves["execution"]["llm_calls"], 0)
        self.assertEqual(
            runner._stage_budget_record(
                "research", 600, runner._stage_reserve("research", 600, 100)
            )["generation"]["conclusion_tokens"],
            600,
        )

    def test_http_count_is_persisted_even_when_stage_raises(self):
        tmp_path = self.tmp_path
        invoke, _, _ = _mock_stages(tmp_path)

        def failing_stage(argv):
            if argv[0] == "proposal-cycle":
                _consume_http_request_budget()
                raise RuntimeError("simulated failure after HTTP request")
            return invoke(argv)

        runner = _runner(tmp_path, failing_stage)
        result = runner.run()
        cycle_dir = tmp_path / "reports" / "automatic-cycles" / result["cycle_id"]
        manifest = json.loads((cycle_dir / "cycle.json").read_text())
        stage = manifest["stages"]["proposal"]
        self.assertEqual(result["status"], "technical_error")
        self.assertEqual(stage["status"], "technical_error")
        self.assertEqual(stage["http_requests_used"], 1)
        self.assertEqual(manifest["usage"]["http_requests"], 1)
        daily = json.loads(runner.daily_budget_path.read_text())
        day = datetime.now(timezone.utc).date().isoformat()
        self.assertEqual(daily["days"][day]["http_requests"], 1)

    def test_bitvavo_scan_request_uses_the_orchestration_http_budget(self):
        class Response:
            def __enter__(self):
                return self

            def __exit__(self, *_args):
                return None

            def read(self, _limit):
                return b"[]"

        scanner = BitvavoScanner(opener=lambda *_args, **_kwargs: Response())
        with http_request_budget(1) as budget:
            self.assertEqual(scanner._get("markets"), [])
        self.assertEqual(budget["used"], 1)

    def test_http_budget_counts_retries_but_not_cache_hits(self):
        class Response:
            status_code = 200
            body = '{"ok":true}'

            def __enter__(self):
                return self

            def __exit__(self, *_args):
                return None

        requests = []

        def opener(request, timeout):
            requests.append((request.full_url, timeout))
            if len(requests) == 1:
                raise URLError("temporary")
            return Response()

        request = Request("https://example.invalid/api")
        cache = InMemoryTTLCache()
        settings = HTTPClientSettings(
            max_retries=1,
            backoff_seconds=0,
            cache_ttl_seconds=60,
            cache_enabled=True,
        )
        with http_request_budget(2) as budget:
            first = send_request_with_retries(
                request,
                opener=opener,
                timeout_seconds=1,
                response_status=lambda response: response.status_code,
                read_text=lambda response: response.body,
                settings=settings,
                cache=cache,
            )
            second = send_request_with_retries(
                request,
                opener=opener,
                timeout_seconds=1,
                response_status=lambda response: response.status_code,
                read_text=lambda response: response.body,
                settings=settings,
                cache=cache,
            )
        self.assertEqual(first.body, second.body)
        self.assertEqual(len(requests), 2)
        self.assertEqual(budget["used"], 2)

    def test_full_cycle_executes_paper_plan_and_keeps_artifacts(self):
        tmp_path = self.tmp_path
        invoke, calls, _ = _mock_stages(tmp_path)
        runner = _runner(tmp_path, invoke)
        result = runner.run()
        directory = tmp_path / "reports" / "automatic-cycles" / result["cycle_id"]
        assert calls == ["research", "review-research", "proposal-cycle", "execute-proposal-cycle"]
        assert result["status"] == "paper_executed"
        assert all(
            (directory / name).is_file()
            for name in (
                "research.json",
                "review.json",
                "proposal.json",
                "execution.json",
                "cycle.json",
            )
        )
        assert result["stages"]["execution"]["outcome"]["transaction_count"] == 1

    def test_integrated_simulated_pipeline_uses_review_proposal_and_paper_engine(self):
        tmp_path = self.tmp_path
        state = tmp_path / "paper.json"
        store = MultiAssetPaperStore(state)
        store.initialize()
        ref = "scan:BTC-EUR:price_change_24h_pct"
        now = datetime.now(timezone.utc)
        research = {
            "schema_version": "autonomous-research.v6",
            "status": "completed",
            "generated_at": now.isoformat(),
            "objective": "Test d'intégration simulé",
            "read_only": True,
            "financial_action_executed": False,
            "conclusion": {
                "candidates": [
                    {
                        "market": "BTC-EUR",
                        "status": "complete",
                        "fact_refs": [ref],
                        "missing_refs": [],
                        "required_missing_refs": [],
                    }
                ]
            },
            "fact_ledger": {
                ref: {
                    "reference": ref,
                    "market": "BTC-EUR",
                    "source": "bitvavo_scanner",
                    "collected_at": now.isoformat(),
                    "source_at": now.isoformat(),
                    "value": {
                        "value": "2.5",
                        "unit": "%/24h",
                        "availability": "available",
                        "freshness": {"fresh": True},
                    },
                }
            },
            "missing_ledger": {},
            "budget": {"ollama_turns": 1, "generation_tokens_reported": 100, "tool_calls_used": 1},
        }

        class ReviewModel:
            def chat_with_tools(self, *_args, **_kwargs):
                payload = {
                    "reviews": [
                        {
                            "market": "BTC-EUR",
                            "arguments": [
                                {
                                    "reference": ref,
                                    "role": "favorable",
                                    "justification": "Observation disponible, sans prétendre qu'une hausse prouve la qualité.",
                                }
                            ],
                            "missing_refs": [],
                            "invalidation_conditions": [],
                            "verdict": "supported",
                        }
                    ]
                }
                return {
                    "message": {"content": json.dumps(payload), "tool_calls": []},
                    "done_reason": "stop",
                    "think": False,
                    "generation": {"eval_count": 80, "prompt_eval_count": 200},
                    "duration_ms": 1,
                    "request_bytes": 100,
                    "response_bytes": 100,
                }

        class ProposalModel:
            def chat_with_tools(self, messages, _tools, **_kwargs):
                market = json.loads(messages[1]["content"])["target_market"]
                payload = {
                    "market": market,
                    "action": "buy",
                    "references": [ref],
                    "rationale": "Proposition fictive fondée sur la référence fournie.",
                    "horizon": "7 jours",
                    "invalidation_conditions": ["Réexaminer si les preuves sont contredites"],
                }
                return {
                    "message": {"content": json.dumps(payload), "tool_calls": []},
                    "done_reason": "stop",
                    "think": False,
                    "generation": {"eval_count": 90, "prompt_eval_count": 200},
                    "duration_ms": 1,
                    "request_bytes": 100,
                    "response_bytes": 100,
                }

        class QuoteClient:
            def get_quote(self, market):
                return Quote(
                    market,
                    Decimal("100"),
                    Decimal("100.1"),
                    datetime.now(timezone.utc),
                    Decimal("5"),
                    Decimal("0.00000001"),
                    Decimal("0.000001"),
                )

        executed_calls = []

        def invoke(argv):
            stage = argv[0]
            output = Path(argv[argv.index("--json") + 1])
            folder = output.parent
            if stage == "research":
                _dump(output, research)
            elif stage == "review-research":
                raw = (folder / "research.json").read_bytes()
                result = review_research_report(
                    research,
                    ReviewModel(),
                    source_sha256=hashlib.sha256(raw).hexdigest(),
                    source_label="research.json",
                    model="fake",
                    now_provider=lambda: datetime.now(timezone.utc),
                )
                _dump(output, result)
            elif stage == "proposal-cycle":
                research_raw, review_raw = (
                    (folder / "research.json").read_bytes(),
                    (folder / "review.json").read_bytes(),
                )
                result = run_proposal_cycle(
                    json.loads(research_raw),
                    json.loads(review_raw),
                    store.load(),
                    ProposalModel(),
                    research_sha256=hashlib.sha256(research_raw).hexdigest(),
                    review_sha256=hashlib.sha256(review_raw).hexdigest(),
                    research_label="research.json",
                    review_label="review.json",
                    model="fake",
                    quote_client=QuoteClient(),
                )
                _dump(output, result)
            else:
                executed_calls.append("execute")
                research_raw, review_raw, plan_raw = (
                    (folder / name).read_bytes()
                    for name in ("research.json", "review.json", "proposal.json")
                )
                result = execute_proposal_cycle(
                    json.loads(plan_raw),
                    json.loads(research_raw),
                    json.loads(review_raw),
                    store,
                    research_sha256=hashlib.sha256(research_raw).hexdigest(),
                    review_sha256=hashlib.sha256(review_raw).hexdigest(),
                )
                _dump(output, result)
                raise KeyboardInterrupt
            return 0

        runner = AutomaticCycleRunner(
            state=state,
            reports_dir=tmp_path / "reports",
            invoke=invoke,
            objective="Test intégré",
            enable_paper_execution=True,
            daily_budget_path=tmp_path / "daily-budget.json",
        )
        result = runner.run()
        self.assertEqual(result["status"], "paper_executed")
        self.assertEqual(result["stages"]["proposal"]["outcome"]["accepted_plans"], 1)
        self.assertEqual(len(store.load().ledger), 1)
        self.assertEqual(len(store.load().cycle_receipts), 1)
        folder = tmp_path / "reports" / "automatic-cycles" / result["cycle_id"]
        resumed = runner.run(resume=folder)
        self.assertEqual(resumed["status"], "paper_executed")
        self.assertEqual(executed_calls, ["execute"])
        self.assertEqual(len(store.load().ledger), 1)

    def test_execution_is_off_by_default(self):
        tmp_path = self.tmp_path
        invoke, calls, _ = _mock_stages(tmp_path)
        runner = AutomaticCycleRunner(
            state=tmp_path / "paper.json",
            reports_dir=tmp_path / "reports",
            invoke=invoke,
            objective="test",
            enable_paper_execution=False,
            daily_budget_path=tmp_path / "daily-budget.json",
        )
        MultiAssetPaperStore(tmp_path / "paper.json").initialize()
        result = runner.run()
        assert "execute-proposal-cycle" not in calls
        assert result["stages"]["execution"]["status"] == "disabled_by_default"

    def test_tracking_only_empty_portfolio_finishes_without_stage_or_budget_calls(self):
        tmp_path = self.tmp_path
        calls = []
        runner = _runner(
            tmp_path,
            lambda argv: calls.append(argv[0]) or 0,
            tracking_only=True,
        )
        result = runner.run()
        self.assertEqual(result["status"], "no_open_positions")
        self.assertEqual(result["cycle_type"], "position_tracking_only")
        self.assertEqual(calls, [])
        self.assertFalse((tmp_path / "daily-budget.json").exists())

    def test_tracking_only_resumes_without_repeating_history_or_model(self):
        from crypto_market_agents.trading.proposal_cycle import collect_position_history_report

        tmp_path = self.tmp_path
        state = tmp_path / "paper.json"
        store = MultiAssetPaperStore(state)
        store.initialize()
        with store.transaction() as portfolio:
            MultiAssetPaperEngine().execute(
                portfolio,
                MultiAssetOrder(
                    "buy", "bitvavo", "BTC-EUR", "fixture position", amount_eur=Decimal("20")
                ),
                Quote("BTC-EUR", Decimal("60000"), Decimal("60001"), datetime.now(timezone.utc)),
                "tracking-only-seed",
                now=datetime.now(timezone.utc),
            )
        before = store.load().to_dict()
        calls = []

        class CandleClient:
            def __init__(self):
                self.calls = 0

            def get_candles(self, market, *, interval, limit):
                self.calls += 1
                now = datetime.now(timezone.utc)
                start = now - timedelta(hours=801)
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

        candle = CandleClient()

        class Model:
            def chat_with_tools(self, messages, tools, **kwargs):
                market = json.loads(messages[1]["content"])["target_market"]
                payload = {
                    "market": market,
                    "action": "abstain",
                    "references": [],
                    "rationale": "Les preuves ne justifient pas une action.",
                    "horizon": "",
                    "invalidation_conditions": [],
                }
                return {
                    "message": {"content": json.dumps(payload), "tool_calls": []},
                    "done_reason": "stop",
                    "think": False,
                    "generation": {"eval_count": 40, "prompt_eval_count": 300},
                    "duration_ms": 5,
                    "request_bytes": 500,
                    "response_bytes": 300,
                }

        class QuoteClient:
            def get_quote(self, market):
                return Quote(market, Decimal("60000"), Decimal("60001"), datetime.now(timezone.utc))

        def invoke(argv):
            calls.append(argv[0])
            output = Path(argv[argv.index("--json") + 1])
            directory = output.parent
            if argv[0] == "position-history":
                report = collect_position_history_report(
                    store.load(),
                    candle,
                    reports_dir=tmp_path / "reports",
                    now=datetime.now(timezone.utc),
                )
                _dump(output, report)
            elif argv[0] == "proposal-cycle":
                research_raw = (directory / "research.json").read_bytes()
                review_raw = (directory / "review.json").read_bytes()
                report = run_proposal_cycle(
                    json.loads(research_raw),
                    json.loads(review_raw),
                    store.load(),
                    Model(),
                    research_sha256=hashlib.sha256(research_raw).hexdigest(),
                    review_sha256=hashlib.sha256(review_raw).hexdigest(),
                    research_label="research.json",
                    review_label="review.json",
                    model="fake",
                    now_provider=lambda: datetime.now(timezone.utc),
                    quote_client=QuoteClient(),
                    position_tracking_only=True,
                )
                _dump(output, report)
            else:
                raise AssertionError(f"unexpected stage: {argv[0]}")
            return 0

        runner = AutomaticCycleRunner(
            state=state,
            reports_dir=tmp_path / "reports",
            invoke=invoke,
            objective="Suivi des positions détenues",
            enable_paper_execution=True,
            tracking_only=True,
            daily_budget_path=tmp_path / "daily-budget.json",
        )
        result = runner.run()
        self.assertEqual(calls, ["position-history", "proposal-cycle"])
        self.assertEqual(candle.calls, 1)
        self.assertEqual(result["cycle_type"], "position_tracking_only")
        self.assertEqual(result["status"], "model_abstained")
        self.assertEqual(result["stages"]["review"]["status"], "not_applicable")
        self.assertEqual(store.load().to_dict(), before)
        cycle_dir = tmp_path / "reports" / "automatic-cycles" / result["cycle_id"]
        resumed = runner.run(resume=cycle_dir)
        self.assertEqual(resumed["status"], result["status"])
        self.assertEqual(calls, ["position-history", "proposal-cycle"])
        self.assertEqual(candle.calls, 1)
        self.assertEqual(store.load().to_dict(), before)

    def test_tracking_only_cycle_lock_rejects_a_concurrent_run(self):
        tmp_path = self.tmp_path
        state = tmp_path / "paper.json"
        store = MultiAssetPaperStore(state)
        store.initialize()
        with store.transaction() as portfolio:
            MultiAssetPaperEngine().execute(
                portfolio,
                MultiAssetOrder(
                    "buy", "bitvavo", "BTC-EUR", "fixture position", amount_eur=Decimal("20")
                ),
                Quote("BTC-EUR", Decimal("60000"), Decimal("60001"), datetime.now(timezone.utc)),
                "tracking-lock-seed",
                now=datetime.now(timezone.utc),
            )
        entered = threading.Event()
        release = threading.Event()

        def blocked(argv):
            self.assertEqual(argv[0], "position-history")
            entered.set()
            release.wait(3)
            raise KeyboardInterrupt

        runner = AutomaticCycleRunner(
            state=state,
            reports_dir=tmp_path / "reports",
            invoke=blocked,
            objective="Suivi des positions",
            tracking_only=True,
            daily_budget_path=tmp_path / "daily-budget.json",
        )
        thread = threading.Thread(target=runner.run)
        thread.start()
        self.assertTrue(entered.wait(2))
        with self.assertRaisesRegex(RuntimeError, "cycle_already_running"):
            runner.run()
        release.set()
        thread.join(5)
        self.assertFalse(thread.is_alive())

    def test_abstention_and_review_unavailable_do_not_execute(self):
        tmp_path = self.tmp_path
        invoke, calls, _ = _mock_stages(tmp_path, terminal=True)
        result = _runner(tmp_path, invoke).run()
        assert result["status"] == "abstained"
        assert "execute-proposal-cycle" not in calls

        invoke, calls, _ = _mock_stages(tmp_path)

        def unavailable(argv):
            code = invoke(argv)
            if argv[0] == "review-research":
                path = Path(argv[argv.index("--json") + 1])
                value = json.loads(path.read_text())
                value["status"] = "unavailable"
                _dump(path, value)
            if argv[0] == "proposal-cycle":
                path = Path(argv[argv.index("--json") + 1])
                value = json.loads(path.read_text())
                value["technical_status"] = "partial"
                value["decision_status"] = "plans_rejected"
                value["proposals"] = [
                    {
                        "market": "BTC-EUR",
                        "action": "hold",
                        "status": "not_evaluated",
                        "reasons": ["position_follow_up_evidence_incomplete"],
                    }
                ]
                value["position_tracking"] = {
                    "status": "partial",
                    "positions": [
                        {
                            "market": "BTC-EUR",
                            "status": "incomplete",
                            "suggested_action": "hold",
                        }
                    ],
                }
                _dump(path, value)
            return code

        other = tmp_path / "other"
        other.mkdir()
        result = _runner(other, unavailable).run()
        assert result["status"] == "plans_rejected"
        assert "proposal-cycle" in calls
        assert "execute-proposal-cycle" not in calls

    def test_research_failure_without_artifact_still_runs_incomplete_position_tracking(self):
        tmp_path = self.tmp_path
        invoke, calls, _ = _mock_stages(tmp_path)

        def continue_tracking(argv):
            if argv[0] == "research":
                raise TimeoutError("simulated research outage")
            code = invoke(argv)
            if argv[0] == "proposal-cycle":
                path = Path(argv[argv.index("--json") + 1])
                value = json.loads(path.read_text())
                value["technical_status"] = "partial"
                value["decision_status"] = "plans_rejected"
                value["proposals"] = [
                    {
                        "market": "BTC-EUR",
                        "action": "hold",
                        "status": "not_evaluated",
                        "reasons": ["position_follow_up_evidence_incomplete"],
                    }
                ]
                value["position_tracking"] = {
                    "status": "partial",
                    "positions": [
                        {
                            "market": "BTC-EUR",
                            "status": "incomplete",
                            "suggested_action": "hold",
                        }
                    ],
                }
                _dump(path, value)
            return code

        runner = _runner(tmp_path, continue_tracking)
        store = MultiAssetPaperStore(tmp_path / "paper.json")
        now = datetime.now(timezone.utc)
        with store.transaction() as portfolio:
            MultiAssetPaperEngine().execute(
                portfolio,
                MultiAssetOrder(
                    "buy", "bitvavo", "BTC-EUR", "fixture position", amount_eur=Decimal("20")
                ),
                Quote("BTC-EUR", Decimal("60000"), Decimal("60001"), now),
                "fixture-seed-btc",
                now=now,
            )
        before = store.load().to_dict()
        result = runner.run()
        cycle_dir = tmp_path / "reports" / "automatic-cycles" / result["cycle_id"]
        manifest = json.loads((cycle_dir / "cycle.json").read_text())
        research = json.loads((cycle_dir / "research.json").read_text())
        assert research["status"] == "unavailable"
        assert manifest["stages"]["research"]["invocation_error"].startswith(
            "research_invocation_failed:TimeoutError"
        )
        assert result["status"] == "plans_rejected"
        assert calls == ["review-research", "proposal-cycle"]
        assert "execute-proposal-cycle" not in calls
        assert store.load().to_dict() == before

    def test_intermediate_failure_is_technical_and_never_executes(self):
        tmp_path = self.tmp_path
        invoke, calls, _ = _mock_stages(tmp_path, fail_at="review-research")
        result = _runner(tmp_path, invoke).run()
        assert result["status"] == "technical_error"
        assert "execute-proposal-cycle" not in calls

    def test_resume_reuses_validated_research_artifact(self):
        tmp_path = self.tmp_path
        invoke, calls, _ = _mock_stages(tmp_path)
        first_calls = []

        def interrupt_after_research(argv):
            first_calls.append(argv[0])
            code = invoke(argv)
            if argv[0] == "research":
                raise KeyboardInterrupt
            return code

        runner = _runner(tmp_path, interrupt_after_research)
        first = runner.run()
        folder = tmp_path / "reports" / "automatic-cycles" / first["cycle_id"]
        assert first["status"] == "interrupted"
        resumed = runner.run(resume=folder)
        assert first_calls.count("research") == 1
        assert resumed["status"] == "paper_executed"

    def test_cycle_lock_rejects_concurrent_run(self):
        tmp_path = self.tmp_path
        entered = threading.Event()
        release = threading.Event()
        invoke, _, _ = _mock_stages(tmp_path)

        def blocked(argv):
            if not entered.is_set():
                entered.set()
                release.wait(3)
            return invoke(argv)

        runner = _runner(tmp_path, blocked)
        thread = threading.Thread(target=runner.run)
        thread.start()
        assert entered.wait(2)
        with self.assertRaisesRegex(RuntimeError, "cycle_already_running"):
            runner.run()
        release.set()
        thread.join(5)
        assert not thread.is_alive()

    def test_daily_budget_exhaustion_prevents_starting_research(self):
        tmp_path = self.tmp_path
        invoke, calls, _ = _mock_stages(tmp_path)
        runner = _runner(
            tmp_path,
            invoke,
            daily_limits={
                "duration_seconds": 1,
                "llm_calls": 1,
                "generation_tokens": 1,
                "external_tool_calls": 1,
                "http_requests": 1,
            },
        )
        result = runner.run()
        assert result["status"] == "budget_exhausted"
        assert not calls

    def test_cycle_generation_budget_stops_before_proposal(self):
        tmp_path = self.tmp_path
        with self.assertRaisesRegex(
            ValueError, "cycle_budget_cannot_fund_configured_stages:generation_tokens"
        ):
            _runner(
                tmp_path,
                lambda _args: 0,
                cycle_limits={
                    "llm_calls": 30,
                    "generation_tokens": 12000,
                    "external_tool_calls": 30,
                    "http_requests": 120,
                },
            )

    def test_repeat_stop_does_not_start_another_cycle(self):
        tmp_path = self.tmp_path
        stop = {"requested": False}
        invoke, calls, _ = _mock_stages(tmp_path)

        def stop_after_research(argv):
            result = invoke(argv)
            if argv[0] == "research":
                stop["requested"] = True
            return result

        runner = _runner(tmp_path, stop_after_research)
        results = run_repeating(
            runner,
            interval_seconds=1,
            stop_requested=lambda: stop["requested"],
            sleep=lambda _seconds: None,
        )
        assert len(results) == 1
        assert results[0]["status"] == "stopped"
        assert calls == ["research"]

    def test_portfolio_change_during_proposal_stops_before_execution(self):
        tmp_path = self.tmp_path
        state = tmp_path / "paper.json"
        MultiAssetPaperStore(state).initialize()
        invoke, calls, _ = _mock_stages(tmp_path)

        def mutate(argv):
            if argv[0] == "proposal-cycle":
                raw = json.loads(state.read_text())
                raw["created_at"] = "2020-01-01T00:00:00+00:00"
                state.write_text(json.dumps(raw), encoding="utf-8")
            return invoke(argv)

        runner = AutomaticCycleRunner(
            state=state,
            reports_dir=tmp_path / "reports",
            invoke=mutate,
            objective="test",
            enable_paper_execution=True,
            daily_budget_path=tmp_path / "daily-budget.json",
        )
        result = runner.run()
        assert result["status"] == "technical_error"
        assert result["stop_reason"] == "portfolio_changed_during_proposal"
        assert "execute-proposal-cycle" not in calls
