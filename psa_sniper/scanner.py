from __future__ import annotations

import os
import sys
from dataclasses import dataclass, field, replace
from datetime import timedelta
from typing import Any

from .cert_extract import extract_cert_from_aspects, extract_cert_from_title, grade_from_listing
from .config import load_queries, load_settings, state_path
from .ebay import EbayBudgetExceeded, EbayClient, EbayError
from .fx import FXRates
from .identity import pricing_identity_from_listing
from .listing_market import (
    ListingCompIdentity,
    build_listing_comp_queries,
    exact_active_comps_for_listing,
    listing_comp_detail_candidates,
    listing_comp_fingerprint,
    listing_comp_identity,
    market_value_from_listing_comps,
)
from .live_check import refresh_hit_for_purchase
from .market import (
    build_comp_query,
    build_fallback_comp_query,
    cert_fingerprint,
    exact_active_comps,
    market_value_from_active_comps,
)
from .models import CertCandidate, Listing, MarketValue, PSACertInfo, RunStats, ScoredHit
from .notify import configured_channels, notify
from .ocr import extract_cert_from_images, ocr_enabled
from .point130 import load_point130_sales, point130_market_for_identity
from .psa import PSABudgetExceeded, PSAClient, cert_needs_api_upgrade, merge_cert_info
from .psa_auth import normalize_psa_access_token
from .renaiss import RenaissClient, RenaissError
from .report import write_reports
from .scoring import (
    cert_identity_trust,
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
    load_state,
    mark_alerted,
    mark_processed,
    processed_recently,
    prune_state,
    put_cached_cert,
    put_cached_market,
    save_state,
    select_queries,
    should_alert,
    upsert_history,
)
from .util import iso_z, normalize_text, utc_now


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
    source_rank = {
        "psa_sales": 7,
        "renaiss_fmv": 6,
        "point130_sold": 5,
        "ebay_active": 3,
        "ebay_active_provisional": 2,
        "psa_estimate": 1,
    }
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


def _cert_safe_for_market(listing: Listing, cert_candidate: CertCandidate | None, cert: Any) -> bool:
    if not cert_candidate or not cert:
        return False
    trusted, _ = cert_identity_trust(
        listing,
        cert,
        cert_source=cert_candidate.source,
        cert_confidence=cert_candidate.confidence,
    )
    return trusted


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


def _new_price_diagnostics() -> dict[str, int]:
    return {
        "OhnePreis": 0,
        "UnterGate": 0,
        "KeineIdentitaet": 0,
        "KeineSuchtreffer": 0,
        "KeineExaktenComps": 0,
        "ZuWenigeComps": 0,
        "Budget": 0,
        "Suchfehler": 0,
        "KeinZielpreis": 0,
        "Sonstiges": 0,
        "Schwach": 0,
        "SchwachPSAEstimate": 0,
        "SchwachComps": 0,
        "SchwachVerkaeufer": 0,
        "SchwachStreuung": 0,
        "SchwachIdentitaet": 0,
    }


def _classify_price_gap(
    market: MarketValue | None,
    *,
    target_available: bool,
    preliminary: int,
    min_preliminary: int,
    identity_available: bool,
    search_attempted: bool,
    search_rows: int,
    exact_matches: int,
    budget_blocked: bool,
    search_error: bool,
) -> str | None:
    if market is not None:
        return None
    if not target_available:
        return "KeinZielpreis"
    if preliminary < min_preliminary:
        return "UnterGate"
    if budget_blocked:
        return "Budget"
    if search_error:
        return "Suchfehler"
    if not identity_available:
        return "KeineIdentitaet"
    if search_attempted and search_rows <= 0:
        return "KeineSuchtreffer"
    if search_attempted and exact_matches <= 0:
        return "KeineExaktenComps"
    if search_attempted and exact_matches < 3:
        return "ZuWenigeComps"
    return "Sonstiges"


def _weak_market_diagnostics(market: MarketValue | None) -> list[str]:
    if market is None or market.confidence.casefold() != "niedrig":
        return []
    keys = ["Schwach"]
    if market.market_type == "psa_estimate":
        keys.append("SchwachPSAEstimate")
        return keys
    if market.market_type == "point130_sold":
        if int(market.sample_size or 0) < 2:
            keys.append("SchwachComps")
        if market.dispersion is not None and float(market.dispersion) > 0.45:
            keys.append("SchwachStreuung")
        return keys
    if market.market_type == "renaiss_fmv":
        return keys
    if market.market_type not in {"ebay_active", "ebay_active_provisional"}:
        return keys
    if int(market.sample_size or 0) < 3:
        keys.append("SchwachComps")
    if market.unique_sellers is not None and int(market.unique_sellers) < 3:
        keys.append("SchwachVerkaeufer")
    if market.dispersion is not None and float(market.dispersion) > 0.35:
        keys.append("SchwachStreuung")
    if market.market_type == "ebay_active_provisional" or len(keys) == 1:
        keys.append("SchwachIdentitaet")
    return keys


def _price_diag_note(values: dict[str, int]) -> str:
    order = (
        "OhnePreis", "UnterGate", "KeineIdentitaet", "KeineSuchtreffer",
        "KeineExaktenComps", "ZuWenigeComps", "Budget", "Suchfehler",
        "KeinZielpreis", "Sonstiges", "Schwach", "SchwachPSAEstimate",
        "SchwachComps", "SchwachVerkaeufer", "SchwachStreuung",
        "SchwachIdentitaet",
    )
    return "PriceDiag: " + "; ".join(f"{key}={int(values.get(key, 0))}" for key in order)


@dataclass(slots=True)
class _CompSearchTask:
    mode: str
    query: str
    offset: int = 0


@dataclass(slots=True)
class _PriceCandidate:
    listing: Listing
    preliminary: int
    cert_candidate: CertCandidate | None
    cert: PSACertInfo | None
    cert_market_safe: bool
    market: MarketValue | None
    listing_identity: ListingCompIdentity | None = None
    cert_market_fingerprint: str | None = None
    listing_market_fingerprint: str | None = None
    screening_score: int = 0
    search_attempted: bool = False
    search_rows: int = 0
    exact_matches: int = 0
    budget_blocked: bool = False
    search_error: bool = False
    search_tasks: list[_CompSearchTask] = field(default_factory=list)
    cert_comp_rows: list[Listing] = field(default_factory=list)
    listing_comp_rows: list[Listing] = field(default_factory=list)
    comp_detail_attempts: set[str] = field(default_factory=set)
    comp_detail_errors: int = 0
    merged_searches: int = 0

    @property
    def target_available(self) -> bool:
        return bool(self.listing.total_cost or self.listing.price)

    @property
    def identity_available(self) -> bool:
        return bool(self.cert_market_fingerprint or self.listing_identity)


def _score_price_candidate(
    candidate: _PriceCandidate,
    *,
    priority_terms: list[str],
    demand_terms: list[str],
    settings: dict[str, Any],
    market: MarketValue | None,
) -> ScoredHit:
    cert_candidate = candidate.cert_candidate
    return score_hit(
        candidate.listing,
        cert_number=cert_candidate.number if cert_candidate else None,
        cert_source=cert_candidate.source if cert_candidate else None,
        cert_confidence=cert_candidate.confidence if cert_candidate else None,
        cert=candidate.cert,
        market_value_listing_currency=market,
        priority_terms=priority_terms,
        demand_terms=demand_terms,
        import_risk_extra_edge=float(settings.get("import_risk_extra_edge", 0.0)),
        import_exempt_countries=list(settings.get("import_risk_exempt_countries") or []),
        unknown_shipping_extra_edge=float(settings.get("unknown_shipping_extra_edge", 0.0)),
    )


def _price_candidate_priority(candidate: _PriceCandidate) -> tuple[int, int, int, int, float]:
    """Rank globally before spending scarce comparison-search calls."""
    cert_ready = int(
        bool(
            candidate.cert
            and candidate.cert.valid
            and candidate.cert_market_safe
            and is_psa10(candidate.cert.grade)
        )
    )
    searchable = int(candidate.identity_available and candidate.target_available)
    created = candidate.listing.created_at.timestamp() if candidate.listing.created_at else 0.0
    return (
        searchable,
        cert_ready,
        candidate.screening_score,
        candidate.preliminary,
        created,
    )


def _prepare_comp_search_tasks(candidate: _PriceCandidate) -> None:
    tasks: list[_CompSearchTask] = []
    candidate.merged_searches = 0

    def add_task(mode: str, query: str) -> None:
        key = normalize_text(query)
        if not key:
            return
        duplicate = next(
            (task for task in tasks if normalize_text(task.query) == key and task.offset == 0),
            None,
        )
        if duplicate is None:
            tasks.append(_CompSearchTask(mode, query))
            return
        if duplicate.mode != mode and duplicate.mode != "cert+listing":
            duplicate.mode = "cert+listing"
            candidate.merged_searches += 1

    if candidate.cert_market_fingerprint and candidate.cert:
        queries = dict.fromkeys(
            query
            for query in [
                build_comp_query(candidate.cert),
                build_fallback_comp_query(candidate.cert),
            ]
            if query
        )
        for query in queries:
            add_task("cert", query)
    if candidate.listing_market_fingerprint and candidate.listing_identity:
        for query in dict.fromkeys(build_listing_comp_queries(candidate.listing_identity)):
            if query:
                add_task("listing", query)
    candidate.search_tasks = tasks


def _run_comp_search_task(
    candidate: _PriceCandidate,
    task: _CompSearchTask,
    *,
    ebay: EbayClient,
    fx: FXRates,
    state: dict[str, Any],
    search_limit: int,
    required_edge: float,
) -> None:
    target = candidate.listing.total_cost or candidate.listing.price
    if target is None:
        return
    rows = ebay.search(
        task.query,
        limit=search_limit,
        started_after=None,
        offset=task.offset,
    )
    candidate.search_attempted = True
    candidate.search_rows += len(rows)

    exact_counts: list[int] = []
    if task.mode in {"cert", "cert+listing"} and candidate.cert:
        candidate.cert_comp_rows.extend(rows)
        values = exact_active_comps(
            candidate.cert_comp_rows,
            candidate.cert,
            target_currency=target.currency,
            fx=fx,
            exclude_item_id=candidate.listing.item_id,
        )
        candidate.exact_matches = max(candidate.exact_matches, len(values))
        comp_market = market_value_from_active_comps(
            values,
            medium_required_edge=required_edge,
        )
        candidate.market = _prefer_market_value(candidate.market, comp_market)
        if comp_market is not None and candidate.cert_market_fingerprint:
            put_cached_market(state, candidate.cert_market_fingerprint, comp_market)
        exact_counts.append(len(values))
    if task.mode in {"listing", "cert+listing"} and candidate.listing_identity:
        candidate.listing_comp_rows.extend(rows)
        values = exact_active_comps_for_listing(
            candidate.listing_comp_rows,
            candidate.listing_identity,
            target_currency=target.currency,
            fx=fx,
            exclude_item_id=candidate.listing.item_id,
        )
        candidate.exact_matches = max(candidate.exact_matches, len(values))
        listing_market = market_value_from_listing_comps(
            values,
            required_edge=max(0.25, required_edge),
        )
        candidate.market = _prefer_market_value(candidate.market, listing_market)
        if listing_market is not None and candidate.listing_market_fingerprint:
            put_cached_market(state, candidate.listing_market_fingerprint, listing_market)
        exact_counts.append(len(values))

    exact_count = max(exact_counts, default=0)

    if task.offset == 0 and len(rows) >= search_limit and exact_count < 3:
        candidate.search_tasks.append(
            _CompSearchTask(task.mode, task.query, offset=search_limit)
        )


def _comp_detail_priority(candidate: _PriceCandidate) -> tuple[int, float, int, int]:
    """Prioritize weak markets with the largest plausible purchase edge."""
    market = candidate.market
    target = candidate.listing.total_cost or candidate.listing.price
    potential_edge = -1.0
    if market is not None and target is not None and market.money.value > 0:
        potential_edge = 1.0 - target.value / market.money.value
    return (
        int(bool(market and market.confidence.casefold() == "niedrig")),
        potential_edge,
        candidate.screening_score,
        candidate.preliminary,
    )


def _enrich_listing_comp_details(
    candidate: _PriceCandidate,
    *,
    ebay: EbayClient,
    fx: FXRates,
    state: dict[str, Any],
    required_edge: float,
    limit: int,
) -> tuple[int, bool]:
    """Load a few full comp records and recompute listing-market evidence."""
    identity = candidate.listing_identity
    target = candidate.listing.total_cost or candidate.listing.price
    if identity is None or target is None or limit <= 0:
        return 0, False

    selected = listing_comp_detail_candidates(
        candidate.listing_comp_rows,
        identity,
        exclude_item_id=candidate.listing.item_id,
        attempted_item_ids=candidate.comp_detail_attempts,
    )[:limit]
    calls = 0
    exhausted = False
    for summary in selected:
        candidate.comp_detail_attempts.add(summary.item_id)
        try:
            detail = ebay.get_item(summary.item_id)
        except EbayBudgetExceeded:
            exhausted = True
            break
        except EbayError:
            calls += 1
            candidate.comp_detail_errors += 1
            continue
        calls += 1
        merged = _merge_listing(summary, detail)
        candidate.listing_comp_rows = [
            merged if row.item_id == summary.item_id else row
            for row in candidate.listing_comp_rows
        ]

    if calls:
        values = exact_active_comps_for_listing(
            candidate.listing_comp_rows,
            identity,
            target_currency=target.currency,
            fx=fx,
            exclude_item_id=candidate.listing.item_id,
        )
        candidate.exact_matches = max(candidate.exact_matches, len(values))
        listing_market = market_value_from_listing_comps(
            values,
            required_edge=max(0.25, required_edge),
        )
        candidate.market = _prefer_market_value(candidate.market, listing_market)
        if listing_market is not None and candidate.listing_market_fingerprint:
            put_cached_market(state, candidate.listing_market_fingerprint, listing_market)
    return calls, exhausted


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
    point130_legacy_enabled = bool(settings.get("enable_point130_legacy", False))
    point130_sales = (
        load_point130_sales()
        if point130_legacy_enabled
        else []
    )
    renaiss = RenaissClient.from_env(
        max_calls=int(settings.get("max_renaiss_calls_per_run", 8)),
    )
    if not renaiss.authenticated:
        renaiss.max_calls = min(
            renaiss.max_calls,
            int(settings.get("max_renaiss_public_calls_per_run", 1)),
        )
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
        if psa_api_status == "abgelehnt":
            notes.append("PSA API Hinweis: Token wurde nach Normalisierung abgelehnt; im PSA-Konto neu erzeugen")
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
    price_candidates: list[_PriceCandidate] = []
    ocr_items = 0
    max_ocr_items = int(settings.get("max_ocr_items_per_run", 8))
    cert_cache_days = int(settings.get("cert_cache_days", 7))
    market_cache_hours = int(settings.get("market_cache_hours", 8))
    max_comp_calls = int(settings.get("max_market_comp_calls_per_run", 0))
    max_comp_detail_calls = int(settings.get("max_market_comp_detail_calls_per_run", 0))
    max_comp_details_per_candidate = int(settings.get("max_market_comp_details_per_candidate", 3))
    comp_detail_min_score = int(settings.get("market_comp_detail_min_screening_score", 6))
    comp_search_limit = int(settings.get("market_comp_search_limit", 100))
    comp_required_edge = float(settings.get("market_active_required_edge", 0.20))
    listing_market_min_prelim = int(settings.get("market_listing_fallback_min_preliminary_score", 7))
    market_comp_calls = 0
    market_comp_detail_calls = 0
    max_psa_market_web_calls = int(settings.get("max_psa_market_web_calls_per_run", 0))
    psa_market_web_calls = 0
    psa_market_web_min_prelim = int(settings.get("psa_market_web_min_preliminary_score", 8))
    psa_cache_upgrades = 0
    point130_matches = 0
    renaiss_matches = 0
    renaiss_cert_matches = 0
    renaiss_cache_hits = 0
    renaiss_errors = 0
    price_diag = _new_price_diagnostics()
    demand_terms = list(settings.get("demand_terms") or [])

    # Phase 1: Alle Detailkandidaten laden, Identität/Cert bestimmen und bereits
    # vorhandene Preis-Caches nutzen. Noch keine knappen eBay-Comp-Calls ausgeben.
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
        prelim = preliminary_score(listing, priority_terms)
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
            if cert is not None and psa_api_status == "ok" and cert_needs_api_upgrade(cert):
                try:
                    api_cert = psa.get_api_cert(cert_candidate.number)
                except PSABudgetExceeded:
                    api_cert = None
                if api_cert:
                    cert = merge_cert_info(api_cert, cert)
                    put_cached_cert(state, cert)
                    psa_cache_upgrades += 1
            if cert is None:
                try:
                    cert = psa.get_cert(cert_candidate.number)
                except PSABudgetExceeded:
                    notes.append("PSA-Call-Budget ausgeschöpft; weitere Kandidaten ohne POP-Anreicherung")
                    cert = None
                if cert:
                    put_cached_cert(state, cert)

        cert_market_safe = bool(
            cert and cert_candidate and _cert_safe_for_market(listing, cert_candidate, cert)
        )
        market = (
            _market_in_listing_currency(market_value_from_cert(cert), listing, fx)
            if cert_market_safe
            else None
        )
        target = listing.total_cost or listing.price
        cert_market_fingerprint_value: str | None = None
        if (
            _market_needs_upgrade(market) and cert and cert.valid and is_psa10(cert.grade)
            and _cert_safe_for_market(listing, cert_candidate, cert)
        ):
            if target:
                fingerprint = f"{cert_fingerprint(cert)}|{target.currency.upper()}"
                if fingerprint.strip("|"):
                    cert_market_fingerprint_value = fingerprint
                    cached, cached_market = get_cached_market(state, fingerprint, market_cache_hours)
                    if cached and cached_market is not None:
                        market = _prefer_market_value(market, cached_market)
        base_listing_identity = (
            listing_comp_identity(listing)
            if prelim >= listing_market_min_prelim
            else None
        )
        point130_identity = base_listing_identity
        if cert_market_safe and cert and cert.valid and is_psa10(cert.grade):
            point130_identity = pricing_identity_from_listing(listing, cert) or point130_identity
        if target and point130_sales and point130_identity:
            point130_market = point130_market_for_identity(
                point130_sales,
                point130_identity,
                target_currency=target.currency,
                fx=fx,
                max_age_days=int(settings.get("point130_sold_max_age_days", 365)),
                required_edge=float(settings.get("point130_sold_required_edge", 0.12)),
            )
            market = _prefer_market_value(market, point130_market)
            if point130_market is not None:
                point130_matches += 1
        identity = base_listing_identity if _market_needs_upgrade(market) else None
        listing_market_fingerprint_value: str | None = None
        if identity and target:
            fingerprint = f"{listing_comp_fingerprint(identity)}|{target.currency.upper()}"
            listing_market_fingerprint_value = fingerprint
            cached, cached_market = get_cached_market(state, fingerprint, market_cache_hours)
            if cached and cached_market is not None:
                market = _prefer_market_value(market, cached_market)

        candidate = _PriceCandidate(
            listing=listing,
            preliminary=prelim,
            cert_candidate=cert_candidate,
            cert=cert,
            cert_market_safe=cert_market_safe,
            market=market,
            listing_identity=identity,
            cert_market_fingerprint=cert_market_fingerprint_value,
            listing_market_fingerprint=listing_market_fingerprint_value,
        )
        candidate.screening_score = _score_price_candidate(
            candidate,
            priority_terms=priority_terms,
            demand_terms=demand_terms,
            settings=settings,
            market=None,
        ).score
        price_candidates.append(candidate)

    # Phase 2: Erst jetzt global priorisieren. Dadurch fließt das begrenzte
    # Vergleichspreis-Budget in die Kandidaten mit der stärksten Identität und
    # dem höchsten Screening-Score – unabhängig von der Discovery-Reihenfolge.
    price_candidates.sort(key=_price_candidate_priority, reverse=True)

    # Renaiss publishes a documented PSA-10 FMV derived from completed sales.
    # Query it before spending eBay calls on active asking-price comparisons.
    renaiss_cache_hours = int(settings.get("renaiss_cache_hours", 24))
    renaiss_max_sale_age_days = int(settings.get("renaiss_max_sale_age_days", 365))
    for candidate in price_candidates:
        if not _market_needs_upgrade(candidate.market):
            continue
        target = candidate.listing.total_cost or candidate.listing.price
        cert_ready = bool(
            candidate.cert
            and candidate.cert.valid
            and candidate.cert_market_safe
            and is_psa10(candidate.cert.grade)
        )
        identity = (
            pricing_identity_from_listing(candidate.listing, candidate.cert)
            if cert_ready
            else candidate.listing_identity
        ) or candidate.listing_identity
        if target is None or (identity is None and not cert_ready):
            continue
        identity_fingerprint = (
            f"renaiss|{listing_comp_fingerprint(identity)}|{target.currency.upper()}"
            if identity is not None
            else None
        )
        cert_fingerprint_value = (
            f"renaiss|cert:{candidate.cert.cert_number}|{target.currency.upper()}"
            if cert_ready and candidate.cert
            else None
        )

        # A certificate miss must not poison the shared card identity, and an
        # older text-search miss must not block a later exact certificate lookup.
        if cert_fingerprint_value:
            cached, cached_market = get_cached_market(
                state,
                cert_fingerprint_value,
                renaiss_cache_hours,
            )
            if cached:
                if cached_market is not None:
                    candidate.market = _prefer_market_value(candidate.market, cached_market)
                    renaiss_cache_hits += 1
                continue
        if identity_fingerprint:
            cached, cached_market = get_cached_market(
                state,
                identity_fingerprint,
                renaiss_cache_hours,
            )
            if cached and cached_market is not None:
                candidate.market = _prefer_market_value(candidate.market, cached_market)
                renaiss_cache_hits += 1
                continue
            if cached and not cert_ready:
                continue
        if renaiss.calls_made >= renaiss.max_calls or renaiss.rate_limited:
            break
        try:
            if cert_ready and candidate.cert:
                match = renaiss.market_for_cert(
                    candidate.cert.cert_number,
                    identity=identity,
                    target_currency=target.currency,
                    fx=fx,
                    max_sale_age_days=renaiss_max_sale_age_days,
                )
            elif identity is not None:
                match = renaiss.market_for_identity(
                    identity,
                    target_currency=target.currency,
                    fx=fx,
                    max_sale_age_days=renaiss_max_sale_age_days,
                )
            else:  # guarded above; keeps type checkers honest
                continue
        except RenaissError:
            renaiss_errors += 1
            if renaiss.rate_limited:
                break
            continue
        query_fingerprint = cert_fingerprint_value or identity_fingerprint
        if match is None:
            if query_fingerprint:
                put_cached_market(state, query_fingerprint, None)
            continue
        candidate.market = _prefer_market_value(candidate.market, match.market)
        for fingerprint in dict.fromkeys(
            value for value in (cert_fingerprint_value, identity_fingerprint) if value
        ):
            put_cached_market(state, fingerprint, match.market)
        renaiss_matches += 1
        if cert_ready:
            renaiss_cert_matches += 1

    notes.append(
        "Renaiss Index: "
        f"{renaiss.calls_made} API-Call(s), {renaiss_matches} exakte PSA-10-FMV-Match(es), "
        f"davon {renaiss_cert_matches} via PSA-Cert, "
        f"{renaiss_cache_hits} Cache-Treffer, {renaiss_errors} Fehler"
    )
    eligible_price_candidates = sum(
        1
        for candidate in price_candidates
        if candidate.identity_available
        and candidate.target_available
        and candidate.preliminary >= listing_market_min_prelim
        and _market_needs_upgrade(candidate.market)
    )
    notes.append(
        "Preis-Priorisierung: "
        f"{eligible_price_candidates} Kandidat(en) global gerankt; "
        f"maximal {max_comp_calls} Comp-Calls"
    )

    # PSA-Daten werden vor der Comp-Verteilung angereichert. Anschließend erhält
    # jeder noch offene Kandidat zunächst genau eine Suche. Erst in späteren
    # Runden kommen Fallback-Abfragen und zweite Ergebnisseiten zum Zug.
    for candidate in price_candidates:
        listing = candidate.listing
        cert = candidate.cert
        market = candidate.market

        if (
            cert and cert.valid and candidate.cert_market_safe and market is None
            and psa_market_web_calls < max_psa_market_web_calls
            and candidate.preliminary >= psa_market_web_min_prelim
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
                candidate.cert = enriched
                put_cached_cert(state, enriched)
                market = _market_in_listing_currency(market_value_from_cert(enriched), listing, fx)

        candidate.market = market
        if _market_needs_upgrade(candidate.market):
            _prepare_comp_search_tasks(candidate)

    merged_searches = sum(candidate.merged_searches for candidate in price_candidates)
    if merged_searches:
        notes.append(
            "Comp-Suchplan: "
            f"{merged_searches} identische Cert-/Listing-Abfrage(n) zusammengeführt"
        )

    comp_candidate_ids: set[str] = set()
    ebay_budget_exhausted = False
    while market_comp_calls < max_comp_calls and not ebay_budget_exhausted:
        round_progress = False
        for candidate in price_candidates:
            if market_comp_calls >= max_comp_calls:
                break
            if not _market_needs_upgrade(candidate.market):
                candidate.search_tasks.clear()
                continue
            if not candidate.search_tasks:
                continue

            round_progress = True
            task = candidate.search_tasks.pop(0)
            try:
                _run_comp_search_task(
                    candidate,
                    task,
                    ebay=ebay,
                    fx=fx,
                    state=state,
                    search_limit=comp_search_limit,
                    required_edge=comp_required_edge,
                )
            except EbayBudgetExceeded:
                candidate.budget_blocked = True
                ebay_budget_exhausted = True
                notes.append("eBay-Budget für weitere Preis-Comps ausgeschöpft")
                break
            except EbayError:
                candidate.search_error = True
                notes.append("Mindestens eine eBay-Preisvergleichssuche ist fehlgeschlagen")
            else:
                market_comp_calls += 1
                comp_candidate_ids.add(candidate.listing.item_id)

        if not round_progress:
            break

    if market_comp_calls >= max_comp_calls or ebay_budget_exhausted:
        for candidate in price_candidates:
            if _market_needs_upgrade(candidate.market) and candidate.search_tasks:
                candidate.budget_blocked = True

    # Search summaries are deliberately cheap but often omit seller, language,
    # set, and variant. Spend a separate bounded detail budget only on the most
    # promising weak markets, ranked by plausible edge and screening score.
    detail_budget_exhausted = False
    for candidate in sorted(price_candidates, key=_comp_detail_priority, reverse=True):
        if market_comp_detail_calls >= max_comp_detail_calls:
            break
        if candidate.screening_score < comp_detail_min_score:
            continue
        if not _market_needs_upgrade(candidate.market) or not candidate.listing_comp_rows:
            continue
        remaining = max_comp_detail_calls - market_comp_detail_calls
        detail_limit = min(max_comp_details_per_candidate, remaining)
        used, exhausted = _enrich_listing_comp_details(
            candidate,
            ebay=ebay,
            fx=fx,
            state=state,
            required_edge=comp_required_edge,
            limit=detail_limit,
        )
        market_comp_detail_calls += used
        if not _market_needs_upgrade(candidate.market):
            candidate.search_tasks.clear()
            candidate.budget_blocked = False
        if exhausted:
            detail_budget_exhausted = True
            break

    if market_comp_detail_calls or max_comp_detail_calls:
        detail_errors = sum(candidate.comp_detail_errors for candidate in price_candidates)
        notes.append(
            "Comp-Detailanreicherung: "
            f"{market_comp_detail_calls} vollständige Listing-Details; "
            f"{detail_errors} Detailfehler"
        )
    if detail_budget_exhausted:
        notes.append("eBay-Budget bei der Comp-Detailanreicherung ausgeschöpft")

    budget_open = sum(1 for candidate in price_candidates if candidate.budget_blocked)
    notes.append(
        "Comp-Fairness: "
        f"{len(comp_candidate_ids)} Kandidat(en) erhielten mindestens eine Suche; "
        f"{budget_open} budgetbedingt offen"
    )

    for candidate in price_candidates:
        listing = candidate.listing
        hit = _score_price_candidate(
            candidate,
            priority_terms=priority_terms,
            demand_terms=demand_terms,
            settings=settings,
            market=candidate.market,
        )
        gap_reason = _classify_price_gap(
            hit.market_value,
            target_available=candidate.target_available,
            preliminary=candidate.preliminary,
            min_preliminary=listing_market_min_prelim,
            identity_available=candidate.identity_available,
            search_attempted=candidate.search_attempted,
            search_rows=candidate.search_rows,
            exact_matches=candidate.exact_matches,
            budget_blocked=candidate.budget_blocked,
            search_error=candidate.search_error,
        )
        if gap_reason:
            price_diag["OhnePreis"] += 1
            price_diag[gap_reason] += 1
        for key in _weak_market_diagnostics(hit.market_value):
            price_diag[key] += 1

        scored.append(hit)
        mark_processed(state, listing.item_id, hit.score)
        if hit.score >= int(settings.get("dashboard_min_score", 7)):
            upsert_history(state, hit, int(settings.get("hit_threshold", 11)))

    scored.sort(key=lambda row: row.score, reverse=True)
    threshold = int(settings.get("hit_threshold", 11))
    dashboard_min = int(settings.get("dashboard_min_score", 7))
    hits = [row for row in scored if row.score >= threshold][: int(settings.get("max_hits_per_run", 12))]
    near_hits = [row for row in scored if dashboard_min <= row.score < threshold]

    # Ein frisch bewerteter Kauf-Hit wird unmittelbar vor Alert und Snapshot
    # nochmals live geladen. Preisänderungen oder ein beendetes Angebot können
    # ihn dadurch noch sicher zu einer Beobachtung herabstufen.
    live_hits: list[ScoredHit] = []
    live_demoted: list[ScoredHit] = []
    for hit in hits:
        refreshed, live_status = refresh_hit_for_purchase(hit, ebay, settings)
        if live_status == "active" and refreshed is not None:
            live_hits.append(refreshed)
            upsert_history(state, refreshed, threshold)
            continue
        if live_status == "no_longer_hit" and refreshed is not None:
            upsert_history(state, refreshed, threshold)
            if dashboard_min <= refreshed.score < threshold:
                live_demoted.append(refreshed)
            notes.append("Mindestens ein Kauf-Hit wurde nach Live-Preisprüfung zur Beobachtung herabgestuft")
            continue

        availability = "ended" if live_status == "ended" else "check_failed"
        for row in state.get("history", []):
            if isinstance(row, dict) and row.get("item_id") == hit.listing.item_id:
                row["availability_status"] = availability
                row["availability_checked_at"] = iso_z(utc_now())
                row["is_hit"] = False
                if availability == "ended":
                    row["price_status"] = "unavailable"
                    row.pop("availability_error", None)
                else:
                    row["availability_error"] = "temporary"
                break
        if live_status == "budget":
            notes.append("Kauf-Hit nicht veröffentlicht: Budget für finalen Live-Recheck erschöpft")
        else:
            notes.append("Kauf-Hit nicht veröffentlicht: finaler Live-Recheck vorübergehend fehlgeschlagen")

    hits = live_hits
    if live_demoted:
        existing_near = {row.listing.item_id for row in near_hits}
        near_hits.extend(row for row in live_demoted if row.listing.item_id not in existing_near)
        near_hits.sort(key=lambda row: row.score, reverse=True)

    channels = configured_channels()
    for hit in hits:
        if not should_alert(
            state, hit,
            min_price_drop_pct=float(settings.get("alert_rearm_price_drop_pct", 0.10)),
            min_edge_improvement=float(settings.get("alert_rearm_edge_improvement", 0.10)),
        ):
            continue
        statuses = notify(hit)
        if not channels or any(statuses.values()):
            mark_alerted(state, hit.listing.item_id, statuses or {"dashboard": True}, hit=hit)
        else:
            notes.append("Mindestens ein Alert konnte nicht zugestellt werden")

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
    renaiss_prices = sum(
        1 for row in scored
        if row.market_value and row.market_value.market_type == "renaiss_fmv"
    )
    verified_edges = sum(1 for row in scored if row.price_status == "verified_edge")
    candidate_api_successes = max(0, psa.api_successes - psa_api_success_baseline)
    notes.append(f"PSA API live: {_psa_status_label(psa_api_status)}")
    if psa_api_status == "abgelehnt":
        notes.append("PSA API Hinweis: Token wurde nach Normalisierung abgelehnt; im PSA-Konto neu erzeugen")
    if psa_cache_upgrades:
        notes.append(f"PSA API Cache-Upgrades: {psa_cache_upgrades}")
    if point130_legacy_enabled:
        notes.append(
            "130point Legacy: "
            f"{len(point130_sales)} manuelle Verkäufe geladen; "
            f"{point130_matches} Kandidaten-Match(es)"
        )
    notes.append(_price_diag_note(price_diag))
    notes.append(
        "Coverage: "
        f"Details={len(scored)}; Cert={cert_detected}; PSA={candidate_api_successes}; "
        f"Verifiziert={cert_verified}; POP={pop_available}; Preis={price_indicators}; "
        f"eBayCompSuche={market_comp_calls}; eBayCompDetails={market_comp_detail_calls}; "
        f"eBayCompPreis={ebay_comp_prices}; RenaissPreis={renaiss_prices}; Edge={verified_edges}"
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
