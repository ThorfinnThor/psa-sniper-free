from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]


def load_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _env_json(name: str) -> dict[str, Any] | None:
    raw = os.getenv(name, "").strip()
    if not raw:
        return None
    data = json.loads(raw)
    if not isinstance(data, dict):
        raise ValueError(f"{name} muss ein JSON-Objekt enthalten")
    return data


def load_settings() -> dict[str, Any]:
    settings = load_json(ROOT / "config" / "settings.json")
    override = _env_json("SETTINGS_OVERRIDE_JSON")
    if override:
        settings.update(override)
    return settings


def load_queries() -> list[str]:
    cfg = _env_json("SEARCH_CONFIG_JSON") or load_json(ROOT / "config" / "searches.json")
    terms = cfg.get("terms", [])
    patterns = cfg.get("patterns", [])
    queries: list[str] = []
    for term in terms:
        for pattern in patterns:
            try:
                queries.append(str(pattern).format(term=term))
            except (KeyError, ValueError) as exc:
                raise ValueError(f"Ungültiges Suchmuster {pattern!r}: {exc}") from exc
    queries.extend(str(x) for x in cfg.get("extra_queries", []))
    return list(dict.fromkeys(q.strip() for q in queries if q and q.strip()))


def state_path() -> Path:
    raw = os.getenv("STATE_PATH", "data/state.json")
    path = Path(raw)
    return path if path.is_absolute() else ROOT / path
