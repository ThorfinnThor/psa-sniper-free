import test from "node:test";
import assert from "node:assert/strict";

import {
  challengeResponse,
  endpointFromRequest,
  githubDispatchRequest,
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

test("builds authenticated GitHub workflow dispatch without leaking token into body", () => {
  const token = "github_pat_test_secret_value";
  const request = githubDispatchRequest(token);
  assert.equal(
    request.url,
    "https://api.github.com/repos/ThorfinnThor/psa-sniper-free/actions/workflows/sniper.yml/dispatches",
  );
  assert.equal(request.init.method, "POST");
  assert.equal(request.init.headers.authorization, `Bearer ${token}`);
  const body = JSON.parse(request.init.body);
  assert.deepEqual(body, {
    ref: "main",
    inputs: { source: "cloudflare" },
  });
  assert.equal(request.init.body.includes(token), false);
});

test("GitHub dispatch requires a token", () => {
  assert.throws(() => githubDispatchRequest(""), /GITHUB_WORKFLOW_TOKEN/);
});
