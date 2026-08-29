from __future__ import annotations

import hashlib
import math
import re
from statistics import median

from .fx import FXRates
from .identity import pricing_identity_from_cert, pricing_identity_from_listing
from .models import Listing, MarketValue, Money, PSACertInfo
from .util import has_phrase, normalize_text

GENERIC_TOKENS = {
    "the", "and", "card", "cards", "trading", "game", "edition", "collection",
    "pokemon", "pokémon", "psa", "gem", "mint", "tcg", "japanese", "english",
    "german", "de", "jp", "jap", "en", "graded", "grade",
}
PSA10_MARKERS = ("psa 10", "psa10", "gem mt 10", "gem mint 10")


def cert_fingerprint(cert: PSACertInfo) -> str:
    identity = pricing_identity_from_cert(cert)
    if identity:
        parts = [
            identity.year,
            identity.set_code,
            identity.card_number,
            "+".join(identity.subjects),
            identity.language,
            identity.edition,
            identity.variant,
        ]
    else:
        parts = [cert.year, cert.brand_title, cert.subject, cert.card_number, cert.variety]
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
    aspects = " ".join(str(value) for values in listing.aspects.values() for value in values)
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


def _dimension_guard(listing: Listing, cert: PSACertInfo) -> tuple[bool, int, int]:
    source = pricing_identity_from_cert(cert)
    candidate = pricing_identity_from_listing(listing)
    if source is None or candidate is None:
        return True, 0, 0
    penalty = 0
    bonus = 0
    for left, right in (
        (source.language, candidate.language),
        (source.edition, candidate.edition),
        (source.variant, candidate.variant),
    ):
        if left and right and left != right:
            return False, 0, 0
        if left and not right:
            penalty += 1
        if left and right == left:
            bonus += 1
    if source.year and candidate.year and source.year != candidate.year:
        return False, 0, 0
    if source.set_code and candidate.set_code and source.set_code != candidate.set_code:
        return False, 0, 0
    if source.set_code and not candidate.set_code:
        penalty += 1
    elif source.set_code and candidate.set_code == source.set_code:
        bonus += 2
    return True, bonus, penalty


def _comp_identity_details(listing: Listing, cert: PSACertInfo) -> tuple[int, bool, int]:
    if not _is_psa10_listing(listing):
        return 0, False, 0
    guard_ok, dimension_bonus, penalty = _dimension_guard(listing, cert)
    if not guard_ok:
        return 0, False, 0

    text = _all_text(listing)
    text_n = normalize_text(text)
    score = dimension_bonus
    card_match = _card_number_match(text, cert.card_number)
    if cert.card_number:
        if not card_match:
            return 0, False, 0
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
            variety_match or brand_overlap >= 1 or (subject_match and year_match) or not brand_tokens
        )
    else:
        accepted = subject_match and score >= 4
    return score, accepted, penalty


def comp_identity_score(listing: Listing, cert: PSACertInfo) -> tuple[int, bool]:
    score, accepted, _ = _comp_identity_details(listing, cert)
    return score, accepted


def _seller_key(value: str | None) -> str | None:
    if not value:
        return None
    return hashlib.sha256(normalize_text(value).encode("utf-8")).hexdigest()[:16]


def _annotated_money(money: Money, listing: Listing, score: int, penalty: int) -> Money:
    return Money(
        money.value,
        money.currency,
        source_id=listing.item_id,
        seller_key=_seller_key(listing.seller),
        identity_score=score,
        match_penalty=penalty,
    )


def exact_active_comps(
    rows: list[Listing],
    cert: PSACertInfo,
    *,
    target_currency: str,
    fx: FXRates,
    exclude_item_id: str | None = None,
) -> list[Money]:
    matches: list[Money] = []
    seen_ids: set[str] = set()
    for row in rows:
        if not row.item_id or row.item_id == exclude_item_id or row.item_id in seen_ids:
            continue
        if row.pure_auction:
            continue
        identity_score, accepted, penalty = _comp_identity_details(row, cert)
        if not accepted:
            continue
        total = row.total_cost or row.price
        if not total or total.value <= 0:
            continue
        converted = fx.convert(total, target_currency)
        if not converted:
            continue
        seen_ids.add(row.item_id)
        matches.append(_annotated_money(converted, row, identity_score, penalty))
    matches.sort(key=lambda item: (-(item.identity_score or 0), item.value))
    return matches


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


def _clean_values(values: list[Money]) -> list[Money]:
    if not values:
        return []
    currency = values[0].currency.upper()
    clean = [m for m in values if m.currency.upper() == currency and m.value > 0]
    clean.sort(key=lambda m: m.value)
    if len(clean) < 5:
        return clean
    numbers = [m.value for m in clean]
    q1 = _quartile(numbers, 0.25)
    q3 = _quartile(numbers, 0.75)
    iqr = max(0.0, q3 - q1)
    low = max(0.0, q1 - 1.5 * iqr)
    high = q3 + 1.5 * iqr
    filtered = [m for m in clean if low <= m.value <= high]
    return filtered if len(filtered) >= 3 else clean


def conservative_active_anchor(values: list[Money]) -> Money | None:
    cleaned = _clean_values(values)
    if not cleaned:
        return None
    if len(cleaned) >= 5:
        lower_half = cleaned[: math.ceil(len(cleaned) / 2)]
        anchor_value = median([m.value for m in lower_half])
    else:
        anchor_value = median([m.value for m in cleaned])
    return Money(float(anchor_value), cleaned[0].currency)


def _quality(values: list[Money]) -> tuple[int, float | None, float | None, float | None, int]:
    cleaned = _clean_values(values)
    if not cleaned:
        return 0, None, None, None, 0
    numbers = [m.value for m in cleaned]
    med = median(numbers)
    q1 = _quartile(numbers, 0.25)
    q3 = _quartile(numbers, 0.75)
    dispersion = (q3 - q1) / med if med > 0 else None
    sellers = {m.seller_key for m in cleaned if m.seller_key}
    penalty = max((int(m.match_penalty or 0) for m in cleaned), default=0)
    return len(sellers), min(numbers), max(numbers), dispersion, penalty


def market_value_from_active_comps(
    values: list[Money],
    *,
    medium_required_edge: float = 0.20,
) -> MarketValue | None:
    anchor = conservative_active_anchor(values)
    if not anchor:
        return None
    cleaned = _clean_values(values)
    sample_size = len(cleaned)
    unique_sellers, price_low, price_high, dispersion, match_penalty = _quality(cleaned)
    independent = unique_sellers >= 3
    coherent = dispersion is not None and dispersion <= 0.35
    identity_complete = match_penalty == 0
    confidence = "mittel" if sample_size >= 3 and independent and coherent and identity_complete else "niedrig"
    required_edge = medium_required_edge if confidence == "mittel" else max(0.25, medium_required_edge)
    if dispersion is not None and dispersion > 0.35:
        required_edge = max(required_edge, 0.30)
    return MarketValue(
        anchor,
        "eBay aktive PSA-10-Vergleichsangebote",
        confidence,
        sample_size,
        market_type="ebay_active",
        required_edge=required_edge,
        unique_sellers=unique_sellers,
        price_low=price_low,
        price_high=price_high,
        dispersion=dispersion,
    )


def find_leave_one_out_deal(
    values: list[Money],
    *,
    min_edge: float = 0.25,
) -> tuple[str, MarketValue, float] | None:
    candidates = [m for m in values if m.source_id and m.value > 0]
    if len(candidates) < 4:
        return None
    cheapest = min(candidates, key=lambda m: m.value)
    others = [m for m in candidates if m.source_id != cheapest.source_id]
    market = market_value_from_active_comps(others)
    if market is None or market.confidence != "mittel" or market.money.value <= 0:
        return None
    edge = 1.0 - cheapest.value / market.money.value
    if edge < max(min_edge, market.required_edge):
        return None
    return str(cheapest.source_id), market, edge
