import json
from pathlib import Path


def test_overlap_cooldown_allows_next_three_hour_scan_recheck():
    settings = json.loads(Path("config/settings.json").read_text(encoding="utf-8"))
    assert settings["processed_cooldown_minutes"] < settings["automatic_scan_min_age_minutes"]
    assert settings["max_psa_backfill_calls_per_run"] <= settings["max_psa_calls_per_run"]
