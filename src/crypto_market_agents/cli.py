"""Command line interface for Crypto Market Agents."""

from __future__ import annotations

import argparse
from collections.abc import Callable, Sequence
from typing import Any

from crypto_market_agents.orchestrator import CryptoMarketOrchestrator


def build_parser() -> argparse.ArgumentParser:
    """Build the CLI argument parser."""

    parser = argparse.ArgumentParser(
        prog="crypto-market-agents",
        description="Generate crypto market analysis reports.",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    report_parser = subparsers.add_parser(
        "report",
        help="Run the full analysis flow and save Markdown/JSON reports.",
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
        help="Directory where Markdown/JSON reports are saved.",
    )
    report_parser.add_argument(
        "--no-whatsapp",
        action="store_true",
        help="Do not send WhatsApp notifications even if enabled in .env.",
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
        print(f"Risque global: {final_report.global_risk_level.value}")
        print(f"Confidence globale: {final_report.confidence:.2f}")
        print(f"WhatsApp summary: {_notification_status(run.whatsapp_summary)}")
        print(f"WhatsApp alert: {_notification_status(run.whatsapp_alert)}")
        return 0

    parser.error(f"Unknown command: {args.command}")
    return 2


def _notification_status(result: dict[str, Any]) -> str:
    status = str(result.get("status", "unknown"))
    if result.get("error"):
        return f"{status} ({result['error']})"

    return status


if __name__ == "__main__":
    raise SystemExit(main())
