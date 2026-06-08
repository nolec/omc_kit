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

# pipeline_session.json 에서 contract_confirmed 읽기
pipeline_path = Path(".omc/pipeline_session.json")
contract_confirmed = False
if pipeline_path.exists():
    try:
        pipeline = json.loads(pipeline_path.read_text(encoding="utf-8"))
        contract_confirmed = pipeline.get("contract_confirmed", False)
    except Exception:
        pass

# pending = AI가 현재 작업 중 → 통과
if status == "pending":
    sys.exit(0)

# confirmed + contract_confirmed = 현재 파이프라인 작업 진행 중 → 통과
if status == "confirmed" and contract_confirmed:
    sys.exit(0)

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
  printf '{"permission":"deny","user_message":"[OMC BLOCK] 활성 세션 없음 — pending 세션을 먼저 선언하세요. python3 scripts/omc.py \"새 작업\"","agent_message":"[OMC BLOCK]"}'
  rm -f "${_OMC_OUT}"
  exit 0
fi
rm -f "${_OMC_OUT}"

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
