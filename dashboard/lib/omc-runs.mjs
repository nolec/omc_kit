import fs from "node:fs/promises";
import path from "node:path";

const KNOWN_RUN_STATUSES = new Set([
  "pending",
  "running",
  "completed",
  "failed",
  "cancelled",
  "timeout",
  "held",
  "invalid",
]);

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
  return Number.isFinite(Date.parse(startedAt));
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
  if (!startedAt || !Number.isFinite(Date.parse(startedAt))) {
    return false;
  }
  const startedAtMs = Date.parse(startedAt);
  const thresholdMs = now.getTime() - sinceDays * 24 * 60 * 60 * 1000;
  return startedAtMs >= thresholdMs;
}

/**
 * Summarize raw run payload for dashboard cards.
 * Status policy includes completed/failed plus cancelled/timeout/held.
 * Invalid started_at is treated as explicit data-quality failure.
 * @param {string} runId
 * @param {any} payload
 * @returns {{
 *   run_id: string,
 *   status: string,
 *   mode: string | null,
 *   branch: string | null,
 *   executor: string | null,
 *   started_at: string | null,
 *   finished_at: string | null,
 *   last_completed_step: string | null,
 *   failed_step: Record<string, any> | null,
 * }}
 */
export function summarizeRun(runId, payload) {
  const steps = payload?.steps && typeof payload.steps === "object" ? payload.steps : {};
  const startedAt = payload?.started_at ?? null;
  const status = normalizeStatus(payload?.status);

  if (!isValidStartedAt(startedAt)) {
    return {
      run_id: runId,
      status: "invalid_started_at",
      mode: payload?.mode ?? null,
      branch: payload?.branch ?? null,
      executor: payload?.executor ?? null,
      started_at: startedAt,
      finished_at: payload?.finished_at ?? null,
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
    status,
    mode: payload?.mode ?? null,
    branch: payload?.branch ?? null,
    executor: payload?.executor ?? null,
    started_at: startedAt,
    finished_at: payload?.finished_at ?? null,
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
    return summarizeRun("current", payload);
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
        const summary = summarizeRun(runId, payload);
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
