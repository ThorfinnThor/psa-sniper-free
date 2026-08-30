import json
from pathlib import Path


def test_overlap_cooldown_allows_next_three_hour_scan_recheck():
    settings = json.loads(Path("config/settings.json").read_text(encoding="utf-8"))
    assert settings["processed_cooldown_minutes"] < settings["automatic_scan_min_age_minutes"]
    assert settings["max_psa_backfill_calls_per_run"] <= settings["max_psa_calls_per_run"]


def test_import_risk_defaults_are_conservative_for_german_buyer():
    settings = json.loads(Path("config/settings.json").read_text(encoding="utf-8"))
    assert settings["import_risk_extra_edge"] >= .10
    assert "DE" in settings["import_risk_exempt_countries"]
    assert "US" not in settings["import_risk_exempt_countries"]


def test_unknown_shipping_has_nonzero_safety_margin():
    settings = json.loads(Path("config/settings.json").read_text(encoding="utf-8"))
    assert settings["unknown_shipping_extra_edge"] >= .05


def test_130point_sold_defaults_require_recent_conservative_psa10_evidence():
    settings = json.loads(Path("config/settings.json").read_text(encoding="utf-8"))
    assert 90 <= settings["point130_sold_max_age_days"] <= 365
    assert settings["point130_sold_required_edge"] >= .10
    assert settings["enable_point130_legacy"] is False


def test_renaiss_defaults_respect_public_api_quota_and_cache_results():
    settings = json.loads(Path("config/settings.json").read_text(encoding="utf-8"))
    assert 1 <= settings["max_renaiss_calls_per_run"] <= 8
    assert 0 <= settings["max_renaiss_reprice_calls_per_run"] <= 2
    assert settings["max_renaiss_public_calls_per_run"] <= 1
    assert settings["max_renaiss_public_reprice_calls_per_run"] == 0
    assert settings["renaiss_cache_hours"] >= 24
    assert settings["renaiss_max_sale_age_days"] <= 365
