"""Lightweight local dashboard for already generated reports."""

from __future__ import annotations

from dataclasses import dataclass
from html import escape
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, quote, urlparse

from crypto_market_agents.security import redact_text


ALLOWED_REPORT_EXTENSIONS = {".json", ".html", ".md"}
EMPTY_REPORTS_MESSAGE = (
    "Aucun rapport disponible. Lancez crypto-market-agents report --mock "
    "pour générer un rapport de démonstration."
)


class DashboardSecurityError(ValueError):
    """Raised when a requested dashboard path is not allowed."""


@dataclass(frozen=True, slots=True)
class ReportSummary:
    """Small dashboard summary for one generated report group."""

    stem: str
    display_name: str
    modified_at: float
    json_file: str | None = None
    html_file: str | None = None
    markdown_file: str | None = None
    global_risk_level: str = "unknown"
    confidence: float | None = None
    finding_count: int = 0
    asset_count: int = 0


def discover_reports(reports_dir: str | Path = "reports") -> list[ReportSummary]:
    """Return report summaries from allowed files in a reports directory."""

    base_dir = Path(reports_dir)
    if not base_dir.exists() or not base_dir.is_dir():
        return []

    grouped_files: dict[str, dict[str, Path]] = {}
    for path in base_dir.iterdir():
        if not path.is_file() or path.suffix.lower() not in ALLOWED_REPORT_EXTENSIONS:
            continue
        grouped_files.setdefault(path.stem, {})[path.suffix.lower()] = path

    summaries = [_build_summary(stem, files) for stem, files in grouped_files.items()]
    return sorted(summaries, key=lambda item: (item.modified_at, item.display_name), reverse=True)


def render_dashboard_home(reports_dir: str | Path = "reports") -> str:
    """Render the dashboard home page listing available reports."""

    reports = discover_reports(reports_dir)
    if not reports:
        body = f'<section class="card empty">{_html(EMPTY_REPORTS_MESSAGE)}</section>'
    else:
        body = _reports_table_html(reports)

    return _page(
        "Crypto Market Agents Dashboard",
        [
            '<header class="hero">',
            "<h1>Crypto Market Agents Dashboard</h1>",
            "<p>Lecture locale des rapports déjà générés dans le dossier reports/.</p>",
            "</header>",
            body,
        ],
    )


def render_report_detail(reports_dir: str | Path, filename: str) -> str:
    """Render a detail page for one JSON final report."""

    path = resolve_report_path(reports_dir, filename)
    if path.suffix.lower() != ".json":
        raise DashboardSecurityError("Only JSON reports can be opened as detail pages.")

    payload = _read_json_payload(path)
    html_file = _related_file_name(path, ".html")
    html_link = ""
    if html_file and _safe_report_exists(reports_dir, html_file):
        html_link = (
            '<p><a class="button" href="/file?file='
            f'{_html(_url_value(html_file))}">Ouvrir le rapport HTML complet</a></p>'
        )

    sections = [
        '<header class="hero">',
        '<a href="/">← Retour aux rapports</a>',
        f"<h1>{_html(payload.get('title') or path.stem)}</h1>",
        '<div class="meta-row">',
        f'<span class="risk-badge risk-{_html(_risk_value(payload))}">'
        f"Risque {_html(_risk_value(payload))}</span>",
        f'<span class="confidence">Confiance {_html(_format_confidence(payload.get("confidence")))}</span>',
        "</div>",
        html_link,
        "</header>",
        _section("Résumé du marché", f"<p>{_html(payload.get('market_summary', ''))}</p>"),
        _section("Findings clés", _findings_detail_html(payload.get("key_findings", []))),
        _section(
            "Assets et protocoles à surveiller",
            _list_html(
                _string_items(payload.get("assets_to_watch") or payload.get("cryptos_to_watch"))
            ),
        ),
        _section("Warnings", _list_html(_string_items(payload.get("warnings")))),
        _section("Contradictions", _list_html(_string_items(payload.get("contradictions")))),
        _section("Résumé par agent", _agent_detail_html(payload.get("agent_reports", []))),
    ]

    return _page("Détail rapport", sections)


def resolve_report_path(reports_dir: str | Path, filename: str) -> Path:
    """Resolve a report file while preventing path traversal."""

    raw_filename = str(filename or "").strip()
    if (
        not raw_filename
        or "/" in raw_filename
        or "\\" in raw_filename
        or raw_filename.startswith(".")
    ):
        raise DashboardSecurityError("Invalid report file name.")

    requested = Path(raw_filename)
    if requested.name != raw_filename or requested.is_absolute():
        raise DashboardSecurityError("Invalid report file path.")
    if requested.suffix.lower() not in ALLOWED_REPORT_EXTENSIONS:
        raise DashboardSecurityError("Unsupported report file extension.")

    base_dir = Path(reports_dir).resolve()
    candidate = (base_dir / requested.name).resolve()
    if candidate.parent != base_dir:
        raise DashboardSecurityError("Report file is outside reports directory.")
    if not candidate.is_file():
        raise FileNotFoundError(requested.name)

    return candidate


def read_report_file(reports_dir: str | Path, filename: str) -> tuple[str, bytes]:
    """Return an allowed report file with a safe content type."""

    path = resolve_report_path(reports_dir, filename)
    content_type = {
        ".html": "text/html; charset=utf-8",
        ".json": "application/json; charset=utf-8",
        ".md": "text/markdown; charset=utf-8",
    }[path.suffix.lower()]

    content = redact_text(path.read_text(encoding="utf-8", errors="replace"))
    return content_type, content.encode("utf-8")


def serve_dashboard(
    *,
    reports_dir: str | Path = "reports",
    host: str = "127.0.0.1",
    port: int = 8000,
) -> None:
    """Start the local dashboard HTTP server."""

    handler = make_dashboard_handler(reports_dir)
    server = ThreadingHTTPServer((host, port), handler)
    print(f"Dashboard disponible: http://{host}:{port}")
    print(f"Dossier rapports: {Path(reports_dir)}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("Dashboard interrompu proprement.")
    finally:
        server.server_close()


def make_dashboard_handler(reports_dir: str | Path) -> type[BaseHTTPRequestHandler]:
    """Return a BaseHTTPRequestHandler class bound to one reports directory."""

    class DashboardRequestHandler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            parsed = urlparse(self.path)
            query = parse_qs(parsed.query)

            try:
                if parsed.path == "/":
                    self._send_html(render_dashboard_home(reports_dir))
                    return
                if parsed.path == "/report":
                    filename = _single_query_value(query, "file")
                    self._send_html(render_report_detail(reports_dir, filename))
                    return
                if parsed.path == "/file":
                    filename = _single_query_value(query, "file")
                    content_type, body = read_report_file(reports_dir, filename)
                    self._send_bytes(200, content_type, body)
                    return
            except DashboardSecurityError as exc:
                self._send_html(_error_page("Accès refusé", str(exc)), status=403)
                return
            except FileNotFoundError:
                self._send_html(
                    _error_page("Rapport introuvable", "Le fichier demandé n'existe pas."),
                    status=404,
                )
                return
            except json.JSONDecodeError:
                self._send_html(
                    _error_page("Rapport invalide", "Le JSON du rapport est invalide."), status=422
                )
                return

            self._send_html(
                _error_page("Page introuvable", "Cette route n'existe pas."), status=404
            )

        def log_message(self, format: str, *args: Any) -> None:  # noqa: A002
            return

        def _send_html(self, body: str, *, status: int = 200) -> None:
            self._send_bytes(status, "text/html; charset=utf-8", body.encode("utf-8"))

        def _send_bytes(self, status: int, content_type: str, body: bytes) -> None:
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

    return DashboardRequestHandler


def _build_summary(stem: str, files: dict[str, Path]) -> ReportSummary:
    json_path = files.get(".json")
    payload = _read_json_payload(json_path) if json_path else {}
    modified_at = max(path.stat().st_mtime for path in files.values())
    key_findings = _list_payload(payload.get("key_findings"))
    assets = _string_items(payload.get("assets_to_watch") or payload.get("cryptos_to_watch"))

    return ReportSummary(
        stem=stem,
        display_name=str(payload.get("title") or stem),
        modified_at=modified_at,
        json_file=files.get(".json").name if files.get(".json") else None,
        html_file=files.get(".html").name if files.get(".html") else None,
        markdown_file=files.get(".md").name if files.get(".md") else None,
        global_risk_level=_risk_value(payload),
        confidence=_optional_float(payload.get("confidence")),
        finding_count=len(key_findings),
        asset_count=len(assets),
    )


def _reports_table_html(reports: list[ReportSummary]) -> str:
    rows = []
    for report in reports:
        links = _report_links_html(report)
        rows.append(
            "<tr>"
            f"<td>{_html(report.display_name)}<br><small>{_html(report.stem)}</small></td>"
            f'<td><span class="risk-badge risk-{_html(report.global_risk_level)}">'
            f"{_html(report.global_risk_level)}</span></td>"
            f"<td>{_html(_format_confidence(report.confidence))}</td>"
            f"<td>{_html(report.finding_count)}</td>"
            f"<td>{_html(report.asset_count)}</td>"
            f"<td>{links}</td>"
            "</tr>"
        )

    return "\n".join(
        [
            '<section class="card">',
            "<h2>Rapports disponibles</h2>",
            '<div class="table-wrap">',
            "<table>",
            "<thead><tr><th>Rapport</th><th>Risque</th><th>Confiance</th>"
            "<th>Findings</th><th>Assets</th><th>Liens</th></tr></thead>",
            f"<tbody>{''.join(rows)}</tbody>",
            "</table>",
            "</div>",
            "</section>",
        ]
    )


def _report_links_html(report: ReportSummary) -> str:
    links = []
    if report.json_file:
        links.append(f'<a href="/report?file={_html(_url_value(report.json_file))}">Détail</a>')
        links.append(f'<a href="/file?file={_html(_url_value(report.json_file))}">JSON</a>')
    if report.html_file:
        links.append(f'<a href="/file?file={_html(_url_value(report.html_file))}">HTML</a>')
    if report.markdown_file:
        links.append(f'<a href="/file?file={_html(_url_value(report.markdown_file))}">Markdown</a>')

    return '<span class="link-row">' + " ".join(links) + "</span>"


def _findings_detail_html(values: Any) -> str:
    findings = _list_payload(values)
    if not findings:
        return '<p class="empty">Aucun finding clé.</p>'

    items = []
    for finding in findings:
        title = finding.get("title", "Finding")
        description = finding.get("description", "")
        confidence = _format_confidence(finding.get("confidence_score"))
        items.append(
            '<article class="finding-card">'
            f"<h3>{_html(title)}</h3>"
            f"<p>{_html(description)}</p>"
            f"<small>Confiance {_html(confidence)}</small>"
            "</article>"
        )

    return '<div class="finding-grid">' + "".join(items) + "</div>"


def _agent_detail_html(values: Any) -> str:
    reports = _list_payload(values)
    if not reports:
        return '<p class="empty">Aucun rapport agent joint.</p>'

    rows = []
    for report in reports:
        risk_level = str(report.get("risk_level", "unknown"))
        rows.append(
            "<tr>"
            f"<td>{_html(report.get('agent_name', 'agent'))}</td>"
            f"<td>{_html(report.get('status', 'unknown'))}</td>"
            f'<td><span class="risk-badge risk-{_html(risk_level)}">{_html(risk_level)}</span></td>'
            f"<td>{_html(_format_confidence(report.get('confidence')))}</td>"
            f"<td>{_html(report.get('summary', ''))}</td>"
            "</tr>"
        )

    return "\n".join(
        [
            '<div class="table-wrap">',
            "<table>",
            "<thead><tr><th>Agent</th><th>Statut</th><th>Risque</th><th>Confiance</th><th>Résumé</th></tr></thead>",
            f"<tbody>{''.join(rows)}</tbody>",
            "</table>",
            "</div>",
        ]
    )


def _section(title: str, body: str) -> str:
    return "\n".join(
        [
            '<section class="card">',
            f"<h2>{_html(title)}</h2>",
            body,
            "</section>",
        ]
    )


def _list_html(values: list[str]) -> str:
    if not values:
        return '<p class="empty">Aucun élément spécifique.</p>'

    return "<ul>" + "".join(f"<li>{_html(value)}</li>" for value in values) + "</ul>"


def _page(title: str, sections: list[str]) -> str:
    return "\n".join(
        [
            "<!doctype html>",
            '<html lang="fr">',
            "<head>",
            '  <meta charset="utf-8">',
            '  <meta name="viewport" content="width=device-width, initial-scale=1">',
            f"  <title>{_html(title)}</title>",
            "  <style>",
            _styles(),
            "  </style>",
            "</head>",
            "<body>",
            '  <main class="dashboard">',
            *sections,
            "  </main>",
            "</body>",
            "</html>",
            "",
        ]
    )


def _error_page(title: str, message: str) -> str:
    return _page(
        title,
        [
            '<section class="card">',
            f"<h1>{_html(title)}</h1>",
            f"<p>{_html(message)}</p>",
            "</section>",
        ],
    )


def _read_json_payload(path: Path | None) -> dict[str, Any]:
    if path is None:
        return {}

    payload = json.loads(path.read_text(encoding="utf-8"))
    return payload if isinstance(payload, dict) else {}


def _related_file_name(path: Path, suffix: str) -> str:
    return f"{path.stem}{suffix}"


def _safe_report_exists(reports_dir: str | Path, filename: str) -> bool:
    try:
        resolve_report_path(reports_dir, filename)
    except (DashboardSecurityError, FileNotFoundError):
        return False

    return True


def _single_query_value(query: dict[str, list[str]], key: str) -> str:
    values = query.get(key)
    if not values:
        raise DashboardSecurityError(f"Missing query parameter: {key}.")

    return values[0]


def _risk_value(payload: dict[str, Any]) -> str:
    raw_level = str(payload.get("global_risk_level") or payload.get("risk_level") or "unknown")
    normalized = raw_level.strip().lower()
    return normalized if normalized in {"low", "medium", "high", "critical"} else "unknown"


def _format_confidence(value: Any) -> str:
    parsed = _optional_float(value)
    if parsed is None:
        return "n/a"

    return f"{max(0.0, min(1.0, parsed)) * 100:.0f}%"


def _optional_float(value: Any) -> float | None:
    if isinstance(value, bool) or value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _string_items(value: Any) -> list[str]:
    if not isinstance(value, list | tuple):
        return []

    return [str(item) for item in value if str(item).strip()]


def _list_payload(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, list | tuple):
        return []

    return [item for item in value if isinstance(item, dict)]


def _url_value(value: str) -> str:
    return quote(value, safe="")


def _html(value: object) -> str:
    return escape(redact_text(str(value)), quote=True)


def _styles() -> str:
    return """
    :root {
      color-scheme: light;
      --bg: #f6f7f9;
      --panel: #ffffff;
      --text: #172033;
      --muted: #5b6475;
      --line: #d9dee8;
    }
    * { box-sizing: border-box; }
    body {
      margin: 0;
      font-family: Arial, Helvetica, sans-serif;
      background: var(--bg);
      color: var(--text);
      line-height: 1.55;
    }
    .dashboard {
      width: min(1100px, calc(100% - 32px));
      margin: 0 auto;
      padding: 32px 0;
    }
    .hero, .card {
      background: var(--panel);
      border: 1px solid var(--line);
      border-radius: 8px;
      box-shadow: 0 8px 24px rgba(23, 32, 51, 0.06);
      padding: 22px;
      margin-bottom: 16px;
    }
    h1, h2, h3 { margin: 0 0 10px; line-height: 1.2; }
    p { margin: 0.35rem 0; }
    a { color: #1d4ed8; font-weight: 700; }
    .button {
      display: inline-flex;
      margin-top: 12px;
      border: 1px solid var(--line);
      border-radius: 8px;
      padding: 8px 12px;
      text-decoration: none;
      background: #f8fafc;
    }
    .empty { color: var(--muted); }
    .meta-row, .link-row {
      display: flex;
      flex-wrap: wrap;
      gap: 10px;
      align-items: center;
    }
    .risk-badge {
      display: inline-flex;
      min-height: 26px;
      align-items: center;
      border-radius: 999px;
      padding: 3px 9px;
      font-size: 0.82rem;
      font-weight: 700;
      text-transform: uppercase;
    }
    .risk-low { color: #0f766e; background: #ccfbf1; }
    .risk-medium { color: #92400e; background: #fef3c7; }
    .risk-high { color: #b91c1c; background: #fee2e2; }
    .risk-critical { color: #7f1d1d; background: #fecaca; }
    .risk-unknown { color: #374151; background: #e5e7eb; }
    .table-wrap { overflow-x: auto; }
    table {
      width: 100%;
      border-collapse: collapse;
      font-size: 0.94rem;
    }
    th, td {
      padding: 10px;
      border-bottom: 1px solid var(--line);
      text-align: left;
      vertical-align: top;
    }
    th { color: var(--muted); }
    small, .confidence { color: var(--muted); }
    .finding-grid {
      display: grid;
      grid-template-columns: repeat(auto-fit, minmax(220px, 1fr));
      gap: 12px;
    }
    .finding-card {
      border: 1px solid var(--line);
      border-radius: 8px;
      padding: 14px;
      background: #ffffff;
    }
    @media (max-width: 640px) {
      .dashboard {
        width: min(100% - 20px, 1100px);
        padding: 18px 0;
      }
      .hero, .card { padding: 18px; }
    }
    """.strip()
