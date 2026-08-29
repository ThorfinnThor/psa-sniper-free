from psa_sniper.models import MarketValue, Money
from psa_sniper.scanner import (
    _classify_price_gap,
    _market_needs_upgrade,
    _prefer_market_value,
    _weak_market_diagnostics,
)


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
