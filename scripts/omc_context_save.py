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
import re
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


def _restore_history(
    root: Path, saved_commit: object, *, ignore_command_config: bool = False
) -> tuple[str, str]:
    """Return current HEAD and its relationship to the saved object ID."""
    git_env = {**os.environ, "GIT_NO_LAZY_FETCH": "1"}
    if ignore_command_config:
        # Session hooks can inherit Git -c settings from the host launcher.
        for name in tuple(git_env):
            if name in ("GIT_CONFIG_COUNT", "GIT_CONFIG_PARAMETERS") or name.startswith(
                ("GIT_CONFIG_KEY_", "GIT_CONFIG_VALUE_")
            ):
                del git_env[name]

    def git(*args: str) -> subprocess.CompletedProcess:
        return subprocess.run(
            ["git", "--no-replace-objects", "--no-optional-locks", *args],
            cwd=root, capture_output=True, text=True, timeout=5,
            env=git_env,
        )

    unknown = "확인 불가"
    head = ""
    try:
        if any(name in git_env for name in (
            "GIT_DIR", "GIT_WORK_TREE", "GIT_COMMON_DIR", "GIT_OBJECT_DIRECTORY",
            "GIT_ALTERNATE_OBJECT_DIRECTORIES", "GIT_NAMESPACE", "GIT_GRAFT_FILE",
            "GIT_SHALLOW_FILE", "GIT_CONFIG_COUNT", "GIT_CONFIG_PARAMETERS",
        )):
            return head, unknown
        current = git("rev-parse", "--verify", "HEAD^{commit}")
        if current.returncode != 0:
            return head, unknown
        head = current.stdout.strip()
        if not isinstance(saved_commit, str) or not re.fullmatch(r"[a-f0-9]{4,64}", saved_commit):
            return head, unknown
        # Resolve old abbreviated IDs as objects, independently of similarly named refs.
        matches = git("rev-parse", "--disambiguate=" + saved_commit)
        objects = matches.stdout.splitlines()
        if matches.returncode != 0 or len(objects) != 1:
            return head, unknown
        saved = objects[0]
        kind = git("cat-file", "-t", saved)
        if kind.returncode != 0 or kind.stdout.strip() != "commit":
            return head, unknown
        if saved == head:
            return head, "같은 지점"
        grafts = git("rev-parse", "--git-path", "info/grafts")
        if grafts.returncode != 0 or os.path.lexists(root / grafts.stdout.removesuffix("\n")):
            return head, unknown
        ancestry = git("merge-base", "--is-ancestor", saved, head)
        if ancestry.returncode == 0:
            return head, "현재 이력이 앞섬"
        if ancestry.returncode == 1:
            shallow = git("rev-parse", "--is-shallow-repository")
            if shallow.returncode == 0 and shallow.stdout.strip() == "false":
                return head, "현재 이력에 없음"
    except (OSError, ValueError, subprocess.SubprocessError):
        pass
    return head, unknown


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
    head, relationship = _restore_history(root, commit, ignore_command_config=True)
    print(f"현재 HEAD  : {head or '확인 불가'}")
    print(f"저장 커밋과 현재 HEAD: {relationship}")

    decisions = data.get("decisions", "")
    remaining = data.get("remaining", "")
    failed = data.get("failed_approaches", "")

    if decisions:
        print(f"\n결정 사항:\n  {decisions}")
    if remaining:
        print(f"\n남은 작업 (저장 당시 메모):\n  {remaining}")
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
