#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path


def _load_latest_state(repo_root: Path) -> dict:
    latest_path = repo_root / ".omc" / "state" / "latest.json"
    return json.loads(latest_path.read_text(encoding="utf-8"))


def _load_session(repo_root: Path, session_id: str) -> dict:
    session_path = repo_root / ".omc" / "state" / "sessions" / session_id / "session.json"
    return json.loads(session_path.read_text(encoding="utf-8"))


def _status_value(value: object) -> object:
    if isinstance(value, dict):
        return value.get("status")
    return value


def _assert_session(repo_root: Path, request: str) -> tuple[str, dict]:
    latest = _load_latest_state(repo_root)
    session_id = latest.get("latest_confirmed_session_id")
    if not session_id:
        raise RuntimeError("latest_confirmed_session_id is empty")
    session = _load_session(repo_root, session_id)
    if session.get("request") != request:
        raise RuntimeError(f"session request mismatch: expected={request!r} actual={session.get('request')!r}")
    if _status_value(session.get("confirmation")) != "confirmed":
        raise RuntimeError(f"confirmation mismatch: {session.get('confirmation')!r}")
    if _status_value(session.get("lifecycle")) != "active":
        raise RuntimeError(f"lifecycle mismatch: {session.get('lifecycle')!r}")
    return session_id, session


def main() -> int:
    ap = argparse.ArgumentParser(description="Smoke test OMC chat loop with headless executor.")
    ap.add_argument("--target", type=Path, default=Path.cwd(), help="Target repository root.")
    ap.add_argument("--executor", choices=["codex", "gemini"], required=True)
    ap.add_argument("--timeout-sec", type=int, default=240)
    ap.add_argument("--exec-timeout-sec", type=int, default=90)
    args = ap.parse_args()

    repo_root = args.target.resolve()
    requests = [
        f"omc chat headless smoke step1 via {args.executor}",
        f"omc chat headless smoke step2 via {args.executor}",
        "/exit",
    ]
    env = os.environ.copy()
    env["OMC_EXEC_TIMEOUT_SEC"] = str(args.exec_timeout_sec)
    try:
        proc = subprocess.run(
            ["./run", "omc-chat", "--headless", "--executor", args.executor],
            input="\n".join(requests) + "\n",
            cwd=str(repo_root),
            capture_output=True,
            text=True,
            timeout=args.timeout_sec,
            check=False,
            env=env,
        )
    except subprocess.TimeoutExpired as exc:
        print(
            f"SMOKE_FAILED: timeout={args.timeout_sec}s "
            f"executor={args.executor} exec_timeout={args.exec_timeout_sec}s",
            file=sys.stderr,
        )
        if exc.stdout:
            print(exc.stdout.strip(), file=sys.stderr)
        if exc.stderr:
            print(exc.stderr.strip(), file=sys.stderr)
        return 1

    stdout = proc.stdout
    stderr = proc.stderr
    if proc.returncode != 0:
        print(f"SMOKE_FAILED: exit_code={proc.returncode}", file=sys.stderr)
        if stdout.strip():
            print(stdout.strip(), file=sys.stderr)
        if stderr.strip():
            print(stderr.strip(), file=sys.stderr)
        return 1

    prompt_hits = stdout.count("omc> ")
    if prompt_hits < 3:
        print(f"SMOKE_FAILED: expected at least 3 prompts, got {prompt_hits}", file=sys.stderr)
        if stdout.strip():
            print(stdout.strip(), file=sys.stderr)
        return 1

    session_id, session = _assert_session(repo_root, requests[1])
    print(
        "SMOKE_OK "
        f"executor={args.executor} "
        f"prompts={prompt_hits} "
        f"latest_confirmed_session_id={session_id} "
        f"lifecycle={_status_value(session.get('lifecycle'))}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
