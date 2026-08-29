from datetime import timedelta

from psa_sniper.models import Money, PSACertInfo
from psa_sniper.psa import cert_needs_api_upgrade, merge_cert_info
from psa_sniper.psa_backfill import backfill_state
from psa_sniper.state import default_state
from psa_sniper.util import iso_z, utc_now


class FakePSA:
    def __init__(self, cert):
        self.cert = cert
        self.calls_made = 0
        self.access_token = "token"
        self.api_auth_status = "ok"
    def get_api_cert(self, number):
        self.calls_made += 1
        return self.cert if number == self.cert.cert_number else None


def history_row():
    now = utc_now() - timedelta(hours=2)
    return {
        "item_id": "x",
        "title": "POKEMON ELFUN EX 165 PSA 10 GEM MINT DE",
        "url": "https://example.test/x",
        "price": {"value": 80, "currency": "EUR"},
        "total_cost": {"value": 80, "currency": "EUR"},
        "created_at": iso_z(now),
        "first_seen_at": iso_z(now),
        "last_seen_at": iso_z(now),
        "buying_options": ["FIXED_PRICE"],
        "score": 7,
        "is_hit": False,
        "price_status": "weak_indicator",
        "availability_status": "active",
        "cert_number": "131778450",
        "cert_source": "OCR (Fallback)",
        "cert_confidence": .95,
        "cert": {
            "cert_number": "131778450", "valid": True, "grade": "GEM MT 10",
            "year": "2025", "brand_title": "POKEMON GERMAN WHT DE-WHITE FLARE",
            "subject": "WHIMSICOTT ex", "card_number": "165", "variety": "SIR",
            "population": None, "population_higher": None,
            "estimate": {"value": 100, "currency": "EUR"}, "recent_sales": [],
            "data_source": "öffentliche PSA-Cert-Seite",
        },
        "market_value": {
            "money": {"value": 100, "currency": "EUR"}, "source": "PSA Estimate",
            "confidence": "niedrig", "sample_size": 0, "market_type": "psa_estimate",
            "required_edge": .25,
        },
        "discount_pct": .20,
        "score_breakdown": [],
    }


def settings():
    return {
        "max_psa_backfill_calls_per_run": 12,
        "psa_backfill_history_hours": 168,
        "hit_threshold": 11,
        "priority_terms": [], "demand_terms": [],
    }


def test_merge_preserves_web_market_and_prefers_api_population():
    old = PSACertInfo(
        cert_number="1", valid=True, grade="10", subject="A", card_number="1",
        population=None, estimate=Money(123, "EUR"), data_source="web",
    )
    api = PSACertInfo(
        cert_number="1", valid=True, grade="GEM MT 10", subject="A", card_number="1",
        population=9, data_source="PSA Public API",
    )
    merged = merge_cert_info(api, old)
    assert merged.population == 9
    assert merged.estimate.value == 123
    assert "PSA Public API" in merged.data_source
    assert cert_needs_api_upgrade(merged) is False


def test_backfill_upgrades_history_and_rescores_low_pop():
    state = default_state()
    state["history"] = [history_row()]
    api = PSACertInfo(
        cert_number="131778450", valid=True, grade="GEM MT 10", year="2025",
        brand_title="POKEMON GERMAN WHT DE-WHITE FLARE", subject="WHIMSICOTT ex",
        card_number="165", variety="SPECIAL ILLUSTRATION RARE", population=9,
        population_higher=0, data_source="PSA Public API",
    )
    result = backfill_state(state, settings(), FakePSA(api))
    assert result.checked_certs == 1
    assert result.upgraded_certs == 1
    assert result.rescored_rows == 1
    row = state["history"][0]
    assert row["cert"]["population"] == 9
    assert "PSA Public API" in row["cert"]["data_source"]
    assert row["cert_trusted"] is True


def test_api_sourced_cert_with_missing_population_waits_for_normal_cache_ttl():
    cert = PSACertInfo(
        cert_number="2", valid=True, grade="10", subject="A", card_number="1",
        population=None, data_source="PSA Public API",
    )
    assert cert_needs_api_upgrade(cert) is False
