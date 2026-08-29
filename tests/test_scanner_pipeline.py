from datetime import UTC, datetime

from psa_sniper import scanner
from psa_sniper.models import Listing, Money
from psa_sniper.state import default_state


class FakeEbay:
    def __init__(
        self,
        events: list[str],
        details: dict[str, Listing],
        summaries: list[Listing] | None = None,
    ) -> None:
        self.events = events
        self.details = details
        self.summaries = summaries
        self.calls_made = 0

    def search(self, query, *, limit, started_after, offset=0):
        self.calls_made += 1
        if started_after is not None:
            self.events.append("discovery")
            if self.summaries is not None:
                return self.summaries
            now = datetime.now(UTC)
            return [
                Listing(
                    item_id="weak",
                    title="PSA 10 Generic #25",
                    url="https://example.test/weak",
                    price=Money(100, "EUR"),
                    created_at=now,
                    buying_options=["FIXED_PRICE"],
                ),
                Listing(
                    item_id="strong",
                    title="PSA 10 Pikachu Collector #99",
                    url="https://example.test/strong",
                    price=Money(100, "EUR"),
                    created_at=now,
                    buying_options=["FIXED_PRICE"],
                ),
            ]
        self.events.append(f"comp:{query}")
        return []

    def get_item(self, item_id):
        self.calls_made += 1
        self.events.append(f"detail:{item_id}")
        return self.details[item_id]


class FakePSA:
    calls_made = 0
    api_successes = 0
    web_rate_limited = False

    def validate_access_token(self):
        return "nicht_konfiguriert"


class FakeFX:
    def refresh(self):
        return None

    def convert(self, money, currency):
        return money if money.currency == currency else None


def test_run_scan_loads_all_details_before_prioritized_comp_search(monkeypatch, tmp_path):
    now = datetime.now(UTC)
    details = {
        "weak": Listing(
            item_id="weak",
            title="PSA 10 Generic #25",
            url="https://example.test/weak",
            price=Money(100, "EUR"),
            created_at=now,
            buying_options=["FIXED_PRICE"],
        ),
        "strong": Listing(
            item_id="strong",
            title="PSA 10 Pikachu #99",
            url="https://example.test/strong",
            price=Money(100, "EUR"),
            created_at=now,
            buying_options=["FIXED_PRICE"],
        ),
    }
    events: list[str] = []
    fake_ebay = FakeEbay(events, details)
    settings = {
        "max_search_calls_per_run": 1,
        "max_results_per_query": 10,
        "max_detail_calls_per_run": 2,
        "max_ebay_calls_per_run": 10,
        "max_market_comp_calls_per_run": 1,
        "market_comp_search_limit": 10,
        "market_listing_fallback_min_preliminary_score": 7,
        "market_active_required_edge": 0.20,
        "max_psa_market_web_calls_per_run": 0,
        "max_psa_calls_per_run": 0,
        "max_ocr_items_per_run": 0,
        "run_window_minutes": 60,
        "processed_cooldown_minutes": 0,
        "minimum_price": 0,
        "maximum_price": 1_000,
        "include_auctions": False,
        "priority_terms": [],
        "demand_terms": ["pikachu"],
        "dashboard_min_score": 4,
        "hit_threshold": 11,
        "max_hits_per_run": 5,
        "max_run_results_per_run": 10,
        "run_history_max_items": 10,
    }

    monkeypatch.setenv("EBAY_CLIENT_ID", "test-id")
    monkeypatch.setenv("EBAY_CLIENT_SECRET", "test-secret")
    monkeypatch.setattr(scanner, "load_settings", lambda: settings)
    monkeypatch.setattr(scanner, "load_queries", lambda: ["PSA 10 cards"])
    monkeypatch.setattr(scanner, "state_path", lambda: tmp_path / "state.json")
    monkeypatch.setattr(scanner, "load_state", lambda _path: default_state())
    monkeypatch.setattr(scanner, "prune_state", lambda state, _settings: state)
    monkeypatch.setattr(scanner, "save_state", lambda _path, _state: None)
    monkeypatch.setattr(scanner, "write_reports", lambda *_args: None)
    monkeypatch.setattr(scanner, "configured_channels", lambda: [])
    monkeypatch.setattr(scanner, "ocr_enabled", lambda: False)
    monkeypatch.setattr(scanner, "EbayClient", lambda *_args, **_kwargs: fake_ebay)
    monkeypatch.setattr(scanner, "PSAClient", lambda **_kwargs: FakePSA())
    monkeypatch.setattr(scanner, "FXRates", FakeFX)

    assert scanner.run_scan() == 0

    first_comp = next(index for index, event in enumerate(events) if event.startswith("comp:"))
    assert events[:first_comp] == ["discovery", "detail:weak", "detail:strong"]
    assert "pikachu" in events[first_comp]


def test_run_scan_gives_each_candidate_one_comp_search_before_fallbacks(
    monkeypatch,
    tmp_path,
):
    now = datetime.now(UTC)
    details = {
        "weak": Listing(
            item_id="weak",
            title="PSA 10 Generic #25",
            url="https://example.test/weak",
            price=Money(100, "EUR"),
            created_at=now,
            buying_options=["FIXED_PRICE"],
        ),
        "medium": Listing(
            item_id="medium",
            title="PSA 10 Eevee #42",
            url="https://example.test/medium",
            price=Money(100, "EUR"),
            created_at=now,
            buying_options=["FIXED_PRICE"],
        ),
        "strong": Listing(
            item_id="strong",
            title="PSA 10 Pikachu #99",
            url="https://example.test/strong",
            price=Money(100, "EUR"),
            created_at=now,
            buying_options=["FIXED_PRICE"],
        ),
    }
    events: list[str] = []
    fake_ebay = FakeEbay(events, details, list(details.values()))
    settings = {
        "max_search_calls_per_run": 1,
        "max_results_per_query": 10,
        "max_detail_calls_per_run": 3,
        "max_ebay_calls_per_run": 20,
        "max_market_comp_calls_per_run": 3,
        "market_comp_search_limit": 10,
        "market_listing_fallback_min_preliminary_score": 7,
        "market_active_required_edge": 0.20,
        "max_psa_market_web_calls_per_run": 0,
        "max_psa_calls_per_run": 0,
        "max_ocr_items_per_run": 0,
        "run_window_minutes": 60,
        "processed_cooldown_minutes": 0,
        "minimum_price": 0,
        "maximum_price": 1_000,
        "include_auctions": False,
        "priority_terms": [],
        "demand_terms": ["pikachu"],
        "dashboard_min_score": 4,
        "hit_threshold": 11,
        "max_hits_per_run": 5,
        "max_run_results_per_run": 10,
        "run_history_max_items": 10,
    }

    monkeypatch.setenv("EBAY_CLIENT_ID", "test-id")
    monkeypatch.setenv("EBAY_CLIENT_SECRET", "test-secret")
    monkeypatch.setattr(scanner, "load_settings", lambda: settings)
    monkeypatch.setattr(scanner, "load_queries", lambda: ["PSA 10 cards"])
    monkeypatch.setattr(scanner, "state_path", lambda: tmp_path / "state.json")
    monkeypatch.setattr(scanner, "load_state", lambda _path: default_state())
    monkeypatch.setattr(scanner, "prune_state", lambda state, _settings: state)
    monkeypatch.setattr(scanner, "save_state", lambda _path, _state: None)
    monkeypatch.setattr(scanner, "write_reports", lambda *_args: None)
    monkeypatch.setattr(scanner, "configured_channels", lambda: [])
    monkeypatch.setattr(scanner, "ocr_enabled", lambda: False)
    monkeypatch.setattr(scanner, "EbayClient", lambda *_args, **_kwargs: fake_ebay)
    monkeypatch.setattr(scanner, "PSAClient", lambda **_kwargs: FakePSA())
    monkeypatch.setattr(scanner, "FXRates", FakeFX)

    assert scanner.run_scan() == 0

    comp_events = [event for event in events if event.startswith("comp:")]
    assert len(comp_events) == 3
    assert "pikachu" in comp_events[0]
    assert {number for number in ("25", "42", "99") if any(number in event for event in comp_events)} == {
        "25",
        "42",
        "99",
    }
