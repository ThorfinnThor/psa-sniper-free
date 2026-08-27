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


def dashboard_payload(state: dict[str, Any]) -> dict[str, Any]:
    history = list(state.get("history", []))
    history.sort(key=lambda row: row.get("last_seen_at", ""), reverse=True)
    runs = list(state.get("runs", []))[:100]
    return {
        "schema_version": 1,
        "generated_at": iso_z(utc_now()),
        "hits": history,
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
                "schema_version": 1,
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
