from __future__ import annotations

import re
import unicodedata
from collections.abc import Iterable
from datetime import UTC, datetime
from statistics import median

from .models import Money


def parse_iso_datetime(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def iso_z(value: datetime) -> str:
    return value.astimezone(UTC).isoformat(timespec="seconds").replace("+00:00", "Z")


def utc_now() -> datetime:
    return datetime.now(UTC)


def normalize_text(value: str | None) -> str:
    if not value:
        return ""
    value = unicodedata.normalize("NFKD", value).encode("ascii", "ignore").decode("ascii")
    value = value.lower()
    value = re.sub(r"[^a-z0-9]+", " ", value)
    return re.sub(r"\s+", " ", value).strip()


def has_phrase(haystack: str, needle: str | None) -> bool:
    needle_n = normalize_text(needle)
    return bool(needle_n and needle_n in normalize_text(haystack))


def parse_float(value: str | int | float | None) -> float | None:
    if value is None:
        return None
    try:
        return float(str(value).replace(",", ""))
    except ValueError:
        return None


def parse_int(value: str | int | None) -> int | None:
    if value is None:
        return None
    match = re.search(r"-?\d[\d,.]*", str(value))
    if not match:
        return None
    try:
        return int(float(match.group(0).replace(",", "")))
    except ValueError:
        return None


def median_money(values: Iterable[Money]) -> Money | None:
    grouped: dict[str, list[float]] = {}
    for money in values:
        grouped.setdefault(money.currency.upper(), []).append(float(money.value))
    if not grouped:
        return None
    currency, nums = max(grouped.items(), key=lambda kv: len(kv[1]))
    return Money(float(median(nums)), currency)


def redact(value: str, keep: int = 4) -> str:
    if len(value) <= keep:
        return "*" * len(value)
    return "*" * (len(value) - keep) + value[-keep:]
