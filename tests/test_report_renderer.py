from pathlib import Path
import json
import sys
import tempfile
import unittest


PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from crypto_market_agents.agents.final_synthesis_agent import FinalSynthesisAgent
from crypto_market_agents.reporting.report_renderer import (
    render_final_report_html,
    render_final_report_json,
    render_final_report_markdown,
    save_html_report,
    save_json_report,
    save_markdown_report,
)
from crypto_market_agents.schemas import (
    AgentReport,
    AgentStatus,
    FinalReport,
    Finding,
    ImpactDirection,
    RiskLevel,
)


class ReportRendererTests(unittest.TestCase):
    def test_render_final_report_markdown(self):
        final_report = sample_final_report()

        markdown = render_final_report_markdown(final_report)

        self.assertIn("# Crypto Market Agents - Rapport final", markdown)
        self.assertIn("## Risque global", markdown)
        self.assertIn("Confiance:", markdown)
        self.assertIn("Analyse informative uniquement, pas un conseil financier", markdown)
        self.assertIn("price_market_agent", markdown)

    def test_render_final_report_html(self):
        final_report = sample_final_report()

        html = render_final_report_html(final_report)

        self.assertIn("<!doctype html>", html)
        self.assertIn("<style>", html)
        self.assertIn("Crypto Market Agents - Rapport final", html)
        self.assertIn("Risque medium", html)
        self.assertIn("Confiance", html)
        self.assertIn("Prix globalement stables", html)
        self.assertIn("price_market_agent", html)
        self.assertIn("Synthese globale", html)
        self.assertIn("metric-card", html)
        self.assertIn("confidence-bar", html)
        self.assertIn("Repartition des risques par agent", html)
        self.assertIn("Confidence par agent", html)
        self.assertIn("Findings cles par niveau de risque", html)
        self.assertIn("Analyse informative uniquement, pas un conseil financier", html)

    def test_render_final_report_html_contains_visual_summary_metrics(self):
        final_report = visual_final_report()

        html = render_final_report_html(final_report)

        self.assertIn("Synthese globale", html)
        self.assertIn("Risque global", html)
        self.assertIn("Findings cles", html)
        self.assertIn("Assets / protocoles", html)
        self.assertIn("Warnings", html)
        self.assertIn("metric-grid", html)
        self.assertIn("risk-high", html)

    def test_render_final_report_html_contains_global_confidence_bar(self):
        final_report = visual_final_report(confidence=0.72)

        html = render_final_report_html(final_report)

        self.assertIn("Confiance globale", html)
        self.assertIn("confidence-bar", html)
        self.assertIn('aria-label="Confiance globale 72%"', html)
        self.assertIn('style="width: 72%"', html)

    def test_render_final_report_html_contains_agent_risk_distribution(self):
        final_report = visual_final_report()

        html = render_final_report_html(final_report)

        self.assertIn("Repartition des risques par agent", html)
        self.assertIn("risk-distribution", html)
        self.assertIn(">low</span>", html)
        self.assertIn(">medium</span>", html)
        self.assertIn(">high</span>", html)
        self.assertIn(">critical</span>", html)
        self.assertIn('class="risk-badge risk-critical"', html)

    def test_render_final_report_html_contains_agent_confidence_cards(self):
        final_report = visual_final_report()

        html = render_final_report_html(final_report)

        self.assertIn("Confidence par agent", html)
        self.assertIn("agent-card", html)
        self.assertIn("price_market_agent", html)
        self.assertIn("volatility_risk_agent", html)
        self.assertIn("Statut: success", html)
        self.assertIn('aria-label="price_market_agent 80%"', html)
        self.assertIn('aria-label="volatility_risk_agent 65%"', html)

    def test_render_final_report_html_contains_risk_css_classes(self):
        html = render_final_report_html(visual_final_report())

        self.assertIn(".risk-low", html)
        self.assertIn(".risk-medium", html)
        self.assertIn(".risk-high", html)
        self.assertIn(".risk-critical", html)

    def test_render_final_report_html_groups_findings_by_risk(self):
        final_report = visual_final_report()

        html = render_final_report_html(final_report)

        self.assertIn("Findings cles par niveau de risque", html)
        self.assertIn("finding-group", html)
        self.assertIn("finding-card risk-critical", html)
        self.assertIn("Hack critique", html)
        self.assertIn("Signal positif", html)

    def test_render_final_report_json(self):
        final_report = sample_final_report()

        payload = json.loads(render_final_report_json(final_report))

        self.assertEqual(payload["title"], "Crypto Market Agents - Rapport final")
        self.assertEqual(payload["global_risk_level"], "medium")
        self.assertIn("confidence", payload)
        self.assertIn("warnings", payload)
        self.assertIn("agent_reports", payload)
        self.assertIn("assets_to_watch", payload)
        self.assertEqual(
            len(payload["assets_to_watch"]),
            len(set(payload["assets_to_watch"])),
        )

    def test_save_reports_to_temp_directory(self):
        final_report = sample_final_report()

        with tempfile.TemporaryDirectory() as temp_dir:
            markdown_path = Path(temp_dir) / "reports" / "final.md"
            json_path = Path(temp_dir) / "reports" / "final.json"
            html_path = Path(temp_dir) / "reports" / "final.html"

            save_markdown_report(final_report, str(markdown_path))
            save_json_report(final_report, str(json_path))
            save_html_report(final_report, str(html_path))

            self.assertTrue(markdown_path.exists())
            self.assertTrue(json_path.exists())
            self.assertTrue(html_path.exists())
            self.assertIn("Rapport final", markdown_path.read_text(encoding="utf-8"))
            self.assertIn("<html", html_path.read_text(encoding="utf-8"))
            self.assertEqual(
                json.loads(json_path.read_text(encoding="utf-8"))["confidence"],
                final_report.confidence,
            )

    def test_render_final_report_html_escapes_agent_text(self):
        final_report = FinalReport(
            title="Rapport <script>alert(1)</script>",
            market_summary="Resume <b>dangereux</b>",
            cryptos_to_watch=("btc<script>",),
            important_risks=(),
            confidence_score=0.7,
            conclusion="Conclusion <img src=x onerror=alert(1)>",
            global_risk_level=RiskLevel.HIGH,
            key_findings=(
                Finding(
                    title="Finding <script>",
                    description="Description <b>non echappee</b>",
                    impact=ImpactDirection.NEUTRAL,
                ),
            ),
            warnings=("Warning <token>",),
            contradictions=("Contradiction <raw>",),
            agent_reports=(
                AgentReport(
                    agent_name="price_market_agent",
                    status=AgentStatus.SUCCESS,
                    summary="Agent summary <script>alert(2)</script>",
                    risk_level=RiskLevel.HIGH,
                    confidence=0.8,
                ),
            ),
        )

        html = render_final_report_html(final_report)

        self.assertNotIn("<script>alert(1)</script>", html)
        self.assertNotIn("<script>alert(2)</script>", html)
        self.assertNotIn("<b>dangereux</b>", html)
        self.assertNotIn("<img src=x onerror=alert(1)>", html)
        self.assertIn("&lt;script&gt;alert(1)&lt;/script&gt;", html)
        self.assertIn("Description &lt;b&gt;non echappee&lt;/b&gt;", html)
        self.assertIn("Conclusion &lt;img src=x onerror=alert(1)&gt;", html)
        self.assertIn("Analyse informative uniquement, pas un conseil financier", html)


def sample_final_report():
    report = AgentReport(
        agent_name="price_market_agent",
        status=AgentStatus.SUCCESS,
        summary="Prix globalement stables avec signal BTC.",
        risk_level=RiskLevel.MEDIUM,
        confidence=0.80,
        findings=(
            Finding(
                title="Variation notable",
                description="BTC montre une variation notable.",
                impact=ImpactDirection.NEUTRAL,
                symbols=("btc",),
            ),
        ),
    )
    return FinalSynthesisAgent().synthesize([report])


def visual_final_report(confidence=0.86):
    agent_reports = (
        AgentReport(
            agent_name="price_market_agent",
            status=AgentStatus.SUCCESS,
            summary="Prix globalement stables.",
            risk_level=RiskLevel.LOW,
            confidence=0.80,
        ),
        AgentReport(
            agent_name="volatility_risk_agent",
            status=AgentStatus.SUCCESS,
            summary="Volatilite notable.",
            risk_level=RiskLevel.MEDIUM,
            confidence=0.65,
        ),
        AgentReport(
            agent_name="news_sentiment_agent",
            status=AgentStatus.PARTIAL,
            summary="News negatives.",
            risk_level=RiskLevel.HIGH,
            confidence=0.70,
        ),
        AgentReport(
            agent_name="onchain_fundamental_agent",
            status=AgentStatus.SUCCESS,
            summary="Signal critique on-chain.",
            risk_level=RiskLevel.CRITICAL,
            confidence=0.92,
        ),
    )
    return FinalReport(
        title="Crypto Market Agents - Rapport final",
        market_summary="Marche teste avec visualisations.",
        cryptos_to_watch=("bitcoin", "ethereum"),
        important_risks=(),
        confidence_score=confidence,
        conclusion="Conclusion pedagogique sans conseil financier.",
        global_risk_level=RiskLevel.HIGH,
        key_findings=(
            Finding(
                title="Hack critique",
                description="Exploit critique detecte sur un protocole.",
                impact=ImpactDirection.BEARISH,
                symbols=("ethereum",),
                confidence_score=0.92,
                data={"risk_level": "critical"},
            ),
            Finding(
                title="Risque eleve",
                description="Liquidation importante detectee.",
                impact=ImpactDirection.BEARISH,
                symbols=("bitcoin",),
                confidence_score=0.84,
                data={"risk_level": "high"},
            ),
            Finding(
                title="Signal positif",
                description="Adoption institutionnelle en hausse.",
                impact=ImpactDirection.BULLISH,
                symbols=("bitcoin",),
                confidence_score=0.72,
                data={"risk_level": "low"},
            ),
        ),
        assets_to_watch=("bitcoin", "ethereum"),
        warnings=("Risque de volatilite.",),
        agent_reports=agent_reports,
    )


if __name__ == "__main__":
    unittest.main()
