import test from "node:test";
import assert from "node:assert/strict";

import {
  challengeResponse,
  endpointFromRequest,
  validateDeletionPayload,
} from "../src/index.js";

test("challenge response matches known SHA-256 vector", () => {
  const result = challengeResponse(
    "challenge-123",
    "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdef123456",
    "https://psa-sniper-ebay-webhook.example.workers.dev/api/ebay-account-deletion",
  );
  assert.equal(
    result,
    "40526dcc6da26b5d839074f87460a546bc8c7eef3e8c84756a6607cb1139ea68",
  );
});

test("endpoint excludes challenge query string", () => {
  const request = new Request(
    "https://example.workers.dev/api/ebay-account-deletion?challenge_code=abc",
  );
  assert.equal(
    endpointFromRequest(request),
    "https://example.workers.dev/api/ebay-account-deletion",
  );
});

test("validates marketplace account deletion shape", () => {
  assert.equal(
    validateDeletionPayload({
      metadata: { topic: "MARKETPLACE_ACCOUNT_DELETION" },
      notification: {
        notificationId: "id-1",
        data: { username: "user", userId: "id", eiasToken: "token" },
      },
    }),
    true,
  );
  assert.equal(
    validateDeletionPayload({
      metadata: { topic: "OTHER" },
      notification: { notificationId: "id-1", data: {} },
    }),
    false,
  );
});
