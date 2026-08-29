from __future__ import annotations

import csv
import io

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

from .models import Money

ECB_URL = (
    "https://data-api.ecb.europa.eu/service/data/EXR/"
    "D.USD+GBP+JPY+CHF.EUR.SP00.A?lastNObservations=1&format=csvdata"
)


class FXRates:
    def __init__(self) -> None:
        self.per_eur: dict[str, float] = {"EUR": 1.0}
        self.available = False

    def refresh(self) -> bool:
        session = requests.Session()
        session.mount(
            "https://",
            HTTPAdapter(
                max_retries=Retry(
                    total=2,
                    backoff_factor=0.5,
                    status_forcelist=(429, 500, 502, 503, 504),
                )
            ),
        )
        try:
            response = session.get(ECB_URL, timeout=20)
            response.raise_for_status()
            reader = csv.DictReader(io.StringIO(response.text))
            for row in reader:
                currency = row.get("CURRENCY") or row.get("CURRENCY_DENOM")
                value = row.get("OBS_VALUE")
                if currency and value:
                    try:
                        self.per_eur[currency.upper()] = float(value)
                    except ValueError:
                        continue
            self.available = len(self.per_eur) > 1
        except Exception:
            self.available = False
        return self.available

    def convert(self, money: Money, to_currency: str) -> Money | None:
        source = money.currency.upper()
        target = to_currency.upper()
        if source == target:
            value = money.value
        else:
            if source not in self.per_eur or target not in self.per_eur:
                return None
            eur_value = money.value / self.per_eur[source]
            value = eur_value * self.per_eur[target]
        return Money(
            value,
            target,
            source_id=money.source_id,
            seller_key=money.seller_key,
            identity_score=money.identity_score,
            match_penalty=money.match_penalty,
        )
