from io import BytesIO, StringIO
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import Request
import logging
import sys
import unittest


PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from crypto_market_agents.http_utils import (
    HTTPClientSettings,
    HTTPResponse,
    InMemoryTTLCache,
    backoff_delay,
    send_request_with_retries,
    should_retry_http_status,
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


class HTTPUtilsTests(unittest.TestCase):
    def test_should_retry_only_temporary_status_codes(self):
        for status_code in (429, 500, 502, 503, 504):
            with self.subTest(status_code=status_code):
                self.assertTrue(should_retry_http_status(status_code))

        for status_code in (400, 401, 403, 404, 418):
            with self.subTest(status_code=status_code):
                self.assertFalse(should_retry_http_status(status_code))

    def test_backoff_delay_is_exponential(self):
        self.assertEqual(backoff_delay(0.5, 0), 0.5)
        self.assertEqual(backoff_delay(0.5, 1), 1.0)
        self.assertEqual(backoff_delay(0.5, 2), 2.0)

    def test_retries_timeout_then_success(self):
        calls = []

        def opener(request, timeout):
            calls.append(request.full_url)
            if len(calls) == 1:
                raise TimeoutError("timed out")
            return FakeResponse(b'{"ok":true}')

        response = self.send(opener, max_retries=1)

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.body, '{"ok":true}')
        self.assertEqual(len(calls), 2)

    def test_retries_network_error_then_success(self):
        calls = []

        def opener(request, timeout):
            calls.append(request.full_url)
            if len(calls) == 1:
                raise URLError("temporary network failure")
            return FakeResponse(b'{"ok":true}')

        response = self.send(opener, max_retries=1)

        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(calls), 2)

    def test_retries_http_error_429_then_success(self):
        calls = []

        def opener(request, timeout):
            calls.append(request.full_url)
            if len(calls) == 1:
                raise HTTPError(
                    request.full_url,
                    429,
                    "Too Many Requests",
                    {},
                    BytesIO(b'{"error":"rate limit"}'),
                )
            return FakeResponse(b'{"ok":true}')

        response = self.send(opener, max_retries=1)

        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(calls), 2)

    def test_retries_temporary_status_response_then_success(self):
        calls = []

        def opener(request, timeout):
            calls.append(request.full_url)
            if len(calls) == 1:
                return FakeResponse(b'{"error":"temporary"}', status=503)
            return FakeResponse(b'{"ok":true}', status=200)

        response = self.send(opener, max_retries=1)

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.body, '{"ok":true}')
        self.assertEqual(len(calls), 2)

    def test_does_not_retry_client_status_response(self):
        for status_code in (400, 401, 403, 404):
            with self.subTest(status_code=status_code):
                calls = []

                def opener(request, timeout):
                    calls.append(request.full_url)
                    return FakeResponse(b'{"error":"client"}', status=status_code)

                response = self.send(opener, max_retries=3)

                self.assertEqual(response.status_code, status_code)
                self.assertEqual(len(calls), 1)

    def test_sleeps_with_backoff_between_retries(self):
        calls = []
        sleeps = []

        def opener(request, timeout):
            calls.append(request.full_url)
            if len(calls) < 3:
                return FakeResponse(b'{"error":"temporary"}', status=500)
            return FakeResponse(b'{"ok":true}', status=200)

        response = self.send(
            opener,
            max_retries=2,
            backoff_seconds=0.5,
            sleep=sleeps.append,
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(sleeps, [0.5, 1.0])

    def test_cache_hit_reuses_response(self):
        calls = []

        def opener(request, timeout):
            calls.append(request.full_url)
            return FakeResponse(b'{"ok":true}', status=200)

        cache = InMemoryTTLCache()
        first = self.send(opener, cache=cache)
        second = self.send(opener, cache=cache)

        self.assertFalse(first.from_cache)
        self.assertTrue(second.from_cache)
        self.assertEqual(second.body, '{"ok":true}')
        self.assertEqual(len(calls), 1)

    def test_cache_miss_for_different_urls(self):
        calls = []
        cache = InMemoryTTLCache()

        def opener(request, timeout):
            calls.append(request.full_url)
            return FakeResponse(b'{"ok":true}', status=200)

        self.send(opener, cache=cache, url="https://example.test/a")
        self.send(opener, cache=cache, url="https://example.test/b")

        self.assertEqual(len(calls), 2)

    def test_cache_expires_after_ttl(self):
        now = [0.0]
        cache = InMemoryTTLCache(time_provider=lambda: now[0])
        calls = []

        def opener(request, timeout):
            calls.append(request.full_url)
            return FakeResponse(b'{"ok":true}', status=200)

        self.send(opener, cache=cache, cache_ttl_seconds=10)
        now[0] = 5.0
        cached = self.send(opener, cache=cache, cache_ttl_seconds=10)
        now[0] = 11.0
        expired = self.send(opener, cache=cache, cache_ttl_seconds=10)

        self.assertTrue(cached.from_cache)
        self.assertFalse(expired.from_cache)
        self.assertEqual(len(calls), 2)

    def test_cache_can_be_disabled(self):
        calls = []
        cache = InMemoryTTLCache()

        def opener(request, timeout):
            calls.append(request.full_url)
            return FakeResponse(b'{"ok":true}', status=200)

        self.send(opener, cache=cache, cache_enabled=False)
        self.send(opener, cache=cache, cache_enabled=False)

        self.assertEqual(len(calls), 2)

    def test_retry_logs_redact_sensitive_url(self):
        output = StringIO()
        handler = logging.StreamHandler(output)
        logger = logging.getLogger("tests.http_utils")
        old_handlers = list(logger.handlers)
        old_level = logger.level
        old_propagate = logger.propagate
        logger.handlers = [handler]
        logger.setLevel(logging.INFO)
        logger.propagate = False
        calls = []

        def opener(request, timeout):
            calls.append(request.full_url)
            if len(calls) == 1:
                return FakeResponse(b'{"error":"temporary"}', status=500)
            return FakeResponse(b'{"ok":true}', status=200)

        try:
            self.send(
                opener,
                max_retries=1,
                url="https://example.test/data?api_key=secret&token=hidden",
                logger_name="tests.http_utils",
            )
        finally:
            logger.handlers = old_handlers
            logger.setLevel(old_level)
            logger.propagate = old_propagate

        logs = output.getvalue()
        self.assertNotIn("secret", logs)
        self.assertNotIn("hidden", logs)
        self.assertIn("[REDACTED]", logs)

    def send(
        self,
        opener,
        *,
        url: str = "https://example.test/data",
        max_retries: int = 0,
        backoff_seconds: float = 0.0,
        cache_ttl_seconds: int = 60,
        cache_enabled: bool = True,
        cache: InMemoryTTLCache | None = None,
        sleep=None,
        logger_name: str = "tests.http_utils.default",
    ) -> HTTPResponse:
        return send_request_with_retries(
            Request(url, method="GET"),
            opener=opener,
            timeout_seconds=5,
            response_status=lambda response: response.status,
            read_text=lambda response: response.read().decode("utf-8"),
            settings=HTTPClientSettings(
                max_retries=max_retries,
                backoff_seconds=backoff_seconds,
                cache_ttl_seconds=cache_ttl_seconds,
                cache_enabled=cache_enabled,
            ),
            cache=cache,
            sleep=sleep or (lambda seconds: None),
            logger_name=logger_name,
        )


if __name__ == "__main__":
    unittest.main()
