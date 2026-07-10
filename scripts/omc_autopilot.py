#!/usr/bin/env python3
"""
omc_autopilot.py — 멀티 LLM 자율 루프 (옵트인)

구조화된 태스크 파일(.omc/tasks/*.json)을 읽어 스텝을 순차 실행합니다.
각 스텝은 omc_exec.py를 경유해 Codex / Gemini / Claude 중 하나로 위임됩니다.
실패 시 max_retries 내 자동 재시도하며, 결과는 .omc/state/autopilot/에 저장됩니다.

태스크 파일 포맷 (.omc/tasks/<name>.json):
  {
    "id": "feat-login",
    "title": "설명",
    "executor": "auto",          // auto | codex | gemini | claude
    "max_retries": 2,            // 스텝 실패 시 재시도 횟수 (기본 1)
    "steps": [
      {
        "id": "s1",
        "title": "스텝 제목 (선택)",
        "prompt": "LLM에 전달할 프롬프트",
        "depends_on": [],         // 이 스텝 이전에 완료돼야 할 step id 목록
        "timeout_sec": 120        // 스텝별 타임아웃 (기본 120)
      }
    ]
  }

사용:
  python3 scripts/omc_autopilot.py run --task .omc/tasks/feat-login.json [--target .]
  python3 scripts/omc_autopilot.py new --id feat-login --title "설명" [--target .]
  python3 scripts/omc_autopilot.py status [--target .]
  python3 scripts/omc_autopilot.py status --task-id feat-login [--target .]
"""
from __future__ import annotations

import argparse
import json
import os
import signal
import tempfile
import subprocess
import sys
import re
import shlex
import textwrap
import threading
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import omc_utils
import omc_cost
import omc_exec
from omc_decision_input import (
    build_next_priority_surface_input,
    build_run_overview_followup_input,
    resolve_next_priority,
    resolve_next_priority_from_input,
    resolve_run_overview_followup_from_input,
)

_TASKS_DIR = ".omc/tasks"
_AUTOPILOT_STATE_DIR = ".omc/state/autopilot"
_DEFAULT_TIMEOUT_SEC = 120
_DEFAULT_MAX_RETRIES = 1
_DEFAULT_STATUS_LIMIT = 20
_POLICY_WARNED_KEYS: set[str] = set()
_COMPATIBILITY_WARNED_COMMANDS: set[str] = set()

_KNOWN_TASK_KINDS = {"task", "plan", "review", "investigate", "ship"}
_KNOWN_COMPLEXITY = {"low", "medium", "high"}
_KNOWN_RISK = {"low", "medium", "high"}
_KNOWN_PROFILES = {"mini_default", "mini_high", "full_default"}
_KNOWN_ESCALATION_POLICIES = {"default", "conservative", "aggressive"}


def _load_allowed_git_subcommands(root: Path) -> set[str]:
    """Return read-only git subcommands allowed for expect checks.

    Policy can only narrow this set. It cannot expand beyond built-in safe commands.
    """
    default = {"status", "diff", "log", "rev-parse", "show", "branch", "remote"}
    policy_env = os.environ.get("OMC_POLICY_PATH")
    policy_path = Path(policy_env) if policy_env else (root / ".omc" / "policy.json")
    try:
        data = json.loads(policy_path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return default
    except Exception as exc:
        warn_key = f"{policy_path}:{exc.__class__.__name__}:{exc}"
        if warn_key not in _POLICY_WARNED_KEYS:
            print(f"[AUTOPILOT] policy parse failed: {policy_path} ({exc})")
            _POLICY_WARNED_KEYS.add(warn_key)
        return default
    values = (
        data.get("autopilot", {}).get("allowed_git_subcommands")
        if isinstance(data, dict)
        else None
    )
    if not isinstance(values, list):
        return default
    normalized = {str(v).strip() for v in values if str(v).strip()}
    if not normalized:
        return default
    return default.intersection(normalized)


def _load_allowed_commands(root: Path) -> tuple[set[str], bool]:
    """Load allowlisted expect commands from policy.

    Returns:
      (commands, from_policy)
      - commands: effective allowlist
      - from_policy: True when policy explicitly set allowed_commands
    """
    default = {"pytest", "npm", "npx", "pnpm", "yarn", "uv", "git", "make", "echo", "true", "false"}
    policy_env = os.environ.get("OMC_POLICY_PATH")
    policy_path = Path(policy_env) if policy_env else (root / ".omc" / "policy.json")
    try:
      data = json.loads(policy_path.read_text(encoding="utf-8"))
    except FileNotFoundError:
      return default, False
    except Exception as exc:
      warn_key = f"{policy_path}:{exc.__class__.__name__}:{exc}"
      if warn_key not in _POLICY_WARNED_KEYS:
          print(f"[AUTOPILOT] policy parse failed: {policy_path} ({exc})")
          _POLICY_WARNED_KEYS.add(warn_key)
      return default, False

    values = data.get("autopilot", {}).get("allowed_commands") if isinstance(data, dict) else None
    if not isinstance(values, list):
      return default, False
    normalized = {str(v).strip() for v in values if str(v).strip()}
    if not normalized:
      return default, True
    return default.union(normalized), True


def _resolve_expect_argv(cmd: str, allowed_commands: set[str]) -> tuple[list[str] | None, str | None]:
    """Parse expect command and return validated argv.

    Supports direct safe commands only.
    """
    if _contains_disallowed_shell_operator(cmd):
        return None, "허용되지 않은 셸 연산자가 포함되어 실행이 차단되었습니다."
    try:
        argv = shlex.split(cmd)
    except ValueError as exc:
        return None, f"[ERROR] 명령 파싱 실패: {exc}"
    if not argv:
        return None, "[ERROR] 빈 커맨드"

    if argv[0] not in allowed_commands:
        return None, f"허용되지 않은 커맨드: {argv[0]}"
    return argv, None


def _warn_expect_compatibility(cmd: str, policy_defined: bool) -> None:
    """Warn once for blocked legacy commands when policy extension is not configured."""
    if policy_defined:
        return
    try:
        argv = shlex.split(cmd)
    except ValueError:
        return
    if not argv:
        return
    compat_commands = {"python", "python3", "bash", "sh", "node"}
    name = argv[0]
    if name not in compat_commands or name in _COMPATIBILITY_WARNED_COMMANDS:
        return
    print(f"[AUTOPILOT] compatibility warning: '{name}' is blocked by default. "
          "Set .omc/policy.json autopilot.allowed_commands to opt in.")
    _COMPATIBILITY_WARNED_COMMANDS.add(name)


def _contains_disallowed_shell_operator(cmd: str) -> bool:
    """Return True when shell operators appear as executable tokens.

    Policy:
    - Block operator tokens (`;`, `&&`, `||`, `|`, `>`, `<`, `` ` ``) that
      could alter command flow or piping.
    - Allow literal characters inside quoted arguments such as:
      `pytest -k "^foo$"` or `python3 -c 'print("a|b")'`.
    """
    lexer = shlex.shlex(cmd, posix=True, punctuation_chars=";&|><`")
    lexer.whitespace_split = True
    try:
        tokens = list(lexer)
    except ValueError:
        return True
    disallowed_tokens = {";", "&&", "||", "|", ">", ">>", "<", "<<", "`"}
    return any(token in disallowed_tokens for token in tokens)


def _normalize_step_metadata(step: dict) -> dict[str, object]:
    step_id = str(step.get("id", "")).strip().lower()
    explicit_task_kind = str(step.get("task_kind", "")).strip().lower()
    inferred_task_kind = step_id if step_id in _KNOWN_TASK_KINDS else "task"
    task_kind = explicit_task_kind if explicit_task_kind in _KNOWN_TASK_KINDS else inferred_task_kind

    complexity = str(step.get("complexity", "")).strip().lower()
    if complexity not in _KNOWN_COMPLEXITY:
        complexity = "medium"

    risk = str(step.get("risk", "")).strip().lower()
    if risk not in _KNOWN_RISK:
        risk = "medium"

    raw_sensitive_paths = step.get("sensitive_paths")
    if isinstance(raw_sensitive_paths, list):
        sensitive_paths = [str(item).strip() for item in raw_sensitive_paths if isinstance(item, str) and str(item).strip()]
    else:
        sensitive_paths = []

    preferred_profile_raw = str(step.get("preferred_profile", "")).strip().lower()
    preferred_profile = preferred_profile_raw if preferred_profile_raw in _KNOWN_PROFILES else None

    escalation_policy = str(step.get("escalation_policy", "")).strip().lower()
    if escalation_policy not in _KNOWN_ESCALATION_POLICIES:
        escalation_policy = "default"

    return {
        "task_kind": task_kind,
        "complexity": complexity,
        "risk": risk,
        "sensitive_paths": sensitive_paths,
        "preferred_profile": preferred_profile,
        "escalation_policy": escalation_policy,
    }


def _read_env_int(name: str, default: int, minimum: int = 1) -> int:
    """Read integer environment variable with deterministic fallback."""
    raw = os.environ.get(name)
    if raw is None or not raw.strip():
        return default
    try:
        value = int(raw.strip())
    except ValueError:
        print(f"[AUTOPILOT] 잘못된 {name}='{raw}', 기본값 {default} 사용")
        return default
    if value < minimum:
        print(f"[AUTOPILOT] {name}는 {minimum} 이상이어야 합니다. 기본값 {default} 사용")
        return default
    return value


# ---------------------------------------------------------------------------
# 내부 유틸
# ---------------------------------------------------------------------------

def _now() -> str:
    return datetime.utcnow().strftime("%Y-%m-%dT%H%M%SZ")


def _state_path(root: Path, task_id: str) -> Path:
    d = root / _AUTOPILOT_STATE_DIR
    d.mkdir(parents=True, exist_ok=True)
    return d / f"{task_id}.json"


def _load_state(root: Path, task_id: str) -> dict:
    p = _state_path(root, task_id)
    if p.exists():
        try:
            return json.loads(p.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            pass
    return {"task_id": task_id, "status": "pending", "steps": {}}


def _save_state(root: Path, task_id: str, state: dict) -> None:
    target = _state_path(root, task_id)
    target.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(
        mode="w",
        suffix=".tmp",
        prefix=f"{task_id}.",
        dir=str(target.parent),
        delete=False,
        encoding="utf-8",
    ) as tf:
        tf.write(json.dumps(state, ensure_ascii=False, indent=2))
        tf.flush()
        os.fsync(tf.fileno())
        temp_path = tf.name
    os.replace(temp_path, target)


def _recover_running_task_state_for_restart(state: dict) -> dict:
    """Convert only stale running task state into a fresh rerun baseline.

    A previous autopilot invocation may exit before it writes pipeline result or
    terminal status, leaving only the task state file in `running`. When a user
    explicitly reruns the same task, keep an audit trace but reset task-level
    timestamps so the new invocation is not reported as the old stale run.
    """
    if str(state.get("status") or "").lower() != "running":
        return state

    pid = state.get("pid")
    pid_state = _is_pid_running(pid if isinstance(pid, int) else None)
    if pid_state is True:
        raise RuntimeError(f"task already running with live pid: {pid}")
    if pid_state is None:
        raise RuntimeError(f"task running state exists but pid liveness is unknown: {pid}")
    stale_reason = (
        f"dead pid before re-run: {pid}"
        if isinstance(pid, int) and pid > 0
        else "missing pid before re-run"
    )

    recovered_at = _now()
    steps = state.get("steps")
    if not isinstance(steps, dict):
        steps = {}

    for step_state in steps.values():
        if isinstance(step_state, dict) and str(step_state.get("status") or "").lower() == "running":
            step_state["status"] = "hold"
            step_state["stale_reason"] = stale_reason

    steps["stale_recovery"] = {
        "status": "auto_hold",
        "reason": stale_reason,
        "recovered_at": recovered_at,
    }
    state["steps"] = steps
    state["previous_status"] = "running"
    state["stale_reason"] = stale_reason
    state["started_at"] = recovered_at
    state["finished_at"] = None
    return state


def _safe_current_branch(root: Path) -> str:
    try:
        proc = subprocess.run(
            ["git", "branch", "--show-current"],
            cwd=str(root),
            check=False,
            capture_output=True,
            text=True,
            timeout=5,
        )
    except Exception:
        return "-"
    branch = (proc.stdout or "").strip()
    return branch or "-"


def _step_state_is_simulated(step_state: object) -> bool:
    if not isinstance(step_state, dict):
        return False
    if step_state.get("simulated") is True:
        return True
    last_output = str(step_state.get("last_output") or "")
    return "[DRY-RUN]" in last_output


def _state_is_simulated(state: object) -> bool:
    if not isinstance(state, dict):
        return False
    if state.get("simulated") is True:
        return True
    steps = state.get("steps")
    if isinstance(steps, dict) and steps:
        return all(_step_state_is_simulated(step_state) for step_state in steps.values())
    return False


def _format_status_label(status: object, *, simulated: bool) -> str:
    label = str(status or "?")
    if simulated and label == "completed":
        return "completed (dry-run)"
    return label


def _run_record_is_simulated(record: object) -> bool:
    if not isinstance(record, dict):
        return False
    if record.get("simulated") is True or record.get("dry_run") is True:
        return True
    steps = record.get("steps")
    if isinstance(steps, dict) and steps:
        for step_state in steps.values():
            if _step_state_is_simulated(step_state):
                return True
    return False


def _staged_files(root: Path) -> list[str]:
    try:
        cp = subprocess.run(
            ["git", "diff", "--staged", "--name-only"],
            cwd=str(root),
            capture_output=True,
            text=True,
            check=False,
        )
    except OSError:
        return []
    if cp.returncode != 0:
        return []
    return [line.strip() for line in cp.stdout.splitlines() if line.strip()]


def _detect_executor(preferred: str) -> str:
    import shutil
    if preferred and preferred != "auto":
        normalized = preferred.strip().lower()
        if normalized == "cursor":
            normalized = "codex"
        if normalized not in {"codex", "gemini", "claude"}:
            raise RuntimeError(f"executor not supported: {preferred}")
        if not shutil.which(normalized):
            raise RuntimeError(f"executor not found: {normalized}")
        return normalized
    env_choice = os.environ.get("OMC_EXECUTOR", "").strip().lower()
    if env_choice in {"codex", "gemini", "claude"}:
        if not shutil.which(env_choice):
            raise RuntimeError(f"executor not found: {env_choice}")
        return env_choice
    for exe in ("codex", "gemini", "claude"):
        if shutil.which(exe):
            return exe
    raise RuntimeError("executor not found: install one of codex/gemini/claude or set OMC_EXECUTOR")


def _resolve_order(steps: list[dict]) -> list[dict]:
    """의존성(depends_on)을 고려한 토폴로지 정렬."""
    id_map = {s["id"]: s for s in steps}
    visited: set[str] = set()
    visiting: set[str] = set()
    order: list[dict] = []

    def visit(step_id: str) -> None:
        if step_id not in id_map:
            raise ValueError(f"unknown dependency: {step_id}")
        if step_id in visited:
            return
        if step_id in visiting:
            raise ValueError(f"cycle detected at step: {step_id}")
        visiting.add(step_id)
        for dep in id_map.get(step_id, {}).get("depends_on", []):
            visit(dep)
        visiting.remove(step_id)
        visited.add(step_id)
        order.append(id_map[step_id])

    for s in steps:
        visit(s["id"])
    return order


# ---------------------------------------------------------------------------
# expect 검증 (하네스 패턴)
# ---------------------------------------------------------------------------

def _run_expect_checks(
    root: Path,
    expect: dict,
    *,
    dry_run: bool = False,
) -> list[dict]:
    """expect 설정에 따라 검증 커맨드를 실행합니다.

    expect 포맷:
      {
        "files": ["src/Login.tsx", "src/Login.test.tsx"],
        "checks": [
          {"cmd": "npx jest Login --passWithNoTests", "label": "테스트", "timeout_sec": 60},
          {"cmd": "npx tsc --noEmit", "label": "타입 체크"}
        ]
      }

    Returns:
        [{"label": ..., "ok": bool, "output": ...}, ...] — 각 검증 결과
    """
    results: list[dict] = []

    # 파일 존재 체크
    for file_path in expect.get("files", []):
        target = root / file_path
        ok = dry_run or target.exists()
        results.append({
            "label": f"file_exists: {file_path}",
            "ok": ok,
            "output": "" if ok else f"파일 없음: {target}",
        })

    allowed_commands, policy_commands_defined = _load_allowed_commands(root)
    allowed_git_subcommands = _load_allowed_git_subcommands(root)

    # 셸 커맨드 체크
    for check in expect.get("checks", []):
        cmd = check.get("cmd", "").strip()
        label = check.get("label", cmd[:40])
        timeout = int(check.get("timeout_sec", 60))

        if not cmd:
            continue

        if dry_run:
            results.append({"label": label, "ok": True, "output": "[DRY-RUN]"})
            continue

        argv, parse_error = _resolve_expect_argv(cmd, allowed_commands)
        if parse_error:
            _warn_expect_compatibility(cmd, policy_commands_defined)
            results.append({"label": label, "ok": False, "output": parse_error})
            continue
        assert argv is not None
        if argv[0] == "git":
            if len(argv) < 2 or argv[1] not in allowed_git_subcommands:
                results.append({"label": label, "ok": False, "output": "허용되지 않은 git 서브커맨드"})
                continue
            if argv[1] == "remote":
                # read-only remote 조회만 허용: git remote, git remote -v, git remote --verbose
                if len(argv) == 2:
                    pass
                elif len(argv) == 3 and argv[2] in {"-v", "--verbose"}:
                    pass
                else:
                    results.append({"label": label, "ok": False, "output": "허용되지 않은 git remote 인자"})
                    continue

        try:
            proc = subprocess.run(
                argv,
                cwd=str(root),
                capture_output=True,
                text=True,
                timeout=timeout,
            )
            ok = proc.returncode == 0
            output = ((proc.stdout or "") + (proc.stderr or "")).strip()
            results.append({"label": label, "ok": ok, "output": output[:500]})
        except subprocess.TimeoutExpired:
            results.append({"label": label, "ok": False, "output": "[ERROR] 타임아웃"})
        except Exception as exc:
            results.append({"label": label, "ok": False, "output": f"[ERROR] {exc}"})

    return results


def _build_retry_prompt(
    original_prompt: str,
    attempt: int,
    failures: list[dict] | None = None,
    *,
    prev_verdict: str | None = None,
    prev_issues: str | None = None,
) -> str:
    """이전 시도 실패 컨텍스트 및/또는 직전 verdict를 프롬프트 앞에 주입합니다.

    - failures: 구체적 실패 목록 (기존 task retry 방식)
    - prev_verdict: critique/review 루프의 직전 VERDICT 값
    - prev_issues: 직전 critique가 지적한 이슈 텍스트 (재시도 시 맥락 제공)
    """
    # 내부 sentinel/object가 문자열화되어 사용자 프롬프트로 유출되는 것을 방지한다.
    safe_prev_verdict = prev_verdict.strip() if isinstance(prev_verdict, str) else ""
    if not failures and not safe_prev_verdict and not prev_issues:
        return original_prompt

    lines: list[str] = []

    if safe_prev_verdict:
        lines += [
            f"[재시도 {attempt}회차] 직전 VERDICT: {safe_prev_verdict}.",
            "이 판정을 극복하기 위해 다른 관점으로 재검토하세요.",
            "",
        ]

    if prev_issues:
        lines += [
            "[이전 critique 지적 사항 — 이 이슈들이 해소됐는지 반드시 확인하세요]",
            prev_issues,
            "",
        ]

    if failures:
        lines += [
            f"[이전 시도 {attempt}회 실패 — 아래 문제를 반드시 해결하세요]",
            "",
        ]
        for f in failures:
            lines.append(f"- {f['label']}: FAIL")
            if f.get("output"):
                snippet = "\n".join(f["output"].splitlines()[:5])
                lines.append(f"  출력: {snippet}")
        lines += ["", "위 문제를 해결하면서 아래 작업을 수행하세요:", ""]

    lines.append(original_prompt)
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# 스텝 실행
# ---------------------------------------------------------------------------

def _resolve_cost_model(executor: str, model_profile: str) -> str:
    normalized_executor = executor.strip().lower()
    normalized_profile = model_profile.strip().lower()
    if normalized_executor == "codex":
        settings = omc_exec._resolve_codex_profile_settings(normalized_profile)
        return str(settings.get("model") or "")
    if normalized_executor == "gemini":
        return omc_exec._GEMINI_MODEL_MAP.get(normalized_profile, "")
    if normalized_executor == "claude":
        return omc_exec._CLAUDE_MODEL_MAP.get(normalized_profile, "")
    return ""


def _extract_cost_info(executor: str, stdout: str, *, model_profile: str = "mini_default") -> dict | None:
    try:
        usage = omc_cost._parse_llm_usage(executor, stdout)
        if usage is None:
            # Gemini CLI -p --output-format json 형식: stats.models.*.tokens
            usage = _parse_gemini_cli_stats(stdout)
        if usage is None:
            return None
        cost_model = _resolve_cost_model(executor, model_profile)
        cost = None
        if omc_cost._supports_cost_estimation(executor, cost_model):
            cost = omc_cost._estimate_cost_usd(usage, cost_model)
        return {"token_usage": usage, "cost_estimate": cost}
    except Exception as exc:
        print(f"[COST] 파싱 실패 (무시됨): {exc}", file=sys.stderr)
        return None


def _parse_gemini_cli_stats(stdout: str) -> dict | None:
    try:
        data = json.loads(stdout)
        models = (data.get("stats") or {}).get("models") or {}
        total_input = total_output = 0
        for model_stats in models.values():
            tokens = model_stats.get("tokens") or {}
            total_input += int(tokens.get("prompt") or tokens.get("input") or 0)
            total_output += int(tokens.get("candidates") or 0)
        if total_input or total_output:
            return {
                "input_tokens": total_input,
                "output_tokens": total_output,
                "cache_read_tokens": 0,
                "cache_write_tokens": 0,
            }
    except (json.JSONDecodeError, KeyError, TypeError, AttributeError):
        pass
    return None


def _run_step(
    root: Path,
    step: dict,
    *,
    executor: str,
    timeout_sec: int,
    prompt_override: str | None = None,
    isolated: bool = False,
) -> tuple[int, str, dict | None, dict | None]:
    """omc_exec.py를 통해 스텝 프롬프트를 실행합니다.

    Args:
        isolated: True이면 fresh context로 실행합니다. 기본값은 False입니다.

    Returns:
        (returncode, output_text, cost_info, step_runtime)
    """
    exec_script = Path(__file__).resolve().parent / "omc_exec.py"
    if not exec_script.exists():
        return 1, f"[ERROR] omc_exec.py 없음: {exec_script}", None

    prompt = (prompt_override or step.get("prompt", "")).strip()
    if not prompt:
        return 1, "[ERROR] 스텝에 prompt가 없습니다.", None

    step_id = str(step.get("id", "")).strip().lower()
    metadata = _normalize_step_metadata(step)
    task_kind = str(metadata["task_kind"])
    retry_count = 2 if "retry" in step_id else 0
    routing = omc_exec.resolve_task_routing(
        task_kind=task_kind,
        request_text=prompt,
        retry_count=retry_count,
        complexity=str(metadata["complexity"]),
        risk=str(metadata["risk"]),
        sensitive_paths=list(metadata["sensitive_paths"]),
        preferred_profile=str(metadata["preferred_profile"]) if metadata["preferred_profile"] is not None else None,
    )
    task_kind = routing["task_kind"]
    model_profile = routing["model_profile"]
    routing_policy = routing["routing_policy"]
    routing_reason_codes = list(routing.get("routing_reason_codes") or [])
    routing_reason_summary = str(routing.get("routing_reason_summary") or "").strip()

    prompt_file = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            suffix=".md",
            prefix="omc_run_step_",
            delete=False,
            encoding="utf-8",
        ) as tf:
            tf.write(prompt)
            prompt_file = tf.name
        with tempfile.NamedTemporaryFile(
            mode="w",
            suffix=".json",
            prefix="omc_run_step_raw_",
            delete=False,
            encoding="utf-8",
        ) as raw_tf:
            raw_output_file = raw_tf.name

        cmd = [
            sys.executable,
            str(exec_script),
            "--target", str(root),
            "--prompt-file", str(prompt_file),
            "--executor", executor,
            "--model-profile", model_profile,
            "--execution-mode", "headless",
            "--timeout-sec", str(timeout_sec),
            "--task-kind", task_kind,
        ]
        if isolated:
            cmd.append("--fresh-context")

        env = os.environ.copy()
        env["OMC_RAW_OUTPUT_FILE"] = raw_output_file
        proc = subprocess.run(
            cmd,
            cwd=str(root),
            capture_output=True,
            text=True,
            timeout=timeout_sec + 30,
            env=env,
        )
        output = (proc.stdout or "") + (proc.stderr or "")
        raw_output = ""
        try:
            raw_output = Path(raw_output_file).read_text(encoding="utf-8")
        except OSError:
            raw_output = ""
        cost_info = _extract_cost_info(
            executor,
            raw_output or (proc.stdout or ""),
            model_profile=model_profile,
        )
        step_runtime = {
            "task_kind": task_kind,
            "model_profile": model_profile,
            "routing_policy": routing_policy,
            "routing_reason_codes": routing_reason_codes,
            "routing_reason_summary": routing_reason_summary,
        }
        return int(proc.returncode), output.strip(), cost_info, step_runtime
    except subprocess.TimeoutExpired:
        return 1, "[ERROR] 타임아웃 초과", None, None
    except Exception as exc:
        return 1, f"[ERROR] 실행 예외: {exc}", None, None
    finally:
        if prompt_file:
            try:
                Path(prompt_file).unlink()
            except OSError:
                pass
        if "raw_output_file" in locals() and raw_output_file:
            try:
                Path(raw_output_file).unlink()
            except OSError:
                pass


def _collect_overview_run_records(root: Path) -> list[dict]:
    run_records: list[dict] = []
    seen_fingerprints: set[str] = set()

    current_path = root / _PIPELINE_RESULT_PATH
    if current_path.exists():
        try:
            current_data = json.loads(current_path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            current_data = None
        if isinstance(current_data, dict):
            seen_fingerprints.add(_overview_run_fingerprint(current_data))
            run_records.append(current_data)

    runs_dir = root / _RUNS_DIR
    if runs_dir.exists():
        run_dirs = sorted(runs_dir.iterdir(), reverse=True)
        for d in run_dirs:
            result_path = d / "result.json"
            if not result_path.exists():
                continue
            try:
                data = json.loads(result_path.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, OSError):
                continue
            fingerprint = _overview_run_fingerprint(data)
            if fingerprint in seen_fingerprints:
                continue
            seen_fingerprints.add(fingerprint)
            run_records.append(data)

    return run_records


def _build_task_run_result(
    *,
    root: Path,
    task: dict,
    state: dict,
    executor: str,
) -> dict[str, object]:
    result: dict[str, object] = {
        "task_id": str(task.get("id") or state.get("task_id") or "").strip(),
        "instruction": str(
            task.get("instruction") or task.get("prompt") or task.get("title") or ""
        ).strip(),
        "mode": str(task.get("mode") or state.get("mode") or "task").strip(),
        "status": str(state.get("status") or "unknown"),
        "branch": _safe_current_branch(root),
        "executor": executor,
        "started_at": state.get("started_at"),
        "finished_at": state.get("finished_at"),
        "simulated": bool(state.get("simulated") is True),
        "completion_requires_real_runs": bool(state.get("completion_requires_real_runs") is True),
        "steps": state.get("steps", {}),
    }
    for key in (
        "title",
        "benchmark_source_type",
        "policy_pair",
        "comparison_scope",
        "baseline_response_sample",
        "candidate_response_sample",
        "dataset_excluded_from_readiness",
        "operational_validation_stage",
        "operational_validation_goal",
    ):
        value = task.get(key)
        if value not in (None, ""):
            result[key] = value
    return result


# ---------------------------------------------------------------------------
# 커맨드: run
# ---------------------------------------------------------------------------

def cmd_run(
    root: Path,
    task_file: Path,
    *,
    dry_run: bool = False,
    resume_failed: bool = False,
) -> int:
    """Run an autopilot task file and persist step-by-step execution state."""
    if not task_file.exists():
        print(f"[AUTOPILOT] 태스크 파일 없음: {task_file}")
        return 1

    try:
        task = json.loads(task_file.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        print(f"[AUTOPILOT] 태스크 파일 파싱 오류: {exc}")
        return 1

    task_id = task.get("id") or task_file.stem
    title = task.get("title", task_id)
    require_clean_scope = bool(task.get("require_clean_scope", False))
    executor_pref = task.get("executor", "auto")
    max_retries = int(task.get("max_retries", _DEFAULT_MAX_RETRIES))
    completion_requires_real_runs = bool(task.get("completion_requires_real_runs") is True)
    effective_resume_failed = resume_failed or bool(task.get("resume_failed") is True)
    steps_raw = task.get("steps", [])

    if not steps_raw:
        print(f"[AUTOPILOT] 스텝이 없습니다: {task_file}")
        return 1

    if require_clean_scope:
        staged_files = _staged_files(root)
        if staged_files:
            preview = ", ".join(staged_files[:3])
            if len(staged_files) > 3:
                preview += f" 외 {len(staged_files) - 3}개"
            print("[AUTOPILOT] clean scope required: 현재 staged 변경이 있어 observed 수집을 시작할 수 없습니다.")
            print(f"             staged: {preview}")
            return 1

    try:
        executor = _detect_executor(executor_pref)
    except RuntimeError as exc:
        print(f"[AUTOPILOT] {exc}")
        return 1
    try:
        steps = _resolve_order(steps_raw)
    except ValueError as exc:
        print(f"[AUTOPILOT] 태스크 의존성 오류: {exc}")
        return 1

    try:
        state = _recover_running_task_state_for_restart(_load_state(root, task_id))
    except RuntimeError as exc:
        print(f"[AUTOPILOT] {exc}")
        return 1

    print(f"\n[AUTOPILOT] ▶ 태스크 시작: {title}")
    print(f"           executor={executor}  스텝={len(steps)}개  max_retries={max_retries}")
    if dry_run:
        print("           [DRY-RUN] 실제 실행 없이 계획만 출력합니다.\n")

    state["task_id"] = task_id
    state["task_file"] = str(task_file.relative_to(root)) if task_file.is_relative_to(root) else str(task_file)
    state["title"] = title
    state["executor"] = executor
    state["pid"] = os.getpid()
    state["started_at"] = state.get("started_at") or _now()
    state["status"] = "running"
    state["simulated"] = bool(dry_run)
    if "completion_requires_real_runs" in task:
        state["completion_requires_real_runs"] = completion_requires_real_runs
    if "steps" not in state:
        state["steps"] = {}
    # Persist the task-level running state before any step blocks on external execution.
    _save_state(root, task_id, state)
    observed_count_before: int | None = None
    if completion_requires_real_runs and not dry_run:
        observed_count_before = int(
            _build_overview_kpi_summary(_collect_overview_run_records(root))["observed_sample_count"]
        )

    failed_count = 0

    for step in steps:
        sid = step["id"]
        step_title = step.get("title", sid)
        timeout_sec = int(step.get("timeout_sec", _DEFAULT_TIMEOUT_SEC))

        # dry-run으로만 완료된 step은 실제 실행에서 다시 돈다.
        existing_step_state = state["steps"].get(sid, {})
        if existing_step_state.get("status") == "completed":
            if dry_run or not _step_state_is_simulated(existing_step_state):
                print(f"  [SKIP] {sid}: {step_title} (이미 완료)")
                continue
            print(f"  [REPLAY] {sid}: {step_title} (dry-run 완료 상태라 실제 실행)")

        # 실패 스텝 재실행 정책:
        # - 기본값(False): 이전 실패 상태를 유지(기존 동작)
        # - resume_failed=True 또는 task.resume_failed=True: 이전 실패 스텝 재실행
        if state["steps"].get(sid, {}).get("status") == "failed":
            if not effective_resume_failed:
                print(f"  [SKIP] {sid}: {step_title} (이전 실패 — 재실행 없음)")
                failed_count += 1
                continue
            print(f"  [RETRY] {sid}: {step_title} (이전 실패 스텝 재실행)")

        # depends_on 검사 — 의존 스텝이 completed가 아니면 블록
        blocked = False
        for dep in step.get("depends_on", []):
            if state["steps"].get(dep, {}).get("status") != "completed":
                print(f"  [BLOCK] {sid}: 의존 스텝 '{dep}' 미완료 — 건너뜀")
                state["steps"][sid] = {"status": "blocked", "blocked_by": dep}
                failed_count += 1
                blocked = True
                break

        if blocked:
            _save_state(root, task_id, state)
            continue

        print(f"\n  [STEP {sid}] {step_title}")
        prompt_preview = textwrap.shorten(step.get("prompt", ""), width=80)
        print(f"  프롬프트: {prompt_preview}")

        expect_cfg = step.get("expect", {})
        if expect_cfg and not dry_run:
            expect_items = (
                expect_cfg.get("files", []) if isinstance(expect_cfg, dict) else []
            )
            checks = (
                expect_cfg.get("checks", []) if isinstance(expect_cfg, dict) else []
            )
            if expect_items or checks:
                print(f"  검증: 파일 {len(expect_items)}개, 커맨드 {len(checks)}개")

        step_state: dict = {
            "status": "running",
            "started_at": _now(),
            "attempt": 0,
        }
        previous_step_state = state["steps"].get(sid, {})
        if isinstance(previous_step_state, dict):
            previous_stale_reason = previous_step_state.get("stale_reason")
            if isinstance(previous_stale_reason, str) and previous_stale_reason.strip():
                step_state["stale_reason"] = previous_stale_reason
        if dry_run:
            step_state["simulated"] = True
        # Persist the step-level running state before handing control to the executor.
        state["steps"][sid] = dict(step_state)
        _save_state(root, task_id, state)

        success = False
        last_failures: list[dict] = []  # 이전 attempt의 expect 실패 목록
        original_prompt = step.get("prompt", "")

        for attempt in range(1, max_retries + 2):
            step_state["attempt"] = attempt

            # 재시도 시 실패 컨텍스트를 프롬프트에 주입
            active_prompt = (
                _build_retry_prompt(original_prompt, attempt - 1, last_failures)
                if last_failures else original_prompt
            )
            # step 복사본에 수정된 프롬프트를 넣어 _run_step에 전달
            step_with_prompt = {**step, "prompt": active_prompt}

            if dry_run:
                print(f"  [DRY-RUN] 스텝 실행 시뮬레이션 (attempt {attempt})")
                rc, output = 0, "[DRY-RUN] 시뮬레이션 성공"
            elif bool(step.get("expect_only") is True):
                print(f"  실행 생략 (attempt {attempt}/{max_retries + 1}) — expect_only")
                rc, output, cost_info, step_runtime = 0, "[EXPECT-ONLY] executor skipped", None, None
            else:
                if last_failures:
                    print(f"  재시도 (attempt {attempt}) — 이전 실패 컨텍스트 주입됨")
                else:
                    print(f"  실행 중 (attempt {attempt}/{max_retries + 1})...")
                rc, output, cost_info, step_runtime = _run_step(
                    root,
                    step_with_prompt,
                    executor=executor,
                    timeout_sec=timeout_sec,
                    isolated=bool(step.get("isolated", False)),
                )
                if cost_info is not None:
                    step_state["token_usage"] = cost_info["token_usage"]
                    step_state["cost_estimate"] = cost_info["cost_estimate"]
                if step_runtime is not None:
                    step_state.update(step_runtime)

            step_state["last_output"] = output[:2000]

            # LLM 실행 자체 실패
            if rc != 0:
                last_failures = [{"label": "LLM 실행 실패", "output": output[:300]}]
                print(f"  ❌ LLM 실행 실패 (attempt {attempt}): {output[:150]}")
                if attempt <= max_retries:
                    print("  재시도합니다...")
                continue

            # expect 검증
            expect_cfg = step.get("expect") or {}
            if expect_cfg and not dry_run:
                check_results = _run_expect_checks(root, expect_cfg, dry_run=dry_run)
                step_state["expect_results"] = check_results
                failures = [r for r in check_results if not r["ok"]]
                if failures:
                    last_failures = failures
                    fail_labels = ", ".join(f["label"] for f in failures)
                    print(f"  ❌ expect 검증 실패 (attempt {attempt}): {fail_labels}")
                    for f in failures:
                        if f.get("output"):
                            print(f"     {f['label']}: {f['output'][:100]}")
                    if attempt <= max_retries:
                        print("  재시도합니다...")
                    continue
                else:
                    print(f"  ✅ expect 검증 통과 ({len(check_results)}개)")

            step_state["status"] = "completed"
            step_state["completed_at"] = _now()
            print(f"  ✅ 완료 (attempt {attempt})")
            success = True
            break

        if not success:
            step_state["status"] = "failed"
            step_state["failed_at"] = _now()
            failed_count += 1
            print(f"  [FAIL] {sid}: {max_retries + 1}번 시도 후 실패")

        state["steps"][sid] = step_state
        _save_state(root, task_id, state)

    # 최종 상태 기록
    all_done = all(
        state["steps"].get(s["id"], {}).get("status") == "completed"
        for s in steps
    )
    state["status"] = "completed" if all_done else "failed"
    state["finished_at"] = _now()
    if all_done and not dry_run:
        run_result = _build_task_run_result(root=root, task=task, state=state, executor=executor)
        _save_pipeline_result(root, run_result)
        if completion_requires_real_runs and observed_count_before is not None:
            observed_count_after = int(
                _build_overview_kpi_summary(_collect_overview_run_records(root))["observed_sample_count"]
            )
            if observed_count_after <= observed_count_before:
                all_done = False
                state["status"] = "failed"
                state["failure_reason"] = "completion_requires_real_runs_unsatisfied"
                state["finished_at"] = _now()
                run_result["status"] = "failed"
                run_result["failure_category"] = "completion_requires_real_runs_unsatisfied"
                run_result["next_kpi_blocker"] = "insufficient_observed_samples"
                run_result["readiness_status_line"] = (
                    "not ready: completion requires real observed runs but observed sample count did not increase"
                )
                _save_pipeline_result(root, run_result)
    _save_state(root, task_id, state)

    print(f"\n[AUTOPILOT] {'✅ 태스크 완료' if all_done else '❌ 태스크 실패'}: {title}")
    print(
        f"           완료={sum(1 for s in state['steps'].values() if s.get('status') == 'completed')}  "
        f"실패/블록={failed_count}"
    )
    print(f"           상태 저장: {_state_path(root, task_id).relative_to(root)}")
    return 0 if all_done else 1


# ---------------------------------------------------------------------------
# 커맨드: new
# ---------------------------------------------------------------------------

def cmd_new(root: Path, task_id: str, title: str) -> int:
    """예시 태스크 파일을 생성합니다."""
    tasks_dir = root / _TASKS_DIR
    tasks_dir.mkdir(parents=True, exist_ok=True)
    out = tasks_dir / f"{task_id}.json"

    if out.exists():
        print(f"[AUTOPILOT] 이미 존재합니다: {out.relative_to(root)}")
        return 1

    template = {
        "id": task_id,
        "title": title,
        "executor": "auto",
        "max_retries": 1,
        "steps": [
            {
                "id": "s1",
                "title": "첫 번째 스텝",
                "prompt": "여기에 LLM에 전달할 프롬프트를 작성하세요.",
                "depends_on": [],
                "timeout_sec": 120,
                "expect": {
                    "files": [],
                    "checks": [
                        {"cmd": "echo 'expect 검증 예시 — 실제 커맨드로 교체하세요'", "label": "샘플 체크", "timeout_sec": 10}
                    ]
                },
            },
            {
                "id": "s2",
                "title": "두 번째 스텝",
                "prompt": "s1 결과를 바탕으로 추가 작업을 수행하세요.",
                "depends_on": ["s1"],
                "timeout_sec": 120,
            },
        ],
    }
    out.write_text(json.dumps(template, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"[AUTOPILOT] ✅ 태스크 파일 생성: {out.relative_to(root)}")
    print(f"            편집 후 실행: python3 scripts/omc_autopilot.py run --task {out.relative_to(root)}")
    return 0


# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# 커맨드: pipeline-status
# ---------------------------------------------------------------------------

def _build_step_detail(ss: dict, rich_markup: bool = False) -> str:
    """pipeline-status 전용 — 단계 상세 정보 문자열을 생성한다."""
    parts = []
    if ss.get("output_preview"):
        parts.append(ss["output_preview"])
    if ss.get("verdict"):
        parts.append(f"VERDICT: {ss['verdict']}")
    if ss.get("error_message"):
        err = ss["error_message"]
        parts.append(f"[red]ERR: {err}[/red]" if rich_markup else f"ERR: {err}")
    return "  |  ".join(parts) or "-"


_STEP_ICON = {
    "completed": "✅",
    "failed": "❌",
    "running": "⏳",
    "skipped": "⏭ ",
    "blocked": "🔒",
    "auto_hold": "⏸",
}

_STATUS_ICON_MAP = {
    "completed": "✅",
    "failed": "❌",
    "running": "⏳",
    "aborted": "🛑",
    "canceled": "🚫",
    "cancelled": "🚫",
    "timeout": "⌛",
    "pending": "⏸",
    "paused": "⏸",
    "hold": "⏸",
    "held": "⏸",
    "blocked": "🔒",
    "auto_hold": "⏸",
}
# Backward-compat alias for older tests/callers.
_PIPELINE_STATUS_ICON = _STATUS_ICON_MAP


def cmd_pipeline_status(root: Path, watch: bool = False, interval: int = 2, recover: bool = False) -> int:
    """pipeline_run_result.json 기반 파이프라인 진행 상황 출력.

    Args:
        watch: True이면 interval초 간격으로 화면 갱신
        interval: --watch 갱신 주기(초). 1 미만이면 exit 1.
    """
    if watch and interval < 1:
        print("[PIPELINE STATUS] --interval은 1 이상이어야 합니다.", file=sys.stderr)
        return 1

    if watch:
        _clear = "\033[2J\033[H" if sys.stdout.isatty() else ""
        try:
            while True:
                if _clear:
                    print(_clear, end="")
                # 반환값 1(JSON 파싱 일시 오류)은 silent skip — 다음 tick에 재시도
                _cmd_pipeline_status_once(root, recover=recover)
                time.sleep(interval)
        except KeyboardInterrupt:
            print("\n[PIPELINE STATUS] 모니터링 종료")
            return 0
    return _cmd_pipeline_status_once(root, recover=recover)


def _cmd_pipeline_status_once(root: Path, recover: bool = False) -> int:
    """pipeline_run_result.json 기반 파이프라인 진행 상황 출력."""
    result_path = root / _PIPELINE_RESULT_PATH

    if not result_path.exists():
        print("[PIPELINE STATUS] 파이프라인 실행 기록 없음")
        return 0

    try:
        data = json.loads(result_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as e:
        print(f"[PIPELINE STATUS] 결과 파일 손상됨: {result_path} — {e}", file=sys.stderr)
        return 1

    if data.get("status") == "running":
        pid = data.get("pid")
        stale_reason = None
        pid_state_unknown = False
        if isinstance(pid, int):
            pid_running = _is_pid_running(pid)
            if pid_running is False:
                stale_reason = f"pipeline pid not running: {pid}"
            elif pid_running is None:
                pid_state_unknown = True
        else:
            stale_reason = "legacy running record without pid"
        if pid_state_unknown:
            print(f"[PIPELINE STATUS] stale 복구 보류: pid 상태 확인 불가(pid={pid})", file=sys.stderr)
        elif stale_reason:
            if recover:
                data["status"] = "hold"
                data["finished_at"] = _now()
                steps = data.get("steps")
                if isinstance(steps, dict):
                    steps["stale_recovery"] = {
                        "status": "auto_hold",
                        "reason": stale_reason,
                    }
                _save_pipeline_result(root, data)
            else:
                # 상태 조회는 read-only 유지: 자동 상태 변조 금지
                print(f"[PIPELINE STATUS] stale 감지(수동 복구 필요): {stale_reason}", file=sys.stderr)

    try:
        from rich.console import Console
        from rich.table import Table
        _use_rich = True
    except ImportError:
        _use_rich = False

    status = data.get("status", "?")
    status_icon = _STATUS_ICON_MAP.get(status, "❓")
    print(
        f"\n{status_icon} OMC Pipeline Status  [{status}]  mode={data.get('mode', '?')}\n"
        f"   branch={data.get('branch', '?')}  executor={data.get('executor', '?')}\n"
        f"   started={data.get('started_at', '-')}  finished={data.get('finished_at', '-')}\n"
    )

    steps = data.get("steps", {})
    if not steps:
        print("   (단계 기록 없음)")
        return 0

    if _use_rich:
        console = Console()
        table = Table(show_header=True, header_style="bold")
        table.add_column("단계", style="cyan", min_width=12)
        table.add_column("상태", min_width=10)
        table.add_column("상세", min_width=30)
        for step_name, ss in steps.items():
            s = ss.get("status", "?")
            icon = _STEP_ICON.get(s, "❓")
            table.add_row(step_name, f"{icon} {s}", _build_step_detail(ss, rich_markup=True))
        console.print(table)
    else:
        col_w = max((len(n) for n in steps), default=10) + 2
        print(f"  {'단계':<{col_w}} {'상태':<12} 상세")
        print(f"  {'-'*col_w} {'-'*12} {'-'*30}")
        for step_name, ss in steps.items():
            s = ss.get("status", "?")
            icon = _STEP_ICON.get(s, "❓")
            print(f"  {step_name:<{col_w}} {icon} {s:<10} {_build_step_detail(ss)}")

    print()
    return 0


# ---------------------------------------------------------------------------
# 커맨드: benchmark-report
# ---------------------------------------------------------------------------

def _parse_pipeline_timestamp(value: object) -> datetime | None:
    """Parse ISO-like timestamp to UTC datetime, returning None on invalid input."""
    if not isinstance(value, str) or not value.strip():
        return None
    raw = value.strip()
    if raw.endswith("Z"):
        raw = raw[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(raw)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _build_benchmark_report(data: dict) -> dict:
    """pipeline_run_result.json을 비교 가능한 최소 벤치마크 지표로 변환한다."""
    steps = data.get("steps") or {}
    if not isinstance(steps, dict):
        steps = {}

    started = _parse_pipeline_timestamp(data.get("started_at"))
    finished = _parse_pipeline_timestamp(data.get("finished_at"))
    missing_timestamps = [name for name, parsed in (("started_at", started), ("finished_at", finished)) if parsed is None]
    quality_failures: list[str] = []
    raw_started = data.get("started_at")
    raw_finished = data.get("finished_at")
    if raw_started and started is None:
        quality_failures.append("invalid_started_at")
    elif started is None:
        quality_failures.append("missing_started_at")
    if raw_finished and finished is None:
        quality_failures.append("invalid_finished_at")
    elif finished is None:
        quality_failures.append("missing_finished_at")
    duration_sec = (
        int((finished - started).total_seconds())
        if started is not None and finished is not None
        else None
    )

    total_steps = len(steps)
    completed_steps = sum(1 for step in steps.values() if step.get("status") == "completed")
    failed_statuses = {"blocked", "retry_exhausted", "timeout", "canceled", "paused", "failed", "aborted"}
    failed_steps = sum(
        1
        for step in steps.values()
        if str(step.get("status", "")).startswith("failed")
        or step.get("status") in failed_statuses
    )
    retry_step_count = sum(1 for name in steps if "retry" in name)
    retry_attempt_count = sum(
        max(0, int(step.get("attempt", 1) or 1) - 1)
        for step in steps.values()
        if str(step.get("attempt", "")).isdigit()
    )
    retry_count = retry_step_count + retry_attempt_count
    had_reroute = any(
        str(step.get("decision") or "").strip().lower() == "reroute"
        or bool(str(step.get("reroute_target") or "").strip())
        for step in steps.values()
        if isinstance(step, dict)
    )
    recovered_after_retry = retry_step_count > 0 and data.get("status") == "completed"

    final_verdict = None
    for step in reversed(list(steps.values())):
        if step.get("verdict"):
            final_verdict = step.get("verdict")
            break

    failure_category = None
    failure_class_breakdown: dict[str, int] = {}
    if data.get("status") != "completed":
        for name, step in steps.items():
            step_status = step.get("status")
            step_is_failure = _step_counts_as_failure(step if isinstance(step, dict) else {})
            if step_is_failure and failure_category is None:
                failure_category = f"{name}:{step_status or 'unknown'}"
            if step_is_failure:
                inferred_failure_class = _infer_failure_class(
                    step_name=str(name),
                    step=step if isinstance(step, dict) else {},
                    run_status=str(data.get("status") or ""),
                )
                if inferred_failure_class:
                    failure_class_breakdown[inferred_failure_class] = (
                        failure_class_breakdown.get(inferred_failure_class, 0) + 1
                    )
        if failure_category is None:
            failure_category = str(data.get("status") or "unknown")

    return {
        "status": data.get("status"),
        "pipeline_success": data.get("status") == "completed",
        "quality_success": final_verdict in {"APPROVE", "APPROVE WITH NOTES"},
        "mode": data.get("mode"),
        "executor": data.get("executor"),
        "branch": data.get("branch"),
        "operational_validation_stage": data.get("operational_validation_stage"),
        "operational_validation_goal": data.get("operational_validation_goal"),
        "started_at": data.get("started_at"),
        "finished_at": data.get("finished_at"),
        "duration_sec": duration_sec,
        "is_complete": data.get("status") == "completed" and not missing_timestamps,
        "missing_timestamps": missing_timestamps,
        "data_quality_status": quality_failures[0] if quality_failures else "ok",
        "data_quality_failures": quality_failures,
        "total_steps": total_steps,
        "completed_steps": completed_steps,
        "failed_steps": failed_steps,
        "retry_count": retry_count,
        "had_reroute": had_reroute,
        "recovered_after_retry": recovered_after_retry,
        "success_rate": (completed_steps / total_steps) if total_steps else 0,
        "final_verdict": final_verdict,
        "failure_category": failure_category,
        "failure_class_breakdown": failure_class_breakdown,
        "baseline_comparison_status": data.get("baseline_comparison_status"),
        "next_kpi_blocker": data.get("next_kpi_blocker"),
        "readiness_status_line": data.get("readiness_status_line"),
        "baseline_comparison_line": data.get("baseline_comparison_line"),
        "cost_estimate": None,
        "token_usage": None,
        "executor_cost_source": None,
        "total_cost_usd": (
            sum(s.get("cost_estimate") or 0 for s in steps.values() if isinstance(s.get("cost_estimate"), (int, float)))
            if any(isinstance(s.get("cost_estimate"), (int, float)) for s in steps.values())
            else None
        ),
        "total_tokens": (
            sum(
                (s.get("token_usage") or {}).get("input_tokens", 0)
                + (s.get("token_usage") or {}).get("output_tokens", 0)
                for s in steps.values()
                if s.get("token_usage")
            )
            if any(s.get("token_usage") for s in steps.values())
            else None
        ),
    }


def cmd_benchmark_report(
    root: Path,
    *,
    result_file: Path | None = None,
    output_format: str = "json",
) -> int:
    """Print normalized benchmark report from pipeline result JSON."""
    if output_format != "json":
        print("[BENCHMARK] 지원하지 않는 format입니다. v1은 json만 지원합니다.", file=sys.stderr)
        return 1

    path = result_file or _get_result_path(root)
    if not path.is_absolute():
        path = root / path
    if not path.exists():
        print(f"[BENCHMARK] 결과 파일 없음: {path}", file=sys.stderr)
        return 1

    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        print(f"[BENCHMARK] 결과 파일 JSON 파싱 실패: {path} — {exc}", file=sys.stderr)
        return 1

    report = _build_benchmark_report(data)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


# 커맨드: status
# ---------------------------------------------------------------------------

def cmd_status(root: Path, task_id: str | None = None) -> int:
    """Print autopilot task history.

    Defaults:
    - OMC_AUTOPILOT_STATUS_LIMIT: 20 (show most recent N records)
    """
    state_dir = root / _AUTOPILOT_STATE_DIR
    if not state_dir.exists():
        print("[AUTOPILOT] 실행 기록 없음")
        return 0

    files = sorted(state_dir.glob("*.json"), reverse=True)
    if task_id:
        # task_id 조회는 recent limit보다 먼저 적용해 특정 실행 이력 누락을 방지한다.
        files = [f for f in files if f.stem == task_id]
        if not files:
            print(f"[AUTOPILOT] '{task_id}' 기록 없음")
            return 1
    else:
        limit = _read_env_int("OMC_AUTOPILOT_STATUS_LIMIT", _DEFAULT_STATUS_LIMIT, minimum=1)
        files = files[:limit]
    if not files:
        print("[AUTOPILOT] 실행 기록 없음")
        return 0

    for f in files:
        try:
            s = json.loads(f.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            print(f"[AUTOPILOT] JSON 파싱 실패: {f.name} — {exc}")
            continue
        simulated = _state_is_simulated(s)
        status_label = _format_status_label(s.get("status", "?"), simulated=simulated)
        status_icon = _STATUS_ICON_MAP.get(str(s.get("status", "")).lower(), "❓")
        print(f"\n{status_icon} [{status_label}] {s.get('title', f.stem)}")
        print(f"   id={s.get('task_id', f.stem)}  executor={s.get('executor', '?')}")
        print(f"   시작: {s.get('started_at', '-')}  완료: {s.get('finished_at', '-')}")
        for sid, ss in s.get("steps", {}).items():
            icon = _STATUS_ICON_MAP.get(str(ss.get("status", "")).lower(), "❓")
            step_label = _format_status_label(
                ss.get("status", "?"),
                simulated=_step_state_is_simulated(ss),
            )
            print(f"   {icon} {sid}: {step_label} (시도 {ss.get('attempt', '-')})")
    return 0



# ---------------------------------------------------------------------------
# 커맨드: runs
# ---------------------------------------------------------------------------

_RUNS_DIR = ".omc/runs"


def cmd_runs(
    root: Path,
    *,
    limit: int = 20,
    branch_filter: str | None = None,
    status_filter: str | None = None,
) -> int:
    """Print pipeline run history from .omc/runs/."""
    runs_dir = root / _RUNS_DIR
    if not runs_dir.exists():
        print("[RUNS] 실행 기록 없음")
        return 0

    run_dirs = sorted(runs_dir.iterdir(), reverse=True)
    entries: list[dict] = []
    for d in run_dirs:
        result_path = d / "result.json"
        if not result_path.exists():
            continue
        try:
            data = json.loads(result_path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            continue
        data["_run_id"] = d.name
        if branch_filter and branch_filter not in (data.get("branch") or ""):
            continue
        if status_filter and status_filter != (data.get("status") or ""):
            continue
        entries.append(data)
        if len(entries) >= limit:
            break

    if not entries:
        print("[RUNS] 조건에 맞는 실행 기록 없음")
        return 0

    for e in entries:
        status = e.get("status", "?")
        icon = _STATUS_ICON_MAP.get(status.lower(), "❓")
        branch = e.get("branch", "-")
        executor = e.get("executor", "-")
        started = e.get("started_at", "-")
        steps = e.get("steps") or {}
        verdict = (steps.get("review") or {}).get("verdict")
        verdict_str = f"  verdict={verdict}" if verdict else ""
        cost = e.get("total_cost_usd")
        tokens = e.get("total_tokens")
        cost_str = f"  cost=${cost:.4f}" if isinstance(cost, (int, float)) else ""
        tokens_str = f"  tokens={tokens}" if tokens is not None else ""
        print(f"{icon} [{status}] {branch}  executor={executor}  started={started}{verdict_str}{cost_str}{tokens_str}")
        print(f"   run_id={e['_run_id']}")
    return 0


def _get_current_step_name(steps: object) -> str:
    if not isinstance(steps, dict) or not steps:
        return "-"
    for step_name, step_data in steps.items():
        if isinstance(step_data, dict) and step_data.get("status") == "running":
            return str(step_name)
    completed = [
        str(step_name)
        for step_name, step_data in steps.items()
        if isinstance(step_data, dict) and step_data.get("status") == "completed"
    ]
    if completed:
        return completed[-1]
    return str(next(iter(steps)))


def _infer_stale_reason(data: dict) -> str | None:
    stale_reason = data.get("stale_reason")
    if isinstance(stale_reason, str) and stale_reason.strip():
        return stale_reason.strip()
    if data.get("status") != "running":
        return None
    pid = data.get("pid")
    if isinstance(pid, int):
        pid_running = _is_pid_running(pid)
        if pid_running is False:
            return f"pipeline pid not running: {pid}"
        return None
    return None


def _recommend_next_action(status: str, *, stale: bool, failure_reason: str, current_step: str) -> str:
    decision_input = build_run_overview_followup_input(
        status=status,
        stale=stale,
        failure_reason=failure_reason,
        current_step=current_step,
    )
    next_action, _ = _resolve_run_overview_followup_from_input(decision_input)
    return next_action


def _resolve_run_overview_followup_from_input(decision_input: dict[str, object]) -> tuple[str, str]:
    return resolve_run_overview_followup_from_input(decision_input)


def _summarize_run_record(run_id: str, data: dict) -> dict[str, object]:
    status = str(data.get("status") or "unknown")
    steps = data.get("steps") or {}
    current_step = _get_current_step_name(steps)
    current_step_state = steps.get(current_step) if isinstance(steps, dict) else None
    stale_reason = _infer_stale_reason(data)
    stale = stale_reason is not None
    failure_reason = str(data.get("failure_category") or stale_reason or "-")
    return {
        "run_id": run_id,
        "branch": str(data.get("branch") or "-"),
        "executor": str(data.get("executor") or "-"),
        "status": status,
        "is_current": run_id == "current",
        "current_step": current_step,
        "current_step_model_profile": (
            str(current_step_state.get("model_profile") or "-")
            if isinstance(current_step_state, dict)
            else "-"
        ),
        "current_step_routing_reason": (
            str(current_step_state.get("routing_reason_summary") or "-")
            if isinstance(current_step_state, dict)
            else "-"
        ),
        "stale": stale,
        "failure_reason": failure_reason,
        "started_at": data.get("started_at"),
        "finished_at": data.get("finished_at"),
        "next_action": _recommend_next_action(
            status,
            stale=stale,
            failure_reason=failure_reason,
            current_step=current_step,
        ),
    }


def _overview_status_priority(summary: dict[str, object]) -> int:
    status = str(summary.get("status") or "unknown")
    stale = bool(summary.get("stale"))
    if stale and status == "running":
        return 0
    if status in {"hold", "auto_hold", "blocked"}:
        return 1
    if status in {"failed", "retry_exhausted", "timeout", "aborted"}:
        return 2
    if status == "running":
        return 3
    if status == "completed":
        return 4
    return 5


def _overview_timestamp_key(summary: dict[str, object]) -> float:
    for field in ("finished_at", "started_at"):
        parsed = _parse_pipeline_timestamp(summary.get(field))
        if parsed is not None:
            return -parsed.timestamp()
    return 0.0


def _sort_overview_summaries(summaries: list[dict[str, object]]) -> list[dict[str, object]]:
    return sorted(
        summaries,
        key=lambda summary: (
            _overview_status_priority(summary),
            0 if bool(summary.get("is_current")) else 1,
            _overview_timestamp_key(summary),
            str(summary.get("run_id") or ""),
        ),
    )


def _build_overview_kpi_summary(run_records: list[dict]) -> dict[str, object]:
    reports = [_build_benchmark_report(record) for record in run_records if isinstance(record, dict)]
    total_runs = len(reports)
    reroute_runs = sum(1 for report in reports if report.get("had_reroute") is True)
    recovered_runs = sum(1 for report in reports if report.get("recovered_after_retry") is True)
    observed_sample_count = 0
    accepted_observed_count = 0
    excluded_observed_count = 0
    rejected_observed_count = 0
    readiness_same_surface_count = 0
    distinct_policy_pairs: set[str] = set()
    rejected_observed_output_reasons: dict[str, int] = {}
    observed_reason_signal_kinds: set[str] = set()
    baseline_comparison_not_ready_seen = False
    for record in run_records:
        if not isinstance(record, dict):
            continue
        simulated_record = _run_record_is_simulated(record)
        if str(record.get("next_kpi_blocker") or "").strip() == "baseline_comparison_not_ready":
            baseline_comparison_not_ready_seen = True
        explicit_rejected_reasons = record.get("dataset_rejected_observed_output_reasons")
        if isinstance(explicit_rejected_reasons, dict):
            for key, raw_count in explicit_rejected_reasons.items():
                reason = str(key).strip()
                if not reason or not isinstance(raw_count, int):
                    continue
                rejected_observed_output_reasons[reason] = (
                    rejected_observed_output_reasons.get(reason, 0) + raw_count
                )
        source_type = str(record.get("benchmark_source_type") or "").strip()
        excluded_from_readiness = bool(record.get("dataset_excluded_from_readiness") is True)
        explicit_rejected_case_count = record.get("dataset_rejected_observed_output_case_count")
        rejected_case_count = explicit_rejected_case_count if isinstance(explicit_rejected_case_count, int) else 0
        rejection_reason = _overview_observed_output_rejection_reason(record)
        if rejection_reason:
            rejected_case_count += 1
        is_observed_record = source_type in {"observed_request", "observed_output"}
        if is_observed_record and not simulated_record:
            observed_sample_count += 1
        if rejected_case_count > 0:
            rejected_observed_count += rejected_case_count
        elif simulated_record and is_observed_record:
            pass
        elif excluded_from_readiness and is_observed_record:
            excluded_observed_count += 1
        elif is_observed_record:
            accepted_observed_count += 1
        if source_type == "observed_output":
            if rejection_reason:
                rejected_observed_output_reasons[rejection_reason] = rejected_observed_output_reasons.get(
                    rejection_reason, 0
                ) + 1
            observed_reason_signal_kinds.update(_overview_observed_reason_signal_kinds(record))
        if (
            source_type == "observed_output"
            and not simulated_record
            and not excluded_from_readiness
            and rejected_case_count == 0
            and str(record.get("comparison_scope") or "").strip() == "same_surface"
        ):
            readiness_same_surface_count += 1
        policy_pair = str(record.get("policy_pair") or "").strip()
        if (
            policy_pair
            and not simulated_record
            and not excluded_from_readiness
            and rejected_case_count == 0
        ):
            distinct_policy_pairs.add(policy_pair)
    next_kpi_blocker = "none"
    readiness_status_line = (
        "not ready: "
        f"samples {observed_sample_count}/20, "
        f"same-surface {readiness_same_surface_count}/1, "
        f"policy pairs {len(distinct_policy_pairs)}/2"
    )
    if observed_sample_count < 20:
        next_kpi_blocker = "insufficient_observed_samples"
    elif readiness_same_surface_count < 1:
        next_kpi_blocker = "insufficient_same_surface_evidence"
    elif len(distinct_policy_pairs) < 2:
        next_kpi_blocker = "insufficient_policy_pairs"
    elif baseline_comparison_not_ready_seen:
        next_kpi_blocker = "baseline_comparison_not_ready"
        readiness_status_line = "not ready: baseline comparison input is not ready"
    else:
        readiness_status_line = "ready: baseline comparison wording can be enabled"
    baseline_comparison_status = "ready" if next_kpi_blocker == "none" else "deferred"
    deferred_reason_map = _overview_readiness_deferred_reason_map()
    if baseline_comparison_status == "ready":
        baseline_comparison_line = "ready: baseline comparison sample is available from observed runs"
        policy_comparison_summary = "policy comparison ready: baseline comparison wording can be enabled"
    else:
        deferred_reason = deferred_reason_map.get(
            next_kpi_blocker,
            "readiness requirements are not met",
        )
        baseline_comparison_line = f"deferred: {deferred_reason}"
        policy_comparison_summary = f"policy comparison pending: {deferred_reason}"
    if observed_reason_signal_kinds:
        policy_comparison_summary += "; reason signals observed"
        reason_signal_summary_line = "observed reason signals: " + ",".join(
            sorted(observed_reason_signal_kinds)
        )
    else:
        reason_signal_summary_line = "observed reason signals: none"
    next_priority_input = _build_overview_next_priority_input(
        blocker=next_kpi_blocker,
        observed_reason_signals_present=bool(observed_reason_signal_kinds),
        baseline_comparison_status=baseline_comparison_status,
    )
    next_priority_core = next_priority_input.get("core")
    if not isinstance(next_priority_core, dict):
        next_priority_core = {}
    next_priority_recommendation, next_priority_reason = _resolve_next_priority_from_overview_input(
        next_priority_input
    )
    operational_validation_readiness, operational_validation_reason = (
        _overview_operational_validation_readiness(
            blocker=next_kpi_blocker,
            observed_sample_count=observed_sample_count,
            same_surface_count=readiness_same_surface_count,
            distinct_policy_pair_count=len(distinct_policy_pairs),
        )
    )
    next_collection_focus = _overview_next_collection_focus(next_kpi_blocker)
    successful_costs = [
        float(report["total_cost_usd"])
        for report in reports
        if report.get("pipeline_success") is True
        and isinstance(report.get("total_cost_usd"), (int, float))
    ]
    return {
        "total_runs": total_runs,
        "reroute_rate": (reroute_runs / total_runs) if total_runs else None,
        "retry_to_success_rate": (recovered_runs / reroute_runs) if reroute_runs else None,
        "cost_per_successful_task": (
            sum(successful_costs) / len(successful_costs)
            if successful_costs
            else None
        ),
        "observed_sample_count": observed_sample_count,
        "accepted_observed_count": accepted_observed_count,
        "excluded_observed_count": excluded_observed_count,
        "rejected_observed_count": rejected_observed_count,
        "readiness_same_surface_count": readiness_same_surface_count,
        "distinct_policy_pair_count": len(distinct_policy_pairs),
        "readiness_status_line": readiness_status_line,
        "baseline_comparison_status": baseline_comparison_status,
        "baseline_comparison_line": baseline_comparison_line,
        "policy_comparison_summary": policy_comparison_summary,
        "reason_signal_summary_line": reason_signal_summary_line,
        "next_priority_input_source_surface": str(
            next_priority_input.get("extension", {}).get("source_surface") or ""
        ),
        "next_priority_input_core": dict(next_priority_core),
        "next_priority_recommendation": next_priority_recommendation,
        "next_priority_reason": next_priority_reason,
        "operational_validation_readiness": operational_validation_readiness,
        "operational_validation_reason": operational_validation_reason,
        "next_kpi_blocker": next_kpi_blocker,
        "next_collection_focus": next_collection_focus,
        "rejected_observed_output_case_count": sum(rejected_observed_output_reasons.values()),
        "rejected_observed_output_reasons": rejected_observed_output_reasons,
    }


def _overview_readiness_deferred_reason_map() -> dict[str, str]:
    return {
        "insufficient_observed_samples": "need more observed samples",
        "insufficient_same_surface_evidence": "need more same-surface evidence",
        "insufficient_policy_pairs": "need more policy pair coverage",
        "baseline_comparison_not_ready": "baseline comparison input is not ready",
    }


def _overview_next_collection_focus(next_kpi_blocker: str) -> str:
    if next_kpi_blocker == "insufficient_observed_samples":
        return "collect_more_observed_runs"
    if next_kpi_blocker == "insufficient_same_surface_evidence":
        return "add_same_surface_observed_evidence"
    if next_kpi_blocker == "insufficient_policy_pairs":
        return "expand_policy_pair_coverage"
    if next_kpi_blocker == "baseline_comparison_not_ready":
        return "stabilize_baseline_comparison_inputs"
    return "maintain_policy_comparison_confidence"


def _overview_resolve_next_priority(
    *,
    blocker: str,
    observed_reason_signals_present: bool,
    baseline_comparison_status: str,
) -> tuple[str, str]:
    return resolve_next_priority(
        blocker=blocker,
        observed_reason_signals_present=observed_reason_signals_present,
        baseline_comparison_status=baseline_comparison_status,
    )


def _resolve_next_priority_from_overview_input(
    decision_input: dict[str, object]
) -> tuple[str, str]:
    return resolve_next_priority_from_input(decision_input)


def _build_overview_next_priority_input(
    *,
    blocker: str,
    observed_reason_signals_present: bool,
    baseline_comparison_status: str,
) -> dict[str, object]:
    return build_next_priority_surface_input(
        blocker=blocker,
        observed_reason_signals_present=observed_reason_signals_present,
        baseline_comparison_status=baseline_comparison_status,
        source_surface="overview_summary",
    )


def _overview_operational_validation_readiness(
    *,
    blocker: str,
    observed_sample_count: int,
    same_surface_count: int,
    distinct_policy_pair_count: int,
) -> tuple[str, str]:
    if (
        blocker == "none"
        and observed_sample_count >= 20
        and same_surface_count >= 1
        and distinct_policy_pair_count >= 2
    ):
        return "start-ready", "ready to start V4-B operational validation"
    return "not-ready", "need more observed evidence before starting V4-B operational validation"


def _format_overview_ratio(value: object) -> str:
    if not isinstance(value, (int, float)):
        return "n/a"
    return f"{value * 100:.1f}%"


def _format_overview_cost(value: object) -> str:
    if not isinstance(value, (int, float)):
        return "n/a"
    return f"${value:.4f}"


def _format_rejected_reason_counts(value: object) -> str:
    if not isinstance(value, dict) or not value:
        return "none"
    parts: list[str] = []
    for key in sorted(value):
        count = value.get(key)
        if isinstance(count, int):
            parts.append(f"{key}:{count}")
    return ",".join(parts) if parts else "none"


def _overview_observed_output_rejection_reason(record: dict) -> str | None:
    source_type = str(record.get("benchmark_source_type") or "").strip()
    if source_type != "observed_output":
        return None
    comparison_scope = str(record.get("comparison_scope") or "").strip()
    if comparison_scope not in {"same_surface", "cross_surface"}:
        return "invalid_comparison_scope"
    baseline_response_sample = str(record.get("baseline_response_sample") or "").strip()
    if not baseline_response_sample:
        return "missing_baseline_response_sample"
    candidate_response_sample = str(record.get("candidate_response_sample") or "").strip()
    if not candidate_response_sample:
        return "missing_candidate_response_sample"
    return None


def _overview_observed_reason_signal_kinds(record: dict) -> set[str]:
    source_type = str(record.get("benchmark_source_type") or "").strip()
    if source_type != "observed_output":
        return set()
    signal_kinds: set[str] = set()
    if str(record.get("response_mode") or "").strip() == "reroute":
        signal_kinds.add("reroute")
    if str(record.get("rerouted_from_response_mode") or "").strip():
        signal_kinds.add("reroute")
    return signal_kinds


def _overview_run_fingerprint(data: dict) -> str:
    return json.dumps(data, sort_keys=True, ensure_ascii=False)


def cmd_overview(root: Path, *, limit: int = 10) -> int:
    """Print a one-screen read-only overview for current and recent autopilot runs."""
    summaries: list[dict[str, object]] = []
    run_records: list[dict] = []
    seen_fingerprints: set[str] = set()

    current_path = root / _PIPELINE_RESULT_PATH
    if current_path.exists():
        try:
            current_data = json.loads(current_path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            current_data = None
        if isinstance(current_data, dict):
            seen_fingerprints.add(_overview_run_fingerprint(current_data))
            run_records.append(current_data)
            summaries.append(_summarize_run_record("current", current_data))

    runs_dir = root / _RUNS_DIR
    if runs_dir.exists():
        run_dirs = sorted(runs_dir.iterdir(), reverse=True)
        for d in run_dirs:
            result_path = d / "result.json"
            if not result_path.exists():
                continue
            try:
                data = json.loads(result_path.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, OSError):
                continue
            fingerprint = _overview_run_fingerprint(data)
            if fingerprint not in seen_fingerprints:
                seen_fingerprints.add(fingerprint)
                run_records.append(data)
            if len(summaries) < limit:
                summaries.append(_summarize_run_record(d.name, data))

    if not summaries:
        print("[OVERVIEW] 실행 기록 없음")
        return 0

    summaries = _sort_overview_summaries(summaries)
    kpi_summary = _build_overview_kpi_summary(run_records)

    print("OMC Autopilot Overview")
    print(
        "KPI Summary "
        f"total_runs={kpi_summary['total_runs']}  "
        f"reroute_rate={_format_overview_ratio(kpi_summary['reroute_rate'])}  "
        f"retry_to_success_rate={_format_overview_ratio(kpi_summary['retry_to_success_rate'])}  "
        f"cost_per_successful_task={_format_overview_cost(kpi_summary['cost_per_successful_task'])}  "
        f"observed_samples={kpi_summary['observed_sample_count']}  "
        f"accepted_observed={kpi_summary['accepted_observed_count']}  "
        f"excluded_observed={kpi_summary['excluded_observed_count']}  "
        f"rejected_observed={kpi_summary['rejected_observed_count']}  "
        f"readiness_same_surface={kpi_summary['readiness_same_surface_count']}  "
        f"distinct_policy_pairs={kpi_summary['distinct_policy_pair_count']}  "
        f"readiness_status={kpi_summary['readiness_status_line']}  "
        f"baseline_comparison_status={kpi_summary['baseline_comparison_status']}  "
        f"next_kpi_blocker={kpi_summary['next_kpi_blocker']}  "
        f"next_collection_focus={kpi_summary['next_collection_focus']}  "
        f"rejected_observed_output={kpi_summary['rejected_observed_output_case_count']}  "
        f"rejected_reasons={_format_rejected_reason_counts(kpi_summary['rejected_observed_output_reasons'])}"
    )
    print(
        "Collected Summary "
        f"baseline_line={kpi_summary['baseline_comparison_line']}  "
        f"policy_summary={kpi_summary['policy_comparison_summary']}  "
        f"reason_signal={kpi_summary['reason_signal_summary_line']}"
    )
    print(
        "Next Priority "
        f"next_priority={kpi_summary['next_priority_recommendation']}  "
        f"reason={kpi_summary['next_priority_reason']}"
    )
    print(
        "Operational Validation "
        f"operational_validation_readiness={kpi_summary['operational_validation_readiness']}  "
        f"operational_validation_reason={kpi_summary['operational_validation_reason']}"
    )
    print("run_id | branch | status | step | stale | failure_reason | next_action")
    print("-" * 78)
    for summary in summaries[:limit]:
        stale_text = "yes" if summary["stale"] else "no"
        print(
            f"{summary['run_id']} | {summary['branch']} | {summary['status']} | "
            f"{summary['current_step']} | {stale_text} | "
            f"{summary['failure_reason']} | next_action={summary['next_action']}"
            f" | routing={summary['current_step_model_profile']} "
            f"({summary['current_step_routing_reason']})"
        )
    return 0


# ---------------------------------------------------------------------------
# 커맨드: pipeline
# ---------------------------------------------------------------------------

_PIPELINE_RESULT_PATH = ".omc/pipeline_run_result.json"
_PIPELINE_TERMINAL_STATUSES = {
    "completed",
    "failed",
    "failed_branch",
    "failed_ambiguous_response",
    "failed_critique_loop",
    "retry_exhausted",
    "timeout",
    "aborted",
    "plan_hold",
    "hold",
}


def _checkout_new_branch(root: Path, name: str, max_retry: int = 3) -> str:
    """브랜치를 보장한다: 존재하면 checkout, 없으면 생성한다.

    생성 충돌 시 suffix(-v2, -v3 ...)로 재시도한다.

    Returns:
        실제 checkout/생성된 브랜치 이름
    Raises:
        RuntimeError: max_retry 초과 시
    """
    existing = subprocess.run(
        ["git", "rev-parse", "--verify", "--quiet", f"refs/heads/{name}"],
        capture_output=True,
        text=True,
        cwd=str(root),
    )
    if existing.returncode == 0:
        switch = subprocess.run(
            ["git", "checkout", name],
            capture_output=True,
            text=True,
            cwd=str(root),
        )
        if switch.returncode == 0:
            return name
        raise RuntimeError(f"failed_branch_switch:{name}: {switch.stderr.strip()}")

    for attempt in range(1, max_retry + 1):
        candidate = name if attempt == 1 else f"{name}-v{attempt}"
        br = subprocess.run(
            ["git", "checkout", "-b", candidate],
            capture_output=True, text=True, cwd=str(root),
        )
        if br.returncode == 0:
            return candidate
        err = (br.stderr or "").strip()
        if "Operation not permitted" in err or "cannot lock ref" in err:
            raise RuntimeError(f"failed_branch_permission:{candidate}: {err}")
    raise RuntimeError(f"failed_branch:{name} (tried {max_retry} times)")


def _get_result_path(root: Path) -> Path:
    """OmC_PIPELINE_RESULT_PATH 환경변수 우선, 없으면 root 기준 기본값을 반환한다."""
    env_val = os.getenv("OmC_PIPELINE_RESULT_PATH", "").strip()
    if env_val:
        return Path(env_val)
    return root / _PIPELINE_RESULT_PATH


def _is_pid_running(pid: int | None) -> bool | None:
    """Return pid liveness status.

    Returns:
        True: alive or permission denied (EPERM)
        False: not running (ESRCH)
        None: unknown state due to other OS errors
    """
    if not pid or pid <= 0:
        return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        # EPERM은 시그널 권한이 없을 뿐 프로세스는 존재한다.
        return True
    except OSError as e:
        print(f"[PIPELINE STATUS] pid 상태 확인 불가(pid={pid}): {e}", file=sys.stderr)
        return None
    return True


def _load_resume_state(root: Path) -> dict | None:
    """이전 pipeline_run_result.json을 읽어 재개 상태를 반환한다.

    Returns:
        dict: 이전 결과 데이터 (없으면 None)
    """
    path = _get_result_path(root)
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None


def _step_already_done(resume_data: dict | None, step: str) -> bool:
    """재개 데이터에서 특정 단계가 completed인지 확인한다."""
    if not resume_data:
        return False
    steps = resume_data.get("steps", {})
    return steps.get(step, {}).get("status") == "completed"


def _compute_step_duration_sec(started_at: object, finished_at: object) -> int | None:
    started = _parse_pipeline_timestamp(started_at)
    finished = _parse_pipeline_timestamp(finished_at)
    if started is None or finished is None:
        return None
    return max(0, int((finished - started).total_seconds()))


def _mark_pipeline_heartbeat(result: dict) -> None:
    result["last_heartbeat_at"] = _now()


def _sync_pipeline_retry_count(result: dict) -> None:
    steps = result.get("steps") or {}
    if not isinstance(steps, dict):
        result["retry_count"] = 0
        return
    retry_step_count = sum(1 for name in steps if "retry" in str(name))
    retry_attempt_count = 0
    for step in steps.values():
        if not isinstance(step, dict):
            continue
        attempt = step.get("attempt")
        if isinstance(attempt, int) and attempt > 1:
            retry_attempt_count += attempt - 1
    result["retry_count"] = retry_step_count + retry_attempt_count


def _build_failure_metadata(
    *,
    step_name: str,
    status: str | None,
    verdict: str | None = None,
    reason_codes: list[str] | None = None,
    rc: int | None = None,
) -> dict[str, object]:
    name = step_name.strip().lower()
    normalized_status = str(status or "").strip().lower()
    normalized_verdict = str(verdict or "").strip().upper()
    normalized_reason_codes = [
        str(code).strip().lower()
        for code in (reason_codes or [])
        if str(code).strip()
    ]

    if any(
        code in {
            "bad_entry_skill",
            "reroute_loop",
            "metadata_missing",
            "block_without_reason_code",
            "ambiguous_response",
            "branch_setup_failed",
        }
        for code in normalized_reason_codes
    ):
        failure_class = "orchestration_failure"
    elif normalized_verdict in {"BLOCK", "REVISE"} and ("review" in name or "critique" in name):
        failure_class = "quality_failure"
    elif normalized_verdict == "HOLD" or normalized_status == "blocked":
        failure_class = "contract_failure"
    else:
        failure_class = "execution_failure"

    inferred_reason_codes: list[str] = []
    if normalized_verdict == "BLOCK":
        inferred_reason_codes.append("verdict_block")
    elif normalized_verdict == "REVISE":
        inferred_reason_codes.append("verdict_revise")
    elif normalized_verdict == "HOLD":
        inferred_reason_codes.append("verdict_hold")

    if normalized_status == "failed_critique_loop":
        inferred_reason_codes.append("same_verdict_streak")
    elif normalized_status == "retry_exhausted":
        inferred_reason_codes.append("retry_exhausted")
    elif normalized_status == "failed":
        inferred_reason_codes.append("step_failed")

    if rc is not None and rc != 0:
        inferred_reason_codes.append("nonzero_exit")

    final_reason_codes = reason_codes if reason_codes is not None else inferred_reason_codes
    deduped_reason_codes: list[str] = []
    for code in final_reason_codes:
        normalized_code = str(code).strip()
        if normalized_code and normalized_code not in deduped_reason_codes:
            deduped_reason_codes.append(normalized_code)

    return {
        "failure_class": failure_class,
        "reason_codes": deduped_reason_codes,
    }


def _infer_failure_class(*, step_name: str, step: dict, run_status: str | None) -> str | None:
    explicit = str(step.get("failure_class") or "").strip()
    if explicit:
        return explicit

    status = str(step.get("status") or "").strip().lower()
    verdict = str(step.get("verdict") or "").strip().upper()
    if status:
        return str(
            _build_failure_metadata(
                step_name=step_name,
                status=status,
                verdict=verdict,
            )["failure_class"]
        )

    if run_status and str(run_status).strip().lower() != "completed":
        return "execution_failure"
    return None


def _step_counts_as_failure(step: dict) -> bool:
    status = str(step.get("status") or "").strip().lower()
    verdict = str(step.get("verdict") or "").strip().upper()
    if status != "completed":
        return True
    return verdict in {"BLOCK", "HOLD", "REVISE"}


def _decide_escalation_action(
    *,
    failure_class: str | None,
    reason_codes: list[str] | None,
    retry_count: int,
    escalation_policy: str | None,
) -> dict[str, object]:
    return _decision_policy_entry(
        failure_class=failure_class,
        reason_codes=reason_codes,
        retry_count=retry_count,
        escalation_policy=escalation_policy,
    )


def _decision_policy_entry(
    *,
    failure_class: str | None,
    reason_codes: list[str] | None,
    retry_count: int,
    escalation_policy: str | None,
) -> dict[str, object]:
    normalized_policy = str(escalation_policy or "").strip().lower()
    if normalized_policy not in _KNOWN_ESCALATION_POLICIES:
        normalized_policy = "default"

    normalized_failure_class = str(failure_class or "").strip().lower()
    normalized_reason_codes = [
        str(code).strip().lower()
        for code in (reason_codes or [])
        if str(code).strip()
    ]

    if normalized_failure_class == "contract_failure":
        return {
            "decision": "hold",
            "decision_reason": "contract failure requires explicit hold",
            "reroute_target": None,
        }

    if normalized_failure_class == "orchestration_failure":
        if "block_without_reason_code" in normalized_reason_codes:
            return {
                "decision": "reroute",
                "decision_reason": "missing task block reason reroutes to planning",
                "reroute_target": "plan_retry",
            }
        if "reroute_loop" in normalized_reason_codes:
            return {
                "decision": "hold",
                "decision_reason": "orchestration reroute loop requires explicit hold",
                "reroute_target": None,
            }
        if any(code in {"bad_entry_skill", "metadata_missing"} for code in normalized_reason_codes):
            return {
                "decision": "reroute",
                "decision_reason": "orchestration failure reroutes to planning",
                "reroute_target": "plan_retry",
            }
        return {
            "decision": "hold",
            "decision_reason": "orchestration failure defaults to explicit hold",
            "reroute_target": None,
        }

    if normalized_failure_class == "quality_failure":
        if normalized_policy == "conservative":
            return {
                "decision": "hold",
                "decision_reason": "conservative policy holds on quality failure",
                "reroute_target": None,
            }
        return {
            "decision": "reroute",
            "decision_reason": "quality failure reroutes to planning",
            "reroute_target": "plan_retry",
        }

    if normalized_failure_class == "execution_failure":
        if normalized_policy == "aggressive":
            return {
                "decision": "reroute",
                "decision_reason": "aggressive policy reroutes execution failure immediately",
                "reroute_target": "task_retry",
            }
        if retry_count >= 2 or "retry_exhausted" in normalized_reason_codes:
            return {
                "decision": "reroute",
                "decision_reason": "execution failure reached reroute threshold",
                "reroute_target": "task_retry",
            }
        return {
            "decision": "same",
            "decision_reason": "execution failure stays on current path before threshold",
            "reroute_target": None,
        }

    return {
        "decision": "hold",
        "decision_reason": "unknown failure defaults to hold",
        "reroute_target": None,
    }


def _persist_escalation_decision(step_payload: dict, decision: dict[str, object]) -> dict[str, object]:
    step_payload["decision"] = decision.get("decision")
    step_payload["decision_reason"] = decision.get("decision_reason")
    step_payload["reroute_target"] = decision.get("reroute_target")
    return step_payload


def _completed_retry_decision() -> dict[str, object]:
    return {
        "decision": "same",
        "decision_reason": "completed retry stays on current path",
        "reroute_target": None,
    }


def _failure_step_decision(
    *,
    step_name: str,
    step_payload: dict[str, object],
    retry_count: int,
) -> dict[str, object]:
    step_metadata = _pipeline_step_metadata(step_name)
    return _decision_policy_entry(
        failure_class=str(step_payload.get("failure_class") or ""),
        reason_codes=list(step_payload.get("reason_codes") or []),
        retry_count=retry_count,
        escalation_policy=str(step_metadata.get("escalation_policy") or "default"),
    )


def _recovery_target_from_decision(
    *,
    loop_step: str,
    decision_name: str,
    reroute_target: str,
    task_auto_retry_count: int,
    critique_auto_retry_count: int,
) -> str | None:
    if decision_name == "hold":
        return None
    if decision_name == "reroute" and reroute_target in {"task_retry", "plan_retry"}:
        return reroute_target
    if task_auto_retry_count < _TASK_AUTO_RETRY_MAX:
        return "task_retry"
    if critique_auto_retry_count < _CRITIQUE_AUTO_RETRY_MAX:
        return "plan_retry"
    return None


def _retry_step_payload(
    *,
    step_name: str,
    rc: int,
    started_at: str | None,
    finished_at: str | None,
    output_preview: str,
    retry_count: int,
) -> dict[str, object]:
    payload = _step_payload(
        "completed" if rc == 0 else "failed",
        started_at,
        finished_at,
        output_preview=output_preview,
        **(
            _build_failure_metadata(
                step_name=step_name,
                status="failed",
                rc=rc,
            )
            if rc != 0
            else {}
        ),
    )
    decision = (
        _completed_retry_decision()
        if rc == 0
        else _failure_step_decision(
            step_name=step_name,
            step_payload=payload,
            retry_count=retry_count,
        )
    )
    return _persist_escalation_decision(payload, decision)


def _task_retry_block_failure_payload(
    *,
    started_at: str | None,
    finished_at: str | None,
    output_preview: str,
    retry_count: int,
    reason_codes: list[str],
) -> dict[str, object]:
    return _failed_step_payload(
        step_name="task_retry",
        status="failed",
        started_at=started_at,
        finished_at=finished_at,
        verdict="BLOCK",
        reason_codes=reason_codes,
        output_preview=output_preview,
        retry_count=retry_count,
    )


def _failed_step_payload(
    *,
    step_name: str,
    status: str,
    started_at: str | None,
    finished_at: str | None,
    verdict: str | None = None,
    reason_codes: list[str] | None = None,
    rc: int | None = None,
    **extra: object,
) -> dict[str, object]:
    payload = _step_payload(
        status,
        started_at,
        finished_at,
        **extra,
        **_build_failure_metadata(
            step_name=step_name,
            status=status,
            verdict=verdict,
            reason_codes=reason_codes,
            rc=rc,
        ),
    )
    decision = _failure_step_decision(step_name=step_name, step_payload=payload, retry_count=0)
    return _persist_escalation_decision(payload, decision)


def _pipeline_step_metadata(step_name: str) -> dict[str, object]:
    return _normalize_step_metadata({"id": step_name})


def _step_payload(
    status: str,
    started_at: str | None,
    finished_at: str | None = None,
    **extra: object,
) -> dict:
    payload = {"status": status}
    if started_at is not None:
        payload["started_at"] = started_at
    if finished_at is not None:
        payload["finished_at"] = finished_at
        duration_sec = _compute_step_duration_sec(started_at, finished_at)
        if duration_sec is not None:
            payload["duration_sec"] = duration_sec
    payload.update(extra)
    return payload
_PIPELINE_MAX_RETRIES = 3
_PIPELINE_MAX_SAME_VERDICT = 2  # 동일 verdict 연속 허용 횟수
_VERDICT_PATTERN = r"VERDICT:\s*(PROCEED|APPROVE|BLOCK|REVISE|HOLD)"

_LITE_BRANCH_PREFIXES = ("fix/", "hotfix/", "chore/", "docs/")
_LITE_INSTRUCTION_MAX_LEN = 50


def _detect_pipeline_mode(branch: str, instruction: str, mode_arg: str) -> str:
    """pipeline 모드를 결정한다.

    Args:
        branch: feature 브랜치 이름
        instruction: 작업 지시문
        mode_arg: CLI --mode 값 ("auto" | "lite" | "full")

    Returns:
        "lite" 또는 "full"
    """
    if mode_arg in ("lite", "full"):
        return mode_arg

    # auto 감지
    if not branch:
        return "full"  # 빈 브랜치는 안전 fallback
    for prefix in _LITE_BRANCH_PREFIXES:
        if branch.startswith(prefix):
            return "lite"
    if len(instruction) <= _LITE_INSTRUCTION_MAX_LEN:
        return "lite"
    return "full"


def _save_pipeline_result(root: Path, data: dict) -> None:
    out = _get_result_path(root)
    out.parent.mkdir(parents=True, exist_ok=True)
    run_id = data.get("__run_id")
    if not isinstance(run_id, str) or not run_id.strip():
        run_id = datetime.now().strftime("%Y%m%dT%H%M%S") + "-" + str(uuid.uuid4())[:8]
        data["__run_id"] = run_id
    task_id = str(data.get("task_id") or "").strip()
    has_source_type = str(data.get("benchmark_source_type") or "").strip()
    has_policy_pair = str(data.get("policy_pair") or "").strip()
    task_meta: dict[str, object] = {}
    source_type = has_source_type
    observed_output_needs_schema = source_type == "observed_output" and any(
        not str(data.get(key) or "").strip()
        for key in (
            "comparison_scope",
            "baseline_response_sample",
            "candidate_response_sample",
        )
    )
    if task_id and (not has_source_type or not has_policy_pair or observed_output_needs_schema):
        task_meta_path = root / ".omc" / "tasks" / f"{task_id}.json"
        try:
            task_meta = json.loads(task_meta_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            task_meta = {}
        if isinstance(task_meta, dict):
            if not has_source_type:
                benchmark_source_type = str(task_meta.get("benchmark_source_type") or "").strip()
                if benchmark_source_type:
                    data["benchmark_source_type"] = benchmark_source_type
            if not has_policy_pair:
                policy_pair = str(task_meta.get("policy_pair") or "").strip()
                if policy_pair:
                    data["policy_pair"] = policy_pair
    source_type = str(data.get("benchmark_source_type") or "").strip()
    if source_type == "observed_output":
        _populate_observed_output_schema(data, task_meta if isinstance(task_meta, dict) else {})
    serialized = {k: v for k, v in data.items() if not str(k).startswith("__")}
    payload = json.dumps(serialized, ensure_ascii=False, indent=2)
    # 원자적 쓰기 (tmpfile → replace)
    tmp = out.parent / (out.name + ".tmp")
    tmp.write_text(payload, encoding="utf-8")
    tmp.replace(out)
    # run 이력 분리 저장 (.omc/runs/{run_id}/result.json)
    try:
        run_path = root / ".omc" / "runs" / run_id / "result.json"
        run_path.parent.mkdir(parents=True, exist_ok=True)
        run_tmp = run_path.parent / "result.json.tmp"
        run_tmp.write_text(payload, encoding="utf-8")
        run_tmp.replace(run_path)
    except Exception as e:
        print(f"[PIPELINE] ⚠️  runs 이력 저장 실패 (무시): {e}")


def _populate_observed_output_schema(data: dict, task_meta: dict[str, object]) -> None:
    required_keys = (
        "comparison_scope",
        "baseline_response_sample",
        "candidate_response_sample",
    )
    missing_from_data = [
        key for key in required_keys if not str(data.get(key) or "").strip()
    ]
    if missing_from_data:
        for key in missing_from_data:
            value = str(task_meta.get(key) or "").strip()
            if value:
                data[key] = value

    if all(str(data.get(key) or "").strip() for key in required_keys):
        return

    rejected_reasons: dict[str, int] = {}
    for key in required_keys:
        if str(data.get(key) or "").strip():
            continue
        rejected_reasons[f"missing_{key}"] = 1
    if rejected_reasons:
        data["dataset_rejected_observed_output_case_count"] = 1
        data["dataset_rejected_observed_output_reasons"] = rejected_reasons

    # observed_output benchmark payload is only valid when the full schema exists.
    for key in ("benchmark_source_type", "policy_pair", *required_keys):
        data.pop(key, None)





def _grep_verdict(output: str) -> str | None:
    """LLM 출력에서 VERDICT: <판정> 키워드를 추출한다."""
    m = re.search(_VERDICT_PATTERN, output, re.IGNORECASE)
    if m:
        return m.group(1).upper()
    return None


_UNSET_VERDICT = object()  # prev_verdict 초기 sentinel — None 과 구분
_CRITIQUE_AUTO_RETRY_MAX = 1  # critique 루프 탈출 후 plan 자동 재진입 최대 횟수
_TASK_AUTO_RETRY_MAX = 2      # critique 루프 탈출 후 task 자동 재실행 최대 횟수
_PLAN_RETRY_REASON_CODE_PATTERN = re.compile(r"^REASON_CODE\s*:\s*(.+)$", re.IGNORECASE | re.MULTILINE)

# critique가 REVISE 판정을 반복하는 핵심 기준 — task 프롬프트에 사전 주입
_CRITIQUE_QUALITY_HINT = (
    "\n\n[critique 품질 기준 — 반드시 충족]\n"
    "- 예외·오류는 침묵 실패 없이 명시적으로 처리(로깅 또는 raise)\n"
    "- 상태 집합은 완전해야 함 (완료·실패 외 취소·타임아웃·보류 포함)\n"
    "- 최근성 윈도우: 전체 스캔 대신 최근 N개 또는 기간 기준 필터 제공\n"
    "- 모든 공개 함수에 타입 힌트와 docstring\n"
    "- 구현 전 TDD(RED→GREEN→REFACTOR) 절차 준수\n"
    "- 데이터 품질 실패(invalid_started_at 등)는 의사결정 흐름에서 명시적으로 분기 처리\n"
    "- 환경변수 의존 정책은 코드 내 기본값을 명시하고 환경마다 동일 판정 보장\n"
    "- 조건 분기마다 단위 테스트 + 운영 기본값을 docstring/주석으로 문서화\n"
)


def _extract_critique_issues(output: str) -> str:
    """critique 출력에서 VERDICT 줄 직전 최대 30줄을 이슈 텍스트로 추출한다.

    VERDICT 줄이 없으면 전체 텍스트를 반환한다.
    빈 입력이면 빈 문자열을 반환한다.
    """
    if not output:
        return ""
    lines = output.splitlines()
    verdict_idx = None
    for i, line in enumerate(lines):
        if re.search(r"VERDICT\s*:", line, re.IGNORECASE):
            verdict_idx = i
            break
    if verdict_idx is None:
        return output
    start = max(0, verdict_idx - 30)
    return "\n".join(lines[start:verdict_idx])


_ORCHESTRATION_REASON_CODE_KEYWORDS = ("bad_entry_skill", "metadata_missing", "reroute_loop")


def _extract_orchestration_reason_codes(output: str) -> list[str]:
    """출력 문자열에서 구조화된 orchestration failure reason code를 추출한다."""
    if not output:
        return []
    codes: list[str] = []
    for match in _PLAN_RETRY_REASON_CODE_PATTERN.finditer(output):
        raw_codes = match.group(1)
        for raw_code in re.split(r"[,\s]+", raw_codes):
            code = raw_code.strip().lower()
            if code in _ORCHESTRATION_REASON_CODE_KEYWORDS and code not in codes:
                codes.append(code)
    return codes


def _ensure_block_without_reason_code(
    *,
    step_name: str,
    verdict: str | None,
    reason_codes: list[str],
) -> list[str]:
    """task/task_retry가 이유 없는 BLOCK을 내면 공통 fallback reason code를 채운다."""
    if (
        str(step_name).strip().lower() in {"task", "task_retry"}
        and str(verdict or "").strip().upper() == "BLOCK"
        and not reason_codes
    ):
        return ["block_without_reason_code"]
    return reason_codes



def _get_critique_context(root: Path, max_diff_lines: int = 200) -> str:
    """staged diff + TDD 결과를 critique 컨텍스트로 반환한다.

    instruction(구현 의도) 없이 코드 자체만 포함해 critique 편향을 방지한다.
    """
    import subprocess as _sp

    diff_proc = _sp.run(
        ["git", "diff", "--staged", "--stat"],
        cwd=str(root), capture_output=True, text=True,
    )
    diff_detail = _sp.run(
        ["git", "diff", "--staged"],
        cwd=str(root), capture_output=True, text=True,
    )
    diff_text = diff_detail.stdout or "(staged 변경 없음)"
    diff_lines = diff_text.splitlines()
    if len(diff_lines) > max_diff_lines:
        diff_text = "\n".join(diff_lines[:max_diff_lines]) + f"\n... (이하 {len(diff_lines) - max_diff_lines}줄 생략)"

    tdd_proc = _sp.run(
        [__import__("sys").executable, "scripts/omc_tdd_check.py", "--staged"],
        cwd=str(root), capture_output=True, text=True,
    )
    tdd_result = (tdd_proc.stdout + tdd_proc.stderr).strip() or "(TDD 결과 없음)"

    return (
        f"[staged diff 요약]\n{diff_proc.stdout.strip() or '(없음)'}\n\n"
        f"[staged diff 전체]\n{diff_text}\n\n"
        f"[TDD 게이트 결과]\n{tdd_result}"
    )


def _run_pipeline_step(
    root: Path,
    step_name: str,
    prompt: str,
    executor: str,
    timeout_sec: int,
    *,
    dry_run: bool = False,
    isolated: bool = False,
) -> tuple[int, str]:
    """단일 파이프라인 스텝을 LLM으로 실행하고 (returncode, output)을 반환한다.

    isolated=True 이면 이전 세션 컨텍스트 없이 새 컨텍스트로 실행한다.
    critique/review 스텝에 사용해 task 대화 이력과 격리한다.
    """
    if dry_run:
        print(f"  [DRY-RUN] {step_name} 시뮬레이션")
        # review 스텝은 APPROVE, 그 외는 PROCEED (verdict 일관성)
        dry_verdict = "APPROVE" if step_name == "review" else "PROCEED"
        return 0, f"[DRY-RUN] {step_name} — VERDICT: {dry_verdict}"

    exec_script = Path(__file__).resolve().parent / "omc_exec.py"
    if not exec_script.exists():
        return 1, f"[ERROR] omc_exec.py 없음"

    prompt_file = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            suffix=".md",
            prefix="omc_pipeline_prompt_",
            delete=False,
            encoding="utf-8",
        ) as tf:
            tf.write(prompt)
            prompt_file = tf.name

        cmd = [
            sys.executable,
            str(exec_script),
            "--target", str(root),
            "--prompt-file", str(prompt_file),
            "--executor", executor,
            "--execution-mode", "headless",
            "--timeout-sec", str(timeout_sec),
        ]
        proc = subprocess.Popen(
            cmd, cwd=str(root), stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            text=True, start_new_session=True,
        )
        timed_out = False

        def _kill_proc_group() -> None:
            nonlocal timed_out
            timed_out = True
            try:
                os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
            except (ProcessLookupError, PermissionError):
                pass

        timer = threading.Timer(timeout_sec, _kill_proc_group)
        try:
            timer.start()
            stdout, stderr = proc.communicate()
        finally:
            timer.cancel()

        if timed_out:
            return 124, f"[ERROR] 타임아웃 ({timeout_sec}s) — 프로세스 그룹 종료"

        output = (stdout or "") + (stderr or "")
        return int(proc.returncode), output.strip()
    except Exception as exc:
        return 1, f"[ERROR] {exc}"
    finally:
        if prompt_file:
            try:
                Path(prompt_file).unlink()
            except OSError:
                pass



def _ensure_staged(root: Path, dry_run: bool, label: str = "TASK") -> None:
    """미staged 파일을 안전 범위에서만 자동 스테이징한다."""
    if dry_run:
        return
    unstaged = subprocess.run(
        ["git", "diff", "--name-only"],
        capture_output=True, text=True, cwd=str(root),
    ).stdout.strip()
    if not unstaged:
        return
    staged = subprocess.run(
        ["git", "diff", "--staged", "--name-only"],
        capture_output=True, text=True, cwd=str(root),
    ).stdout.strip()
    unstaged_files = [line.strip() for line in unstaged.splitlines() if line.strip()]
    deny_prefixes = (".omc/", ".cursor/", ".git/", ".claude/", ".gemini/")
    allow_prefixes = ("scripts/", "dashboard/", "src/", "tests/")
    extra_allow = os.environ.get("OMC_PIPELINE_STAGE_ALLOW_PREFIXES", "").strip()
    if extra_allow:
        dynamic = tuple(p.strip() for p in extra_allow.split(",") if p.strip())
        allow_prefixes = (*allow_prefixes, *dynamic)
    safe_files = [
        path for path in unstaged_files
        if path.startswith(allow_prefixes) and not path.startswith(deny_prefixes)
    ]
    blocked_files = [path for path in unstaged_files if path not in safe_files]

    if safe_files:
        subprocess.run(["git", "add", "--", *safe_files], cwd=str(root))
        level = "⚠️" if not staged else "ℹ️"
        print(f"[PIPELINE] {level} {label} 후 안전 파일만 자동 스테이징: {len(safe_files)}개")
    if blocked_files:
        print(f"[PIPELINE] ⚠️  {label} 후 자동 스테이징 제외 파일 감지({len(blocked_files)}개) — 수동 검토 필요")

def cmd_pipeline(
    root: Path,
    instruction: str,
    branch: str,
    executor_pref: str = "auto",
    max_time: int = 7200,
    *,
    dry_run: bool = False,
    auto: bool = False,
    mode_arg: str = "auto",
    allow_dirty: bool = False,
    resume: bool = False,
) -> int:
    """plan→critique→task→review→PR 전체 자동화 파이프라인.

    Args:
        instruction: 사람이 작성한 작업 지시문
        branch: 생성할 feature 브랜치 이름
        executor_pref: LLM 실행기 (auto|codex|gemini|claude)
        max_time: 전체 파이프라인 최대 실행 시간(초)
        dry_run: 실제 LLM 호출 없이 흐름만 확인
        auto: 사람 게이트(plan 승인) 없이 자동 진행
    """
    started_at = _now()
    executor = _detect_executor(executor_pref)
    mode = _detect_pipeline_mode(branch, instruction, mode_arg)
    result: dict = {
        "status": "running",
        "mode": mode,
        "branch": branch,
        "instruction": instruction[:200],
        "executor": executor,
        "pid": os.getpid(),
        "started_at": started_at,
        "steps": {},
        "pr_url": None,
        "finished_at": None,
        "last_completed_step": None,
        "last_heartbeat_at": started_at,
        "retry_count": 0,
        "resume_count": 0,
        "approval_required": False,
        "manual_gate_reason": None,
    }

    # ── resume 처리 ──────────────────────────────────────────────────────
    _resume_data: dict | None = None
    if resume:
        _resume_data = _load_resume_state(root)
        if _resume_data is None:
            print("[PIPELINE] ❌ --resume: 재개할 이전 결과 파일이 없습니다.")
            print(f"  ({root / _PIPELINE_RESULT_PATH} 없음)")
            return 1
        if _resume_data.get("status") == "completed":
            print("[PIPELINE] ✅ 이미 완료된 파이프라인입니다. (--resume 불필요)")
            print(f"  PR: {_resume_data.get('pr_url') or '없음'}")
            return 0
        # 이전 steps를 현재 result에 복원
        result["steps"] = _resume_data.get("steps", {})
        result["retry_count"] = int(_resume_data.get("retry_count") or 0)
        result["resume_count"] = int(_resume_data.get("resume_count") or 0) + 1
        result["approval_required"] = bool(_resume_data.get("approval_required", False))
        result["manual_gate_reason"] = _resume_data.get("manual_gate_reason")
        result["last_heartbeat_at"] = _resume_data.get("last_heartbeat_at") or started_at
        print(f"[PIPELINE] 🔄 resume: 이전 실행 결과 로드 완료")

    def save(status: str) -> None:
        result["status"] = status
        _mark_pipeline_heartbeat(result)
        _sync_pipeline_retry_count(result)
        if status in _PIPELINE_TERMINAL_STATUSES:
            result["finished_at"] = _now()
        else:
            result["finished_at"] = None
        _save_pipeline_result(root, result)

    print(f"\n[PIPELINE] ▶ 시작: {instruction[:60]}")
    print(f"           브랜치={branch}  executor={executor}  dry_run={dry_run}")
    _mode_reason = (
        f"--mode {mode_arg} 명시" if mode_arg != "auto"
        else f"브랜치={branch[:20]}, 지시문={len(instruction)}자"
    )
    print(f"           모드: {mode.upper()} (근거: {_mode_reason})")
    if mode_arg == "auto":
        print(f"           override: --mode {'full' if mode == 'lite' else 'lite'}")

    deadline = time.time() + max_time

    # ── 전제 조건: git 상태 체크 ──────────────────────────────────────────
    git_status = subprocess.run(
        ["git", "status", "--porcelain"],
        capture_output=True, text=True, cwd=str(root),
    )
    if git_status.stdout.strip():
        dirty_files = git_status.stdout.strip()
        dirty_count = len(dirty_files.splitlines())
        if not allow_dirty and not dry_run:
            print(f"[PIPELINE] ❌ uncommitted 변경 감지 ({dirty_count}개 파일)")
            print(f"  {dirty_files[:200]}")
            print("  → git commit -am 'wip' 또는 git stash 후 재실행")
            print("  → 또는 --allow-dirty 플래그 추가하면 강제 진행")
            return 1
        print(f"[PIPELINE] ⚠️  uncommitted 변경 있음 ({dirty_count}개) — {'--allow-dirty' if allow_dirty else 'dry-run'} 모드")
        print(f"  {dirty_files[:200]}")

    if not dry_run:
        print("[PIPELINE] ✅ git 상태 확인 완료")

        # 브랜치 생성 (충돌 시 suffix 재시도)
        try:
            actual_branch = _checkout_new_branch(root, branch, max_retry=3)
            if actual_branch != branch:
                print(f"[PIPELINE] ⚠️  브랜치 충돌 — {actual_branch} 으로 생성")
                branch = actual_branch
                result["branch"] = branch
            _branch_finished_at = _now()
            result["steps"]["branch"] = _step_payload(
                "completed",
                _branch_finished_at,
                _branch_finished_at,
                name=branch,
            )
            print(f"[PIPELINE] ✅ 브랜치 준비: {branch}")
        except RuntimeError as e:
            print(f"[PIPELINE] ❌ 브랜치 생성 실패: {e}")
            _branch_failed_at = _now()
            result["steps"]["branch"] = _failed_step_payload(
                step_name="branch",
                status="failed_branch",
                started_at=_branch_failed_at,
                finished_at=_branch_failed_at,
                reason_codes=["branch_setup_failed"],
                error=str(e),
            )
            save("failed_branch")
            return 1

    _preflight_finished_at = _now()
    result["steps"]["preflight"] = _step_payload(
        "completed",
        started_at,
        _preflight_finished_at,
    )
    _save_pipeline_result(root, result)

    # ── pipeline_guard: contract-done + session-start ────────────────────
    if not dry_run:
        guard = Path(__file__).resolve().parent / "omc_pipeline_guard.py"
        subprocess.run([sys.executable, str(guard), "session-start"], cwd=str(root))
        subprocess.run([sys.executable, str(guard), "contract-done",
                        "--content", f"pipeline: {instruction[:100]}"], cwd=str(root))
        # 타깃 프로젝트 guard 초기화 (omc_kit와 다른 경우에만)
        target_guard = root / "scripts" / "omc_pipeline_guard.py"
        if target_guard.exists() and target_guard.resolve() != guard.resolve():
            result_target = subprocess.run(
                [sys.executable, str(target_guard), "session-start"], cwd=str(root)
            )
            if result_target.returncode == 0:
                cd_result = subprocess.run(
                    [sys.executable, str(target_guard), "contract-done",
                     "--content", f"pipeline: {instruction[:100]}"],
                    cwd=str(root)
                )
                if cd_result.returncode != 0:
                    print("[PIPELINE] ⚠️  타깃 guard contract-done 실패 — 계속 진행")
            else:
                print("[PIPELINE] ⚠️  타깃 guard session-start 실패 — 계속 진행")

    STEP_TIMEOUT = 600  # 스텝별 기본 타임아웃 (초)

    # ── LITE 모드: plan/critique 스킵 ──────────────────────────────────
    if mode == "lite":
        print("\n[PIPELINE] ⚡ LITE 모드 — plan/critique 스킵")
        result["steps"]["preflight"]["mode"] = "lite"
        _save_pipeline_result(root, result)
        # TASK 스텝으로 바로 진입
        task_prompt_lite = (
            "[자동화 모드] 사용자 확인 없이 즉시 실행하세요.\n\n"
            f"{instruction}\n\n"
            "TDD로 구현하세요.\n"
            "1. 실패하는 테스트 작성 후 `python3 scripts/omc_pipeline_guard.py red-done <파일>` 실행\n"
            "2. 구현 후 테스트 GREEN 확인\n"
            "반드시 마지막 줄에 `VERDICT: PROCEED` 또는 `VERDICT: BLOCK`을 출력하세요."
            + _CRITIQUE_QUALITY_HINT
        )
        if _step_already_done(_resume_data, "task"):
            print("[PIPELINE] ⏭ TASK 건너뜀 (이미 완료)")
        else:
            print("\n[PIPELINE] ▶ TASK 스텝 (LITE)...")
            task_started_at = _now()
            task_rc, task_out = _run_pipeline_step(
                root, "task", task_prompt_lite, executor, STEP_TIMEOUT * 2, dry_run=dry_run
            )
            task_finished_at = _now()
            result["steps"]["task"] = (
                _step_payload(
                    "completed",
                    task_started_at,
                    task_finished_at,
                    output_preview=task_out[:300],
                )
                if task_rc == 0
                else _failed_step_payload(
                    step_name="task",
                    status="failed",
                    started_at=task_started_at,
                    finished_at=task_finished_at,
                    rc=task_rc,
                    output_preview=task_out[:300],
                )
            )
            if task_rc != 0:
                save("failed")
                return 1

        # REVIEW 스텝 (retry 없음)
        review_prompt_lite = (
            f"다음 코드 변경을 리뷰하세요.\n지시문: {instruction[:200]}\n\n"
            "omc-review 스킬 포맷을 따르세요.\n"
            "반드시 마지막 줄에 `VERDICT: APPROVE` 또는 `VERDICT: BLOCK`을 출력하세요."
        )
        if _step_already_done(_resume_data, "review"):
            print("[PIPELINE] ⏭ REVIEW 건너뜀 (이미 완료)")
        else:
            print("\n[PIPELINE] ▶ REVIEW 스텝 (LITE, retry 없음)...")
            review_started_at = _now()
            review_rc, review_out = _run_pipeline_step(
                root, "review", review_prompt_lite, executor, STEP_TIMEOUT, dry_run=dry_run
            )
            review_finished_at = _now()
        review_verdict = _grep_verdict(review_out)
        print(f"  VERDICT: {review_verdict or '미감지'}")
        if not _step_already_done(_resume_data, "review"):
            result["steps"]["review"] = (
                _step_payload(
                    "completed",
                    review_started_at,
                    review_finished_at,
                    verdict=review_verdict,
                )
                if review_verdict == "APPROVE"
                else _failed_step_payload(
                    step_name="review",
                    status="failed",
                    started_at=review_started_at,
                    finished_at=review_finished_at,
                    verdict=review_verdict,
                )
            )
        if review_verdict != "APPROVE":
            save("failed")
            return 1

        # PR 생성 (LITE)
        pr_url = None
        if not dry_run:
            git_push = subprocess.run(
                ["git", "push", "-u", "origin", branch],
                capture_output=True, text=True, cwd=str(root),
            )
            if git_push.returncode == 0:
                pr_proc = subprocess.run(
                    ["gh", "pr", "create",
                     "--title", instruction[:72],
                     "--body", f"자동 생성 PR (LITE)\n\n지시문: {instruction[:300]}",
                     "--base", "main"],
                    capture_output=True, text=True, cwd=str(root),
                )
                if pr_proc.returncode == 0:
                    pr_url = pr_proc.stdout.strip()
                    print(f"[PIPELINE] ✅ PR 생성: {pr_url}")
                else:
                    print(f"[PIPELINE] ⚠️  PR 생성 실패: {pr_proc.stderr.strip()[:150]}")
                    result["steps"]["pr"] = {"status": "failed", "reason": "gh_create_failed"}
            else:
                print(f"[PIPELINE] ❌ git push 실패: {git_push.stderr.strip()[:150]}")
                result["steps"]["pr"] = {"status": "failed", "reason": "push_failed"}
                save("failed")
                return 1
        else:
            pr_url = "[DRY-RUN] PR URL"
        result["pr_url"] = pr_url
        result["steps"]["pr"] = {"status": "completed" if pr_url else "failed"}
        save("completed")
        print(f"\n[PIPELINE] ✅ LITE 완료  결과: {_PIPELINE_RESULT_PATH}")
        return 0

    # ── FULL 모드 ──────────────────────────────────────────────────────

    # ── PLAN 스텝 ────────────────────────────────────────────────────────
    plan_prompt = (
        f"다음 지시문에 대한 구현 계획을 작성하세요.\n\n{instruction}\n\n"
        "목표/범위/DoD/제약/실패조건을 각각 한 줄로 명시하세요.\n"
        "반드시 마지막 줄에 `VERDICT: PROCEED` 또는 `VERDICT: HOLD`를 출력하세요."
    )
    print("\n[PIPELINE] ▶ PLAN 스텝 실행 중...")
    if _step_already_done(_resume_data, "plan"):
        print("[PIPELINE] ⏭ PLAN 건너뜀 (이미 완료)")
        rc = 0
        out = "[RESUME] plan skipped"
    else:
        plan_started_at = _now()
        rc, out = _run_pipeline_step(root, "plan", plan_prompt, executor, STEP_TIMEOUT, dry_run=dry_run)
        plan_finished_at = _now()
        result["steps"]["plan"] = (
            _step_payload(
                "completed",
                plan_started_at,
                plan_finished_at,
                output_preview=out[:300],
            )
            if rc == 0
            else _failed_step_payload(
                step_name="plan",
                status="failed",
                started_at=plan_started_at,
                finished_at=plan_finished_at,
                rc=rc,
                output_preview=out[:300],
            )
        )
    _save_pipeline_result(root, result)

    if rc != 0:
        print(f"[PIPELINE] ❌ PLAN 실패")
        save("failed")
        return 1

    # PLAN VERDICT 판별 — HOLD이면 중단
    plan_verdict = _grep_verdict(out)
    if plan_verdict == "HOLD":
        print(f"[PIPELINE] ❌ PLAN VERDICT: HOLD — 재설계 필요")
        result["steps"]["plan"]["verdict"] = "HOLD"
        save("plan_hold")
        return 1

    # 사람 게이트 (--auto 없으면)
    if not auto and not dry_run:
        print("\n[PIPELINE] ⏸  PLAN 완료 — 확인 후 계속하려면 Enter, 중단하려면 Ctrl-C:")
        result["approval_required"] = True
        result["manual_gate_reason"] = "plan_confirmation"
        save("running")
        try:
            input()
        except (EOFError, KeyboardInterrupt):
            print("[PIPELINE] 중단됨")
            save("aborted")
            return 1
        result["approval_required"] = False
        result["manual_gate_reason"] = None
        save("running")

    # ── TASK 스텝 ────────────────────────────────────────────────────────
    task_prompt = (
        "[자동화 모드] 사용자 확인 없이 즉시 실행하세요.\n\n"
        f"{instruction}\n\n"
        "위 계획을 TDD로 구현하세요.\n"
        "1. 실패하는 테스트 작성 후 `python3 scripts/omc_pipeline_guard.py red-done <파일>` 실행\n"
        "2. 구현 후 테스트 GREEN 확인\n"
        "3. `python3 scripts/omc_tdd_check.py --staged` exit 0 확인\n"
        "만약 `VERDICT: BLOCK`이면 마지막에 `REASON_CODE: bad_entry_skill|metadata_missing|reroute_loop` 중 하나를 출력하세요.\n"
        "반드시 마지막 줄에 `VERDICT: PROCEED` (성공) 또는 `VERDICT: BLOCK` (실패)를 출력하세요."
        + _CRITIQUE_QUALITY_HINT
    )
    print("\n[PIPELINE] ▶ TASK 스텝 실행 중...")
    if _step_already_done(_resume_data, "task"):
        print("[PIPELINE] ⏭ TASK 건너뜀 (이미 완료)")
        task_rc = 0
        task_out = "[RESUME] task skipped"
    else:
        task_started_at = _now()
        task_rc, task_out = _run_pipeline_step(root, "task", task_prompt, executor, STEP_TIMEOUT * 2, dry_run=dry_run)
        # AMBIGUOUS_RESPONSE: verdict None 이면 1회 재시도
        if task_rc == 0 and _grep_verdict(task_out) is None:
            print("[PIPELINE] ⚠️  TASK verdict 미감지 (AMBIGUOUS) — 1회 재시도...")
            retry_prompt = task_prompt + "\n\n반드시 마지막 줄에 `VERDICT: PROCEED` 또는 `VERDICT: BLOCK`을 출력하세요."
            task_rc, task_out = _run_pipeline_step(root, "task", retry_prompt, executor, STEP_TIMEOUT * 2, dry_run=dry_run)
            if _grep_verdict(task_out) is None:
                print("[PIPELINE] ❌ TASK AMBIGUOUS_RESPONSE 2회 연속 — 중단")
                task_failed_at = _now()
                result["steps"]["task"] = _failed_step_payload(
                    step_name="task",
                    status="failed",
                    started_at=task_started_at,
                    finished_at=task_failed_at,
                    reason_codes=["ambiguous_response"],
                    output_preview=task_out[:300],
                )
                save("failed_ambiguous_response")
                return 1
        task_finished_at = _now()
        task_verdict = _grep_verdict(task_out)
        task_orchestration_reason_codes = _ensure_block_without_reason_code(
            step_name="task",
            verdict=task_verdict,
            reason_codes=_extract_orchestration_reason_codes(task_out),
        )
        if task_rc == 0 and task_verdict == "BLOCK" and task_orchestration_reason_codes:
            result["steps"]["task"] = _failed_step_payload(
                step_name="task",
                status="failed",
                started_at=task_started_at,
                finished_at=task_finished_at,
                verdict="BLOCK",
                reason_codes=task_orchestration_reason_codes,
                output_preview=task_out[:300],
            )
        else:
            result["steps"]["task"] = (
                _step_payload(
                    "completed",
                    task_started_at,
                    task_finished_at,
                    output_preview=task_out[:300],
                )
                if task_rc == 0
                else _failed_step_payload(
                    step_name="task",
                    status="failed",
                    started_at=task_started_at,
                    finished_at=task_finished_at,
                    rc=task_rc,
                    output_preview=task_out[:300],
                )
            )
    result["last_completed_step"] = "task"
    _save_pipeline_result(root, result)

    if task_rc != 0:
        print("[PIPELINE] ❌ TASK 실패")
        save("failed")
        return 1

    _ensure_staged(root, dry_run, "TASK")

    # ── CRITIQUE/REVIEW 루프 ────────────────────────────────────────────
    critique_auto_retry_count = 0  # critique 루프 탈출 후 plan 자동 재진입 횟수
    task_auto_retry_count = 0       # critique 루프 탈출 후 task 재실행 횟수
    task_stage_plan_retry_count = 0

    def _run_plan_retry_recovery(
        *,
        critique_issues: str,
        reason_label: str,
        counter_key: str,
    ) -> bool:
        """plan_retry 복구 실행을 공통화한다."""
        nonlocal critique_auto_retry_count, task_auto_retry_count, needs_ctx_refresh, task_stage_plan_retry_count
        if counter_key == "critique":
            critique_auto_retry_count += 1
            current_retry_count = critique_auto_retry_count
            retry_limit = _CRITIQUE_AUTO_RETRY_MAX
        else:
            task_stage_plan_retry_count += 1
            current_retry_count = task_stage_plan_retry_count
            retry_limit = 1
        if current_retry_count > retry_limit:
            print(f"[PIPELINE] ❌ PLAN 재실행 한도 초과 ({current_retry_count}/{retry_limit}) — HOLD")
            save("hold")
            return False
        print(f"[PIPELINE] 🔄 PLAN 재실행 ({current_retry_count}/{retry_limit}) — {reason_label}")
        issues_section = (
            f"\n\n[이전 critique 지적 사항]\n{critique_issues}\n"
            if critique_issues else ""
        )
        retry_plan_prompt = (
            f"{reason_label}에 의해 plan 재실행이 필요합니다.{issues_section}"
            f"\n지시문: {instruction[:200]}\n\n"
            "목표/범위/DoD/제약/실패조건을 각각 한 줄로 명시하세요.\n"
            "반드시 마지막 줄에 `VERDICT: PROCEED` 또는 `VERDICT: HOLD`를 출력하세요."
        )
        print("\n[PIPELINE] ▶ PLAN 재실행...")
        plan_retry_started_at = _now()
        plan_rc, plan_out = _run_pipeline_step(
            root, "plan_retry", retry_plan_prompt, executor, STEP_TIMEOUT, dry_run=dry_run
        )
        plan_retry_finished_at = _now()
        result["steps"]["plan_retry"] = _retry_step_payload(
            step_name="plan_retry",
            rc=plan_rc,
            started_at=plan_retry_started_at,
            finished_at=plan_retry_finished_at,
            output_preview=plan_out[:300],
            retry_count=current_retry_count,
        )
        result["last_completed_step"] = "plan_retry"
        _save_pipeline_result(root, result)
        if plan_rc != 0:
            print("[PIPELINE] ❌ PLAN 재실행 실패 — HOLD")
            save("hold")
            return False
        if _grep_verdict(plan_out) == "HOLD":
            print("[PIPELINE] ❌ PLAN 재실행 VERDICT: HOLD — 재설계 필요")
            result["steps"]["plan_retry"]["verdict"] = "HOLD"
            result["steps"]["plan_retry"].update(
                _build_failure_metadata(
                    step_name="plan_retry",
                    status=result["steps"]["plan_retry"].get("status"),
                    verdict="HOLD",
                )
            )
            _save_pipeline_result(root, result)
            save("hold")
            return False
        task_auto_retry_count = 0  # plan_retry 후 task retry 기회 복원
        needs_ctx_refresh = True  # plan_retry 후 새 diff 반영
        return True

    task_step = result["steps"].get("task", {})
    if (
        task_step.get("decision") == "reroute"
        and str(task_step.get("reroute_target") or "").strip() == "plan_retry"
    ):
        if _step_already_done(_resume_data, "plan_retry"):
            print("[PIPELINE] ⏭ TASK-stage PLAN 재실행 건너뜀 (resume: 이미 완료)")
        else:
            if not _run_plan_retry_recovery(
                critique_issues=str(task_step.get("critique_issues") or ""),
                reason_label="TASK orchestration failure",
                counter_key="task_stage",
            ):
                return 2
    elif task_step.get("decision") == "hold":
        save("hold")
        return 2

    # resume 시 task_retry/plan_retry 완료 여부로 루프 카운터 복원
    if _resume_data:
        if _step_already_done(_resume_data, "task_retry"):
            task_auto_retry_count = 1
        if _step_already_done(_resume_data, "plan_retry"):
            critique_auto_retry_count = 1
            task_auto_retry_count = 0  # plan_retry 후엔 task retry 기회 복원

    for loop_step in ("critique", "review"):
        verdict_ok = ("PROCEED", "APPROVE")
        retry_count = 0
        prev_verdict: object = _UNSET_VERDICT  # sentinel: 아직 verdict 없음
        same_verdict_streak = 0

        # isolated 컨텍스트: instruction 없이 diff+TDD 결과만 전달 → 편향 제거
        def _make_loop_prompt() -> str:
            """현재 staged diff를 읽어 critique/review 프롬프트를 생성한다.

            task_retry/plan_retry 후 루프 재진입 시 새 변경을 반영하기 위해 재호출한다.
            """
            ctx = _get_critique_context(root)
            step_label = "omc-critique 스킬(pre-mortem)" if loop_step == "critique" else "omc-review 스킬"
            return (
                f"아래 코드 변경을 {step_label}으로 검토하세요.\n"
                "구현 의도나 지시문은 제공하지 않습니다 — 코드와 테스트 결과 자체로만 판단하세요.\n\n"
                f"{ctx}\n\n"
                "반드시 마지막 줄에 `VERDICT: PROCEED` / `VERDICT: APPROVE` / "
                "`VERDICT: REVISE` / `VERDICT: BLOCK` / `VERDICT: HOLD` 중 하나를 출력하세요."
            )

        base_loop_prompt = _make_loop_prompt()
        needs_ctx_refresh = False  # task_retry/plan_retry 후 True → 루프 상단에서 재생성

        prev_critique_issues: str = ""  # 직전 critique 지적 내용 — 다음 retry에 전달
        while retry_count <= _PIPELINE_MAX_RETRIES:
            loop_started_at = _now()
            if time.time() > deadline:
                print(f"[PIPELINE] ❌ 최대 실행 시간 초과 ({max_time}s)")
                timeout_payload = _step_payload(
                    "timeout",
                    loop_started_at,
                    _now(),
                    **_build_failure_metadata(
                        step_name=loop_step,
                        status="timeout",
                        reason_codes=["timeout"],
                    ),
                )
                timeout_decision = _decision_policy_entry(
                    failure_class=str(timeout_payload.get("failure_class") or ""),
                    reason_codes=list(timeout_payload.get("reason_codes") or []),
                    retry_count=retry_count,
                    escalation_policy=str(
                        _pipeline_step_metadata(loop_step).get("escalation_policy") or "default"
                    ),
                )
                result["steps"][loop_step] = _persist_escalation_decision(timeout_payload, timeout_decision)
                save("timeout")
                return 1

            # task_retry/plan_retry 후 재진입 시 staged diff 갱신
            if needs_ctx_refresh:
                base_loop_prompt = _make_loop_prompt()
                needs_ctx_refresh = False

            # 재시도 시 직전 verdict + 이전 지적 내용 주입
            loop_prompt = _build_retry_prompt(
                base_loop_prompt, retry_count,
                prev_verdict=prev_verdict,
                prev_issues=prev_critique_issues if retry_count > 0 else None,
            )

            print(f"\n[PIPELINE] ▶ {loop_step.upper()} 스텝 (시도 {retry_count + 1}/{_PIPELINE_MAX_RETRIES + 1})...")
            rc, out = _run_pipeline_step(
                root, loop_step, loop_prompt, executor, STEP_TIMEOUT,
                dry_run=dry_run, isolated=True,
            )
            loop_finished_at = _now()

            verdict = _grep_verdict(out)
            print(f"  VERDICT: {verdict or '미감지'}")

            # REVISE/HOLD 시 지적 내용 저장 → 다음 retry 프롬프트에 전달
            if verdict in ("REVISE", "HOLD"):
                _issues = _extract_critique_issues(out)
                if _issues:
                    prev_critique_issues = _issues

            if rc == 0 and verdict in verdict_ok:
                result["steps"][loop_step] = _step_payload(
                    "completed",
                    loop_started_at,
                    loop_finished_at,
                    verdict=verdict,
                    attempt=retry_count + 1,
                )
                break

            # T2: BLOCK 즉시 탈출 — 재시도 없이 바로 에스컬레이션
            if rc == 0 and verdict == "BLOCK":
                print(f"[PIPELINE] ❌ {loop_step.upper()} VERDICT: BLOCK — 즉시 탈출")
                critique_issues = _extract_critique_issues(out)
                step_payload = _step_payload(
                    "failed_critique_loop",
                    loop_started_at,
                    loop_finished_at,
                    verdict="BLOCK",
                    streak=1,
                    last_output=out[:2000],
                    critique_issues=critique_issues,
                    attempt=retry_count + 1,
                    **_build_failure_metadata(
                        step_name=loop_step,
                        status="failed_critique_loop",
                        verdict="BLOCK",
                    ),
                )
                decision = _failure_step_decision(
                    step_name=loop_step,
                    step_payload=step_payload,
                    retry_count=retry_count,
                )
                result["steps"][loop_step] = _persist_escalation_decision(step_payload, decision)
                _save_pipeline_result(root, result)
                same_verdict_streak = _PIPELINE_MAX_SAME_VERDICT  # 에스컬레이션 트리거
                # 아래 에스컬레이션 블록으로 fall-through

            # 동일 verdict 연속 감지
            # T1: rc=0 + None(미감지)도 streak에 포함 — 인프라 오류(rc≠0)는 제외
            elif rc == 0 and verdict is None and prev_verdict is None:
                # prev_verdict == _UNSET_VERDICT(sentinel)이면 이 조건 불충족 → streak 미증가
                same_verdict_streak += 1
            elif verdict is not None and verdict == prev_verdict:
                same_verdict_streak += 1
            else:
                same_verdict_streak = 0
            prev_verdict = verdict

            if same_verdict_streak >= _PIPELINE_MAX_SAME_VERDICT:
                # BLOCK 즉시 탈출이 이미 result["steps"]를 저장했으면 덮어쓰지 않음
                if result["steps"].get(loop_step, {}).get("verdict") != "BLOCK":
                    print(f"[PIPELINE] ❌ {loop_step.upper()} 동일 verdict({verdict}) {same_verdict_streak + 1}회 연속 — 탈출")
                    critique_issues = _extract_critique_issues(out)
                    step_payload = _step_payload(
                        "failed_critique_loop",
                        loop_started_at,
                        loop_finished_at,
                        verdict=verdict,
                        streak=same_verdict_streak + 1,
                        last_output=out[:2000],
                        critique_issues=critique_issues,
                        attempt=retry_count + 1,
                        **_build_failure_metadata(
                            step_name=loop_step,
                            status="failed_critique_loop",
                            verdict=verdict,
                        ),
                    )
                    decision = _failure_step_decision(
                        step_name=loop_step,
                        step_payload=step_payload,
                        retry_count=retry_count,
                    )
                    result["steps"][loop_step] = _persist_escalation_decision(step_payload, decision)
                    _save_pipeline_result(root, result)
                else:
                    critique_issues = (
                        result["steps"].get(loop_step, {}).get("critique_issues")
                        or prev_critique_issues
                        or ""
                    )
                if not isinstance(critique_issues, str):
                    critique_issues = str(critique_issues)

                persisted_step = result["steps"].get(loop_step, {})
                decision_name = str(persisted_step.get("decision") or "").strip().lower()
                reroute_target = str(persisted_step.get("reroute_target") or "").strip()
                if loop_step == "critique" and decision_name == "hold":
                    save("hold")
                    return 2

                recovery_target = _recovery_target_from_decision(
                    loop_step=loop_step,
                    decision_name=decision_name,
                    reroute_target=reroute_target,
                    task_auto_retry_count=task_auto_retry_count,
                    critique_auto_retry_count=critique_auto_retry_count,
                )

                # ── critique 루프 탈출 시 복구 에스컬레이션 ─────────────────
                # 1순위: task_retry (critique_issues 반영) → critique 재진입
                # 2순위: plan_retry → critique 재진입
                # 소진 시: hold
                if recovery_target == "task_retry":
                    task_auto_retry_count += 1
                    print(
                        f"[PIPELINE] 🔄 TASK 재실행 ({task_auto_retry_count}/{_TASK_AUTO_RETRY_MAX})"
                        " — critique 이슈 반영"
                    )
                    issues_section = (
                        f"\n\n[critique 지적 사항]\n{critique_issues}\n"
                        if critique_issues else ""
                    )
                    task_retry_prompt = (
                        "[자동화 모드] 사용자 확인 없이 즉시 실행하세요.\n\n"
                        f"{instruction}\n\n"
                        "이전 critique에서 다음 문제가 지적됐습니다. 이를 수정해 재구현하세요."
                        f"{issues_section}"
                        "TDD로 구현하세요.\n"
                        "1. 실패하는 테스트 작성 후 `python3 scripts/omc_pipeline_guard.py red-done <파일>` 실행\n"
                        "2. 구현 후 테스트 GREEN 확인\n"
                        "3. `python3 scripts/omc_tdd_check.py --staged` exit 0 확인\n"
                        "반드시 마지막 줄에 `VERDICT: PROCEED` (성공) 또는 `VERDICT: BLOCK` (실패)를 출력하세요."
                        + _CRITIQUE_QUALITY_HINT
                    )
                    print("\n[PIPELINE] ▶ TASK 재실행 (critique 이슈 반영)...")
                    task_retry_started_at = _now()
                    task_retry_rc, task_retry_out = _run_pipeline_step(
                        root, "task_retry", task_retry_prompt, executor, STEP_TIMEOUT * 2, dry_run=dry_run
                    )
                    task_retry_finished_at = _now()
                    result["steps"]["task_retry"] = _retry_step_payload(
                        step_name="task_retry",
                        rc=task_retry_rc,
                        started_at=task_retry_started_at,
                        finished_at=task_retry_finished_at,
                        output_preview=task_retry_out[:300],
                        retry_count=task_auto_retry_count,
                    )
                    result["last_completed_step"] = "task_retry"
                    _save_pipeline_result(root, result)
                    if task_retry_rc != 0:
                        print("[PIPELINE] ❌ TASK 재실행 실패 — HOLD")
                        save("hold")
                        return 2
                    task_retry_verdict = _grep_verdict(task_retry_out)
                    task_retry_orchestration_reason_codes = _ensure_block_without_reason_code(
                        step_name="task_retry",
                        verdict=task_retry_verdict,
                        reason_codes=_extract_orchestration_reason_codes(task_retry_out),
                    )
                    if task_retry_verdict == "BLOCK":
                        print("[PIPELINE] ❌ TASK 재실행 VERDICT: BLOCK — HOLD")
                        result["steps"]["task_retry"] = _task_retry_block_failure_payload(
                            started_at=task_retry_started_at,
                            finished_at=task_retry_finished_at,
                            output_preview=task_retry_out[:300],
                            retry_count=task_auto_retry_count,
                            reason_codes=task_retry_orchestration_reason_codes,
                        )
                        _save_pipeline_result(root, result)
                        save("hold")
                        return 2
                    # critique 루프 재진입: 카운터·스트릭 초기화
                    retry_count = 0
                    prev_verdict = _UNSET_VERDICT  # 재진입 후 첫 None 오분류 방지
                    same_verdict_streak = 0
                    prev_critique_issues = ""  # 재진입 시 지적 내용 초기화
                    _ensure_staged(root, dry_run, "TASK_RETRY")
                    needs_ctx_refresh = True  # task_retry 후 새 diff 반영
                    continue

                # 2순위: plan 자동 재진입 (최대 1회)
                if recovery_target == "plan_retry":
                    if not _run_plan_retry_recovery(
                        critique_issues=critique_issues,
                        reason_label="이전 critique",
                        counter_key="critique",
                    ):
                        return 2
                    # critique 루프 재진입: 카운터·스트릭 초기화
                    retry_count = 0
                    prev_verdict = _UNSET_VERDICT  # 재진입 후 첫 None 오분류 방지
                    same_verdict_streak = 0
                    continue

                save("hold")
                return 2

            retry_count += 1
            if retry_count > _PIPELINE_MAX_RETRIES:
                print(f"[PIPELINE] ❌ {loop_step.upper()} retry 소진 ({_PIPELINE_MAX_RETRIES}회)")
                critique_issues = prev_critique_issues or _extract_critique_issues(out)
                if not critique_issues:
                    critique_issues = f"{loop_step} retry exhausted without parseable critique issues"
                step_payload = _step_payload(
                    "retry_exhausted",
                    loop_started_at,
                    loop_finished_at,
                    verdict=verdict,
                    last_output=out[:300],
                    critique_issues=critique_issues[:2000],
                    attempt=retry_count,
                    **_build_failure_metadata(
                        step_name=loop_step,
                        status="retry_exhausted",
                        verdict=verdict,
                    ),
                )
                decision = _failure_step_decision(
                    step_name=loop_step,
                    step_payload=step_payload,
                    retry_count=retry_count,
                )
                result["steps"][loop_step] = _persist_escalation_decision(step_payload, decision)
                _save_pipeline_result(root, result)

                persisted_step = result["steps"].get(loop_step, {})
                decision_name = str(persisted_step.get("decision") or "").strip().lower()
                reroute_target = str(persisted_step.get("reroute_target") or "").strip()
                if loop_step == "critique" and decision_name == "hold":
                    save("hold")
                    return 2

                # retry_exhausted → 복구 경로는 헬퍼에서 단일 결정
                recovery_target = _recovery_target_from_decision(
                    loop_step=loop_step,
                    decision_name=decision_name,
                    reroute_target=reroute_target,
                    task_auto_retry_count=task_auto_retry_count,
                    critique_auto_retry_count=critique_auto_retry_count,
                )
                if recovery_target == "task_retry":
                    task_auto_retry_count += 1
                    print(
                        f"[PIPELINE] 🔄 TASK 재실행 ({task_auto_retry_count}/{_TASK_AUTO_RETRY_MAX})"
                        " — retry_exhausted 복구"
                    )
                    issues_section = f"\n\n[critique 지적 사항]\n{critique_issues}\n"
                    task_retry_prompt = (
                        "[자동화 모드] 사용자 확인 없이 즉시 실행하세요.\n\n"
                        f"{instruction}\n\n"
                        "critique에서 다음 문제가 지적됐습니다. 이를 수정해 재구현하세요."
                        f"{issues_section}"
                        "TDD로 구현하세요.\n"
                        "1. 실패하는 테스트 작성 후 `python3 scripts/omc_pipeline_guard.py red-done <파일>` 실행\n"
                        "2. 구현 후 테스트 GREEN 확인\n"
                        "3. `python3 scripts/omc_tdd_check.py --staged` exit 0 확인\n"
                        "반드시 마지막 줄에 `VERDICT: PROCEED` (성공) 또는 `VERDICT: BLOCK` (실패)를 출력하세요."
                        + _CRITIQUE_QUALITY_HINT
                    )
                    print("\n[PIPELINE] ▶ TASK 재실행 (retry_exhausted 복구)...")
                    task_retry_started_at = _now()
                    task_retry_rc, task_retry_out = _run_pipeline_step(
                        root, "task_retry", task_retry_prompt, executor, STEP_TIMEOUT * 2, dry_run=dry_run
                    )
                    task_retry_finished_at = _now()
                    result["steps"]["task_retry"] = _retry_step_payload(
                        step_name="task_retry",
                        rc=task_retry_rc,
                        started_at=task_retry_started_at,
                        finished_at=task_retry_finished_at,
                        output_preview=task_retry_out[:300],
                        retry_count=task_auto_retry_count,
                    )
                    _save_pipeline_result(root, result)
                    task_retry_verdict = _grep_verdict(task_retry_out)
                    task_retry_orchestration_reason_codes = _ensure_block_without_reason_code(
                        step_name="task_retry",
                        verdict=task_retry_verdict,
                        reason_codes=_extract_orchestration_reason_codes(task_retry_out),
                    )
                    if task_retry_rc == 0 and task_retry_verdict not in ("BLOCK", None):
                        # critique 루프 재진입
                        retry_count = 0
                        prev_verdict = _UNSET_VERDICT
                        same_verdict_streak = 0
                        prev_critique_issues = ""
                        needs_ctx_refresh = True  # task_retry 후 새 diff 반영
                        continue
                    if task_retry_verdict == "BLOCK":
                        result["steps"]["task_retry"] = _task_retry_block_failure_payload(
                            started_at=task_retry_started_at,
                            finished_at=task_retry_finished_at,
                            output_preview=task_retry_out[:300],
                            retry_count=task_auto_retry_count,
                            reason_codes=task_retry_orchestration_reason_codes,
                        )
                        _save_pipeline_result(root, result)
                    # task_retry 실패/BLOCK → retry_exhausted 유지
                    print("[PIPELINE] ❌ TASK 재실행 실패 또는 BLOCK — retry_exhausted")

                if recovery_target == "plan_retry":
                    critique_auto_retry_count += 1
                    print(f"[PIPELINE] 🔄 CRITIQUE 자동 재진입 ({critique_auto_retry_count}/{_CRITIQUE_AUTO_RETRY_MAX}) — plan 재실행")
                    issues_section = (
                        f"\n\n[이전 critique 지적 사항]\n{critique_issues}\n"
                        if critique_issues else ""
                    )
                    retry_plan_prompt = (
                        f"이전 critique에서 다음 문제가 지적됐습니다. 이를 반영해 구현 계획을 수정하세요.{issues_section}"
                        f"\n지시문: {instruction[:200]}\n\n"
                        "목표/범위/DoD/제약/실패조건을 각각 한 줄로 명시하세요.\n"
                        "반드시 마지막 줄에 `VERDICT: PROCEED` 또는 `VERDICT: HOLD`를 출력하세요."
                    )
                    print("\n[PIPELINE] ▶ PLAN 재실행 (critique 이슈 반영)...")
                    plan_retry_started_at = _now()
                    plan_rc, plan_out = _run_pipeline_step(
                        root, "plan_retry", retry_plan_prompt, executor, STEP_TIMEOUT, dry_run=dry_run
                    )
                    plan_retry_finished_at = _now()
                    result["steps"]["plan_retry"] = _retry_step_payload(
                        step_name="plan_retry",
                        rc=plan_rc,
                        started_at=plan_retry_started_at,
                        finished_at=plan_retry_finished_at,
                        output_preview=plan_out[:300],
                        retry_count=critique_auto_retry_count,
                    )
                    _save_pipeline_result(root, result)
                    if plan_rc != 0:
                        print("[PIPELINE] ❌ PLAN 재실행 실패 — HOLD")
                        save("hold")
                        return 2
                    if _grep_verdict(plan_out) == "HOLD":
                        print("[PIPELINE] ❌ PLAN 재실행 VERDICT: HOLD — 재설계 필요")
                        result["steps"]["plan_retry"]["verdict"] = "HOLD"
                        result["steps"]["plan_retry"].update(
                            _build_failure_metadata(
                                step_name="plan_retry",
                                status=result["steps"]["plan_retry"].get("status"),
                                verdict="HOLD",
                            )
                        )
                        _save_pipeline_result(root, result)
                        save("hold")
                        return 2
                    retry_count = 0
                    prev_verdict = _UNSET_VERDICT
                    same_verdict_streak = 0
                    task_auto_retry_count = 0
                    needs_ctx_refresh = True
                    continue

                save("retry_exhausted")
                return 1

            print(f"  재시도 중...")

        _save_pipeline_result(root, result)

    # ── PR 생성 ──────────────────────────────────────────────────────────
    pr_url = None
    if not dry_run:
        pr_started_at = _now()
        git_push = subprocess.run(
            ["git", "push", "-u", "origin", branch],
            capture_output=True, text=True, cwd=str(root),
        )
        if git_push.returncode == 0:
            pr_proc = subprocess.run(
                ["gh", "pr", "create",
                 "--title", instruction[:72],
                 "--body", f"자동 생성 PR\n\n지시문: {instruction[:300]}",
                 "--base", "main"],
                capture_output=True, text=True, cwd=str(root),
            )
            if pr_proc.returncode == 0:
                pr_url = pr_proc.stdout.strip()
                print(f"[PIPELINE] ✅ PR 생성: {pr_url}")
            else:
                print(f"[PIPELINE] ⚠️  PR 생성 실패: {pr_proc.stderr.strip()[:150]}")
        else:
            print(f"[PIPELINE] ❌ git push 실패: {git_push.stderr.strip()[:150]}")
            pr_finished_at = _now()
            result["steps"]["pr"] = _step_payload(
                "failed",
                pr_started_at,
                pr_finished_at,
                reason="push_failed",
            )
            save("failed")
            return 1
    else:
        pr_started_at = _now()
        pr_url = "[DRY-RUN] PR URL"
        print(f"[PIPELINE] [DRY-RUN] PR 생성 시뮬레이션")

    result["pr_url"] = pr_url
    pr_finished_at = _now()
    result["steps"]["pr"] = _step_payload(
        "completed" if pr_url else "failed",
        pr_started_at,
        pr_finished_at,
    )
    save("completed")
    print(f"\n[PIPELINE] ✅ 완료  결과: {_PIPELINE_RESULT_PATH}")
    return 0

# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main() -> int:
    """CLI entrypoint for omc_autopilot.py."""
    ap = argparse.ArgumentParser(description="OMC 멀티 LLM 자율 루프 (옵트인)")
    ap.add_argument("--target", type=Path, default=Path.cwd())
    sub = ap.add_subparsers(dest="cmd", required=True)

    p_run = sub.add_parser("run", help="태스크 파일 실행")
    p_run.add_argument("--task", type=Path, required=True, help="태스크 JSON 파일 경로")
    p_run.add_argument("--dry-run", action="store_true", help="실제 LLM 호출 없이 계획만 출력")
    p_run.add_argument(
        "--resume-failed",
        action=argparse.BooleanOptionalAction,
        default=False,
        help="이전 failed 스텝을 재실행할지 여부 (기본: 미재실행)",
    )

    p_new = sub.add_parser("new", help="예시 태스크 파일 생성")
    p_new.add_argument("--id", dest="task_id", required=True, help="태스크 ID (파일명)")
    p_new.add_argument("--title", required=True, help="태스크 제목")

    p_status = sub.add_parser("status", help="실행 기록 조회")
    p_status.add_argument("--task-id", default=None, help="특정 태스크 ID 조회")

    p_pipeline = sub.add_parser("pipeline", help="full-pipeline 자동화 (plan→critique→task→review→PR)")
    p_pipeline.add_argument("--instruction", required=True, help="작업 지시문")
    p_pipeline.add_argument("--branch", default="feat/autopilot", help="생성할 feature 브랜치 이름")
    p_pipeline.add_argument("--executor", default="auto", help="LLM 실행기 (auto|codex|gemini|claude)")
    p_pipeline.add_argument("--max-time", type=int, default=7200, help="전체 최대 실행 시간(초)")
    p_pipeline.add_argument("--dry-run", action="store_true", help="실제 LLM 호출 없이 흐름 확인")
    p_pipeline.add_argument("--auto", action="store_true", help="사람 게이트 없이 완전 자동 실행")
    p_pipeline.add_argument("--mode", choices=["auto", "lite", "full"], default="auto",
                            help="파이프라인 모드 (auto: 자동감지, lite: 토큰 절약, full: 전체)")
    p_pipeline.add_argument("--force", action="store_true",
                            help="짧은 지시문 경고 무시하고 강제 실행")
    p_pipeline.add_argument("--allow-dirty", action="store_true",
                            help="uncommitted 변경이 있어도 강제 실행")
    p_pipeline.add_argument("--resume", action="store_true",
                            help="이전 실행 결과에서 실패 단계부터 재개")


    p_pipeline_status = sub.add_parser("pipeline-status", help="pipeline 실행 결과 상태 조회")
    p_pipeline_status.add_argument("--watch", action="store_true", help="N초 간격으로 화면을 갱신하며 실시간 모니터링")
    p_pipeline_status.add_argument("--interval", type=int, default=2, help="--watch 갱신 주기(초, 기본 2, 최소 1)")
    p_pipeline_status.add_argument("--recover", action="store_true", help="stale running 상태를 hold로 수동 복구")

    p_benchmark_report = sub.add_parser("benchmark-report", help="pipeline 결과를 벤치마크 리포트 JSON으로 출력")
    p_benchmark_report.add_argument("--result-file", type=Path, default=None, help="읽을 pipeline result JSON 경로")
    p_benchmark_report.add_argument("--format", choices=["json"], default="json", help="출력 형식 (v1: json)")

    p_runs = sub.add_parser("runs", help="pipeline 실행 이력 조회 (.omc/runs/)")
    p_runs.add_argument("--limit", type=int, default=20, help="표시할 최대 개수 (기본: 20)")
    p_runs.add_argument("--branch", dest="branch_filter", default=None, help="브랜치 이름 필터")
    p_runs.add_argument("--status", dest="status_filter", default=None, help="상태 필터 (completed/failed 등)")

    p_overview = sub.add_parser("overview", help="read-only autopilot 관제 요약")
    p_overview.add_argument("--limit", type=int, default=10, help="표시할 최대 run 개수 (기본: 10)")

    args = ap.parse_args()
    root = omc_utils.project_root(args.target)

    if args.cmd == "run":
        task_file = args.task if args.task.is_absolute() else (root / args.task)
        return cmd_run(
            root,
            task_file,
            dry_run=args.dry_run,
            resume_failed=args.resume_failed,
        )
    if args.cmd == "new":
        return cmd_new(root, args.task_id, args.title)
    if args.cmd == "status":
        return cmd_status(root, args.task_id)
    if args.cmd == "pipeline-status":
        return cmd_pipeline_status(root, watch=args.watch, interval=args.interval, recover=args.recover)
    if args.cmd == "benchmark-report":
        return cmd_benchmark_report(root, result_file=args.result_file, output_format=args.format)
    if args.cmd == "runs":
        return cmd_runs(root, limit=args.limit, branch_filter=args.branch_filter, status_filter=args.status_filter)
    if args.cmd == "overview":
        return cmd_overview(root, limit=args.limit)
    if args.cmd == "pipeline":
        # pre-flight 검증
        args.instruction = args.instruction.strip()
        if not args.instruction:
            print("[PIPELINE] ❌ --instruction이 비어있습니다.", file=sys.stderr)
            return 1
        args.branch = args.branch.strip()
        if not args.branch:
            print("[PIPELINE] ❌ --branch가 비어있습니다.", file=sys.stderr)
            return 1
        if len(args.instruction) < 10 and not args.force:
            print(
                f"[PIPELINE] ⚠️  지시문이 너무 짧습니다 ({len(args.instruction)}자).\n"
                "           구체적인 지시문을 작성하거나 --force로 강제 실행하세요.",
                file=sys.stderr,
            )
            return 1
        return cmd_pipeline(
            root,
            instruction=args.instruction,
            branch=args.branch,
            executor_pref=args.executor,
            max_time=args.max_time,
            dry_run=args.dry_run,
            auto=args.auto,
            mode_arg=args.mode,
            allow_dirty=args.allow_dirty,
            resume=args.resume,
        )
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
