from datetime import datetime, timezone

from psa_sniper.models import Listing, MarketValue, Money, RunStats, ScoredHit
from psa_sniper.state import (
    append_run,
    default_state,
    get_cached_market,
    migrate_state,
    put_cached_market,
    select_queries,
    upsert_history,
)


def test_query_rotation_wraps_fairly():
    queries = ["a", "b", "c", "d", "e"]
    first, cursor = select_queries(queries, 0, 3)
    second, cursor = select_queries(queries, cursor, 3)
    assert first == ["a", "b", "c"]
    assert second == ["d", "e", "a"]
    assert cursor == 1


def test_default_state_has_no_plaintext_hits():
    state = default_state()
    assert state["history"] == []
    assert state["market_cache"] == {}
    assert state["schema_version"] >= 6


def test_migration_scrubs_seller_identity_fields():
    state = {
        "schema_version": 2,
        "history": [{
            "item_id": "123",
            "title": "PSA 10 Card",
            "seller": "seller-name",
            "seller_feedback_percentage": 99.9,
            "seller_feedback_score": 1000,
        }],
    }
    migrated = migrate_state(state)
    row = migrated["history"][0]
    assert row["item_id"] == "123"
    assert "seller" not in row
    assert "seller_feedback_percentage" not in row
    assert "seller_feedback_score" not in row
    assert migrated["market_cache"] == {}


def test_run_snapshot_keeps_hit_details_but_scrubs_seller_identity():
    state = default_state()
    stats = RunStats(
        started_at="2026-08-28T08:00:00Z",
        completed_at="2026-08-28T08:01:00Z",
        queries_used=12,
        listings_seen=40,
        fresh_listings=30,
        detailed_candidates=18,
        psa_lookups=4,
        hits=1,
        near_hits=0,
        ebay_calls=30,
    )
    append_run(
        state,
        stats,
        100,
        results=[{
            "item_id": "v1|123|0",
            "title": "PSA 10 Card",
            "score": 14,
            "seller": "seller-name",
            "seller_feedback_percentage": 99.9,
            "seller_feedback_score": 500,
        }],
    )
    result = state["runs"][0]["results"][0]
    assert result["item_id"] == "v1|123|0"
    assert result["score"] == 14
    assert state["runs"][0]["total_ebay_calls"] == 30
    assert "seller" not in result
    assert "seller_feedback_percentage" not in result
    assert "seller_feedback_score" not in result


def test_market_cache_round_trip_preserves_quality_metadata():
    state = default_state()
    market = MarketValue(
        Money(120, "EUR"),
        "eBay aktive PSA-10-Vergleichsangebote",
        "mittel",
        5,
        market_type="ebay_active",
        required_edge=0.20,
        unique_sellers=4,
        price_low=110,
        price_high=135,
        dispersion=0.18,
    )
    put_cached_market(state, "fingerprint", market)
    found, restored = get_cached_market(state, "fingerprint", 8)
    assert found is True
    assert restored is not None
    assert restored.money.value == 120
    assert restored.market_type == "ebay_active"
    assert restored.required_edge == 0.20
    assert restored.unique_sellers == 4
    assert restored.price_low == 110
    assert restored.price_high == 135
    assert restored.dispersion == 0.18

    put_cached_market(state, "no-market", None)
    found, restored = get_cached_market(state, "no-market", 8)
    assert found is True
    assert restored is None


def test_upsert_history_preserves_repricing_metadata_and_adds_pricing_identity():
    state = default_state()
    state["history"] = [{
        "item_id": "x",
        "first_seen_at": "2026-08-28T00:00:00Z",
        "last_seen_at": "2026-08-28T01:00:00Z",
        "price_checked_at": "2026-08-28T01:10:00Z",
        "price_check_attempts": 2,
        "price_last_improved_at": "2026-08-28T01:10:00Z",
        "availability_checked_at": "2026-08-28T01:10:00Z",
    }]
    listing = Listing(
        item_id="x",
        title="Pikachu SV2A #173 Japanese PSA 10",
        url="https://example.test/x",
        price=Money(100, "EUR"),
        created_at=datetime.now(timezone.utc),
        buying_options=["FIXED_PRICE"],
    )
    hit = ScoredHit(listing=listing, score=5, reasons=[], price_status="unverified")
    upsert_history(state, hit, 11)
    row = state["history"][0]
    assert row["first_seen_at"] == "2026-08-28T00:00:00Z"
    assert row["price_check_attempts"] == 2
    assert row["price_checked_at"] == "2026-08-28T01:10:00Z"
    assert row["pricing_identity"]["card_number"] == "173"
    assert row["pricing_identity"]["language"] == "JP"
