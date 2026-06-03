#!/bin/sh
# omc-post-file-check.sh — Codex PostToolUse 소프트 가드
#
# PreToolUse가 파일 편집 경로에서 동작하지 않는 Codex에서
# 파일 수정 직후 세션 미확인이면 경고를 출력해 모델이 인식하게 함.
#
# 차단(exit 2)이 아닌 경고(exit 0) — PostToolUse는 블로킹 아님
# stdin: Codex PostToolUse JSON { tool_name, tool_input: { file_path } }

PYTHON_BIN="python3"
if ! command -v "${PYTHON_BIN}" >/dev/null 2>&1; then
  PYTHON_BIN="python"
fi

INPUT_JSON="$(cat)"

# 파일 경로 추출
FILE_PATH="$(
  printf '%s' "${INPUT_JSON}" | "${PYTHON_BIN}" -c '
import json, sys
try:
    data = json.load(sys.stdin)
except Exception:
    print(""); raise SystemExit(0)
inp = data.get("tool_input") or data.get("params") or {}
print(inp.get("file_path") or inp.get("target_file") or inp.get("path") or "(unknown)")
' 2>/dev/null
)"

# policy + 세션 상태 확인
_OMC_PY="$(mktemp /tmp/omc_post_XXXXXX.py)"
_OMC_OUT="$(mktemp /tmp/omc_post_out_XXXXXX)"

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
    sys.exit(1)  # 세션 없음 → 경고 필요

try:
    latest = json.loads(latest_path.read_text(encoding="utf-8"))
except Exception:
    sys.exit(0)

status = (latest.get("latest_confirmation") or {}).get("status", "")

if status in ("pending", ""):
    sys.exit(1)  # 미확인 세션 → 경고 필요

sys.exit(0)  # confirmed → 조용히 통과
OMCPYEOF

"${PYTHON_BIN}" "${_OMC_PY}" > "${_OMC_OUT}" 2>/dev/null
_EXIT=$?
rm -f "${_OMC_PY}" "${_OMC_OUT}"

if [ "${_EXIT}" -ne 0 ]; then
  cat >&2 << WARNEOF
[OMC WARNING] 세션 미확인 상태에서 파일이 수정됐습니다.
  파일: ${FILE_PATH}
  조치: CONTRACT 등록 후 작업하세요.
    python3 scripts/omc.py state record --target . --request "작업 설명" --roles senior_coding
    python3 scripts/omc_pipeline_guard.py contract-done
WARNEOF
fi

exit 0
