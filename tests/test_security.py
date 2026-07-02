from pathlib import Path
import sys
import unittest


PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from crypto_market_agents.security import (
    SecurityError,
    assert_read_only_operation,
    redact_environment,
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


if __name__ == "__main__":
    unittest.main()
