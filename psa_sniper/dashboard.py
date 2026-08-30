from __future__ import annotations

import json
import shutil
from pathlib import Path
from typing import Any

from .config import ROOT
from .crypto import encrypt_json
from .state import load_state
from .util import iso_z, utc_now

TEMPLATE_DIR = ROOT / "site" / "template"
MIN_VERIFIED_PRICE_EDGE = 0.10


def _float_or_none(value: Any) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _infer_price_status(row: dict[str, Any]) -> str:
    availability = str(row.get("availability_status") or "active")
    if availability in {"ended", "unavailable"}:
        return "unavailable"
    if availability == "check_failed":
        return "live_check_failed"
    existing = str(row.get("price_status") or "").strip()
    if existing:
        return existing
    if bool(row.get("pure_auction")):
        return "auction"
    market = row.get("market_value")
    discount = _float_or_none(row.get("discount_pct"))
    if not isinstance(market, dict) or discount is None:
        return "unverified"
    confidence = str(market.get("confidence") or "").casefold()
    required_edge = max(
        MIN_VERIFIED_PRICE_EDGE,
        _float_or_none(market.get("required_edge")) or MIN_VERIFIED_PRICE_EDGE,
    )
    if confidence in {"hoch", "mittel"} and discount >= required_edge:
        return "verified_edge"
    if confidence == "niedrig":
        return "weak_indicator"
    if discount <= -0.10:
        return "over_market"
    return "no_edge"


def _normalize_record(row: dict[str, Any]) -> dict[str, Any]:
    clean = dict(row)
    original_hit = bool(clean.get("is_hit"))
    clean["scan_is_hit"] = original_hit
    clean["price_status"] = _infer_price_status(clean)
    if clean["price_status"] != "verified_edge":
        clean["is_hit"] = False
    clean.setdefault("score_breakdown", [])
    return clean


def _normalize_run(row: dict[str, Any]) -> dict[str, Any]:
    clean = dict(row)
    for field in ("results", "repricing_results"):
        results = clean.get(field)
        if isinstance(results, list):
            clean[field] = [
                _normalize_record(item)
                for item in results
                if isinstance(item, dict)
            ]
    return clean


def _dashboard_eligible(row: dict[str, Any]) -> bool:
    if str(row.get("availability_status") or "active") in {"ended", "unavailable"}:
        return False
    discount_value = _float_or_none(row.get("discount_pct"))
    market = row.get("market_value")
    if discount_value is None or not isinstance(market, dict):
        return True
    confidence = str(market.get("confidence") or "").casefold()
    if confidence == "hoch" and discount_value <= -0.25:
        return False
    if confidence == "mittel" and discount_value <= -0.50:
        return False
    return True


def dashboard_payload(state: dict[str, Any]) -> dict[str, Any]:
    archive_history = [
        _normalize_record(row)
        for row in list(state.get("history", []))
        if isinstance(row, dict)
    ]
    archive_history.sort(key=lambda row: row.get("last_seen_at", ""), reverse=True)
    history = [row for row in archive_history if _dashboard_eligible(row)]
    runs = [
        _normalize_run(row)
        for row in list(state.get("runs", []))[:100]
        if isinstance(row, dict)
    ]
    return {
        "schema_version": 5,
        "generated_at": iso_z(utc_now()),
        "hits": history,
        "archive_hits": archive_history,
        "runs": runs,
    }


def _replace_required(text: str, old: str, new: str, *, label: str) -> str:
    if old not in text:
        raise RuntimeError(f"Dashboard-Template unerwartet: {label} nicht gefunden")
    return text.replace(old, new)


def _coverage_js() -> str:
    return r'''function coverageData() {
  const run = state.payload?.runs?.[0];
  const notes = Array.isArray(run?.notes) ? run.notes : [];
  const coverageLine = notes.find(note => String(note).startsWith('Coverage:'));
  const priceDiagLine = notes.find(note => String(note).startsWith('PriceDiag:'));
  const statusLine = notes.find(note => String(note).startsWith('PSA API live:'));
  const values = {};
  const priceDiag = {};
  if (coverageLine) {
    String(coverageLine).replace(/^Coverage:\s*/, '').split(';').forEach(part => {
      const separator = part.indexOf('=');
      if (separator < 0) return;
      const key = part.slice(0, separator).trim();
      const raw = part.slice(separator + 1).trim();
      const number = Number(raw);
      values[key] = Number.isFinite(number) ? number : raw;
    });
  }
  if (priceDiagLine) {
    String(priceDiagLine).replace(/^PriceDiag:\s*/, '').split(';').forEach(part => {
      const separator = part.indexOf('=');
      if (separator < 0) return;
      const key = part.slice(0, separator).trim();
      const number = Number(part.slice(separator + 1).trim());
      priceDiag[key] = Number.isFinite(number) ? number : 0;
    });
  }
  const psaStatus = statusLine
    ? String(statusLine).split(':').slice(1).join(':').trim()
    : 'NICHT GETESTET';
  return { run, values, priceDiag, psaStatus };
}

function marketDetail(market) {
  if (!market) return 'Preisindikator: nicht verfügbar';
  const bits = [
    `Preisindikator: ${money(market.money)}`,
    market.source,
    `Vertrauen ${market.confidence}`,
    market.sample_size != null && market.market_type !== 'renaiss_fmv' ? `${market.sample_size} Comp(s)` : null,
    market.unique_sellers != null ? `${market.unique_sellers} unabh. Verkäufer` : null,
    market.price_low != null && market.price_high != null
      ? `Spanne ${money({value: market.price_low, currency: market.money?.currency})}–${money({value: market.price_high, currency: market.money?.currency})}`
      : null,
    Number.isFinite(Number(market.dispersion)) ? `Streuung ${percent(Number(market.dispersion))}` : null,
    `Gate ${percent(Number(market.required_edge ?? .10))}`,
  ].filter(Boolean);
  return bits.join(' · ');
}

function ensureCoverageElements() {
  let header = $('coverageHeader');
  let grid = $('coverage');
  if (!header) {
    header = el('div', 'result-bar');
    header.id = 'coverageHeader';
    $('stats').after(header);
  }
  if (!grid) {
    grid = el('section', 'stats-grid');
    grid.id = 'coverage';
    grid.setAttribute('aria-label', 'Preis-Coverage des letzten Scanner-Laufs');
    header.after(grid);
  }
  return { header, grid };
}

function renderCoverage() {
  const { header, grid } = ensureCoverageElements();
  const { run, values, psaStatus } = coverageData();
  const details = values.Details ?? run?.detailed_candidates ?? '–';
  header.replaceChildren(
    el('p', '', 'Preis-Coverage · letzter Lauf'),
    el('p', 'muted compact', run ? `Basis: ${details} Detailprüfungen` : 'Noch kein Lauf'),
  );
  const cards = [
    ['Details', details, 'Listings vollständig geprüft'],
    ['Cert erkannt', values.Cert ?? '–', 'Titel · Item-Specifics · OCR'],
    ['PSA API', psaStatus, `${values.PSA ?? 0} neue gültige API-Cert(s)`],
    ['Cert bestätigt', values.Verifiziert ?? '–', 'API · Cache · Web-Fallback'],
    ['POP vorhanden', values.POP ?? '–', 'Population erfolgreich verfügbar'],
    ['PSA Backfill', run?.psa_backfill_upgraded ?? 0, `${run?.psa_backfill_checked ?? 0} alte Cert(s) API-seitig geprüft`],
    ['Preisindikator', values.Preis ?? '–', 'Renaiss PSA-10 FMV · PSA Sales · eBay Comps'],
    ['Renaiss PSA 10', values.RenaissPreis ?? 0, 'aus echten abgeschlossenen Verkäufen modelliert'],
    ['eBay Comp-Suchen', values.eBayCompSuche ?? '–', `${values.eBayCompPreis ?? 0} brauchbare Comp-Preis(e)`],
    ['Comp-Details', values.eBayCompDetails ?? 0, 'vollständige Daten für schwache Top-Comps nachgeladen'],
    ['Preisvorteil bestätigt', values.Edge ?? '–', 'je Preisquelle erforderliches Gate erfüllt'],
    ['Repricing', run?.repricing_checked ?? 0, `${run?.repricing_improved ?? 0} Quelle(n) verbessert · ${run?.repricing_comp_detail_calls ?? 0} Comp-Details`],
    ['Live-Rechecks', run?.repricing_live_rechecks ?? 0, `${run?.repricing_expired ?? 0} beendet · ${run?.repricing_live_errors ?? 0} temporäre Fehler`],
    ['Sekundär entdeckt', run?.secondary_candidates ?? 0, 'ältere Fehlpreise aus Leave-One-Out-Comps'],
    ['Calls gesamt', run?.total_ebay_calls ?? run?.ebay_calls ?? '–', `davon ${run?.repricing_calls ?? 0} Repricing`],
  ];
  grid.replaceChildren(...cards.map(([label, value, note]) => {
    const card = el('article', 'stat-card');
    card.append(
      el('div', 'stat-label', label),
      el('div', 'stat-value', String(value)),
      el('div', 'stat-note', note),
    );
    return card;
  }));
}

function ensurePriceDiagnosticsElements() {
  let header = $('priceDiagHeader');
  let grid = $('priceDiag');
  if (!header) {
    header = el('div', 'result-bar');
    header.id = 'priceDiagHeader';
    $('coverage').after(header);
  }
  if (!grid) {
    grid = el('section', 'stats-grid');
    grid.id = 'priceDiag';
    grid.setAttribute('aria-label', 'Preisdiagnose des letzten Scanner-Laufs');
    header.after(grid);
  }
  return { header, grid };
}

function renderPriceDiagnostics() {
  const { header, grid } = ensurePriceDiagnosticsElements();
  const { run, priceDiag } = coverageData();
  const noPrice = Number(priceDiag.OhnePreis || 0);
  const weak = Number(priceDiag.Schwach || 0);
  header.replaceChildren(
    el('p', '', 'Warum fehlt / schwächelt der Preis?'),
    el('p', 'muted compact', run ? `${noPrice} ohne Preisindikator · ${weak} schwache Preisquelle(n)` : 'Noch keine Diagnosedaten'),
  );
  const cards = [
    ['Ohne Preisindikator', noPrice, 'nach allen verfügbaren Preiswegen'],
    ['Screening-Gate', priceDiag.UnterGate || 0, 'Pre-Score zu niedrig für zusätzliche Comp-Calls'],
    ['Keine sichere Identität', priceDiag.KeineIdentitaet || 0, 'Kartennummer / Subject / Set nicht belastbar genug'],
    ['Keine Suchtreffer', priceDiag.KeineSuchtreffer || 0, 'eBay lieferte für die Comp-Queries keine Treffer'],
    ['0 exakte Comps', priceDiag.KeineExaktenComps || 0, 'Treffer vorhanden, aber Identitätsfilter lehnten alle ab'],
    ['Nur wenige Comps', priceDiag.ZuWenigeComps || 0, 'weniger als 3 exakte Vergleichsangebote ohne Preisanker'],
    ['Budget blockiert', priceDiag.Budget || 0, 'Comp-Budget war für diesen Kandidaten ausgeschöpft'],
    ['Suchfehler', priceDiag.Suchfehler || 0, 'eBay-Comp-Suche technisch fehlgeschlagen'],
    ['Kein Zielpreis', priceDiag.KeinZielpreis || 0, 'Listing hatte keinen verwertbaren Gesamtpreis'],
    ['Sonstige Ursache', priceDiag.Sonstiges || 0, 'Identität vorhanden, aber kein belastbarer Preis entstanden'],
    ['Schwache Quellen', weak, 'Preis vorhanden, aber nicht stark genug für Kaufurteil'],
    ['davon PSA Estimate', priceDiag.SchwachPSAEstimate || 0, 'nur PSA-Schätzwert statt belastbarer Markt-Comps'],
    ['< 3 exakte Comps', priceDiag.SchwachComps || 0, 'Preisanker basiert auf sehr kleiner Stichprobe'],
    ['< 3 Verkäufer', priceDiag.SchwachVerkaeufer || 0, 'zu wenig unabhängige Angebotsquellen'],
    ['Hohe Streuung', priceDiag.SchwachStreuung || 0, 'Comp-Preise liegen zu weit auseinander'],
    ['Identität unvollständig', priceDiag.SchwachIdentitaet || 0, 'Listing-basierter oder unvollständig bestätigter Match'],
  ].filter(([label, value]) => label === 'Ohne Preisindikator' || Number(value) > 0);
  grid.replaceChildren(...cards.map(([label, value, note]) => {
    const card = el('article', 'stat-card');
    card.append(
      el('div', 'stat-label', label),
      el('div', 'stat-value', String(value)),
      el('div', 'stat-note', note),
    );
    return card;
  }));
}

'''


def _apply_dashboard_ui_defaults(output_dir: Path) -> None:
    app_path = output_dir / "app.js"
    app = app_path.read_text(encoding="utf-8")
    replacements = [
        ("  $('minScore').value = '7';", "  $('minScore').value = '4';", "Score-Reset"),
        (
            "  const score = el('span', `score-badge ${row.is_hit ? 'hot' : ''}`, `Score ${row.score ?? 0}`);",
            "  const scoreText = row.is_hit ? `Kauf-Hit · Score ${row.score ?? 0}` : `Beobachtung · Rohscore ${row.score ?? 0}`;\n"
            "  const score = el('span', `score-badge ${row.is_hit ? 'hot' : ''}`, scoreText);",
            "Score-Badge",
        ),
        (
            "  const discount = Number(row.discount_pct);",
            "  const discount = Number(row.discount_pct);\n"
            "  const requiredEdge = Number(row.market_value?.required_edge ?? 0.10);",
            "dynamische Preis-Gate-Schwelle",
        ),
        (
            "        ? `${distanceLabel(discount)} zum Vergleichswert. Für einen Kauf-Hit verlangen wir mindestens 10 % bestätigten Preisvorteil.`\n"
            "        : 'Für einen Kauf-Hit verlangen wir mindestens 10 % bestätigten Preisvorteil.',",
            "        ? `${distanceLabel(discount)} zum Vergleichswert. Für diese Preisquelle verlangen wir mindestens ${percent(requiredEdge)} bestätigten Preisvorteil.`\n"
            "        : `Für diese Preisquelle verlangen wir mindestens ${percent(requiredEdge)} bestätigten Preisvorteil.`,",
            "dynamischer Preis-Gate-Text",
        ),
        (
            "  if (status === 'auction') {",
            "  if (status === 'unavailable') {\n"
            "    return { tone: 'bad', title: 'Nicht mehr verfügbar', text: 'Das Zielangebot wurde live geprüft und ist beendet oder nicht mehr kaufbar.' };\n"
            "  }\n"
            "  if (status === 'live_check_failed') {\n"
            "    return { tone: 'warn', title: 'Live-Recheck fehlgeschlagen', text: 'Der letzte Verfügbarkeitscheck war vorübergehend nicht möglich. Bis zur nächsten erfolgreichen Prüfung ist dies ausdrücklich kein Kauf-Hit.' };\n"
            "  }\n"
            "  if (status === 'auction') {",
            "Verfügbarkeitsstatus",
        ),
        (
            "metric('Abstand', row.discount_pct == null ? '–' : distanceLabel(row.discount_pct), row.discount_pct >= .15)",
            "metric('Abstand', row.discount_pct == null ? '–' : distanceLabel(row.discount_pct), row.price_status === 'verified_edge')",
            "grüner Preisabstand",
        ),
        (
            "  renderStats(all);\n  renderHealth();",
            "  renderStats(all);\n  renderCoverage();\n  renderPriceDiagnostics();\n  renderHealth();",
            "Coverage-Render",
        ),
        (
            "function renderHealth() {",
            _coverage_js() + "function renderHealth() {",
            "Coverage-UI",
        ),
        ("ageHours <= 1.5", "ageHours <= 3.75", "Scanner-Aktualität"),
        (
            "    row.market_value ? `Preisindikator: ${money(row.market_value.money)} · ${row.market_value.source} · Vertrauen ${row.market_value.confidence}` : 'Preisindikator: nicht verfügbar',",
            "    row.market_value ? marketDetail(row.market_value) : 'Preisindikator: nicht verfügbar',",
            "Marktqualitätsdetails",
        ),
        (
            "    row.returns_accepted == null ? 'Rückgabe: unbekannt' : `Rückgabe: ${row.returns_accepted ? 'akzeptiert' : 'nicht akzeptiert'}`,",
            "    row.returns_accepted == null ? 'Rückgabe: unbekannt' : `Rückgabe: ${row.returns_accepted ? 'akzeptiert' : 'nicht akzeptiert'}`,\n"
            "    row.price_checked_at ? `Preis zuletzt geprüft ${formatRelative(row.price_checked_at)}` : null,\n"
            "    row.pricing_identity ? `Preisidentität: ${[row.pricing_identity.subjects?.join(' '), row.pricing_identity.set_code, '#' + row.pricing_identity.card_number, row.pricing_identity.language, row.pricing_identity.edition, row.pricing_identity.variant].filter(Boolean).join(' · ')}` : null,",
            "Repricing-Details",
        ),
        (
            "  if (Array.isArray(run.results)) return run.results;",
            "  if (Array.isArray(run.results)) {\n"
            "    const merged = [...(Array.isArray(run.repricing_results) ? run.repricing_results : []), ...run.results];\n"
            "    const seen = new Set();\n"
            "    return merged.filter(item => item?.item_id && !seen.has(item.item_id) && seen.add(item.item_id));\n"
            "  }",
            "Run-Repricing-Ergebnisse",
        ),
        (
            "    el('span', '', `${run.hits ?? 0} Hit(s) · ${run.near_hits ?? 0} Beobachtung(en)`),",
            "    el('span', '', `${run.hits ?? 0} Scan-Hit(s) · ${run.near_hits ?? 0} Scan-Beobachtung(en) · Repricing ${run.repricing_hits ?? 0} Hit(s) / ${run.repricing_checked ?? 0} geprüft`),",
            "getrennte Run-Zähler",
        ),
        (
            "    const expected = Number(run.hits || 0) + Number(run.near_hits || 0);",
            "    const expected = Number(run.hits || 0) + Number(run.near_hits || 0) + Number(run.repricing_checked || 0);",
            "Run-Expand Repricing",
        ),
        (
            "  ['Zeit', 'Queries', 'Frisch', 'Details', 'Hits / Watch', 'Calls'].forEach(value => header.append(el('span', '', value)));",
            "  ['Zeit', 'Queries', 'Frisch', 'Details', 'Scan H/W', 'Calls gesamt'].forEach(value => header.append(el('span', '', value)));",
            "Run-Header",
        ),
        (
            "      run.ebay_calls ?? '–',",
            "      run.total_ebay_calls ?? run.ebay_calls ?? '–',",
            "Run-Gesamtcalls",
        ),
    ]
    for old, new, label in replacements:
        app = _replace_required(app, old, new, label=label)
    app_path.write_text(app, encoding="utf-8")

    index_path = output_dir / "index.html"
    index = index_path.read_text(encoding="utf-8")
    index_replacements = [
        ("<span>Min. Score</span>", "<span>Min. Screening-Score</span>", "Score-Label"),
        ('<input id="minScore" type="number" min="0" max="50" value="7">', '<input id="minScore" type="number" min="4" max="50" value="4">', "Score-Eingabe"),
    ]
    for old, new, label in index_replacements:
        index = _replace_required(index, old, new, label=label)
    index_path.write_text(index, encoding="utf-8")


def build_dashboard(
    state_file: Path,
    output_dir: Path,
    *,
    password: str | None,
    plain: bool = False,
) -> Path:
    state = load_state(state_file)
    payload = dashboard_payload(state)
    if output_dir.exists():
        shutil.rmtree(output_dir)
    shutil.copytree(TEMPLATE_DIR, output_dir)
    _apply_dashboard_ui_defaults(output_dir)
    if plain:
        envelope: dict[str, Any] = {"format": "plain", "payload": payload}
    else:
        if not password:
            raise ValueError("DASHBOARD_PASSWORD fehlt")
        envelope = encrypt_json(payload, password)
    (output_dir / "data.enc.json").write_text(
        json.dumps(envelope, ensure_ascii=False, separators=(",", ":")) + "\n",
        encoding="utf-8",
    )
    (output_dir / "meta.json").write_text(
        json.dumps(
            {"schema_version": 5, "encrypted": not plain, "generated_at": payload["generated_at"]},
            separators=(",", ":"),
        )
        + "\n",
        encoding="utf-8",
    )
    (output_dir / ".nojekyll").write_text("", encoding="utf-8")
    shutil.copy2(output_dir / "index.html", output_dir / "404.html")
    return output_dir
