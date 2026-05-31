import fs from "node:fs/promises";
import path from "node:path";

function resultPath(root) {
  return path.join(root, ".omc", "pipeline_run_result.json");
}

function runsDir(root) {
  return path.join(root, ".omc", "runs");
}

async function readJson(filePath) {
  const raw = await fs.readFile(filePath, "utf8");
  return JSON.parse(raw);
}

export function summarizeRun(runId, payload) {
  const steps = payload?.steps && typeof payload.steps === "object" ? payload.steps : {};
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
    status: payload?.status ?? "unknown",
    mode: payload?.mode ?? null,
    branch: payload?.branch ?? null,
    executor: payload?.executor ?? null,
    started_at: payload?.started_at ?? null,
    finished_at: payload?.finished_at ?? null,
    last_completed_step: payload?.last_completed_step ?? null,
    failed_step: failedStep,
  };
}

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

export async function listRecentRuns(root, maxRuns = 50) {
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

  const candidates = entries
    .filter((entry) => entry.isDirectory())
    .map((entry) => entry.name)
    .sort((a, b) => b.localeCompare(a))
    .slice(0, maxRuns);

  const results = [];
  for (const runId of candidates) {
    const filePath = path.join(dir, runId, "result.json");
    try {
      const payload = await readJson(filePath);
      results.push(summarizeRun(runId, payload));
    } catch (error) {
      if (error?.code === "ENOENT") {
        continue;
      }
      results.push({
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
    }
  }
  return results;
}

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
