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


def replace_block(path: str, start_marker: str, end_marker: str, new_block: str, label: str) -> None:
    text = read(path)
    start = text.find(start_marker)
    if start < 0:
        raise RuntimeError(f"{label}: Startmarker fehlt in {path}")
    end = text.find(end_marker, start)
    if end < 0:
        raise RuntimeError(f"{label}: Endmarker fehlt in {path}")
    write(path, text[:start] + new_block + text[end:])


# ---------------------------------------------------------------------------
# PSA: öffentliche Methode für API-only Lookup + Merge aus autoritativer API
# und vorhandenen Web-Sales/Estimate-Daten.
# ---------------------------------------------------------------------------
psa = "psa_sniper/psa.py"
replace_once(
    psa,
    '''    def get_cert(self, cert_number: str) -> PSACertInfo | None:\n        if self.access_token and not self.api_rate_limited:\n            info = self._get_api(cert_number)\n            if info and info.valid:\n                return info\n        if self.web_fallback and not self.web_rate_limited:\n            info = self._get_web(cert_number)\n            if info and info.valid:\n                return info\n        return None\n\n''',
    '''    def get_api_cert(self, cert_number: str) -> PSACertInfo | None:\n        if not self.access_token or self.api_rate_limited:\n            return None\n        info = self._get_api(cert_number)\n        return info if info and info.valid else None\n\n    def get_cert(self, cert_number: str) -> PSACertInfo | None:\n        info = self.get_api_cert(cert_number)\n        if info:\n            return info\n        if self.web_fallback and not self.web_rate_limited:\n            info = self._get_web(cert_number)\n            if info and info.valid:\n                return info\n        return None\n\n''',
    "PSA API-only Lookup",
)
# Merge-Helfer vor Client-Klasse einfügen.
marker = "\n\nclass PSABudgetExceeded(RuntimeError):\n"
text = read(psa)
if marker not in text:
    raise RuntimeError("PSA Merge-Marker fehlt")
merge_helper = '''\n\ndef merge_cert_info(authoritative: PSACertInfo, existing: PSACertInfo | None) -> PSACertInfo:\n    """API-Identität/POP bevorzugen, vorhandene Web-Marktinfos bewahren."""\n    old = existing or PSACertInfo(cert_number=authoritative.cert_number)\n    old_source = str(old.data_source or "")\n    combined_source = "PSA Public API"\n    if old_source and "PSA Public API" not in old_source:\n        combined_source += f" + {old_source}"\n    return PSACertInfo(\n        cert_number=authoritative.cert_number or old.cert_number,\n        valid=authoritative.valid or old.valid,\n        grade=authoritative.grade or old.grade,\n        year=authoritative.year or old.year,\n        brand_title=authoritative.brand_title or old.brand_title,\n        subject=authoritative.subject or old.subject,\n        card_number=authoritative.card_number or old.card_number,\n        category=authoritative.category or old.category,\n        variety=authoritative.variety or old.variety,\n        population=(\n            authoritative.population\n            if authoritative.population is not None\n            else old.population\n        ),\n        population_higher=(\n            authoritative.population_higher\n            if authoritative.population_higher is not None\n            else old.population_higher\n        ),\n        estimate=old.estimate or authoritative.estimate,\n        recent_sales=list(old.recent_sales or authoritative.recent_sales),\n        source_url=authoritative.source_url or old.source_url,\n        data_source=combined_source,\n    )\n\n\ndef cert_needs_api_upgrade(cert: PSACertInfo | None) -> bool:\n    if cert is None:\n        return True\n    source = str(cert.data_source or "")\n    if "PSA Public API" not in source:\n        return True\n    return any(\n        value is None or value == ""\n        for value in (cert.grade, cert.subject, cert.card_number, cert.population)\n    )\n'''
write(psa, text.replace(marker, merge_helper + marker, 1))


# ---------------------------------------------------------------------------
# Cert-Vertrauen: eine echte Cert-Nummer muss trotzdem zur gelisteten Karte
# passen. Kartennummer ist stärkster sprachunabhängiger Beleg.
# ---------------------------------------------------------------------------
scoring = "psa_sniper/scoring.py"
replace_once(
    scoring,
    "from .models import Listing, MarketValue, Money, PSACertInfo, ScoredHit\n",
    "from .identity import pricing_identity_from_listing\nfrom .models import Listing, MarketValue, Money, PSACertInfo, ScoredHit\n",
    "Scoring Identity import",
)
old_identity = '''def identity_overlap(listing: Listing, cert: PSACertInfo | None) -> int:\n    if not cert:\n        return 0\n    title_tokens = set(normalize_text(listing.title).split()) - STOPWORDS\n    cert_text = " ".join(\n        value\n        for value in (\n            cert.year,\n            cert.brand_title,\n            cert.subject,\n            cert.card_number,\n            cert.variety,\n        )\n        if value\n    )\n    cert_tokens = set(normalize_text(cert_text).split()) - STOPWORDS\n    meaningful = {token for token in cert_tokens if len(token) >= 2 or token.isdigit()}\n    return len(title_tokens & meaningful)\n\n\n'''
new_identity = '''def _listing_identity_text(listing: Listing) -> str:\n    aspects = " ".join(\n        str(value)\n        for values in listing.aspects.values()\n        for value in values\n    )\n    return f"{listing.title} {aspects}".strip()\n\n\ndef identity_overlap(listing: Listing, cert: PSACertInfo | None) -> int:\n    if not cert:\n        return 0\n    listing_tokens = set(normalize_text(_listing_identity_text(listing)).split()) - STOPWORDS\n    cert_text = " ".join(\n        value\n        for value in (\n            cert.year,\n            cert.brand_title,\n            cert.subject,\n            cert.card_number,\n            cert.variety,\n        )\n        if value\n    )\n    cert_tokens = set(normalize_text(cert_text).split()) - STOPWORDS\n    meaningful = {token for token in cert_tokens if len(token) >= 2 or token.isdigit()}\n    return len(listing_tokens & meaningful)\n\n\ndef cert_identity_trust(\n    listing: Listing,\n    cert: PSACertInfo | None,\n    *,\n    cert_source: str | None = None,\n    cert_confidence: float | None = None,\n) -> tuple[bool, str]:\n    if cert is None:\n        return True, "keine Cert-Daten"\n\n    text = _listing_identity_text(listing)\n    text_n = normalize_text(text)\n    candidate = pricing_identity_from_listing(listing)\n    cert_card = normalize_text(cert.card_number or "")\n    candidate_card = normalize_text(candidate.card_number) if candidate else ""\n\n    if cert_card and candidate_card:\n        if cert_card != candidate_card:\n            return False, f"Kartennummer widerspricht Cert ({candidate_card} ≠ {cert_card})"\n        return True, "Kartennummer stimmt mit Cert überein"\n\n    subject_match = bool(cert.subject and has_phrase(text, cert.subject))\n    variety_match = bool(cert.variety and has_phrase(text, cert.variety))\n    year_match = bool(cert.year and normalize_text(cert.year) in set(text_n.split()))\n    brand_tokens = {\n        token\n        for token in normalize_text(cert.brand_title or "").split()\n        if token not in STOPWORDS and (len(token) >= 3 or token.isdigit())\n    }\n    listing_tokens = set(text_n.split())\n    brand_overlap = len(brand_tokens & listing_tokens)\n    overlap = identity_overlap(listing, cert)\n\n    if subject_match and (year_match or variety_match or brand_overlap >= 1):\n        return True, "Subject plus Set/Jahr/Variante stimmen"\n    if subject_match and not cert_card:\n        return True, "Subject stimmt mit Cert überein"\n\n    source = str(cert_source or "").casefold()\n    if source.startswith("ocr"):\n        if (cert_confidence or 0.0) >= 0.85 and overlap >= 2:\n            return True, "OCR-Cert hat mehrere Identitätsmerkmale"\n        return False, "OCR-Cert hat keine ausreichend belastbare Kartenidentität"\n\n    if overlap >= 2:\n        return True, "mehrere Cert-Merkmale stimmen mit Listing überein"\n    return False, "PSA-Cert ist nicht ausreichend der gelisteten Karte zuordenbar"\n\n\n'''
replace_once(scoring, old_identity, new_identity, "Cert Trust Helfer")
old_cert_block = '''    overlap = identity_overlap(listing, cert)\n    cert_trusted = True\n    if cert and is_psa10(cert.grade):\n        adjust(2, "PSA-Cert bestätigt GEM MT 10")\n        reasons.append("PSA-Cert bestätigt GEM MT 10")\n    elif cert and cert.grade:\n        label = f"Cert-Grade ist {cert.grade}, nicht PSA 10"\n        adjust(-20, label)\n        cert_trusted = False\n        warnings.append(label)\n\n    if cert and cert_source and cert_source.startswith("OCR"):\n        confidence = cert_confidence or 0.0\n        if confidence < 0.7 and overlap == 0:\n            label = "OCR-Cert passt nicht plausibel zum Listing; POP/Preis werden ignoriert"\n            adjust(-7, label)\n            cert_trusted = False\n            warnings.append(label)\n        elif overlap == 0:\n            label = "OCR-Cert hat keine erkennbare Titelüberschneidung"\n            adjust(-2, label)\n            warnings.append(label)\n        else:\n            label = "OCR-Cert passt inhaltlich zum Listing"\n            adjust(0, label)\n            reasons.append(label)\n\n'''
new_cert_block = '''    cert_trusted, cert_trust_reason = cert_identity_trust(\n        listing,\n        cert,\n        cert_source=cert_source,\n        cert_confidence=cert_confidence,\n    )\n    if cert and not cert_trusted:\n        prefix = "OCR-Cert" if str(cert_source or "").startswith("OCR") else "PSA-Cert"\n        label = f"{prefix} passt nicht sicher zum Listing: {cert_trust_reason}; POP/Preis werden ignoriert"\n        adjust(-4, label)\n        warnings.append(label)\n    elif cert and is_psa10(cert.grade):\n        adjust(2, "PSA-Cert bestätigt GEM MT 10")\n        reasons.append("PSA-Cert bestätigt GEM MT 10")\n        adjust(0, f"Cert-Identität bestätigt: {cert_trust_reason}")\n    elif cert and cert.grade:\n        label = f"Cert-Grade ist {cert.grade}, nicht PSA 10"\n        adjust(-20, label)\n        cert_trusted = False\n        warnings.append(label)\n\n'''
replace_once(scoring, old_cert_block, new_cert_block, "Cert Trust Scoring")
# Neue/alte Jahreswarnungen nur auf vertrauenswürdige Cert beziehen.
replace_once(
    scoring,
    '''    if cert and cert.year:\n''',
    '''    if cert and cert_trusted and cert.year:\n''',
    "Jahr nur vertrauenswürdige Cert",
)


# ---------------------------------------------------------------------------
# Scanner: dieselbe Trust-Logik für Cert-basierte Markt-Comps und API-Upgrade
# des vorhandenen Web-Caches verwenden.
# ---------------------------------------------------------------------------
scanner = "psa_sniper/scanner.py"
replace_once(
    scanner,
    "from .psa import PSABudgetExceeded, PSAClient\n",
    "from .psa import PSABudgetExceeded, PSAClient, cert_needs_api_upgrade, merge_cert_info\n",
    "Scanner PSA Merge imports",
)
replace_once(
    scanner,
    '''    identity_overlap,\n    is_psa10,\n''',
    '''    cert_identity_trust,\n    identity_overlap,\n    is_psa10,\n''',
    "Scanner Cert Trust import",
)
old_safe = '''def _ocr_cert_safe_for_market(listing: Listing, cert_candidate: CertCandidate | None, cert: Any) -> bool:\n    if not cert_candidate or not cert:\n        return False\n    if not cert_candidate.source.startswith("OCR"):\n        return True\n    return identity_overlap(listing, cert) > 0\n\n\n'''
new_safe = '''def _cert_safe_for_market(listing: Listing, cert_candidate: CertCandidate | None, cert: Any) -> bool:\n    if not cert_candidate or not cert:\n        return False\n    trusted, _ = cert_identity_trust(\n        listing,\n        cert,\n        cert_source=cert_candidate.source,\n        cert_confidence=cert_candidate.confidence,\n    )\n    return trusted\n\n\n'''
replace_once(scanner, old_safe, new_safe, "Scanner Cert Market Trust")
replace_once(
    scanner,
    "            and _ocr_cert_safe_for_market(listing, cert_candidate, cert)\n",
    "            and _cert_safe_for_market(listing, cert_candidate, cert)\n",
    "Scanner Markt Cert Trust anwenden",
)
# Cache Upgrade Counter.
replace_once(
    scanner,
    '''    psa_market_web_calls = 0\n    psa_market_web_min_prelim = int(settings.get("psa_market_web_min_preliminary_score", 8))\n    price_diag = _new_price_diagnostics()\n''',
    '''    psa_market_web_calls = 0\n    psa_market_web_min_prelim = int(settings.get("psa_market_web_min_preliminary_score", 8))\n    psa_cache_upgrades = 0\n    price_diag = _new_price_diagnostics()\n''',
    "Scanner PSA Cache Upgrade Counter",
)
old_cached = '''        if cert_candidate:\n            cert = get_cached_cert(state, cert_candidate.number, cert_cache_days)\n            if cert is None:\n                try:\n                    cert = psa.get_cert(cert_candidate.number)\n                except PSABudgetExceeded:\n                    notes.append("PSA-Call-Budget ausgeschöpft; weitere Kandidaten ohne POP-Anreicherung")\n                    cert = None\n                if cert:\n                    put_cached_cert(state, cert)\n\n'''
new_cached = '''        if cert_candidate:\n            cert = get_cached_cert(state, cert_candidate.number, cert_cache_days)\n            if cert is not None and psa_api_status == "ok" and cert_needs_api_upgrade(cert):\n                try:\n                    api_cert = psa.get_api_cert(cert_candidate.number)\n                except PSABudgetExceeded:\n                    api_cert = None\n                if api_cert:\n                    cert = merge_cert_info(api_cert, cert)\n                    put_cached_cert(state, cert)\n                    psa_cache_upgrades += 1\n            if cert is None:\n                try:\n                    cert = psa.get_cert(cert_candidate.number)\n                except PSABudgetExceeded:\n                    notes.append("PSA-Call-Budget ausgeschöpft; weitere Kandidaten ohne POP-Anreicherung")\n                    cert = None\n                if cert:\n                    put_cached_cert(state, cert)\n\n'''
replace_once(scanner, old_cached, new_cached, "Scanner PSA Cache Upgrade")
replace_once(
    scanner,
    '''    notes.append(f"PSA API live: {_psa_status_label(psa_api_status)}")\n    notes.append(_price_diag_note(price_diag))\n''',
    '''    notes.append(f"PSA API live: {_psa_status_label(psa_api_status)}")\n    if psa_cache_upgrades:\n        notes.append(f"PSA API Cache-Upgrades: {psa_cache_upgrades}")\n    notes.append(_price_diag_note(price_diag))\n''',
    "Scanner Cache Upgrade Note",
)


# ---------------------------------------------------------------------------
# PSA-History-Backfill: nach einem Scan mit PSA API OK alte Web-Certs in der
# verschlüsselten Historie API-seitig nachziehen und neu bewerten.
# ---------------------------------------------------------------------------
Path("psa_sniper/psa_backfill.py").write_text(r'''from __future__ import annotations

import os
from dataclasses import dataclass, field
from datetime import timedelta
from typing import Any

from .config import load_settings, state_path
from .psa import PSAClient, cert_needs_api_upgrade, merge_cert_info
from .psa_auth import normalize_psa_access_token
from .repricing import listing_from_history
from .scoring import score_hit
from .state import cert_from_dict, hit_to_record, load_state, market_from_dict, put_cached_cert, save_state
from .util import iso_z, parse_iso_datetime, utc_now


@dataclass(slots=True)
class PSABackfillResult:
    checked_certs: int = 0
    upgraded_certs: int = 0
    rescored_rows: int = 0
    calls: int = 0
    status: str = "skipped"
    notes: list[str] = field(default_factory=list)


def _latest_psa_status(state: dict[str, Any]) -> str:
    runs = state.get("runs") or []
    if not runs or not isinstance(runs[0], dict):
        return ""
    for note in runs[0].get("notes") or []:
        text = str(note)
        if text.startswith("PSA API live:"):
            return text.split(":", 1)[1].strip().upper()
    return ""


def _preserve_history_meta(old: dict[str, Any], new: dict[str, Any]) -> dict[str, Any]:
    for key in (
        "first_seen_at", "last_seen_at", "price_checked_at", "price_check_attempts",
        "price_last_improved_at", "availability_checked_at", "availability_status",
        "availability_error",
    ):
        if old.get(key) is not None:
            new[key] = old[key]
    return new


def backfill_state(
    state: dict[str, Any],
    settings: dict[str, Any],
    psa: PSAClient,
) -> PSABackfillResult:
    result = PSABackfillResult(status="ok")
    max_calls = max(0, int(settings.get("max_psa_backfill_calls_per_run", 12)))
    if max_calls <= 0:
        result.status = "disabled"
        return result

    cutoff = utc_now() - timedelta(hours=max(1, int(settings.get("psa_backfill_history_hours", 168))))
    candidates: dict[str, tuple[int, str]] = {}
    history = [row for row in state.get("history", []) if isinstance(row, dict)]
    for row in history:
        if str(row.get("availability_status") or "active") in {"ended", "unavailable"}:
            continue
        seen = parse_iso_datetime(row.get("last_seen_at") or row.get("first_seen_at"))
        if not seen or seen < cutoff:
            continue
        number = str(row.get("cert_number") or "").strip()
        if not number.isdigit():
            continue
        cert_data = row.get("cert")
        existing = cert_from_dict(cert_data) if isinstance(cert_data, dict) else None
        if not cert_needs_api_upgrade(existing):
            continue
        score = int(row.get("score") or 0)
        last_seen = str(row.get("last_seen_at") or "")
        previous = candidates.get(number)
        if previous is None or (score, last_seen) > previous:
            candidates[number] = (score, last_seen)

    ordered = sorted(candidates, key=lambda number: candidates[number], reverse=True)
    threshold = int(settings.get("hit_threshold", 11))
    for number in ordered[:max_calls]:
        if psa.calls_made >= max_calls or not psa.access_token:
            break
        result.checked_certs += 1
        api_cert = psa.get_api_cert(number)
        if not api_cert:
            if not psa.access_token:
                result.status = psa.api_auth_status
                break
            continue

        matching = [row for row in history if str(row.get("cert_number") or "") == number]
        existing = None
        for row in matching:
            if isinstance(row.get("cert"), dict):
                existing = cert_from_dict(row["cert"])
                break
        merged = merge_cert_info(api_cert, existing)
        put_cached_cert(state, merged)
        result.upgraded_certs += 1

        for row in matching:
            listing = listing_from_history(row)
            if listing is None:
                continue
            market = market_from_dict(row.get("market_value"))
            hit = score_hit(
                listing,
                cert_number=number,
                cert_source=row.get("cert_source"),
                cert_confidence=row.get("cert_confidence"),
                cert=merged,
                market_value_listing_currency=market,
                priority_terms=list(settings.get("priority_terms") or []),
                demand_terms=list(settings.get("demand_terms") or []),
            )
            updated = _preserve_history_meta(row, hit_to_record(hit, threshold))
            row.clear()
            row.update(updated)
            result.rescored_rows += 1

    result.calls = psa.calls_made
    return result


def run_psa_backfill_queue() -> PSABackfillResult:
    path = state_path()
    settings = load_settings()
    state = load_state(path)
    result = PSABackfillResult()
    if _latest_psa_status(state) != "OK":
        return result
    token = normalize_psa_access_token(os.getenv("PSA_ACCESS_TOKEN"))
    if not token:
        return result
    max_calls = max(0, int(settings.get("max_psa_backfill_calls_per_run", 12)))
    psa = PSAClient(
        access_token=token,
        web_fallback=False,
        delay_seconds=float(settings.get("psa_request_delay_seconds", 1.5)),
        max_calls=max_calls,
    )
    result = backfill_state(state, settings, psa)
    runs = state.get("runs") or []
    if runs and isinstance(runs[0], dict):
        latest = runs[0]
        latest["psa_backfill_checked"] = result.checked_certs
        latest["psa_backfill_upgraded"] = result.upgraded_certs
        latest["psa_backfill_rescored"] = result.rescored_rows
        latest["psa_backfill_calls"] = result.calls
        notes = list(latest.get("notes") or [])
        notes.append(
            "PSA-Backfill: "
            f"{result.checked_certs} Cert(s) geprüft; {result.upgraded_certs} verbessert; "
            f"{result.rescored_rows} Historieneintrag/-einträge neu bewertet; {result.calls} Call(s)"
        )
        latest["notes"] = list(dict.fromkeys(notes))
    save_state(path, state)
    return result
''', encoding="utf-8")

cli = "psa_sniper/cli.py"
replace_once(
    cli,
    "from .quota import prepare_scan_quota\n",
    "from .psa_backfill import run_psa_backfill_queue\nfrom .quota import prepare_scan_quota\n",
    "CLI PSA Backfill import",
)
replace_once(
    cli,
    '''    result = run_scan()\n    if result != 0:\n        return result\n    try:\n        run_repricing_queue()\n''',
    '''    result = run_scan()\n    if result != 0:\n        return result\n    try:\n        backfill = run_psa_backfill_queue()\n        if backfill.status != "skipped":\n            print(\n                "PSA-Backfill abgeschlossen: "\n                f"{backfill.checked_certs} geprüft, {backfill.upgraded_certs} verbessert, "\n                f"{backfill.rescored_rows} neu bewertet, {backfill.calls} PSA-Calls."\n            )\n    except Exception as exc:\n        print(f"PSA-Backfill-Warnung: {exc.__class__.__name__}", file=sys.stderr)\n    try:\n        run_repricing_queue()\n''',
    "CLI PSA Backfill Reihenfolge",
)

# Dashboard Coverage um PSA Backfill ergänzen.
dashboard = "psa_sniper/dashboard.py"
replace_once(
    dashboard,
    '''    ['POP vorhanden', values.POP ?? '–', 'Population erfolgreich verfügbar'],\n    ['Preisindikator', values.Preis ?? '–', 'PSA Sales · eBay Comps · Estimate'],\n''',
    '''    ['POP vorhanden', values.POP ?? '–', 'Population erfolgreich verfügbar'],\n    ['PSA Backfill', run?.psa_backfill_upgraded ?? 0, `${run?.psa_backfill_checked ?? 0} alte Cert(s) API-seitig geprüft`],\n    ['Preisindikator', values.Preis ?? '–', 'PSA Sales · eBay Comps · Estimate'],\n''',
    "Dashboard PSA Backfill Card",
)

# Config: kurze Discovery-Überlappung erneut prüfen und PSA-Backfill begrenzen.
config = "config/settings.json"
text = read(config)
text = text.replace('"processed_cooldown_minutes": 360,', '"processed_cooldown_minutes": 170,')
text = text.replace('"max_psa_calls_per_run": 40,', '"max_psa_calls_per_run": 40,\n  "max_psa_backfill_calls_per_run": 12,\n  "psa_backfill_history_hours": 168,')
write(config, text)


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------
path = Path("tests/test_scoring.py")
text = path.read_text(encoding="utf-8")
text = text.replace(
    "from psa_sniper.scoring import score_hit\n",
    "from psa_sniper.scoring import cert_identity_trust, score_hit\n",
)
text += '''\n\ndef test_non_ocr_cert_with_wrong_card_number_is_untrusted():\n    listing = Listing(\n        item_id="wrong-cert", title="2021 Bundesliga PSA 10 #99",\n        url="https://example.test/wrong-cert", price=Money(20, "EUR"),\n        created_at=datetime.now(timezone.utc), buying_options=["FIXED_PRICE"],\n    )\n    cert = _cert()\n    trusted, reason = cert_identity_trust(\n        listing, cert, cert_source="Item-Specifics", cert_confidence=1.0\n    )\n    assert trusted is False\n    assert "kartennummer" in reason.casefold()\n    hit = score_hit(\n        listing, cert_number=cert.cert_number, cert_source="Item-Specifics",\n        cert_confidence=1.0, cert=cert,\n        market_value_listing_currency=MarketValue(Money(200, "EUR"), "PSA Sales", "hoch", 5),\n        priority_terms=[], demand_terms=[],\n    )\n    assert hit.cert_trusted is False\n    assert hit.market_value is None\n    assert hit.discount_pct is None\n    assert not any("niedrige PSA-10-Population" in r for r in hit.reasons)\n\n\ndef test_localized_subject_is_trusted_by_matching_card_number():\n    listing = Listing(\n        item_id="elfun-trust", title="POKEMON ELFUN EX 165 PSA 10 GEM MINT DE",\n        url="https://example.test/elfun", price=Money(100, "EUR"),\n        created_at=datetime.now(timezone.utc), buying_options=["FIXED_PRICE"],\n    )\n    cert = PSACertInfo(\n        cert_number="131778450", valid=True, grade="GEM MT 10", year="2025",\n        brand_title="POKEMON GERMAN WHT DE-WHITE FLARE", subject="WHIMSICOTT ex",\n        card_number="165", variety="SPECIAL ILLUSTRATION RARE", population=9,\n    )\n    trusted, reason = cert_identity_trust(\n        listing, cert, cert_source="OCR (Fallback)", cert_confidence=.95\n    )\n    assert trusted is True\n    assert "kartennummer" in reason.casefold()\n'''
path.write_text(text, encoding="utf-8")

Path("tests/test_psa_backfill.py").write_text(r'''from datetime import timedelta

from psa_sniper.models import Money, PSACertInfo
from psa_sniper.psa import cert_needs_api_upgrade, merge_cert_info
from psa_sniper.psa_backfill import backfill_state
from psa_sniper.state import default_state
from psa_sniper.util import iso_z, utc_now


class FakePSA:
    def __init__(self, cert):
        self.cert = cert
        self.calls_made = 0
        self.access_token = "token"
        self.api_auth_status = "ok"
    def get_api_cert(self, number):
        self.calls_made += 1
        return self.cert if number == self.cert.cert_number else None


def history_row():
    now = utc_now() - timedelta(hours=2)
    return {
        "item_id": "x",
        "title": "POKEMON ELFUN EX 165 PSA 10 GEM MINT DE",
        "url": "https://example.test/x",
        "price": {"value": 80, "currency": "EUR"},
        "total_cost": {"value": 80, "currency": "EUR"},
        "created_at": iso_z(now),
        "first_seen_at": iso_z(now),
        "last_seen_at": iso_z(now),
        "buying_options": ["FIXED_PRICE"],
        "score": 7,
        "is_hit": False,
        "price_status": "weak_indicator",
        "availability_status": "active",
        "cert_number": "131778450",
        "cert_source": "OCR (Fallback)",
        "cert_confidence": .95,
        "cert": {
            "cert_number": "131778450", "valid": True, "grade": "GEM MT 10",
            "year": "2025", "brand_title": "POKEMON GERMAN WHT DE-WHITE FLARE",
            "subject": "WHIMSICOTT ex", "card_number": "165", "variety": "SIR",
            "population": None, "population_higher": None,
            "estimate": {"value": 100, "currency": "EUR"}, "recent_sales": [],
            "data_source": "öffentliche PSA-Cert-Seite",
        },
        "market_value": {
            "money": {"value": 100, "currency": "EUR"}, "source": "PSA Estimate",
            "confidence": "niedrig", "sample_size": 0, "market_type": "psa_estimate",
            "required_edge": .25,
        },
        "discount_pct": .20,
        "score_breakdown": [],
    }


def settings():
    return {
        "max_psa_backfill_calls_per_run": 12,
        "psa_backfill_history_hours": 168,
        "hit_threshold": 11,
        "priority_terms": [], "demand_terms": [],
    }


def test_merge_preserves_web_market_and_prefers_api_population():
    old = PSACertInfo(
        cert_number="1", valid=True, grade="10", subject="A", card_number="1",
        population=None, estimate=Money(123, "EUR"), data_source="web",
    )
    api = PSACertInfo(
        cert_number="1", valid=True, grade="GEM MT 10", subject="A", card_number="1",
        population=9, data_source="PSA Public API",
    )
    merged = merge_cert_info(api, old)
    assert merged.population == 9
    assert merged.estimate.value == 123
    assert "PSA Public API" in merged.data_source
    assert cert_needs_api_upgrade(merged) is False


def test_backfill_upgrades_history_and_rescores_low_pop():
    state = default_state()
    state["history"] = [history_row()]
    api = PSACertInfo(
        cert_number="131778450", valid=True, grade="GEM MT 10", year="2025",
        brand_title="POKEMON GERMAN WHT DE-WHITE FLARE", subject="WHIMSICOTT ex",
        card_number="165", variety="SPECIAL ILLUSTRATION RARE", population=9,
        population_higher=0, data_source="PSA Public API",
    )
    result = backfill_state(state, settings(), FakePSA(api))
    assert result.checked_certs == 1
    assert result.upgraded_certs == 1
    assert result.rescored_rows == 1
    row = state["history"][0]
    assert row["cert"]["population"] == 9
    assert "PSA Public API" in row["cert"]["data_source"]
    assert row["cert_trusted"] is True
''', encoding="utf-8")

# PSA API-only helper tests.
path = Path("tests/test_psa.py")
text = path.read_text(encoding="utf-8")
text += '''\n\ndef test_api_only_lookup_does_not_fall_back_to_web(monkeypatch):\n    client = PSAClient(access_token="token", web_fallback=True, delay_seconds=0, max_calls=3)\n    monkeypatch.setattr(client, "_get_api", lambda cert: None)\n    called = {"web": 0}\n    monkeypatch.setattr(client, "_get_web", lambda cert: called.__setitem__("web", called["web"] + 1))\n    assert client.get_api_cert("12345678") is None\n    assert called["web"] == 0\n'''
path.write_text(text, encoding="utf-8")

# Config invariants.
Path("tests/test_quality_config.py").write_text('''import json\nfrom pathlib import Path\n\n\ndef test_overlap_cooldown_allows_next_three_hour_scan_recheck():\n    settings = json.loads(Path("config/settings.json").read_text(encoding="utf-8"))\n    assert settings["processed_cooldown_minutes"] < settings["automatic_scan_min_age_minutes"]\n    assert settings["max_psa_backfill_calls_per_run"] <= settings["max_psa_calls_per_run"]\n''', encoding="utf-8")

print("Audit-v2 quality patch applied")
