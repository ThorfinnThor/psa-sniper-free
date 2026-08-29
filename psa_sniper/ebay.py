from __future__ import annotations

import base64
import time
from dataclasses import dataclass
from datetime import datetime
from typing import Any
from urllib.parse import quote

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

from .models import Listing, Money
from .util import iso_z, parse_float, parse_int, parse_iso_datetime


class EbayError(RuntimeError):
    def __init__(
        self,
        message: str,
        *,
        status_code: int | None = None,
        retryable: bool = False,
    ) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.retryable = retryable

    @property
    def missing(self) -> bool:
        return self.status_code in {404, 410}


class EbayBudgetExceeded(EbayError):
    pass


@dataclass(slots=True)
class EbayQuotaSnapshot:
    limit: int
    remaining: int
    count: int | None = None
    reset: str | None = None


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
        self.analytics_url = f"https://{host}/developer/analytics/v1_beta/rate_limit/"
        self.client_id = client_id
        self.client_secret = client_secret
        self.marketplace_id = marketplace_id
        self.delivery_country = delivery_country
        self.buyer_postal_code = buyer_postal_code.strip()
        self.delay_seconds = max(0.0, delay_seconds)
        self.max_calls = max_calls
        self.calls_made = 0
        self.session = requests.Session()
        # Keine versteckten Status-Retries: jeder Browse-Versuch muss in
        # calls_made landen, sonst kann das echte eBay-Tagesbudget überschritten
        # werden. Retries passieren kontrolliert in _get().
        retry = Retry(total=0, connect=0, read=0, redirect=0, status=0)
        self.session.mount("https://", HTTPAdapter(max_retries=retry))
        self.token: str | None = None

    def authenticate(self) -> None:
        raw = f"{self.client_id}:{self.client_secret}".encode()
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

    def get_rate_limits(self) -> EbayQuotaSnapshot | None:
        """Best-effort Browse quota snapshot; analytics failures never break a scan."""
        try:
            headers = self._headers()
            response = self.session.get(
                self.analytics_url,
                headers=headers,
                params={"api_name": "browse", "api_context": "buy"},
                timeout=20,
            )
        except requests.RequestException:
            return None
        if response.status_code == 204 or response.status_code >= 400:
            return None
        try:
            payload = response.json()
        except ValueError:
            return None
        rates: list[dict[str, Any]] = []
        for group in payload.get("rateLimits", []) or []:
            for resource in group.get("resources", []) or []:
                for rate in resource.get("rates", []) or []:
                    if isinstance(rate, dict):
                        rates.append(rate)
        usable = [
            rate for rate in rates
            if parse_int(rate.get("limit")) is not None and parse_int(rate.get("remaining")) is not None
        ]
        if not usable:
            return None
        # Browse resources can expose separate windows. The minimum remaining
        # value is the conservative usable budget for the application.
        remaining = min(int(parse_int(rate.get("remaining")) or 0) for rate in usable)
        limit = min(int(parse_int(rate.get("limit")) or 0) for rate in usable)
        count_values = [parse_int(rate.get("count")) for rate in usable]
        count = max((value for value in count_values if value is not None), default=None)
        reset = next((str(rate.get("reset")) for rate in usable if rate.get("reset")), None)
        return EbayQuotaSnapshot(limit=limit, remaining=remaining, count=count, reset=reset)

    def _get(
        self,
        path: str,
        *,
        params: dict[str, Any] | None = None,
        retry_auth: bool = True,
    ) -> dict[str, Any]:
        retryable_statuses = {429, 500, 502, 503, 504}
        status_retries = 0
        network_retries = 0
        auth_retry_left = bool(retry_auth)

        while True:
            if self.calls_made >= self.max_calls:
                raise EbayBudgetExceeded(f"eBay-Call-Budget von {self.max_calls} ist ausgeschöpft")
            if self.delay_seconds:
                time.sleep(self.delay_seconds)
            self.calls_made += 1

            try:
                response = self.session.get(
                    f"{self.api_base}{path}",
                    headers=self._headers(),
                    params=params,
                    timeout=30,
                )
            except requests.RequestException as exc:
                # Konservativ mitzählen: auch ein Netzwerkfehler kann den Server
                # bereits erreicht haben. Maximal zwei kontrollierte Wiederholungen.
                if network_retries < 2:
                    network_retries += 1
                    time.sleep(min(4.0, 0.5 * (2 ** (network_retries - 1))))
                    continue
                raise EbayError(
                    "eBay Browse API Netzwerkfehler nach Wiederholungen",
                    retryable=True,
                ) from exc

            if response.status_code == 401 and auth_retry_left:
                # Ein abgelaufener App-Token wird einmal erneuert. Der zweite
                # Browse-Versuch wird ebenfalls sauber als Call gezählt.
                self.token = None
                auth_retry_left = False
                continue

            if response.status_code in retryable_statuses and status_retries < 2:
                status_retries += 1
                wait = min(4.0, 0.5 * (2 ** (status_retries - 1)))
                raw_retry_after = response.headers.get("Retry-After")
                if raw_retry_after:
                    try:
                        wait = min(10.0, max(wait, float(raw_retry_after)))
                    except (TypeError, ValueError):
                        pass
                time.sleep(wait)
                continue

            if response.status_code >= 400:
                message = ""
                try:
                    body = response.json()
                    errors = body.get("errors") or []
                    if errors:
                        message = str(errors[0].get("message") or errors[0].get("longMessage") or "")
                except Exception:
                    message = ""
                hint = (
                    " Prüfe außerdem, ob deine App für die Browse API in Production freigeschaltet ist."
                    if response.status_code in {401, 403}
                    else ""
                )
                raise EbayError(
                    f"eBay Browse API fehlgeschlagen ({response.status_code})"
                    + (f": {message}" if message else "")
                    + hint,
                    status_code=response.status_code,
                    retryable=response.status_code in retryable_statuses,
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
        offset: int = 0,
        sort: str | None = None,
        fixed_price_only: bool | None = None,
    ) -> list[Listing]:
        # Discovery wants newest first. Comp searches (started_after=None) use
        # eBay's default Best Match ranking and fixed-price inventory.
        if sort is None and started_after is not None:
            sort = "newlyListed"
        if fixed_price_only is None:
            fixed_price_only = started_after is None
        params: dict[str, Any] = {
            "q": query,
            "limit": min(max(1, limit), 200),
            "offset": max(0, int(offset)),
        }
        if sort:
            params["sort"] = sort
        filters: list[str] = []
        if started_after:
            filters.append(f"itemStartDate:[{iso_z(started_after)}]")
        if self.delivery_country:
            filters.append(f"deliveryCountry:{self.delivery_country}")
        if fixed_price_only:
            filters.append("buyingOptions:{FIXED_PRICE}")
        if filters:
            params["filter"] = ",".join(filters)
        payload = self._get("/item_summary/search", params=params)
        return [self._listing_from_summary(x) for x in payload.get("itemSummaries", [])]

    def get_item(self, item_id: str, *, compact: bool = False) -> Listing:
        params = {"fieldgroups": "COMPACT"} if compact else None
        payload = self._get(f"/item/{quote(item_id, safe='')}", params=params)
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
        username = seller.get("username") or seller.get("userId")
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
