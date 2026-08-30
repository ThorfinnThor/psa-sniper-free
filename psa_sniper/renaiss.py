from __future__ import annotations

import os
import re
from dataclasses import dataclass
from datetime import timedelta
from typing import Any

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

from .fx import FXRates
from .identity import PricingIdentity, normalize_language, variant_from_text
from .models import MarketValue, Money
from .util import normalize_text, parse_iso_datetime, utc_now

RENAISS_API_URL = "https://api.renaissos.com"
RENAISS_SITE_URL = "https://index.renaissos.com"


class RenaissError(RuntimeError):
    def __init__(self, message: str, *, status_code: int | None = None) -> None:
        super().__init__(message)
        self.status_code = status_code


@dataclass(frozen=True, slots=True)
class RenaissMatch:
    market: MarketValue
    item_id: str
    href: str | None
    last_sale_at: str | None


def build_renaiss_query(identity: PricingIdentity) -> str:
    language = {
        "JP": "Japanese",
        "EN": "English",
        "DE": "German",
        "KR": "Korean",
        "FR": "French",
        "IT": "Italian",
        "ES": "Spanish",
        "CN": "Chinese",
    }.get(str(identity.language or "").upper(), identity.language or "")
    parts = [
        " ".join(identity.subjects),
        identity.set_code or "",
        identity.card_number,
        language,
        "PSA 10",
    ]
    return " ".join(part.strip() for part in parts if part and part.strip())


def _number_key(value: Any) -> str:
    return "".join(re.findall(r"[a-z0-9]+", normalize_text(str(value or ""))))


def _number_matches(expected: str, actual: Any) -> bool:
    actual_text = str(actual or "").strip()
    if not actual_text:
        return False
    if _number_key(expected) == _number_key(actual_text):
        return True
    if expected.isdigit() and actual_text.isdigit():
        return int(expected) == int(actual_text)
    # Catalog APIs commonly store 039/100 as card number 39. This is safe only
    # together with the separately required subject, set and language matches.
    if "/" in expected:
        numerator = expected.split("/", 1)[0]
        if _number_key(numerator).lstrip("0") == _number_key(actual_text).lstrip("0"):
            return True
    return False


def _result_matches_identity(row: dict[str, Any], identity: PricingIdentity) -> bool:
    if str(row.get("company") or "").upper() != "PSA":
        return False
    grade = normalize_text(str(row.get("gradeLabel") or row.get("grade") or ""))
    if grade not in {"psa 10", "10 gem mint", "gem mint 10", "gem mt 10"}:
        return False
    if not _number_matches(identity.card_number, row.get("cardNumber")):
        return False

    name_tokens = set(re.findall(r"[a-z0-9]+", normalize_text(str(row.get("name") or ""))))
    if not name_tokens or any(normalize_text(subject) not in name_tokens for subject in identity.subjects):
        return False

    expected_set = _number_key(identity.set_code)
    actual_set = _number_key(row.get("setCode"))
    if expected_set and actual_set != expected_set:
        return False

    if identity.language:
        actual_language = normalize_language(str(row.get("language") or ""))
        if actual_language != identity.language:
            return False

    if identity.variant:
        variant_text = " ".join(
            str(row.get(key) or "")
            for key in ("variation", "rarity", "name")
        )
        if variant_from_text(variant_text) != identity.variant:
            return False
    return True


def market_from_renaiss_result(
    row: dict[str, Any],
    *,
    target_currency: str,
    fx: FXRates,
    max_sale_age_days: int = 365,
    now=None,
) -> RenaissMatch | None:
    try:
        cents = int(row.get("priceUsdCents"))
    except (TypeError, ValueError):
        return None
    if cents <= 0:
        return None

    last_sale_at = parse_iso_datetime(row.get("lastSaleAt"))
    reference_at = last_sale_at or parse_iso_datetime(row.get("updatedAt"))
    current = now or utc_now()
    if reference_at is None:
        return None
    if reference_at < current - timedelta(days=max(1, max_sale_age_days)):
        return None
    if reference_at > current + timedelta(days=1):
        return None

    converted = fx.convert(Money(cents / 100.0, "USD"), target_currency)
    if converted is None:
        return None
    confidence_raw = str(row.get("confidence") or "low").casefold()
    confidence = {"high": "hoch", "medium": "mittel", "med": "mittel"}.get(
        confidence_raw,
        "niedrig",
    )
    required_edge = {"hoch": 0.12, "mittel": 0.15}.get(confidence, 0.25)
    market = MarketValue(
        converted,
        "Renaiss Index · echte PSA-10-Verkäufe",
        confidence,
        0,
        market_type="renaiss_fmv",
        required_edge=required_edge,
    )
    return RenaissMatch(
        market=market,
        item_id=str(row.get("id") or ""),
        href=str(row.get("href")) if row.get("href") else None,
        last_sale_at=str(row.get("lastSaleAt")) if row.get("lastSaleAt") else None,
    )


class RenaissClient:
    def __init__(
        self,
        *,
        key_id: str | None = None,
        secret: str | None = None,
        max_calls: int = 8,
        timeout_seconds: float = 20,
        session: requests.Session | None = None,
    ) -> None:
        self.key_id = str(key_id or "").strip()
        self.secret = str(secret or "").strip()
        self.max_calls = max(0, int(max_calls))
        self.timeout_seconds = max(1.0, float(timeout_seconds))
        self.calls_made = 0
        self.rate_limited = False
        self.session = session or requests.Session()
        if session is None:
            self.session.mount(
                "https://",
                HTTPAdapter(
                    max_retries=Retry(
                        total=2,
                        backoff_factor=0.5,
                        status_forcelist=(500, 502, 503, 504),
                        allowed_methods=("GET",),
                    )
                ),
            )

    @classmethod
    def from_env(cls, *, max_calls: int = 8) -> RenaissClient:
        return cls(
            key_id=os.getenv("RENAISS_API_KEY"),
            secret=os.getenv("RENAISS_API_SECRET"),
            max_calls=max_calls,
        )

    @property
    def authenticated(self) -> bool:
        return bool(self.key_id and self.secret)

    def _headers(self) -> dict[str, str]:
        if not self.authenticated:
            return {"Accept": "application/json"}
        return {
            "Accept": "application/json",
            "X-Api-Key": self.key_id,
            "X-Api-Secret": self.secret,
        }

    def _get(self, path: str, *, params: dict[str, str] | None = None) -> Any:
        if self.calls_made >= self.max_calls:
            raise RenaissError("Renaiss-Abfragebudget ausgeschöpft")
        self.calls_made += 1
        try:
            response = self.session.get(
                f"{RENAISS_API_URL}{path}",
                params=params,
                headers=self._headers(),
                timeout=self.timeout_seconds,
            )
        except requests.RequestException as exc:
            raise RenaissError("Renaiss-Netzwerkfehler") from exc
        if response.status_code == 429:
            self.rate_limited = True
            raise RenaissError("Renaiss-Rate-Limit erreicht", status_code=429)
        if response.status_code >= 400:
            raise RenaissError(
                f"Renaiss HTTP {response.status_code}",
                status_code=response.status_code,
            )
        try:
            return response.json()
        except ValueError as exc:
            raise RenaissError("Renaiss-Antwort ist kein gültiges JSON") from exc

    def market_for_identity(
        self,
        identity: PricingIdentity,
        *,
        target_currency: str,
        fx: FXRates,
        max_sale_age_days: int = 365,
    ) -> RenaissMatch | None:
        data = self._get("/v1/search", params={"q": build_renaiss_query(identity)})
        rows = data.get("results", []) if isinstance(data, dict) else []
        if not isinstance(rows, list):
            raise RenaissError("Renaiss-Suchergebnis hat ein ungültiges Schema")
        exact: list[dict[str, Any]] = []
        seen: set[str] = set()
        for row in rows:
            if not isinstance(row, dict) or not _result_matches_identity(row, identity):
                continue
            item_id = str(row.get("id") or "")
            if not item_id or item_id in seen:
                continue
            seen.add(item_id)
            exact.append(row)
        # Multiple exact catalog identities mean the title lacks a dimension
        # needed to distinguish a printing. Never guess a price in that case.
        if len(exact) != 1:
            return None
        return market_from_renaiss_result(
            exact[0],
            target_currency=target_currency,
            fx=fx,
            max_sale_age_days=max_sale_age_days,
        )

    def market_for_cert(
        self,
        cert_number: str,
        *,
        target_currency: str,
        fx: FXRates,
        identity: PricingIdentity | None = None,
        max_sale_age_days: int = 365,
    ) -> RenaissMatch | None:
        """Resolve one exact PSA slab and return its card's PSA-10 FMV.

        A trusted certificate is a stronger lookup key than a marketplace title.
        The optional identity check remains deliberately strict so a disagreement
        between the PSA/listing identity and Renaiss can never create a price.
        """
        expected = "".join(re.findall(r"\d+", str(cert_number or "")))
        if len(expected) < 7:
            raise RenaissError("Renaiss-Certnummer ist ungültig")
        data = self._get(f"/v1/graded/PSA{expected}")
        if not isinstance(data, dict):
            raise RenaissError("Renaiss-Certantwort hat ein ungültiges Schema")
        actual = "".join(re.findall(r"\d+", str(data.get("certNumber") or data.get("cert") or "")))
        if actual != expected or str(data.get("company") or "").upper() != "PSA":
            return None
        if data.get("found") is not True:
            return None
        row_raw = data.get("card") or data.get("item")
        if not isinstance(row_raw, dict):
            return None
        row = dict(row_raw)
        row.setdefault("company", data.get("company"))
        row.setdefault("grade", data.get("grade"))
        row.setdefault("gradeLabel", data.get("gradeLabel"))
        if identity is not None and not _result_matches_identity(row, identity):
            return None
        return market_from_renaiss_result(
            row,
            target_currency=target_currency,
            fx=fx,
            max_sale_age_days=max_sale_age_days,
        )
