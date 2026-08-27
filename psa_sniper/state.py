from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from .models import MarketValue, PSACertInfo, RunStats, ScoredHit
from .util import iso_z, parse_iso_datetime, utc_now

SCHEMA_VERSION = 2


def default_state() -> dict[str, Any]:
    return {
        "schema_version": SCHEMA_VERSION,
        "query_cursor": 0,
        "processed": {},
        "alerted": {},
        "cert_cache": {},
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
    return base


def migrate_state(state: dict[str, Any]) -> dict[str, Any]:
    state.setdefault("processed", {})
    state.setdefault("alerted", {})
    state.setdefault("cert_cache", {})
    state.setdefault("history", [])
    state.setdefault("runs", [])
    state.setdefault("query_cursor", 0)
    state["schema_version"] = SCHEMA_VERSION
    return state


def save_state(path: Path, state: dict[str, Any]) -> None:
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
    cache_cutoff = now - timedelta(days=int(settings.get("cert_cache_days", 7)) * 3)

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
        if _fresh(value.get("fetched_at") if isinstance(value, dict) else None, cache_cutoff)
    }
    history = [
        row
        for row in list(state.get("history", []))
        if _fresh(row.get("last_seen_at") or row.get("first_seen_at"), history_cutoff)
    ]
    history.sort(key=lambda row: row.get("last_seen_at", ""), reverse=True)
    state["history"] = history[: int(settings.get("history_max_items", 750))]
    state["runs"] = list(state.get("runs", []))[: int(settings.get("run_history_max_items", 100))]
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
    state.setdefault("alerted", {})[item_id] = {
        "at": iso_z(utc_now()),
        "channels": channels,
    }


def is_alerted(state: dict[str, Any], item_id: str) -> bool:
    return item_id in dict(state.get("alerted", {}))


def _money_dict(value: Any) -> dict[str, Any] | None:
    return value.to_dict() if value else None


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
    from .models import Money

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


def get_cached_cert(state: dict[str, Any], cert_number: str, max_age_days: int) -> PSACertInfo | None:
    row = dict(state.get("cert_cache", {})).get(cert_number)
    if not isinstance(row, dict):
        return None
    fetched = parse_iso_datetime(row.get("fetched_at"))
    if not fetched or fetched < utc_now() - timedelta(days=max_age_days):
        return None
    data = row.get("data")
    return cert_from_dict(data) if isinstance(data, dict) else None


def put_cached_cert(state: dict[str, Any], cert: PSACertInfo) -> None:
    state.setdefault("cert_cache", {})[cert.cert_number] = {
        "fetched_at": iso_z(utc_now()),
        "data": _cert_dict(cert),
    }


def hit_to_record(hit: ScoredHit, threshold: int) -> dict[str, Any]:
    listing = hit.listing
    market: MarketValue | None = hit.market_value
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
        "seller": listing.seller,
        "seller_feedback_percentage": listing.seller_feedback_percentage,
        "seller_feedback_score": listing.seller_feedback_score,
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
        "cert_number": hit.cert_number,
        "cert_source": hit.cert_source,
        "cert_confidence": hit.cert_confidence,
        "cert_trusted": hit.cert_trusted,
        "cert": _cert_dict(hit.cert),
        "market_value": (
            {
                "money": market.money.to_dict(),
                "source": market.source,
                "confidence": market.confidence,
                "sample_size": market.sample_size,
            }
            if market
            else None
        ),
        "discount_pct": hit.discount_pct,
    }


def upsert_history(state: dict[str, Any], hit: ScoredHit, threshold: int) -> None:
    record = hit_to_record(hit, threshold)
    history = list(state.get("history", []))
    for idx, old in enumerate(history):
        if old.get("item_id") == hit.listing.item_id:
            record["first_seen_at"] = old.get("first_seen_at") or record["first_seen_at"]
            history[idx] = record
            break
    else:
        history.append(record)
    history.sort(key=lambda row: row.get("last_seen_at", ""), reverse=True)
    state["history"] = history


def append_run(state: dict[str, Any], stats: RunStats, max_items: int) -> None:
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
        "notes": stats.notes,
    }
    state["runs"] = [row, *list(state.get("runs", []))][:max_items]
