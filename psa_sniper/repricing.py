from __future__ import annotations

import os
import sys
from dataclasses import dataclass, field
from datetime import timedelta
from typing import Any

from .config import ROOT, load_settings, state_path
from .ebay import EbayBudgetExceeded, EbayClient, EbayError
from .fx import FXRates
from .listing_market import (
    build_listing_comp_queries,
    exact_active_comps_for_listing,
    listing_comp_fingerprint,
    listing_comp_identity,
    market_value_from_listing_comps,
)
from .market import (
    build_comp_query,
    build_fallback_comp_query,
    cert_fingerprint,
    exact_active_comps,
    market_value_from_active_comps,
)
from .models import CertCandidate, Listing, MarketValue, Money, PSACertInfo, ScoredHit
from .notify import configured_channels, notify
from .scoring import identity_overlap, is_psa10, score_hit
from .state import (
    cert_from_dict,
    get_cached_market,
    hit_to_record,
    is_alerted,
    load_state,
    mark_alerted,
    market_from_dict,
    prune_state,
    put_cached_market,
    save_state,
)
from .util import iso_z, parse_iso_datetime, utc_now


WEAK_PRICE_STATUSES = {"unverified", "weak_indicator"}


@dataclass(slots=True)
class RepriceResult:
    scored: list[ScoredHit] = field(default_factory=list)
    checked: int = 0
    improved: int = 0
    calls: int = 0
    notes: list[str] = field(default_factory=list)


def _money(data: Any) -> Money | None:
    if not isinstance(data, dict):
        return None
    try:
        return Money(float(data["value"]), str(data["currency"]))
    except (KeyError, TypeError, ValueError):
        return None


def _synthetic_seller_flags(row: dict[str, Any]) -> tuple[float | None, int | None]:
    """Recreate only old score penalties without persisting seller identity/data."""
    labels = {
        str(item.get("label") or "")
        for item in row.get("score_breakdown", [])
        if isinstance(item, dict)
    }
    feedback_pct: float | None = None
    if "Verkäuferbewertung unter 95 %" in labels:
        feedback_pct = 94.0
    elif "Verkäuferbewertung unter 98 %" in labels:
        feedback_pct = 97.0
    feedback_score = 5 if "sehr wenige Verkäuferbewertungen" in labels else None
    return feedback_pct, feedback_score


def listing_from_history(row: dict[str, Any]) -> Listing | None:
    item_id = str(row.get("item_id") or "").strip()
    title = str(row.get("title") or "").strip()
    if not item_id or not title:
        return None

    price = _money(row.get("price"))
    shipping = _money(row.get("shipping"))
    if price is None:
        price = _money(row.get("total_cost"))
        shipping = None
    if price is None:
        return None

    image_urls = [
        str(value)
        for value in row.get("image_urls", [])
        if isinstance(value, str) and value
    ]
    if not image_urls and row.get("image_url"):
        image_urls = [str(row["image_url"])]

    buying_options = [str(value) for value in row.get("buying_options", []) if value]
    if not buying_options:
        buying_options = ["AUCTION"] if row.get("pure_auction") else ["FIXED_PRICE"]

    feedback_pct, feedback_score = _synthetic_seller_flags(row)
    return Listing(
        item_id=item_id,
        title=title,
        url=str(row.get("url") or ""),
        price=price,
        created_at=parse_iso_datetime(row.get("created_at")),
        end_at=parse_iso_datetime(row.get("end_at")),
        shipping=shipping,
        image_urls=image_urls,
        buying_options=buying_options,
        condition=row.get("condition"),
        returns_accepted=row.get("returns_accepted"),
        item_location_country=row.get("item_location_country"),
        matched_queries=[str(value) for value in row.get("matched_queries", []) if value],
        seller_feedback_percentage=feedback_pct,
        seller_feedback_score=feedback_score,
    )


def _cert_from_history(row: dict[str, Any]) -> PSACertInfo | None:
    data = row.get("cert")
    return cert_from_dict(data) if isinstance(data, dict) else None


def _cert_candidate_from_history(row: dict[str, Any]) -> CertCandidate | None:
    number = str(row.get("cert_number") or "").strip()
    if not number:
        return None
    try:
        confidence = float(row.get("cert_confidence") or 0.0)
    except (TypeError, ValueError):
        confidence = 0.0
    return CertCandidate(
        number=number,
        source=str(row.get("cert_source") or "Historie"),
        confidence=confidence,
    )


def _market_key(market: MarketValue | None) -> tuple[int, int, int]:
    if market is None:
        return (0, 0, 0)
    confidence_rank = {"hoch": 3, "mittel": 2, "niedrig": 1}
    source_rank = {
        "psa_sales": 4,
        "ebay_active": 3,
        "ebay_active_provisional": 2,
        "psa_estimate": 1,
    }
    return (
        confidence_rank.get(market.confidence.casefold(), 0),
        source_rank.get(market.market_type, 0),
        int(market.sample_size or 0),
    )


def _prefer_market(current: MarketValue | None, candidate: MarketValue | None) -> MarketValue | None:
    if candidate is None:
        return current
    return candidate if _market_key(candidate) > _market_key(current) else current


def _needs_upgrade(market: MarketValue | None) -> bool:
    return market is None or market.confidence.casefold() == "niedrig"


def _cert_safe_for_market(
    listing: Listing,
    candidate: CertCandidate | None,
    cert: PSACertInfo | None,
    trusted: bool,
) -> bool:
    if not trusted or candidate is None or cert is None:
        return False
    if not candidate.source.startswith("OCR"):
        return True
    return identity_overlap(listing, cert) > 0


def _due_history_rows(
    state: dict[str, Any],
    settings: dict[str, Any],
) -> list[tuple[int, dict[str, Any]]]:
    now = utc_now()
    base_wait = max(1, int(settings.get("reprice_min_age_minutes", 60)))
    max_age = timedelta(hours=max(1, int(settings.get("reprice_max_history_age_hours", 72))))
    max_items = max(0, int(settings.get("max_reprice_items_per_run", 16)))
    candidates: list[tuple[int, dict[str, Any], float]] = []

    for index, row in enumerate(list(state.get("history", []))):
        if not isinstance(row, dict):
            continue
        if row.get("price_status") not in WEAK_PRICE_STATUSES:
            continue
        if row.get("is_hit") or row.get("pure_auction"):
            continue
        last_seen = parse_iso_datetime(row.get("last_seen_at") or row.get("first_seen_at"))
        if last_seen is None or now - last_seen > max_age:
            continue

        try:
            attempts = max(0, int(row.get("price_check_attempts") or 0))
        except (TypeError, ValueError):
            attempts = 0
        checked_at = parse_iso_datetime(row.get("price_checked_at"))
        reference = checked_at or last_seen
        wait_minutes = min(24 * 60, base_wait * (2 ** min(attempts, 4)))
        if now - reference < timedelta(minutes=wait_minutes):
            continue

        # Highest raw score first; for equal scores retry the oldest price check first.
        priority = float(row.get("score") or 0) * 1_000_000 - reference.timestamp()
        candidates.append((index, row, priority))

    candidates.sort(key=lambda item: item[2], reverse=True)
    return [(index, row) for index, row, _ in candidates[:max_items]]


def reprice_state(
    state: dict[str, Any],
    settings: dict[str, Any],
    ebay: Any,
    fx: FXRates,
    *,
    max_comp_calls: int,
) -> RepriceResult:
    result = RepriceResult()
    if max_comp_calls <= 0:
        return result

    threshold = int(settings.get("hit_threshold", 11))
    comp_limit = int(settings.get("market_comp_search_limit", 100))
    comp_required_edge = float(settings.get("market_active_required_edge", 0.20))
    priority_terms = list(settings.get("priority_terms") or [])
    demand_terms = list(settings.get("demand_terms") or [])
    start_calls = int(getattr(ebay, "calls_made", 0))
    history = list(state.get("history", []))

    for index, old in _due_history_rows(state, settings):
        if int(getattr(ebay, "calls_made", 0)) - start_calls >= max_comp_calls:
            break
        listing = listing_from_history(old)
        if listing is None:
            continue

        cert = _cert_from_history(old)
        cert_candidate = _cert_candidate_from_history(old)
        current_market = market_from_dict(old.get("market_value"))
        market = current_market
        trusted = bool(old.get("cert_trusted", True))
        target = listing.total_cost or listing.price
        budget_exhausted = False

        if (
            target
            and _needs_upgrade(market)
            and cert
            and cert.valid
            and is_psa10(cert.grade)
            and _cert_safe_for_market(listing, cert_candidate, cert, trusted)
        ):
            fingerprint = f"{cert_fingerprint(cert)}|{target.currency.upper()}"
            cached, cached_market = get_cached_market(
                state,
                fingerprint,
                int(settings.get("market_cache_hours", 8)),
            )
            if cached and cached_market is not None:
                market = _prefer_market(market, cached_market)
            else:
                comp_rows: list[Listing] = []
                values: list[Money] = []
                try:
                    for query in dict.fromkeys(
                        value
                        for value in (build_comp_query(cert), build_fallback_comp_query(cert))
                        if value
                    ):
                        if int(getattr(ebay, "calls_made", 0)) - start_calls >= max_comp_calls:
                            break
                        rows = ebay.search(query, limit=comp_limit, started_after=None)
                        comp_rows.extend(rows)
                        values = exact_active_comps(
                            comp_rows,
                            cert,
                            target_currency=target.currency,
                            fx=fx,
                            exclude_item_id=listing.item_id,
                        )
                        if len(values) >= 3:
                            break
                    comp_market = market_value_from_active_comps(
                        values,
                        medium_required_edge=comp_required_edge,
                    )
                    market = _prefer_market(market, comp_market)
                    if comp_market is not None:
                        put_cached_market(state, fingerprint, comp_market)
                except EbayBudgetExceeded:
                    result.notes.append("Repricing: eBay-Budget ausgeschöpft")
                    budget_exhausted = True
                except EbayError:
                    result.notes.append("Repricing: mindestens eine Cert-Comp-Suche fehlgeschlagen")

        if target and _needs_upgrade(market) and not budget_exhausted:
            identity = listing_comp_identity(listing)
            if identity:
                fingerprint = (
                    f"{listing_comp_fingerprint(identity)}|{target.currency.upper()}"
                )
                cached, cached_market = get_cached_market(
                    state,
                    fingerprint,
                    int(settings.get("market_cache_hours", 8)),
                )
                if cached and cached_market is not None:
                    market = _prefer_market(market, cached_market)
                else:
                    comp_rows = []
                    values = []
                    try:
                        for query in build_listing_comp_queries(identity):
                            if int(getattr(ebay, "calls_made", 0)) - start_calls >= max_comp_calls:
                                break
                            rows = ebay.search(query, limit=comp_limit, started_after=None)
                            comp_rows.extend(rows)
                            values = exact_active_comps_for_listing(
                                comp_rows,
                                identity,
                                target_currency=target.currency,
                                fx=fx,
                                exclude_item_id=listing.item_id,
                            )
                            if len(values) >= 3:
                                break
                        listing_market = market_value_from_listing_comps(
                            values,
                            required_edge=max(0.25, comp_required_edge),
                        )
                        market = _prefer_market(market, listing_market)
                        if listing_market is not None:
                            put_cached_market(state, fingerprint, listing_market)
                    except EbayBudgetExceeded:
                        result.notes.append("Repricing: eBay-Budget ausgeschöpft")
                        budget_exhausted = True
                    except EbayError:
                        result.notes.append("Repricing: mindestens eine Listing-Comp-Suche fehlgeschlagen")

        hit = score_hit(
            listing,
            cert_number=cert_candidate.number if cert_candidate else None,
            cert_source=cert_candidate.source if cert_candidate else None,
            cert_confidence=cert_candidate.confidence if cert_candidate else None,
            cert=cert,
            market_value_listing_currency=market,
            priority_terms=priority_terms,
            demand_terms=demand_terms,
        )
        result.scored.append(hit)
        result.checked += 1
        improved = _market_key(market) > _market_key(current_market)
        if improved:
            result.improved += 1

        updated = hit_to_record(hit, threshold)
        # Repricing is not a new eBay sighting; preserve discovery/last-seen times.
        for field_name in ("first_seen_at", "last_seen_at", "created_at", "end_at"):
            if old.get(field_name) is not None:
                updated[field_name] = old[field_name]
        now_text = iso_z(utc_now())
        updated["price_checked_at"] = now_text
        try:
            old_attempts = max(0, int(old.get("price_check_attempts") or 0))
        except (TypeError, ValueError):
            old_attempts = 0
        updated["price_check_attempts"] = old_attempts + 1
        if improved:
            updated["price_last_improved_at"] = now_text
        elif old.get("price_last_improved_at"):
            updated["price_last_improved_at"] = old["price_last_improved_at"]
        history[index] = updated

        if budget_exhausted:
            break

    state["history"] = history
    result.calls = int(getattr(ebay, "calls_made", 0)) - start_calls
    return result


def _append_summary(result: RepriceResult, hit_count: int) -> None:
    report = ROOT / "reports" / "summary.md"
    if not report.exists():
        return
    with report.open("a", encoding="utf-8") as handle:
        handle.write(
            "\n- Repricing-Queue: "
            f"{result.checked} geprüft, {result.improved} Preisquelle(n) verbessert, "
            f"{hit_count} Kauf-Hit(s), {result.calls} zusätzliche eBay-Calls.\n"
        )


def run_repricing_queue() -> int:
    settings = load_settings()
    if not bool(settings.get("enable_repricing_queue", True)):
        return 0

    path = state_path()
    state = prune_state(load_state(path), settings)
    runs = list(state.get("runs", []))
    previous_calls = int(runs[0].get("ebay_calls") or 0) if runs else 0
    hard_limit = int(settings.get("max_ebay_calls_per_run", 575))
    remaining = max(0, hard_limit - previous_calls)
    queue_limit = min(
        remaining,
        max(0, int(settings.get("max_reprice_comp_calls_per_run", 24))),
    )
    if queue_limit <= 0:
        return 0

    client_id = os.getenv("EBAY_CLIENT_ID", "").strip()
    client_secret = os.getenv("EBAY_CLIENT_SECRET", "").strip()
    if not client_id or not client_secret:
        return 0

    ebay = EbayClient(
        client_id,
        client_secret,
        environment=str(settings.get("environment", "production")),
        marketplace_id=str(settings.get("marketplace_id", "EBAY_DE")),
        delivery_country=str(settings.get("delivery_country", "DE")),
        buyer_postal_code=str(settings.get("buyer_postal_code", "")),
        delay_seconds=float(settings.get("request_delay_seconds", 0.25)),
        max_calls=queue_limit,
    )
    fx = FXRates()
    fx.refresh()
    result = reprice_state(
        state,
        settings,
        ebay,
        fx,
        max_comp_calls=queue_limit,
    )
    if result.checked <= 0:
        return 0

    threshold = int(settings.get("hit_threshold", 11))
    dashboard_min = int(settings.get("dashboard_min_score", 4))
    repriced_hits = [row for row in result.scored if row.score >= threshold]
    repriced_near = [row for row in result.scored if dashboard_min <= row.score < threshold]

    channels = configured_channels()
    for hit in repriced_hits:
        if is_alerted(state, hit.listing.item_id):
            continue
        statuses = notify(hit)
        if not channels or any(statuses.values()):
            mark_alerted(state, hit.listing.item_id, statuses or {"dashboard": True})

    if runs:
        latest = dict(state["runs"][0])
        latest["ebay_calls"] = previous_calls + result.calls
        latest["hits"] = int(latest.get("hits") or 0) + len(repriced_hits)
        latest["near_hits"] = int(latest.get("near_hits") or 0) + len(repriced_near)
        notes = list(latest.get("notes") or [])
        notes.extend(result.notes)
        notes.append(
            "Repricing: "
            f"geprüft={result.checked}; verbessert={result.improved}; "
            f"Hits={len(repriced_hits)}; eBayCalls={result.calls}"
        )
        latest["notes"] = list(dict.fromkeys(notes))

        existing_results = [
            item for item in latest.get("results", []) if isinstance(item, dict)
        ]
        new_results = [hit_to_record(row, threshold) for row in result.scored]
        merged: dict[str, dict[str, Any]] = {}
        for item in [*new_results, *existing_results]:
            item_id = str(item.get("item_id") or "")
            if item_id and item_id not in merged:
                merged[item_id] = item
        latest["results"] = list(merged.values())[
            : int(settings.get("max_run_results_per_run", 60))
        ]
        state["runs"][0] = latest

    save_state(path, state)
    _append_summary(result, len(repriced_hits))
    print(
        "Repricing abgeschlossen: "
        f"{result.checked} geprüft, {result.improved} verbessert, "
        f"{len(repriced_hits)} Hits, {result.calls} eBay-Calls."
    )
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(run_repricing_queue())
    except Exception as exc:  # optional maintenance must never expose secrets
        print(f"Repricing-Warnung: {exc.__class__.__name__}", file=sys.stderr)
        raise SystemExit(0)
