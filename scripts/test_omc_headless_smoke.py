#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
import uuid
from pathlib import Path


def _project_root(target: Path | None = None) -> Path:
    return (target or Path.cwd()).resolve()


def _read_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def _latest_meta(project_root: Path) -> dict:
    return _read_json(project_root / ".omc" / "state" / "latest.json")


def _session_json(project_root: Path, session_id: str) -> dict:
    return _read_json(project_root / ".omc" / "state" / "sessions" / session_id / "session.json")


def _build_request(executor: str) -> str:
    nonce = uuid.uuid4().hex[:8]
    return f"OMC headless smoke {executor} {nonce}. 파일 수정 없이 현재 상태 한 줄 요약만 출력해."


def _status_value(value: object) -> object:
    if isinstance(value, dict):
        return value.get("status")
    return value


def _run_smoke(project_root: Path, *, executor: str, timeout_sec: int) -> subprocess.CompletedProcess[str]:
    request = _build_request(executor)
    cmd = ["./run", "omc", "--headless", "--executor", executor, request]
    started = time.time()
    timed_out = False
    try:
        proc = subprocess.run(
            cmd,
            cwd=str(project_root),
            check=False,
            capture_output=True,
            text=True,
            timeout=timeout_sec,
        )
    except subprocess.TimeoutExpired as exc:
        timed_out = True
        proc = subprocess.CompletedProcess(
            exc.cmd,
            124,
            exc.stdout if isinstance(exc.stdout, str) else "",
            exc.stderr if isinstance(exc.stderr, str) else "",
        )
    elapsed = round(time.time() - started, 2)

    latest = _latest_meta(project_root)
    latest_session_id = str(latest.get("latest_session_id") or "")
    latest_confirmed_session_id = str(latest.get("latest_confirmed_session_id") or "")
    latest_session = _session_json(project_root, latest_session_id) if latest_session_id else {}

    failures: list[str] = []
    if timed_out:
        failures.append(f"timeout={timeout_sec}s")
    if proc.returncode != 0:
        failures.append(f"exit_code={proc.returncode}")
    if latest.get("latest_request") != request:
        failures.append("latest_request_mismatch")
    if latest.get("latest_confirmed_request") != request:
        failures.append("latest_confirmed_request_mismatch")
    if latest_session_id != latest_confirmed_session_id:
        failures.append("latest_confirmed_session_mismatch")
    if _status_value(latest_session.get("confirmation")) != "confirmed":
        failures.append("session_not_confirmed")
    if _status_value(latest_session.get("lifecycle")) != "active":
        failures.append("session_not_active")

    print(f"[executor] {executor}")
    print(f"[elapsed_sec] {elapsed}")
    print(f"[request] {request}")
    print(f"[exit_code] {proc.returncode}")
    print(f"[latest_session_id] {latest_session_id}")
    print(f"[latest_confirmed_session_id] {latest_confirmed_session_id}")
    print(f"[latest_confirmation] {_status_value(latest_session.get('confirmation'))}")
    print(f"[latest_lifecycle] {_status_value(latest_session.get('lifecycle'))}")
    stdout = proc.stdout.strip()
    stderr = proc.stderr.strip()
    if stdout:
        print("[stdout_tail]")
        print("\n".join(stdout.splitlines()[-20:]))
    if stderr:
        print("[stderr_tail]")
        print("\n".join(stderr.splitlines()[-20:]), file=sys.stderr)

    if failures:
        raise SystemExit("SMOKE_FAILED: " + ", ".join(failures))

    print("SMOKE_OK")
    return proc


def main() -> int:
    ap = argparse.ArgumentParser(description="Run a headless OMC smoke test and verify latest confirmed session state.")
    ap.add_argument("--target", type=Path, default=Path.cwd(), help="Target repository root.")
    ap.add_argument("--executor", choices=["codex", "gemini"], required=True, help="Executor to test.")
    ap.add_argument("--timeout-sec", type=int, default=180, help="Timeout for the wrapped headless OMC command.")
    args = ap.parse_args()

    project_root = _project_root(args.target)
    _run_smoke(project_root, executor=args.executor, timeout_sec=args.timeout_sec)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
