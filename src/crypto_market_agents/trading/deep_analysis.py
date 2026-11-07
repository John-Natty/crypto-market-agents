"""Read-only orchestration that joins scanner candidates and candle analysis."""

from __future__ import annotations

from datetime import datetime, timezone

from .history_analysis import calculate_metrics, normalize_candles


class DeepMarketAnalyzer:
    def __init__(self, candle_client, *, now_provider=None):
        self.candle_client = candle_client
        self.now_provider = now_provider or (lambda: datetime.now(timezone.utc))

    def analyze(self, scan_result, *, count=5, interval="1h", limit=720):
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
                item["reasons"] = [scan_age_error]
                output.append(item)
                continue
            if not isinstance(market, str) or not market.endswith("-EUR"):
                item["data_quality"] = {"sufficient": False, "error": "marché EUR invalide"}
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
                item["data_quality"] = quality
                item["data_quality"]["sufficient"] = bool(
                    item["data_sufficiency"]["sufficient"]
                    and quality["fresh"]
                    and not quality["gaps"]
                    and not quality["invalid_rows"]
                )
                if not item["data_quality"]["sufficient"]:
                    item["disposition"] = (
                        "écarter pour l'instant: qualité ou quantité de données insuffisante"
                    )
                if not quality["fresh"]:
                    item["reasons"].append("données trop anciennes ou aucune bougie clôturée")
                    item["disposition"] = (
                        "écarter pour l'instant: données historiques périmées ou absentes"
                    )
                if quality["gaps"]:
                    item["reasons"].append("historique avec trous temporels")
                    item["disposition"] = "écarter pour l'instant: série temporelle incomplète"
                if quality["invalid_rows"]:
                    item["reasons"].extend(quality["invalid_rows"])
            except (OSError, RuntimeError, ValueError, TypeError, KeyError) as exc:
                item["data_quality"] = {
                    "sufficient": False,
                    "error": f"{type(exc).__name__}: {exc}",
                }
                item["reasons"] = ["analyse indisponible pour ce candidat"]
            output.append(item)
        return {
            "generated_at": now.isoformat(),
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
            ],
        }
