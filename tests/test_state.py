from psa_sniper.state import default_state, select_queries


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
    assert state["schema_version"] >= 2
