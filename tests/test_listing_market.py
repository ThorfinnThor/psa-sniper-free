from datetime import datetime, timezone

from psa_sniper.listing_market import (
    build_listing_comp_query,
    exact_active_comps_for_listing,
    listing_comp_identity,
    listing_comp_identity_score,
    market_value_from_listing_comps,
)
from psa_sniper.models import Listing, Money


class IdentityFX:
    def convert(self, money, currency):
        if money.currency.upper() != currency.upper():
            return None
        return Money(money.value, currency)


def listing(item_id, title, price=100, aspects=None):
    return Listing(
        item_id=item_id,
        title=title,
        url=f"https://example.test/{item_id}",
        price=Money(price, "EUR"),
        created_at=datetime(2026, 8, 28, tzinfo=timezone.utc),
        buying_options=["FIXED_PRICE"],
        aspects=aspects or {},
    )


def test_listing_identity_handles_german_whimsicott_title():
    row = listing(
        "own",
        "POKEMON ELFUN EX 165 PSA 10 GEM MINT DE",
        aspects={"Kartennummer": ["165"], "Charakter": ["Elfun"]},
    )
    identity = listing_comp_identity(row)
    assert identity is not None
    assert identity.card_number == "165"
    assert identity.terms == ("elfun",)
    assert build_listing_comp_query(identity) == "elfun 165 PSA 10"


def test_listing_identity_requires_card_number_and_psa10():
    assert listing_comp_identity(listing("1", "ELFUN EX PSA 10")) is None
    assert listing_comp_identity(listing("2", "ELFUN EX #165 PSA 9")) is None


def test_listing_comp_match_rejects_wrong_number_and_grade():
    source = listing("own", "ELFUN EX #165 PSA 10")
    identity = listing_comp_identity(source)
    assert identity is not None

    score, accepted = listing_comp_identity_score(
        listing("ok", "Pokemon Elfun EX #165 PSA 10 GEM MINT"), identity
    )
    assert accepted and score >= 6

    assert listing_comp_identity_score(
        listing("bad-number", "Pokemon Elfun EX #164 PSA 10"), identity
    )[1] is False
    assert listing_comp_identity_score(
        listing("bad-grade", "Pokemon Elfun EX #165 PSA 9"), identity
    )[1] is False


def test_provisional_market_is_always_low_confidence():
    source = listing("own", "ELFUN EX #165 PSA 10")
    identity = listing_comp_identity(source)
    assert identity is not None
    rows = [
        listing("a", "ELFUN EX #165 PSA 10", 90),
        listing("b", "ELFUN EX #165 PSA 10", 100),
        listing("c", "ELFUN EX #165 PSA 10", 110),
    ]
    values = exact_active_comps_for_listing(
        rows,
        identity,
        target_currency="EUR",
        fx=IdentityFX(),
        exclude_item_id="own",
    )
    market = market_value_from_listing_comps(values, required_edge=0.20)
    assert market is not None
    assert market.sample_size == 3
    assert market.confidence == "niedrig"
    assert market.market_type == "ebay_active_provisional"
    assert market.required_edge == 0.25
