"""Command line interface for Crypto Market Agents."""

from __future__ import annotations

import argparse
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
import time
from typing import Any

from crypto_market_agents.agents.final_synthesis_agent import FinalSynthesisAgent
from crypto_market_agents.mock_data import build_mock_agent_reports
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

    return parser


def main(
    argv: Sequence[str] | None = None,
    *,
    orchestrator_factory: Callable[..., CryptoMarketOrchestrator] = CryptoMarketOrchestrator,
    sleep_func: Callable[[float], None] = time.sleep,
    now_provider: Callable[[], datetime] = datetime.now,
) -> int:
    """Run the command line interface."""

    parser = build_parser()
    args = parser.parse_args(argv)

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
        default="usd",
        help="Quote currency for market data, default: usd.",
    )
    parser.add_argument(
        "--news-query",
        default=None,
        help='Optional NewsAPI query, for example: "crypto OR bitcoin OR ethereum".',
    )
    parser.add_argument(
        "--news-language",
        default="en",
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
        default="reports",
        help="Directory where Markdown/JSON/HTML reports are saved.",
    )
    parser.add_argument(
        "--no-whatsapp",
        action="store_true",
        help="Do not send WhatsApp notifications even if enabled in .env.",
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
        notify_whatsapp=not args.no_whatsapp,
    )

    run = orchestrator.last_run
    if run is None:
        raise RuntimeError("orchestrator did not expose run metadata.")

    return GeneratedReport(
        final_report=final_report,
        markdown_path=run.markdown_path,
        json_path=run.json_path,
        html_path=run.html_path,
        mock=False,
        whatsapp_summary=run.whatsapp_summary,
        whatsapp_alert=run.whatsapp_alert,
    )


def _generate_mock_report(args: argparse.Namespace) -> GeneratedReport:
    """Generate a complete report from official fictitious mock data."""

    mock_risk_level = RiskLevel(args.mock_risk_level)
    agent_reports = build_mock_agent_reports(mock_risk_level)
    final_report = FinalSynthesisAgent().synthesize(agent_reports)
    markdown_path, json_path, html_path = _save_mock_reports(final_report, args.output_dir)

    return GeneratedReport(
        final_report=final_report,
        markdown_path=markdown_path,
        json_path=json_path,
        html_path=html_path,
        mock=True,
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
    try:
        while args.runs is None or run_number < args.runs:
            run_number += 1
            started_at = now_provider()
            print(f"Run {run_number} demarre a {started_at.isoformat(timespec='seconds')}")
            report = _generate_report(args, orchestrator_factory)
            _print_generated_report(report)

            if args.runs is not None and run_number >= args.runs:
                break

            next_run = now_provider() + timedelta(seconds=interval_seconds)
            print(f"Prochain run prevu: {next_run.isoformat(timespec='seconds')}")
            sleep_func(interval_seconds)
    except KeyboardInterrupt:
        print("Scheduler interrompu proprement.")
        return 0

    print("Scheduler termine.")
    return 0


def _print_generated_report(report: GeneratedReport) -> None:
    print(f"Rapport Markdown: {report.markdown_path}")
    print(f"Rapport JSON: {report.json_path}")
    print(f"Rapport HTML: {report.html_path}")
    print(f"Risque global: {report.final_report.global_risk_level.value}")
    print(f"Confidence globale: {report.final_report.confidence:.2f}")
    if report.mock:
        print("Aucune API externe appelee.")
        print("WhatsApp: disabled (mock mode)")
        return

    print(f"WhatsApp summary: {_notification_status(report.whatsapp_summary or {})}")
    print(f"WhatsApp alert: {_notification_status(report.whatsapp_alert or {})}")


def _save_mock_reports(
    final_report: FinalReport,
    output_dir: str | Path,
) -> tuple[Path, Path, Path]:
    target_dir = Path(output_dir)
    target_dir.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now().strftime("%Y-%m-%d_%H%M")
    markdown_path, json_path, html_path = _unique_report_paths(
        target_dir,
        f"mock_report_{timestamp}",
    )

    save_markdown_report(final_report, str(markdown_path))
    save_json_report(final_report, str(json_path))
    save_html_report(final_report, str(html_path))

    return markdown_path, json_path, html_path


def _unique_report_paths(target_dir: Path, base_name: str) -> tuple[Path, Path, Path]:
    suffix = ""
    index = 2
    while True:
        markdown_path = target_dir / f"{base_name}{suffix}.md"
        json_path = target_dir / f"{base_name}{suffix}.json"
        html_path = target_dir / f"{base_name}{suffix}.html"
        if not markdown_path.exists() and not json_path.exists() and not html_path.exists():
            return markdown_path, json_path, html_path
        suffix = f"_{index}"
        index += 1


def _notification_status(result: dict[str, Any]) -> str:
    status = str(result.get("status", "unknown"))
    if result.get("error"):
        return f"{status} ({result['error']})"

    return status


if __name__ == "__main__":
    raise SystemExit(main())
