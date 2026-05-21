#!/usr/bin/env python3
from __future__ import annotations

import argparse
import subprocess
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import omc_utils



def _scripts_dir() -> Path:
    return Path(__file__).resolve().parent


def _configure_stdio(*, stdin_errors: str | None = None, output_errors: str = "replace") -> None:
    # Keep stdin strict so malformed user input fails explicitly instead of
    # being silently rewritten into U+FFFD inside session state.
    stream_policies = (
        (sys.stdin, stdin_errors),
        (sys.stdout, output_errors),
        (sys.stderr, output_errors),
    )
    for stream, errors in stream_policies:
        if errors is None:
            continue
        reconfigure = getattr(stream, "reconfigure", None)
        if callable(reconfigure):
            try:
                reconfigure(errors=errors)
            except Exception:
                pass


def _run_request(
    project_root: Path,
    request: str,
    *,
    mode: str,
    executor: str,
    execution_mode: str,
    friction_mode: str,
    confirm_style: str,
    context_mode: str,
    no_confirm: bool,
    top: int | None,
) -> int:
    scripts_dir = _scripts_dir()
    omc_script = scripts_dir / "omc.py"
    exec_script = scripts_dir / "omc_exec.py"

    if not omc_script.exists():
        raise FileNotFoundError(omc_script)
    if not exec_script.exists():
        raise FileNotFoundError(exec_script)

    with tempfile.NamedTemporaryFile(prefix="omc-chat.", suffix=".md", delete=False) as fp:
        prompt_path = Path(fp.name)
    non_interactive = (not sys.stdin.isatty()) or (not sys.stdout.isatty())

    compose_cmd = [
        sys.executable,
        str(omc_script),
        mode,
        request,
        "--out",
        str(prompt_path),
        "--friction-mode",
        friction_mode,
        "--confirm-style",
        confirm_style,
        "--quiet-write",
        "--context-mode",
        context_mode,
    ]
    if execution_mode == "headless" or non_interactive:
        compose_cmd.append("--assume-confirm")
    if no_confirm or non_interactive:
        compose_cmd.append("--no-confirm")
    if top is not None:
        compose_cmd.extend(["--top", str(top)])

    compose = subprocess.run(compose_cmd, cwd=str(project_root), check=False)
    if compose.returncode != 0:
        return int(compose.returncode)

    execute = subprocess.run(
        [
            sys.executable,
            str(exec_script),
            "--target",
            str(project_root),
            "--prompt-file",
            str(prompt_path),
            "--executor",
            executor,
            "--execution-mode",
            execution_mode,
        ],
        cwd=str(project_root),
        check=False,
    )

    if execute.returncode != 0:
        print(f"[omc-chat] 실행 실패. 프롬프트 보관: {prompt_path}")
        return int(execute.returncode)

    prompt_path.unlink(missing_ok=True)
    return 0


def _interactive_loop(
    project_root: Path,
    *,
    mode: str,
    executor: str,
    execution_mode: str,
    friction_mode: str,
    confirm_style: str,
    context_mode: str,
    no_confirm: bool,
    top: int | None,
) -> int:
    print("OMC chat mode started. 종료: /exit, /quit")
    while True:
        try:
            text = input("omc> ").strip()
        except UnicodeDecodeError:
            _configure_stdio(stdin_errors="strict")
            print("[omc-chat] 입력 인코딩 오류를 감지했습니다. 다시 입력해 주세요.")
            continue
        except EOFError:
            print("")
            return 0
        except KeyboardInterrupt:
            print("")
            return 130

        if not text:
            continue
        if text in {"/exit", "/quit"}:
            return 0

        code = _run_request(
            project_root,
            text,
            mode=mode,
            executor=executor,
            execution_mode=execution_mode,
            friction_mode=friction_mode,
            confirm_style=confirm_style,
            context_mode=context_mode,
            no_confirm=no_confirm,
            top=top,
        )
        if code != 0:
            print(f"[omc-chat] 요청 실행 실패 (exit={code})")


def main() -> int:
    _configure_stdio(stdin_errors="strict")
    ap = argparse.ArgumentParser(description="Natural language OMC chat wrapper for Codex/Gemini.")
    ap.add_argument("request", nargs="*", help="Natural language request text.")
    ap.add_argument("--target", type=Path, default=Path.cwd(), help="Target repository root.")
    ap.add_argument("--mode", choices=["prompt", "autopilot", "team", "ulw", "ralph", "deep-interview"], default="prompt")
    ap.add_argument("--executor", choices=["auto", "codex", "gemini", "claude"], default="auto")
    ap.add_argument("--execution-mode", choices=["interactive", "headless"], default="interactive")
    ap.add_argument("--friction-mode", choices=["auto", "strict", "light"], default="auto")
    ap.add_argument("--confirm-style", choices=["full", "compact"], default="compact")
    ap.add_argument("--context-mode", choices=["full", "lean"], default="lean")
    ap.add_argument("--no-confirm", action="store_true", help="Skip interactive role confirmation.")
    ap.add_argument("--top", type=int, default=None, help="Limit auto-selected role count for faster prompts.")
    args = ap.parse_args()

    project_root = omc_utils.project_root(args.target)
    request = " ".join(args.request).strip()
    if request:
        return _run_request(
            project_root,
            request,
            mode=args.mode,
            executor=args.executor,
            execution_mode=args.execution_mode,
            friction_mode=args.friction_mode,
            confirm_style=args.confirm_style,
            context_mode=args.context_mode,
            no_confirm=args.no_confirm,
            top=args.top,
        )
    return _interactive_loop(
        project_root,
        mode=args.mode,
        executor=args.executor,
        execution_mode=args.execution_mode,
        friction_mode=args.friction_mode,
        confirm_style=args.confirm_style,
        context_mode=args.context_mode,
        no_confirm=args.no_confirm,
        top=args.top,
    )


if __name__ == "__main__":
    raise SystemExit(main())
