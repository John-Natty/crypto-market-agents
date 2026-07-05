from pathlib import Path
import json
import os
import sys
import tempfile
import unittest


PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from crypto_market_agents.dashboard import (
    DashboardSecurityError,
    discover_reports,
    read_report_file,
    render_dashboard_home,
    render_report_detail,
    resolve_report_path,
)


class DashboardTests(unittest.TestCase):
    def test_dashboard_lists_json_reports_with_related_links(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            reports_dir = Path(temp_dir)
            write_report(reports_dir / "report_2026-07-05_1200.json", title="Report A")
            (reports_dir / "report_2026-07-05_1200.html").write_text(
                "<html>Report A</html>",
                encoding="utf-8",
            )
            (reports_dir / "report_2026-07-05_1200.md").write_text(
                "# Report A",
                encoding="utf-8",
            )

            reports = discover_reports(reports_dir)
            html = render_dashboard_home(reports_dir)

        self.assertEqual(len(reports), 1)
        self.assertEqual(reports[0].json_file, "report_2026-07-05_1200.json")
        self.assertIn("Crypto Market Agents Dashboard", html)
        self.assertIn("Report A", html)
        self.assertIn("Détail", html)
        self.assertIn("HTML", html)
        self.assertIn("JSON", html)
        self.assertIn("Markdown", html)

    def test_dashboard_sorts_latest_report_first(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            reports_dir = Path(temp_dir)
            old_path = reports_dir / "report_old.json"
            new_path = reports_dir / "report_new.json"
            write_report(old_path, title="Old Report")
            write_report(new_path, title="New Report")
            os.utime(old_path, (1000, 1000))
            os.utime(new_path, (2000, 2000))

            reports = discover_reports(reports_dir)
            html = render_dashboard_home(reports_dir)

        self.assertEqual([report.display_name for report in reports], ["New Report", "Old Report"])
        self.assertLess(html.index("New Report"), html.index("Old Report"))

    def test_dashboard_empty_state_when_no_reports(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            html = render_dashboard_home(temp_dir)

        self.assertIn("Aucun rapport disponible", html)
        self.assertIn("crypto-market-agents report --mock", html)

    def test_dashboard_refuses_path_traversal(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            with self.assertRaises(DashboardSecurityError):
                resolve_report_path(temp_dir, "../secret.json")
            with self.assertRaises(DashboardSecurityError):
                resolve_report_path(temp_dir, "/tmp/secret.json")
            with self.assertRaises(DashboardSecurityError):
                resolve_report_path(temp_dir, ".env")

    def test_dashboard_does_not_list_files_outside_reports_or_unsupported_extensions(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            reports_dir = root / "reports"
            reports_dir.mkdir()
            write_report(reports_dir / "inside.json", title="Inside Report")
            write_report(root / "outside.json", title="Outside Report")
            (reports_dir / ".env").write_text("SECRET=bad", encoding="utf-8")
            (reports_dir / "cache.sqlite").write_text("cache", encoding="utf-8")

            html = render_dashboard_home(reports_dir)

        self.assertIn("Inside Report", html)
        self.assertNotIn("Outside Report", html)
        self.assertNotIn("SECRET=bad", html)
        self.assertNotIn("cache.sqlite", html)

    def test_dashboard_detail_escapes_dynamic_html(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            reports_dir = Path(temp_dir)
            write_report(
                reports_dir / "danger.json",
                title="Report <script>alert(1)</script>",
                market_summary="Summary <b>raw</b>",
                finding_title="Finding <img src=x onerror=alert(1)>",
            )

            html = render_report_detail(reports_dir, "danger.json")

        self.assertNotIn("<script>alert(1)</script>", html)
        self.assertNotIn("<b>raw</b>", html)
        self.assertNotIn("<img src=x onerror=alert(1)>", html)
        self.assertIn("&lt;script&gt;alert(1)&lt;/script&gt;", html)
        self.assertIn("Summary &lt;b&gt;raw&lt;/b&gt;", html)

    def test_dashboard_redacts_secrets_in_rendered_and_served_content(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            reports_dir = Path(temp_dir)
            write_report(
                reports_dir / "secret.json",
                title="Secret report",
                market_summary="News API key api_key=supersecret should be hidden.",
            )

            html = render_report_detail(reports_dir, "secret.json")
            _, raw_body = read_report_file(reports_dir, "secret.json")

        self.assertNotIn("supersecret", html)
        self.assertNotIn(b"supersecret", raw_body)
        self.assertIn("[REDACTED]", html)


def write_report(
    path: Path,
    *,
    title: str,
    market_summary: str = "Market summary.",
    finding_title: str = "Finding",
) -> None:
    payload = {
        "title": title,
        "market_summary": market_summary,
        "global_risk_level": "high",
        "confidence": 0.82,
        "key_findings": [
            {
                "title": finding_title,
                "description": "Finding description.",
                "confidence_score": 0.7,
            }
        ],
        "assets_to_watch": ["bitcoin", "ethereum"],
        "warnings": ["Warning"],
        "contradictions": [],
        "agent_reports": [
            {
                "agent_name": "price_market_agent",
                "status": "success",
                "risk_level": "low",
                "confidence": 0.8,
                "summary": "Agent summary.",
            }
        ],
    }
    path.write_text(json.dumps(payload), encoding="utf-8")


if __name__ == "__main__":
    unittest.main()
