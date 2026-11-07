"""Readable paper-portfolio snapshots, also discoverable by the existing dashboard."""

from datetime import datetime, timezone
from decimal import Decimal, ROUND_DOWN
from html import escape
import json

from crypto_market_agents.file_utils import atomic_write_text, reserve_report_paths
from crypto_market_agents.security import redact_text


def summary(portfolio) -> dict:
    first = getattr(portfolio, "first_quote", None)
    benchmark = None
    if first and portfolio.last_bid:
        ask = Decimal(first["ask"]) * (1 + portfolio.config.slippage_bps / 10000)
        step = Decimal(first["quantity_step"])
        quantity = (
            portfolio.initial_cash / (ask * (1 + portfolio.config.fee_rate)) / step
        ).to_integral_value(rounding=ROUND_DOWN) * step
        notional = quantity * ask
        if quantity >= Decimal(first["min_quantity"]) and notional >= Decimal(
            first["min_order_eur"]
        ):
            remaining = portfolio.initial_cash - notional * (1 + portfolio.config.fee_rate)
            benchmark = str(remaining + quantity * portfolio.last_bid)
    return {
        "mode": "paper",
        "market": portfolio.market,
        "source": portfolio.market_source,
        "initial_cash_eur": str(portfolio.initial_cash),
        "cash_eur": str(portfolio.cash),
        "quantity": str(portfolio.quantity),
        "equity_eur": str(portfolio.equity),
        "net_pnl_eur": str(portfolio.equity - portfolio.initial_cash),
        "realized_pnl_eur": str(portfolio.realized_pnl),
        "unrealized_pnl_eur": str(portfolio.unrealized_pnl),
        "fees_paid_eur": str(portfolio.fees_paid),
        "max_drawdown_eur": str(getattr(portfolio, "max_drawdown_eur", 0)),
        "cash_benchmark_eur": str(portfolio.initial_cash),
        "buy_hold_benchmark_eur": benchmark,
        "fills": sum(e["status"] == "executed" for e in portfolio.ledger),
        "decisions": len(portfolio.ledger),
        "last_observed_at": portfolio.history[-1]["timestamp"] if portfolio.history else None,
    }


def export_report(portfolio, output_dir) -> tuple:
    metrics = summary(portfolio)
    warning = (
        "SIMULATION — aucun argent reel, aucun ordre envoye. "
        "Les frais et le glissement sont des hypotheses; les gains futurs ne sont pas garantis. "
        "Valorisation au dernier bid observe, sans frais de liquidation hypothetique."
    )
    labels = {
        "mode": "Mode",
        "market": "Marche",
        "source": "Source des cours",
        "initial_cash_eur": "Capital initial (EUR)",
        "cash_eur": "Euros disponibles",
        "quantity": "Quantite detenue",
        "equity_eur": "Valeur du portefeuille (EUR)",
        "net_pnl_eur": "Resultat net (EUR)",
        "realized_pnl_eur": "Resultat realise (EUR)",
        "unrealized_pnl_eur": "Resultat latent (EUR)",
        "fees_paid_eur": "Frais cumules (EUR)",
        "max_drawdown_eur": "Baisse maximale depuis un sommet (EUR)",
        "cash_benchmark_eur": "Temoin : conserver les euros",
        "buy_hold_benchmark_eur": "Temoin : acheter puis conserver",
        "fills": "Operations simulees",
        "decisions": "Decisions examinees",
        "last_observed_at": "Derniere observation des cours",
    }
    findings = [
        {"title": labels[key], "description": str(value), "impact": "neutral"}
        for key, value in metrics.items()
        if value is not None
    ]
    payload = {
        "title": f"Portefeuille simule — {portfolio.market} ({portfolio.market_source})",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "market_summary": f"Capital {portfolio.equity:.4f} EUR ; resultat net {portfolio.equity - portfolio.initial_cash:+.4f} EUR.",
        "global_risk_level": "unknown",
        "confidence": None,
        "key_findings": findings,
        "assets_to_watch": [portfolio.market],
        "warnings": [warning],
        "metrics": metrics,
        "portfolio": portfolio.to_dict(),
    }
    markdown = f"# {payload['title']}\n\n{warning}\n\n"
    markdown += "\n".join(f"- {f['title']}: {f['description']}" for f in findings)
    rows = "".join(
        f"<tr><th>{escape(f['title'])}</th><td>{escape(f['description'])}</td></tr>"
        for f in findings
    )
    events = "".join(
        "<tr>"
        + "".join(
            f"<td>{escape(redact_text(str(event.get(k, ''))))}</td>"
            for k in (
                "timestamp",
                "provider",
                "action",
                "status",
                "notional_eur",
                "fee_eur",
                "reason",
            )
        )
        + "</tr>"
        for event in reversed(portfolio.ledger[-100:])
    )
    html = (
        '<!doctype html><html lang="fr"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">'
        f"<title>{escape(payload['title'])}</title>"
        "<style>body{font:16px system-ui;max-width:1100px;margin:40px auto;padding:0 20px;background:#101827;color:#edf3ff}"
        "table{border-collapse:collapse;width:100%;margin:24px 0}td,th{text-align:left;padding:10px;border-bottom:1px solid #354157}"
        ".notice{padding:16px;background:#20384a;border-left:4px solid #61dac8}.scroll{overflow:auto}</style>"
        f"<h1>{escape(payload['title'])}</h1><p class='notice'>{escape(warning)}</p>"
        f"<table>{rows}</table><h2>Dernieres decisions</h2><div class='scroll'><table><tr>"
        "<th>Date</th><th>Decideur</th><th>Action</th><th>Resultat</th><th>Notionnel EUR</th><th>Frais EUR</th><th>Raison</th>"
        f"</tr>{events}</table></div></html>"
    )
    base = "paper_report_" + datetime.now(timezone.utc).strftime("%Y-%m-%d_%H%M")
    with reserve_report_paths(output_dir, base) as paths:
        for path, content in zip(
            paths, (markdown, json.dumps(payload, ensure_ascii=False, indent=2), html), strict=True
        ):
            atomic_write_text(path, content)
    return paths
