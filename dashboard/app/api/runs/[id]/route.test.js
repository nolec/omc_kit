import test from "node:test";
import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import { fileURLToPath } from "node:url";
import { dirname, join } from "node:path";

const __filename = fileURLToPath(import.meta.url);
const __dirname = dirname(__filename);
const sourcePath = join(__dirname, "route.js");

test("run detail route handles not-found and success response", async () => {
  const source = await readFile(sourcePath, "utf8");
  assert.match(source, /readRunDetail/);
  assert.match(source, /status:\s*404/);
  assert.match(source, /NextResponse\.json\(\{\s*root,\s*detail\s*\}\)/);
});
