from datetime import datetime, timezone
import json
from pathlib import Path
import sys
import unittest


PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from crypto_market_agents.schemas import (
    AgentFinding,
    AgentName,
    AgentReport,
    AgentResult,
    AgentStatus,
    FinalReport,
    ImpactDirection,
    MarketAssetSnapshot,
    RiskLevel,
    RiskSignal,
    SchemaError,
    SentimentLabel,
    SourceReference,
    risk_level_at_or_above,
)


class SchemaTests(unittest.TestCase):
    def test_agent_result_serializes_to_json_friendly_dict(self):
        result = AgentResult(
            agent_name=AgentName.PRICE_MARKET,
            summary="Marche stable avec volume en hausse.",
            key_findings=(
                AgentFinding(
                    title="BTC tient son support",
                    description="Le prix reste proche de sa zone recente.",
                    impact=ImpactDirection.NEUTRAL,
                    symbols=("BTC", "btc"),
                    confidence_score=0.7,
                    data={"observed_at": datetime(2026, 1, 1, tzinfo=timezone.utc)},
                ),
            ),
            risks=(
                RiskSignal(
                    title="Volume fragile",
                    description="Le volume reste inferieur a sa moyenne recente.",
                    level=RiskLevel.MEDIUM,
                    symbols=("BTC",),
                    metric_name="volume_change_24h",
                    metric_value=-18.2,
                    threshold=-15,
                ),
            ),
            confidence_score=0.75,
            sources=(
                SourceReference(
                    name="CoinGecko coins markets",
                    provider="coingecko",
                    url="https://api.coingecko.com/api/v3/coins/markets",
                ),
            ),
        )

        payload = result.to_dict()

        self.assertEqual(payload["agent_name"], "price_market")
        self.assertEqual(payload["key_findings"][0]["symbols"], ["btc"])
        self.assertEqual(payload["risks"][0]["level"], "medium")
        json.dumps(payload)

    def test_agent_result_from_dict_round_trip(self):
        payload = {
            "agent_name": "volatility_risk",
            "summary": "Risque eleve sur plusieurs actifs.",
            "key_findings": [
                {
                    "title": "Forte baisse",
                    "description": "SOL recule rapidement.",
                    "impact": "bearish",
                    "symbols": ["SOL"],
                    "confidence_score": 0.8,
                }
            ],
            "risks": [
                {
                    "title": "Drawdown rapide",
                    "description": "La baisse depasse le seuil choisi.",
                    "level": "high",
                    "symbols": ["SOL"],
                }
            ],
            "confidence_score": 0.7,
            "sources": [{"name": "Computed risk rules"}],
            "generated_at": "2026-06-30T10:00:00Z",
        }

        result = AgentResult.from_dict(payload)

        self.assertEqual(result.agent_name, AgentName.VOLATILITY_RISK)
        self.assertEqual(result.key_findings[0].impact, ImpactDirection.BEARISH)
        self.assertEqual(result.highest_risk_level, RiskLevel.HIGH)
        self.assertTrue(result.has_risk_at_or_above("medium"))

    def test_probability_validation(self):
        with self.assertRaises(SchemaError):
            AgentFinding(
                title="Score invalide",
                description="Le score doit rester entre 0 et 1.",
                confidence_score=1.2,
            )

    def test_risk_threshold_comparison(self):
        self.assertTrue(risk_level_at_or_above("critical", RiskLevel.HIGH))
        self.assertFalse(risk_level_at_or_above(RiskLevel.LOW, "medium"))

    def test_market_asset_snapshot_normalizes_symbol_and_datetime(self):
        snapshot = MarketAssetSnapshot(
            asset_id="bitcoin",
            symbol="BTC",
            name="Bitcoin",
            current_price=100000,
            market_cap=2_000_000_000_000,
            total_volume=50_000_000_000,
            price_change_percentage_24h=-2.5,
            price_change_percentage_7d=4,
            last_updated="2026-06-30T08:00:00Z",
        )

        payload = snapshot.to_dict()

        self.assertEqual(snapshot.symbol, "btc")
        self.assertEqual(payload["last_updated"], "2026-06-30T08:00:00+00:00")
        json.dumps(payload)

    def test_final_report_serializes_agent_results(self):
        risk = RiskSignal(
            title="Risque de liquidation",
            description="Un mouvement rapide peut amplifier la volatilite.",
            level=RiskLevel.HIGH,
            symbols=("eth",),
        )
        agent_result = AgentResult(
            agent_name=AgentName.VOLATILITY_RISK,
            summary="Risque important detecte.",
            risks=(risk,),
            confidence_score=0.72,
        )
        report = FinalReport(
            title="Rapport marche crypto",
            market_summary="Le marche est mixte.",
            cryptos_to_watch=("BTC", "ETH"),
            important_risks=(risk,),
            confidence_score=0.68,
            conclusion="Rapport informatif, sans conseil financier direct.",
            agent_results=(agent_result,),
            contradictions=("Prix positif mais risque de volatilite eleve.",),
            sentiment=SentimentLabel.MIXED,
        )

        payload = report.to_dict()

        self.assertTrue(report.has_risk_at_or_above("high"))
        self.assertEqual(payload["sentiment"], "mixed")
        self.assertEqual(payload["agent_results"][0]["agent_name"], "volatility_risk")
        self.assertEqual(
            payload["contradictions"],
            ["Prix positif mais risque de volatilite eleve."],
        )
        json.dumps(payload)

    def test_agent_report_serializes_status_risk_and_errors(self):
        report = AgentReport(
            agent_name="price_market_agent",
            status=AgentStatus.PARTIAL,
            summary="Analyse partielle Prix & Marche.",
            risk_level=RiskLevel.MEDIUM,
            confidence=0.62,
            findings=(
                AgentFinding(
                    title="Volume eleve",
                    description="BTC affiche un volume eleve.",
                    symbols=("BTC",),
                ),
            ),
            sources=(SourceReference(name="CoinGecko coins markets"),),
            errors=("Missing fields for bitcoin: market_cap",),
        )

        payload = report.to_dict()

        self.assertEqual(payload["agent_name"], "price_market_agent")
        self.assertEqual(payload["status"], "partial")
        self.assertEqual(payload["risk_level"], "medium")
        self.assertEqual(payload["confidence"], 0.62)
        self.assertEqual(payload["errors"][0], "Missing fields for bitcoin: market_cap")
        json.dumps(payload)

    def test_agent_report_rejects_unknown_risk_level(self):
        with self.assertRaises(SchemaError):
            AgentReport(
                agent_name="price_market_agent",
                status=AgentStatus.SUCCESS,
                summary="Risk level invalide.",
                risk_level="unknown",
                confidence=0.5,
            )


if __name__ == "__main__":
    unittest.main()
