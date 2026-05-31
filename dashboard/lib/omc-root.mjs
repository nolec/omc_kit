import path from "node:path";

export function resolveDashboardRoot() {
  const configured = process.env.OMC_DASHBOARD_ROOT;
  if (configured && configured.trim()) {
    return path.resolve(configured.trim());
  }
  return path.resolve(process.cwd(), "..");
}
