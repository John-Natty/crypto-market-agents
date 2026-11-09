from pathlib import Path
import json
import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from crypto_market_agents.agents.asset_fundamentals import enrich_analysis_report


class FakeCoinGecko:
    def __init__(self, coins=None, details=None, error=None, tickers=None):
        self.coins = coins or []
        self.details = details or {}
        self.error = error
        self.tickers = tickers or {}
        self.requested = []
        self.ticker_requests = []

    def get_coin_list(self, *, include_platform=True):
        if self.error:
            raise RuntimeError(self.error)
        return self.coins

    def get_coin(self, coin_id, *, market_data=True):
        self.requested.append(coin_id)
        return self.details[coin_id]

    def get_coin_tickers(self, coin_id, *, exchange_ids="bitvavo", page=1):
        self.ticker_requests.append((coin_id, exchange_ids, page))
        if self.error:
            raise RuntimeError(self.error)
        return {"tickers": self.tickers.get(coin_id, [])}


class FakeBitvavo:
    def __init__(self, assets=None, error=None):
        self.assets = assets or []
        self.error = error

    def get_assets(self):
        if self.error:
            raise RuntimeError(self.error)
        return self.assets


class FakeDefiLlama:
    def __init__(self, protocols=None, details=None, error=None):
        self.protocols = protocols or []
        self.details = details or {}
        self.error = error
        self.requested = []

    def get_protocols(self):
        if self.error:
            raise RuntimeError(self.error)
        return self.protocols

    def get_protocol(self, slug):
        self.requested.append(slug)
        return self.details[slug]


def report(*markets):
    return {
        "generated_at": "2026-11-08T00:00:00+00:00",
        "candidates": [
            {
                "market": market,
                "scanner_candidate": {"market": market, "base_asset": market.split("-")[0]},
            }
            for market in markets
        ],
    }


def coin(coin_id, symbol="abc", name="Asset", platforms=None):
    return {"id": coin_id, "symbol": symbol, "name": name, "platforms": platforms or {}}


def details(coin_id, *, market_cap=None, supply=None):
    return {
        "id": coin_id,
        "description": {"en": "external text"},
        "links": {"homepage": ["https://example.test"]},
        "categories": ["Example"],
        "market_data": {
            "market_cap": {"eur": market_cap} if market_cap is not None else {},
            "fully_diluted_valuation": {},
            "circulating_supply": supply,
            "total_supply": None,
            "max_supply": None,
        },
    }


class AssetFundamentalsTests(unittest.TestCase):
    def test_same_symbol_different_assets_remains_unresolved(self):
        cg = FakeCoinGecko([coin("abc-one"), coin("abc-two")])
        result = enrich_analysis_report(
            report("ABC-EUR"),
            bitvavo_client=FakeBitvavo(
                [{"symbol": "ABC", "name": "ABC Token", "networks": ["ETH"]}]
            ),
            coingecko_client=cg,
            defillama_client=FakeDefiLlama(),
            coingecko_min_interval_seconds=0,
        )
        row = result["candidates"][0]
        self.assertEqual(row["identity_status"], "unresolved")
        self.assertEqual(row["bitvavo_identity"]["name"], "ABC Token")
        self.assertEqual(row["bitvavo_identity"]["provider_id"], "ABC")
        self.assertEqual(row["identity_observations"]["supported_networks"]["value"], ["ETH"])
        self.assertEqual(len(row["symbol_candidates"]), 2)
        self.assertEqual(row["observations"]["market_cap"]["reason"], "identity_unresolved")
        self.assertEqual(cg.requested, [])

    def test_cross_referenced_bitvavo_market_resolves_and_preserves_proof_without_mapping_write(
        self,
    ):
        exact_ticker = {
            "market": {"identifier": "bitvavo", "name": "Bitvavo"},
            "base": "ABC",
            "target": "EUR",
            "trade_url": "https://www.bitvavo.com/en/markets/abc-eur",
            "last_traded_at": "2026-11-09T10:00:00Z",
            "trust_score": "green",
            "is_stale": False,
            "is_anomaly": False,
        }
        cg = FakeCoinGecko(
            [
                coin("abc-one", platforms={"ethereum": "0xabc"}),
                coin("abc-two", platforms={"base": "0xdef"}),
            ],
            {"abc-one": details("abc-one")},
            tickers={"abc-one": [exact_ticker]},
        )
        mappings = {}
        out = enrich_analysis_report(
            report("ABC-EUR"),
            bitvavo_client=FakeBitvavo(
                [{"symbol": "ABC", "name": "ABC Token", "networks": ["Ethereum"]}]
            ),
            coingecko_client=cg,
            defillama_client=FakeDefiLlama(),
            explicit_mappings=mappings,
            now_provider=lambda: datetime(2026, 11, 9, tzinfo=timezone.utc),
            coingecko_min_interval_seconds=0,
        )
        row = out["candidates"][0]
        self.assertEqual(row["identity_status"], "resolved_cross_referenced")
        self.assertEqual(row["coingecko_identity"]["provider_id"], "abc-one")
        self.assertEqual(row["coingecko_identity"]["network"], "ethereum")
        self.assertEqual(row["coingecko_identity"]["contract_address"], "0xabc")
        self.assertEqual(row["identity_resolution"]["basis"], "coingecko_bitvavo_market_ticker")
        proof = row["identity_resolution"]["evidence"][0]
        self.assertEqual(proof["bitvavo_market"], "ABC-EUR")
        self.assertEqual(proof["source_at"], "2026-11-09T10:00:00Z")
        self.assertEqual(len(cg.ticker_requests), 2)
        self.assertEqual(mappings, {})
        self.assertEqual(row["observations"]["market_cap"]["value"], None)

    def test_stale_ticker_cannot_resolve_identity(self):
        ticker = {
            "market": {"identifier": "bitvavo", "name": "Bitvavo"},
            "base": "ABC",
            "target": "EUR",
            "last_traded_at": "2026-11-09T10:00:00Z",
            "is_stale": True,
            "is_anomaly": False,
        }
        cg = FakeCoinGecko([coin("abc-one")], tickers={"abc-one": [ticker]})
        out = enrich_analysis_report(
            report("ABC-EUR"),
            bitvavo_client=FakeBitvavo([{"symbol": "ABC", "networks": ["Ethereum"]}]),
            coingecko_client=cg,
            defillama_client=FakeDefiLlama(),
            coingecko_min_interval_seconds=0,
        )
        self.assertEqual(out["candidates"][0]["identity_status"], "unresolved")
        self.assertEqual(
            out["candidates"][0]["identity_reason"], "no_matching_bitvavo_market_ticker"
        )

    def test_conflicting_cross_provider_market_evidence_stays_unresolved(self):
        ticker = {
            "market": {"identifier": "bitvavo", "name": "Bitvavo"},
            "base": "ABC",
            "target": "EUR",
            "last_traded_at": "2026-11-09T10:00:00Z",
            "is_stale": False,
            "is_anomaly": False,
        }
        cg = FakeCoinGecko(
            [coin("abc-one"), coin("abc-two")],
            tickers={"abc-one": [ticker], "abc-two": [ticker]},
        )
        out = enrich_analysis_report(
            report("ABC-EUR"),
            bitvavo_client=FakeBitvavo([{"symbol": "ABC", "networks": ["Ethereum"]}]),
            coingecko_client=cg,
            defillama_client=FakeDefiLlama(),
            coingecko_min_interval_seconds=0,
        )
        row = out["candidates"][0]
        self.assertEqual(row["identity_status"], "unresolved")
        self.assertEqual(row["identity_reason"], "identity_probe_ambiguous_market_tickers")
        self.assertEqual(row["identity_resolution"]["status"], "unresolved")

    def test_identity_requires_explicit_mapping_and_contract_must_match(self):
        cg = FakeCoinGecko(
            [coin("abc-one", platforms={"ethereum": "0xabc"})], {"abc-one": details("abc-one")}
        )
        result = enrich_analysis_report(
            report("ABC-EUR"),
            bitvavo_client=FakeBitvavo(),
            coingecko_client=cg,
            defillama_client=FakeDefiLlama(),
            explicit_mappings={
                "ABC-EUR": {
                    "coingecko_id": "abc-one",
                    "network": "ethereum",
                    "contract_address": "0xdef",
                }
            },
            coingecko_min_interval_seconds=0,
        )
        self.assertEqual(
            result["candidates"][0]["identity_reason"], "explicit_mapping_contract_mismatch"
        )
        self.assertEqual(cg.requested, [])

    def test_exact_explicit_match_keeps_missing_values_absent_and_links_exact_protocol(self):
        cg = FakeCoinGecko(
            [coin("abc-one", platforms={"ethereum": "0xabc"})],
            {"abc-one": details("abc-one", market_cap=12, supply=50)},
        )
        llama = FakeDefiLlama(
            [{"id": "llama-id", "slug": "proto", "symbol": "XYZ"}],
            {
                "proto": {
                    "name": "Protocol",
                    "tvl": [{"date": 1794000000, "totalLiquidityUSD": 100}],
                }
            },
        )
        source = report("ABC-EUR")
        mappings = {
            "ABC-EUR": {
                "coingecko_id": "abc-one",
                "network": "ethereum",
                "contract_address": "0xabc",
                "defillama_slug": "proto",
            }
        }
        result = enrich_analysis_report(
            source,
            bitvavo_client=FakeBitvavo(
                [{"symbol": "ABC", "name": "ABC Token", "networks": ["Ethereum"]}]
            ),
            coingecko_client=cg,
            defillama_client=llama,
            explicit_mappings=mappings,
            now_provider=lambda: datetime(2026, 11, 9, tzinfo=timezone.utc),
            coingecko_min_interval_seconds=0,
        )
        row = result["candidates"][0]
        self.assertEqual(row["identity_status"], "resolved_explicit")
        self.assertEqual(row["observations"]["market_cap"]["value"], 12)
        self.assertEqual(row["observations"]["market_cap"]["unit"], "EUR")
        self.assertIsNone(row["observations"]["total_supply"]["value"])
        self.assertEqual(row["observations"]["total_supply"]["availability"], "unavailable")
        self.assertEqual(row["observations"]["total_supply"]["reason"], "source_field_absent")
        self.assertEqual(
            row["observations"]["description"]["content_trust"], "untrusted_external_data"
        )
        self.assertEqual(row["protocol"]["link_basis"], "explicit_verified_mapping_slug")
        self.assertEqual(row["protocol"]["tvl"]["unit"], "USD")
        self.assertEqual(source["candidates"][0]["market"], "ABC-EUR")
        self.assertEqual(mappings["ABC-EUR"]["coingecko_id"], "abc-one")

    def test_protocol_symbol_alone_does_not_link_and_errors_are_partial(self):
        cg = FakeCoinGecko([coin("abc-one")], {"abc-one": details("abc-one")})
        llama = FakeDefiLlama([{"slug": "unrelated", "symbol": "ABC"}])
        out = enrich_analysis_report(
            report("ABC-EUR", "DEF-EUR"),
            bitvavo_client=FakeBitvavo(),
            coingecko_client=cg,
            defillama_client=llama,
            explicit_mappings={"ABC-EUR": {"coingecko_id": "abc-one"}},
            coingecko_min_interval_seconds=0,
        )
        self.assertEqual(out["candidates"][0]["protocol"]["availability"], "unavailable")
        bad = enrich_analysis_report(
            report("ABC-EUR", "DEF-EUR"),
            bitvavo_client=FakeBitvavo(error="offline"),
            coingecko_client=FakeCoinGecko(error="offline"),
            defillama_client=FakeDefiLlama(error="offline"),
            coingecko_min_interval_seconds=0,
        )
        self.assertEqual(len(bad["candidates"]), 2)
        self.assertIn("provider_errors", bad["candidates"][1])

    def test_coingecko_rate_gate_waits_between_calls(self):
        cg = FakeCoinGecko([coin("abc-one")], {"abc-one": details("abc-one")})
        waits = []
        enrich_analysis_report(
            report("ABC-EUR"),
            bitvavo_client=FakeBitvavo(),
            coingecko_client=cg,
            defillama_client=FakeDefiLlama(),
            explicit_mappings={"ABC-EUR": {"coingecko_id": "abc-one"}},
            sleep_provider=waits.append,
        )
        self.assertEqual(len(waits), 1)
        self.assertGreaterEqual(waits[0], 11.9)

    def test_explicit_network_must_match_bitvavo_supported_network(self):
        cg = FakeCoinGecko(
            [coin("abc-one", platforms={"ethereum": "0xabc"})],
            {"abc-one": details("abc-one")},
        )
        result = enrich_analysis_report(
            report("ABC-EUR"),
            bitvavo_client=FakeBitvavo([{"symbol": "ABC", "networks": ["BASE"]}]),
            coingecko_client=cg,
            defillama_client=FakeDefiLlama(),
            explicit_mappings={
                "ABC-EUR": {
                    "coingecko_id": "abc-one",
                    "network": "ethereum",
                    "contract_address": "0xabc",
                }
            },
            coingecko_min_interval_seconds=0,
        )
        self.assertEqual(
            result["candidates"][0]["identity_reason"],
            "explicit_mapping_network_not_supported_by_bitvavo",
        )

    def test_generated_timestamp_follows_market_data_observation(self):
        current = datetime(2026, 11, 9, tzinfo=timezone.utc)

        def advance_clock():
            nonlocal current
            current += timedelta(seconds=1)
            return current

        result = enrich_analysis_report(
            report("ABC-EUR"),
            bitvavo_client=FakeBitvavo(),
            coingecko_client=FakeCoinGecko(
                [coin("abc-one")], {"abc-one": details("abc-one", market_cap=1)}
            ),
            defillama_client=FakeDefiLlama(),
            explicit_mappings={"ABC-EUR": {"coingecko_id": "abc-one"}},
            now_provider=advance_clock,
            coingecko_min_interval_seconds=0,
        )
        collected = datetime.fromisoformat(
            result["candidates"][0]["observations"]["market_cap"]["collected_at"]
        )
        generated = datetime.fromisoformat(result["generated_at"])
        self.assertGreaterEqual(generated, collected)


class EnrichmentCliTests(unittest.TestCase):
    def test_cli_writes_separate_json_and_refuses_overwrite(self):
        from crypto_market_agents import cli

        with tempfile.TemporaryDirectory() as tmp:
            source = Path(tmp) / "analysis.json"
            destination = Path(tmp) / "dossier.json"
            source.write_text(json.dumps(report("ABC-EUR")), encoding="utf-8")
            dossier = {"candidates": [], "llm_used": False}
            with (
                patch.object(cli.BitvavoAssetClient, "from_env"),
                patch.object(cli.CoinGeckoClient, "from_free_public_api"),
                patch.object(cli.DefiLlamaClient, "from_env"),
                patch.object(cli, "enrich_analysis_report", return_value=dossier),
            ):
                self.assertEqual(
                    cli.main(
                        ["enrich", "--analysis-json", str(source), "--json", str(destination)]
                    ),
                    0,
                )
                self.assertEqual(json.loads(destination.read_text(encoding="utf-8")), dossier)
                self.assertEqual(
                    cli.main(
                        ["enrich", "--analysis-json", str(source), "--json", str(destination)]
                    ),
                    1,
                )


if __name__ == "__main__":
    unittest.main()
