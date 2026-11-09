from pathlib import Path
import sys
import unittest

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from crypto_market_agents.clients.bitvavo_asset_client import BitvavoAssetClient, BitvavoAssetError
from crypto_market_agents.http_utils import HTTPClientSettings, InMemoryTTLCache


class Response:
    def __init__(self, body, status=200):
        self.body = body
        self.status = status

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return None

    def read(self):
        return self.body


class BitvavoAssetClientTests(unittest.TestCase):
    def test_get_assets_is_public_get_and_cached(self):
        calls = []

        def opener(request, timeout):
            calls.append(request)
            return Response(b'[{"symbol":"BTC","name":"Bitcoin","networks":["Mainnet"]}]')

        client = BitvavoAssetClient(
            opener=opener,
            cache=InMemoryTTLCache(),
            http_settings=HTTPClientSettings(max_retries=0, cache_ttl_seconds=30),
        )
        self.assertEqual(client.get_assets()[0]["name"], "Bitcoin")
        client.get_assets()
        self.assertEqual(len(calls), 1)
        self.assertEqual(calls[0].get_method(), "GET")
        self.assertTrue(calls[0].full_url.endswith("/assets"))

    def test_invalid_response_and_http_error_are_reported(self):
        client = BitvavoAssetClient(
            opener=lambda req, timeout: Response(b"{}"),
            http_settings=HTTPClientSettings(max_retries=0, cache_enabled=False),
        )
        with self.assertRaises(BitvavoAssetError):
            client.get_assets()

        client = BitvavoAssetClient(
            opener=lambda request, timeout: Response(b"{}", status=403),
            http_settings=HTTPClientSettings(max_retries=0, cache_enabled=False),
        )
        with self.assertRaises(BitvavoAssetError):
            client.get_assets()

    def test_validates_url_and_timeout(self):
        with self.assertRaises(ValueError):
            BitvavoAssetClient(base_url="bad")
        with self.assertRaises(ValueError):
            BitvavoAssetClient(timeout_seconds=0)


if __name__ == "__main__":
    unittest.main()
