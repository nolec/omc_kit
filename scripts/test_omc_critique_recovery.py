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
    # REVISE×3 탈출 → plan_retry HOLD → hold 종료, critique_issues 저장돼야 함
    verdicts = iter(["PROCEED", "PROCEED", "REVISE", "REVISE", "REVISE", "HOLD"])
    def _mock(root, step, prompt, executor, timeout, dry_run=False):
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
    def _mock_step(root, step, prompt, executor, timeout, dry_run=False):
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
    def _mock(root, step, prompt, executor, timeout, dry_run=False):
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
    def _mock(root, step, prompt, executor, timeout, dry_run=False):
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
    def _mock(root, step, prompt, executor, timeout, dry_run=False):
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

    def _mock(root, step, prompt, executor, timeout, dry_run=False):
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
# T2 (A): critique REVISE × 3 탈출 시 task_retry 먼저 실행
# ─────────────────────────────────────────────

def test_critique_revise_triggers_task_retry_then_proceeds(tmp_repo, monkeypatch):
    """critique REVISE×3 탈출 → task_retry → critique PROCEED → completed."""
    monkeypatch.setenv("OmC_PIPELINE_RESULT_PATH", str(tmp_repo / "result.json"))
    # plan, task, critique(REVISE×3 탈출), task_retry, critique_retry(PROCEED), review(APPROVE)
    verdicts = iter(["PROCEED", "PROCEED", "REVISE", "REVISE", "REVISE",
                     "PROCEED", "PROCEED", "APPROVE"])

    def _mock_step(root, step, prompt, executor, timeout, dry_run=False):
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
    assert "task_retry" in data.get("steps", {}), "task_retry 스텝이 result.json 에 없음"


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

    def _mock(root, step, prompt, executor, timeout, dry_run=False):
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

    def _mock(root, step, prompt, executor, timeout, dry_run=False):
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
# REVIEW-FIX R2: task_retry rc≠0 → hold exit 2 (not exit 1)
# ─────────────────────────────────────────────

def test_task_retry_rc_nonzero_exits_hold_with_code_2(tmp_repo, monkeypatch):
    """task_retry 가 rc≠0(프로세스 실패)이면 status=hold, exit code=2."""
    monkeypatch.setenv("OmC_PIPELINE_RESULT_PATH", str(tmp_repo / "result.json"))
    verdicts_task_retry_fails = iter(["PROCEED", "PROCEED",
                                      "REVISE", "REVISE", "REVISE"])

    def _mock(root, step, prompt, executor, timeout, dry_run=False):
        v = next(verdicts_task_retry_fails, "PROCEED")
        if step == "task_retry":
            return (1, "fatal error")
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
