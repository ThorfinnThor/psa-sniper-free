from __future__ import annotations

import os
import sys
from dataclasses import dataclass, field
from datetime import timedelta
from typing import Any

from .config import ROOT, load_settings, state_path
from .ebay import EbayBudgetExceeded, EbayClient, EbayError
from .fx import FXRates
from .identity import PricingIdentity, pricing_identity_from_dict, pricing_identity_from_listing
from .listing_market import (
    build_listing_comp_queries,
    exact_active_comps_for_listing,
    listing_comp_detail_candidates,
    listing_comp_fingerprint,
    listing_comp_identity,
    market_value_from_listing_comps,
)
from .live_check import listing_available, merge_live_listing
from .market import (
    build_comp_query,
    build_fallback_comp_query,
    cert_fingerprint,
    exact_active_comps,
    find_leave_one_out_deal,
    market_value_from_active_comps,
)
from .models import CertCandidate, Listing, MarketValue, Money, PSACertInfo, ScoredHit
from .notify import configured_channels, notify
from .point130 import Point130Sale, load_point130_sales, point130_market_for_identity
from .renaiss import RenaissClient, RenaissError
from .scoring import identity_overlap, is_psa10, score_hit
from .state import (
    cert_from_dict,
    get_cached_market,
    hit_to_record,
    load_state,
    mark_alerted,
    market_from_dict,
    prune_state,
    put_cached_market,
    save_state,
    should_alert,
)
from .util import iso_z, parse_iso_datetime, utc_now

WEAK_PRICE_STATUSES = {"unverified", "weak_indicator"}
REFRESHABLE_PRICE_STATUSES = {"no_edge", "over_market"}


@dataclass(slots=True)
class RepriceResult:
    scored: list[ScoredHit] = field(default_factory=list)
    checked: int = 0
    improved: int = 0
    calls: int = 0
    live_rechecks: int = 0
    expired: int = 0
    live_errors: int = 0
    budget_stops: int = 0
    comp_detail_calls: int = 0
    comp_detail_errors: int = 0
    point130_matches: int = 0
    renaiss_matches: int = 0
    renaiss_cache_hits: int = 0
    renaiss_errors: int = 0
    secondary: list[ScoredHit] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)


def _money(data: Any) -> Money | None:
    if not isinstance(data, dict):
        return None
    try:
        return Money(float(data["value"]), str(data["currency"]))
    except (KeyError, TypeError, ValueError):
        return None


def _synthetic_seller_flags(row: dict[str, Any]) -> tuple[float | None, int | None]:
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
    image_urls = [str(value) for value in row.get("image_urls", []) if isinstance(value, str) and value]
    if not image_urls and row.get("image_url"):
        image_urls = [str(row["image_url"])]
    buying_options = [str(value) for value in row.get("buying_options", []) if value]
    if not buying_options:
        buying_options = ["AUCTION"] if row.get("pure_auction") else ["FIXED_PRICE"]
    feedback_pct, feedback_score = _synthetic_seller_flags(row)

    aspects: dict[str, list[str]] = {}
    identity = pricing_identity_from_dict(row.get("pricing_identity"))
    if identity:
        aspects["Card Number"] = [identity.card_number]
        aspects["Character"] = [" ".join(identity.subjects)]
        if identity.language:
            aspects["Language"] = [identity.language]
        if identity.set_code:
            aspects["Set Code"] = [identity.set_code]

    return Listing(
        item_id=item_id,
        title=title,
        url=str(row.get("url") or ""),
        price=price,
        created_at=parse_iso_datetime(row.get("created_at")),
        end_at=parse_iso_datetime(row.get("end_at")),
        shipping=shipping,
        image_urls=image_urls,
        aspects=aspects,
        buying_options=buying_options,
        condition=row.get("condition"),
        returns_accepted=row.get("returns_accepted"),
        item_location_country=row.get("item_location_country"),
        matched_queries=[str(value) for value in row.get("matched_queries", []) if value],
        seller_feedback_percentage=feedback_pct,
        seller_feedback_score=feedback_score,
    )


def _history_pricing_identity(row: dict[str, Any], listing: Listing) -> PricingIdentity | None:
    """Prefer newly parsed high-signal collector numbers over stale state."""
    stored = pricing_identity_from_dict(row.get("pricing_identity"))
    title_only = Listing(
        item_id=listing.item_id,
        title=listing.title,
        url=listing.url,
        price=listing.price,
        created_at=listing.created_at,
    )
    reparsed = pricing_identity_from_listing(title_only)
    if reparsed and "/" in reparsed.card_number and (
        stored is None or "/" not in stored.card_number
    ):
        return reparsed
    return stored or listing_comp_identity(listing)


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
    return CertCandidate(number=number, source=str(row.get("cert_source") or "Historie"), confidence=confidence)


def _market_key(market: MarketValue | None) -> tuple[int, int, int, int]:
    if market is None:
        return (0, 0, 0, 0)
    confidence_rank = {"hoch": 3, "mittel": 2, "niedrig": 1}
    source_rank = {
        "psa_sales": 7,
        "renaiss_fmv": 6,
        "point130_sold": 5,
        "ebay_active": 3,
        "ebay_active_provisional": 2,
        "psa_estimate": 1,
    }
    return (
        confidence_rank.get(market.confidence.casefold(), 0),
        source_rank.get(market.market_type, 0),
        int(market.unique_sellers or 0),
        int(market.sample_size or 0),
    )


def _prefer_market(
    current: MarketValue | None,
    candidate: MarketValue | None,
    *,
    refresh_same_type: bool = False,
) -> MarketValue | None:
    if candidate is None:
        return current
    if current is None:
        return candidate
    if _market_key(candidate) > _market_key(current):
        return candidate
    if (
        refresh_same_type
        and candidate.market_type == current.market_type
        and candidate.confidence.casefold() == current.confidence.casefold()
    ):
        # Gleiche Quellenklasse: beim fälligen Refresh soll der frische Markt
        # den alten Wert ersetzen. Eine schwächere Quellenklasse darf dagegen
        # niemals PSA-Sales oder eine bessere Quelle überschreiben.
        return candidate
    return current


def _cert_safe_for_market(listing: Listing, candidate: CertCandidate | None, cert: PSACertInfo | None, trusted: bool) -> bool:
    if not trusted or candidate is None or cert is None:
        return False
    if not candidate.source.startswith("OCR"):
        return True
    return identity_overlap(listing, cert) > 0


def _refresh_minutes(row: dict[str, Any], settings: dict[str, Any]) -> int:
    market = row.get("market_value") if isinstance(row.get("market_value"), dict) else {}
    market_type = str(market.get("market_type") or "")
    if market_type == "psa_estimate" or row.get("price_status") in WEAK_PRICE_STATUSES:
        return max(30, int(settings.get("reprice_min_age_minutes", 60)))
    if market_type == "ebay_active_provisional":
        return 4 * 60
    if market_type == "ebay_active":
        return 8 * 60
    if market_type == "psa_sales":
        return 24 * 60
    if market_type == "point130_sold":
        return 24 * 60
    if market_type == "renaiss_fmv":
        return 24 * 60
    return 6 * 60


def _due_history_rows(state: dict[str, Any], settings: dict[str, Any]) -> list[tuple[int, dict[str, Any]]]:
    now = utc_now()
    max_age = timedelta(hours=max(1, int(settings.get("reprice_max_history_age_hours", 72))))
    max_items = max(0, int(settings.get("max_reprice_items_per_run", 60)))
    candidates: list[tuple[int, dict[str, Any], float]] = []
    for index, row in enumerate(list(state.get("history", []))):
        if not isinstance(row, dict) or row.get("pure_auction"):
            continue
        availability = str(row.get("availability_status") or "active")
        if availability in {"ended", "unavailable"}:
            continue
        last_seen = parse_iso_datetime(row.get("last_seen_at") or row.get("first_seen_at"))
        if last_seen is None or now - last_seen > max_age:
            continue
        # Current buy hits and previously failed live checks receive a fresh
        # COMPACT validation with highest priority.
        if row.get("is_hit") or availability == "check_failed":
            priority = 10_000_000_000 + float(row.get("score") or 0) * 1_000_000
            candidates.append((index, row, priority))
            continue
        status = str(row.get("price_status") or "unverified")
        if status not in WEAK_PRICE_STATUSES | REFRESHABLE_PRICE_STATUSES:
            continue
        checked_at = parse_iso_datetime(row.get("price_checked_at"))
        reference = checked_at or last_seen
        try:
            attempts = max(0, int(row.get("price_check_attempts") or 0))
        except (TypeError, ValueError):
            attempts = 0
        wait_minutes = _refresh_minutes(row, settings)
        if status in WEAK_PRICE_STATUSES:
            wait_minutes = min(24 * 60, wait_minutes * (2 ** min(attempts, 3)))
        if now - reference < timedelta(minutes=wait_minutes):
            continue
        priority = float(row.get("score") or 0) * 1_000_000 - reference.timestamp()
        candidates.append((index, row, priority))
    candidates.sort(key=lambda item: item[2], reverse=True)
    return [(index, row) for index, row, _ in candidates[:max_items]]


def _point130_priority_rows(
    state: dict[str, Any],
    settings: dict[str, Any],
    sales: list[Point130Sale],
    fx: FXRates,
) -> list[tuple[int, dict[str, Any]]]:
    """Prioritize recent history rows improved by newly imported sold comps."""
    if not sales:
        return []
    now = utc_now()
    max_age = timedelta(hours=max(1, int(settings.get("reprice_max_history_age_hours", 72))))
    candidates: list[tuple[int, dict[str, Any], float]] = []
    for index, row in enumerate(list(state.get("history", []))):
        if not isinstance(row, dict) or row.get("pure_auction"):
            continue
        if str(row.get("availability_status") or "active") in {"ended", "unavailable"}:
            continue
        last_seen = parse_iso_datetime(row.get("last_seen_at") or row.get("first_seen_at"))
        if last_seen is None or now - last_seen > max_age:
            continue
        listing = listing_from_history(row)
        if listing is None:
            continue
        target = listing.total_cost or listing.price
        identity = _history_pricing_identity(row, listing)
        if target is None or identity is None:
            continue
        candidate = point130_market_for_identity(
            sales,
            identity,
            target_currency=target.currency,
            fx=fx,
            max_age_days=int(settings.get("point130_sold_max_age_days", 365)),
            required_edge=float(settings.get("point130_sold_required_edge", 0.12)),
        )
        current = market_from_dict(row.get("market_value"))
        if candidate is None or _market_key(candidate) <= _market_key(current):
            continue
        priority = float(row.get("score") or 0) * 1_000_000 + last_seen.timestamp()
        candidates.append((index, row, priority))
    candidates.sort(key=lambda item: item[2], reverse=True)
    return [(index, row) for index, row, _ in candidates]


def _budget_left(ebay: Any, start_calls: int, max_comp_calls: int) -> bool:
    return int(getattr(ebay, "calls_made", 0)) - start_calls < max_comp_calls


def _search_cert_comps(
    ebay: Any,
    cert: PSACertInfo,
    listing: Listing,
    target_currency: str,
    fx: FXRates,
    comp_limit: int,
    start_calls: int,
    max_comp_calls: int,
) -> tuple[list[Listing], list[Money]]:
    comp_rows: list[Listing] = []
    values: list[Money] = []
    for query in dict.fromkeys(value for value in (build_comp_query(cert), build_fallback_comp_query(cert)) if value):
        if not _budget_left(ebay, start_calls, max_comp_calls):
            break
        page = ebay.search(query, limit=comp_limit, started_after=None, offset=0)
        comp_rows.extend(page)
        values = exact_active_comps(comp_rows, cert, target_currency=target_currency, fx=fx, exclude_item_id=listing.item_id)
        if len(values) < 3 and len(page) >= comp_limit and _budget_left(ebay, start_calls, max_comp_calls):
            page2 = ebay.search(query, limit=comp_limit, started_after=None, offset=comp_limit)
            comp_rows.extend(page2)
            values = exact_active_comps(comp_rows, cert, target_currency=target_currency, fx=fx, exclude_item_id=listing.item_id)
        if len(values) >= 3:
            break
    return comp_rows, values


def _search_listing_comps(
    ebay: Any,
    identity: Any,
    listing: Listing,
    target_currency: str,
    fx: FXRates,
    comp_limit: int,
    start_calls: int,
    max_comp_calls: int,
    detail_limit: int = 0,
) -> tuple[list[Listing], list[Money], int, int]:
    comp_rows: list[Listing] = []
    values: list[Money] = []
    for query in build_listing_comp_queries(identity):
        if not _budget_left(ebay, start_calls, max_comp_calls):
            break
        page = ebay.search(query, limit=comp_limit, started_after=None, offset=0)
        comp_rows.extend(page)
        values = exact_active_comps_for_listing(comp_rows, identity, target_currency=target_currency, fx=fx, exclude_item_id=listing.item_id)
        if len(values) < 3 and len(page) >= comp_limit and _budget_left(ebay, start_calls, max_comp_calls):
            page2 = ebay.search(query, limit=comp_limit, started_after=None, offset=comp_limit)
            comp_rows.extend(page2)
            values = exact_active_comps_for_listing(comp_rows, identity, target_currency=target_currency, fx=fx, exclude_item_id=listing.item_id)
        if len(values) >= 3:
            break

    detail_calls = 0
    detail_errors = 0
    market = market_value_from_listing_comps(values)
    if detail_limit > 0 and (market is None or market.confidence.casefold() == "niedrig"):
        attempted: set[str] = set()
        candidates = listing_comp_detail_candidates(
            comp_rows,
            identity,
            exclude_item_id=listing.item_id,
            attempted_item_ids=attempted,
        )
        for summary in candidates[:detail_limit]:
            if not _budget_left(ebay, start_calls, max_comp_calls):
                break
            attempted.add(summary.item_id)
            try:
                detail = ebay.get_item(summary.item_id)
            except EbayBudgetExceeded:
                break
            except EbayError:
                detail_calls += 1
                detail_errors += 1
                continue
            detail_calls += 1
            merged = merge_live_listing(summary, detail)
            comp_rows = [
                merged if row.item_id == summary.item_id else row
                for row in comp_rows
            ]
        if detail_calls:
            values = exact_active_comps_for_listing(
                comp_rows,
                identity,
                target_currency=target_currency,
                fx=fx,
                exclude_item_id=listing.item_id,
            )
    return comp_rows, values, detail_calls, detail_errors


def _maybe_secondary_candidate(
    result: RepriceResult,
    history: list[dict[str, Any]],
    comp_rows: list[Listing],
    values: list[Money],
    cert: PSACertInfo,
    ebay: Any,
    fx: FXRates,
    settings: dict[str, Any],
    start_calls: int,
    max_comp_calls: int,
) -> None:
    found = find_leave_one_out_deal(values, min_edge=float(settings.get("secondary_discovery_min_edge", 0.25)))
    if not found:
        return
    item_id, market, _ = found
    if any(str(row.get("item_id") or "") == item_id for row in history):
        return
    if not _budget_left(ebay, start_calls, max_comp_calls):
        return
    source = next((row for row in comp_rows if row.item_id == item_id), None)
    if source is None:
        return
    try:
        live = ebay.get_item(item_id)
    except EbayError:
        return
    if not listing_available(live):
        return
    candidate = merge_live_listing(source, live)
    hit = score_hit(
        candidate,
        cert_number=cert.cert_number,
        cert_source="exakte Comp-Identität",
        cert_confidence=0.99,
        cert=cert,
        market_value_listing_currency=market,
        priority_terms=list(settings.get("priority_terms") or []),
        demand_terms=list(settings.get("demand_terms") or []),
    )
    if hit.score < int(settings.get("dashboard_min_score", 4)):
        return
    record = hit_to_record(hit, int(settings.get("hit_threshold", 11)))
    record["discovery_source"] = "eBay Comp Leave-One-Out"
    record["price_checked_at"] = iso_z(utc_now())
    record["availability_checked_at"] = iso_z(utc_now())
    history.append(record)
    result.secondary.append(hit)
    result.scored.append(hit)


def reprice_state(
    state: dict[str, Any],
    settings: dict[str, Any],
    ebay: Any,
    fx: FXRates,
    *,
    max_comp_calls: int,
    point130_sales: list[Point130Sale] | None = None,
    renaiss: RenaissClient | None = None,
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
    max_detail_calls = max(0, int(settings.get("max_reprice_comp_detail_calls_per_run", 0)))
    max_details_per_candidate = max(
        0,
        int(settings.get("max_reprice_comp_details_per_candidate", 3)),
    )
    history = list(state.get("history", []))
    imported_sales = point130_sales or []

    priority_rows = _point130_priority_rows(state, settings, imported_sales, fx)
    normal_rows = _due_history_rows(state, settings)
    max_items = max(0, int(settings.get("max_reprice_items_per_run", 60)))
    queued_rows: list[tuple[int, dict[str, Any]]] = []
    queued_indexes: set[int] = set()
    if max_items > 0:
        for candidate in [*priority_rows, *normal_rows]:
            if candidate[0] in queued_indexes:
                continue
            queued_indexes.add(candidate[0])
            queued_rows.append(candidate)
            if len(queued_rows) >= max_items:
                break

    for index, old in queued_rows:
        if not _budget_left(ebay, start_calls, max_comp_calls):
            break
        stored = listing_from_history(old)
        if stored is None:
            continue
        result.checked += 1
        now_text = iso_z(utc_now())
        try:
            live = ebay.get_item(stored.item_id, compact=True)
            result.live_rechecks += 1
        except EbayBudgetExceeded:
            result.notes.append("Repricing: Live-Recheck wegen eBay-Budget gestoppt")
            result.budget_stops += 1
            break
        except EbayError as exc:
            updated = dict(old)
            updated["availability_checked_at"] = now_text
            updated["is_hit"] = False
            if exc.missing:
                updated["availability_status"] = "unavailable"
                updated["price_status"] = "unavailable"
                updated.pop("availability_error", None)
                result.expired += 1
            else:
                updated["availability_status"] = "check_failed"
                updated["availability_error"] = "temporary"
                result.live_errors += 1
                result.notes.append("Repricing: mindestens ein Live-Recheck war vorübergehend nicht möglich")
            history[index] = updated
            continue
        if not listing_available(live):
            updated = dict(old)
            updated["availability_status"] = "ended"
            updated["availability_checked_at"] = now_text
            updated["is_hit"] = False
            updated["price_status"] = "unavailable"
            history[index] = updated
            result.expired += 1
            continue
        listing = merge_live_listing(stored, live)

        cert = _cert_from_history(old)
        cert_candidate = _cert_candidate_from_history(old)
        current_market = market_from_dict(old.get("market_value"))
        market = current_market
        trusted = bool(old.get("cert_trusted", True))
        target = listing.total_cost or listing.price
        force_refresh = str(old.get("price_status") or "") in REFRESHABLE_PRICE_STATUSES
        cert_comp_rows: list[Listing] = []
        cert_values: list[Money] = []
        identity = _history_pricing_identity(old, listing)

        if target and identity:
            fingerprint = f"renaiss|{listing_comp_fingerprint(identity)}|{target.currency.upper()}"
            cached, cached_market = get_cached_market(
                state,
                fingerprint,
                int(settings.get("renaiss_cache_hours", 24)),
            )
            if cached and cached_market is not None:
                selected = _prefer_market(market, cached_market)
                if selected is not market:
                    result.renaiss_cache_hits += 1
                market = selected
            if (
                renaiss is not None
                and not cached
                and (market is None or market.confidence.casefold() == "niedrig")
                and renaiss.calls_made < renaiss.max_calls
                and not renaiss.rate_limited
            ):
                try:
                    match = renaiss.market_for_identity(
                        identity,
                        target_currency=target.currency,
                        fx=fx,
                        max_sale_age_days=int(settings.get("renaiss_max_sale_age_days", 365)),
                    )
                except RenaissError:
                    result.renaiss_errors += 1
                else:
                    if match is None:
                        put_cached_market(state, fingerprint, None)
                    else:
                        market = _prefer_market(market, match.market)
                        put_cached_market(state, fingerprint, match.market)
                        result.renaiss_matches += 1

        if target and imported_sales and identity:
            point130_market = point130_market_for_identity(
                imported_sales,
                identity,
                target_currency=target.currency,
                fx=fx,
                max_age_days=int(settings.get("point130_sold_max_age_days", 365)),
                required_edge=float(settings.get("point130_sold_required_edge", 0.12)),
            )
            if point130_market is not None:
                result.point130_matches += 1
                market = _prefer_market(market, point130_market)

        if (
            target
            and (force_refresh or market is None or market.confidence.casefold() == "niedrig")
            and cert
            and cert.valid
            and is_psa10(cert.grade)
            and _cert_safe_for_market(listing, cert_candidate, cert, trusted)
            and _budget_left(ebay, start_calls, max_comp_calls)
        ):
            fingerprint = f"{cert_fingerprint(cert)}|{target.currency.upper()}"
            cached, cached_market = get_cached_market(state, fingerprint, int(settings.get("market_cache_hours", 8)))
            if cached and cached_market is not None and not force_refresh:
                market = _prefer_market(market, cached_market)
            else:
                try:
                    cert_comp_rows, cert_values = _search_cert_comps(
                        ebay, cert, listing, target.currency, fx, comp_limit, start_calls, max_comp_calls
                    )
                    comp_market = market_value_from_active_comps(cert_values, medium_required_edge=comp_required_edge)
                    market = _prefer_market(
                        market,
                        comp_market,
                        refresh_same_type=force_refresh,
                    )
                    if comp_market is not None:
                        put_cached_market(state, fingerprint, comp_market)
                except EbayBudgetExceeded:
                    result.notes.append("Repricing: eBay-Budget ausgeschöpft")
                except EbayError:
                    result.notes.append("Repricing: mindestens eine Cert-Comp-Suche fehlgeschlagen")

        if cert and cert.valid and cert_values:
            _maybe_secondary_candidate(
                result, history, cert_comp_rows, cert_values, cert, ebay, fx, settings, start_calls, max_comp_calls
            )

        if (
            target
            and (market is None or market.confidence.casefold() == "niedrig")
            and _budget_left(ebay, start_calls, max_comp_calls)
        ):
            if identity:
                fingerprint = f"{listing_comp_fingerprint(identity)}|{target.currency.upper()}"
                cached, cached_market = get_cached_market(state, fingerprint, int(settings.get("market_cache_hours", 8)))
                if cached and cached_market is not None:
                    market = _prefer_market(market, cached_market)
                if force_refresh or not cached or cached_market is None or cached_market.confidence.casefold() == "niedrig":
                    try:
                        remaining_detail_calls = max(0, max_detail_calls - result.comp_detail_calls)
                        detail_limit = min(max_details_per_candidate, remaining_detail_calls)
                        _, values, detail_calls, detail_errors = _search_listing_comps(
                            ebay,
                            identity,
                            listing,
                            target.currency,
                            fx,
                            comp_limit,
                            start_calls,
                            max_comp_calls,
                            detail_limit,
                        )
                        result.comp_detail_calls += detail_calls
                        result.comp_detail_errors += detail_errors
                        listing_market = market_value_from_listing_comps(values, required_edge=max(0.25, comp_required_edge))
                        market = _prefer_market(market, listing_market)
                        if listing_market is not None:
                            put_cached_market(state, fingerprint, listing_market)
                    except EbayBudgetExceeded:
                        result.notes.append("Repricing: eBay-Budget ausgeschöpft")
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
            import_risk_extra_edge=float(settings.get("import_risk_extra_edge", 0.0)),
            import_exempt_countries=list(settings.get("import_risk_exempt_countries") or []),
            unknown_shipping_extra_edge=float(settings.get("unknown_shipping_extra_edge", 0.0)),
        )
        result.scored.append(hit)
        if _market_key(market) > _market_key(current_market):
            result.improved += 1

        updated = hit_to_record(hit, threshold)
        for field_name in ("first_seen_at", "last_seen_at", "created_at"):
            if old.get(field_name) is not None:
                updated[field_name] = old[field_name]
        updated["availability_status"] = "active"
        updated.pop("availability_error", None)
        updated["availability_checked_at"] = now_text
        updated["price_checked_at"] = now_text
        try:
            old_attempts = max(0, int(old.get("price_check_attempts") or 0))
        except (TypeError, ValueError):
            old_attempts = 0
        updated["price_check_attempts"] = old_attempts + 1
        if _market_key(market) > _market_key(current_market):
            updated["price_last_improved_at"] = now_text
        elif old.get("price_last_improved_at"):
            updated["price_last_improved_at"] = old["price_last_improved_at"]
        if not updated.get("pricing_identity") and old.get("pricing_identity"):
            updated["pricing_identity"] = old["pricing_identity"]
        history[index] = updated

    history.sort(key=lambda row: row.get("last_seen_at", ""), reverse=True)
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
            f"{result.live_rechecks} live geprüft, {result.expired} beendet, "
            f"{result.live_errors} Live-Fehler, {len(result.secondary)} sekundär entdeckt, "
            f"{result.renaiss_matches} Renaiss-Matches, "
            f"{result.comp_detail_calls} Comp-Details, {result.comp_detail_errors} Detailfehler, "
            f"{hit_count} Kauf-Hit(s), "
            f"{result.calls} zusätzliche eBay-Calls.\n"
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
    queue_limit = min(remaining, max(0, int(settings.get("max_reprice_comp_calls_per_run", 60))))
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
    point130_sales = (
        load_point130_sales()
        if bool(settings.get("enable_point130_legacy", False))
        else []
    )
    renaiss = RenaissClient.from_env(
        max_calls=int(settings.get("max_renaiss_reprice_calls_per_run", 2)),
    )
    if not renaiss.authenticated:
        renaiss.max_calls = min(
            renaiss.max_calls,
            int(settings.get("max_renaiss_public_reprice_calls_per_run", 0)),
        )
    result = reprice_state(
        state,
        settings,
        ebay,
        fx,
        max_comp_calls=queue_limit,
        point130_sales=point130_sales,
        renaiss=renaiss,
    )
    if result.checked <= 0 and not result.secondary:
        return 0

    threshold = int(settings.get("hit_threshold", 11))
    dashboard_min = int(settings.get("dashboard_min_score", 4))
    repriced_hits = [row for row in result.scored if row.score >= threshold and row.price_status == "verified_edge"]
    repriced_near = [row for row in result.scored if dashboard_min <= row.score < threshold]
    channels = configured_channels()
    for hit in repriced_hits:
        if not should_alert(
            state, hit,
            min_price_drop_pct=float(settings.get("alert_rearm_price_drop_pct", 0.10)),
            min_edge_improvement=float(settings.get("alert_rearm_edge_improvement", 0.10)),
        ):
            continue
        statuses = notify(hit)
        if not channels or any(statuses.values()):
            mark_alerted(state, hit.listing.item_id, statuses or {"dashboard": True}, hit=hit)

    if runs:
        latest = dict(state["runs"][0])
        latest["repricing_checked"] = result.checked
        latest["repricing_improved"] = result.improved
        latest["repricing_hits"] = len(repriced_hits)
        latest["repricing_observations"] = len(repriced_near)
        latest["repricing_calls"] = result.calls
        latest["repricing_live_rechecks"] = result.live_rechecks
        latest["repricing_expired"] = result.expired
        latest["repricing_live_errors"] = result.live_errors
        latest["repricing_budget_stops"] = result.budget_stops
        latest["repricing_comp_detail_calls"] = result.comp_detail_calls
        latest["repricing_comp_detail_errors"] = result.comp_detail_errors
        latest["repricing_point130_matches"] = result.point130_matches
        latest["repricing_renaiss_matches"] = result.renaiss_matches
        latest["repricing_renaiss_cache_hits"] = result.renaiss_cache_hits
        latest["repricing_renaiss_errors"] = result.renaiss_errors
        latest["secondary_candidates"] = len(result.secondary)
        latest["total_ebay_calls"] = previous_calls + result.calls
        notes = list(latest.get("notes") or [])
        notes.extend(result.notes)
        notes.append(
            "Repricing: "
            f"geprüft={result.checked}; verbessert={result.improved}; live={result.live_rechecks}; "
            f"beendet={result.expired}; LiveFehler={result.live_errors}; "
            f"CompDetails={result.comp_detail_calls}; Detailfehler={result.comp_detail_errors}; "
            f"Renaiss={result.renaiss_matches}; RenaissCache={result.renaiss_cache_hits}; "
            f"RenaissFehler={result.renaiss_errors}; "
            f"sekundär={len(result.secondary)}; Hits={len(repriced_hits)}; "
            f"eBayCalls={result.calls}"
        )
        latest["notes"] = list(dict.fromkeys(notes))
        latest["repricing_results"] = [hit_to_record(row, threshold) for row in result.scored][
            : int(settings.get("max_run_results_per_run", 60))
        ]
        state["runs"][0] = latest

    save_state(path, state)
    _append_summary(result, len(repriced_hits))
    print(
        "Repricing abgeschlossen: "
        f"{result.checked} geprüft, {result.improved} verbessert, {result.live_rechecks} live geprüft, "
        f"{result.expired} beendet, {result.live_errors} Live-Fehler, "
        f"{result.comp_detail_calls} Comp-Details, {result.comp_detail_errors} Detailfehler, "
        f"{result.renaiss_matches} Renaiss-Matches, "
        f"{len(result.secondary)} sekundär entdeckt, {len(repriced_hits)} Hits, "
        f"{result.calls} eBay-Calls."
    )
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(run_repricing_queue())
    except Exception as exc:
        print(f"Repricing-Warnung: {exc.__class__.__name__}", file=sys.stderr)
        raise SystemExit(0) from exc
