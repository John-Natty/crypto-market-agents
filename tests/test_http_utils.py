from io import BytesIO, StringIO
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import Request
import json
import logging
import sys
import tempfile
import unittest


PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from crypto_market_agents.http_utils import (
    CacheBackend,
    FileCacheBackend,
    HTTPClientSettings,
    HTTPResponse,
    InMemoryTTLCache,
    backoff_delay,
    build_cache_backend,
    build_cache_key,
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

    def test_build_cache_backend_defaults_to_memory(self):
        cache = build_cache_backend(HTTPClientSettings())

        self.assertIsInstance(cache, InMemoryTTLCache)

    def test_build_cache_backend_can_create_file_cache(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            settings = HTTPClientSettings(cache_backend="file", cache_dir=temp_dir)

            cache = build_cache_backend(settings)

            self.assertIsInstance(cache, FileCacheBackend)

    def test_unknown_cache_backend_raises_value_error(self):
        with self.assertRaises(ValueError):
            HTTPClientSettings(cache_backend="unknown")

    def test_file_cache_writes_successful_get_response(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            calls = []
            cache = FileCacheBackend(temp_dir, time_provider=lambda: 100.0)

            def opener(request, timeout):
                calls.append(request.full_url)
                return FakeResponse(b'{"ok":true}', status=200)

            response = self.send(
                opener,
                cache=cache,
                cache_backend="file",
                cache_dir=temp_dir,
            )

            files = list(Path(temp_dir).glob("*.json"))
            self.assertEqual(response.status_code, 200)
            self.assertEqual(len(calls), 1)
            self.assertEqual(len(files), 1)
            payload = json.loads(files[0].read_text(encoding="utf-8"))
            self.assertEqual(payload["created_at"], 100.0)
            self.assertEqual(payload["ttl_seconds"], 60)
            self.assertEqual(payload["status_code"], 200)
            self.assertEqual(payload["payload"], '{"ok":true}')

    def test_file_cache_reads_non_expired_response(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            calls = []
            cache = FileCacheBackend(temp_dir, time_provider=lambda: 100.0)

            def opener(request, timeout):
                calls.append(request.full_url)
                return FakeResponse(b'{"count":1}', status=200)

            first = self.send(
                opener,
                cache=cache,
                cache_backend="file",
                cache_dir=temp_dir,
                cache_ttl_seconds=30,
            )
            cached = self.send(
                opener,
                cache=FileCacheBackend(temp_dir, time_provider=lambda: 120.0),
                cache_backend="file",
                cache_dir=temp_dir,
                cache_ttl_seconds=30,
            )

            self.assertFalse(first.from_cache)
            self.assertTrue(cached.from_cache)
            self.assertEqual(cached.body, '{"count":1}')
            self.assertEqual(len(calls), 1)

    def test_file_cache_ignores_expired_response(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            calls = []
            logger_name = self.quiet_logger_name("tests.http_utils.file_cache.expired")

            def opener(request, timeout):
                calls.append(request.full_url)
                return FakeResponse(f'{{"count":{len(calls)}}}'.encode(), status=200)

            self.send(
                opener,
                cache=FileCacheBackend(temp_dir, time_provider=lambda: 100.0),
                cache_backend="file",
                cache_dir=temp_dir,
                cache_ttl_seconds=10,
                logger_name=logger_name,
            )
            expired = self.send(
                opener,
                cache=FileCacheBackend(
                    temp_dir,
                    time_provider=lambda: 111.0,
                    logger_name=logger_name,
                ),
                cache_backend="file",
                cache_dir=temp_dir,
                cache_ttl_seconds=10,
                logger_name=logger_name,
            )

            self.assertFalse(expired.from_cache)
            self.assertEqual(expired.body, '{"count":2}')
            self.assertEqual(len(calls), 2)

    def test_file_cache_ignores_corrupted_entry(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            logger_name = self.quiet_logger_name("tests.http_utils.file_cache.corrupted")
            cache = FileCacheBackend(temp_dir, logger_name=logger_name)
            request = Request("https://example.test/data", method="GET")
            key = build_cache_key(request)
            self.assertIsNotNone(key)
            cache_path = cache.cache_path(str(key))
            cache_path.parent.mkdir(parents=True, exist_ok=True)
            cache_path.write_text("{broken", encoding="utf-8")
            calls = []

            def opener(request, timeout):
                calls.append(request.full_url)
                return FakeResponse(b'{"ok":true}', status=200)

            response = self.send(
                opener,
                cache=cache,
                cache_backend="file",
                cache_dir=temp_dir,
                logger_name=logger_name,
            )

            self.assertEqual(response.status_code, 200)
            self.assertFalse(response.from_cache)
            self.assertEqual(len(calls), 1)

    def test_file_cache_filename_hides_sensitive_url(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            cache = FileCacheBackend(temp_dir)

            def opener(request, timeout):
                return FakeResponse(b'{"ok":true}', status=200)

            self.send(
                opener,
                cache=cache,
                cache_backend="file",
                cache_dir=temp_dir,
                url="https://example.test/data?api_key=secret&access_token=hidden",
            )

            filenames = [path.name for path in Path(temp_dir).glob("*.json")]
            serialized = "\n".join(filenames)
            self.assertEqual(len(filenames), 1)
            self.assertNotIn("secret", serialized)
            self.assertNotIn("hidden", serialized)
            self.assertNotIn("api_key", serialized)
            self.assertNotIn("access_token", serialized)

    def test_file_cache_logs_redact_sensitive_url(self):
        output = StringIO()
        handler = logging.StreamHandler(output)
        logger = logging.getLogger("tests.http_utils.file_cache")
        old_handlers = list(logger.handlers)
        old_level = logger.level
        old_propagate = logger.propagate
        logger.handlers = [handler]
        logger.setLevel(logging.DEBUG)
        logger.propagate = False

        try:
            with tempfile.TemporaryDirectory() as temp_dir:
                cache = FileCacheBackend(temp_dir, logger_name="tests.http_utils.file_cache")

                def opener(request, timeout):
                    return FakeResponse(b'{"ok":true}', status=200)

                self.send(
                    opener,
                    cache=cache,
                    cache_backend="file",
                    cache_dir=temp_dir,
                    logger_name="tests.http_utils.file_cache",
                    url="https://example.test/data?api_key=secret&token=hidden",
                )
                self.send(
                    opener,
                    cache=cache,
                    cache_backend="file",
                    cache_dir=temp_dir,
                    logger_name="tests.http_utils.file_cache",
                    url="https://example.test/data?api_key=secret&token=hidden",
                )
        finally:
            logger.handlers = old_handlers
            logger.setLevel(old_level)
            logger.propagate = old_propagate

        logs = output.getvalue()
        self.assertNotIn("secret", logs)
        self.assertNotIn("hidden", logs)
        self.assertIn("[REDACTED]", logs)

    def test_cache_disabled_does_not_write_file_cache(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            calls = []
            cache = FileCacheBackend(temp_dir)

            def opener(request, timeout):
                calls.append(request.full_url)
                return FakeResponse(b'{"ok":true}', status=200)

            self.send(
                opener,
                cache=cache,
                cache_backend="file",
                cache_dir=temp_dir,
                cache_enabled=False,
            )
            self.send(
                opener,
                cache=cache,
                cache_backend="file",
                cache_dir=temp_dir,
                cache_enabled=False,
            )

            self.assertEqual(len(calls), 2)
            self.assertEqual(list(Path(temp_dir).glob("*.json")), [])

    def test_post_requests_are_not_cached(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            calls = []
            cache = FileCacheBackend(temp_dir)

            def opener(request, timeout):
                calls.append(request.full_url)
                return FakeResponse(b'{"ok":true}', status=200)

            self.send(
                opener,
                cache=cache,
                cache_backend="file",
                cache_dir=temp_dir,
                method="POST",
            )
            self.send(
                opener,
                cache=cache,
                cache_backend="file",
                cache_dir=temp_dir,
                method="POST",
            )

            self.assertEqual(len(calls), 2)
            self.assertEqual(list(Path(temp_dir).glob("*.json")), [])

    def test_unavailable_file_cache_directory_does_not_break_request(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            parent_file = Path(temp_dir) / "not-a-directory"
            parent_file.write_text("occupied", encoding="utf-8")
            logger_name = self.quiet_logger_name("tests.http_utils.file_cache.unavailable")
            cache = FileCacheBackend(parent_file / "cache", logger_name=logger_name)
            calls = []

            def opener(request, timeout):
                calls.append(request.full_url)
                return FakeResponse(b'{"ok":true}', status=200)

            response = self.send(
                opener,
                cache=cache,
                cache_backend="file",
                cache_dir=str(parent_file / "cache"),
                logger_name=logger_name,
            )

            self.assertEqual(response.status_code, 200)
            self.assertEqual(len(calls), 1)

    def test_unavailable_file_cache_logs_redact_sensitive_path(self):
        output = StringIO()
        handler = logging.StreamHandler(output)
        logger = logging.getLogger("tests.http_utils.file_cache.unavailable.redacted")
        old_handlers = list(logger.handlers)
        old_level = logger.level
        old_propagate = logger.propagate
        logger.handlers = [handler]
        logger.setLevel(logging.WARNING)
        logger.propagate = False

        try:
            with tempfile.TemporaryDirectory() as temp_dir:
                parent_file = Path(temp_dir) / "api_key=supersecret"
                parent_file.write_text("occupied", encoding="utf-8")
                cache = FileCacheBackend(parent_file / "cache", logger_name=logger.name)

                def opener(request, timeout):
                    return FakeResponse(b'{"ok":true}', status=200)

                self.send(
                    opener,
                    cache=cache,
                    cache_backend="file",
                    cache_dir=str(parent_file / "cache"),
                    logger_name=logger.name,
                )
        finally:
            logger.handlers = old_handlers
            logger.setLevel(old_level)
            logger.propagate = old_propagate

        logs = output.getvalue()
        self.assertNotIn("supersecret", logs)
        self.assertIn("api_key=[REDACTED]", logs)

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
        cache_backend: str = "memory",
        cache_dir: str = ".cache/crypto-market-agents",
        method: str = "GET",
        cache: CacheBackend | None = None,
        sleep=None,
        logger_name: str = "tests.http_utils.default",
    ) -> HTTPResponse:
        return send_request_with_retries(
            Request(url, data=b"{}" if method.upper() == "POST" else None, method=method),
            opener=opener,
            timeout_seconds=5,
            response_status=lambda response: response.status,
            read_text=lambda response: response.read().decode("utf-8"),
            settings=HTTPClientSettings(
                max_retries=max_retries,
                backoff_seconds=backoff_seconds,
                cache_ttl_seconds=cache_ttl_seconds,
                cache_enabled=cache_enabled,
                cache_backend=cache_backend,
                cache_dir=cache_dir,
            ),
            cache=cache,
            sleep=sleep or (lambda seconds: None),
            logger_name=logger_name,
        )

    def quiet_logger_name(self, name: str) -> str:
        logger = logging.getLogger(name)
        old_handlers = list(logger.handlers)
        old_level = logger.level
        old_propagate = logger.propagate
        logger.handlers = [logging.NullHandler()]
        logger.setLevel(logging.CRITICAL)
        logger.propagate = False

        def restore_logger() -> None:
            logger.handlers = old_handlers
            logger.setLevel(old_level)
            logger.propagate = old_propagate

        self.addCleanup(restore_logger)
        return name


if __name__ == "__main__":
    unittest.main()
