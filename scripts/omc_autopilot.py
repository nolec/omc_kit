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

_TASKS_DIR = ".omc/tasks"
_AUTOPILOT_STATE_DIR = ".omc/state/autopilot"
_DEFAULT_TIMEOUT_SEC = 120
_DEFAULT_MAX_RETRIES = 1
_DEFAULT_STATUS_LIMIT = 20
_POLICY_WARNED_KEYS: set[str] = set()
_COMPATIBILITY_WARNED_COMMANDS: set[str] = set()


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

def _run_step(
    root: Path,
    step: dict,
    *,
    executor: str,
    timeout_sec: int,
    prompt_override: str | None = None,
    isolated: bool = False,
) -> tuple[int, str]:
    """omc_exec.py를 통해 스텝 프롬프트를 실행합니다.

    Args:
        isolated: True이면 fresh context로 실행합니다. 기본값은 False입니다.

    Returns:
        (returncode, output_text)
    """
    exec_script = Path(__file__).resolve().parent / "omc_exec.py"
    if not exec_script.exists():
        return 1, f"[ERROR] omc_exec.py 없음: {exec_script}"

    prompt = (prompt_override or step.get("prompt", "")).strip()
    if not prompt:
        return 1, "[ERROR] 스텝에 prompt가 없습니다."

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

        cmd = [
            sys.executable,
            str(exec_script),
            "--target", str(root),
            "--prompt-file", str(prompt_file),
            "--executor", executor,
            "--execution-mode", "headless",
            "--timeout-sec", str(timeout_sec),
        ]
        if isolated:
            cmd.append("--fresh-context")

        proc = subprocess.run(
            cmd,
            cwd=str(root),
            capture_output=True,
            text=True,
            timeout=timeout_sec + 30,
        )
        output = (proc.stdout or "") + (proc.stderr or "")
        return int(proc.returncode), output.strip()
    except subprocess.TimeoutExpired:
        return 1, "[ERROR] 타임아웃 초과"
    except Exception as exc:
        return 1, f"[ERROR] 실행 예외: {exc}"
    finally:
        if prompt_file:
            try:
                Path(prompt_file).unlink()
            except OSError:
                pass


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
    executor_pref = task.get("executor", "auto")
    max_retries = int(task.get("max_retries", _DEFAULT_MAX_RETRIES))
    steps_raw = task.get("steps", [])

    if not steps_raw:
        print(f"[AUTOPILOT] 스텝이 없습니다: {task_file}")
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

    print(f"\n[AUTOPILOT] ▶ 태스크 시작: {title}")
    print(f"           executor={executor}  스텝={len(steps)}개  max_retries={max_retries}")
    if dry_run:
        print("           [DRY-RUN] 실제 실행 없이 계획만 출력합니다.\n")

    state = _load_state(root, task_id)
    state["task_id"] = task_id
    state["title"] = title
    state["executor"] = executor
    state["started_at"] = state.get("started_at") or _now()
    state["status"] = "running"
    if "steps" not in state:
        state["steps"] = {}

    failed_count = 0

    for step in steps:
        sid = step["id"]
        step_title = step.get("title", sid)
        timeout_sec = int(step.get("timeout_sec", _DEFAULT_TIMEOUT_SEC))

        # 이미 완료된 스텝은 건너뜀
        if state["steps"].get(sid, {}).get("status") == "completed":
            print(f"  [SKIP] {sid}: {step_title} (이미 완료)")
            continue

        # 실패 스텝 재실행 정책:
        # - 기본값(False): 이전 실패 상태를 유지(기존 동작)
        # - resume_failed=True: 이전 실패 스텝 재실행
        if state["steps"].get(sid, {}).get("status") == "failed":
            if not resume_failed:
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
            else:
                if last_failures:
                    print(f"  재시도 (attempt {attempt}) — 이전 실패 컨텍스트 주입됨")
                else:
                    print(f"  실행 중 (attempt {attempt}/{max_retries + 1})...")
                rc, output = _run_step(
                    root,
                    step_with_prompt,
                    executor=executor,
                    timeout_sec=timeout_sec,
                    isolated=bool(step.get("isolated", False)),
                )

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

    final_verdict = None
    for step in reversed(list(steps.values())):
        if step.get("verdict"):
            final_verdict = step.get("verdict")
            break

    failure_category = None
    if data.get("status") != "completed":
        for name, step in steps.items():
            step_status = step.get("status")
            if step_status != "completed":
                failure_category = f"{name}:{step_status or 'unknown'}"
                break
        if failure_category is None:
            failure_category = str(data.get("status") or "unknown")

    return {
        "status": data.get("status"),
        "pipeline_success": data.get("status") == "completed",
        "mode": data.get("mode"),
        "executor": data.get("executor"),
        "branch": data.get("branch"),
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
        "success_rate": (completed_steps / total_steps) if total_steps else 0,
        "final_verdict": final_verdict,
        "failure_category": failure_category,
        "cost_estimate": None,
        "token_usage": None,
        "executor_cost_source": None,
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
        status_icon = _STATUS_ICON_MAP.get(str(s.get("status", "")).lower(), "❓")
        print(f"\n{status_icon} [{s.get('status', '?')}] {s.get('title', f.stem)}")
        print(f"   id={s.get('task_id', f.stem)}  executor={s.get('executor', '?')}")
        print(f"   시작: {s.get('started_at', '-')}  완료: {s.get('finished_at', '-')}")
        for sid, ss in s.get("steps", {}).items():
            icon = _STATUS_ICON_MAP.get(str(ss.get("status", "")).lower(), "❓")
            print(f"   {icon} {sid}: {ss.get('status', '?')} (시도 {ss.get('attempt', '-')})")
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
    payload = json.dumps(data, ensure_ascii=False, indent=2)
    # 원자적 쓰기 (tmpfile → replace)
    tmp = out.parent / (out.name + ".tmp")
    tmp.write_text(payload, encoding="utf-8")
    tmp.replace(out)
    # run 이력 분리 저장 (.omc/runs/{run_id}/result.json)
    try:
        run_id = datetime.now().strftime("%Y%m%dT%H%M%S") + "-" + str(uuid.uuid4())[:8]
        run_path = root / ".omc" / "runs" / run_id / "result.json"
        run_path.parent.mkdir(parents=True, exist_ok=True)
        run_tmp = run_path.parent / "result.json.tmp"
        run_tmp.write_text(payload, encoding="utf-8")
        run_tmp.replace(run_path)
    except Exception as e:
        print(f"[PIPELINE] ⚠️  runs 이력 저장 실패 (무시): {e}")





def _grep_verdict(output: str) -> str | None:
    """LLM 출력에서 VERDICT: <판정> 키워드를 추출한다."""
    m = re.search(_VERDICT_PATTERN, output, re.IGNORECASE)
    if m:
        return m.group(1).upper()
    return None


_UNSET_VERDICT = object()  # prev_verdict 초기 sentinel — None 과 구분
_CRITIQUE_AUTO_RETRY_MAX = 1  # critique 루프 탈출 후 plan 자동 재진입 최대 횟수
_TASK_AUTO_RETRY_MAX = 2      # critique 루프 탈출 후 task 자동 재실행 최대 횟수

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
        print(f"[PIPELINE] 🔄 resume: 이전 실행 결과 로드 완료")

    def save(status: str) -> None:
        result["status"] = status
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
            result["steps"]["branch"] = {"status": "completed", "name": branch}
            print(f"[PIPELINE] ✅ 브랜치 준비: {branch}")
        except RuntimeError as e:
            print(f"[PIPELINE] ❌ 브랜치 생성 실패: {e}")
            result["steps"]["branch"] = {"status": "failed_branch", "error": str(e)}
            save("failed_branch")
            return 1

    result["steps"]["preflight"] = {"status": "completed"}
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
            task_rc, task_out = _run_pipeline_step(
                root, "task", task_prompt_lite, executor, STEP_TIMEOUT * 2, dry_run=dry_run
            )
            result["steps"]["task"] = {
                "status": "completed" if task_rc == 0 else "failed",
                "output_preview": task_out[:300],
            }
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
            review_rc, review_out = _run_pipeline_step(
                root, "review", review_prompt_lite, executor, STEP_TIMEOUT, dry_run=dry_run
            )
        review_verdict = _grep_verdict(review_out)
        print(f"  VERDICT: {review_verdict or '미감지'}")
        result["steps"]["review"] = {
            "status": "completed" if review_verdict == "APPROVE" else "failed",
            "verdict": review_verdict,
        }
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
        rc, out = _run_pipeline_step(root, "plan", plan_prompt, executor, STEP_TIMEOUT, dry_run=dry_run)
        result["steps"]["plan"] = {"status": "completed" if rc == 0 else "failed", "output_preview": out[:300]}
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
        try:
            input()
        except (EOFError, KeyboardInterrupt):
            print("[PIPELINE] 중단됨")
            save("aborted")
            return 1

    # ── TASK 스텝 ────────────────────────────────────────────────────────
    task_prompt = (
        "[자동화 모드] 사용자 확인 없이 즉시 실행하세요.\n\n"
        f"{instruction}\n\n"
        "위 계획을 TDD로 구현하세요.\n"
        "1. 실패하는 테스트 작성 후 `python3 scripts/omc_pipeline_guard.py red-done <파일>` 실행\n"
        "2. 구현 후 테스트 GREEN 확인\n"
        "3. `python3 scripts/omc_tdd_check.py --staged` exit 0 확인\n"
        "반드시 마지막 줄에 `VERDICT: PROCEED` (성공) 또는 `VERDICT: BLOCK` (실패)를 출력하세요."
        + _CRITIQUE_QUALITY_HINT
    )
    print("\n[PIPELINE] ▶ TASK 스텝 실행 중...")
    if _step_already_done(_resume_data, "task"):
        print("[PIPELINE] ⏭ TASK 건너뜀 (이미 완료)")
        task_rc = 0
        task_out = "[RESUME] task skipped"
    else:
        task_rc, task_out = _run_pipeline_step(root, "task", task_prompt, executor, STEP_TIMEOUT * 2, dry_run=dry_run)
        # AMBIGUOUS_RESPONSE: verdict None 이면 1회 재시도
        if task_rc == 0 and _grep_verdict(task_out) is None:
            print("[PIPELINE] ⚠️  TASK verdict 미감지 (AMBIGUOUS) — 1회 재시도...")
            retry_prompt = task_prompt + "\n\n반드시 마지막 줄에 `VERDICT: PROCEED` 또는 `VERDICT: BLOCK`을 출력하세요."
            task_rc, task_out = _run_pipeline_step(root, "task", retry_prompt, executor, STEP_TIMEOUT * 2, dry_run=dry_run)
            if _grep_verdict(task_out) is None:
                print("[PIPELINE] ❌ TASK AMBIGUOUS_RESPONSE 2회 연속 — 중단")
                result["steps"]["task"] = {"status": "failed", "output_preview": task_out[:300]}
                save("failed_ambiguous_response")
                return 1
        result["steps"]["task"] = {"status": "completed" if task_rc == 0 else "failed", "output_preview": task_out[:300]}
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
            if time.time() > deadline:
                print(f"[PIPELINE] ❌ 최대 실행 시간 초과 ({max_time}s)")
                result["steps"][loop_step] = {"status": "timeout"}
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

            verdict = _grep_verdict(out)
            print(f"  VERDICT: {verdict or '미감지'}")

            # REVISE/HOLD 시 지적 내용 저장 → 다음 retry 프롬프트에 전달
            if verdict in ("REVISE", "HOLD"):
                _issues = _extract_critique_issues(out)
                if _issues:
                    prev_critique_issues = _issues

            if rc == 0 and verdict in verdict_ok:
                result["steps"][loop_step] = {"status": "completed", "verdict": verdict}
                break

            # T2: BLOCK 즉시 탈출 — 재시도 없이 바로 에스컬레이션
            if rc == 0 and verdict == "BLOCK":
                print(f"[PIPELINE] ❌ {loop_step.upper()} VERDICT: BLOCK — 즉시 탈출")
                critique_issues = _extract_critique_issues(out)
                result["steps"][loop_step] = {
                    "status": "failed_critique_loop",
                    "verdict": "BLOCK",
                    "streak": 1,
                    "last_output": out[:2000],
                    "critique_issues": critique_issues,
                }
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
                    result["steps"][loop_step] = {
                        "status": "failed_critique_loop",
                        "verdict": verdict,
                        "streak": same_verdict_streak + 1,
                        "last_output": out[:2000],
                        "critique_issues": critique_issues,
                    }
                    _save_pipeline_result(root, result)
                else:
                    critique_issues = (
                        result["steps"].get(loop_step, {}).get("critique_issues")
                        or prev_critique_issues
                        or ""
                    )
                if not isinstance(critique_issues, str):
                    critique_issues = str(critique_issues)

                # ── critique 루프 탈출 시 복구 에스컬레이션 ─────────────────
                # 1순위: task_retry (critique_issues 반영) → critique 재진입
                # 2순위: plan_retry → critique 재진입
                # 소진 시: hold
                if loop_step == "critique" and task_auto_retry_count < _TASK_AUTO_RETRY_MAX:
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
                    task_retry_rc, task_retry_out = _run_pipeline_step(
                        root, "task_retry", task_retry_prompt, executor, STEP_TIMEOUT * 2, dry_run=dry_run
                    )
                    result["steps"]["task_retry"] = {
                        "status": "completed" if task_retry_rc == 0 else "failed",
                        "output_preview": task_retry_out[:300],
                    }
                    result["last_completed_step"] = "task_retry"
                    _save_pipeline_result(root, result)
                    if task_retry_rc != 0:
                        print("[PIPELINE] ❌ TASK 재실행 실패 — HOLD")
                        save("hold")
                        return 2
                    if _grep_verdict(task_retry_out) == "BLOCK":
                        print("[PIPELINE] ❌ TASK 재실행 VERDICT: BLOCK — HOLD")
                        result["steps"]["task_retry"]["verdict"] = "BLOCK"
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
                if loop_step == "critique" and critique_auto_retry_count < _CRITIQUE_AUTO_RETRY_MAX:
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
                    plan_rc, plan_out = _run_pipeline_step(
                        root, "plan_retry", retry_plan_prompt, executor, STEP_TIMEOUT, dry_run=dry_run
                    )
                    result["steps"]["plan_retry"] = {
                        "status": "completed" if plan_rc == 0 else "failed",
                        "output_preview": plan_out[:300],
                    }
                    result["last_completed_step"] = "plan_retry"
                    _save_pipeline_result(root, result)
                    if plan_rc != 0:
                        print("[PIPELINE] ❌ PLAN 재실행 실패 — HOLD")
                        save("hold")
                        return 2
                    # plan_retry VERDICT=HOLD 이면 critique 재진입 없이 즉시 탈출
                    if _grep_verdict(plan_out) == "HOLD":
                        print("[PIPELINE] ❌ PLAN 재실행 VERDICT: HOLD — 재설계 필요")
                        result["steps"]["plan_retry"]["verdict"] = "HOLD"
                        _save_pipeline_result(root, result)
                        save("hold")
                        return 2
                    # critique 루프 재진입: 카운터·스트릭 초기화
                    retry_count = 0
                    prev_verdict = _UNSET_VERDICT  # 재진입 후 첫 None 오분류 방지
                    same_verdict_streak = 0
                    task_auto_retry_count = 0  # plan_retry 후 task retry 기회 복원
                    needs_ctx_refresh = True  # plan_retry 후 새 diff 반영
                    continue

                save("hold")
                return 2

            retry_count += 1
            if retry_count > _PIPELINE_MAX_RETRIES:
                print(f"[PIPELINE] ❌ {loop_step.upper()} retry 소진 ({_PIPELINE_MAX_RETRIES}회)")
                critique_issues = prev_critique_issues or _extract_critique_issues(out)
                if not critique_issues:
                    critique_issues = f"{loop_step} retry exhausted without parseable critique issues"
                result["steps"][loop_step] = {
                    "status": "retry_exhausted",
                    "verdict": verdict,
                    "last_output": out[:300],
                    "critique_issues": critique_issues[:2000],
                }
                _save_pipeline_result(root, result)

                # retry_exhausted → task_retry 복구 시도 (critique 스텝에서만)
                if loop_step == "critique" and task_auto_retry_count < _TASK_AUTO_RETRY_MAX:
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
                    task_retry_rc, task_retry_out = _run_pipeline_step(
                        root, "task_retry", task_retry_prompt, executor, STEP_TIMEOUT * 2, dry_run=dry_run
                    )
                    result["steps"]["task_retry"] = {
                        "status": "completed" if task_retry_rc == 0 else "failed",
                        "output_preview": task_retry_out[:300],
                    }
                    _save_pipeline_result(root, result)
                    if task_retry_rc == 0 and _grep_verdict(task_retry_out) not in ("BLOCK", None):
                        # critique 루프 재진입
                        retry_count = 0
                        prev_verdict = _UNSET_VERDICT
                        same_verdict_streak = 0
                        prev_critique_issues = ""
                        needs_ctx_refresh = True  # task_retry 후 새 diff 반영
                        continue
                    # task_retry 실패/BLOCK → retry_exhausted 유지
                    print("[PIPELINE] ❌ TASK 재실행 실패 또는 BLOCK — retry_exhausted")

                save("retry_exhausted")
                return 1

            print(f"  재시도 중...")

        _save_pipeline_result(root, result)

    # ── PR 생성 ──────────────────────────────────────────────────────────
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
            result["steps"]["pr"] = {"status": "failed", "reason": "push_failed"}
            save("failed")
            return 1
    else:
        pr_url = "[DRY-RUN] PR URL"
        print(f"[PIPELINE] [DRY-RUN] PR 생성 시뮬레이션")

    result["pr_url"] = pr_url
    result["steps"]["pr"] = {"status": "completed" if pr_url else "failed"}
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
