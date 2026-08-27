from psa_sniper.state import default_state, migrate_state, select_queries


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
