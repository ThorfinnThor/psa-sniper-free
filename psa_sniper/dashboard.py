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
    if confidence in {"hoch", "mittel"} and discount >= MIN_VERIFIED_PRICE_EDGE:
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
    # Legacy entries used to become Hits from rarity/title signals alone. Under the
    # current rules only a verified price edge can keep the Kauf-Hit label.
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
    """Keep the main dashboard focused on plausible buys, not obvious overpricing."""
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
        "schema_version": 3,
        "generated_at": iso_z(utc_now()),
        "hits": history,
        # Encrypted with the rest of the payload; used only for legacy run drill-down.
        "archive_hits": archive_history,
        "runs": runs,
    }


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
                "schema_version": 3,
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
