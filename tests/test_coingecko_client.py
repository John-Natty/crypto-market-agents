from io import BytesIO
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qs, urlparse
import sys
import unittest


PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from crypto_market_agents.clients.coingecko_client import (
    CoinGeckoAPIError,
    CoinGeckoClient,
    CoinGeckoTimeoutError,
)


class FakeResponse:
    def __init__(self, payload: bytes, *, status: int = 200) -> None:
        self.payload = payload
        self.status = status

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_value, traceback):
        return None

    def read(self):
        return self.payload


class CoinGeckoClientTests(unittest.TestCase):
    def test_ping_returns_dict(self):
        client = CoinGeckoClient(opener=self.make_opener(b'{"gecko_says":"ok"}'))

        payload = client.ping()

        self.assertEqual(payload, {"gecko_says": "ok"})

    def test_simple_prices_uses_demo_header_and_query_params(self):
        calls = []
        client = CoinGeckoClient(
            api_key="demo-key",
            opener=self.make_opener(b'{"bitcoin":{"usd":100}}', calls),
        )

        payload = client.get_simple_prices(("bitcoin", "ethereum"), vs_currencies="usd")

        request = calls[0]
        query = parse_qs(urlparse(request.full_url).query)
        headers = {key.lower(): value for key, value in request.header_items()}
        self.assertEqual(payload["bitcoin"]["usd"], 100)
        self.assertEqual(query["ids"], ["bitcoin,ethereum"])
        self.assertEqual(query["vs_currencies"], ["usd"])
        self.assertEqual(headers["x-cg-demo-api-key"], "demo-key")

    def test_pro_base_url_uses_pro_header(self):
        calls = []
        client = CoinGeckoClient(
            base_url="https://pro-api.coingecko.com/api/v3",
            api_key="pro-key",
            opener=self.make_opener(b'{"gecko_says":"ok"}', calls),
        )

        client.ping()

        headers = {key.lower(): value for key, value in calls[0].header_items()}
        self.assertEqual(headers["x-cg-pro-api-key"], "pro-key")

    def test_coin_markets_returns_list(self):
        calls = []
        body = (
            b'[{"id":"bitcoin","symbol":"btc","current_price":100},'
            b'{"id":"ethereum","symbol":"eth","current_price":10}]'
        )
        client = CoinGeckoClient(opener=self.make_opener(body, calls))

        payload = client.get_coin_markets(("bitcoin", "ethereum"), vs_currency="usd")

        query = parse_qs(urlparse(calls[0].full_url).query)
        self.assertEqual(len(payload), 2)
        self.assertEqual(query["ids"], ["bitcoin,ethereum"])
        self.assertEqual(query["price_change_percentage"], ["1h,24h,7d"])

    def test_non_200_response_raises_api_error(self):
        client = CoinGeckoClient(opener=self.make_opener(b'{"error":"rate limit"}', status=429))

        with self.assertRaises(CoinGeckoAPIError) as context:
            client.ping()

        self.assertEqual(context.exception.status_code, 429)

    def test_http_error_raises_api_error(self):
        def opener(request, timeout):
            raise HTTPError(
                request.full_url,
                401,
                "Unauthorized",
                {},
                BytesIO(b'{"error":"invalid key"}'),
            )

        client = CoinGeckoClient(opener=opener)

        with self.assertRaises(CoinGeckoAPIError) as context:
            client.ping()

        self.assertEqual(context.exception.status_code, 401)

    def test_timeout_raises_timeout_error(self):
        def opener(request, timeout):
            raise URLError(TimeoutError("timed out"))

        client = CoinGeckoClient(opener=opener)

        with self.assertRaises(CoinGeckoTimeoutError):
            client.ping()

    def test_invalid_base_url_raises_value_error(self):
        with self.assertRaises(ValueError):
            CoinGeckoClient(base_url="not-a-url")

    @staticmethod
    def make_opener(payload: bytes, calls=None, *, status: int = 200):
        def opener(request, timeout):
            if calls is not None:
                calls.append(request)
            return FakeResponse(payload, status=status)

        return opener


if __name__ == "__main__":
    unittest.main()
