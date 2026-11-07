"""Read-only, bounded snapshots for the local live dashboard."""

from datetime import datetime, timezone
from decimal import localcontext
from hashlib import sha256
from pathlib import Path

from crypto_market_agents.security import redact_mapping, redact_text
from .activity import read_activity
from .reporting import summary
from .store import PaperStore


def live_snapshot(state_path: str | Path, *, now: datetime | None = None) -> dict:
    """Observe persisted state only; never fetch quotes, run a model or trade."""
    now = now or datetime.now(timezone.utc)
    portfolio = PaperStore(state_path).read_snapshot()
    metrics = summary(portfolio)
    with localcontext() as context:
        context.prec = 60
        metrics.update(
            position_value_eur=str(portfolio.equity - portfolio.cash),
            net_pnl_percent=str(
                (portfolio.equity - portfolio.initial_cash) / portfolio.initial_cash * 100
            ),
            last_bid_eur=str(portfolio.last_bid) if portfolio.last_bid is not None else None,
        )
    observed = metrics["last_observed_at"]
    age = (now - datetime.fromisoformat(observed)).total_seconds() if observed else None
    history = [
        {"timestamp": portfolio.created_at.isoformat(), "equity_eur": str(portfolio.initial_cash)}
    ]
    history.extend(
        {"timestamp": event["timestamp"], "equity_eur": event["equity_after"]}
        for event in portfolio.ledger[-500:]
    )
    return {
        "ok": True,
        "mode": "paper",
        "server_time": now.isoformat(),
        "portfolio_name": redact_text(Path(state_path).name),
        "portfolio_id": sha256(
            f"{Path(state_path).expanduser().absolute()}\0{portfolio.created_at.isoformat()}".encode()
        ).hexdigest()[:24],
        "metrics": metrics,
        "limits": portfolio.to_dict()["config"],
        "buying_paused": portfolio.buying_paused,
        "runtime": read_activity(state_path, now=now),
        "history": history,
        "market_history": [
            {"timestamp": point["timestamp"], "bid_eur": point["bid"]}
            for point in portfolio.history[-500:]
        ],
        "decisions": [redact_mapping(event) for event in reversed(portfolio.ledger[-100:])],
        "freshness": {
            "age_seconds": age,
            "stale": age is None or age < 0 or age > portfolio.config.max_quote_age_seconds,
            "max_age_seconds": portfolio.config.max_quote_age_seconds,
        },
    }
