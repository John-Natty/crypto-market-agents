from io import StringIO
from pathlib import Path
import logging
import sys
import unittest


PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from crypto_market_agents.logging_utils import configure_logging, get_logger


class LoggingUtilsTests(unittest.TestCase):
    def test_configure_logging_redacts_secrets_from_messages(self):
        stream = StringIO()
        handler = logging.StreamHandler(stream)
        root_logger = logging.getLogger()
        old_handlers = root_logger.handlers[:]
        old_level = root_logger.level

        try:
            root_logger.handlers = [handler]
            configure_logging("DEBUG")
            logger = get_logger("crypto_market_agents.tests")

            logger.debug(
                "failed https://example.test/path?api_key=secret Authorization: Bearer abc123"
            )

            output = stream.getvalue()
            self.assertNotIn("secret", output)
            self.assertNotIn("abc123", output)
            self.assertIn("[REDACTED]", output)
        finally:
            root_logger.handlers = old_handlers
            root_logger.setLevel(old_level)

    def test_configure_logging_defaults_unknown_level_to_info(self):
        root_logger = logging.getLogger()
        old_handlers = root_logger.handlers[:]
        old_level = root_logger.level

        try:
            root_logger.handlers = [logging.StreamHandler(StringIO())]
            configure_logging("not-a-level")

            self.assertEqual(root_logger.level, logging.INFO)
        finally:
            root_logger.handlers = old_handlers
            root_logger.setLevel(old_level)


if __name__ == "__main__":
    unittest.main()
