from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

from .models import Listing, PSACertInfo
from .util import normalize_text

_GENERIC = {
    "the", "and", "one", "piece", "card", "cards", "trading", "game", "edition", "collection",
    "pokemon", "pokémon", "psa", "gem", "mint", "gemmt", "tcg", "graded", "grade",
    "original", "rare", "holo", "foil", "cardgame", "karte", "karten", "sammelkarte",
    "sammelkarten", "pokemonkarte", "pokemonkarten", "topps", "panini",
}
_DESCRIPTOR = {
    "art", "rare", "promo", "foil", "holo", "ex", "v", "vmax", "vstar", "gx",
    "mega", "ma", "sar", "sir", "alt", "full", "illustration", "special", "secret",
    "ultra", "refractor", "parallel", "reverse", "shiny", "rainbow", "shadowless",
}
_LANGUAGE_TOKENS = {
    "JP": {"jp", "jpn", "japanese", "japanisch"},
    "EN": {"en", "eng", "english", "englisch"},
    "DE": {"de", "ger", "german", "deutsch"},
    "KR": {"kr", "kor", "korean", "koreanisch"},
    "FR": {"fr", "french", "franzosisch", "franzoesisch"},
    "IT": {"it", "italian", "italienisch"},
    "ES": {"es", "spanish", "spanisch"},
    "CN": {"cn", "chinese", "chinesisch"},
}
_SUBJECT_KEYS = (
    "subject", "player", "athlete", "player athlete", "character", "card name", "name",
    "spieler", "athlet", "charakter", "kartenname",
)
_CARD_NUMBER_KEYS = (
    "card number", "card no", "card nr", "cardnumber", "kartennummer", "karten nummer",
    "kartennr", "nummer",
)
_LANGUAGE_KEYS = ("language", "sprache", "card language", "kartensprache")
_SET_KEYS = ("set", "set name", "set code", "serie", "series")


@dataclass(frozen=True, slots=True)
class PricingIdentity:
    card_number: str
    subjects: tuple[str, ...]
    terms: tuple[str, ...]
    year: str | None = None
    set_code: str | None = None
    language: str | None = None
    edition: str | None = None
    variant: str | None = None


def _tokens(value: str) -> list[str]:
    return re.findall(r"[a-z0-9]+", normalize_text(value))


def _all_text(listing: Listing) -> str:
    aspects = " ".join(str(value) for values in listing.aspects.values() for value in values)
    return f"{listing.title} {aspects}".strip()


def _aspect_value(listing: Listing, wanted: tuple[str, ...]) -> str | None:
    wanted_set = {normalize_text(x).replace("/", " ") for x in wanted}
    for key, values in listing.aspects.items():
        key_n = normalize_text(key).replace("/", " ")
        if key_n not in wanted_set:
            continue
        for value in values:
            text = str(value).strip()
            if text:
                return text
    return None


def _normalize_card_number(value: str | None) -> str | None:
    if not value:
        return None
    text = normalize_text(value).strip().lstrip("#")
    match = re.search(r"[a-z0-9]+(?:[\-_/][a-z0-9]+)?", text)
    return match.group(0) if match else None


def _card_number_near_psa(title: str) -> str | None:
    match = re.search(
        r"(?<![a-z0-9])([a-z]{0,4}\d{1,4}[a-z]?(?:[\-_/][a-z0-9]+)?)"
        r"\s+(?:(?:jp|jpn|en|de|ger|kor|kr|korean|japanese|english|german|deutsch)\s+)?"
        r"psa\s*10\b",
        title,
        re.IGNORECASE,
    )
    if not match:
        return None
    value = normalize_text(match.group(1))
    return None if re.fullmatch(r"(?:19|20)\d{2}", value) else value


def card_number_from_title(title: str) -> str | None:
    near = _card_number_near_psa(title)
    if near:
        return near
    match = re.search(r"(?<![a-z0-9])#\s*([a-z0-9]+(?:[\-_/][a-z0-9]+)?)", title, re.I)
    if match:
        return normalize_text(match.group(1))
    match = re.search(
        r"(?<![a-z0-9])([a-z]{0,3}\d{1,4}[a-z]?(?:[\-_/][a-z0-9]+)?)\s+psa\s*10\b",
        title,
        re.I,
    )
    if not match:
        return None
    value = normalize_text(match.group(1))
    return None if re.fullmatch(r"(?:19|20)\d{2}", value) else value


def normalize_language(value: str | None) -> str | None:
    if not value:
        return None
    tokens = set(_tokens(value))
    for code, aliases in _LANGUAGE_TOKENS.items():
        if tokens & aliases:
            return code
    return None


def language_from_listing(listing: Listing) -> str | None:
    aspect = _aspect_value(listing, _LANGUAGE_KEYS)
    return normalize_language(aspect) or normalize_language(_all_text(listing))


def edition_from_text(value: str | None) -> str | None:
    text = normalize_text(value or "")
    if re.search(r"\b(?:1st|first|1)\s*(?:edition|ed)\b", text):
        return "1ST"
    if "shadowless" in text:
        return "SHADOWLESS"
    if "unlimited" in text:
        return "UNLIMITED"
    return None


def variant_from_text(value: str | None) -> str | None:
    text = normalize_text(value or "")
    patterns = (
        ("SPECIAL_ILLUSTRATION_RARE", ("special illustration rare", " sir ")),
        ("ILLUSTRATION_RARE", ("illustration rare", " art rare ")),
        ("REVERSE_HOLO", ("reverse holo", "reverse foil")),
        ("REFRACTOR", ("refractor",)),
        ("PARALLEL", ("parallel",)),
        ("PROMO", ("promo",)),
        ("ALT_ART", ("alt art", "alternative art")),
        ("FULL_ART", ("full art",)),
        ("RAINBOW", ("rainbow",)),
        ("SHINY", ("shiny",)),
        ("HOLO", (" holo", "holo ")),
    )
    padded = f" {text} "
    for canonical, needles in patterns:
        if any(needle in padded for needle in needles):
            return canonical
    return None


def _subject_terms(value: str, *, exclude: set[str]) -> list[str]:
    result: list[str] = []
    for token in _tokens(value):
        if token in exclude or token in _GENERIC or token in _DESCRIPTOR:
            continue
        if any(token in aliases for aliases in _LANGUAGE_TOKENS.values()):
            continue
        if re.fullmatch(r"(?:19|20)\d{2}", token) or any(ch.isdigit() for ch in token):
            continue
        if len(token) < 3:
            continue
        if token not in result:
            result.append(token)
    return result


def _set_code(value: str, *, exclude: set[str]) -> str | None:
    for token in _tokens(value):
        if token in exclude or token in _GENERIC:
            continue
        if re.fullmatch(r"(?:19|20)\d{2}", token):
            continue
        if any(ch.isalpha() for ch in token) and any(ch.isdigit() for ch in token) and 2 <= len(token) <= 10:
            return token.upper()
    return None


def _psa10(listing: Listing) -> bool:
    text = normalize_text(_all_text(listing))
    wrong = re.search(r"\bpsa\s*(?:[1-9](?:\.\d)?|10\.\d)\b", text)
    if wrong and "psa 10" not in wrong.group(0):
        return False
    return any(marker in text for marker in ("psa 10", "psa10", "gem mt 10", "gem mint 10"))


def pricing_identity_from_listing(
    listing: Listing,
    cert: PSACertInfo | None = None,
) -> PricingIdentity | None:
    if not _psa10(listing):
        return None
    card_number = _normalize_card_number(cert.card_number if cert else None)
    if not card_number:
        card_number = _normalize_card_number(_aspect_value(listing, _CARD_NUMBER_KEYS))
    if not card_number:
        card_number = card_number_from_title(listing.title)
    if not card_number:
        return None

    card_parts = set(_tokens(card_number)) | {card_number}
    subject_value = cert.subject if cert and cert.subject else _aspect_value(listing, _SUBJECT_KEYS)
    subjects = _subject_terms(subject_value or "", exclude=card_parts)
    if not subjects:
        subjects = _subject_terms(listing.title, exclude=card_parts)
    if not subjects:
        return None

    all_text = _all_text(listing)
    year = None
    if cert and cert.year:
        match = re.search(r"(?:19|20)\d{2}", cert.year)
        year = match.group(0) if match else None
    if not year:
        match = re.search(r"\b((?:19|20)\d{2})\b", all_text)
        year = match.group(1) if match else None

    language = normalize_language(
        " ".join(x for x in ((cert.brand_title if cert else None), (cert.variety if cert else None)) if x)
    ) or language_from_listing(listing)
    edition = edition_from_text(" ".join(x for x in ((cert.variety if cert else None), all_text) if x))
    variant = variant_from_text(" ".join(x for x in ((cert.variety if cert else None), all_text) if x))
    set_code = _set_code(all_text, exclude=card_parts | set(subjects))

    terms: list[str] = []
    for token in [*subjects[:2], *( [set_code.lower()] if set_code else [])]:
        if token and token not in terms:
            terms.append(token)
        if len(terms) >= 3:
            break
    return PricingIdentity(
        card_number=card_number,
        subjects=tuple(subjects[:2]),
        terms=tuple(terms),
        year=year,
        set_code=set_code,
        language=language,
        edition=edition,
        variant=variant,
    )


def pricing_identity_from_cert(cert: PSACertInfo) -> PricingIdentity | None:
    if not cert.card_number or not cert.subject:
        return None
    fake = Listing(
        item_id="cert",
        title=" ".join(x for x in (cert.year, cert.brand_title, cert.subject, cert.card_number, cert.variety, "PSA 10") if x),
        url="",
        price=None,
        created_at=None,
    )
    return pricing_identity_from_listing(fake, cert)


def pricing_identity_to_dict(identity: PricingIdentity | None) -> dict[str, Any] | None:
    if identity is None:
        return None
    return {
        "version": 2,
        "card_number": identity.card_number,
        "subjects": list(identity.subjects),
        "terms": list(identity.terms),
        "year": identity.year,
        "set_code": identity.set_code,
        "language": identity.language,
        "edition": identity.edition,
        "variant": identity.variant,
    }


def pricing_identity_from_dict(data: Any) -> PricingIdentity | None:
    if not isinstance(data, dict) or not data.get("card_number"):
        return None
    subjects = tuple(str(x) for x in data.get("subjects", []) if x)
    terms = tuple(str(x) for x in data.get("terms", []) if x)
    if not subjects:
        subjects = terms[:2]
    if not subjects:
        return None
    return PricingIdentity(
        card_number=str(data["card_number"]),
        subjects=subjects,
        terms=terms or subjects,
        year=str(data["year"]) if data.get("year") else None,
        set_code=str(data["set_code"]) if data.get("set_code") else None,
        language=str(data["language"]) if data.get("language") else None,
        edition=str(data["edition"]) if data.get("edition") else None,
        variant=str(data["variant"]) if data.get("variant") else None,
    )


def pricing_identity_fingerprint(identity: PricingIdentity) -> str:
    parts = [
        identity.year or "",
        identity.set_code or "",
        identity.card_number,
        "+".join(identity.subjects),
        identity.language or "",
        identity.edition or "",
        identity.variant or "",
    ]
    return "identity|" + "|".join(normalize_text(x) for x in parts)


def build_identity_queries(identity: PricingIdentity) -> list[str]:
    subject = " ".join(identity.subjects[:2])
    precise: list[str] = [subject]
    if identity.set_code:
        precise.append(identity.set_code)
    precise.extend([identity.card_number, "PSA 10"])
    queries = [" ".join(x for x in precise if x)]
    if identity.set_code:
        queries.append(" ".join([subject, identity.card_number, "PSA 10"]))
    if len(identity.subjects) >= 2:
        queries.append(" ".join([identity.subjects[0], identity.card_number, "PSA 10"]))
    return list(dict.fromkeys(q.strip() for q in queries if q.strip()))


def identity_match(
    listing: Listing,
    identity: PricingIdentity,
) -> tuple[int, bool, int]:
    candidate = pricing_identity_from_listing(listing)
    if candidate is None or normalize_text(candidate.card_number) != normalize_text(identity.card_number):
        return 0, False, 0

    # Explicit conflicts are hard rejects. Missing information is allowed but
    # carried as a confidence penalty for the market anchor.
    penalty = 0
    for source, other in (
        (identity.language, candidate.language),
        (identity.edition, candidate.edition),
        (identity.variant, candidate.variant),
    ):
        if source and other and source != other:
            return 0, False, 0
        if source and not other:
            penalty += 1
    if identity.year and candidate.year and identity.year != candidate.year:
        return 0, False, 0
    if identity.set_code and candidate.set_code and identity.set_code != candidate.set_code:
        return 0, False, 0

    source_subjects = set(identity.subjects)
    candidate_tokens = set(_tokens(_all_text(listing)))
    overlap = sum(1 for term in source_subjects if normalize_text(term) in candidate_tokens)
    if overlap < 1:
        return 0, False, 0

    score = 4 + overlap * 2
    if identity.set_code and candidate.set_code == identity.set_code:
        score += 2
    elif identity.set_code and not candidate.set_code:
        penalty += 1
    if identity.year and candidate.year == identity.year:
        score += 1
    if identity.language and candidate.language == identity.language:
        score += 1
    if identity.variant and candidate.variant == identity.variant:
        score += 1
    if identity.edition and candidate.edition == identity.edition:
        score += 1
    return score, True, penalty
