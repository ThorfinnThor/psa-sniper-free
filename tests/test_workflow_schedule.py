from pathlib import Path


def test_scheduler_is_separate_from_scanner_core():
    core = Path(".github/workflows/sniper.yml").read_text(encoding="utf-8")
    scheduler = Path(".github/workflows/sniper-schedule.yml").read_text(encoding="utf-8")

    assert "workflow_call:" in core
    assert "workflow_dispatch:" in core
    assert "schedule:" not in core
    assert "automated:" in core
    assert "concurrency:" in core
    assert "cancel-in-progress: false" in core

    assert "schedule:" in scheduler
    assert 'cron: "2-57/5 * * * *"' in scheduler
    assert "actions: write" in scheduler
    assert "GH_TOKEN: ${{ github.token }}" in scheduler
    assert "/actions/workflows/sniper.yml/dispatches" in scheduler
    assert "inputs[automated]=true" in scheduler
    assert "status=in_progress" in scheduler
    assert "status=queued" in scheduler
    assert "kein doppelter Dispatch" in scheduler
