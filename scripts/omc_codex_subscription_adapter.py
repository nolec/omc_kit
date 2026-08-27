#!/usr/bin/env python3
from __future__ import annotations

import json
import os
from pathlib import Path
import shutil
import signal
import subprocess
import sys
import tempfile
import time
from typing import Any


PROTOCOL = "omc-provider/v1"
EXECUTION_PROFILE = "subscription_bounded"
DEFAULT_CODEX_BINARY = Path("/Applications/ChatGPT.app/Contents/Resources/codex")
MAX_JSONL_BYTES = 4 * 1024 * 1024
CAPABILITIES = {
    "protocol": PROTOCOL,
    "execution_profile": EXECUTION_PROFILE,
    "authentication": "chatgpt_subscription",
    "hard_total_token_limit": False,
    "hard_output_limit": True,
    "token_usage_mode": "observed_post_call",
    "hard_bounds": ["elapsed_time", "output_chars", "process_group"],
}


def _blocked(reason_code: str, message: str, *, returncode: int = 69) -> dict[str, Any]:
    return {"returncode": returncode, "output": message, "reason_code": reason_code}


def _codex_binary() -> Path | None:
    configured = os.environ.get("OMC_CODEX_BINARY", "").strip()
    candidate = Path(configured).expanduser() if configured else DEFAULT_CODEX_BINARY
    if not configured and not candidate.is_file():
        resolved = shutil.which("codex")
        candidate = Path(resolved) if resolved else candidate
    candidate = candidate.resolve(strict=False)
    if not candidate.is_file() or not os.access(candidate, os.X_OK):
        return None
    return candidate


def _subscription_environment() -> dict[str, str]:
    environment = dict(os.environ)
    for name in ("OPENAI_API_KEY", "CODEX_API_KEY"):
        environment.pop(name, None)
    return environment


def _kill_process_group(process: subprocess.Popen[Any]) -> None:
    try:
        os.killpg(process.pid, signal.SIGKILL)
    except OSError:
        pass


def _run_bounded(
    command: list[str],
    *,
    cwd: Path,
    timeout_sec: float,
    max_response_bytes: int,
    input_text: str = "",
) -> dict[str, Any]:
    with tempfile.TemporaryDirectory(prefix="omc-codex-subscription-") as raw_temp:
        temp_root = Path(raw_temp)
        stdout_path = temp_root / "stdout"
        stderr_path = temp_root / "stderr"
        input_path = temp_root / "stdin"
        input_path.write_text(input_text, encoding="utf-8")
        try:
            with (
                input_path.open("rb") as input_file,
                stdout_path.open("wb") as stdout_file,
                stderr_path.open("wb") as stderr_file,
            ):
                process = subprocess.Popen(
                    command,
                    cwd=cwd,
                    stdin=input_file,
                    stdout=stdout_file,
                    stderr=stderr_file,
                    env=_subscription_environment(),
                    start_new_session=True,
                )
                deadline = time.monotonic() + timeout_sec
                timed_out = False
                limit_exceeded = False
                while process.poll() is None:
                    response_size = stdout_path.stat().st_size + stderr_path.stat().st_size
                    if response_size > max_response_bytes:
                        limit_exceeded = True
                        _kill_process_group(process)
                        break
                    if time.monotonic() >= deadline:
                        timed_out = True
                        _kill_process_group(process)
                        break
                    time.sleep(0.01)
                process.wait()
        except OSError:
            return {
                "returncode": 70,
                "stdout": "",
                "stderr": "",
                "timed_out": False,
                "limit_exceeded": False,
            }
        response_size = stdout_path.stat().st_size + stderr_path.stat().st_size
        return {
            "returncode": int(process.returncode),
            "stdout": stdout_path.read_bytes()[: max_response_bytes + 1].decode(
                "utf-8", errors="replace"
            ),
            "stderr": stderr_path.read_bytes()[: max_response_bytes + 1].decode(
                "utf-8", errors="replace"
            ),
            "timed_out": timed_out,
            "limit_exceeded": limit_exceeded or response_size > max_response_bytes,
        }


def _chatgpt_login_available(binary: Path, *, timeout_sec: float = 10) -> bool:
    result = _run_bounded(
        [str(binary), "login", "status"],
        cwd=binary.parent,
        timeout_sec=timeout_sec,
        max_response_bytes=16 * 1024,
    )
    output = "\n".join((result["stdout"], result["stderr"]))
    return (
        result["returncode"] == 0
        and not result["timed_out"]
        and not result["limit_exceeded"]
        and "logged in using chatgpt" in output.lower()
    )


def _validate_request(payload: Any) -> dict[str, Any] | None:
    if not isinstance(payload, dict) or payload.get("protocol") != PROTOCOL:
        return None
    if payload.get("executor") != "codex":
        return None
    if not isinstance(payload.get("prompt"), str) or not payload["prompt"]:
        return None
    root = payload.get("project_root")
    if not isinstance(root, str) or not Path(root).resolve(strict=False).is_dir():
        return None
    timeout = payload.get("timeout_sec")
    if (
        not isinstance(timeout, (int, float))
        or isinstance(timeout, bool)
        or timeout <= 0
    ):
        return None
    for name in ("max_total_tokens", "max_output_chars"):
        value = payload.get(name)
        if not isinstance(value, int) or isinstance(value, bool) or value <= 0:
            return None
    return payload


def _parse_jsonl(raw: str, *, max_output_chars: int) -> dict[str, Any]:
    output: str | None = None
    usage: dict[str, Any] | None = None
    try:
        events = [json.loads(line) for line in raw.splitlines() if line.strip()]
    except json.JSONDecodeError:
        return _blocked("backend_response_invalid", "Codex JSONL response invalid", returncode=65)
    for event in events:
        if not isinstance(event, dict):
            continue
        item = event.get("item")
        if (
            event.get("type") == "item.completed"
            and isinstance(item, dict)
            and item.get("type") == "agent_message"
            and isinstance(item.get("text"), str)
        ):
            output = item["text"]
        if event.get("type") == "turn.completed" and isinstance(event.get("usage"), dict):
            usage = event["usage"]
    if output is None:
        return _blocked("backend_output_missing", "Codex output unavailable", returncode=65)
    if len(output) > max_output_chars:
        return _blocked(
            "backend_output_limit_violated",
            "Codex output exceeded the character limit",
            returncode=65,
        )
    if usage is None:
        return _blocked("backend_usage_missing", "Codex subscription usage unavailable", returncode=65)
    input_tokens = usage.get("input_tokens")
    output_tokens = usage.get("output_tokens")
    if any(
        not isinstance(value, int) or isinstance(value, bool) or value < 0
        for value in (input_tokens, output_tokens)
    ):
        return _blocked("backend_usage_invalid", "Codex subscription usage invalid", returncode=65)
    return {
        "returncode": 0,
        "output": output,
        "token_usage": {
            "input_tokens": input_tokens,
            "output_tokens": output_tokens,
            "total_tokens": input_tokens + output_tokens,
        },
        "token_usage_mode": "observed_post_call",
    }


def capabilities() -> int:
    binary = _codex_binary()
    if binary is None:
        print(json.dumps({"status": "blocked", "reason_code": "codex_binary_unavailable"}))
        return 2
    if not _chatgpt_login_available(binary):
        print(
            json.dumps(
                {
                    "status": "blocked",
                    "reason_code": "chatgpt_subscription_auth_unavailable",
                },
                sort_keys=True,
            )
        )
        return 2
    print(json.dumps(CAPABILITIES, sort_keys=True))
    return 0


def execute() -> int:
    binary = _codex_binary()
    if binary is None:
        print(json.dumps(_blocked("codex_binary_unavailable", "Codex binary unavailable")))
        return 0
    try:
        request = _validate_request(json.load(sys.stdin))
    except (UnicodeError, json.JSONDecodeError):
        request = None
    if request is None:
        print(json.dumps(_blocked("provider_request_invalid", "provider request invalid", returncode=64)))
        return 0
    deadline = time.monotonic() + float(request["timeout_sec"])
    login_timeout = min(10.0, max(0.001, deadline - time.monotonic()))
    if not _chatgpt_login_available(binary, timeout_sec=login_timeout):
        reason_code = (
            "backend_timeout"
            if time.monotonic() >= deadline
            else "chatgpt_subscription_auth_unavailable"
        )
        message = (
            "Codex subscription execution timed out"
            if reason_code == "backend_timeout"
            else "ChatGPT subscription authentication unavailable"
        )
        returncode = 124 if reason_code == "backend_timeout" else 69
        print(json.dumps(_blocked(reason_code, message, returncode=returncode)))
        return 0
    remaining_timeout = deadline - time.monotonic()
    if remaining_timeout <= 0:
        print(
            json.dumps(
                _blocked(
                    "backend_timeout",
                    "Codex subscription execution timed out",
                    returncode=124,
                )
            )
        )
        return 0
    root = Path(request["project_root"]).resolve()
    command = [
        str(binary),
        "exec",
        "--ephemeral",
        "--ignore-user-config",
        "--ignore-rules",
        "--skip-git-repo-check",
        "--sandbox",
        "workspace-write",
        "--json",
        "-C",
        str(root),
        "-",
    ]
    completed = _run_bounded(
        command,
        cwd=root,
        timeout_sec=remaining_timeout,
        max_response_bytes=max(MAX_JSONL_BYTES, request["max_output_chars"] * 8 + 65536),
        input_text=request["prompt"],
    )
    if completed["timed_out"]:
        result = _blocked("backend_timeout", "Codex subscription execution timed out", returncode=124)
    elif completed["limit_exceeded"]:
        result = _blocked("backend_response_limit_violated", "Codex JSONL response exceeded limit", returncode=65)
    elif completed["returncode"] != 0:
        result = _blocked("backend_failed", completed["stderr"] or "Codex subscription execution failed", returncode=completed["returncode"])
    else:
        result = _parse_jsonl(
            completed["stdout"],
            max_output_chars=request["max_output_chars"],
        )
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return 0


def main(argv: list[str] | None = None) -> int:
    args = sys.argv[1:] if argv is None else argv
    if args == ["capabilities"]:
        return capabilities()
    if args == ["execute"]:
        return execute()
    print(json.dumps({"status": "blocked", "reason_code": "adapter_action_invalid"}))
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
