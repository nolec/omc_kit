#!/bin/sh
# omc-pipeline-check.sh — 공용 파이프라인 가드 (Claude Code PreToolUse)
#
# Claude Code: PreToolUse 훅에서 호출
#   exit 0  → 허용
#   exit 2  → 차단 (Claude Code 전용 차단 코드)
#
# stdin: 도구 호출 JSON
# {
#   "tool_name": "Write" | "Edit" | "MultiEdit" | "create_file",
#   "tool_input": { "file_path": "...", ... }
# }
# Write/create_file → 모든 경로 세션 검사
# Edit/MultiEdit    → 민감 경로(scripts/, .agent-hooks/ 등)만 세션 검사

PYTHON_BIN="python3"
if ! command -v "${PYTHON_BIN}" >/dev/null 2>&1; then
  PYTHON_BIN="python"
fi

GUARD="scripts/omc_pipeline_guard.py"
if [ ! -f "${GUARD}" ]; then
  exit 0
fi

INPUT_JSON="$(cat)"

# 도구 이름 추출
TOOL_NAME="$(
  printf '%s' "${INPUT_JSON}" | "${PYTHON_BIN}" -c '
import json, sys
try:
    data = json.load(sys.stdin)
except Exception:
    print("")
    raise SystemExit(0)
print(data.get("tool_name", data.get("tool", "")))
' 2>/dev/null
)"

# ── 민감 경로 판별 함수 ──────────────────────────────────────────────────
# OMC 시스템 파일(scripts/, .agent-hooks/ 등)은 Edit도 세션 검사 대상
_is_sensitive_path() {
  case "$1" in
    omc_kit/*|scripts/*|.omc/*|\
    .agent-hooks/*|.agent/*|.agents/*|\
    .cursor/hooks/*|.cursor/rules/*|.cursor/hooks.json|\
    .claude/*|.gemini/*|.codex/*|\
    AGENTS.md|CLAUDE.md|GEMINI.md)
      return 0 ;;
    *) return 1 ;;
  esac
}

# 도구 종류별 처리
case "${TOOL_NAME}" in
  Write|create_file)
    # 신규 파일 생성: 경로 무관 항상 세션 검사
    ;;
  Edit|MultiEdit)
    # 기존 파일 수정: 민감 경로일 때만 세션 검사
    EARLY_PATH="$(
      printf '%s' "${INPUT_JSON}" | "${PYTHON_BIN}" -c '
import json, sys
try:
    data = json.load(sys.stdin)
except Exception:
    print(""); raise SystemExit(0)
inp = data.get("tool_input") or data.get("params") or data.get("input") or {}
print(inp.get("file_path") or inp.get("target_file") or inp.get("path") or "")
' 2>/dev/null
    )"
    if ! _is_sensitive_path "${EARLY_PATH}"; then
      exit 0
    fi
    ;;
  *) exit 0 ;;
esac

# ── OMC 세션 동기화 검사 ──────────────────────────────────────────────────
# 버그 수정: python3 -c '...' 내부 작은따옴표(state confirm 등)가 sh 파서에게
# 코드 종료로 해석되는 문제 → 큰따옴표(-c "...") + 임시파일 방식으로 전환
_OMC_TMP="$(mktemp)"
"${PYTHON_BIN}" -c "
import json, sys
from pathlib import Path

policy_path = Path('.omc/policy.json')
latest_path = Path('.omc/state/latest.json')

if not policy_path.exists():
    sys.exit(0)

try:
    policy = json.loads(policy_path.read_text(encoding='utf-8'))
except Exception:
    sys.exit(0)

if not policy.get('enforce_confirm', False):
    sys.exit(0)

if not latest_path.exists():
    sys.exit(0)

try:
    latest = json.loads(latest_path.read_text(encoding='utf-8'))
except Exception:
    sys.exit(0)

status = (latest.get('latest_confirmation') or {}).get('status', '')
request = latest.get('latest_confirmed_request', '(알 수 없음)')

if status == 'pending':
    sys.exit(0)

if status == 'confirmed':
    print(f'[OMC BLOCK] 활성 세션 없음 — 마지막 작업: {request}')
    print()
    print('state confirm 은 작업 완료 처리입니다. 실행하면 또 막힙니다.')
    print()
    print('▶ 올바른 절차: 새 작업을 선언해서 pending 세션을 만드세요.')
    print('    python3 scripts/omc.py 새작업내용')
    print('  또는 Claude Code에서: /plan [작업] / /task [설명]')
    sys.exit(1)
" > "${_OMC_TMP}" 2>/dev/null
OMC_SYNC_EXIT=$?

if [ "${OMC_SYNC_EXIT}" -ne 0 ]; then
  cat "${_OMC_TMP}"
  rm -f "${_OMC_TMP}"
  exit 2
fi
rm -f "${_OMC_TMP}"

# 파일 경로 추출 (Claude Code: tool_input.file_path / Cursor: params.target_file)
FILE_PATH="$(
  printf '%s' "${INPUT_JSON}" | "${PYTHON_BIN}" -c '
import json, sys
try:
    data = json.load(sys.stdin)
except Exception:
    print("")
    raise SystemExit(0)
params = (data.get("tool_input")
          or data.get("params")
          or data.get("input")
          or {})
path = (params.get("file_path")
        or params.get("target_file")
        or params.get("path")
        or "")
print(path)
' 2>/dev/null
)"

if [ -z "${FILE_PATH}" ]; then
  exit 0
fi

# pipeline guard 실행
GUARD_OUTPUT="$("${PYTHON_BIN}" "${GUARD}" check "${FILE_PATH}" 2>&1)"
GUARD_EXIT_CODE=$?

if [ "${GUARD_EXIT_CODE}" -eq 0 ]; then
  exit 0
fi

# 차단 출력
echo "${GUARD_OUTPUT}"

# Claude Code 차단: exit 2
# Cursor는 permission:deny JSON을 stdout에 써야 하지만,
# 이 스크립트는 Claude Code용이므로 exit 2 사용
exit 2
