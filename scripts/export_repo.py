#!/usr/bin/env python3
from __future__ import annotations

import argparse
import shutil
import subprocess
from pathlib import Path


def _kit_root() -> Path:
    # omc_kit/scripts/export_repo.py -> kit root is parent of scripts/
    return Path(__file__).resolve().parents[1]


def _run(cmd: list[str], *, cwd: Path) -> None:
    subprocess.run(cmd, cwd=str(cwd), check=True)


def _ignore(_: str, names: list[str]) -> set[str]:
    ignored = {
        ".DS_Store",
        "__pycache__",
        ".pytest_cache",
        ".mypy_cache",
        ".ruff_cache",
    }
    return {n for n in names if n in ignored}


def main() -> int:
    ap = argparse.ArgumentParser(
        description="Export omc_kit as a standalone git repository (no network)."
    )
    ap.add_argument("--dest", type=Path, required=True, help="Destination directory to create.")
    ap.add_argument("--force", action="store_true", help="Overwrite destination if it exists.")
    ap.add_argument("--init-git", action="store_true", default=True, help="Initialize git repository.")
    ap.add_argument("--no-init-git", action="store_false", dest="init_git", help="Do not init git.")
    ap.add_argument("--commit", action="store_true", default=True, help="Create initial commit.")
    ap.add_argument("--no-commit", action="store_false", dest="commit", help="Do not commit.")
    ap.add_argument("--message", type=str, default="init: multi assistant kit", help="Initial commit message.")
    args = ap.parse_args()

    src = _kit_root()
    dest = args.dest.resolve()

    if dest.exists():
        if not args.force:
            raise FileExistsError(dest)
        shutil.rmtree(dest)

    shutil.copytree(src, dest, ignore=_ignore)

    if args.init_git:
        _run(["git", "init"], cwd=dest)
        _run(["git", "add", "-A"], cwd=dest)
        if args.commit:
            _run(["git", "commit", "-m", args.message], cwd=dest)

    print(f"Exported kit repo to: {dest}")
    if args.init_git:
        print("Next:")
        print(f"- cd {dest}")
        print("- (optional) git remote add origin <your-remote-url>")
        print("- (optional) git push -u origin main")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

