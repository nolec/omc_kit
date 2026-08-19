#!/usr/bin/env bash
# 공용 OMC 세션 시작 훅 — Cursor / Claude Code / Gemini CLI 공통
# 사용: .agent-hooks/omc-session-start.sh [EXECUTOR_NAME]
#   EXECUTOR_NAME: cursor | claude | gemini | codex (기본값: unknown)
set -u

EXECUTOR="${1:-unknown}"

# stdin은 한 번만 소비한다. 값은 디스크에 저장하지 않고 event_id 판정과
# opt-in shape 진단에만 사용한다.
HOOK_INPUT="$(cat 2>/dev/null || true)"

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
DEDUPE_LOCK=""
DEDUPE_DONE=""
DEDUPE_LOCK_ACQUIRED=0

_release_dedupe_lock() {
  if [[ "${DEDUPE_LOCK_ACQUIRED}" == "1" && -n "${DEDUPE_LOCK}" ]]; then
    rm -f "${DEDUPE_LOCK}/pid" 2>/dev/null || true
    rmdir "${DEDUPE_LOCK}" 2>/dev/null || true
  fi
}
trap _release_dedupe_lock EXIT

if [[ "${OMC_HOOK_DIAGNOSTICS:-0}" == "1" ]]; then
  printf '%s' "${HOOK_INPUT}" | "${PYTHON_BIN}" -c '
import hashlib, json, sys
from pathlib import Path

raw = sys.stdin.buffer.read()
try:
    payload = json.loads(raw.decode("utf-8")) if raw else {}
except Exception:
    payload = {}
fields = sorted(str(key) for key in payload) if isinstance(payload, dict) else []
record = {
    "fields": fields,
    "payload_bytes": len(raw),
    "sha256": hashlib.sha256(raw).hexdigest(),
}
path = Path(sys.argv[1])
path.parent.mkdir(parents=True, exist_ok=True)
with path.open("a", encoding="utf-8") as fh:
    fh.write(json.dumps(record, sort_keys=True) + "\n")
' "${OMC_HOOK_DIAGNOSTICS_FILE:-/private/tmp/omc-hook-diagnostics.jsonl}" 2>/dev/null || true
fi

# event_id는 hook 호출 자체의 안정적 식별자로 확인된 경우에만 사용한다.
# session_id는 resume 등 별도 시작 이벤트를 합칠 수 있어 의도적으로 제외한다.
EVENT_ID=$(printf '%s' "${HOOK_INPUT}" | "${PYTHON_BIN}" -c '
import json, sys
try:
    payload = json.load(sys.stdin)
except Exception:
    payload = {}
value = payload.get("event_id", "") if isinstance(payload, dict) else ""
print(value if isinstance(value, str) else "")
' 2>/dev/null || true)

if [[ -n "${EVENT_ID}" ]]; then
  DEDUPE_KEY=$("${PYTHON_BIN}" -c '
import hashlib, sys
executor, event_id, root = sys.argv[1:]
print(hashlib.sha256(f"{executor}\0{event_id}\0{root}".encode()).hexdigest())
' "${EXECUTOR}" "${EVENT_ID}" "${ROOT}" 2>/dev/null || true)
  if [[ -n "${DEDUPE_KEY}" ]]; then
    DEDUPE_LOCK="${TMPDIR:-/tmp}/omc-session-start-${DEDUPE_KEY}.lock"
    DEDUPE_DONE="${TMPDIR:-/tmp}/omc-session-start-${DEDUPE_KEY}.done"
    DEDUPE_WAIT_COUNT=0
    while (( DEDUPE_WAIT_COUNT < 100 )); do
      [[ -f "${DEDUPE_DONE}" ]] && exit 0
      if mkdir "${DEDUPE_LOCK}" 2>/dev/null; then
        DEDUPE_LOCK_ACQUIRED=1
        break
      fi
      LOCK_PID=$(cat "${DEDUPE_LOCK}/pid" 2>/dev/null || true)
      RECLAIM_LOCK=0
      if [[ -z "${LOCK_PID}" ]]; then
        # mkdir와 pid 기록 사이의 소유권 창을 stale lock으로 오인하지 않는다.
        LOCK_AGE=$("${PYTHON_BIN}" -c '
import os, sys, time
try:
    print(max(0, int(time.time() - os.path.getmtime(sys.argv[1]))))
except OSError:
    print(0)
' "${DEDUPE_LOCK}" 2>/dev/null || echo 0)
        [[ "${LOCK_AGE}" =~ ^[0-9]+$ ]] || LOCK_AGE=0
        (( LOCK_AGE >= 5 )) && RECLAIM_LOCK=1
      elif ! kill -0 "${LOCK_PID}" 2>/dev/null; then
        RECLAIM_LOCK=1
      fi
      if [[ "${RECLAIM_LOCK}" == "1" ]]; then
        rm -f "${DEDUPE_LOCK}/pid" 2>/dev/null || true
        if rmdir "${DEDUPE_LOCK}" 2>/dev/null; then
          continue
        fi
      fi
      DEDUPE_WAIT_COUNT=$((DEDUPE_WAIT_COUNT + 1))
      sleep 0.05
    done
    [[ "${DEDUPE_LOCK_ACQUIRED}" == "1" ]] || exit 0
    printf '%s\n' "$$" >"${DEDUPE_LOCK}/pid" 2>/dev/null || exit 0
  fi
fi

mkdir -p .omc 2>/dev/null || true
printf '%s [%s] sessionStart -> omc state init + hook session_start (cwd=%s)\n' \
  "$(date '+%Y-%m-%dT%H:%M:%S%z')" "${EXECUTOR}" "${ROOT}" \
  >>.omc/agent-hook.log 2>/dev/null || true

"${PYTHON_BIN}" "${OMC_SCRIPT}" state init --target "${ROOT}" --force >/dev/null 2>&1 || exit 0
OMC_EXECUTOR="${EXECUTOR}" "${PYTHON_BIN}" "${OMC_SCRIPT}" hook session_start --target "${ROOT}" >/dev/null 2>&1 || exit 0

# SessionStart stdout → Claude/Codex: 평문, Gemini: JSON (Gemini는 순수 JSON 필수)
SUMMARY_FILE="${ROOT}/.omc/summary.md"
if [[ -f "${SUMMARY_FILE}" ]]; then
  OUTPUT_OK=0
  if [[ "${EXECUTOR}" == "gemini" ]]; then
    # Gemini는 stdout이 반드시 순수 JSON이어야 함
    LESSONS_FILE="${ROOT}/.cursor/rules/omc-lessons-inject.mdc"
    "${PYTHON_BIN}" - "${SUMMARY_FILE}" "${LESSONS_FILE}" <<'PYEOF'
import json, sys
from pathlib import Path
summary = open(sys.argv[1], encoding="utf-8").read()
lessons_path = Path(sys.argv[2]) if len(sys.argv) > 2 else None
lessons = ("\n\n<!-- OMC 자동 주입 교훈 -->\n" + lessons_path.read_text(encoding="utf-8")) if lessons_path and lessons_path.exists() else ""
print(json.dumps({"additionalContext": summary + lessons}))
PYEOF
    [[ $? -eq 0 ]] && OUTPUT_OK=1
  else
    # Claude Code / Codex: 평문 stdout → 컨텍스트로 자동 주입
    LESSONS_FILE="${ROOT}/.cursor/rules/omc-lessons-inject.mdc"
    if {
      echo "<!-- OMC Session Context -->" &&
      cat "${SUMMARY_FILE}" &&
      {
        if [[ -f "${LESSONS_FILE}" ]]; then
          echo "" &&
          echo "<!-- OMC 자동 주입 교훈 -->" &&
          cat "${LESSONS_FILE}"
        else
          true
        fi
      }
    }; then
      OUTPUT_OK=1
    fi
  fi
  if [[ "${OUTPUT_OK}" == "1" && -n "${DEDUPE_DONE}" ]]; then
    touch "${DEDUPE_DONE}" 2>/dev/null || true
  fi
fi

exit 0
