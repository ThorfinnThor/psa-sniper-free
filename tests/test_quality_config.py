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
