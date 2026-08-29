from __future__ import annotations

import re


def normalize_psa_access_token(value: str | None) -> str | None:
    """Normalize a PSA Public API secret without ever logging it.

    GitHub Secrets sometimes contain the copied HTTP prefix (``Bearer ...``)
    or surrounding quotes. PSAClient adds the Authorization scheme itself, so
    those wrappers must be removed before the token is handed to the client.
    """
    if not value:
        return None
    token = value.strip().strip('"\'').strip()
    token = re.sub(r"^authorization\s*:\s*", "", token, flags=re.IGNORECASE).strip()
    token = re.sub(r"^bearer\s+", "", token, flags=re.IGNORECASE).strip()
    return token or None
