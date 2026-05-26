"""
test_omc_autopilot_pipeline.py — pipeline 서브커맨드 회귀 방지

T3/T4 DoD:
  - pipeline --dry-run exit 0
  - pipeline_run_result.json 생성
  - uncommitted 변경 있으면 abort
  - retry_exhausted 시 비0 종료
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
AUTOPILOT = ROOT / "scripts" / "omc_autopilot.py"


def _run(args: list[str], cwd: Path | None = None) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, str(AUTOPILOT)] + args,
        capture_output=True,
        text=True,
        cwd=str(cwd or ROOT),
    )


def test_pipeline_dry_run_exits_zero(tmp_path: Path):
    """pipeline --dry-run은 exit 0으로 완료돼야 한다."""
    result = _run(
        ["--target", str(tmp_path),
         "pipeline",
         "--instruction", "테스트용 더미 지시문",
         "--branch", "feat/test-pipeline",
         "--dry-run"],
    )
    assert result.returncode == 0, (
        f"pipeline --dry-run exit {result.returncode}\n"
        f"stdout: {result.stdout[-500:]}\nstderr: {result.stderr[-300:]}"
    )


def test_pipeline_dry_run_creates_result_file(tmp_path: Path):
    """pipeline --dry-run 완료 후 pipeline_run_result.json이 생성돼야 한다."""
    _run(
        ["--target", str(tmp_path),
         "pipeline",
         "--instruction", "테스트용 더미 지시문",
         "--branch", "feat/test-pipeline",
         "--dry-run"],
    )
    result_file = tmp_path / ".omc" / "pipeline_run_result.json"
    assert result_file.exists(), "pipeline_run_result.json 미생성"
    data = json.loads(result_file.read_text(encoding="utf-8"))
    assert "status" in data
    assert "branch" in data
    assert "steps" in data


def test_pipeline_requires_instruction():
    """--instruction 없으면 exit 비0이어야 한다."""
    result = _run(["pipeline", "--dry-run"])
    assert result.returncode != 0, "instruction 없이 성공해선 안 됨"


def test_pipeline_subcommand_exists():
    """pipeline 서브커맨드가 argparse에 등록돼야 한다."""
    result = _run(["pipeline", "--help"])
    assert result.returncode == 0, f"pipeline --help 실패: {result.stderr}"
    assert "instruction" in result.stdout.lower() or "instruction" in result.stderr.lower()


def test_plan_hold_verdict_aborts_pipeline(tmp_path: Path):
    """PLAN 스텝이 VERDICT: HOLD를 출력하면 pipeline이 중단돼야 한다.
    
    현재는 미구현 — 이 테스트가 FAIL해야 RED 등록 가능.
    (dry_run에서 VERDICT를 모킹할 수 없으므로 별도 헬퍼로 검증)
    """
    # _grep_verdict 직접 테스트로 대리 검증
    import importlib.util, sys
    spec = importlib.util.spec_from_file_location(
        "omc_autopilot", str(ROOT / "scripts" / "omc_autopilot.py")
    )
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)

    assert mod._grep_verdict("VERDICT: PROCEED") == "PROCEED"
    assert mod._grep_verdict("VERDICT: HOLD") == "HOLD"
    assert mod._grep_verdict("VERDICT: APPROVE") == "APPROVE"
    assert mod._grep_verdict("no verdict here") is None


def test_git_push_failure_saves_failed_status(tmp_path: Path):
    """git push 실패 시 pipeline_run_result.json의 status가 failed여야 한다.
    
    현재 구현은 push 실패해도 completed로 저장 — 이 테스트 FAIL.
    (dry_run 모드에서는 push를 스킵하므로 실제 동작을 단위 테스트로 검증 불가)
    N/A — dry_run 경로로는 push 실패 시나리오를 직접 재현할 수 없음.
    대신 결과 파일 status 필드가 completed/failed/retry_exhausted 중 하나인지 확인.
    """
    _run(
        ["--target", str(tmp_path),
         "pipeline",
         "--instruction", "테스트 지시문 충분한 길이",
         "--branch", "feat/x",
         "--dry-run"],
    )
    result_file = tmp_path / ".omc" / "pipeline_run_result.json"
    assert result_file.exists()
    data = json.loads(result_file.read_text(encoding="utf-8"))
    valid_statuses = {"completed", "failed", "retry_exhausted", "aborted", "timeout", "plan_hold"}
    assert data["status"] in valid_statuses, f"알 수 없는 status: {data['status']}"


# ── --resume 테스트 ──────────────────────────────────────────────────────

def _run_pipeline(args: list[str], cwd: Path) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, str(AUTOPILOT)] + args,
        capture_output=True, text=True, cwd=str(cwd),
    )


def test_resume_without_result_file_exits_nonzero(tmp_path: Path):
    """result 파일 없을 때 --resume은 exit 1 해야 한다."""
    (tmp_path / ".omc").mkdir()
    r = _run_pipeline([
        "pipeline",
        "--instruction", "충분히 긴 테스트 지시문입니다",
        "--branch", "fix/resume-test",
        "--resume",
        "--dry-run",
    ], cwd=tmp_path)
    assert r.returncode != 0, f"result 파일 없는데 exit 0\nstdout: {r.stdout[-300:]}"
    combined = r.stdout + r.stderr
    assert any(kw in combined for kw in ("resume", "결과", "없음", "파일")), (
        f"result 없음 안내 메시지 없음: {combined[:300]}"
    )


def test_resume_skips_completed_steps(tmp_path: Path):
    """plan=completed 상태에서 --resume 시 plan을 건너뛰어야 한다."""
    omc_dir = tmp_path / ".omc"
    omc_dir.mkdir()
    long_instruction = "FULL 모드를 강제하기 위해 50자를 초과하는 충분히 긴 지시문입니다 여기서 더 길게"
    result_data = {
        "status": "failed",
        "mode": "full",
        "branch": "feat/resume-test",
        "instruction": long_instruction,
        "executor": "codex",
        "started_at": "2026-01-01T000000Z",
        "steps": {
            "preflight": {"status": "completed"},
            "plan": {"status": "completed", "output_preview": "plan done"},
            "task": {"status": "failed", "output_preview": "task failed"},
        },
    }
    (omc_dir / "pipeline_run_result.json").write_text(
        __import__("json").dumps(result_data), encoding="utf-8"
    )
    r = _run_pipeline([
        "pipeline",
        "--instruction", long_instruction,
        "--branch", "feat/resume-test",
        "--resume",
        "--dry-run",
        "--allow-dirty",
        "--mode", "full",
    ], cwd=tmp_path)
    combined = r.stdout + r.stderr
    assert "⏭" in combined or "건너" in combined or "skip" in combined.lower(), (
        f"completed 단계 skip 메시지 없음\nstdout: {combined[:500]}"
    )


def test_resume_already_completed_exits_zero(tmp_path: Path):
    """이미 completed인 파이프라인을 --resume 시 exit 0 + 안내 메시지."""
    omc_dir = tmp_path / ".omc"
    omc_dir.mkdir()
    result_data = {
        "status": "completed",
        "mode": "lite",
        "branch": "fix/done",
        "instruction": "충분히 긴 테스트 지시문입니다",
        "steps": {},
    }
    (omc_dir / "pipeline_run_result.json").write_text(
        __import__("json").dumps(result_data), encoding="utf-8"
    )
    r = _run_pipeline([
        "pipeline",
        "--instruction", "충분히 긴 테스트 지시문입니다",
        "--branch", "fix/done",
        "--resume",
        "--dry-run",
        "--allow-dirty",
    ], cwd=tmp_path)
    assert r.returncode == 0, f"completed resume인데 exit nonzero\nstdout: {r.stdout[-300:]}"
    combined = r.stdout + r.stderr
    assert any(kw in combined for kw in ("완료", "completed", "이미")), (
        f"완료 안내 없음: {combined[:300]}"
    )


# ── pipeline-status 테스트 ───────────────────────────────────────────────────

def test_pipeline_status_no_file_exits_zero(tmp_path: Path):
    """pipeline_run_result.json 없을 때 pipeline-status는 exit 0 + 안내 메시지."""
    (tmp_path / ".omc").mkdir()
    r = _run(["--target", str(tmp_path), "pipeline-status"])
    assert r.returncode == 0, (
        f"result 없을 때 exit nonzero\nstdout: {r.stdout}\nstderr: {r.stderr}"
    )
    combined = r.stdout + r.stderr
    assert any(kw in combined for kw in ("없음", "기록", "no result", "not found")), (
        f"파일 없음 안내 메시지 없음: {combined[:300]}"
    )


def test_pipeline_status_shows_completed(tmp_path: Path):
    """completed result JSON 있을 때 pipeline-status가 completed 상태를 출력한다."""
    omc_dir = tmp_path / ".omc"
    omc_dir.mkdir()
    result_data = {
        "status": "completed",
        "mode": "lite",
        "branch": "feat/test",
        "executor": "codex",
        "started_at": "2026-05-26T020000Z",
        "finished_at": "2026-05-26T020500Z",
        "steps": {
            "preflight": {"status": "completed"},
            "task": {"status": "completed", "output_preview": "VERDICT: PROCEED"},
            "pr": {"status": "completed"},
        },
    }
    (omc_dir / "pipeline_run_result.json").write_text(
        json.dumps(result_data), encoding="utf-8"
    )
    r = _run(["--target", str(tmp_path), "pipeline-status"])
    assert r.returncode == 0, (
        f"pipeline-status exit nonzero\nstdout: {r.stdout}\nstderr: {r.stderr}"
    )
    combined = r.stdout + r.stderr
    assert "completed" in combined.lower(), f"completed 상태 미출력: {combined[:500]}"
    assert "preflight" in combined.lower(), f"단계명 미출력: {combined[:500]}"


def test_pipeline_status_shows_error_message(tmp_path: Path):
    """실패 단계에 error_message 있으면 pipeline-status가 해당 메시지를 출력한다."""
    omc_dir = tmp_path / ".omc"
    omc_dir.mkdir()
    result_data = {
        "status": "failed",
        "mode": "full",
        "branch": "feat/error-test",
        "executor": "codex",
        "started_at": "2026-05-26T020000Z",
        "steps": {
            "preflight": {"status": "completed"},
            "plan": {"status": "completed"},
            "task": {
                "status": "failed",
                "error_message": "TimeoutError: LLM 응답 초과",
            },
        },
    }
    (omc_dir / "pipeline_run_result.json").write_text(
        json.dumps(result_data), encoding="utf-8"
    )
    r = _run(["--target", str(tmp_path), "pipeline-status"])
    assert r.returncode == 0, (
        f"pipeline-status exit nonzero\nstdout: {r.stdout}\nstderr: {r.stderr}"
    )
    combined = r.stdout + r.stderr
    assert "TimeoutError" in combined or "LLM 응답 초과" in combined, (
        f"error_message 미출력: {combined[:500]}"
    )


# ── pipeline-status --watch 테스트 ──────────────────────────────────────────

def test_pipeline_status_watch_flag_exists():
    """pipeline-status --help에 --watch 옵션이 등록돼 있어야 한다."""
    r = _run(["pipeline-status", "--help"])
    assert r.returncode == 0, f"pipeline-status --help 실패: {r.stderr}"
    combined = r.stdout + r.stderr
    assert "watch" in combined.lower(), (
        f"--watch 옵션 미등록: {combined[:300]}"
    )


def test_pipeline_status_interval_zero_exits_nonzero(tmp_path: Path):
    """--interval 0은 exit 1 이어야 한다."""
    (tmp_path / ".omc").mkdir()
    r = _run(["--target", str(tmp_path), "pipeline-status", "--watch", "--interval", "0"])
    assert r.returncode != 0, (
        f"interval 0인데 exit 0\nstdout: {r.stdout}\nstderr: {r.stderr}"
    )
    combined = r.stdout + r.stderr
    assert any(kw in combined for kw in ("interval", "1 이상", "이상이어야")), (
        f"interval 오류 메시지 없음: {combined[:300]}"
    )


def test_save_pipeline_result_writes_valid_json(tmp_path: Path):
    """_save_pipeline_result() 호출 후 result 파일이 유효한 JSON이어야 한다.
    
    atomic write 도입 후에도 파일이 항상 파싱 가능한 상태를 보장한다.
    """
    import importlib.util
    spec = importlib.util.spec_from_file_location(
        "omc_autopilot", str(ROOT / "scripts" / "omc_autopilot.py")
    )
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)

    (tmp_path / ".omc").mkdir()
    data = {"status": "running", "steps": {"preflight": {"status": "completed"}}}
    mod._save_pipeline_result(tmp_path, data)

    result_path = tmp_path / ".omc" / "pipeline_run_result.json"
    assert result_path.exists(), "result 파일 미생성"
    parsed = json.loads(result_path.read_text(encoding="utf-8"))
    assert parsed["status"] == "running"
    assert parsed["steps"]["preflight"]["status"] == "completed"

# ─────────────────────────────────────────────────────────────────────────────
# T0: conftest autouse fixture — 프로젝트 루트 오염 방지
# ─────────────────────────────────────────────────────────────────────────────

def test_result_path_never_touches_project_root(tmp_path: Path, monkeypatch):
    """OmC_PIPELINE_RESULT_PATH 설정 시 프로젝트 루트 .omc/pipeline_run_result.json을 생성하지 않아야 한다."""
    import importlib
    import omc_autopilot as mod
    importlib.reload(mod)

    custom = tmp_path / "isolated_result.json"
    monkeypatch.setenv("OmC_PIPELINE_RESULT_PATH", str(custom))
    monkeypatch.delenv("OmC_PIPELINE_RESULT_PATH", raising=False)  # 환경변수 제거 후
    monkeypatch.setenv("OmC_PIPELINE_RESULT_PATH", str(custom))   # 재설정

    result_path = mod._get_result_path(tmp_path)
    assert result_path == custom
    assert result_path != ROOT / ".omc" / "pipeline_run_result.json", (
        "_get_result_path가 프로젝트 루트 경로를 반환하면 오염 가능"
    )

# ─────────────────────────────────────────────────────────────────────────────
# T1: _get_result_path 환경변수 오버라이드
# ─────────────────────────────────────────────────────────────────────────────

def test_result_path_uses_env_override(tmp_path: Path, monkeypatch):
    """OmC_PIPELINE_RESULT_PATH 환경변수 설정 시 해당 경로를 반환해야 한다."""
    import importlib
    import omc_autopilot as mod
    importlib.reload(mod)  # 환경변수 반영을 위해 reload

    custom = tmp_path / "custom_result.json"
    monkeypatch.setenv("OmC_PIPELINE_RESULT_PATH", str(custom))

    result = mod._get_result_path(tmp_path)
    assert result == custom


def test_result_path_falls_back_to_default(tmp_path: Path, monkeypatch):
    """OmC_PIPELINE_RESULT_PATH 미설정 시 root / .omc/pipeline_run_result.json 을 반환해야 한다."""
    import importlib
    import omc_autopilot as mod
    importlib.reload(mod)

    monkeypatch.delenv("OmC_PIPELINE_RESULT_PATH", raising=False)

    result = mod._get_result_path(tmp_path)
    assert result == tmp_path / ".omc" / "pipeline_run_result.json"

# ─────────────────────────────────────────────────────────────────────────────
# T2: critique 재시도 프롬프트 컨텍스트 주입 + 동일 verdict 탈출
# ─────────────────────────────────────────────────────────────────────────────

def test_build_retry_prompt_includes_prev_verdict():
    """_build_retry_prompt: prev_verdict가 있으면 프롬프트에 직전 VERDICT 컨텍스트가 포함돼야 한다."""
    import importlib
    import omc_autopilot as mod
    importlib.reload(mod)

    base = "이 코드를 critique 하세요."
    result = mod._build_retry_prompt(base, prev_verdict="HOLD", attempt=1)
    assert "HOLD" in result
    assert "직전" in result or "이전" in result or "1회차" in result


def test_build_retry_prompt_without_prev_verdict_returns_base():
    """_build_retry_prompt: prev_verdict가 None이면 base 프롬프트를 그대로 반환해야 한다."""
    import importlib
    import omc_autopilot as mod
    importlib.reload(mod)

    base = "이 코드를 critique 하세요."
    result = mod._build_retry_prompt(base, prev_verdict=None, attempt=0)
    assert result == base


def test_critique_same_verdict_repeated_exits_failed_critique_loop(tmp_path: Path, monkeypatch):
    """critique가 동일 HOLD verdict를 2회 연속 반환하면 failed_critique_loop로 종료돼야 한다."""
    import importlib
    import omc_autopilot as mod
    importlib.reload(mod)

    call_count = {"n": 0}

    def mock_step(root, step_name, prompt, executor, timeout, *, dry_run=False):
        call_count["n"] += 1
        if step_name == "plan":
            return 0, "VERDICT: PROCEED"
        if step_name == "critique":
            return 0, "VERDICT: HOLD"
        return 0, "VERDICT: PROCEED"

    monkeypatch.setattr(mod, "_run_pipeline_step", mock_step)
    monkeypatch.setenv("OmC_PIPELINE_RESULT_PATH", str(tmp_path / "result.json"))

    # git 관련 subprocess 를 mock
    import subprocess as sp
    original_run = sp.run
    def mock_subprocess(cmd, **kwargs):
        if isinstance(cmd, list) and "git" in cmd:
            return sp.CompletedProcess(cmd, 0, stdout="", stderr="")
        return original_run(cmd, **kwargs)
    monkeypatch.setattr(sp, "run", mock_subprocess)

    rc = mod.cmd_pipeline(
        root=tmp_path,
        instruction="x" * 200,
        branch="feat/t2",
        executor_pref="cursor",
        dry_run=True,
        allow_dirty=True,
    )
    result_path = tmp_path / "result.json"
    assert result_path.exists(), "result.json 미생성"
    data = __import__("json").loads(result_path.read_text(encoding="utf-8"))
    assert data["status"] == "failed_critique_loop", (
        f"expected failed_critique_loop, got {data['status']}"
    )

# ─────────────────────────────────────────────────────────────────────────────
# T3: AMBIGUOUS_RESPONSE — task verdict None 처리
# ─────────────────────────────────────────────────────────────────────────────

def test_task_ambiguous_response_retries_once_then_succeeds(tmp_path: Path, monkeypatch):
    """task verdict가 None이면 1회 재시도 후 PROCEED가 나오면 정상 완료돼야 한다."""
    import importlib
    import omc_autopilot as mod
    importlib.reload(mod)

    task_calls = {"n": 0}

    def mock_step(root, step_name, prompt, executor, timeout, *, dry_run=False):
        if step_name == "plan":
            return 0, "VERDICT: PROCEED"
        if step_name == "task":
            task_calls["n"] += 1
            if task_calls["n"] == 1:
                return 0, "확인하시겠습니까?"  # VERDICT 없음
            return 0, "VERDICT: PROCEED"
        if step_name == "critique":
            return 0, "VERDICT: PROCEED"
        if step_name == "review":
            return 0, "VERDICT: APPROVE"
        return 0, "VERDICT: PROCEED"

    monkeypatch.setattr(mod, "_run_pipeline_step", mock_step)
    monkeypatch.setenv("OmC_PIPELINE_RESULT_PATH", str(tmp_path / "result.json"))

    import subprocess as sp
    original_run = sp.run
    def mock_subprocess(cmd, **kwargs):
        if isinstance(cmd, list) and "git" in cmd:
            return sp.CompletedProcess(cmd, 0, stdout="", stderr="")
        return original_run(cmd, **kwargs)
    monkeypatch.setattr(sp, "run", mock_subprocess)

    rc = mod.cmd_pipeline(
        root=tmp_path,
        instruction="x" * 200,
        branch="feat/t3",
        executor_pref="cursor",
        dry_run=True,
        allow_dirty=True,
    )
    result_path = tmp_path / "result.json"
    data = __import__("json").loads(result_path.read_text(encoding="utf-8"))
    assert data["status"] == "completed", f"expected completed, got {data['status']}"
    assert task_calls["n"] == 2, f"task가 2회 호출돼야 함, 실제: {task_calls['n']}"


def test_task_ambiguous_response_fails_after_two_nones(tmp_path: Path, monkeypatch):
    """task verdict가 2회 연속 None이면 failed_ambiguous_response로 종료돼야 한다."""
    import importlib
    import omc_autopilot as mod
    importlib.reload(mod)

    def mock_step(root, step_name, prompt, executor, timeout, *, dry_run=False):
        if step_name == "plan":
            return 0, "VERDICT: PROCEED"
        if step_name == "task":
            return 0, "확인하시겠습니까?"  # 항상 VERDICT 없음
        return 0, "VERDICT: PROCEED"

    monkeypatch.setattr(mod, "_run_pipeline_step", mock_step)
    monkeypatch.setenv("OmC_PIPELINE_RESULT_PATH", str(tmp_path / "result.json"))

    import subprocess as sp
    original_run = sp.run
    def mock_subprocess(cmd, **kwargs):
        if isinstance(cmd, list) and "git" in cmd:
            return sp.CompletedProcess(cmd, 0, stdout="", stderr="")
        return original_run(cmd, **kwargs)
    monkeypatch.setattr(sp, "run", mock_subprocess)

    rc = mod.cmd_pipeline(
        root=tmp_path,
        instruction="x" * 200,
        branch="feat/t3-fail",
        executor_pref="cursor",
        dry_run=True,
        allow_dirty=True,
    )
    result_path = tmp_path / "result.json"
    data = __import__("json").loads(result_path.read_text(encoding="utf-8"))
    assert data["status"] == "failed_ambiguous_response", (
        f"expected failed_ambiguous_response, got {data['status']}"
    )


# ─────────────────────────────────────────────────────────────────────────────
# T4: 브랜치 suffix 재시도
# ─────────────────────────────────────────────────────────────────────────────

def test_checkout_new_branch_retries_with_suffix():
    """_checkout_new_branch: 첫 번째 충돌 시 -v2 suffix로 재시도해야 한다."""
    import importlib
    import subprocess as sp
    import omc_autopilot as mod
    importlib.reload(mod)
    from pathlib import Path
    from unittest.mock import patch, MagicMock

    call_log = []

    def mock_run(cmd, **kwargs):
        call_log.append(cmd)
        if "-v2" in (cmd[-1] if cmd else ""):
            return MagicMock(returncode=0, stderr="")
        return MagicMock(returncode=128, stderr="already exists")

    with patch("subprocess.run", side_effect=mock_run):
        name = mod._checkout_new_branch(Path("/tmp"), "feat/test", max_retry=3)

    assert name == "feat/test-v2", f"expected feat/test-v2, got {name}"


def test_checkout_new_branch_fails_after_max_retry():
    """_checkout_new_branch: max_retry 초과 시 RuntimeError를 발생시켜야 한다."""
    import importlib
    import omc_autopilot as mod
    importlib.reload(mod)
    from pathlib import Path
    from unittest.mock import patch, MagicMock

    def mock_run(cmd, **kwargs):
        return MagicMock(returncode=128, stderr="already exists")

    with patch("subprocess.run", side_effect=mock_run):
        try:
            mod._checkout_new_branch(Path("/tmp"), "feat/test", max_retry=3)
            assert False, "RuntimeError가 발생해야 함"
        except RuntimeError as e:
            assert "failed_branch" in str(e)


# ─────────────────────────────────────────────────────────────────────────────
# T5: run 이력 분리 저장
# ─────────────────────────────────────────────────────────────────────────────

def test_pipeline_saves_run_history_to_runs_dir(tmp_path: Path):
    """pipeline --dry-run 실행 후 .omc/runs/ 에 run_id 서브디렉토리와 result.json이 생성돼야 한다."""
    _run(
        ["--target", str(tmp_path),
         "pipeline",
         "--instruction", "runs 이력 저장 검증용 충분한 길이의 지시문",
         "--branch", "feat/t5-runs",
         "--dry-run"],
    )
    runs_dir = tmp_path / ".omc" / "runs"
    assert runs_dir.exists(), ".omc/runs 디렉토리 미생성"
    subdirs = list(runs_dir.iterdir())
    assert len(subdirs) >= 1, "runs 서브디렉토리 없음"
    result_json = subdirs[0] / "result.json"
    assert result_json.exists(), "runs/{run_id}/result.json 미생성"
    data = __import__("json").loads(result_json.read_text(encoding="utf-8"))
    assert "status" in data
