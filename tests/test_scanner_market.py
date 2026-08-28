from psa_sniper.models import MarketValue, Money
from psa_sniper.scanner import _market_needs_upgrade, _prefer_market_value


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
