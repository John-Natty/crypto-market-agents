"""Local smoke test for CryptoMarketOrchestrator with mocked agents."""

from __future__ import annotations

from pathlib import Path
import sys
import tempfile


PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from crypto_market_agents.agents.final_synthesis_agent import FinalSynthesisAgent
from crypto_market_agents.config import load_config
from crypto_market_agents.orchestrator import CryptoMarketOrchestrator
from crypto_market_agents.schemas import AgentReport, AgentStatus, Finding, RiskLevel


class StaticAgent:
    def __init__(self, agent_name: str, risk_level: RiskLevel) -> None:
        self.agent_name = agent_name
        self.risk_level = risk_level

    def analyze(self, *args, **kwargs) -> AgentReport:
        return AgentReport(
            agent_name=self.agent_name,
            status=AgentStatus.SUCCESS,
            summary=f"Rapport mock pour {self.agent_name}.",
            risk_level=self.risk_level,
            confidence=0.82,
            findings=(
                Finding(
                    title="Signal mock",
                    description=f"Signal de demonstration pour {self.agent_name}.",
                    symbols=("btc",),
                ),
            ),
        )


def main() -> None:
    config = load_config(PROJECT_ROOT / ".env")
    output_dir = Path(tempfile.mkdtemp(prefix="crypto-market-orchestrator-"))
    orchestrator = CryptoMarketOrchestrator(
        config=config,
        price_market_agent=StaticAgent("price_market_agent", RiskLevel.LOW),
        volatility_risk_agent=StaticAgent("volatility_risk_agent", RiskLevel.HIGH),
        news_sentiment_agent=StaticAgent("news_sentiment_agent", RiskLevel.MEDIUM),
        onchain_fundamental_agent=StaticAgent("onchain_fundamental_agent", RiskLevel.LOW),
        final_synthesis_agent=FinalSynthesisAgent(),
    )

    final_report = orchestrator.run_full_analysis(
        coin_ids=["bitcoin", "ethereum"],
        protocol_slugs=["uniswap", "aave"],
        output_dir=output_dir,
        notify_whatsapp=False,
    )
    run = orchestrator.last_run

    print("Analyse orchestrateur mock terminee.")
    print(f"Risque global: {final_report.global_risk_level.value}")
    print(f"Confidence: {final_report.confidence:.2f}")
    if run:
        print(f"Markdown: {run.markdown_path}")
        print(f"JSON: {run.json_path}")


if __name__ == "__main__":
    main()
