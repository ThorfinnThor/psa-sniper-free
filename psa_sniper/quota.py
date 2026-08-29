from __future__ import annotations

import json
import os
from dataclasses import dataclass
from datetime import timedelta

from .config import load_settings, state_path
from .ebay import EbayClient
from .state import load_state
from .util import parse_iso_datetime, utc_now


@dataclass(slots=True)
class QuotaDecision:
    allowed_calls: int
    rolling_used: int
    analytics_remaining: int | None
    skipped: bool
    note: str


def _rolling_calls(state: dict, hours: int = 24) -> int:
    cutoff = utc_now() - timedelta(hours=hours)
    total = 0
    for run in state.get("runs", []) or []:
        if not isinstance(run, dict):
            continue
        completed = parse_iso_datetime(run.get("completed_at"))
        if not completed or completed < cutoff:
            continue
        total += int(run.get("total_ebay_calls") or run.get("ebay_calls") or 0)
    return total


def _merge_override(values: dict[str, int]) -> None:
    raw = os.getenv("SETTINGS_OVERRIDE_JSON", "").strip()
    current: dict = {}
    if raw:
        try:
            parsed = json.loads(raw)
            if isinstance(parsed, dict):
                current.update(parsed)
        except json.JSONDecodeError:
            pass
    current.update(values)
    os.environ["SETTINGS_OVERRIDE_JSON"] = json.dumps(current, separators=(",", ":"))


def prepare_scan_quota() -> QuotaDecision:
    settings = load_settings()
    configured = int(settings.get("max_ebay_calls_per_run", 575))
    daily_limit = int(settings.get("ebay_daily_call_limit", 5000))
    reserve = int(settings.get("ebay_daily_reserve_calls", 350))
    minimum = int(settings.get("minimum_scan_budget_calls", 40))
    state = load_state(state_path())
    rolling_used = _rolling_calls(state)
    fallback_available = max(0, daily_limit - rolling_used - reserve)

    analytics_remaining: int | None = None
    client_id = os.getenv("EBAY_CLIENT_ID", "").strip()
    client_secret = os.getenv("EBAY_CLIENT_SECRET", "").strip()
    if client_id and client_secret:
        try:
            probe = EbayClient(
                client_id,
                client_secret,
                environment=str(settings.get("environment", "production")),
                marketplace_id=str(settings.get("marketplace_id", "EBAY_DE")),
                delivery_country=str(settings.get("delivery_country", "DE")),
                delay_seconds=0,
                max_calls=1,
            )
            snapshot = probe.get_rate_limits()
            if snapshot is not None:
                analytics_remaining = snapshot.remaining
        except Exception:
            analytics_remaining = None

    available = fallback_available
    source = "rollierende 24h-Schätzung"
    if analytics_remaining is not None:
        available = min(available, max(0, analytics_remaining - reserve))
        source = "eBay Developer Analytics + 24h-Fallback"
    allowed = min(configured, available)
    if allowed < minimum:
        return QuotaDecision(
            allowed_calls=max(0, allowed),
            rolling_used=rolling_used,
            analytics_remaining=analytics_remaining,
            skipped=True,
            note=f"eBay-Budget zu niedrig ({allowed} sichere Calls; Quelle: {source})",
        )

    search_calls = int(settings.get("max_search_calls_per_run", 24))
    market_budget = min(int(settings.get("max_market_comp_calls_per_run", 80)), max(8, allowed // 4))
    reprice_budget = min(int(settings.get("max_reprice_comp_calls_per_run", 60)), max(4, allowed // 6))
    detail_budget = min(
        int(settings.get("max_detail_calls_per_run", 470)),
        max(0, allowed - search_calls - max(market_budget, reprice_budget)),
    )
    _merge_override(
        {
            "max_ebay_calls_per_run": allowed,
            "max_detail_calls_per_run": detail_budget,
            "max_market_comp_calls_per_run": market_budget,
            "max_reprice_comp_calls_per_run": reprice_budget,
        }
    )
    return QuotaDecision(
        allowed_calls=allowed,
        rolling_used=rolling_used,
        analytics_remaining=analytics_remaining,
        skipped=False,
        note=f"eBay-Budget {allowed} Calls freigegeben ({source})",
    )
