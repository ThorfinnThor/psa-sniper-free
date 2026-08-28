from datetime import datetime, timezone

from psa_sniper.fx import FXRates
from psa_sniper.market import (
    build_comp_query,
    build_fallback_comp_query,
    cert_fingerprint,
    conservative_active_anchor,
    exact_active_comps,
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


def _listing(item_id: str, title: str, price: float) -> Listing:
    return Listing(
        item_id=item_id,
        title=title,
        url=f"https://www.ebay.de/itm/{item_id}",
        price=Money(price, "EUR"),
        created_at=datetime.now(timezone.utc),
        buying_options=["FIXED_PRICE"],
    )


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
        _listing("self", "2025 WHIMSICOTT EX #165 SPECIAL ILLUSTRATION RARE PSA 10", 80),
        _listing("good", "2025 WHIMSICOTT EX #165 SPECIAL ILLUSTRATION RARE PSA 10", 120),
        _listing("psa9", "2025 WHIMSICOTT EX #165 SPECIAL ILLUSTRATION RARE PSA 9", 70),
        _listing("wrong", "2025 WHIMSICOTT EX #166 SPECIAL ILLUSTRATION RARE PSA 10", 90),
        _listing("german", "2025 POKEMON GERMAN WHITE FLARE ELFUN EX #165 SPECIAL ILLUSTRATION RARE PSA 10", 110),
    ]
    values = exact_active_comps(
        rows,
        cert,
        target_currency="EUR",
        fx=fx,
        exclude_item_id="self",
    )
    assert sorted(value.value for value in values) == [110, 120]


def test_active_anchor_is_conservative_and_ignores_high_outlier():
    values = [Money(x, "EUR") for x in [129, 135, 139, 145, 159, 499]]
    anchor = conservative_active_anchor(values)
    assert anchor is not None
    assert 129 <= anchor.value <= 145


def test_three_active_comps_create_medium_confidence_with_20_percent_gate():
    values = [Money(x, "EUR") for x in [100, 110, 120]]
    market = market_value_from_active_comps(values, medium_required_edge=0.20)
    assert market is not None
    assert market.confidence == "mittel"
    assert market.market_type == "ebay_active"
    assert market.required_edge == 0.20
    assert market.sample_size == 3


def test_one_or_two_active_comps_are_only_low_confidence():
    market = market_value_from_active_comps(
        [Money(100, "EUR"), Money(110, "EUR")],
        medium_required_edge=0.20,
    )
    assert market is not None
    assert market.confidence == "niedrig"
    assert market.required_edge >= 0.25
