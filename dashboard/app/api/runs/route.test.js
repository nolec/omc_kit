import test from "node:test";
import assert from "node:assert/strict";
import fs from "node:fs/promises";
import os from "node:os";
import path from "node:path";
import { GET, resolveMaxRuns, resolveSinceDays } from "./route.js";

async function mktempDir() {
  return fs.mkdtemp(path.join(os.tmpdir(), "omc-dashboard-runs-route-"));
}

test("resolveMaxRuns caps max to 100", () => {
  assert.equal(resolveMaxRuns("1000"), 100);
  assert.equal(resolveMaxRuns("100"), 100);
  assert.equal(resolveMaxRuns("0"), 50);
  assert.equal(resolveMaxRuns("-1"), 50);
  assert.equal(resolveMaxRuns("abc"), 50);
  assert.equal(resolveMaxRuns("1.9"), 1);
});

test("resolveSinceDays normalizes optional recency window", () => {
  assert.equal(resolveSinceDays(null), null);
  assert.equal(resolveSinceDays(""), null);
  assert.equal(resolveSinceDays("7"), 7);
  assert.equal(resolveSinceDays("7.9"), 7);
  assert.equal(resolveSinceDays("0"), null);
  assert.equal(resolveSinceDays("-1"), null);
  assert.equal(resolveSinceDays("abc"), null);
  assert.equal(resolveSinceDays("400"), 365);
});

test("runs route returns count and runs without exposing root path", async () => {
  const request = new Request("http://localhost/api/runs?max=2");
  const response = await GET(request);
  assert.equal(response.status, 200);
  assert.equal(response.headers.get("X-OMC-API-Version"), "2026-06-01");
  const payload = await response.json();
  assert.deepEqual(Object.keys(payload).sort(), [
    "breaking_changes",
    "count",
    "deprecation",
    "runs",
    "schema_version",
  ]);
  assert.equal(typeof payload.count, "number");
  assert.ok(Array.isArray(payload.runs));
  for (const run of payload.runs) {
    assert.equal(typeof run.run_id, "string");
    assert.equal(typeof run.status, "string");
    assert.ok(run.started_at === null || typeof run.started_at === "string");
    assert.equal("finished_at" in run, true);
    assert.equal("failed_step" in run, true);
  }
  assert.equal("root" in payload, false);
  assert.equal(payload.schema_version, "2026-06-01");
  assert.equal(payload.breaking_changes?.root_removed, true);
});

test("runs route handles internal errors explicitly without silent failure", async () => {
  const root = await mktempDir();
  const previousRoot = process.env.OMC_DASHBOARD_ROOT;
  try {
    process.env.OMC_DASHBOARD_ROOT = path.join(root, "not-a-directory.json");
    await fs.writeFile(process.env.OMC_DASHBOARD_ROOT, "{}", "utf8");

    const request = new Request("http://localhost/api/runs?max=2");
    const response = await GET(request);
    assert.equal(response.status, 500);
    assert.equal(response.headers.get("X-OMC-API-Version"), "2026-06-01");
    const payload = await response.json();
    assert.equal(payload.error, "failed_to_list_runs");
    assert.equal(typeof payload.message, "string");
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

test("runs route applies since_days recency window", async () => {
  const root = await mktempDir();
  const previousRoot = process.env.OMC_DASHBOARD_ROOT;
  try {
    process.env.OMC_DASHBOARD_ROOT = root;
    const runsDir = path.join(root, ".omc", "runs");
    await fs.mkdir(runsDir, { recursive: true });

    const recentId = "20260531T100000-recent";
    const oldId = "20260501T100000-old";
    await fs.mkdir(path.join(runsDir, recentId), { recursive: true });
    await fs.mkdir(path.join(runsDir, oldId), { recursive: true });
    const now = Date.now();
    const withinWindow = new Date(now - 2 * 24 * 60 * 60 * 1000).toISOString();
    const outsideWindow = new Date(now - 20 * 24 * 60 * 60 * 1000).toISOString();
    await fs.writeFile(
      path.join(runsDir, recentId, "result.json"),
      JSON.stringify({ status: "completed", started_at: withinWindow, steps: {} }),
      "utf8",
    );
    await fs.writeFile(
      path.join(runsDir, oldId, "result.json"),
      JSON.stringify({ status: "completed", started_at: outsideWindow, steps: {} }),
      "utf8",
    );

    const request = new Request("http://localhost/api/runs?since_days=7");
    const response = await GET(request);
    assert.equal(response.headers.get("X-OMC-API-Version"), "2026-06-01");
    const payload = await response.json();
    assert.equal(payload.count, 1);
    assert.equal(payload.runs[0]?.run_id, recentId);
  } finally {
    if (previousRoot === undefined) {
      delete process.env.OMC_DASHBOARD_ROOT;
    } else {
      process.env.OMC_DASHBOARD_ROOT = previousRoot;
    }
  }
});

test("runs route provides legacy root when compat is requested", async () => {
  const request = new Request("http://localhost/api/runs?compat=legacy&max=1");
  const response = await GET(request);
  assert.equal(response.status, 200);
  const payload = await response.json();
  assert.equal(typeof payload.root, "string");
});

test("runs route includes version transition warning metadata", async () => {
  const request = new Request("http://localhost/api/runs?max=1");
  const response = await GET(request);
  assert.equal(response.status, 200);
  const payload = await response.json();
  assert.equal(typeof payload.deprecation, "object");
  assert.equal(typeof payload.deprecation?.legacy_root_sunset, "string");
  assert.equal(typeof payload.deprecation?.legacy_root_deprecated, "boolean");
});
