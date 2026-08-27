from __future__ import annotations

import json
import os
from http.server import BaseHTTPRequestHandler
from urllib.parse import parse_qs, urlsplit

from psa_sniper.ebay_compliance import challenge_response, validate_deletion_payload


def _endpoint_from_request(handler: BaseHTTPRequestHandler) -> str:
    configured = os.getenv("EBAY_NOTIFICATION_ENDPOINT", "").strip()
    if configured:
        return configured
    proto = handler.headers.get("x-forwarded-proto", "https").split(",")[0].strip()
    host = handler.headers.get("x-forwarded-host") or handler.headers.get("host")
    if not host:
        raise ValueError("request host missing")
    path = urlsplit(handler.path).path
    return f"{proto}://{host}{path}"


class handler(BaseHTTPRequestHandler):
    server_version = "PSASniperWebhook/1.0"

    def _json(self, status: int, payload: dict) -> None:
        body = json.dumps(payload, separators=(",", ":")).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:
        try:
            token = os.environ["EBAY_VERIFICATION_TOKEN"]
            challenge = parse_qs(urlsplit(self.path).query).get("challenge_code", [""])[0]
            endpoint = _endpoint_from_request(self)
            response = challenge_response(challenge, token, endpoint)
            self._json(200, {"challengeResponse": response})
        except (KeyError, ValueError) as exc:
            self._json(400, {"error": str(exc)})

    def do_POST(self) -> None:
        # eBay sends X-EBAY-SIGNATURE on deletion notifications. We require the
        # header and validate the payload shape, but deliberately do not persist
        # any username/userId/eiasToken from the notification.
        signature = self.headers.get("x-ebay-signature")
        if not signature:
            self._json(412, {"error": "x-ebay-signature missing"})
            return
        try:
            length = int(self.headers.get("content-length", "0"))
            if length <= 0 or length > 256_000:
                raise ValueError("invalid payload length")
            raw = self.rfile.read(length)
            payload = json.loads(raw.decode("utf-8"))
            notification_id, event_date = validate_deletion_payload(payload)
            # Log only non-user identifiers needed for operational diagnosis.
            print(json.dumps({
                "event": "MARKETPLACE_ACCOUNT_DELETION_ACK",
                "notificationId": notification_id,
                "eventDate": event_date,
            }, separators=(",", ":")))
            self.send_response(204)
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
        except (ValueError, json.JSONDecodeError, UnicodeDecodeError) as exc:
            self._json(400, {"error": str(exc)})

    def do_HEAD(self) -> None:
        self.send_response(204)
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
