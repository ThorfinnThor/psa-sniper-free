from __future__ import annotations

import hashlib
import json
import os
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from statistics import median
from typing import Any
from urllib.parse import urlparse

from .config import ROOT
from .fx import FXRates
from .identity import PricingIdentity, identity_match, pricing_identity_from_listing
from .market import clean_active_comp_values
from .models import Listing, MarketValue, Money
from .util import normalize_text, parse_iso_datetime, utc_now

POINT130_SEARCH_URL = "https://130point.com/search?new=sold"


@dataclass(frozen=True, slots=True)
class Point130Sale:
    sale_id: str
    title: str
    price: Money
    sold_at: datetime
    source_url: str = POINT130_SEARCH_URL
    marketplace: str = "eBay"

    def as_listing(self) -> Listing:
        return Listing(
            item_id=f"130point:{self.sale_id}",
            title=self.title,
            url=self.source_url,
            price=self.price,
            created_at=self.sold_at,
            buying_options=["SOLD"],
            raw={"source": "130point", "marketplace": self.marketplace},
        )


def _point130_url(value: Any) -> str:
    url = str(value or POINT130_SEARCH_URL).strip()
    parsed = urlparse(url)
    if parsed.scheme != "https" or (parsed.hostname or "").casefold() not in {
        "130point.com",
        "www.130point.com",
    }:
        raise ValueError("130point source_url muss auf https://130point.com zeigen")
    return url


def _sale_id(row: dict[str, Any], *, title: str, sold_at: datetime, money: Money) -> str:
    explicit = str(row.get("id") or row.get("sale_id") or "").strip()
    if explicit:
        return explicit
    payload = f"{title}|{sold_at.isoformat()}|{money.value:.8f}|{money.currency.upper()}"
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:20]


def parse_point130_sales(data: Any) -> list[Point130Sale]:
    if not isinstance(data, dict):
        raise ValueError("130point-Daten müssen ein JSON-Objekt sein")
    rows = data.get("sales", [])
    if not isinstance(rows, list):
        raise ValueError("130point-Feld 'sales' muss eine Liste sein")

    sales: list[Point130Sale] = []
    seen: set[str] = set()
    for index, row in enumerate(rows, start=1):
        if not isinstance(row, dict):
            raise ValueError(f"130point-Verkauf {index} muss ein Objekt sein")
        title = str(row.get("title") or "").strip()
        if not title:
            raise ValueError(f"130point-Verkauf {index}: title fehlt")
        price = row.get("price")
        if not isinstance(price, dict):
            raise ValueError(f"130point-Verkauf {index}: price fehlt")
        try:
            money = Money(float(price["value"]), str(price["currency"]).upper())
        except (KeyError, TypeError, ValueError) as exc:
            raise ValueError(f"130point-Verkauf {index}: price ist ungültig") from exc
        if money.value <= 0 or len(money.currency) != 3:
            raise ValueError(f"130point-Verkauf {index}: positiver Preis und ISO-Währung nötig")
        sold_at = parse_iso_datetime(str(row.get("sold_at") or ""))
        if sold_at is None:
            raise ValueError(f"130point-Verkauf {index}: sold_at ist ungültig")
        source_url = _point130_url(row.get("source_url"))
        sale_id = _sale_id(row, title=title, sold_at=sold_at, money=money)
        if sale_id in seen:
            continue
        seen.add(sale_id)
        sales.append(
            Point130Sale(
                sale_id=sale_id,
                title=title,
                price=money,
                sold_at=sold_at,
                source_url=source_url,
                marketplace=str(row.get("marketplace") or "eBay").strip() or "eBay",
            )
        )
    return sales


def load_point130_sales(path: Path | None = None) -> list[Point130Sale]:
    raw = os.getenv("POINT130_SOLD_COMPS_JSON", "").strip()
    if raw:
        return parse_point130_sales(json.loads(raw))
    source = path or ROOT / "config" / "130point_sold_comps.json"
    if not source.exists():
        return []
    return parse_point130_sales(json.loads(source.read_text(encoding="utf-8")))


def _strict_identity_match(sale: Point130Sale, identity: PricingIdentity) -> tuple[int, bool]:
    listing = sale.as_listing()
    score, accepted, penalty = identity_match(listing, identity)
    if not accepted:
        return score, False
    candidate = pricing_identity_from_listing(listing)
    if candidate is None:
        return score, False
    title_tokens = set(normalize_text(sale.title).split())
    if any(normalize_text(subject) not in title_tokens for subject in identity.subjects):
        return score, False
    # Sold-Comps may promote a listing to a buy hit. Subject, language and all
    # variant dimensions therefore have to be explicit. A missing set code is
    # tolerated only for numerator/denominator card numbers (039/100), where
    # number + subject + language remain a strong composite identity.
    for expected, actual in (
        (identity.language, candidate.language),
        (identity.edition, candidate.edition),
        (identity.variant, candidate.variant),
    ):
        if expected and actual != expected:
            return score, False
    if identity.set_code and candidate.set_code != identity.set_code:
        if candidate.set_code or "/" not in identity.card_number:
            return score, False
    # identity_match already rejected explicit conflicts. Remaining penalties
    # can only represent an allowed redundant dimension missing from the title.
    if penalty > 1:
        return score, False
    return score, True


def exact_point130_sales(
    sales: list[Point130Sale],
    identity: PricingIdentity,
    *,
    target_currency: str,
    fx: FXRates,
    max_age_days: int = 365,
    now: datetime | None = None,
) -> list[Money]:
    current = now or utc_now()
    cutoff = current - timedelta(days=max(1, max_age_days))
    result: list[Money] = []
    seen: set[str] = set()
    for sale in sales:
        if sale.sale_id in seen or sale.sold_at < cutoff or sale.sold_at > current + timedelta(days=1):
            continue
        score, accepted = _strict_identity_match(sale, identity)
        if not accepted:
            continue
        converted = fx.convert(sale.price, target_currency)
        if converted is None or converted.value <= 0:
            continue
        seen.add(sale.sale_id)
        result.append(
            Money(
                converted.value,
                converted.currency,
                source_id=f"130point:{sale.sale_id}",
                identity_score=score,
            )
        )
    result.sort(key=lambda value: value.value)
    return result


def market_value_from_point130_sales(
    values: list[Money],
    *,
    required_edge: float = 0.12,
) -> MarketValue | None:
    cleaned = clean_active_comp_values(values)
    if not cleaned:
        return None
    numbers = [value.value for value in cleaned]
    anchor = float(median(numbers))
    price_low = min(numbers)
    price_high = max(numbers)
    dispersion = (price_high - price_low) / anchor if anchor > 0 else None
    sample_size = len(cleaned)
    coherent = dispersion is not None and dispersion <= 0.45
    if sample_size >= 3 and coherent:
        confidence = "hoch"
        edge = max(0.10, required_edge)
    elif sample_size >= 2 and coherent:
        confidence = "mittel"
        edge = max(0.15, required_edge)
    else:
        confidence = "niedrig"
        edge = max(0.25, required_edge)
    if dispersion is not None and dispersion > 0.45:
        edge = max(edge, 0.30)
    return MarketValue(
        Money(anchor, cleaned[0].currency),
        "130point verkaufte PSA-10-Comps",
        confidence,
        sample_size,
        market_type="point130_sold",
        required_edge=edge,
        price_low=price_low,
        price_high=price_high,
        dispersion=dispersion,
    )


def point130_market_for_identity(
    sales: list[Point130Sale],
    identity: PricingIdentity | None,
    *,
    target_currency: str,
    fx: FXRates,
    max_age_days: int = 365,
    required_edge: float = 0.12,
) -> MarketValue | None:
    if identity is None:
        return None
    values = exact_point130_sales(
        sales,
        identity,
        target_currency=target_currency,
        fx=fx,
        max_age_days=max_age_days,
    )
    return market_value_from_point130_sales(values, required_edge=required_edge)
