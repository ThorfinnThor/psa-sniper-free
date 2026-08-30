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
    build_dashboard(state_file, out, password="this-is-a-long-test-password", plain=False)
    raw = (out / "data.enc.json").read_text(encoding="utf-8")
    assert "Pikachu" not in raw
    assert (out / "index.html").exists()
    meta = json.loads((out / "meta.json").read_text(encoding="utf-8"))
    assert meta["schema_version"] == 5


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
    assert payload["schema_version"] == 5
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
    row = dashboard_payload(state)["hits"][0]
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


def test_ended_listing_is_removed_from_main_dashboard_but_kept_in_archive():
    state = {
        "history": [{
            "item_id": "ended",
            "title": "Ended PSA 10",
            "last_seen_at": "2026-08-29T07:00:00Z",
            "availability_status": "ended",
            "price_status": "verified_edge",
            "is_hit": True,
            "score": 15,
        }],
        "runs": [],
    }
    payload = dashboard_payload(state)
    assert payload["hits"] == []
    assert payload["archive_hits"][0]["price_status"] == "unavailable"
    assert payload["archive_hits"][0]["is_hit"] is False


def test_dashboard_build_contains_dynamic_market_quality_and_repricing_ui(tmp_path: Path):
    state_file = tmp_path / "state.json"
    out = tmp_path / "site"
    save_state(state_file, demo_state())
    build_dashboard(state_file, out, password="this-is-a-long-test-password", plain=False)
    app = (out / "app.js").read_text(encoding="utf-8")
    index = (out / "index.html").read_text(encoding="utf-8")
    assert "unabh. Verkäufer" in app
    assert "price_status === 'verified_edge'" in app
    assert "repricing_checked" in app
    assert "PriceDiag:" in app
    assert "Warum fehlt / schwächelt der Preis?" in app
    assert "Keine sichere Identität" in app
    assert "ageHours <= 3.75" in app
    assert "function selectDefaultView()" in app
    assert "rows.some(row => row.is_hit)" in app
    assert "setResultView('watch')" in app
    assert "function weakPriceExplanation(row)" in app
    assert "Identität nur aus Listingdaten bestätigt" in app
    assert "eBayCompDetails" in app
    assert "130point Sold prüfen" not in app
    assert "point130_sold" in app
    assert "Renaiss PSA-10-Preis" in app
    assert "renaiss_fmv" in app
    assert 'id="showAvailable"' in index
    assert 'class="chip active" data-view="all"' in index
    assert 'class="chip active" data-view="hits"' not in index


def test_live_check_failed_is_visible_but_never_a_hit():
    state = {
        "history": [{
            "item_id": "live-error", "title": "PSA 10",
            "last_seen_at": "2026-08-29T10:00:00Z",
            "availability_status": "check_failed",
            "price_status": "verified_edge", "is_hit": True, "score": 14,
        }],
        "runs": [],
    }
    row = dashboard_payload(state)["hits"][0]
    assert row["price_status"] == "live_check_failed"
    assert row["is_hit"] is False
