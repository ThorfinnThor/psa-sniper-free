from pathlib import Path


def replace_required(path: str, old: str, new: str, label: str, count: int | None = 1) -> None:
    p = Path(path)
    text = p.read_text(encoding="utf-8")
    found = text.count(old)
    if found == 0:
        raise RuntimeError(f"{label}: Suchtext nicht gefunden in {path}")
    if count is not None and found != count:
        raise RuntimeError(f"{label}: erwartet {count}, gefunden {found} in {path}")
    p.write_text(text.replace(old, new), encoding="utf-8")


scanner = "psa_sniper/scanner.py"

old = '''    }.get(status, "UNBEKANNT")\n\n\ndef run_scan() -> int:\n'''
new = '''    }.get(status, "UNBEKANNT")\n\n\ndef _new_price_diagnostics() -> dict[str, int]:\n    return {\n        "OhnePreis": 0,\n        "UnterGate": 0,\n        "KeineIdentitaet": 0,\n        "KeineSuchtreffer": 0,\n        "KeineExaktenComps": 0,\n        "ZuWenigeComps": 0,\n        "Budget": 0,\n        "Suchfehler": 0,\n        "KeinZielpreis": 0,\n        "Sonstiges": 0,\n        "Schwach": 0,\n        "SchwachPSAEstimate": 0,\n        "SchwachComps": 0,\n        "SchwachVerkaeufer": 0,\n        "SchwachStreuung": 0,\n        "SchwachIdentitaet": 0,\n    }\n\n\ndef _classify_price_gap(\n    market: MarketValue | None,\n    *,\n    target_available: bool,\n    preliminary: int,\n    min_preliminary: int,\n    identity_available: bool,\n    search_attempted: bool,\n    search_rows: int,\n    exact_matches: int,\n    budget_blocked: bool,\n    search_error: bool,\n) -> str | None:\n    if market is not None:\n        return None\n    if not target_available:\n        return "KeinZielpreis"\n    if preliminary < min_preliminary:\n        return "UnterGate"\n    if budget_blocked:\n        return "Budget"\n    if search_error:\n        return "Suchfehler"\n    if not identity_available:\n        return "KeineIdentitaet"\n    if search_attempted and search_rows <= 0:\n        return "KeineSuchtreffer"\n    if search_attempted and exact_matches <= 0:\n        return "KeineExaktenComps"\n    if search_attempted and exact_matches < 3:\n        return "ZuWenigeComps"\n    return "Sonstiges"\n\n\ndef _weak_market_diagnostics(market: MarketValue | None) -> list[str]:\n    if market is None or market.confidence.casefold() != "niedrig":\n        return []\n    keys = ["Schwach"]\n    if market.market_type == "psa_estimate":\n        keys.append("SchwachPSAEstimate")\n        return keys\n    if market.market_type not in {"ebay_active", "ebay_active_provisional"}:\n        return keys\n    if int(market.sample_size or 0) < 3:\n        keys.append("SchwachComps")\n    if market.unique_sellers is not None and int(market.unique_sellers) < 3:\n        keys.append("SchwachVerkaeufer")\n    if market.dispersion is not None and float(market.dispersion) > 0.35:\n        keys.append("SchwachStreuung")\n    if market.market_type == "ebay_active_provisional" or len(keys) == 1:\n        keys.append("SchwachIdentitaet")\n    return keys\n\n\ndef _price_diag_note(values: dict[str, int]) -> str:\n    order = (\n        "OhnePreis", "UnterGate", "KeineIdentitaet", "KeineSuchtreffer",\n        "KeineExaktenComps", "ZuWenigeComps", "Budget", "Suchfehler",\n        "KeinZielpreis", "Sonstiges", "Schwach", "SchwachPSAEstimate",\n        "SchwachComps", "SchwachVerkaeufer", "SchwachStreuung",\n        "SchwachIdentitaet",\n    )\n    return "PriceDiag: " + "; ".join(f"{key}={int(values.get(key, 0))}" for key in order)\n\n\ndef run_scan() -> int:\n'''
replace_required(scanner, old, new, "Diagnose-Helfer")

old = '''    psa_market_web_calls = 0\n    psa_market_web_min_prelim = int(settings.get("psa_market_web_min_preliminary_score", 8))\n\n    for summary in candidates:\n'''
new = '''    psa_market_web_calls = 0\n    psa_market_web_min_prelim = int(settings.get("psa_market_web_min_preliminary_score", 8))\n    price_diag = _new_price_diagnostics()\n\n    for summary in candidates:\n'''
replace_required(scanner, old, new, "Diagnose-Initialisierung")

old = '''        listing = _merge_listing(summary, detail)\n        cert_candidate: CertCandidate | None = extract_cert_from_aspects(listing)\n'''
new = '''        listing = _merge_listing(summary, detail)\n        prelim = preliminary_score(listing, priority_terms)\n        diag_target = listing.total_cost or listing.price\n        diag_identity_available = False\n        diag_search_attempted = False\n        diag_search_rows = 0\n        diag_exact_matches = 0\n        diag_budget_blocked = False\n        diag_search_error = False\n        cert_candidate: CertCandidate | None = extract_cert_from_aspects(listing)\n'''
replace_required(scanner, old, new, "Kandidaten-Diagnose")

replace_required(
    scanner,
    '            and preliminary_score(listing, priority_terms) >= psa_market_web_min_prelim\n',
    '            and prelim >= psa_market_web_min_prelim\n',
    "PSA-Web-PreScore",
)
replace_required(
    scanner,
    '            and preliminary_score(listing, priority_terms) >= listing_market_min_prelim\n',
    '            and prelim >= listing_market_min_prelim\n',
    "Listing-Comp-PreScore",
)

old = '''        if (\n            _market_needs_upgrade(market) and cert and cert.valid and is_psa10(cert.grade)\n            and _ocr_cert_safe_for_market(listing, cert_candidate, cert)\n        ):\n            target = listing.total_cost or listing.price\n'''
new = '''        if (\n            _market_needs_upgrade(market) and cert and cert.valid and is_psa10(cert.grade)\n            and _ocr_cert_safe_for_market(listing, cert_candidate, cert)\n        ):\n            diag_identity_available = True\n            target = listing.total_cost or listing.price\n'''
replace_required(scanner, old, new, "PSA-Identität markieren")

replace_required(
    scanner,
    '''                                rows = ebay.search(comp_query, limit=comp_search_limit, started_after=None, offset=0)\n                                market_comp_calls += 1\n                                comp_rows.extend(rows)\n''',
    '''                                rows = ebay.search(comp_query, limit=comp_search_limit, started_after=None, offset=0)\n                                diag_search_attempted = True\n                                diag_search_rows += len(rows)\n                                market_comp_calls += 1\n                                comp_rows.extend(rows)\n''',
    "erste Comp-Seiten instrumentieren",
    count=2,
)
replace_required(
    scanner,
    '''                                    rows2 = ebay.search(\n                                        comp_query, limit=comp_search_limit,\n                                        started_after=None, offset=comp_search_limit,\n                                    )\n                                    market_comp_calls += 1\n                                    comp_rows.extend(rows2)\n''',
    '''                                    rows2 = ebay.search(\n                                        comp_query, limit=comp_search_limit,\n                                        started_after=None, offset=comp_search_limit,\n                                    )\n                                    diag_search_attempted = True\n                                    diag_search_rows += len(rows2)\n                                    market_comp_calls += 1\n                                    comp_rows.extend(rows2)\n''',
    "zweite Comp-Seiten instrumentieren",
    count=2,
)
replace_required(
    scanner,
    '''                                    exclude_item_id=listing.item_id,\n                                )\n                                if (\n''',
    '''                                    exclude_item_id=listing.item_id,\n                                )\n                                diag_exact_matches = max(diag_exact_matches, len(values))\n                                if (\n''',
    "erste Exact-Matches instrumentieren",
    count=2,
)
replace_required(
    scanner,
    '''                                        exclude_item_id=listing.item_id,\n                                    )\n                                if len(values) >= 3:\n''',
    '''                                        exclude_item_id=listing.item_id,\n                                    )\n                                    diag_exact_matches = max(diag_exact_matches, len(values))\n                                if len(values) >= 3:\n''',
    "zweite Exact-Matches instrumentieren",
    count=2,
)

old = '''            identity = listing_comp_identity(listing)\n            target = listing.total_cost or listing.price\n            if identity and target:\n'''
new = '''            identity = listing_comp_identity(listing)\n            if identity is not None:\n                diag_identity_available = True\n            target = listing.total_cost or listing.price\n            if identity and target:\n'''
replace_required(scanner, old, new, "Listing-Identität markieren")

replace_required(
    scanner,
    '''                        except EbayBudgetExceeded:\n                            notes.append("eBay-Budget für weitere Preis-Comps ausgeschöpft")\n                        except EbayError:\n                            notes.append("Mindestens eine eBay-Preisvergleichssuche ist fehlgeschlagen")\n''',
    '''                        except EbayBudgetExceeded:\n                            diag_budget_blocked = True\n                            notes.append("eBay-Budget für weitere Preis-Comps ausgeschöpft")\n                        except EbayError:\n                            diag_search_error = True\n                            notes.append("Mindestens eine eBay-Preisvergleichssuche ist fehlgeschlagen")\n''',
    "Cert-Comp-Fehlerdiagnose",
)
replace_required(
    scanner,
    '''                    except EbayBudgetExceeded:\n                        notes.append("eBay-Budget für weitere Listing-Preis-Comps ausgeschöpft")\n                    except EbayError:\n                        notes.append("Mindestens eine Listing-Preisvergleichssuche ist fehlgeschlagen")\n\n        hit = score_hit(\n''',
    '''                    except EbayBudgetExceeded:\n                        diag_budget_blocked = True\n                        notes.append("eBay-Budget für weitere Listing-Preis-Comps ausgeschöpft")\n                    except EbayError:\n                        diag_search_error = True\n                        notes.append("Mindestens eine Listing-Preisvergleichssuche ist fehlgeschlagen")\n\n        hit = score_hit(\n''',
    "Listing-Comp-Fehlerdiagnose",
)

old = '''        scored.append(hit)\n        mark_processed(state, listing.item_id, hit.score)\n'''
new = '''        gap_reason = _classify_price_gap(\n            hit.market_value,\n            target_available=diag_target is not None,\n            preliminary=prelim,\n            min_preliminary=listing_market_min_prelim,\n            identity_available=diag_identity_available,\n            search_attempted=diag_search_attempted,\n            search_rows=diag_search_rows,\n            exact_matches=diag_exact_matches,\n            budget_blocked=(\n                diag_budget_blocked\n                or (hit.market_value is None and market_comp_calls >= max_comp_calls)\n            ),\n            search_error=diag_search_error,\n        )\n        if gap_reason:\n            price_diag["OhnePreis"] += 1\n            price_diag[gap_reason] += 1\n        for key in _weak_market_diagnostics(hit.market_value):\n            price_diag[key] += 1\n\n        scored.append(hit)\n        mark_processed(state, listing.item_id, hit.score)\n'''
replace_required(scanner, old, new, "Preisdiagnose finalisieren")

old = '''    candidate_api_successes = max(0, psa.api_successes - psa_api_success_baseline)\n    notes.append(f"PSA API live: {_psa_status_label(psa_api_status)}")\n    notes.append(\n'''
new = '''    candidate_api_successes = max(0, psa.api_successes - psa_api_success_baseline)\n    notes.append(f"PSA API live: {_psa_status_label(psa_api_status)}")\n    notes.append(_price_diag_note(price_diag))\n    notes.append(\n'''
replace_required(scanner, old, new, "Preisdiagnose in Run speichern")


dashboard = "psa_sniper/dashboard.py"
old = '''  const coverageLine = notes.find(note => String(note).startsWith('Coverage:'));\n  const statusLine = notes.find(note => String(note).startsWith('PSA API live:'));\n  const values = {};\n'''
new = '''  const coverageLine = notes.find(note => String(note).startsWith('Coverage:'));\n  const priceDiagLine = notes.find(note => String(note).startsWith('PriceDiag:'));\n  const statusLine = notes.find(note => String(note).startsWith('PSA API live:'));\n  const values = {};\n  const priceDiag = {};\n'''
replace_required(dashboard, old, new, "Dashboard PriceDiag-Linie")

old = '''  const psaStatus = statusLine\n    ? String(statusLine).split(':').slice(1).join(':').trim()\n    : 'NICHT GETESTET';\n  return { run, values, psaStatus };\n}\n\nfunction marketDetail(market) {\n'''
new = '''  if (priceDiagLine) {\n    String(priceDiagLine).replace(/^PriceDiag:\\s*/, '').split(';').forEach(part => {\n      const separator = part.indexOf('=');\n      if (separator < 0) return;\n      const key = part.slice(0, separator).trim();\n      const number = Number(part.slice(separator + 1).trim());\n      priceDiag[key] = Number.isFinite(number) ? number : 0;\n    });\n  }\n  const psaStatus = statusLine\n    ? String(statusLine).split(':').slice(1).join(':').trim()\n    : 'NICHT GETESTET';\n  return { run, values, priceDiag, psaStatus };\n}\n\nfunction marketDetail(market) {\n'''
replace_required(dashboard, old, new, "Dashboard PriceDiag parsen")

old = '''  grid.replaceChildren(...cards.map(([label, value, note]) => {\n    const card = el('article', 'stat-card');\n    card.append(\n      el('div', 'stat-label', label),\n      el('div', 'stat-value', String(value)),\n      el('div', 'stat-note', note),\n    );\n    return card;\n  }));\n}\n\n'''
new = '''  grid.replaceChildren(...cards.map(([label, value, note]) => {\n    const card = el('article', 'stat-card');\n    card.append(\n      el('div', 'stat-label', label),\n      el('div', 'stat-value', String(value)),\n      el('div', 'stat-note', note),\n    );\n    return card;\n  }));\n}\n\nfunction ensurePriceDiagnosticsElements() {\n  let header = $('priceDiagHeader');\n  let grid = $('priceDiag');\n  if (!header) {\n    header = el('div', 'result-bar');\n    header.id = 'priceDiagHeader';\n    $('coverage').after(header);\n  }\n  if (!grid) {\n    grid = el('section', 'stats-grid');\n    grid.id = 'priceDiag';\n    grid.setAttribute('aria-label', 'Preisdiagnose des letzten Scanner-Laufs');\n    header.after(grid);\n  }\n  return { header, grid };\n}\n\nfunction renderPriceDiagnostics() {\n  const { header, grid } = ensurePriceDiagnosticsElements();\n  const { run, priceDiag } = coverageData();\n  const noPrice = Number(priceDiag.OhnePreis || 0);\n  const weak = Number(priceDiag.Schwach || 0);\n  header.replaceChildren(\n    el('p', '', 'Warum fehlt / schwächelt der Preis?'),\n    el('p', 'muted compact', run ? `${noPrice} ohne Preisindikator · ${weak} schwache Preisquelle(n)` : 'Noch keine Diagnosedaten'),\n  );\n  const cards = [\n    ['Ohne Preisindikator', noPrice, 'nach allen verfügbaren Preiswegen'],\n    ['Screening-Gate', priceDiag.UnterGate || 0, 'Pre-Score zu niedrig für zusätzliche Comp-Calls'],\n    ['Keine sichere Identität', priceDiag.KeineIdentitaet || 0, 'Kartennummer / Subject / Set nicht belastbar genug'],\n    ['Keine Suchtreffer', priceDiag.KeineSuchtreffer || 0, 'eBay lieferte für die Comp-Queries keine Treffer'],\n    ['0 exakte Comps', priceDiag.KeineExaktenComps || 0, 'Treffer vorhanden, aber Identitätsfilter lehnten alle ab'],\n    ['Nur wenige Comps', priceDiag.ZuWenigeComps || 0, 'weniger als 3 exakte Vergleichsangebote ohne Preisanker'],\n    ['Budget blockiert', priceDiag.Budget || 0, 'Comp-Budget war für diesen Kandidaten ausgeschöpft'],\n    ['Suchfehler', priceDiag.Suchfehler || 0, 'eBay-Comp-Suche technisch fehlgeschlagen'],\n    ['Kein Zielpreis', priceDiag.KeinZielpreis || 0, 'Listing hatte keinen verwertbaren Gesamtpreis'],\n    ['Sonstige Ursache', priceDiag.Sonstiges || 0, 'Identität vorhanden, aber kein belastbarer Preis entstanden'],\n    ['Schwache Quellen', weak, 'Preis vorhanden, aber nicht stark genug für Kaufurteil'],\n    ['davon PSA Estimate', priceDiag.SchwachPSAEstimate || 0, 'nur PSA-Schätzwert statt belastbarer Markt-Comps'],\n    ['< 3 exakte Comps', priceDiag.SchwachComps || 0, 'Preisanker basiert auf sehr kleiner Stichprobe'],\n    ['< 3 Verkäufer', priceDiag.SchwachVerkaeufer || 0, 'zu wenig unabhängige Angebotsquellen'],\n    ['Hohe Streuung', priceDiag.SchwachStreuung || 0, 'Comp-Preise liegen zu weit auseinander'],\n    ['Identität unvollständig', priceDiag.SchwachIdentitaet || 0, 'Listing-basierter oder unvollständig bestätigter Match'],\n  ].filter(([label, value]) => label === 'Ohne Preisindikator' || Number(value) > 0);\n  grid.replaceChildren(...cards.map(([label, value, note]) => {\n    const card = el('article', 'stat-card');\n    card.append(\n      el('div', 'stat-label', label),\n      el('div', 'stat-value', String(value)),\n      el('div', 'stat-note', note),\n    );\n    return card;\n  }));\n}\n\n'''
replace_required(dashboard, old, new, "Dashboard Diagnoseblock")

old = '''            "  renderStats(all);\\n  renderCoverage();\\n  renderHealth();",\n'''
new = '''            "  renderStats(all);\\n  renderCoverage();\\n  renderPriceDiagnostics();\\n  renderHealth();",\n'''
replace_required(dashboard, old, new, "Dashboard Diagnose rendern")


test_scanner = Path("tests/test_scanner_market.py")
text = test_scanner.read_text(encoding="utf-8")
old_import = "from psa_sniper.scanner import _market_needs_upgrade, _prefer_market_value\n"
new_import = (
    "from psa_sniper.scanner import (\n"
    "    _classify_price_gap,\n"
    "    _market_needs_upgrade,\n"
    "    _prefer_market_value,\n"
    "    _weak_market_diagnostics,\n"
    ")\n"
)
if old_import not in text:
    raise RuntimeError("Scanner-Testimport nicht gefunden")
text = text.replace(old_import, new_import)
text += '''\n\ndef test_price_gap_diagnoses_identity_and_search_losses():\n    assert _classify_price_gap(\n        None, target_available=True, preliminary=8, min_preliminary=7,\n        identity_available=False, search_attempted=False, search_rows=0,\n        exact_matches=0, budget_blocked=False, search_error=False,\n    ) == "KeineIdentitaet"\n    assert _classify_price_gap(\n        None, target_available=True, preliminary=8, min_preliminary=7,\n        identity_available=True, search_attempted=True, search_rows=12,\n        exact_matches=0, budget_blocked=False, search_error=False,\n    ) == "KeineExaktenComps"\n\n\ndef test_price_gap_prioritizes_screening_gate_and_budget():\n    assert _classify_price_gap(\n        None, target_available=True, preliminary=6, min_preliminary=7,\n        identity_available=False, search_attempted=False, search_rows=0,\n        exact_matches=0, budget_blocked=False, search_error=False,\n    ) == "UnterGate"\n    assert _classify_price_gap(\n        None, target_available=True, preliminary=8, min_preliminary=7,\n        identity_available=True, search_attempted=False, search_rows=0,\n        exact_matches=0, budget_blocked=True, search_error=False,\n    ) == "Budget"\n\n\ndef test_weak_market_diagnostics_explain_source_quality():\n    estimate = market(700, "PSA Estimate", "niedrig", 0, "psa_estimate")\n    assert _weak_market_diagnostics(estimate) == ["Schwach", "SchwachPSAEstimate"]\n    comps = MarketValue(\n        Money(500, "EUR"), "eBay", "niedrig", 2,\n        market_type="ebay_active", required_edge=0.25, unique_sellers=1,\n        price_low=480, price_high=540, dispersion=0.12,\n    )\n    flags = _weak_market_diagnostics(comps)\n    assert "SchwachComps" in flags\n    assert "SchwachVerkaeufer" in flags\n'''
test_scanner.write_text(text, encoding="utf-8")


test_dashboard = Path("tests/test_crypto_dashboard.py")
text = test_dashboard.read_text(encoding="utf-8")
old_asserts = '    assert "repricing_checked" in app\n    assert "ageHours <= 3.75" in app\n'
new_asserts = (
    '    assert "repricing_checked" in app\n'
    '    assert "PriceDiag:" in app\n'
    '    assert "Warum fehlt / schwächelt der Preis?" in app\n'
    '    assert "Keine sichere Identität" in app\n'
    '    assert "ageHours <= 3.75" in app\n'
)
if old_asserts not in text:
    raise RuntimeError("Dashboard-Testanker nicht gefunden")
text = text.replace(old_asserts, new_asserts)
test_dashboard.write_text(text, encoding="utf-8")
