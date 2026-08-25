#!/usr/bin/env python3
from __future__ import annotations

import json
import math
import os
from pathlib import Path
import signal
import subprocess
import sys
import tempfile
import time
from typing import Any


PROTOCOL = "omc-provider/v1"
BACKEND_PROTOCOL = "omc-provider-backend/v1"


def _error(reason_code: str) -> dict[str, Any]:
    return {"status": "blocked", "reason_code": reason_code}


def _kill_process_group(proc: subprocess.Popen[Any]) -> None:
    try:
        os.killpg(proc.pid, signal.SIGKILL)
    except OSError:
        pass


def _backend_path() -> Path | None:
    value = os.environ.get("OMC_PROVIDER_BACKEND", "").strip()
    if not value:
        return None
    path = Path(value).expanduser().resolve(strict=False)
    if not path.is_file() or not os.access(path, os.X_OK):
        return None
    return path


def _invoke_bounded(
    backend: Path,
    action: str,
    *,
    input_text: str,
    cwd: Path,
    timeout_sec: float,
    max_response_bytes: int,
) -> dict[str, Any] | None:
    try:
        with tempfile.TemporaryDirectory(prefix="omc-provider-backend-") as raw_temp:
            root = Path(raw_temp)
            input_path = root / "input.json"
            stdout_path = root / "stdout"
            stderr_path = root / "stderr"
            input_path.write_text(input_text, encoding="utf-8")
            with (
                input_path.open("rb") as input_file,
                stdout_path.open("wb") as stdout_file,
                stderr_path.open("wb") as stderr_file,
            ):
                proc = subprocess.Popen(
                    [str(backend), action],
                    stdin=input_file,
                    stdout=stdout_file,
                    stderr=stderr_file,
                    cwd=str(cwd),
                    start_new_session=True,
                )
                deadline = time.monotonic() + timeout_sec
                limit_exceeded = False
                timed_out = False
                while proc.poll() is None:
                    if stdout_path.stat().st_size + stderr_path.stat().st_size > max_response_bytes:
                        limit_exceeded = True
                        _kill_process_group(proc)
                        break
                    if time.monotonic() >= deadline:
                        timed_out = True
                        _kill_process_group(proc)
                        break
                    time.sleep(0.01)
                proc.wait()
            size = stdout_path.stat().st_size + stderr_path.stat().st_size
            return {
                "returncode": int(proc.returncode),
                "stdout": stdout_path.read_bytes()[: max_response_bytes + 1].decode(
                    "utf-8", errors="replace"
                ),
                "stderr": stderr_path.read_bytes()[: max_response_bytes + 1].decode(
                    "utf-8", errors="replace"
                ),
                "limit_exceeded": limit_exceeded or size > max_response_bytes,
                "timed_out": timed_out,
            }
    except OSError:
        return None


def _capabilities(backend: Path) -> dict[str, Any] | None:
    completed = _invoke_bounded(
        backend,
        "capabilities",
        input_text="",
        cwd=backend.parent,
        timeout_sec=10.0,
        max_response_bytes=64 * 1024,
    )
    if (
        completed is None
        or completed["returncode"] != 0
        or completed["timed_out"]
        or completed["limit_exceeded"]
    ):
        return None
    try:
        payload = json.loads(completed["stdout"])
    except json.JSONDecodeError:
        return None
    if not isinstance(payload, dict) or payload.get("protocol") != BACKEND_PROTOCOL:
        return None
    return payload


def _valid_positive_number(value: Any) -> bool:
    return (
        isinstance(value, (int, float))
        and not isinstance(value, bool)
        and math.isfinite(float(value))
        and float(value) > 0
    )


def _validate_request(payload: Any) -> dict[str, Any] | None:
    if not isinstance(payload, dict) or payload.get("protocol") != PROTOCOL:
        return None
    if not isinstance(payload.get("prompt"), str) or not payload["prompt"]:
        return None
    if not isinstance(payload.get("executor"), str) or not payload["executor"]:
        return None
    if not _valid_positive_number(payload.get("timeout_sec")):
        return None
    for field in ("max_total_tokens", "max_output_chars"):
        value = payload.get(field)
        if not isinstance(value, int) or isinstance(value, bool) or value <= 0:
            return None
    root = payload.get("project_root")
    if not isinstance(root, str) or not Path(root).resolve(strict=False).is_dir():
        return None
    return payload


def _validate_result(result: Any, request: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(result, dict):
        return {"returncode": 65, "output": "backend result invalid", "reason_code": "backend_result_invalid"}
    output = result.get("output")
    usage = result.get("token_usage")
    if not isinstance(output, str):
        return {"returncode": 65, "output": "backend output invalid", "reason_code": "backend_output_invalid"}
    if len(output) > request["max_output_chars"]:
        return {"returncode": 65, "output": "backend output limit exceeded", "reason_code": "backend_output_limit_violated"}
    if not isinstance(usage, dict):
        return {"returncode": 65, "output": "backend usage missing", "reason_code": "backend_usage_missing"}
    fields = ("input_tokens", "output_tokens", "total_tokens")
    if any(
        not isinstance(usage.get(field), int)
        or isinstance(usage.get(field), bool)
        or usage[field] < 0
        for field in fields
    ) or usage["total_tokens"] != usage["input_tokens"] + usage["output_tokens"]:
        return {"returncode": 65, "output": "backend usage invalid", "reason_code": "backend_usage_invalid"}
    if usage["total_tokens"] > request["max_total_tokens"]:
        return {"returncode": 65, "output": "backend token limit exceeded", "reason_code": "backend_token_limit_violated"}
    return result


def capabilities() -> int:
    backend = _backend_path()
    if backend is None:
        print(json.dumps(_error("backend_unavailable"), sort_keys=True))
        return 2
    payload = _capabilities(backend)
    if (
        payload is None
        or payload.get("hard_total_token_limit") is not True
        or payload.get("hard_output_limit") is not True
    ):
        print(json.dumps(_error("backend_hard_limit_unsupported"), sort_keys=True))
        return 2
    print(
        json.dumps(
            {
                "protocol": PROTOCOL,
                "hard_total_token_limit": True,
                "hard_output_limit": True,
            },
            sort_keys=True,
        )
    )
    return 0


def execute() -> int:
    backend = _backend_path()
    if backend is None:
        print(json.dumps({"returncode": 69, "output": "backend unavailable", "reason_code": "backend_unavailable"}))
        return 0
    capabilities_payload = _capabilities(backend)
    if (
        capabilities_payload is None
        or capabilities_payload.get("hard_total_token_limit") is not True
        or capabilities_payload.get("hard_output_limit") is not True
    ):
        print(json.dumps({"returncode": 69, "output": "backend hard limit unsupported", "reason_code": "backend_hard_limit_unsupported"}))
        return 0
    try:
        request = _validate_request(json.load(sys.stdin))
    except (UnicodeError, json.JSONDecodeError):
        request = None
    if request is None:
        print(json.dumps({"returncode": 64, "output": "provider request invalid", "reason_code": "provider_request_invalid"}))
        return 0
    completed = _invoke_bounded(
        backend,
        "execute",
        input_text=json.dumps(request, ensure_ascii=False),
        cwd=Path(request["project_root"]),
        timeout_sec=float(request["timeout_sec"]),
        max_response_bytes=max(4096, int(request["max_output_chars"]) * 6 + 4096),
    )
    if completed is None:
        print(json.dumps({"returncode": 70, "output": "backend unavailable", "reason_code": "backend_unavailable"}))
        return 0
    if completed["limit_exceeded"]:
        print(json.dumps({"returncode": 65, "output": "backend response limit exceeded", "reason_code": "backend_response_limit_violated"}))
        return 0
    if completed["timed_out"]:
        print(json.dumps({"returncode": 124, "output": "backend timeout", "reason_code": "backend_timeout"}))
        return 0
    if completed["returncode"] != 0:
        print(json.dumps({"returncode": completed["returncode"], "output": completed["stderr"] or completed["stdout"], "reason_code": "backend_failed"}))
        return 0
    try:
        result = json.loads(completed["stdout"])
    except json.JSONDecodeError:
        result = None
    print(json.dumps(_validate_result(result, request), ensure_ascii=False, sort_keys=True))
    return 0


def main(argv: list[str] | None = None) -> int:
    args = sys.argv[1:] if argv is None else argv
    if args == ["capabilities"]:
        return capabilities()
    if args == ["execute"]:
        return execute()
    print(json.dumps(_error("adapter_action_invalid"), sort_keys=True))
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
