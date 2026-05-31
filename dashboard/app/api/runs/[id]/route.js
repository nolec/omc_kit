import { NextResponse } from "next/server";
import { resolveDashboardRoot } from "../../../../lib/omc-root.mjs";
import { readRunDetail } from "../../../../lib/omc-runs.mjs";

export async function GET(_request, { params }) {
  const root = resolveDashboardRoot();
  const detail = await readRunDetail(root, params.id);
  if (!detail) {
    return NextResponse.json({ error: "run not found" }, { status: 404 });
  }
  return NextResponse.json({ root, detail });
}
