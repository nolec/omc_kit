import { resolveDashboardRoot } from "../../../../lib/omc-root.mjs";
import { readRunDetail } from "../../../../lib/omc-runs.mjs";
import {
  jsonError,
  jsonWithSchema,
  shouldUseLegacyCompatibility,
  validateAcceptVersion,
} from "../../_shared/response.js";

export async function GET(_request, { params }) {
  try {
    const reject = validateAcceptVersion(_request);
    if (reject) {
      return reject;
    }
    const runId = typeof params?.id === "string" ? params.id.trim() : "";
    if (!runId) {
      return jsonError("invalid_run_id", "run_id is required", 400);
    }
    const root = resolveDashboardRoot();
    const detail = await readRunDetail(root, runId);
    if (!detail) {
      const notFoundPayload = { error: "run not found", message: `run_id not found: ${runId}` };
      if (shouldUseLegacyCompatibility(_request)) {
        notFoundPayload.root = root;
      }
      return jsonWithSchema(notFoundPayload, 404);
    }
    const payload = { detail };
    if (shouldUseLegacyCompatibility(_request)) {
      payload.root = root;
    }
    return jsonWithSchema(payload);
  } catch (error) {
    console.error("[dashboard/api/runs/:id] failed_to_read_run_detail", error);
    return jsonError("failed_to_read_run_detail", error, 500);
  }
}
