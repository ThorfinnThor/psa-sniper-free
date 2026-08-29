from pathlib import Path

import requests

from psa_sniper.psa import PSAClient, _recent_sales, parse_psa_cert_html


def _response(status, content=b"{}"):
    response = requests.Response()
    response.status_code = status
    response.url = "https://api.psacard.com/publicapi/cert/GetByCertNumber/67205095"
    response._content = content
    return response


def test_parse_psa_cert_html():
    html = Path("tests/fixtures/psa_cert.html").read_text(encoding="utf-8")
    cert = parse_psa_cert_html("67205095", html)
    assert cert.valid
    assert cert.grade == "GEM MT 10"
    assert cert.population == 2
    assert cert.population_higher == 0
    assert cert.subject == "TAIWO AWONIYI"
    assert cert.variety == "X-FRACTOR"
    assert cert.estimate is not None
    assert cert.estimate.value == 90.0
    assert len(cert.recent_sales) == 2


def test_empty_psa_api_response_is_not_treated_as_valid():
    from psa_sniper.psa import parse_psa_api_json
    assert not parse_psa_api_json("67205095", {}).valid


def test_psa_api_response_extracts_core_fields():
    from psa_sniper.psa import parse_psa_api_json
    cert = parse_psa_api_json(
        "67205095",
        {
            "IsValidRequest": True,
            "CertNumber": "67205095",
            "Year": "2020",
            "Brand": "TOPPS CHROME",
            "Subject": "PLAYER NAME",
            "CardNumber": "16",
            "Grade": "10",
            "Population": 4,
            "PopulationHigher": 0,
        },
    )
    assert cert.valid
    assert cert.grade == "10"
    assert cert.population == 4


def test_psa_api_no_data_response_is_not_a_valid_cert():
    from psa_sniper.psa import parse_psa_api_json
    assert not parse_psa_api_json(
        "67205095", {"IsValidRequest": True, "ServerMessage": "No data found"}
    ).valid


def test_web_request_error_is_nonfatal(monkeypatch):
    client = PSAClient(web_fallback=True, delay_seconds=0, max_calls=8)
    def fail_get(*args, **kwargs):
        raise requests.exceptions.ConnectionError("temporary network error")
    monkeypatch.setattr(client.session, "get", fail_get)
    assert client.get_cert("79959648") is None


def test_direct_web_429_is_nonfatal_and_stops_more_web_lookups(monkeypatch):
    client = PSAClient(web_fallback=True, delay_seconds=0, max_calls=8)
    calls = 0
    response = requests.Response()
    response.status_code = 429
    response.url = "https://www.psacard.com/cert/79959648/psa"
    def get(*args, **kwargs):
        nonlocal calls
        calls += 1
        return response
    monkeypatch.setattr(client.session, "get", get)
    assert client.get_cert("79959648") is None
    assert client.web_rate_limited is True
    assert calls == 1
    assert client.get_cert("79959649") is None
    assert calls == 1


def test_validate_access_token_marks_success_without_exposing_token(monkeypatch):
    client = PSAClient(access_token="secret-test-token", web_fallback=False, delay_seconds=0, max_calls=2)
    response = _response(200, b'{"IsValidRequest":true,"CertNumber":"67205095","Grade":"10"}')
    monkeypatch.setattr(client.session, "get", lambda *args, **kwargs: response)
    assert client.validate_access_token() == "ok"
    assert client.api_successes == 1
    assert client.access_token == "secret-test-token"


def test_psa_client_normalizes_complete_authorization_header(monkeypatch):
    client = PSAClient(
        access_token="Authorization: Bearer secret-test-token",
        web_fallback=False,
        delay_seconds=0,
        max_calls=2,
    )
    response = _response(200, b'{"IsValidRequest":true,"CertNumber":"67205095","Grade":"10"}')
    seen_header = None

    def get(*args, **kwargs):
        nonlocal seen_header
        seen_header = kwargs["headers"]["Authorization"]
        return response

    monkeypatch.setattr(client.session, "get", get)
    assert client.validate_access_token() == "ok"
    assert seen_header == "bearer secret-test-token"


def test_validate_access_token_marks_rejected_and_disables_bad_token(monkeypatch):
    client = PSAClient(access_token="bad-token", web_fallback=True, delay_seconds=0, max_calls=2)
    monkeypatch.setattr(client.session, "get", lambda *args, **kwargs: _response(401))
    assert client.validate_access_token() == "abgelehnt"
    assert client.access_token is None
    assert client.api_successes == 0
    assert client.api_disabled_reason == "auth"


def test_initial_psa_500_opens_circuit_breaker_immediately(monkeypatch):
    client = PSAClient(access_token="maybe-bad", web_fallback=False, delay_seconds=0, max_calls=4)
    calls = 0
    def get(*args, **kwargs):
        nonlocal calls
        calls += 1
        return _response(500)
    monkeypatch.setattr(client.session, "get", get)
    assert client.validate_access_token() == "servicefehler"
    assert client.access_token is None
    assert client.api_disabled_reason == "server_or_credentials"
    # The run does not keep hammering the same API after the failed token probe.
    assert client.get_cert("79959648") is None
    assert calls == 1


def test_two_later_psa_5xx_open_circuit(monkeypatch):
    client = PSAClient(access_token="accepted-once", web_fallback=False, delay_seconds=0, max_calls=6)
    responses = iter([
        _response(200, b'{"IsValidRequest":true,"CertNumber":"67205095","Grade":"10"}'),
        _response(503),
        _response(503),
    ])
    monkeypatch.setattr(client.session, "get", lambda *args, **kwargs: next(responses))
    assert client.validate_access_token() == "ok"
    assert client.get_cert("79959648") is None
    assert client.access_token is not None
    assert client.get_cert("79959649") is None
    assert client.access_token is None
    assert client.api_disabled_reason == "service_unavailable"


def test_recent_sales_stops_before_duplicate_mobile_sales_block():
    text = """
Sales of Similar Items
$100.00
$120.00
$140.00
Sales of Similar Items
$100.00
$120.00
$140.00
Set Registry
PSA Estimate
$999.00
""".strip()
    sales = _recent_sales(text)
    assert [row.value for row in sales] == [100.0, 120.0, 140.0]
    assert all(row.currency == "USD" for row in sales)


def test_api_only_lookup_does_not_fall_back_to_web(monkeypatch):
    client = PSAClient(access_token="token", web_fallback=True, delay_seconds=0, max_calls=3)
    monkeypatch.setattr(client, "_get_api", lambda cert: None)
    called = {"web": 0}
    monkeypatch.setattr(client, "_get_web", lambda cert: called.__setitem__("web", called["web"] + 1))
    assert client.get_api_cert("12345678") is None
    assert called["web"] == 0


def test_initial_psa_503_is_transient_and_keeps_token(monkeypatch):
    client = PSAClient(access_token="fresh-token", web_fallback=False, delay_seconds=0, max_calls=3)
    monkeypatch.setattr(client.session, "get", lambda *args, **kwargs: _response(503))
    assert client.validate_access_token() == "servicefehler"
    assert client.access_token == "fresh-token"
    assert client.api_disabled_reason is None
    assert client.calls_made == 1


def test_second_psa_503_opens_transient_service_circuit(monkeypatch):
    client = PSAClient(access_token="fresh-token", web_fallback=False, delay_seconds=0, max_calls=4)
    monkeypatch.setattr(client.session, "get", lambda *args, **kwargs: _response(503))
    assert client.validate_access_token() == "servicefehler"
    assert client.access_token is not None
    assert client.get_api_cert("79959648") is None
    assert client.access_token is None
    assert client.api_disabled_reason == "service_unavailable"
    assert client.calls_made == 2
