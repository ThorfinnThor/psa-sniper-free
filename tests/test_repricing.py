from datetime import timedelta

from psa_sniper.models import Listing, Money
from psa_sniper.repricing import listing_from_history, reprice_state
from psa_sniper.state import default_state
from psa_sniper.util import iso_z, utc_now


class IdentityFX:
    def convert(self, money, currency):
        if money.currency.upper() != currency.upper():
            return None
        return Money(money.value, currency)


class FakeEbay:
    def __init__(self, rows):
        self.rows = rows
        self.calls_made = 0
        self.queries = []

    def search(self, query, *, limit, started_after):
        self.calls_made += 1
        self.queries.append(query)
        return self.rows


def comp(item_id, price):
    return Listing(
        item_id=item_id,
        title="Pokemon Elfun EX #165 PSA 10 GEM MINT",
        url=f"https://example.test/{item_id}",
        price=Money(price, "EUR"),
        created_at=utc_now(),
        buying_options=["FIXED_PRICE"],
    )


def weak_row(*, checked_at=None, attempts=0):
    seen = utc_now() - timedelta(hours=2)
    row = {
        "item_id": "own",
        "title": "POKEMON ELFUN EX 165 PSA 10 GEM MINT DE",
        "url": "https://example.test/own",
        "price": {"value": 80.0, "currency": "EUR"},
        "shipping": {"value": 0.0, "currency": "EUR"},
        "total_cost": {"value": 80.0, "currency": "EUR"},
        "created_at": iso_z(seen),
        "first_seen_at": iso_z(seen),
        "last_seen_at": iso_z(seen),
        "buying_options": ["FIXED_PRICE"],
        "pure_auction": False,
        "score": 10,
        "is_hit": False,
        "price_status": "weak_indicator",
        "cert_number": "137178450",
        "cert_source": "OCR Fallback",
        "cert_confidence": 0.95,
        "cert_trusted": True,
        "cert": {
            "cert_number": "137178450",
            "valid": True,
            "grade": "GEM MT 10",
            "year": "2025",
            "brand_title": None,
            "subject": "ELFUN EX",
            "card_number": "165",
            "category": "TCG Cards",
            "variety": None,
            "population": 9,
            "population_higher": 0,
            "estimate": {"value": 100.0, "currency": "EUR"},
            "recent_sales": [],
            "source_url": None,
            "data_source": "öffentliche PSA-Cert-Seite",
        },
        "market_value": {
            "money": {"value": 100.0, "currency": "EUR"},
            "source": "PSA Estimate",
            "confidence": "niedrig",
            "sample_size": 0,
            "market_type": "psa_estimate",
            "required_edge": 0.25,
        },
        "discount_pct": 0.20,
        "score_breakdown": [],
    }
    if checked_at:
        row["price_checked_at"] = checked_at
    if attempts:
        row["price_check_attempts"] = attempts
    return row


def settings():
    return {
        "hit_threshold": 11,
        "dashboard_min_score": 4,
        "market_comp_search_limit": 100,
        "market_active_required_edge": 0.20,
        "market_cache_hours": 8,
        "reprice_min_age_minutes": 60,
        "reprice_max_history_age_hours": 72,
        "max_reprice_items_per_run": 16,
        "priority_terms": [],
        "demand_terms": [],
    }


def test_listing_reconstruction_preserves_nonidentifying_seller_penalties():
    row = weak_row()
    row["score_breakdown"] = [
        {"points": -4, "label": "Verkäuferbewertung unter 95 %", "kind": "negative"},
        {"points": -1, "label": "sehr wenige Verkäuferbewertungen", "kind": "negative"},
    ]
    listing = listing_from_history(row)
    assert listing is not None
    assert listing.seller is None
    assert listing.seller_feedback_percentage == 94.0
    assert listing.seller_feedback_score == 5


def test_repricing_upgrades_psa_estimate_to_exact_ebay_market():
    state = default_state()
    row = weak_row()
    original_last_seen = row["last_seen_at"]
    state["history"] = [row]
    ebay = FakeEbay([comp("a", 130), comp("b", 140), comp("c", 150)])

    result = reprice_state(state, settings(), ebay, IdentityFX(), max_comp_calls=4)

    assert result.checked == 1
    assert result.improved == 1
    assert result.calls == 1
    updated = state["history"][0]
    assert updated["market_value"]["market_type"] == "ebay_active"
    assert updated["market_value"]["confidence"] == "mittel"
    assert updated["price_status"] == "verified_edge"
    assert updated["is_hit"] is True
    assert updated["last_seen_at"] == original_last_seen
    assert updated["price_checked_at"]
    assert updated["price_check_attempts"] == 1


def test_repricing_backoff_skips_recent_price_check():
    state = default_state()
    state["history"] = [
        weak_row(
            checked_at=iso_z(utc_now() - timedelta(minutes=30)),
            attempts=1,
        )
    ]
    ebay = FakeEbay([comp("a", 130), comp("b", 140), comp("c", 150)])

    result = reprice_state(state, settings(), ebay, IdentityFX(), max_comp_calls=4)

    assert result.checked == 0
    assert result.calls == 0
    assert ebay.queries == []
