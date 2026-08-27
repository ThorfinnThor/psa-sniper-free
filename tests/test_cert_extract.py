from psa_sniper.cert_extract import extract_cert_from_aspects, extract_cert_from_title
from psa_sniper.models import Listing


def test_extract_cert_from_aspects():
    listing = Listing(
        item_id="1",
        title="PSA 10 card",
        url="x",
        price=None,
        created_at=None,
        aspects={"Certification Number": ["67205095"]},
    )
    candidate = extract_cert_from_aspects(listing)
    assert candidate is not None
    assert candidate.number == "67205095"
    assert candidate.confidence == 1.0


def test_extract_cert_from_title():
    candidate = extract_cert_from_title("Pikachu PSA 10 Cert #67205095")
    assert candidate is not None
    assert candidate.number == "67205095"
    assert candidate.source == "Titel"
