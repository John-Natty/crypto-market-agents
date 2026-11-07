"""Offline end-to-end smoke check for the installed paper CLI."""

from pathlib import Path
import sys
import tempfile

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from crypto_market_agents.cli import main
from crypto_market_agents.trading.store import PaperStore


def run():
    with tempfile.TemporaryDirectory() as tmp:
        state = str(Path(tmp) / "portfolio.json")
        reports = str(Path(tmp) / "reports")
        assert main(["paper", "init", "--state", state]) == 0
        assert (
            main(
                [
                    "paper",
                    "run",
                    "--state",
                    state,
                    "--mock",
                    "--runs",
                    "3",
                    "--interval-seconds",
                    "0",
                    "--output-dir",
                    reports,
                ]
            )
            == 0
        )
        portfolio = PaperStore(state).load()
        assert [event["action"] for event in portfolio.ledger] == ["hold", "buy", "sell"]
        assert sum(event["status"] == "executed" for event in portfolio.ledger) == 2
        assert len(list(Path(reports).glob("*.html"))) == 1
        assert portfolio.fees_paid > 0
    print("Paper smoke OK: observations et ordres fictifs, aucun compte utilise.")


if __name__ == "__main__":
    run()
