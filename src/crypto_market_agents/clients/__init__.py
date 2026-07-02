"""API clients used by Crypto Market Agents."""

from crypto_market_agents.clients.coingecko_client import (
    CoinGeckoAPIError,
    CoinGeckoClient,
    CoinGeckoError,
    CoinGeckoNetworkError,
    CoinGeckoResponseError,
    CoinGeckoTimeoutError,
)
from crypto_market_agents.clients.defillama_client import (
    DefiLlamaAPIError,
    DefiLlamaClient,
    DefiLlamaError,
    DefiLlamaNetworkError,
    DefiLlamaResponseError,
    DefiLlamaTimeoutError,
)
from crypto_market_agents.clients.news_client import (
    NewsAPIHTTPError,
    NewsAPIKeyMissingError,
    NewsAPIStatusError,
    NewsClient,
    NewsError,
    NewsNetworkError,
    NewsResponseError,
    NewsTimeoutError,
)

__all__ = [
    "CoinGeckoAPIError",
    "CoinGeckoClient",
    "CoinGeckoError",
    "CoinGeckoNetworkError",
    "CoinGeckoResponseError",
    "CoinGeckoTimeoutError",
    "DefiLlamaAPIError",
    "DefiLlamaClient",
    "DefiLlamaError",
    "DefiLlamaNetworkError",
    "DefiLlamaResponseError",
    "DefiLlamaTimeoutError",
    "NewsAPIHTTPError",
    "NewsAPIKeyMissingError",
    "NewsAPIStatusError",
    "NewsClient",
    "NewsError",
    "NewsNetworkError",
    "NewsResponseError",
    "NewsTimeoutError",
]
