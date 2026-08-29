from __future__ import annotations

import json
from dataclasses import replace
from typing import Any

from .config import load_settings
from .ebay import EbayBudgetExceeded, EbayClient, EbayError
from .models import Listing, ScoredHit
from .scoring import score_hit
from .util import utc_now


def listing_available(listing: Listing) -> bool:
    if listing.end_at and listing.end_at <= utc_now():
        return False
    raw = listing.raw or {}
    text = json.dumps(raw, ensure_ascii=False).upper()
    unavailable_markers = (
        '"ESTIMATEDAVAILABILITYSTATUS":"OUT_OF_STOCK"',
        '"ESTIMATEDAVAILABILITYSTATUS":"UNAVAILABLE"',
        '"AVAILABILITYSTATUS":"OUT_OF_STOCK"',
        '"AVAILABILITYSTATUS":"UNAVAILABLE"',
    )
    return not any(marker in text.replace(" ", "") for marker in unavailable_markers)


def merge_live_listing(stored: Listing, live: Listing) -> Listing:
    return replace(
        stored,
        title=live.title or stored.title,
        url=live.url or stored.url,
        price=live.price or stored.price,
        shipping=live.shipping if live.shipping is not None else stored.shipping,
        created_at=live.created_at or stored.created_at,
        end_at=live.end_at or stored.end_at,
        image_urls=live.image_urls or stored.image_urls,
        buying_options=live.buying_options or stored.buying_options,
        condition=live.condition or stored.condition,
        returns_accepted=(
            live.returns_accepted if live.returns_accepted is not None else stored.returns_accepted
        ),
        seller_feedback_percentage=(
            live.seller_feedback_percentage
            if live.seller_feedback_percentage is not None
            else stored.seller_feedback_percentage
        ),
        seller_feedback_score=(
            live.seller_feedback_score
            if live.seller_feedback_score is not None
            else stored.seller_feedback_score
        ),
        raw=live.raw or stored.raw,
    )


def refresh_hit_for_purchase(
    hit: ScoredHit,
    ebay: EbayClient,
    settings: dict[str, Any] | None = None,
) -> tuple[ScoredHit | None, str]:
    settings = settings or load_settings()
    try:
        live = ebay.get_item(hit.listing.item_id, compact=True)
    except EbayBudgetExceeded:
        return None, "budget"
    except EbayError as exc:
        if exc.missing:
            return None, "ended"
        return None, "check_failed"
    if not listing_available(live):
        return None, "ended"
    listing = merge_live_listing(hit.listing, live)
    refreshed = score_hit(
        listing,
        cert_number=hit.cert_number,
        cert_source=hit.cert_source,
        cert_confidence=hit.cert_confidence,
        cert=hit.cert,
        market_value_listing_currency=hit.market_value,
        priority_terms=list(settings.get("priority_terms") or []),
        demand_terms=list(settings.get("demand_terms") or []),
        import_risk_extra_edge=float(settings.get("import_risk_extra_edge", 0.0)),
        import_exempt_countries=list(settings.get("import_risk_exempt_countries") or []),
        unknown_shipping_extra_edge=float(settings.get("unknown_shipping_extra_edge", 0.0)),
    )
    threshold = int(settings.get("hit_threshold", 11))
    if refreshed.score < threshold or refreshed.price_status != "verified_edge":
        return refreshed, "no_longer_hit"
    return refreshed, "active"
