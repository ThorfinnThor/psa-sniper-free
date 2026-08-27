# PSA Sniper – Einrichtungs-Checkliste

## eBay

- [ ] eBay Developer Account ist aktiv.
- [ ] Production-Keyset ist aktiv und nicht disabled.
- [ ] Production App ID / Client ID liegt vor.
- [ ] Production Cert ID / Client Secret liegt vor.
- [ ] Browse API wurde mit einem echten Production-Aufruf getestet.
- [ ] Bei `403` wurde die erforderliche eBay-Buy-/Browse-Freigabe geprüft.

## GitHub Repository

- [ ] Neues Repository wurde erstellt.
- [ ] Für häufige kostenlose Runs ist das Repository öffentlich.
- [ ] Projektinhalt liegt direkt im Repository-Root.
- [ ] `.github/workflows/sniper.yml` ist vorhanden.
- [ ] Es wurden keine Credentials committed.
- [ ] Branch Protection blockiert die automatisch erzeugte Branch `sniper-state` nicht.

## GitHub Secrets

- [ ] `EBAY_CLIENT_ID` ist angelegt.
- [ ] `EBAY_CLIENT_SECRET` ist angelegt.
- [ ] `DASHBOARD_PASSWORD` ist angelegt und mindestens 16, besser 24+ zufällige Zeichen lang.
- [ ] `SEARCH_CONFIG_JSON` enthält optional die private Suchstrategie.
- [ ] `SETTINGS_OVERRIDE_JSON` enthält optional eigene Limits/Schwellenwerte.
- [ ] `PSA_ACCESS_TOKEN` ist optional angelegt.
- [ ] Telegram- oder Discord-Secrets sind optional angelegt.

## GitHub Variables

- [ ] `ENABLE_DASHBOARD=true` ist angelegt.
- [ ] `ENABLE_OCR=true` ist angelegt, wenn OCR genutzt werden soll.

## GitHub Pages

- [ ] `Settings → Pages → Source: GitHub Actions` ist ausgewählt.
- [ ] Der erste Workflow-Lauf war erfolgreich.
- [ ] Job `deploy-dashboard` hat eine Pages-URL ausgegeben.
- [ ] Dashboard lässt sich mit `DASHBOARD_PASSWORD` entsperren.
- [ ] Ein falsches Passwort entsperrt das Dashboard nicht.

## Scanner

- [ ] Actions-Summary zeigt einen erfolgreichen Scan.
- [ ] eBay-Call-Budget liegt unter 5.000 Calls/Tag.
- [ ] Die Suchkonfiguration enthält die eigenen Märkte, Setcodes und Varianten.
- [ ] `hit_threshold` und Preisrahmen passen zum Budget.
- [ ] Reine Auktionen sind nur aktiviert, wenn sie bewusst ausgewertet werden sollen.
- [ ] Telegram-/Discord-Testalert wurde empfangen, sofern konfiguriert.

## Sicherheit

- [ ] Dashboard-Passwort wird nirgendwo wiederverwendet.
- [ ] Passwort steht nicht in `README`, Issues, Commits oder Screenshots.
- [ ] Das Repository enthält keinen Klartext-State `data/state.json`.
- [ ] Actions-Logs enthalten nur Zähler und keine Trefferdetails.
- [ ] Die Branch `sniper-state` enthält nur `state.enc.json` und einen Hinweistext.
