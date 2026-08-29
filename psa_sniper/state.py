from __future__ import annotations

import json
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from .identity import pricing_identity_from_listing, pricing_identity_to_dict
from .models import MarketValue, Money, PSACertInfo, RunStats, ScoredHit
from .util import iso_z, parse_iso_datetime, utc_now

SCHEMA_VERSION = 6
PERSONAL_HISTORY_FIELDS = {
    "seller",
    "seller_feedback_percentage",
    "seller_feedback_score",
}


def _scrub_history_row(row: dict[str, Any]) -> dict[str, Any]:
    clean = dict(row)
    for key in PERSONAL_HISTORY_FIELDS:
        clean.pop(key, None)
    return clean


def _scrub_run_row(row: dict[str, Any]) -> dict[str, Any]:
    clean = dict(row)
    results = clean.get("results")
    if isinstance(results, list):
        clean["results"] = [
            _scrub_history_row(item)
            for item in results
            if isinstance(item, dict)
        ]
    repricing_results = clean.get("repricing_results")
    if isinstance(repricing_results, list):
        clean["repricing_results"] = [
            _scrub_history_row(item)
            for item in repricing_results
            if isinstance(item, dict)
        ]
    return clean


def default_state() -> dict[str, Any]:
    return {
        "schema_version": SCHEMA_VERSION,
        "query_cursor": 0,
        "processed": {},
        "alerted": {},
        "cert_cache": {},
        "market_cache": {},
        "history": [],
        "runs": [],
        "updated_at": iso_z(utc_now()),
    }


def load_state(path: Path) -> dict[str, Any]:
    if not path.exists():
        return default_state()
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return default_state()
    if not isinstance(data, dict):
        return default_state()
    base = default_state()
    base.update(data)
    if int(base.get("schema_version", 0)) < SCHEMA_VERSION:
        base = migrate_state(base)
    else:
        base["history"] = [
            _scrub_history_row(row)
            for row in list(base.get("history", []))
            if isinstance(row, dict)
        ]
        base["runs"] = [
            _scrub_run_row(row)
            for row in list(base.get("runs", []))
            if isinstance(row, dict)
        ]
    return base


def migrate_state(state: dict[str, Any]) -> dict[str, Any]:
    state.setdefault("processed", {})
    state.setdefault("alerted", {})
    state.setdefault("cert_cache", {})
    state.setdefault("market_cache", {})
    state.setdefault("history", [])
    state.setdefault("runs", [])
    state.setdefault("query_cursor", 0)
    state["history"] = [
        _scrub_history_row(row)
        for row in list(state.get("history", []))
        if isinstance(row, dict)
    ]
    state["runs"] = [
        _scrub_run_row(row)
        for row in list(state.get("runs", []))
        if isinstance(row, dict)
    ]
    state["schema_version"] = SCHEMA_VERSION
    return state


def save_state(path: Path, state: dict[str, Any]) -> None:
    state["history"] = [
        _scrub_history_row(row)
        for row in list(state.get("history", []))
        if isinstance(row, dict)
    ]
    state["runs"] = [
        _scrub_run_row(row)
        for row in list(state.get("runs", []))
        if isinstance(row, dict)
    ]
    state["schema_version"] = SCHEMA_VERSION
    state["updated_at"] = iso_z(utc_now())
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(state, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def _fresh(timestamp: str | None, cutoff: datetime) -> bool:
    dt = parse_iso_datetime(timestamp)
    return bool(dt and dt >= cutoff)


def prune_state(state: dict[str, Any], settings: dict[str, Any]) -> dict[str, Any]:
    now = utc_now()
    processed_cutoff = now - timedelta(days=int(settings.get("processed_retention_days", 7)))
    alert_cutoff = now - timedelta(days=int(settings.get("alert_retention_days", 180)))
    history_cutoff = now - timedelta(days=int(settings.get("history_retention_days", 180)))
    cert_cache_cutoff = now - timedelta(days=int(settings.get("cert_cache_days", 7)) * 3)
    market_cache_cutoff = now - timedelta(hours=max(1, int(settings.get("market_cache_hours", 8))) * 3)

    state["processed"] = {
        key: value
        for key, value in dict(state.get("processed", {})).items()
        if _fresh(value.get("at") if isinstance(value, dict) else str(value), processed_cutoff)
    }
    state["alerted"] = {
        key: value
        for key, value in dict(state.get("alerted", {})).items()
        if _fresh(value.get("at") if isinstance(value, dict) else str(value), alert_cutoff)
    }
    state["cert_cache"] = {
        key: value
        for key, value in dict(state.get("cert_cache", {})).items()
        if _fresh(value.get("fetched_at") if isinstance(value, dict) else None, cert_cache_cutoff)
    }
    state["market_cache"] = {
        key: value
        for key, value in dict(state.get("market_cache", {})).items()
        if _fresh(value.get("fetched_at") if isinstance(value, dict) else None, market_cache_cutoff)
    }

    history = [
        _scrub_history_row(row)
        for row in list(state.get("history", []))
        if isinstance(row, dict)
        and _fresh(row.get("last_seen_at") or row.get("first_seen_at"), history_cutoff)
    ]
    history.sort(key=lambda row: row.get("last_seen_at", ""), reverse=True)
    state["history"] = history[: int(settings.get("history_max_items", 750))]
    state["runs"] = [
        _scrub_run_row(row)
        for row in list(state.get("runs", []))
        if isinstance(row, dict)
    ][: int(settings.get("run_history_max_items", 100))]
    return state


def select_queries(queries: list[str], cursor: int, limit: int) -> tuple[list[str], int]:
    if not queries or limit <= 0:
        return [], 0
    limit = min(limit, len(queries))
    start = cursor % len(queries)
    selected = [queries[(start + i) % len(queries)] for i in range(limit)]
    return selected, (start + limit) % len(queries)


def processed_recently(state: dict[str, Any], item_id: str, cooldown_minutes: int) -> bool:
    row = dict(state.get("processed", {})).get(item_id)
    if not isinstance(row, dict):
        return False
    at = parse_iso_datetime(row.get("at"))
    return bool(at and at >= utc_now() - timedelta(minutes=cooldown_minutes))


def mark_processed(state: dict[str, Any], item_id: str, score: int) -> None:
    state.setdefault("processed", {})[item_id] = {"at": iso_z(utc_now()), "score": score}


def mark_alerted(state: dict[str, Any], item_id: str, channels: dict[str, bool]) -> None:
    state.setdefault("alerted", {})[item_id] = {"at": iso_z(utc_now()), "channels": channels}


def is_alerted(state: dict[str, Any], item_id: str) -> bool:
    return item_id in dict(state.get("alerted", {}))


def _money_dict(value: Any) -> dict[str, Any] | None:
    return value.to_dict() if value else None


def _market_dict(market: MarketValue | None) -> dict[str, Any] | None:
    if not market:
        return None
    return {
        "money": market.money.to_dict(),
        "source": market.source,
        "confidence": market.confidence,
        "sample_size": market.sample_size,
        "market_type": market.market_type,
        "required_edge": market.required_edge,
        "unique_sellers": market.unique_sellers,
        "price_low": market.price_low,
        "price_high": market.price_high,
        "dispersion": market.dispersion,
    }


def market_from_dict(data: dict[str, Any] | None) -> MarketValue | None:
    if not isinstance(data, dict):
        return None
    money = data.get("money")
    if not isinstance(money, dict):
        return None
    try:
        parsed_money = Money(float(money["value"]), str(money["currency"]))
    except (KeyError, TypeError, ValueError):
        return None
    def f(name: str) -> float | None:
        try:
            return float(data[name]) if data.get(name) is not None else None
        except (TypeError, ValueError):
            return None
    return MarketValue(
        parsed_money,
        str(data.get("source") or "Preisindikator"),
        str(data.get("confidence") or "niedrig"),
        int(data.get("sample_size") or 0),
        market_type=str(data.get("market_type") or "generic"),
        required_edge=float(data.get("required_edge") or 0.10),
        unique_sellers=int(data.get("unique_sellers")) if data.get("unique_sellers") is not None else None,
        price_low=f("price_low"),
        price_high=f("price_high"),
        dispersion=f("dispersion"),
    )


def _cert_dict(cert: PSACertInfo | None) -> dict[str, Any] | None:
    if not cert:
        return None
    return {
        "cert_number": cert.cert_number,
        "valid": cert.valid,
        "grade": cert.grade,
        "year": cert.year,
        "brand_title": cert.brand_title,
        "subject": cert.subject,
        "card_number": cert.card_number,
        "category": cert.category,
        "variety": cert.variety,
        "population": cert.population,
        "population_higher": cert.population_higher,
        "estimate": _money_dict(cert.estimate),
        "recent_sales": [_money_dict(x) for x in cert.recent_sales],
        "source_url": cert.source_url,
        "data_source": cert.data_source,
    }


def cert_from_dict(data: dict[str, Any]) -> PSACertInfo:
    def money(obj: Any) -> Money | None:
        if not isinstance(obj, dict):
            return None
        try:
            return Money(float(obj["value"]), str(obj["currency"]))
        except (KeyError, TypeError, ValueError):
            return None
    return PSACertInfo(
        cert_number=str(data.get("cert_number", "")),
        valid=bool(data.get("valid")),
        grade=data.get("grade"),
        year=data.get("year"),
        brand_title=data.get("brand_title"),
        subject=data.get("subject"),
        card_number=data.get("card_number"),
        category=data.get("category"),
        variety=data.get("variety"),
        population=data.get("population"),
        population_higher=data.get("population_higher"),
        estimate=money(data.get("estimate")),
        recent_sales=[m for x in data.get("recent_sales", []) if (m := money(x))],
        source_url=data.get("source_url"),
        data_source=data.get("data_source"),
    )


def _cert_cache_days(cert: PSACertInfo, configured_days: int) -> int:
    configured_days = max(1, configured_days)
    current_year = utc_now().year
    year = None
    if cert.year:
        match = re.search(r"(?:19|20)\d{2}", cert.year)
        year = int(match.group(0)) if match else None
    if (cert.population is not None and cert.population <= 25) or (year is not None and year >= current_year - 1):
        return 1
    if cert.population is not None and cert.population <= 50:
        return min(configured_days, 3)
    return configured_days


def get_cached_cert(state: dict[str, Any], cert_number: str, max_age_days: int) -> PSACertInfo | None:
    row = dict(state.get("cert_cache", {})).get(cert_number)
    if not isinstance(row, dict):
        return None
    data = row.get("data")
    if not isinstance(data, dict):
        return None
    cert = cert_from_dict(data)
    fetched = parse_iso_datetime(row.get("fetched_at"))
    ttl_days = _cert_cache_days(cert, max_age_days)
    if not fetched or fetched < utc_now() - timedelta(days=ttl_days):
        return None
    return cert


def put_cached_cert(state: dict[str, Any], cert: PSACertInfo) -> None:
    state.setdefault("cert_cache", {})[cert.cert_number] = {
        "fetched_at": iso_z(utc_now()),
        "data": _cert_dict(cert),
    }


def _market_cache_hours(data: dict[str, Any], configured_hours: int) -> int:
    kind = str(data.get("market_type") or "")
    if kind == "psa_estimate":
        return 1
    if kind == "ebay_active_provisional":
        return min(max(1, configured_hours), 4)
    if kind == "psa_sales":
        return max(configured_hours, 24)
    try:
        dispersion = float(data.get("dispersion")) if data.get("dispersion") is not None else None
    except (TypeError, ValueError):
        dispersion = None
    if dispersion is not None and dispersion > 0.35:
        return min(max(1, configured_hours), 2)
    return max(1, configured_hours)


def get_cached_market(
    state: dict[str, Any],
    fingerprint: str,
    max_age_hours: int,
) -> tuple[bool, MarketValue | None]:
    row = dict(state.get("market_cache", {})).get(fingerprint)
    if not isinstance(row, dict):
        return False, None
    data = row.get("data")
    if data is None:
        effective_hours = min(max(1, max_age_hours), 1)
    elif isinstance(data, dict):
        effective_hours = _market_cache_hours(data, max_age_hours)
    else:
        return False, None
    fetched = parse_iso_datetime(row.get("fetched_at"))
    if not fetched or fetched < utc_now() - timedelta(hours=effective_hours):
        return False, None
    return True, market_from_dict(data) if isinstance(data, dict) else None


def put_cached_market(
    state: dict[str, Any],
    fingerprint: str,
    market: MarketValue | None,
) -> None:
    state.setdefault("market_cache", {})[fingerprint] = {
        "fetched_at": iso_z(utc_now()),
        "data": _market_dict(market),
    }


def hit_to_record(hit: ScoredHit, threshold: int) -> dict[str, Any]:
    listing = hit.listing
    now = iso_z(utc_now())
    return {
        "item_id": listing.item_id,
        "title": listing.title,
        "url": listing.url,
        "image_url": listing.image_urls[0] if listing.image_urls else None,
        "image_urls": listing.image_urls[:4],
        "price": _money_dict(listing.price),
        "shipping": _money_dict(listing.shipping),
        "total_cost": _money_dict(listing.total_cost),
        "created_at": iso_z(listing.created_at) if listing.created_at else None,
        "end_at": iso_z(listing.end_at) if listing.end_at else None,
        "first_seen_at": now,
        "last_seen_at": now,
        "buying_options": listing.buying_options,
        "pure_auction": listing.pure_auction,
        "condition": listing.condition,
        "returns_accepted": listing.returns_accepted,
        "item_location_country": listing.item_location_country,
        "matched_queries": listing.matched_queries,
        "score": hit.score,
        "is_hit": hit.score >= threshold,
        "reasons": hit.reasons,
        "warnings": hit.warnings,
        "score_breakdown": hit.score_breakdown,
        "price_status": hit.price_status,
        "cert_number": hit.cert_number,
        "cert_source": hit.cert_source,
        "cert_confidence": hit.cert_confidence,
        "cert_trusted": hit.cert_trusted,
        "cert": _cert_dict(hit.cert),
        "pricing_identity": pricing_identity_to_dict(pricing_identity_from_listing(listing, hit.cert)),
        "market_value": _market_dict(hit.market_value),
        "discount_pct": hit.discount_pct,
        "availability_status": "active",
    }


def upsert_history(state: dict[str, Any], hit: ScoredHit, threshold: int) -> None:
    record = hit_to_record(hit, threshold)
    history = list(state.get("history", []))
    for idx, old in enumerate(history):
        if old.get("item_id") == hit.listing.item_id:
            record["first_seen_at"] = old.get("first_seen_at") or record["first_seen_at"]
            for field_name in (
                "price_checked_at",
                "price_check_attempts",
                "price_last_improved_at",
                "availability_checked_at",
            ):
                if old.get(field_name) is not None:
                    record[field_name] = old[field_name]
            history[idx] = record
            break
    else:
        history.append(record)
    history.sort(key=lambda row: row.get("last_seen_at", ""), reverse=True)
    state["history"] = history


def append_run(
    state: dict[str, Any],
    stats: RunStats,
    max_items: int,
    *,
    results: list[dict[str, Any]] | None = None,
) -> None:
    row = {
        "started_at": stats.started_at,
        "completed_at": stats.completed_at,
        "queries_used": stats.queries_used,
        "listings_seen": stats.listings_seen,
        "fresh_listings": stats.fresh_listings,
        "detailed_candidates": stats.detailed_candidates,
        "psa_lookups": stats.psa_lookups,
        "hits": stats.hits,
        "near_hits": stats.near_hits,
        "ebay_calls": stats.ebay_calls,
        "total_ebay_calls": stats.ebay_calls,
        "notes": stats.notes,
    }
    if results is not None:
        row["results"] = [
            _scrub_history_row(item)
            for item in results
            if isinstance(item, dict)
        ]
    state["runs"] = [row, *list(state.get("runs", []))][:max_items]
