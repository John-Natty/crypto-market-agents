"""CLI for persistent simulated portfolios; no real trading credentials or orders."""

from datetime import datetime, timedelta
from decimal import Decimal, InvalidOperation
import json
from pathlib import Path
import time
import uuid

from crypto_market_agents.security import redact_text
from crypto_market_agents.agents.final_synthesis_agent import EXPECTED_AGENT_NAMES
from .activity import ActivityError, ActivitySession
from .decision import OllamaDecisionAgent, ReferenceDecisionAgent, hold
from .engine import PaperEngine
from .market import BitvavoMarketData, MockMarketData, utc_now
from .models import RiskConfig, TradingError
from .reporting import export_report, summary
from .store import PaperStore


def add_paper_parser(subparsers):
    paper = subparsers.add_parser("paper", help="Portefeuille simule, sans ordre reel.")
    commands = paper.add_subparsers(dest="paper_command", required=True)
    init = commands.add_parser(
        "init", help="Creer un portefeuille fictif, sans ecraser un existant."
    )
    init.add_argument("--market", default="BTC-EUR")
    init.add_argument("--source", choices=("mock", "bitvavo"), default="mock")
    init.add_argument("--capital", default="50", help="Capital fictif EUR, maximum 50.")
    init.add_argument("--max-trade", default="10")
    init.add_argument("--max-position", default="25")
    init.add_argument("--max-loss", default="5")
    init.add_argument(
        "--fee-rate", default="0.0025", help="Hypothese de frais par execution (0.0025 = 0.25%%)."
    )
    init.add_argument(
        "--slippage-bps", default="10", help="Glissement defavorable en points de base."
    )
    run = commands.add_parser("run", help="Observer, decider et simuler; conserver le journal.")
    source = run.add_mutually_exclusive_group()
    source.add_argument("--source", choices=("mock", "bitvavo"), default=None)
    source.add_argument("--mock", dest="source", action="store_const", const="mock")
    run.add_argument("--provider", choices=("rules", "ollama"), default="rules")
    run.add_argument("--model", help="Nom d'un modele deja installe dans Ollama local.")
    run.add_argument(
        "--runs", type=int, default=1, help="Nombre de cycles; 0 continue jusqu'a Ctrl+C."
    )
    run.add_argument("--interval-seconds", type=int, default=300)
    run.add_argument("--decision-id", help="Cle de reprise idempotente, uniquement pour un cycle.")
    run.add_argument(
        "--analysis-report",
        help="Rapport JSON existant, frais et complet, comme contexte supplementaire.",
    )
    run.add_argument("--output-dir", default="reports")
    status = commands.add_parser(
        "status", help="Consulter les soldes et resultats sans appel reseau."
    )
    status.add_argument("--json", action="store_true")
    for command in (init, run, status):
        command.add_argument("--state", default="paper/portfolio.json")


def _print_status(portfolio):
    metrics = summary(portfolio)
    print(f"SIMULATION | {portfolio.market} | source: {portfolio.market_source}")
    print(
        f"Capital initial: {portfolio.initial_cash:.2f} EUR | Disponible: {portfolio.cash:.4f} EUR"
    )
    print(f"Quantite: {portfolio.quantity} | Valorisation: {portfolio.equity:.4f} EUR")
    print(
        f"Resultat net: {portfolio.equity - portfolio.initial_cash:+.4f} EUR | Frais: {portfolio.fees_paid:.4f} EUR"
    )
    print(f"Operations simulees: {metrics['fills']} | Decisions: {metrics['decisions']}")
    print("Aucun ordre reel. Valorisation au dernier cours observe.")


def _analysis_context(path, now):
    if not path:
        return None, False
    raw = Path(path).read_text(encoding="utf-8")
    if len(raw) > 1_000_000:
        raise TradingError("Rapport d'analyse trop volumineux.")
    payload = json.loads(raw)
    if not isinstance(payload, dict):
        raise TradingError("Rapport d'analyse invalide.")
    try:
        if not isinstance(payload.get("generated_at"), str):
            raise ValueError
        generated = datetime.fromisoformat(payload["generated_at"].replace("Z", "+00:00"))
        if generated.tzinfo is None or not 0 <= (now - generated).total_seconds() <= 3600:
            raise ValueError
        confidence = Decimal(str(payload["confidence"]))
        if not confidence.is_finite() or not 0 <= confidence <= 1:
            raise ValueError
        warnings = payload.get("warnings", [])
        if not isinstance(warnings, list):
            raise ValueError
        reports = payload["agent_reports"]
        if (
            not isinstance(reports, list)
            or not reports
            or not all(isinstance(r, dict) for r in reports)
        ):
            raise ValueError
    except (KeyError, ValueError, TypeError, InvalidOperation):
        raise TradingError("Rapport absent, invalide ou perime (maximum 1 heure).") from None
    risk = payload.get("global_risk_level")
    valid_names = [r.get("agent_name") for r in reports if isinstance(r.get("agent_name"), str)]
    incomplete = len(valid_names) != len(EXPECTED_AGENT_NAMES) or set(valid_names) != set(
        EXPECTED_AGENT_NAMES
    )
    agents_unusable = False
    for report in reports:
        try:
            timestamp = report["generated_at"]
            if not isinstance(timestamp, str):
                raise ValueError
            timestamp = datetime.fromisoformat(timestamp.replace("Z", "+00:00"))
            confidence_agent = Decimal(str(report["confidence"]))
            if timestamp.tzinfo is None or not 0 <= (now - timestamp).total_seconds() <= 3600:
                raise ValueError
            if not confidence_agent.is_finite() or not Decimal("0.5") <= confidence_agent <= 1:
                raise ValueError
        except (KeyError, ValueError, TypeError, InvalidOperation):
            agents_unusable = True
    risk_off = (
        not isinstance(risk, str)
        or risk not in {"low", "medium"}
        or confidence < Decimal("0.5")
        or incomplete
        or agents_unusable
        or any(r.get("status") != "success" for r in reports)
    )
    return {
        "generated_at": generated.isoformat(),
        "global_risk_level": risk,
        "confidence": str(confidence),
        "market_summary": str(payload.get("market_summary", ""))[:2000],
        "warnings": [str(x)[:500] for x in warnings[:10]],
    }, risk_off


def make_context(portfolio, quote, analysis_path=None, *, now=None):
    analysis, risk_off = _analysis_context(analysis_path, now or utc_now())
    state = portfolio.to_dict()
    context_state = {
        key: state[key]
        for key in (
            "initial_cash",
            "cash",
            "quantity",
            "cost_basis",
            "config",
            "buying_paused",
            "peak_equity",
            "max_drawdown_eur",
        )
    }
    context_state["equity_eur"] = str(portfolio.equity)
    context_state["history"] = state["history"][-48:]
    return {
        "mode": "paper",
        "market": portfolio.market,
        "portfolio": context_state,
        "quote": {
            "bid": str(quote.bid),
            "ask": str(quote.ask),
            "observed_at": quote.timestamp.isoformat(),
            "min_order_eur": str(quote.min_order_eur),
            "min_quantity": str(quote.min_quantity),
            "quantity_step": str(quote.quantity_step),
        },
        "analysis": analysis,
        "risk_off": risk_off or portfolio.buying_paused,
    }


def run_paper(
    args, *, sleep_func=time.sleep, market_factory=None, decision_factory=None, now_provider=utc_now
):
    store = PaperStore(args.state)
    try:
        if args.paper_command == "init":
            config = RiskConfig(
                max_trade_eur=Decimal(args.max_trade),
                max_position_eur=Decimal(args.max_position),
                max_loss_eur=Decimal(args.max_loss),
                fee_rate=Decimal(args.fee_rate),
                slippage_bps=Decimal(args.slippage_bps),
            )
            store.initialize(
                market=args.market,
                market_source=args.source,
                initial_cash=Decimal(args.capital),
                config=config,
            )
            _print_status(store.load())
            print(f"Etat: {args.state}")
            return 0
        if args.paper_command == "status":
            portfolio = store.load()
            if args.json:
                print(json.dumps(summary(portfolio), ensure_ascii=False, indent=2))
            else:
                _print_status(portfolio)
            return 0
        if args.runs < 0 or args.interval_seconds < 0:
            raise TradingError("Runs et intervalle doivent etre positifs ou nuls.")
        with ActivitySession(
            args.state,
            provider=args.provider,
            model=args.model,
            interval_seconds=args.interval_seconds,
            now_provider=now_provider,
        ) as activity:
            if args.runs != 1 and args.interval_seconds < 1 and args.source != "mock":
                # The persisted mock source is also permitted below after loading.
                if store.load().market_source != "mock":
                    raise TradingError(
                        "Un intervalle d'au moins une seconde est requis pour les cours publics."
                    )
            if args.decision_id and args.runs != 1:
                raise TradingError("--decision-id exige --runs 1.")
            portfolio = store.load(expected_source=args.source)
            if args.provider == "ollama" and not args.model:
                raise TradingError("--provider ollama exige --model (modele local deja installe).")
            if args.runs == 0 and args.interval_seconds < 1:
                raise TradingError("Une boucle continue exige un intervalle positif.")
            if market_factory:
                market = market_factory(portfolio)
            elif portfolio.market_source == "mock":
                market = MockMarketData(index=len(portfolio.ledger), now_provider=now_provider)
            else:
                market = BitvavoMarketData(now_provider=now_provider)
            decider = (
                decision_factory()
                if decision_factory
                else (
                    OllamaDecisionAgent(args.model)
                    if args.provider == "ollama"
                    else ReferenceDecisionAgent()
                )
            )
            print(
                f"Decideur: {args.provider}"
                + (
                    " (strategie temoin, sans IA)"
                    if args.provider == "rules"
                    else f" (IA locale: {args.model})"
                )
            )
            cycles = 0
            failed = False
            try:
                while args.runs == 0 or cycles < args.runs:
                    cycles += 1
                    activity.update("market", cycle=cycles)
                    identifier = args.decision_id or uuid.uuid4().hex
                    provider_error = None
                    try:
                        with store.transaction(expected_source=args.source) as current:
                            previous = next(
                                (e for e in current.ledger if e["decision_id"] == identifier), None
                            )
                            if previous is not None:
                                result = {**previous, "duplicate": True}
                            else:
                                quote = market.get_quote(current.market)
                                context = make_context(
                                    current, quote, args.analysis_report, now=now_provider()
                                )
                                activity.update("deciding")
                                decision = decider.decide(context)
                                if getattr(decider, "last_error", None):
                                    failed = True
                                    provider_error = decision.reason
                                if context["risk_off"] and decision.action == "buy":
                                    decision = hold(
                                        current.market,
                                        "Rapport incomplet ou risque eleve : achat bloque.",
                                        decision.provider,
                                    )
                                activity.update("executing")
                                result = PaperEngine().step(
                                    current, decision, quote, identifier, now=now_provider()
                                )
                        activity.record_result(result, error=provider_error)
                        print(
                            f"Cycle {cycles}: {result['action']} / {result['status']} — {redact_text(result['reason'])}"
                            + (" (deja traite)" if result.get("duplicate") else "")
                        )
                    except ActivityError:
                        raise
                    except (OSError, ValueError, ArithmeticError) as exc:
                        failed = True
                        activity.record_error(exc)
                        print(f"Cycle {cycles} sans execution: {redact_text(str(exc))}")
                    if args.runs == 0 or cycles < args.runs:
                        activity.update(
                            "waiting",
                            next_run_at=now_provider() + timedelta(seconds=args.interval_seconds),
                        )
                        sleep_func(args.interval_seconds)
            except KeyboardInterrupt:
                print("Simulation interrompue; le dernier cycle valide est conserve.")
            portfolio = store.load()
            _print_status(portfolio)
            paths = export_report(portfolio, args.output_dir)
            print(f"Rapport HTML: {paths[2]}")
            print(f"Journal JSON: {paths[1]}")
            return 1 if failed else 0
    except (OSError, ValueError, ArithmeticError) as exc:
        print(f"Simulation arretee: {redact_text(str(exc))}")
        return 1
