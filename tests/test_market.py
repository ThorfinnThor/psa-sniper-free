from datetime import datetime, timezone

from psa_sniper.fx import FXRates
from psa_sniper.market import (
    build_comp_query,
    build_fallback_comp_query,
    cert_fingerprint,
    conservative_active_anchor,
    exact_active_comps,
    find_leave_one_out_deal,
    market_value_from_active_comps,
)
from psa_sniper.models import Listing, Money, PSACertInfo


def _cert() -> PSACertInfo:
    return PSACertInfo(
        cert_number="131778450",
        valid=True,
        grade="GEM MT 10",
        year="2025",
        brand_title="POKEMON GERMAN WHT DE-WHITE FLARE",
        subject="WHIMSICOTT EX",
        card_number="165",
        variety="SPECIAL ILLUSTRATION RARE",
        population=9,
    )


def _listing(item_id: str, title: str, price: float, seller: str | None = None) -> Listing:
    return Listing(
        item_id=item_id,
        title=title,
        url=f"https://www.ebay.de/itm/{item_id}",
        price=Money(price, "EUR"),
        created_at=datetime.now(timezone.utc),
        buying_options=["FIXED_PRICE"],
        seller=seller,
    )


def _money(value, seller, item=None, penalty=0):
    return Money(value, "EUR", source_id=item, seller_key=seller, match_penalty=penalty)


def test_comp_query_and_fingerprint_use_psa_identity():
    cert = _cert()
    query = build_comp_query(cert)
    assert "WHIMSICOTT EX" in query
    assert "165" in query
    assert "PSA 10" in query
    fallback = build_fallback_comp_query(cert)
    assert "165" in fallback
    assert "flare" in fallback.casefold()
    assert "165" in cert_fingerprint(cert)


def test_exact_active_comps_reject_wrong_grade_and_wrong_card_and_self():
    cert = _cert()
    fx = FXRates()
    rows = [
        _listing("self", "2025 WHIMSICOTT EX #165 SPECIAL ILLUSTRATION RARE PSA 10 GERMAN", 80, "s0"),
        _listing("good", "2025 WHIMSICOTT EX #165 SPECIAL ILLUSTRATION RARE PSA 10 GERMAN", 120, "s1"),
        _listing("psa9", "2025 WHIMSICOTT EX #165 SPECIAL ILLUSTRATION RARE PSA 9 GERMAN", 70, "s2"),
        _listing("wrong", "2025 WHIMSICOTT EX #166 SPECIAL ILLUSTRATION RARE PSA 10 GERMAN", 90, "s3"),
        _listing("german", "2025 POKEMON GERMAN WHITE FLARE ELFUN EX #165 SPECIAL ILLUSTRATION RARE PSA 10", 110, "s4"),
        _listing("english", "2025 WHIMSICOTT EX #165 SPECIAL ILLUSTRATION RARE PSA 10 ENGLISH", 95, "s5"),
    ]
    values = exact_active_comps(rows, cert, target_currency="EUR", fx=fx, exclude_item_id="self")
    assert sorted(value.value for value in values) == [110, 120]


def test_active_anchor_is_conservative_and_ignores_high_outlier():
    values = [Money(x, "EUR") for x in [129, 135, 139, 145, 159, 499]]
    anchor = conservative_active_anchor(values)
    assert anchor is not None
    assert 129 <= anchor.value <= 145


def test_three_independent_coherent_active_comps_create_medium_confidence():
    values = [_money(100, "s1"), _money(110, "s2"), _money(120, "s3")]
    market = market_value_from_active_comps(values, medium_required_edge=0.20)
    assert market is not None
    assert market.confidence == "mittel"
    assert market.market_type == "ebay_active"
    assert market.required_edge == 0.20
    assert market.sample_size == 3
    assert market.unique_sellers == 3


def test_same_seller_or_missing_identity_dimension_stays_low_confidence():
    same = [_money(100, "s1"), _money(110, "s1"), _money(120, "s1")]
    market = market_value_from_active_comps(same, medium_required_edge=0.20)
    assert market is not None
    assert market.confidence == "niedrig"
    assert market.required_edge >= 0.25

    missing_dimension = [_money(100, "a", penalty=1), _money(110, "b"), _money(120, "c")]
    market = market_value_from_active_comps(missing_dimension, medium_required_edge=0.20)
    assert market is not None
    assert market.confidence == "niedrig"


def test_high_dispersion_downgrades_market_and_raises_gate():
    values = [_money(90, "a"), _money(100, "b"), _money(260, "c"), _money(300, "d")]
    market = market_value_from_active_comps(values, medium_required_edge=0.20)
    assert market is not None
    assert market.confidence == "niedrig"
    assert market.required_edge >= 0.30


def test_leave_one_out_detects_old_cheap_listing_only_against_strong_market():
    values = [
        _money(70, "cheap", "cheap"),
        _money(120, "a", "a"),
        _money(125, "b", "b"),
        _money(130, "c", "c"),
        _money(135, "d", "d"),
    ]
    found = find_leave_one_out_deal(values, min_edge=0.25)
    assert found is not None
    item_id, market, edge = found
    assert item_id == "cheap"
    assert market.confidence == "mittel"
    assert edge >= 0.25
