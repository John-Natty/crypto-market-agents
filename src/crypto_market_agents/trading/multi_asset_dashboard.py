"""Read-only dashboard data and rendering for the multi-asset paper cycle."""

from __future__ import annotations

from datetime import datetime, timezone
import fcntl
import json
import os
from pathlib import Path
import re
import stat
from typing import Any

from .automatic_cycle import AUTO_SCHEMA, STAGES
from .multi_asset import MAX_STATE_BYTES, MultiAssetPaperStore
from .multi_asset_market import MultiAssetQuoteStore
from crypto_market_agents.security import redact_mapping, redact_text

MAX_CYCLES = 50
MAX_MANIFEST_BYTES = 2 * 1024 * 1024
MAX_ARTIFACT_BYTES = 20 * 1024 * 1024
CYCLE_ID_PATTERN = re.compile(r"^[a-f0-9]{32}$")
STAGE_ARTIFACTS = {
    "research": "research.json",
    "review": "review.json",
    "proposal": "proposal.json",
    "execution": "execution.json",
}


def is_multi_asset_state(path: str | Path) -> bool:
    """Identify a multi-asset state, including a truncated JSON header."""
    candidate = Path(path).expanduser().absolute()
    try:
        info = candidate.lstat()
        if not stat.S_ISREG(info.st_mode) or info.st_size > MAX_STATE_BYTES:
            return False
        descriptor = os.open(candidate, os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC)
        with os.fdopen(descriptor, "rb") as handle:
            prefix = handle.read(min(info.st_size, 16_384))
    except OSError:
        return False
    return bool(re.search(rb'"format"\s*:\s*"paper-multiasset"', prefix))


def multi_asset_dashboard_snapshot(
    state_path: str | Path,
    reports_dir: str | Path,
    *,
    quote_state_path: str | Path | None = None,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Read portfolio valuation and cycle artifacts without refreshing quotes."""
    portfolio_path = Path(state_path).expanduser().absolute()
    report_root = Path(reports_dir).expanduser().resolve()
    timestamp = now or datetime.now(timezone.utc)
    if not portfolio_path.exists():
        return {"ok": False, "error": "state_missing", "message": "Portefeuille absent."}
    if portfolio_path.is_symlink():
        return {"ok": False, "error": "state_invalid", "message": "Le portefeuille est invalide."}

    try:
        portfolio = MultiAssetPaperStore(portfolio_path).load()
        quote_path = (
            Path(quote_state_path).expanduser()
            if quote_state_path
            else Path(f"{portfolio_path}.quotes-v1.json")
        )
        observations = MultiAssetQuoteStore(quote_path).load_for(portfolio)["observations"]
        valuation = portfolio.valuation(quote_observations=observations, now=timestamp)
        for row in valuation["positions"]:
            quote_at = row.get("quote_timestamp")
            try:
                observed_at = datetime.fromisoformat(quote_at.replace("Z", "+00:00"))
                age_seconds = max(0.0, (timestamp - observed_at).total_seconds())
            except (AttributeError, TypeError, ValueError):
                age_seconds = None
            row["quote_age_seconds"] = age_seconds
            row["quote_max_age_seconds"] = portfolio.config.max_quote_age_seconds
            row["quote_fresh"] = (
                age_seconds is not None and age_seconds <= portfolio.config.max_quote_age_seconds
            )
    except FileNotFoundError:
        return {"ok": False, "error": "state_missing", "message": "Portefeuille absent."}
    except OSError:
        return {
            "ok": False,
            "error": "state_unavailable",
            "message": "Le portefeuille est momentanément inaccessible.",
        }
    except (ValueError, ArithmeticError, TypeError, json.JSONDecodeError):
        return {
            "ok": False,
            "error": "state_invalid",
            "message": "Le portefeuille ou son état de cotations est invalide; aucune donnée n’a été réinitialisée.",
        }

    cycles = _read_cycles(report_root)
    activity = _orchestrator_activity(portfolio_path)
    return {
        "ok": True,
        "simulation": "Simulation — aucun argent réel",
        "portfolio": {
            "format_version": portfolio.format_version,
            "risk_policy_version": portfolio.config.version,
            "initial_capital_eur": str(portfolio.initial_capital_eur),
            "cash_eur": str(portfolio.cash_eur),
            "cost_basis_eur": str(portfolio.invested_cost_basis_eur),
            "realized_pnl_eur": str(portfolio.realized_pnl_eur),
            "fees_paid_eur": str(portfolio.fees_paid_eur),
            "valuation": valuation,
        },
        "activity": activity,
        "latest_cycle": cycles[0] if cycles else None,
        "cycles": cycles,
        "reports_state": _reports_state(report_root),
        "as_of": timestamp.astimezone(timezone.utc).isoformat(),
        "read_only": True,
        "external_requests": 0,
        "llm_calls": 0,
    }


def cycle_report_bytes(reports_dir: str | Path, cycle_id: str, stage: str) -> bytes:
    """Read one whitelisted cycle artifact below the configured report root."""
    if not CYCLE_ID_PATTERN.fullmatch(cycle_id) or stage not in STAGE_ARTIFACTS:
        raise FileNotFoundError
    root = Path(reports_dir).expanduser().resolve()
    cycles_root = root / "automatic-cycles"
    if cycles_root.resolve() != cycles_root or cycles_root.is_symlink():
        raise FileNotFoundError
    cycle_dir = cycles_root / cycle_id
    if cycle_dir.is_symlink() or cycle_dir.resolve().parent != cycles_root:
        raise FileNotFoundError
    artifact = cycle_dir / STAGE_ARTIFACTS[stage]
    if artifact.is_symlink() or artifact.resolve().parent != cycle_dir:
        raise FileNotFoundError
    manifest_path = cycle_dir / "cycle.json"
    _ensure_regular_bounded(manifest_path, MAX_MANIFEST_BYTES, cycle_dir)
    manifest = _load_json(manifest_path.read_bytes(), MAX_MANIFEST_BYTES)
    stage_record = (
        manifest.get("stages", {}).get(stage)
        if isinstance(manifest, dict) and isinstance(manifest.get("stages"), dict)
        else None
    )
    if (
        not isinstance(manifest, dict)
        or manifest.get("schema_version") != AUTO_SCHEMA
        or manifest.get("cycle_id") != cycle_id
        or not isinstance(stage_record, dict)
        or stage_record.get("artifact") != STAGE_ARTIFACTS[stage]
        or not isinstance(stage_record.get("sha256"), str)
    ):
        raise FileNotFoundError
    info = artifact.stat()
    if not stat.S_ISREG(info.st_mode) or info.st_size > MAX_ARTIFACT_BYTES:
        raise FileNotFoundError
    raw = artifact.read_bytes()
    if _sha256(raw) != stage_record["sha256"]:
        raise ValueError("cycle report hash mismatch")
    payload = _load_json(raw, MAX_ARTIFACT_BYTES)
    if not isinstance(payload, dict):
        raise ValueError("cycle report is not an object")
    return json.dumps(redact_mapping(payload), ensure_ascii=False, indent=2).encode("utf-8")


def render_multi_asset_dashboard() -> str:
    """Render a responsive dashboard using text-only DOM updates."""
    return _PAGE


def _read_cycles(report_root: Path) -> list[dict[str, Any]]:
    cycles_root = report_root / "automatic-cycles"
    if cycles_root.is_symlink() or not cycles_root.exists() or not cycles_root.is_dir():
        return []
    if cycles_root.resolve().parent != report_root:
        return []
    directories = []
    try:
        for item in cycles_root.iterdir():
            if not CYCLE_ID_PATTERN.fullmatch(item.name) or item.is_symlink() or not item.is_dir():
                continue
            try:
                if item.resolve().parent == cycles_root.resolve():
                    directories.append((item.stat().st_mtime, item))
            except OSError:
                continue
    except OSError:
        return []
    directories.sort(key=lambda row: row[0], reverse=True)
    cycles = []
    for _, directory in directories[:MAX_CYCLES]:
        try:
            manifest_path = directory / "cycle.json"
            _ensure_regular_bounded(manifest_path, MAX_MANIFEST_BYTES, directory)
            manifest = _load_json(manifest_path.read_bytes(), MAX_MANIFEST_BYTES)
            if (
                not isinstance(manifest, dict)
                or manifest.get("schema_version") != AUTO_SCHEMA
                or manifest.get("cycle_id") != directory.name
            ):
                cycles.append(
                    _invalid_cycle(
                        directory.name, "Le schéma ou l’identifiant du manifeste est invalide."
                    )
                )
                continue
            cycles.append(_cycle_summary(directory, manifest))
        except (OSError, ValueError, TypeError, AttributeError, KeyError, json.JSONDecodeError):
            cycles.append(
                _invalid_cycle(directory.name, "Le manifeste est absent, incomplet ou illisible.")
            )
    cycles.sort(key=lambda item: str(item.get("created_at") or ""), reverse=True)
    return cycles


def _invalid_cycle(cycle_id: str, reason: str) -> dict[str, Any]:
    return {
        "cycle_id": cycle_id,
        "created_at": None,
        "status": "report_invalid_or_incomplete",
        "stop_reason": reason,
        "model": None,
        "elapsed_seconds": None,
        "usage": {},
        "stages": [{"stage": stage, "status": "unknown"} for stage in STAGES],
        "proof_refs": [],
        "missing_refs": [],
        "report_links": [],
        "manifest_valid": False,
    }


def _reports_state(report_root: Path) -> str:
    cycles_root = report_root / "automatic-cycles"
    if cycles_root.is_symlink():
        return "invalid"
    try:
        with os.scandir(cycles_root) as entries:
            has_entries = next(entries, None) is not None
    except FileNotFoundError:
        return "not_found"
    except NotADirectoryError:
        return "invalid"
    except OSError:
        return "unavailable"
    return "available" if has_entries else "empty"


def _cycle_summary(directory: Path, manifest: dict[str, Any]) -> dict[str, Any]:
    stage_map = manifest.get("stages") if isinstance(manifest.get("stages"), dict) else {}
    stage_rows = []
    proof_refs: set[str] = set()
    missing_refs: set[str] = set()
    report_links = []
    for stage in STAGES:
        record = stage_map.get(stage)
        if not isinstance(record, dict):
            stage_rows.append(
                {
                    "stage": stage,
                    "status": "not_started",
                    "reason": "Étape non atteinte dans ce cycle.",
                }
            )
            continue
        expected = STAGE_ARTIFACTS[stage]
        filename = record.get("artifact")
        report = None
        integrity = "not_available"
        if filename == expected:
            artifact = directory / expected
            try:
                _ensure_regular_bounded(artifact, MAX_ARTIFACT_BYTES, directory)
                raw = artifact.read_bytes()
                digest = _sha256(raw)
                if not isinstance(record.get("sha256"), str):
                    integrity = (
                        "in_progress_unverified"
                        if record.get("status") == "running"
                        else "missing_hash"
                    )
                elif record.get("sha256") != digest:
                    integrity = "hash_mismatch"
                else:
                    report = _load_json(raw, MAX_ARTIFACT_BYTES)
                    integrity = "verified"
                    if isinstance(report, dict):
                        _collect_references(stage, report, proof_refs, missing_refs)
                        report_links.append({"stage": stage, "cycle_id": directory.name})
            except (OSError, ValueError, TypeError, json.JSONDecodeError):
                integrity = "invalid_or_incomplete"
        row = {
            "stage": stage,
            "status": record.get("status", "unknown"),
            "report_status": _report_status(stage, report),
            "model_response_status": report.get("model_response_status")
            if isinstance(report, dict)
            else None,
            "plan_admissibility": report.get("plan_admissibility")
            if isinstance(report, dict)
            else None,
            "truncated_output": _has_truncated_generation(stage, report),
            "started_at": record.get("started_at") or (report or {}).get("generated_at"),
            "duration_seconds": record.get("duration_seconds"),
            "model": _model_name(report),
            "tokens": _mapping(record.get("usage")).get("generation_tokens"),
            "llm_calls": _mapping(record.get("usage")).get("llm_calls"),
            "http_requests": record.get("http_requests_used"),
            "http_request_upper_bound": record.get("http_request_upper_bound"),
            "budget": record.get("budget_granted"),
            "reason": _stage_reason(stage, record, report),
            "integrity": integrity,
        }
        if report is not None:
            row["model_interpretations"] = _model_interpretations(stage, report)
            row["rejection_reasons"] = _rejection_reasons(stage, report)
        stage_rows.append(row)
    usage = manifest.get("usage") if isinstance(manifest.get("usage"), dict) else {}
    return {
        "cycle_id": directory.name,
        "created_at": manifest.get("created_at"),
        "status": manifest.get("status", "unknown"),
        "stop_reason": _safe_reason(manifest.get("stop_reason")),
        "model": manifest.get("model"),
        "elapsed_seconds": manifest.get("elapsed_seconds"),
        "usage": usage,
        "stages": stage_rows,
        "proof_refs": sorted(proof_refs)[:100],
        "missing_refs": sorted(missing_refs)[:100],
        "report_links": report_links,
        "manifest_valid": True,
    }


def _collect_references(
    stage: str, report: dict, proof_refs: set[str], missing_refs: set[str]
) -> None:
    if stage == "research":
        for row in _list(report.get("candidates")):
            if not isinstance(row, dict):
                continue
            result = row.get("tools_executed", {})
            for tool in result.values() if isinstance(result, dict) else []:
                if not isinstance(tool, dict):
                    continue
                proof_refs.update(_strings(tool.get("available_fact_refs")))
                missing_refs.update(_strings(tool.get("missing_refs")))
        conclusion = report.get("conclusion", {})
        for row in _list(conclusion.get("candidates")) if isinstance(conclusion, dict) else []:
            if isinstance(row, dict):
                proof_refs.update(_strings(row.get("fact_refs")))
                missing_refs.update(_strings(row.get("missing_refs")))
                missing_refs.update(_strings(row.get("required_missing_refs")))
    elif stage == "review":
        review = report.get("review", {})
        candidates = _list(review.get("candidates")) if isinstance(review, dict) else []
        for row in candidates:
            if not isinstance(row, dict):
                continue
            for key in ("favorable_arguments", "unfavorable_arguments", "uncertain_arguments"):
                for argument in _list(row.get(key)):
                    if isinstance(argument, dict) and isinstance(argument.get("reference"), str):
                        proof_refs.add(argument["reference"])
            for key in ("rejected_arguments", "rejected_invalidation_conditions"):
                for argument in _list(row.get(key)):
                    if isinstance(argument, dict) and isinstance(argument.get("reference"), str):
                        missing_refs.add(argument["reference"])
            for item in _list(row.get("information_to_obtain")):
                if isinstance(item, dict) and isinstance(item.get("reference"), str):
                    missing_refs.add(item["reference"])
    elif stage == "proposal":
        for row in _list(report.get("proposals")):
            if isinstance(row, dict):
                proof_refs.update(_strings(row.get("references")))
    elif stage == "execution":
        pass


def _model_interpretations(stage: str, report: dict) -> list[str]:
    values = []
    if stage == "research":
        conclusion = report.get("conclusion")
        candidates = _list(conclusion.get("candidates")) if isinstance(conclusion, dict) else []
        values.extend(str(row.get("interpretation")) for row in candidates if isinstance(row, dict))
    elif stage == "proposal":
        values.extend(
            str(row.get("rationale"))
            for row in _list(report.get("proposals"))
            if isinstance(row, dict)
        )
    return [redact_text(value[:700]) for value in values if value and value != "None"][:10]


def _rejection_reasons(stage: str, report: dict) -> list[str]:
    reasons = []
    if stage == "proposal":
        for row in _list(report.get("proposals")):
            if isinstance(row, dict):
                reasons.extend(_strings(row.get("reasons")))
    elif stage == "review":
        review = report.get("review")
        candidates = _list(review.get("candidates")) if isinstance(review, dict) else []
        for row in candidates:
            if isinstance(row, dict):
                rejected = _list(row.get("rejected_arguments")) + _list(
                    row.get("rejected_invalidation_conditions")
                )
                for item in rejected:
                    if isinstance(item, dict):
                        reasons.append(
                            str(item.get("reason") or item.get("error") or "Élément rejeté")
                        )
    elif stage == "execution":
        reasons.extend(_strings(report.get("errors")))
        if report.get("reason"):
            reasons.append(str(report["reason"]))
    return [redact_text(reason[:300]) for reason in reasons[:30]]


def _report_status(stage: str, report: Any) -> str | None:
    if not isinstance(report, dict):
        return None
    if stage == "research":
        return str(report.get("research_status") or report.get("status") or "unknown")
    if stage == "proposal":
        return str(report.get("decision_status") or report.get("status") or "unknown")
    if stage == "execution":
        return str(report.get("status") or "unknown")
    return str(report.get("status") or "unknown")


def _has_truncated_generation(stage: str, report: Any) -> bool:
    if not isinstance(report, dict):
        return False
    if stage == "research":
        return any(
            isinstance(event, dict)
            and event.get("kind") == "ollama_turn"
            and (
                event.get("done_reason") == "length"
                or _mapping(event.get("generation")).get("done_reason") == "length"
            )
            for event in _list(report.get("events"))
        )
    if stage == "review":
        return _mapping(report.get("generation")).get("done_reason") == "length"
    if stage == "proposal":
        generation = _mapping(report.get("generation"))
        if generation.get("done_reason") == "length":
            return True
        return any(
            isinstance(call, dict) and call.get("done_reason") == "length"
            for call in _list(generation.get("calls"))
        )
    return False


def _stage_reason(stage: str, record: dict, report: Any) -> str | None:
    if isinstance(report, dict):
        if report.get("stop_reason"):
            return _safe_reason(report["stop_reason"])
        if report.get("reason"):
            return _safe_reason(report["reason"])
        reasons = _rejection_reasons(stage, report)
        if reasons:
            return "; ".join(reasons[:3])
    outcome = record.get("outcome")
    if isinstance(outcome, dict) and outcome.get("reason"):
        return _safe_reason(outcome["reason"])
    details = record.get("details")
    if isinstance(details, dict) and details.get("reason"):
        return _safe_reason(details["reason"])
    if record.get("reason"):
        return _safe_reason(record["reason"])
    return None


def _model_name(report: Any) -> str | None:
    if not isinstance(report, dict):
        return None
    model = report.get("model")
    if isinstance(model, str):
        return model
    if isinstance(model, dict):
        return str(model.get("name") or model.get("model") or "") or None
    return None


def _orchestrator_activity(state_path: Path) -> dict[str, str]:
    lock_path = state_path.with_name(state_path.name + ".automatic-cycle.lock")
    try:
        info = lock_path.lstat()
        if not stat.S_ISREG(info.st_mode):
            return {"status": "unconfirmed", "label": "Activité non confirmée"}
        fd = os.open(lock_path, os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC)
    except OSError:
        return {"status": "unconfirmed", "label": "Activité non confirmée"}
    try:
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return {"status": "running", "label": "En cours — verrou du portefeuille actif"}
        except OSError:
            return {"status": "unconfirmed", "label": "Activité non confirmée"}
        else:
            fcntl.flock(fd, fcntl.LOCK_UN)
            return {
                "status": "unconfirmed",
                "label": "Activité non confirmée — aucun cycle ne détient le verrou",
            }
    finally:
        os.close(fd)


def _ensure_regular_bounded(path: Path, maximum: int, parent: Path) -> None:
    if path.is_symlink() or path.resolve().parent != parent.resolve():
        raise FileNotFoundError
    info = path.stat()
    if not stat.S_ISREG(info.st_mode) or info.st_size > maximum:
        raise ValueError("dashboard_file_invalid")


def _load_json(raw: bytes, maximum: int) -> Any:
    if len(raw) > maximum:
        raise ValueError("dashboard_json_too_large")

    def unique(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise ValueError("dashboard_json_duplicate_key")
            result[key] = value
        return result

    return json.loads(raw, object_pairs_hook=unique)


def _strings(values: Any) -> list[str]:
    if not isinstance(values, list):
        return []
    return [item[:300] for item in values if isinstance(item, str)][:100]


def _safe_reason(value: Any) -> str:
    return redact_text(str(value).replace("\x00", "")[:500])


def _list(value: Any) -> list:
    return value if isinstance(value, list) else []


def _mapping(value: Any) -> dict:
    return value if isinstance(value, dict) else {}


def _sha256(value: bytes) -> str:
    import hashlib

    return hashlib.sha256(value).hexdigest()


_PAGE = r"""<!doctype html>
<html lang="fr"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>Portefeuille fictif multi-actifs</title>
<style>
:root{color-scheme:dark;--bg:#0b1016;--panel:#111a24;--border:#263544;--text:#e7edf3;--muted:#9baaba;--green:#62d6a2;--red:#ff8d8d;--amber:#f0c779;--blue:#83b8ff}*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--text);font:15px/1.5 system-ui,-apple-system,"Segoe UI",sans-serif}.shell{max-width:1240px;margin:auto;padding:24px}.banner{position:sticky;top:0;z-index:4;margin:-24px -24px 20px;padding:12px 24px;background:#5a3417;border-bottom:1px solid #a46a30;color:#ffe2b8;font-weight:800;letter-spacing:.02em}.head{display:flex;justify-content:space-between;align-items:flex-start;gap:16px}.head h1{margin:0 0 4px;font-size:clamp(1.5rem,3vw,2.2rem)}.muted,.sub{color:var(--muted)}.pill{display:inline-block;border:1px solid var(--border);border-radius:999px;padding:4px 10px;font-size:.82rem}.pill.good{color:var(--green)}.pill.warn{color:var(--amber)}.pill.bad{color:var(--red)}.grid{display:grid;grid-template-columns:repeat(4,minmax(0,1fr));gap:12px;margin:18px 0}.card,.metric{background:var(--panel);border:1px solid var(--border);border-radius:14px;padding:16px;min-width:0}.metric small{display:block;color:var(--muted);font-size:.8rem}.metric strong{display:block;font-size:1.35rem;overflow-wrap:anywhere}.section{margin:20px 0}.section h2{font-size:1.15rem;margin:0 0 12px}.table-wrap{overflow:auto;border:1px solid var(--border);border-radius:12px}.table{width:100%;border-collapse:collapse;min-width:740px}.table th,.table td{text-align:left;padding:11px 12px;border-bottom:1px solid var(--border);vertical-align:top}.table th{color:var(--muted);font-size:.8rem}.table tr:last-child td{border-bottom:0}.cycle{margin:12px 0;padding:0;overflow:hidden}.cycle summary{cursor:pointer;padding:16px;list-style:none}.cycle summary::-webkit-details-marker{display:none}.cycle-head{display:flex;justify-content:space-between;gap:12px;align-items:center}.cycle-id{font-family:ui-monospace,monospace;color:var(--blue);overflow-wrap:anywhere}.cycle-body{padding:0 16px 16px}.stages{display:grid;grid-template-columns:repeat(4,minmax(0,1fr));gap:10px}.stage{border:1px solid var(--border);border-radius:10px;padding:12px;min-width:0}.stage h3{font-size:.95rem;margin:0 0 8px}.stage p{margin:4px 0;overflow-wrap:anywhere}.stage a,.links a{color:var(--blue);margin-right:10px}.refs{display:flex;flex-wrap:wrap;gap:6px}.ref{font:12px ui-monospace,monospace;background:#0c141c;border:1px solid var(--border);padding:4px 7px;border-radius:7px;overflow-wrap:anywhere}.reason{color:var(--amber)}.danger{color:var(--red)}.safe{color:var(--green)}.empty{padding:18px;text-align:center;color:var(--muted)}.error{border-color:#744646;color:#ffd1d1}.foot{font-size:.82rem;color:var(--muted);margin:20px 0}.toplinks a{color:var(--blue);margin-left:12px}@media(max-width:850px){.grid{grid-template-columns:repeat(2,minmax(0,1fr))}.stages{grid-template-columns:repeat(2,minmax(0,1fr))}}@media(max-width:540px){.shell{padding:16px}.banner{margin:-16px -16px 16px;padding:11px 16px}.head{display:block}.toplinks{margin-top:10px}.grid{grid-template-columns:1fr 1fr;gap:8px}.metric{padding:12px}.metric strong{font-size:1.05rem}.stages{grid-template-columns:1fr}.cycle-head{display:block}.cycle-head .pill{margin-top:8px}.card{padding:12px}}
</style></head><body><main class="shell"><div class="banner">Simulation — aucun argent réel</div><header class="head"><div><h1>Portefeuille multi-actifs</h1><p class="muted" id="as-of">Lecture des états enregistrés…</p></div><nav class="toplinks"><a href="/reports">Rapports</a></nav></header>
<section class="card"><strong>Activité de l’orchestrateur</strong><p id="activity">Activité non confirmée</p><p class="sub">Le rafraîchissement de cette page ne prouve pas qu’un cycle tourne. L’état « en cours » exige le verrou du portefeuille.</p></section>
<section id="error" class="card error" hidden></section><section id="portfolio" hidden><div id="incomplete" class="card error" hidden><strong>Valorisation incomplète</strong><p>Au moins une position ne possède pas de cotation exploitable. Sa valeur et le résultat non réalisé restent inconnus.</p></div><div class="grid" id="metrics"></div>
<section class="section"><h2>Positions</h2><div class="table-wrap"><table class="table"><thead><tr><th>Marché</th><th>Quantité</th><th>Coût de revient</th><th>Valeur actuelle</th><th>Réalisé / non réalisé</th><th>Cotation</th></tr></thead><tbody id="positions"></tbody></table></div><div class="card empty" id="positions-empty" hidden>Aucune position ouverte.</div></section>
<section class="section"><h2>Cycles automatiques</h2><p class="muted">Historique des rapports enregistrés. Les rapports anciens ne sont pas des observations actuelles.</p><div id="cycles"></div><div id="cycles-empty" class="card empty" hidden>Aucun rapport de cycle trouvé dans le dossier configuré.</div></section></section>
<p class="foot">Lecture seule · aucun appel marché ou LLM depuis l’affichage · les interprétations libres ne sont pas certifiées.</p></main>
<script>(()=>{'use strict';const $=id=>document.getElementById(id);const eur=new Intl.NumberFormat('fr-FR',{style:'currency',currency:'EUR',minimumFractionDigits:2,maximumFractionDigits:2});const exact=new Intl.NumberFormat('fr-FR',{maximumFractionDigits:10});const date=v=>{if(!v)return '—';const d=new Date(v);return Number.isFinite(d.getTime())?new Intl.DateTimeFormat('fr-FR',{dateStyle:'medium',timeStyle:'short'}).format(d):'—'};const duration=s=>s==null||!Number.isFinite(Number(s))?'—':Math.floor(Number(s))+' s';function node(tag,text,cls){const e=document.createElement(tag);if(text!==undefined&&text!==null)e.textContent=String(text);if(cls)e.className=cls;return e}function metric(label,value,sub){const d=node('div',null,'metric');d.append(node('small',label),node('strong',value),node('small',sub||''));return d}function refs(container,values,label){if(!values||!values.length)return;container.append(node('p',label,'sub'));const list=node('div',null,'refs');for(const v of values)list.append(node('span',v,'ref'));container.append(list)}function renderCycle(c){const details=node('details',null,'card cycle');details.dataset.cycleId=c.cycle_id||'';const summary=node('summary');summary.dataset.focusKey=(c.cycle_id||'')+':summary';const head=node('div',null,'cycle-head');const title=node('span',null,'cycle-id');title.textContent=c.cycle_id||'Cycle illisible';head.append(title,node('span',({'paper_executed':'Opération fictive exécutée','plans_rejected':'Proposition rejetée','technical_error':'Erreur technique','abstained':'Abstention volontaire','proposal_abstained':'Abstention volontaire','review_unavailable':'Revue indisponible','stopped':'Cycle arrêté','interrupted':'Cycle interrompu','plan_stale_rejected':'Plan périmé rejeté','proposal_ready_execution_disabled':'Proposition prête · exécution désactivée','completed_without_transaction':'Cycle terminé sans transaction','completed_without_execution':'Cycle terminé · exécution non demandée','budget_exhausted':'Budget épuisé','state_changed':'Portefeuille modifié'}[c.status]||c.status||'inconnu')+' · '+date(c.created_at),'pill '+(c.status==='paper_executed'?'good':c.status==='technical_error'?'bad':'warn')));summary.append(head);details.append(summary);const body=node('div',null,'cycle-body');body.append(node('p','Modèle : '+(c.model||'—')+' · Durée : '+duration(c.elapsed_seconds)+' · Tokens : '+(c.usage?.generation_tokens??'—')+' · appels LLM : '+(c.usage?.llm_calls??'—')+' · tentatives HTTP : '+(c.usage?.http_requests??'—')));if(c.stop_reason)body.append(node('p','Arrêt : '+c.stop_reason,'reason'));const stages=node('div',null,'stages');for(const s of c.stages||[]){const card=node('article',null,'stage');card.append(node('h3',{research:'Recherche',review:'Revue',proposal:'Proposition',execution:'Exécution fictive'}[s.stage]||s.stage));card.append(node('p','Statut : '+(s.truncated_output?'Sortie tronquée · ':'')+({'abstained':'Abstention volontaire','plans_rejected':'Proposition rejetée','rejected':'Proposition rejetée','technical_error':'Erreur technique','committed':'Opération fictive exécutée'}[s.report_status||s.status]||s.report_status||s.status||'—')));if(s.model_response_status)card.append(node('p','Réponse modèle : '+s.model_response_status));if(s.plan_admissibility)card.append(node('p','Admissibilité : '+(typeof s.plan_admissibility==='string'?s.plan_admissibility:JSON.stringify(s.plan_admissibility))));card.append(node('p','Date : '+date(s.started_at)+' · durée : '+duration(s.duration_seconds)));card.append(node('p','LLM : '+(s.llm_calls??'—')+' · tokens : '+(s.tokens??'—')+' · HTTP : '+(s.http_requests??s.http_request_upper_bound??'—')));if(s.reason)card.append(node('p','Motif : '+s.reason,'reason'));if(s.integrity&&s.integrity!=='verified'&&s.integrity!=='not_available')card.append(node('p','Rapport incomplet ou empreinte non vérifiée : '+s.integrity,'danger'));if(s.model_interpretations)for(const text of s.model_interpretations)card.append(node('p','Interprétation non certifiée : '+text,'sub'));if(s.rejection_reasons)for(const text of s.rejection_reasons)card.append(node('p','Rejet : '+text,'danger'));if(s.budget)card.append(node('p','Budget : '+JSON.stringify(s.budget)));const link=(c.report_links||[]).find(x=>x.stage===s.stage);if(link){const a=node('a','Ouvrir le rapport JSON');a.href='/cycle-file?cycle_id='+encodeURIComponent(c.cycle_id)+'&stage='+encodeURIComponent(s.stage);a.rel='noopener';a.dataset.focusKey=(c.cycle_id||'')+':'+s.stage;card.append(a)}stages.append(card)}body.append(stages);refs(body,c.proof_refs,'Références factuelles disponibles');refs(body,c.missing_refs,'Informations manquantes ou éléments rejetés');details.append(body);return details}function render(data){$('as-of').textContent='État lu le '+date(data.as_of)+' · données horodatées selon les rapports et cotations enregistrés';$('activity').textContent=data.activity?.label||'Activité non confirmée';$('portfolio').hidden=false;const p=data.portfolio,v=p.valuation;$('incomplete').hidden=v.complete;const total=v.total_value_eur===null?'Incomplète':eur.format(Number(v.total_value_eur));$('metrics').replaceChildren(metric('Liquidités',eur.format(Number(p.cash_eur)),'Capital initial '+eur.format(Number(p.initial_capital_eur))),metric('Coût de revient / engagé',eur.format(Number(p.cost_basis_eur)),'Limites version '+p.risk_policy_version),metric('Valeur / exposition actuelle',v.complete?total:'Incomplète',v.complete?'Valorisation complète au '+date(v.as_of):'Valeurs partielles, total inconnu'),metric('Résultat réalisé',eur.format(Number(p.realized_pnl_eur)),'Comptabilisé'),metric('Résultat non réalisé',v.unrealized_pnl_eur===null?'Incomplet':eur.format(Number(v.unrealized_pnl_eur)),'Calculé uniquement si la valorisation est complète'),metric('Frais cumulés',eur.format(Number(p.fees_paid_eur)),'Valeurs comptables exactes conservées dans l’état'));const tbody=$('positions');tbody.replaceChildren();for(const x of v.positions||[]){if(x.status==='closed')continue;const tr=node('tr');const values=[x.key+' · '+x.status,exact.format(Number(x.quantity)),x.cost_basis_eur===null?'—':eur.format(Number(x.cost_basis_eur)),x.market_value_eur===null?'Inconnue':eur.format(Number(x.market_value_eur)),(x.realized_pnl_eur===null?'—':eur.format(Number(x.realized_pnl_eur)))+' / '+(x.unrealized_pnl_eur===null?'Inconnu':eur.format(Number(x.unrealized_pnl_eur))),x.quote_timestamp?date(x.quote_timestamp)+' · '+(x.quote_fresh?(x.status==='valued'?'fraîche':'âge admissible, mais cotation non retenue'): (x.quote_age_seconds===null?'fraîcheur inconnue':'périmée · '+Math.floor(x.quote_age_seconds)+' s'))+' · '+(x.quote_source||'source inconnue')+(x.last_known_bid_eur===null?'':' · dernier bid enregistré '+eur.format(Number(x.last_known_bid_eur)))+(x.quote_error?' · '+x.quote_error:''):'Aucune cotation enregistrée'];for(const value of values)tr.append(node('td',value));tbody.append(tr)}$('positions-empty').hidden=(v.positions||[]).some(x=>x.status!=='closed');const list=$('cycles');list.replaceChildren();for(const c of data.cycles||[])list.append(renderCycle(c));$('cycles-empty').textContent=({not_found:'Dossier de cycles absent dans le répertoire configuré.',empty:'Aucun cycle enregistré.',unavailable:'Dossier de cycles inaccessible.',invalid:'Dossier de cycles invalide.'}[data.reports_state]||'Aucun cycle disponible.');$('cycles-empty').hidden=(data.cycles||[]).length>0}async function refresh(){try{const response=await fetch('/api/multi-asset',{cache:'no-store'});const data=await response.json();if(!response.ok||!data.ok){$('portfolio').hidden=true;$('error').hidden=false;$('error').textContent=data.message||'État du portefeuille indisponible; aucune réparation n’a été tentée.';return}const list=$('cycles');const openIds=new Set(Array.from(list.querySelectorAll('details.cycle[open]'),el=>el.dataset.cycleId));const active=document.activeElement;const focusKey=list.contains(active)?active.dataset.focusKey:null;const scrollX=window.scrollX,scrollY=window.scrollY;$('error').hidden=true;render(data);for(const el of list.querySelectorAll('details.cycle'))el.open=openIds.has(el.dataset.cycleId);if(focusKey){for(const el of list.querySelectorAll('[data-focus-key]'))if(el.dataset.focusKey===focusKey){el.focus({preventScroll:true});break}}window.scrollTo(scrollX,scrollY)}catch(_){$('error').hidden=false;$('error').textContent='État temporairement indisponible. Les fichiers et soldes n’ont pas été modifiés.'}}refresh();setInterval(refresh,5000)})();</script></body></html>"""
