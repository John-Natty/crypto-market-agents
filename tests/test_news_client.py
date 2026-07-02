from io import BytesIO
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qs, urlparse
import sys
import unittest


PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from crypto_market_agents.clients.news_client import (
    NewsAPIHTTPError,
    NewsAPIKeyMissingError,
    NewsClient,
    NewsResponseError,
    NewsTimeoutError,
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


class NewsClientTests(unittest.TestCase):
    def test_search_articles_returns_clean_articles(self):
        calls = []
        body = (
            b'{"status":"ok","totalResults":1,"articles":[{'
            b'"source":{"id":"test","name":"Test News"},'
            b'"author":"Reporter",'
            b'"title":"Bitcoin adoption grows",'
            b'"description":"Institutional adoption keeps growing.",'
            b'"url":"https://example.com/article",'
            b'"publishedAt":"2026-07-01T09:00:00Z",'
            b'"content":"Bitcoin adoption grows quickly."'
            b"}]}"
        )
        client = NewsClient(
            api_key="news-key",
            opener=self.make_opener(body, calls),
        )

        articles = client.search_articles("bitcoin", language="en", page_size=5)

        request = calls[0]
        query = parse_qs(urlparse(request.full_url).query)
        headers = {key.lower(): value for key, value in request.header_items()}
        self.assertEqual(query["q"], ["bitcoin"])
        self.assertEqual(query["language"], ["en"])
        self.assertEqual(query["pageSize"], ["5"])
        self.assertEqual(headers["x-api-key"], "news-key")
        self.assertEqual(len(articles), 1)
        self.assertEqual(articles[0]["title"], "Bitcoin adoption grows")
        self.assertEqual(articles[0]["source"]["name"], "Test News")

    def test_search_articles_requires_api_key(self):
        client = NewsClient(api_key=None)

        with self.assertRaises(NewsAPIKeyMissingError):
            client.search_articles("bitcoin")

    def test_non_200_response_raises_http_error(self):
        client = NewsClient(
            api_key="news-key",
            opener=self.make_opener(b'{"status":"error"}', status=429),
        )

        with self.assertRaises(NewsAPIHTTPError) as context:
            client.search_articles("bitcoin")

        self.assertEqual(context.exception.status_code, 429)

    def test_http_error_raises_http_error(self):
        def opener(request, timeout):
            raise HTTPError(
                request.full_url,
                401,
                "Unauthorized",
                {},
                BytesIO(b'{"status":"error","message":"bad key"}'),
            )

        client = NewsClient(api_key="bad-key", opener=opener)

        with self.assertRaises(NewsAPIHTTPError) as context:
            client.search_articles("bitcoin")

        self.assertEqual(context.exception.status_code, 401)

    def test_invalid_json_raises_response_error(self):
        client = NewsClient(
            api_key="news-key",
            opener=self.make_opener(b"not json"),
        )

        with self.assertRaises(NewsResponseError):
            client.search_articles("bitcoin")

    def test_timeout_raises_timeout_error(self):
        def opener(request, timeout):
            raise URLError(TimeoutError("timed out"))

        client = NewsClient(api_key="news-key", opener=opener)

        with self.assertRaises(NewsTimeoutError):
            client.search_articles("bitcoin")

    def test_invalid_base_url_raises_value_error(self):
        with self.assertRaises(ValueError):
            NewsClient(base_url="not-a-url", api_key="news-key")

    @staticmethod
    def make_opener(payload: bytes, calls=None, *, status: int = 200):
        def opener(request, timeout):
            if calls is not None:
                calls.append(request)
            return FakeResponse(payload, status=status)

        return opener


if __name__ == "__main__":
    unittest.main()
