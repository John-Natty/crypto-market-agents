"""Read-only orchestration that joins scanner candidates and candle analysis."""

from __future__ import annotations

from datetime import datetime, timezone

from .history_analysis import calculate_metrics, normalize_candles


class DeepMarketAnalyzer:
    def __init__(self, candle_client, *, now_provider=None):
        self.candle_client = candle_client
        self.now_provider = now_provider or (lambda: datetime.now(timezone.utc))

    def analyze(self, scan_result, *, count=5, interval="1h", limit=1440):
        if isinstance(count, bool) or count < 1:
            raise ValueError("Le nombre de candidats doit être positif.")
        selected = scan_result.get("candidates", [])[:count]
        now = self.now_provider().astimezone(timezone.utc)
        generated_at = scan_result.get("collected_at")
        try:
            scan_time = datetime.fromisoformat(generated_at.replace("Z", "+00:00"))
            scan_age = (now - scan_time.astimezone(timezone.utc)).total_seconds()
            if scan_age < 0 or scan_age > 3600:
                raise ValueError
            scan_age_error = None
        except (AttributeError, TypeError, ValueError, OverflowError):
            scan_age_error = "résultat scan absent, invalide, futur ou âgé de plus d'une heure"
        output = []
        for candidate in selected:
            market = candidate.get("market", "")
            item = {
                "market": market,
                "scanner_candidate": candidate,
                "interval": interval,
                "collected_at": now.isoformat(),
                "observations": [],
                "favorable_signals": [],
                "risks_and_contradictions": [],
                "data_quality": {},
                "reasons": [],
                "disposition": "écarter pour l'instant: historique indisponible",
            }
            if scan_age_error:
                item["data_quality"] = {"sufficient": False, "scan_error": scan_age_error}
                item["analysis_quality"] = {"status": "unavailable", "reason": scan_age_error}
                item["reasons"] = [scan_age_error]
                output.append(item)
                continue
            if not isinstance(market, str) or not market.endswith("-EUR"):
                item["data_quality"] = {"sufficient": False, "error": "marché EUR invalide"}
                item["analysis_quality"] = {
                    "status": "unavailable",
                    "reason": "marché absent ou non EUR dans le fichier de scan",
                }
                item["reasons"] = ["marché absent ou non EUR dans le fichier de scan"]
                output.append(item)
                continue
            try:
                rows = self.candle_client.get_candles(market, interval=interval, limit=limit)
                candles, quality = normalize_candles(rows, interval=interval, now=now)
                calculated = calculate_metrics(candles, interval=interval, now=now)
                item.update(calculated)
                item["observations"] = [
                    {
                        "timestamp": c.timestamp.isoformat(),
                        "open": str(c.open),
                        "high": str(c.high),
                        "low": str(c.low),
                        "close": str(c.close),
                        "volume_base": str(c.volume_base),
                    }
                    for c in candles
                ]
                unavailable_metrics = [
                    name
                    for name, status in item.get("metric_quality", {}).items()
                    if not status["available"] or not status["freshness"]["fresh"]
                ]
                analysis_status = (
                    "partial"
                    if unavailable_metrics
                    or quality["invalid_data"]
                    or quality["gaps"]
                    or not quality["fresh"]
                    else "complete"
                )
                item["analysis_quality"] = {
                    "status": analysis_status,
                    "available_metrics": len(item.get("metric_quality", {}))
                    - len(unavailable_metrics),
                    "unavailable_metrics": unavailable_metrics,
                    "invalid_data": quality["invalid_data"],
                    "observed_gaps": quality["gaps"],
                    "fresh": quality["fresh"],
                }
                item["data_quality"] = {
                    "received": quality["received"],
                    "valid": quality["valid"],
                    "closed": quality["closed"],
                    "open_excluded": quality["open_excluded"],
                    "duplicates_removed": quality["duplicates_removed"],
                    "invalid_data": quality["invalid_data"],
                    "invalid_rows": quality["invalid_data"],
                    "normalization_notes": quality["normalization_notes"],
                    "gaps": quality["gaps"],
                    "latest_closed_at": quality["latest_closed_at"],
                    "latest_age_seconds": quality["latest_age_seconds"],
                    "fresh": quality["fresh"],
                }
                if analysis_status == "partial":
                    item["disposition"] = (
                        "analyse partielle: disponibilité évaluée par métrique et fenêtre"
                    )
                else:
                    item["disposition"] = "analyse complète pour les métriques calculées"
                if unavailable_metrics:
                    item["reasons"].append(
                        "métriques indisponibles ou périmées: " + ", ".join(unavailable_metrics)
                    )
                if quality["invalid_data"]:
                    item["reasons"].extend(quality["invalid_data"])
                if quality["gaps"]:
                    item["reasons"].append(
                        f"{len(quality['gaps'])} lacune(s) observée(s), cause inconnue; "
                        "seules les fenêtres qui les traversent sont indisponibles"
                    )
                item["reasons"].extend(quality["normalization_notes"])
            except (OSError, RuntimeError, ValueError, TypeError, KeyError) as exc:
                item["data_quality"] = {
                    "invalid_data": [],
                    "observed_gaps": [],
                    "error": f"{type(exc).__name__}: {exc}",
                }
                item["analysis_quality"] = {
                    "status": "unavailable",
                    "reason": f"{type(exc).__name__}: {exc}",
                }
                item["reasons"] = ["analyse indisponible pour ce candidat"]
            output.append(item)
        return {
            "generated_at": now.isoformat(),
            "analysis_version": "metric-window-quality.v1",
            "scan_collected_at": generated_at,
            "source": "Bitvavo public candles",
            "sources": [
                {
                    "name": "Bitvavo REST candles",
                    "url": "https://api.bitvavo.com/v2/{market}/candles",
                }
            ],
            "read_only": True,
            "llm_used": False,
            "interval": interval,
            "selection": {"requested": count, "selected": len(selected)},
            "candidates": output,
            "limitations": [
                "Observations historiques descriptives; aucune probabilité ni cible de prix.",
                "Volume en actif de base; il ne mesure pas la profondeur disponible.",
                "Les trous observés peuvent correspondre à des intervalles sans transaction; leur cause n'est pas déterminable à partir des seules bougies.",
                "La disponibilité est évaluée par métrique et fenêtre; une mesure disponible ne rend pas le jeu de données complet.",
            ],
        }
