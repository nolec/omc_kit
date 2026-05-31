import { NextResponse } from "next/server";
import { resolveDashboardRoot } from "../../../lib/omc-root.mjs";
import { listRecentRuns } from "../../../lib/omc-runs.mjs";

export async function GET(request) {
  const root = resolveDashboardRoot();
  const { searchParams } = new URL(request.url);
  const max = Number(searchParams.get("max") || "50");
  const safeMax = Number.isFinite(max) && max > 0 ? Math.min(max, 100) : 50;
  const runs = await listRecentRuns(root, safeMax);
  return NextResponse.json({ root, count: runs.length, runs });
}
