"""
critique 루프 자동 복구 관련 테스트.

T1: _extract_critique_issues — VERDICT 앞 30줄 추출
T2: failed_critique_loop 시 critique_issues 저장
T3: plan 자동 재진입 성공 (REVISE→PROCEED)
T4: plan 자동 재진입 실패 시 exit 2 + status=hold
T5: --resume 시 critique_issues 필드 없어도 KeyError 없음
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

sys.path.insert(0, str(Path(__file__).parent))
import omc_autopilot as _aut


def test_extract_issues_returns_lines_before_verdict() -> None:
    """VERDICT: REVISE 앞 텍스트가 반환돼야 한다."""
    body = "\n".join(f"line {i}" for i in range(1, 50))
    text = body + "\nVERDICT: REVISE\n추가 설명"
    result = _aut._extract_critique_issues(text)
    assert "VERDICT" not in result
    assert "line 49" in result or "line 1" in result


def test_extract_issues_no_verdict_returns_full_text() -> None:
    text = "some output without verdict keyword"
    assert _aut._extract_critique_issues(text) == text


def test_extract_issues_empty_input_returns_empty() -> None:
    assert _aut._extract_critique_issues("") == ""


@pytest.fixture()
def tmp_repo(tmp_path):
    import subprocess, os
    env = {**os.environ, "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t",
           "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@t"}
    subprocess.run(["git", "init"], cwd=tmp_path, check=True, capture_output=True)
    subprocess.run(["git", "commit", "--allow-empty", "-m", "init"],
                   cwd=tmp_path, check=True, capture_output=True, env=env)
    return tmp_path


def test_failed_critique_loop_saves_critique_issues(tmp_repo, monkeypatch):
    monkeypatch.setenv("OmC_PIPELINE_RESULT_PATH", str(tmp_repo / "result.json"))
    # REVISE×3 탈출 → plan_retry HOLD → hold 종료여도 critique_issues 저장돼야 함
    verdicts = iter(["PROCEED", "PROCEED", "REVISE", "REVISE", "REVISE", "HOLD"])
    def _mock(root, step, prompt, executor, timeout, *, dry_run=False, isolated=False):
        v = next(verdicts, "HOLD")
        return (0, f"detail\nVERDICT: {v}")
    with patch.object(_aut, "_run_pipeline_step", side_effect=_mock), \
         patch.object(_aut, "_checkout_new_branch", return_value="feat/test"), \
         patch.object(_aut, "_detect_executor", return_value="codex"), \
         patch("subprocess.run"):
        _aut.cmd_pipeline(root=tmp_repo, instruction="test instruction that is long enough",
                          branch="feat/test", executor_pref="codex", max_time=60,
                          dry_run=False, auto=True, mode_arg="full", allow_dirty=True)
    data = json.loads((tmp_repo / "result.json").read_text())
    critique_step = data.get("steps", {}).get("critique", {})
    assert "critique_issues" in critique_step, f"실제: {critique_step}"
    assert isinstance(critique_step["critique_issues"], str)


def test_critique_auto_retry_succeeds_on_second_attempt(tmp_repo, monkeypatch):
    monkeypatch.setenv("OmC_PIPELINE_RESULT_PATH", str(tmp_repo / "result.json"))
    # FULL 순서: plan, task, critique(REVISE×3 탈출), plan_retry, critique_retry(PROCEED), review(APPROVE)
    verdicts = iter(["PROCEED", "PROCEED", "REVISE", "REVISE", "REVISE", "PROCEED", "PROCEED", "APPROVE"])
    def _mock_step(root, step, prompt, executor, timeout, *, dry_run=False, isolated=False):
        v = next(verdicts, "PROCEED")
        return (0, f"output\nVERDICT: {v}")
    import subprocess as _sp
    def _mock_subprocess(cmd, **kw):
        # git push, gh pr 모두 성공으로 mock
        result = _sp.CompletedProcess(cmd, 0, stdout="https://github.com/test/pr/1", stderr="")
        return result
    with patch.object(_aut, "_run_pipeline_step", side_effect=_mock_step), \
         patch.object(_aut, "_checkout_new_branch", return_value="feat/test"), \
         patch.object(_aut, "_detect_executor", return_value="codex"), \
         patch("subprocess.run", side_effect=_mock_subprocess):
        rc = _aut.cmd_pipeline(root=tmp_repo, instruction="test instruction that is long enough",
                               branch="feat/test", executor_pref="codex", max_time=60,
                               dry_run=False, auto=True, mode_arg="full", allow_dirty=True)
    data = json.loads((tmp_repo / "result.json").read_text())
    assert rc == 0, f"rc={rc}"
    assert data["status"] == "completed", f"status={data['status']}"


def test_critique_auto_retry_fails_then_hold(tmp_repo, monkeypatch):
    monkeypatch.setenv("OmC_PIPELINE_RESULT_PATH", str(tmp_repo / "result.json"))
    # plan, task, critique(REVISE×3 탈출), plan_retry, critique_retry(REVISE×3 탈출) → hold
    verdicts = iter(["PROCEED", "PROCEED", "REVISE", "REVISE", "REVISE", "PROCEED", "REVISE", "REVISE", "REVISE"])
    def _mock(root, step, prompt, executor, timeout, *, dry_run=False, isolated=False):
        v = next(verdicts, "REVISE")
        return (0, f"output\nVERDICT: {v}")
    with patch.object(_aut, "_run_pipeline_step", side_effect=_mock), \
         patch.object(_aut, "_checkout_new_branch", return_value="feat/test"), \
         patch.object(_aut, "_detect_executor", return_value="codex"), \
         patch("subprocess.run"):
        rc = _aut.cmd_pipeline(root=tmp_repo, instruction="test instruction that is long enough",
                               branch="feat/test", executor_pref="codex", max_time=60,
                               dry_run=False, auto=True, mode_arg="full", allow_dirty=True)
    data = json.loads((tmp_repo / "result.json").read_text())
    assert rc == 2, f"rc={rc}"
    assert data["status"] == "hold", f"status={data['status']}"


def test_resume_without_critique_issues_no_keyerror(tmp_repo, monkeypatch):
    monkeypatch.setenv("OmC_PIPELINE_RESULT_PATH", str(tmp_repo / "result.json"))
    old = {"status": "failed_critique_loop", "mode": "full", "branch": "feat/old",
           "instruction": "test", "executor": "codex", "started_at": "2026-01-01T000000Z",
           "steps": {"preflight": {"status": "completed"},
                     "plan": {"status": "completed", "output_preview": "..."},
                     "critique": {"status": "failed_critique_loop", "verdict": "REVISE",
                                  "streak": 2, "last_output": "old output"}},
           "pr_url": None, "finished_at": "2026-01-01T000100Z"}
    (tmp_repo / "result.json").write_text(json.dumps(old))
    verdicts = iter(["PROCEED", "PROCEED", "APPROVE"])
    def _mock(root, step, prompt, executor, timeout, *, dry_run=False, isolated=False):
        v = next(verdicts, "PROCEED")
        return (0, f"output\nVERDICT: {v}")
    with patch.object(_aut, "_run_pipeline_step", side_effect=_mock), \
         patch.object(_aut, "_checkout_new_branch", return_value="feat/old"), \
         patch.object(_aut, "_detect_executor", return_value="codex"), \
         patch("subprocess.run"):
        try:
            _aut.cmd_pipeline(root=tmp_repo, instruction="test instruction that is long enough",
                              branch="feat/old", executor_pref="codex", max_time=60,
                              dry_run=False, auto=True, mode_arg="full", resume=True,
                              allow_dirty=True)
        except KeyError as e:
            raise AssertionError(f"KeyError: {e}")

def test_plan_retry_hold_verdict_exits_hold(tmp_repo, monkeypatch):
    """plan_retry VERDICT=HOLD 이면 critique 재진입 없이 즉시 hold + exit 2 해야 한다."""
    monkeypatch.setenv("OmC_PIPELINE_RESULT_PATH", str(tmp_repo / "result.json"))
    # plan, task, critique(REVISE×3 탈출), plan_retry(rc=0 but HOLD) → 즉시 hold
    # plan_retry 후 추가 _run_pipeline_step 호출이 없어야 함
    step_calls: list[str] = []
    verdicts = iter(["PROCEED", "PROCEED", "REVISE", "REVISE", "REVISE", "HOLD"])
    def _mock(root, step, prompt, executor, timeout, *, dry_run=False, isolated=False):
        step_calls.append(step)
        v = next(verdicts, "HOLD")
        return (0, f"output\nVERDICT: {v}")
    with patch.object(_aut, "_run_pipeline_step", side_effect=_mock), \
         patch.object(_aut, "_checkout_new_branch", return_value="feat/test"), \
         patch.object(_aut, "_detect_executor", return_value="codex"), \
         patch("subprocess.run"):
        rc = _aut.cmd_pipeline(root=tmp_repo, instruction="test instruction that is long enough",
                               branch="feat/test", executor_pref="codex", max_time=60,
                               dry_run=False, auto=True, mode_arg="full", allow_dirty=True)
    data = json.loads((tmp_repo / "result.json").read_text())
    assert rc == 2, f"HOLD exit code 여야 함 (rc={rc})"
    assert data["status"] == "hold", f"status={data['status']}"
    # plan_retry 이후 critique 가 추가 호출되면 안 됨
    # 예상 순서: plan, task, critique(×3), plan_retry
    assert step_calls[-1] == "plan_retry", \
        f"plan_retry 가 마지막 호출이어야 함, 실제: {step_calls}"

# ─────────────────────────────────────────────
# T1 (B): task 프롬프트에 critique 품질 힌트 포함
# ─────────────────────────────────────────────

def test_task_prompt_contains_critique_quality_hint(tmp_repo, monkeypatch):
    """task 에 전달되는 프롬프트에 _CRITIQUE_QUALITY_HINT 텍스트가 포함돼야 한다."""
    monkeypatch.setenv("OmC_PIPELINE_RESULT_PATH", str(tmp_repo / "result.json"))
    captured_prompts: dict[str, str] = {}
    verdicts = iter(["PROCEED", "PROCEED", "PROCEED", "APPROVE"])

    def _mock(root, step, prompt, executor, timeout, *, dry_run=False, isolated=False):
        captured_prompts[step] = prompt
        v = next(verdicts, "PROCEED")
        return (0, f"output\nVERDICT: {v}")

    import subprocess as _sp
    def _mock_sp(cmd, **kw):
        return _sp.CompletedProcess(cmd, 0, stdout="https://github.com/pr/1", stderr="")

    with patch.object(_aut, "_run_pipeline_step", side_effect=_mock), \
         patch.object(_aut, "_checkout_new_branch", return_value="feat/test"), \
         patch.object(_aut, "_detect_executor", return_value="codex"), \
         patch("subprocess.run", side_effect=_mock_sp):
        _aut.cmd_pipeline(root=tmp_repo, instruction="test instruction that is long enough",
                          branch="feat/test", executor_pref="codex", max_time=60,
                          dry_run=False, auto=True, mode_arg="full", allow_dirty=True)

    assert "task" in captured_prompts, "task 스텝이 호출되지 않음"
    assert _aut._CRITIQUE_QUALITY_HINT in captured_prompts["task"], \
        f"task 프롬프트에 _CRITIQUE_QUALITY_HINT 없음\n실제: {captured_prompts['task'][:200]}"


# ─────────────────────────────────────────────
# T2 (A): critique REVISE × 3 탈출 시 plan_retry 경로를 소비
# ─────────────────────────────────────────────

def test_critique_revise_triggers_plan_retry_then_proceeds(tmp_repo, monkeypatch):
    """critique REVISE×3 탈출 → plan_retry → critique PROCEED → completed."""
    monkeypatch.setenv("OmC_PIPELINE_RESULT_PATH", str(tmp_repo / "result.json"))
    # plan, task, critique(REVISE×3 탈출), plan_retry, critique_retry(PROCEED), review(APPROVE)
    verdicts = iter(["PROCEED", "PROCEED", "REVISE", "REVISE", "REVISE",
                     "PROCEED", "PROCEED", "APPROVE"])

    def _mock_step(root, step, prompt, executor, timeout, *, dry_run=False, isolated=False):
        v = next(verdicts, "PROCEED")
        return (0, f"output\nVERDICT: {v}")

    import subprocess as _sp
    def _mock_sp(cmd, **kw):
        return _sp.CompletedProcess(cmd, 0, stdout="https://github.com/pr/1", stderr="")

    with patch.object(_aut, "_run_pipeline_step", side_effect=_mock_step), \
         patch.object(_aut, "_checkout_new_branch", return_value="feat/test"), \
         patch.object(_aut, "_detect_executor", return_value="codex"), \
         patch("subprocess.run", side_effect=_mock_sp):
        rc = _aut.cmd_pipeline(root=tmp_repo, instruction="test instruction that is long enough",
                               branch="feat/test", executor_pref="codex", max_time=60,
                               dry_run=False, auto=True, mode_arg="full", allow_dirty=True)

    data = json.loads((tmp_repo / "result.json").read_text())
    assert rc == 0, f"rc={rc}"
    assert data["status"] == "completed", f"status={data['status']}"
    assert "plan_retry" in data.get("steps", {}), "plan_retry 스텝이 result.json 에 없음"


# ─────────────────────────────────────────────
# T3: task_retry 후에도 REVISE → plan_retry → hold 에스컬레이션
# ─────────────────────────────────────────────

def test_task_retry_still_revise_falls_back_to_plan_retry_then_hold(tmp_repo, monkeypatch):
    """task_retry 후에도 REVISE×3 → plan_retry → REVISE×3 → hold exit 2."""
    monkeypatch.setenv("OmC_PIPELINE_RESULT_PATH", str(tmp_repo / "result.json"))
    # plan, task, critique(REVISE×3 탈출)
    # → task_retry, critique(REVISE×3 탈출)
    # → plan_retry(PROCEED), critique(REVISE×3 탈출) → hold
    verdicts = iter([
        "PROCEED", "PROCEED",          # plan, task
        "REVISE", "REVISE", "REVISE",  # critique 1차 탈출
        "PROCEED",                     # task_retry
        "REVISE", "REVISE", "REVISE",  # critique 2차 탈출 → plan_retry
        "PROCEED",                     # plan_retry
        "REVISE", "REVISE", "REVISE",  # critique 3차 탈출 → hold
    ])

    def _mock(root, step, prompt, executor, timeout, *, dry_run=False, isolated=False):
        v = next(verdicts, "REVISE")
        return (0, f"output\nVERDICT: {v}")

    with patch.object(_aut, "_run_pipeline_step", side_effect=_mock), \
         patch.object(_aut, "_checkout_new_branch", return_value="feat/test"), \
         patch.object(_aut, "_detect_executor", return_value="codex"), \
         patch("subprocess.run"):
        rc = _aut.cmd_pipeline(root=tmp_repo, instruction="test instruction that is long enough",
                               branch="feat/test", executor_pref="codex", max_time=60,
                               dry_run=False, auto=True, mode_arg="full", allow_dirty=True)

    data = json.loads((tmp_repo / "result.json").read_text())
    assert rc == 2, f"rc={rc}"
    assert data["status"] == "hold", f"status={data['status']}"

# ─────────────────────────────────────────────
# REVIEW-FIX R1: task_retry rc=0 + VERDICT:BLOCK → hold exit 2
# ─────────────────────────────────────────────

def test_task_retry_block_verdict_exits_hold(tmp_repo, monkeypatch):
    """task_retry 가 rc=0 이면서 VERDICT:BLOCK 을 반환하면 즉시 hold exit 2."""
    monkeypatch.setenv("OmC_PIPELINE_RESULT_PATH", str(tmp_repo / "result.json"))
    # plan(PROCEED), task(PROCEED), critique(REVISE×3 탈출), task_retry(BLOCK)
    verdicts = iter(["PROCEED", "PROCEED", "REVISE", "REVISE", "REVISE", "BLOCK"])

    def _mock(root, step, prompt, executor, timeout, *, dry_run=False, isolated=False):
        v = next(verdicts, "BLOCK")
        return (0, f"output\nVERDICT: {v}")

    with patch.object(_aut, "_run_pipeline_step", side_effect=_mock), \
         patch.object(_aut, "_checkout_new_branch", return_value="feat/test"), \
         patch.object(_aut, "_detect_executor", return_value="codex"), \
         patch("subprocess.run"):
        rc = _aut.cmd_pipeline(root=tmp_repo,
                               instruction="test instruction that is long enough",
                               branch="feat/test", executor_pref="codex", max_time=60,
                               dry_run=False, auto=True, mode_arg="full", allow_dirty=True)

    data = json.loads((tmp_repo / "result.json").read_text())
    assert rc == 2, f"rc={rc} (expected 2 for hold)"
    assert data["status"] == "hold", f"status={data['status']}"


# ─────────────────────────────────────────────
# REVIEW-FIX R2: None-streak 기반 task_retry rc≠0 → hold exit 2 (not exit 1)
# ─────────────────────────────────────────────

def test_task_retry_rc_nonzero_exits_hold_with_code_2(tmp_repo, monkeypatch):
    """task_retry 가 rc≠0(프로세스 실패)이면 status=hold, exit code=2."""
    monkeypatch.setenv("OmC_PIPELINE_RESULT_PATH", str(tmp_repo / "result.json"))
    critique_verdicts = iter([None, None, None])

    def _mock(root, step, prompt, executor, timeout, *, dry_run=False, isolated=False):
        if step == "task_retry":
            return (1, "fatal error")
        if step == "critique":
            v = next(critique_verdicts, None)
            return (0, "output" if v is None else f"output\nVERDICT: {v}")
        return (0, "output\nVERDICT: PROCEED")

    with patch.object(_aut, "_run_pipeline_step", side_effect=_mock), \
         patch.object(_aut, "_checkout_new_branch", return_value="feat/test"), \
         patch.object(_aut, "_detect_executor", return_value="codex"), \
         patch("subprocess.run"):
        rc = _aut.cmd_pipeline(root=tmp_repo,
                               instruction="test instruction that is long enough",
                               branch="feat/test", executor_pref="codex", max_time=60,
                               dry_run=False, auto=True, mode_arg="full", allow_dirty=True)

    data = json.loads((tmp_repo / "result.json").read_text())
    assert rc == 2, f"rc={rc} (expected 2 for hold)"
    assert data["status"] == "hold", f"status={data['status']}"

# ─────────────────────────────────────────────
# T1: critique verdict=None(rc=0) × 3 → streak 탈출 → task_retry → completed
# ─────────────────────────────────────────────

def test_critique_none_verdict_streak_triggers_task_retry(tmp_repo, monkeypatch):
    """critique rc=0 + verdict=None × 3 연속 → task_retry 실행 → completed."""
    monkeypatch.setenv("OmC_PIPELINE_RESULT_PATH", str(tmp_repo / "result.json"))
    # plan(PROCEED), task(PROCEED),
    # critique(None×3 탈출), task_retry(PROCEED), critique_retry(PROCEED), review(APPROVE)
    step_verdicts = {
        "plan":       iter(["PROCEED"]),
        "task":       iter(["PROCEED"]),
        "critique":   iter([None, None, None, "PROCEED"]),  # None×3 → task_retry 후 PROCEED
        "task_retry": iter(["PROCEED"]),
        "review":     iter(["APPROVE"]),
    }

    def _mock(root, step, prompt, executor, timeout, *, dry_run=False, isolated=False):
        v = next(step_verdicts.get(step, iter(["PROCEED"])), "PROCEED")
        body = "output" if v is None else f"output\nVERDICT: {v}"
        return (0, body)

    import subprocess as _sp
    def _mock_sp(cmd, **kw):
        return _sp.CompletedProcess(cmd, 0, stdout="https://github.com/pr/1", stderr="")

    with patch.object(_aut, "_run_pipeline_step", side_effect=_mock), \
         patch.object(_aut, "_checkout_new_branch", return_value="feat/test"), \
         patch.object(_aut, "_detect_executor", return_value="codex"), \
         patch("subprocess.run", side_effect=_mock_sp):
        rc = _aut.cmd_pipeline(root=tmp_repo,
                               instruction="test instruction that is long enough",
                               branch="feat/test", executor_pref="codex", max_time=60,
                               dry_run=False, auto=True, mode_arg="full", allow_dirty=True)

    data = json.loads((tmp_repo / "result.json").read_text())
    assert rc == 0, f"rc={rc}"
    assert data["status"] == "completed", f"status={data['status']}"
    assert "task_retry" in data.get("steps", {}), "task_retry 가 result.json 에 없음"


# ─────────────────────────────────────────────
# T2: critique verdict=BLOCK 1회 → 즉시 plan_retry → completed
# ─────────────────────────────────────────────

def test_critique_block_verdict_immediate_plan_retry(tmp_repo, monkeypatch):
    """critique BLOCK 1회 → streak 기다리지 않고 즉시 plan_retry → completed."""
    monkeypatch.setenv("OmC_PIPELINE_RESULT_PATH", str(tmp_repo / "result.json"))
    # plan(PROCEED), task(PROCEED), critique(BLOCK 1회 즉시 탈출),
    # plan_retry(PROCEED), critique_retry(PROCEED), review(APPROVE)
    step_verdicts = {
        "plan":       iter(["PROCEED"]),
        "task":       iter(["PROCEED"]),
        "critique":   iter(["BLOCK", "PROCEED"]),  # BLOCK 1회 → 즉시 탈출, retry 후 PROCEED
        "plan_retry": iter(["PROCEED"]),
        "review":     iter(["APPROVE"]),
    }

    def _mock(root, step, prompt, executor, timeout, *, dry_run=False, isolated=False):
        v = next(step_verdicts.get(step, iter(["PROCEED"])), "PROCEED")
        return (0, f"output\nVERDICT: {v}")

    import subprocess as _sp
    def _mock_sp(cmd, **kw):
        return _sp.CompletedProcess(cmd, 0, stdout="https://github.com/pr/1", stderr="")

    with patch.object(_aut, "_run_pipeline_step", side_effect=_mock), \
         patch.object(_aut, "_checkout_new_branch", return_value="feat/test"), \
         patch.object(_aut, "_detect_executor", return_value="codex"), \
         patch("subprocess.run", side_effect=_mock_sp):
        rc = _aut.cmd_pipeline(root=tmp_repo,
                               instruction="test instruction that is long enough",
                               branch="feat/test", executor_pref="codex", max_time=60,
                               dry_run=False, auto=True, mode_arg="full", allow_dirty=True)

    data = json.loads((tmp_repo / "result.json").read_text())
    assert rc == 0, f"rc={rc}"
    assert data["status"] == "completed", f"status={data['status']}"
    assert "plan_retry" in data.get("steps", {}), "plan_retry 가 result.json 에 없음"

# ─────────────────────────────────────────────
# REVIEW-FIX R3: critique 첫 번째 None만으로 streak이 발동하지 않아야 한다
# ─────────────────────────────────────────────

def test_critique_first_none_does_not_trigger_streak(tmp_repo, monkeypatch):
    """critique 첫 번째 verdict=None 은 streak에 포함되지 않아야 한다.
    None×2 만으로는 탈출이 발동하면 안 되며 (sentinel 초기화 검증),
    None, REVISE, None 처럼 첫 None 이후 다른 verdict가 끼어들어도
    streak이 리셋돼 task_retry가 발동하지 않아야 한다."""
    monkeypatch.setenv("OmC_PIPELINE_RESULT_PATH", str(tmp_repo / "result.json"))
    # None × 2 → prev_verdict sentinel 오분류 없이 정상 retry 소진 후 PROCEED
    # critique(None, None, PROCEED), review(APPROVE) — task_retry 없이 completed
    step_verdicts = {
        "plan":     iter(["PROCEED"]),
        "task":     iter(["PROCEED"]),
        "critique": iter([None, None, "PROCEED"]),
        "review":   iter(["APPROVE"]),
    }

    def _mock(root, step, prompt, executor, timeout, *, dry_run=False, isolated=False):
        v = next(step_verdicts.get(step, iter(["PROCEED"])), "PROCEED")
        body = "output" if v is None else f"output\nVERDICT: {v}"
        return (0, body)

    import subprocess as _sp
    def _mock_sp(cmd, **kw):
        return _sp.CompletedProcess(cmd, 0, stdout="https://github.com/pr/1", stderr="")

    with patch.object(_aut, "_run_pipeline_step", side_effect=_mock), \
         patch.object(_aut, "_checkout_new_branch", return_value="feat/test"), \
         patch.object(_aut, "_detect_executor", return_value="codex"), \
         patch("subprocess.run", side_effect=_mock_sp):
        rc = _aut.cmd_pipeline(root=tmp_repo,
                               instruction="test instruction that is long enough",
                               branch="feat/test", executor_pref="codex", max_time=60,
                               dry_run=False, auto=True, mode_arg="full", allow_dirty=True)

    data = json.loads((tmp_repo / "result.json").read_text())
    assert rc == 0, f"rc={rc}"
    assert data["status"] == "completed", f"status={data['status']}"
    # None×2 가 sentinel 오분류로 인해 streak 탈출→task_retry로 이어지면 안 됨
    assert "task_retry" not in data.get("steps", {}), \
        "None×2 만으로 task_retry가 발동하면 sentinel 초기화 버그"

# ─────────────────────────────────────────────
# T1-NEW: _CRITIQUE_QUALITY_HINT에 3가지 새 기준 포함
# ─────────────────────────────────────────────

def test_quality_hint_contains_new_three_items():
    """_CRITIQUE_QUALITY_HINT에 이번 BLOCK 유발 3개 기준이 포함돼야 한다.
    - 데이터 품질 실패 강제 분기 키워드
    - 환경변수 의존 정책 기본값 명시 키워드
    - 조건 분기 테스트 + 운영 기본값 문서화 키워드
    """
    hint = _aut._CRITIQUE_QUALITY_HINT
    assert "데이터 품질" in hint or "invalid_" in hint,         "데이터 품질 실패 강제 분기 기준 누락"
    assert "환경변수" in hint,         "환경변수 의존 정책 기본값 명시 기준 누락"
    assert "운영 기본값" in hint or "기본값 문서" in hint,         "운영 기본값 문서화 기준 누락"

# ─────────────────────────────────────────────
# B-T1: task 프롬프트에 contract-done 지시 없어야 함
# ─────────────────────────────────────────────

def test_task_prompts_have_no_contract_done_instruction(tmp_repo, monkeypatch):
    """task_prompt / task_prompt_lite / task_retry_prompt 어디에도
    'contract-done' 문자열이 없어야 한다.
    preflight에서 이미 처리하므로 executor가 재실행할 필요 없음."""
    monkeypatch.setenv("OmC_PIPELINE_RESULT_PATH", str(tmp_repo / "result.json"))
    captured: dict[str, str] = {}
    verdicts = iter(["PROCEED", "PROCEED", "PROCEED", "APPROVE"])

    def _mock(root, step, prompt, executor, timeout, *, dry_run=False, isolated=False):
        captured[step] = prompt
        v = next(verdicts, "PROCEED")
        return (0, f"output\nVERDICT: {v}")

    import subprocess as _sp
    def _mock_sp(cmd, **kw):
        return _sp.CompletedProcess(cmd, 0, stdout="https://github.com/pr/1", stderr="")

    with patch.object(_aut, "_run_pipeline_step", side_effect=_mock), \
         patch.object(_aut, "_checkout_new_branch", return_value="feat/test"), \
         patch.object(_aut, "_detect_executor", return_value="codex"), \
         patch("subprocess.run", side_effect=_mock_sp):
        _aut.cmd_pipeline(root=tmp_repo,
                          instruction="test instruction that is long enough",
                          branch="feat/test", executor_pref="codex", max_time=60,
                          dry_run=False, auto=True, mode_arg="full", allow_dirty=True)

    for step_name, prompt in captured.items():
        assert "contract-done" not in prompt, (
            f"step='{step_name}' 프롬프트에 'contract-done' 지시가 남아 있음\n"
            f"프롬프트 앞 200자: {prompt[:200]}"
        )


# ─────────────────────────────────────────────
# B-T2: preflight에서 타깃 guard session-start + contract-done 호출
# ─────────────────────────────────────────────

def test_preflight_initializes_target_guard_when_different(tmp_repo, monkeypatch):
    """cmd_pipeline 실행 시 root/scripts/omc_pipeline_guard.py 가 존재하고
    omc_kit guard와 다른 파일이면 session-start + contract-done 이 호출돼야 한다."""
    monkeypatch.setenv("OmC_PIPELINE_RESULT_PATH", str(tmp_repo / "result.json"))

    # 타깃 프로젝트에 별도 guard 생성 (omc_kit와 다른 경로)
    target_guard = tmp_repo / "scripts" / "omc_pipeline_guard.py"
    target_guard.parent.mkdir(parents=True, exist_ok=True)
    target_guard.write_text("# mock guard\n")

    called_cmds: list[list] = []

    import subprocess as _sp
    def _mock_sp(cmd, **kw):
        called_cmds.append(list(cmd))
        return _sp.CompletedProcess(cmd, 0, stdout="", stderr="")

    verdicts = iter(["PROCEED", "PROCEED", "PROCEED", "APPROVE"])
    def _mock_step(root, step, prompt, executor, timeout, *, dry_run=False, isolated=False):
        v = next(verdicts, "PROCEED")
        return (0, f"output\nVERDICT: {v}")

    with patch.object(_aut, "_run_pipeline_step", side_effect=_mock_step), \
         patch.object(_aut, "_checkout_new_branch", return_value="feat/test"), \
         patch.object(_aut, "_detect_executor", return_value="codex"), \
         patch("subprocess.run", side_effect=_mock_sp):
        _aut.cmd_pipeline(root=tmp_repo,
                          instruction="test instruction that is long enough",
                          branch="feat/test", executor_pref="codex", max_time=60,
                          dry_run=False, auto=True, mode_arg="full", allow_dirty=True)

    guard_path = str(target_guard)
    session_start_called = any(
        "session-start" in cmd and guard_path in " ".join(cmd)
        for cmd in called_cmds
    )
    contract_done_called = any(
        "contract-done" in " ".join(cmd) and guard_path in " ".join(cmd)
        for cmd in called_cmds
    )
    assert session_start_called, (
        f"타깃 guard session-start 미호출\n실제 호출: {called_cmds}"
    )
    assert contract_done_called, (
        f"타깃 guard contract-done 미호출\n실제 호출: {called_cmds}"
    )

def test_preflight_does_not_double_init_when_same_guard(tmp_repo, monkeypatch):
    """target_guard.resolve() == omc_kit guard.resolve() 이면
    추가 session-start / contract-done 이 호출되지 않아야 한다.
    즉 omc_kit 자기 자신을 대상으로 실행할 때 이중 초기화 금지."""
    monkeypatch.setenv("OmC_PIPELINE_RESULT_PATH", str(tmp_repo / "result.json"))

    # 타깃 guard 를 omc_kit guard와 동일하게 설정 (resolve() 같음)
    import omc_autopilot as _a2
    real_guard = Path(_a2.__file__).resolve().parent / "omc_pipeline_guard.py"

    called_cmds: list[list] = []

    import subprocess as _sp
    def _mock_sp(cmd, **kw):
        called_cmds.append(list(cmd))
        return _sp.CompletedProcess(cmd, 0, stdout="", stderr="")

    verdicts = iter(["PROCEED", "PROCEED", "PROCEED", "APPROVE"])
    def _mock_step(root, step, prompt, executor, timeout, *, dry_run=False, isolated=False):
        v = next(verdicts, "PROCEED")
        return (0, f"output\nVERDICT: {v}")

    # tmp_repo/scripts/omc_pipeline_guard.py 를 real_guard 의 심볼릭 링크로 만든다
    target_scripts = tmp_repo / "scripts"
    target_scripts.mkdir(parents=True, exist_ok=True)
    target_link = target_scripts / "omc_pipeline_guard.py"
    target_link.symlink_to(real_guard)

    with patch.object(_aut, "_run_pipeline_step", side_effect=_mock_step), \
         patch.object(_aut, "_checkout_new_branch", return_value="feat/test"), \
         patch.object(_aut, "_detect_executor", return_value="codex"), \
         patch("subprocess.run", side_effect=_mock_sp):
        _aut.cmd_pipeline(root=tmp_repo,
                          instruction="test instruction that is long enough",
                          branch="feat/test", executor_pref="codex", max_time=60,
                          dry_run=False, auto=True, mode_arg="full", allow_dirty=True)

    real_guard_str = str(real_guard)
    # omc_kit guard 에 대한 정상 session-start/contract-done 은 1회씩만
    guard_calls = [c for c in called_cmds if real_guard_str in " ".join(c)]
    session_start_count = sum(1 for c in guard_calls if "session-start" in c)
    contract_done_count = sum(1 for c in guard_calls if "contract-done" in " ".join(c))

    assert session_start_count == 1, (
        f"session-start 이중 호출 감지: {session_start_count}회\n호출 목록: {guard_calls}"
    )
    assert contract_done_count == 1, (
        f"contract-done 이중 호출 감지: {contract_done_count}회\n호출 목록: {guard_calls}"
    )

# ─────────────────────────────────────────────
# C-T1: plan_prompt에 omc-task 참조 없어야 함
# ─────────────────────────────────────────────

def test_plan_prompt_has_no_omc_task_reference(tmp_repo, monkeypatch):
    """plan_prompt / retry_plan_prompt 어디에도 'omc-task' 문자열이 없어야 한다.
    대신 목표/범위/DoD/제약/실패조건 구조 힌트가 있어야 한다."""
    monkeypatch.setenv("OmC_PIPELINE_RESULT_PATH", str(tmp_repo / "result.json"))
    captured: dict[str, str] = {}
    verdicts = iter(["PROCEED", "PROCEED", "PROCEED", "APPROVE"])

    def _mock(root, step, prompt, executor, timeout, *, dry_run=False, isolated=False):
        captured[step] = prompt
        v = next(verdicts, "PROCEED")
        return (0, f"output\nVERDICT: {v}")

    import subprocess as _sp
    def _mock_sp(cmd, **kw):
        return _sp.CompletedProcess(cmd, 0, stdout="https://github.com/pr/1", stderr="")

    with patch.object(_aut, "_run_pipeline_step", side_effect=_mock), \
         patch.object(_aut, "_checkout_new_branch", return_value="feat/test"), \
         patch.object(_aut, "_detect_executor", return_value="codex"), \
         patch("subprocess.run", side_effect=_mock_sp):
        _aut.cmd_pipeline(root=tmp_repo,
                          instruction="test instruction that is long enough",
                          branch="feat/test", executor_pref="codex", max_time=60,
                          dry_run=False, auto=True, mode_arg="full", allow_dirty=True)

    plan_prompt = captured.get("plan", "")
    assert "omc-task" not in plan_prompt, (
        f"plan_prompt에 'omc-task' 참조가 남아 있음\n앞 300자: {plan_prompt[:300]}"
    )
    # 대체 구조 힌트 포함 여부
    assert any(kw in plan_prompt for kw in ["목표", "범위", "DoD", "실패조건"]), (
        f"plan_prompt에 구조 힌트(목표/범위/DoD/실패조건) 없음\n앞 300자: {plan_prompt[:300]}"
    )


# ─────────────────────────────────────────────
# C-T2: task_prompt 3곳에 자동화 모드 헤더 있어야 함
# ─────────────────────────────────────────────

def test_task_prompts_have_automation_mode_header(tmp_repo, monkeypatch):
    """task_prompt(full) / task_prompt_lite / task_retry_prompt 모두
    '[자동화 모드]' 헤더를 포함해야 한다."""
    # ── full 모드: task_prompt / task_retry_prompt 검증 ──
    monkeypatch.setenv("OmC_PIPELINE_RESULT_PATH", str(tmp_repo / "result.json"))
    captured_full: dict[str, str] = {}
    # critique verdict 없음 3회 → task_retry 트리거
    critique_verdicts_full = iter([None, None, None, "PROCEED"])

    def _mock_full(root, step, prompt, executor, timeout, *, dry_run=False, isolated=False):
        captured_full[step] = prompt
        if step == "critique":
            v = next(critique_verdicts_full, "PROCEED")
            return (0, "output" if v is None else f"output\nVERDICT: {v}")
        return (0, "output\nVERDICT: PROCEED")

    import subprocess as _sp
    def _mock_sp(cmd, **kw):
        return _sp.CompletedProcess(cmd, 0, stdout="https://github.com/pr/1", stderr="")

    with patch.object(_aut, "_run_pipeline_step", side_effect=_mock_full), \
         patch.object(_aut, "_checkout_new_branch", return_value="feat/test"), \
         patch.object(_aut, "_detect_executor", return_value="codex"), \
         patch("subprocess.run", side_effect=_mock_sp):
        _aut.cmd_pipeline(root=tmp_repo,
                          instruction="test instruction that is long enough",
                          branch="feat/test", executor_pref="codex", max_time=60,
                          dry_run=False, auto=True, mode_arg="full", allow_dirty=True)

    for step_name in ["task", "task_retry"]:
        prompt = captured_full.get(step_name, "")
        assert "[자동화 모드]" in prompt or "AUTOMATION MODE" in prompt, (
            f"[full 모드] step='{step_name}' 프롬프트에 자동화 모드 헤더 없음\n앞 200자: {prompt[:200]}"
        )

    # ── lite 모드: task_prompt_lite 검증 ──
    captured_lite: dict[str, str] = {}
    verdicts_lite = iter(["PROCEED", "APPROVE"])

    def _mock_lite(root, step, prompt, executor, timeout, dry_run=False):
        captured_lite[step] = prompt
        v = next(verdicts_lite, "PROCEED")
        return (0, f"output\nVERDICT: {v}")

    (tmp_repo / "result.json").unlink(missing_ok=True)
    monkeypatch.setenv("OmC_PIPELINE_RESULT_PATH", str(tmp_repo / "result_lite.json"))

    with patch.object(_aut, "_run_pipeline_step", side_effect=_mock_lite), \
         patch.object(_aut, "_checkout_new_branch", return_value="feat/test"), \
         patch.object(_aut, "_detect_executor", return_value="codex"), \
         patch("subprocess.run", side_effect=_mock_sp):
        _aut.cmd_pipeline(root=tmp_repo,
                          instruction="test instruction that is long enough",
                          branch="feat/test", executor_pref="codex", max_time=60,
                          dry_run=False, auto=True, mode_arg="lite", allow_dirty=True)

    lite_prompt = captured_lite.get("task", "")
    assert "[자동화 모드]" in lite_prompt or "AUTOMATION MODE" in lite_prompt, (
        f"[lite 모드] task_prompt_lite에 자동화 모드 헤더 없음\n앞 200자: {lite_prompt[:200]}"
    )
