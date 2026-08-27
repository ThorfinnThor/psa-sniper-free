from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any


@dataclass(slots=True)
class Money:
    value: float
    currency: str

    def to_dict(self) -> dict[str, Any]:
        return {"value": round(float(self.value), 2), "currency": self.currency.upper()}


@dataclass(slots=True)
class CertCandidate:
    number: str
    source: str
    confidence: float


@dataclass(slots=True)
class Listing:
    item_id: str
    title: str
    url: str
    price: Money | None
    created_at: datetime | None
    end_at: datetime | None = None
    shipping: Money | None = None
    image_urls: list[str] = field(default_factory=list)
    aspects: dict[str, list[str]] = field(default_factory=dict)
    seller: str | None = None
    seller_feedback_percentage: float | None = None
    seller_feedback_score: int | None = None
    buying_options: list[str] = field(default_factory=list)
    condition: str | None = None
    returns_accepted: bool | None = None
    item_location_country: str | None = None
    matched_queries: list[str] = field(default_factory=list)
    raw: dict[str, Any] = field(default_factory=dict)

    @property
    def pure_auction(self) -> bool:
        options = {x.upper() for x in self.buying_options}
        return "AUCTION" in options and "FIXED_PRICE" not in options

    @property
    def total_cost(self) -> Money | None:
        if not self.price:
            return None
        if self.shipping and self.shipping.currency.upper() == self.price.currency.upper():
            return Money(self.price.value + self.shipping.value, self.price.currency)
        return self.price


@dataclass(slots=True)
class PSACertInfo:
    cert_number: str
    valid: bool = False
    grade: str | None = None
    year: str | None = None
    brand_title: str | None = None
    subject: str | None = None
    card_number: str | None = None
    category: str | None = None
    variety: str | None = None
    population: int | None = None
    population_higher: int | None = None
    estimate: Money | None = None
    recent_sales: list[Money] = field(default_factory=list)
    source_url: str | None = None
    data_source: str | None = None


@dataclass(slots=True)
class MarketValue:
    money: Money
    source: str
    confidence: str
    sample_size: int = 0


@dataclass(slots=True)
class ScoredHit:
    listing: Listing
    score: int
    reasons: list[str]
    warnings: list[str] = field(default_factory=list)
    cert: PSACertInfo | None = None
    cert_number: str | None = None
    cert_source: str | None = None
    cert_confidence: float | None = None
    cert_trusted: bool = True
    market_value: MarketValue | None = None
    discount_pct: float | None = None


@dataclass(slots=True)
class RunStats:
    started_at: str
    completed_at: str
    queries_used: int
    listings_seen: int
    fresh_listings: int
    detailed_candidates: int
    psa_lookups: int
    hits: int
    near_hits: int
    ebay_calls: int
    notes: list[str] = field(default_factory=list)
