"""
omc_context_save.py — WIP 작업 컨텍스트 구조화 저장/복원

사용법:
  python3 scripts/omc_context_save.py save [--decisions "..."] [--remaining "..."] [--failed "..."]
  python3 scripts/omc_context_save.py restore
  python3 scripts/omc_context_save.py status
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
from datetime import datetime, timezone
from pathlib import Path


def _project_root() -> Path:
    env_root = os.environ.get("OMC_PROJECT_ROOT")
    if env_root:
        return Path(env_root)
    return Path(__file__).parent.parent


def _wip_path(root: Path | None = None) -> Path:
    env_path = os.environ.get("OMC_WIP_PATH")
    if env_path:
        return Path(env_path)
    r = root or _project_root()
    return r / ".omc" / "wip" / "latest.json"


def _git_context(root: Path) -> dict:
    def _run(args: list[str]) -> str:
        try:
            result = subprocess.run(
                args, capture_output=True, text=True, cwd=str(root), timeout=5
            )
            return result.stdout.strip()
        except Exception:
            return ""

    return {
        "branch": _run(["git", "rev-parse", "--abbrev-ref", "HEAD"]),
        "commit": _run(["git", "rev-parse", "--short", "HEAD"]),
        "subject": _run(["git", "log", "-1", "--format=%s"]),
    }


def cmd_save(
    root: Path,
    decisions: str = "",
    remaining: str = "",
    failed_approaches: str = "",
) -> None:
    wip = _wip_path(root)
    wip.parent.mkdir(parents=True, exist_ok=True)

    git = _git_context(root)
    payload = {
        "saved_at": datetime.now(timezone.utc).isoformat(),
        "branch": git.get("branch", ""),
        "commit": git.get("commit", ""),
        "commit_subject": git.get("subject", ""),
        "decisions": decisions,
        "remaining": remaining,
        "failed_approaches": failed_approaches,
    }
    wip.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"[WIP] 저장 완료 → {wip}")
    if decisions:
        print(f"  결정 사항: {decisions[:80]}")
    if remaining:
        print(f"  남은 작업: {remaining[:80]}")
    if failed_approaches:
        print(f"  실패한 접근: {failed_approaches[:80]}")


def cmd_restore(root: Path) -> int:
    wip = _wip_path(root)
    if not wip.exists():
        print("[WIP] 저장된 컨텍스트가 없습니다.")
        print("      힌트: python3 scripts/omc_context_save.py save 로 저장하세요.")
        return 1

    data: dict = json.loads(wip.read_text(encoding="utf-8"))
    print("━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━")
    print("WIP 컨텍스트 복원")
    print("━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━")
    saved_at = data.get("saved_at", "")
    branch = data.get("branch", "")
    commit = data.get("commit", "")
    subject = data.get("commit_subject", "")

    if saved_at:
        print(f"저장 시각  : {saved_at}")
    if branch or commit:
        print(f"브랜치/커밋: {branch} @ {commit}  {subject}")

    decisions = data.get("decisions", "")
    remaining = data.get("remaining", "")
    failed = data.get("failed_approaches", "")

    if decisions:
        print(f"\n결정 사항:\n  {decisions}")
    if remaining:
        print(f"\n남은 작업:\n  {remaining}")
    if failed:
        print(f"\n실패한 접근:\n  {failed}")
    print("━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━")
    return 0


def cmd_status(root: Path) -> int:
    wip = _wip_path(root)
    if not wip.exists():
        print("[WIP] 저장된 컨텍스트 없음")
        return 0
    data: dict = json.loads(wip.read_text(encoding="utf-8"))
    saved_at = data.get("saved_at", "?")
    branch = data.get("branch", "?")
    remaining = data.get("remaining", "없음")
    print(f"[WIP] {saved_at[:19]}  브랜치: {branch}")
    print(f"      남은 작업: {remaining[:100]}")
    return 0


def main() -> None:
    parser = argparse.ArgumentParser(
        description="OMC WIP 작업 컨텍스트 저장/복원",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    sub = parser.add_subparsers(dest="cmd")

    p_save = sub.add_parser("save", help="현재 작업 컨텍스트를 저장합니다")
    p_save.add_argument("--decisions", default="", help="완료된 결정 사항")
    p_save.add_argument("--remaining", default="", help="남은 작업")
    p_save.add_argument("--failed", default="", help="실패한 접근 방법")

    sub.add_parser("restore", help="저장된 컨텍스트를 출력합니다")
    sub.add_parser("status", help="간단한 WIP 상태를 출력합니다")

    args = parser.parse_args()
    root = _project_root()

    if args.cmd == "save":
        cmd_save(
            root,
            decisions=args.decisions,
            remaining=args.remaining,
            failed_approaches=args.failed,
        )
    elif args.cmd == "restore":
        raise SystemExit(cmd_restore(root))
    elif args.cmd == "status":
        raise SystemExit(cmd_status(root))
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
