# Changelog

## 1.0.0

- verschlüsseltes GitHub-Pages-Dashboard
- dauerhafter AES-GCM-State auf separater Snapshot-Branch
- private Suchkonfiguration über GitHub Secret
- rotierende Query-Batches
- Versandkosten in Gesamtkosten und Discount-Score
- reine Auktionen standardmäßig ausgeschlossen und nie als BIN-Discount bewertet
- OCR-Vertrauen und Identitäts-Plausibilitätsprüfung
- PSA-Cert-Cache und begrenztes PSA-Call-Budget
- optionaler PSA Public API Token
- Verkäuferbewertung und Kartenalter im Score
- Qualitätsstufen für Preisindikatoren
- Telegram- und Discord-Alerts
- Datenschutzmodus für öffentliche Actions-Logs
- Diagnosemodus, Demo-GUI und automatisierte Tests
- installierbares Paket mit expliziter Setuptools-Paket-Discovery
- globale zweistufige Priorisierung des knappen Preisvergleichs-Budgets
- faire rundenbasierte Comp-Verteilung mit einer Primärsuche pro Kandidat vor Fallbacks
- Deduplizierung identischer Cert-/Listing-Comp-Abfragen bei doppelter Identitätsauswertung
- konsistente IQR-Ausreißerbereinigung für Listing-Preisanker und Marktqualität
- dynamischer Dashboard-Startfilter und hilfreicher Leerzustand statt leerer Kauf-Hit-Ansicht
- robuste Dashboard-Breiten ohne horizontales Abschneiden bei Desktop, Tablet und Mobilgerät
- Ruff-Prüfung für den Produktionscode in CI und Live-Workflow
