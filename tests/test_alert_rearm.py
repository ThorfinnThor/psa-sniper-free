from datetime import datetime, timezone

from psa_sniper.models import Listing, Money, ScoredHit
from psa_sniper.state import default_state, mark_alerted, should_alert


def hit(price=80, edge=.25):
    listing = Listing(
        item_id="x", title="PSA 10", url="https://example.test/x",
        price=Money(price, "EUR"), created_at=datetime.now(timezone.utc),
        buying_options=["FIXED_PRICE"],
    )
    return ScoredHit(listing=listing, score=13, reasons=[], discount_pct=edge, price_status="verified_edge")


def test_first_hit_alerts_and_unchanged_hit_does_not_repeat():
    state = default_state()
    first = hit()
    assert should_alert(state, first) is True
    mark_alerted(state, "x", {"dashboard": True}, hit=first)
    assert should_alert(state, hit()) is False


def test_material_price_drop_rearms_alert():
    state = default_state()
    first = hit(price=80, edge=.25)
    mark_alerted(state, "x", {"dashboard": True}, hit=first)
    assert should_alert(state, hit(price=70, edge=.25), min_price_drop_pct=.10) is True


def test_material_edge_improvement_rearms_alert():
    state = default_state()
    first = hit(price=80, edge=.20)
    mark_alerted(state, "x", {"dashboard": True}, hit=first)
    assert should_alert(state, hit(price=80, edge=.31), min_edge_improvement=.10) is True
