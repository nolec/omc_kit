#!/usr/bin/env bash
set -u

OMC_GUARD="scripts/omc_guard.py"
PYTHON_BIN="python3"

if ! command -v "${PYTHON_BIN}" >/dev/null 2>&1; then
  PYTHON_BIN="python"
fi

if ! command -v "${PYTHON_BIN}" >/dev/null 2>&1; then
  echo '{"permission":"allow"}'
  exit 0
fi

if [[ ! -f "${OMC_GUARD}" ]]; then
  echo '{"permission":"allow"}'
  exit 0
fi

INPUT_JSON="$(cat)"

COMMAND="$(
  printf '%s' "${INPUT_JSON}" | "${PYTHON_BIN}" -c '
import json, sys
try:
    data = json.load(sys.stdin)
except Exception:
    print("")
    raise SystemExit(0)
print(data.get("command", ""))
'
)"

is_risky_command() {
  local cmd="$1"

  if [[ "${cmd}" =~ (^|[[:space:]])rm[[:space:]]+-rf([[:space:]]|$) ]]; then
    return 0
  fi
  if [[ "${cmd}" =~ (^|[[:space:]])git[[:space:]]+reset[[:space:]]+--hard([[:space:]]|$) ]]; then
    return 0
  fi
  if [[ "${cmd}" =~ (^|[[:space:]])git[[:space:]]+clean[[:space:]]+-f ]]; then
    return 0
  fi
  if [[ "${cmd}" =~ (^|[[:space:]])git[[:space:]]+push[[:space:]]+.*--force([[:space:]]|$) ]]; then
    return 0
  fi
  if [[ "${cmd}" =~ (^|[[:space:]])git[[:space:]]+push[[:space:]]+.*[[:space:]]-f([[:space:]]|$) ]]; then
    return 0
  fi
  if [[ "${cmd}" =~ (^|[[:space:]])(npm|yarn|pnpm)[[:space:]]+publish([[:space:]]|$) ]]; then
    return 0
  fi
  if [[ "${cmd}" =~ (^|[[:space:]])(vercel|flyctl)[[:space:]]+deploy([[:space:]]|$) ]]; then
    return 0
  fi
  if [[ "${cmd}" =~ (^|[[:space:]])terraform[[:space:]]+(apply|destroy)([[:space:]]|$) ]]; then
    return 0
  fi
  if [[ "${cmd}" =~ (^|[[:space:]])kubectl[[:space:]]+delete([[:space:]]|$) ]]; then
    return 0
  fi

  return 1
}

if [[ "${COMMAND}" == *"scripts/omc.py"* ]] || [[ "${COMMAND}" == *"omc_kit/scripts/omc.py"* ]] || [[ "${COMMAND}" == *"./run omc"* ]] || [[ "${COMMAND}" == *"./run omc-chat"* ]] || [[ "${COMMAND}" == *"scripts/omc_chat.py"* ]] || [[ "${COMMAND}" == *"scripts/omc_guard.py"* ]] || [[ "${COMMAND}" == *"omc_pipeline_guard"* ]] || [[ "${COMMAND}" == *"omc_tdd_check"* ]] || [[ "${COMMAND}" == *"omc_doctor"* ]]; then
  echo '{"permission":"allow"}'
  exit 0
fi

GUARD_OUTPUT="$("${PYTHON_BIN}" "${OMC_GUARD}" require --target . --for "beforeShellExecution: ${COMMAND}" 2>&1)"
GUARD_EXIT_CODE=$?

if [[ ${GUARD_EXIT_CODE} -eq 0 ]]; then
  if is_risky_command "${COMMAND}"; then
    RISK_MESSAGE="$(
      printf '%s' "${COMMAND}" | "${PYTHON_BIN}" -c '
import json, sys
command = sys.stdin.read().strip()
if not command:
    command = "(empty command)"
message = (
    "[OMC-GUARD] 고위험 명령으로 분류되어 자동 차단되었습니다.\n"
    f"Command: {command}\n"
    "정말 필요하면 명령을 분해하거나 safer 대안을 먼저 사용해 주세요."
)
print(json.dumps({
    "permission": "deny",
    "user_message": message,
    "agent_message": message,
}, ensure_ascii=False))
'
    )"
    printf '%s\n' "${RISK_MESSAGE}"
    exit 0
  fi
  echo '{"permission":"allow"}'
  exit 0
fi

HOOK_MESSAGE="$(
  printf '%s' "${GUARD_OUTPUT}" | "${PYTHON_BIN}" -c '
import json, sys
message = sys.stdin.read().strip()
if not message:
    message = "OMC 확인이 필요합니다. 먼저 OMC 요청을 생성/확인해 주세요."
print(json.dumps({
    "permission": "deny",
    "user_message": message,
    "agent_message": message,
}, ensure_ascii=False))
'
)"

printf '%s\n' "${HOOK_MESSAGE}"
exit 0
