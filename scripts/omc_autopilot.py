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
import subprocess
import sys
import re
import textwrap
import time
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import omc_utils

_TASKS_DIR = ".omc/tasks"
_AUTOPILOT_STATE_DIR = ".omc/state/autopilot"
_DEFAULT_TIMEOUT_SEC = 120
_DEFAULT_MAX_RETRIES = 1


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
    _state_path(root, task_id).write_text(
        json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8"
    )


def _detect_executor(preferred: str) -> str:
    import shutil
    if preferred and preferred != "auto":
        return preferred
    env_choice = os.environ.get("OMC_EXECUTOR", "").strip().lower()
    if env_choice in {"codex", "gemini", "claude"}:
        return env_choice
    for exe in ("codex", "gemini", "claude"):
        if shutil.which(exe):
            return exe
    return "codex"


def _resolve_order(steps: list[dict]) -> list[dict]:
    """의존성(depends_on)을 고려한 토폴로지 정렬."""
    id_map = {s["id"]: s for s in steps}
    visited: set[str] = set()
    order: list[dict] = []

    def visit(step_id: str) -> None:
        if step_id in visited:
            return
        for dep in id_map.get(step_id, {}).get("depends_on", []):
            visit(dep)
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

        try:
            proc = subprocess.run(
                cmd,
                shell=True,
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


def _build_retry_prompt(original_prompt: str, attempt: int, failures: list[dict]) -> str:
    """이전 시도 실패 컨텍스트를 프롬프트 앞에 주입합니다."""
    if not failures:
        return original_prompt

    lines = [
        f"[이전 시도 {attempt}회 실패 — 아래 문제를 반드시 해결하세요]",
        "",
    ]
    for f in failures:
        lines.append(f"- {f['label']}: FAIL")
        if f.get("output"):
            # 첫 5줄만 발췌
            snippet = "\n".join(f["output"].splitlines()[:5])
            lines.append(f"  출력: {snippet}")
    lines += ["", "위 문제를 해결하면서 아래 작업을 수행하세요:", "", original_prompt]
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
) -> tuple[int, str]:
    """omc_exec.py를 통해 스텝 프롬프트를 실행합니다.

    Returns:
        (returncode, output_text)
    """
    exec_script = Path(__file__).resolve().parent / "omc_exec.py"
    if not exec_script.exists():
        return 1, f"[ERROR] omc_exec.py 없음: {exec_script}"

    prompt = (prompt_override or step.get("prompt", "")).strip()
    if not prompt:
        return 1, "[ERROR] 스텝에 prompt가 없습니다."

    cmd = [
        sys.executable,
        str(exec_script),
        "--target", str(root),
        "--executor", executor,
        "--headless",
        "--timeout", str(timeout_sec),
        "--prompt", prompt,
    ]

    try:
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


# ---------------------------------------------------------------------------
# 커맨드: run
# ---------------------------------------------------------------------------

def cmd_run(root: Path, task_file: Path, *, dry_run: bool = False) -> int:
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

    executor = _detect_executor(executor_pref)
    steps = _resolve_order(steps_raw)

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

        # 이미 실패한 스텝은 dry-run에서도 재실행하지 않고 실패 유지
        if state["steps"].get(sid, {}).get("status") == "failed":
            print(f"  [SKIP] {sid}: {step_title} (이전 실패 — 재실행 없음)")
            failed_count += 1
            continue

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
                rc, output = _run_step(root, step_with_prompt, executor=executor, timeout_sec=timeout_sec)

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
# 커맨드: status
# ---------------------------------------------------------------------------

def cmd_status(root: Path, task_id: str | None = None) -> int:
    state_dir = root / _AUTOPILOT_STATE_DIR
    if not state_dir.exists():
        print("[AUTOPILOT] 실행 기록 없음")
        return 0

    files = sorted(state_dir.glob("*.json"), reverse=True)
    if not files:
        print("[AUTOPILOT] 실행 기록 없음")
        return 0

    if task_id:
        files = [f for f in files if f.stem == task_id]
        if not files:
            print(f"[AUTOPILOT] '{task_id}' 기록 없음")
            return 1

    for f in files:
        try:
            s = json.loads(f.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            continue
        status_icon = {
            "completed": "✅", "failed": "❌", "running": "⏳", "pending": "⏸"
        }.get(s.get("status", ""), "❓")
        print(f"\n{status_icon} [{s.get('status', '?')}] {s.get('title', f.stem)}")
        print(f"   id={s.get('task_id', f.stem)}  executor={s.get('executor', '?')}")
        print(f"   시작: {s.get('started_at', '-')}  완료: {s.get('finished_at', '-')}")
        for sid, ss in s.get("steps", {}).items():
            icon = {
                "completed": "✅", "failed": "❌", "blocked": "🔒", "running": "⏳"
            }.get(ss.get("status", ""), "❓")
            print(f"   {icon} {sid}: {ss.get('status', '?')} (시도 {ss.get('attempt', '-')})")
    return 0



# ---------------------------------------------------------------------------
# 커맨드: pipeline
# ---------------------------------------------------------------------------

_PIPELINE_RESULT_PATH = ".omc/pipeline_run_result.json"
_PIPELINE_MAX_RETRIES = 3
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
    for prefix in _LITE_BRANCH_PREFIXES:
        if branch.startswith(prefix):
            return "lite"
    if len(instruction) <= _LITE_INSTRUCTION_MAX_LEN:
        return "lite"
    return "full"



def _save_pipeline_result(root: Path, data: dict) -> None:
    out = root / _PIPELINE_RESULT_PATH
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def _grep_verdict(output: str) -> str | None:
    """LLM 출력에서 VERDICT: <판정> 키워드를 추출한다."""
    m = re.search(_VERDICT_PATTERN, output, re.IGNORECASE)
    if m:
        return m.group(1).upper()
    return None


def _run_pipeline_step(
    root: Path,
    step_name: str,
    prompt: str,
    executor: str,
    timeout_sec: int,
    *,
    dry_run: bool = False,
) -> tuple[int, str]:
    """단일 파이프라인 스텝을 LLM으로 실행하고 (returncode, output)을 반환한다."""
    if dry_run:
        print(f"  [DRY-RUN] {step_name} 시뮬레이션")
        return 0, f"[DRY-RUN] {step_name} — VERDICT: PROCEED"

    exec_script = Path(__file__).resolve().parent / "omc_exec.py"
    if not exec_script.exists():
        return 1, f"[ERROR] omc_exec.py 없음"

    cmd = [
        sys.executable, str(exec_script),
        "--target", str(root),
        "--executor", executor,
        "--headless",
        "--timeout", str(timeout_sec),
        "--prompt", prompt,
    ]
    try:
        proc = subprocess.run(
            cmd, cwd=str(root), capture_output=True, text=True,
            timeout=timeout_sec + 30,
        )
        output = (proc.stdout or "") + (proc.stderr or "")
        return int(proc.returncode), output.strip()
    except subprocess.TimeoutExpired:
        return 1, "[ERROR] 타임아웃"
    except Exception as exc:
        return 1, f"[ERROR] {exc}"


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
        "started_at": started_at,
        "steps": {},
        "pr_url": None,
        "finished_at": None,
    }

    def save(status: str) -> None:
        result["status"] = status
        result["finished_at"] = _now()
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
    if not dry_run:
        git_status = subprocess.run(
            ["git", "status", "--porcelain"],
            capture_output=True, text=True, cwd=str(root),
        )
        if git_status.stdout.strip():
            print("[PIPELINE] ❌ uncommitted 변경 있음 — abort")
            print(f"  {git_status.stdout.strip()[:200]}")
            result["steps"]["preflight"] = {"status": "failed", "reason": "uncommitted changes"}
            save("failed")
            return 1
        print("[PIPELINE] ✅ git 상태 clean")

        # 브랜치 생성
        br = subprocess.run(
            ["git", "checkout", "-b", branch],
            capture_output=True, text=True, cwd=str(root),
        )
        if br.returncode != 0:
            print(f"[PIPELINE] ❌ 브랜치 생성 실패: {br.stderr.strip()[:150]}")
            result["steps"]["branch"] = {"status": "failed", "reason": br.stderr.strip()}
            save("failed")
            return 1
        print(f"[PIPELINE] ✅ 브랜치 생성: {branch}")

    result["steps"]["preflight"] = {"status": "completed"}
    _save_pipeline_result(root, result)

    # ── pipeline_guard: contract-done + session-start ────────────────────
    if not dry_run:
        guard = Path(__file__).resolve().parent / "omc_pipeline_guard.py"
        subprocess.run([sys.executable, str(guard), "session-start"], cwd=str(root))
        subprocess.run([sys.executable, str(guard), "contract-done",
                        "--content", f"pipeline: {instruction[:100]}"], cwd=str(root))

    STEP_TIMEOUT = 600  # 스텝별 기본 타임아웃 (초)

    # ── LITE 모드: plan/critique 스킵 ──────────────────────────────────
    if mode == "lite":
        print("\n[PIPELINE] ⚡ LITE 모드 — plan/critique 스킵")
        result["steps"]["preflight"]["mode"] = "lite"
        _save_pipeline_result(root, result)
        # TASK 스텝으로 바로 진입
        task_prompt_lite = (
            f"{instruction}\n\n"
            "TDD로 구현하세요.\n"
            "1. 먼저 `python3 scripts/omc_pipeline_guard.py contract-done` 실행\n"
            "2. 실패하는 테스트 작성 후 `python3 scripts/omc_pipeline_guard.py red-done <파일>` 실행\n"
            "3. 구현 후 테스트 GREEN 확인\n"
            "반드시 마지막 줄에 `VERDICT: PROCEED` 또는 `VERDICT: BLOCK`을 출력하세요."
        )
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
        print("\n[PIPELINE] ▶ REVIEW 스텝 (LITE, retry 없음)...")
        review_rc, review_out = _run_pipeline_step(
            root, "review", review_prompt_lite, executor, STEP_TIMEOUT, dry_run=dry_run
        )
        review_verdict = _grep_verdict(review_out)
        print(f"  VERDICT: {review_verdict or '미감지'}")
        result["steps"]["review"] = {
            "status": "completed" if review_verdict in ("APPROVE", "PROCEED") else "failed",
            "verdict": review_verdict,
        }
        if review_verdict not in ("APPROVE", "PROCEED"):
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
    STEP_TIMEOUT = 600  # 이하 기존 코드

    # ── PLAN 스텝 ────────────────────────────────────────────────────────
    plan_prompt = (
        f"다음 지시문에 대한 구현 계획을 작성하세요.\n\n{instruction}\n\n"
        "omc-task 스킬의 CONTRACT + DESIGN 단계를 채우세요.\n"
        "반드시 마지막 줄에 `VERDICT: PROCEED` 또는 `VERDICT: HOLD`를 출력하세요."
    )
    print("\n[PIPELINE] ▶ PLAN 스텝 실행 중...")
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
        f"{instruction}\n\n"
        "위 계획을 TDD로 구현하세요.\n"
        "1. 먼저 `python3 scripts/omc_pipeline_guard.py contract-done` 실행\n"
        "2. 실패하는 테스트 작성 후 `python3 scripts/omc_pipeline_guard.py red-done <파일>` 실행\n"
        "3. 구현 후 테스트 GREEN 확인\n"
        "4. `python3 scripts/omc_tdd_check.py --staged` exit 0 확인\n"
        "반드시 마지막 줄에 `VERDICT: PROCEED` (성공) 또는 `VERDICT: BLOCK` (실패)를 출력하세요."
    )
    print("\n[PIPELINE] ▶ TASK 스텝 실행 중...")
    task_rc, task_out = _run_pipeline_step(root, "task", task_prompt, executor, STEP_TIMEOUT * 2, dry_run=dry_run)
    result["steps"]["task"] = {"status": "completed" if task_rc == 0 else "failed", "output_preview": task_out[:300]}
    _save_pipeline_result(root, result)

    if task_rc != 0:
        print("[PIPELINE] ❌ TASK 실패")
        save("failed")
        return 1

    # ── CRITIQUE/REVIEW 루프 ────────────────────────────────────────────
    for loop_step in ("critique", "review"):
        verdict_ok = ("PROCEED", "APPROVE")
        retry_count = 0
        # critique PROCEED 통과 후 review 진입

        while retry_count <= _PIPELINE_MAX_RETRIES:
            if time.time() > deadline:
                print(f"[PIPELINE] ❌ 최대 실행 시간 초과 ({max_time}s)")
                result["steps"][loop_step] = {"status": "timeout"}
                save("timeout")
                return 1

            loop_prompt = (
                f"다음 코드 변경에 대해 {'critique(pre-mortem)' if loop_step == 'critique' else 'review'}를 수행하세요.\n"
                f"지시문: {instruction[:200]}\n\n"
                f"{'omc-critique 스킬' if loop_step == 'critique' else 'omc-review 스킬'}의 포맷을 따르세요.\n"
                "반드시 마지막 줄에 `VERDICT: PROCEED` / `VERDICT: APPROVE` / "
                "`VERDICT: REVISE` / `VERDICT: BLOCK` / `VERDICT: HOLD` 중 하나를 출력하세요."
            )
            print(f"\n[PIPELINE] ▶ {loop_step.upper()} 스텝 (시도 {retry_count + 1}/{_PIPELINE_MAX_RETRIES + 1})...")
            rc, out = _run_pipeline_step(root, loop_step, loop_prompt, executor, STEP_TIMEOUT, dry_run=dry_run)

            verdict = _grep_verdict(out)
            print(f"  VERDICT: {verdict or '미감지'}")

            if rc == 0 and verdict in verdict_ok:
                result["steps"][loop_step] = {"status": "completed", "verdict": verdict}
                break

            retry_count += 1
            if retry_count > _PIPELINE_MAX_RETRIES:
                print(f"[PIPELINE] ❌ {loop_step.upper()} retry 소진 ({_PIPELINE_MAX_RETRIES}회)")
                result["steps"][loop_step] = {
                    "status": "retry_exhausted",
                    "verdict": verdict,
                    "last_output": out[:300],
                }
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
    ap = argparse.ArgumentParser(description="OMC 멀티 LLM 자율 루프 (옵트인)")
    ap.add_argument("--target", type=Path, default=Path.cwd())
    sub = ap.add_subparsers(dest="cmd", required=True)

    p_run = sub.add_parser("run", help="태스크 파일 실행")
    p_run.add_argument("--task", type=Path, required=True, help="태스크 JSON 파일 경로")
    p_run.add_argument("--dry-run", action="store_true", help="실제 LLM 호출 없이 계획만 출력")

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

    args = ap.parse_args()
    root = omc_utils.project_root(args.target)

    if args.cmd == "run":
        task_file = args.task if args.task.is_absolute() else (root / args.task)
        return cmd_run(root, task_file, dry_run=args.dry_run)
    if args.cmd == "new":
        return cmd_new(root, args.task_id, args.title)
    if args.cmd == "status":
        return cmd_status(root, args.task_id)
    if args.cmd == "pipeline":
        return cmd_pipeline(
            root,
            instruction=args.instruction,
            branch=args.branch,
            executor_pref=args.executor,
            max_time=args.max_time,
            dry_run=args.dry_run,
            auto=args.auto,
            mode_arg=args.mode,
        )
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
