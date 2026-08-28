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


def _dashboard_eligible(row: dict[str, Any]) -> bool:
    """Keep the dashboard focused on plausible buys, not obvious overpricing."""
    discount = row.get("discount_pct")
    market = row.get("market_value")
    if discount is None or not isinstance(market, dict):
        return True
    try:
        discount_value = float(discount)
    except (TypeError, ValueError):
        return True

    confidence = str(market.get("confidence") or "").casefold()
    # High-confidence PSA sales: 25%+ over the indicator is not a sniper candidate.
    if confidence == "hoch" and discount_value <= -0.25:
        return False
    # With only medium confidence we use a wider safety margin.
    if confidence == "mittel" and discount_value <= -0.50:
        return False
    return True


def dashboard_payload(state: dict[str, Any]) -> dict[str, Any]:
    archive_history = [row for row in list(state.get("history", [])) if isinstance(row, dict)]
    archive_history.sort(key=lambda row: row.get("last_seen_at", ""), reverse=True)
    history = [row for row in archive_history if _dashboard_eligible(row)]
    runs = list(state.get("runs", []))[:100]
    return {
        "schema_version": 2,
        "generated_at": iso_z(utc_now()),
        "hits": history,
        # This is encrypted together with the rest of the dashboard payload. It exists
        # only so older scanner runs can be linked back to their historical cards.
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
                "schema_version": 2,
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