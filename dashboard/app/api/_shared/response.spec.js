import test from "node:test";
import assert from "node:assert/strict";
import {
  API_VERSION_HEADER,
  BREAKING_CHANGES,
  LEGACY_SCHEMA_VERSION,
  SCHEMA_VERSION,
  compareSchemaVersion,
  jsonError,
  jsonWithSchema,
  parseSchemaVersion,
  validateAcceptVersion,
  shouldUseLegacyCompatibility,
} from "./response.js";

test("jsonWithSchema adds schema contract and preserves status/header", async () => {
  const response = jsonWithSchema({ ok: true }, 202);
  assert.equal(response.status, 202);
  assert.equal(response.headers.get("X-OMC-API-Version"), SCHEMA_VERSION);
  assert.equal(API_VERSION_HEADER["X-OMC-API-Version"], SCHEMA_VERSION);

  const payload = await response.json();
  assert.equal(payload.ok, true);
  assert.equal(payload.schema_version, SCHEMA_VERSION);
  assert.deepEqual(payload.breaking_changes, BREAKING_CHANGES);
});

test("jsonError normalizes unknown errors and applies schema contract", async () => {
  const response = jsonError("invalid_input", null, 400);
  assert.equal(response.status, 400);
  assert.equal(response.headers.get("X-OMC-API-Version"), SCHEMA_VERSION);

  const payload = await response.json();
  assert.equal(payload.error, "invalid_input");
  assert.equal(payload.message, "unknown error");
  assert.equal(payload.schema_version, SCHEMA_VERSION);
  assert.deepEqual(payload.breaking_changes, BREAKING_CHANGES);
});

test("jsonError preserves Error message when error instance is provided", async () => {
  const response = jsonError("failed_to_read", new Error("boom"), 500);
  assert.equal(response.status, 500);
  const payload = await response.json();
  assert.equal(payload.error, "failed_to_read");
  assert.equal(payload.message, "boom");
  assert.equal(payload.schema_version, SCHEMA_VERSION);
});

test("compareSchemaVersion compares date-based schema versions", () => {
  assert.equal(compareSchemaVersion(LEGACY_SCHEMA_VERSION, SCHEMA_VERSION), -1);
  assert.equal(compareSchemaVersion(SCHEMA_VERSION, LEGACY_SCHEMA_VERSION), 1);
  assert.equal(compareSchemaVersion(SCHEMA_VERSION, SCHEMA_VERSION), 0);
});

test("shouldUseLegacyCompatibility resolves via query flag and Accept-Version", () => {
  const byQuery = new Request("http://localhost/api/runs?compat=legacy");
  assert.equal(shouldUseLegacyCompatibility(byQuery), true);

  const byIncludeRoot = new Request("http://localhost/api/runs?include_root=1");
  assert.equal(shouldUseLegacyCompatibility(byIncludeRoot), true);

  const byVersion = new Request("http://localhost/api/runs", {
    headers: { "Accept-Version": LEGACY_SCHEMA_VERSION },
  });
  assert.equal(shouldUseLegacyCompatibility(byVersion), true);

  const currentVersion = new Request("http://localhost/api/runs", {
    headers: { "Accept-Version": SCHEMA_VERSION },
  });
  assert.equal(shouldUseLegacyCompatibility(currentVersion), false);
});

test("shouldUseLegacyCompatibility returns false when request.url is malformed", () => {
  const request = { url: "://bad-url", headers: new Headers() };
  assert.equal(shouldUseLegacyCompatibility(request), false);
});

test("validateAcceptVersion rejects invalid or future Accept-Version headers", async () => {
  const invalid = new Request("http://localhost/api/runs", {
    headers: { "Accept-Version": "not-a-date" },
  });
  const invalidRes = validateAcceptVersion(invalid);
  assert.ok(invalidRes);
  assert.equal(invalidRes.status, 400);
  const invalidPayload = await invalidRes.json();
  assert.equal(invalidPayload.error, "invalid_accept_version");

  const future = new Request("http://localhost/api/runs", {
    headers: { "Accept-Version": "2099-01-01" },
  });
  const futureRes = validateAcceptVersion(future);
  assert.ok(futureRes);
  assert.equal(futureRes.status, 406);
  const futurePayload = await futureRes.json();
  assert.equal(futurePayload.error, "not_acceptable");
});

test("parseSchemaVersion rejects non-existent calendar dates and handles leap years", () => {
  assert.equal(parseSchemaVersion("2026-02-31"), null);
  assert.equal(parseSchemaVersion("2026-04-31"), null);
  assert.deepEqual(parseSchemaVersion("2024-02-29"), [2024, 2, 29]);
  assert.equal(parseSchemaVersion("2025-02-29"), null);
});

test("validateAcceptVersion rejects non-existent calendar Accept-Version dates", async () => {
  const invalidCalendar = new Request("http://localhost/api/runs", {
    headers: { "Accept-Version": "2026-02-31" },
  });
  const response = validateAcceptVersion(invalidCalendar);
  assert.ok(response);
  assert.equal(response.status, 400);
  const payload = await response.json();
  assert.equal(payload.error, "invalid_accept_version");
});
