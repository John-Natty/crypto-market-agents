"""CLI for the separate, manual-quote multi-asset paper portfolio."""

from datetime import datetime, timezone
from decimal import Decimal, ROUND_HALF_UP
import json

from .models import Quote, TradingError
from .multi_asset import (
    MultiAssetOrder,
    MultiAssetPaperEngine,
    MultiAssetPaperStore,
    MultiAssetRiskConfig,
)
from .multi_asset_market import MultiAssetQuoteRefresher, MultiAssetQuoteStore
from .market import BitvavoMarketData


def add_multi_asset_parser(subparsers):
    parser = subparsers.add_parser(
        "paper-multi",
        help="Portefeuille fictif multi-actifs, capital partagé de 150 EUR, sans connexion exchange.",
    )
    commands = parser.add_subparsers(dest="multi_command", required=True)
    initialize = commands.add_parser("init", help="Créer un nouvel état multi-actifs versionné.")
    status = commands.add_parser("status", help="Valoriser les dernières cotations conservées.")
    status.add_argument("--json", action="store_true")
    refresh = commands.add_parser(
        "refresh", help="Rafraîchir les cours publics Bitvavo sans transaction."
    )
    migrate = commands.add_parser(
        "upgrade-for-proposal-execution",
        help="Migrer explicitement v1 vers v2 avec copie de sauvegarde, pour journaliser les cycles.",
    )
    trade = commands.add_parser(
        "trade", help="Simuler un achat ou une vente avec une cotation saisie."
    )
    trade.add_argument("--action", choices=("buy", "sell"), required=True)
    trade.add_argument(
        "--platform", required=True, help="Identifiant explicite, p. ex. bitvavo ou mock."
    )
    trade.add_argument("--market", required=True, help="Marché spot EUR, p. ex. BTC-EUR.")
    trade.add_argument("--bid", required=True)
    trade.add_argument("--ask", required=True)
    trade.add_argument("--amount-eur", help="Budget total d'achat, frais inclus.")
    trade.add_argument("--quantity", help="Quantité exacte demandée pour une vente partielle.")
    trade.add_argument("--min-order-eur", default="5")
    trade.add_argument("--min-quantity", default="0")
    trade.add_argument("--quantity-step", default="0.00000001")
    trade.add_argument("--quote-time", help="ISO 8601; défaut : heure locale de saisie.")
    trade.add_argument("--decision-id", required=True)
    trade.add_argument("--reason", default="simulation manuelle")
    trade.add_argument("--provider", default="manual")
    for command in (initialize, status, refresh, trade):
        command.add_argument("--state", default="paper/multiasset-v1.json")
    migrate.add_argument("--state", default="paper/multiasset-v1.json")
    for command in (status, refresh, trade):
        command.add_argument("--quote-state", help="Sidecar versionné des observations publiques.")
    for option in (
        "max-trade",
        "max-total-exposure",
        "max-position",
        "fee-rate",
        "slippage-bps",
        "max-spread-bps",
        "max-quote-age-seconds",
    ):
        initialize.add_argument("--" + option, default=None)


def run_multi_asset(args, *, now_provider=lambda: datetime.now(timezone.utc)) -> int:
    store = MultiAssetPaperStore(args.state)
    try:
        if args.multi_command == "init":
            defaults = MultiAssetRiskConfig()
            config = MultiAssetRiskConfig(
                max_trade_eur=Decimal(args.max_trade or defaults.max_trade_eur),
                max_total_exposure_eur=Decimal(
                    args.max_total_exposure or defaults.max_total_exposure_eur
                ),
                max_position_eur=Decimal(args.max_position or defaults.max_position_eur),
                fee_rate=Decimal(args.fee_rate or defaults.fee_rate),
                slippage_bps=Decimal(args.slippage_bps or defaults.slippage_bps),
                max_spread_bps=Decimal(args.max_spread_bps or defaults.max_spread_bps),
                max_quote_age_seconds=int(
                    args.max_quote_age_seconds or defaults.max_quote_age_seconds
                ),
            )
            portfolio = store.initialize(config)
            print(
                f"Portefeuille multi-actifs v{portfolio.format_version} créé | réserve partagée: {portfolio.cash_eur} EUR | état: {args.state}"
            )
            return 0
        if args.multi_command == "upgrade-for-proposal-execution":
            previous_version = store.load().format_version
            portfolio = store.upgrade_for_cycle_execution()
            backup = store.path.with_name(store.path.name + ".v1-backup")
            backup_status = f" | sauvegarde: {backup}" if previous_version == 1 else " | déjà en v2"
            print(
                f"Portefeuille prêt pour proposal-cycle | format: v{portfolio.format_version}{backup_status}"
            )
            return 0
        if args.multi_command == "status":
            portfolio = store.load()
            quote_store = _quote_store(args)
            observations = quote_store.load_for(portfolio)["observations"]
            valuation = portfolio.valuation(quote_observations=observations, now=now_provider())
            if args.json:
                print(
                    json.dumps(
                        {
                            "format": "paper-multiasset",
                            "format_version": portfolio.format_version,
                            "risk_policy_version": portfolio.config.version,
                            "risk_policy": portfolio.config.to_dict(),
                            **valuation,
                        },
                        ensure_ascii=False,
                        indent=2,
                    )
                )
            else:
                total = valuation["total_value_eur"]
                value = _format_eur(total) if total is not None else "incomplète"
                print(f"PAPER MULTI-ACTIFS | réserve {portfolio.cash_eur} EUR | valeur {value}")
                exposure = valuation["current_market_exposure_eur"]
                exposure_display = _format_eur(exposure) if exposure is not None else "incomplète"
                print(
                    f"Budget engagé: {_format_eur(valuation['budget_engaged_eur'])} | exposition marché: {exposure_display}"
                )
                print(
                    f"Réalisé: {_format_eur(portfolio.realized_pnl_eur)} | non réalisé: {_format_eur(valuation['unrealized_pnl_eur']) if valuation['unrealized_pnl_eur'] is not None else 'incomplet'} | frais: {_format_eur(portfolio.fees_paid_eur)} | valorisation complète: {valuation['complete']}"
                )
                for position in valuation["positions"]:
                    current = (
                        _format_eur(position["market_value_eur"])
                        if position["market_value_eur"] is not None
                        else position["status"]
                    )
                    print(
                        f"{position['key']}: coût {_format_eur(position['cost_basis_eur'])} | valeur {current}"
                    )
            return 0
        if args.multi_command == "refresh":
            quote_store = _quote_store(args)
            result = MultiAssetQuoteRefresher(
                client=BitvavoMarketData(now_provider=now_provider), now_provider=now_provider
            ).refresh(store, quote_store)
            print(json.dumps(result, ensure_ascii=False, indent=2))
            return 0 if result["status"] in {"success", "no_open_positions", "partial"} else 1
        timestamp = _parse_quote_time(args.quote_time, now_provider())
        quote = Quote(
            args.market,
            Decimal(args.bid),
            Decimal(args.ask),
            timestamp,
            Decimal(args.min_order_eur),
            Decimal(args.quantity_step),
            Decimal(args.min_quantity),
        )
        order = MultiAssetOrder(
            action=args.action,
            platform=args.platform,
            market=args.market,
            reason=args.reason,
            provider=args.provider,
            amount_eur=Decimal(args.amount_eur) if args.amount_eur is not None else None,
            quantity=Decimal(args.quantity) if args.quantity is not None else None,
        )
        quote_states = None
        if args.action == "buy":
            portfolio = store.load()
            quote_states = _quote_store(args).load_for(portfolio)["observations"]
        with store.transaction() as portfolio:
            event = MultiAssetPaperEngine().execute(
                portfolio,
                order,
                quote,
                args.decision_id,
                quote_observations=quote_states,
                now=now_provider(),
            )
        print(json.dumps(event, ensure_ascii=False, indent=2))
        return 0 if event["status"] == "executed" or event.get("duplicate") else 1
    except (OSError, ValueError, ArithmeticError, TradingError) as exc:
        print(f"Simulation multi-actifs impossible: {exc}")
        return 1


def _parse_quote_time(value, now):
    if value is None:
        return now
    if not isinstance(value, str):
        raise TradingError("quote-time must be ISO 8601")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise TradingError("Invalid quote-time") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise TradingError("quote-time must include a timezone")
    return parsed


def _quote_store(args):
    path = args.quote_state or f"{args.state}.quotes-v1.json"
    return MultiAssetQuoteStore(path)


def _format_eur(value):
    if value is None:
        return "inconnue"
    amount = Decimal(str(value)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    return f"{amount:.2f} EUR"
