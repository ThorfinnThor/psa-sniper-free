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
# PSA: keine versteckten urllib3-Retries. Jeder echte HTTP-Versuch entspricht
# exakt einem calls_made. Nur HTTP 500 beim initialen Token-Probe wird sofort
# als dokumentierter Credentials-Verdacht behandelt; 502/503/504 brauchen zwei
# aufeinanderfolgende Fehler, bevor der Circuit Breaker öffnet.
# ---------------------------------------------------------------------------
psa = "psa_sniper/psa.py"
replace_once(
    psa,
    '''        # PSA documents HTTP 500 as commonly meaning invalid credentials. Do not\n        # blindly retry 500; it is handled by the circuit breaker below.\n        retry = Retry(\n            total=2,\n            connect=2,\n            read=2,\n            backoff_factor=1.0,\n            status_forcelist=(502, 503, 504),\n            allowed_methods=frozenset({"GET"}),\n            respect_retry_after_header=True,\n        )\n        self.session.mount("https://", HTTPAdapter(max_retries=retry))\n''',
    '''        # Keine versteckten HTTP-Retries: PSA-Budget und Diagnose sollen\n        # tatsächliche Requests zählen. Circuit-Breaker-Entscheidungen passieren\n        # ausschließlich in _get_api().\n        retry = Retry(total=0, connect=0, read=0, redirect=0, status=0)\n        self.session.mount("https://", HTTPAdapter(max_retries=retry))\n''',
    "PSA versteckte Retries deaktivieren",
)
replace_once(
    psa,
    '''        if response.status_code >= 500:\n            self.api_auth_status = "servicefehler"\n            self.api_failure_streak += 1\n            # PSA says 500 usually indicates invalid credentials. On the initial\n            # token validation, disable immediately; during a run, two consecutive\n            # 5xx responses open the circuit breaker.\n            if was_initial_validation or self.api_failure_streak >= 2:\n                self._disable_api("server_or_credentials")\n            return None\n''',
    '''        if response.status_code >= 500:\n            self.api_auth_status = "servicefehler"\n            self.api_failure_streak += 1\n            # PSA dokumentiert speziell HTTP 500 als häufiges Credentials-Signal.\n            # 502/503/504 sind dagegen klassische transiente Gateway/Service-Fehler\n            # und dürfen einen frisch erzeugten Token nicht nach nur einem Fehler\n            # permanent für den Lauf deaktivieren.\n            if (\n                (was_initial_validation and response.status_code == 500)\n                or self.api_failure_streak >= 2\n            ):\n                reason = (\n                    "server_or_credentials"\n                    if response.status_code == 500\n                    else "service_unavailable"\n                )\n                self._disable_api(reason)\n            return None\n''',
    "PSA 500 vs 503 Circuit Breaker",
)


# ---------------------------------------------------------------------------
# Preis-Gate: unbekannte Versandkosten sind kein kostenloser Versand. Wenn eBay
# keinen konkreten shipping-Wert liefert, wird ein zusätzlicher Sicherheitsabstand
# verlangt. Free shipping ist Money(0) und bleibt unbestraft.
# ---------------------------------------------------------------------------
scoring = "psa_sniper/scoring.py"
replace_once(
    scoring,
    '''    import_risk_extra_edge: float = 0.0,\n    import_exempt_countries: list[str] | None = None,\n) -> ScoredHit:\n''',
    '''    import_risk_extra_edge: float = 0.0,\n    import_exempt_countries: list[str] | None = None,\n    unknown_shipping_extra_edge: float = 0.0,\n) -> ScoredHit:\n''',
    "Scoring unbekannter Versand Parameter",
)
old = '''        if country and exempt and country not in exempt and extra_import_edge > 0:\n            required_edge = min(0.95, required_edge + extra_import_edge)\n            market = replace(market, required_edge=required_edge)\n            label = (\n                f"Nicht-EU-/Import-Risiko ({country}): zusätzlich "\n                f"{extra_import_edge:.0%} Sicherheitsabstand erforderlich"\n            )\n            adjust(0, label)\n            warnings.append(label)\n\n        if confidence in {"hoch", "mittel"} and discount_pct >= required_edge:\n'''
new = '''        if country and exempt and country not in exempt and extra_import_edge > 0:\n            required_edge = min(0.95, required_edge + extra_import_edge)\n            label = (\n                f"Nicht-EU-/Import-Risiko ({country}): zusätzlich "\n                f"{extra_import_edge:.0%} Sicherheitsabstand erforderlich"\n            )\n            adjust(0, label)\n            warnings.append(label)\n\n        shipping_extra = max(0.0, float(unknown_shipping_extra_edge or 0.0))\n        if listing.shipping is None and shipping_extra > 0:\n            required_edge = min(0.95, required_edge + shipping_extra)\n            label = (\n                "Versandkosten nicht sicher bestimmt: zusätzlich "\n                f"{shipping_extra:.0%} Sicherheitsabstand erforderlich"\n            )\n            adjust(0, label)\n            warnings.append(label)\n\n        if required_edge != float(market.required_edge or 0.10):\n            market = replace(market, required_edge=required_edge)\n\n        if confidence in {"hoch", "mittel"} and discount_pct >= required_edge:\n'''
replace_once(scoring, old, new, "Scoring Versand Sicherheitsabstand")

# Alle produktiven score_hit-Aufrufer erhalten denselben Parameter.
scanner = "psa_sniper/scanner.py"
replace_once(
    scanner,
    '''            import_risk_extra_edge=float(settings.get("import_risk_extra_edge", 0.0)),\n            import_exempt_countries=list(settings.get("import_risk_exempt_countries") or []),\n        )\n''',
    '''            import_risk_extra_edge=float(settings.get("import_risk_extra_edge", 0.0)),\n            import_exempt_countries=list(settings.get("import_risk_exempt_countries") or []),\n            unknown_shipping_extra_edge=float(settings.get("unknown_shipping_extra_edge", 0.0)),\n        )\n''',
    "Scanner unbekannter Versand",
)
live = "psa_sniper/live_check.py"
replace_once(
    live,
    '''        import_risk_extra_edge=float(settings.get("import_risk_extra_edge", 0.0)),\n        import_exempt_countries=list(settings.get("import_risk_exempt_countries") or []),\n    )\n''',
    '''        import_risk_extra_edge=float(settings.get("import_risk_extra_edge", 0.0)),\n        import_exempt_countries=list(settings.get("import_risk_exempt_countries") or []),\n        unknown_shipping_extra_edge=float(settings.get("unknown_shipping_extra_edge", 0.0)),\n    )\n''',
    "Live Check unbekannter Versand",
)
repricing = "psa_sniper/repricing.py"
replace_once(
    repricing,
    '''            import_risk_extra_edge=float(settings.get("import_risk_extra_edge", 0.0)),\n            import_exempt_countries=list(settings.get("import_risk_exempt_countries") or []),\n        )\n''',
    '''            import_risk_extra_edge=float(settings.get("import_risk_extra_edge", 0.0)),\n            import_exempt_countries=list(settings.get("import_risk_exempt_countries") or []),\n            unknown_shipping_extra_edge=float(settings.get("unknown_shipping_extra_edge", 0.0)),\n        )\n''',
    "Repricing unbekannter Versand",
)
backfill = "psa_sniper/psa_backfill.py"
replace_once(
    backfill,
    '''                import_risk_extra_edge=float(settings.get("import_risk_extra_edge", 0.0)),\n                import_exempt_countries=list(settings.get("import_risk_exempt_countries") or []),\n            )\n''',
    '''                import_risk_extra_edge=float(settings.get("import_risk_extra_edge", 0.0)),\n                import_exempt_countries=list(settings.get("import_risk_exempt_countries") or []),\n                unknown_shipping_extra_edge=float(settings.get("unknown_shipping_extra_edge", 0.0)),\n            )\n''',
    "PSA Backfill unbekannter Versand",
)

# Konfiguration.
config = "config/settings.json"
text = read(config)
marker = '  "import_risk_extra_edge": 0.15,\n'
if marker not in text:
    raise RuntimeError("Unknown-shipping Config-Marker fehlt")
text = text.replace(marker, marker + '  "unknown_shipping_extra_edge": 0.10,\n', 1)
write(config, text)


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------
path = Path("tests/test_psa.py")
text = path.read_text(encoding="utf-8")
text += '''\n\ndef test_initial_psa_503_is_transient_and_keeps_token(monkeypatch):\n    client = PSAClient(access_token="fresh-token", web_fallback=False, delay_seconds=0, max_calls=3)\n    monkeypatch.setattr(client.session, "get", lambda *args, **kwargs: _response(503))\n    assert client.validate_access_token() == "servicefehler"\n    assert client.access_token == "fresh-token"\n    assert client.api_disabled_reason is None\n    assert client.calls_made == 1\n\n\ndef test_second_psa_503_opens_transient_service_circuit(monkeypatch):\n    client = PSAClient(access_token="fresh-token", web_fallback=False, delay_seconds=0, max_calls=4)\n    monkeypatch.setattr(client.session, "get", lambda *args, **kwargs: _response(503))\n    assert client.validate_access_token() == "servicefehler"\n    assert client.access_token is not None\n    assert client.get_api_cert("79959648") is None\n    assert client.access_token is None\n    assert client.api_disabled_reason == "service_unavailable"\n    assert client.calls_made == 2\n'''
path.write_text(text, encoding="utf-8")

path = Path("tests/test_scoring.py")
text = path.read_text(encoding="utf-8")
text += '''\n\ndef test_unknown_shipping_requires_extra_edge():\n    listing = Listing(\n        item_id="shipping-unknown", title="2021 Bundesliga PSA 10 #16",\n        url="https://example.test/shipping-unknown", price=Money(75, "EUR"), shipping=None,\n        created_at=datetime.now(timezone.utc), buying_options=["FIXED_PRICE"],\n        item_location_country="DE",\n    )\n    market = MarketValue(\n        Money(100, "EUR"), "eBay", "mittel", 5,\n        market_type="ebay_active", required_edge=.20, unique_sellers=4,\n    )\n    hit = score_hit(\n        listing, cert_number="67205095", cert_source="Titel", cert=_cert(),\n        market_value_listing_currency=market, priority_terms=[], demand_terms=[],\n        import_risk_extra_edge=.15, import_exempt_countries=["DE"],\n        unknown_shipping_extra_edge=.10,\n    )\n    assert hit.market_value.required_edge == .30\n    assert hit.price_status == "no_edge"\n    assert any("versandkosten nicht sicher" in warning.casefold() for warning in hit.warnings)\n\n\ndef test_explicit_free_shipping_has_no_unknown_shipping_penalty():\n    listing = Listing(\n        item_id="free-shipping", title="2021 Bundesliga PSA 10 #16",\n        url="https://example.test/free", price=Money(75, "EUR"), shipping=Money(0, "EUR"),\n        created_at=datetime.now(timezone.utc), buying_options=["FIXED_PRICE"],\n        item_location_country="DE",\n    )\n    market = MarketValue(\n        Money(100, "EUR"), "eBay", "mittel", 5,\n        market_type="ebay_active", required_edge=.20, unique_sellers=4,\n    )\n    hit = score_hit(\n        listing, cert_number="67205095", cert_source="Titel", cert=_cert(),\n        market_value_listing_currency=market, priority_terms=[], demand_terms=[],\n        import_risk_extra_edge=.15, import_exempt_countries=["DE"],\n        unknown_shipping_extra_edge=.10,\n    )\n    assert hit.market_value.required_edge == .20\n    assert hit.price_status == "verified_edge"\n'''
path.write_text(text, encoding="utf-8")

path = Path("tests/test_quality_config.py")
text = path.read_text(encoding="utf-8")
text += '''\n\ndef test_unknown_shipping_has_nonzero_safety_margin():\n    settings = json.loads(Path("config/settings.json").read_text(encoding="utf-8"))\n    assert settings["unknown_shipping_extra_edge"] >= .05\n'''
path.write_text(text, encoding="utf-8")

print("Audit-v2 operations patch applied")
