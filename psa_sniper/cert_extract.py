from __future__ import annotations

import re

from .models import CertCandidate, Listing
from .util import normalize_text

CERT_LABELS = (
    "certification number",
    "cert number",
    "certification no",
    "psa cert",
    "certificate number",
    "zertifizierungsnummer",
    "zertifikat nummer",
    "zertifikatsnummer",
)


def _digits(value: str) -> str | None:
    compact = re.sub(r"\s+", "", value)
    candidates = re.findall(r"(?<!\d)(\d{7,12})(?!\d)", compact)
    return max(candidates, key=len) if candidates else None


def extract_cert_from_aspects(listing: Listing) -> CertCandidate | None:
    for name, values in listing.aspects.items():
        normalized_name = normalize_text(name)
        if any(label in normalized_name for label in CERT_LABELS):
            for value in values:
                cert = _digits(value)
                if cert:
                    return CertCandidate(cert, "Item-Specifics", 1.0)
    return None


def extract_cert_from_title(title: str) -> CertCandidate | None:
    patterns = [
        r"\bcert(?:ification)?\s*(?:#|no\.?|number)?\s*[:#-]?\s*(\d{7,12})\b",
        r"\bpsa\s*cert\s*[:#-]?\s*(\d{7,12})\b",
        r"\bzert(?:ifikat|ifizierung)?\s*(?:#|nr\.?)?\s*[:#-]?\s*(\d{7,12})\b",
    ]
    for pattern in patterns:
        match = re.search(pattern, title, flags=re.I)
        if match:
            return CertCandidate(match.group(1), "Titel", 0.97)
    return None


def grade_from_listing(listing: Listing) -> str | None:
    for name, values in listing.aspects.items():
        normalized_name = normalize_text(name)
        if normalized_name in {
            "grade",
            "card grade",
            "professional grade",
            "professioneller grad",
            "bewertung",
        }:
            return " ".join(values)
    title_n = normalize_text(listing.title)
    if any(x in title_n for x in ("psa 10", "psa10", "gem mt 10", "gem mint 10")):
        return "10"
    return None
