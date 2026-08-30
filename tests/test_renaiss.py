from datetime import UTC, datetime

import pytest

from psa_sniper.identity import PricingIdentity
from psa_sniper.models import Money
from psa_sniper.renaiss import RenaissClient, RenaissError, build_renaiss_query


class IdentityFX:
    def convert(self, money, target_currency):
        if money.currency == "USD" and target_currency == "EUR":
            return Money(money.value * 0.85, "EUR")
        return None


class Response:
    def __init__(self, data, status_code=200):
        self.data = data
        self.status_code = status_code

    def json(self):
        return self.data


class Session:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []

    def get(self, url, **kwargs):
        self.calls.append((url, kwargs))
        return self.responses.pop(0)


def mew_identity():
    return PricingIdentity(
        card_number="039/100",
        subjects=("mew",),
        terms=("mew", "s8"),
        set_code="S8",
        language="JP",
    )


def mew_result(**updates):
    row = {
        "id": "mew-s8-39-jp-psa10",
        "name": "Mew V",
        "setCode": "S8",
        "cardNumber": "39",
        "language": "Japanese",
        "company": "PSA",
        "gradeLabel": "PSA 10",
        "priceUsdCents": 6022,
        "confidence": "medium",
        "lastSaleAt": "2026-08-20T00:00:00Z",
        "href": "/card/pokemon/fusion-arts/39-mew-v-psa-10",
    }
    row.update(updates)
    return row


def test_query_contains_exact_psa10_identity():
    assert build_renaiss_query(mew_identity()) == "mew S8 039/100 Japanese PSA 10"


def test_exact_result_creates_medium_psa10_market():
    session = Session([Response({"results": [mew_result()]})])
    client = RenaissClient(session=session, max_calls=2)

    match = client.market_for_identity(
        mew_identity(),
        target_currency="EUR",
        fx=IdentityFX(),
        max_sale_age_days=365,
    )

    assert match is not None
    assert match.market.market_type == "renaiss_fmv"
    assert match.market.confidence == "mittel"
    assert match.market.money.value == pytest.approx(51.187)
    assert match.market.required_edge == 0.15
    assert match.item_id == "mew-s8-39-jp-psa10"
    assert client.calls_made == 1


@pytest.mark.parametrize(
    "updates",
    [
        {"setCode": "S9"},
        {"cardNumber": "106"},
        {"language": "English"},
        {"name": "Mewtwo V"},
        {"gradeLabel": "PSA 9"},
        {"company": "CGC"},
    ],
)
def test_wrong_identity_or_grade_is_rejected(updates):
    client = RenaissClient(
        session=Session([Response({"results": [mew_result(**updates)]})]),
        max_calls=1,
    )
    assert client.market_for_identity(
        mew_identity(),
        target_currency="EUR",
        fx=IdentityFX(),
    ) is None


def test_ambiguous_exact_printings_are_rejected():
    client = RenaissClient(
        session=Session([
            Response({"results": [mew_result(), mew_result(id="second-printing")]})
        ]),
        max_calls=1,
    )
    assert client.market_for_identity(
        mew_identity(),
        target_currency="EUR",
        fx=IdentityFX(),
    ) is None


def test_old_sale_and_rate_limit_are_safe_failures():
    old = mew_result(lastSaleAt="2024-01-01T00:00:00Z")
    client = RenaissClient(session=Session([Response({"results": [old]})]), max_calls=1)
    assert client.market_for_identity(
        mew_identity(),
        target_currency="EUR",
        fx=IdentityFX(),
        max_sale_age_days=30,
    ) is None

    limited = RenaissClient(session=Session([Response({}, status_code=429)]), max_calls=1)
    with pytest.raises(RenaissError, match="Rate-Limit"):
        limited.market_for_identity(mew_identity(), target_currency="EUR", fx=IdentityFX())
    assert limited.rate_limited is True


def test_price_without_sale_or_update_timestamp_is_rejected():
    row = mew_result(lastSaleAt=None)
    row.pop("updatedAt", None)
    client = RenaissClient(
        session=Session([Response({"results": [row]})]),
        max_calls=1,
    )
    assert client.market_for_identity(
        mew_identity(),
        target_currency="EUR",
        fx=IdentityFX(),
    ) is None


def test_market_age_can_be_evaluated_deterministically(monkeypatch):
    from psa_sniper import renaiss

    monkeypatch.setattr(renaiss, "utc_now", lambda: datetime(2026, 8, 30, tzinfo=UTC))
    client = RenaissClient(
        session=Session([Response({"results": [mew_result(lastSaleAt="2026-08-01T00:00:00Z")]})]),
        max_calls=1,
    )
    assert client.market_for_identity(
        mew_identity(),
        target_currency="EUR",
        fx=IdentityFX(),
        max_sale_age_days=30,
    ) is not None
