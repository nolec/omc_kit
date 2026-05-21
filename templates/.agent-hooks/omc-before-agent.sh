#!/usr/bin/env bash
# Gemini CLI BeforeAgent 훅 — 사용자 메시지 기반 BM25 교훈 자동 주입
# + OMC 세션 동기화 경고 (enforce_confirm=true + 활성 세션 없을 때, 세션당 1회)
# Gemini는 stdout이 반드시 순수 JSON이어야 함 (평문 출력 시 파싱 오류)
set -u

PYTHON_BIN="python3"
command -v python3 >/dev/null 2>&1 || PYTHON_BIN="python"
command -v "${PYTHON_BIN}" >/dev/null 2>&1 || exit 0

# stdin JSON에서 prompt 추출
PROMPT=$("${PYTHON_BIN}" -c "
import json, sys
try:
    d = json.load(sys.stdin)
    print(d.get('prompt', d.get('userMessage', '')))
except Exception:
    print('')
" 2>/dev/null || echo "")

if [[ -z "${PROMPT}" ]]; then
  exit 0
fi

# 짧은 프롬프트(30자 미만) 스킵 — 불필요한 BM25 토큰 절약
if [[ ${#PROMPT} -lt 30 ]]; then
  exit 0
fi

_resolve_script() {
  if [[ -f "scripts/$1" ]]; then echo "scripts/$1"; return 0; fi
  if [[ -f "omc_kit/scripts/$1" ]]; then echo "omc_kit/scripts/$1"; return 0; fi
  return 1
}

# ── 세션 ID 추출 (경고 중복 방지용) ──────────────────────────────────────
SESSION_ID=$("${PYTHON_BIN}" -c "
import json
from pathlib import Path
try:
    d = json.loads(Path('.omc/state/latest.json').read_text(encoding='utf-8'))
    print(d.get('latest_session_id', 'unknown'))
except Exception:
    print('unknown')
" 2>/dev/null || echo "unknown")

WARNED_FLAG="/tmp/omc-session-warned-gemini-${SESSION_ID}"

# ── OMC 세션 동기화 경고 (LESSON_SCRIPT 유무와 무관하게 항상 먼저, 세션당 1회) ──
OMC_WARN=""
if [[ ! -f "${WARNED_FLAG}" ]]; then
  OMC_WARN=$("${PYTHON_BIN}" -c '
import json, sys
from pathlib import Path

policy_path = Path(".omc/policy.json")
latest_path = Path(".omc/state/latest.json")

if not policy_path.exists() or not latest_path.exists():
    print("")
    sys.exit(0)

try:
    policy = json.loads(policy_path.read_text(encoding="utf-8"))
    latest = json.loads(latest_path.read_text(encoding="utf-8"))
except Exception:
    print("")
    sys.exit(0)

if not policy.get("enforce_confirm", False):
    print("")
    sys.exit(0)

status = (latest.get("latest_confirmation") or {}).get("status", "")
request = latest.get("latest_confirmed_request", "(알 수 없음)")

if status != "confirmed":
    print("")
    sys.exit(0)

print(
    f"[OMC 경고] 활성 세션 없음 — 마지막 완료 작업: \"{request}\"\n"
    "새 파일 생성 전에 반드시 새 작업을 선언하세요: "
    "python3 scripts/omc.py \"새 작업 내용\"  또는  /plan [작업]"
)
' 2>/dev/null || echo "")

  if [[ -n "${OMC_WARN}" ]]; then
    touch "${WARNED_FLAG}"
  fi
fi

LESSON_SCRIPT="$(_resolve_script omc_lesson.py || true)"

# LESSON_SCRIPT 없어도 OMC 경고가 있으면 JSON으로 출력 후 종료
if [[ -z "${LESSON_SCRIPT}" ]]; then
  if [[ -n "${OMC_WARN}" ]]; then
    "${PYTHON_BIN}" -c "
import json, sys
warn = sys.argv[1]
print(json.dumps({'additionalContext': '## OMC 세션 동기화 경고\n' + warn}))
" "${OMC_WARN}" 2>/dev/null || true
  fi
  exit 0
fi

# BM25 검색 — 가장 관련성 높은 1개만 주입 (top 3 → top 1 으로 토큰 절약)
TMPFILE=$(mktemp /tmp/omc-lessons.XXXXXX)
"${PYTHON_BIN}" "${LESSON_SCRIPT}" search "${PROMPT}" --top 1 >"${TMPFILE}" 2>/dev/null || true

# 교훈 + 세션 경고를 합쳐 JSON으로 출력
if [[ -s "${TMPFILE}" ]] || [[ -n "${OMC_WARN}" ]]; then
  "${PYTHON_BIN}" - "${TMPFILE}" <<PYEOF
import json, sys
from pathlib import Path

lessons = open(sys.argv[1], encoding="utf-8").read()
warn = """${OMC_WARN}"""

parts = []
if warn.strip():
    parts.append("## OMC 세션 동기화 경고\n" + warn.strip())
if lessons.strip():
    parts.append("## OMC BM25 자동 주입: 관련 교훈\n" + lessons.strip())

if parts:
    print(json.dumps({"additionalContext": "\n\n".join(parts)}))
PYEOF
fi
rm -f "${TMPFILE}"

exit 0
