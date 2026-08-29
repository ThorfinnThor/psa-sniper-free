from __future__ import annotations

import os
from dataclasses import dataclass, field
from datetime import timedelta
from typing import Any

from .config import load_settings, state_path
from .psa import PSAClient, cert_needs_api_upgrade, merge_cert_info
from .psa_auth import normalize_psa_access_token
from .repricing import listing_from_history
from .scoring import score_hit
from .state import cert_from_dict, hit_to_record, load_state, market_from_dict, put_cached_cert, save_state
from .util import iso_z, parse_iso_datetime, utc_now


@dataclass(slots=True)
class PSABackfillResult:
    checked_certs: int = 0
    upgraded_certs: int = 0
    rescored_rows: int = 0
    calls: int = 0
    status: str = "skipped"
    notes: list[str] = field(default_factory=list)


def _latest_psa_status(state: dict[str, Any]) -> str:
    runs = state.get("runs") or []
    if not runs or not isinstance(runs[0], dict):
        return ""
    for note in runs[0].get("notes") or []:
        text = str(note)
        if text.startswith("PSA API live:"):
            return text.split(":", 1)[1].strip().upper()
    return ""


def _preserve_history_meta(old: dict[str, Any], new: dict[str, Any]) -> dict[str, Any]:
    for key in (
        "first_seen_at", "last_seen_at", "price_checked_at", "price_check_attempts",
        "price_last_improved_at", "availability_checked_at", "availability_status",
        "availability_error",
    ):
        if old.get(key) is not None:
            new[key] = old[key]
    return new


def backfill_state(
    state: dict[str, Any],
    settings: dict[str, Any],
    psa: PSAClient,
) -> PSABackfillResult:
    result = PSABackfillResult(status="ok")
    max_calls = max(0, int(settings.get("max_psa_backfill_calls_per_run", 12)))
    if max_calls <= 0:
        result.status = "disabled"
        return result

    cutoff = utc_now() - timedelta(hours=max(1, int(settings.get("psa_backfill_history_hours", 168))))
    candidates: dict[str, tuple[int, str]] = {}
    history = [row for row in state.get("history", []) if isinstance(row, dict)]
    for row in history:
        if str(row.get("availability_status") or "active") in {"ended", "unavailable"}:
            continue
        seen = parse_iso_datetime(row.get("last_seen_at") or row.get("first_seen_at"))
        if not seen or seen < cutoff:
            continue
        number = str(row.get("cert_number") or "").strip()
        if not number.isdigit():
            continue
        cert_data = row.get("cert")
        existing = cert_from_dict(cert_data) if isinstance(cert_data, dict) else None
        if not cert_needs_api_upgrade(existing):
            continue
        score = int(row.get("score") or 0)
        last_seen = str(row.get("last_seen_at") or "")
        previous = candidates.get(number)
        if previous is None or (score, last_seen) > previous:
            candidates[number] = (score, last_seen)

    ordered = sorted(candidates, key=lambda number: candidates[number], reverse=True)
    threshold = int(settings.get("hit_threshold", 11))
    for number in ordered[:max_calls]:
        if psa.calls_made >= max_calls or not psa.access_token:
            break
        result.checked_certs += 1
        api_cert = psa.get_api_cert(number)
        if not api_cert:
            if not psa.access_token:
                result.status = psa.api_auth_status
                break
            continue

        matching = [row for row in history if str(row.get("cert_number") or "") == number]
        existing = None
        for row in matching:
            if isinstance(row.get("cert"), dict):
                existing = cert_from_dict(row["cert"])
                break
        merged = merge_cert_info(api_cert, existing)
        put_cached_cert(state, merged)
        result.upgraded_certs += 1

        for row in matching:
            listing = listing_from_history(row)
            if listing is None:
                continue
            market = market_from_dict(row.get("market_value"))
            hit = score_hit(
                listing,
                cert_number=number,
                cert_source=row.get("cert_source"),
                cert_confidence=row.get("cert_confidence"),
                cert=merged,
                market_value_listing_currency=market,
                priority_terms=list(settings.get("priority_terms") or []),
                demand_terms=list(settings.get("demand_terms") or []),
                import_risk_extra_edge=float(settings.get("import_risk_extra_edge", 0.0)),
                import_exempt_countries=list(settings.get("import_risk_exempt_countries") or []),
            )
            updated = _preserve_history_meta(row, hit_to_record(hit, threshold))
            row.clear()
            row.update(updated)
            result.rescored_rows += 1

    result.calls = psa.calls_made
    return result


def run_psa_backfill_queue() -> PSABackfillResult:
    path = state_path()
    settings = load_settings()
    state = load_state(path)
    result = PSABackfillResult()
    if _latest_psa_status(state) != "OK":
        return result
    token = normalize_psa_access_token(os.getenv("PSA_ACCESS_TOKEN"))
    if not token:
        return result
    max_calls = max(0, int(settings.get("max_psa_backfill_calls_per_run", 12)))
    psa = PSAClient(
        access_token=token,
        web_fallback=False,
        delay_seconds=float(settings.get("psa_request_delay_seconds", 1.5)),
        max_calls=max_calls,
    )
    result = backfill_state(state, settings, psa)
    runs = state.get("runs") or []
    if runs and isinstance(runs[0], dict):
        latest = runs[0]
        latest["psa_backfill_checked"] = result.checked_certs
        latest["psa_backfill_upgraded"] = result.upgraded_certs
        latest["psa_backfill_rescored"] = result.rescored_rows
        latest["psa_backfill_calls"] = result.calls
        notes = list(latest.get("notes") or [])
        notes.append(
            "PSA-Backfill: "
            f"{result.checked_certs} Cert(s) geprüft; {result.upgraded_certs} verbessert; "
            f"{result.rescored_rows} Historieneintrag/-einträge neu bewertet; {result.calls} Call(s)"
        )
        latest["notes"] = list(dict.fromkeys(notes))
    save_state(path, state)
    return result
