from psa_sniper.quota import allocate_call_budgets


def settings():
    return {
        "max_search_calls_per_run": 24,
        "max_detail_calls_per_run": 470,
        "max_market_comp_calls_per_run": 140,
        "max_market_comp_detail_calls_per_run": 48,
        "max_reprice_comp_calls_per_run": 100,
    }


def test_full_budget_reserves_market_and_repricing_inside_hardcap():
    value = allocate_call_budgets(575, settings())
    assert value == {
        "search": 24,
        "detail": 266,
        "market": 138,
        "market_detail": 48,
        "reprice": 99,
    }
    assert sum(value.values()) == 575


def test_low_budget_scales_maintenance_and_preserves_details():
    value = allocate_call_budgets(100, settings())
    assert sum(value.values()) <= 100
    assert value["search"] == 24
    assert value["detail"] > 0
    assert value["market"] < 140
    assert value["market_detail"] < 48
    assert value["reprice"] < 100
