"""Read-only, provenance-preserving asset identity and fundamentals dossiers.

CoinGecko public API contracts: /coins/list provides provider IDs and symbols;
/coins/{id}/tickers can cross-reference an exact Bitvavo spot market, and
/coins/{id} provides metadata and market_data. DefiLlama Free API /protocols
provides protocol records; a token-to-protocol link is accepted only via an
explicitly verified exact protocol slug, never a shared ticker. External descriptions remain
untrusted data. See docs/asset-fundamentals.md for endpoints and limits.
"""

from __future__ import annotations

from datetime import datetime, timezone
import time
from typing import Any


def enrich_analysis_report(
    report: dict[str, Any],
    *,
    bitvavo_client,
    coingecko_client,
    defillama_client,
    explicit_mappings: dict[str, dict[str, Any]] | None = None,
    now_provider=None,
    sleep_provider=None,
    coingecko_min_interval_seconds: float = 12.0,
    identity_probe_limit: int = 3,
) -> dict[str, Any]:
    """Create a new dossier; failures are isolated and source report is untouched."""

    now_provider = now_provider or (lambda: datetime.now(timezone.utc))
    sleep_provider = sleep_provider or time.sleep
    if coingecko_min_interval_seconds < 0:
        raise ValueError("coingecko_min_interval_seconds must be non-negative")
    if isinstance(identity_probe_limit, bool) or not 1 <= identity_probe_limit <= 5:
        raise ValueError("identity_probe_limit must be between 1 and 5")
    last_coingecko_call = None

    def coingecko_call(function, *args, **kwargs):
        nonlocal last_coingecko_call
        if last_coingecko_call is not None:
            elapsed = time.monotonic() - last_coingecko_call
            remaining = coingecko_min_interval_seconds - elapsed
            if remaining > 0:
                sleep_provider(remaining)
        result = function(*args, **kwargs)
        last_coingecko_call = time.monotonic()
        return result

    mapping = explicit_mappings or {}
    candidates = report.get("candidates")
    if not isinstance(candidates, list):
        raise ValueError("Rapport d'analyse invalide: candidates doit être une liste.")

    try:
        bitvavo_assets = bitvavo_client.get_assets()
        bitvavo_error = None
    except Exception as exc:
        bitvavo_assets = []
        bitvavo_error = _safe_error(exc)
    try:
        coin_list = coingecko_call(coingecko_client.get_coin_list, include_platform=True)
        cg_error = None
    except Exception as exc:  # retain other provider/candidate results
        coin_list = []
        cg_error = _safe_error(exc)
    try:
        protocols = defillama_client.get_protocols()
        llama_error = None
    except Exception as exc:
        protocols = []
        llama_error = _safe_error(exc)
    collected_at = now_provider().astimezone(timezone.utc).isoformat()

    output = []
    for candidate in candidates:
        scanner = candidate.get("scanner_candidate") or {}
        market = candidate.get("market") or scanner.get("market")
        symbol = scanner.get("base_asset")
        if not symbol and isinstance(market, str) and "-" in market:
            symbol = market.split("-", 1)[0]
        symbol = str(symbol or "").strip().upper()
        exchange_rows = [
            asset
            for asset in bitvavo_assets
            if isinstance(asset.get("symbol"), str) and asset["symbol"].upper() == symbol
        ]
        exchange_asset = exchange_rows[0] if len(exchange_rows) == 1 else None
        matches = [
            item
            for item in coin_list
            if isinstance(item.get("symbol"), str) and item["symbol"].upper() == symbol
        ]
        explicit = mapping.get(str(market), mapping.get(symbol))
        dossier: dict[str, Any] = {
            "market": market,
            "bitvavo_identity": {
                "name": exchange_asset.get("name") if exchange_asset else None,
                "symbol": symbol or None,
                "provider_id": symbol if exchange_asset else None,
                "network": None,
                "contract_address": None,
                "supported_networks": exchange_asset.get("networks") if exchange_asset else None,
            },
            "coingecko_identity": {
                "provider_id": None,
                "name": None,
                "symbol": None,
                "network": None,
                "contract_address": None,
            },
            "identity_observations": {
                "name": _observation(
                    exchange_asset.get("name") if exchange_asset else None,
                    "text",
                    "bitvavo",
                    collected_at,
                    reason="source_field_absent"
                    if exchange_asset
                    else "bitvavo_identity_ambiguous_or_absent",
                ),
                "symbol": _observation(
                    symbol or None,
                    "ticker",
                    "bitvavo",
                    collected_at,
                    reason="symbol_absent" if not symbol else None,
                ),
                "provider_id": _observation(
                    symbol if exchange_asset else None,
                    "Bitvavo asset symbol",
                    "bitvavo",
                    collected_at,
                    reason="bitvavo_identity_ambiguous_or_absent",
                ),
                "supported_networks": _observation(
                    exchange_asset.get("networks") or None if exchange_asset else None,
                    "network[]",
                    "bitvavo",
                    collected_at,
                    reason="source_field_absent"
                    if exchange_asset
                    else "bitvavo_identity_ambiguous_or_absent",
                ),
                "contract_address": _observation(
                    None,
                    "address",
                    "bitvavo",
                    collected_at,
                    reason="not_provided_by_bitvavo_public_asset_endpoint",
                ),
                "coingecko_provider_id": _observation(
                    None,
                    "CoinGecko coin ID",
                    "coingecko",
                    collected_at,
                    reason="identity_unresolved",
                ),
                "coingecko_network": _observation(
                    None,
                    "platform",
                    "coingecko",
                    collected_at,
                    reason="identity_unresolved",
                ),
                "coingecko_contract_address": _observation(
                    None,
                    "address",
                    "coingecko",
                    collected_at,
                    reason="identity_unresolved",
                ),
            },
            "bitvavo_asset_status": "resolved"
            if exchange_asset
            else "ambiguous"
            if len(exchange_rows) > 1
            else "unavailable",
            "identity_status": "unresolved",
            "identity_reason": "no_explicit_verified_mapping",
            "identity_resolution": {
                "status": "unresolved",
                "basis": None,
                "evidence": [],
                "reason": "no_explicit_verified_mapping",
            },
            "symbol_candidates": [
                {
                    "source": "coingecko",
                    "collected_at": collected_at,
                    "source_at": None,
                    "availability": "available",
                    "reason": None,
                    "provider": "coingecko",
                    "provider_id": item.get("id"),
                    "name": item.get("name"),
                    "symbol": item.get("symbol"),
                    "platforms": item.get("platforms") or {},
                }
                for item in matches
            ],
            "observations": _missing_coin_observations(collected_at, "identity_unresolved"),
            "protocol": {"availability": "unavailable", "reason": "no_verified_protocol_link"},
            "collected_at": collected_at,
        }
        if cg_error:
            dossier["identity_reason"] = "coingecko_lookup_error"
            dossier["provider_errors"] = {"coingecko": cg_error}
            dossier["observations"] = _missing_coin_observations(
                collected_at, "coingecko_lookup_error"
            )
        if llama_error:
            dossier.setdefault("provider_errors", {})["defillama"] = llama_error
        if bitvavo_error:
            dossier.setdefault("provider_errors", {})["bitvavo"] = bitvavo_error

        coin_id = explicit.get("coingecko_id") if isinstance(explicit, dict) else None
        ticker_proof = None
        if not explicit and not cg_error and matches:
            ticker_method = getattr(coingecko_client, "get_coin_tickers", None)
            if not callable(ticker_method):
                dossier["identity_reason"] = "identity_probe_unavailable"
            elif len(matches) > identity_probe_limit:
                dossier["identity_reason"] = "identity_probe_candidate_limit_exceeded"
            else:
                market_parts = str(market or "").split("-", 1)
                base_symbol = symbol.casefold()
                quote_symbol = market_parts[1].casefold() if len(market_parts) == 2 else None
                proven = []
                try:
                    for candidate_coin in matches:
                        coin_candidate_id = candidate_coin.get("id")
                        if not isinstance(coin_candidate_id, str) or not coin_candidate_id:
                            continue
                        ticker_payload = coingecko_call(
                            ticker_method, coin_candidate_id, exchange_ids="bitvavo", page=1
                        )
                        ticker_rows = ticker_payload.get("tickers", [])
                        matching_tickers = [
                            row
                            for row in ticker_rows
                            if isinstance(row, dict)
                            and isinstance(row.get("market"), dict)
                            and str(row["market"].get("identifier", "")).casefold() == "bitvavo"
                            and str(row.get("base", "")).casefold() == base_symbol
                            and quote_symbol is not None
                            and str(row.get("target", "")).casefold() == quote_symbol
                            and row.get("is_anomaly") is False
                            and row.get("is_stale") is False
                            and isinstance(row.get("last_traded_at"), str)
                            and bool(row["last_traded_at"])
                        ]
                        if matching_tickers:
                            proven.append(
                                {
                                    "coin": candidate_coin,
                                    "tickers": matching_tickers,
                                }
                            )
                except Exception as exc:
                    dossier["identity_reason"] = "identity_probe_error"
                    dossier.setdefault("provider_errors", {})["coingecko_tickers"] = _safe_error(
                        exc
                    )
                if len(proven) == 1:
                    proof = proven[0]
                    coin_id = proof["coin"]["id"]
                    ticker_proof = proof["tickers"][0]
                    ticker_collected_at = now_provider().astimezone(timezone.utc).isoformat()
                    ticker_source_at = ticker_proof.get("last_traded_at")
                    dossier["identity_status"] = "resolved_cross_referenced"
                    dossier["identity_reason"] = None
                    dossier["identity_resolution"] = {
                        "status": "resolved",
                        "basis": "coingecko_bitvavo_market_ticker",
                        "evidence": [
                            {
                                "source": "coingecko_coin_tickers",
                                "source_at": ticker_source_at,
                                "collected_at": ticker_collected_at,
                                "coin_id": coin_id,
                                "exchange_id": ticker_proof["market"].get("identifier"),
                                "market_name": ticker_proof["market"].get("name"),
                                "base": ticker_proof.get("base"),
                                "target": ticker_proof.get("target"),
                                "trade_url": ticker_proof.get("trade_url"),
                                "trust_score": ticker_proof.get("trust_score"),
                                "bitvavo_market": market,
                                "bitvavo_market_source": "bitvavo_scanner_market",
                                "bitvavo_market_collected_at": scanner.get("collected_at"),
                            }
                        ],
                        "reason": None,
                    }
                    platform = _matching_platform(
                        proof["coin"].get("platforms"),
                        exchange_asset.get("networks", []) if exchange_asset else [],
                    )
                    if platform:
                        dossier["coingecko_identity"].update(
                            {
                                "network": platform[0],
                                "contract_address": platform[1],
                            }
                        )
                        dossier["identity_observations"].update(
                            {
                                "coingecko_network": _observation(
                                    platform[0], "platform", "coingecko", ticker_collected_at
                                ),
                                "coingecko_contract_address": _observation(
                                    platform[1], "address", "coingecko", ticker_collected_at
                                ),
                            }
                        )
                elif len(proven) > 1:
                    dossier["identity_reason"] = "identity_probe_ambiguous_market_tickers"
                elif dossier["identity_reason"] == "no_explicit_verified_mapping":
                    dossier["identity_reason"] = "no_matching_bitvavo_market_ticker"
        if ticker_proof:
            explicit = {"coingecko_id": coin_id}
        if coin_id and not cg_error:
            coin_row = next((item for item in coin_list if item.get("id") == coin_id), None)
            contract = (
                dossier["coingecko_identity"].get("contract_address")
                if ticker_proof
                else explicit.get("contract_address")
            )
            network = (
                dossier["coingecko_identity"].get("network")
                if ticker_proof
                else explicit.get("network")
            )
            if coin_row is None:
                dossier["identity_reason"] = "explicit_provider_id_not_found"
            elif (
                not isinstance(coin_row.get("symbol"), str) or coin_row["symbol"].upper() != symbol
            ):
                dossier["identity_reason"] = "explicit_mapping_symbol_mismatch"
            elif not ticker_proof and bool(contract) != bool(network):
                dossier["identity_reason"] = "explicit_mapping_network_contract_incomplete"
            elif contract and not _platform_matches(coin_row.get("platforms"), network, contract):
                dossier["identity_reason"] = "explicit_mapping_contract_mismatch"
            elif (
                exchange_asset
                and contract
                and not _bitvavo_network_matches(exchange_asset.get("networks"), network)
            ):
                dossier["identity_reason"] = "explicit_mapping_network_not_supported_by_bitvavo"
            else:
                dossier["identity_status"] = (
                    "resolved_explicit" if not ticker_proof else "resolved_cross_referenced"
                )
                dossier["identity_reason"] = None
                if not ticker_proof:
                    dossier["identity_resolution"] = {
                        "status": "resolved",
                        "basis": "verified_explicit_mapping",
                        "evidence": [
                            {
                                "source": "data/asset-identity-mappings.json",
                                "collected_at": collected_at,
                                "coin_id": coin_id,
                                "network": network,
                                "contract_address": contract,
                            }
                        ],
                        "reason": None,
                    }
                dossier["bitvavo_identity"].update(
                    {
                        "supported_networks": exchange_asset.get("networks")
                        if exchange_asset
                        else None,
                    }
                )
                dossier["coingecko_identity"].update(
                    {
                        "provider_id": coin_id,
                        "name": coin_row.get("name"),
                        "symbol": coin_row.get("symbol"),
                        "network": network,
                        "contract_address": contract,
                    }
                )
                dossier["identity_observations"].update(
                    {
                        "coingecko_provider_id": _observation(
                            coin_id, "CoinGecko coin ID", "coingecko", collected_at
                        ),
                        "coingecko_network": _observation(
                            network, "platform", "coingecko", collected_at
                        ),
                        "coingecko_contract_address": _observation(
                            contract, "address", "coingecko", collected_at
                        ),
                    }
                )
                try:
                    details = coingecko_call(coingecko_client.get_coin, coin_id, market_data=True)
                    details_collected_at = now_provider().astimezone(timezone.utc).isoformat()
                    dossier["observations"].update(
                        _coin_observations(details, details_collected_at)
                    )
                except Exception as exc:
                    dossier.setdefault("provider_errors", {})["coingecko_details"] = _safe_error(
                        exc
                    )
                    dossier["observations"].update(
                        _missing_coin_observations(
                            now_provider().astimezone(timezone.utc).isoformat(),
                            "coingecko_details_error",
                        )
                    )
                if not llama_error:
                    slug = explicit.get("defillama_slug")
                    linked = [p for p in protocols if p.get("slug") == slug] if slug else []
                    if len(linked) == 1:
                        protocol = linked[0]
                        if isinstance(slug, str) and slug:
                            try:
                                details = defillama_client.get_protocol(slug)
                                protocol_collected_at = (
                                    now_provider().astimezone(timezone.utc).isoformat()
                                )
                                tvl = details.get("tvl")
                                if isinstance(tvl, list):
                                    tvl = next(
                                        (
                                            r.get("totalLiquidityUSD")
                                            for r in reversed(tvl)
                                            if isinstance(r, dict)
                                            and r.get("totalLiquidityUSD") is not None
                                        ),
                                        None,
                                    )
                                dossier["protocol"] = {
                                    "availability": "available"
                                    if _is_number(tvl)
                                    else "unavailable",
                                    "reason": None if _is_number(tvl) else "tvl_absent",
                                    "provider": "defillama",
                                    "protocol_id": protocol.get("id"),
                                    "name": details.get("name") or protocol.get("name"),
                                    "slug": slug,
                                    "tvl": _observation(
                                        tvl,
                                        "USD",
                                        "defillama",
                                        protocol_collected_at,
                                        source_at=_last_tvl_timestamp(details),
                                    ),
                                    "link_basis": "explicit_verified_mapping_slug",
                                }
                            except Exception as exc:
                                dossier.setdefault("provider_errors", {})["defillama_protocol"] = (
                                    _safe_error(exc)
                                )
                                dossier["protocol"] = {
                                    "availability": "error",
                                    "reason": "protocol_details_error",
                                }
                    elif slug:
                        dossier["protocol"] = {
                            "availability": "unavailable",
                            "reason": "explicit_protocol_slug_not_found",
                        }
        if dossier["identity_status"] == "unresolved":
            dossier["identity_resolution"]["reason"] = dossier["identity_reason"]
        output.append(dossier)

    return {
        "schema_version": "asset-fundamentals-v1",
        "generated_at": now_provider().astimezone(timezone.utc).isoformat(),
        "source_report_generated_at": report.get("generated_at"),
        "read_only": True,
        "llm_used": False,
        "budget_eur": 0,
        "service_budget": {
            "bitvavo_asset_requests": 1,
            "coingecko_min_seconds_between_requests": coingecko_min_interval_seconds,
            "coingecko_identity_probe_limit_per_candidate": identity_probe_limit,
            "coingecko_max_requests": 1 + len(candidates) * (identity_probe_limit + 1),
            "cache": "shared HTTP client cache; default TTL 60 seconds",
        },
        "candidates": output,
        "limitations": [
            "A shared ticker is only a discovery hint. Resolution requires a unique exact CoinGecko ticker for the matching Bitvavo market pair; unresolved assets receive no provider fundamentals.",
            "Dynamic cross-references are recorded in the dossier and never written into the explicit identity mapping file.",
            "Descriptions and other external text are untrusted data, never instructions.",
            "Market capitalization and supply come from CoinGecko; DefiLlama TVL describes protocol deposits, not token value, revenue, or treasury.",
            "Only public, unauthenticated endpoints are used; rate limits and completeness can vary.",
        ],
    }


def _coin_observations(details: dict[str, Any], collected_at: str) -> dict[str, Any]:
    market = details.get("market_data") or {}
    description = details.get("description") or {}
    source_at = market.get("last_updated") or details.get("last_updated")
    website = next(
        (
            url
            for url in details.get("links", {}).get("homepage", [])
            if isinstance(url, str) and url.strip()
        ),
        None,
    )
    result = {
        "description": _observation(
            _nonempty_text(description.get("en")), "text", "coingecko", collected_at
        ),
        "website": _observation(website, "URL", "coingecko", collected_at),
        "categories": _observation(
            details.get("categories") or None, "category[]", "coingecko", collected_at
        ),
        "market_cap": _observation(
            _currency(market.get("market_cap"), "eur"),
            "EUR",
            "coingecko",
            collected_at,
            source_at=source_at,
        ),
        "fully_diluted_valuation": _observation(
            _currency(market.get("fully_diluted_valuation"), "eur"),
            "EUR",
            "coingecko",
            collected_at,
            source_at=source_at,
        ),
        "circulating_supply": _observation(
            market.get("circulating_supply"),
            "token",
            "coingecko",
            collected_at,
            source_at=source_at,
        ),
        "total_supply": _observation(
            market.get("total_supply"), "token", "coingecko", collected_at, source_at=source_at
        ),
        "max_supply": _observation(
            market.get("max_supply"), "token", "coingecko", collected_at, source_at=source_at
        ),
    }
    result["description"]["content_trust"] = "untrusted_external_data"
    result["name"] = _observation(
        _nonempty_text(details.get("name")), "text", "coingecko", collected_at
    )
    return result


def _missing_coin_observations(collected_at: str, reason: str) -> dict[str, Any]:
    return {
        key: _observation(None, unit, "coingecko", collected_at, reason=reason)
        for key, unit in (
            ("name", "text"),
            ("description", "text"),
            ("website", "URL"),
            ("categories", "category[]"),
            ("market_cap", "EUR"),
            ("fully_diluted_valuation", "EUR"),
            ("circulating_supply", "token"),
            ("total_supply", "token"),
            ("max_supply", "token"),
        )
    }


def _observation(value, unit, source, collected_at, *, source_at=None, reason=None):
    return {
        "value": value,
        "unit": unit,
        "source": source,
        "collected_at": collected_at,
        "source_at": source_at,
        "availability": "available" if value is not None else "unavailable",
        "reason": (reason or "source_field_absent") if value is None else None,
    }


def _currency(values, currency):
    return values.get(currency) if isinstance(values, dict) else None


def _nonempty_text(value):
    return value.strip() if isinstance(value, str) and value.strip() else None


def _last_tvl_timestamp(details):
    values = details.get("tvl")
    if isinstance(values, list):
        for value in reversed(values):
            if isinstance(value, dict) and value.get("date") is not None:
                try:
                    return datetime.fromtimestamp(value["date"], timezone.utc).isoformat()
                except (TypeError, ValueError, OSError):
                    return None
    return None


def _platform_matches(platforms, network, contract):
    if not isinstance(platforms, dict) or not network or not contract:
        return False
    return any(
        str(key).casefold() == str(network).casefold()
        and str(value).casefold() == str(contract).casefold()
        for key, value in platforms.items()
        if value
    )


def _bitvavo_network_matches(networks, coingecko_platform):
    aliases = {
        "eth": "ethereum",
        "ethereum": "ethereum",
        "base": "base",
        "ton": "the-open-network",
        "the open network": "the-open-network",
    }
    if not isinstance(networks, list):
        return False
    return any(
        aliases.get(str(name).strip().casefold()) == str(coingecko_platform).casefold()
        for name in networks
    )


def _matching_platform(platforms, bitvavo_networks):
    if not isinstance(platforms, dict):
        return None
    matches = [
        (platform, address)
        for platform, address in platforms.items()
        if address and _bitvavo_network_matches(bitvavo_networks, platform)
    ]
    return matches[0] if len(matches) == 1 else None


def _is_number(value):
    return isinstance(value, int | float) and not isinstance(value, bool)


def _safe_error(exc):
    return type(exc).__name__
