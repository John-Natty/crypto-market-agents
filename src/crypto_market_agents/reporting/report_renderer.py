"""Markdown, JSON, and HTML renderers for FinalReport."""

from __future__ import annotations

from html import escape
import json
from pathlib import Path

from crypto_market_agents.schemas import FinalReport, RiskLevel


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


def render_final_report_html(final_report: FinalReport) -> str:
    """Render a FinalReport as a standalone, escaped HTML document."""

    risk_level = final_report.global_risk_level.value
    return "\n".join(
        [
            "<!doctype html>",
            '<html lang="fr">',
            "<head>",
            '  <meta charset="utf-8">',
            '  <meta name="viewport" content="width=device-width, initial-scale=1">',
            f"  <title>{_html(final_report.title)}</title>",
            "  <style>",
            _html_styles(),
            "  </style>",
            "</head>",
            "<body>",
            '  <main class="report">',
            '    <header class="hero">',
            f'      <p class="eyebrow">Rapport genere le {_html(final_report.generated_at.isoformat())}</p>',
            f"      <h1>{_html(final_report.title)}</h1>",
            '      <div class="meta-row">',
            f'        <span class="risk-badge risk-{_html(risk_level)}">Risque {_html(risk_level)}</span>',
            f'        <span class="confidence">Confiance {_html(_percent(final_report.confidence))}</span>',
            "      </div>",
            "    </header>",
            _section("Resume du marche", f"<p>{_html(final_report.market_summary)}</p>"),
            _section("Findings cles", _findings_html(final_report)),
            _section("Assets et protocoles a surveiller", _list_html(final_report.assets_to_watch)),
            _section("Warnings", _list_html(final_report.warnings)),
            _section("Contradictions", _list_html(final_report.contradictions)),
            _section("Resume par agent", _agent_reports_html(final_report)),
            _section("Conclusion", f"<p>{_html(final_report.conclusion)}</p>"),
            f'    <section class="card disclaimer"><strong>Disclaimer:</strong> {_html(DISCLAIMER)}</section>',
            "  </main>",
            "</body>",
            "</html>",
            "",
        ]
    )


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


def save_html_report(final_report: FinalReport, output_path: str) -> None:
    """Save an HTML final report to disk."""

    path = Path(output_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(render_final_report_html(final_report), encoding="utf-8")


def _section(title: str, body: str) -> str:
    return "\n".join(
        [
            '    <section class="card">',
            f"      <h2>{_html(title)}</h2>",
            f"      {body}",
            "    </section>",
        ]
    )


def _findings_html(final_report: FinalReport) -> str:
    if not final_report.key_findings:
        return '<p class="empty">Aucun finding cle.</p>'

    items = []
    for finding in final_report.key_findings:
        symbols = f" <span>{_html(', '.join(finding.symbols))}</span>" if finding.symbols else ""
        items.append(
            "<li>"
            f"<strong>{_html(finding.title)}</strong>{symbols}"
            f"<p>{_html(finding.description)}</p>"
            "</li>"
        )

    return f'<ul class="finding-list">{"".join(items)}</ul>'


def _agent_reports_html(final_report: FinalReport) -> str:
    if not final_report.agent_reports:
        return '<p class="empty">Aucun rapport agent joint.</p>'

    rows = []
    for report in final_report.agent_reports:
        risk_level = report.risk_level.value
        rows.append(
            "<tr>"
            f"<td>{_html(report.agent_name)}</td>"
            f"<td>{_html(report.status.value)}</td>"
            f'<td><span class="risk-badge risk-{_html(risk_level)}">{_html(risk_level)}</span></td>'
            f"<td>{_html(_percent(report.confidence))}</td>"
            f"<td>{_html(report.summary)}</td>"
            "</tr>"
        )

    return "\n".join(
        [
            '<div class="table-wrap">',
            "<table>",
            "<thead><tr><th>Agent</th><th>Statut</th><th>Risque</th><th>Confiance</th><th>Resume</th></tr></thead>",
            f"<tbody>{''.join(rows)}</tbody>",
            "</table>",
            "</div>",
        ]
    )


def _list_html(values: tuple[str, ...]) -> str:
    if not values:
        return '<p class="empty">Aucun element specifique.</p>'

    return "<ul>" + "".join(f"<li>{_html(value)}</li>" for value in values) + "</ul>"


def _html(value: object) -> str:
    return escape(str(value), quote=True)


def _percent(value: float) -> str:
    return f"{value * 100:.0f}%"


def _html_styles() -> str:
    risk_colors = {
        RiskLevel.LOW.value: ("#0f766e", "#ccfbf1"),
        RiskLevel.MEDIUM.value: ("#92400e", "#fef3c7"),
        RiskLevel.HIGH.value: ("#b91c1c", "#fee2e2"),
        RiskLevel.CRITICAL.value: ("#7f1d1d", "#fecaca"),
    }
    risk_rules = "\n".join(
        f".risk-{level} {{ color: {foreground}; background: {background}; }}"
        for level, (foreground, background) in risk_colors.items()
    )
    return f"""
    :root {{
      color-scheme: light;
      --bg: #f6f7f9;
      --panel: #ffffff;
      --text: #172033;
      --muted: #5b6475;
      --line: #d9dee8;
      --accent: #14532d;
    }}
    * {{ box-sizing: border-box; }}
    body {{
      margin: 0;
      font-family: Arial, Helvetica, sans-serif;
      background: var(--bg);
      color: var(--text);
      line-height: 1.55;
    }}
    .report {{
      width: min(1040px, calc(100% - 32px));
      margin: 0 auto;
      padding: 32px 0;
    }}
    .hero, .card {{
      background: var(--panel);
      border: 1px solid var(--line);
      border-radius: 8px;
      box-shadow: 0 8px 24px rgba(23, 32, 51, 0.06);
    }}
    .hero {{
      padding: 28px;
      margin-bottom: 18px;
    }}
    .card {{
      padding: 22px;
      margin: 16px 0;
    }}
    .eyebrow, .empty, .confidence {{
      color: var(--muted);
    }}
    .eyebrow {{
      margin: 0 0 8px;
      font-size: 0.92rem;
    }}
    h1, h2 {{
      margin: 0;
      line-height: 1.2;
    }}
    h1 {{
      font-size: clamp(1.9rem, 4vw, 3rem);
      margin-bottom: 18px;
    }}
    h2 {{
      font-size: 1.2rem;
      margin-bottom: 12px;
    }}
    p {{
      margin: 0.35rem 0 0;
    }}
    ul {{
      margin: 0;
      padding-left: 1.2rem;
    }}
    li + li {{
      margin-top: 0.5rem;
    }}
    .finding-list li {{
      margin-bottom: 0.8rem;
    }}
    .finding-list span {{
      color: var(--muted);
      font-size: 0.92rem;
    }}
    .meta-row {{
      display: flex;
      flex-wrap: wrap;
      gap: 10px;
      align-items: center;
    }}
    .risk-badge {{
      display: inline-flex;
      align-items: center;
      min-height: 28px;
      border-radius: 999px;
      padding: 4px 10px;
      font-size: 0.84rem;
      font-weight: 700;
      text-transform: uppercase;
      letter-spacing: 0;
    }}
    {risk_rules}
    .table-wrap {{
      overflow-x: auto;
    }}
    table {{
      width: 100%;
      border-collapse: collapse;
      font-size: 0.94rem;
    }}
    th, td {{
      padding: 10px;
      border-bottom: 1px solid var(--line);
      text-align: left;
      vertical-align: top;
    }}
    th {{
      color: var(--muted);
      font-weight: 700;
    }}
    .disclaimer {{
      border-color: #f59e0b;
      background: #fffbeb;
    }}
    @media (max-width: 640px) {{
      .report {{
        width: min(100% - 20px, 1040px);
        padding: 18px 0;
      }}
      .hero, .card {{
        padding: 18px;
      }}
    }}
    """.strip()
