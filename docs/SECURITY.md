# Sicherheitsmodell

## Ziel

Das Projekt soll auf einem öffentlichen GitHub-Repository kostenlos häufig laufen können, ohne Suchstrategie, Trefferhistorie oder Zugangsdaten im Klartext zu veröffentlichen.

## Was GitHub Secrets schützt

Folgende Werte werden ausschließlich als GitHub Actions Secrets erwartet:

- `EBAY_CLIENT_ID`
- `EBAY_CLIENT_SECRET`
- `DASHBOARD_PASSWORD`
- `PSA_ACCESS_TOKEN`
- Telegram-/Discord-Credentials
- `SEARCH_CONFIG_JSON`
- `SETTINGS_OVERRIDE_JSON`

Secrets werden nicht in Dateien geschrieben, nicht committed und nicht in normalen Logs ausgegeben.

## Verschlüsselter State

Der Klartext-State existiert nur temporär im GitHub-Runner unter `data/state.json`. Danach wird er mit folgenden Parametern verschlüsselt:

- KDF: PBKDF2-HMAC-SHA256
- Iterationen: 310.000
- Salt: 16 zufällige Bytes
- Cipher: AES-256-GCM
- IV/Nonce: 12 zufällige Bytes
- Authenticated Additional Data: Formatkennung

Der verschlüsselte Snapshot wird als `state.enc.json` auf die automatisch verwaltete Branch `sniper-state` geschrieben. Die Branch wird bei jedem Lauf auf einen einzelnen Snapshot-Commit reduziert, damit keine unnötig lange Historie wächst.

## Verschlüsseltes Dashboard

GitHub Pages veröffentlicht HTML, CSS, JavaScript sowie `data.enc.json`. Die Trefferdatei ist mit demselben Verfahren verschlüsselt. Die Entschlüsselung erfolgt ausschließlich mit der Web Crypto API im Browser.

Das Passwort wird:

- nicht an einen Server gesendet,
- nicht in `localStorage` oder Cookies gespeichert,
- nach erfolgreichem Entsperren aus dem Eingabefeld entfernt.

Lokale Statusmarkierungen werden im Browser gespeichert und enthalten nur Item-ID → Status.

## Öffentliche Metadaten

Öffentlich erkennbar bleiben:

- dass das Repository einen Scanner enthält,
- Workflow-Zeitpunkte und grobe Laufzähler,
- der Aktualisierungszeitpunkt des Dashboards,
- Größe und Inhalt der verschlüsselten Dateien.

Nicht verborgen werden kann, dass überhaupt ein Dashboard betrieben wird.

## Passwortanforderung

Die Verschlüsselungsdatei ist öffentlich herunterladbar. Daher sind Offline-Passwortversuche möglich. Ein menschlich merkbares kurzes Passwort ist trotz PBKDF2 nicht ausreichend.

Empfehlung:

- mindestens 24 zufällige Zeichen,
- einzigartig für dieses Projekt,
- in einem Passwortmanager gespeichert,
- nicht in Screenshots, Issues, Logs oder Commit-Nachrichten verwenden.

## GitHub Actions

Der Live-Workflow läuft nur per Zeitplan oder manuell. Es gibt absichtlich keinen `pull_request_target`-Trigger. Pull Requests erhalten dadurch keinen Zugriff auf Secrets.

Trefferdetails werden nicht in die öffentliche Actions-Summary geschrieben. Der lokale private Bericht bleibt auf dem temporären Runner und wird nicht als Artefakt hochgeladen.

## Grenzen

Die statische Verschlüsselung ist kein Ersatz für eine serverseitige Anmeldung mit Rate Limiting und Zugriffskontrolle. Wer die Datei besitzt, kann offline Passwörter testen. Für besonders sensible oder kommerzielle Nutzung ist ein privater Server mit Authentifizierung vorzuziehen.

Externe Links und Kartenbilder werden nach dem Entsperren direkt von eBay bzw. PSA geladen. Diese Dienste können dabei Browser-Metadaten wie IP-Adresse und User-Agent sehen.
