from pathlib import Path


def test_scheduler_is_separate_from_scanner_core():
    core = Path(".github/workflows/sniper.yml").read_text(encoding="utf-8")
    scheduler = Path(".github/workflows/sniper-schedule.yml").read_text(encoding="utf-8")

    assert "workflow_call:" in core
    assert "schedule:" not in core
    assert "schedule:" in scheduler
    assert 'cron: "2,7,12,17,22,27,32,37,42,47,52,57 * * * *"' in scheduler
    assert "uses: ./.github/workflows/sniper.yml" in scheduler
    assert "automated: true" in scheduler
    assert "secrets: inherit" in scheduler
