import json
from datetime import UTC, datetime

import pytest

from psa_sniper.identity import PricingIdentity
from psa_sniper.models import Money
from psa_sniper.point130 import (
    exact_point130_sales,
    market_value_from_point130_sales,
    parse_point130_sales,
)


class IdentityFX:
    def convert(self, money, target_currency):
        if money.currency.upper() != target_currency.upper():
            return None
        return Money(money.value, target_currency)


def sale(title, value, sold_at="2026-08-10T23:52:14Z", sale_id=None):
    row = {
        "title": title,
        "price": {"value": value, "currency": "USD"},
        "sold_at": sold_at,
        "source_url": "https://130point.com/search?new=sold",
    }
    if sale_id:
        row["id"] = sale_id
    return row


def mew_identity():
    return PricingIdentity(
        card_number="039/100",
        subjects=("mew",),
        terms=("mew", "s8"),
        year="2021",
        set_code="S8",
        language="JP",
        variant="HOLO",
    )


def test_exact_130point_psa10_sales_create_medium_sold_market():
    data = {
        "sales": [
            sale("Pokemon 2021 Japanese S8 Fusion Arts Mew V 039/100 Holo PSA 10", 52, sale_id="a"),
            sale("Mew V 039/100 Japanese Holo Fusion Arts Pokemon PSA 10", 50, sale_id="b"),
        ]
    }
    values = exact_point130_sales(
        parse_point130_sales(data),
        mew_identity(),
        target_currency="USD",
        fx=IdentityFX(),
        now=datetime(2026, 8, 29, tzinfo=UTC),
    )
    market = market_value_from_point130_sales(values)
    assert len(values) == 2
    assert market is not None
    assert market.money.value == 51
    assert market.market_type == "point130_sold"
    assert market.confidence == "mittel"
    assert market.required_edge == .15


@pytest.mark.parametrize(
    "title",
    [
        "Pokemon 2021 English S8 Fusion Arts Mew V 039/100 Holo PSA 10",
        "Pokemon 2021 Japanese S9 Fusion Arts Mew V 039/100 Holo PSA 10",
        "Pokemon 2021 Japanese S8 Fusion Arts Mew V 106/100 Holo PSA 10",
        "Pokemon 2021 Japanese S8 Fusion Arts Mew V 039/100 Holo PSA 9",
        "Pokemon 2021 Japanese S8 Fusion Arts Mew V 039/100 PSA 10",
    ],
)
def test_130point_sale_rejects_wrong_or_incomplete_psa10_identity(title):
    values = exact_point130_sales(
        parse_point130_sales({"sales": [sale(title, 52)]}),
        mew_identity(),
        target_currency="USD",
        fx=IdentityFX(),
        now=datetime(2026, 8, 29, tzinfo=UTC),
    )
    assert values == []


def test_130point_sales_are_deduplicated_and_old_sales_expire():
    recent = sale(
        "Pokemon 2021 Japanese S8 Fusion Arts Mew V 039/100 Holo PSA 10",
        52,
        sale_id="same",
    )
    old = sale(
        "Pokemon 2021 Japanese S8 Fusion Arts Mew V 039/100 Holo PSA 10",
        45,
        sold_at="2024-01-01T00:00:00Z",
        sale_id="old",
    )
    values = exact_point130_sales(
        parse_point130_sales({"sales": [recent, dict(recent), old]}),
        mew_identity(),
        target_currency="USD",
        fx=IdentityFX(),
        max_age_days=365,
        now=datetime(2026, 8, 29, tzinfo=UTC),
    )
    assert [value.value for value in values] == [52]


def test_130point_import_rejects_non_130point_provenance():
    payload = {"sales": [sale("Mew 039/100 PSA 10", 52)]}
    payload["sales"][0]["source_url"] = "https://example.test/sold"
    with pytest.raises(ValueError, match="130point"):
        parse_point130_sales(json.loads(json.dumps(payload)))
