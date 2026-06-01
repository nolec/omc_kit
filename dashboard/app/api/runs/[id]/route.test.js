import test from "node:test";
import assert from "node:assert/strict";
import path from "node:path";
import os from "node:os";
import fs from "node:fs/promises";
import { GET } from "./route.js";

async function mktempDir() {
  return fs.mkdtemp(path.join(os.tmpdir(), "omc-dashboard-route-"));
}

test("run detail route handles not-found without exposing root path", async () => {
  const request = new Request("http://localhost/api/runs/missing");
  const response = await GET(request, { params: { id: "missing" } });
  assert.equal(response.status, 404);
  assert.equal(response.headers.get("X-OMC-API-Version"), "2026-06-01");
  const payload = await response.json();
  assert.equal(payload.error, "run not found");
  assert.equal("root" in payload, false);
  assert.equal(payload.schema_version, "2026-06-01");
  assert.equal(payload.breaking_changes?.root_removed, true);
});

test("run detail route returns detail for existing run without exposing root path", async () => {
  const root = await mktempDir();
  const previousRoot = process.env.OMC_DASHBOARD_ROOT;
  try {
    process.env.OMC_DASHBOARD_ROOT = root;
    const runId = "20260531T120000-a";
    const runDir = path.join(root, ".omc", "runs", runId);
    await fs.mkdir(runDir, { recursive: true });
    await fs.writeFile(path.join(runDir, "result.json"), JSON.stringify({ status: "completed", steps: {} }), "utf8");

    const request = new Request(`http://localhost/api/runs/${runId}`);
    const response = await GET(request, { params: { id: runId } });
    assert.equal(response.status, 200);
    assert.equal(response.headers.get("X-OMC-API-Version"), "2026-06-01");
    const payload = await response.json();
    assert.deepEqual(Object.keys(payload).sort(), ["breaking_changes", "deprecation", "detail", "schema_version"]);
    assert.deepEqual(Object.keys(payload.detail).sort(), ["raw", "run_id", "summary"]);
    assert.equal(typeof payload.detail.raw, "object");
    assert.equal(typeof payload.detail.summary, "object");
    assert.equal(payload.detail?.run_id, runId);
    assert.equal(payload.detail?.summary?.status, "completed");
    assert.equal("root" in payload, false);
    assert.equal(payload.schema_version, "2026-06-01");
    assert.equal(payload.breaking_changes?.root_removed, true);
  } finally {
    if (previousRoot === undefined) {
      delete process.env.OMC_DASHBOARD_ROOT;
    } else {
      process.env.OMC_DASHBOARD_ROOT = previousRoot;
    }
  }
});

test("run detail route returns 400 for invalid params", async () => {
  const request = new Request("http://localhost/api/runs/abc");
  const response = await GET(request, { params: null });
  assert.equal(response.status, 400);
  assert.equal(response.headers.get("X-OMC-API-Version"), "2026-06-01");
  const payload = await response.json();
  assert.equal(payload.error, "invalid_run_id");
  assert.equal(payload.message, "run_id is required");
  assert.equal(payload.schema_version, "2026-06-01");
  assert.equal(payload.breaking_changes?.root_removed, true);
});

test("run detail route provides legacy root when include_root is requested", async () => {
  const request = new Request("http://localhost/api/runs/missing?include_root=1");
  const response = await GET(request, { params: { id: "missing" } });
  assert.equal(response.status, 404);
  const payload = await response.json();
  assert.equal(typeof payload.root, "string");
});
