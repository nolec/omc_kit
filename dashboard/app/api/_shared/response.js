export const SCHEMA_VERSION = "2026-06-01";
export const BREAKING_CHANGES = { root_removed: true };
export const API_VERSION_HEADER = { "X-OMC-API-Version": SCHEMA_VERSION };
export const LEGACY_SCHEMA_VERSION = "2026-05-01";
export const DEPRECATION_META = {
  legacy_root_deprecated: true,
  legacy_root_sunset: "2026-07-01",
};

/**
 * Build standardized API payload with schema contract.
 * @param {Record<string, any>} payload
 * @param {number} [status=200]
 * @returns {Response}
 */
export function jsonWithSchema(payload, status = 200) {
  return Response.json(
    {
      ...payload,
      schema_version: SCHEMA_VERSION,
      breaking_changes: BREAKING_CHANGES,
      deprecation: DEPRECATION_META,
    },
    { status, headers: API_VERSION_HEADER },
  );
}

/**
 * Compare ISO-like YYYY-MM-DD schema versions.
 * @param {string} left
 * @param {string} right
 * @returns {number}
 */
export function compareSchemaVersion(left, right) {
  const leftParts = parseSchemaVersion(left);
  const rightParts = parseSchemaVersion(right);
  if (!leftParts || !rightParts) {
    throw new Error("invalid schema version");
  }
  for (let i = 0; i < 3; i += 1) {
    if (leftParts[i] !== rightParts[i]) {
      return leftParts[i] < rightParts[i] ? -1 : 1;
    }
  }
  return 0;
}

/**
 * Parse strict YYYY-MM-DD schema version.
 * @param {string} value
 * @returns {[number, number, number] | null}
 */
export function parseSchemaVersion(value) {
  if (typeof value !== "string") {
    return null;
  }
  const trimmed = value.trim();
  const match = /^(\d{4})-(\d{2})-(\d{2})$/.exec(trimmed);
  if (!match) {
    return null;
  }
  const year = Number(match[1]);
  const month = Number(match[2]);
  const day = Number(match[3]);
  if (!Number.isFinite(year) || !Number.isFinite(month) || !Number.isFinite(day)) {
    return null;
  }
  if (month < 1 || month > 12 || day < 1 || day > 31) {
    return null;
  }
  const utc = new Date(Date.UTC(year, month - 1, day));
  if (
    utc.getUTCFullYear() !== year ||
    utc.getUTCMonth() + 1 !== month ||
    utc.getUTCDate() !== day
  ) {
    return null;
  }
  return [year, month, day];
}

/**
 * Validate Accept-Version header for forward-compat negotiation.
 * - Invalid format => 400
 * - Future version => 406
 * @param {Request | undefined} request
 * @returns {Response | null}
 */
export function validateAcceptVersion(request) {
  if (!request) {
    return null;
  }
  const acceptVersion = request.headers.get("Accept-Version");
  if (!acceptVersion) {
    return null;
  }
  const parsed = parseSchemaVersion(acceptVersion);
  if (!parsed) {
    return jsonError("invalid_accept_version", `invalid Accept-Version: ${acceptVersion}`, 400);
  }
  try {
    if (compareSchemaVersion(acceptVersion.trim(), SCHEMA_VERSION) > 0) {
      return jsonError(
        "not_acceptable",
        `accept_version not supported: ${acceptVersion.trim()}`,
        406,
      );
    }
  } catch {
    return jsonError("invalid_accept_version", `invalid Accept-Version: ${acceptVersion}`, 400);
  }
  return null;
}

/**
 * Resolve whether legacy response compatibility should be enabled.
 * Default policy: compatibility disabled unless explicitly requested.
 * Supported toggles:
 * - `?compat=legacy` or `?include_root=1`
 * - `Accept-Version` header older than current schema.
 * @param {Request | undefined} request
 * @returns {boolean}
 */
export function shouldUseLegacyCompatibility(request) {
  if (!request) {
    return false;
  }

  let url;
  try {
    url = new URL(request.url);
  } catch {
    return false;
  }
  const compat = url.searchParams.get("compat");
  const includeRoot = url.searchParams.get("include_root");
  if (compat === "legacy" || includeRoot === "1") {
    return true;
  }

  const acceptVersion = request.headers.get("Accept-Version");
  if (!acceptVersion) {
    return false;
  }

  try {
    return compareSchemaVersion(acceptVersion.trim(), SCHEMA_VERSION) < 0;
  } catch {
    return false;
  }
}

/**
 * Build standardized API error payload with schema contract.
 * @param {string} errorCode
 * @param {unknown} error
 * @param {number} [status=500]
 * @returns {Response}
 */
export function jsonError(errorCode, error, status = 500) {
  return jsonWithSchema(
    {
      error: errorCode,
      message: error instanceof Error ? error.message : String(error ?? "unknown error"),
    },
    status,
  );
}
