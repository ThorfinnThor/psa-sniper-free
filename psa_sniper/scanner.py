from __future__ import annotations

import os
import sys
from dataclasses import replace
from datetime import timedelta
from typing import Any

from .cert_extract import extract_cert_from_aspects, extract_cert_from_title, grade_from_listing
from .config import load_queries, load_settings, state_path
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
from .models import CertCandidate, Listing, MarketValue, RunStats, ScoredHit
from .notify import configured_channels, notify
from .ocr import extract_cert_from_images, ocr_enabled
from .psa import PSABudgetExceeded, PSAClient
from .psa_auth import normalize_psa_access_token
from .report import write_reports
from .scoring import (
    identity_overlap,
    is_psa10,
    market_value_from_cert,
    preliminary_score,
    score_hit,
)
from .state import (
    append_run,
    get_cached_cert,
    get_cached_market,
    hit_to_record,
    is_alerted,
    load_state,
    mark_alerted,
    mark_processed,
    processed_recently,
    prune_state,
    put_cached_cert,
    put_cached_market,
    save_state,
    select_queries,
    upsert_history,
)
from .util import iso_z, utc_now


def _merge_listing(summary: Listing, detail: Listing) -> Listing:
    return replace(
        detail,
        created_at=detail.created_at or summary.created_at,
        end_at=detail.end_at or summary.end_at,
        url=detail.url or summary.url,
        price=detail.price or summary.price,
        shipping=detail.shipping or summary.shipping,
        image_urls=detail.image_urls or summary.image_urls,
        matched_queries=list(dict.fromkeys([*summary.matched_queries, *detail.matched_queries])),
    )


def _within_window(listing: Listing, minutes: int) -> bool:
    if listing.created_at is None:
        return True
    return listing.created_at >= utc_now() - timedelta(minutes=minutes)


def _price_ok(listing: Listing, low: float, high: float) -> bool:
    money = listing.total_cost or listing.price
    if not money:
        return True
    return low <= money.value <= high


def _buying_option_ok(listing: Listing, include_auctions: bool) -> bool:
    return include_auctions or not listing.pure_auction


def _market_in_listing_currency(
    market: MarketValue | None,
    listing: Listing,
    fx: FXRates,
) -> MarketValue | None:
    if not market:
        return None
    target = listing.total_cost or listing.price
    if not target:
        return market
    converted = fx.convert(market.money, target.currency)
    if not converted:
        return market if market.money.currency == target.currency else None
    rate = converted.value / market.money.value if market.money.value else 1.0
    return MarketValue(
        converted,
        market.source,
        market.confidence,
        market.sample_size,
        market_type=market.market_type,
        required_edge=market.required_edge,
        unique_sellers=market.unique_sellers,
        price_low=market.price_low * rate if market.price_low is not None else None,
        price_high=market.price_high * rate if market.price_high is not None else None,
        dispersion=market.dispersion,
    )


def _market_needs_upgrade(market: MarketValue | None) -> bool:
    return market is None or market.confidence.casefold() == "niedrig"


def _prefer_market_value(current: MarketValue | None, candidate: MarketValue | None) -> MarketValue | None:
    if candidate is None:
        return current
    if current is None:
        return candidate
    confidence_rank = {"hoch": 3, "mittel": 2, "niedrig": 1}
    source_rank = {"psa_sales": 4, "ebay_active": 3, "ebay_active_provisional": 2, "psa_estimate": 1}
    current_key = (
        confidence_rank.get(current.confidence.casefold(), 0),
        source_rank.get(current.market_type, 0),
        int(current.unique_sellers or 0),
        int(current.sample_size or 0),
    )
    candidate_key = (
        confidence_rank.get(candidate.confidence.casefold(), 0),
        source_rank.get(candidate.market_type, 0),
        int(candidate.unique_sellers or 0),
        int(candidate.sample_size or 0),
    )
    return candidate if candidate_key > current_key else current


def _public_note(exc: Exception) -> str:
    if isinstance(exc, EbayError):
        return str(exc)
    return exc.__class__.__name__


def _ocr_cert_safe_for_market(listing: Listing, cert_candidate: CertCandidate | None, cert: Any) -> bool:
    if not cert_candidate or not cert:
        return False
    if not cert_candidate.source.startswith("OCR"):
        return True
    return identity_overlap(listing, cert) > 0


def _psa_status_label(status: str) -> str:
    return {
        "ok": "OK",
        "abgelehnt": "ABGELEHNT",
        "rate_limited": "RATE-LIMIT",
        "nicht_konfiguriert": "NICHT KONFIGURIERT",
        "netzwerkfehler": "NETZWERKFEHLER",
        "servicefehler": "SERVICEFEHLER",
        "http_fehler": "HTTP-FEHLER",
        "budget": "BUDGET ERSCHÖPFT",
        "nicht_getestet": "NICHT GETESTET",
    }.get(status, "UNBEKANNT")


def run_scan() -> int:
    started = utc_now()
    settings = load_settings()
    queries_all = load_queries()
    path = state_path()
    state = prune_state(load_state(path), settings)
    notes: list[str] = []

    if not queries_all:
        notes.append("Keine Suchabfragen konfiguriert")
        completed = utc_now()
        stats = RunStats(iso_z(started), iso_z(completed), 0, 0, 0, 0, 0, 0, 0, 0, notes)
        append_run(state, stats, int(settings.get("run_history_max_items", 100)))
        save_state(path, state)
        write_reports([], [], stats)
        return 2

    selected_queries, next_cursor = select_queries(
        queries_all,
        int(state.get("query_cursor", 0)),
        int(settings.get("max_search_calls_per_run", 12)),
    )
    state["query_cursor"] = next_cursor

    client_id = os.getenv("EBAY_CLIENT_ID", "").strip()
    client_secret = os.getenv("EBAY_CLIENT_SECRET", "").strip()
    if not client_id or not client_secret:
        notes.append("EBAY_CLIENT_ID oder EBAY_CLIENT_SECRET fehlt")
        completed = utc_now()
        stats = RunStats(iso_z(started), iso_z(completed), len(selected_queries), 0, 0, 0, 0, 0, 0, 0, notes)
        append_run(state, stats, int(settings.get("run_history_max_items", 100)))
        save_state(path, state)
        write_reports([], [], stats)
        return 2

    ebay = EbayClient(
        client_id,
        client_secret,
        environment=str(settings.get("environment", "production")),
        marketplace_id=str(settings.get("marketplace_id", "EBAY_DE")),
        delivery_country=str(settings.get("delivery_country", "DE")),
        buyer_postal_code=str(settings.get("buyer_postal_code", "")),
        delay_seconds=float(settings.get("request_delay_seconds", 0.25)),
        max_calls=int(settings.get("max_ebay_calls_per_run", 30)),
    )
    psa = PSAClient(
        access_token=normalize_psa_access_token(os.getenv("PSA_ACCESS_TOKEN")),
        web_fallback=bool(settings.get("enable_psa_web_fallback", True)),
        delay_seconds=float(settings.get("psa_request_delay_seconds", 0.8)),
        max_calls=int(settings.get("max_psa_calls_per_run", 8)),
    )
    psa_api_status = psa.validate_access_token()
    psa_api_success_baseline = psa.api_successes

    fx = FXRates()
    fx.refresh()
    window_minutes = int(settings.get("run_window_minutes", 75))
    started_after = utc_now() - timedelta(minutes=window_minutes)
    summaries: dict[str, Listing] = {}
    raw_seen = 0

    try:
        for query in selected_queries:
            rows = ebay.search(
                query,
                limit=int(settings.get("max_results_per_query", 45)),
                started_after=started_after,
            )
            raw_seen += len(rows)
            for item in rows:
                if not item.item_id or not _within_window(item, window_minutes):
                    continue
                if not _price_ok(item, float(settings.get("minimum_price", 0)), float(settings.get("maximum_price", 1e9))):
                    continue
                if not _buying_option_ok(item, bool(settings.get("include_auctions", False))):
                    continue
                grade_hint = grade_from_listing(item)
                if grade_hint and not is_psa10(grade_hint):
                    continue
                if item.item_id in summaries:
                    summaries[item.item_id].matched_queries = list(
                        dict.fromkeys([*summaries[item.item_id].matched_queries, query])
                    )
                else:
                    item.matched_queries = [query]
                    summaries[item.item_id] = item
    except (EbayError, EbayBudgetExceeded) as exc:
        notes.append(_public_note(exc))
        notes.append(f"PSA API live: {_psa_status_label(psa_api_status)}")
        completed = utc_now()
        stats = RunStats(
            iso_z(started), iso_z(completed), len(selected_queries), raw_seen,
            len(summaries), 0, psa.calls_made, 0, 0, ebay.calls_made, notes,
        )
        append_run(state, stats, int(settings.get("run_history_max_items", 100)))
        save_state(path, state)
        write_reports([], [], stats)
        print(notes[-1], file=sys.stderr)
        return 3

    priority_terms = list(settings.get("priority_terms") or [])
    ordered = sorted(
        summaries.values(),
        key=lambda listing: (
            -preliminary_score(listing, priority_terms),
            len(listing.title.split()),
            -(listing.created_at.timestamp() if listing.created_at else 0.0),
        ),
    )
    cooldown = int(settings.get("processed_cooldown_minutes", 360))
    candidates = [
        row for row in ordered if not processed_recently(state, row.item_id, cooldown)
    ][: int(settings.get("max_detail_calls_per_run", 18))]

    scored: list[ScoredHit] = []
    ocr_items = 0
    max_ocr_items = int(settings.get("max_ocr_items_per_run", 8))
    cert_cache_days = int(settings.get("cert_cache_days", 7))
    market_cache_hours = int(settings.get("market_cache_hours", 8))
    max_comp_calls = int(settings.get("max_market_comp_calls_per_run", 0))
    comp_search_limit = int(settings.get("market_comp_search_limit", 100))
    comp_required_edge = float(settings.get("market_active_required_edge", 0.20))
    listing_market_min_prelim = int(settings.get("market_listing_fallback_min_preliminary_score", 7))
    market_comp_calls = 0
    max_psa_market_web_calls = int(settings.get("max_psa_market_web_calls_per_run", 0))
    psa_market_web_calls = 0
    psa_market_web_min_prelim = int(settings.get("psa_market_web_min_preliminary_score", 8))

    for summary in candidates:
        try:
            detail = ebay.get_item(summary.item_id)
        except EbayBudgetExceeded:
            notes.append("eBay-Call-Budget vor Ende der Detailprüfung ausgeschöpft")
            break
        except EbayError:
            notes.append("Mindestens eine eBay-Detailprüfung ist fehlgeschlagen")
            continue

        listing = _merge_listing(summary, detail)
        cert_candidate: CertCandidate | None = extract_cert_from_aspects(listing)
        if not cert_candidate:
            cert_candidate = extract_cert_from_title(listing.title)
        if not cert_candidate and ocr_enabled() and ocr_items < max_ocr_items and listing.image_urls:
            ocr_items += 1
            cert_candidate = extract_cert_from_images(
                listing.image_urls,
                int(settings.get("max_ocr_images_per_item", 3)),
            )

        cert = None
        if cert_candidate:
            cert = get_cached_cert(state, cert_candidate.number, cert_cache_days)
            if cert is None:
                try:
                    cert = psa.get_cert(cert_candidate.number)
                except PSABudgetExceeded:
                    notes.append("PSA-Call-Budget ausgeschöpft; weitere Kandidaten ohne POP-Anreicherung")
                    cert = None
                if cert:
                    put_cached_cert(state, cert)

        market = _market_in_listing_currency(market_value_from_cert(cert), listing, fx)

        if (
            cert and cert.valid and market is None
            and psa_market_web_calls < max_psa_market_web_calls
            and preliminary_score(listing, priority_terms) >= psa_market_web_min_prelim
            and not psa.web_rate_limited
        ):
            before = psa.calls_made
            try:
                enriched = psa.enrich_market_data(cert)
            except PSABudgetExceeded:
                enriched = cert
            if psa.calls_made > before:
                psa_market_web_calls += 1
            if enriched is not cert:
                cert = enriched
                put_cached_cert(state, cert)
                market = _market_in_listing_currency(market_value_from_cert(cert), listing, fx)

        if (
            _market_needs_upgrade(market) and cert and cert.valid and is_psa10(cert.grade)
            and _ocr_cert_safe_for_market(listing, cert_candidate, cert)
        ):
            target = listing.total_cost or listing.price
            if target:
                fingerprint = f"{cert_fingerprint(cert)}|{target.currency.upper()}"
                if fingerprint.strip("|"):
                    cached, cached_market = get_cached_market(state, fingerprint, market_cache_hours)
                    if cached and cached_market is not None:
                        market = _prefer_market_value(market, cached_market)
                    elif market_comp_calls < max_comp_calls:
                        try:
                            comp_rows: list[Listing] = []
                            values = []
                            for comp_query in dict.fromkeys(
                                query for query in [build_comp_query(cert), build_fallback_comp_query(cert)] if query
                            ):
                                if market_comp_calls >= max_comp_calls:
                                    break
                                rows = ebay.search(comp_query, limit=comp_search_limit, started_after=None, offset=0)
                                market_comp_calls += 1
                                comp_rows.extend(rows)
                                values = exact_active_comps(
                                    comp_rows, cert, target_currency=target.currency, fx=fx,
                                    exclude_item_id=listing.item_id,
                                )
                                if (
                                    len(values) < 3 and len(rows) >= comp_search_limit
                                    and market_comp_calls < max_comp_calls
                                ):
                                    rows2 = ebay.search(
                                        comp_query, limit=comp_search_limit,
                                        started_after=None, offset=comp_search_limit,
                                    )
                                    market_comp_calls += 1
                                    comp_rows.extend(rows2)
                                    values = exact_active_comps(
                                        comp_rows, cert, target_currency=target.currency, fx=fx,
                                        exclude_item_id=listing.item_id,
                                    )
                                if len(values) >= 3:
                                    break
                            comp_market = market_value_from_active_comps(
                                values, medium_required_edge=comp_required_edge,
                            )
                            market = _prefer_market_value(market, comp_market)
                            if comp_market is not None:
                                put_cached_market(state, fingerprint, comp_market)
                        except EbayBudgetExceeded:
                            notes.append("eBay-Budget für weitere Preis-Comps ausgeschöpft")
                        except EbayError:
                            notes.append("Mindestens eine eBay-Preisvergleichssuche ist fehlgeschlagen")

        if (
            _market_needs_upgrade(market)
            and market_comp_calls < max_comp_calls
            and preliminary_score(listing, priority_terms) >= listing_market_min_prelim
        ):
            identity = listing_comp_identity(listing)
            target = listing.total_cost or listing.price
            if identity and target:
                fingerprint = f"{listing_comp_fingerprint(identity)}|{target.currency.upper()}"
                cached, cached_market = get_cached_market(state, fingerprint, market_cache_hours)
                if cached and cached_market is not None:
                    market = _prefer_market_value(market, cached_market)
                else:
                    try:
                        comp_rows: list[Listing] = []
                        values = []
                        for comp_query in build_listing_comp_queries(identity):
                            if market_comp_calls >= max_comp_calls:
                                break
                            rows = ebay.search(comp_query, limit=comp_search_limit, started_after=None, offset=0)
                            market_comp_calls += 1
                            comp_rows.extend(rows)
                            values = exact_active_comps_for_listing(
                                comp_rows, identity, target_currency=target.currency, fx=fx,
                                exclude_item_id=listing.item_id,
                            )
                            if (
                                len(values) < 3 and len(rows) >= comp_search_limit
                                and market_comp_calls < max_comp_calls
                            ):
                                rows2 = ebay.search(
                                    comp_query, limit=comp_search_limit,
                                    started_after=None, offset=comp_search_limit,
                                )
                                market_comp_calls += 1
                                comp_rows.extend(rows2)
                                values = exact_active_comps_for_listing(
                                    comp_rows, identity, target_currency=target.currency, fx=fx,
                                    exclude_item_id=listing.item_id,
                                )
                            if len(values) >= 3:
                                break
                        listing_market = market_value_from_listing_comps(
                            values, required_edge=max(0.25, comp_required_edge),
                        )
                        market = _prefer_market_value(market, listing_market)
                        if listing_market is not None:
                            put_cached_market(state, fingerprint, listing_market)
                    except EbayBudgetExceeded:
                        notes.append("eBay-Budget für weitere Listing-Preis-Comps ausgeschöpft")
                    except EbayError:
                        notes.append("Mindestens eine Listing-Preisvergleichssuche ist fehlgeschlagen")

        hit = score_hit(
            listing,
            cert_number=cert_candidate.number if cert_candidate else None,
            cert_source=cert_candidate.source if cert_candidate else None,
            cert_confidence=cert_candidate.confidence if cert_candidate else None,
            cert=cert,
            market_value_listing_currency=market,
            priority_terms=priority_terms,
            demand_terms=list(settings.get("demand_terms") or []),
        )
        scored.append(hit)
        mark_processed(state, listing.item_id, hit.score)
        if hit.score >= int(settings.get("dashboard_min_score", 7)):
            upsert_history(state, hit, int(settings.get("hit_threshold", 11)))

    scored.sort(key=lambda row: row.score, reverse=True)
    threshold = int(settings.get("hit_threshold", 11))
    dashboard_min = int(settings.get("dashboard_min_score", 7))
    hits = [row for row in scored if row.score >= threshold][: int(settings.get("max_hits_per_run", 12))]
    near_hits = [row for row in scored if dashboard_min <= row.score < threshold]

    channels = configured_channels()
    for hit in hits:
        if is_alerted(state, hit.listing.item_id):
            continue
        statuses = notify(hit)
        if not channels or any(statuses.values()):
            mark_alerted(state, hit.listing.item_id, statuses or {"dashboard": True})
        else:
            notes.append("Mindestens ein Alert konnte nicht zugestellt werden; Live-Recheck/Alert fehlgeschlagen")

    if market_comp_calls:
        notes.append(f"{market_comp_calls} eBay-Preisvergleichssuche(n) ausgeführt")
    if psa_market_web_calls:
        notes.append(f"{psa_market_web_calls} PSA-Sales-Webanreicherung(en) versucht")

    cert_detected = sum(1 for row in scored if row.cert_number)
    cert_verified = sum(1 for row in scored if row.cert and row.cert.valid)
    pop_available = sum(1 for row in scored if row.cert and row.cert.population is not None)
    price_indicators = sum(1 for row in scored if row.market_value is not None)
    ebay_comp_prices = sum(
        1 for row in scored
        if row.market_value and row.market_value.market_type in {"ebay_active", "ebay_active_provisional"}
    )
    verified_edges = sum(1 for row in scored if row.price_status == "verified_edge")
    candidate_api_successes = max(0, psa.api_successes - psa_api_success_baseline)
    notes.append(f"PSA API live: {_psa_status_label(psa_api_status)}")
    notes.append(
        "Coverage: "
        f"Details={len(scored)}; Cert={cert_detected}; PSA={candidate_api_successes}; "
        f"Verifiziert={cert_verified}; POP={pop_available}; Preis={price_indicators}; "
        f"eBayCompSuche={market_comp_calls}; eBayCompPreis={ebay_comp_prices}; Edge={verified_edges}"
    )

    completed = utc_now()
    stats = RunStats(
        started_at=iso_z(started),
        completed_at=iso_z(completed),
        queries_used=len(selected_queries),
        listings_seen=raw_seen,
        fresh_listings=len(summaries),
        detailed_candidates=len(scored),
        psa_lookups=psa.calls_made,
        hits=len(hits),
        near_hits=len(near_hits),
        ebay_calls=ebay.calls_made,
        notes=list(dict.fromkeys(notes)),
    )
    run_candidates = [*hits, *near_hits][: int(settings.get("max_run_results_per_run", 60))]
    run_results = [hit_to_record(row, threshold) for row in run_candidates]
    append_run(
        state, stats, int(settings.get("run_history_max_items", 100)), results=run_results,
    )
    prune_state(state, settings)
    save_state(path, state)
    write_reports(hits, near_hits, stats)

    privacy = os.getenv("PRIVACY_MODE", "false").strip().lower() in {"1", "true", "yes"}
    if privacy:
        print(f"Scan abgeschlossen: {len(hits)} Hits, {len(near_hits)} Beobachtungen, {ebay.calls_made} eBay-Calls.")
    else:
        for hit in hits:
            print(f"HIT {hit.score}: {hit.listing.title}\n  {hit.listing.url}")
        if not hits:
            print(f"Keine Hits >= {threshold}; {len(near_hits)} Beobachtungen gespeichert.")
    return 0
