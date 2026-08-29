from __future__ import annotations

import math
import os
import shutil
from dataclasses import dataclass
from datetime import timedelta

from .config import load_queries, load_settings
from .ebay import EbayClient, EbayError
from .ocr import ocr_enabled
from .point130 import load_point130_sales
from .psa_auth import normalize_psa_access_token
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
    interval_hours = max(0.25, float(settings.get("schedule_interval_hours", 0.25)))
    runs_per_day = math.ceil(24 / interval_hours)
    daily = max_calls * runs_per_day
    daily_limit = int(settings.get("ebay_daily_call_limit", 5000))
    required_reserve = int(settings.get("ebay_daily_reserve_calls", 350))
    reserve = daily_limit - daily
    if daily > daily_limit:
        level = "FEHLER"
        ok = False
    elif reserve < required_reserve:
        level = "WARNUNG"
    else:
        level = "OK"
    checks.append(
        Check(
            level,
            "eBay-Call-Budget",
            f"theoretisch max. {daily} Calls/Tag bei {interval_hours:g}h-Cron; "
            f"Puffer zu {daily_limit}: {reserve} (Zielreserve {required_reserve})",
        )
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

    raw_psa_token = os.getenv("PSA_ACCESS_TOKEN")
    normalized_psa_token = normalize_psa_access_token(raw_psa_token)
    if normalized_psa_token:
        normalized_note = (
            "; kopiertes Authorization-/Bearer-Präfix wird automatisch entfernt"
            if raw_psa_token and raw_psa_token.strip() != normalized_psa_token
            else ""
        )
        checks.append(Check("OK", "PSA API", f"optionaler Access Token vorhanden{normalized_note}"))
    elif bool(settings.get("enable_psa_web_fallback", True)):
        checks.append(Check("INFO", "PSA API", "kein Token; öffentliche Cert-Seite als Best-Effort-Fallback"))
    else:
        checks.append(Check("WARNUNG", "PSA API", "kein Token und Web-Fallback deaktiviert"))

    try:
        point130_sales = load_point130_sales()
    except (OSError, ValueError, TypeError) as exc:
        checks.append(Check("FEHLER", "130point Sold-Comps", str(exc)))
        ok = False
    else:
        if point130_sales:
            checks.append(
                Check(
                    "OK",
                    "130point Sold-Comps",
                    f"{len(point130_sales)} manuell verifizierte PSA-10-Verkäufe geladen",
                )
            )
        else:
            checks.append(
                Check(
                    "INFO",
                    "130point Sold-Comps",
                    "noch keine Verkäufe importiert; automatische Abfrage bleibt deaktiviert",
                )
            )

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
