import json
from pathlib import Path

from psa_sniper.crypto import decrypt_json, encrypt_json
from psa_sniper.dashboard import build_dashboard, dashboard_payload
from psa_sniper.demo import demo_state
from psa_sniper.state import save_state


def test_encryption_round_trip():
    payload = {"secret": "Pikachu", "count": 3}
    envelope = encrypt_json(payload, "this-is-a-long-test-password")
    assert "Pikachu" not in json.dumps(envelope)
    assert decrypt_json(envelope, "this-is-a-long-test-password") == payload


def test_dashboard_contains_only_encrypted_payload(tmp_path: Path):
    state_file = tmp_path / "state.json"
    out = tmp_path / "site"
    save_state(state_file, demo_state())
    build_dashboard(
        state_file,
        out,
        password="this-is-a-long-test-password",
        plain=False,
    )
    raw = (out / "data.enc.json").read_text(encoding="utf-8")
    assert "Pikachu" not in raw
    assert (out / "index.html").exists()


def test_dashboard_hides_high_confidence_listing_25_percent_over_market_but_archives_it():
    state = {
        "history": [
            {
                "item_id": "bad-buy",
                "last_seen_at": "2026-08-27T20:00:00Z",
                "discount_pct": -0.30,
                "market_value": {"confidence": "hoch"},
            },
            {
                "item_id": "possible-buy",
                "last_seen_at": "2026-08-27T20:00:00Z",
                "discount_pct": 0.20,
                "market_value": {"confidence": "hoch"},
            },
        ],
        "runs": [],
    }
    payload = dashboard_payload(state)
    assert [row["item_id"] for row in payload["hits"]] == ["possible-buy"]
    assert {row["item_id"] for row in payload["archive_hits"]} == {"bad-buy", "possible-buy"}
