from datetime import datetime, timezone
import json
from pathlib import Path
import sys
import tempfile
import unittest

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from crypto_market_agents.clients.gnews_news_client import (
    GNewsAPIKeyMissingError,
    GNewsNewsClient,
    GNewsQuotaExceededError,
)
from crypto_market_agents.http_utils import HTTPClientSettings, InMemoryTTLCache
from crypto_market_agents.trading.autonomous_research import (
    _record_tool_facts,
    _tool_schemas,
    _validate_tool_args,
)


NOW = datetime(2026, 11, 9, 17, 0, tzinfo=timezone.utc)
PEPE_IDENTITY = {
    "status": "resolved_cross_referenced",
    "provider_id": "pepe",
    "symbol": "PEPE",
    "coingecko_name": "Pepe",
    "bitvavo_name": "Pepe",
}

# Titles and metadata are a compact subset of the prior real, sanitized test
# response. No credential or full article text is stored in test fixtures.
PREVIOUS_TEST_ARTICLES = [
    {
        "title": "Pepe Price Prediction: A 7% Jump Against Bitcoin's Slide Lifts It Toward $0.0000065",
        "description": "PEPE just did, jumping about 7% while Bitcoin slid toward $75,000. The frog-themed token remains volatile.",
        "content": "PEPE just did, jumping about 7% while Bitcoin slid toward $75,000. The frog-themed token remains volatile.",
        "url": "https://techbullion.com/pepe-price-prediction-a-7-jump-against-bitcoins-slide-lifts-it-toward-0-0000065/",
        "publishedAt": "2026-11-07T15:00:17Z",
        "source": {"name": "TechBullion", "url": "https://techbullion.com"},
    },
    {
        "title": "Crypto Bull Run: Bitcoin ETFs Lose $411.9M Friday as Pepe’s Cofounder Builds Pepeto",
        "description": "Explore the current state of the Crypto Bull Run.",
        "content": "Investors waited for crypto buyers to return as the article promoted Pepeto.",
        "url": "https://techbullion.com/crypto-bull-run-bitcoin-etfs-lose-411-9m-friday-as-pepes-cofounder-builds-pepeto/",
        "publishedAt": "2026-11-07T23:40:17Z",
        "source": {"name": "TechBullion", "url": "https://techbullion.com"},
    },
    {
        "title": "Best Crypto Presale 2026: Buyers Back Pepeto From Pepe's Cofounder",
        "description": "Discover why Pepeto is gaining attention.",
        "content": "A promoted presale article about Pepeto.",
        "url": "https://techbullion.com/best-crypto-presale-2026-buyers-back-pepeto/",
        "publishedAt": "2026-11-05T05:55:03Z",
        "source": {"name": "TechBullion", "url": "https://techbullion.com"},
    },
]


class FakeResponse:
    status = 200

    def __init__(self, body):
        self.body = body

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return None

    def read(self, size=-1):
        return self.body[:size] if size >= 0 else self.body


class GNewsNewsClientTests(unittest.TestCase):
    def client(self, payloads, **kwargs):
        calls = []

        def opener(request, timeout):
            calls.append(request)
            key = request.full_url.split("?", 1)[-1]
            body = payloads[key]
            return FakeResponse(json.dumps(body).encode())

        temp_dir = kwargs.pop("temp_dir", None)
        if temp_dir is None:
            temp_dir = tempfile.TemporaryDirectory()
            self.addCleanup(temp_dir.cleanup)
        api_key = kwargs.pop("api_key", "test-secret-value")
        settings = HTTPClientSettings(
            max_retries=0,
            backoff_seconds=0,
            cache_ttl_seconds=3600,
            cache_enabled=True,
        )
        client = GNewsNewsClient(
            api_key=api_key,
            timeout_seconds=2,
            opener=opener,
            http_settings=settings,
            cache=kwargs.pop("cache", InMemoryTTLCache()),
            quota_path=Path(temp_dir.name) / "quota.sqlite3",
            now_provider=lambda: NOW,
            monotonic=lambda: 100.0,
            sleep=lambda _seconds: None,
            **kwargs,
        )
        return client, calls, temp_dir

    def test_missing_key_fails_without_network(self):
        with self.assertRaises(GNewsAPIKeyMissingError) as raised:
            GNewsNewsClient(api_key=None)
        self.assertNotIn("test-secret", str(raised.exception))

    def test_request_caps_cannot_be_configured_above_the_free_app_limits(self):
        with self.assertRaises(ValueError):
            GNewsNewsClient(api_key="test-key", max_requests_per_research=6)
        with self.assertRaises(ValueError):
            GNewsNewsClient(api_key="test-key", max_requests_per_day=21)

    def test_duplicate_rows_in_one_response_are_deduplicated(self):
        query = "crypto OR bitcoin OR ethereum OR blockchain"
        article = {
            "title": "Bitcoin crypto market update",
            "description": "The crypto market moved.",
            "url": "https://market.example/story",
            "publishedAt": "2026-11-09T12:00:00Z",
            "source": {"name": "Market Desk", "url": "https://market.example"},
        }
        client, _, _ = self.client({self.query_key(query): {"articles": [article, article]}})
        result = client.search_market()
        self.assertEqual(len(result["articles"]), 1)
        self.assertEqual(result["duplicate_item_count"], 1)

    def test_pepe_sample_separates_token_story_from_pepeto_homonym_and_promotion(self):
        query = '"Pepe"'
        params = self.query_key(query)
        client, calls, _ = self.client({params: {"articles": PREVIOUS_TEST_ARTICLES}})
        result = client.search_asset(identity=PEPE_IDENTITY)
        self.assertEqual(len(calls), 1)
        self.assertEqual(len(result["articles"]), 1)
        self.assertIn("Pepe Price Prediction", result["articles"][0]["title"])
        self.assertEqual(
            result["articles"][0]["attribution_status"], "verified_name_and_token_specific_cue"
        )
        excluded = {row["relevance_status"] for row in result["excluded_articles"]}
        self.assertIn("ambiguous", excluded)
        self.assertIn("promotional", excluded)
        self.assertEqual(result["publisher_count"], 1)
        self.assertEqual(result["source_independence_status"], "single_publisher_or_none")

    def test_duplicate_across_general_and_pepe_queries_is_grouped_not_independent(self):
        general_query = "crypto OR bitcoin OR ethereum OR blockchain"
        asset_query = '"Pepe"'
        article = PREVIOUS_TEST_ARTICLES[1]
        client, calls, _ = self.client(
            {
                self.query_key(general_query): {"articles": [article]},
                self.query_key(asset_query): {"articles": [article]},
            },
            max_requests_per_research=2,
        )
        client.search_market()
        result = client.search_asset(identity=PEPE_IDENTITY)
        self.assertEqual(len(calls), 2)
        self.assertEqual(len(result["duplicate_groups"]), 1)
        group = result["duplicate_groups"][0]
        self.assertTrue(group["repeated_coverage_is_not_independent_confirmation"])
        self.assertEqual(group["publisher_count"], 1)
        self.assertFalse(result["articles"])
        self.assertEqual(result["excluded_articles"][0]["relevance_status"], "ambiguous")

    def test_no_results_does_not_establish_event_absence(self):
        query = "crypto OR bitcoin OR ethereum OR blockchain"
        client, _, _ = self.client({self.query_key(query): {"articles": []}})
        result = client.search_market()
        self.assertEqual(result["search_result_status"], "no_results_in_requested_window")
        self.assertFalse(result["event_absence_established"])
        self.assertEqual(result["coverage"]["retained_relevant_count"], 0)

    def test_daily_quota_persists_across_client_instances_and_limits_network_calls(self):
        query1 = "crypto OR bitcoin OR ethereum OR blockchain"
        query2 = '"Pepe"'
        shared = tempfile.TemporaryDirectory()
        self.addCleanup(shared.cleanup)
        first, first_calls, _ = self.client(
            {self.query_key(query1): {"articles": []}},
            temp_dir=shared,
            max_requests_per_research=2,
            max_requests_per_day=1,
        )
        first.search_market()
        second, second_calls, _ = self.client(
            {self.query_key(query2): {"articles": []}},
            temp_dir=shared,
            max_requests_per_research=2,
            max_requests_per_day=1,
        )
        with self.assertRaises(GNewsQuotaExceededError):
            second.search_asset(identity=PEPE_IDENTITY)
        self.assertEqual(len(first_calls), 1)
        self.assertEqual(second_calls, [])

    def test_research_request_cap_includes_cache_hits_and_key_never_enters_url(self):
        query = "crypto OR bitcoin OR ethereum OR blockchain"
        client, calls, _ = self.client(
            {self.query_key(query): {"articles": []}}, max_requests_per_research=1
        )
        client.search_market()
        with self.assertRaises(GNewsQuotaExceededError):
            client.search_market()
        self.assertEqual(len(calls), 1)
        self.assertNotIn("test-secret-value", calls[0].full_url)
        self.assertEqual(calls[0].get_header("X-api-key"), "test-secret-value")

    def test_cache_hit_consumes_run_allowance_but_not_persistent_daily_quota(self):
        query = "crypto OR bitcoin OR ethereum OR blockchain"
        cache = InMemoryTTLCache()
        client, calls, _ = self.client(
            {self.query_key(query): {"articles": []}},
            cache=cache,
            max_requests_per_research=2,
        )
        first = client.search_market()
        second = client.search_market()
        self.assertEqual(len(calls), 1)
        self.assertEqual(first["quota"]["daily_requests_used"], 1)
        self.assertEqual(second["quota"]["daily_requests_used"], 1)
        self.assertEqual(second["quota"]["research_requests_used"], 2)
        self.assertTrue(second["from_cache"])
        self.assertNotIn("test-secret-value", json.dumps(second))

    def test_cache_isolated_between_credentials(self):
        query = "crypto OR bitcoin OR ethereum OR blockchain"
        cache = InMemoryTTLCache()
        payload = {self.query_key(query): {"articles": []}}
        first, first_calls, _ = self.client(payload, cache=cache, api_key="first-secret")
        second, second_calls, _ = self.client(payload, cache=cache, api_key="second-secret")
        first.search_market()
        second.search_market()
        self.assertEqual(len(first_calls), 1)
        self.assertEqual(len(second_calls), 1)

    def test_bad_publication_date_remains_unknown_and_invalid(self):
        query = "crypto OR bitcoin OR ethereum OR blockchain"
        article = {
            "title": "Bitcoin crypto update",
            "description": "Crypto market.",
            "url": "https://market.example/story",
            "publishedAt": "not-a-date",
            "source": {"name": "Market Desk"},
        }
        client, _, _ = self.client({self.query_key(query): {"articles": [article]}})
        result = client.search_market()
        self.assertIsNone(result["articles"][0]["published_at"])
        self.assertEqual(result["articles"][0]["publication_date_status"], "invalid")
        self.assertEqual(
            result["articles"][0]["freshness_status"],
            "unknown_or_invalid_publication_date",
        )

    def test_excerpt_is_bounded_and_article_urls_are_never_requested(self):
        query = "crypto OR bitcoin OR ethereum OR blockchain"
        article = {
            "title": "Bitcoin market update",
            "description": "Crypto market.",
            "content": "x" * 500,
            "url": "https://article.example/full-story",
            "publishedAt": "2026-11-09T12:00:00Z",
            "source": {"name": "Market Desk"},
        }
        client, calls, _ = self.client({self.query_key(query): {"articles": [article]}})
        result = client.search_market()
        self.assertEqual(len(result["articles"][0]["excerpt"]), 250)
        self.assertEqual(calls[0].full_url.split("?", 1)[0], "https://gnews.io/api/v4/search")
        self.assertFalse(result["articles"][0]["is_full_article"])

    def test_unresolved_identity_never_queries_symbol(self):
        client, calls, _ = self.client({})
        result = client.search_asset(identity={"status": "unresolved", "symbol": "PEPE"})
        self.assertFalse(calls)
        self.assertEqual(result["search_result_status"], "not_searched_identity_unresolved")
        self.assertFalse(result["event_absence_established"])

    def test_resolved_but_nondistinctive_name_is_not_searched(self):
        client, calls, _ = self.client({})
        identity = {**PEPE_IDENTITY, "coingecko_name": "O", "bitvavo_name": "O"}
        result = client.search_asset(identity=identity)
        self.assertEqual(calls, [])
        self.assertEqual(result["identity_status"], "resolved_cross_referenced")
        self.assertEqual(
            result["identity_reason"], "verified_name_not_distinctive_enough_for_search"
        )
        self.assertEqual(result["search_result_status"], "not_searched_name_not_distinctive")

    def test_gnews_tools_are_opt_in_and_excluded_results_are_not_evidence(self):
        default_names = {row["function"]["name"] for row in _tool_schemas(["PEPE-EUR"], 5)}
        enabled_names = {
            row["function"]["name"] for row in _tool_schemas(["PEPE-EUR"], 5, include_gnews=True)
        }
        self.assertNotIn("get_gnews_asset_news", default_names)
        self.assertIn("get_gnews_asset_news", enabled_names)
        self.assertFalse(any("order" in name for name in enabled_names))
        self.assertEqual(
            _validate_tool_args("get_gnews_asset_news", {"market": "PEPE-EUR"}, ["PEPE-EUR"], 5),
            {"market": "PEPE-EUR"},
        )
        result = {
            "identity_status": "resolved_cross_referenced",
            "articles": [
                {
                    "id": "accepted-1",
                    "title": "PEPE token price rises",
                    "publisher": "Crypto Desk",
                    "published_at": "2026-11-09T12:00:00Z",
                    "freshness_status": "within_requested_window",
                    "relevance_status": "retained_relevant_result",
                }
            ],
            "excluded_articles": [
                {
                    "id": "excluded-1",
                    "title": "Pepeto presale 100x",
                    "publisher": "Crypto Desk",
                    "relevance_status": "promotional",
                    "exclusion_reason": "promotional_language_detected",
                }
            ],
            "duplicate_groups": [],
            "search_result_status": "results_observed",
            "coverage": {
                "status": "partial_single_provider",
                "coverage_missing": ["other sources"],
            },
            "limits": {"free_plan_delay_hours": 12, "article_pages_fetched": False},
            "quota": {"research_requests_used": 1, "daily_requests_used": 1},
        }
        facts, missing = {}, {}
        summary = _record_tool_facts(
            "get_gnews_asset_news", "PEPE-EUR", result, facts, missing, "2026-11-09T17:00:00Z"
        )
        self.assertIn("news:gnews:PEPE-EUR:article:accepted-1", summary["available_fact_refs"])
        self.assertTrue(any(":coverage:" in ref for ref in summary["available_fact_refs"]))
        self.assertEqual(len(summary["excluded_result_refs"]), 1)
        self.assertEqual(facts["news:gnews:PEPE-EUR:article:accepted-1"]["source"], "gnews_api")
        self.assertEqual(
            facts["news:gnews:PEPE-EUR:excluded:excluded-1"]["source"],
            "gnews_excluded_result",
        )
        self.assertNotIn("excluded-1", summary["available_fact_refs"])

    @staticmethod
    def query_key(query):
        from urllib.parse import urlencode

        return urlencode(
            {
                "q": query,
                "lang": "en",
                "max": "10",
                "sortby": "publishedAt",
                "from": "2026-10-10T17:00:00Z",
                "to": "2026-11-09T17:00:00Z",
            }
        )


if __name__ == "__main__":
    unittest.main()
