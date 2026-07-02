"""Manual smoke test for FinalSynthesisAgent without external API calls."""

from pathlib import Path
import sys


PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from crypto_market_agents.agents.final_synthesis_agent import FinalSynthesisAgent
from crypto_market_agents.reporting.report_renderer import render_final_report_markdown
from crypto_market_agents.schemas import (
    AgentReport,
    AgentStatus,
    Finding,
    ImpactDirection,
    RiskLevel,
    SentimentLabel,
)


def main() -> None:
    reports = [
        AgentReport(
            agent_name="price_market_agent",
            status=AgentStatus.SUCCESS,
            summary="Prix en hausse moderee sur BTC et ETH.",
            risk_level=RiskLevel.MEDIUM,
            confidence=0.86,
            findings=(
                Finding(
                    title="Forte hausse 24h",
                    description="BTC affiche une hausse notable sur 24h.",
                    impact=ImpactDirection.BULLISH,
                    symbols=("btc",),
                    data={"related_assets": ["eth"]},
                ),
            ),
        ),
        AgentReport(
            agent_name="volatility_risk_agent",
            status=AgentStatus.SUCCESS,
            summary="Volatilite elevee mais pas extreme.",
            risk_level=RiskLevel.HIGH,
            confidence=0.82,
            findings=(
                Finding(
                    title="Volatilite 24h elevee",
                    description="Amplitude 24h importante sur BTC.",
                    impact=ImpactDirection.MIXED,
                    symbols=("btc",),
                ),
            ),
        ),
        AgentReport(
            agent_name="news_sentiment_agent",
            status=AgentStatus.SUCCESS,
            summary="Actualites mixtes, avec quelques signaux negatifs.",
            risk_level=RiskLevel.MEDIUM,
            confidence=0.74,
            findings=(
                Finding(
                    title="Actualite negative importante",
                    description="Signal negatif detecte autour du marche crypto.",
                    impact=ImpactDirection.BEARISH,
                    symbols=("btc", "eth"),
                ),
            ),
            data={"sentiment": SentimentLabel.MIXED.value},
        ),
        AgentReport(
            agent_name="onchain_fundamental_agent",
            status=AgentStatus.SUCCESS,
            summary="Fondamentaux DeFi globalement corrects.",
            risk_level=RiskLevel.LOW,
            confidence=0.80,
            findings=(
                Finding(
                    title="TVL elevee",
                    description="Uniswap affiche une TVL elevee.",
                    impact=ImpactDirection.NEUTRAL,
                    symbols=("uniswap",),
                ),
            ),
            data={"protocols": [{"slug": "uniswap", "name": "Uniswap"}]},
        ),
    ]

    final_report = FinalSynthesisAgent().synthesize(reports)
    print(render_final_report_markdown(final_report))


if __name__ == "__main__":
    main()
