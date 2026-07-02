from io import BytesIO
from pathlib import Path
from urllib.error import HTTPError, URLError
import json
import sys
import unittest


PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from crypto_market_agents.notifications.whatsapp_client import WhatsAppClient


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


class WhatsAppClientTests(unittest.TestCase):
    def test_send_text_message_skips_when_disabled(self):
        calls = []
        client = WhatsAppClient(
            enabled=False,
            access_token="token",
            phone_number_id="123",
            to_number="33600000000",
            opener=self.make_opener(b"{}", calls),
        )

        result = client.send_text_message("Bonjour")

        self.assertFalse(result["sent"])
        self.assertEqual(result["status"], "disabled")
        self.assertEqual(calls, [])

    def test_send_text_message_returns_configuration_error(self):
        calls = []
        client = WhatsAppClient(enabled=True, opener=self.make_opener(b"{}", calls))

        result = client.send_text_message("Bonjour")

        self.assertFalse(result["sent"])
        self.assertEqual(result["status"], "configuration_error")
        self.assertIn("WHATSAPP_ACCESS_TOKEN", result["error"])
        self.assertEqual(calls, [])

    def test_send_text_message_posts_to_cloud_api(self):
        calls = []
        client = WhatsAppClient(
            enabled=True,
            access_token="secret-token",
            phone_number_id="123456",
            to_number="33600000000",
            graph_api_version="v23.0",
            opener=self.make_opener(
                b'{"messaging_product":"whatsapp","messages":[{"id":"wamid.test"}]}',
                calls,
            ),
        )

        result = client.send_text_message("Rapport crypto")

        request = calls[0]
        headers = {key.lower(): value for key, value in request.header_items()}
        body = json.loads(request.data.decode("utf-8"))
        self.assertTrue(result["sent"])
        self.assertEqual(result["status"], "sent")
        self.assertEqual(request.get_method(), "POST")
        self.assertEqual(
            request.full_url,
            "https://graph.facebook.com/v23.0/123456/messages",
        )
        self.assertEqual(headers["authorization"], "Bearer secret-token")
        self.assertEqual(body["messaging_product"], "whatsapp")
        self.assertEqual(body["to"], "33600000000")
        self.assertEqual(body["text"]["body"], "Rapport crypto")
        self.assertNotIn("secret-token", str(result))

    def test_send_text_message_handles_http_status_error(self):
        client = WhatsAppClient(
            enabled=True,
            access_token="token",
            phone_number_id="123",
            to_number="33600000000",
            opener=self.make_opener(b'{"error":{"message":"bad request"}}', status=400),
        )

        result = client.send_text_message("Rapport crypto")

        self.assertFalse(result["sent"])
        self.assertEqual(result["status"], "http_error")
        self.assertIn("HTTP 400", result["error"])

    def test_send_text_message_handles_http_error_exception(self):
        def opener(request, timeout):
            raise HTTPError(
                request.full_url,
                401,
                "Unauthorized",
                {},
                BytesIO(b'{"error":{"message":"bad token"}}'),
            )

        client = WhatsAppClient(
            enabled=True,
            access_token="bad-token",
            phone_number_id="123",
            to_number="33600000000",
            opener=opener,
        )

        result = client.send_text_message("Rapport crypto")

        self.assertFalse(result["sent"])
        self.assertEqual(result["status"], "http_error")
        self.assertIn("HTTP 401", result["error"])

    def test_send_text_message_redacts_token_from_http_error_body(self):
        client = WhatsAppClient(
            enabled=True,
            access_token="super-secret-token",
            phone_number_id="123",
            to_number="33600000000",
            opener=self.make_opener(
                b'{"error":"super-secret-token is invalid"}',
                status=401,
            ),
        )

        result = client.send_text_message("Rapport crypto")

        self.assertFalse(result["sent"])
        self.assertEqual(result["status"], "http_error")
        self.assertNotIn("super-secret-token", result["error"])
        self.assertIn("***", result["error"])

    def test_send_text_message_handles_timeout(self):
        def opener(request, timeout):
            raise URLError(TimeoutError("timed out"))

        client = WhatsAppClient(
            enabled=True,
            access_token="token",
            phone_number_id="123",
            to_number="33600000000",
            opener=opener,
        )

        result = client.send_text_message("Rapport crypto")

        self.assertFalse(result["sent"])
        self.assertEqual(result["status"], "timeout")

    def test_send_text_message_handles_invalid_json(self):
        client = WhatsAppClient(
            enabled=True,
            access_token="token",
            phone_number_id="123",
            to_number="33600000000",
            opener=self.make_opener(b"not json"),
        )

        result = client.send_text_message("Rapport crypto")

        self.assertFalse(result["sent"])
        self.assertEqual(result["status"], "invalid_json")

    def test_send_text_message_rejects_empty_message(self):
        client = WhatsAppClient(
            enabled=True,
            access_token="token",
            phone_number_id="123",
            to_number="33600000000",
        )

        result = client.send_text_message("   ")

        self.assertFalse(result["sent"])
        self.assertEqual(result["status"], "invalid_message")

    @staticmethod
    def make_opener(payload: bytes, calls=None, *, status: int = 200):
        def opener(request, timeout):
            if calls is not None:
                calls.append(request)
            return FakeResponse(payload, status=status)

        return opener


if __name__ == "__main__":
    unittest.main()
