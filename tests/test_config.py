from pathlib import Path
import sys
import tempfile
import unittest


PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from crypto_market_agents.config import ConfigError, load_config, read_env_file


class ConfigTests(unittest.TestCase):
    def write_env(self, content: str) -> Path:
        temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(temp_dir.cleanup)
        path = Path(temp_dir.name) / ".env"
        path.write_text(content, encoding="utf-8")
        return path

    def test_loads_default_configuration_from_env_file(self):
        env_path = self.write_env(
            """
            APP_ENV=development
            LOG_LEVEL=INFO
            BASE_CURRENCY=usd
            WATCHLIST=bitcoin, ethereum, bitcoin, solana
            REPORT_LANGUAGE=fr
            COINGECKO_BASE_URL=https://api.coingecko.com/api/v3/
            COINGECKO_TIMEOUT=12
            NEWS_PROVIDER=newsapi
            NEWS_API_KEY=test-news-key
            NEWS_BASE_URL=https://newsapi.org/v2/
            NEWS_TIMEOUT=9
            NEWS_LANGUAGE=en
            NEWS_DEFAULT_QUERY=crypto OR bitcoin
            NEWS_MAX_ARTICLES=7
            DEFILLAMA_BASE_URL=https://api.llama.fi
            DEFILLAMA_TIMEOUT=11
            WHATSAPP_ENABLED=false
            EXCHANGE_MODE=disabled
            TRADING_ENABLED=false
            WITHDRAWALS_ENABLED=false
            ORDER_EXECUTION_ENABLED=false
            """
        )

        config = load_config(env_path, include_os_environ=False)

        self.assertEqual(config.app_env, "development")
        self.assertEqual(config.log_level, "INFO")
        self.assertEqual(config.watchlist, ("bitcoin", "ethereum", "solana"))
        self.assertEqual(config.coingecko.base_url, "https://api.coingecko.com/api/v3")
        self.assertEqual(config.coingecko.timeout_seconds, 12)
        self.assertEqual(config.news.provider, "newsapi")
        self.assertEqual(config.news.api_key, "test-news-key")
        self.assertEqual(config.news.base_url, "https://newsapi.org/v2")
        self.assertEqual(config.news.timeout_seconds, 9)
        self.assertEqual(config.news.language, "en")
        self.assertEqual(config.news.default_query, "crypto OR bitcoin")
        self.assertEqual(config.news.max_articles, 7)
        self.assertEqual(config.defillama.base_url, "https://api.llama.fi")
        self.assertEqual(config.defillama.timeout_seconds, 11)
        self.assertFalse(config.whatsapp.enabled)
        self.assertEqual(config.security.exchange_mode, "disabled")

    def test_defaults_do_not_require_news_or_whatsapp_keys(self):
        env_path = self.write_env("")

        config = load_config(env_path, include_os_environ=False)

        self.assertIsNone(config.news.api_key)
        self.assertFalse(config.whatsapp.enabled)
        self.assertIsNone(config.whatsapp.access_token)
        self.assertIsNone(config.whatsapp.to_number)

    def test_dotenv_parser_supports_quotes_exports_and_comments(self):
        env_path = self.write_env(
            """
            export APP_ENV="test"
            WATCHLIST='bitcoin,ethereum' # comment
            LLM_MODEL=gpt-test#kept
            """
        )

        env = read_env_file(env_path)

        self.assertEqual(env["APP_ENV"], "test")
        self.assertEqual(env["WATCHLIST"], "bitcoin,ethereum")
        self.assertEqual(env["LLM_MODEL"], "gpt-test#kept")

    def test_whatsapp_enabled_without_credentials_does_not_break_config(self):
        env_path = self.write_env("WHATSAPP_ENABLED=true\n")

        config = load_config(env_path, include_os_environ=False)

        self.assertTrue(config.whatsapp.enabled)
        self.assertIsNone(config.whatsapp.access_token)
        self.assertIsNone(config.whatsapp.phone_number_id)
        self.assertIsNone(config.whatsapp.to_number)

    def test_loads_whatsapp_configuration(self):
        env_path = self.write_env(
            """
            WHATSAPP_ENABLED=true
            WHATSAPP_ACCESS_TOKEN=test-token
            WHATSAPP_PHONE_NUMBER_ID=123456789
            WHATSAPP_TO_NUMBER=33600000000
            WHATSAPP_GRAPH_API_VERSION=v23.0
            WHATSAPP_TIMEOUT=7
            """
        )

        config = load_config(env_path, include_os_environ=False)

        self.assertTrue(config.whatsapp.enabled)
        self.assertEqual(config.whatsapp.access_token, "test-token")
        self.assertEqual(config.whatsapp.phone_number_id, "123456789")
        self.assertEqual(config.whatsapp.to_number, "33600000000")
        self.assertEqual(config.whatsapp.graph_api_version, "v23.0")
        self.assertEqual(config.whatsapp.timeout_seconds, 7)

    def test_llm_enabled_requires_api_key_and_model(self):
        env_path = self.write_env("LLM_ENABLED=true\nOPENAI_API_KEY=test\n")

        with self.assertRaises(ConfigError):
            load_config(env_path, include_os_environ=False)


if __name__ == "__main__":
    unittest.main()
