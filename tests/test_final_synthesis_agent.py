from pathlib import Path
import sys
import unittest


PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from crypto_market_agents.agents.final_synthesis_agent import FinalSynthesisAgent
from crypto_market_agents.schemas import (
    AgentReport,
    AgentStatus,
    Finding,
    ImpactDirection,
    RiskLevel,
    SentimentLabel,
)


EXPECTED_AGENTS = (
    "price_market_agent",
    "volatility_risk_agent",
    "news_sentiment_agent",
    "onchain_fundamental_agent",
)


class FinalSynthesisAgentTests(unittest.TestCase):
    def test_synthesize_empty_report_list_returns_low_confidence_report(self):
        final_report = FinalSynthesisAgent().synthesize([])

        self.assertEqual(final_report.global_risk_level, RiskLevel.MEDIUM)
        self.assertEqual(final_report.confidence, 0.0)
        self.assertEqual(final_report.agent_reports, ())
        self.assertTrue(final_report.warnings)

    def test_synthesize_rejects_invalid_report_items(self):
        with self.assertRaises(TypeError):
            FinalSynthesisAgent().synthesize(["not-a-report"])

    def test_synthesize_all_agents_low(self):
        reports = tuple(
            make_report(agent_name, risk_level=RiskLevel.LOW, confidence=0.90)
            for agent_name in EXPECTED_AGENTS
        )

        final_report = FinalSynthesisAgent().synthesize(reports)

        self.assertEqual(final_report.global_risk_level, RiskLevel.LOW)
        self.assertGreaterEqual(final_report.confidence, 0.85)
        self.assertEqual(len(final_report.agent_reports), 4)
        self.assertEqual(final_report.important_risks, ())

    def test_synthesize_one_high_agent_sets_high_risk(self):
        reports = (
            make_report("price_market_agent", risk_level=RiskLevel.LOW),
            make_report("volatility_risk_agent", risk_level=RiskLevel.HIGH),
            make_report("news_sentiment_agent", risk_level=RiskLevel.LOW),
            make_report("onchain_fundamental_agent", risk_level=RiskLevel.LOW),
        )

        final_report = FinalSynthesisAgent().synthesize(reports)

        self.assertEqual(final_report.global_risk_level, RiskLevel.HIGH)
        self.assertTrue(final_report.has_risk_at_or_above("high"))
        self.assertTrue(final_report.important_risks)

    def test_synthesize_one_critical_agent_sets_critical_risk(self):
        reports = (
            make_report("price_market_agent", risk_level=RiskLevel.LOW),
            make_report("volatility_risk_agent", risk_level=RiskLevel.CRITICAL),
            make_report("news_sentiment_agent", risk_level=RiskLevel.LOW),
            make_report("onchain_fundamental_agent", risk_level=RiskLevel.LOW),
        )

        final_report = FinalSynthesisAgent().synthesize(reports)

        self.assertEqual(final_report.global_risk_level, RiskLevel.CRITICAL)

    def test_synthesize_several_partial_reports_lowers_confidence(self):
        reports = (
            make_report(
                "price_market_agent",
                status=AgentStatus.PARTIAL,
                confidence=0.70,
            ),
            make_report(
                "volatility_risk_agent",
                status=AgentStatus.PARTIAL,
                confidence=0.70,
            ),
            make_report("news_sentiment_agent", confidence=0.90),
            make_report("onchain_fundamental_agent", confidence=0.90),
        )

        final_report = FinalSynthesisAgent().synthesize(reports)

        self.assertLess(final_report.confidence, 0.80)
        self.assertTrue(
            any("Analyse partielle" in warning for warning in final_report.warnings)
        )

    def test_synthesize_failed_agent_lowers_confidence_and_adds_warning(self):
        reports = (
            make_report("price_market_agent", confidence=0.90),
            make_report(
                "volatility_risk_agent",
                status=AgentStatus.FAILED,
                confidence=0.0,
                errors=("client unavailable",),
            ),
            make_report("news_sentiment_agent", confidence=0.90),
            make_report("onchain_fundamental_agent", confidence=0.90),
        )

        final_report = FinalSynthesisAgent().synthesize(reports)

        self.assertLess(final_report.confidence, 0.70)
        self.assertTrue(any("Agent en echec" in warning for warning in final_report.warnings))

    def test_synthesize_detects_simple_contradictions(self):
        reports = (
            make_report(
                "price_market_agent",
                risk_level=RiskLevel.LOW,
                findings=(
                    Finding(
                        title="Forte hausse 24h",
                        description="BTC progresse fortement.",
                        impact=ImpactDirection.BULLISH,
                        symbols=("btc",),
                    ),
                ),
            ),
            make_report(
                "volatility_risk_agent",
                risk_level=RiskLevel.HIGH,
            ),
            make_report(
                "news_sentiment_agent",
                risk_level=RiskLevel.HIGH,
                findings=(
                    Finding(
                        title="Actualite negative importante",
                        description="Signal negatif pour BTC.",
                        impact=ImpactDirection.BEARISH,
                        symbols=("btc",),
                    ),
                ),
                data={"sentiment": SentimentLabel.NEGATIVE.value},
            ),
            make_report("onchain_fundamental_agent", risk_level=RiskLevel.LOW),
        )

        final_report = FinalSynthesisAgent().synthesize(reports)

        self.assertTrue(
            any("prix positif" in contradiction for contradiction in final_report.contradictions)
        )
        self.assertTrue(
            any("Fondamentaux solides" in contradiction for contradiction in final_report.contradictions)
        )
        self.assertTrue(
            any("Risque prix faible" in contradiction for contradiction in final_report.contradictions)
        )

    def test_synthesize_extracts_assets_to_watch(self):
        reports = (
            make_report(
                "price_market_agent",
                findings=(
                    Finding(
                        title="Forte hausse 24h",
                        description="Bitcoin et Solana bougent rapidement.",
                        impact=ImpactDirection.BULLISH,
                        symbols=("BTC",),
                        data={"related_assets": ["ETH", "AAVE"]},
                    ),
                ),
            ),
            make_report(
                "onchain_fundamental_agent",
                findings=(
                    Finding(
                        title="TVL elevee",
                        description="Uniswap affiche une TVL elevee.",
                        symbols=("uniswap",),
                    ),
                ),
                data={"protocols": [{"slug": "uniswap", "name": "Uniswap"}]},
            ),
        )

        final_report = FinalSynthesisAgent().synthesize(reports)

        self.assertIn("btc", final_report.assets_to_watch)
        self.assertIn("eth", final_report.assets_to_watch)
        self.assertIn("aave", final_report.assets_to_watch)
        self.assertIn("bitcoin", final_report.assets_to_watch)
        self.assertIn("solana", final_report.assets_to_watch)
        self.assertIn("uniswap", final_report.assets_to_watch)
        self.assertEqual(
            len(final_report.assets_to_watch),
            len(set(final_report.assets_to_watch)),
        )


def make_report(
    agent_name,
    *,
    risk_level=RiskLevel.LOW,
    status=AgentStatus.SUCCESS,
    confidence=0.85,
    findings=(),
    errors=(),
    data=None,
):
    return AgentReport(
        agent_name=agent_name,
        status=status,
        summary=f"Synthese factice pour {agent_name}.",
        risk_level=risk_level,
        confidence=confidence,
        findings=findings,
        errors=errors,
        data=data or {},
    )


if __name__ == "__main__":
    unittest.main()
