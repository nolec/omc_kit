#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import tomllib
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import omc_utils

_DEFAULT_HEADLESS_TIMEOUT_SEC = 120
_MODEL_PROFILES = {"mini_default", "mini_high", "full_default"}


def _normalize_text(value: str | None) -> str:
    return (value or "").strip().lower()


def _has_broader_context_signal(request_text: str) -> bool:
    text = _normalize_text(request_text)
    keywords = (
        "plan",
        "review",
        "investigate",
        "refactor",
        "architecture",
        "impact",
        "side effect",
        "broader context",
        "영향",
        "리팩터",
        "아키텍처",
        "계획",
        "리뷰",
        "디버그",
    )
    return any(keyword in text for keyword in keywords)


def _is_sensitive_path(path: str) -> bool:
    normalized = _normalize_text(path)
    sensitive_markers = (
        "scripts/",
        "/scripts/",
        "src/api/",
        "/api/",
        "src/state/",
        "/state/",
        "store",
        "types.ts",
        "schema",
    )
    return any(marker in normalized for marker in sensitive_markers)


def select_model_profile(
    *,
    task_kind: str,
    request_text: str,
    touched_files: list[str],
    retry_count: int,
    review_severity: str | None,
) -> str:
    override = _normalize_text(os.environ.get("OMC_MODEL_PROFILE"))
    if override in _MODEL_PROFILES:
        return override

    kind = _normalize_text(task_kind)
    severity = _normalize_text(review_severity)
    touched_count = len(touched_files)
    has_sensitive_path = any(_is_sensitive_path(path) for path in touched_files)
    broader_signal = _has_broader_context_signal(request_text)

    if kind == "ship":
        return "full_default"

    if (
        severity in {"major", "critical", "high"}
        or retry_count >= 2
        or touched_count >= 8
        or (kind in {"review", "investigate"} and has_sensitive_path and touched_count >= 3)
    ):
        return "full_default"

    if (
        kind in {"plan", "review", "investigate"}
        or touched_count >= 4
        or has_sensitive_path
        or broader_signal
    ):
        return "mini_high"

    return "mini_default"



def _is_tty_available() -> bool:
    term = os.environ.get("TERM", "").strip().lower()
    if term in {"", "dumb"}:
        return False
    return bool(sys.stdin.isatty() and sys.stdout.isatty())


def _detect_executor(preferred: str) -> str:
    if preferred != "auto":
        return preferred
    env_choice = os.environ.get("OMC_EXECUTOR", "").strip().lower()
    if env_choice in {"codex", "gemini", "claude"}:
        return env_choice
    if shutil.which("codex"):
        return "codex"
    if shutil.which("gemini"):
        return "gemini"
    if shutil.which("claude"):
        return "claude"
    return "codex"


def _codex_interactive_command(project_root: Path, prompt_text: str) -> list[str]:
    return ["codex", "-C", str(project_root), prompt_text]


def _codex_headless_command(project_root: Path, prompt_text: str, output_path: Path) -> list[str]:
    cmd = [
        "codex",
        "exec",
        "--ephemeral",
        "-s",
        "workspace-write",
        "-C",
        str(project_root),
        "-o",
        str(output_path),
        prompt_text,
    ]
    if os.environ.get("OMC_CODEX_FULL_AUTO", "").strip() in {"1", "true", "TRUE", "yes"}:
        cmd.insert(2, "--full-auto")
    return cmd


def _gemini_command(prompt_text: str) -> list[str]:
    return ["gemini", "--prompt-interactive", prompt_text]


def _gemini_headless_command(prompt_text: str) -> list[str]:
    return ["gemini", "-p", prompt_text, "--output-format", "json"]


def _claude_interactive_command(prompt_text: str) -> list[str]:
    return ["claude", prompt_text]


def _claude_headless_command(prompt_text: str) -> list[str]:
    return ["claude", "-p", prompt_text]


def _adapt_prompt_for_executor(prompt_text: str, *, executor: str) -> str:
    if executor != "gemini":
        return prompt_text
    adapter = (
        "# Gemini Executor Adapter\n\n"
        "- 현재 실행기는 Gemini CLI interactive mode다.\n"
        "- 현재 세션에서 실제로 제공되는 도구만 사용한다.\n"
        "- `run_shell_command`, `replace`, `invoke_agent`, `grep_search`, `read_file`, `glob` 같은 특정 도구 이름이 본문에 보이더라도, "
        "현재 환경에 없으면 그대로 호출하거나 언급하지 않는다.\n"
        "- 도구 이름을 추측하거나 가짜 tool call/error를 출력하지 않는다.\n"
        "- Codex 전용 도구/런타임 전제는 무시하고, 현재 Gemini CLI가 제공하는 파일 읽기/검색/편집/명령 실행 능력 안에서 직접 작업한다.\n"
        "- 로컬 OMC 역할 컨펌은 이미 끝났으므로 역할 재컨펌을 다시 요구하지 않는다.\n"
        "- 작업을 시작할 때는 짧은 계획 후 바로 실행으로 들어간다.\n"
        "- 과도한 장문 thinking을 피한다. 먼저 3-5줄 핵심 결론을 제시하고, 필요한 경우에만 상세 분석을 이어간다.\n"
        "- 구현/수정 요청이면 설명을 길게 늘리지 말고 즉시 파일 탐색과 수정을 시작한다.\n"
    )
    return f"{adapter}\n\n---\n\n{prompt_text}"


def _manual_retry(executor: str, *, prompt_path: Path, project_root: Path) -> str:
    if executor == "gemini":
        return f'cd "{project_root}" && gemini --prompt-interactive "$(cat "{prompt_path}")"'
    if executor == "claude":
        return f'cd "{project_root}" && claude "$(cat "{prompt_path}")"'
    return f'codex -C "{project_root}" "$(cat "{prompt_path}")"'


def _check_codex_auth() -> bool:
    """Quickly check if codex is authenticated."""
    try:
        # Use a fast, read-only command to check auth
        proc = subprocess.run(["codex", "features"], capture_output=True, text=True, timeout=5)
        if proc.returncode == 0:
            return True
        if "401 Unauthorized" in proc.stderr or "401 Unauthorized" in proc.stdout:
            return False
        return True # Assume OK for other errors to avoid false positives
    except Exception:
        return True


def _read_text_if_exists(path: Path) -> str:
    if not path.exists():
        return ""
    return path.read_text(encoding="utf-8").strip()


def _resolve_codex_home() -> Path:
    raw = os.environ.get("CODEX_HOME", "").strip()
    if raw:
        return Path(raw).expanduser()
    return Path.home() / ".codex"


def _copy_codex_runtime_file(source_home: Path, runtime_home: Path, name: str) -> None:
    src = source_home / name
    if not src.exists():
        return
    dst = runtime_home / name
    dst.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(src, dst)


def _copy_codex_config_for_headless(source_home: Path, runtime_home: Path) -> None:
    src = source_home / "config.toml"
    if not src.exists():
        return

    text = src.read_text(encoding="utf-8")
    try:
        parsed = tomllib.loads(text)
    except tomllib.TOMLDecodeError:
        (runtime_home / "config.toml").write_text(text, encoding="utf-8")
        return

    current_model = parsed.get("model")
    migrations = parsed.get("notice", {}).get("model_migrations", {})
    if isinstance(current_model, str) and isinstance(migrations, dict):
        migrated_model = migrations.get(current_model)
        if isinstance(migrated_model, str) and migrated_model.strip():
            text = re.sub(
                r'(?m)^model\s*=\s*"[^"]*"\s*$',
                f'model = "{migrated_model}"',
                text,
                count=1,
            )

    (runtime_home / "config.toml").write_text(text, encoding="utf-8")


def _prepare_codex_headless_runtime() -> tuple[tempfile.TemporaryDirectory[str], dict[str, str]]:
    source_home = _resolve_codex_home()
    runtime = tempfile.TemporaryDirectory(prefix="omc-codex-home-")
    runtime_home = Path(runtime.name)

    for name in ("auth.json", "version.json", "installation_id"):
        _copy_codex_runtime_file(source_home, runtime_home, name)
    _copy_codex_config_for_headless(source_home, runtime_home)

    env = os.environ.copy()
    env["CODEX_HOME"] = str(runtime_home)
    return runtime, env


def _extract_gemini_headless_text(stdout: str) -> str:
    text = stdout.strip()
    if not text:
        return ""
    try:
        payload = json.loads(text)
    except json.JSONDecodeError:
        return text
    if isinstance(payload, dict):
        for key in ("response", "text", "content"):
            value = payload.get(key)
            if isinstance(value, str) and value.strip():
                return value.strip()
    return text


def _run_codex_headless(project_root: Path, prompt_text: str, *, timeout_sec: int) -> int:
    with tempfile.NamedTemporaryFile(prefix="omc-codex-last.", suffix=".txt", delete=False) as fp:
        output_path = Path(fp.name)
    runtime = None
    try:
        runtime, env = _prepare_codex_headless_runtime()
        cmd = _codex_headless_command(project_root, prompt_text, output_path)
        try:
            proc = subprocess.run(
                cmd,
                check=False,
                capture_output=True,
                text=True,
                timeout=timeout_sec,
                env=env,
            )
        except subprocess.TimeoutExpired:
            print(f"[!] Codex headless execution timed out after {timeout_sec}s", file=sys.stderr)
            return 124
        output_text = _read_text_if_exists(output_path)
        if output_text:
            print(output_text)
        elif proc.stdout.strip():
            print(proc.stdout.strip())
        if proc.returncode != 0 and proc.stderr.strip():
            print(proc.stderr.strip(), file=sys.stderr)
        if proc.returncode != 0:
            print(f"[!] Codex headless execution exited with code {proc.returncode}")
        return int(proc.returncode)
    finally:
        if runtime is not None:
            runtime.cleanup()
        output_path.unlink(missing_ok=True)


def _run_gemini_headless(project_root: Path, prompt_text: str, *, timeout_sec: int) -> int:
    try:
        proc = subprocess.run(
            _gemini_headless_command(prompt_text),
            cwd=str(project_root),
            check=False,
            capture_output=True,
            text=True,
            timeout=timeout_sec,
        )
    except subprocess.TimeoutExpired:
        print(f"[!] Gemini headless execution timed out after {timeout_sec}s", file=sys.stderr)
        return 124
    text = _extract_gemini_headless_text(proc.stdout)
    if text:
        print(text)
    if proc.returncode != 0 and proc.stderr.strip():
        print(proc.stderr.strip(), file=sys.stderr)
    return int(proc.returncode)


def _run_claude_headless(project_root: Path, prompt_text: str, *, timeout_sec: int) -> int:
    try:
        proc = subprocess.run(
            _claude_headless_command(prompt_text),
            cwd=str(project_root),
            check=False,
            capture_output=True,
            text=True,
            timeout=timeout_sec,
        )
    except subprocess.TimeoutExpired:
        print(f"[!] Claude headless execution timed out after {timeout_sec}s", file=sys.stderr)
        return 124
    if proc.stdout.strip():
        print(proc.stdout.strip())
    if proc.returncode != 0 and proc.stderr.strip():
        print(proc.stderr.strip(), file=sys.stderr)
    return int(proc.returncode)


def main() -> int:
    ap = argparse.ArgumentParser(description="Execute an OMC-composed prompt with Codex/Gemini/Claude.")
    ap.add_argument("--target", type=Path, default=Path.cwd(), help="Project root.")
    ap.add_argument("--prompt-file", type=Path, required=True, help="Composed prompt markdown file.")
    ap.add_argument("--executor", choices=["auto", "codex", "gemini", "claude"], default="auto", help="LLM CLI executor.")
    ap.add_argument(
        "--execution-mode",
        choices=["interactive", "headless"],
        default="interactive",
        help="Use TUI handoff or non-interactive script mode.",
    )
    ap.add_argument(
        "--timeout-sec",
        type=int,
        default=int(os.environ.get("OMC_EXEC_TIMEOUT_SEC", _DEFAULT_HEADLESS_TIMEOUT_SEC)),
        help="Timeout for headless execution mode.",
    )
    ap.add_argument(
        "--fresh-context",
        action="store_true",
        help="이전 세션 컨텍스트 없이 새 격리 컨텍스트로 실행 (critique/review 에이전트 격리용).",
    )
    args = ap.parse_args()

    project_root = omc_utils.project_root(args.target)
    prompt_path = args.prompt_file.resolve()
    if not prompt_path.exists():
        raise FileNotFoundError(prompt_path)

    executor = _detect_executor(args.executor)
    if args.execution_mode == "interactive" and not _is_tty_available():
        args.execution_mode = "headless"
    if args.fresh_context:
        # critique/review 에이전트 격리: 환경변수로 전달 (executor별 처리)
        os.environ["OMC_FRESH_CONTEXT"] = "1"
    prompt_text = _adapt_prompt_for_executor(
        prompt_path.read_text(encoding="utf-8"),
        executor=executor,
    )

    if executor == "codex":
        if not shutil.which("codex"):
            print(f"codex CLI not found. Prompt preserved at: {prompt_path}")
            return 127
        
        # 1. Auth Check & Auto Login
        if not _check_codex_auth():
            print("\n[!] Codex authentication failed. Attempting login...")
            try:
                subprocess.run(["codex", "login"], check=True)
                print("[+] Login successful.\n")
            except subprocess.CalledProcessError:
                print("[-] Login failed or cancelled.")
                return 1

        if args.execution_mode == "headless":
            return _run_codex_headless(project_root, prompt_text, timeout_sec=args.timeout_sec)

        # 2. Transition to Interactive CLI (TUI)
        # We use subprocess.run without capturing to hand over the terminal (TTY)
        print(f"🧭 Launching Codex CLI for {project_root.name}...")
        cmd = _codex_interactive_command(project_root, prompt_text)
        proc = subprocess.run(cmd, check=False)
        
        if proc.returncode != 0:
            print(f"\n[!] Codex CLI exited with code {proc.returncode}")
            print(f"Prompt preserved at: {prompt_path}")
        return int(proc.returncode)

    if executor == "gemini":
        if not shutil.which("gemini"):
            print(f"gemini CLI not found. Prompt preserved at: {prompt_path}")
            return 127
        if args.execution_mode == "headless":
            return _run_gemini_headless(project_root, prompt_text, timeout_sec=args.timeout_sec)
        proc = subprocess.run(_gemini_command(prompt_text), cwd=str(project_root), check=False)
        return int(proc.returncode)

    if not shutil.which("claude"):
        print(f"claude CLI not found. Prompt preserved at: {prompt_path}")
        return 127

    if args.execution_mode == "headless":
        return _run_claude_headless(project_root, prompt_text, timeout_sec=args.timeout_sec)

    proc = subprocess.run(_claude_interactive_command(prompt_text), cwd=str(project_root), check=False)
    return int(proc.returncode)


if __name__ == "__main__":
    raise SystemExit(main())
