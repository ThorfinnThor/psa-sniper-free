# Validierungsbericht – PSA Sniper Free 1.0

Stand: 29. August 2026

## Ergebnis

Das Projekt ist technisch **bereit für die Einrichtung in einem GitHub-Repository**. Der Code, die Workflows, das verschlüsselte Dashboard, die State-Persistenz und die Dokumentation sind vollständig enthalten.

Zwei externe Schritte können ohne Kontozugang nicht vorab abgeschlossen werden:

1. eBays Production-Freigabe für die Browse API,
2. der tatsächliche GitHub-Pages-Deploy im Repository des Nutzers.

Beide Punkte werden durch Diagnose, Workflow-Fehlertexte und Troubleshooting eindeutig geprüft.

## Ergänzte bzw. behobene Punkte

- echte Web-GUI statt reiner Hit-Liste,
- verschlüsseltes Dashboard für ein öffentliches Gratis-Repository,
- dauerhafte verschlüsselte Historie statt flüchtigem Actions Cache,
- private Suchstrategie über GitHub Secret,
- faire Round-Robin-Rotation vieler Queries,
- Cooldown für bereits verarbeitete Listings,
- quota-gesteuertes eBay-Call-Budget mit maximal 4.600 theoretischen Calls/Tag,
- zweistufige globale Preispriorisierung statt Comp-Budget nach Discovery-Reihenfolge,
- rundenbasierte Comp-Fairness: eine Primärsuche pro geeignetem Kandidaten vor Fallbacks,
- Versandkosten in den Gesamtkosten,
- reine Auktionen standardmäßig ausgeschlossen,
- keine Discount-Punkte für laufende Auktionen,
- Verkäuferfeedback und Rückgabeinformationen,
- OCR-Confidence und Schutz gegen unplausible Cert-Zuordnung,
- reduzierte Low-Pop-Wertung bei sehr neuen Karten,
- unterschiedliche Vertrauensstufen für Sales und PSA Estimate,
- Cert-Cache mit TTL,
- optionale offizielle PSA Public API,
- privacy-safe Actions-Summary ohne Trefferdetails,
- Telegram- und Discord-Alerts,
- Diagnosemodus,
- Passwortwechsel-/State-Reset-Anleitung,
- Master-Prompt für spätere Erweiterungen.

## Automatisierte Prüfungen

| Prüfung | Ergebnis |
|---|---|
| Python Unit Tests | 111 bestanden |
| Ruff Produktionscode | bestanden |
| Python `compileall` | bestanden |
| JavaScript Syntaxcheck | bestanden |
| JSON-Konfiguration | valide |
| GitHub-Workflow-YAML | parsebar |
| AES-GCM/PBKDF2 Roundtrip | bestanden |
| Kein Kartentitel im verschlüsselten Dashboard-JSON | bestanden |
| JavaScript/WebCrypto-kompatible Entschlüsselung | bestanden |
| Statisches Dashboard in Chromium gerendert | bestanden |
| Suche filtert Demo-Hits | bestanden |
| lokale Statusaktion | bestanden |
| verschlüsselter Git-State persistieren/wiederherstellen | bestanden |
| `sniper-state` bleibt ein Snapshot-Commit | bestanden |

## Nicht vorgetäuschte Prüfungen

Folgende Punkte wurden bewusst nicht als „getestet“ bezeichnet:

- echter eBay-Live-Scan ohne die Production-Keys des Nutzers,
- eBay Browse Production-Zulassung des Nutzerkontos,
- echter GitHub-Pages-Deploy ohne Nutzer-Repository,
- Telegram-/Discord-Zustellung ohne Nutzer-Credentials,
- OCR auf konkreten eBay-Slab-Fotos ohne Live-Listings.

## Verbleibende Datenlimits

- vollständige Gem Rate ist ohne vollständige Grade-Verteilung nicht zuverlässig verfügbar,
- kostenlose historische eBay-Sold-Daten sind nicht allgemein über die Browse API verfügbar,
- PSA Estimate und sichtbare ähnliche Verkäufe sind Preisindikatoren, keine Garantie,
- OCR und öffentlicher PSA-Web-Fallback bleiben Best Effort,
- GitHub Scheduled Actions sind nicht echtzeitgarantiert.

Diese Einschränkungen sind im Score, im Dashboard und in der Dokumentation sichtbar.
