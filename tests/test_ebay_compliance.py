import hashlib

import pytest

from psa_sniper.ebay_compliance import challenge_response, validate_deletion_payload


def test_challenge_response_matches_ebay_order():
    challenge = "abc123"
    token = "A" * 32
    endpoint = "https://example.vercel.app/api/ebay_account_deletion"
    expected = hashlib.sha256(f"{challenge}{token}{endpoint}".encode()).hexdigest()
    assert challenge_response(challenge, token, endpoint) == expected


def test_validate_deletion_payload_does_not_return_user_identifiers():
    payload = {
        "metadata": {"topic": "MARKETPLACE_ACCOUNT_DELETION"},
        "notification": {
            "notificationId": "n-123",
            "eventDate": "2026-01-01T00:00:00Z",
            "data": {
                "username": "private-user",
                "userId": "private-id",
                "eiasToken": "private-token",
            },
        },
    }
    assert validate_deletion_payload(payload) == ("n-123", "2026-01-01T00:00:00Z")


def test_validate_deletion_payload_rejects_other_topics():
    with pytest.raises(ValueError):
        validate_deletion_payload({
            "metadata": {"topic": "OTHER"},
            "notification": {"notificationId": "n-1"},
        })
