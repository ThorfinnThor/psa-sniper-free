# Fehlerdiagnose

## Workflow startet nicht

- Unter `Actions` prüfen, ob Workflows für das Repository aktiviert sind.
- `.github/workflows/sniper.yml` muss auf der Default Branch liegen.
- Zeitgesteuerte Workflows können verspätet starten.
- GitHub kann Scheduled Workflows in lange inaktiven öffentlichen Repositories deaktivieren; ein manueller Lauf aktiviert sie wieder.

## `EBAY_CLIENT_ID fehlt`

Repository → `Settings → Secrets and variables → Actions → Secrets`.

Der Name muss exakt `EBAY_CLIENT_ID` lauten. Analog für `EBAY_CLIENT_SECRET`.

## eBay OAuth 401

Häufige Ursachen:

- Sandbox-Client-ID mit Production-Secret oder umgekehrt,
- Leerzeichen beim Kopieren,
- Secret wurde regeneriert,
- Production-Keyset ist disabled,
- Client ID und Client Secret wurden vertauscht.

## eBay Browse 403

Der OAuth-Token kann funktionieren, obwohl die Browse API in Production nicht freigeschaltet ist. Prüfe im eBay Developer Portal die Buy-API-/Browse-Production-Anforderungen und den Status deiner App.

Das Projekt enthält keinen Scraping-Workaround, der eine fehlende eBay-Freigabe umgeht.

## eBay 429

- keine zusätzlichen manuellen Runs starten,
- `max_ebay_calls_per_run` nicht erhöhen,
- Cron-Intervall prüfen,
- Tageslimit im eBay Developer Portal kontrollieren.

## Dashboard-Deploy schlägt fehl

- Repository → `Settings → Pages`.
- Source muss `GitHub Actions` sein.
- Variable `ENABLE_DASHBOARD=true` prüfen.
- Bei GitHub Free muss das Repository für Pages öffentlich sein.
- Prüfen, ob GitHub Pages im Konto/Repository erlaubt ist.

## Dashboard zeigt nur die Passwortseite

Das ist bei verschlüsselten Daten korrekt. Das Passwort ist der Inhalt des Secrets `DASHBOARD_PASSWORD`, nicht der eBay-Key.

## Passwort wird abgelehnt

- Groß-/Kleinschreibung prüfen.
- Mindestens 16 Zeichen.
- Wenn das Secret nach dem ersten Lauf geändert wurde, kann der alte State nicht mit dem neuen Passwort entschlüsselt werden.
- Entweder mit dem alten Passwort re-encrypten oder Branch `sniper-state` löschen und neu beginnen.

## Dashboard bleibt leer

- Unter Actions die öffentliche Summary des letzten Runs ansehen.
- `fresh_listings`, `Detailprüfungen`, `Hits` und `Beobachtung` kontrollieren.
- Bei null frischen Listings Suchbegriffe und `run_window_minutes` prüfen.
- Bei null Details können alle Items bereits im Cooldown liegen.
- `dashboard_min_score` testweise senken, zum Beispiel auf 5.
- Demo prüfen:

```bash
python -m psa_sniper demo --output site/dist
python -m http.server 8000 -d site/dist
```

## Keine POP-Daten

- Cert-Nummer fehlt in Titel/Item-Specifics und OCR ist deaktiviert.
- OCR konnte das Bild nicht lesen.
- PSA Public API Token fehlt oder ist ungültig.
- Öffentliche Cert-Seite wurde geändert oder blockiert.
- PSA-Call-Budget war ausgeschöpft.

OCR einschalten:

```text
ENABLE_OCR = true
```

Optional `PSA_ACCESS_TOKEN` als Secret hinterlegen.

## OCR-Schritt schlägt fehl

- GitHub Variable muss exakt `ENABLE_OCR=true` sein.
- Der Workflow installiert Tesseract nur bei diesem Wert.
- Lokale Installation muss im `PATH` liegen.
- Unscharfe Bilder bleiben Best Effort; das ist kein Softwarefehler.

## Telegram sendet nichts

- Bot-Token prüfen.
- Dem Bot zuerst eine Nachricht senden.
- Chat-ID prüfen.
- Beide Secrets müssen gesetzt sein.
- Ein Alert wird erst ab `hit_threshold` ausgelöst.
- Treffer bleibt im Dashboard, auch wenn Telegram fehlschlägt.

## `sniper-state` kann nicht gepusht werden

- Workflow benötigt `contents: write`.
- Repository Actions-Einstellung darf Schreibzugriff des `GITHUB_TOKEN` nicht global verbieten.
- Branch Protection darf `sniper-state` nicht blockieren.
- Bei Organisationsrichtlinien kann ein Administrator die Berechtigung freigeben müssen.

## Tests lokal

```bash
pip install -r requirements-dev.txt
pytest -q
python -m compileall -q psa_sniper
node --check site/template/app.js
```
