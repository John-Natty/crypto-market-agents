"""Command line interface for Crypto Market Agents."""

from __future__ import annotations

import argparse
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import Decimal
from pathlib import Path
import json
import time
from typing import Any

from crypto_market_agents.agents.final_synthesis_agent import FinalSynthesisAgent
from crypto_market_agents.agents.asset_fundamentals import enrich_analysis_report
from crypto_market_agents.clients.coingecko_client import CoinGeckoClient
from crypto_market_agents.clients.defillama_client import DefiLlamaClient
from crypto_market_agents.clients.bitvavo_asset_client import BitvavoAssetClient
from crypto_market_agents.dashboard import serve_dashboard
from crypto_market_agents.file_utils import reserve_report_paths
from crypto_market_agents.security import redact_text
from crypto_market_agents.trading.cli import add_paper_parser, run_paper
from crypto_market_agents.trading.scanner import BitvavoScanner, ScanConfig
from crypto_market_agents.trading.history_client import BitvavoCandleClient
from crypto_market_agents.trading.deep_analysis import DeepMarketAnalyzer
from crypto_market_agents.trading.autonomous_research import (
    AutonomousResearchRunner,
    ResearchLimits,
)
from crypto_market_agents.trading.evaluation import (
    DeterministicEvaluator,
    OllamaCandidateEvaluator,
    PROMPT_VERSION,
    build_snapshot,
    render_candidate_result,
    compare_evaluators,
)
from crypto_market_agents.mock_data import build_mock_agent_reports
from crypto_market_agents.notifications.whatsapp_notifier import (
    DEFAULT_MAX_MESSAGE_CHARS,
    ReportPaths,
    build_final_report_summary_message,
    build_high_risk_alert_message,
)
from crypto_market_agents.orchestrator import CryptoMarketOrchestrator
from crypto_market_agents.reporting.report_renderer import (
    save_html_report,
    save_json_report,
    save_markdown_report,
)
from crypto_market_agents.schemas import FinalReport, RiskLevel


@dataclass(frozen=True, slots=True)
class GeneratedReport:
    """Paths and summary metadata produced by one CLI report run."""

    final_report: FinalReport
    markdown_path: Path
    json_path: Path
    html_path: Path
    mock: bool
    whatsapp_summary: dict[str, Any] | None = None
    whatsapp_alert: dict[str, Any] | None = None
    whatsapp_preview: bool = False
    whatsapp_summary_preview: str | None = None
    whatsapp_alert_preview: str | None = None


class _UnavailableCandidateEvaluator:
    """Explicitly disabled Ollama side for deterministic-only comparisons."""

    def evaluate(self, snapshot):
        from crypto_market_agents.trading.evaluation import EvaluationResult

        return EvaluationResult(
            evaluator="ollama",
            version=PROMPT_VERSION,
            status="not_requested",
            results=tuple(
                render_candidate_result(
                    snapshot,
                    candidate,
                    {"classification": None, "interpretation": None},
                    evaluator_status="not_requested",
                )
                for candidate in snapshot.get("candidates", [])
            ),
            duration_ms=0,
            error=None,
        )


def build_parser() -> argparse.ArgumentParser:
    """Build the CLI argument parser."""

    parser = argparse.ArgumentParser(
        prog="crypto-market-agents",
        description="Generate crypto market analysis reports.",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    report_parser = subparsers.add_parser(
        "report",
        help="Run the full analysis flow and save Markdown/JSON/HTML reports.",
    )
    _add_report_arguments(report_parser)

    schedule_parser = subparsers.add_parser(
        "schedule",
        help="Run reports periodically with a lightweight local scheduler.",
    )
    schedule_parser.add_argument(
        "--interval-minutes",
        type=int,
        default=60,
        help="Minutes between two reports, default: 60.",
    )
    schedule_parser.add_argument(
        "--runs",
        type=int,
        default=None,
        help="Number of reports to generate before stopping. Defaults to run until Ctrl+C.",
    )
    _add_report_arguments(schedule_parser)

    dashboard_parser = subparsers.add_parser(
        "dashboard",
        help="Afficher les rapports et le suivi en direct du portefeuille simule.",
    )
    dashboard_parser.add_argument(
        "--reports-dir",
        default="reports",
        help="Directory containing generated reports, default: reports.",
    )
    dashboard_parser.add_argument(
        "--state",
        default=None,
        help="Fichier du portefeuille simule a suivre en direct (ex. paper/bitvavo.json).",
    )
    dashboard_parser.add_argument(
        "--host",
        default="127.0.0.1",
        help="Host interface for the local dashboard, default: 127.0.0.1.",
    )
    dashboard_parser.add_argument(
        "--port",
        type=int,
        default=8000,
        help="Port for the local dashboard, default: 8000.",
    )

    add_paper_parser(subparsers)
    scan = subparsers.add_parser(
        "scan", help="Présélection en lecture seule des marchés EUR Bitvavo."
    )
    scan.add_argument("--min-volume-eur", default="100000")
    scan.add_argument("--max-spread-pct", default="1")
    scan.add_argument("--include-unavailable", action="store_true")
    scan.add_argument("--json", dest="json_path", help="Exporter le résultat JSON vers ce fichier.")
    deep = subparsers.add_parser(
        "analyze", help="Analyse historique approfondie, en lecture seule."
    )
    deep.add_argument(
        "--scan-json", default="reports/market-scan.json", help="Résultat JSON de scan existant."
    )
    deep.add_argument(
        "--count", type=int, default=5, help="Nombre de candidats à analyser (défaut: 5)."
    )
    deep.add_argument("--interval", choices=("1h", "4h", "1d"), default="1h")
    deep.add_argument(
        "--limit",
        type=int,
        default=1440,
        help="Bougies demandées par candidat (maximum Bitvavo: 1440).",
    )
    deep.add_argument("--json", dest="json_path", default="reports/market-analysis.json")
    evaluate = subparsers.add_parser(
        "compare", help="Comparer les évaluateurs watch/avoid/abstain, sans exécution."
    )
    evaluate.add_argument("--analysis-json", default="reports/market-analysis.json")
    evaluate.add_argument("--json", dest="json_path", default="reports/evaluation-comparison.json")
    evaluate.add_argument(
        "--with-ollama", action="store_true", help="Inclure le modèle local déjà installé."
    )
    evaluate.add_argument("--model", help="Nom d'un modèle Ollama local existant.")
    evaluate.add_argument("--timeout", type=int, default=120)
    evaluate.add_argument("--num-predict", type=int, default=1200)
    evaluate.add_argument("--total-num-predict", type=int, default=6000)
    evaluate.add_argument("--total-timeout", type=int, default=180)
    evaluate.add_argument("--num-ctx", type=int, default=8192)
    evaluate.add_argument(
        "--diagnostic-dir", help="Capture locale optionnelle sous reports/ (ignorée)."
    )
    enrich = subparsers.add_parser(
        "enrich", help="Créer un dossier factuel CoinGecko/DefiLlama en lecture seule."
    )
    enrich.add_argument("--analysis-json", required=True, help="Rapport d'analyse existant.")
    enrich.add_argument(
        "--json", dest="json_path", required=True, help="Nouvel export JSON (doit ne pas exister)."
    )
    enrich.add_argument(
        "--mapping", help="JSON de correspondances explicites market/symbole vers CoinGecko ID."
    )
    research = subparsers.add_parser(
        "research", help="Recherche autonome bornée, locale et en lecture seule."
    )
    research.add_argument(
        "--objective", required=True, help="Question de recherche (1000 caractères max)."
    )
    research.add_argument("--model", default="qwen3:8b", help="Modèle Ollama local déjà installé.")
    research.add_argument(
        "--json", dest="json_path", required=True, help="Nouveau rapport JSON (jamais écrasé)."
    )
    research.add_argument("--mapping", default="data/asset-identity-mappings.json")
    research.add_argument("--max-candidates", type=int, default=5)
    research.add_argument("--max-tool-calls", type=int, default=10)
    research.add_argument(
        "--timeout", type=int, default=300, help="Budget total en secondes, plafond 300."
    )
    research.add_argument(
        "--llm-timeout", type=int, default=90, help="Timeout maximal par appel Ollama."
    )
    research.add_argument("--num-predict", type=int, default=1200)
    research.add_argument("--total-num-predict", type=int, default=6000)
    research.add_argument("--conclusion-num-predict", type=int, default=600)
    research.add_argument("--conclusion-timeout", type=int, default=45)
    research.add_argument("--context-safety-margin-tokens", type=int, default=512)
    research.add_argument("--max-response-bytes", type=int, default=200000)
    research.add_argument("--num-ctx", type=int, default=8192)
    return parser


def main(
    argv: Sequence[str] | None = None,
    *,
    orchestrator_factory: Callable[..., CryptoMarketOrchestrator] = CryptoMarketOrchestrator,
    dashboard_runner: Callable[..., None] = serve_dashboard,
    sleep_func: Callable[[float], None] = time.sleep,
    now_provider: Callable[[], datetime] = datetime.now,
) -> int:
    """Run the command line interface."""

    parser = build_parser()
    args = parser.parse_args(argv)

    if args.command == "enrich":
        try:
            source = Path(args.analysis_json)
            destination = Path(args.json_path)
            if destination.exists():
                raise ValueError("Le fichier de sortie existe déjà; choisir un nouveau chemin.")
            report = json.loads(source.read_text(encoding="utf-8"))
            if not isinstance(report, dict) or not isinstance(report.get("candidates"), list):
                raise ValueError("Rapport d'analyse invalide ou sans candidats.")
            mappings = {}
            if args.mapping:
                mappings = json.loads(Path(args.mapping).read_text(encoding="utf-8"))
                if not isinstance(mappings, dict) or not all(
                    isinstance(k, str) and isinstance(v, dict) for k, v in mappings.items()
                ):
                    raise ValueError("Le fichier de correspondances doit être un objet JSON.")
            dossier = enrich_analysis_report(
                report,
                bitvavo_client=BitvavoAssetClient.from_env(),
                coingecko_client=CoinGeckoClient.from_free_public_api(),
                defillama_client=DefiLlamaClient.from_env(),
                explicit_mappings=mappings,
            )
            destination.parent.mkdir(parents=True, exist_ok=True)
            with destination.open("x", encoding="utf-8") as handle:
                json.dump(dossier, handle, ensure_ascii=False, indent=2)
                handle.write("\n")
        except (OSError, json.JSONDecodeError, ValueError, RuntimeError) as exc:
            print(f"Enrichissement impossible: {exc}")
            return 1
        resolved = sum(
            item["identity_status"] in {"resolved_explicit", "resolved_cross_referenced"}
            for item in dossier["candidates"]
        )
        print(f"Dossier factuel | lecture seule | résolus: {resolved}/{len(dossier['candidates'])}")
        print(f"JSON: {destination}")
        return 0

    if args.command == "research":
        try:
            destination = Path(args.json_path)
            if destination.exists():
                raise ValueError("Le fichier de sortie existe déjà; choisir un nouveau chemin.")
            if args.llm_timeout <= 0 or args.llm_timeout > 180:
                raise ValueError("--llm-timeout doit être entre 1 et 180 secondes.")
            limits = ResearchLimits(
                max_candidates=args.max_candidates,
                max_tool_calls=args.max_tool_calls,
                total_timeout_seconds=args.timeout,
                num_predict=args.num_predict,
                total_num_predict=args.total_num_predict,
                max_response_bytes=args.max_response_bytes,
                conclusion_num_predict=args.conclusion_num_predict,
                conclusion_timeout_seconds=args.conclusion_timeout,
                context_safety_margin_tokens=args.context_safety_margin_tokens,
            )
            mapping_path = Path(args.mapping)
            mappings = (
                json.loads(mapping_path.read_text(encoding="utf-8"))
                if mapping_path.exists()
                else {}
            )
            if not isinstance(mappings, dict) or not all(
                isinstance(key, str) and isinstance(value, dict) for key, value in mappings.items()
            ):
                raise ValueError("Le fichier de mappings doit être un objet JSON.")
            evaluator = OllamaCandidateEvaluator(
                args.model,
                timeout=args.llm_timeout,
                num_predict=max(args.num_predict, args.conclusion_num_predict),
                total_timeout=args.timeout,
                total_num_predict=args.total_num_predict,
                num_ctx=args.num_ctx,
            )
            bitvavo_assets = BitvavoAssetClient.from_env()
            coingecko = CoinGeckoClient.from_free_public_api()
            defillama = DefiLlamaClient.from_env()

            def scan_with_budget(remaining):
                if remaining < 3:
                    raise TimeoutError("budget_exhausted_before_scan")
                return BitvavoScanner(timeout=min(10, max(1, remaining / 3))).scan()

            def analyze_one(market, candidate, scan_result, remaining):
                if remaining < 1:
                    raise TimeoutError("budget_exhausted_before_history")
                local_scan = {
                    "collected_at": scan_result.get("collected_at"),
                    "candidates": [candidate],
                    "rejected": [],
                }
                return DeepMarketAnalyzer(
                    BitvavoCandleClient(timeout=min(10, max(1, remaining)))
                ).analyze(local_scan, count=1, interval="1h", limit=1440)

            def fundamentals_one(market, candidate, remaining):
                if remaining <= 60:
                    raise TimeoutError("budget_exhausted_before_fundamentals")
                settings = [
                    bitvavo_assets.http_settings,
                    coingecko.http_settings,
                    defillama.http_settings,
                ]
                retries = max((setting.max_retries for setting in settings), default=0)
                backoff = max((setting.backoff_seconds for setting in settings), default=0)
                retry_allowance = 8 * backoff * ((2**retries) - 1)
                request_timeout = int((remaining - 48 - retry_allowance) / (8 * (retries + 1)))
                if request_timeout < 1:
                    raise TimeoutError("budget_too_small_for_fundamentals_requests")
                clients = (bitvavo_assets, coingecko, defillama)
                old_timeouts = [client.timeout_seconds for client in clients]
                try:
                    for client in clients:
                        client.timeout_seconds = min(client.timeout_seconds, request_timeout)
                    one_candidate = {
                        "market": market,
                        "scanner_candidate": candidate,
                    }
                    return enrich_analysis_report(
                        {
                            "generated_at": candidate.get("collected_at"),
                            "candidates": [one_candidate],
                        },
                        bitvavo_client=bitvavo_assets,
                        coingecko_client=coingecko,
                        defillama_client=defillama,
                        explicit_mappings=mappings,
                        identity_probe_limit=3,
                    )
                finally:
                    for client, old_timeout in zip(clients, old_timeouts, strict=True):
                        client.timeout_seconds = old_timeout

            runner = AutonomousResearchRunner(
                model=args.model,
                ollama=evaluator,
                scan=scan_with_budget,
                analyze=analyze_one,
                fundamentals=fundamentals_one,
                limits=limits,
            )
            result = runner.run(args.objective)
            destination.parent.mkdir(parents=True, exist_ok=True)
            with destination.open("x", encoding="utf-8") as handle:
                json.dump(result, handle, ensure_ascii=False, indent=2)
                handle.write("\n")
        except (OSError, json.JSONDecodeError, ValueError, RuntimeError, TimeoutError) as exc:
            print(f"Recherche impossible: {type(exc).__name__}: {exc}")
            return 1
        for event in result["events"]:
            if event["kind"] == "tool_call":
                request = event.get("request", {})
                print(
                    f"Outil {request.get('tool', 'inconnu')}: {event['status']} {event.get('error') or ''}"
                )
        print(
            f"Recherche {result['status']} | arrêt: {result['stop_reason']} | JSON: {destination}"
        )
        return 0

    if args.command == "paper":
        return run_paper(args, sleep_func=sleep_func)

    if args.command == "scan":
        try:
            result = BitvavoScanner().scan(
                ScanConfig(
                    Decimal(args.min_volume_eur),
                    Decimal(args.max_spread_pct),
                    not args.include_unavailable,
                )
            )
        except (ValueError, RuntimeError) as exc:
            print(f"Scan impossible: {exc}")
            return 1
        print("SCAN EUR BITVAVO | lecture seule — aucun achat")
        for index, candidate in enumerate(result["candidates"], 1):
            print(
                f"{index:>3}. {candidate['market']} | score {candidate['score']} | volume {candidate['volume_quote_24h_eur']} EUR | variation {candidate['price_change_24h_pct']}% | spread {candidate['spread_pct']}%"
            )
        print(f"Rejetés: {len(result['rejected'])}")
        if args.json_path:
            Path(args.json_path).write_text(
                json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8"
            )
            print(f"JSON: {args.json_path}")
        return 0

    if args.command == "analyze":
        try:
            if args.count < 1 or not 1 <= args.limit <= 1440:
                raise ValueError("count doit être positif et limit entre 1 et 1440.")
            scan_result = json.loads(Path(args.scan_json).read_text(encoding="utf-8"))
            if not isinstance(scan_result, dict) or not isinstance(
                scan_result.get("candidates"), list
            ):
                raise ValueError("Fichier de scan invalide ou sans candidats.")
            rejected = scan_result.get("rejected", [])
            if not isinstance(rejected, list):
                raise ValueError("Liste rejected du scan invalide.")
            result = DeepMarketAnalyzer(BitvavoCandleClient(), now_provider=now_provider).analyze(
                scan_result, count=args.count, interval=args.interval, limit=args.limit
            )
            Path(args.json_path).parent.mkdir(parents=True, exist_ok=True)
            Path(args.json_path).write_text(
                json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8"
            )
        except (OSError, json.JSONDecodeError, ValueError, RuntimeError) as exc:
            print(f"Analyse impossible: {exc}")
            return 1
        print("ANALYSE HISTORIQUE BITVAVO | lecture seule — aucun ordre, aucune décision LLM")
        for item in result["candidates"]:
            print(f"{item['market']} | {item['disposition']}")
            metrics = item.get("metrics", {})
            print(
                f"  clôtures: {item.get('data_sufficiency', {}).get('closed_candles', 0)} | variation 24h: {metrics.get('returns_pct', {}).get('24h', 'insuffisante')}% | volatilité: {metrics.get('return_volatility_pct_per_interval', 'insuffisante')}% par {args.interval}"
            )
            for reason in item.get("reasons", []):
                print(f"  - {reason}")
        print(f"JSON: {args.json_path}")
        return 0

    if args.command == "compare":
        try:
            report = json.loads(Path(args.analysis_json).read_text(encoding="utf-8"))
            if not isinstance(report, dict) or not isinstance(report.get("candidates"), list):
                raise ValueError("Rapport d'analyse invalide.")
            if args.timeout <= 0:
                raise ValueError("Le timeout doit être positif.")
            snapshot_path = Path(args.analysis_json).with_name("analysis-snapshot.json")
            if snapshot_path.exists():
                snapshot = json.loads(snapshot_path.read_text(encoding="utf-8"))
                from crypto_market_agents.trading.evaluation import verify_snapshot

                if not verify_snapshot(snapshot):
                    raise ValueError("Snapshot existant altéré ou invalide.")
                if snapshot.get("source_report_generated_at") != report.get("generated_at"):
                    raise ValueError("Snapshot associé à un autre rapport d'analyse.")
            else:
                snapshot = build_snapshot(report)
                snapshot_path.write_text(
                    json.dumps(snapshot, ensure_ascii=False, indent=2), encoding="utf-8"
                )
            if args.with_ollama:
                if not args.model:
                    raise ValueError("--with-ollama exige --model avec un modèle local existant.")
                llm_evaluator = OllamaCandidateEvaluator(
                    args.model,
                    timeout=args.timeout,
                    num_predict=args.num_predict,
                    total_timeout=args.total_timeout,
                    total_num_predict=args.total_num_predict,
                    diagnostic_dir=args.diagnostic_dir,
                    num_ctx=args.num_ctx,
                )
            else:
                llm_evaluator = _UnavailableCandidateEvaluator()
            result = compare_evaluators(snapshot, DeterministicEvaluator(), llm_evaluator)
            result["snapshot_path"] = str(snapshot_path)
            Path(args.json_path).parent.mkdir(parents=True, exist_ok=True)
            Path(args.json_path).write_text(
                json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8"
            )
        except (OSError, json.JSONDecodeError, ValueError, RuntimeError) as exc:
            print(f"Comparaison impossible: {exc}")
            return 1
        print("COMPARAISON D'ÉVALUATEURS | lecture seule — aucun ordre")
        print(f"Snapshot: {snapshot['snapshot_id']} | SHA-256: {snapshot['content_sha256']}")
        for row in result["comparison"]:
            left = (
                row["deterministic"].get("classification") if row["deterministic"] else None
            ) or "non évalué"
            right = (row["ollama"].get("classification") if row["ollama"] else None) or "non évalué"
            outcome = (
                "accord"
                if row["agreement"]
                else "divergence"
                if row["status"] == "comparable"
                else f"non comparable ({row['reason']})"
            )
            print(f"{row['market']}: règles={left} | Ollama={right} | {outcome}")
        print(
            f"Accords: {result['summary']['agreements']} | divergences: {result['summary']['divergences']} | non comparables: {result['summary']['non_comparable']}"
        )
        print(f"JSON: {args.json_path}")
        return 0

    if args.command == "report":
        report = _generate_report(args, orchestrator_factory)
        if report.mock:
            print("Mode: mock")
        _print_generated_report(report)
        return 0

    if args.command == "schedule":
        return _run_schedule(
            args,
            parser,
            orchestrator_factory=orchestrator_factory,
            sleep_func=sleep_func,
            now_provider=now_provider,
        )

    if args.command == "dashboard":
        dashboard_runner(
            reports_dir=args.reports_dir,
            host=args.host,
            port=args.port,
            **({"state": args.state} if args.state is not None else {}),
        )
        return 0

    parser.error(f"Unknown command: {args.command}")
    return 2


def _add_report_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--coins",
        nargs="+",
        default=None,
        help="CoinGecko coin IDs to analyze, for example: bitcoin ethereum solana.",
    )
    parser.add_argument(
        "--currency",
        default=None,
        help="Quote currency for market data, default: usd.",
    )
    parser.add_argument(
        "--news-query",
        default=None,
        help='Optional NewsAPI query, for example: "crypto OR bitcoin OR ethereum".',
    )
    parser.add_argument(
        "--news-language",
        default=None,
        help="News language, default: en.",
    )
    parser.add_argument(
        "--protocols",
        nargs="+",
        default=None,
        help="DefiLlama protocol slugs, for example: uniswap aave lido.",
    )
    parser.add_argument(
        "--output-dir",
        default=None,
        help="Directory where Markdown/JSON/HTML reports are saved.",
    )
    parser.add_argument(
        "--no-whatsapp",
        action="store_true",
        help="Do not send WhatsApp notifications even if enabled in .env.",
    )
    parser.add_argument(
        "--whatsapp-preview",
        action="store_true",
        help="Display WhatsApp messages without sending them.",
    )
    parser.add_argument(
        "--mock",
        action="store_true",
        help="Run a full demo with fictitious data and no external API calls.",
    )
    parser.add_argument(
        "--mock-risk-level",
        choices=[level.value for level in RiskLevel],
        default=RiskLevel.MEDIUM.value,
        help="Risk scenario for --mock, default: medium.",
    )
    parser.add_argument(
        "--env-file",
        default=None,
        help="Optional dotenv file path, default: .env.",
    )


def _generate_report(
    args: argparse.Namespace,
    orchestrator_factory: Callable[..., CryptoMarketOrchestrator],
) -> GeneratedReport:
    if args.mock:
        return _generate_mock_report(args)

    return _generate_real_report(args, orchestrator_factory)


def _generate_real_report(
    args: argparse.Namespace,
    orchestrator_factory: Callable[..., CryptoMarketOrchestrator],
) -> GeneratedReport:
    orchestrator = orchestrator_factory(env_file=args.env_file)
    final_report = orchestrator.run_full_analysis(
        coin_ids=args.coins,
        vs_currency=args.currency,
        news_query=args.news_query,
        news_language=args.news_language,
        protocol_slugs=args.protocols,
        output_dir=args.output_dir,
        notify_whatsapp=not args.no_whatsapp and not args.whatsapp_preview,
    )

    run = orchestrator.last_run
    if run is None:
        raise RuntimeError("orchestrator did not expose run metadata.")

    summary_preview, alert_preview = _build_whatsapp_previews(
        final_report,
        run.markdown_path,
        run.json_path,
        run.html_path,
        max_message_chars=_orchestrator_whatsapp_max_chars(orchestrator),
    )

    return GeneratedReport(
        final_report=final_report,
        markdown_path=run.markdown_path,
        json_path=run.json_path,
        html_path=run.html_path,
        mock=False,
        whatsapp_summary=run.whatsapp_summary,
        whatsapp_alert=run.whatsapp_alert,
        whatsapp_preview=args.whatsapp_preview,
        whatsapp_summary_preview=summary_preview if args.whatsapp_preview else None,
        whatsapp_alert_preview=alert_preview if args.whatsapp_preview else None,
    )


def _generate_mock_report(args: argparse.Namespace) -> GeneratedReport:
    """Generate a complete report from official fictitious mock data."""

    mock_risk_level = RiskLevel(args.mock_risk_level)
    agent_reports = build_mock_agent_reports(mock_risk_level)
    final_report = FinalSynthesisAgent().synthesize(agent_reports)
    markdown_path, json_path, html_path = _save_mock_reports(
        final_report, args.output_dir or "reports"
    )

    summary_preview, alert_preview = _build_whatsapp_previews(
        final_report,
        markdown_path,
        json_path,
        html_path,
        max_message_chars=DEFAULT_MAX_MESSAGE_CHARS,
    )

    return GeneratedReport(
        final_report=final_report,
        markdown_path=markdown_path,
        json_path=json_path,
        html_path=html_path,
        mock=True,
        whatsapp_preview=args.whatsapp_preview,
        whatsapp_summary_preview=summary_preview if args.whatsapp_preview else None,
        whatsapp_alert_preview=alert_preview if args.whatsapp_preview else None,
    )


def _run_schedule(
    args: argparse.Namespace,
    parser: argparse.ArgumentParser,
    *,
    orchestrator_factory: Callable[..., CryptoMarketOrchestrator],
    sleep_func: Callable[[float], None],
    now_provider: Callable[[], datetime],
) -> int:
    if args.interval_minutes < 1:
        parser.error("--interval-minutes must be at least 1.")
    if args.runs is not None and args.runs < 1:
        parser.error("--runs must be greater than or equal to 1.")

    interval_seconds = args.interval_minutes * 60
    print("Scheduler demarre.")
    print(f"Mode: {'mock' if args.mock else 'reel'}")
    print(f"Intervalle: {args.interval_minutes} minute(s)")
    if args.runs is None:
        print("Runs prevus: continu jusqu'a Ctrl+C")
    else:
        print(f"Runs prevus: {args.runs}")

    run_number = 0
    had_error = False
    try:
        while args.runs is None or run_number < args.runs:
            run_number += 1
            started_at = now_provider()
            print(f"Run {run_number} demarre a {started_at.isoformat(timespec='seconds')}")
            try:
                report = _generate_report(args, orchestrator_factory)
                _print_generated_report(report)
            except (OSError, ValueError, RuntimeError) as exc:
                had_error = True
                print(f"Run {run_number} echoue: {redact_text(str(exc))}")

            if args.runs is not None and run_number >= args.runs:
                break

            next_run = now_provider() + timedelta(seconds=interval_seconds)
            print(f"Prochain run prevu: {next_run.isoformat(timespec='seconds')}")
            sleep_func(interval_seconds)
    except KeyboardInterrupt:
        print("Scheduler interrompu proprement.")
        return 0

    print("Scheduler termine.")
    return 1 if had_error else 0


def _print_generated_report(report: GeneratedReport) -> None:
    print(f"Rapport Markdown: {report.markdown_path}")
    print(f"Rapport JSON: {report.json_path}")
    print(f"Rapport HTML: {report.html_path}")
    print(f"Risque global: {report.final_report.global_risk_level.value}")
    print(f"Confidence globale: {report.final_report.confidence:.2f}")
    if report.mock:
        print("Aucune API externe appelee.")
        print("WhatsApp: disabled (mock mode)")
        _print_whatsapp_preview(report)
        return

    print(f"WhatsApp summary: {_notification_status(report.whatsapp_summary or {})}")
    print(f"WhatsApp alert: {_notification_status(report.whatsapp_alert or {})}")
    _print_whatsapp_preview(report)


def _save_mock_reports(
    final_report: FinalReport,
    output_dir: str | Path,
) -> tuple[Path, Path, Path]:
    target_dir = Path(output_dir)
    target_dir.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now().strftime("%Y-%m-%d_%H%M")
    with reserve_report_paths(target_dir, f"mock_report_{timestamp}") as paths:
        markdown_path, json_path, html_path = paths
        save_markdown_report(final_report, str(markdown_path))
        save_json_report(final_report, str(json_path))
        save_html_report(final_report, str(html_path))

    return markdown_path, json_path, html_path


def _notification_status(result: dict[str, Any]) -> str:
    status = str(result.get("status", "unknown"))
    if result.get("error"):
        return f"{status} ({result['error']})"

    return status


def _build_whatsapp_previews(
    final_report: FinalReport,
    markdown_path: Path,
    json_path: Path,
    html_path: Path,
    *,
    max_message_chars: int,
) -> tuple[str, str]:
    report_paths = ReportPaths(
        markdown_path=markdown_path,
        json_path=json_path,
        html_path=html_path,
    )
    summary = build_final_report_summary_message(
        final_report,
        report_paths,
        max_message_chars=max_message_chars,
    )
    alert = (
        build_high_risk_alert_message(
            final_report,
            report_paths,
            max_message_chars=max_message_chars,
        )
        if final_report.global_risk_level in {RiskLevel.HIGH, RiskLevel.CRITICAL}
        else (
            "Aucune alerte WhatsApp high/critical: risque global "
            f"{final_report.global_risk_level.value}."
        )
    )
    return summary, alert


def _orchestrator_whatsapp_max_chars(orchestrator: CryptoMarketOrchestrator) -> int:
    config = getattr(orchestrator, "config", None)
    whatsapp = getattr(config, "whatsapp", None)
    return int(getattr(whatsapp, "max_message_chars", DEFAULT_MAX_MESSAGE_CHARS))


def _print_whatsapp_preview(report: GeneratedReport) -> None:
    if not report.whatsapp_preview:
        return

    print("WhatsApp preview: enabled")
    print("WhatsApp summary preview:")
    print(report.whatsapp_summary_preview or "")
    print("WhatsApp alert preview:")
    print(report.whatsapp_alert_preview or "")


if __name__ == "__main__":
    raise SystemExit(main())
