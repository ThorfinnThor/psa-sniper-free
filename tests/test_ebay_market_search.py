from datetime import datetime, timezone

import requests

from psa_sniper.ebay import EbayClient


def _response(payload):
    response = requests.Response()
    response.status_code = 200
    response._content = __import__('json').dumps(payload).encode()
    return response


def client():
    ebay = EbayClient("id", "secret", delay_seconds=0, max_calls=10)
    ebay.token = "token"
    return ebay


def test_discovery_search_uses_newly_listed_and_time_filter(monkeypatch):
    ebay = client()
    captured = {}
    def get(url, **kwargs):
        captured.update(kwargs.get("params") or {})
        return _response({"itemSummaries": []})
    monkeypatch.setattr(ebay.session, "get", get)
    ebay.search(
        "PSA 10 Pokemon",
        limit=100,
        started_after=datetime(2026, 8, 29, 8, 0, tzinfo=timezone.utc),
    )
    assert captured["sort"] == "newlyListed"
    assert "itemStartDate" in captured["filter"]
    assert "buyingOptions" not in captured["filter"]


def test_comp_search_uses_best_match_default_fixed_price_and_offset(monkeypatch):
    ebay = client()
    captured = {}
    def get(url, **kwargs):
        captured.update(kwargs.get("params") or {})
        return _response({"itemSummaries": []})
    monkeypatch.setattr(ebay.session, "get", get)
    ebay.search("Pikachu 173 PSA 10", limit=100, started_after=None, offset=100)
    assert "sort" not in captured
    assert captured["offset"] == 100
    assert "buyingOptions:{FIXED_PRICE}" in captured["filter"]


def test_developer_analytics_parses_conservative_remaining(monkeypatch):
    ebay = client()
    payload = {
        "rateLimits": [
            {"resources": [{"rates": [{"limit": 5000, "remaining": 3200, "count": 1800}]}]},
            {"resources": [{"rates": [{"limit": 5000, "remaining": 3000, "count": 2000}]}]},
        ]
    }
    monkeypatch.setattr(ebay.session, "get", lambda *args, **kwargs: _response(payload))
    snapshot = ebay.get_rate_limits()
    assert snapshot is not None
    assert snapshot.remaining == 3000
    assert snapshot.limit == 5000
    assert snapshot.count == 2000
