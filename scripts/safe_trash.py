#!/usr/bin/env python3
from __future__ import annotations

import argparse
import platform
import shutil
import subprocess
import sys
from pathlib import Path


def _move_to_trash(path: Path) -> tuple[bool, str]:
    resolved = path.expanduser().resolve()
    if not resolved.exists():
        return False, f"[SKIP] not found: {resolved}"
    if platform.system() == "Darwin":
        cmd = ["osascript", "-e", f'tell application "Finder" to delete POSIX file "{resolved}"']
        result = subprocess.run(cmd, capture_output=True, text=True)
        if result.returncode == 0:
            return True, f"[OK] moved to Trash: {resolved}"
        return False, f"[FAIL] {resolved}: {result.stderr.strip() or result.stdout.strip()}"
    trash_dir = Path.home() / ".Trash"
    trash_dir.mkdir(parents=True, exist_ok=True)
    target = trash_dir / resolved.name
    suffix = 1
    while target.exists():
        target = trash_dir / f"{resolved.name}.{suffix}"
        suffix += 1
    shutil.move(str(resolved), str(target))
    return True, f"[OK] moved to {target}"


def main() -> int:
    parser = argparse.ArgumentParser(description="Move files/directories to Trash safely.")
    parser.add_argument("paths", nargs="+", help="Paths to move to Trash.")
    args = parser.parse_args()

    ok_count = 0
    fail_count = 0
    for raw in args.paths:
        ok, message = _move_to_trash(Path(raw))
        print(message)
        if ok:
            ok_count += 1
        else:
            fail_count += 1
    if fail_count:
        print(f"[SUMMARY] success={ok_count} failed={fail_count}")
        return 1
    print(f"[SUMMARY] success={ok_count} failed=0")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
