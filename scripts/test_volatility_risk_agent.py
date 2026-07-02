#!/usr/bin/env python3
"""Quick live check for the VolatilityRiskAgent."""

from __future__ import annotations

import json
import sys
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from crypto_market_agents.agents.volatility_risk_agent import (  # noqa: E402
    VolatilityRiskAgent,
)
from crypto_market_agents.clients.coingecko_client import CoinGeckoClient  # noqa: E402
from crypto_market_agents.config import load_config  # noqa: E402


def main() -> int:
    """Run a simple live volatility analysis."""

    config = load_config(PROJECT_ROOT / ".env")
    client = CoinGeckoClient.from_config(config.coingecko)
    report = VolatilityRiskAgent(client).analyze(
        list(config.watchlist),
        vs_currency=config.base_currency,
    )

    print(json.dumps(report.to_dict(), indent=2, ensure_ascii=False))
    return 0 if report.status.value != "failed" else 1


if __name__ == "__main__":
    raise SystemExit(main())

