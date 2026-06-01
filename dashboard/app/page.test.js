import test from "node:test";
import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import { join, dirname } from "node:path";
import { fileURLToPath } from "node:url";

const __filename = fileURLToPath(import.meta.url);
const __dirname = dirname(__filename);
const sourcePath = join(__dirname, "page.js");

test("home page renders current status and latest run detail sections", async () => {
  const source = await readFile(sourcePath, "utf8");
  assert.match(source, /Current Status/);
  assert.match(source, /Recent Runs/);
  assert.match(source, /Latest Run Detail/);
  assert.match(source, /readCurrentRun/);
  assert.match(source, /listRecentRuns/);
  assert.doesNotMatch(source, /Root:/);
});
