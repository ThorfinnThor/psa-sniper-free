from datetime import datetime, timezone

from psa_sniper.models import Listing, MarketValue, Money, PSACertInfo
from psa_sniper.scoring import score_hit


def _cert() -> PSACertInfo:
    return PSACertInfo(
        cert_number="67205095",
        valid=True,
        grade="GEM MT 10",
        year="2021",
        brand_title="TOPPS CHROME BUNDESLIGA",
        subject="TAIWO AWONIYI",
        card_number="16",
        variety="X-FRACTOR",
        population=2,
        population_higher=0,
    )


def test_low_pop_information_gap_scores_as_hit_and_uses_shipping():
    listing = Listing(
        item_id="v1|123|0",
        title="2021 Bundesliga PSA 10 #16",
        url="https://www.ebay.de/itm/123",
        price=Money(45, "EUR"),
        shipping=Money(5, "EUR"),
        created_at=datetime.now(timezone.utc),
        buying_options=["FIXED_PRICE"],
    )
    hit = score_hit(
        listing,
        cert_number="67205095",
        cert_source="Item-Specifics",
        cert_confidence=1.0,
        cert=_cert(),
        market_value_listing_currency=MarketValue(Money(100, "EUR"), "Sales", "hoch", 3),
        priority_terms=[],
        demand_terms=[],
    )
    assert hit.score >= 20
    assert hit.discount_pct == 0.5
    assert hit.price_status == "verified_edge"
    assert sum(int(row["points"]) for row in hit.score_breakdown) == hit.score
    assert any("population" in reason.casefold() for reason in hit.reasons)
    assert any("variante" in reason.casefold() for reason in hit.reasons)


def test_pure_auction_does_not_receive_discount_points():
    listing = Listing(
        item_id="auction",
        title="2021 Bundesliga PSA 10 #16",
        url="https://www.ebay.de/itm/456",
        price=Money(1, "EUR"),
        created_at=datetime.now(timezone.utc),
        buying_options=["AUCTION"],
    )
    hit = score_hit(
        listing,
        cert_number="67205095",
        cert_source="Titel",
        cert_confidence=.97,
        cert=_cert(),
        market_value_listing_currency=MarketValue(Money(100, "EUR"), "Sales", "hoch", 3),
    )
    assert hit.discount_pct is None
    assert hit.price_status == "auction"
    assert hit.score <= 10
    assert any("auktion" in warning.casefold() for warning in hit.warnings)


def test_untrusted_ocr_cert_cannot_create_fake_low_pop_discount_hit():
    listing = Listing(
        item_id="ocr-mismatch",
        title="Completely Different Soccer Card PSA 10",
        url="https://www.ebay.de/itm/789",
        price=Money(10, "EUR"),
        created_at=datetime.now(timezone.utc),
        buying_options=["FIXED_PRICE"],
    )
    unrelated = PSACertInfo(
        cert_number="11112222",
        valid=True,
        grade="10",
        year="1999",
        brand_title="POKEMON JAPANESE PROMO",
        subject="PIKACHU",
        card_number="25",
        variety="HOLO",
        population=1,
    )
    hit = score_hit(
        listing,
        cert_number="11112222",
        cert_source="OCR (Fallback)",
        cert_confidence=0.55,
        cert=unrelated,
        market_value_listing_currency=MarketValue(Money(500, "EUR"), "Sales", "hoch", 5),
    )
    assert not hit.cert_trusted
    assert hit.discount_pct is None
    assert hit.price_status == "unverified"
    assert any("ocr-cert" in warning.casefold() for warning in hit.warnings)


def test_high_confidence_overpriced_listing_is_hard_gated_below_dashboard_threshold():
    listing = Listing(
        item_id="overpriced",
        title="2021 Bundesliga PSA 10 #16",
        url="https://www.ebay.de/itm/999",
        price=Money(275, "EUR"),
        shipping=Money(5, "EUR"),
        created_at=datetime.now(timezone.utc),
        buying_options=["FIXED_PRICE"],
    )
    hit = score_hit(
        listing,
        cert_number="67205095",
        cert_source="OCR (Fallback)",
        cert_confidence=0.95,
        cert=_cert(),
        market_value_listing_currency=MarketValue(Money(100, "EUR"), "PSA Sales", "hoch", 5),
        priority_terms=[],
        demand_terms=[],
    )
    assert hit.discount_pct is not None
    assert round(hit.discount_pct, 2) == -1.8
    assert hit.price_status == "over_market"
    assert hit.score <= 5
    assert any("über dem preisindikator" in warning.casefold() for warning in hit.warnings)


def test_low_pop_new_card_without_price_signal_is_watch_not_hit():
    listing = Listing(
        item_id="elfun",
        title="POKEMON ELFUN EX 165 PSA 10 GEM MINT DE",
        url="https://www.ebay.de/itm/elfun",
        price=Money(105.99, "EUR"),
        created_at=datetime.now(timezone.utc),
        buying_options=["FIXED_PRICE"],
    )
    cert = PSACertInfo(
        cert_number="131778450",
        valid=True,
        grade="GEM MT 10",
        year="2025",
        brand_title="POKEMON GERMAN WHT DE-WHITE FLARE",
        subject="WHIMSICOTT ex",
        card_number="165",
        variety="SPECIAL ILLUSTRATION RARE",
        population=9,
    )
    hit = score_hit(
        listing,
        cert_number="131778450",
        cert_source="OCR (Fallback)",
        cert_confidence=0.95,
        cert=cert,
        market_value_listing_currency=None,
        priority_terms=[],
        demand_terms=[],
    )
    assert hit.price_status == "unverified"
    assert hit.score <= 10
    assert any(row.get("kind") == "gate" for row in hit.score_breakdown)
    assert any("kein belastbarer preisindikator" in warning.casefold() for warning in hit.warnings)


def test_active_ebay_comps_need_twenty_percent_edge_for_purchase_hit():
    listing = Listing(
        item_id="active-comp",
        title="2021 Bundesliga PSA 10 #16",
        url="https://www.ebay.de/itm/active-comp",
        price=Money(85, "EUR"),
        created_at=datetime.now(timezone.utc),
        buying_options=["FIXED_PRICE"],
    )
    market = MarketValue(
        Money(100, "EUR"),
        "eBay aktive PSA-10-Vergleichsangebote",
        "mittel",
        5,
        market_type="ebay_active",
        required_edge=0.20,
    )
    hit = score_hit(
        listing,
        cert_number="67205095",
        cert_source="Item-Specifics",
        cert_confidence=1.0,
        cert=_cert(),
        market_value_listing_currency=market,
        priority_terms=[],
        demand_terms=[],
    )
    assert round(hit.discount_pct or 0, 2) == 0.15
    assert hit.price_status == "no_edge"
    assert hit.score <= 10
    assert any("mindestens 20%" in str(row.get("label", "")) for row in hit.score_breakdown)


def test_active_ebay_comps_can_verify_large_discount():
    listing = Listing(
        item_id="active-comp-hit",
        title="2021 Bundesliga PSA 10 #16",
        url="https://www.ebay.de/itm/active-comp-hit",
        price=Money(75, "EUR"),
        created_at=datetime.now(timezone.utc),
        buying_options=["FIXED_PRICE"],
    )
    market = MarketValue(
        Money(100, "EUR"),
        "eBay aktive PSA-10-Vergleichsangebote",
        "mittel",
        5,
        market_type="ebay_active",
        required_edge=0.20,
    )
    hit = score_hit(
        listing,
        cert_number="67205095",
        cert_source="Item-Specifics",
        cert_confidence=1.0,
        cert=_cert(),
        market_value_listing_currency=market,
        priority_terms=[],
        demand_terms=[],
    )
    assert hit.price_status == "verified_edge"
    assert hit.score >= 11


def test_population_without_confirmed_psa10_grade_gets_no_low_pop_bonus():
    cert = _cert()
    cert.grade = None
    cert.population = 1
    listing = Listing(
        item_id="no-grade", title="2021 Bundesliga PSA 10 #16",
        url="https://example.test/no-grade", price=Money(40, "EUR"),
        created_at=datetime.now(timezone.utc), buying_options=["FIXED_PRICE"],
    )
    hit = score_hit(
        listing, cert_number=cert.cert_number, cert_source="Item-Specifics",
        cert=cert, market_value_listing_currency=None,
        priority_terms=[], demand_terms=[],
    )
    assert not any(
        "niedrige PSA-10-Population" in reason
        or "sehr niedrige PSA-10-Population" in reason
        for reason in hit.reasons
    )
