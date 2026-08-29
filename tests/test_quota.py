from psa_sniper.quota import allocate_call_budgets


def settings():
    return {
        "max_search_calls_per_run": 24,
        "max_detail_calls_per_run": 470,
        "max_market_comp_calls_per_run": 80,
        "max_reprice_comp_calls_per_run": 60,
    }


def test_full_budget_reserves_market_and_repricing_inside_hardcap():
    value = allocate_call_budgets(575, settings())
    assert value == {"search": 24, "detail": 411, "market": 80, "reprice": 60}
    assert sum(value.values()) == 575


def test_low_budget_scales_maintenance_and_preserves_details():
    value = allocate_call_budgets(100, settings())
    assert sum(value.values()) <= 100
    assert value["search"] == 24
    assert value["detail"] > 0
    assert value["market"] < 80
    assert value["reprice"] < 60
