import { NextResponse } from "next/server";
import { resolveDashboardRoot } from "../../../lib/omc-root.mjs";
import { readCurrentRun } from "../../../lib/omc-runs.mjs";

export async function GET() {
  const root = resolveDashboardRoot();
  const current = await readCurrentRun(root);
  return NextResponse.json({ root, current });
}
