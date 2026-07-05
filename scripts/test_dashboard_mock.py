"""Local smoke test for the lightweight dashboard with mock reports."""

from pathlib import Path
import sys
import tempfile


PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from crypto_market_agents.cli import main as cli_main
from crypto_market_agents.dashboard import (
    discover_reports,
    render_dashboard_home,
    render_report_detail,
)


def main_dashboard_mock() -> None:
    output_dir = Path(tempfile.mkdtemp(prefix="crypto-market-dashboard-mock-"))
    exit_code = cli_main(
        [
            "report",
            "--mock",
            "--output-dir",
            str(output_dir),
        ]
    )
    if exit_code != 0:
        raise SystemExit(exit_code)

    reports = discover_reports(output_dir)
    if not reports:
        raise SystemExit("dashboard did not discover the generated mock report")

    home_html = render_dashboard_home(output_dir)
    if "Crypto Market Agents Dashboard" not in home_html:
        raise SystemExit("dashboard home page was not rendered")

    json_file = reports[0].json_file
    if not json_file:
        raise SystemExit("generated mock report has no JSON file")

    detail_html = render_report_detail(output_dir, json_file)
    if "Résumé du marché" not in detail_html:
        raise SystemExit("dashboard detail page was not rendered")

    print(f"Dashboard mock OK pour: {output_dir}")
    print("Serveur non demarre pendant ce smoke test.")


if __name__ == "__main__":
    main_dashboard_mock()
