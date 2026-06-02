import test from "node:test";
import assert from "node:assert/strict";
import path from "node:path";
import os from "node:os";
import fs from "node:fs/promises";

import {
  KNOWN_OPERATION_REASON_MAP,
  OPERATIONS_CONSOLE_V1_DATA_AVAILABILITY,
  buildOperationsConsoleSummary,
  classifyOperationalReason,
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

test("readCurrentRun exposes last activity time for freshness calculations", async () => {
  const root = await mktempDir();
  const omcDir = path.join(root, ".omc");
  await fs.mkdir(omcDir, { recursive: true });
  await fs.writeFile(
    path.join(omcDir, "pipeline_run_result.json"),
    JSON.stringify({
      status: "running",
      started_at: "2026-05-31T11:50:00Z",
      steps: {},
    }),
    "utf8",
  );

  const current = await readCurrentRun(root);
  assert.equal(current?.status, "running");
  assert.ok(current?.last_activity_at);
  assert.equal(Number.isFinite(Date.parse(String(current?.last_activity_at))), true);
});

test("operations console v1 data availability declares available and unavailable sources", async () => {
  assert.deepEqual(OPERATIONS_CONSOLE_V1_DATA_AVAILABILITY.available, [
    "current_run",
    "recent_runs",
    "run_status_counts",
    "known_reason_buckets",
    "next_action_rule",
    "per_step_duration",
  ]);
  assert.deepEqual(OPERATIONS_CONSOLE_V1_DATA_AVAILABILITY.unavailable, [
    "queue_depth",
    "worker_health",
    "parallel_agent_count",
  ]);
});

test("classifyOperationalReason maps known reasons and passes through unknown ones", async () => {
  assert.equal(KNOWN_OPERATION_REASON_MAP.stale_running.label, "실행 중 멈춤");
  assert.deepEqual(classifyOperationalReason("retry_exhausted"), {
    key: "retry_exhausted",
    label: "재시도 소진",
  });
  assert.deepEqual(classifyOperationalReason("custom_reason"), {
    key: "custom_reason",
    label: "custom_reason",
  });
});

test("buildOperationsConsoleSummary returns action required counts and freshness status", async () => {
  const currentRun = {
    run_id: "current",
    status: "running",
    started_at: "2026-05-31T11:50:00Z",
    approval_required: true,
    step_duration_summary: {
      total_duration_sec: 21,
      total_steps_with_duration: 2,
      longest_step: { name: "task", duration_sec: 13 },
    },
    failed_step: null,
  };
  const recentRuns = [
    currentRun,
    {
      run_id: "held-1",
      status: "held",
      started_at: "2026-05-31T11:00:00Z",
      failed_step: { reason: "stale_running" },
    },
    {
      run_id: "failed-1",
      status: "failed",
      started_at: "2026-05-31T10:00:00Z",
      failed_step: { reason: "retry_exhausted" },
    },
    {
      run_id: "done-1",
      status: "completed",
      started_at: "2026-05-31T09:00:00Z",
      failed_step: null,
    },
  ];

  const summary = buildOperationsConsoleSummary(currentRun, recentRuns, {
    now: "2026-05-31T12:00:00Z",
    currentUpdatedAt: "2026-05-31T11:57:00Z",
    staleMinutes: 10,
  });

  assert.equal(summary.action_required_count, 3);
  assert.equal(summary.approval_required_count, 2);
  assert.equal(summary.recovery_required_count, 2);
  assert.equal(summary.held_count, 1);
  assert.equal(summary.failed_count, 1);
  assert.equal(summary.stale_run_count, 1);
  assert.equal(summary.idle_run_count, 0);
  assert.equal(summary.freshness_status, "fresh");
  assert.equal(summary.session_health.status, "attention");
  assert.equal(summary.session_health.reason, "approval_required");
  assert.equal(summary.next_action.action, "review_approval_required_run");
  assert.equal(summary.next_action.reason, "approval_required");
  assert.equal(summary.reason_buckets.stale_running, 1);
  assert.equal(summary.reason_buckets.retry_exhausted, 1);
  assert.equal(summary.approval_queue.length, 2);
  assert.equal(summary.recovery_queue.length, 2);
  assert.equal(summary.duration_summary.total_runs_with_duration, 1);
  assert.equal(summary.duration_summary.total_duration_sec, 21);
  assert.deepEqual(summary.duration_summary.longest_step, {
    run_id: "current",
    name: "task",
    duration_sec: 13,
  });
  assert.deepEqual(summary.reason_breakdown, [
    { key: "retry_exhausted", label: "재시도 소진", count: 1 },
    { key: "stale_running", label: "실행 중 멈춤", count: 1 },
  ]);
});

test("buildOperationsConsoleSummary counts currentRun even when it is not part of recentRuns", async () => {
  const currentRun = {
    run_id: "current-held",
    status: "held",
    started_at: "2026-05-31T11:50:00Z",
    failed_step: { reason: "stale_running" },
  };
  const recentRuns = [
    {
      run_id: "done-1",
      status: "completed",
      started_at: "2026-05-31T09:00:00Z",
      failed_step: null,
    },
  ];

  const summary = buildOperationsConsoleSummary(currentRun, recentRuns, {
    now: "2026-05-31T12:00:00Z",
    currentUpdatedAt: "2026-05-31T11:57:00Z",
    staleMinutes: 10,
  });

  assert.equal(summary.action_required_count, 1);
  assert.equal(summary.approval_required_count, 1);
  assert.equal(summary.recovery_required_count, 1);
  assert.equal(summary.held_count, 1);
  assert.equal(summary.stale_run_count, 1);
  assert.equal(summary.next_action.action, "review_held_run");
});

test("buildOperationsConsoleSummary does not double count duplicated currentRun by run_id", async () => {
  const currentRun = {
    run_id: "dup-run",
    status: "failed",
    started_at: "2026-05-31T11:50:00Z",
    failed_step: { reason: "retry_exhausted" },
  };
  const recentRuns = [
    currentRun,
    {
      run_id: "done-1",
      status: "completed",
      started_at: "2026-05-31T09:00:00Z",
      failed_step: null,
    },
  ];

  const summary = buildOperationsConsoleSummary(currentRun, recentRuns, {
    now: "2026-05-31T12:00:00Z",
  });

  assert.equal(summary.action_required_count, 1);
  assert.equal(summary.failed_count, 1);
  assert.equal(summary.approval_required_count, 0);
  assert.equal(summary.recovery_required_count, 1);
  assert.equal(summary.stale_run_count, 0);
  assert.equal(summary.reason_buckets.retry_exhausted, 1);
});

test("buildOperationsConsoleSummary reports idle current run when it is not running", async () => {
  const currentRun = {
    run_id: "current-done",
    status: "completed",
    started_at: "2026-05-31T11:50:00Z",
    failed_step: null,
  };

  const summary = buildOperationsConsoleSummary(currentRun, [], {
    now: "2026-05-31T12:00:00Z",
  });

  assert.equal(summary.idle_run_count, 1);
  assert.equal(summary.freshness_status, "idle");
  assert.equal(summary.approval_required_count, 0);
  assert.equal(summary.recovery_required_count, 0);
});

test("buildOperationsConsoleSummary treats approval_required current run as action required even without held or failed runs", async () => {
  const currentRun = {
    run_id: "current-awaiting-approval",
    status: "running",
    started_at: "2026-05-31T11:50:00Z",
    approval_required: true,
    failed_step: null,
  };

  const summary = buildOperationsConsoleSummary(currentRun, [], {
    now: "2026-05-31T12:00:00Z",
    currentUpdatedAt: "2026-05-31T11:57:00Z",
    staleMinutes: 10,
  });

  assert.equal(summary.action_required_count, 1);
  assert.equal(summary.approval_required_count, 1);
  assert.equal(summary.recovery_required_count, 0);
  assert.equal(summary.held_count, 0);
  assert.equal(summary.failed_count, 0);
  assert.equal(summary.next_action.action, "review_approval_required_run");
  assert.equal(summary.next_action.reason, "approval_required");
  assert.equal(summary.session_health.status, "attention");
  assert.equal(summary.session_health.reason, "approval_required");
});

test("buildOperationsConsoleSummary preserves approval queue semantics for plan confirmation artifact", async () => {
  const currentRun = summarizeRun("20260602T163447-codex-ops-approval-gate-2", {
    status: "aborted",
    branch: "codex-ops-approval-gate-2",
    started_at: "2026-06-02T16:34:47Z",
    finished_at: "2026-06-02T16:35:29Z",
    approval_required: true,
    manual_gate_reason: "plan_confirmation",
    last_heartbeat_at: "2026-06-02T16:35:29Z",
    steps: {
      plan: {
        status: "completed",
        started_at: "2026-06-02T16:34:48Z",
        finished_at: "2026-06-02T16:35:09Z",
        duration_sec: 21,
      },
    },
  });

  const summary = buildOperationsConsoleSummary(currentRun, [], {
    now: "2026-06-02T16:35:29Z",
    currentUpdatedAt: "2026-06-02T16:35:29Z",
    staleMinutes: 10,
  });

  assert.equal(summary.action_required_count, 1);
  assert.equal(summary.approval_required_count, 1);
  assert.equal(summary.recovery_required_count, 0);
  assert.equal(summary.next_action.action, "review_approval_required_run");
  assert.equal(summary.next_action.reason, "approval_required");
  assert.equal(summary.approval_queue.length, 1);
  assert.equal(summary.approval_queue[0].manual_gate_reason, "plan_confirmation");
  assert.equal(summary.duration_summary.total_runs_with_duration, 1);
  assert.equal(summary.duration_summary.total_duration_sec, 21);
  assert.deepEqual(summary.duration_summary.longest_step, {
    run_id: "20260602T163447-codex-ops-approval-gate-2",
    name: "plan",
    duration_sec: 21,
  });
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

test("summarizeRun accepts compact UTC started_at emitted by autopilot", async () => {
  const summary = summarizeRun("run-compact", {
    status: "completed",
    started_at: "2026-06-01T085646Z",
    approval_required: true,
    retry_count: 2,
    resume_count: 1,
    last_heartbeat_at: "2026-06-01T08:57:00Z",
    manual_gate_reason: "plan_confirmation",
    steps: {
      plan: { status: "completed", started_at: "2026-06-01T08:56:46Z", finished_at: "2026-06-01T08:56:49Z", duration_sec: 3 },
      task: { status: "completed", started_at: "2026-06-01T08:56:49Z", finished_at: "2026-06-01T08:56:59Z", duration_sec: 10 },
    },
  });
  assert.equal(summary.status, "completed");
  assert.equal(summary.failed_step, null);
  assert.equal(summary.approval_required, true);
  assert.equal(summary.retry_count, 2);
  assert.equal(summary.resume_count, 1);
  assert.equal(summary.last_heartbeat_at, "2026-06-01T08:57:00Z");
  assert.equal(summary.manual_gate_reason, "plan_confirmation");
  assert.deepEqual(summary.step_duration_summary, {
    total_duration_sec: 13,
    total_steps_with_duration: 2,
    longest_step: { name: "task", duration_sec: 10 },
  });
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

test("summarizeRun converts stale running record to held", async () => {
  const summary = summarizeRun(
    "run-stale",
    {
      status: "running",
      started_at: "2026-05-31T09:00:00Z",
      finished_at: null,
      steps: {},
    },
    { now: "2026-05-31T12:00:00Z", staleRunningMinutes: 10 },
  );
  assert.equal(summary.status, "held");
  assert.equal(summary.failed_step?.reason, "stale_running");
});

test("summarizeRun keeps running when last activity is recent even if started_at is old", async () => {
  const summary = summarizeRun(
    "run-active",
    {
      status: "running",
      started_at: "2026-05-31T09:00:00Z",
      finished_at: null,
      steps: {},
    },
    {
      now: "2026-05-31T12:00:00Z",
      staleRunningMinutes: 10,
      lastActivityAt: "2026-05-31T11:56:00Z",
    },
  );
  assert.equal(summary.status, "running");
  assert.equal(summary.failed_step, null);
});

test("summarizeRun honors stale running minutes from env when option is omitted", async () => {
  const previous = process.env.OMC_DASHBOARD_STALE_RUNNING_MINUTES;
  try {
    process.env.OMC_DASHBOARD_STALE_RUNNING_MINUTES = "240";
    const summary = summarizeRun(
      "run-env-threshold",
      {
        status: "running",
        started_at: "2026-05-31T09:00:00Z",
        finished_at: null,
        steps: {},
      },
      { now: "2026-05-31T12:00:00Z" },
    );
    assert.equal(summary.status, "running");
    assert.equal(summary.failed_step, null);
  } finally {
    if (previous === undefined) {
      delete process.env.OMC_DASHBOARD_STALE_RUNNING_MINUTES;
    } else {
      process.env.OMC_DASHBOARD_STALE_RUNNING_MINUTES = previous;
    }
  }
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

test("listRecentRuns includes compact UTC started_at within sinceDays window", async () => {
  const root = await mktempDir();
  const runsDir = path.join(root, ".omc", "runs");
  await fs.mkdir(runsDir, { recursive: true });

  const runId = "20260531T120000-compact";
  const runPath = path.join(runsDir, runId);
  await fs.mkdir(runPath, { recursive: true });
  await fs.writeFile(
    path.join(runPath, "result.json"),
    JSON.stringify({ status: "completed", started_at: "2026-05-31T113000Z", steps: {} }),
    "utf8",
  );

  const runs = await listRecentRuns(root, 10, { sinceDays: 7, now: "2026-05-31T12:00:00Z" });
  assert.equal(runs.length, 1);
  assert.equal(runs[0].run_id, runId);
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
