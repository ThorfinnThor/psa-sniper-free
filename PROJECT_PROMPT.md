# Master-Prompt: PSA Sniper Free

Diesen Prompt kannst du einem Coding-Agenten geben, wenn das Projekt später erweitert, geprüft oder neu aufgebaut werden soll.

---

Du bist Senior Python Engineer, DevSecOps Engineer und Frontend Engineer. Baue bzw. pflege ein produktionsnahes, vollständig dokumentiertes Open-Source-Projekt namens **PSA Sniper Free**.

## Ziel

Das System soll neue eBay-Listings für PSA-10-Sammelkarten systematisch untersuchen und besonders solche Kandidaten priorisieren, bei denen eine Kombination aus niedriger PSA-10-Population, schlechter Listing-Beschreibung, plausibler Nachfrage und möglichem Preisabstand vorliegt.

Das System darf nicht einfach nach den Begriffen `low pop` oder `POP 1` suchen. Der Vorteil soll aus Informationslücken zwischen dem eBay-Listing und der tatsächlichen PSA-Kartenidentität entstehen.

## Harte Rahmenbedingungen

1. Der normale Betrieb muss ohne bezahlte Hosting-, Datenbank- oder Benachrichtigungsdienste möglich sein.
2. Verwende Python 3.11+.
3. Verwende für eBay ausschließlich dokumentierte APIs; kein eBay-Webscraping als Umgehung fehlender API-Freigaben.
4. Verwende GitHub Actions für die Automatisierung.
5. Verwende GitHub Pages für eine statische GUI.
6. Da das Gratis-Setup ein öffentliches Repository verwenden kann, müssen Suchstrategie, Trefferhistorie und Links verschlüsselt gespeichert werden.
7. Secrets dürfen niemals committed oder in öffentliche Logs geschrieben werden.
8. Ergebnisse müssen Unsicherheit sichtbar machen. Keine erfundenen Marktwerte, POP-Daten oder Trefferwahrscheinlichkeiten.
9. Auktionspreise dürfen nicht wie feste Kaufpreise bewertet werden.
10. Versandkosten müssen in den Vergleichspreis einfließen.
11. Niedrige Population allein darf keinen starken Hit erzeugen, insbesondere nicht bei sehr neuen oder kaum nachgefragten Karten.
12. Alle externen Zugriffs-, Lizenz- und API-Grenzen müssen ehrlich dokumentiert werden.

## Datenpipeline

Implementiere folgende Pipeline:

```text
rotierender Suchpool
    → eBay Browse API / newlyListed
    → Zeit-, Preis- und Kaufartfilter
    → Deduplizierung
    → Preliminary Score
    → eBay-Detailabruf
    → Cert-Erkennung aus Item-Specifics, Titel oder optional OCR
    → PSA-Cert-Anreicherung
    → Identitäts-Plausibilitätsprüfung
    → POP-/Informationslücken-/Preis-/Risiko-Score
    → Dashboard-Historie
    → optional Telegram/Discord
```

## eBay

- Application OAuth über Client Credentials.
- Production und Sandbox konfigurierbar.
- Marketplace standardmäßig `EBAY_DE`.
- Suche nach `newlyListed`.
- Nutze `itemStartDate` und `deliveryCountry`, sofern unterstützt.
- Parse mindestens:
  - Item-ID,
  - Titel,
  - URL,
  - Preis,
  - Versand,
  - Erstellungs- und Enddatum,
  - Bilder,
  - Item-Specifics,
  - Verkäufername,
  - Feedback-Prozent und Feedback-Score,
  - Buying Options,
  - Zustand,
  - Rückgabe,
  - Standortland.
- Verwende Retry/Backoff für 429 und temporäre 5xx-Fehler.
- Verwende ein hartes Call-Budget pro Lauf.
- Erneuere einen abgelehnten Token höchstens einmal.
- Gib bei 401/403 eine klare Diagnose aus, ohne Secrets zu loggen.

## Suchpool

- Konfiguration aus `terms`, `patterns` und `extra_queries`.
- Private Konfiguration muss über `SEARCH_CONFIG_JSON` möglich sein.
- Verwende einen persistenten Cursor und Round-Robin-Auswahl, damit bei einem begrenzten Call-Budget alle Queries regelmäßig verarbeitet werden.
- Speichere zu jedem Listing, über welche Queries es gefunden wurde.

## PSA-Cert-Erkennung

Reihenfolge:

1. eBay Item-Specifics,
2. Listing-Titel,
3. optional Tesseract-OCR aus maximal wenigen Slab-Bildern.

Jeder Cert-Kandidat benötigt:

- Nummer,
- Quelle,
- Confidence.

Unbeschriftete OCR-Zahlen haben deutlich geringere Confidence als eine beschriftete `Cert`-Erkennung. Eine schwache OCR-Cert darf POP und Preis nur beeinflussen, wenn sie plausibel zur Listing-Identität passt.

## PSA-Daten

- Bevorzuge einen optionalen offiziellen PSA Public API Access Token.
- Ein öffentlicher Cert-Seiten-Fallback darf nur langsam, kandidatengesteuert, begrenzt und klar als fragil dokumentiert verwendet werden.
- Cache erfolgreiche Cert-Daten mit TTL.
- Parse, soweit verfügbar:
  - Grade,
  - Jahr,
  - Brand/Set,
  - Subject,
  - Kartennummer,
  - Kategorie,
  - Variante,
  - Population,
  - Population Higher,
  - Estimate,
  - sichtbare ähnliche Verkäufe.
- Bei ungültigem oder widersprüchlichem Cert keine POP-/Preispunkte vergeben.

## Preislogik

- Erwerbskosten = Kartenpreis + Versand, wenn dieselbe Währung vorliegt.
- Verwende ECB-Referenzkurse nur als optionalen, ausfallsicheren FX-Helfer.
- Reine Auktionen erhalten keinen Discount-Score.
- Marktwertquellen benötigen eine Vertrauensstufe:
  - hoch: mindestens drei relevante Verkäufe,
  - mittel: ein oder zwei Verkäufe,
  - niedrig: nur Estimate.
- Schwache Preisquellen dürfen weniger Punkte erzeugen.
- Preisindikator ist nie als garantierter Marktwert zu bezeichnen.

## Score

Der Score muss nachvollziehbare Gründe und Warnungen ausgeben.

Positive Signale:

- PSA 10 im Listing,
- Cert erkannt,
- Cert bestätigt PSA 10,
- niedrige PSA-10-POP,
- fehlender Subject/Spieler,
- fehlende Variante/Parallel,
- fehlende Kartennummer,
- unvollständiges Set/Brand,
- kurzer Titel,
- kein bereits aggressiv vermarkteter Low-Pop-Hype,
- Prioritäts- oder Nachfragebegriff,
- ältere/reifere Population,
- belastbarer Preisabstand.

Negative Signale:

- Cert-Grade widerspricht PSA 10,
- unplausible OCR-Identität,
- sehr neue Karte,
- reine Auktion,
- niedrige Verkäuferbewertung,
- kaum Verkäuferfeedback,
- hohe Versandkosten,
- Preis über dem Indikator,
- nur schwacher Estimate-Preis.

Konfigurierbare Schwellen:

- Dashboard-Minimum,
- Hit-Schwelle,
- Maximalzahl Alerts pro Lauf,
- Preisbereich,
- Priority Terms.

## State und Datenschutz

Persistiere mindestens:

- Schema-Version,
- Query-Cursor,
- bereits verarbeitete Items mit Cooldown,
- bereits alarmierte Items,
- Cert-Cache,
- Treffer-/Beobachtungshistorie,
- Run-Historie.

Der persistente State muss mit folgendem Verfahren verschlüsselt werden:

- PBKDF2-HMAC-SHA256,
- mindestens 310.000 Iterationen,
- zufälliger Salt,
- AES-256-GCM,
- zufälliger 12-Byte-IV,
- Authenticated Additional Data.

Nutze `DASHBOARD_PASSWORD` als GitHub Secret. Mindestlänge 16, Empfehlung 24+ zufällige Zeichen.

Speichere den verschlüsselten Snapshot auf einer dedizierten Branch `sniper-state`. Halte dort nur einen Snapshot-Commit, damit keine endlose Commit-Historie entsteht.

## Öffentliche Logs

Im öffentlichen GitHub-Workflow dürfen nur folgende Daten erscheinen:

- Zeitpunkte,
- Anzahl Queries,
- Anzahl Listings,
- Anzahl Details,
- Anzahl Calls,
- Anzahl Hits/Beobachtungen,
- allgemeine Fehlerdiagnose.

Nicht öffentlich loggen:

- Titel,
- Item-IDs,
- eBay-Links,
- Cert-Nummern,
- private Queries,
- Treffergründe,
- Preisdetails.

## Dashboard

Baue eine statische, responsive GUI ohne externe JavaScript-Frameworks oder CDN-Abhängigkeiten.

Funktionen:

- Passwort-Entsperrung mit Web Crypto,
- Suche,
- Mindestscore,
- maximale POP,
- maximaler Gesamtpreis,
- Sortierung nach Aktualität, Score, POP, Discount und Preis,
- Ansichten Hits, Beobachtung, Sofortkauf, Auktion,
- Kartenbild,
- Kartenidentität,
- Preis, Versand, Gesamtkosten,
- POP,
- Score-Gründe und Warnungen,
- Verkäuferdaten,
- eBay- und PSA-Link,
- Scanner-Run-Historie,
- lokale Statusmarkierungen `saved`, `bought`, `ignored` über localStorage.

Bei einer unverschlüsselten lokalen Demo soll die GUI automatisch öffnen. Bei verschlüsselten Daten muss sie standardmäßig gesperrt bleiben.

## GitHub Actions

Erstelle:

1. einen Test-Workflow für Push/Pull Request,
2. einen Live-Workflow für `workflow_dispatch` und 15-Minuten-Cron.

Live-Workflow:

- Checkout mit vollständiger Historie,
- Python einrichten,
- Abhängigkeiten installieren,
- OCR optional installieren,
- Secrets validieren,
- Tests ausführen,
- verschlüsselten State wiederherstellen,
- Konfiguration prüfen,
- eBay scannen,
- Dashboard bauen,
- State verschlüsseln und persistieren,
- ausschließlich öffentliche Summary anhängen,
- Pages-Artefakt hochladen,
- optional deployen.

Verwende Concurrency ohne Abbruch eines laufenden State-Schreibvorgangs.

## Benachrichtigungen

- Telegram über Bot Token + Chat ID.
- Discord über Webhook Embed.
- Netzwerkfehler dürfen den State nicht beschädigen.
- Der Treffer muss im Dashboard bleiben, auch wenn ein Alert fehlschlägt.

## Diagnose

Implementiere:

```text
python -m psa_sniper doctor
python -m psa_sniper doctor --live
```

Prüfe:

- JSON-Konfiguration,
- Query-Anzahl,
- tägliches eBay-Budget,
- eBay-Secrets,
- Dashboard-Passwort,
- OCR/Tesseract,
- PSA-Konfiguration,
- optional echten eBay OAuth-/Browse-Zugriff.

## Tests

Mindestens testen:

- Cert-Erkennung aus Titel und Item-Specifics,
- PSA-HTML-Parser,
- PSA-API-Parser,
- leere API-Antwort ist nicht gültig,
- niedrige POP + Informationslücke,
- Versand wird in Discount einbezogen,
- reine Auktion erhält keinen Discount,
- unplausible OCR-Cert kann keinen Fake-Hit erzeugen,
- Query-Rotation,
- Verschlüsselungs-Roundtrip,
- verschlüsseltes Dashboard enthält keinen Kartentitel im Klartext,
- Python-Compile und JavaScript-Syntax.

## Dokumentation

Liefere:

- vollständiges deutsches `README.md`,
- `SETUP_CHECKLIST.md`,
- Sicherheitsdokument,
- Scoring-Dokument,
- Troubleshooting,
- `.env.example`,
- klare bekannte Grenzen,
- Passwortwechsel-/State-Reset-Anleitung.

## Definition of Done

Das Projekt ist erst fertig, wenn:

1. alle Tests erfolgreich sind,
2. Python kompiliert,
3. JavaScript syntaktisch gültig ist,
4. Demo-Dashboard sichtbar rendert,
5. Filter und lokale Statusaktionen funktionieren,
6. Browser-WebCrypto die Python-Verschlüsselung entschlüsseln kann,
7. der verschlüsselte State erfolgreich persistiert und wiederhergestellt wird,
8. keine Treffer im verschlüsselten JSON im Klartext vorkommen,
9. öffentliche Logs keine Trefferdetails enthalten,
10. die externe eBay-Production-Freigabe als verbleibende Voraussetzung klar genannt wird.
