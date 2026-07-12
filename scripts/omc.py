#!/usr/bin/env python3
from __future__ import annotations

import argparse
import runpy
import subprocess
import sys
from pathlib import Path


def _kit_root() -> Path:
    base = Path(__file__).resolve().parents[1]
    direct_install = base / "scripts" / "install.py"
    # Prefer base if it has templates/ (i.e. we are inside the real kit)
    if direct_install.exists() and (base / "templates").is_dir():
        return base

    nested = base / "omc_kit"
    nested_install = nested / "scripts" / "install.py"
    if nested_install.exists() and (nested / "templates").is_dir():
        return nested

    # Last resort: return base even without templates
    if direct_install.exists():
        return base

    return base


def _run_script(script: Path, argv: list[str]) -> int:
    if not script.exists():
        raise FileNotFoundError(script)
    sys.argv = [str(script), *argv]
    script_dir = str(script.parent.resolve())
    inserted = False
    if script_dir not in sys.path:
        sys.path.insert(0, script_dir)
        inserted = True
    try:
        runpy.run_path(str(script), run_name="__main__")
    except SystemExit as exc:
        code = exc.code
        if code in (None, 0):
            return 0
        if isinstance(code, int):
            return code
        raise
    finally:
        if inserted and sys.path and sys.path[0] == script_dir:
            sys.path.pop(0)
    return 0


def _auto_prompt_args(args: argparse.Namespace, *, mode: str) -> list[str]:
    cmd: list[str] = ["--mode", mode, "--out", str(args.out)]
    if args.request is not None:
        cmd.extend(["--request", args.request])
    elif args.request_file is not None:
        cmd.extend(["--request-file", str(args.request_file)])
    else:
        raise SystemExit("Provide request text as a positional argument or via --request-file")
    if getattr(args, "no_confirm", False):
        pass
    else:
        cmd.append("--confirm")
    if getattr(args, "assume_confirm", False):
        cmd.append("--assume-confirm")
    if getattr(args, "friction_mode", None):
        cmd.extend(["--friction-mode", args.friction_mode])
    if getattr(args, "confirm_style", None):
        cmd.extend(["--confirm-style", args.confirm_style])
    if getattr(args, "quiet_write", False):
        cmd.append("--quiet-write")
    if getattr(args, "context_mode", None):
        cmd.extend(["--context-mode", args.context_mode])
    for b in args.base:
        cmd.extend(["--base", str(b)])
    for t in args.team:
        cmd.extend(["--team", str(t)])
    if args.profile is not None:
        cmd.extend(["--profile", args.profile])
    if args.roles is not None:
        cmd.extend(["--roles", args.roles])
    if args.top is not None:
        cmd.extend(["--top", str(args.top)])
    return cmd


def _quickstart_text() -> str:
    return """# OMC Quickstart KR

1. 현재 프로젝트에서 바로 시작
   - `./run omc "작업 요청"`
   - `./run omc --executor gemini "작업 요청"`

2. 상태 확인
   - `python scripts/omc.py state status`

3. 컨텍스트 압축
   - `python scripts/omc.py state compact`

4. 새 프로젝트 초기 설치
   - `python omc_kit/scripts/omc.py setup --target /path/to/project`

5. 실행기 선택
   - 기본은 `OMC_EXECUTOR` 또는 자동 감지(`codex` 우선, 없으면 `gemini`)
   - 강제 지정: `./run omc --executor codex "작업 요청"`
   - 강제 지정: `./run omc --executor gemini "작업 요청"`

6. 실행 실패 시
   - `./run omc`가 `/tmp/omc-prompt.*.md`를 남기고 선택된 실행기 기준 수동 재실행 명령을 출력

7. 훅 커스터마이징
   - `.omc/hooks.json`
"""


def _project_root(target: Path | None = None) -> Path:
    return (target or Path.cwd()).resolve()


def _run_guarded(project_root: Path, *, label: str, command: list[str]) -> int:
    kit = _kit_root()
    guard_script = kit / "scripts" / "omc_guard.py"
    run_script = kit / "scripts" / "omc_run.py"

    guard_code = _run_script(guard_script, ["require", "--target", str(project_root), "--for", label])
    if guard_code != 0:
        return guard_code

    wrapped = [sys.executable, str(run_script), "--target", str(project_root), "--label", label, "--", *command]
    proc = subprocess.run(wrapped, cwd=str(project_root), check=False)
    return int(proc.returncode)


def main() -> int:
    argv = sys.argv[1:]
    commands = {
        "setup",
        "prompt",
        "autopilot",
        "orchestrate",
        "team",
        "ulw",
        "ralph",
        "deep-interview",
        "state",
        "guard",
        "hook",
        "domain",
        "doctor",
        "quickstart",
        "run",
        "peer-review",
    }
    if not argv or argv[0].startswith("-") or argv[0] not in commands:
        argv = ["prompt", *argv]
    sys.argv = [sys.argv[0], *argv]

    ap = argparse.ArgumentParser(description="OMC-style single entrypoint for the OMC kit.")
    sub = ap.add_subparsers(dest="command", required=True)

    setup = sub.add_parser("setup", help="Install the kit into the current or target project.")
    setup.add_argument("--target", type=Path, default=Path.cwd(), help="Target repository root.")
    setup.add_argument("--force", action="store_true", help="Overwrite existing files.")
    setup.add_argument(
        "--skip-session-start",
        action="store_true",
        help="Skip running the session_start lifecycle hook after setup.",
    )

    hook = sub.add_parser("hook", help="Run OMC lifecycle hooks.")
    hook.add_argument("event", choices=["session_start", "session_end", "pre_compact", "post_compact"])
    hook.add_argument("--target", type=Path, default=Path.cwd(), help="Target repository root.")

    domain = sub.add_parser("domain", help="Create or update project-local domain OMC overlays.")
    domain_sub = domain.add_subparsers(dest="domain_command", required=True)
    domain_init = domain_sub.add_parser("init", help="Create project_prompts/team.local.json and a role prompt.")
    domain_init.add_argument("domain", help="Domain name, e.g. crypto, ipo, legal, support.")
    domain_init.add_argument("--target", type=Path, default=Path.cwd(), help="Target repository root.")
    domain_init.add_argument("--force", action="store_true", help="Overwrite generated role prompt/readme.")

    doctor = sub.add_parser("doctor", help="Check common OMC setup and UX dependencies.")
    doctor.add_argument("--target", type=Path, default=Path.cwd(), help="Target repository root.")

    guard = sub.add_parser("guard", help="Check whether the latest OMC session is confirmed.")
    guard.add_argument("--target", type=Path, default=Path.cwd(), help="Target repository root.")
    guard.add_argument("--for", dest="command_name", required=True, help="Human-readable command name.")

    sub.add_parser("quickstart", help="Print the Korean OMC quickstart.")

    run_cmd = sub.add_parser("run", help="Run any guarded command through the OMC run lifecycle (omc_run.py).")
    run_cmd.add_argument("--target", type=Path, default=Path.cwd(), help="Target repository root.")
    run_cmd.add_argument("--label", required=True, help="Human-readable command label for OMC tracking.")
    run_cmd.add_argument("--summary", default=None, help="Short run summary.")
    run_cmd.add_argument("command", nargs=argparse.REMAINDER, help="Command to execute (put after --).")

    orchestrate = sub.add_parser("orchestrate", help="Create a read-only orchestration plan.")
    orchestrate.add_argument("--request", required=True, help="Natural-language request to classify and decompose.")
    orchestrate.add_argument("--target", type=Path, default=Path.cwd(), help="Target repository root.")
    orchestrate.add_argument("--dry-run", action="store_true", help="Required safety marker; never executes stages.")
    orchestrate.add_argument("--execute-simple", action="store_true", help="Opt in to gated simple-task autopilot execution.")

    peer_review = sub.add_parser("peer-review", help="Run peer-review of the latest uncommitted changes.")
    peer_review.add_argument("--target", type=Path, default=Path.cwd(), help="Target repository root.")
    peer_review.add_argument("--async-mode", action="store_true", help="Run in background.")

    state = sub.add_parser("state", help="Manage persistent .omc state.")
    state_sub = state.add_subparsers(dest="state_command", required=True)
    state_init = state_sub.add_parser("init", help="Initialize .omc state files.")
    state_init.add_argument("--target", type=Path, default=Path.cwd(), help="Target repository root.")
    state_init.add_argument("--force", action="store_true", help="Rebuild derived files.")

    state_record = state_sub.add_parser("record", help="Record a prompt/session entry.")
    state_record.add_argument("--target", type=Path, default=Path.cwd(), help="Target repository root.")
    state_record.add_argument("--mode", required=True, help="OMC mode name.")
    state_record.add_argument("--title", required=True, help="Mode title.")
    state_record.add_argument("--request", required=True, help="Request text.")
    state_record.add_argument("--roles", required=True, help="Comma-separated role ids.")
    state_record.add_argument("--prompt-path", type=str, default=None, help="Prompt output path.")
    state_record.add_argument("--confirm", action="store_true", help="Record the session as already confirmed/active.")
    state_record.add_argument(
        "--confirmation-source",
        type=str,
        default=None,
        help="Optional confirmation source label when --confirm is used.",
    )
    state_record.add_argument("--keep", type=int, default=80, help="Maximum stored entries.")

    state_sync = state_sub.add_parser("sync-session", help="Record a skill-driven session as confirmed/active.")
    state_sync.add_argument("--target", type=Path, default=Path.cwd(), help="Target repository root.")
    state_sync.add_argument("--mode", required=True, help="OMC mode name.")
    state_sync.add_argument("--title", required=True, help="Mode title.")
    state_sync.add_argument("--request", required=True, help="Request text.")
    state_sync.add_argument("--roles", required=True, help="Comma-separated role ids.")
    state_sync.add_argument("--prompt-path", type=str, default=None, help="Prompt output path.")
    state_sync.add_argument("--keep", type=int, default=80, help="Maximum stored entries.")

    state_note = state_sub.add_parser("note", help="Append a persistent note.")
    state_note.add_argument("--target", type=Path, default=Path.cwd(), help="Target repository root.")
    state_note.add_argument("--kind", default="note", help="Note kind label.")
    state_note.add_argument("--text", required=True, help="Note text.")
    state_note.add_argument("--keep", type=int, default=80, help="Maximum stored entries.")

    state_compact = state_sub.add_parser("compact", help="Prune history and rewrite the notepad.")
    state_compact.add_argument("--target", type=Path, default=Path.cwd(), help="Target repository root.")
    state_compact.add_argument("--keep", type=int, default=25, help="Entries to preserve.")

    state_status = state_sub.add_parser("status", help="Show current state summary.")
    state_status.add_argument("--target", type=Path, default=Path.cwd(), help="Target repository root.")

    state_confirm = state_sub.add_parser("confirm", help="Mark the latest or a specific session as confirmed.")
    state_confirm.add_argument("--target", type=Path, default=Path.cwd(), help="Target repository root.")
    state_confirm.add_argument("--session-id", type=str, default=None, help="Specific session id to confirm.")
    state_confirm.add_argument("--skip-tdd", action="store_true", help="TDD 게이트 체크를 건너뜀 (사용자 명시 승인 필요).")

    state_session_status = state_sub.add_parser("session-status", help="Update session lifecycle status.")
    state_session_status.add_argument("--target", type=Path, default=Path.cwd(), help="Target repository root.")
    state_session_status.add_argument("--status", required=True, choices=["active", "waiting_input", "blocked", "superseded"])
    state_session_status.add_argument("--session-id", type=str, default=None)
    state_session_status.add_argument("--reason", type=str, default=None)
    state_session_status.add_argument("--superseded-by", type=str, default=None)

    state_run_start = state_sub.add_parser("run-start", help="Mark a guarded command as running.")
    state_run_start.add_argument("--target", type=Path, default=Path.cwd(), help="Target repository root.")
    state_run_start.add_argument("--command-name", required=True, help="Human-readable command name.")
    state_run_start.add_argument("--summary", type=str, default=None, help="Short run summary.")

    state_run_update = state_sub.add_parser("run-update", help="Update active run progress.")
    state_run_update.add_argument("--target", type=Path, default=Path.cwd(), help="Target repository root.")
    state_run_update.add_argument("--run-id", required=True, help="Run id to update.")
    state_run_update.add_argument("--phase", type=str, default=None, help="Current phase label.")
    state_run_update.add_argument("--message", type=str, default=None, help="Progress message.")
    state_run_update.add_argument("--metrics-json", type=str, default=None, help="JSON object for progress metrics.")

    state_run_finish = state_sub.add_parser("run-finish", help="Mark a guarded command as completed/failed.")
    state_run_finish.add_argument("--target", type=Path, default=Path.cwd(), help="Target repository root.")
    state_run_finish.add_argument("--run-id", required=True, help="Run id to finish.")
    state_run_finish.add_argument("--status", required=True, choices=["completed", "failed", "aborted"])
    state_run_finish.add_argument("--message", type=str, default=None, help="Final result message.")
    state_run_finish.add_argument("--result-json", type=str, default=None, help="JSON object for final result.")

    for name in ["prompt", "autopilot", "team", "ulw", "ralph", "deep-interview"]:
        sp = sub.add_parser(name, help=f"Compose a prompt in {name} mode.")
        sp.add_argument("request", nargs="?", default=None, help="Request text.")
        sp.add_argument("--request-file", type=Path, default=None, help="Read request text from file.")
        sp.add_argument("--out", type=Path, default=Path("/tmp/prompt.md"))
        sp.add_argument("--no-confirm", action="store_true", help="Skip the interactive role confirmation step.")
        sp.add_argument(
            "--assume-confirm",
            action="store_true",
            help="Confirm the recommended roles automatically without interactive input.",
        )
        sp.add_argument(
            "--friction-mode",
            type=str,
            choices=["auto", "strict", "light"],
            default="auto",
            help="Control OMC overhead. `light` skips confirm and shrinks prompt for short/simple requests.",
        )
        sp.add_argument(
            "--confirm-style",
            type=str,
            choices=["full", "compact"],
            default="full",
            help="How to render the interactive role confirmation UI.",
        )
        sp.add_argument("--quiet-write", action="store_true", help="Do not print the output path when writing a prompt file.")
        sp.add_argument(
            "--context-mode",
            type=str,
            choices=["full", "lean"],
            default="full",
            help="How much static prompt context to include.",
        )
        sp.add_argument("--base", type=Path, action="append", default=[], help="Base prompt markdown file(s).")
        sp.add_argument("--team", type=Path, action="append", default=[], help="Team JSON file(s).")
        sp.add_argument("--profile", type=str, default=None, help="Profile id in team.json.")
        sp.add_argument("--roles", type=str, default=None, help="Comma-separated role ids in team.json.")
        sp.add_argument("--top", type=int, default=None, help="When auto-selecting, how many roles to include.")
        if name == "autopilot":
            sp.add_argument(
                "--task-file",
                type=Path,
                default=None,
                help="태스크 JSON 파일 경로. 지정 시 구조화된 자율 루프로 실행합니다.",
            )
            sp.add_argument(
                "--dry-run",
                action="store_true",
                help="태스크 파일 실행 시 실제 LLM 호출 없이 계획만 출력합니다.",
            )

    args = ap.parse_args()
    kit = _kit_root()

    if args.command == "setup":
        install = kit / "scripts" / "install.py"
        init_state = kit / "scripts" / "omc_state.py"
        hook_script = kit / "scripts" / "omc_hooks.py"
        target = args.target.resolve()
        install_code = _run_script(install, ["--target", str(target), *(["--force"] if args.force else [])])
        if install_code != 0:
            raise SystemExit(install_code)
        init_code = _run_script(init_state, ["init", "--target", str(target), *(["--force"] if args.force else [])])
        if init_code != 0:
            raise SystemExit(init_code)
        if args.skip_session_start:
            return 0
        return _run_script(hook_script, ["session_start", "--target", str(target)])

    if args.command == "hook":
        hook_script = kit / "scripts" / "omc_hooks.py"
        return _run_script(hook_script, [args.event, "--target", str(args.target)])

    if args.command == "domain":
        domain_script = kit / "scripts" / "omc_domain.py"
        if args.domain_command == "init":
            return _run_script(
                domain_script,
                [
                    "init",
                    args.domain,
                    "--target",
                    str(args.target),
                    *(["--force"] if args.force else []),
                ],
            )

    if args.command == "doctor":
        doctor_script = kit / "scripts" / "omc_doctor.py"
        return _run_script(doctor_script, ["--target", str(args.target)])

    if args.command == "guard":
        guard_script = kit / "scripts" / "omc_guard.py"
        return _run_script(guard_script, ["require", "--target", str(args.target), "--for", args.command_name])

    if args.command == "quickstart":
        print(_quickstart_text())
        return 0

    if args.command == "run":
        run_script = kit / "scripts" / "omc_run.py"
        command = list(args.command_args) if hasattr(args, "command_args") else list(args.command)
        if command and command[0] == "--":
            command = command[1:]
        run_args = ["--target", str(args.target.resolve()), "--label", args.label]
        if args.summary:
            run_args += ["--summary", args.summary]
        return _run_script(run_script, [*run_args, "--", *command])

    if args.command == "peer-review":
        peer_review_script = kit / "scripts" / "omc_peer_review.py"
        return _run_script(peer_review_script, ["--target", str(args.target), *(["--async-mode"] if getattr(args, "async_mode", False) else [])])

    if args.command == "state":
        state_script = kit / "scripts" / "omc_state.py"
        hook_script = kit / "scripts" / "omc_hooks.py"
        args.target = args.target.resolve()
        if args.state_command == "init":
            return _run_script(state_script, ["init", "--target", str(args.target), *(["--force"] if args.force else [])])
        if args.state_command == "record":
            return _run_script(
                state_script,
                [
                    "record",
                    "--target",
                    str(args.target),
                    "--mode",
                    args.mode,
                    "--title",
                    args.title,
                    "--request",
                    args.request,
                    "--roles",
                    args.roles,
                    *(["--prompt-path", args.prompt_path] if args.prompt_path is not None else []),
                    *(["--confirm"] if args.confirm else []),
                    *(["--confirmation-source", args.confirmation_source] if args.confirmation_source else []),
                    "--keep",
                    str(args.keep),
                ],
            )
        if args.state_command == "note":
            return _run_script(
                state_script,
                [
                    "note",
                    "--target",
                    str(args.target),
                    "--kind",
                    args.kind,
                    "--text",
                    args.text,
                    "--keep",
                    str(args.keep),
                ],
            )
        if args.state_command == "sync-session":
            return _run_script(
                state_script,
                [
                    "sync-session",
                    "--target",
                    str(args.target),
                    "--mode",
                    args.mode,
                    "--title",
                    args.title,
                    "--request",
                    args.request,
                    "--roles",
                    args.roles,
                    *(["--prompt-path", args.prompt_path] if args.prompt_path is not None else []),
                    "--keep",
                    str(args.keep),
                ],
            )
        if args.state_command == "compact":
            pre_code = _run_script(hook_script, ["pre_compact", "--target", str(args.target)])
            if pre_code != 0:
                raise SystemExit(pre_code)
            compact_code = _run_script(state_script, ["compact", "--target", str(args.target), "--keep", str(args.keep)])
            if compact_code != 0:
                raise SystemExit(compact_code)
            return _run_script(hook_script, ["post_compact", "--target", str(args.target)])
        if args.state_command == "status":
            return _run_script(state_script, ["status", "--target", str(args.target)])
        if args.state_command == "confirm":
            # TDD 게이트: 컨펌 전에 staged 파일 TDD 체크
            tdd_script = args.target / "scripts" / "omc_tdd_check.py"
            if not tdd_script.exists():
                tdd_script = Path(__file__).parent / "omc_tdd_check.py"
            if tdd_script.exists():
                import subprocess as _sp
                tdd_result = _sp.run(
                    ["python3", str(tdd_script), "--staged"],
                    cwd=str(args.target),
                )
                if tdd_result.returncode != 0:
                    print()
                    print("===========================================================")
                    print(" TDD GATE: confirm 차단")
                    print(" staged 파일 중 테스트 없는 구현 파일이 있습니다.")
                    print(" 테스트를 먼저 작성하거나 --skip-tdd 플래그를 사용하세요.")
                    print(" python3 scripts/omc.py state confirm --skip-tdd")
                    print("===========================================================")
                    if not getattr(args, "skip_tdd", False):
                        return 6
            return _run_script(
                state_script,
                ["confirm", "--target", str(args.target), *(["--session-id", args.session_id] if args.session_id else [])],
            )
        if args.state_command == "session-status":
            return _run_script(
                state_script,
                [
                    "session-status",
                    "--target",
                    str(args.target),
                    "--status",
                    args.status,
                    *(["--session-id", args.session_id] if args.session_id else []),
                    *(["--reason", args.reason] if args.reason else []),
                    *(["--superseded-by", args.superseded_by] if args.superseded_by else []),
                ],
            )
        if args.state_command == "run-start":
            return _run_script(
                state_script,
                [
                    "run-start",
                    "--target",
                    str(args.target),
                    "--command",
                    args.command_name,
                    *(["--summary", args.summary] if args.summary else []),
                ],
            )
        if args.state_command == "run-update":
            return _run_script(
                state_script,
                [
                    "run-update",
                    "--target",
                    str(args.target),
                    "--run-id",
                    args.run_id,
                    *(["--phase", args.phase] if args.phase else []),
                    *(["--message", args.message] if args.message else []),
                    *(["--metrics-json", args.metrics_json] if args.metrics_json else []),
                ],
            )
        if args.state_command == "run-finish":
            return _run_script(
                state_script,
                [
                    "run-finish",
                    "--target",
                    str(args.target),
                    "--run-id",
                    args.run_id,
                    "--status",
                    args.status,
                    *(["--message", args.message] if args.message else []),
                    *(["--result-json", args.result_json] if args.result_json else []),
                ],
            )
        raise SystemExit(f"Unknown state command: {args.state_command}")

    request = args.request
    if request is None and args.request_file is not None:
        request = args.request_file.read_text(encoding="utf-8")

    # autopilot + --task-file → omc_autopilot.py 자율 루프로 라우팅
    if args.command == "autopilot" and getattr(args, "task_file", None) is not None:
        autopilot_script = kit / "scripts" / "omc_autopilot.py"
        if not autopilot_script.exists():
            raise SystemExit(f"[ERROR] omc_autopilot.py 없음: {autopilot_script}")
        ap_args = ["run", "--task", str(args.task_file.resolve())]
        if getattr(args, "dry_run", False):
            ap_args.append("--dry-run")
        if hasattr(args, "target"):
            ap_args += ["--target", str(args.target.resolve())]
        return _run_script(autopilot_script, ap_args)

    if args.command == "orchestrate":
        orchestrator_script = kit / "scripts" / "omc_orchestrator.py"
        if not orchestrator_script.exists():
            raise SystemExit(f"[ERROR] omc_orchestrator.py 없음: {orchestrator_script}")
        ap_args = ["--request", args.request, "--target", str(args.target.resolve())]
        if args.dry_run:
            ap_args.append("--dry-run")
        if args.execute_simple:
            ap_args.append("--execute-simple")
        return _run_script(orchestrator_script, ap_args)

    if request is None:
        raise SystemExit("Provide request text as a positional argument or via --request-file")

    auto_prompt = kit / "scripts" / "auto_prompt.py"
    mode = "auto" if args.command == "prompt" else args.command
    return _run_script(auto_prompt, _auto_prompt_args(args, mode=mode))


if __name__ == "__main__":
    raise SystemExit(main())
