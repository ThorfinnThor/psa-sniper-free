from datetime import UTC, datetime

from psa_sniper.identity import PricingIdentity
from psa_sniper.models import Listing, MarketValue, Money, PSACertInfo
from psa_sniper.scanner import (
    _classify_price_gap,
    _CompSearchTask,
    _enrich_listing_comp_details,
    _market_needs_upgrade,
    _prefer_market_value,
    _prepare_comp_search_tasks,
    _price_candidate_priority,
    _PriceCandidate,
    _run_comp_search_task,
    _weak_market_diagnostics,
)
from psa_sniper.state import default_state


def market(value, source, confidence, sample_size, market_type):
    return MarketValue(
        Money(value, "EUR"),
        source,
        confidence,
        sample_size,
        market_type=market_type,
        required_edge=0.20,
    )


def test_psa_estimate_is_upgradeable():
    estimate = market(700, "PSA Estimate", "niedrig", 0, "psa_estimate")
    assert _market_needs_upgrade(estimate) is True


def test_medium_exact_ebay_comps_replace_psa_estimate():
    estimate = market(700, "PSA Estimate", "niedrig", 0, "psa_estimate")
    comps = market(520, "eBay aktive PSA-10-Vergleichsangebote", "mittel", 4, "ebay_active")
    assert _prefer_market_value(estimate, comps) is comps


def test_low_listing_comps_replace_psa_estimate_but_remain_low_confidence():
    estimate = market(700, "PSA Estimate", "niedrig", 0, "psa_estimate")
    comps = market(530, "eBay Listing-Comps", "niedrig", 3, "ebay_active_provisional")
    selected = _prefer_market_value(estimate, comps)
    assert selected is comps
    assert selected.confidence == "niedrig"


def test_psa_sales_are_not_replaced_by_active_asking_prices():
    sales = market(500, "PSA ähnliche Verkäufe", "mittel", 2, "psa_sales")
    comps = market(520, "eBay aktive PSA-10-Vergleichsangebote", "mittel", 8, "ebay_active")
    assert _prefer_market_value(sales, comps) is sales


def test_price_gap_diagnoses_identity_and_search_losses():
    assert _classify_price_gap(
        None, target_available=True, preliminary=8, min_preliminary=7,
        identity_available=False, search_attempted=False, search_rows=0,
        exact_matches=0, budget_blocked=False, search_error=False,
    ) == "KeineIdentitaet"
    assert _classify_price_gap(
        None, target_available=True, preliminary=8, min_preliminary=7,
        identity_available=True, search_attempted=True, search_rows=12,
        exact_matches=0, budget_blocked=False, search_error=False,
    ) == "KeineExaktenComps"


def test_price_gap_prioritizes_screening_gate_and_budget():
    assert _classify_price_gap(
        None, target_available=True, preliminary=6, min_preliminary=7,
        identity_available=False, search_attempted=False, search_rows=0,
        exact_matches=0, budget_blocked=False, search_error=False,
    ) == "UnterGate"
    assert _classify_price_gap(
        None, target_available=True, preliminary=8, min_preliminary=7,
        identity_available=True, search_attempted=False, search_rows=0,
        exact_matches=0, budget_blocked=True, search_error=False,
    ) == "Budget"


def test_weak_market_diagnostics_explain_source_quality():
    estimate = market(700, "PSA Estimate", "niedrig", 0, "psa_estimate")
    assert _weak_market_diagnostics(estimate) == ["Schwach", "SchwachPSAEstimate"]
    comps = MarketValue(
        Money(500, "EUR"), "eBay", "niedrig", 2,
        market_type="ebay_active", required_edge=0.25, unique_sellers=1,
        price_low=480, price_high=540, dispersion=0.12,
    )
    flags = _weak_market_diagnostics(comps)
    assert "SchwachComps" in flags
    assert "SchwachVerkaeufer" in flags


def price_candidate(
    item_id: str,
    *,
    screening_score: int,
    listing_identity: PricingIdentity | None = None,
    cert: PSACertInfo | None = None,
    cert_market_safe: bool = False,
) -> _PriceCandidate:
    return _PriceCandidate(
        listing=Listing(
            item_id=item_id,
            title=f"PSA 10 {item_id} #25",
            url=f"https://example.test/{item_id}",
            price=Money(100, "EUR"),
            created_at=datetime(2026, 8, 29, tzinfo=UTC),
            buying_options=["FIXED_PRICE"],
        ),
        preliminary=8,
        cert_candidate=None,
        cert=cert,
        cert_market_safe=cert_market_safe,
        market=None,
        listing_identity=listing_identity,
        cert_market_fingerprint=(f"cert:{item_id}|EUR" if cert_market_safe else None),
        screening_score=screening_score,
    )


def test_price_priority_spends_budget_on_searchable_candidates_first():
    identity = PricingIdentity(
        card_number="25",
        subjects=("pikachu",),
        terms=("pikachu",),
    )
    strong_but_unsearchable = price_candidate("no-identity", screening_score=20)
    searchable = price_candidate(
        "searchable",
        screening_score=8,
        listing_identity=identity,
    )

    ranked = sorted(
        [strong_but_unsearchable, searchable],
        key=_price_candidate_priority,
        reverse=True,
    )

    assert [candidate.listing.item_id for candidate in ranked] == [
        "searchable",
        "no-identity",
    ]


def test_price_priority_prefers_verified_cert_then_screening_score():
    identity = PricingIdentity(
        card_number="25",
        subjects=("pikachu",),
        terms=("pikachu",),
    )
    listing_only = price_candidate(
        "listing-only",
        screening_score=14,
        listing_identity=identity,
    )
    cert_ready = price_candidate(
        "cert-ready",
        screening_score=9,
        listing_identity=identity,
        cert=PSACertInfo(
            cert_number="12345678",
            valid=True,
            grade="GEM MT 10",
            population=7,
        ),
        cert_market_safe=True,
    )
    lower_score = price_candidate(
        "lower-score",
        screening_score=6,
        listing_identity=identity,
    )

    ranked = sorted(
        [lower_score, listing_only, cert_ready],
        key=_price_candidate_priority,
        reverse=True,
    )

    assert [candidate.listing.item_id for candidate in ranked] == [
        "cert-ready",
        "listing-only",
        "lower-score",
    ]


def test_comp_plan_merges_identical_cert_and_listing_queries():
    identity = PricingIdentity(
        card_number="25",
        subjects=("pikachu",),
        terms=("pikachu",),
    )
    candidate = price_candidate(
        "shared-query",
        screening_score=10,
        listing_identity=identity,
        cert=PSACertInfo(
            cert_number="12345678",
            valid=True,
            grade="GEM MT 10",
            subject="Pikachu",
            card_number="25",
        ),
        cert_market_safe=True,
    )
    candidate.listing_market_fingerprint = "listing:shared-query|EUR"

    _prepare_comp_search_tasks(candidate)

    shared = [task for task in candidate.search_tasks if task.mode == "cert+listing"]
    assert len(shared) == 1
    assert shared[0].query == "Pikachu 25 PSA 10"
    assert candidate.merged_searches == 1


def test_shared_comp_query_feeds_both_identity_filters_with_one_call():
    identity = PricingIdentity(
        card_number="25",
        subjects=("pikachu",),
        terms=("pikachu",),
    )
    candidate = price_candidate(
        "shared-evidence",
        screening_score=10,
        listing_identity=identity,
        cert=PSACertInfo(
            cert_number="12345678",
            valid=True,
            grade="GEM MT 10",
            subject="Pikachu",
            card_number="25",
        ),
        cert_market_safe=True,
    )
    candidate.listing_market_fingerprint = "listing:shared-evidence|EUR"
    rows = [
        Listing(
            item_id=f"comp-{index}",
            title="Pikachu #25 PSA 10",
            url=f"https://example.test/comp-{index}",
            price=Money(price, "EUR"),
            created_at=datetime(2026, 8, 29, tzinfo=UTC),
            buying_options=["FIXED_PRICE"],
            seller=f"seller-{index}",
        )
        for index, price in enumerate((130, 135, 140), start=1)
    ]

    class Ebay:
        calls_made = 0

        def search(self, *_args, **_kwargs):
            self.calls_made += 1
            return rows

    class FX:
        def convert(self, money, currency):
            return money if money.currency == currency else None

    ebay = Ebay()
    state = default_state()
    _run_comp_search_task(
        candidate,
        _CompSearchTask("cert+listing", "Pikachu 25 PSA 10"),
        ebay=ebay,
        fx=FX(),
        state=state,
        search_limit=100,
        required_edge=0.20,
    )

    assert ebay.calls_made == 1
    assert len(candidate.cert_comp_rows) == 3
    assert len(candidate.listing_comp_rows) == 3
    assert candidate.market is not None
    assert candidate.market.market_type == "ebay_active"
    assert candidate.market.confidence == "mittel"


def test_full_comp_details_can_upgrade_missing_language_evidence():
    identity = PricingIdentity(
        card_number="173",
        subjects=("pikachu",),
        terms=("pikachu", "sv2a"),
        set_code="SV2A",
        language="JP",
    )
    candidate = price_candidate(
        "detail-upgrade",
        screening_score=10,
        listing_identity=identity,
    )
    candidate.listing_market_fingerprint = "listing:detail-upgrade|EUR"
    summaries = [
        Listing(
            item_id=f"comp-{index}",
            title="Pikachu #173 SV2A PSA 10",
            url=f"https://example.test/comp-{index}",
            price=Money(price, "EUR"),
            created_at=datetime(2026, 8, 29, tzinfo=UTC),
            buying_options=["FIXED_PRICE"],
            seller=f"seller-{index}",
        )
        for index, price in enumerate((130, 135, 140), start=1)
    ]
    details = {
        row.item_id: Listing(
            item_id=row.item_id,
            title=f"{row.title} Japanese",
            url=row.url,
            price=row.price,
            created_at=row.created_at,
            buying_options=row.buying_options,
            seller=row.seller,
            aspects={"Language": ["Japanese"], "Set": ["SV2A"]},
        )
        for row in summaries
    }

    class Ebay:
        calls_made = 0

        def search(self, *_args, **_kwargs):
            self.calls_made += 1
            return summaries

        def get_item(self, item_id):
            self.calls_made += 1
            return details[item_id]

    class FX:
        def convert(self, money, currency):
            return money if money.currency == currency else None

    ebay = Ebay()
    state = default_state()
    _run_comp_search_task(
        candidate,
        _CompSearchTask("listing", "Pikachu SV2A 173 PSA 10"),
        ebay=ebay,
        fx=FX(),
        state=state,
        search_limit=100,
        required_edge=0.20,
    )
    assert candidate.market is not None
    assert candidate.market.confidence == "niedrig"

    calls, exhausted = _enrich_listing_comp_details(
        candidate,
        ebay=ebay,
        fx=FX(),
        state=state,
        required_edge=0.20,
        limit=3,
    )

    assert calls == 3
    assert exhausted is False
    assert candidate.market is not None
    assert candidate.market.confidence == "mittel"
    assert candidate.market.unique_sellers == 3
