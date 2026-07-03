from io import BytesIO
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import urlparse
import sys
import unittest


PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from crypto_market_agents.clients.defillama_client import (
    DefiLlamaAPIError,
    DefiLlamaClient,
    DefiLlamaResponseError,
    DefiLlamaTimeoutError,
)
from crypto_market_agents.http_utils import HTTPClientSettings, InMemoryTTLCache


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


class DefiLlamaClientTests(unittest.TestCase):
    def test_client_returns_expected_data_from_normal_responses(self):
        calls = []
        responses = {
            "/protocols": b'[{"name":"Aave","slug":"aave","tvl":1000}]',
            "/protocol/aave": (
                b'{"name":"Aave","slug":"aave","category":"Lending",'
                b'"chains":["Ethereum"],"tvl":[{"totalLiquidityUSD":1000}]}'
            ),
            "/tvl/aave": b"1000",
            "/v2/chains": b'[{"name":"Ethereum","tvl":1000000}]',
            "/stablecoins": b'{"peggedAssets":[],"totalCirculating":{"peggedUSD":10}}',
            "/overview/fees": b'{"protocols":[{"name":"Aave","total24h":50}]}',
        }
        client = DefiLlamaClient(opener=self.make_router(responses, calls))

        self.assertEqual(client.get_protocols()[0]["slug"], "aave")
        self.assertEqual(client.get_protocol("aave")["category"], "Lending")
        self.assertEqual(client.get_current_tvl("aave"), 1000)
        self.assertEqual(client.get_chains()[0]["name"], "Ethereum")
        self.assertEqual(client.get_stablecoins()["totalCirculating"]["peggedUSD"], 10)
        self.assertEqual(client.get_fees_overview()["protocols"][0]["total24h"], 50)
        self.assertEqual(calls[0], "/protocols")

    def test_non_200_response_raises_api_error(self):
        client = DefiLlamaClient(opener=self.make_opener(b'{"error":"not found"}', status=404))

        with self.assertRaises(DefiLlamaAPIError) as context:
            client.get_protocol("missing")

        self.assertEqual(context.exception.status_code, 404)

    def test_http_error_raises_api_error(self):
        def opener(request, timeout):
            raise HTTPError(
                request.full_url,
                500,
                "Server Error",
                {},
                BytesIO(b'{"error":"server"}'),
            )

        client = DefiLlamaClient(opener=opener, http_settings=self.no_retry_settings())

        with self.assertRaises(DefiLlamaAPIError) as context:
            client.get_protocols()

        self.assertEqual(context.exception.status_code, 500)

    def test_invalid_json_raises_response_error(self):
        client = DefiLlamaClient(
            opener=self.make_opener(b"not json"),
            http_settings=self.no_retry_settings(),
        )

        with self.assertRaises(DefiLlamaResponseError):
            client.get_protocols()

    def test_timeout_raises_timeout_error(self):
        def opener(request, timeout):
            raise URLError(TimeoutError("timed out"))

        client = DefiLlamaClient(opener=opener, http_settings=self.no_retry_settings())

        with self.assertRaises(DefiLlamaTimeoutError):
            client.get_protocols()

    def test_retries_temporary_status_then_succeeds(self):
        calls = []

        def opener(request, timeout):
            calls.append(request.full_url)
            if len(calls) == 1:
                return FakeResponse(b'{"error":"rate limit"}', status=429)
            return FakeResponse(b'[{"name":"Aave","slug":"aave","tvl":1000}]', status=200)

        client = DefiLlamaClient(
            opener=opener,
            http_settings=HTTPClientSettings(max_retries=1, backoff_seconds=0),
            sleep=lambda seconds: None,
        )

        protocols = client.get_protocols()

        self.assertEqual(protocols[0]["slug"], "aave")
        self.assertEqual(len(calls), 2)

    def test_cache_hit_reuses_protocols_response(self):
        calls = []
        client = DefiLlamaClient(
            opener=self.make_opener(b'[{"name":"Aave","slug":"aave","tvl":1000}]', calls),
            http_settings=HTTPClientSettings(
                max_retries=0,
                backoff_seconds=0,
                cache_ttl_seconds=60,
                cache_enabled=True,
            ),
            cache=InMemoryTTLCache(),
        )

        client.get_protocols()
        client.get_protocols()

        self.assertEqual(len(calls), 1)

    def test_invalid_base_url_raises_value_error(self):
        with self.assertRaises(ValueError):
            DefiLlamaClient(base_url="not-a-url")

    @staticmethod
    def make_opener(payload: bytes, calls=None, *, status: int = 200):
        def opener(request, timeout):
            if calls is not None:
                calls.append(request)
            return FakeResponse(payload, status=status)

        return opener

    @staticmethod
    def make_router(responses, calls):
        def opener(request, timeout):
            path = urlparse(request.full_url).path
            calls.append(path)
            return FakeResponse(responses[path])

        return opener

    @staticmethod
    def no_retry_settings():
        return HTTPClientSettings(max_retries=0, backoff_seconds=0, cache_enabled=False)


if __name__ == "__main__":
    unittest.main()
