#!/usr/bin/env bash
# omc-pipeline-check.sh — Cursor beforeToolCall 훅
# create_file / edit_file 호출 전 세션 검사 + TDD 파이프라인 게이트
#
# Cursor가 JSON으로 도구 호출 정보를 stdin에 전달합니다:
# { "tool": "create_file", "params": { "target_file": "path/to/file.ts", ... } }
set -u

PYTHON_BIN="python3"
if ! command -v "${PYTHON_BIN}" >/dev/null 2>&1; then
  PYTHON_BIN="python"
fi

GUARD="scripts/omc_pipeline_guard.py"
if [[ ! -f "${GUARD}" ]]; then
  echo '{"permission":"allow"}'
  exit 0
fi

# stdin에서 JSON 읽기
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

# 파일 경로 추출
FILE_PATH="$(
  printf '%s' "${INPUT_JSON}" | "${PYTHON_BIN}" -c '
import json, sys
try:
    data = json.load(sys.stdin)
except Exception:
    print("")
    raise SystemExit(0)
params = data.get("params", data.get("input", {}))
print(params.get("target_file") or params.get("path") or "")
' 2>/dev/null
)"

# ── 민감 경로 판별 함수 ──────────────────────────────────────────────────
# OMC 시스템 파일(scripts/, .agent-hooks/ 등)은 edit_file도 세션 검사 대상
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
  create_file)
    # 신규 파일 생성: 경로 무관 항상 세션 검사
    ;;
  edit_file)
    # 기존 파일 수정: 항상 CONTRACT 확인 + 민감 경로면 세션 검사도 수행
    ;;
  *)
    echo '{"permission":"allow"}'
    exit 0
    ;;
esac

# ── OMC 세션 동기화 검사 ──────────────────────────────────────────────────
# enforce_confirm=true + 활성 세션(pending) 없으면 차단
_OMC_TMP="$(mktemp)"
"${PYTHON_BIN}" -c "

import json, sys
from pathlib import Path

policy_path = Path(".omc/policy.json")
latest_path = Path(".omc/state/latest.json")

if not policy_path.exists() or not latest_path.exists():
    sys.exit(0)

try:
    policy = json.loads(policy_path.read_text(encoding="utf-8"))
    latest = json.loads(latest_path.read_text(encoding="utf-8"))
except Exception:
    sys.exit(0)

if not policy.get("enforce_confirm", False):
    sys.exit(0)

status = (latest.get("latest_confirmation") or {}).get("status", "")
request = latest.get("latest_confirmed_request", "(알 수 없음)")

if status == "pending":
    sys.exit(0)

if status == "confirmed":
    msg = (
        f"[OMC BLOCK] 활성 세션 없음 — 마지막 작업: \"{request}\"\n"
        "\n"
        "⚠️  'state confirm'은 작업 완료 처리입니다. 실행하면 또 막힙니다.\n"
        "\n"
        "▶ 올바른 절차: 새 작업을 선언해서 pending 세션을 만드세요.\n"
        "  python3 scripts/omc.py \"새 작업 내용\"\n"
        "  또는 Cursor에서: /plan [작업]"
    )
    print(json.dumps({"permission": "deny", "user_message": msg, "agent_message": msg}, ensure_ascii=False))
    sys.exit(1)
" > "${_OMC_TMP}" 2>/dev/null
OMC_SYNC_EXIT=$?

if [[ ${OMC_SYNC_EXIT} -ne 0 ]]; then
  cat "${_OMC_TMP}"
  rm -f "${_OMC_TMP}"
  exit 0
fi
rm -f "${_OMC_TMP}"

# edit_file은 CONTRACT 확인 체크 후 허용
if [[ "${TOOL_NAME}" == "edit_file" ]]; then
  if [[ -z "${FILE_PATH}" ]]; then
    echo '{"permission":"allow"}'
    exit 0
  fi

  # 변경 범위 경고 (차단 없음 — 정보 제공용)
  "${PYTHON_BIN}" "${GUARD}" scope-check 2>/dev/null || true

  CONTRACT_OUTPUT="$("${PYTHON_BIN}" "${GUARD}" check-edit "${FILE_PATH}" 2>&1)"
  CONTRACT_EXIT=$?

  if [[ ${CONTRACT_EXIT} -eq 0 ]]; then
    echo '{"permission":"allow"}'
    exit 0
  fi

  BLOCK_MESSAGE="$(
    printf '%s' "${CONTRACT_OUTPUT}" | "${PYTHON_BIN}" -c '
import json, sys
message = sys.stdin.read().strip()
if not message:
    message = "CONTRACT GATE: CONTRACT 양식을 먼저 작성하고 contract-done을 실행하세요."
print(json.dumps({
    "permission": "deny",
    "user_message": message,
    "agent_message": message,
}, ensure_ascii=False))
'
  )"
  printf '%s\n' "${BLOCK_MESSAGE}"
  exit 0
fi

# create_file: 파일 경로 없으면 통과
if [[ -z "${FILE_PATH}" ]]; then
  echo '{"permission":"allow"}'
  exit 0
fi

# ── TDD 파이프라인 게이트 (create_file만) ────────────────────────────────
GUARD_OUTPUT="$("${PYTHON_BIN}" "${GUARD}" check "${FILE_PATH}" 2>&1)"
GUARD_EXIT_CODE=$?

if [[ ${GUARD_EXIT_CODE} -eq 0 ]]; then
  echo '{"permission":"allow"}'
  exit 0
fi

BLOCK_MESSAGE="$(
  printf '%s' "${GUARD_OUTPUT}" | "${PYTHON_BIN}" -c '
import json, sys
message = sys.stdin.read().strip()
if not message:
    message = "PIPELINE GATE: RED 단계(실패 테스트)를 먼저 완료하세요."
print(json.dumps({
    "permission": "deny",
    "user_message": message,
    "agent_message": message,
}, ensure_ascii=False))
'
)"

printf '%s\n' "${BLOCK_MESSAGE}"
exit 0
