#!/usr/bin/env bash
# omc-hub-push.sh — omc_* 파일 수정 후 hub에 자동 동기화
#
# afterToolCall 훅: 파일 write 후 실행
# omc_*.py 또는 omc-*.mdc 파일이 수정됐을 때만 hub push

set -euo pipefail

PYTHON_BIN="${PYTHON_BIN:-python3}"
ROOT_DIR="$(git rev-parse --show-toplevel 2>/dev/null || pwd)"

# hub.path 없으면 조용히 종료
HUB_PATH_FILE="$ROOT_DIR/.omc/hub.path"
if [ ! -f "$HUB_PATH_FILE" ]; then
    exit 0
fi

# 수정된 파일 경로 추출 (afterToolCall INPUT_JSON)
INPUT_JSON="${INPUT_JSON:-}"
if [ -z "$INPUT_JSON" ]; then
    exit 0
fi

FILE_PATH="$(
    printf '%s' "$INPUT_JSON" | "$PYTHON_BIN" -c '
import json, sys
try:
    data = json.load(sys.stdin)
    # Cursor afterToolCall: tool_input.target_file 또는 params.target_file
    path = (
        data.get("tool_input", {}).get("target_file")
        or data.get("tool_input", {}).get("path")
        or data.get("params", {}).get("target_file")
        or ""
    )
    print(path)
except Exception:
    print("")
' 2>/dev/null
)"

if [ -z "$FILE_PATH" ]; then
    exit 0
fi

# omc_*.py 또는 omc-*.mdc 파일인지 확인
BASENAME="$(basename "$FILE_PATH")"
if [[ ! "$BASENAME" =~ ^omc_.*\.py$ ]] && [[ ! "$BASENAME" =~ ^omc-.*\.mdc$ ]]; then
    exit 0
fi

# hub push 실행 (--push: 확인 없이 바로)
"$PYTHON_BIN" "$ROOT_DIR/scripts/omc_hub_push.py" --push -m "auto-sync: $BASENAME" 2>&1 || true
