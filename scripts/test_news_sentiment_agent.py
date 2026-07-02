#!/usr/bin/env python3
"""Quick live check for the NewsSentimentAgent."""

from __future__ import annotations

import json
import sys
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from crypto_market_agents.agents.news_sentiment_agent import (  # noqa: E402
    NewsSentimentAgent,
)
from crypto_market_agents.clients.news_client import NewsClient  # noqa: E402
from crypto_market_agents.config import load_config  # noqa: E402


def main() -> int:
    """Run a simple live news sentiment analysis."""

    config = load_config(PROJECT_ROOT / ".env")
    client = NewsClient.from_config(config.news)
    report = NewsSentimentAgent(client).analyze(
        list(config.watchlist),
        language=config.news.language,
    )

    print(json.dumps(report.to_dict(), indent=2, ensure_ascii=False))
    return 0 if report.status.value != "failed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
