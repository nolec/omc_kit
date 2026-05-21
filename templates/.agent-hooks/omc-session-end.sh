#!/usr/bin/env bash
# 공용 OMC 세션 종료 훅
# 사용: .agent-hooks/omc-session-end.sh [EXECUTOR_NAME]
set -u

EXECUTOR="${1:-unknown}"

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

_resolve_omc_cost_script() {
  if [[ -f "scripts/omc_cost.py" ]]; then
    echo "scripts/omc_cost.py"
    return 0
  fi
  if [[ -f "omc_kit/scripts/omc_cost.py" ]]; then
    echo "omc_kit/scripts/omc_cost.py"
    return 0
  fi
  return 1
}

OMC_SCRIPT="$(_resolve_omc_script || true)"
OMC_COST_SCRIPT="$(_resolve_omc_cost_script || true)"
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
printf '%s [%s] sessionEnd -> omc hook session_end (cwd=%s)\n' \
  "$(date '+%Y-%m-%dT%H:%M:%S%z')" "${EXECUTOR}" "${ROOT}" \
  >>.omc/agent-hook.log 2>/dev/null || true

OMC_EXECUTOR="${EXECUTOR}" python3 "${OMC_SCRIPT}" hook session_end --target "${ROOT}" >/dev/null 2>&1 || true

# 비용 로그는 best-effort (실패해도 세션 종료 훅 자체는 통과)
if [[ -n "${OMC_COST_SCRIPT}" ]]; then
  OMC_EXECUTOR="${EXECUTOR}" "${PYTHON_BIN}" "${OMC_COST_SCRIPT}" \
    --target "${ROOT}" record \
    --executor "${EXECUTOR}" \
    --task "session_end:auto" >/dev/null 2>&1 || true
fi

exit 0
