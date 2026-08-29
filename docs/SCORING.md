# Hit-Score

Der Score dient dazu, aus vielen neuen eBay-Listings eine kleine Prüfmenge zu erzeugen. Er ist keine mathematische Marktwertgarantie.

## Positivsignale

| Signal | Typische Wirkung |
|---|---:|
| PSA 10 im Listing angegeben | +2 |
| Cert-Nummer erkannt | +1 |
| Cert bestätigt GEM MT 10 | +2 |
| PSA-10-POP ≤ 3 | +5 |
| PSA-10-POP ≤ 10 | +4 |
| PSA-10-POP ≤ 25 | +3 |
| PSA-10-POP ≤ 50 | +1 |
| fehlender Subject/Spieler, Parallel, Kartennummer oder vollständiges Set | bis +4 |
| kein Low-Pop-/Investment-Hype im Titel | +1 |
| kurzer/informationsarmer Titel | +1 |
| eigener Prioritätsbegriff | +3 |
| allgemeiner Nachfragebegriff | +1 |
| ältere/reifere Karte | +1 |
| Preisabstand ≥ 15/25/40 % | abhängig von Preisquellen-Vertrauen bis +7 |

Die tatsächlichen Punkte stehen in `psa_sniper/scoring.py` und können dort nachvollzogen werden.

## Negativsignale

| Signal | Typische Wirkung |
|---|---:|
| Cert bestätigt nicht PSA 10 | −20 |
| unplausible schwache OCR-Cert | −7; POP und Preis werden ignoriert |
| Anbieter bewirbt Seltenheit bereits | −2 |
| sehr neue Karte | −2 |
| reine Auktion | −3; kein Discount-Score |
| Verkäuferbewertung < 98 % | −2 |
| Verkäuferbewertung < 95 % | −4 |
| sehr wenige Verkäuferbewertungen | −1 |
| Preis deutlich über Indikator | −2 |
| Versand ≥ 25 % des Kartenpreises | −1 |

## Warum eine POP 1 nicht automatisch ein Hit ist

Eine niedrige Population kann bedeuten:

- echte condition rarity,
- kaum eingeschickte Exemplare,
- neues Set mit noch wachsender POP,
- wenig Nachfrage,
- falsch erkannte Cert,
- unbekannte oder wenig liquide Variante.

Deshalb kombiniert der Scanner POP mit Kartenalter, Titellücke, Nachfragehinweisen, Preisindikator, Verkäuferdaten und OCR-Vertrauen.

## Preisvertrauen

- **hoch:** mindestens drei kohärente, exakt passende und manuell auf 130point verifizierte PSA-10-Verkäufe.
- **mittel:** mindestens zwei kohärente 130point-PSA-10-Verkäufe oder eine ausreichend unabhängige und vollständige aktive eBay-Comp-Stichprobe.
- **niedrig:** nur ein 130point-Verkauf, unvollständige/streuende aktive Comps oder nur ein PSA Estimate.

130point-Sold-Comps haben bei gleicher Vertrauensstufe Vorrang vor aktiven Angebotspreisen. Kartennummer, Subject, Sprache, PSA 10 und bekannte Varianten müssen übereinstimmen. Ein fehlender Setcode wird nur bei vollständigen Nummern wie `039/100` toleriert; explizite Konflikte werden immer verworfen. Der Scanner fragt 130point nicht automatisiert ab.

Je schwächer die Quelle, desto weniger Score kann ein Preisabstand erzeugen. Versand wird in die Gesamtkosten einbezogen. Einfuhrabgaben und Steuern sind nicht automatisch vollständig kalkuliert.

## Standardschwellen

- `dashboard_min_score = 7`: als Beobachtung im Dashboard speichern.
- `hit_threshold = 11`: als Hit behandeln und optional alarmieren.
- Score ≥ 16: in der GUI als sehr stark hervorgehoben.

Diese Schwellen sollten erst nach einigen Tagen anhand echter Treffer angepasst werden.
