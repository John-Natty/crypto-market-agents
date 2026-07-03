"""Command line interface for Crypto Market Agents."""

from __future__ import annotations

import argparse
from collections.abc import Callable, Sequence
from datetime import datetime
from pathlib import Path
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
    report_parser.add_argument(
        "--coins",
        nargs="+",
        default=None,
        help="CoinGecko coin IDs to analyze, for example: bitcoin ethereum solana.",
    )
    report_parser.add_argument(
        "--currency",
        default="usd",
        help="Quote currency for market data, default: usd.",
    )
    report_parser.add_argument(
        "--news-query",
        default=None,
        help='Optional NewsAPI query, for example: "crypto OR bitcoin OR ethereum".',
    )
    report_parser.add_argument(
        "--news-language",
        default="en",
        help="News language, default: en.",
    )
    report_parser.add_argument(
        "--protocols",
        nargs="+",
        default=None,
        help="DefiLlama protocol slugs, for example: uniswap aave lido.",
    )
    report_parser.add_argument(
        "--output-dir",
        default="reports",
        help="Directory where Markdown/JSON/HTML reports are saved.",
    )
    report_parser.add_argument(
        "--no-whatsapp",
        action="store_true",
        help="Do not send WhatsApp notifications even if enabled in .env.",
    )
    report_parser.add_argument(
        "--mock",
        action="store_true",
        help="Run a full demo with fictitious data and no external API calls.",
    )
    report_parser.add_argument(
        "--mock-risk-level",
        choices=[level.value for level in RiskLevel],
        default=RiskLevel.MEDIUM.value,
        help="Risk scenario for --mock, default: medium.",
    )
    report_parser.add_argument(
        "--env-file",
        default=None,
        help="Optional dotenv file path, default: .env.",
    )

    return parser


def main(
    argv: Sequence[str] | None = None,
    *,
    orchestrator_factory: Callable[..., CryptoMarketOrchestrator] = CryptoMarketOrchestrator,
) -> int:
    """Run the command line interface."""

    parser = build_parser()
    args = parser.parse_args(argv)

    if args.command == "report":
        if args.mock:
            return _run_mock_report(args)

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
            parser.error("orchestrator did not expose run metadata.")

        print(f"Rapport Markdown: {run.markdown_path}")
        print(f"Rapport JSON: {run.json_path}")
        print(f"Rapport HTML: {run.html_path}")
        print(f"Risque global: {final_report.global_risk_level.value}")
        print(f"Confidence globale: {final_report.confidence:.2f}")
        print(f"WhatsApp summary: {_notification_status(run.whatsapp_summary)}")
        print(f"WhatsApp alert: {_notification_status(run.whatsapp_alert)}")
        return 0

    parser.error(f"Unknown command: {args.command}")
    return 2


def _run_mock_report(args: argparse.Namespace) -> int:
    """Generate a complete report from official fictitious mock data."""

    mock_risk_level = RiskLevel(args.mock_risk_level)
    agent_reports = build_mock_agent_reports(mock_risk_level)
    final_report = FinalSynthesisAgent().synthesize(agent_reports)
    markdown_path, json_path, html_path = _save_mock_reports(final_report, args.output_dir)

    print("Mode: mock")
    print(f"Rapport Markdown: {markdown_path}")
    print(f"Rapport JSON: {json_path}")
    print(f"Rapport HTML: {html_path}")
    print(f"Risque global: {final_report.global_risk_level.value}")
    print(f"Confidence globale: {final_report.confidence:.2f}")
    print("Aucune API externe appelee.")
    print("WhatsApp: disabled (mock mode)")
    return 0


def _save_mock_reports(
    final_report: FinalReport,
    output_dir: str | Path,
) -> tuple[Path, Path, Path]:
    target_dir = Path(output_dir)
    target_dir.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now().strftime("%Y-%m-%d_%H%M")
    markdown_path = target_dir / f"mock_report_{timestamp}.md"
    json_path = target_dir / f"mock_report_{timestamp}.json"
    html_path = target_dir / f"mock_report_{timestamp}.html"

    save_markdown_report(final_report, str(markdown_path))
    save_json_report(final_report, str(json_path))
    save_html_report(final_report, str(html_path))

    return markdown_path, json_path, html_path


def _notification_status(result: dict[str, Any]) -> str:
    status = str(result.get("status", "unknown"))
    if result.get("error"):
        return f"{status} ({result['error']})"

    return status


if __name__ == "__main__":
    raise SystemExit(main())
