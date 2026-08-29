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
from .market import conservative_active_anchor
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


def market_value_from_listing_comps(
    values: list[Money],
    *,
    required_edge: float = 0.25,
) -> MarketValue | None:
    anchor = conservative_active_anchor(values)
    if not anchor:
        return None
    clean_values = [m for m in values if m.currency.upper() == anchor.currency.upper() and m.value > 0]
    sellers = {m.seller_key for m in clean_values if m.seller_key}
    numbers = sorted(m.value for m in clean_values)
    med = numbers[len(numbers) // 2] if numbers else 0.0
    spread = ((numbers[-1] - numbers[0]) / med) if len(numbers) >= 2 and med > 0 else 0.0
    return MarketValue(
        anchor,
        "eBay aktive PSA-10-Comps (Listing-Identität)",
        "niedrig",
        len(clean_values),
        market_type="ebay_active_provisional",
        required_edge=max(0.25, required_edge),
        unique_sellers=len(sellers),
        price_low=min(numbers) if numbers else None,
        price_high=max(numbers) if numbers else None,
        dispersion=spread,
    )
