"""Markdown and JSON renderers for FinalReport."""

from __future__ import annotations

import json
from pathlib import Path

from crypto_market_agents.schemas import FinalReport


DISCLAIMER = "Analyse informative uniquement, pas un conseil financier."


def render_final_report_markdown(final_report: FinalReport) -> str:
    """Render a FinalReport as readable Markdown."""

    lines = [
        f"# {final_report.title}",
        "",
        f"Date de generation: {final_report.generated_at.isoformat()}",
        "",
        "## Resume du marche",
        final_report.market_summary,
        "",
        "## Risque global",
        f"- Niveau: {final_report.global_risk_level.value}",
        f"- Confiance: {final_report.confidence:.2f}",
        "",
        "## Findings cles",
    ]

    if final_report.key_findings:
        for finding in final_report.key_findings:
            lines.append(f"- {finding.title}: {finding.description}")
    else:
        lines.append("- Aucun finding cle.")

    lines.extend(["", "## Assets et protocoles a surveiller"])
    if final_report.assets_to_watch:
        for asset in final_report.assets_to_watch:
            lines.append(f"- {asset}")
    else:
        lines.append("- Aucun asset ou protocole specifique.")

    lines.extend(["", "## Warnings"])
    if final_report.warnings:
        for warning in final_report.warnings:
            lines.append(f"- {warning}")
    else:
        lines.append("- Aucun warning specifique.")

    lines.extend(["", "## Contradictions"])
    if final_report.contradictions:
        for contradiction in final_report.contradictions:
            lines.append(f"- {contradiction}")
    else:
        lines.append("- Aucune contradiction simple detectee.")

    lines.extend(["", "## Resume par agent"])
    if final_report.agent_reports:
        for report in final_report.agent_reports:
            lines.append(
                "- "
                f"{report.agent_name}: {report.status.value}, "
                f"risque {report.risk_level.value}, "
                f"confiance {report.confidence:.2f}. {report.summary}"
            )
    else:
        lines.append("- Aucun rapport agent joint.")

    lines.extend(
        ["", "## Conclusion", final_report.conclusion, "", f"**Disclaimer:** {DISCLAIMER}", ""]
    )

    return "\n".join(lines)


def render_final_report_json(final_report: FinalReport) -> str:
    """Render a FinalReport as clean JSON."""

    return json.dumps(final_report.to_dict(), ensure_ascii=False, indent=2, sort_keys=True)


def save_markdown_report(final_report: FinalReport, output_path: str) -> None:
    """Save a Markdown final report to disk."""

    path = Path(output_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(render_final_report_markdown(final_report), encoding="utf-8")


def save_json_report(final_report: FinalReport, output_path: str) -> None:
    """Save a JSON final report to disk."""

    path = Path(output_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(render_final_report_json(final_report), encoding="utf-8")
