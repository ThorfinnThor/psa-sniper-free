from pathlib import Path

from psa_sniper.psa import parse_psa_cert_html


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
