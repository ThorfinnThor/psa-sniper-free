# PSA Sniper Free 1.0

Ein kostenloser, systematischer Scanner für **neu eingestellte PSA-10-Karten auf eBay**. Er sucht nicht nur nach dem Begriff „low pop“, sondern versucht schlecht beschriebene Listings zu erkennen, die tatsächliche Karte über die PSA-Cert-Nummer zu identifizieren und daraus einen nachvollziehbaren Hit-Score zu bilden.

Die Version enthält eine **verschlüsselte Web-GUI**, rotierende Suchabfragen, eine dauerhafte verschlüsselte Trefferhistorie, Versandkosten- und Auktionslogik, Telegram-/Discord-Alerts, Tests und einen Diagnosemodus.

> **Wichtig:** Das Projekt ist kostenlos nutzbar. Ob dein eBay-Keyset die Browse API in Production verwenden darf, entscheidet jedoch eBay. Ein Developer-Konto und Production-Keys allein garantieren diese zusätzliche Freigabe nicht.

## Was Version 1.0 systematisch macht

1. Erstellt aus deinen Märkten und Suchmustern automatisch einen Suchpool.
2. Verwendet pro Lauf nur einen rotierenden Teil des Pools, damit nicht immer dieselben Queries dein API-Budget verbrauchen.
3. Sucht mit `newlyListed` und einem Zeitfilter nach frischen Listings.
4. Filtert Preisrahmen und standardmäßig reine Auktionen heraus.
5. Lädt für die interessantesten Kandidaten die eBay-Detaildaten.
6. Ermittelt die PSA-Cert-Nummer aus:
   - eBay Item-Specifics,
   - dem Titel,
   - optional dem Slab-Bild per Tesseract-OCR.
7. Prüft die Cert-Daten optional über die PSA Public API und ansonsten als vorsichtigen Best-Effort-Fallback über die öffentliche Cert-Seite.
8. Berücksichtigt unter anderem:
   - bestätigten PSA-10-Grade,
   - PSA-10-Population,
   - Informationslücken im eBay-Titel,
   - Alter der Karte,
   - erkennbare Sammlerrelevanz,
   - Verkäuferbewertung,
   - Kartenpreis **plus Versand**,
   - Qualität der Preisquelle,
   - Unsicherheit einer OCR-Erkennung.
9. Verhindert, dass ein niedriger aktueller Auktionspreis als vermeintlicher Sofortkauf-Fehlpreis gewertet wird.
10. Speichert Hits und Beobachtungen verschlüsselt und zeigt sie in einer filterbaren GitHub-Pages-GUI an.
11. Sendet neue Hits optional sofort an Telegram oder Discord.

## Dashboard

Die GUI bietet:

- Suche nach Karte, Spieler, Charakter, Set, Variante oder Cert,
- Filter nach Mindestscore, maximaler POP und maximalen Gesamtkosten,
- Ansichten für Hits, Beobachtung, Sofortkauf und Auktion,
- intelligenter Startfilter: Kauf-Hits, wenn vorhanden, sonst direkt die Beobachtungen,
- direkte Schaltfläche zu Beobachtungen, wenn ein manuell gewählter Kauf-Hit-Filter leer ist,
- Sortierung nach Aktualität, Score, POP, Preis und Preisabstand,
- Kartenbild, eBay-Link, PSA-Link, Preis, Versand, POP, Score-Gründe und Warnungen,
- direkter Renaiss-Lookup für die exakt erkannte PSA-10-Identität,
- Verkäuferdaten und Listing-Alter,
- Historie der letzten Scanner-Läufe,
- lokale Markierungen `Merken`, `Gekauft` und `Ausblenden`.

Die Markierungen werden nur im Browser per `localStorage` gespeichert. Sie landen nicht auf GitHub.

---

# Schnellstart mit GitHub Actions

## Voraussetzungen

- ein GitHub-Konto,
- ein eBay-Developer-Konto,
- ein **aktives Production-Keyset**,
- Browse-API-Zugriff in Production,
- ein starkes Dashboard-Passwort.

Für häufige, vollständig kostenlose Läufe ist ein **öffentliches GitHub-Repository** vorgesehen. Bei GitHub Free ist GitHub Pages für öffentliche Repositories verfügbar und Standard-Runner für öffentliche Repositories verursachen keine Actions-Minutenkosten. Die Treffer bleiben trotzdem verschlüsselt.

## 1. Repository anlegen

1. Auf GitHub ein neues Repository erstellen.
2. Für die kostenlosen geplanten Ausführungen `Public` wählen.
3. Den **Inhalt** dieses Projektordners hochladen, nicht den übergeordneten Ordner.
4. Prüfen, dass sich `.github/workflows/sniper.yml` direkt im Repository befindet.

Die Branch `sniper-state` wird beim ersten erfolgreichen Lauf automatisch erstellt. Sie enthält nur einen verschlüsselten Snapshot und sollte nicht manuell bearbeitet oder durch Branch Protection blockiert werden.

## 2. GitHub Secrets hinterlegen

Repository öffnen:

`Settings → Secrets and variables → Actions → Secrets → New repository secret`

### Erforderlich

| Secret | Inhalt |
|---|---|
| `EBAY_CLIENT_ID` | Production App ID / Client ID von eBay |
| `EBAY_CLIENT_SECRET` | Production Cert ID / Client Secret von eBay |
| `DASHBOARD_PASSWORD` | starkes, einzigartiges Passwort; mindestens 16, empfohlen 24+ zufällige Zeichen |

Verwende **keine Sandbox-Keys** für echte Listings. Teile die Keys und das Dashboard-Passwort niemals im Chat oder in Issues.

### Optional

| Secret | Zweck |
|---|---|
| `SEARCH_CONFIG_JSON` | hält deine echten Suchbegriffe trotz öffentlichem Repo geheim |
| `SETTINGS_OVERRIDE_JSON` | überschreibt einzelne Scanner-Einstellungen ohne Commit |
| `PSA_ACCESS_TOKEN` | bevorzugt die offizielle PSA Public API vor dem Web-Fallback; reiner Token oder kopierter `Authorization: Bearer …`-Wert |
| `RENAISS_API_KEY` / `RENAISS_API_SECRET` | optionaler Partnerzugang für bis zu 10.000 Renaiss-Abfragen/Tag; ohne Secrets gilt der öffentliche 10/Tag-Tarif |
| `POINT130_SOLD_COMPS_JSON` | nur Legacy: optionaler manueller Import; standardmäßig deaktiviert |
| `TELEGRAM_BOT_TOKEN` | Telegram-Alerts |
| `TELEGRAM_CHAT_ID` | Zielchat für Telegram-Alerts |
| `DISCORD_WEBHOOK_URL` | Discord-Alerts |

## 3. GitHub Variables hinterlegen

`Settings → Secrets and variables → Actions → Variables → New repository variable`

| Variable | Empfohlener Wert | Zweck |
|---|---:|---|
| `ENABLE_DASHBOARD` | `true` | verschlüsseltes GitHub-Pages-Dashboard veröffentlichen |
| `ENABLE_OCR` | `true` | Cert-Nummern aus Slab-Bildern erkennen |

OCR ist optional. Ohne OCR werden Cert-Nummern aus Titel und Item-Specifics verarbeitet. Für schlecht betitelte Listings ist OCR jedoch ein wesentlicher Teil des Vorteils.

## 4. GitHub Pages aktivieren

1. Repository → `Settings → Pages`.
2. Unter `Build and deployment` als Source **GitHub Actions** auswählen.
3. Prüfen, dass die Variable `ENABLE_DASHBOARD=true` vorhanden ist.

Das Dashboard ist technisch öffentlich erreichbar, aber `data.enc.json` und die dauerhafte State-Branch sind mit AES-256-GCM verschlüsselt. Ohne ein starkes Passwort ist diese Konstruktion nicht sicher gegen Offline-Passwortversuche.

## 5. Ersten Lauf starten

1. Repository → `Actions`.
2. Workflow `PSA Sniper` auswählen.
3. `Run workflow` anklicken.
4. Alle Schritte kontrollieren.
5. Nach erfolgreichem Deploy den Link im Job `deploy-dashboard` öffnen.
6. Dashboard mit `DASHBOARD_PASSWORD` entsperren.

Danach läuft der Workflow standardmäßig zu Minute `07`, `22`, `37` und `52` jeder Stunde.

## 6. eBay-Zugriff eindeutig prüfen

Ein erfolgreicher OAuth-Token beweist noch nicht, dass die Browse API in Production freigeschaltet ist. Der entscheidende Test ist der echte Browse-Aufruf im Schritt `eBay scannen` oder lokal:

```bash
python -m psa_sniper doctor --live
```

Typische Ergebnisse:

- `✓ eBay Live-Test`: OAuth und Browse API funktionieren.
- `401`: meist falsches Keyset oder Client Secret.
- `403`: häufig fehlende Production-/Buy-API-Freigabe.
- `429`: Rate Limit; später erneut ausführen und Call-Budget prüfen.

---

# Suchstrategie konfigurieren

## Öffentliche Standardkonfiguration

`config/searches.json` enthält einen generischen Startpunkt:

```json
{
  "terms": [
    "Pokemon",
    "One Piece Card Game",
    "Topps Chrome",
    "Panini Prizm"
  ],
  "patterns": [
    "PSA 10 {term}",
    "PSA10 {term}",
    "GEM MT 10 {term}",
    "PSA GEM {term}",
    "PSA graded 10 {term}"
  ],
  "extra_queries": [
    "PSA 10 promo",
    "PSA 10 refractor",
    "PSA 10 rookie",
    "PSA 10 parallel"
  ]
}
```

Der Scanner bildet daraus 24 Queries. Die aktuelle Standardkonfiguration verarbeitet den vollständigen Pool pro Deep-Scan. Bei einem kleineren Call-Budget greift weiterhin die persistente Round-Robin-Rotation.

## Private Strategie als Secret

Für einen echten Vorteil sollten konkrete Setcodes, Sprachen, Promos, Parallels und Kartennummernbereiche verwendet werden. Lege diese nicht in einem öffentlichen Commit ab, sondern als einzeiliges `SEARCH_CONFIG_JSON`-Secret:

```json
{"terms":["SV-P Promo","S-P Promo","OP05","Topps Chrome Bundesliga X-Fractor"],"patterns":["PSA 10 {term}","PSA10 {term}","GEM MT 10 {term}","PSA GEM {term}"],"extra_queries":["PSA 10 Japanese promo","PSA 10 numbered refractor"]}
```

Die Suchabfragen werden gemeinsam mit der Trefferhistorie nur innerhalb des verschlüsselten States gespeichert.

## Einstellungen ohne Commit überschreiben

Beispiel für `SETTINGS_OVERRIDE_JSON`:

```json
{"hit_threshold":13,"dashboard_min_score":8,"maximum_price":500,"priority_terms":["Pikachu","Luffy","Messi"]}
```

Das Secret überschreibt nur die angegebenen Werte aus `config/settings.json`.

## Automatische PSA-10-Preise über Renaiss Index

Rohkartenpreise von Cardmarket oder TCGplayer werden bewusst **nicht** als PSA-10-Marktwert verwendet. Primäre externe Preisquelle ist der dokumentierte Renaiss Index. Er modelliert einen PSA-10-Fair-Market-Value aus echten abgeschlossenen Verkäufen und liefert eine eigene Konfidenzstufe.

Der Scanner sucht mit Subject, Setcode, Kartennummer, Sprache und `PSA 10`. Ein Preis wird nur übernommen, wenn genau ein Katalogeintrag alle bekannten Identitätsmerkmale erfüllt. `039/100` darf dabei mit der API-Kartennummer `39` übereinstimmen, aber nur zusammen mit identischem Subject, Set und Sprache. Mehrdeutige, falsche oder veraltete Ergebnisse werden verworfen.

- Public API: 10 Abfragen pro Tag und IP, keine Anmeldung nötig. Der 3-Stunden-Scanner nutzt deshalb höchstens eine neue Public-Abfrage pro Lauf und arbeitet sonst aus dem 24-Stunden-Cache.
- Partner API: bis zu 10.000 Abfragen pro Tag mit `RENAISS_API_KEY` und `RENAISS_API_SECRET`.
- Ergebnisse werden mindestens 24 Stunden gecacht.
- Hohe Konfidenz verlangt mindestens 12 %, mittlere 15 % und niedrige 25 % Preisvorteil.
- Danach folgen offizielle PSA-Sales-Daten, sofern verfügbar, und konservative aktive eBay-Comps als Fallback.

130point ist nicht mehr Teil der Standardbewertung. Der alte manuelle Import kann für bestehende Installationen mit `{"enable_point130_legacy":true}` in `SETTINGS_OVERRIDE_JSON` reaktiviert werden, löst aber das Abdeckungsproblem nicht und wird nicht empfohlen.

---

# Kosten- und API-Budget

Die Standardkonfiguration verwendet maximal:

- 24 Discovery-Suchaufrufe pro Deep-Scan,
- bis zu 470 Discovery-Detailaufrufe (durch die Quota-Verteilung im Volllauf derzeit 266),
- ungefähr 140 priorisierte Preisvergleichssuchen,
- bis zu 48 gezielte Voll-Details für schwache Top-Comps,
- ungefähr 100 Repricing-Aufrufe einschließlich bis zu 24 Comp-Details,
- insgesamt höchstens 575 eBay-Aufrufe pro Deep-Scan.

Der Scheduler prüft alle fünf Minuten, ob Arbeit fällig ist. Ein echter Deep-Scan wird standardmäßig aber nur ungefähr alle drei Stunden ausgeführt. Vor jedem Lauf reduziert die Quota-Logik die Teilbudgets anhand der rollierenden 24-Stunden-Nutzung und – falls verfügbar – der eBay-Analytics.

Theoretisches Maximum:

```text
575 × 8 = 4.600 eBay-Calls pro Tag
```

Das ist nur die harte theoretische Obergrenze. Die Quota-Logik reserviert standardmäßig 350 Calls und reduziert spätere Läufe, sobald die rollierende Nutzung zu hoch wird. OAuth-Aufrufe und externe PSA-/ECB-Aufrufe sind separate Dienste.

Vergleichspreise werden nicht mehr in Discovery-Reihenfolge gesucht. Zuerst lädt der Scanner alle Detailkandidaten, bestimmt Identität, Cert und vorhandene Caches und rankt anschließend global. Danach verteilt er die Comp-Suchen in Runden: Jeder geeignete Kandidat erhält zunächst eine Primärsuche; erst anschließend werden Fallback-Abfragen und zweite Ergebnisseiten ausgeführt. Identische Cert- und Listing-Abfragen werden nur einmal an eBay gesendet, ihre Ergebnisse aber weiterhin mit beiden unabhängigen Identitätsfiltern ausgewertet. So bleibt die Qualitätspriorisierung erhalten, ohne dass einzelne Kandidaten oder doppelte Netzaufrufe das knappe Budget vorzeitig aufbrauchen.

Wenn ein rechnerisch interessanter Markt nur deshalb schwach bleibt, weil eBays Suchzusammenfassungen Sprache, Set, Variante oder Verkäufer nicht vollständig enthalten, lädt der Scanner für die bestpriorisierten Fälle bis zu drei vollständige Comp-Datensätze nach. Explizit widersprüchliche Karten werden dabei nicht angereichert. Suchseiten, Comp-Details, Discovery-Details und Repricing besitzen getrennte Quota-Pools und bleiben gemeinsam unter der harten Laufgrenze.

Das Dashboard zeigt bei schwachen Preisquellen nicht mehr nur einen Sammelhinweis, sondern nennt pro Karte Quellenkonfidenz, verfügbare Stichprobengröße, unabhängige Verkäufer, Preisstreuung und den Status der Listing-Identität. Renaiss-Resultate werden ausdrücklich als PSA-10-FMV aus abgeschlossenen Verkäufen gekennzeichnet.

Bei mindestens fünf exakten aktiven Comps entfernt eine konservative IQR-Prüfung extreme Preisausreißer. Preisanker, Stichprobengröße, Verkäuferzahl und Streuung werden anschließend aus derselben bereinigten Stichprobe berechnet. Ein einzelnes offensichtlich extremes Angebot kann die Qualitätsstufe daher nicht mehr künstlich herabsetzen; kleine oder tatsächlich uneinheitliche Märkte bleiben weiterhin schwach.

Erhöhe die Werte nicht blind. Der Diagnosemodus warnt bei einer Konfiguration, die das kostenlose Budget zu stark ausreizt.

---

# Scoring richtig verstehen

Ein hoher Score ist ein **Screening-Signal**, keine Kaufempfehlung und keine Wertgarantie.

Starke positive Signale sind beispielsweise:

- Cert bestätigt PSA 10,
- niedrige PSA-10-Population,
- Charakter, Spieler, Set, Variante oder Kartennummer fehlen im eBay-Titel,
- ältere bzw. reifere Population,
- Prioritäts- oder Nachfragebegriff,
- Gesamtkosten deutlich unter einem brauchbaren Preisindikator.

Korrekturen und Warnungen gibt es unter anderem für:

- sehr neue Karten mit schnell wachsender Population,
- reine Auktionen,
- niedrige Verkäuferbewertung,
- hohe Versandkosten,
- aktiv beworbene Low-Pop-/Investment-Titel,
- unsichere oder unplausible OCR-Certs,
- Preisvergleich nur anhand eines PSA Estimate.

Weitere Details: [`docs/SCORING.md`](docs/SCORING.md).

---

# Telegram-Alerts

1. In Telegram über `@BotFather` einen Bot erstellen.
2. Dem neuen Bot mindestens eine Nachricht senden.
3. Bot-Token als `TELEGRAM_BOT_TOKEN` speichern.
4. Die Chat-ID ermitteln und als `TELEGRAM_CHAT_ID` speichern.
5. Workflow manuell starten.

Ein Alert enthält Score, Titel, Preis, Versand, Gesamtkosten, Kartenidentität, POP, Preisindikator, Gründe, Warnungen und den eBay-Link.

Wenn Telegram oder Discord vorübergehend nicht erreichbar ist, bleibt der Treffer trotzdem im verschlüsselten Dashboard. Fehlgeschlagene Zustellungen werden nicht als erfolgreich markiert.

# Discord-Alerts

In einem Discord-Kanal unter `Integrationen → Webhooks` einen Webhook erzeugen und die URL als `DISCORD_WEBHOOK_URL` speichern. Der Scanner sendet einen Embed mit Bild, Score, Gesamtkosten, POP und eBay-Link.

---

# Lokal testen

Python 3.11 oder neuer:

```bash
python -m venv .venv
source .venv/bin/activate        # Windows PowerShell: .venv\Scripts\Activate.ps1
pip install -r requirements-dev.txt
ruff check psa_sniper
pytest -q
python -m compileall -q psa_sniper
node --check site/template/app.js
```

## Demo-GUI ohne Credentials

```bash
python -m psa_sniper demo --output site/dist
python -m http.server 8000 -d site/dist
```

Danach `http://localhost:8000` öffnen. Das Demo-Dashboard ist unverschlüsselt und öffnet sich automatisch.

## Lokaler Live-Scan

Linux/macOS:

```bash
export EBAY_CLIENT_ID='DEINE_PRODUCTION_CLIENT_ID'
export EBAY_CLIENT_SECRET='DEIN_PRODUCTION_CLIENT_SECRET'
export DASHBOARD_PASSWORD='EIN_SEHR_LANGES_EINZIGARTIGES_PASSWORT'
python -m psa_sniper doctor --live
python -m psa_sniper scan
python -m psa_sniper dashboard --output site/dist
```

PowerShell:

```powershell
$env:EBAY_CLIENT_ID='DEINE_PRODUCTION_CLIENT_ID'
$env:EBAY_CLIENT_SECRET='DEIN_PRODUCTION_CLIENT_SECRET'
$env:DASHBOARD_PASSWORD='EIN_SEHR_LANGES_EINZIGARTIGES_PASSWORT'
python -m psa_sniper doctor --live
python -m psa_sniper scan
python -m psa_sniper dashboard --output site/dist
```

## OCR lokal aktivieren

Ubuntu/Debian:

```bash
sudo apt-get install tesseract-ocr
pip install -r requirements-ocr.txt
export ENABLE_OCR=true
```

Windows benötigt eine lokale Tesseract-Installation, die im `PATH` erreichbar ist.

---

# Befehle

```text
python -m psa_sniper scan
python -m psa_sniper doctor
python -m psa_sniper doctor --live
python -m psa_sniper dashboard --output site/dist
python -m psa_sniper dashboard --plain --output site/dist
python -m psa_sniper demo --output site/dist
python -m psa_sniper state init --output data/state.json
python -m psa_sniper state encrypt --input data/state.json --output state.enc.json
python -m psa_sniper state decrypt --input state.enc.json --output data/state.json
```

---

# Datenschutz und Verschlüsselung

Bei einem öffentlichen Gratis-Repository sind folgende Informationen **nicht im Klartext** öffentlich:

- eBay- und PSA-Credentials,
- dein Dashboard-Passwort,
- private Suchkonfiguration aus `SEARCH_CONFIG_JSON`,
- Treffer, eBay-Links und Kartenbilder,
- dauerhafte Scanner-Historie.

Die öffentlichen Actions-Logs zeigen standardmäßig nur Zähler und Laufstatus. Trefferdetails werden weder in die Summary geschrieben noch als unverschlüsseltes Artefakt hochgeladen.

Die Branch `sniper-state` und die Datei `data.enc.json` sind mit PBKDF2-HMAC-SHA256 und AES-256-GCM verschlüsselt. Das Passwort selbst verlässt beim Dashboard-Entsperren nicht den Browser-Tab.

Die Konstruktion ersetzt keine echte serverseitige Authentifizierung. Ein Angreifer kann die verschlüsselte Datei herunterladen und offline Passwörter ausprobieren. Nutze daher ein langes, einzigartiges, zufälliges Passwort.

Weitere Details: [`docs/SECURITY.md`](docs/SECURITY.md).

---

# Passwort ändern oder State zurücksetzen

## Passwort ändern und Historie behalten

1. Alten `DASHBOARD_PASSWORD` lokal setzen.
2. `state.enc.json` aus der Branch `sniper-state` herunterladen.
3. Entschlüsseln:

```bash
python -m psa_sniper state decrypt --input state.enc.json --output data/state.json
```

4. Neues Passwort setzen.
5. Neu verschlüsseln und den nächsten Workflow mit dem neuen Secret starten.

Einfacher ist ein State-Reset, falls die alte Historie nicht benötigt wird.

## State vollständig zurücksetzen

1. Branch `sniper-state` in GitHub löschen.
2. `DASHBOARD_PASSWORD` prüfen oder ändern.
3. Workflow erneut starten.

Der nächste Lauf initialisiert einen neuen leeren State.

---

# Bekannte Grenzen

1. **eBay-Freigabe:** Die Browse API kann in Production eine zusätzliche eBay-Freigabe bzw. Lizenzanforderung haben. Das Projekt kann diese externe Freigabe nicht umgehen.
2. **POP-Kontext:** Eine Cert-Seite liefert die Population des konkreten Grades, aber nicht zwingend die vollständige Grade-Verteilung. Eine echte Gem Rate ist deshalb nicht zuverlässig verfügbar.
3. **PSA-Daten:** Der optionale öffentliche Cert-Seiten-Fallback kann sich ändern, blockiert werden oder aus rechtlichen/vertraglichen Gründen ungeeignet werden. Die offizielle PSA Public API ist vorzuziehen.
4. **Preisindikator:** Kostenlose historische eBay-Sold-Daten sind nicht allgemein als offene Browse-API verfügbar. PSA Estimate und sichtbare ähnliche Verkäufe sind nur Indikatoren.
5. **OCR:** Unscharfe, schräg fotografierte oder verdeckte Slabs können nicht sicher erkannt werden. Unsichere OCR-Certs werden im Score abgewertet.
6. **Kaufprüfung:** Vor jedem Kauf Cert direkt bei PSA, Fotos, Verkäufer, Versand, Einfuhrkosten, Rückgabe und echte Verkäufe selbst prüfen.
7. **GitHub Scheduler:** Zeitgesteuerte Actions können verspätet starten. Der Fünf-Minuten-Heartbeat und die Drei-Stunden-Deep-Scan-Kadenz sind keine Echtzeitgarantie.

Diese Grenzen sind bewusst im Score, in Warnungen und in der Dokumentation sichtbar statt durch scheinpräzise Ergebnisse verdeckt zu werden.

---

# Projektstruktur

```text
.github/workflows/
  sniper.yml              Live-Scan, verschlüsselter State, Pages-Deploy
  tests.yml                Tests bei Push/Pull Request
config/
  searches.json            öffentliche Standardsuche
  settings.json            Scanner-, Budget- und Score-Einstellungen
data/                       lokaler Klartext-State; wird nicht committed
docs/
  SCORING.md
  SECURITY.md
  TROUBLESHOOTING.md
psa_sniper/
  ebay.py                   OAuth und Browse API
  scanner.py                Scan-Orchestrierung
  psa.py                    PSA API / vorsichtiger Web-Fallback
  ocr.py                    optionale Cert-OCR
  scoring.py                nachvollziehbarer Hit-Score
  state.py                  Historie, Cache, Query-Rotation
  crypto.py                 AES-GCM/PBKDF2
  dashboard.py              statischer GUI-Build
  notify.py                 Telegram/Discord
scripts/
  restore_state.sh          State aus verschlüsselter Branch laden
  persist_state.sh          verschlüsselten Snapshot speichern
site/template/              HTML/CSS/JS ohne externe Frameworks
tests/                      Parser-, Score-, State- und Crypto-Tests
```

## Einrichtungs-Checkliste

Siehe [`SETUP_CHECKLIST.md`](SETUP_CHECKLIST.md).

## Fehlerdiagnose

Siehe [`docs/TROUBLESHOOTING.md`](docs/TROUBLESHOOTING.md).

## Master-Prompt für spätere Erweiterungen

Siehe [`PROJECT_PROMPT.md`](PROJECT_PROMPT.md). Er enthält Ziel, Architektur, Sicherheitsanforderungen, Tests und Definition of Done in einer wiederverwendbaren Form für Coding-Agenten.

## Lizenz

MIT. Nutzung auf eigene Verantwortung; eBay-, PSA-, GitHub-, Telegram- und Discord-Bedingungen sowie geltendes Recht sind einzuhalten.
