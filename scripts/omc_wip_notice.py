#!/usr/bin/env python3
"""Provide a read-only Codex SessionStart hint for a saved local WIP."""
from __future__ import annotations

import json
import os
import stat
import subprocess
import sys
from pathlib import Path

from omc_context_save import _restore_history

_MAX_BYTES = 64 * 1024


def build_notice(root: Path) -> dict[str, object] | None:
    path = root / ".omc/wip/latest.json"
    try:
        if path.resolve(strict=True) != path or not path.is_file():
            return None
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
        with os.fdopen(fd, "rb") as file:
            if not stat.S_ISREG(os.fstat(file.fileno()).st_mode):
                return None
            raw = file.read(_MAX_BYTES + 1)
        if len(raw) > _MAX_BYTES:
            return None
        data = json.loads(raw)
    except (OSError, ValueError, RuntimeError):
        return None

    if not isinstance(data, dict) or "commit" not in data:
        return None
    saved_at = data.get("saved_at")
    if not isinstance(saved_at, str) or not saved_at.strip():
        return None

    _, relation = _restore_history(root, data["commit"])
    message = (
        f"이 worktree에 저장된 작업 메모가 있습니다. 저장 커밋과 현재 HEAD: {relation}. "
        "이어가려면 WIP를 복원하고 Superpowers 원장을 Git과 대조하세요."
    )
    return {
        "systemMessage": message,
        "hookSpecificOutput": {"hookEventName": "SessionStart", "additionalContext": message},
    }


def _same_worktree(root: Path, cwd: object) -> bool:
    if not isinstance(cwd, str) or not Path(cwd).is_absolute():
        return False
    directory = Path(cwd).resolve(strict=True)
    if not directory.is_relative_to(root):
        return False
    result = subprocess.run(
        ["git", "--no-optional-locks", "rev-parse", "--show-toplevel"],
        cwd=directory,
        capture_output=True,
        text=True,
        timeout=2,
        env={key: value for key, value in os.environ.items() if not key.startswith("GIT_")},
    )
    return result.returncode == 0 and Path(result.stdout.removesuffix("\n")).resolve() == root


def main(root: Path | None = None) -> int:
    try:
        raw = sys.stdin.buffer.read(_MAX_BYTES + 1)
        if len(raw) > _MAX_BYTES:
            return 0
        event = json.loads(raw)
        root = (root or Path(__file__).resolve().parents[1]).resolve()
        if (not isinstance(event, dict) or event.get("hook_event_name") != "SessionStart"
                or event.get("source") != "startup" or not _same_worktree(root, event.get("cwd"))):
            return 0
    except (OSError, ValueError, RuntimeError, subprocess.SubprocessError):
        return 0

    notice = build_notice(root)
    if notice is not None:
        print(json.dumps(notice, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
