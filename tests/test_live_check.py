from datetime import timedelta

from psa_sniper.live_check import listing_available, merge_live_listing, refresh_hit_for_purchase
from psa_sniper.models import Listing, MarketValue, Money, ScoredHit
from psa_sniper.util import utc_now


def listing(price=80, *, ended=False):
    return Listing(
        item_id="x",
        title="Pikachu SV2A #173 Japanese PSA 10",
        url="https://example.test/x",
        price=Money(price, "EUR"),
        created_at=utc_now() - timedelta(hours=2),
        end_at=utc_now() - timedelta(minutes=1) if ended else utc_now() + timedelta(days=1),
        buying_options=["FIXED_PRICE"],
    )


class FakeEbay:
    def __init__(self, live):
        self.live = live
    def get_item(self, item_id, *, compact=False):
        assert compact is True
        return self.live


def test_ended_listing_is_not_available():
    assert listing_available(listing(ended=True)) is False


def test_merge_live_listing_replaces_price_and_end_time():
    stored = listing(80)
    live = listing(95)
    merged = merge_live_listing(stored, live)
    assert merged.price.value == 95
    assert merged.end_at == live.end_at


def test_purchase_refresh_demotes_hit_after_live_price_increase():
    stored = listing(80)
    hit = ScoredHit(
        listing=stored,
        score=13,
        reasons=[],
        market_value=MarketValue(
            Money(120, "EUR"),
            "eBay aktive PSA-10-Vergleichsangebote",
            "mittel",
            4,
            market_type="ebay_active",
            required_edge=0.20,
        ),
        discount_pct=1 - 80 / 120,
        price_status="verified_edge",
    )
    refreshed, status = refresh_hit_for_purchase(
        hit,
        FakeEbay(listing(110)),
        {"hit_threshold": 11, "priority_terms": [], "demand_terms": []},
    )
    assert refreshed is not None
    assert status == "no_longer_hit"
    assert refreshed.price_status in {"no_edge", "over_market"}
