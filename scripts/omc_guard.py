#!/usr/bin/env python3
from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

import omc_state

sys.path.insert(0, str(Path(__file__).resolve().parent))
import omc_utils

MUTATING_ROLE_IDS = {"directive", "senior_coding"}
READ_ONLY_COMMAND_HINTS = (
    "status",
    "state status",
    "omc status",
    "omc_guard status",
    "omc_pipeline_guard",
    "omc_tdd_check",
    "omc_doctor",
    "doctor",
    "help",
    "logs",
    "tail",
    "cat",
    "sed",
    "rg",
    "grep",
    "ls",
    "find",
)

DESTRUCTIVE_COMMAND_PATTERNS = (
    r"(^|\s)rm(\s|$)",
    r"rm\s+-[^\n]*\br\b[^\n]*\bf\b",
    r"find\s+[^\n]*\s-delete(\s|$)",
    r"(^|\s)git\s+reset\s+--hard(\s|$)",
)

ALLOW_DESTRUCTIVE_FLAG = "#ALLOW_DESTRUCTIVE"


def _latest(project_root: Path) -> dict:
    return omc_state.read_latest(project_root)


def _policy(project_root: Path) -> dict:
    omc_state.init_state(project_root)
    return omc_state.read_policy(project_root)


def _confirmed_roles(latest: dict) -> set[str]:
    return {str(role).strip() for role in latest.get("latest_confirmed_roles", []) if str(role).strip()}


def _resolved_scope(command_name: str, requested_scope: str) -> str:
    if requested_scope != "auto":
        return requested_scope
    normalized = " ".join(command_name.lower().split())
    if normalized in READ_ONLY_COMMAND_HINTS:
        return "read"
    if any(normalized.startswith(f"{hint} ") for hint in READ_ONLY_COMMAND_HINTS):
        return "read"
    return "mutate"


def _has_mutating_role(roles: set[str]) -> bool:
    return bool(roles & MUTATING_ROLE_IDS)


def _is_destructive_command(command_name: str) -> bool:
    normalized = " ".join(command_name.lower().split())
    return any(re.search(pattern, normalized) for pattern in DESTRUCTIVE_COMMAND_PATTERNS)


def _run_tdd_check(project_root: Path, command_name: str) -> int:
    """TDD 체크 스크립트를 실행해 exit code를 반환한다."""
    import subprocess

    tdd_script = project_root / "scripts" / "omc_tdd_check.py"
    if not tdd_script.exists():
        return 0  # 스크립트 없으면 건너뜀

    # ship / git commit 은 staged 모드, 그 외 mutate 는 report-only (경고만)
    staged = command_name in ("git commit", "ship")
    args = ["python3", str(tdd_script)]
    if staged:
        args.append("--staged")
    else:
        args.append("--report-only")

    result = subprocess.run(args, cwd=str(project_root))
    return result.returncode


# 코드 변경을 수반하는 작업에서 TDD 체크를 강제로 실행할 커맨드 이름
_TDD_REQUIRED_COMMANDS = ("git commit", "ship", "deploy")


def require_confirmation(project_root: Path, *, command_name: str, scope: str = "auto") -> int:
    if _is_destructive_command(command_name) and ALLOW_DESTRUCTIVE_FLAG not in command_name:
        print(
            f"[OMC-GUARD] blocked: destructive command detected in `{command_name}`."
        )
        print(
            "[OMC-GUARD] policy: delete must go through Trash-safe flow by default."
        )
        print(
            "Use: python omc_kit/scripts/safe_trash.py <path1> <path2> ..."
        )
        print(
            f"If you really need destructive delete, append {ALLOW_DESTRUCTIVE_FLAG} with explicit user approval."
        )
        return 5

    policy = _policy(project_root)
    if not bool(policy.get("enforce_confirm", True)):
        print(f"[OMC-GUARD] bypassed by policy: `{command_name}`")
        return 0
    latest = _latest(project_root)
    latest_session_id = latest.get("latest_session_id")
    latest_confirmed_session_id = latest.get("latest_confirmed_session_id")
    if not latest_session_id:
        print(f"[OMC-GUARD] blocked: `{command_name}` requires an OMC-confirmed session first.")
        print("Run: ./run omc \"작업 요청\"")
        return 2
    if latest_session_id != latest_confirmed_session_id:
        try:
            omc_state.set_session_status(
                project_root,
                status="blocked",
                session_id=str(latest_session_id),
                reason=f"Blocked `{command_name}` until role confirmation is completed.",
            )
        except Exception:
            pass
        print(f"[OMC-GUARD] blocked: latest session `{latest_session_id}` is not confirmed.")
        print("Run: python scripts/omc.py state status")
        print("Then confirm by re-running `./run omc \"작업 요청\"` interactively or:")
        print("python scripts/omc.py state confirm --target .")
        return 3
    roles = _confirmed_roles(latest)
    resolved_scope = _resolved_scope(command_name, scope)
    if resolved_scope == "mutate" and not _has_mutating_role(roles):
        required = ",".join(sorted(MUTATING_ROLE_IDS))
        found = ",".join(sorted(roles)) or "none"
        print(
            f"[OMC-GUARD] blocked: `{command_name}` requires a mutating role "
            f"for scope={resolved_scope}."
        )
        print(f"[OMC-GUARD] found roles={found}; required any of={required}")
        print("Run: ./run omc \"작업 요청\" and confirm roles including directive or senior_coding.")
        return 4
    print(
        f"[OMC-GUARD] confirmed: session={latest_confirmed_session_id} "
        f"roles={','.join(sorted(roles))} scope={resolved_scope}"
    )

    # ── TDD 체크 — ship / git commit / deploy 는 물리적으로 차단 ──────────
    if command_name in _TDD_REQUIRED_COMMANDS:
        tdd_rc = _run_tdd_check(project_root, command_name)
        if tdd_rc != 0:
            print()
            print("===========================================================")
            print(" TDD GATE BLOCK")
            print(f" `{command_name}` 실행 차단: 테스트 없는 구현 파일이 있습니다.")
            print()
            print(" ─── 복구 절차 ───────────────────────────────────────────")
            print(" 1. 빠진 테스트 확인:")
            print("      python3 scripts/omc_tdd_check.py --staged")
            print(" 2. 각 파일에 대응 테스트 파일 작성 → FAIL 확인")
            print(" 3. RED 등록:")
            print("      python3 scripts/omc_pipeline_guard.py red-done <테스트파일>")
            print(" 4. 테스트 GREEN 확인 후 재시도")
            print()
            print(" ─── 예외 허용 (보고만, 차단 없음) ──────────────────────")
            print("      python3 scripts/omc_tdd_check.py --staged --report-only")
            print("===========================================================")
            return 6

    return 0


def _parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(description="Block execution unless the latest OMC session is confirmed.")
    sub = ap.add_subparsers(dest="command", required=True)

    require = sub.add_parser("require", help="Require a confirmed latest OMC session.")
    require.add_argument("--target", type=Path, default=Path.cwd(), help="Target repository root.")
    require.add_argument("--for", dest="command_name", required=True, help="Human-readable command name.")
    require.add_argument(
        "--scope",
        choices=["auto", "read", "mutate"],
        default="auto",
        help="Guard level. auto treats known read-only commands as read and everything else as mutate.",
    )

    status = sub.add_parser("status", help="Show latest and confirmed session summary.")
    status.add_argument("--target", type=Path, default=Path.cwd(), help="Target repository root.")
    return ap


def main() -> int:
    args = _parser().parse_args()
    project_root = omc_utils.project_root(args.target)
    if args.command == "require":
        return require_confirmation(project_root, command_name=args.command_name, scope=args.scope)
    latest = _latest(project_root)
    policy = _policy(project_root)
    print(f"latest_session_id={latest.get('latest_session_id')}")
    print(f"latest_confirmed_session_id={latest.get('latest_confirmed_session_id')}")
    print(f"latest_confirmed_roles={','.join(latest.get('latest_confirmed_roles', []))}")
    print(f"active_run_id={latest.get('active_run_id')}")
    print(f"latest_run_id={latest.get('latest_run_id')}")
    print(f"enforce_confirm={policy.get('enforce_confirm', True)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
