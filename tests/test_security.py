from pathlib import Path
import sys
import unittest


PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from crypto_market_agents.security import (
    SecurityError,
    assert_read_only_operation,
    redact_environment,
    redact_mapping,
    redact_text,
    redact_url,
    redact_value,
    validate_security_environment,
)


class SecurityTests(unittest.TestCase):
    def test_rejects_private_key_environment_variable(self):
        with self.assertRaises(SecurityError):
            validate_security_environment({"WALLET_PRIVATE_KEY": "not-allowed"})

    def test_rejects_truthy_trading_flag(self):
        with self.assertRaises(SecurityError):
            validate_security_environment({"TRADING_ENABLED": "true"})

    def test_rejects_non_read_only_exchange_mode(self):
        with self.assertRaises(SecurityError):
            validate_security_environment({"EXCHANGE_MODE": "trading"})

    def test_rejects_non_read_only_operation_names(self):
        with self.assertRaises(SecurityError):
            assert_read_only_operation("place_buy_order")

    def test_redacts_secret_like_values(self):
        redacted = redact_environment(
            {
                "OPENAI_API_KEY": "sk-123456789",
                "BASE_CURRENCY": "usd",
            }
        )

        self.assertEqual(redacted["OPENAI_API_KEY"], "sk-1...6789")
        self.assertEqual(redacted["BASE_CURRENCY"], "usd")

    def test_redacts_whatsapp_token_values(self):
        redacted = redact_environment(
            {
                "WHATSAPP_ACCESS_TOKEN": "eaag-long-token-value",
                "WHATSAPP_TO_NUMBER": "33600000000",
            }
        )

        self.assertEqual(redacted["WHATSAPP_ACCESS_TOKEN"], "eaag...alue")
        self.assertEqual(redacted["WHATSAPP_TO_NUMBER"], "33600000000")

    def test_redact_value_returns_full_redaction_marker(self):
        self.assertEqual(redact_value("secret"), "[REDACTED]")

    def test_redact_url_masks_api_key(self):
        redacted = redact_url("https://example.test/path?api_key=secret&safe=value")

        self.assertNotIn("secret", redacted)
        self.assertIn("api_key=[REDACTED]", redacted)
        self.assertIn("safe=value", redacted)

    def test_redact_url_masks_access_token(self):
        redacted = redact_url("https://example.test/path?access_token=secret-token")

        self.assertNotIn("secret-token", redacted)
        self.assertIn("access_token=[REDACTED]", redacted)

    def test_redact_url_masks_credentials_in_netloc(self):
        redacted = redact_url("https://user:password@example.test/path?safe=value")

        self.assertNotIn("user", redacted)
        self.assertNotIn("password", redacted)
        self.assertIn("[REDACTED]@example.test", redacted)

    def test_redact_url_removes_fragment(self):
        redacted = redact_url("https://example.test/path?safe=value#access_token=secret")

        self.assertNotIn("#", redacted)
        self.assertNotIn("secret", redacted)

    def test_redact_mapping_masks_sensitive_keys(self):
        redacted = redact_mapping(
            {
                "NEWS_API_KEY": "secret",
                "safe": "value",
                "count": 2,
                "enabled": True,
            }
        )

        self.assertEqual(redacted["NEWS_API_KEY"], "[REDACTED]")
        self.assertEqual(redacted["safe"], "value")
        self.assertEqual(redacted["count"], 2)
        self.assertTrue(redacted["enabled"])

    def test_redact_mapping_handles_nested_values(self):
        redacted = redact_mapping(
            {
                "outer": {
                    "token": "secret",
                    "items": [
                        {"password": "hidden"},
                        {"url": "https://example.test/path?api_key=secret"},
                    ],
                }
            }
        )

        self.assertEqual(redacted["outer"]["token"], "[REDACTED]")
        self.assertEqual(redacted["outer"]["items"][0]["password"], "[REDACTED]")
        self.assertNotIn("secret", redacted["outer"]["items"][1]["url"])

    def test_redact_text_masks_sensitive_url_and_bearer_token(self):
        redacted = redact_text(
            "failed https://example.test/path?api_key=secret Authorization: Bearer abc123"
        )

        self.assertNotIn("secret", redacted)
        self.assertNotIn("abc123", redacted)
        self.assertIn("api_key=[REDACTED]", redacted)


if __name__ == "__main__":
    unittest.main()
