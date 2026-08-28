import { Buffer } from "node:buffer";
import { createHash, createVerify } from "node:crypto";

const TOPIC = "MARKETPLACE_ACCOUNT_DELETION";
const TOKEN_RE = /^[A-Za-z0-9_-]{32,80}$/;
const EBAY_SCOPE = "https://api.ebay.com/oauth/api_scope";
const TOKEN_URL = "https://api.ebay.com/identity/v1/oauth2/token";
const PUBLIC_KEY_URL = "https://api.ebay.com/commerce/notification/v1/public_key/";
const GITHUB_DISPATCH_URL = "https://api.github.com/repos/ThorfinnThor/psa-sniper-free/actions/workflows/sniper.yml/dispatches";

function json(body, status = 200) {
  return new Response(JSON.stringify(body), {
    status,
    headers: {
      "content-type": "application/json; charset=utf-8",
      "cache-control": "no-store",
    },
  });
}

export function endpointFromRequest(request) {
  const url = new URL(request.url);
  return `${url.origin}${url.pathname}`;
}

export function challengeResponse(challengeCode, verificationToken, endpoint) {
  return createHash("sha256")
    .update(challengeCode)
    .update(verificationToken)
    .update(endpoint)
    .digest("hex");
}

export function validateDeletionPayload(payload) {
  if (!payload || typeof payload !== "object") return false;
  if (payload?.metadata?.topic !== TOPIC) return false;
  if (typeof payload?.notification?.notificationId !== "string") return false;
  if (!payload.notification.notificationId.trim()) return false;
  if (!payload?.notification?.data || typeof payload.notification.data !== "object") return false;
  return true;
}

export function githubDispatchRequest(token) {
  if (!token) throw new Error("GITHUB_WORKFLOW_TOKEN is missing");
  return {
    url: GITHUB_DISPATCH_URL,
    init: {
      method: "POST",
      headers: {
        authorization: `Bearer ${token}`,
        accept: "application/vnd.github+json",
        "content-type": "application/json",
        "user-agent": "psa-sniper-cloudflare-scheduler/1.0",
        "x-github-api-version": "2022-11-28",
      },
      body: JSON.stringify({
        ref: "main",
        inputs: { source: "cloudflare" },
      }),
    },
  };
}

export async function dispatchScanner(env) {
  const { url, init } = githubDispatchRequest(env.GITHUB_WORKFLOW_TOKEN);
  const response = await fetch(url, init);
  if (response.status !== 204) {
    throw new Error(`GitHub workflow dispatch failed with ${response.status}`);
  }
}

function validateChallengeEnvironment(env) {
  if (!TOKEN_RE.test(env.EBAY_VERIFICATION_TOKEN || "")) {
    throw new Error("EBAY_VERIFICATION_TOKEN is missing or invalid");
  }
}

function canVerifyNotifications(env) {
  return Boolean(env.EBAY_CLIENT_ID && env.EBAY_CLIENT_SECRET);
}

function decodeSignatureHeader(header) {
  if (!header) throw new Error("x-ebay-signature header missing");
  const decoded = Buffer.from(header, "base64").toString("utf8");
  const meta = JSON.parse(decoded);
  if (!meta.kid || !meta.signature) throw new Error("Invalid eBay signature metadata");
  return meta;
}

function formatPublicKey(key) {
  const compact = String(key || "").trim();
  if (!compact) throw new Error("Empty eBay public key");
  if (!compact.includes("-----BEGIN PUBLIC KEY-----")) {
    throw new Error("Unexpected eBay public key format");
  }
  return compact
    .replace(/-----BEGIN PUBLIC KEY-----\s*/, "-----BEGIN PUBLIC KEY-----\n")
    .replace(/\s*-----END PUBLIC KEY-----/, "\n-----END PUBLIC KEY-----");
}

function digestAlgorithm(signatureMeta, publicKey) {
  const raw = String(signatureMeta.digest || publicKey.digest || "SHA1")
    .replace(/[^A-Za-z0-9]/g, "")
    .toUpperCase();
  if (raw === "SHA256") return "sha256";
  if (raw === "SHA384") return "sha384";
  if (raw === "SHA512") return "sha512";
  return "sha1";
}

async function getApplicationToken(env) {
  const credentials = Buffer.from(`${env.EBAY_CLIENT_ID}:${env.EBAY_CLIENT_SECRET}`).toString("base64");
  const body = new URLSearchParams({
    grant_type: "client_credentials",
    scope: EBAY_SCOPE,
  });
  const response = await fetch(TOKEN_URL, {
    method: "POST",
    headers: {
      authorization: `Basic ${credentials}`,
      "content-type": "application/x-www-form-urlencoded",
    },
    body,
  });
  if (!response.ok) {
    throw new Error(`eBay OAuth failed with ${response.status}`);
  }
  const data = await response.json();
  if (!data.access_token) throw new Error("eBay OAuth response contains no access token");
  return data.access_token;
}

async function getPublicKey(keyId, env) {
  const cacheKey = new Request(`https://psa-sniper.internal/ebay-public-key/${encodeURIComponent(keyId)}`);
  const cache = caches.default;
  const cached = await cache.match(cacheKey);
  if (cached) return cached.json();

  const token = await getApplicationToken(env);
  const response = await fetch(`${PUBLIC_KEY_URL}${encodeURIComponent(keyId)}`, {
    headers: {
      authorization: `Bearer ${token}`,
      accept: "application/json",
    },
  });
  if (!response.ok) {
    throw new Error(`eBay public-key lookup failed with ${response.status}`);
  }
  const data = await response.json();
  await cache.put(
    cacheKey,
    new Response(JSON.stringify(data), {
      headers: {
        "content-type": "application/json",
        "cache-control": "public, max-age=3600",
      },
    }),
  );
  return data;
}

async function verifyNotification(rawBody, signatureHeader, env) {
  const signatureMeta = decodeSignatureHeader(signatureHeader);
  const publicKey = await getPublicKey(signatureMeta.kid, env);
  const verifier = createVerify(digestAlgorithm(signatureMeta, publicKey));
  verifier.update(rawBody);
  verifier.end();
  return verifier.verify(
    formatPublicKey(publicKey.key),
    signatureMeta.signature,
    "base64",
  );
}

async function processDeletionInBackground(request, env) {
  try {
    const rawBody = await request.text();
    const payload = JSON.parse(rawBody);
    if (!validateDeletionPayload(payload)) {
      console.warn("eBay notification ignored: unexpected payload shape");
      return;
    }

    if (!canVerifyNotifications(env)) {
      console.warn("eBay notification verification skipped: client credentials unavailable");
      return;
    }

    const valid = await verifyNotification(
      rawBody,
      request.headers.get("x-ebay-signature"),
      env,
    );
    if (!valid) {
      console.warn("eBay notification signature verification failed");
    }
  } catch (error) {
    // Never log the payload or any eBay user identifiers.
    console.warn(`eBay notification background processing error: ${error?.message || "unknown error"}`);
  }
}

async function handleChallenge(request, env) {
  validateChallengeEnvironment(env);
  const url = new URL(request.url);
  const challengeCode = url.searchParams.get("challenge_code");
  if (!challengeCode) {
    return json({ ok: true, service: "psa-sniper-ebay-webhook" });
  }
  const endpoint = endpointFromRequest(request);
  return json({
    challengeResponse: challengeResponse(
      challengeCode,
      env.EBAY_VERIFICATION_TOKEN,
      endpoint,
    ),
  });
}

function handleDeletion(request, env, ctx) {
  // Dedicated endpoint: acknowledge the POST before reading/parsing/verifying it.
  // eBay explicitly requires immediate 2xx acknowledgement, with validation after.
  const backgroundRequest = request.clone();
  const processing = processDeletionInBackground(backgroundRequest, env);
  if (ctx?.waitUntil) {
    ctx.waitUntil(processing);
  } else {
    processing.catch(() => undefined);
  }

  // 200 OK is intentionally used for maximum compatibility with eBay's test tool.
  return new Response("", {
    status: 200,
    headers: {
      "content-type": "text/plain; charset=utf-8",
      "cache-control": "no-store",
    },
  });
}

export default {
  async fetch(request, env, ctx) {
    const url = new URL(request.url);
    if (url.pathname !== "/api/ebay-account-deletion") {
      return new Response("Not found", { status: 404 });
    }
    try {
      if (request.method === "GET") return await handleChallenge(request, env);
      if (request.method === "POST") return handleDeletion(request, env, ctx);
      if (request.method === "HEAD") return new Response(null, { status: 204 });
      return new Response("Method not allowed", {
        status: 405,
        headers: { allow: "GET, POST, HEAD" },
      });
    } catch {
      return json({ error: "webhook_not_configured" }, 503);
    }
  },

  scheduled(controller, env, ctx) {
    const task = dispatchScanner(env).catch((error) => {
      // Never print the GitHub token. Status/error text is sufficient for diagnostics.
      console.warn(`PSA Sniper schedule dispatch error (${controller?.cron || "unknown cron"}): ${error?.message || "unknown error"}`);
    });
    ctx.waitUntil(task);
  },
};
