#!/usr/bin/env python3
from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

DEFAULT_PROFILE = [
    "observed-collect",
    "observed-collect-reverse",
    "observed-ready-surface",
]


def expand_task_ids(
    *,
    profile: str,
    repeats: int,
    task_ids: list[str] | None = None,
) -> list[str]:
    if task_ids:
        return [task_id for task_id in task_ids if str(task_id).strip()]
    if profile != "default":
        raise ValueError(f"unknown profile: {profile}")
    if repeats < 1:
        raise ValueError("repeats must be >= 1")
    expanded: list[str] = []
    for _ in range(repeats):
        expanded.extend(DEFAULT_PROFILE)
    return expanded


def _task_path(root: Path, task_id: str) -> Path:
    return root / ".omc" / "tasks" / f"{task_id}.json"


def run_plan(*, root: Path, task_ids: list[str], dry_run: bool) -> int:
    print("Observed Collect Batch")
    for index, task_id in enumerate(task_ids, start=1):
        print(f"{index}. {task_id}")
    if dry_run:
        return 0

    for index, task_id in enumerate(task_ids, start=1):
        task_path = _task_path(root, task_id)
        if not task_path.exists():
            print(f"[BATCH] missing task: {task_path}")
            return 1
        print(f"\n[BATCH] ({index}/{len(task_ids)}) running {task_id}")
        proc = subprocess.run(
            [
                sys.executable,
                "scripts/omc_autopilot.py",
                "run",
                "--task",
                str(task_path),
            ],
            cwd=str(root),
            check=False,
        )
        if proc.returncode != 0:
            print(f"[BATCH] failed: {task_id} (rc={proc.returncode})")
            return proc.returncode
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run observed collection tasks in batch.")
    parser.add_argument("--target", type=Path, default=Path.cwd(), help="Repository root.")
    parser.add_argument("--profile", default="default", help="Batch profile name.")
    parser.add_argument("--repeats", type=int, default=1, help="How many times to repeat the profile.")
    parser.add_argument("--task-id", action="append", dest="task_ids", help="Custom task id. Repeatable.")
    parser.add_argument("--dry-run", action="store_true", help="Print task order only.")
    args = parser.parse_args(argv)

    task_ids = expand_task_ids(
        profile=args.profile,
        repeats=args.repeats,
        task_ids=args.task_ids,
    )
    return run_plan(root=args.target, task_ids=task_ids, dry_run=args.dry_run)


if __name__ == "__main__":
    raise SystemExit(main())
