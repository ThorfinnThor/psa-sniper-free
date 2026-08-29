from __future__ import annotations

import pytest

from psa_sniper.ebay import EbayBudgetExceeded, EbayClient, EbayError


class Response:
    def __init__(self, status, payload=None, headers=None):
        self.status_code = status
        self._payload = payload if payload is not None else {}
        self.headers = headers or {}

    def json(self):
        return self._payload


class SequenceSession:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = 0

    def get(self, *args, **kwargs):
        self.calls += 1
        return self.responses.pop(0)


def client(max_calls=5):
    c = EbayClient("id", "secret", delay_seconds=0, max_calls=max_calls)
    c.token = "token"
    return c


def test_retry_attempts_are_counted_against_browse_budget(monkeypatch):
    c = client()
    c.session = SequenceSession([Response(503), Response(200, {"ok": True})])
    monkeypatch.setattr("psa_sniper.ebay.time.sleep", lambda *_: None)
    assert c._get("/x") == {"ok": True}
    assert c.calls_made == 2
    assert c.session.calls == 2


def test_retry_cannot_escape_hard_call_cap(monkeypatch):
    c = client(max_calls=1)
    c.session = SequenceSession([Response(503), Response(200, {"ok": True})])
    monkeypatch.setattr("psa_sniper.ebay.time.sleep", lambda *_: None)
    with pytest.raises(EbayBudgetExceeded):
        c._get("/x")
    assert c.calls_made == 1


def test_http_error_exposes_status_and_missing_flag(monkeypatch):
    c = client()
    c.session = SequenceSession([Response(404, {"errors": [{"message": "gone"}]})])
    monkeypatch.setattr("psa_sniper.ebay.time.sleep", lambda *_: None)
    with pytest.raises(EbayError) as caught:
        c._get("/item/x")
    assert caught.value.status_code == 404
    assert caught.value.missing is True
