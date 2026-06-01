import test from "node:test";
import assert from "node:assert/strict";
import { GET } from "./route.js";
import { jsonError } from "../_shared/response.js";

test("current route returns current payload without exposing root path", async () => {
  const response = await GET();
  assert.equal(response.status, 200);
  assert.equal(response.headers.get("X-OMC-API-Version"), "2026-06-01");
  const payload = await response.json();
  assert.deepEqual(Object.keys(payload).sort(), [
    "breaking_changes",
    "current",
    "deprecation",
    "schema_version",
  ]);
  assert.ok(payload.current === null || typeof payload.current === "object");
  assert.ok("current" in payload);
  assert.equal("root" in payload, false);
  assert.equal(payload.schema_version, "2026-06-01");
  assert.equal(payload.breaking_changes?.root_removed, true);
});

test("shared api error helper returns standardized 500 payload and version header", async () => {
  const response = jsonError("failed_to_read_current_run", new Error("boom"), 500);
  assert.equal(response.status, 500);
  assert.equal(response.headers.get("X-OMC-API-Version"), "2026-06-01");
  const payload = await response.json();
  assert.equal(payload.error, "failed_to_read_current_run");
  assert.equal(payload.message, "boom");
  assert.equal(payload.schema_version, "2026-06-01");
  assert.equal(payload.breaking_changes?.root_removed, true);
});

test("current route provides legacy root when Accept-Version is legacy", async () => {
  const request = new Request("http://localhost/api/current", {
    headers: { "Accept-Version": "2026-05-01" },
  });
  const response = await GET(request);
  assert.equal(response.status, 200);
  const payload = await response.json();
  assert.equal(typeof payload.root, "string");
});

test("current route includes version transition warning metadata", async () => {
  const response = await GET(new Request("http://localhost/api/current"));
  assert.equal(response.status, 200);
  const payload = await response.json();
  assert.equal(typeof payload.deprecation, "object");
  assert.equal(typeof payload.deprecation?.legacy_root_sunset, "string");
  assert.equal(typeof payload.deprecation?.legacy_root_deprecated, "boolean");
});
