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


def test_dashboard_hides_high_confidence_listing_25_percent_over_market():
    state = {
        "history": [
            {
                "item_id": "bad-buy",
                "last_seen_at": "2026-08-27T20:00:00Z",
                "discount_pct": -0.30,
                "market_value": {"confidence": "hoch"},
                "is_hit": False,
            },
            {
                "item_id": "possible-buy",
                "last_seen_at": "2026-08-27T20:00:00Z",
                "discount_pct": 0.20,
                "market_value": {"confidence": "hoch"},
                "is_hit": True,
            },
        ],
        "runs": [],
    }
    payload = dashboard_payload(state)
    assert [row["item_id"] for row in payload["hits"]] == ["possible-buy"]
    assert payload["hits"][0]["price_status"] == "verified_edge"
    assert payload["hits"][0]["is_hit"] is True


def test_legacy_hit_without_verified_price_is_demoted_to_watch():
    state = {
        "history": [
            {
                "item_id": "legacy-low-pop",
                "title": "Low pop PSA 10",
                "last_seen_at": "2026-08-28T08:00:00Z",
                "score": 11,
                "is_hit": True,
                "market_value": None,
                "discount_pct": None,
            }
        ],
        "runs": [],
    }
    payload = dashboard_payload(state)
    row = payload["hits"][0]
    assert row["scan_is_hit"] is True
    assert row["is_hit"] is False
    assert row["price_status"] == "unverified"


def test_active_comp_legacy_record_uses_its_twenty_percent_required_edge():
    state = {
        "history": [
            {
                "item_id": "active-comp-15",
                "last_seen_at": "2026-08-28T08:00:00Z",
                "score": 13,
                "is_hit": True,
                "discount_pct": 0.15,
                "market_value": {
                    "confidence": "mittel",
                    "required_edge": 0.20,
                    "market_type": "ebay_active",
                },
            }
        ],
        "runs": [],
    }
    row = dashboard_payload(state)["hits"][0]
    assert row["price_status"] == "no_edge"
    assert row["is_hit"] is False
