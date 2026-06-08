#!/bin/sh
# omc-pipeline-check.sh — 공용 파이프라인 가드
#
# 지원 LLM: Claude Code (PreToolUse), Codex (PreToolUse), Gemini CLI (BeforeTool)
#
# 차단 exit code:
#   exit 0  → 허용
#   exit 2  → 차단 기본 (Claude Code / Gemini CLI 표준)
#   exit 1  → 차단 Codex 호환 (OMC_BLOCK_EXIT=1 환경변수로 지정)
#
# stdin: 도구 호출 JSON (LLM별 tool 이름)
# {
#   "tool_name": "Write"|"create_file"          (Claude Code — 신규 파일)
#              | "Edit"|"MultiEdit"              (Claude Code — 수정)
#              | "write_file"                    (Gemini CLI  — 신규/덮어쓰기)
#              | "replace"                       (Gemini CLI  — 수정)
#   "tool_input": { "file_path": "...", ... }
# }
# write / write_file / create_file → 모든 경로 세션 검사
# edit / replace                   → 민감 경로(scripts/, .agent-hooks/ 등)만 검사

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
# Claude Code: Write, create_file, Edit, MultiEdit
# Gemini CLI:  write_file, replace, overwrite_file, edit
# Codex CLI:   apply_patch, Write (파일 편집)
case "${TOOL_NAME}" in
  Write|create_file|write_file|overwrite_file|apply_patch)
    # 신규/덮어쓰기 파일 생성: 경로 무관 항상 세션 검사
    ;;
  Edit|MultiEdit|replace|edit|str_replace)
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
# 인라인 -c "..." 는 Python 코드 내 따옴표 충돌 위험 → 임시 .py 파일 방식 사용
_OMC_PY="$(mktemp /tmp/omc_check_XXXXXX.py)"
_OMC_OUT="$(mktemp -t omc_out_XXXXXX)"
cat > "${_OMC_PY}" << 'OMCPYEOF'
import json, sys
from pathlib import Path

policy_path = Path(".omc/policy.json")
latest_path = Path(".omc/state/latest.json")

if not policy_path.exists():
    sys.exit(0)

try:
    policy = json.loads(policy_path.read_text(encoding="utf-8"))
except Exception:
    sys.exit(0)

if not policy.get("enforce_confirm", False):
    sys.exit(0)

if not latest_path.exists():
    sys.exit(0)

try:
    latest = json.loads(latest_path.read_text(encoding="utf-8"))
except Exception:
    sys.exit(0)

status = (latest.get("latest_confirmation") or {}).get("status", "")
request = latest.get("latest_confirmed_request", "(알 수 없음)")

# pipeline_session.json 에서 contract_confirmed + session_id 읽기
pipeline_path = Path(".omc/pipeline_session.json")
contract_confirmed = False
pipeline_session_id = ""
if pipeline_path.exists():
    try:
        pipeline = json.loads(pipeline_path.read_text(encoding="utf-8"))
        contract_confirmed = pipeline.get("contract_confirmed", False)
        pipeline_session_id = pipeline.get("session_id", "")
    except Exception:
        pass

# latest 에서 현재 confirmed session_id 읽기
latest_session_id = latest.get("latest_confirmed_session_id", "")

# pending = AI가 현재 작업 중 → 통과
if status == "pending":
    sys.exit(0)

# confirmed + contract_confirmed + session_id 일치 = 현재 파이프라인 작업 진행 중 → 통과
# session_id 가 비어있는 경우(기존 install, 이전 버전 호환)는 일치로 간주
# 단, latest_session_id 자체가 없으면 현재 세션 연계를 확인할 수 없으므로 통과시키지 않음
if status == "confirmed" and contract_confirmed:
    if latest_session_id and (not pipeline_session_id or pipeline_session_id == latest_session_id):
        sys.exit(0)
    # latest_session_id 없음 또는 session_id 불일치 = 세션 연계 불명확/이전 잔재 → 차단
    msg = f"[OMC BLOCK] 세션 불일치 — pipeline_session={pipeline_session_id[:8]} latest={latest_session_id[:8]}. contract-done 재실행 필요"
    print(msg)
    sys.exit(1)

# confirmed + contract_confirmed 없음 = 세션 완료 후 새 작업 선언 전 → 차단
if status == "confirmed":
    msg = f"[OMC BLOCK] 활성 세션 없음 — 마지막 작업: {request}"
    sys.exit(1)

# 그 외(빈 문자열, "none", 첫 설치 등) → 세션 미설정 상태로 간주, 통과
sys.exit(0)
OMCPYEOF
"${PYTHON_BIN}" "${_OMC_PY}" > "${_OMC_OUT}" 2>/dev/null
OMC_SYNC_EXIT=$?
rm -f "${_OMC_PY}"

if [ "${OMC_SYNC_EXIT}" -ne 0 ]; then
  cat "${_OMC_OUT}"
  rm -f "${_OMC_OUT}"
  _BLOCK_EXIT="${OMC_BLOCK_EXIT:-2}"
  exit "${_BLOCK_EXIT}"
fi
rm -f "${_OMC_OUT}"

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

# 차단 exit code: OMC_BLOCK_EXIT 환경변수로 LLM별 호환 코드 지정 가능
# Claude Code 기본: exit 2 / Codex 호환: OMC_BLOCK_EXIT=1
_BLOCK_EXIT="${OMC_BLOCK_EXIT:-2}"
exit "${_BLOCK_EXIT}"
