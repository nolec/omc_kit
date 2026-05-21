#!/usr/bin/env python3
"""
omc_hub_push.py — 현재 프로젝트의 OMC 수정사항을 hub(omc_kit)에 동기화 + push

설정:
  .omc/hub.path 파일에 로컬 omc_kit 경로를 저장하세요. (.gitignore에 포함됨)
  예: echo "/path/to/omc_kit" > .omc/hub.path

사용:
  python3 scripts/omc_hub_push.py           # diff 확인 후 interactive
  python3 scripts/omc_hub_push.py --dry-run  # 변경 파일 목록만 출력
  python3 scripts/omc_hub_push.py --push     # 확인 없이 바로 commit + push
"""
from __future__ import annotations

import argparse
import filecmp
import shutil
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
HUB_PATH_FILE = ROOT / ".omc" / "hub.path"

SYNC_MAP: list[tuple[str, str]] = [
    *[(f"scripts/{p.name}", f"scripts/{p.name}") for p in (ROOT / "scripts").glob("omc_*.py")],
    *[
        (f".cursor/rules/{p.name}", f"templates/.cursor/rules/{p.name}")
        for p in (ROOT / ".cursor" / "rules").glob("omc-*.mdc")
    ],
    *[
        (f".cursor/rules/{p.name}", f"templates/.cursor/rules/{p.name}")
        for p in (ROOT / ".cursor" / "rules").glob("bm-*.mdc")
        if p.name not in {"bm-block-patterns.mdc"}  # 프로젝트 전용 제외
    ],
    ("AGENTS.md", "templates/AGENTS.md"),
    ("CLAUDE.md", "templates/CLAUDE.md"),
    ("GEMINI.md", "templates/GEMINI.md"),
    ("CODEX.md",  "templates/CODEX.md"),
    *[
        (f".agent/skills/{p.parent.name}/{p.name}", f"templates/.agent/skills/{p.parent.name}/{p.name}")
        for p in (ROOT / ".agent" / "skills").rglob("*.md")
        if (ROOT / ".agent" / "skills").exists()
    ],
    *[
        (f".codex/commands/{p.name}", f"templates/.codex/commands/{p.name}")
        for p in (ROOT / ".codex" / "commands").glob("*.md")
        if (ROOT / ".codex" / "commands").exists()
    ],
    *[
        (f".gemini/{p.relative_to(ROOT / '.gemini')}", f"templates/.gemini/{p.relative_to(ROOT / '.gemini')}")
        for p in (ROOT / ".gemini").rglob("*")
        if p.is_file() and (ROOT / ".gemini").exists()
    ],
]


def _read_hub() -> Path | None:
    if not HUB_PATH_FILE.exists():
        return None
    path = HUB_PATH_FILE.read_text(encoding="utf-8").strip()
    return Path(path) if path else None


def _diff_pairs(hub: Path) -> list[tuple[Path, Path, bool]]:
    result = []
    for live_rel, hub_rel in SYNC_MAP:
        live = ROOT / live_rel
        dst = hub / hub_rel
        if not live.exists():
            continue
        is_new = not dst.exists()
        changed = is_new or not filecmp.cmp(live, dst, shallow=False)
        if changed:
            result.append((live, dst, is_new))
    return result


def _copy(pairs: list[tuple[Path, Path, bool]]) -> None:
    for live, dst, _ in pairs:
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(live, dst)


def _git_push(hub: Path, commit_msg: str) -> bool:
    for cmd in (
        ["git", "add", "-A"],
        ["git", "commit", "-m", commit_msg],
        ["git", "push"],
    ):
        result = subprocess.run(cmd, cwd=hub, capture_output=True, text=True)
        if result.returncode != 0 and cmd[1] == "commit" and "nothing to commit" in result.stdout:
            print("[hub] 커밋할 변경사항 없음 (이미 최신)")
            return True
        if result.returncode != 0:
            print(f"[hub] {' '.join(cmd)} 실패:\n{result.stderr.strip()}")
            return False
    return True


def main() -> int:
    ap = argparse.ArgumentParser(description="OMC hub push")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--push", action="store_true")
    ap.add_argument("--message", "-m", default="")
    args = ap.parse_args()

    hub = _read_hub()
    if not hub:
        print("[hub] .omc/hub.path 파일이 없습니다.")
        print("     설정: echo '/path/to/omc_kit' > .omc/hub.path")
        return 1
    if not hub.is_dir():
        print(f"[hub] hub 경로 없음: {hub}")
        return 1

    print(f"[hub] hub: {hub}")
    pairs = _diff_pairs(hub)

    if not pairs:
        print("[hub] 모두 최신 상태")
        return 0

    print(f"[hub] 변경 {len(pairs)}개:")
    for live, dst, is_new in pairs:
        print(f"  [{'NEW' if is_new else 'MOD'}] {live.relative_to(ROOT)}  →  {dst.relative_to(hub)}")

    if args.dry_run:
        return 0

    if not args.push:
        try:
            ans = input("\nhub에 commit + push? [Y/n]: ").strip().lower()
        except (EOFError, KeyboardInterrupt):
            print("\n취소")
            return 0
        if ans not in ("", "y", "yes"):
            _copy(pairs)
            print("[hub] 파일 복사 완료 (push 건너뜀)")
            return 0

    _copy(pairs)
    print("[hub] 파일 복사 완료")

    names = [live.name for live, _, _ in pairs]
    msg = args.message or f"sync: {', '.join(names[:3])}{'...' if len(names) > 3 else ''}"

    if _git_push(hub, msg):
        print(f"[hub] push 완료 → {hub}")
    else:
        print("[hub] 파일 복사는 됐으나 push 실패. hub에서 수동 push 필요.")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
