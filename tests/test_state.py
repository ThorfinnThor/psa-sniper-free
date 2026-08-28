from psa_sniper.models import RunStats
from psa_sniper.state import append_run, default_state, migrate_state, select_queries


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
    assert state["schema_version"] >= 3


def test_migration_scrubs_seller_identity_fields():
    state = {
        "schema_version": 2,
        "history": [
            {
                "item_id": "123",
                "title": "PSA 10 Card",
                "seller": "seller-name",
                "seller_feedback_percentage": 99.9,
                "seller_feedback_score": 1000,
            }
        ],
    }
    migrated = migrate_state(state)
    row = migrated["history"][0]
    assert row["item_id"] == "123"
    assert "seller" not in row
    assert "seller_feedback_percentage" not in row
    assert "seller_feedback_score" not in row


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
        results=[
            {
                "item_id": "v1|123|0",
                "title": "PSA 10 Card",
                "score": 14,
                "seller": "seller-name",
                "seller_feedback_percentage": 99.9,
                "seller_feedback_score": 500,
            }
        ],
    )
    result = state["runs"][0]["results"][0]
    assert result["item_id"] == "v1|123|0"
    assert result["score"] == 14
    assert "seller" not in result
    assert "seller_feedback_percentage" not in result
    assert "seller_feedback_score" not in result
