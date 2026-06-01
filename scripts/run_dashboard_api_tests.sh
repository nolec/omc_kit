#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

# Keep bracket path quoted so zsh/glob does not drop the test file.
node --test \
  dashboard/app/api/_shared/response.spec.js \
  dashboard/app/api/current/route.test.js \
  'dashboard/app/api/runs/[id]/route.test.js' \
  dashboard/app/api/runs/route.test.js \
  dashboard/lib/omc-runs.test.mjs
