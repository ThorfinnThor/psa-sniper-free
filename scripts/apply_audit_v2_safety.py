from __future__ import annotations

from pathlib import Path


def read(path: str) -> str:
    return Path(path).read_text(encoding="utf-8")


def write(path: str, text: str) -> None:
    Path(path).write_text(text, encoding="utf-8")


def replace_once(path: str, old: str, new: str, label: str) -> None:
    text = read(path)
    count = text.count(old)
    if count != 1:
        raise RuntimeError(f"{label}: erwartet 1 Treffer, gefunden {count} in {path}")
    write(path, text.replace(old, new, 1))


# ---------------------------------------------------------------------------
# PSA: ein bereits API-geprüfter Cert wird nicht bei jedem Lauf erneut abgefragt,
# nur weil einzelne Felder von PSA legitimerweise leer sind.
# ---------------------------------------------------------------------------
psa = "psa_sniper/psa.py"
replace_once(
    psa,
    '''def cert_needs_api_upgrade(cert: PSACertInfo | None) -> bool:\n    if cert is None:\n        return True\n    source = str(cert.data_source or "")\n    if "PSA Public API" not in source:\n        return True\n    return any(\n        value is None or value == ""\n        for value in (cert.grade, cert.subject, cert.card_number, cert.population)\n    )\n''',
    '''def cert_needs_api_upgrade(cert: PSACertInfo | None) -> bool:\n    if cert is None:\n        return True\n    # Sobald der Datensatz aus der Public API stammt, übernimmt die normale\n    # dynamische Cert-Cache-TTL spätere Refreshes. Leere PSA-Felder sind nicht\n    # automatisch ein Grund, dieselbe Cert in jedem Lauf erneut abzufragen.\n    return "PSA Public API" not in str(cert.data_source or "")\n''',
    "PSA API Upgrade nicht wiederholen",
)


# ---------------------------------------------------------------------------
# Scoring: untrusted Cert darf Listing-basierte Comps nicht vernichten;
# Cert-Inhalte dürfen Demand/Prio nur beeinflussen, wenn die Cert vertrauenswürdig ist.
# Nicht-EU-Zielangebote erhalten ein zusätzliches Sicherheits-Gate, weil Browse
# Importabgaben nicht vollständig als Checkout-Gesamtkosten liefert.
# ---------------------------------------------------------------------------
scoring = "psa_sniper/scoring.py"
replace_once(
    scoring,
    "import re\nfrom datetime import datetime, timezone\n",
    "import re\nfrom dataclasses import replace\nfrom datetime import datetime, timezone\n",
    "Scoring dataclasses replace",
)
replace_once(
    scoring,
    '''    demand_terms: list[str] | None = None,\n) -> ScoredHit:\n''',
    '''    demand_terms: list[str] | None = None,\n    import_risk_extra_edge: float = 0.0,\n    import_exempt_countries: list[str] | None = None,\n) -> ScoredHit:\n''',
    "Scoring Import-Risk Parameter",
)
replace_once(
    scoring,
    '''            cert.subject if cert else None,\n            cert.brand_title if cert else None,\n            cert.variety if cert else None,\n''',
    '''            cert.subject if cert and cert_trusted else None,\n            cert.brand_title if cert and cert_trusted else None,\n            cert.variety if cert and cert_trusted else None,\n''',
    "Demand nur vertrauenswürdige Cert",
)
old_market = '''    market = market_raw if cert_trusted else None\n    acquisition = listing.total_cost\n\n    if not listing.pure_auction and market and acquisition and market.money.value > 0:\n        discount_pct = 1.0 - acquisition.value / market.money.value\n        confidence = market.confidence.casefold()\n        required_edge = max(MIN_VERIFIED_PRICE_EDGE, float(market.required_edge or 0.10))\n'''
new_market = '''    market = market_raw\n    if not cert_trusted and market and market.market_type != "ebay_active_provisional":\n        # Listing-basierte eBay-Comps sind unabhängig von einer falschen Cert und\n        # dürfen als konservative Beobachtungs-Preisquelle erhalten bleiben.\n        market = None\n    acquisition = listing.total_cost\n\n    if not listing.pure_auction and market and acquisition and market.money.value > 0:\n        discount_pct = 1.0 - acquisition.value / market.money.value\n        confidence = market.confidence.casefold()\n        required_edge = max(MIN_VERIFIED_PRICE_EDGE, float(market.required_edge or 0.10))\n\n        country = str(listing.item_location_country or "").upper().strip()\n        exempt = {str(value).upper().strip() for value in (import_exempt_countries or []) if str(value).strip()}\n        extra_import_edge = max(0.0, float(import_risk_extra_edge or 0.0))\n        if country and exempt and country not in exempt and extra_import_edge > 0:\n            required_edge = min(0.95, required_edge + extra_import_edge)\n            market = replace(market, required_edge=required_edge)\n            label = (\n                f"Nicht-EU-/Import-Risiko ({country}): zusätzlich "\n                f"{extra_import_edge:.0%} Sicherheitsabstand erforderlich"\n            )\n            adjust(0, label)\n            warnings.append(label)\n'''
replace_once(scoring, old_market, new_market, "Scoring Import- und untrusted Marktlogik")


# ---------------------------------------------------------------------------
# Scanner: untrusted PSA-Marktwerte nie vor Listing-Fallback setzen; Import-Gate
# in das Scoring weiterreichen.
# ---------------------------------------------------------------------------
scanner = "psa_sniper/scanner.py"
replace_once(
    scanner,
    '''        market = _market_in_listing_currency(market_value_from_cert(cert), listing, fx)\n\n        if (\n            cert and cert.valid and market is None\n''',
    '''        cert_market_safe = bool(\n            cert and cert_candidate and _cert_safe_for_market(listing, cert_candidate, cert)\n        )\n        market = (\n            _market_in_listing_currency(market_value_from_cert(cert), listing, fx)\n            if cert_market_safe\n            else None\n        )\n\n        if (\n            cert and cert.valid and cert_market_safe and market is None\n''',
    "Scanner untrusted PSA Markt blockiert Listing nicht",
)
replace_once(
    scanner,
    '''            demand_terms=list(settings.get("demand_terms") or []),\n        )\n''',
    '''            demand_terms=list(settings.get("demand_terms") or []),\n            import_risk_extra_edge=float(settings.get("import_risk_extra_edge", 0.0)),\n            import_exempt_countries=list(settings.get("import_risk_exempt_countries") or []),\n        )\n''',
    "Scanner Import Gate Parameter",
)


# Live-Check übergibt Import-Regeln ebenfalls.
live = "psa_sniper/live_check.py"
replace_once(
    live,
    '''        demand_terms=list(settings.get("demand_terms") or []),\n    )\n''',
    '''        demand_terms=list(settings.get("demand_terms") or []),\n        import_risk_extra_edge=float(settings.get("import_risk_extra_edge", 0.0)),\n        import_exempt_countries=list(settings.get("import_risk_exempt_countries") or []),\n    )\n''',
    "Live Check Import Gate",
)

# Repricing übergibt Import-Regeln.
repricing = "psa_sniper/repricing.py"
replace_once(
    repricing,
    '''            market_value_listing_currency=market,\n            priority_terms=priority_terms,\n            demand_terms=demand_terms,\n        )\n''',
    '''            market_value_listing_currency=market,\n            priority_terms=priority_terms,\n            demand_terms=demand_terms,\n            import_risk_extra_edge=float(settings.get("import_risk_extra_edge", 0.0)),\n            import_exempt_countries=list(settings.get("import_risk_exempt_countries") or []),\n        )\n''',
    "Repricing Import Gate",
)

# PSA Backfill ebenfalls konsistent neu scoren.
backfill = "psa_sniper/psa_backfill.py"
replace_once(
    backfill,
    '''                priority_terms=list(settings.get("priority_terms") or []),\n                demand_terms=list(settings.get("demand_terms") or []),\n            )\n''',
    '''                priority_terms=list(settings.get("priority_terms") or []),\n                demand_terms=list(settings.get("demand_terms") or []),\n                import_risk_extra_edge=float(settings.get("import_risk_extra_edge", 0.0)),\n                import_exempt_countries=list(settings.get("import_risk_exempt_countries") or []),\n            )\n''',
    "PSA Backfill Import Gate",
)


# ---------------------------------------------------------------------------
# Alerts: ein Listing bleibt dedupliziert, kann aber bei einer echten materiellen
# Preis-/Edge-Verbesserung erneut alarmieren.
# ---------------------------------------------------------------------------
state = "psa_sniper/state.py"
replace_once(
    state,
    '''def mark_alerted(state: dict[str, Any], item_id: str, channels: dict[str, bool]) -> None:\n    state.setdefault("alerted", {})[item_id] = {"at": iso_z(utc_now()), "channels": channels}\n\n\ndef is_alerted(state: dict[str, Any], item_id: str) -> bool:\n    return item_id in dict(state.get("alerted", {}))\n\n\n''',
    '''def mark_alerted(\n    state: dict[str, Any],\n    item_id: str,\n    channels: dict[str, bool],\n    *,\n    hit: ScoredHit | None = None,\n) -> None:\n    row: dict[str, Any] = {"at": iso_z(utc_now()), "channels": channels}\n    if hit is not None:\n        total = hit.listing.total_cost or hit.listing.price\n        if total is not None:\n            row["total_cost"] = total.to_dict()\n        if hit.discount_pct is not None:\n            row["discount_pct"] = float(hit.discount_pct)\n        row["score"] = int(hit.score)\n    state.setdefault("alerted", {})[item_id] = row\n\n\ndef is_alerted(state: dict[str, Any], item_id: str) -> bool:\n    return item_id in dict(state.get("alerted", {}))\n\n\ndef should_alert(\n    state: dict[str, Any],\n    hit: ScoredHit,\n    *,\n    min_price_drop_pct: float = 0.10,\n    min_edge_improvement: float = 0.10,\n) -> bool:\n    previous = dict(state.get("alerted", {})).get(hit.listing.item_id)\n    if not isinstance(previous, dict):\n        return True\n\n    current_total = hit.listing.total_cost or hit.listing.price\n    old_total = previous.get("total_cost")\n    if current_total and isinstance(old_total, dict):\n        try:\n            old_value = float(old_total.get("value"))\n            old_currency = str(old_total.get("currency") or "").upper()\n            if (\n                old_value > 0\n                and old_currency == current_total.currency.upper()\n                and current_total.value <= old_value * (1.0 - max(0.0, min_price_drop_pct))\n            ):\n                return True\n        except (TypeError, ValueError):\n            pass\n\n    try:\n        old_edge = float(previous.get("discount_pct"))\n    except (TypeError, ValueError):\n        old_edge = None\n    if (\n        old_edge is not None\n        and hit.discount_pct is not None\n        and hit.discount_pct >= old_edge + max(0.0, min_edge_improvement)\n    ):\n        return True\n    return False\n\n\n''',
    "Alert Re-Arm State",
)

# Scanner imports/uses should_alert and records hit metadata.
replace_once(
    scanner,
    '''    save_state,\n    select_queries,\n    upsert_history,\n''',
    '''    save_state,\n    select_queries,\n    should_alert,\n    upsert_history,\n''',
    "Scanner should_alert import",
)
replace_once(
    scanner,
    '''    for hit in hits:\n        if is_alerted(state, hit.listing.item_id):\n            continue\n        statuses = notify(hit)\n        if not channels or any(statuses.values()):\n            mark_alerted(state, hit.listing.item_id, statuses or {"dashboard": True})\n''',
    '''    for hit in hits:\n        if not should_alert(\n            state, hit,\n            min_price_drop_pct=float(settings.get("alert_rearm_price_drop_pct", 0.10)),\n            min_edge_improvement=float(settings.get("alert_rearm_edge_improvement", 0.10)),\n        ):\n            continue\n        statuses = notify(hit)\n        if not channels or any(statuses.values()):\n            mark_alerted(state, hit.listing.item_id, statuses or {"dashboard": True}, hit=hit)\n''',
    "Scanner Alert Re-Arm",
)
# is_alerted import becomes unused; remove in scanner.
replace_once(scanner, "    is_alerted,\n", "", "Scanner is_alerted entfernen")

# Repricing dito.
replace_once(
    repricing,
    '''    is_alerted,\n    load_state,\n''',
    '''    load_state,\n''',
    "Repricing is_alerted entfernen",
)
replace_once(
    repricing,
    '''    save_state,\n)\n''',
    '''    save_state,\n    should_alert,\n)\n''',
    "Repricing should_alert import",
)
replace_once(
    repricing,
    '''    for hit in repriced_hits:\n        if is_alerted(state, hit.listing.item_id):\n            continue\n        statuses = notify(hit)\n        if not channels or any(statuses.values()):\n            mark_alerted(state, hit.listing.item_id, statuses or {"dashboard": True})\n''',
    '''    for hit in repriced_hits:\n        if not should_alert(\n            state, hit,\n            min_price_drop_pct=float(settings.get("alert_rearm_price_drop_pct", 0.10)),\n            min_edge_improvement=float(settings.get("alert_rearm_edge_improvement", 0.10)),\n        ):\n            continue\n        statuses = notify(hit)\n        if not channels or any(statuses.values()):\n            mark_alerted(state, hit.listing.item_id, statuses or {"dashboard": True}, hit=hit)\n''',
    "Repricing Alert Re-Arm",
)


# ---------------------------------------------------------------------------
# Konfiguration: EU-Ausnahmen + konservativer Import-Puffer, Alert-Rearm.
# ---------------------------------------------------------------------------
config = "config/settings.json"
text = read(config)
insert_after = '  "market_listing_fallback_min_preliminary_score": 7,\n'
if insert_after not in text:
    raise RuntimeError("Config Import Marker fehlt")
addition = '''  "import_risk_extra_edge": 0.15,\n  "import_risk_exempt_countries": [\n    "AT", "BE", "BG", "HR", "CY", "CZ", "DK", "EE", "FI", "FR",\n    "DE", "GR", "HU", "IE", "IT", "LV", "LT", "LU", "MT", "NL",\n    "PL", "PT", "RO", "SK", "SI", "ES", "SE"\n  ],\n  "alert_rearm_price_drop_pct": 0.10,\n  "alert_rearm_edge_improvement": 0.10,\n'''
text = text.replace(insert_after, insert_after + addition, 1)
write(config, text)


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------
path = Path("tests/test_scoring.py")
text = path.read_text(encoding="utf-8")
text += '''\n\ndef test_untrusted_cert_keeps_independent_listing_comp_indicator():\n    listing = Listing(\n        item_id="listing-comp-survives", title="2021 Bundesliga PSA 10 #99",\n        url="https://example.test/listing-comp", price=Money(70, "EUR"),\n        created_at=datetime.now(timezone.utc), buying_options=["FIXED_PRICE"],\n    )\n    cert = _cert()\n    market = MarketValue(\n        Money(100, "EUR"), "eBay Listing-Comps", "niedrig", 4,\n        market_type="ebay_active_provisional", required_edge=.25, unique_sellers=4,\n    )\n    hit = score_hit(\n        listing, cert_number=cert.cert_number, cert_source="Item-Specifics", cert=cert,\n        market_value_listing_currency=market, priority_terms=[], demand_terms=[],\n    )\n    assert hit.cert_trusted is False\n    assert hit.market_value is not None\n    assert hit.market_value.market_type == "ebay_active_provisional"\n    assert hit.price_status == "weak_indicator"\n\n\ndef test_non_eu_listing_requires_extra_import_edge():\n    listing = Listing(\n        item_id="import", title="2021 Bundesliga PSA 10 #16",\n        url="https://example.test/import", price=Money(70, "EUR"),\n        created_at=datetime.now(timezone.utc), buying_options=["FIXED_PRICE"],\n        item_location_country="US",\n    )\n    market = MarketValue(\n        Money(100, "EUR"), "eBay", "mittel", 5,\n        market_type="ebay_active", required_edge=.20, unique_sellers=4,\n    )\n    hit = score_hit(\n        listing, cert_number="67205095", cert_source="Titel", cert=_cert(),\n        market_value_listing_currency=market, priority_terms=[], demand_terms=[],\n        import_risk_extra_edge=.15, import_exempt_countries=["DE", "FR"],\n    )\n    assert hit.market_value.required_edge == .35\n    assert hit.price_status == "no_edge"\n    assert any("import-risiko" in warning.casefold() for warning in hit.warnings)\n\n\ndef test_eu_listing_keeps_base_price_gate():\n    listing = Listing(\n        item_id="eu", title="2021 Bundesliga PSA 10 #16",\n        url="https://example.test/eu", price=Money(75, "EUR"),\n        created_at=datetime.now(timezone.utc), buying_options=["FIXED_PRICE"],\n        item_location_country="DE",\n    )\n    market = MarketValue(\n        Money(100, "EUR"), "eBay", "mittel", 5,\n        market_type="ebay_active", required_edge=.20, unique_sellers=4,\n    )\n    hit = score_hit(\n        listing, cert_number="67205095", cert_source="Titel", cert=_cert(),\n        market_value_listing_currency=market, priority_terms=[], demand_terms=[],\n        import_risk_extra_edge=.15, import_exempt_countries=["DE", "FR"],\n    )\n    assert hit.market_value.required_edge == .20\n    assert hit.price_status == "verified_edge"\n'''
path.write_text(text, encoding="utf-8")

Path("tests/test_alert_rearm.py").write_text('''from datetime import datetime, timezone\n\nfrom psa_sniper.models import Listing, Money, ScoredHit\nfrom psa_sniper.state import default_state, mark_alerted, should_alert\n\n\ndef hit(price=80, edge=.25):\n    listing = Listing(\n        item_id="x", title="PSA 10", url="https://example.test/x",\n        price=Money(price, "EUR"), created_at=datetime.now(timezone.utc),\n        buying_options=["FIXED_PRICE"],\n    )\n    return ScoredHit(listing=listing, score=13, reasons=[], discount_pct=edge, price_status="verified_edge")\n\n\ndef test_first_hit_alerts_and_unchanged_hit_does_not_repeat():\n    state = default_state()\n    first = hit()\n    assert should_alert(state, first) is True\n    mark_alerted(state, "x", {"dashboard": True}, hit=first)\n    assert should_alert(state, hit()) is False\n\n\ndef test_material_price_drop_rearms_alert():\n    state = default_state()\n    first = hit(price=80, edge=.25)\n    mark_alerted(state, "x", {"dashboard": True}, hit=first)\n    assert should_alert(state, hit(price=70, edge=.25), min_price_drop_pct=.10) is True\n\n\ndef test_material_edge_improvement_rearms_alert():\n    state = default_state()\n    first = hit(price=80, edge=.20)\n    mark_alerted(state, "x", {"dashboard": True}, hit=first)\n    assert should_alert(state, hit(price=80, edge=.31), min_edge_improvement=.10) is True\n''', encoding="utf-8")

# PSA upgrade missing field should not hammer API once source is API.
path = Path("tests/test_psa_backfill.py")
text = path.read_text(encoding="utf-8")
text += '''\n\ndef test_api_sourced_cert_with_missing_population_waits_for_normal_cache_ttl():\n    cert = PSACertInfo(\n        cert_number="2", valid=True, grade="10", subject="A", card_number="1",\n        population=None, data_source="PSA Public API",\n    )\n    assert cert_needs_api_upgrade(cert) is False\n'''
path.write_text(text, encoding="utf-8")

# Config safety test.
path = Path("tests/test_quality_config.py")
text = path.read_text(encoding="utf-8")
text += '''\n\ndef test_import_risk_defaults_are_conservative_for_german_buyer():\n    settings = json.loads(Path("config/settings.json").read_text(encoding="utf-8"))\n    assert settings["import_risk_extra_edge"] >= .10\n    assert "DE" in settings["import_risk_exempt_countries"]\n    assert "US" not in settings["import_risk_exempt_countries"]\n'''
path.write_text(text, encoding="utf-8")

print("Audit-v2 safety patch applied")
