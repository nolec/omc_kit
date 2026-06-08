#!/usr/bin/env bash
# 공용 OMC 세션 시작 훅 — Cursor / Claude Code / Gemini CLI 공통
# 사용: .agent-hooks/omc-session-start.sh [EXECUTOR_NAME]
#   EXECUTOR_NAME: cursor | claude | gemini | codex (기본값: unknown)
set -u

EXECUTOR="${1:-unknown}"

# stdin(JSON) 소비 — 일부 환경에서 stdin 미소비 시 훅이 대기/실패
cat >/dev/null 2>&1 || true

_resolve_omc_script() {
  if [[ -f "scripts/omc.py" ]]; then
    echo "scripts/omc.py"
    return 0
  fi
  if [[ -f "omc_kit/scripts/omc.py" ]]; then
    echo "omc_kit/scripts/omc.py"
    return 0
  fi
  return 1
}

OMC_SCRIPT="$(_resolve_omc_script || true)"
if [[ -z "${OMC_SCRIPT}" ]]; then
  exit 0
fi

PYTHON_BIN="python3"
if ! command -v "${PYTHON_BIN}" >/dev/null 2>&1; then
  PYTHON_BIN="python"
fi
if ! command -v "${PYTHON_BIN}" >/dev/null 2>&1; then
  exit 0
fi

ROOT="$(pwd)"

mkdir -p .omc 2>/dev/null || true
printf '%s [%s] sessionStart -> omc state init + hook session_start (cwd=%s)\n' \
  "$(date '+%Y-%m-%dT%H:%M:%S%z')" "${EXECUTOR}" "${ROOT}" \
  >>.omc/agent-hook.log 2>/dev/null || true

"${PYTHON_BIN}" "${OMC_SCRIPT}" state init --target "${ROOT}" >/dev/null 2>&1 || true
OMC_EXECUTOR="${EXECUTOR}" "${PYTHON_BIN}" "${OMC_SCRIPT}" hook session_start --target "${ROOT}" >/dev/null 2>&1 || true

# SessionStart stdout → Claude/Codex: 평문, Gemini: JSON (Gemini는 순수 JSON 필수)
SUMMARY_FILE="${ROOT}/.omc/summary.md"
if [[ -f "${SUMMARY_FILE}" ]]; then
  if [[ "${EXECUTOR}" == "gemini" ]]; then
    # Gemini는 stdout이 반드시 순수 JSON이어야 함
    LESSONS_FILE="${ROOT}/.cursor/rules/omc-lessons-inject.mdc"
    "${PYTHON_BIN}" - "${SUMMARY_FILE}" "${LESSONS_FILE}" <<'PYEOF'
import json, sys
from pathlib import Path
summary = open(sys.argv[1], encoding="utf-8").read()
lessons_path = Path(sys.argv[2]) if len(sys.argv) > 2 else None
lessons = ("

<!-- OMC 자동 주입 교훈 -->
" + lessons_path.read_text(encoding="utf-8")) if lessons_path and lessons_path.exists() else ""
print(json.dumps({"additionalContext": summary + lessons}))
PYEOF
  else
    # Claude Code / Codex: 평문 stdout → 컨텍스트로 자동 주입
    echo "<!-- OMC Session Context -->"
    cat "${SUMMARY_FILE}"
    # BM25 교훈 주입 (.cursor/rules/omc-lessons-inject.mdc 존재 시)
    LESSONS_FILE="${ROOT}/.cursor/rules/omc-lessons-inject.mdc"
    if [ -f "${LESSONS_FILE}" ]; then
      echo ""
      echo "<!-- OMC 자동 주입 교훈 -->"
      cat "${LESSONS_FILE}"
    fi
  fi
fi

exit 0
