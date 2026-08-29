from __future__ import annotations

import hashlib
from typing import Any

TOPIC = "MARKETPLACE_ACCOUNT_DELETION"


def challenge_response(challenge_code: str, verification_token: str, endpoint: str) -> str:
    """Return the SHA-256 challenge response required by eBay.

    eBay requires the exact byte order:
    challengeCode + verificationToken + endpoint.
    """
    if not challenge_code:
        raise ValueError("challenge_code is required")
    if not 32 <= len(verification_token) <= 80:
        raise ValueError("verification_token must be 32-80 characters")
    if not endpoint.startswith("https://"):
        raise ValueError("endpoint must be https")
    payload = f"{challenge_code}{verification_token}{endpoint}".encode()
    return hashlib.sha256(payload).hexdigest()


def validate_deletion_payload(payload: Any) -> tuple[str, str | None]:
    """Validate the minimal eBay Marketplace Account Deletion schema.

    Returns (notification_id, event_date). User identifiers are deliberately
    not returned, logged or persisted by this project.
    """
    if not isinstance(payload, dict):
        raise ValueError("payload must be a JSON object")
    metadata = payload.get("metadata")
    notification = payload.get("notification")
    if not isinstance(metadata, dict) or metadata.get("topic") != TOPIC:
        raise ValueError("unexpected notification topic")
    if not isinstance(notification, dict):
        raise ValueError("notification object missing")
    notification_id = notification.get("notificationId")
    if not isinstance(notification_id, str) or not notification_id:
        raise ValueError("notificationId missing")
    return notification_id, notification.get("eventDate")
