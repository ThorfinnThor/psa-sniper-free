from datetime import timedelta

from psa_sniper.models import Listing, MarketValue, Money
from psa_sniper.point130 import parse_point130_sales
from psa_sniper.renaiss import RenaissMatch
from psa_sniper.repricing import _prefer_market, listing_from_history, reprice_state
from psa_sniper.state import default_state
from psa_sniper.util import iso_z, utc_now


class IdentityFX:
    def convert(self, money, currency):
        if money.currency.upper() != currency.upper():
            return None
        return Money(money.value, currency)


class FakeEbay:
    def __init__(self, rows, live=None, fail_live=False, fail_status=None):
        self.rows = rows
        self.live = live
        self.fail_live = fail_live
        self.fail_status = fail_status
        self.calls_made = 0
        self.queries = []

    def search(self, query, *, limit, started_after, offset=0, **kwargs):
        self.calls_made += 1
        self.queries.append((query, offset))
        return self.rows if offset == 0 else []

    def get_item(self, item_id, *, compact=False):
        from psa_sniper.ebay import EbayError
        self.calls_made += 1
        if self.fail_live:
            raise EbayError("gone", status_code=self.fail_status)
        if self.live is not None:
            return self.live
        return Listing(
            item_id=item_id,
            title="POKEMON ELFUN EX 165 PSA 10 GEM MINT DE",
            url=f"https://example.test/{item_id}",
            price=Money(80, "EUR"),
            created_at=utc_now() - timedelta(hours=2),
            buying_options=["FIXED_PRICE"],
        )


class FakeRenaiss:
    def __init__(self, market):
        self.market = market
        self.calls_made = 0
        self.max_calls = 2
        self.rate_limited = False

    def market_for_identity(self, identity, **kwargs):
        self.calls_made += 1
        return RenaissMatch(self.market, "renaiss-item", None, None)


def comp(item_id, price, seller):
    return Listing(
        item_id=item_id,
        title="Pokemon Elfun EX #165 PSA 10 GEM MINT",
        url=f"https://example.test/{item_id}",
        price=Money(price, "EUR"),
        created_at=utc_now(),
        buying_options=["FIXED_PRICE"],
        seller=seller,
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
        "availability_status": "active",
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
        "max_reprice_items_per_run": 60,
        "secondary_discovery_min_edge": 0.25,
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


def test_repricing_live_refresh_upgrades_psa_estimate_to_independent_ebay_market():
    state = default_state()
    row = weak_row()
    original_last_seen = row["last_seen_at"]
    state["history"] = [row]
    ebay = FakeEbay([comp("a", 130, "a"), comp("b", 140, "b"), comp("c", 150, "c")])

    result = reprice_state(state, settings(), ebay, IdentityFX(), max_comp_calls=6)

    assert result.checked == 1
    assert result.live_rechecks == 1
    assert result.improved == 1
    assert result.calls == 2
    updated = state["history"][0]
    assert updated["market_value"]["market_type"] == "ebay_active"
    assert updated["market_value"]["confidence"] == "mittel"
    assert updated["market_value"]["unique_sellers"] == 3
    assert updated["price_status"] == "verified_edge"
    assert updated["is_hit"] is True
    assert updated["last_seen_at"] == original_last_seen
    assert updated["price_checked_at"]
    assert updated["availability_checked_at"]
    assert updated["price_check_attempts"] == 1
    assert updated["pricing_identity"]


def test_repricing_prefers_exact_130point_sold_comps_without_active_comp_search():
    state = default_state()
    state["history"] = [weak_row()]
    sold_at = iso_z(utc_now() - timedelta(days=10))
    sales = parse_point130_sales({
        "sales": [
            {
                "id": sale_id,
                "title": "2025 Pokemon Elfun EX 165 German PSA 10",
                "price": {"value": value, "currency": "EUR"},
                "sold_at": sold_at,
                "source_url": "https://130point.com/search?new=sold",
            }
            for sale_id, value in (("a", 130), ("b", 140), ("c", 150))
        ]
    })
    ebay = FakeEbay([])

    result = reprice_state(
        state,
        settings(),
        ebay,
        IdentityFX(),
        max_comp_calls=6,
        point130_sales=sales,
    )

    assert result.point130_matches == 1
    assert result.improved == 1
    assert result.calls == 1
    assert ebay.queries == []
    updated = state["history"][0]
    assert updated["market_value"]["market_type"] == "point130_sold"
    assert updated["market_value"]["confidence"] == "hoch"
    assert updated["price_status"] == "verified_edge"


def test_new_130point_comps_immediately_repair_and_reprice_recent_bad_identity():
    state = default_state()
    row = weak_row(
        checked_at=iso_z(utc_now() - timedelta(minutes=5)),
        attempts=2,
    )
    row["title"] = "Pokemon Japanese Mew V RR PSA 10 Gem Mint s8 039/100 #c620"
    row["pricing_identity"] = {
        "version": 2,
        "card_number": "c620",
        "subjects": ["mew"],
        "terms": ["mew", "s8"],
        "set_code": "S8",
        "language": "JP",
    }
    state["history"] = [row]
    sold_at = iso_z(utc_now() - timedelta(days=10))
    sales = parse_point130_sales({
        "sales": [
            {
                "id": sale_id,
                "title": title,
                "price": {"value": value, "currency": "EUR"},
                "sold_at": sold_at,
                "source_url": "https://130point.com/search?new=sold",
            }
            for sale_id, title, value in (
                ("a", "Pokemon Japanese S8 Fusion Arts Mew V 039/100 Holo PSA 10", 130),
                ("b", "Mew V 039/100 RR Fusion Arts Japanese S8 Pokemon PSA 10", 140),
            )
        ]
    })
    live = Listing(
        item_id="own",
        title=row["title"],
        url=row["url"],
        price=Money(80, "EUR"),
        created_at=utc_now() - timedelta(hours=2),
        buying_options=["FIXED_PRICE"],
    )
    ebay = FakeEbay([], live=live)

    result = reprice_state(
        state,
        settings(),
        ebay,
        IdentityFX(),
        max_comp_calls=6,
        point130_sales=sales,
    )

    assert result.checked == 1
    assert result.point130_matches == 1
    assert result.calls == 1
    assert ebay.queries == []
    updated = state["history"][0]
    assert updated["pricing_identity"]["card_number"] == "039/100"
    assert updated["market_value"]["market_type"] == "point130_sold"
    assert updated["market_value"]["sample_size"] == 2


def test_repricing_uses_renaiss_psa10_fmv_before_active_asks():
    state = default_state()
    state["history"] = [weak_row()]
    renaiss_market = MarketValue(
        Money(145, "EUR"),
        "Renaiss Index · echte PSA-10-Verkäufe",
        "mittel",
        0,
        market_type="renaiss_fmv",
        required_edge=0.15,
    )
    renaiss = FakeRenaiss(renaiss_market)
    ebay = FakeEbay([])

    result = reprice_state(
        state,
        settings(),
        ebay,
        IdentityFX(),
        max_comp_calls=6,
        renaiss=renaiss,
    )

    assert result.renaiss_matches == 1
    assert result.improved == 1
    assert result.calls == 1
    assert ebay.queries == []
    updated = state["history"][0]
    assert updated["market_value"]["market_type"] == "renaiss_fmv"
    assert updated["price_status"] == "verified_edge"


def test_repricing_backoff_skips_recent_price_check():
    state = default_state()
    state["history"] = [
        weak_row(checked_at=iso_z(utc_now() - timedelta(minutes=30)), attempts=1)
    ]
    ebay = FakeEbay([comp("a", 130, "a"), comp("b", 140, "b"), comp("c", 150, "c")])
    result = reprice_state(state, settings(), ebay, IdentityFX(), max_comp_calls=6)
    assert result.checked == 0
    assert result.calls == 0
    assert ebay.queries == []


def test_repricing_marks_unavailable_target_and_removes_hit_status():
    state = default_state()
    row = weak_row()
    row["is_hit"] = True
    row["price_status"] = "verified_edge"
    state["history"] = [row]
    ebay = FakeEbay([], fail_live=True, fail_status=404)
    result = reprice_state(state, settings(), ebay, IdentityFX(), max_comp_calls=3)
    assert result.checked == 1
    assert result.expired == 1
    updated = state["history"][0]
    assert updated["availability_status"] == "unavailable"
    assert updated["is_hit"] is False
    assert updated["price_status"] == "unavailable"


def test_repricing_transient_live_error_keeps_listing_for_retry():
    state = default_state()
    row = weak_row()
    row["is_hit"] = True
    row["price_status"] = "verified_edge"
    state["history"] = [row]
    ebay = FakeEbay([], fail_live=True, fail_status=503)
    result = reprice_state(state, settings(), ebay, IdentityFX(), max_comp_calls=3)
    assert result.checked == 1
    assert result.expired == 0
    assert result.live_errors == 1
    updated = state["history"][0]
    assert updated["availability_status"] == "check_failed"
    assert updated["is_hit"] is False
    assert updated["price_status"] == "verified_edge"


def test_refresh_never_replaces_psa_sales_with_weaker_active_market():
    sales = MarketValue(
        Money(200, "EUR"), "PSA Sales", "hoch", 5,
        market_type="psa_sales", required_edge=0.10,
    )
    active = MarketValue(
        Money(180, "EUR"), "eBay", "mittel", 5,
        market_type="ebay_active", required_edge=0.20, unique_sellers=4,
    )
    assert _prefer_market(sales, active, refresh_same_type=True) is sales


def test_refresh_replaces_stale_market_of_same_quality_class():
    old = MarketValue(
        Money(180, "EUR"), "eBay", "mittel", 5,
        market_type="ebay_active", required_edge=0.20, unique_sellers=4,
    )
    fresh = MarketValue(
        Money(160, "EUR"), "eBay", "mittel", 5,
        market_type="ebay_active", required_edge=0.20, unique_sellers=4,
    )
    assert _prefer_market(old, fresh, refresh_same_type=True) is fresh
