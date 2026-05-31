import test from "node:test";
import assert from "node:assert/strict";
import path from "node:path";
import os from "node:os";
import fs from "node:fs/promises";

import {
  listRecentRuns,
  readCurrentRun,
  readRunDetail,
} from "./omc-runs.mjs";

async function mktempDir() {
  return fs.mkdtemp(path.join(os.tmpdir(), "omc-dashboard-"));
}

test("readCurrentRun returns null when pipeline file missing", async () => {
  const root = await mktempDir();
  const current = await readCurrentRun(root);
  assert.equal(current, null);
});

test("listRecentRuns returns at most maxRuns sorted by run_id desc", async () => {
  const root = await mktempDir();
  const runsDir = path.join(root, ".omc", "runs");
  await fs.mkdir(runsDir, { recursive: true });

  for (const runId of ["20260531T010000-a", "20260531T030000-c", "20260531T020000-b"]) {
    const runPath = path.join(runsDir, runId);
    await fs.mkdir(runPath, { recursive: true });
    await fs.writeFile(
      path.join(runPath, "result.json"),
      JSON.stringify({ status: "completed", steps: {} }),
      "utf8",
    );
  }

  const runs = await listRecentRuns(root, 2);
  assert.equal(runs.length, 2);
  assert.equal(runs[0].run_id, "20260531T030000-c");
  assert.equal(runs[1].run_id, "20260531T020000-b");
});

test("readRunDetail returns invalid payload for bad JSON", async () => {
  const root = await mktempDir();
  const runId = "20260531T090000-z";
  const runPath = path.join(root, ".omc", "runs", runId);
  await fs.mkdir(runPath, { recursive: true });
  await fs.writeFile(path.join(runPath, "result.json"), "{bad json", "utf8");

  const detail = await readRunDetail(root, runId);
  assert.equal(detail?.run_id, runId);
  assert.equal(detail?.status, "invalid");
});
