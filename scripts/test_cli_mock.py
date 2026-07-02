"""Local smoke test for the official CLI mock mode."""

from pathlib import Path
import sys
import tempfile


PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from crypto_market_agents.cli import main


def main_cli_mock() -> None:
    output_dir = Path(tempfile.mkdtemp(prefix="crypto-market-cli-mock-"))
    exit_code = main(
        [
            "report",
            "--mock",
            "--mock-risk-level",
            "high",
            "--output-dir",
            str(output_dir),
        ]
    )

    if exit_code != 0:
        raise SystemExit(exit_code)

    print(f"Rapports mock sauvegardes dans: {output_dir}")


if __name__ == "__main__":
    main_cli_mock()
