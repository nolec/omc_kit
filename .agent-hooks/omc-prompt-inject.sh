#!/usr/bin/env bash
# UserPromptSubmit 훅 — 사용자 메시지를 BM25 쿼리로 관련 교훈 자동 주입
# 짧고 모호한 진행 요청에는 현재 파이프라인 상태에 맞는 확인 질문을 제공
# Claude Code / Codex: stdout 평문 → 컨텍스트로 자동 주입됨
set -u

PYTHON_BIN="python3"
command -v python3 >/dev/null 2>&1 || PYTHON_BIN="python"
command -v "${PYTHON_BIN}" >/dev/null 2>&1 || exit 0

# stdin JSON에서 prompt 텍스트 추출
# 환경변수 PROMPT가 이미 설정돼 있으면 stdin 파싱 스킵 (테스트/직접 호출 지원)
if [[ -n "${PROMPT:-}" ]]; then
  _PROMPT_BASE64=$(PROMPT="${PROMPT}" "${PYTHON_BIN}" -c '
import base64, os, sys
sys.stdout.write(base64.b64encode(os.environ["PROMPT"].encode("utf-8")).decode("ascii"))
' 2>/dev/null || echo "")
elif [ -t 0 ]; then
  # 대화형 터미널(stdin이 실제 키보드) → 블로킹 방지를 위해 스킵
  exit 0
else
  _PROMPT_BASE64=$("${PYTHON_BIN}" -c "
import base64, json, sys
try:
    d = json.load(sys.stdin)
    prompt = d.get('prompt', '')
    if not isinstance(prompt, str):
        raise ValueError('prompt must be text')
    sys.stdout.write(base64.b64encode(prompt.encode('utf-8')).decode('ascii'))
except Exception:
    pass
" 2>/dev/null || echo "")
  PROMPT=$(OMC_LIVE_PROMPT_BASE64="${_PROMPT_BASE64}" "${PYTHON_BIN}" -c '
import base64, os, sys
sys.stdout.write(base64.b64decode(os.environ["OMC_LIVE_PROMPT_BASE64"]).decode("utf-8"))
' 2>/dev/null || echo "")
fi

if [[ -z "${PROMPT}" ]]; then
  exit 0
fi

# Codex completion observation: an exact work id routes across registered repos;
# otherwise the original prompt is quarantined for target resolution.
_OBSERVATION_SCRIPT="${OMC_COMPLETION_OBSERVATION_SCRIPT:-}"
if [ -z "${_OBSERVATION_SCRIPT}" ] && [ -f "scripts/omc_completion_observation.py" ]; then
  _OBSERVATION_SCRIPT="scripts/omc_completion_observation.py"
elif [ -z "${_OBSERVATION_SCRIPT}" ] && [ -f "omc_kit/scripts/omc_completion_observation.py" ]; then
  _OBSERVATION_SCRIPT="omc_kit/scripts/omc_completion_observation.py"
fi
if [ "${1:-unknown}" = "codex" ] && [ -n "${_OBSERVATION_SCRIPT}" ]; then
  if [ -n "${OMC_COMPLETION_OBSERVATION_REGISTRY:-}" ] && [ -n "${OMC_COMPLETION_OBSERVATION_QUARANTINE:-}" ]; then
    _OBSERVATION_COMMAND=("${PYTHON_BIN}" "${_OBSERVATION_SCRIPT}" live-route-prompt --target . --executor-surface codex --repository-registry "${OMC_COMPLETION_OBSERVATION_REGISTRY}" --quarantine-root "${OMC_COMPLETION_OBSERVATION_QUARANTINE}")
    if [ -n "${OMC_COMPLETION_OBSERVATION_WORK_ID:-}" ]; then
      _OBSERVATION_COMMAND+=(--work-id "${OMC_COMPLETION_OBSERVATION_WORK_ID}")
    fi
  else
    _OBSERVATION_COMMAND=("${PYTHON_BIN}" "${_OBSERVATION_SCRIPT}" live-prompt --target . --executor-surface codex)
  fi
  _OBSERVATION_RESULT=$(OMC_LIVE_PROMPT_BASE64="${_PROMPT_BASE64}" "${_OBSERVATION_COMMAND[@]}" 2>/dev/null || true)
  _OBSERVATION_STATUS=$(printf '%s' "${_OBSERVATION_RESULT}" | "${PYTHON_BIN}" -c '
import json, sys
try:
    print(json.load(sys.stdin).get("status", ""))
except Exception:
    print("")
' 2>/dev/null || echo "")
  _OBSERVATION_REASON=$(printf '%s' "${_OBSERVATION_RESULT}" | "${PYTHON_BIN}" -c '
import json, sys
try:
    print(json.load(sys.stdin).get("reason", ""))
except Exception:
    print("")
' 2>/dev/null || echo "")
  if [ "${_OBSERVATION_STATUS}" = "OUTCOME_RECORDED" ]; then
    echo ""
    echo "[OMC] 완료 품질 관찰: 사용자 수용/보류 원문 기록 완료"
  elif [ "${_OBSERVATION_STATUS}" = "FOLLOWUP_RECORDED" ]; then
    echo ""
    echo "[OMC] 완료 품질 관찰: 후속 원문 보존 완료"
  elif [ "${_OBSERVATION_STATUS}" = "CLASSIFICATION_RECORDED" ]; then
    echo ""
    echo "[OMC] 완료 품질 관찰: 사용자 확정 분류 기록 완료"
  elif [ "${_OBSERVATION_STATUS}" = "TARGET_RESOLUTION_REQUIRED" ]; then
    echo ""
    echo "[OMC] 완료 품질 관찰: 후속 원문 보존 완료 — 연결할 작업만 지정해 주세요."
  elif [ "${_OBSERVATION_STATUS}" = "OBSERVATION_INVALID" ] && [ "${_OBSERVATION_REASON}" != "pending_completion_invalid" ] && [ "${_OBSERVATION_REASON}" != "live_observation_not_enabled" ] && [ "${_OBSERVATION_REASON}" != "live_observation_not_started" ] && [ "${_OBSERVATION_REASON}" != "live_prompt_not_expected" ]; then
    echo ""
    echo "[OMC] OBSERVATION_INVALID: ${_OBSERVATION_REASON}"
    echo "  제품 작업은 계속하지만 이 표본은 성공으로 집계하지 않습니다."
  fi
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

# Codex의 Markdown skill 링크는 검색어가 아니라 호출 메타데이터다.
# 링크만 있는 호출은 추가 컨텍스트가 필요 없고, 설명이 붙으면 설명만 BM25에 사용한다.
if [ "${_EXPLICIT}" -eq 1 ]; then
  PROMPT=$(PROMPT="${PROMPT}" "${PYTHON_BIN}" -c '
import os, re
value = os.environ.get("PROMPT", "")
value = re.sub(r"\[\$?omc-[^\]]+\]\([^)]*\)", " ", value, flags=re.IGNORECASE)
print(" ".join(value.split()))
' 2>/dev/null || printf '%s' "${PROMPT}")
  if [[ -z "${PROMPT}" ]]; then
    exit 0
  fi
fi

# 짧은 응답은 먼저 현재 세션의 단일 local-commit decision에 결합합니다.
# 이 receipt는 UX 중복 질문 억제용이며 push/deploy 권한으로 사용하지 않습니다.
if [ "${_EXPLICIT}" -eq 0 ] && [ "${#PROMPT}" -lt 30 ]; then
  _OMC_CLI="${OMC_CLI_SCRIPT:-}"
  if [ -z "${_OMC_CLI}" ] && [ -f "scripts/omc.py" ]; then
    _OMC_CLI="scripts/omc.py"
  elif [ -z "${_OMC_CLI}" ] && [ -f "omc_kit/scripts/omc.py" ]; then
    _OMC_CLI="omc_kit/scripts/omc.py"
  fi
  if [ -n "${_OMC_CLI}" ]; then
    _DECISION_RESULT=$("${PYTHON_BIN}" "${_OMC_CLI}" state decision-resolve --target . --response "${PROMPT}" 2>/dev/null || true)
    _DECISION_RESOLVED=$(printf '%s' "${_DECISION_RESULT}" | "${PYTHON_BIN}" -c '
import json, sys
try:
    print("1" if json.load(sys.stdin).get("resolved") is True else "0")
except Exception:
    print("0")
' 2>/dev/null || echo "0")
    if [ "${_DECISION_RESOLVED}" = "1" ]; then
      echo ""
      echo "[OMC] 승인된 현재 작업 계속 실행 — local commit acknowledgment inherited"
      echo "  커밋 메시지는 저장소 규칙과 확정 범위에서 자동 생성합니다."
      echo "  push/deploy/delete 승인은 포함하지 않습니다."
      exit 0
    fi
  fi
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
  # 3-state: active / ask / skip
  # active  = 파이프라인 진행 중 (contract_confirmed + session_id 일치) → 주입 안 함
  # ask     = confirmed 세션 존재하지만 파이프라인 비활성 → 확인 질문 주입
  # skip    = pending·미설정 등 → 주입 안 함
  _PROMPT_STATE=$("${PYTHON_BIN}" -c '
import json, sys
from pathlib import Path

try:
    latest = json.loads(Path(".omc/state/latest.json").read_text(encoding="utf-8"))
except Exception:
    print("skip"); sys.exit(0)

status = (latest.get("latest_confirmation") or {}).get("status", "")

if status == "pending":
    print("skip"); sys.exit(0)

if status != "confirmed":
    print("skip"); sys.exit(0)

# confirmed 상태 — pipeline_session 확인
pipeline_path = Path(".omc/pipeline_session.json")
contract_confirmed = False
pipeline_session_id = ""
if pipeline_path.exists():
    try:
        ps = json.loads(pipeline_path.read_text(encoding="utf-8"))
        contract_confirmed = ps.get("contract_confirmed", False)
        pipeline_session_id = ps.get("session_id", "")
    except Exception:
        pass

latest_session_id = latest.get("latest_confirmed_session_id", "")

if contract_confirmed:
    # latest_session_id가 있을 때만 active 판정
    # pipeline_session_id가 비어있는 경우는 구버전 호환으로 active 허용
    if latest_session_id and (not pipeline_session_id or pipeline_session_id == latest_session_id):
        print("active"); sys.exit(0)

print("ask")
' 2>/dev/null || echo "skip")

  if [ "${_PROMPT_STATE}" = "ask" ]; then
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
