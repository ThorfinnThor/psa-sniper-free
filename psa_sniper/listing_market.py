from __future__ import annotations

import re
from dataclasses import dataclass

from .fx import FXRates
from .market import PSA10_MARKERS, conservative_active_anchor
from .models import Listing, MarketValue, Money
from .util import normalize_text

_GENERIC = {
    "the", "and", "card", "cards", "trading", "game", "edition", "collection",
    "pokemon", "pokémon", "psa", "gem", "mint", "gemmt", "tcg", "graded", "grade",
    "original", "rare", "holo", "foil", "cardgame", "karte", "karten", "sammelkarte",
    "sammelkarten", "pokemonkarte", "pokemonkarten",
}

_DESCRIPTOR = {
    "art", "rare", "promo", "foil", "holo", "ex", "v", "vmax", "vstar", "gx",
    "mega", "ma", "sar", "sir", "alt", "full", "illustration", "special", "secret",
    "ultra", "refractor", "parallel", "reverse", "shiny", "rainbow",
}

_LANGUAGE = {
    "jp", "jpn", "japanese", "en", "english", "de", "ger", "german", "deutsch",
    "kor", "korean", "kr",
}

_SUBJECT_KEYS = (
    "subject", "player", "athlete", "player athlete", "character", "card name",
    "name", "spieler", "athlet", "charakter", "kartenname",
)
_CARD_NUMBER_KEYS = (
    "card number", "card no", "card nr", "cardnumber", "kartennummer", "karten nummer",
    "kartennr", "nummer",
)


@dataclass(frozen=True, slots=True)
class ListingCompIdentity:
    card_number: str
    terms: tuple[str, ...]
    year: str | None = None


def _all_text(listing: Listing) -> str:
    aspects = " ".join(
        str(value)
        for values in listing.aspects.values()
        for value in values
    )
    return f"{listing.title} {aspects}".strip()


def _psa10(listing: Listing) -> bool:
    text = normalize_text(_all_text(listing))
    wrong = re.search(r"\bpsa\s*(?:[1-9](?:\.\d)?|10\.\d)\b", text)
    if wrong and "psa 10" not in wrong.group(0):
        return False
    return any(marker in text for marker in PSA10_MARKERS)


def _aspect_value(listing: Listing, wanted: tuple[str, ...]) -> str | None:
    wanted_set = {normalize_text(x) for x in wanted}
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
    # Prefer a card-like token directly before PSA 10, optionally followed by a
    # language marker. This avoids treating magazine/issue numbers such as
    # "WSJ #36-37 043 JP PSA 10" as the actual card number.
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
    if re.fullmatch(r"(?:19|20)\d{2}", value):
        return None
    return value


def _card_number_from_title(title: str) -> str | None:
    near_psa = _card_number_near_psa(title)
    if near_psa:
        return near_psa

    # Strong title form: explicit #165 / #TG12 / #165/182.
    match = re.search(r"(?<![a-z0-9])#\s*([a-z0-9]+(?:[\-_/][a-z0-9]+)?)", title, re.IGNORECASE)
    if match:
        return normalize_text(match.group(1))

    # Common seller form: "... 165 PSA 10". Reject year-like values.
    match = re.search(
        r"(?<![a-z0-9])([a-z]{0,3}\d{1,4}[a-z]?(?:[\-_/][a-z0-9]+)?)\s+psa\s*10\b",
        title,
        re.IGNORECASE,
    )
    if not match:
        return None
    value = normalize_text(match.group(1))
    if re.fullmatch(r"(?:19|20)\d{2}", value):
        return None
    return value


def _tokens(value: str) -> list[str]:
    return re.findall(r"[a-z0-9]+", normalize_text(value))


def _subject_terms(value: str, *, exclude: set[str]) -> list[str]:
    result: list[str] = []
    for token in _tokens(value):
        if token in exclude or token in _GENERIC or token in _DESCRIPTOR or token in _LANGUAGE:
            continue
        if re.fullmatch(r"(?:19|20)\d{2}", token):
            continue
        if any(char.isdigit() for char in token):
            continue
        if len(token) < 3:
            continue
        if token not in result:
            result.append(token)
    return result


def _qualifier_terms(value: str, *, exclude: set[str]) -> list[str]:
    raw = _tokens(value)
    result: list[str] = []

    # Set/product codes such as SV2A or M2A are excellent disambiguators.
    for token in raw:
        if token in exclude or token in _GENERIC or token in _LANGUAGE:
            continue
        if re.fullmatch(r"(?:19|20)\d{2}", token):
            continue
        if any(char.isdigit() for char in token) and not token.isdigit():
            if token not in result:
                result.append(token)

    # Then keep meaningful set/brand words. Descriptors are deliberately skipped.
    for token in raw:
        if token in exclude or token in _GENERIC or token in _DESCRIPTOR or token in _LANGUAGE:
            continue
        if re.fullmatch(r"(?:19|20)\d{2}", token) or token.isdigit() or len(token) < 3:
            continue
        if token not in result:
            result.append(token)
    return result


def listing_comp_identity(listing: Listing) -> ListingCompIdentity | None:
    """Build a conservative identity from the eBay listing itself.

    A card number is mandatory. Query terms prioritize a subject/character and
    then a set/product-code discriminator, so long localized titles do not waste
    the search on generic words such as "Karte" or "Art Rare". The resulting
    market value remains low confidence because PSA identity is not confirmed.
    """
    if not _psa10(listing):
        return None

    card_number = _normalize_card_number(_aspect_value(listing, _CARD_NUMBER_KEYS))
    if not card_number:
        card_number = _card_number_from_title(listing.title)
    if not card_number:
        return None

    card_parts = set(_tokens(card_number)) | {card_number}
    subject = _aspect_value(listing, _SUBJECT_KEYS)
    subject_terms = _subject_terms(subject or "", exclude=card_parts) if subject else []
    title_subjects = _subject_terms(listing.title, exclude=card_parts)
    if not subject_terms:
        subject_terms = title_subjects[:2]

    if not subject_terms:
        return None

    used = set(subject_terms) | card_parts
    qualifiers = _qualifier_terms(listing.title, exclude=used)

    terms: list[str] = []
    for token in [*subject_terms[:2], *qualifiers]:
        if token not in terms:
            terms.append(token)
        if len(terms) >= 3:
            break

    year_match = re.search(r"\b((?:19|20)\d{2})\b", _all_text(listing))
    return ListingCompIdentity(
        card_number=card_number,
        terms=tuple(terms),
        year=year_match.group(1) if year_match else None,
    )


def listing_comp_fingerprint(identity: ListingCompIdentity) -> str:
    return "listing|" + "|".join(
        [identity.year or "", identity.card_number, *identity.terms]
    )


def build_listing_comp_queries(identity: ListingCompIdentity) -> list[str]:
    """Return a precise query plus one controlled fallback query."""
    queries: list[str] = []
    primary_terms = list(identity.terms[:2])
    if primary_terms:
        queries.append(" ".join([*primary_terms, identity.card_number, "PSA 10"]))
    if len(primary_terms) >= 2:
        queries.append(" ".join([primary_terms[0], identity.card_number, "PSA 10"]))
    return list(dict.fromkeys(queries))


def build_listing_comp_query(identity: ListingCompIdentity) -> str:
    """Compatibility helper returning the most precise query."""
    queries = build_listing_comp_queries(identity)
    return queries[0] if queries else ""


def _card_number_match(text: str, card_number: str) -> bool:
    parts = re.findall(r"[a-z]+|\d+", normalize_text(card_number))
    if not parts:
        return False
    flexible = r"[\s#\-_/.:]*".join(re.escape(part) for part in parts)
    return bool(re.search(rf"(?<![a-z0-9])#?{flexible}(?![a-z0-9])", normalize_text(text)))


def listing_comp_identity_score(
    listing: Listing,
    identity: ListingCompIdentity,
) -> tuple[int, bool]:
    if not _psa10(listing):
        return 0, False
    text = _all_text(listing)
    if not _card_number_match(text, identity.card_number):
        return 0, False

    tokens = set(_tokens(text))
    overlap = sum(1 for term in identity.terms if term in tokens)
    required_overlap = 1 if len(identity.terms) == 1 else 2
    if overlap < required_overlap:
        return 0, False

    score = 4 + overlap * 2
    years = set(re.findall(r"\b(?:19|20)\d{2}\b", text))
    if identity.year:
        if years and identity.year not in years:
            return 0, False
        if identity.year in years:
            score += 1
    return score, True


def exact_active_comps_for_listing(
    rows: list[Listing],
    identity: ListingCompIdentity,
    *,
    target_currency: str,
    fx: FXRates,
    exclude_item_id: str | None = None,
) -> list[Money]:
    matches: list[tuple[int, Money]] = []
    seen: set[str] = set()
    for row in rows:
        if not row.item_id or row.item_id == exclude_item_id or row.item_id in seen:
            continue
        if row.pure_auction:
            continue
        score, accepted = listing_comp_identity_score(row, identity)
        if not accepted:
            continue
        total = row.total_cost or row.price
        if not total or total.value <= 0:
            continue
        converted = fx.convert(total, target_currency)
        if not converted:
            continue
        seen.add(row.item_id)
        matches.append((score, converted))
    matches.sort(key=lambda pair: (-pair[0], pair[1].value))
    return [money for _, money in matches]


def market_value_from_listing_comps(
    values: list[Money],
    *,
    required_edge: float = 0.25,
) -> MarketValue | None:
    anchor = conservative_active_anchor(values)
    if not anchor:
        return None
    return MarketValue(
        anchor,
        "eBay aktive PSA-10-Comps (Listing-Identität)",
        "niedrig",
        len(values),
        market_type="ebay_active_provisional",
        required_edge=max(0.25, required_edge),
    )
