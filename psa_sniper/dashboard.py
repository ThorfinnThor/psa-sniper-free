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
    results = clean.get("results")
    if isinstance(results, list):
        clean["results"] = [
            _normalize_record(item)
            for item in results
            if isinstance(item, dict)
        ]
    return clean


def _dashboard_eligible(row: dict[str, Any]) -> bool:
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
        "schema_version": 4,
        "generated_at": iso_z(utc_now()),
        "hits": history,
        "archive_hits": archive_history,
        "runs": runs,
    }


def _replace_required(text: str, old: str, new: str, *, label: str) -> str:
    if old not in text:
        raise RuntimeError(f"Dashboard-Template unerwartet: {label} nicht gefunden")
    return text.replace(old, new)


def _apply_dashboard_ui_defaults(output_dir: Path) -> None:
    app_path = output_dir / "app.js"
    app = app_path.read_text(encoding="utf-8")
    replacements = [
        ("  view: 'all',", "  view: 'hits',", "initiale Ansicht"),
        ("  $('minScore').value = '7';", "  $('minScore').value = '4';", "Score-Reset"),
        ("  state.view = 'all';", "  state.view = 'hits';", "Ansicht-Reset"),
        (
            "  setActiveChip('viewChips', $('viewChips').querySelector('[data-view=\"all\"]'));",
            "  setActiveChip('viewChips', $('viewChips').querySelector('[data-view=\"hits\"]'));",
            "aktiver Ansicht-Chip",
        ),
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
    ]
    for old, new, label in replacements:
        app = _replace_required(app, old, new, label=label)
    app_path.write_text(app, encoding="utf-8")

    index_path = output_dir / "index.html"
    index = index_path.read_text(encoding="utf-8")
    index_replacements = [
        ("<span>Min. Score</span>", "<span>Min. Screening-Score</span>", "Score-Label"),
        (
            '<input id="minScore" type="number" min="0" max="50" value="7">',
            '<input id="minScore" type="number" min="4" max="50" value="4">',
            "Score-Eingabe",
        ),
        (
            '<button class="chip active" data-view="all" type="button">Alle</button>',
            '<button class="chip" data-view="all" type="button">Alle</button>',
            "Alle-Chip",
        ),
        (
            '<button class="chip" data-view="hits" type="button">🔥 Kauf-Hits</button>',
            '<button class="chip active" data-view="hits" type="button">🔥 Kauf-Hits</button>',
            "Kauf-Hit-Chip",
        ),
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
            {
                "schema_version": 4,
                "encrypted": not plain,
                "generated_at": payload["generated_at"],
            },
            separators=(",", ":"),
        )
        + "\n",
        encoding="utf-8",
    )
    (output_dir / ".nojekyll").write_text("", encoding="utf-8")
    shutil.copy2(output_dir / "index.html", output_dir / "404.html")
    return output_dir
