import test from "node:test";
import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import { join, dirname } from "node:path";
import { fileURLToPath } from "node:url";

const __filename = fileURLToPath(import.meta.url);
const __dirname = dirname(__filename);
const sourcePath = join(__dirname, "layout.js");

test("layout defines metadata and html shell", async () => {
  const source = await readFile(sourcePath, "utf8");
  assert.match(source, /metadata/);
  assert.match(source, /OMC Autopilot Dashboard/);
  assert.match(source, /<html lang="en">/);
});
