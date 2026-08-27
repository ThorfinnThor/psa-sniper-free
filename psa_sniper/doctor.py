from __future__ import annotations

import os
import shutil
from dataclasses import dataclass
from datetime import timedelta

from .config import load_queries, load_settings
from .ebay import EbayClient, EbayError
from .ocr import ocr_enabled
from .util import utc_now


@dataclass(slots=True)
class Check:
    level: str
    label: str
    detail: str


def run_doctor(live: bool = False) -> tuple[list[Check], bool]:
    checks: list[Check] = []
    ok = True
    try:
        settings = load_settings()
        queries = load_queries()
        if queries:
            checks.append(Check("OK", "Suchkonfiguration", f"{len(queries)} rotierende Queries"))
        else:
            checks.append(Check("FEHLER", "Suchkonfiguration", "keine Queries"))
            ok = False
    except Exception as exc:
        checks.append(Check("FEHLER", "Konfiguration", str(exc)))
        return checks, False

    max_calls = int(settings.get("max_ebay_calls_per_run", 30))
    daily = max_calls * 96
    level = "OK" if daily <= 4500 else "WARNUNG"
    checks.append(
        Check(level, "eBay-Call-Budget", f"theoretisch max. {daily} Calls/Tag bei 15-Minuten-Cron")
    )

    client_id = os.getenv("EBAY_CLIENT_ID", "").strip()
    client_secret = os.getenv("EBAY_CLIENT_SECRET", "").strip()
    if client_id and client_secret:
        checks.append(Check("OK", "eBay-Secrets", "Client ID und Client Secret vorhanden"))
    else:
        checks.append(Check("FEHLER", "eBay-Secrets", "EBAY_CLIENT_ID/EBAY_CLIENT_SECRET fehlen"))
        ok = False

    password = os.getenv("DASHBOARD_PASSWORD", "")
    if len(password) >= 16:
        checks.append(Check("OK", "Dashboard-Passwort", "mindestens 16 Zeichen"))
    elif password:
        checks.append(Check("FEHLER", "Dashboard-Passwort", "kürzer als 16 Zeichen"))
        ok = False
    else:
        checks.append(Check("WARNUNG", "Dashboard-Passwort", "für GitHub-Workflow erforderlich"))

    if ocr_enabled():
        if shutil.which("tesseract"):
            checks.append(Check("OK", "OCR", "Tesseract gefunden"))
        else:
            checks.append(Check("FEHLER", "OCR", "ENABLE_OCR=true, aber Tesseract fehlt"))
            ok = False
    else:
        checks.append(Check("INFO", "OCR", "deaktiviert; Scanner funktioniert mit Cert in Titel/Item-Specifics"))

    if os.getenv("PSA_ACCESS_TOKEN"):
        checks.append(Check("OK", "PSA API", "optionaler Access Token vorhanden"))
    elif bool(settings.get("enable_psa_web_fallback", True)):
        checks.append(Check("INFO", "PSA API", "kein Token; öffentliche Cert-Seite als Best-Effort-Fallback"))
    else:
        checks.append(Check("WARNUNG", "PSA API", "kein Token und Web-Fallback deaktiviert"))

    if live and client_id and client_secret:
        try:
            client = EbayClient(
                client_id,
                client_secret,
                environment=str(settings.get("environment", "production")),
                marketplace_id=str(settings.get("marketplace_id", "EBAY_DE")),
                delivery_country=str(settings.get("delivery_country", "DE")),
                buyer_postal_code=str(settings.get("buyer_postal_code", "")),
                max_calls=2,
                delay_seconds=0,
            )
            rows = client.search(queries[0], limit=1, started_after=utc_now() - timedelta(days=1))
            checks.append(Check("OK", "eBay Live-Test", f"Browse API erreichbar; {len(rows)} Ergebnis(se)"))
        except EbayError as exc:
            checks.append(Check("FEHLER", "eBay Live-Test", str(exc)))
            ok = False

    return checks, ok


def print_checks(checks: list[Check]) -> None:
    symbols = {"OK": "✓", "INFO": "i", "WARNUNG": "!", "FEHLER": "✗"}
    for check in checks:
        print(f"{symbols.get(check.level, '-')} {check.label}: {check.detail}")
