from datetime import datetime, timezone

from psa_sniper.listing_market import (
    build_listing_comp_queries,
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


def listing(item_id, title, price=100, aspects=None, seller=None):
    return Listing(
        item_id=item_id,
        title=title,
        url=f"https://example.test/{item_id}",
        price=Money(price, "EUR"),
        created_at=datetime(2026, 8, 28, tzinfo=timezone.utc),
        buying_options=["FIXED_PRICE"],
        aspects=aspects or {},
        seller=seller,
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
    assert identity.language == "DE"
    assert build_listing_comp_query(identity) == "elfun 165 PSA 10"


def test_listing_identity_requires_card_number_and_psa10():
    assert listing_comp_identity(listing("1", "ELFUN EX PSA 10")) is None
    assert listing_comp_identity(listing("2", "ELFUN EX #165 PSA 9")) is None


def test_listing_comp_match_rejects_wrong_number_grade_language_and_variant():
    source = listing("own", "ELFUN EX #165 PSA 10 DE SPECIAL ILLUSTRATION RARE")
    identity = listing_comp_identity(source)
    assert identity is not None
    assert listing_comp_identity_score(
        listing("ok", "Pokemon Elfun EX #165 PSA 10 DE SPECIAL ILLUSTRATION RARE"), identity
    )[1] is True
    assert listing_comp_identity_score(
        listing("bad-number", "Pokemon Elfun EX #164 PSA 10 DE SPECIAL ILLUSTRATION RARE"), identity
    )[1] is False
    assert listing_comp_identity_score(
        listing("bad-grade", "Pokemon Elfun EX #165 PSA 9 DE SPECIAL ILLUSTRATION RARE"), identity
    )[1] is False
    assert listing_comp_identity_score(
        listing("bad-language", "Pokemon Elfun EX #165 PSA 10 EN SPECIAL ILLUSTRATION RARE"), identity
    )[1] is False
    assert listing_comp_identity_score(
        listing("bad-variant", "Pokemon Elfun EX #165 PSA 10 DE REVERSE HOLO"), identity
    )[1] is False


def test_three_independent_exact_listing_comps_can_be_medium_confidence():
    source = listing("own", "ELFUN EX #165 PSA 10")
    identity = listing_comp_identity(source)
    assert identity is not None
    rows = [
        listing("a", "ELFUN EX #165 PSA 10", 90, seller="a"),
        listing("b", "ELFUN EX #165 PSA 10", 100, seller="b"),
        listing("c", "ELFUN EX #165 PSA 10", 110, seller="c"),
    ]
    values = exact_active_comps_for_listing(
        rows, identity, target_currency="EUR", fx=IdentityFX(), exclude_item_id="own"
    )
    market = market_value_from_listing_comps(values, required_edge=0.20)
    assert market is not None
    assert market.sample_size == 3
    assert market.confidence == "mittel"
    assert market.market_type == "ebay_active_provisional"
    assert market.required_edge == 0.25
    assert market.unique_sellers == 3


def test_pikachu_query_prioritizes_subject_and_set_code():
    row = listing(
        "pika",
        "2023 Pokemon Japanese SV2A - Pokemon Card 151 Art Rare #173 Pikachu PSA 10",
    )
    identity = listing_comp_identity(row)
    assert identity is not None
    assert identity.card_number == "173"
    assert identity.terms[:2] == ("pikachu", "sv2a")
    assert identity.language == "JP"
    assert build_listing_comp_query(identity) == "pikachu SV2A 173 PSA 10"
    assert build_listing_comp_queries(identity) == [
        "pikachu SV2A 173 PSA 10",
        "pikachu 173 PSA 10",
    ]


def test_charizard_query_drops_generic_localized_words():
    row = listing(
        "zard",
        "2026 Pokemon Karte M2a Mega Charizard X ex #223 MA Korean PSA 10 Gem Mint",
    )
    identity = listing_comp_identity(row)
    assert identity is not None
    assert identity.card_number == "223"
    assert identity.terms[:2] == ("charizard", "m2a")
    assert identity.language == "KR"
    assert build_listing_comp_query(identity) == "charizard M2A 223 PSA 10"


def test_luffy_prefers_card_number_near_psa_over_magazine_issue_number():
    row = listing(
        "luffy",
        "One Piece Card Game Monkey D. Luffy Promo Foil WSJ #36-37 043 JP PSA 10 2023",
    )
    identity = listing_comp_identity(row)
    assert identity is not None
    assert identity.card_number == "043"
    assert identity.terms[:2] == ("monkey", "luffy")
    assert identity.variant == "PROMO"
    assert build_listing_comp_query(identity) == "monkey luffy 043 PSA 10"


def test_listing_match_rejects_explicit_other_set_code():
    source = listing("own", "2023 Pokemon Japanese SV2A Art Rare #173 Pikachu PSA 10")
    identity = listing_comp_identity(source)
    assert identity is not None
    good = listing("good", "Pikachu #173 SV2A PSA 10 Japanese", 120)
    wrong_set = listing("wrong", "Pikachu #173 SV3A PSA 10 Japanese", 100)
    assert listing_comp_identity_score(good, identity)[1] is True
    assert listing_comp_identity_score(wrong_set, identity)[1] is False


def test_missing_explicit_language_is_allowed_but_penalized_to_low_market_confidence():
    source = listing("own", "Pikachu #173 SV2A PSA 10 Japanese")
    identity = listing_comp_identity(source)
    assert identity is not None
    no_language = listing("unknown", "Pikachu #173 SV2A PSA 10", 120, seller="x")
    score, accepted = listing_comp_identity_score(no_language, identity)
    assert accepted is True
    assert score >= 6



def test_mewtwo_fraction_number_is_detected_far_before_psa():
    row = listing(
        "mewtwo",
        "Pokémon Karte Team Rocket's Mewtwo ex SAR Mega Dream ex 237/193 m2a - Jap Psa 10",
    )
    identity = listing_comp_identity(row)
    assert identity is not None
    assert identity.card_number == "237/193"
    assert "mewtwo" in identity.subjects
    assert identity.set_code == "M2A"
    assert identity.language == "JP"
    queries = build_listing_comp_queries(identity)
    assert any("237/193" in q and "mewtwo" in q.lower() for q in queries)
    assert any("237" in q and "mewtwo" in q.lower() for q in queries)


def test_luffy_promo_code_is_detected_far_before_psa_and_matches_compact_form():
    row = listing(
        "luffy-p043",
        "One Piece Card Monkey D Luffy P-043 Promo Weekly Shonen Jump - PSA 10 GEM MT",
    )
    identity = listing_comp_identity(row)
    assert identity is not None
    assert identity.card_number == "p-043"
    assert identity.subjects[:2] == ("monkey", "luffy")
    assert identity.variant == "PROMO"
    compact = listing("compact", "Monkey D Luffy P043 Promo PSA 10", 250, seller="x")
    assert listing_comp_identity_score(compact, identity)[1] is True


def test_two_tight_independent_exact_listing_comps_are_medium_confidence():
    source = listing("own-ursaring", "Pokemon Ursaring Holo #217 Japanese Neo 2 Crossing The Ruins PSA 10 Gem Mint")
    identity = listing_comp_identity(source)
    assert identity is not None
    rows = [
        listing("u1", "Pokemon Ursaring Holo #217 Japanese Neo 2 Crossing The Ruins PSA 10", 450, seller="seller-a"),
        listing("u2", "Ursaring #217 Japanese Neo 2 Holo PSA 10 Gem Mint", 470, seller="seller-b"),
    ]
    values = exact_active_comps_for_listing(rows, identity, target_currency="EUR", fx=IdentityFX())
    market = market_value_from_listing_comps(values, required_edge=0.25)
    assert market is not None
    assert market.sample_size == 2
    assert market.unique_sellers == 2
    assert market.confidence == "mittel"
    assert market.money.value == 450
    assert market.required_edge == 0.25


def test_sparse_listing_comps_stay_low_if_same_seller_or_wide_spread():
    source = listing("own", "Pikachu #173 SV2A PSA 10 Japanese")
    identity = listing_comp_identity(source)
    assert identity is not None
    same_seller = exact_active_comps_for_listing(
        [
            listing("a", "Pikachu #173 SV2A PSA 10 Japanese", 100, seller="same"),
            listing("b", "Pikachu #173 SV2A PSA 10 Japanese", 105, seller="same"),
        ],
        identity, target_currency="EUR", fx=IdentityFX(),
    )
    assert market_value_from_listing_comps(same_seller).confidence == "niedrig"

    wide = exact_active_comps_for_listing(
        [
            listing("c", "Pikachu #173 SV2A PSA 10 Japanese", 100, seller="c"),
            listing("d", "Pikachu #173 SV2A PSA 10 Japanese", 160, seller="d"),
        ],
        identity, target_currency="EUR", fx=IdentityFX(),
    )
    assert market_value_from_listing_comps(wide).confidence == "niedrig"
