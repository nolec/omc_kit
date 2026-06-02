import fs from "node:fs/promises";
import path from "node:path";

const KNOWN_RUN_STATUSES = new Set([
  "pending",
  "running",
  "completed",
  "failed",
  "retry_exhausted",
  "cancelled",
  "timeout",
  "held",
  "invalid",
]);

export const OPERATIONS_CONSOLE_V1_DATA_AVAILABILITY = {
  available: [
    "current_run",
    "recent_runs",
    "run_status_counts",
    "known_reason_buckets",
    "next_action_rule",
    "per_step_duration",
  ],
  unavailable: [
    "queue_depth",
    "worker_health",
    "parallel_agent_count",
  ],
};

export const KNOWN_OPERATION_REASON_MAP = {
  stale_running: { label: "실행 중 멈춤" },
  invalid_started_at: { label: "시작 시간 오류" },
  retry_exhausted: { label: "재시도 소진" },
  failed_critique_loop: { label: "critique 루프 실패" },
};

function resultPath(root) {
  return path.join(root, ".omc", "pipeline_run_result.json");
}

function runsDir(root) {
  return path.join(root, ".omc", "runs");
}

/**
 * Build prioritized run candidates based on file mtime.
 * Operational defaults:
 * - First pass scans only `scanLimit` lexicographic candidates for bounded cost.
 * - If sinceDays filter finds no in-window run, second pass expands to all runs.
 * @param {string} dir
 * @param {string[]} runIds
 * @param {number} scanLimit
 * @param {boolean} forceFullScan
 * @returns {Promise<Array<{ runId: string, mtimeMs: number }>>}
 */
async function buildRecentCandidates(dir, runIds, scanLimit, forceFullScan = false) {
  const prefiltered = runIds
    .slice()
    .sort((a, b) => b.localeCompare(a))
    .slice(0, forceFullScan ? runIds.length : scanLimit);
  const candidates = await Promise.all(
    prefiltered.map(async (runId) => {
      const filePath = path.join(dir, runId, "result.json");
      try {
        const stat = await fs.stat(filePath);
        return { runId, mtimeMs: stat.mtimeMs };
      } catch (error) {
        if (error?.code === "ENOENT") {
          return { runId, mtimeMs: 0 };
        }
        throw error;
      }
    }),
  );
  candidates.sort((a, b) => b.mtimeMs - a.mtimeMs || b.runId.localeCompare(a.runId));
  return candidates;
}

async function readJson(filePath) {
  const raw = await fs.readFile(filePath, "utf8");
  return JSON.parse(raw);
}

/**
 * @param {string | null | undefined} startedAt
 * @returns {boolean}
 */
function isValidStartedAt(startedAt) {
  if (!startedAt) {
    return true;
  }
  return Number.isFinite(Date.parse(normalizeTimestamp(startedAt)));
}

/**
 * Normalize timestamp for compatibility with historical autopilot output.
 * Accepts compact UTC format: YYYY-MM-DDTHHMMSSZ
 * @param {string | null | undefined} value
 * @returns {string | null | undefined}
 */
function normalizeTimestamp(value) {
  if (typeof value !== "string") {
    return value;
  }
  const compactUtc = /^(\d{4}-\d{2}-\d{2})T(\d{2})(\d{2})(\d{2})Z$/;
  const matched = compactUtc.exec(value);
  if (!matched) {
    return value;
  }
  return `${matched[1]}T${matched[2]}:${matched[3]}:${matched[4]}Z`;
}

/**
 * @param {unknown} rawStatus
 * @returns {string}
 */
function normalizeStatus(rawStatus) {
  if (typeof rawStatus !== "string" || !rawStatus.trim()) {
    return "unknown";
  }
  const normalized = rawStatus.trim().toLowerCase();
  if (normalized === "canceled") {
    return "cancelled";
  }
  return KNOWN_RUN_STATUSES.has(normalized) ? normalized : "unknown";
}

function resolveStaleRunningMinutes(optionValue) {
  if (Number.isFinite(optionValue)) {
    return Number(optionValue);
  }
  const envRaw = process.env.OMC_DASHBOARD_STALE_RUNNING_MINUTES;
  const envValue = Number(envRaw);
  if (Number.isFinite(envValue) && envValue > 0) {
    return envValue;
  }
  return 10;
}

function normalizeReason(reason) {
  if (typeof reason !== "string" || !reason.trim()) {
    return "unknown";
  }
  return reason.trim().toLowerCase();
}

function parseTimestampMs(value) {
  if (value instanceof Date) {
    return value.getTime();
  }
  if (typeof value === "number" && Number.isFinite(value)) {
    return value;
  }
  const normalized = normalizeTimestamp(value);
  const parsed = Date.parse(String(normalized));
  return Number.isFinite(parsed) ? parsed : Number.NaN;
}

export function classifyOperationalReason(reason) {
  const key = normalizeReason(reason);
  const known = KNOWN_OPERATION_REASON_MAP[key];
  return {
    key,
    label: known?.label ?? key,
  };
}

function resolveFreshnessStatus(currentRun, currentUpdatedAt, now, staleMinutes) {
  if (!currentRun) {
    return "unavailable";
  }
  if (currentRun.status !== "running") {
    return "idle";
  }
  const updatedAtMs = parseTimestampMs(currentUpdatedAt);
  if (!Number.isFinite(updatedAtMs) || !Number.isFinite(now.getTime())) {
    return "unknown";
  }
  return now.getTime() - updatedAtMs >= staleMinutes * 60 * 1000 ? "stale" : "fresh";
}

function buildStepDurationSummary(steps) {
  const entries = Object.entries(steps ?? {});
  let totalDurationSec = 0;
  let totalStepsWithDuration = 0;
  let longestStep = null;

  for (const [name, step] of entries) {
    const durationSec = Number(step?.duration_sec);
    if (!Number.isFinite(durationSec) || durationSec < 0) {
      continue;
    }
    totalDurationSec += durationSec;
    totalStepsWithDuration += 1;
    if (!longestStep || durationSec > longestStep.duration_sec) {
      longestStep = { name, duration_sec: durationSec };
    }
  }

  return {
    total_duration_sec: totalDurationSec,
    total_steps_with_duration: totalStepsWithDuration,
    longest_step: longestStep,
  };
}

/**
 * Build read-only operations console summary from existing run data only.
 * Missing operational sources remain explicitly unavailable rather than inferred.
 * @param {ReturnType<typeof summarizeRun> | null} currentRun
 * @param {Array<ReturnType<typeof summarizeRun>>} recentRuns
 * @param {{ now?: string | Date, currentUpdatedAt?: string | number | Date | null, staleMinutes?: number }} [options]
 * @returns {{
 *   data_availability: typeof OPERATIONS_CONSOLE_V1_DATA_AVAILABILITY,
 *   current_run: ReturnType<typeof summarizeRun> | null,
 *   recent_runs: Array<ReturnType<typeof summarizeRun>>,
 *   action_required_count: number,
 *   approval_required_count: number,
 *   recovery_required_count: number,
 *   held_count: number,
 *   failed_count: number,
 *   stale_run_count: number,
 *   idle_run_count: number,
 *   approval_queue: Array<ReturnType<typeof summarizeRun>>,
 *   recovery_queue: Array<ReturnType<typeof summarizeRun>>,
 *   duration_summary: {
 *     total_runs_with_duration: number,
 *     total_duration_sec: number,
 *     longest_step: { run_id: string, name: string, duration_sec: number } | null,
 *   },
 *   session_health: { status: string, reason: string },
 *   freshness_status: string,
 *   reason_buckets: Record<string, number>,
 *   reason_breakdown: Array<{ key: string, label: string, count: number }>,
 *   next_action: { action: string, reason: string },
 * }}
 */
export function buildOperationsConsoleSummary(currentRun, recentRuns, options = {}) {
  const runs = Array.isArray(recentRuns) ? recentRuns : [];
  const now = options?.now ? new Date(options.now) : new Date();
  const staleMinutes = Number.isFinite(options?.staleMinutes) ? Number(options.staleMinutes) : 10;
  const freshnessStatus = resolveFreshnessStatus(currentRun, options?.currentUpdatedAt ?? null, now, staleMinutes);
  const operationalRuns = [];
  const seenRunIds = new Set();
  const appendRun = (run) => {
    if (!run) {
      return;
    }
    const runId = typeof run?.run_id === "string" ? run.run_id : null;
    if (runId && seenRunIds.has(runId)) {
      return;
    }
    if (runId) {
      seenRunIds.add(runId);
    }
    operationalRuns.push(run);
  };

  appendRun(currentRun);
  for (const run of runs) {
    appendRun(run);
  }

  const heldRuns = operationalRuns.filter((run) => run?.status === "held");
  const failedRuns = operationalRuns.filter(
    (run) => run?.status === "failed" || run?.status === "retry_exhausted",
  );
  const actionRequiredRuns = operationalRuns.filter(
    (run) =>
      run?.status === "held" ||
      run?.status === "failed" ||
      run?.status === "retry_exhausted" ||
      run?.approval_required === true,
  );
  const reasonBuckets = {};

  for (const run of actionRequiredRuns) {
    if (!run?.failed_step?.reason) {
      continue;
    }
    const reason = classifyOperationalReason(run?.failed_step?.reason);
    reasonBuckets[reason.key] = (reasonBuckets[reason.key] ?? 0) + 1;
  }

  const reasonBreakdown = Object.entries(reasonBuckets)
    .map(([key, count]) => ({
      key,
      label: classifyOperationalReason(key).label,
      count,
    }))
    .sort((a, b) => a.key.localeCompare(b.key));
  const approvalQueue = operationalRuns.filter((run) => run?.status === "held" || run?.approval_required === true);
  const recoveryQueue = operationalRuns.filter((run) => {
    if (run?.status === "failed" || run?.status === "retry_exhausted") {
      return true;
    }
    if (normalizeReason(run?.failed_step?.reason) === "stale_running") {
      return true;
    }
    return run?.run_id === currentRun?.run_id && freshnessStatus === "stale";
  });
  const staleRunCount = recoveryQueue.filter((run) => normalizeReason(run?.failed_step?.reason) === "stale_running").length
    || (freshnessStatus === "stale" ? 1 : 0);
  const approvalRequiredCount = approvalQueue.length;
  const recoveryRequiredCount = recoveryQueue.length;
  const idleRunCount = currentRun && freshnessStatus === "idle" ? 1 : 0;
  const runsWithDuration = operationalRuns.filter((run) => run?.step_duration_summary?.total_steps_with_duration > 0);
  let longestDurationStep = null;
  for (const run of runsWithDuration) {
    const longestStep = run?.step_duration_summary?.longest_step;
    if (!longestStep) {
      continue;
    }
    if (!longestDurationStep || longestStep.duration_sec > longestDurationStep.duration_sec) {
      longestDurationStep = {
        run_id: run.run_id,
        name: longestStep.name,
        duration_sec: longestStep.duration_sec,
      };
    }
  }
  const durationSummary = {
    total_runs_with_duration: runsWithDuration.length,
    total_duration_sec: runsWithDuration.reduce(
      (sum, run) => sum + Number(run?.step_duration_summary?.total_duration_sec ?? 0),
      0,
    ),
    longest_step: longestDurationStep,
  };
  let sessionHealth = { status: "healthy", reason: "running_or_idle" };
  if (!currentRun) {
    sessionHealth = { status: "unknown", reason: "no_current_run" };
  } else if (currentRun.approval_required) {
    sessionHealth = { status: "attention", reason: "approval_required" };
  } else if (freshnessStatus === "stale") {
    sessionHealth = { status: "attention", reason: "stale_current_run" };
  } else if (freshnessStatus === "unknown" || freshnessStatus === "unavailable") {
    sessionHealth = { status: "unknown", reason: freshnessStatus };
  } else if (actionRequiredRuns.length > 0) {
    sessionHealth = { status: "attention", reason: "action_required_runs" };
  }

  let nextAction = { action: "none", reason: "no_action_required" };
  if (currentRun?.approval_required) {
    nextAction = { action: "review_approval_required_run", reason: "approval_required" };
  } else if (heldRuns.length > 0) {
    nextAction = { action: "review_held_run", reason: normalizeReason(heldRuns[0]?.failed_step?.reason) };
  } else if (failedRuns.length > 0) {
    nextAction = { action: "inspect_failed_run", reason: normalizeReason(failedRuns[0]?.failed_step?.reason) };
  } else if (freshnessStatus === "stale") {
    nextAction = { action: "check_session_freshness", reason: "stale_current_run" };
  }

  return {
    data_availability: OPERATIONS_CONSOLE_V1_DATA_AVAILABILITY,
    current_run: currentRun,
    recent_runs: runs,
    action_required_count: actionRequiredRuns.length,
    approval_required_count: approvalRequiredCount,
    recovery_required_count: recoveryRequiredCount,
    held_count: heldRuns.length,
    failed_count: failedRuns.length,
    stale_run_count: staleRunCount,
    idle_run_count: idleRunCount,
    approval_queue: approvalQueue,
    recovery_queue: recoveryQueue,
    duration_summary: durationSummary,
    session_health: sessionHealth,
    freshness_status: freshnessStatus,
    reason_buckets: reasonBuckets,
    reason_breakdown: reasonBreakdown,
    next_action: nextAction,
  };
}

/**
 * @param {string | null | undefined} startedAt
 * @param {number | null | undefined} sinceDays
 * @param {Date} now
 * @param {string | null | undefined} summaryStatus
 * @returns {boolean}
 */
function isWithinRecentWindow(startedAt, sinceDays, now, summaryStatus = null) {
  if (!Number.isFinite(sinceDays) || sinceDays <= 0) {
    return true;
  }
  if (summaryStatus === "invalid_started_at") {
    return true;
  }
  const normalized = normalizeTimestamp(startedAt);
  if (!normalized || !Number.isFinite(Date.parse(normalized))) {
    return false;
  }
  const startedAtMs = Date.parse(normalized);
  const thresholdMs = now.getTime() - sinceDays * 24 * 60 * 60 * 1000;
  return startedAtMs >= thresholdMs;
}

/**
 * Summarize raw run payload for dashboard cards.
 * Status policy includes completed/failed plus cancelled/timeout/held.
 * Invalid started_at is treated as explicit data-quality failure.
 * @param {string} runId
 * @param {any} payload
 * @param {{ now?: string | Date, staleRunningMinutes?: number, lastActivityAt?: string | number | Date | null }} [options]
 * @returns {{
 *   run_id: string,
 *   status: string,
 *   mode: string | null,
 *   branch: string | null,
 *   executor: string | null,
 *   started_at: string | null,
 *   finished_at: string | null,
 *   last_activity_at: string | null,
 *   last_heartbeat_at: string | null,
 *   approval_required: boolean,
 *   manual_gate_reason: string | null,
 *   retry_count: number,
 *   resume_count: number,
 *   step_duration_summary: {
 *     total_duration_sec: number,
 *     total_steps_with_duration: number,
 *     longest_step: { name: string, duration_sec: number } | null,
 *   },
 *   last_completed_step: string | null,
 *   failed_step: Record<string, any> | null,
 * }}
 */
export function summarizeRun(runId, payload, options = {}) {
  const steps = payload?.steps && typeof payload.steps === "object" ? payload.steps : {};
  const startedAt = payload?.started_at ?? null;
  const status = normalizeStatus(payload?.status);
  const now = options?.now ? new Date(options.now) : new Date();
  const staleRunningMinutes = resolveStaleRunningMinutes(options?.staleRunningMinutes);
  const explicitActivityAt = options?.lastActivityAt ?? null;
  const explicitActivityAtMs = parseTimestampMs(explicitActivityAt);
  const explicitActivityAtIso = Number.isFinite(explicitActivityAtMs) ? new Date(explicitActivityAtMs).toISOString() : null;
  const stepDurationSummary = buildStepDurationSummary(steps);

  if (!isValidStartedAt(startedAt)) {
    return {
      run_id: runId,
      status: "invalid_started_at",
      mode: payload?.mode ?? null,
      branch: payload?.branch ?? null,
      executor: payload?.executor ?? null,
      started_at: startedAt,
      finished_at: payload?.finished_at ?? null,
      last_heartbeat_at: normalizeTimestamp(payload?.last_heartbeat_at) ?? null,
      approval_required: payload?.approval_required === true,
      manual_gate_reason: typeof payload?.manual_gate_reason === "string" ? payload.manual_gate_reason : null,
      retry_count: Number.isFinite(Number(payload?.retry_count)) ? Number(payload.retry_count) : 0,
      resume_count: Number.isFinite(Number(payload?.resume_count)) ? Number(payload.resume_count) : 0,
      step_duration_summary: stepDurationSummary,
      last_activity_at: explicitActivityAtIso ?? normalizeTimestamp(payload?.updated_at) ?? normalizeTimestamp(payload?.last_event_at) ?? normalizeTimestamp(payload?.last_output_at) ?? null,
      last_completed_step: payload?.last_completed_step ?? null,
      failed_step: {
        name: "data_quality",
        status: "invalid",
        verdict: "block",
        reason: "invalid_started_at",
        error_message: `invalid started_at: ${String(startedAt)}`,
        output_preview: null,
        last_output: null,
        critique_issues: ["invalid_started_at"],
      },
    };
  }

  let failedStep = null;
  let summaryStatus = status;

  if (
    status === "running" &&
    !payload?.finished_at &&
    startedAt &&
    Number.isFinite(Date.parse(normalizeTimestamp(startedAt))) &&
    Number.isFinite(now.getTime())
  ) {
    const startedAtMs = Date.parse(normalizeTimestamp(startedAt));
    const activityCandidates = [
      payload?.updated_at,
      payload?.last_event_at,
      payload?.last_output_at,
      explicitActivityAt,
      startedAt,
    ]
      .map((value) => parseTimestampMs(value))
      .filter((value) => Number.isFinite(value));
    const lastActivityMs = activityCandidates.length > 0 ? Math.max(...activityCandidates) : startedAtMs;
    const staleThresholdMs = staleRunningMinutes * 60 * 1000;
    if (now.getTime() - lastActivityMs >= staleThresholdMs) {
      summaryStatus = "held";
      failedStep = {
        name: "stale_recovery",
        status: "auto_hold",
        verdict: null,
        reason: "stale_running",
        error_message: null,
        output_preview: null,
        last_output: null,
        critique_issues: null,
      };
    }
  }

  for (const [name, step] of Object.entries(steps)) {
    const stepStatus = step?.status;
    if (stepStatus && stepStatus !== "completed") {
      failedStep = {
        name,
        status: stepStatus,
        verdict: step?.verdict ?? null,
        reason: step?.reason ?? null,
        error_message: step?.error_message ?? null,
        output_preview: step?.output_preview ?? null,
        last_output: step?.last_output ?? null,
        critique_issues: step?.critique_issues ?? null,
      };
      break;
    }
  }

  return {
    run_id: runId,
    status: summaryStatus,
    mode: payload?.mode ?? null,
    branch: payload?.branch ?? null,
    executor: payload?.executor ?? null,
    started_at: startedAt,
    finished_at: payload?.finished_at ?? null,
    last_heartbeat_at: normalizeTimestamp(payload?.last_heartbeat_at) ?? null,
    approval_required: payload?.approval_required === true,
    manual_gate_reason: typeof payload?.manual_gate_reason === "string" ? payload.manual_gate_reason : null,
    retry_count: Number.isFinite(Number(payload?.retry_count)) ? Number(payload.retry_count) : 0,
    resume_count: Number.isFinite(Number(payload?.resume_count)) ? Number(payload.resume_count) : 0,
    step_duration_summary: stepDurationSummary,
    last_activity_at:
      explicitActivityAtIso ??
      normalizeTimestamp(payload?.updated_at) ??
      normalizeTimestamp(payload?.last_event_at) ??
      normalizeTimestamp(payload?.last_output_at) ??
      normalizeTimestamp(startedAt) ??
      null,
    last_completed_step: payload?.last_completed_step ?? null,
    failed_step: failedStep,
  };
}

/**
 * Read current in-progress pipeline summary.
 * @param {string} root
 * @returns {Promise<ReturnType<typeof summarizeRun> | null>}
 */
export async function readCurrentRun(root) {
  const filePath = resultPath(root);
  try {
    const payload = await readJson(filePath);
    const fileStat = await fs.stat(filePath);
    return summarizeRun("current", payload, { lastActivityAt: fileStat.mtimeMs });
  } catch (error) {
    if (error?.code === "ENOENT") {
      return null;
    }
    return {
      run_id: "current",
      status: "invalid",
      mode: null,
      branch: null,
      executor: null,
      started_at: null,
      finished_at: null,
      last_completed_step: null,
      failed_step: {
        name: "parse",
        status: "invalid",
        verdict: null,
        reason: null,
        error_message: error?.message ?? "invalid json",
        output_preview: null,
        last_output: null,
        critique_issues: null,
      },
    };
  }
}

/**
 * List recent runs with optional recency filtering.
 * @param {string} root
 * @param {number} [maxRuns=50]
 * Operational defaults:
 * - `maxRuns`: maximum number of summaries returned (default 50)
 * - `scanLimit`: maximum candidate directories scanned before filtering (default `max(maxRuns*10, 200)`)
 * - when `sinceDays` is set, only the most-recent `scanLimit` run-id candidates are stat'ed first
 * @param {{ sinceDays?: number, now?: string | Date, scanLimit?: number }} [options]
 * @returns {Promise<Array<ReturnType<typeof summarizeRun>>>}
 */
export async function listRecentRuns(root, maxRuns = 50, options = {}) {
  const dir = runsDir(root);
  let entries;
  try {
    entries = await fs.readdir(dir, { withFileTypes: true });
  } catch (error) {
    if (error?.code === "ENOENT") {
      return [];
    }
    throw error;
  }

  const sortedCandidates = entries
    .filter((entry) => entry.isDirectory())
    .map((entry) => entry.name);

  const validResults = [];
  const invalidResults = [];
  const seenRunIds = new Set();
  const isInvalidSummary = (summary) =>
    summary?.status === "invalid" || summary?.status === "invalid_started_at";
  const sinceDays = Number.isFinite(options?.sinceDays) ? Number(options.sinceDays) : null;
  const countValidResults = () => validResults.length;
  const hasEnoughResults = () => countValidResults() >= maxRuns;
  const now = options?.now ? new Date(options.now) : new Date();
  if (Number.isNaN(now.getTime())) {
    throw new TypeError("invalid now option: expected Date or date-like value");
  }
  const defaultScanLimit = Math.max(maxRuns * 10, 200);
  const scanLimit = Number.isFinite(options?.scanLimit) ? Number(options.scanLimit) : defaultScanLimit;
  let scanned = 0;
  let candidates;
  let expandedScan = false;
  const pushInvalidSummary = (runId, error) => {
    if (seenRunIds.has(runId)) {
      return;
    }
    seenRunIds.add(runId);
    invalidResults.push({
      run_id: runId,
      status: "invalid",
      mode: null,
      branch: null,
      executor: null,
      started_at: null,
      finished_at: null,
      last_completed_step: null,
      failed_step: {
        name: "parse",
        status: "invalid",
        verdict: null,
        reason: null,
        error_message: error?.message ?? "invalid json",
        output_preview: null,
        last_output: null,
        critique_issues: null,
      },
    });
  };

  const processCandidates = async (candidateList, candidateLimit) => {
    for (const candidate of candidateList) {
      if (scanned >= candidateLimit) {
        break;
      }
      scanned += 1;
      const runId = candidate.runId;
      const filePath = path.join(dir, runId, "result.json");
      try {
        const payload = await readJson(filePath);
        const summary = summarizeRun(runId, payload, {
          now,
          lastActivityAt: candidate.mtimeMs,
        });
        if (
          isWithinRecentWindow(summary.started_at, sinceDays, now, summary.status) &&
          !seenRunIds.has(runId)
        ) {
          seenRunIds.add(runId);
          if (isInvalidSummary(summary)) {
            invalidResults.push(summary);
          } else {
            validResults.push(summary);
          }
          if (hasEnoughResults()) {
            break;
          }
        }
      } catch (error) {
        if (error?.code === "ENOENT") {
          continue;
        }
        pushInvalidSummary(runId, error);
        if (hasEnoughResults()) {
          break;
        }
      }
    }
  };
  if (sinceDays == null) {
    candidates = sortedCandidates
      .slice()
      .sort((a, b) => b.localeCompare(a))
      .map((runId) => ({ runId, mtimeMs: 0 }));
  } else {
    candidates = await buildRecentCandidates(dir, sortedCandidates, scanLimit, false);
  }

  const initialCandidateLimit = sinceDays == null ? candidates.length : scanLimit;
  await processCandidates(candidates, initialCandidateLimit);
  if (!expandedScan && sortedCandidates.length > scanLimit && countValidResults() < maxRuns) {
    // Prefilter는 비용 상 run-id lexicographic 상위 scanLimit만 stat/read 한다.
    // 이 때문에 최근 run이 누락될 수 있으므로 결과가 부족하면 확장 스캔으로 채운다.
    candidates = await buildRecentCandidates(dir, sortedCandidates, scanLimit, true);
    scanned = 0;
    expandedScan = true;
    await processCandidates(candidates, candidates.length);
  }
  if (validResults.length >= maxRuns) {
    return validResults.slice(0, maxRuns);
  }
  return validResults.concat(invalidResults).slice(0, maxRuns);
}

/**
 * Read full detail for a specific run id.
 * @param {string} root
 * @param {string} runId
 * @returns {Promise<Record<string, any> | null>}
 */
export async function readRunDetail(root, runId) {
  const filePath = path.join(runsDir(root), runId, "result.json");
  try {
    const payload = await readJson(filePath);
    return {
      run_id: runId,
      summary: summarizeRun(runId, payload),
      raw: payload,
    };
  } catch (error) {
    if (error?.code === "ENOENT") {
      return null;
    }
    return {
      run_id: runId,
      status: "invalid",
      summary: {
        run_id: runId,
        status: "invalid",
      },
      raw: null,
      error: error?.message ?? "invalid json",
    };
  }
}
