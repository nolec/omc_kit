import { resolveDashboardRoot } from "../../../lib/omc-root.mjs";
import { readCurrentRun } from "../../../lib/omc-runs.mjs";
import {
  jsonError,
  jsonWithSchema,
  shouldUseLegacyCompatibility,
  validateAcceptVersion,
} from "../_shared/response.js";

export async function GET(request) {
  try {
    const reject = validateAcceptVersion(request);
    if (reject) {
      return reject;
    }
    const root = resolveDashboardRoot();
    const current = await readCurrentRun(root);
    const payload = { current };
    if (shouldUseLegacyCompatibility(request)) {
      payload.root = root;
    }
    return jsonWithSchema(payload);
  } catch (error) {
    console.error("[dashboard/api/current] failed_to_read_current_run", error);
    return jsonError("failed_to_read_current_run", error, 500);
  }
}
