from __future__ import annotations

import base64
import time
from datetime import datetime
from typing import Any
from urllib.parse import quote

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

from .models import Listing, Money
from .util import iso_z, parse_float, parse_int, parse_iso_datetime


class EbayError(RuntimeError):
    pass


class EbayBudgetExceeded(EbayError):
    pass


class EbayClient:
    def __init__(
        self,
        client_id: str,
        client_secret: str,
        *,
        environment: str = "production",
        marketplace_id: str = "EBAY_DE",
        delivery_country: str = "DE",
        buyer_postal_code: str = "",
        delay_seconds: float = 0.25,
        max_calls: int = 30,
    ) -> None:
        environment = environment.strip().lower()
        if environment not in {"production", "sandbox"}:
            raise ValueError("environment muss 'production' oder 'sandbox' sein")
        host = "api.ebay.com" if environment == "production" else "api.sandbox.ebay.com"
        self.token_url = f"https://{host}/identity/v1/oauth2/token"
        self.api_base = f"https://{host}/buy/browse/v1"
        self.client_id = client_id
        self.client_secret = client_secret
        self.marketplace_id = marketplace_id
        self.delivery_country = delivery_country
        self.buyer_postal_code = buyer_postal_code.strip()
        self.delay_seconds = max(0.0, delay_seconds)
        self.max_calls = max_calls
        self.calls_made = 0
        self.session = requests.Session()
        retry = Retry(
            total=3,
            connect=3,
            read=3,
            backoff_factor=0.8,
            status_forcelist=(429, 500, 502, 503, 504),
            allowed_methods=frozenset({"GET", "POST"}),
            respect_retry_after_header=True,
        )
        self.session.mount("https://", HTTPAdapter(max_retries=retry))
        self.token: str | None = None

    def authenticate(self) -> None:
        raw = f"{self.client_id}:{self.client_secret}".encode("utf-8")
        auth = base64.b64encode(raw).decode("ascii")
        response = self.session.post(
            self.token_url,
            headers={
                "Authorization": f"Basic {auth}",
                "Content-Type": "application/x-www-form-urlencoded",
                "Accept": "application/json",
            },
            data={
                "grant_type": "client_credentials",
                "scope": "https://api.ebay.com/oauth/api_scope",
            },
            timeout=30,
        )
        if response.status_code >= 400:
            raise EbayError(
                f"eBay OAuth fehlgeschlagen ({response.status_code}). "
                "Prüfe Production Client ID/Secret und den Status des Keysets."
            )
        payload = response.json()
        self.token = payload.get("access_token")
        if not self.token:
            raise EbayError("eBay OAuth-Antwort enthielt keinen Access Token")

    def _headers(self) -> dict[str, str]:
        if not self.token:
            self.authenticate()
        headers = {
            "Authorization": f"Bearer {self.token}",
            "X-EBAY-C-MARKETPLACE-ID": self.marketplace_id,
            "Accept": "application/json",
            "Accept-Language": "de-DE,de;q=0.9,en;q=0.7",
        }
        contextual: list[str] = []
        if self.delivery_country:
            contextual.append(f"country={self.delivery_country}")
        if self.buyer_postal_code:
            contextual.append(f"zip={self.buyer_postal_code}")
        if contextual:
            headers["X-EBAY-C-ENDUSERCTX"] = "contextualLocation=" + ",".join(contextual)
        return headers

    def _get(
        self,
        path: str,
        *,
        params: dict[str, Any] | None = None,
        retry_auth: bool = True,
    ) -> dict[str, Any]:
        if self.calls_made >= self.max_calls:
            raise EbayBudgetExceeded(f"eBay-Call-Budget von {self.max_calls} ist ausgeschöpft")
        if self.delay_seconds:
            time.sleep(self.delay_seconds)
        self.calls_made += 1
        response = self.session.get(
            f"{self.api_base}{path}",
            headers=self._headers(),
            params=params,
            timeout=30,
        )
        if response.status_code == 401 and retry_auth:
            self.token = None
            return self._get(path, params=params, retry_auth=False)
        if response.status_code >= 400:
            message = ""
            try:
                body = response.json()
                errors = body.get("errors") or []
                if errors:
                    message = str(errors[0].get("message") or errors[0].get("longMessage") or "")
            except Exception:
                message = ""
            if response.status_code in {401, 403}:
                hint = (
                    " Prüfe außerdem, ob deine App für die Browse API in Production "
                    "freigeschaltet ist."
                )
            else:
                hint = ""
            raise EbayError(
                f"eBay Browse API fehlgeschlagen ({response.status_code})"
                + (f": {message}" if message else "")
                + hint
            )
        try:
            return response.json()
        except ValueError as exc:
            raise EbayError("eBay Browse API lieferte keine gültige JSON-Antwort") from exc

    def search(
        self,
        query: str,
        *,
        limit: int = 45,
        started_after: datetime | None = None,
    ) -> list[Listing]:
        params: dict[str, Any] = {
            "q": query,
            "sort": "newlyListed",
            "limit": min(max(1, limit), 200),
        }
        filters: list[str] = []
        if started_after:
            filters.append(f"itemStartDate:[{iso_z(started_after)}]")
        if self.delivery_country:
            filters.append(f"deliveryCountry:{self.delivery_country}")
        if filters:
            params["filter"] = ",".join(filters)
        payload = self._get("/item_summary/search", params=params)
        return [self._listing_from_summary(x) for x in payload.get("itemSummaries", [])]

    def get_item(self, item_id: str) -> Listing:
        payload = self._get(f"/item/{quote(item_id, safe='')}")
        return self._listing_from_item(payload)

    @staticmethod
    def _money(obj: dict[str, Any] | None) -> Money | None:
        if not obj:
            return None
        value = parse_float(obj.get("value"))
        currency = obj.get("currency")
        if value is None or not currency:
            return None
        return Money(value=value, currency=str(currency).upper())

    @classmethod
    def _shipping_money(cls, payload: dict[str, Any]) -> Money | None:
        options = payload.get("shippingOptions") or []
        costs: list[Money] = []
        for option in options:
            cost = cls._money(option.get("shippingCost"))
            if cost:
                costs.append(cost)
        if not costs:
            return None
        grouped: dict[str, list[Money]] = {}
        for cost in costs:
            grouped.setdefault(cost.currency, []).append(cost)
        currency, values = max(grouped.items(), key=lambda row: len(row[1]))
        return Money(min(x.value for x in values), currency)

    @staticmethod
    def _aspects(payload: dict[str, Any]) -> dict[str, list[str]]:
        aspects: dict[str, list[str]] = {}
        for row in payload.get("localizedAspects", []) or []:
            name = str(row.get("name") or row.get("localizedAspectName") or "").strip()
            value = row.get("value")
            if not name or value is None:
                continue
            values = [str(v) for v in value] if isinstance(value, list) else [str(value)]
            aspects.setdefault(name, []).extend(values)
        return aspects

    @staticmethod
    def _images(payload: dict[str, Any]) -> list[str]:
        urls: list[str] = []
        for obj in [payload.get("image"), *(payload.get("additionalImages") or [])]:
            if isinstance(obj, dict) and obj.get("imageUrl") and obj["imageUrl"] not in urls:
                urls.append(str(obj["imageUrl"]))
        return urls

    @staticmethod
    def _returns_accepted(payload: dict[str, Any]) -> bool | None:
        terms = payload.get("returnTerms") or {}
        value = terms.get("returnsAccepted")
        return bool(value) if isinstance(value, bool) else None

    @staticmethod
    def _seller_fields(payload: dict[str, Any]) -> tuple[str | None, float | None, int | None]:
        seller = payload.get("seller") or {}
        username = seller.get("username")
        percentage = parse_float(seller.get("feedbackPercentage"))
        score = parse_int(seller.get("feedbackScore"))
        return username, percentage, score

    def _listing_from_summary(self, payload: dict[str, Any]) -> Listing:
        seller, feedback_pct, feedback_score = self._seller_fields(payload)
        location = payload.get("itemLocation") or {}
        return Listing(
            item_id=str(payload.get("itemId", "")),
            title=str(payload.get("title", "")),
            url=str(payload.get("itemWebUrl", "")),
            price=self._money(payload.get("price")),
            shipping=self._shipping_money(payload),
            created_at=parse_iso_datetime(payload.get("itemCreationDate")),
            end_at=parse_iso_datetime(payload.get("itemEndDate")),
            image_urls=self._images(payload),
            aspects=self._aspects(payload),
            seller=seller,
            seller_feedback_percentage=feedback_pct,
            seller_feedback_score=feedback_score,
            buying_options=list(payload.get("buyingOptions") or []),
            condition=payload.get("condition"),
            returns_accepted=self._returns_accepted(payload),
            item_location_country=location.get("country"),
            raw=payload,
        )

    def _listing_from_item(self, payload: dict[str, Any]) -> Listing:
        seller, feedback_pct, feedback_score = self._seller_fields(payload)
        location = payload.get("itemLocation") or {}
        return Listing(
            item_id=str(payload.get("itemId", "")),
            title=str(payload.get("title", "")),
            url=str(payload.get("itemWebUrl", "")),
            price=self._money(payload.get("price")),
            shipping=self._shipping_money(payload),
            created_at=parse_iso_datetime(payload.get("itemCreationDate")),
            end_at=parse_iso_datetime(payload.get("itemEndDate")),
            image_urls=self._images(payload),
            aspects=self._aspects(payload),
            seller=seller,
            seller_feedback_percentage=feedback_pct,
            seller_feedback_score=feedback_score,
            buying_options=list(payload.get("buyingOptions") or []),
            condition=payload.get("condition"),
            returns_accepted=self._returns_accepted(payload),
            item_location_country=location.get("country"),
            raw=payload,
        )
