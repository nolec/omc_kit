import test from "node:test";
import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import { fileURLToPath } from "node:url";
import { dirname, join } from "node:path";

const __filename = fileURLToPath(import.meta.url);
const __dirname = dirname(__filename);
const sourcePath = join(__dirname, "route.js");

test("runs route applies max cap and returns runs list", async () => {
  const source = await readFile(sourcePath, "utf8");
  assert.match(source, /Math\.min\(max,\s*100\)/);
  assert.match(source, /listRecentRuns/);
  assert.match(source, /NextResponse\.json\(\{\s*root,\s*count:\s*runs\.length,\s*runs\s*\}\)/);
});
