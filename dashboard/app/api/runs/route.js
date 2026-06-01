import { resolveDashboardRoot } from "../../../lib/omc-root.mjs";
import { listRecentRuns } from "../../../lib/omc-runs.mjs";
import {
  jsonError,
  jsonWithSchema,
  shouldUseLegacyCompatibility,
  validateAcceptVersion,
} from "../_shared/response.js";

/**
 * Normalize requested max runs.
 * Default: 50, allowed range: 1..100.
 * @param {string | null} rawMax
 * @returns {number}
 */
export function resolveMaxRuns(rawMax) {
  const max = Number(rawMax || "50");
  return Number.isFinite(max) && max > 0 ? Math.min(Math.trunc(max), 100) : 50;
}

/**
 * Normalize optional recency window in days.
 * Default: no recency filter (null), allowed range: 1..365.
 * @param {string | null} rawSinceDays
 * @returns {number | null}
 */
export function resolveSinceDays(rawSinceDays) {
  if (rawSinceDays == null || rawSinceDays === "") {
    return null;
  }
  const sinceDays = Number(rawSinceDays);
  return Number.isFinite(sinceDays) && sinceDays > 0
    ? Math.min(Math.trunc(sinceDays), 365)
    : null;
}

export async function GET(request) {
  try {
    const reject = validateAcceptVersion(request);
    if (reject) {
      return reject;
    }
    const root = resolveDashboardRoot();
    const { searchParams } = new URL(request.url);
    const safeMax = resolveMaxRuns(searchParams.get("max"));
    const sinceDays = resolveSinceDays(searchParams.get("since_days"));
    const runs = await listRecentRuns(root, safeMax, { sinceDays });
    const payload = { count: runs.length, runs };
    if (shouldUseLegacyCompatibility(request)) {
      payload.root = root;
    }
    return jsonWithSchema(payload);
  } catch (error) {
    console.error("[dashboard/api/runs] failed_to_list_runs", error);
    return jsonError("failed_to_list_runs", error, 500);
  }
}
