from __future__ import annotations

import hashlib

from .fx import FXRates
from .identity import (
    PricingIdentity,
    build_identity_queries,
    identity_match,
    pricing_identity_fingerprint,
    pricing_identity_from_listing,
)
from .market import clean_active_comp_values, conservative_active_anchor
from .models import Listing, MarketValue, Money
from .util import normalize_text

ListingCompIdentity = PricingIdentity


def listing_comp_identity(listing: Listing) -> ListingCompIdentity | None:
    return pricing_identity_from_listing(listing)


def listing_comp_fingerprint(identity: ListingCompIdentity) -> str:
    return pricing_identity_fingerprint(identity)


def build_listing_comp_queries(identity: ListingCompIdentity) -> list[str]:
    return build_identity_queries(identity)


def build_listing_comp_query(identity: ListingCompIdentity) -> str:
    queries = build_listing_comp_queries(identity)
    return queries[0] if queries else ""


def listing_comp_identity_score(
    listing: Listing,
    identity: ListingCompIdentity,
) -> tuple[int, bool]:
    score, accepted, _ = identity_match(listing, identity)
    return score, accepted


def _seller_key(value: str | None) -> str | None:
    if not value:
        return None
    return hashlib.sha256(normalize_text(value).encode("utf-8")).hexdigest()[:16]


def exact_active_comps_for_listing(
    rows: list[Listing],
    identity: ListingCompIdentity,
    *,
    target_currency: str,
    fx: FXRates,
    exclude_item_id: str | None = None,
) -> list[Money]:
    matches: list[Money] = []
    seen: set[str] = set()
    for row in rows:
        if not row.item_id or row.item_id == exclude_item_id or row.item_id in seen:
            continue
        if row.pure_auction:
            continue
        score, accepted, penalty = identity_match(row, identity)
        if not accepted:
            continue
        total = row.total_cost or row.price
        if not total or total.value <= 0:
            continue
        converted = fx.convert(total, target_currency)
        if not converted:
            continue
        seen.add(row.item_id)
        matches.append(
            Money(
                converted.value,
                converted.currency,
                source_id=row.item_id,
                seller_key=_seller_key(row.seller),
                identity_score=score,
                match_penalty=penalty,
            )
        )
    matches.sort(key=lambda money: (-(money.identity_score or 0), money.value))
    return matches


def listing_comp_detail_candidates(
    rows: list[Listing],
    identity: ListingCompIdentity,
    *,
    exclude_item_id: str | None = None,
    attempted_item_ids: set[str] | None = None,
) -> list[Listing]:
    """Rank summaries whose full eBay details can improve comp confidence.

    Browse search summaries often omit language, set/variant aspects, or seller
    data. Exact-looking rows with those gaps are accepted conservatively but
    receive a match penalty, which keeps the market confidence low. A small,
    prioritized number of full item reads can fill those dimensions. Rows with
    an explicit conflicting identity are never enriched merely because their
    search query happened to match.
    """
    attempted = attempted_item_ids or set()
    subject_tokens = {normalize_text(value) for value in identity.subjects if value}
    ranked: list[tuple[int, int, int, float, Listing]] = []
    seen: set[str] = set()

    for row in rows:
        if (
            not row.item_id
            or row.item_id == exclude_item_id
            or row.item_id in attempted
            or row.item_id in seen
        ):
            continue
        seen.add(row.item_id)
        score, accepted, penalty = identity_match(row, identity)
        total = row.total_cost or row.price
        price = float(total.value) if total else float("inf")

        if accepted:
            missing_seller = int(not row.seller)
            if penalty <= 0 and not missing_seller:
                continue
            ranked.append((3, int(penalty), missing_seller, -price, row))
            continue

        # A missing card number/aspect in the summary can make identity parsing
        # impossible. Only consider such a row when the visible title still has
        # PSA 10 and the expected subject; explicit conflicts were parsed above
        # and are deliberately rejected.
        if pricing_identity_from_listing(row) is not None:
            continue
        title = normalize_text(row.title)
        title_tokens = set(title.split())
        if not any(marker in title for marker in ("psa 10", "psa10", "gem mt 10", "gem mint 10")):
            continue
        if subject_tokens and not (subject_tokens & title_tokens):
            continue
        ranked.append((1, 0, int(not row.seller), -price, row))

    ranked.sort(key=lambda item: item[:4], reverse=True)
    return [item[-1] for item in ranked]


def market_value_from_listing_comps(
    values: list[Money],
    *,
    required_edge: float = 0.25,
) -> MarketValue | None:
    anchor = conservative_active_anchor(values)
    if not anchor:
        return None
    clean_values = clean_active_comp_values(values)
    sellers = {m.seller_key for m in clean_values if m.seller_key}
    numbers = sorted(m.value for m in clean_values)
    med = numbers[len(numbers) // 2] if numbers else 0.0
    spread = ((numbers[-1] - numbers[0]) / med) if len(numbers) >= 2 and med > 0 else 0.0
    max_penalty = max((int(m.match_penalty or 0) for m in clean_values), default=0)
    min_identity = min((int(m.identity_score or 0) for m in clean_values), default=0)

    sparse_exact = (
        len(clean_values) == 2
        and len(sellers) == 2
        and spread <= 0.18
        and max_penalty == 0
        and min_identity >= 7
    )
    dense_exact = (
        len(clean_values) >= 3
        and len(sellers) >= 3
        and spread <= 0.30
        and max_penalty == 0
        and min_identity >= 6
    )
    confidence = "mittel" if sparse_exact or dense_exact else "niedrig"

    # With only two comps, use the cheaper ask rather than their median. The
    # buyer therefore has to beat both independent exact listings by the gate.
    if sparse_exact:
        anchor = Money(float(numbers[0]), anchor.currency)

    source = (
        "eBay aktive PSA-10-Comps (exakte Listing-Identität)"
        if confidence == "mittel"
        else "eBay aktive PSA-10-Comps (Listing-Identität)"
    )
    return MarketValue(
        anchor,
        source,
        confidence,
        len(clean_values),
        market_type="ebay_active_provisional",
        required_edge=max(0.25, required_edge),
        unique_sellers=len(sellers),
        price_low=min(numbers) if numbers else None,
        price_high=max(numbers) if numbers else None,
        dispersion=spread,
    )
