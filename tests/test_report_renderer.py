from pathlib import Path
import json
import sys
import tempfile
import unittest


PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from crypto_market_agents.agents.final_synthesis_agent import FinalSynthesisAgent
from crypto_market_agents.reporting.report_renderer import (
    render_final_report_json,
    render_final_report_markdown,
    save_json_report,
    save_markdown_report,
)
from crypto_market_agents.schemas import (
    AgentReport,
    AgentStatus,
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

            save_markdown_report(final_report, str(markdown_path))
            save_json_report(final_report, str(json_path))

            self.assertTrue(markdown_path.exists())
            self.assertTrue(json_path.exists())
            self.assertIn("Rapport final", markdown_path.read_text(encoding="utf-8"))
            self.assertEqual(
                json.loads(json_path.read_text(encoding="utf-8"))["confidence"],
                final_report.confidence,
            )


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


if __name__ == "__main__":
    unittest.main()
