"""Explicit, atomic execution of accepted proposal-cycle plans in paper state only."""

from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone
from decimal import Decimal
import hashlib
import json
import re
from typing import Any

from .models import Quote, TradingError, checked_market
from .multi_asset import (
    FORMAT_VERSION,
    MultiAssetOrder,
    MultiAssetPaperEngine,
    MultiAssetPaperStore,
    MultiAssetPortfolio,
    _quote_reason,
    _quote_snapshot,
)
from .proposal_cycle import (
    CYCLE_SCHEMA,
    MAX_REPORT_AGE_SECONDS,
    SIZING_POLICY,
    ProposalCycleError,
    _check_age,
    _fact_freshness,
    _parse_time,
    _plan_report_fingerprint,
    _portfolio_fingerprint,
    _validate_inputs,
    _validate_single_proposal,
)

EXECUTION_SCHEMA = "proposal-cycle-execution.v1"
_ACTION_PRIORITY = {"close": 0, "reduce": 1, "buy": 2, "hold": 3, "abstain": 4}


class ProposalExecutionRejected(ValueError):
    """The frozen proposal can no longer be safely committed."""

    def __init__(self, code: str, details: dict[str, Any] | None = None):
        super().__init__(code)
        self.code = code
        self.details = details or {}


def execute_proposal_cycle(
    plan_report: dict[str, Any],
    research: dict[str, Any],
    review: dict[str, Any],
    store: MultiAssetPaperStore,
    *,
    research_sha256: str,
    review_sha256: str,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Validate and atomically apply accepted paper plans under the store lock.

    This function has no exchange client. Quotes are the exact public observations
    captured by proposal-cycle and must still satisfy its age/risk policy.
    """
    timestamp = now or datetime.now(timezone.utc)
    if timestamp.tzinfo is None or timestamp.utcoffset() is None:
        raise ProposalExecutionRejected("execution_timestamp_must_be_timezone_aware")
    if not isinstance(plan_report, dict):
        raise ProposalExecutionRejected("execution_plan_report_invalid")
    cycle_id = plan_report.get("cycle_id")
    if not isinstance(cycle_id, str) or not re.fullmatch(r"[0-9a-f]{24}", cycle_id):
        raise ProposalExecutionRejected("execution_cycle_id_invalid")
    content_sha256 = _execution_content_fingerprint(plan_report, research_sha256, review_sha256)
    try:
        with store.transaction() as portfolio:
            if portfolio.format_version != FORMAT_VERSION:
                raise ProposalExecutionRejected("execution_requires_portfolio_format_v2")
            existing = next(
                (
                    receipt
                    for receipt in portfolio.cycle_receipts
                    if receipt["cycle_id"] == cycle_id
                ),
                None,
            )
            if existing is not None:
                if existing["content_sha256"] != content_sha256:
                    raise ProposalExecutionRejected("execution_cycle_id_content_mismatch")
                replay = deepcopy(existing["result"])
                replay["replayed"] = True
                return replay

            _validate_plan_envelope(plan_report, research_sha256, review_sha256, timestamp)
            candidates, reviews, facts = _validate_inputs(
                research, review, research_sha256, timestamp
            )
            expected_portfolio_sha = plan_report["portfolio_snapshot"]["sha256"]
            actual_portfolio_sha = _portfolio_fingerprint(portfolio)
            if actual_portfolio_sha != expected_portfolio_sha:
                raise ProposalExecutionRejected(
                    "execution_portfolio_changed_since_proposal",
                    {
                        "expected_portfolio_sha256": expected_portfolio_sha,
                        "actual_portfolio_sha256": actual_portfolio_sha,
                    },
                )
            if plan_report["portfolio_snapshot"].get("format_version") != portfolio.format_version:
                raise ProposalExecutionRejected("execution_portfolio_format_changed")
            expected_policy = {
                "version": portfolio.config.version,
                "sizing_policy": SIZING_POLICY,
                **portfolio.config.to_dict(),
            }
            if plan_report.get("risk_policy") != expected_policy:
                raise ProposalExecutionRejected("execution_risk_policy_mismatch")

            rows = plan_report.get("proposals")
            if not isinstance(rows, list) or any(not isinstance(row, dict) for row in rows):
                raise ProposalExecutionRejected("execution_proposals_invalid")
            _validate_evaluation_order(plan_report, rows)
            accepted = [row for row in rows if row.get("status") == "accepted"]
            if accepted and plan_report.get("model_response_status") not in {"valid", "partial"}:
                raise ProposalExecutionRejected("execution_model_response_not_valid")
            seen_markets: set[str] = set()
            for row in rows:
                market = row.get("market")
                try:
                    checked_market(market)
                except ValueError:
                    raise ProposalExecutionRejected("execution_proposal_market_invalid") from None
                if market in seen_markets:
                    raise ProposalExecutionRejected("execution_duplicate_market_plan")
                seen_markets.add(market)

            quote_markets = {
                row["market"] for row in accepted if row.get("action") in {"buy", "reduce", "close"}
            }
            if any(row.get("action") == "buy" for row in accepted):
                quote_markets.update(
                    position.market
                    for position in portfolio.positions.values()
                    if position.quantity > 0
                )
            quotes = (
                _load_fresh_quotes(plan_report, portfolio, timestamp, quote_markets)
                if quote_markets
                else {}
            )
            quote_observations = {
                position.key: {
                    "status": "available",
                    "last_quote": _quote_snapshot(quotes[position.market]),
                    "error": None,
                }
                for position in portfolio.positions.values()
                if position.quantity > 0 and position.market in quotes
            }
            working = deepcopy(portfolio)
            engine = MultiAssetPaperEngine()
            transaction_rows: list[dict[str, Any]] = []
            ordered = sorted(
                enumerate(accepted),
                key=lambda pair: (
                    _ACTION_PRIORITY.get(pair[1].get("action"), 4),
                    pair[1].get("market", ""),
                    pair[0],
                ),
            )
            for index, plan in ordered:
                _revalidate_accepted_plan(plan, candidates, reviews, facts, working, timestamp)
                action = plan["action"]
                if action == "hold":
                    transaction_rows.append(
                        {"market": plan["market"], "action": "hold", "status": "no_action"}
                    )
                    continue
                quote = quotes.get(plan["market"])
                if quote is None:
                    raise ProposalExecutionRejected("execution_quote_unavailable")
                if action == "buy":
                    requested_amount = _required_decimal_string(
                        plan.get("requested_amount_eur"), "requested_amount_eur"
                    )
                    order = MultiAssetOrder(
                        "buy",
                        "bitvavo",
                        plan["market"],
                        f"proposal-cycle:{cycle_id}:{content_sha256[:16]}",
                        provider="proposal_cycle",
                        amount_eur=requested_amount,
                    )
                elif action == "reduce":
                    held = [
                        position
                        for position in working.positions.values()
                        if position.market == plan["market"] and position.quantity > 0
                    ]
                    if len(held) != 1:
                        raise ProposalExecutionRejected("execution_reduction_position_ambiguous")
                    requested_quantity = _required_decimal_string(
                        plan.get("requested_quantity"), "requested_quantity"
                    )
                    order = MultiAssetOrder(
                        "sell",
                        held[0].platform,
                        plan["market"],
                        f"proposal-cycle:{cycle_id}:{content_sha256[:16]}",
                        provider="proposal_cycle",
                        quantity=requested_quantity,
                    )
                elif action == "close":
                    held = [
                        position
                        for position in working.positions.values()
                        if position.market == plan["market"] and position.quantity > 0
                    ]
                    if len(held) != 1:
                        raise ProposalExecutionRejected("execution_reduction_position_ambiguous")
                    order = MultiAssetOrder(
                        "sell",
                        held[0].platform,
                        plan["market"],
                        f"proposal-cycle:{cycle_id}:{content_sha256[:16]}",
                        provider="proposal_cycle",
                        quantity=held[0].quantity,
                    )
                else:
                    raise ProposalExecutionRejected("execution_accepted_action_invalid")
                event = engine.execute(
                    working,
                    order,
                    quote,
                    f"proposal-cycle:{cycle_id}:{plan['market']}",
                    quote_observations=quote_observations,
                    now=timestamp,
                )
                if event.get("status") != "executed":
                    raise ProposalExecutionRejected(
                        "execution_paper_engine_revalidation_failed",
                        {"market": plan["market"], "reason": event.get("reason")},
                    )
                if Decimal(str(plan.get("amount_eur"))) != Decimal(
                    event["notional_eur"]
                ) or Decimal(str(plan.get("quantity"))) != Decimal(event["quantity"]):
                    raise ProposalExecutionRejected(
                        "execution_fill_differs_from_accepted_plan", {"market": plan["market"]}
                    )
                transaction_rows.append(
                    {
                        "market": plan["market"],
                        "action": action,
                        "status": "executed",
                        "decision_id": event["decision_id"],
                        "quantity": event["quantity"],
                        "notional_eur": event["notional_eur"],
                        "fee_eur": event["fee_eur"],
                        "execution_price": event["execution_price"],
                        "quote_timestamp": event["quote_snapshot"]["timestamp"],
                    }
                )

            revision_after_financial_operations = _portfolio_fingerprint(working)
            did_execute = any(row["status"] == "executed" for row in transaction_rows)
            status = (
                "committed"
                if did_execute
                else "no_accepted_operations"
                if not accepted
                else "no_transactions"
            )
            execution_result = {
                "schema_version": EXECUTION_SCHEMA,
                "cycle_id": cycle_id,
                "content_sha256": content_sha256,
                "plan_sha256": plan_report["plan_sha256"],
                "status": status,
                "committed_at": timestamp.isoformat(),
                "source_reports": {
                    "research_sha256": research_sha256,
                    "review_sha256": review_sha256,
                },
                "risk_policy_version": portfolio.config.version,
                "sizing_policy": SIZING_POLICY,
                "portfolio_revision_before": actual_portfolio_sha,
                "portfolio_revision_after_financial_operations": revision_after_financial_operations,
                "accepted_plan_count": len(accepted),
                "transaction_count": sum(row["status"] == "executed" for row in transaction_rows),
                "financial_action_executed": did_execute,
                "transactions": transaction_rows,
                "replayed": False,
            }
            receipt = {
                "cycle_id": cycle_id,
                "content_sha256": content_sha256,
                "plan_sha256": plan_report["plan_sha256"],
                "source_reports": {
                    "research_sha256": research_sha256,
                    "review_sha256": review_sha256,
                },
                "risk_policy_version": portfolio.config.version,
                "sizing_policy": SIZING_POLICY,
                "portfolio_revision_before": actual_portfolio_sha,
                "portfolio_revision_after": revision_after_financial_operations,
                "committed_at": timestamp.isoformat(),
                "status": status,
                "result": execution_result,
            }
            working.cycle_receipts.append(receipt)
            working.validate()
            _replace_mutable_state(portfolio, working)
            return execution_result
    except ProposalExecutionRejected as exc:
        return {
            "schema_version": EXECUTION_SCHEMA,
            "cycle_id": cycle_id,
            "status": "rejected",
            "reason": exc.code,
            "details": exc.details,
            "financial_action_executed": False,
            "transactions": [],
            "replayed": False,
        }
    except (ProposalCycleError, TradingError, ValueError, KeyError, TypeError) as exc:
        code = str(exc) if str(exc) and len(str(exc)) <= 120 else type(exc).__name__
        return {
            "schema_version": EXECUTION_SCHEMA,
            "cycle_id": cycle_id,
            "status": "rejected",
            "reason": f"execution_validation_failed:{code}",
            "financial_action_executed": False,
            "transactions": [],
            "replayed": False,
        }


def _validate_plan_envelope(report, research_sha256, review_sha256, now):
    if report.get("schema_version") != CYCLE_SCHEMA:
        raise ProposalExecutionRejected("execution_plan_schema_invalid")
    if report.get("technical_status") not in {"completed", "partial"}:
        raise ProposalExecutionRejected("execution_cycle_technical_status_unusable")
    if report.get("read_only") is not True or report.get("financial_action_executed") is not False:
        raise ProposalExecutionRejected("execution_plan_read_only_contract_invalid")
    if report.get("model_response_status") not in {"valid", "partial", "invalid", "not_run"}:
        raise ProposalExecutionRejected("execution_model_response_status_invalid")
    _check_age(report.get("generated_at"), now, MAX_REPORT_AGE_SECONDS, "proposal_report")
    if report.get("plan_sha256") != _plan_report_fingerprint(report):
        raise ProposalExecutionRejected("execution_plan_fingerprint_mismatch")
    sources = report.get("source_reports")
    if (
        not isinstance(sources, dict)
        or not isinstance(sources.get("research"), dict)
        or not isinstance(sources.get("review"), dict)
    ):
        raise ProposalExecutionRejected("execution_source_fingerprints_missing")
    if sources.get("research", {}).get("sha256") != research_sha256:
        raise ProposalExecutionRejected("execution_research_fingerprint_mismatch")
    if sources.get("review", {}).get("sha256") != review_sha256:
        raise ProposalExecutionRejected("execution_review_fingerprint_mismatch")


def _validate_evaluation_order(report, rows):
    expected = [{"market": row.get("market"), "action": row.get("action")} for row in rows]
    if report.get("plan_evaluation_order") != expected:
        raise ProposalExecutionRejected("execution_plan_order_mismatch")
    sorted_rows = sorted(
        enumerate(rows),
        key=lambda pair: (
            _ACTION_PRIORITY.get(pair[1].get("action"), 4),
            pair[1].get("market", ""),
            pair[0],
        ),
    )
    if rows != [row for _, row in sorted_rows]:
        raise ProposalExecutionRejected("execution_plan_order_not_deterministic")


def _load_fresh_quotes(report, portfolio, now, required_markets):
    diagnostics = report.get("quote_observations")
    if not isinstance(diagnostics, list):
        raise ProposalExecutionRejected("execution_quote_observations_invalid")
    quotes: dict[str, Quote] = {}
    seen_markets: set[str] = set()
    for observation in diagnostics:
        if not isinstance(observation, dict):
            raise ProposalExecutionRejected("execution_quote_observations_invalid")
        market = observation.get("market")
        if not isinstance(market, str):
            raise ProposalExecutionRejected("execution_quote_snapshot_invalid")
        if market in seen_markets:
            raise ProposalExecutionRejected("execution_duplicate_quote_market")
        seen_markets.add(market)
        if market not in required_markets:
            continue
        if observation.get("status") != "available":
            raise ProposalExecutionRejected(
                "execution_quote_unavailable",
                {"market": market, "reason": observation.get("reason")},
            )
        if observation.get("source") != "bitvavo_public":
            raise ProposalExecutionRejected("execution_quote_source_invalid", {"market": market})
        try:
            checked_market(market)
            for field in (
                "bid_eur",
                "ask_eur",
                "min_order_eur",
                "quantity_step",
                "min_quantity",
            ):
                if not isinstance(observation.get(field), str):
                    raise ValueError
            timestamp = _parse_time(observation.get("received_at"))
            quote = Quote(
                market,
                Decimal(observation["bid_eur"]),
                Decimal(observation["ask_eur"]),
                timestamp,
                Decimal(observation["min_order_eur"]),
                Decimal(observation["quantity_step"]),
                Decimal(observation["min_quantity"]),
            )
        except (ValueError, TypeError, KeyError, ArithmeticError):
            raise ProposalExecutionRejected("execution_quote_snapshot_invalid") from None
        reason = _quote_reason(quote, "bitvavo", market, now, portfolio.config)
        if reason:
            raise ProposalExecutionRejected(
                "execution_quote_expired_or_invalid", {"market": market, "reason": reason}
            )
        quotes[market] = quote
    missing = sorted(set(required_markets) - set(quotes))
    if missing:
        raise ProposalExecutionRejected("execution_quote_unavailable", {"markets": missing})
    return quotes


def _revalidate_accepted_plan(plan, candidates, reviews, facts, portfolio, now):
    action = plan.get("action")
    if action not in {"buy", "reduce", "close", "hold"}:
        raise ProposalExecutionRejected("execution_accepted_action_invalid")
    if action in {"buy", "reduce", "close"} and plan.get("reasons") != [
        "multi_asset_engine_preview_accepted"
    ]:
        raise ProposalExecutionRejected("execution_plan_acceptance_reason_invalid")
    if action == "hold" and plan.get("reasons") != ["no_financial_action; position unchanged"]:
        raise ProposalExecutionRejected("execution_hold_reason_invalid")
    raw = {
        key: plan.get(key)
        for key in (
            "market",
            "action",
            "references",
            "rationale",
            "horizon",
            "invalidation_conditions",
        )
    }
    validated = _validate_single_proposal(
        raw, plan["market"], candidates, reviews, facts, portfolio, now
    )
    if validated.get("validation_errors"):
        raise ProposalExecutionRejected(
            "execution_evidence_or_eligibility_revalidation_failed",
            {"market": plan["market"], "reasons": validated["validation_errors"]},
        )
    if action == "buy":
        for reference in raw["references"]:
            if _fact_freshness(facts[reference], now) != "fresh":
                raise ProposalExecutionRejected(
                    "execution_evidence_expired", {"market": plan["market"], "reference": reference}
                )


def _execution_content_fingerprint(report, research_sha256, review_sha256):
    payload = {
        "plan_report": report,
        "research_sha256": research_sha256,
        "review_sha256": review_sha256,
    }
    encoded = json.dumps(
        payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False
    ).encode()
    return hashlib.sha256(encoded).hexdigest()


def _required_decimal_string(value, name):
    if not isinstance(value, str) or len(value) > 80:
        raise ProposalExecutionRejected("execution_amount_invalid", {"field": name})
    try:
        result = Decimal(value)
    except Exception:
        raise ProposalExecutionRejected("execution_amount_invalid", {"field": name}) from None
    if not result.is_finite() or result <= 0:
        raise ProposalExecutionRejected("execution_amount_invalid", {"field": name})
    return result


def _replace_mutable_state(target: MultiAssetPortfolio, source: MultiAssetPortfolio) -> None:
    target.cash_eur = source.cash_eur
    target.fees_paid_eur = source.fees_paid_eur
    target.realized_pnl_eur = source.realized_pnl_eur
    target.positions = source.positions
    target.ledger = source.ledger
    target.cycle_receipts = source.cycle_receipts
