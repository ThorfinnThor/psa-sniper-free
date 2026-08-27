from pathlib import Path

import requests

from psa_sniper.psa import PSAClient, parse_psa_cert_html


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


def test_web_retry_error_from_429_is_nonfatal_and_disables_psa(monkeypatch):
    client = PSAClient(web_fallback=True, delay_seconds=0, max_calls=8)
    calls = 0

    def fail_get(*args, **kwargs):
        nonlocal calls
        calls += 1
        raise requests.exceptions.RetryError(
            "too many 429 error responses"
        )

    monkeypatch.setattr(client.session, "get", fail_get)

    assert client.get_cert("79959648") is None
    assert client.rate_limited is True
    assert client.web_fallback is False
    assert calls == 1

    # The rest of this scan must not keep hammering PSA.
    assert client.get_cert("79959649") is None
    assert calls == 1


def test_direct_web_429_is_nonfatal_and_disables_psa(monkeypatch):
    client = PSAClient(web_fallback=True, delay_seconds=0, max_calls=8)

    response = requests.Response()
    response.status_code = 429
    response.url = "https://www.psacard.com/cert/79959648/psa"

    monkeypatch.setattr(client.session, "get", lambda *args, **kwargs: response)

    assert client.get_cert("79959648") is None
    assert client.rate_limited is True
    assert client.web_fallback is False
