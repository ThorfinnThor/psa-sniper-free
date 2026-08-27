# Cloudflare Worker für eBay Marketplace Account Deletion

Dieses Projekt enthält einen Cloudflare Worker unter `cloudflare/ebay-webhook`, der den von eBay verlangten Marketplace-Account-Deletion/Closure-Webhook bereitstellt.

## Was der Worker macht

- `GET /api/ebay-account-deletion?challenge_code=...`
  - berechnet `SHA256(challengeCode + verificationToken + endpoint)`
  - antwortet als JSON mit `challengeResponse`
- `POST /api/ebay-account-deletion`
  - akzeptiert nur `MARKETPLACE_ACCOUNT_DELETION`
  - liest `X-EBAY-SIGNATURE`
  - holt den zugehörigen öffentlichen Schlüssel über die eBay Notification API
  - prüft die ECDSA-Signatur
  - antwortet bei gültiger Notification mit `204 No Content`
  - antwortet bei ungültiger Signatur mit `412 Precondition Failed`
- speichert weder `username` noch `userId` noch `eiasToken`

Der PSA-Sniper-State speichert ebenfalls keine Seller-Identität oder Seller-Feedback-Werte mehr dauerhaft.

## GitHub-Secrets

Unter `Settings → Secrets and variables → Actions → Secrets` müssen folgende Werte existieren:

```text
CLOUDFLARE_ACCOUNT_ID
CLOUDFLARE_API_TOKEN
EBAY_VERIFICATION_TOKEN
EBAY_CLIENT_ID
EBAY_CLIENT_SECRET
```

`EBAY_CLIENT_ID` und `EBAY_CLIENT_SECRET` sind dieselben Production-Werte, die auch der Scanner verwendet.

### EBAY_VERIFICATION_TOKEN erzeugen

Der Token muss 32–80 Zeichen lang sein und nur Buchstaben, Zahlen, `_` und `-` enthalten.

Lokal kann ein geeigneter Wert erzeugt werden mit:

```bash
python -c "import secrets; print(secrets.token_urlsafe(48))"
```

Diesen Wert **nicht committen**. Er muss identisch sein in:

1. GitHub Secret `EBAY_VERIFICATION_TOKEN`
2. eBay Developer Portal → Verification token

## Cloudflare API Token

Im Cloudflare Dashboard einen API Token anlegen, der auf das gewünschte Konto beschränkt ist und Workers Scripts bearbeiten/deployen darf. Den Token als `CLOUDFLARE_API_TOKEN` speichern. Die Account ID als `CLOUDFLARE_ACCOUNT_ID` speichern.

Der Workflow `.github/workflows/cloudflare-webhook.yml` deployed mit der offiziellen Wrangler Action. Solange die benötigten Secrets fehlen, wird das Deployment sauber übersprungen.

## Deploy

Nach dem Setzen der Secrets:

`GitHub → Actions → Deploy eBay Webhook to Cloudflare → Run workflow`

Der Workflow zeigt anschließend in seiner Summary den fertigen Endpoint, typischerweise:

```text
https://psa-sniper-ebay-webhook.<deine-workers-subdomain>.workers.dev/api/ebay-account-deletion
```

## In eBay eintragen

Im eBay Developer Portal beim Production-Keyset:

1. `Notifications` öffnen.
2. `Marketplace Account Deletion` wählen.
3. Alert-E-Mail speichern.
4. Als Notification Endpoint den URL aus der GitHub-Action-Summary eintragen.
5. Als Verification Token exakt den Wert aus `EBAY_VERIFICATION_TOKEN` eintragen.
6. Speichern. eBay sendet sofort den GET-Challenge-Request.
7. Nach erfolgreicher Verifikation `Send Test Notification` ausführen.

Der Endpoint muss exakt so eingetragen werden, wie er vom Worker aufgerufen wird. Kein zusätzlicher Slash am Ende und keine Query-Parameter.

## Datenschutz

Der Worker loggt keine Notification-Payloads und persistiert keine eBay-User-Identifier. Die eBay-Userdaten aus einer Account-Deletion-Notification werden ausschließlich für die Validierung des Notification-Typs verarbeitet und anschließend verworfen.

Der Scanner verwendet Seller-Feedback weiterhin transient als mögliches Scoring-Signal während eines Runs, schreibt Seller-Name und Seller-Feedback aber nicht in die dauerhafte Trefferhistorie.
