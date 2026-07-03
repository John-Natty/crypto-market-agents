#!/usr/bin/env python3
"""Quick live check for the read-only CoinGecko client."""

from __future__ import annotations

import json
import sys
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from crypto_market_agents.clients.coingecko_client import (  # noqa: E402
    CoinGeckoClient,
    CoinGeckoError,
)
from crypto_market_agents.config import load_config  # noqa: E402


def main() -> int:
    """Run a simple live API check against CoinGecko."""

    config = load_config(PROJECT_ROOT / ".env")
    client = CoinGeckoClient.from_config(config.coingecko, config.http)

    try:
        ping = client.ping()
        simple_prices = client.get_simple_prices(
            ("bitcoin", "ethereum"),
            vs_currencies="usd",
        )
        markets = client.get_coin_markets(
            ("bitcoin", "ethereum"),
            vs_currency="usd",
        )
    except CoinGeckoError as exc:
        print(f"CoinGecko check failed: {exc}", file=sys.stderr)
        return 1

    print("Ping:")
    print(json.dumps(ping, indent=2, ensure_ascii=False))
    print("\nSimple prices:")
    print(json.dumps(simple_prices, indent=2, ensure_ascii=False))
    print("\nMarkets:")
    print(json.dumps(markets, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
