from pathlib import Path

import requests

from psa_sniper.psa import PSAClient, _recent_sales, parse_psa_cert_html


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

    cert = parse_psa_api_json("67205095", {})
    assert not cert.valid


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

    cert = parse_psa_api_json(
        "67205095",
        {"IsValidRequest": True, "ServerMessage": "No data found"},
    )
    assert not cert.valid


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

    # The rest of this scan must not keep hammering PSA.
    assert client.get_cert("79959649") is None
    assert calls == 1


def test_validate_access_token_marks_success_without_exposing_token(monkeypatch):
    client = PSAClient(
        access_token="secret-test-token",
        web_fallback=False,
        delay_seconds=0,
        max_calls=2,
    )

    response = requests.Response()
    response.status_code = 200
    response.url = "https://api.psacard.com/publicapi/cert/GetByCertNumber/67205095"
    response._content = b'{"IsValidRequest":true,"CertNumber":"67205095","Grade":"10"}'

    monkeypatch.setattr(client.session, "get", lambda *args, **kwargs: response)

    assert client.validate_access_token() == "ok"
    assert client.api_successes == 1
    assert client.access_token == "secret-test-token"


def test_validate_access_token_marks_rejected_and_disables_bad_token(monkeypatch):
    client = PSAClient(
        access_token="bad-token",
        web_fallback=True,
        delay_seconds=0,
        max_calls=2,
    )

    response = requests.Response()
    response.status_code = 401
    response.url = "https://api.psacard.com/publicapi/cert/GetByCertNumber/67205095"
    monkeypatch.setattr(client.session, "get", lambda *args, **kwargs: response)

    assert client.validate_access_token() == "abgelehnt"
    assert client.access_token is None
    assert client.api_successes == 0


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
