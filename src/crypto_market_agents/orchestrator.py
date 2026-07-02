"""Global orchestration for the Crypto Market Agents analysis flow."""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Protocol

from crypto_market_agents.agents.final_synthesis_agent import FinalSynthesisAgent
from crypto_market_agents.agents.news_sentiment_agent import NewsSentimentAgent
from crypto_market_agents.agents.onchain_fundamental_agent import (
    DEFAULT_PROTOCOL_SLUGS,
    OnchainFundamentalAgent,
)
from crypto_market_agents.agents.price_market_agent import PriceMarketAgent
from crypto_market_agents.agents.volatility_risk_agent import VolatilityRiskAgent
from crypto_market_agents.clients.coingecko_client import CoinGeckoClient
from crypto_market_agents.clients.defillama_client import DefiLlamaClient
from crypto_market_agents.clients.news_client import NewsClient
from crypto_market_agents.config import AppConfig, load_config
from crypto_market_agents.notifications.whatsapp_client import NotificationResult, WhatsAppClient
from crypto_market_agents.notifications.whatsapp_notifier import WhatsAppNotifier
from crypto_market_agents.reporting.report_renderer import (
    save_json_report,
    save_markdown_report,
)
from crypto_market_agents.schemas import AgentReport, AgentStatus, FinalReport, RiskLevel


class TextNotifier(Protocol):
    """Protocol implemented by WhatsAppNotifier and test doubles."""

    def send_final_report_summary(self, final_report: FinalReport) -> dict[str, Any]:
        """Send a final report summary."""

    def send_high_risk_alert(self, final_report: FinalReport) -> dict[str, Any]:
        """Send a high-risk alert if needed."""


@dataclass(frozen=True, slots=True)
class OrchestrationRunResult:
    """Metadata produced by one orchestrator run."""

    final_report: FinalReport
    agent_reports: tuple[AgentReport, ...]
    markdown_path: Path
    json_path: Path
    whatsapp_summary: dict[str, Any]
    whatsapp_alert: dict[str, Any]


class CryptoMarketOrchestrator:
    """Run the complete analysis flow from agents to final report files."""

    def __init__(
        self,
        *,
        config: AppConfig | None = None,
        env_file: str | Path | None = None,
        include_os_environ: bool = True,
        price_market_agent: Any | None = None,
        volatility_risk_agent: Any | None = None,
        news_sentiment_agent: Any | None = None,
        onchain_fundamental_agent: Any | None = None,
        final_synthesis_agent: FinalSynthesisAgent | None = None,
        whatsapp_notifier: TextNotifier | None = None,
        now_provider: Callable[[], datetime] | None = None,
    ) -> None:
        self.config = config or load_config(
            env_file,
            include_os_environ=include_os_environ,
        )
        self.now_provider = now_provider or datetime.now

        if any(
            agent is None
            for agent in (
                price_market_agent,
                volatility_risk_agent,
                news_sentiment_agent,
                onchain_fundamental_agent,
            )
        ):
            coingecko_client = CoinGeckoClient.from_config(self.config.coingecko)
            news_client = NewsClient.from_config(self.config.news)
            defillama_client = DefiLlamaClient.from_config(self.config.defillama)

            price_market_agent = price_market_agent or PriceMarketAgent(coingecko_client)
            volatility_risk_agent = volatility_risk_agent or VolatilityRiskAgent(
                coingecko_client
            )
            news_sentiment_agent = news_sentiment_agent or NewsSentimentAgent(
                news_client,
                default_query=self.config.news.default_query,
                max_articles=self.config.news.max_articles,
            )
            onchain_fundamental_agent = (
                onchain_fundamental_agent
                or OnchainFundamentalAgent(defillama_client)
            )

        self.price_market_agent = price_market_agent
        self.volatility_risk_agent = volatility_risk_agent
        self.news_sentiment_agent = news_sentiment_agent
        self.onchain_fundamental_agent = onchain_fundamental_agent
        self.final_synthesis_agent = final_synthesis_agent or FinalSynthesisAgent()
        self.whatsapp_notifier = whatsapp_notifier or WhatsAppNotifier(
            WhatsAppClient.from_config(self.config.whatsapp)
        )
        self.last_run: OrchestrationRunResult | None = None

    def run_full_analysis(
        self,
        *,
        coin_ids: list[str] | Sequence[str] | None = None,
        vs_currency: str = "usd",
        news_query: str | None = None,
        news_language: str = "en",
        protocol_slugs: list[str] | Sequence[str] | None = None,
        output_dir: str | Path = "reports",
        notify_whatsapp: bool = True,
    ) -> FinalReport:
        """Run every agent, synthesize the final report, and save Markdown/JSON."""

        selected_coin_ids = _clean_items(coin_ids) or self.config.watchlist
        selected_protocols = _clean_items(protocol_slugs) or DEFAULT_PROTOCOL_SLUGS
        selected_currency = str(vs_currency or self.config.base_currency).strip().lower()
        selected_news_language = str(news_language or self.config.news.language).strip().lower()

        agent_reports = (
            self._run_price_market_agent(selected_coin_ids, selected_currency),
            self._run_volatility_risk_agent(selected_coin_ids, selected_currency),
            self._run_news_sentiment_agent(
                selected_coin_ids,
                news_query,
                selected_news_language,
            ),
            self._run_onchain_fundamental_agent(selected_protocols),
        )

        final_report = self.final_synthesis_agent.synthesize(agent_reports)
        markdown_path, json_path = self._save_reports(final_report, output_dir)
        whatsapp_summary, whatsapp_alert = self._notify(final_report, notify_whatsapp)

        self.last_run = OrchestrationRunResult(
            final_report=final_report,
            agent_reports=agent_reports,
            markdown_path=markdown_path,
            json_path=json_path,
            whatsapp_summary=whatsapp_summary,
            whatsapp_alert=whatsapp_alert,
        )

        return final_report

    def _run_price_market_agent(
        self,
        coin_ids: tuple[str, ...],
        vs_currency: str,
    ) -> AgentReport:
        return self._safe_agent_call(
            "price_market_agent",
            lambda: self.price_market_agent.analyze(
                list(coin_ids),
                vs_currency=vs_currency,
            ),
        )

    def _run_volatility_risk_agent(
        self,
        coin_ids: tuple[str, ...],
        vs_currency: str,
    ) -> AgentReport:
        return self._safe_agent_call(
            "volatility_risk_agent",
            lambda: self.volatility_risk_agent.analyze(
                list(coin_ids),
                vs_currency=vs_currency,
            ),
        )

    def _run_news_sentiment_agent(
        self,
        coin_ids: tuple[str, ...],
        query: str | None,
        language: str,
    ) -> AgentReport:
        return self._safe_agent_call(
            "news_sentiment_agent",
            lambda: self.news_sentiment_agent.analyze(
                list(coin_ids),
                query=query,
                language=language,
            ),
        )

    def _run_onchain_fundamental_agent(
        self,
        protocol_slugs: tuple[str, ...],
    ) -> AgentReport:
        return self._safe_agent_call(
            "onchain_fundamental_agent",
            lambda: self.onchain_fundamental_agent.analyze(list(protocol_slugs)),
        )

    def _safe_agent_call(
        self,
        agent_name: str,
        callback: Callable[[], AgentReport],
    ) -> AgentReport:
        try:
            report = callback()
        except Exception as exc:  # defensive boundary for the global flow
            return _failed_agent_report(agent_name, exc)

        if not isinstance(report, AgentReport):
            return _failed_agent_report(
                agent_name,
                TypeError("agent did not return an AgentReport"),
            )

        return report

    def _save_reports(
        self,
        final_report: FinalReport,
        output_dir: str | Path,
    ) -> tuple[Path, Path]:
        target_dir = Path(output_dir)
        target_dir.mkdir(parents=True, exist_ok=True)
        timestamp = self.now_provider().strftime("%Y-%m-%d_%H%M")
        markdown_path = target_dir / f"report_{timestamp}.md"
        json_path = target_dir / f"report_{timestamp}.json"

        save_markdown_report(final_report, str(markdown_path))
        save_json_report(final_report, str(json_path))

        return markdown_path, json_path

    def _notify(
        self,
        final_report: FinalReport,
        notify_whatsapp: bool,
    ) -> tuple[dict[str, Any], dict[str, Any]]:
        if not notify_whatsapp:
            return (
                _notification_result("skipped", "WhatsApp disabled by CLI option."),
                _notification_result("skipped", "WhatsApp disabled by CLI option."),
            )

        if not self.config.whatsapp.enabled:
            return (
                _notification_result("disabled", "WhatsApp notifications are disabled."),
                _notification_result("disabled", "WhatsApp notifications are disabled."),
            )

        summary = self._safe_notification_call(
            lambda: self.whatsapp_notifier.send_final_report_summary(final_report)
        )
        alert = self._safe_notification_call(
            lambda: self.whatsapp_notifier.send_high_risk_alert(final_report)
        )

        return summary, alert

    def _safe_notification_call(
        self,
        callback: Callable[[], dict[str, Any]],
    ) -> dict[str, Any]:
        try:
            return callback()
        except Exception as exc:  # WhatsApp must not break report generation.
            return _notification_result(
                "error",
                "WhatsApp notification failed.",
                error=str(exc),
            )


def _failed_agent_report(agent_name: str, exc: Exception) -> AgentReport:
    return AgentReport(
        agent_name=agent_name,
        status=AgentStatus.FAILED,
        summary=f"Analyse {agent_name} echouee dans l'orchestrateur.",
        risk_level=RiskLevel.MEDIUM,
        confidence=0.0,
        findings=(),
        sources=(),
        errors=(str(exc),),
        data={"orchestrator_error": True},
    )


def _notification_result(
    status: str,
    message: str,
    *,
    error: str | None = None,
) -> dict[str, Any]:
    return NotificationResult(
        sent=False,
        channel="whatsapp",
        status=status,
        message=message,
        error=error,
    ).to_dict()


def _clean_items(values: Sequence[str] | None) -> tuple[str, ...]:
    if not values:
        return ()

    if isinstance(values, str):
        values = values.split(",")

    cleaned_items: list[str] = []
    seen: set[str] = set()
    for value in values:
        cleaned = str(value).strip().lower()
        if cleaned and cleaned not in seen:
            seen.add(cleaned)
            cleaned_items.append(cleaned)

    return tuple(cleaned_items)
