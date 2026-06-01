import test from "node:test";
import assert from "node:assert/strict";
import path from "node:path";
import os from "node:os";
import fs from "node:fs/promises";

import {
  listRecentRuns,
  readCurrentRun,
  readRunDetail,
  summarizeRun,
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

test("summarizeRun classifies invalid started_at as data-quality failure", async () => {
  const summary = summarizeRun("run-1", {
    status: "completed",
    started_at: "not-a-date",
    steps: {},
  });

  assert.equal(summary.status, "invalid_started_at");
  assert.equal(summary.failed_step?.reason, "invalid_started_at");
});

test("summarizeRun preserves operational statuses including cancelled timeout and held", async () => {
  const cancelled = summarizeRun("run-cancelled", { status: "cancelled", steps: {} });
  const canceledAlias = summarizeRun("run-canceled", { status: "canceled", steps: {} });
  const timeout = summarizeRun("run-timeout", { status: "timeout", steps: {} });
  const held = summarizeRun("run-held", { status: "held", steps: {} });

  assert.equal(cancelled.status, "cancelled");
  assert.equal(canceledAlias.status, "cancelled");
  assert.equal(timeout.status, "timeout");
  assert.equal(held.status, "held");
});

test("listRecentRuns supports sinceDays filter to avoid full historical scan", async () => {
  const root = await mktempDir();
  const runsDir = path.join(root, ".omc", "runs");
  await fs.mkdir(runsDir, { recursive: true });

  const recentId = "20260531T100000-recent";
  const oldId = "20260501T100000-old";

  for (const runId of [recentId, oldId]) {
    const runPath = path.join(runsDir, runId);
    await fs.mkdir(runPath, { recursive: true });
    await fs.writeFile(
      path.join(runPath, "result.json"),
      JSON.stringify({
        status: "completed",
        started_at: runId.startsWith("20260531") ? "2026-05-31T10:00:00Z" : "2026-05-01T10:00:00Z",
        steps: {},
      }),
      "utf8",
    );
  }

  const runs = await listRecentRuns(root, 50, { sinceDays: 7, now: "2026-05-31T12:00:00Z" });
  assert.equal(runs.length, 1);
  assert.equal(runs[0].run_id, recentId);
});

test("listRecentRuns includes invalid_started_at entries even when sinceDays is set", async () => {
  const root = await mktempDir();
  const runsDir = path.join(root, ".omc", "runs");
  await fs.mkdir(runsDir, { recursive: true });

  const invalidId = "20260531T110000-invalid";
  const oldValidId = "20260501T100000-old";

  for (const runId of [invalidId, oldValidId]) {
    const runPath = path.join(runsDir, runId);
    await fs.mkdir(runPath, { recursive: true });
    const payload =
      runId === invalidId
        ? { status: "completed", started_at: "not-a-date", steps: {} }
        : { status: "completed", started_at: "2026-05-01T10:00:00Z", steps: {} };
    await fs.writeFile(path.join(runPath, "result.json"), JSON.stringify(payload), "utf8");
  }

  const runs = await listRecentRuns(root, 50, { sinceDays: 7, now: "2026-05-31T12:00:00Z" });
  assert.equal(runs.length, 1);
  assert.equal(runs[0].run_id, invalidId);
  assert.equal(runs[0].status, "invalid_started_at");
});

test("listRecentRuns applies sinceDays before maxRuns slicing to avoid dropping recent runs", async () => {
  const root = await mktempDir();
  const runsDir = path.join(root, ".omc", "runs");
  await fs.mkdir(runsDir, { recursive: true });

  const oldButLexicographicallyNewer = "zzzz-old";
  const recentButLexicographicallyOlder = "aaaa-recent";

  await fs.mkdir(path.join(runsDir, oldButLexicographicallyNewer), { recursive: true });
  await fs.writeFile(
    path.join(runsDir, oldButLexicographicallyNewer, "result.json"),
    JSON.stringify({ status: "completed", started_at: "2026-01-01T00:00:00Z", steps: {} }),
    "utf8",
  );

  await fs.mkdir(path.join(runsDir, recentButLexicographicallyOlder), { recursive: true });
  await fs.writeFile(
    path.join(runsDir, recentButLexicographicallyOlder, "result.json"),
    JSON.stringify({ status: "completed", started_at: "2026-05-31T10:00:00Z", steps: {} }),
    "utf8",
  );

  const runs = await listRecentRuns(root, 1, { sinceDays: 7, now: "2026-05-31T12:00:00Z" });
  assert.equal(runs.length, 1);
  assert.equal(runs[0].run_id, recentButLexicographicallyOlder);
});

test("listRecentRuns falls back to expanded scan when prefilter misses recent runs", async () => {
  const root = await mktempDir();
  const runsDir = path.join(root, ".omc", "runs");
  await fs.mkdir(runsDir, { recursive: true });

  for (let i = 0; i < 40; i += 1) {
    const runId = `zz-${String(i).padStart(2, "0")}`;
    await fs.mkdir(path.join(runsDir, runId), { recursive: true });
    await fs.writeFile(
      path.join(runsDir, runId, "result.json"),
      JSON.stringify({ status: "completed", started_at: "2025-01-01T00:00:00Z", steps: {} }),
      "utf8",
    );
  }

  const recentRunId = "aa-recent-real";
  await fs.mkdir(path.join(runsDir, recentRunId), { recursive: true });
  await fs.writeFile(
    path.join(runsDir, recentRunId, "result.json"),
    JSON.stringify({ status: "completed", started_at: "2026-05-31T11:00:00Z", steps: {} }),
    "utf8",
  );

  const runs = await listRecentRuns(root, 1, {
    sinceDays: 7,
    now: "2026-05-31T12:00:00Z",
    scanLimit: 10,
  });
  assert.equal(runs.length, 1);
  assert.equal(runs[0].run_id, recentRunId);
});

test("listRecentRuns expands scan when results are below maxRuns to avoid false negatives", async () => {
  const root = await mktempDir();
  const runsDir = path.join(root, ".omc", "runs");
  await fs.mkdir(runsDir, { recursive: true });

  for (let i = 0; i < 40; i += 1) {
    const runId = `zz-${String(i).padStart(2, "0")}`;
    await fs.mkdir(path.join(runsDir, runId), { recursive: true });
    await fs.writeFile(
      path.join(runsDir, runId, "result.json"),
      JSON.stringify({ status: "completed", started_at: "2025-01-01T00:00:00Z", steps: {} }),
      "utf8",
    );
  }

  const inPrefilterRecent = "zz-recent-in-prefilter";
  await fs.mkdir(path.join(runsDir, inPrefilterRecent), { recursive: true });
  await fs.writeFile(
    path.join(runsDir, inPrefilterRecent, "result.json"),
    JSON.stringify({ status: "completed", started_at: "2026-05-31T11:30:00Z", steps: {} }),
    "utf8",
  );

  const outsidePrefilterRecent = "aa-recent-outside-prefilter";
  await fs.mkdir(path.join(runsDir, outsidePrefilterRecent), { recursive: true });
  await fs.writeFile(
    path.join(runsDir, outsidePrefilterRecent, "result.json"),
    JSON.stringify({ status: "completed", started_at: "2026-05-31T11:45:00Z", steps: {} }),
    "utf8",
  );

  const runs = await listRecentRuns(root, 2, {
    sinceDays: 7,
    now: "2026-05-31T12:00:00Z",
    scanLimit: 10,
  });
  const ids = runs.map((r) => r.run_id);
  assert.equal(runs.length, 2);
  assert.ok(ids.includes(inPrefilterRecent));
  assert.ok(ids.includes(outsidePrefilterRecent));
});

test("listRecentRuns does not duplicate run_id when expanded scan revisits the same candidate", async () => {
  const root = await mktempDir();
  const runsDir = path.join(root, ".omc", "runs");
  await fs.mkdir(runsDir, { recursive: true });

  for (let i = 0; i < 40; i += 1) {
    const runId = `zz-${String(i).padStart(2, "0")}`;
    await fs.mkdir(path.join(runsDir, runId), { recursive: true });
    await fs.writeFile(
      path.join(runsDir, runId, "result.json"),
      JSON.stringify({ status: "completed", started_at: "2025-01-01T00:00:00Z", steps: {} }),
      "utf8",
    );
  }

  const inPrefilterRecent = "zz-recent-in-prefilter";
  await fs.mkdir(path.join(runsDir, inPrefilterRecent), { recursive: true });
  await fs.writeFile(
    path.join(runsDir, inPrefilterRecent, "result.json"),
    JSON.stringify({ status: "completed", started_at: "2026-05-31T11:30:00Z", steps: {} }),
    "utf8",
  );

  const outsidePrefilterRecent = "aa-recent-outside-prefilter";
  await fs.mkdir(path.join(runsDir, outsidePrefilterRecent), { recursive: true });
  await fs.writeFile(
    path.join(runsDir, outsidePrefilterRecent, "result.json"),
    JSON.stringify({ status: "completed", started_at: "2026-05-31T11:45:00Z", steps: {} }),
    "utf8",
  );

  const runs = await listRecentRuns(root, 2, {
    sinceDays: 7,
    now: "2026-05-31T12:00:00Z",
    scanLimit: 10,
  });
  const ids = runs.map((r) => r.run_id);
  assert.equal(runs.length, 2);
  assert.deepEqual(new Set(ids).size, 2);
  assert.ok(ids.includes(inPrefilterRecent));
  assert.ok(ids.includes(outsidePrefilterRecent));
});

test("listRecentRuns in sinceDays mode does not stop early on invalid-first candidates", async () => {
  const root = await mktempDir();
  const runsDir = path.join(root, ".omc", "runs");
  await fs.mkdir(runsDir, { recursive: true });

  const invalidNewest = "20260531T120000-invalid";
  const validSecond = "20260531T110000-valid";

  await fs.mkdir(path.join(runsDir, invalidNewest), { recursive: true });
  await fs.writeFile(path.join(runsDir, invalidNewest, "result.json"), "{bad json", "utf8");

  await fs.mkdir(path.join(runsDir, validSecond), { recursive: true });
  await fs.writeFile(
    path.join(runsDir, validSecond, "result.json"),
    JSON.stringify({ status: "completed", started_at: "2026-05-31T11:00:00Z", steps: {} }),
    "utf8",
  );

  const runs = await listRecentRuns(root, 1, {
    sinceDays: 7,
    now: "2026-05-31T12:30:00Z",
    scanLimit: 10,
  });

  assert.equal(runs.length, 1);
  assert.equal(runs[0].run_id, validSecond);
  assert.equal(runs[0].status, "completed");
});

test("listRecentRuns with sinceDays null scans beyond scanLimit when results are insufficient", async () => {
  const root = await mktempDir();
  const runsDir = path.join(root, ".omc", "runs");
  await fs.mkdir(runsDir, { recursive: true });

  for (let i = 0; i < 20; i += 1) {
    const runId = `run-${String(i).padStart(2, "0")}`;
    await fs.mkdir(path.join(runsDir, runId), { recursive: true });
    const payload =
      i >= 10
        ? { status: "completed", started_at: "not-a-date", steps: {} }
        : { status: "completed", started_at: "2026-05-31T11:00:00Z", steps: {} };
    await fs.writeFile(path.join(runsDir, runId, "result.json"), JSON.stringify(payload), "utf8");
  }

  const runs = await listRecentRuns(root, 5, { sinceDays: null, scanLimit: 10 });
  assert.equal(runs.length, 5);
  assert.ok(runs.every((run) => run.status !== "invalid_started_at"));
});

test("listRecentRuns bounds stat fan-out by scanLimit when sinceDays is enabled", async (t) => {
  const root = await mktempDir();
  const runsDir = path.join(root, ".omc", "runs");
  await fs.mkdir(runsDir, { recursive: true });

  for (let i = 0; i < 120; i += 1) {
    const runId = `run-${String(i).padStart(3, "0")}`;
    await fs.mkdir(path.join(runsDir, runId), { recursive: true });
    await fs.writeFile(
      path.join(runsDir, runId, "result.json"),
      JSON.stringify({ status: "completed", started_at: "2026-05-31T11:00:00Z", steps: {} }),
      "utf8",
    );
  }

  let statCalls = 0;
  const originalStat = fs.stat;
  t.mock.method(fs, "stat", async (...args) => {
    statCalls += 1;
    return originalStat(...args);
  });

  await listRecentRuns(root, 5, { sinceDays: 7, now: "2026-05-31T12:00:00Z", scanLimit: 10 });
  assert.ok(statCalls <= 10, `expected <=10 stat calls, got ${statCalls}`);
});

test("listRecentRuns throws for invalid now option to avoid silent filtering", async () => {
  const root = await mktempDir();
  const runsDir = path.join(root, ".omc", "runs");
  await fs.mkdir(runsDir, { recursive: true });

  await assert.rejects(
    () => listRecentRuns(root, 10, { sinceDays: 7, now: "not-a-date" }),
    /invalid now option/,
  );
});
