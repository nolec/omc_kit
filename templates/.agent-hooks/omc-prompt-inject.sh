#!/usr/bin/env bash
# UserPromptSubmit 훅 — 사용자 메시지를 BM25 쿼리로 관련 교훈 자동 주입
# + OMC 세션 동기화 경고 (enforce_confirm=true + 활성 세션 없을 때, 세션당 1회)
# Claude Code / Codex: stdout 평문 → 컨텍스트로 자동 주입됨
set -u

PYTHON_BIN="python3"
command -v python3 >/dev/null 2>&1 || PYTHON_BIN="python"
command -v "${PYTHON_BIN}" >/dev/null 2>&1 || exit 0

# stdin JSON에서 prompt 텍스트 추출
# 환경변수 PROMPT가 이미 설정돼 있으면 stdin 파싱 스킵 (테스트/직접 호출 지원)
if [[ -n "${PROMPT:-}" ]]; then
  : # 환경변수로 이미 설정됨
elif [ -t 0 ]; then
  # 대화형 터미널(stdin이 실제 키보드) → 블로킹 방지를 위해 스킵
  exit 0
else
  PROMPT=$("${PYTHON_BIN}" -c "
import json, sys
try:
    d = json.load(sys.stdin)
    print(d.get('prompt', ''))
except Exception:
    print('')
" 2>/dev/null || echo "")
fi

if [[ -z "${PROMPT}" ]]; then
  exit 0
fi


# ── 모호 메시지 감지 (30자 early-exit 이전) ──────────────────────────────
# "응" "ㅇ" "진행하자" 같은 메시지에 확인 질문 주입
# 판정: (15자 미만 + 부분 일치) OR (완전 일치) AND 명시적 스킬명 없으면 모호
_AMBIGUOUS=0
_EXPLICIT=0

# 명시적 스킬명 포함 여부 먼저 확인
if printf '%s' "${PROMPT}" | grep -qiE "omc-|/plan|/task|/review|/ship|/investigate|/critique|/brainstorm|스킬|skill"; then
  _EXPLICIT=1
fi

if [ "${_EXPLICIT}" -eq 0 ]; then
  # 완전 일치 패턴
  if printf '%s' "${PROMPT}" | grep -qE "^(진행|계속|ㅇ|응|고고|go|next|ok|ㅇㅇ|yes|계속해|그래|그렇게 진행|진행하자|계속하자)$"; then
    _AMBIGUOUS=1
  fi
  # 15자 미만 + 부분 일치
  if [ "${#PROMPT}" -lt 15 ] && printf '%s' "${PROMPT}" | grep -qiE "(진행|계속|ㅇ|응|go|next|ok)"; then
    _AMBIGUOUS=1
  fi
fi

if [ "${_AMBIGUOUS}" -eq 1 ]; then
  # confirmed 상태일 때만 확인 질문 주입 (pending = 작업 진행 중 → 주입 안 함)
  _CONFIRM_STATUS=$("${PYTHON_BIN}" -c '
import json
from pathlib import Path
try:
    d = json.loads(Path(".omc/state/latest.json").read_text(encoding="utf-8"))
    print((d.get("latest_confirmation") or {}).get("status", ""))
except Exception:
    print("")
' 2>/dev/null || echo "")

  if [ "${_CONFIRM_STATUS}" = "confirmed" ]; then
    _SKILL=$("${PYTHON_BIN}" -c '
import json
from pathlib import Path
try:
    d = json.loads(Path(".omc/state/latest.json").read_text(encoding="utf-8"))
    print(d.get("latest_skill", ""))
except Exception:
    print("")
' 2>/dev/null || echo "")

    echo ""
    echo "[OMC] 모호한 진행 요청입니다 — 무엇을 진행할까요?"
    if [ -n "${_SKILL}" ]; then
      echo "  직전 스킬: ${_SKILL}"
    fi
    echo "  예: \"omc-task 진행해줘\" 또는 \"/plan [설명]\""
    exit 0
  fi
fi

# 짧은 프롬프트(응, 고마워, 확인 등 30자 미만) 스킵 — 불필요한 BM25 토큰 절약
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

WARNED_FLAG="/tmp/omc-session-warned-${SESSION_ID}"

# ── OMC 세션 동기화 경고 (세션당 1회만) ──────────────────────────────────
if [[ ! -f "${WARNED_FLAG}" ]]; then
  OMC_WARNING=$("${PYTHON_BIN}" -c '
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

if status != "confirmed":
    sys.exit(0)

print()
print("=========================================================")
print("[OMC BLOCK] 활성 세션 없음 — 새 작업 선언이 필요합니다")
print(f"  마지막 완료 작업: \"{request}\"")
print()
print("  파일을 수정·생성하기 전에 반드시 선언하세요:")
print("    python3 scripts/omc.py \"새 작업 내용\"")
print("  또는 IDE 커맨드: /plan [작업] / /task [설명]")
print()
print("  ⚠ 선언 없이 진행하면 TDD 게이트·커밋 훅이 차단합니다.")
print("=========================================================")
' 2>/dev/null || true)

  if [[ -n "${OMC_WARNING}" ]]; then
    echo "${OMC_WARNING}"
    touch "${WARNED_FLAG}"
  fi
fi

LESSON_SCRIPT="$(_resolve_script omc_lesson.py || true)"
if [[ -z "${LESSON_SCRIPT}" ]]; then
  exit 0
fi

# BM25 검색 — 가장 관련성 높은 1개만 주입 (top 3 → top 1 으로 토큰 절약)
LESSONS=$("${PYTHON_BIN}" "${LESSON_SCRIPT}" search "${PROMPT}" --top 1 2>/dev/null || true)
if [[ -n "${LESSONS}" ]]; then
  echo "<!-- OMC BM25 자동 주입: 관련 교훈 -->"
  echo "${LESSONS}"
fi

exit 0
