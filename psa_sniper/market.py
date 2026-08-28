from __future__ import annotations

import math
import re
from statistics import median

from .fx import FXRates
from .models import Listing, MarketValue, Money, PSACertInfo
from .util import has_phrase, normalize_text

GENERIC_TOKENS = {
    "the", "and", "card", "cards", "trading", "game", "edition", "collection",
    "pokemon", "pokémon", "psa", "gem", "mint", "tcg", "japanese", "english",
    "german", "de", "jp", "jap", "en",
}

PSA10_MARKERS = ("psa 10", "psa10", "gem mt 10", "gem mint 10")


def cert_fingerprint(cert: PSACertInfo) -> str:
    parts = [
        cert.year,
        cert.brand_title,
        cert.subject,
        cert.card_number,
        cert.variety,
    ]
    return "|".join(normalize_text(value or "") for value in parts)


def build_comp_query(cert: PSACertInfo) -> str:
    parts: list[str] = []
    if cert.subject:
        parts.append(cert.subject)
    if cert.card_number:
        parts.append(cert.card_number)
    if cert.year and len(parts) < 2:
        parts.append(cert.year)
    if not parts and cert.brand_title:
        parts.append(cert.brand_title)
    parts.append("PSA 10")
    return " ".join(parts)


def build_fallback_comp_query(cert: PSACertInfo) -> str:
    brand_ordered = [
        token
        for token in normalize_text(cert.brand_title or "").split()
        if token not in GENERIC_TOKENS and (len(token) >= 3 or token.isdigit())
    ]
    parts = brand_ordered[-3:]
    if cert.card_number:
        parts.append(cert.card_number)
    if cert.year and not parts:
        parts.append(cert.year)
    parts.append("PSA 10")
    return " ".join(dict.fromkeys(parts))


def _all_text(listing: Listing) -> str:
    aspects = " ".join(
        str(value)
        for values in listing.aspects.values()
        for value in values
    )
    return f"{listing.title} {aspects}".strip()


def _is_psa10_listing(listing: Listing) -> bool:
    text = normalize_text(_all_text(listing))
    negative = re.search(r"\bpsa\s*(?:[1-9](?:\.\d)?|10\.\d)\b", text)
    if negative and "psa 10" not in negative.group(0):
        return False
    return any(marker in text for marker in PSA10_MARKERS)


def _card_number_match(text: str, card_number: str | None) -> bool:
    if not card_number:
        return False
    parts = re.findall(r"[a-z]+|\d+", normalize_text(card_number))
    if not parts:
        return False
    flexible = r"[\s#\-_/.:]*".join(re.escape(part) for part in parts)
    return bool(re.search(rf"(?<![a-z0-9])#?{flexible}(?![a-z0-9])", normalize_text(text)))


def _meaningful_tokens(value: str | None) -> set[str]:
    if not value:
        return set()
    return {
        token
        for token in normalize_text(value).split()
        if token not in GENERIC_TOKENS and (len(token) >= 3 or token.isdigit())
    }


def comp_identity_score(listing: Listing, cert: PSACertInfo) -> tuple[int, bool]:
    if not _is_psa10_listing(listing):
        return 0, False

    text = _all_text(listing)
    text_n = normalize_text(text)
    score = 0

    card_match = _card_number_match(text, cert.card_number)
    if cert.card_number:
        if not card_match:
            return 0, False
        score += 4

    subject_match = bool(cert.subject and has_phrase(text, cert.subject))
    if subject_match:
        score += 3

    variety_match = bool(cert.variety and has_phrase(text, cert.variety))
    if variety_match:
        score += 2

    brand_tokens = _meaningful_tokens(cert.brand_title)
    title_tokens = set(text_n.split())
    brand_overlap = min(2, len(brand_tokens & title_tokens))
    score += brand_overlap

    year_match = bool(cert.year and normalize_text(cert.year) in title_tokens)
    if year_match:
        score += 1

    if cert.card_number:
        accepted = score >= 6 and (
            variety_match
            or brand_overlap >= 1
            or (subject_match and year_match)
            or not brand_tokens
        )
    else:
        accepted = subject_match and score >= 4

    return score, accepted


def exact_active_comps(
    rows: list[Listing],
    cert: PSACertInfo,
    *,
    target_currency: str,
    fx: FXRates,
    exclude_item_id: str | None = None,
) -> list[Money]:
    matches: list[tuple[int, Money]] = []
    seen_ids: set[str] = set()

    for row in rows:
        if not row.item_id or row.item_id == exclude_item_id or row.item_id in seen_ids:
            continue
        if row.pure_auction:
            continue
        identity_score, accepted = comp_identity_score(row, cert)
        if not accepted:
            continue
        total = row.total_cost or row.price
        if not total or total.value <= 0:
            continue
        converted = fx.convert(total, target_currency)
        if not converted:
            continue
        seen_ids.add(row.item_id)
        matches.append((identity_score, converted))

    matches.sort(key=lambda item: (-item[0], item[1].value))
    return [money for _, money in matches]


def _quartile(sorted_values: list[float], fraction: float) -> float:
    if not sorted_values:
        raise ValueError("empty values")
    if len(sorted_values) == 1:
        return sorted_values[0]
    position = (len(sorted_values) - 1) * fraction
    low = math.floor(position)
    high = math.ceil(position)
    if low == high:
        return sorted_values[low]
    weight = position - low
    return sorted_values[low] * (1 - weight) + sorted_values[high] * weight


def conservative_active_anchor(values: list[Money]) -> Money | None:
    if not values:
        return None
    currency = values[0].currency.upper()
    numbers = sorted(
        money.value for money in values
        if money.currency.upper() == currency and money.value > 0
    )
    if not numbers:
        return None

    cleaned = numbers
    if len(numbers) >= 5:
        q1 = _quartile(numbers, 0.25)
        q3 = _quartile(numbers, 0.75)
        iqr = max(0.0, q3 - q1)
        low = max(0.0, q1 - 1.5 * iqr)
        high = q3 + 1.5 * iqr
        filtered = [value for value in numbers if low <= value <= high]
        if len(filtered) >= 3:
            cleaned = filtered

    if len(cleaned) >= 5:
        lower_half = cleaned[: math.ceil(len(cleaned) / 2)]
        anchor_value = median(lower_half)
    else:
        anchor_value = median(cleaned)

    return Money(float(anchor_value), currency)


def market_value_from_active_comps(
    values: list[Money],
    *,
    medium_required_edge: float = 0.20,
) -> MarketValue | None:
    anchor = conservative_active_anchor(values)
    if not anchor:
        return None

    sample_size = len(values)
    confidence = "mittel" if sample_size >= 3 else "niedrig"
    required_edge = medium_required_edge if sample_size >= 3 else max(0.25, medium_required_edge)
    return MarketValue(
        anchor,
        "eBay aktive PSA-10-Vergleichsangebote",
        confidence,
        sample_size,
        market_type="ebay_active",
        required_edge=required_edge,
    )
