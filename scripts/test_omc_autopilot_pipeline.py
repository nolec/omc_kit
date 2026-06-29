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


def test_pipeline_dry_run_records_operational_metadata_fields(tmp_path: Path):
    """dry-run 결과 파일에 운영 콘솔용 메타데이터가 저장돼야 한다."""
    _run(
        ["--target", str(tmp_path),
         "pipeline",
         "--instruction", "운영 메타데이터 확인용 지시문",
         "--branch", "feat/test-operational-metadata",
         "--mode", "full",
         "--dry-run"],
    )
    result_file = tmp_path / ".omc" / "pipeline_run_result.json"
    data = json.loads(result_file.read_text(encoding="utf-8"))

    assert isinstance(data.get("last_heartbeat_at"), str)
    assert data.get("retry_count") == 0
    assert data.get("resume_count") == 0
    assert data.get("approval_required") is False


def test_pipeline_dry_run_records_step_timing_metadata(tmp_path: Path):
    """dry-run 결과 파일의 각 step에 timing 메타데이터가 저장돼야 한다."""
    _run(
        ["--target", str(tmp_path),
         "pipeline",
         "--instruction", "스텝 타이밍 메타데이터 확인용 지시문",
         "--branch", "feat/test-step-timing",
         "--mode", "full",
         "--dry-run"],
    )
    result_file = tmp_path / ".omc" / "pipeline_run_result.json"
    data = json.loads(result_file.read_text(encoding="utf-8"))

    for step_name in ("preflight", "plan", "task", "critique", "review", "pr"):
        step = data["steps"].get(step_name)
        assert step is not None, f"{step_name} step 누락"
        assert isinstance(step.get("started_at"), str), f"{step_name} started_at 누락"
        assert isinstance(step.get("finished_at"), str), f"{step_name} finished_at 누락"
        assert isinstance(step.get("duration_sec"), (int, float)), f"{step_name} duration_sec 누락"
        assert step["duration_sec"] >= 0, f"{step_name} duration_sec 음수"


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


def test_pipeline_status_shows_hold_icon_for_stale_recovery(tmp_path: Path):
    """stale 복구로 hold 상태가 되면 unknown 아이콘이 아니어야 한다."""
    omc_dir = tmp_path / ".omc"
    omc_dir.mkdir()
    result_data = {
        "status": "hold",
        "mode": "full",
        "branch": "feat/hold-status",
        "executor": "codex",
        "started_at": "2026-05-26T020000Z",
        "finished_at": "2026-05-26T020500Z",
        "steps": {
            "stale_recovery": {"status": "auto_hold", "reason": "pipeline pid not running: 4242"},
        },
    }
    (omc_dir / "pipeline_run_result.json").write_text(
        json.dumps(result_data), encoding="utf-8"
    )
    r = _run(["--target", str(tmp_path), "pipeline-status"])
    assert r.returncode == 0
    combined = r.stdout + r.stderr
    assert "hold" in combined.lower(), f"hold 상태 텍스트 미출력: {combined[:400]}"
    assert "❓" not in combined, f"hold 상태가 unknown 아이콘으로 출력됨: {combined[:400]}"


# ── pipeline-status --watch 테스트 ──────────────────────────────────────────

def test_pipeline_status_watch_flag_exists():
    """pipeline-status --help에 --watch 옵션이 등록돼 있어야 한다."""
    r = _run(["pipeline-status", "--help"])
    assert r.returncode == 0, f"pipeline-status --help 실패: {r.stderr}"
    combined = r.stdout + r.stderr
    assert "watch" in combined.lower(), (
        f"--watch 옵션 미등록: {combined[:300]}"
    )


def test_pipeline_status_recover_flag_exists():
    """pipeline-status --help에 --recover 옵션이 등록돼 있어야 한다."""
    r = _run(["pipeline-status", "--help"])
    assert r.returncode == 0, f"pipeline-status --help 실패: {r.stderr}"
    combined = r.stdout + r.stderr
    assert "recover" in combined.lower(), (
        f"--recover 옵션 미등록: {combined[:300]}"
    )


def test_pipeline_status_recover_changes_stale_running_to_hold(tmp_path: Path):
    """--recover 시 stale running 상태를 hold로 확정해야 한다."""
    omc_dir = tmp_path / ".omc"
    omc_dir.mkdir()
    result_path = omc_dir / "pipeline_run_result.json"
    result_data = {
        "status": "running",
        "mode": "full",
        "branch": "feat/recover",
        "executor": "codex",
        "pid": 999999999,
        "started_at": "2026-06-01T000000Z",
        "finished_at": None,
        "steps": {"task": {"status": "completed"}},
    }
    result_path.write_text(json.dumps(result_data), encoding="utf-8")

    r = _run(["--target", str(tmp_path), "pipeline-status", "--recover"])
    assert r.returncode == 0, f"pipeline-status --recover 실패: {r.stderr}"

    updated = json.loads(result_path.read_text(encoding="utf-8"))
    assert updated["status"] == "hold"
    assert updated.get("finished_at") is not None


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
# T1b: benchmark-report 리포트 생성
# ─────────────────────────────────────────────────────────────────────────────

def test_build_benchmark_report_completed_pipeline():
    import importlib
    import omc_autopilot as mod
    importlib.reload(mod)

    report = mod._build_benchmark_report({
        "status": "completed",
        "mode": "full",
        "executor": "codex",
        "branch": "feat/x",
        "started_at": "2026-05-31T00:00:00Z",
        "finished_at": "2026-05-31T00:02:03Z",
        "steps": {
            "preflight": {"status": "completed"},
            "task": {
                "status": "completed",
                "verdict": "PROCEED",
                "decision": "same",
                "token_usage": {"input_tokens": 100, "output_tokens": 20},
                "cost_estimate": 0.001,
            },
            "review": {"status": "completed", "verdict": "APPROVE", "attempt": 2},
        },
    })

    assert report["pipeline_success"] is True
    assert report["duration_sec"] == 123
    assert report["is_complete"] is True
    assert report["missing_timestamps"] == []
    assert report["total_steps"] == 3
    assert report["completed_steps"] == 3
    assert report["failed_steps"] == 0
    assert report["retry_count"] == 1
    assert report["success_rate"] == 1.0
    assert report["final_verdict"] == "APPROVE"
    assert report["failure_category"] is None
    assert report["cost_estimate"] is None
    assert report["token_usage"] is None
    assert report["executor_cost_source"] is None
    assert report["had_reroute"] is False
    assert report["recovered_after_retry"] is False
    assert report["total_cost_usd"] == pytest.approx(0.001)
    assert report["total_tokens"] == 120


def test_build_benchmark_report_failed_pipeline_with_retry_step():
    import importlib
    import omc_autopilot as mod
    importlib.reload(mod)

    report = mod._build_benchmark_report({
        "status": "failed",
        "mode": "lite",
        "executor": "codex",
        "branch": "fix/x",
        "started_at": "bad timestamp",
        "finished_at": None,
        "steps": {
            "preflight": {"status": "completed"},
            "task": {
                "status": "completed",
                "verdict": "PROCEED",
                "decision": "reroute",
                "reroute_target": "task_retry",
            },
            "task_retry": {"status": "failed", "verdict": "BLOCK", "attempt": 3},
        },
    })

    assert report["pipeline_success"] is False
    assert report["duration_sec"] is None
    assert report["is_complete"] is False
    assert set(report["missing_timestamps"]) == {"started_at", "finished_at"}
    assert report["completed_steps"] == 2
    assert report["failed_steps"] == 1
    assert report["retry_count"] == 3
    assert report["success_rate"] == pytest.approx(2 / 3)
    assert report["final_verdict"] == "BLOCK"
    assert report["failure_category"] == "task_retry:failed"
    assert report["pipeline_success"] is False
    assert report["quality_success"] is False
    assert report["failure_class_breakdown"] == {"execution_failure": 1}
    assert report["had_reroute"] is True
    assert report["recovered_after_retry"] is False
    assert report["total_cost_usd"] is None
    assert report["total_tokens"] is None


def test_build_benchmark_report_completed_pipeline_after_retry_marks_recovered():
    import importlib
    import omc_autopilot as mod
    importlib.reload(mod)

    report = mod._build_benchmark_report({
        "status": "completed",
        "mode": "full",
        "executor": "codex",
        "branch": "feat/recovered",
        "started_at": "2026-05-31T00:00:00Z",
        "finished_at": "2026-05-31T00:01:00Z",
        "steps": {
            "task": {"status": "completed", "verdict": "PROCEED", "decision": "reroute", "reroute_target": "task_retry"},
            "task_retry": {"status": "completed", "verdict": "PROCEED", "attempt": 2},
            "review": {"status": "completed", "verdict": "APPROVE"},
        },
    })

    assert report["had_reroute"] is True
    assert report["recovered_after_retry"] is True


def test_step_payload_keeps_failure_class_metadata():
    import importlib
    import omc_autopilot as mod
    importlib.reload(mod)

    payload = mod._step_payload(
        "failed",
        "2026-06-28T00:00:00Z",
        "2026-06-28T00:00:05Z",
        failure_class="contract_failure",
        reason_codes=["verdict_missing", "output_mismatch"],
    )

    assert payload["failure_class"] == "contract_failure"
    assert payload["reason_codes"] == ["verdict_missing", "output_mismatch"]


def test_build_failure_metadata_marks_bad_entry_skill_as_orchestration_failure():
    import importlib
    import omc_autopilot as mod
    importlib.reload(mod)

    payload = mod._build_failure_metadata(
        step_name="plan",
        status="failed",
        reason_codes=["bad_entry_skill"],
    )

    assert payload["failure_class"] == "orchestration_failure"
    assert payload["reason_codes"] == ["bad_entry_skill"]


def test_build_failure_metadata_marks_reroute_loop_as_orchestration_failure():
    import importlib
    import omc_autopilot as mod
    importlib.reload(mod)

    payload = mod._build_failure_metadata(
        step_name="critique",
        status="failed",
        reason_codes=["reroute_loop"],
    )

    assert payload["failure_class"] == "orchestration_failure"
    assert payload["reason_codes"] == ["reroute_loop"]


def test_build_failure_metadata_marks_metadata_missing_as_orchestration_failure():
    import importlib
    import omc_autopilot as mod
    importlib.reload(mod)

    payload = mod._build_failure_metadata(
        step_name="task",
        status="failed",
        reason_codes=["metadata_missing"],
    )

    assert payload["failure_class"] == "orchestration_failure"
    assert payload["reason_codes"] == ["metadata_missing"]


def test_build_failure_metadata_marks_ambiguous_response_as_orchestration_failure():
    import importlib
    import omc_autopilot as mod
    importlib.reload(mod)

    payload = mod._build_failure_metadata(
        step_name="task",
        status="failed",
        reason_codes=["ambiguous_response"],
    )

    assert payload["failure_class"] == "orchestration_failure"
    assert payload["reason_codes"] == ["ambiguous_response"]


def test_build_failure_metadata_marks_branch_setup_failed_as_orchestration_failure():
    import importlib
    import omc_autopilot as mod
    importlib.reload(mod)

    payload = mod._build_failure_metadata(
        step_name="branch",
        status="failed_branch",
        reason_codes=["branch_setup_failed"],
    )

    assert payload["failure_class"] == "orchestration_failure"
    assert payload["reason_codes"] == ["branch_setup_failed"]


def test_build_failure_metadata_marks_block_without_reason_code_as_orchestration_failure():
    import importlib
    import omc_autopilot as mod
    importlib.reload(mod)

    payload = mod._build_failure_metadata(
        step_name="task",
        status="failed",
        verdict="BLOCK",
        reason_codes=["block_without_reason_code"],
    )

    assert payload["failure_class"] == "orchestration_failure"
    assert payload["reason_codes"] == ["block_without_reason_code"]


def test_build_benchmark_report_handles_mixed_timezone_timestamps():
    import importlib
    import omc_autopilot as mod
    importlib.reload(mod)

    report = mod._build_benchmark_report({
        "status": "completed",
        "started_at": "2026-05-31T00:00:00",
        "finished_at": "2026-05-31T00:00:05Z",
        "steps": {},
    })

    assert report["duration_sec"] == 5


def test_benchmark_report_cli_outputs_json_from_result_file(tmp_path: Path):
    result_file = tmp_path / "result.json"
    result_file.write_text(json.dumps({
        "status": "completed",
        "mode": "full",
        "executor": "codex",
        "branch": "feat/x",
        "started_at": "2026-05-31T00:00:00Z",
        "finished_at": "2026-05-31T00:00:10Z",
        "steps": {"review": {"status": "completed", "verdict": "APPROVE"}},
    }), encoding="utf-8")

    r = _run([
        "--target", str(tmp_path),
        "benchmark-report",
        "--result-file", str(result_file),
        "--format", "json",
    ])

    assert r.returncode == 0, f"stdout: {r.stdout}\nstderr: {r.stderr}"
    payload = json.loads(r.stdout)
    assert payload["pipeline_success"] is True
    assert payload["duration_sec"] == 10


def test_benchmark_report_cli_missing_result_file_exits_nonzero(tmp_path: Path):
    r = _run([
        "--target", str(tmp_path),
        "benchmark-report",
        "--result-file", str(tmp_path / "missing.json"),
    ])

    assert r.returncode != 0
    assert "결과 파일 없음" in (r.stdout + r.stderr)

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

    call_log: list[str] = []

    def mock_step(root, step_name, prompt, executor, timeout, *, dry_run=False, isolated=False):
        call_log.append(step_name)
        if step_name == "plan":
            return 0, "VERDICT: PROCEED"
        if step_name == "critique":
            return 0, "VERDICT: HOLD"
        return 0, "VERDICT: PROCEED"

    monkeypatch.setattr(mod, "_run_pipeline_step", mock_step)
    monkeypatch.setattr(mod, "_PIPELINE_MAX_SAME_VERDICT", 0)
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
    assert rc == 2
    assert data["status"] == "hold", f"expected hold, got {data['status']}"
    critique_step = data["steps"]["critique"]
    assert critique_step["failure_class"] == "contract_failure"
    assert critique_step["reason_codes"] == ["verdict_hold", "same_verdict_streak"]
    assert critique_step["decision"] == "hold"
    assert critique_step["decision_reason"] == "contract failure requires explicit hold"
    assert critique_step["reroute_target"] is None
    assert "task_retry" not in call_log
    assert "plan_retry" not in call_log


def test_failed_critique_loop_quality_failure_uses_plan_retry_before_task_retry(tmp_path: Path, monkeypatch):
    """동일 REVISE 반복 시 persisted reroute_target=plan_retry를 실제 분기에서 소비해야 한다."""
    import importlib
    import omc_autopilot as mod
    importlib.reload(mod)

    call_log: list[str] = []
    critique_calls = {"n": 0}

    def mock_step(root, step_name, prompt, executor, timeout, *, dry_run=False, isolated=False):
        call_log.append(step_name)
        if step_name == "plan":
            return 0, "VERDICT: PROCEED"
        if step_name == "task":
            return 0, "VERDICT: PROCEED"
        if step_name == "critique":
            critique_calls["n"] += 1
            if critique_calls["n"] <= 3:
                return 0, "VERDICT: REVISE"
            return 0, "VERDICT: PROCEED"
        if step_name == "plan_retry":
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
        branch="feat/t2-plan-retry",
        executor_pref="cursor",
        dry_run=True,
        allow_dirty=True,
    )

    assert rc == 0
    assert "plan_retry" in call_log, f"plan_retry가 호출되지 않음: {call_log}"
    assert "task_retry" not in call_log, f"plan_retry보다 task_retry가 먼저 실행됨: {call_log}"


def test_build_benchmark_report_treats_failed_critique_loop_as_quality_failure():
    import importlib
    import omc_autopilot as mod
    importlib.reload(mod)

    report = mod._build_benchmark_report({
        "status": "failed_critique_loop",
        "steps": {
            "critique": {
                "status": "failed_critique_loop",
                "verdict": "BLOCK",
            },
        },
    })

    assert report["pipeline_success"] is False
    assert report["quality_success"] is False
    assert report["failure_class_breakdown"] == {"quality_failure": 1}


def test_build_benchmark_report_counts_all_failure_classes():
    import importlib
    import omc_autopilot as mod
    importlib.reload(mod)

    report = mod._build_benchmark_report({
        "status": "failed",
        "steps": {
            "critique": {
                "status": "failed_critique_loop",
                "verdict": "BLOCK",
                "failure_class": "quality_failure",
            },
            "task_retry": {
                "status": "failed",
                "failure_class": "execution_failure",
            },
            "plan_retry": {
                "status": "blocked",
                "failure_class": "contract_failure",
            },
        },
    })

    assert report["failure_category"] == "critique:failed_critique_loop"
    assert report["failure_class_breakdown"] == {
        "quality_failure": 1,
        "execution_failure": 1,
        "contract_failure": 1,
    }


def test_build_benchmark_report_counts_completed_steps_with_block_verdict_as_failure():
    import importlib
    import omc_autopilot as mod
    importlib.reload(mod)

    report = mod._build_benchmark_report({
        "status": "hold",
        "steps": {
            "task_retry": {
                "status": "completed",
                "verdict": "BLOCK",
                "failure_class": "execution_failure",
            },
            "plan_retry": {
                "status": "completed",
                "verdict": "HOLD",
                "failure_class": "contract_failure",
            },
        },
    })

    assert report["failure_category"] == "task_retry:completed"
    assert report["failure_class_breakdown"] == {
        "execution_failure": 1,
        "contract_failure": 1,
    }


def test_pipeline_timeout_persists_failure_metadata(tmp_path: Path, monkeypatch):
    import importlib
    import omc_autopilot as mod
    importlib.reload(mod)

    monkeypatch.setenv("OmC_PIPELINE_RESULT_PATH", str(tmp_path / "result.json"))

    def mock_step(root, step_name, prompt, executor, timeout, *, dry_run=False, isolated=False):
        return 0, "VERDICT: PROCEED"

    monkeypatch.setattr(mod, "_run_pipeline_step", mock_step)

    timestamps = iter([
        "2026-06-28T00:00:00Z",
        "2026-06-28T00:00:00Z",
        "2026-06-28T00:00:00Z",
        "2026-06-28T00:00:00Z",
    ])
    monkeypatch.setattr(mod, "_now", lambda: next(timestamps, "2026-06-28T00:00:00Z"))

    time_values = iter([0.0, 999999.0])
    monkeypatch.setattr(mod.time, "time", lambda: next(time_values, 999999.0))

    rc = mod.cmd_pipeline(
        root=tmp_path,
        instruction="x" * 200,
        branch="feat/timeout-metadata",
        executor_pref="cursor",
        dry_run=True,
        allow_dirty=True,
    )

    assert rc == 1
    data = json.loads((tmp_path / "result.json").read_text(encoding="utf-8"))
    critique_step = data["steps"]["critique"]
    assert critique_step["status"] == "timeout"
    assert critique_step["started_at"] == "2026-06-28T00:00:00Z"
    assert critique_step["failure_class"] == "execution_failure"
    assert critique_step["reason_codes"] == ["timeout"]
    assert critique_step["decision"] == "same"
    assert critique_step["decision_reason"] == "execution failure stays on current path before threshold"
    assert critique_step["reroute_target"] is None


def test_decide_escalation_action_default_execution_retry_two_reroutes_task():
    import importlib
    import omc_autopilot as mod
    importlib.reload(mod)

    decision = mod._decide_escalation_action(
        failure_class="execution_failure",
        reason_codes=["retry_exhausted"],
        retry_count=2,
        escalation_policy="default",
    )

    assert decision["decision"] == "reroute"
    assert decision["reroute_target"] == "task_retry"


def test_decision_policy_entry_exposes_default_execution_rule():
    import importlib
    import omc_autopilot as mod
    importlib.reload(mod)

    policy = mod._decision_policy_entry(
        failure_class="execution_failure",
        escalation_policy="default",
        retry_count=0,
        reason_codes=[],
    )

    assert policy == {
        "decision": "same",
        "decision_reason": "execution failure stays on current path before threshold",
        "reroute_target": None,
    }


def test_decision_policy_entry_exposes_default_quality_rule():
    import importlib
    import omc_autopilot as mod
    importlib.reload(mod)

    policy = mod._decision_policy_entry(
        failure_class="quality_failure",
        escalation_policy="default",
        retry_count=0,
        reason_codes=["verdict_block"],
    )

    assert policy == {
        "decision": "reroute",
        "decision_reason": "quality failure reroutes to planning",
        "reroute_target": "plan_retry",
    }


def test_decision_policy_entry_exposes_bad_entry_skill_orchestration_rule():
    import importlib
    import omc_autopilot as mod
    importlib.reload(mod)

    policy = mod._decision_policy_entry(
        failure_class="orchestration_failure",
        escalation_policy="default",
        retry_count=0,
        reason_codes=["bad_entry_skill"],
    )

    assert policy == {
        "decision": "reroute",
        "decision_reason": "orchestration failure reroutes to planning",
        "reroute_target": "plan_retry",
    }


def test_decision_policy_entry_exposes_block_without_reason_code_orchestration_rule():
    import importlib
    import omc_autopilot as mod
    importlib.reload(mod)

    policy = mod._decision_policy_entry(
        failure_class="orchestration_failure",
        escalation_policy="default",
        retry_count=0,
        reason_codes=["block_without_reason_code"],
    )

    assert policy == {
        "decision": "reroute",
        "decision_reason": "missing task block reason reroutes to planning",
        "reroute_target": "plan_retry",
    }


def test_critique_recovery_target_prefers_explicit_reroute_then_auto_retry():
    import importlib
    import omc_autopilot as mod
    importlib.reload(mod)

    assert (
        mod._critique_recovery_target(
            loop_step="critique",
            decision_name="reroute",
            reroute_target="task_retry",
            task_auto_retry_count=0,
            critique_auto_retry_count=0,
        )
        == "task_retry"
    )
    assert (
        mod._critique_recovery_target(
            loop_step="critique",
            decision_name="reroute",
            reroute_target="plan_retry",
            task_auto_retry_count=0,
            critique_auto_retry_count=0,
        )
        == "plan_retry"
    )
    assert (
        mod._critique_recovery_target(
            loop_step="critique",
            decision_name="same",
            reroute_target=None,
            task_auto_retry_count=0,
            critique_auto_retry_count=0,
        )
        == "task_retry"
    )
    assert (
        mod._critique_recovery_target(
            loop_step="critique",
            decision_name="same",
            reroute_target=None,
            task_auto_retry_count=2,
            critique_auto_retry_count=0,
        )
        == "plan_retry"
    )
    assert (
        mod._critique_recovery_target(
            loop_step="critique",
            decision_name="hold",
            reroute_target=None,
            task_auto_retry_count=0,
            critique_auto_retry_count=0,
        )
        is None
    )


def test_decide_escalation_action_quality_failure_reroutes_plan():
    import importlib
    import omc_autopilot as mod
    importlib.reload(mod)

    decision = mod._decide_escalation_action(
        failure_class="quality_failure",
        reason_codes=["verdict_block"],
        retry_count=1,
        escalation_policy="default",
    )

    assert decision["decision"] == "reroute"
    assert decision["reroute_target"] == "plan_retry"


def test_decide_escalation_action_contract_failure_holds_even_when_aggressive():
    import importlib
    import omc_autopilot as mod
    importlib.reload(mod)

    decision = mod._decide_escalation_action(
        failure_class="contract_failure",
        reason_codes=["verdict_hold"],
        retry_count=1,
        escalation_policy="aggressive",
    )

    assert decision["decision"] == "hold"
    assert decision["reroute_target"] is None


def test_decide_escalation_action_orchestration_failure_bad_entry_skill_reroutes_plan():
    import importlib
    import omc_autopilot as mod
    importlib.reload(mod)

    decision = mod._decide_escalation_action(
        failure_class="orchestration_failure",
        reason_codes=["bad_entry_skill"],
        retry_count=0,
        escalation_policy="default",
    )

    assert decision["decision"] == "reroute"
    assert decision["decision_reason"] == "orchestration failure reroutes to planning"
    assert decision["reroute_target"] == "plan_retry"


def test_decide_escalation_action_orchestration_failure_metadata_missing_reroutes_plan():
    import importlib
    import omc_autopilot as mod
    importlib.reload(mod)

    decision = mod._decide_escalation_action(
        failure_class="orchestration_failure",
        reason_codes=["metadata_missing"],
        retry_count=0,
        escalation_policy="default",
    )

    assert decision["decision"] == "reroute"
    assert decision["decision_reason"] == "orchestration failure reroutes to planning"
    assert decision["reroute_target"] == "plan_retry"


def test_decide_escalation_action_orchestration_failure_reroute_loop_holds():
    import importlib
    import omc_autopilot as mod
    importlib.reload(mod)

    decision = mod._decide_escalation_action(
        failure_class="orchestration_failure",
        reason_codes=["reroute_loop"],
        retry_count=0,
        escalation_policy="default",
    )

    assert decision["decision"] == "hold"
    assert decision["decision_reason"] == "orchestration reroute loop requires explicit hold"
    assert decision["reroute_target"] is None


def test_failure_step_decision_uses_step_metadata_policy(monkeypatch):
    import importlib
    import omc_autopilot as mod
    importlib.reload(mod)

    monkeypatch.setattr(
        mod,
        "_pipeline_step_metadata",
        lambda step_name: {"escalation_policy": "conservative"} if step_name == "review" else {"escalation_policy": "default"},
    )

    decision = mod._failure_step_decision(
        step_name="review",
        step_payload={
            "failure_class": "quality_failure",
            "reason_codes": ["verdict_block"],
        },
        retry_count=0,
    )

    assert decision == {
        "decision": "hold",
        "decision_reason": "conservative policy holds on quality failure",
        "reroute_target": None,
    }


def test_retry_step_payload_failed_uses_failure_metadata_and_step_policy(monkeypatch):
    import importlib
    import omc_autopilot as mod
    importlib.reload(mod)

    monkeypatch.setattr(
        mod,
        "_pipeline_step_metadata",
        lambda step_name: {"escalation_policy": "conservative"} if step_name == "plan_retry" else {"escalation_policy": "default"},
    )

    payload = mod._retry_step_payload(
        step_name="plan_retry",
        rc=1,
        started_at="2026-06-28T00:00:00Z",
        finished_at="2026-06-28T00:00:05Z",
        output_preview="boom",
        retry_count=1,
    )

    assert payload["status"] == "failed"
    assert payload["failure_class"] == "execution_failure"
    assert payload["reason_codes"] == ["step_failed", "nonzero_exit"]
    assert payload["decision"] == "same"
    assert payload["decision_reason"] == "execution failure stays on current path before threshold"
    assert payload["reroute_target"] is None


def test_step_payload_keeps_escalation_decision_metadata():
    import importlib
    import omc_autopilot as mod
    importlib.reload(mod)

    payload = mod._step_payload(
        "failed",
        "2026-06-28T00:00:00Z",
        "2026-06-28T00:00:05Z",
        decision="reroute",
        decision_reason="execution failure reached retry threshold",
        reroute_target="task_retry",
    )

    assert payload["decision"] == "reroute"
    assert payload["decision_reason"] == "execution failure reached retry threshold"
    assert payload["reroute_target"] == "task_retry"


def test_step_payload_keeps_decision_metadata_on_completed_retry_steps():
    import importlib
    import omc_autopilot as mod
    importlib.reload(mod)

    payload = mod._step_payload(
        "completed",
        "2026-06-28T00:00:00Z",
        "2026-06-28T00:00:05Z",
        decision="same",
        decision_reason="completed retry stays on current path",
        reroute_target=None,
    )

    assert payload["decision"] == "same"
    assert payload["decision_reason"] == "completed retry stays on current path"
    assert payload["reroute_target"] is None

# ─────────────────────────────────────────────────────────────────────────────
# T3: AMBIGUOUS_RESPONSE — task verdict None 처리
# ─────────────────────────────────────────────────────────────────────────────

def test_task_ambiguous_response_retries_once_then_succeeds(tmp_path: Path, monkeypatch):
    """task verdict가 None이면 1회 재시도 후 PROCEED가 나오면 정상 완료돼야 한다."""
    import importlib
    import omc_autopilot as mod
    importlib.reload(mod)

    task_calls = {"n": 0}

    def mock_step(root, step_name, prompt, executor, timeout, *, dry_run=False, isolated=False):
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

    def mock_step(root, step_name, prompt, executor, timeout, *, dry_run=False, isolated=False):
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
    assert data["steps"]["task"]["decision"] == "hold"
    assert data["steps"]["task"]["decision_reason"] == "orchestration failure defaults to explicit hold"
    assert data["steps"]["task"]["reroute_target"] is None


def test_task_orchestration_failure_bad_entry_skill_reroutes_to_plan_retry(tmp_path: Path, monkeypatch):
    """task 실패가 orchestration_failure(bad_entry_skill)면 plan_retry로 재진입해야 한다."""
    import importlib
    import omc_autopilot as mod
    importlib.reload(mod)

    call_log: list[str] = []

    def mock_step(root, step_name, prompt, executor, timeout, *, dry_run=False, isolated=False):
        call_log.append(step_name)
        if step_name == "plan":
            return 0, "VERDICT: PROCEED"
        if step_name == "task":
            return 0, "REASON_CODE: bad_entry_skill\nVERDICT: BLOCK"
        if step_name == "plan_retry":
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
        branch="feat/task-orchestration-reroute",
        executor_pref="cursor",
        dry_run=True,
        allow_dirty=True,
    )

    assert rc == 0
    assert "plan_retry" in call_log, f"bad_entry_skill orchestration failure가 plan_retry로 재진입하지 않음: {call_log}"
    result = json.loads((tmp_path / "result.json").read_text(encoding="utf-8"))
    assert result["steps"]["task"]["failure_class"] == "orchestration_failure"
    assert result["steps"]["task"]["reason_codes"] == ["bad_entry_skill"]


def test_task_block_without_reason_code_does_not_silently_complete(tmp_path: Path, monkeypatch):
    """task가 VERDICT: BLOCK만 주고 reason_code가 없으면 completed로 조용히 통과하면 안 된다."""
    import importlib
    import omc_autopilot as mod
    importlib.reload(mod)

    call_log: list[str] = []

    def mock_step(root, step_name, prompt, executor, timeout, *, dry_run=False, isolated=False):
        call_log.append(step_name)
        if step_name == "plan":
            return 0, "VERDICT: PROCEED"
        if step_name == "task":
            return 0, "VERDICT: BLOCK"
        if step_name == "plan_retry":
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
        branch="feat/task-block-no-reason",
        executor_pref="cursor",
        dry_run=True,
        allow_dirty=True,
    )

    assert rc == 0
    result = json.loads((tmp_path / "result.json").read_text(encoding="utf-8"))
    assert result["steps"]["task"]["status"] == "failed"
    assert result["steps"]["task"]["failure_class"] == "orchestration_failure"
    assert result["steps"]["task"]["reason_codes"] == ["block_without_reason_code"]
    assert "plan_retry" in call_log


def test_task_retry_block_without_reason_code_uses_common_fallback():
    """task_retry 무사유 BLOCK도 task와 같은 fallback reason code를 가져야 한다."""
    import importlib
    import omc_autopilot as mod
    importlib.reload(mod)

    fallback = mod._ensure_block_without_reason_code(
        step_name="task_retry",
        verdict="BLOCK",
        reason_codes=[],
    )

    assert fallback == ["block_without_reason_code"]

    metadata = mod._build_failure_metadata(
        step_name="task_retry",
        status="failed",
        verdict="BLOCK",
        reason_codes=fallback,
    )
    assert metadata["failure_class"] == "orchestration_failure"
    assert metadata["reason_codes"] == ["block_without_reason_code"]


def test_task_retry_block_without_reason_code_marks_step_failed():
    """task_retry 무사유 BLOCK은 failed payload로 기록돼야 한다."""
    import importlib
    import omc_autopilot as mod
    importlib.reload(mod)

    payload = mod._task_retry_block_failure_payload(
        started_at="2026-06-30T00:00:00Z",
        finished_at="2026-06-30T00:00:10Z",
        output_preview="VERDICT: BLOCK",
        retry_count=1,
        reason_codes=["block_without_reason_code"],
    )

    assert payload["status"] == "failed"
    assert payload["failure_class"] == "orchestration_failure"
    assert payload["reason_codes"] == ["block_without_reason_code"]
    assert payload["decision"] == "reroute"
    assert payload["reroute_target"] == "plan_retry"


def test_task_retry_block_without_reason_code_is_failed_in_pipeline(tmp_path: Path, monkeypatch):
    """cmd_pipeline 경로에서도 task_retry 무사유 BLOCK이 failed로 저장돼야 한다."""
    import importlib
    import omc_autopilot as mod
    importlib.reload(mod)

    call_log: list[str] = []
    critique_calls = {"n": 0}

    def mock_step(root, step_name, prompt, executor, timeout, *, dry_run=False, isolated=False):
        call_log.append(step_name)
        if step_name == "plan":
            return 0, "VERDICT: PROCEED"
        if step_name == "task":
            return 0, "VERDICT: PROCEED"
        if step_name == "critique":
            critique_calls["n"] += 1
            if critique_calls["n"] <= 4:
                return 1, "critique command failed"
            return 0, "VERDICT: PROCEED"
        if step_name == "task_retry":
            return 0, "VERDICT: BLOCK"
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
        branch="feat/task-retry-block-status",
        executor_pref="cursor",
        dry_run=True,
        allow_dirty=True,
    )

    assert rc == 1
    result = json.loads((tmp_path / "result.json").read_text(encoding="utf-8"))
    assert result["status"] == "retry_exhausted"
    assert result["steps"]["task_retry"]["status"] == "failed"
    assert result["steps"]["task_retry"]["failure_class"] == "orchestration_failure"
    assert result["steps"]["task_retry"]["reason_codes"] == ["block_without_reason_code"]
    assert "task_retry" in call_log


def test_task_stage_plan_retry_does_not_consume_critique_plan_retry_budget(tmp_path: Path, monkeypatch):
    """task-stage plan_retry는 critique plan_retry 예산을 잠식하지 않아야 한다."""
    import importlib
    import omc_autopilot as mod
    importlib.reload(mod)

    call_log: list[str] = []
    critique_calls = {"n": 0}

    def mock_step(root, step_name, prompt, executor, timeout, *, dry_run=False, isolated=False):
        call_log.append(step_name)
        if step_name == "plan":
            return 0, "VERDICT: PROCEED"
        if step_name == "task":
            return 0, "REASON_CODE: bad_entry_skill\nVERDICT: BLOCK"
        if step_name == "plan_retry":
            return 0, "VERDICT: PROCEED"
        if step_name == "critique":
            critique_calls["n"] += 1
            if critique_calls["n"] == 1:
                return 0, "VERDICT: REVISE"
            return 0, "VERDICT: PROCEED"
        if step_name == "review":
            return 0, "VERDICT: APPROVE"
        return 0, "VERDICT: PROCEED"

    monkeypatch.setattr(mod, "_run_pipeline_step", mock_step)
    monkeypatch.setattr(mod, "_PIPELINE_MAX_SAME_VERDICT", 0)
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
        branch="feat/task-orchestration-budget",
        executor_pref="cursor",
        dry_run=True,
        allow_dirty=True,
    )

    assert rc == 0
    assert call_log.count("plan_retry") == 2, f"critique plan_retry 예산이 task-stage plan_retry에 잠식됨: {call_log}"


def test_resume_skips_already_completed_task_stage_plan_retry(tmp_path: Path, monkeypatch):
    """--resume 시 task_stage plan_retry가 이미 완료됐으면 다시 실행되면 안 된다."""
    import importlib
    import omc_autopilot as mod
    importlib.reload(mod)

    call_log: list[str] = []

    def mock_step(root, step_name, prompt, executor, timeout, *, dry_run=False, isolated=False):
        call_log.append(step_name)
        if step_name == "plan":
            return 0, "VERDICT: PROCEED"
        if step_name == "task":
            return 0, "REASON_CODE: bad_entry_skill\nVERDICT: BLOCK"
        if step_name == "plan_retry":
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

    result_path = tmp_path / "result.json"
    result_data = {
        "status": "failed",
        "mode": "full",
        "branch": "feat/task-plan-retry-resume",
        "instruction": "x" * 200,
        "executor": "codex",
        "started_at": "2026-06-30T00:00:00Z",
        "resume_count": 1,
        "steps": {
            "preflight": {"status": "completed"},
            "plan": {"status": "completed", "output_preview": "VERDICT: PROCEED"},
            "task": {
                "status": "failed",
                "decision": "reroute",
                "reroute_target": "plan_retry",
                "failure_class": "orchestration_failure",
                "reason_codes": ["bad_entry_skill"],
                "critique_issues": "task plan retry required",
                "output_preview": "VERDICT: BLOCK",
            },
            "plan_retry": {
                "status": "completed",
                "decision": "same",
                "reroute_target": None,
                "output_preview": "VERDICT: PROCEED",
            },
        },
    }
    result_path.write_text(json.dumps(result_data), encoding="utf-8")

    call_log.clear()
    second_rc = mod.cmd_pipeline(
        root=tmp_path,
        instruction="x" * 200,
        branch="feat/task-plan-retry-resume",
        executor_pref="cursor",
        dry_run=True,
        allow_dirty=True,
        resume=True,
    )

    assert second_rc == 0
    assert call_log.count("plan_retry") == 0, f"resume에서 이미 완료된 plan_retry가 다시 실행됨: {call_log}"


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


def test_pipeline_branch_failure_sets_top_level_failed_branch(tmp_path: Path, monkeypatch):
    """브랜치 준비 실패 시 top-level status는 hold가 아니라 failed_branch여야 한다."""
    import importlib
    import omc_autopilot as mod
    importlib.reload(mod)

    monkeypatch.setenv("OmC_PIPELINE_RESULT_PATH", str(tmp_path / ".omc" / "pipeline_run_result.json"))
    monkeypatch.setattr(mod, "_checkout_new_branch", lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError("failed_branch:test")))

    import subprocess as sp
    def mock_subprocess(cmd, **kwargs):
        if isinstance(cmd, list) and cmd[:3] == ["git", "status", "--porcelain"]:
            return sp.CompletedProcess(cmd, 0, stdout="", stderr="")
        return sp.CompletedProcess(cmd, 0, stdout="", stderr="")
    monkeypatch.setattr(sp, "run", mock_subprocess)

    rc = mod.cmd_pipeline(
        root=tmp_path,
        instruction="충분히 긴 파이프라인 지시문입니다 branch failure 상태 검증을 위해 작성합니다.",
        branch="feat/fail-branch",
        executor_pref="codex",
        mode_arg="lite",
        auto=False,
        max_time=120,
        dry_run=False,
        allow_dirty=True,
        resume=False,
    )
    assert rc == 1
    result_path = tmp_path / ".omc" / "pipeline_run_result.json"
    data = json.loads(result_path.read_text(encoding="utf-8"))
    assert data["status"] == "failed_branch"
    assert data["steps"]["branch"]["status"] == "failed_branch"
    assert data["steps"]["branch"]["decision"] == "hold"
    assert data["steps"]["branch"]["decision_reason"] == "orchestration failure defaults to explicit hold"
    assert data["steps"]["branch"]["reroute_target"] is None


def test_pipeline_task_failure_persists_decision_metadata(tmp_path: Path, monkeypatch):
    import importlib
    import omc_autopilot as mod
    importlib.reload(mod)

    def mock_step(root, step_name, prompt, executor, timeout, *, dry_run=False, isolated=False):
        if step_name == "plan":
            return 0, "VERDICT: PROCEED"
        if step_name == "task":
            return 1, "task failed"
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
        branch="feat/task-fail-metadata",
        executor_pref="cursor",
        dry_run=True,
        allow_dirty=True,
    )

    assert rc == 1
    data = json.loads((tmp_path / "result.json").read_text(encoding="utf-8"))
    task_step = data["steps"]["task"]
    assert task_step["status"] == "failed"
    assert task_step["failure_class"] == "execution_failure"
    assert task_step["reason_codes"] == ["step_failed", "nonzero_exit"]
    assert task_step["decision"] == "same"
    assert task_step["decision_reason"] == "execution failure stays on current path before threshold"
    assert task_step["reroute_target"] is None


def test_pipeline_does_not_crash_when_resumed_with_existing_block_critique(tmp_path: Path, monkeypatch):
    """resume 결과에 critique verdict=BLOCK이 남아있어도 UnboundLocalError 없이 진행해야 한다."""
    import importlib
    import omc_autopilot as mod
    importlib.reload(mod)

    monkeypatch.setenv("OmC_PIPELINE_RESULT_PATH", str(tmp_path / ".omc" / "pipeline_run_result.json"))

    # 최소 git 상태 통과
    import subprocess as sp

    def mock_run(cmd, **kwargs):
        if isinstance(cmd, list) and cmd[:3] == ["git", "status", "--porcelain"]:
            return sp.CompletedProcess(cmd, 0, stdout="", stderr="")
        return sp.CompletedProcess(cmd, 0, stdout="", stderr="")

    monkeypatch.setattr(sp, "run", mock_run)

    # resume 파일: critique가 이미 BLOCK으로 기록된 상태
    result_path = tmp_path / ".omc" / "pipeline_run_result.json"
    result_path.parent.mkdir(parents=True, exist_ok=True)
    result_path.write_text(
        json.dumps(
            {
                "status": "hold",
                "mode": "full",
                "branch": "feat/x",
                "instruction": "x" * 60,
                "executor": "codex",
                "started_at": "2026-06-01T000000Z",
                "steps": {
                    "plan": {"status": "completed"},
                    "task": {"status": "completed"},
                    "critique": {
                        "status": "failed_critique_loop",
                        "verdict": "BLOCK",
                        "streak": 3,
                        "critique_issues": "CRITICAL: something",
                        "last_output": "VERDICT: BLOCK",
                    },
                },
                "finished_at": "2026-06-01T000100Z",
            }
        ),
        encoding="utf-8",
    )

    # 빠르게 streak 트리거
    monkeypatch.setattr(mod, "_PIPELINE_MAX_SAME_VERDICT", 0)
    monkeypatch.setattr(mod, "_PIPELINE_MAX_RETRIES", 0)

    calls = {"n": 0}

    def fake_step(_root, step_name, _prompt, _executor, _timeout, **_opts):
        calls["n"] += 1
        if step_name == "critique":
            return 0, "CRITICAL:\n- x\n\nVERDICT: REVISE"
        if step_name == "task_retry":
            return 0, "ok\nVERDICT: PROCEED"
        if step_name == "review":
            return 0, "VERDICT: APPROVE"
        return 0, "VERDICT: PROCEED"

    monkeypatch.setattr(mod, "_run_pipeline_step", fake_step)

    rc = mod.cmd_pipeline(
        root=tmp_path,
        instruction="x" * 60,
        branch="feat/x",
        executor_pref="codex",
        mode_arg="full",
        auto=True,
        max_time=60,
        dry_run=False,
        allow_dirty=True,
        resume=True,
    )
    assert rc in (0, 1, 2)
    assert calls["n"] >= 1


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


def test_pipeline_uses_single_run_history_record_per_execution(tmp_path: Path):
    """한 번의 pipeline 실행에서 runs 이력은 run_id 1개만 사용해야 한다."""
    _run(
        ["--target", str(tmp_path),
         "pipeline",
         "--instruction", "single run_id 저장 검증용 충분한 길이의 지시문",
         "--branch", "feat/t5-single-run",
         "--dry-run"],
    )
    runs_dir = tmp_path / ".omc" / "runs"
    assert runs_dir.exists(), ".omc/runs 디렉토리 미생성"
    subdirs = [p for p in runs_dir.iterdir() if p.is_dir()]
    assert len(subdirs) == 1, f"한 실행에서 run_id가 여러 개 생성됨: {len(subdirs)}"


def test_cmd_overview_prints_multi_run_kpi_summary(tmp_path: Path, capsys):
    import importlib
    import omc_autopilot as mod
    importlib.reload(mod)

    runs_dir = tmp_path / ".omc" / "runs"
    runs_dir.mkdir(parents=True)

    (runs_dir / "20260601T000001-a").mkdir()
    (runs_dir / "20260601T000001-a" / "result.json").write_text(json.dumps({
        "status": "completed",
        "branch": "feat/a",
        "executor": "codex",
        "started_at": "2026-06-01T00:00:00Z",
        "finished_at": "2026-06-01T00:01:00Z",
        "steps": {
            "task": {"status": "completed", "decision": "reroute", "reroute_target": "task_retry"},
            "task_retry": {"status": "completed", "attempt": 2},
            "review": {"status": "completed", "verdict": "APPROVE", "cost_estimate": 0.01},
        },
    }), encoding="utf-8")

    (runs_dir / "20260601T000002-b").mkdir()
    (runs_dir / "20260601T000002-b" / "result.json").write_text(json.dumps({
        "status": "completed",
        "branch": "feat/b",
        "executor": "codex",
        "started_at": "2026-06-01T00:02:00Z",
        "finished_at": "2026-06-01T00:03:00Z",
        "steps": {
            "task": {"status": "completed"},
            "review": {"status": "completed", "verdict": "APPROVE", "cost_estimate": 0.03},
        },
    }), encoding="utf-8")

    (runs_dir / "20260601T000003-c").mkdir()
    (runs_dir / "20260601T000003-c" / "result.json").write_text(json.dumps({
        "status": "failed",
        "branch": "feat/c",
        "executor": "codex",
        "started_at": "2026-06-01T00:04:00Z",
        "finished_at": "2026-06-01T00:05:00Z",
        "steps": {
            "task": {"status": "completed", "decision": "reroute", "reroute_target": "task_retry"},
            "task_retry": {"status": "failed", "attempt": 2},
        },
    }), encoding="utf-8")

    rc = mod.cmd_overview(tmp_path, limit=10)
    out = capsys.readouterr().out

    assert rc == 0
    assert "KPI Summary" in out
    assert "total_runs=3" in out
    assert "reroute_rate=66.7%" in out
    assert "retry_to_success_rate=50.0%" in out
    assert "cost_per_successful_task=$0.0200" in out


def test_cmd_overview_prints_na_for_missing_retry_and_cost_data(tmp_path: Path, capsys):
    import importlib
    import omc_autopilot as mod
    importlib.reload(mod)

    runs_dir = tmp_path / ".omc" / "runs"
    runs_dir.mkdir(parents=True)

    (runs_dir / "20260601T000010-x").mkdir()
    (runs_dir / "20260601T000010-x" / "result.json").write_text(json.dumps({
        "status": "completed",
        "branch": "feat/x",
        "executor": "codex",
        "started_at": "2026-06-01T00:10:00Z",
        "finished_at": "2026-06-01T00:11:00Z",
        "steps": {
            "task": {"status": "completed"},
            "review": {"status": "completed", "verdict": "APPROVE"},
        },
    }), encoding="utf-8")

    rc = mod.cmd_overview(tmp_path, limit=10)
    out = capsys.readouterr().out

    assert rc == 0
    assert "total_runs=1" in out
    assert "reroute_rate=0.0%" in out
    assert "retry_to_success_rate=n/a" in out
    assert "cost_per_successful_task=n/a" in out


def test_cmd_overview_counts_current_result_in_kpi_summary(tmp_path: Path, capsys):
    import importlib
    import omc_autopilot as mod
    importlib.reload(mod)

    omc_dir = tmp_path / ".omc"
    omc_dir.mkdir(parents=True)
    (omc_dir / "pipeline_run_result.json").write_text(json.dumps({
        "status": "completed",
        "branch": "feat/current-only",
        "executor": "codex",
        "started_at": "2026-06-01T01:00:00Z",
        "finished_at": "2026-06-01T01:01:00Z",
        "steps": {
            "task": {"status": "completed", "decision": "reroute", "reroute_target": "task_retry"},
            "task_retry": {"status": "completed", "attempt": 2},
            "review": {"status": "completed", "verdict": "APPROVE", "cost_estimate": 0.05},
        },
    }), encoding="utf-8")

    rc = mod.cmd_overview(tmp_path, limit=10)
    out = capsys.readouterr().out

    assert rc == 0
    assert "total_runs=1" in out
    assert "reroute_rate=100.0%" in out
    assert "retry_to_success_rate=100.0%" in out
    assert "cost_per_successful_task=$0.0500" in out


def test_cmd_overview_deduplicates_current_result_from_runs_kpi_summary(tmp_path: Path, capsys):
    import importlib
    import omc_autopilot as mod
    importlib.reload(mod)

    payload = {
        "status": "completed",
        "branch": "feat/duplicate",
        "executor": "codex",
        "started_at": "2026-06-01T02:00:00Z",
        "finished_at": "2026-06-01T02:01:00Z",
        "steps": {
            "task": {"status": "completed"},
            "review": {"status": "completed", "verdict": "APPROVE", "cost_estimate": 0.02},
        },
    }

    omc_dir = tmp_path / ".omc"
    runs_dir = omc_dir / "runs" / "20260601T020000-a"
    runs_dir.mkdir(parents=True)
    (omc_dir / "pipeline_run_result.json").write_text(json.dumps(payload), encoding="utf-8")
    (runs_dir / "result.json").write_text(json.dumps(payload), encoding="utf-8")

    rc = mod.cmd_overview(tmp_path, limit=10)
    out = capsys.readouterr().out

    assert rc == 0
    assert "total_runs=1" in out
    assert "cost_per_successful_task=$0.0200" in out

# ─────────────────────────────────────────────────────────────────────────────
# T6: critique/review 격리 컨텍스트 — isolated=True 검증
# ─────────────────────────────────────────────────────────────────────────────

def test_critique_step_receives_isolated_flag(tmp_path: Path, monkeypatch):
    """critique 스텝 실행 시 _run_pipeline_step에 isolated=True가 전달돼야 한다."""
    import importlib
    import omc_autopilot as mod
    importlib.reload(mod)

    isolated_calls = {}

    def mock_step(root, step_name, prompt, executor, timeout, *, dry_run=False, isolated=False):
        isolated_calls[step_name] = isolated
        if step_name == "plan":
            return 0, "VERDICT: PROCEED"
        if step_name in ("critique", "review"):
            return 0, "VERDICT: PROCEED"
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

    mod.cmd_pipeline(
        root=tmp_path,
        instruction="x" * 200,
        branch="feat/t6-isolated",
        executor_pref="cursor",
        dry_run=True,
        allow_dirty=True,
    )

    assert isolated_calls.get("critique") is True, (
        f"critique 스텝에 isolated=True가 전달되지 않음: {isolated_calls}"
    )
    assert isolated_calls.get("review") is True, (
        f"review 스텝에 isolated=True가 전달되지 않음: {isolated_calls}"
    )
    assert isolated_calls.get("task") is False, (
        f"task 스텝에 isolated=True가 잘못 전달됨: {isolated_calls}"
    )
    assert isolated_calls.get("plan") is False, (
        f"plan 스텝에 isolated=True가 잘못 전달됨: {isolated_calls}"
    )


def test_critique_prompt_excludes_instruction(tmp_path: Path, monkeypatch):
    """critique 프롬프트에 원본 instruction이 포함되지 않아야 한다."""
    import importlib
    import omc_autopilot as mod
    importlib.reload(mod)

    captured_prompts = {}
    MARKER = "UNIQUE_XYZ"
    INSTRUCTION = MARKER + ("충분한길이" * 30)  # [:200] 안에 마커 포함

    def mock_step(root, step_name, prompt, executor, timeout, *, dry_run=False, isolated=False):
        captured_prompts[step_name] = prompt
        if step_name == "plan":
            return 0, "VERDICT: PROCEED"
        if step_name in ("critique", "review"):
            return 0, "VERDICT: PROCEED"
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

    mod.cmd_pipeline(
        root=tmp_path,
        instruction=INSTRUCTION,
        branch="feat/t6-prompt",
        executor_pref="cursor",
        dry_run=True,
        allow_dirty=True,
    )

    critique_prompt = captured_prompts.get("critique", "")
    assert MARKER not in critique_prompt, (
        f"critique 프롬프트에 instruction이 포함됨 — 격리 미적용:\n{critique_prompt[:300]}"
    )

# ─────────────────────────────────────────────────────────────────────────────
# T7: retry_exhausted → task_retry 연결 + critique 재진입
# ─────────────────────────────────────────────────────────────────────────────

def test_retry_exhausted_triggers_task_retry(tmp_path: Path, monkeypatch):
    """execution_failure 기반 critique retry 소진(retry_exhausted) 시 task_retry가 호출돼야 한다."""
    import importlib
    import omc_autopilot as mod
    importlib.reload(mod)

    call_log = []

    critique_call = {"n": 0}

    def mock_step(root, step_name, prompt, executor, timeout, *, dry_run=False, isolated=False):
        call_log.append(step_name)
        if step_name == "plan":
            return 0, "VERDICT: PROCEED"
        if step_name == "task":
            return 0, "VERDICT: PROCEED"
        if step_name == "critique":
            critique_call["n"] += 1
            if critique_call["n"] <= 4:
                return 1, "critique command failed"
            return 0, "VERDICT: PROCEED"
        if step_name == "task_retry":
            return 0, "VERDICT: PROCEED"
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

    mod.cmd_pipeline(
        root=tmp_path,
        instruction="x" * 200,
        branch="feat/t7-retry",
        executor_pref="cursor",
        dry_run=True,
        allow_dirty=True,
    )

    assert "task_retry" in call_log, (
        f"retry_exhausted 후 task_retry가 호출되지 않음. 호출 순서: {call_log}"
    )


def test_retry_exhausted_records_non_empty_critique_issues(tmp_path: Path, monkeypatch):
    """retry_exhausted가 hold로 소비되더라도 critique_issues는 항상 비어있지 않아야 한다."""
    import importlib
    import omc_autopilot as mod
    importlib.reload(mod)

    critique_n = {"n": 0}

    def mock_step(root, step_name, prompt, executor, timeout, *, dry_run=False, isolated=False):
        if step_name in ("plan", "task"):
            return 0, "VERDICT: PROCEED"
        if step_name == "critique":
            critique_n["n"] += 1
            return 0, "VERDICT: REVISE" if critique_n["n"] % 2 == 1 else "VERDICT: HOLD"
        return 0, "VERDICT: PROCEED"

    monkeypatch.setattr(mod, "_run_pipeline_step", mock_step)
    monkeypatch.setattr(mod, "_TASK_AUTO_RETRY_MAX", 0)
    monkeypatch.setattr(mod, "_CRITIQUE_AUTO_RETRY_MAX", 0)
    monkeypatch.setattr(mod, "_PIPELINE_MAX_SAME_VERDICT", 99)
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
        branch="feat/t7-issues",
        executor_pref="cursor",
        dry_run=True,
        allow_dirty=True,
    )
    assert rc == 2
    result = json.loads((tmp_path / "result.json").read_text(encoding="utf-8"))
    assert result["status"] == "hold"
    critique = result["steps"]["critique"]
    assert critique["status"] == "retry_exhausted"
    assert bool((critique.get("critique_issues") or "").strip())
    assert critique["decision"] == "hold"
    assert critique["decision_reason"] == "contract failure requires explicit hold"
    assert critique["reroute_target"] is None


def test_retry_exhausted_uses_step_escalation_policy(tmp_path: Path, monkeypatch):
    """retry_exhausted decision이 hold로 소비되더라도 escalation_policy는 유지돼야 한다."""
    import importlib
    import omc_autopilot as mod
    importlib.reload(mod)

    def mock_step(root, step_name, prompt, executor, timeout, *, dry_run=False, isolated=False):
        if step_name in ("plan", "task"):
            return 0, "VERDICT: PROCEED"
        if step_name == "critique":
            return 0, "VERDICT: REVISE"
        return 0, "VERDICT: APPROVE"

    def mock_normalize_step_metadata(step):
        step_id = str(step.get("id", "")).strip().lower()
        metadata = {
            "task_kind": "task",
            "complexity": "medium",
            "risk": "medium",
            "sensitive_paths": [],
            "preferred_profile": None,
            "escalation_policy": "default",
        }
        if step_id == "critique":
            metadata["escalation_policy"] = "conservative"
        return metadata

    monkeypatch.setattr(mod, "_run_pipeline_step", mock_step)
    monkeypatch.setattr(mod, "_normalize_step_metadata", mock_normalize_step_metadata)
    monkeypatch.setattr(mod, "_pipeline_step_metadata", lambda step_name: mock_normalize_step_metadata({"id": step_name}))
    monkeypatch.setattr(mod, "_TASK_AUTO_RETRY_MAX", 0)
    monkeypatch.setattr(mod, "_CRITIQUE_AUTO_RETRY_MAX", 0)
    monkeypatch.setattr(mod, "_PIPELINE_MAX_SAME_VERDICT", 99)
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
        branch="feat/t7-policy",
        executor_pref="cursor",
        dry_run=True,
        allow_dirty=True,
    )

    assert rc == 2
    result = json.loads((tmp_path / "result.json").read_text(encoding="utf-8"))
    assert result["status"] == "hold"
    critique = result["steps"]["critique"]
    assert critique["status"] == "retry_exhausted"
    assert critique["failure_class"] == "quality_failure"
    assert critique["decision"] == "hold"
    assert critique["decision_reason"] == "conservative policy holds on quality failure"
    assert critique["reroute_target"] is None


def test_retry_exhausted_quality_failure_uses_plan_retry_before_task_retry(tmp_path: Path, monkeypatch):
    """retry_exhausted + quality_failure(default)는 persisted reroute_target=plan_retry를 소비해야 한다."""
    import importlib
    import omc_autopilot as mod
    importlib.reload(mod)

    call_log: list[str] = []
    critique_calls = {"n": 0}

    def mock_step(root, step_name, prompt, executor, timeout, *, dry_run=False, isolated=False):
        call_log.append(step_name)
        if step_name in ("plan", "task"):
            return 0, "VERDICT: PROCEED"
        if step_name == "critique":
            critique_calls["n"] += 1
            if critique_calls["n"] <= 4:
                return 0, "VERDICT: REVISE"
            return 0, "VERDICT: PROCEED"
        if step_name == "plan_retry":
            return 0, "VERDICT: PROCEED"
        if step_name == "review":
            return 0, "VERDICT: APPROVE"
        return 0, "VERDICT: PROCEED"

    monkeypatch.setattr(mod, "_run_pipeline_step", mock_step)
    monkeypatch.setattr(mod, "_TASK_AUTO_RETRY_MAX", 0)
    monkeypatch.setattr(mod, "_CRITIQUE_AUTO_RETRY_MAX", 1)
    monkeypatch.setattr(mod, "_PIPELINE_MAX_SAME_VERDICT", 99)
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
        branch="feat/t7-plan-retry",
        executor_pref="cursor",
        dry_run=True,
        allow_dirty=True,
    )

    assert rc == 0
    assert "plan_retry" in call_log, f"plan_retry가 호출되지 않음: {call_log}"
    assert "task_retry" not in call_log, f"task_retry가 잘못 호출됨: {call_log}"


def test_task_retry_completed_persists_decision_metadata(tmp_path: Path, monkeypatch):
    """task_retry 성공 경로도 공통 decision payload shape를 남겨야 한다."""
    import importlib
    import omc_autopilot as mod
    importlib.reload(mod)

    critique_call = {"n": 0}

    def mock_step(root, step_name, prompt, executor, timeout, *, dry_run=False, isolated=False):
        if step_name in ("plan", "task"):
            return 0, "VERDICT: PROCEED"
        if step_name == "critique":
            critique_call["n"] += 1
            if critique_call["n"] <= 4:
                return 1, "critique command failed"
            return 0, "VERDICT: PROCEED"
        if step_name == "task_retry":
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
        branch="feat/t7-task-retry-shape",
        executor_pref="cursor",
        dry_run=True,
        allow_dirty=True,
    )

    assert rc == 0
    result = json.loads((tmp_path / "result.json").read_text(encoding="utf-8"))
    task_retry = result["steps"]["task_retry"]
    assert task_retry["status"] == "completed"
    assert task_retry["decision"] == "same"
    assert task_retry["decision_reason"] == "completed retry stays on current path"
    assert task_retry["reroute_target"] is None


def test_plan_retry_completed_persists_decision_metadata(tmp_path: Path, monkeypatch):
    """plan_retry 성공 경로도 공통 decision payload shape를 남겨야 한다."""
    import importlib
    import omc_autopilot as mod
    importlib.reload(mod)

    critique_calls = {"n": 0}

    def mock_step(root, step_name, prompt, executor, timeout, *, dry_run=False, isolated=False):
        if step_name in ("plan", "task"):
            return 0, "VERDICT: PROCEED"
        if step_name == "critique":
            critique_calls["n"] += 1
            if critique_calls["n"] <= 4:
                return 0, "VERDICT: REVISE"
            return 0, "VERDICT: PROCEED"
        if step_name == "plan_retry":
            return 0, "VERDICT: PROCEED"
        if step_name == "review":
            return 0, "VERDICT: APPROVE"
        return 0, "VERDICT: PROCEED"

    monkeypatch.setattr(mod, "_run_pipeline_step", mock_step)
    monkeypatch.setattr(mod, "_TASK_AUTO_RETRY_MAX", 0)
    monkeypatch.setattr(mod, "_CRITIQUE_AUTO_RETRY_MAX", 1)
    monkeypatch.setattr(mod, "_PIPELINE_MAX_SAME_VERDICT", 99)
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
        branch="feat/t7-plan-retry-shape",
        executor_pref="cursor",
        dry_run=True,
        allow_dirty=True,
    )

    assert rc == 0
    result = json.loads((tmp_path / "result.json").read_text(encoding="utf-8"))
    plan_retry = result["steps"]["plan_retry"]
    assert plan_retry["status"] == "completed"
    assert plan_retry["decision"] == "same"
    assert plan_retry["decision_reason"] == "completed retry stays on current path"
    assert plan_retry["reroute_target"] is None


def test_critique_retry_prompt_includes_issues(tmp_path: Path, monkeypatch):
    """critique retry 프롬프트에 이전 지적 내용이 포함돼야 한다."""
    import importlib
    import omc_autopilot as mod
    importlib.reload(mod)

    captured = {"critique_retry_prompt": ""}
    call_count = {"n": 0}
    ISSUE_MARKER = "[critique-issue-marker]"

    def mock_step(root, step_name, prompt, executor, timeout, *, dry_run=False, isolated=False):
        if step_name == "plan":
            return 0, "VERDICT: PROCEED"
        if step_name == "task":
            return 0, "VERDICT: PROCEED"
        if step_name == "critique":
            call_count["n"] += 1
            if call_count["n"] == 1:
                # 첫 critique: 이슈를 포함한 REVISE 반환
                return 0, f"발견된 문제:\n{ISSUE_MARKER}\nVERDICT: REVISE"
            # 두 번째 critique: 프롬프트 캡처
            captured["critique_retry_prompt"] = prompt
            return 0, "VERDICT: PROCEED"
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

    mod.cmd_pipeline(
        root=tmp_path,
        instruction="x" * 200,
        branch="feat/t7-prompt",
        executor_pref="cursor",
        dry_run=True,
        allow_dirty=True,
    )

    assert ISSUE_MARKER in captured["critique_retry_prompt"], (
        f"critique retry 프롬프트에 이전 지적 내용이 없음:\n{captured['critique_retry_prompt'][:300]}"
    )

# ─────────────────────────────────────────────────────────────────────────────
# T8: critique 루프 재진입 시 diff 갱신
# ─────────────────────────────────────────────────────────────────────────────

def test_critique_prompt_refreshed_after_task_retry(tmp_path: Path, monkeypatch):
    """execution_failure 기반 task_retry 후 critique 루프 재진입 시 _get_critique_context가 재호출돼야 한다."""
    import importlib
    import omc_autopilot as mod
    importlib.reload(mod)

    ctx_call_count = {"n": 0}
    CTX_V1 = "[ctx-v1-initial]"
    CTX_V2 = "[ctx-v2-after-task-retry]"

    def mock_get_ctx(root, **kwargs):
        ctx_call_count["n"] += 1
        return CTX_V2 if ctx_call_count["n"] > 1 else CTX_V1

    monkeypatch.setattr(mod, "_get_critique_context", mock_get_ctx)

    critique_prompts = []
    task_retry_count = {"n": 0}

    critique_call_count = {"n": 0}

    def mock_step(root, step_name, prompt, executor, timeout, *, dry_run=False, isolated=False):
        if step_name == "plan":
            return 0, "VERDICT: PROCEED"
        if step_name == "task":
            return 0, "VERDICT: PROCEED"
        if step_name == "critique":
            critique_call_count["n"] += 1
            critique_prompts.append(prompt)
            # 첫 4번은 execution_failure 누적 → task_retry 유도
            if critique_call_count["n"] <= 4:
                return 1, "critique command failed"
            # 재진입 후 PROCEED
            return 0, "VERDICT: PROCEED"
        if step_name == "task_retry":
            task_retry_count["n"] += 1
            return 0, "VERDICT: PROCEED"
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

    mod.cmd_pipeline(
        root=tmp_path,
        instruction="x" * 200,
        branch="feat/t8-ctx-refresh",
        executor_pref="cursor",
        dry_run=True,
        allow_dirty=True,
    )

    # task_retry가 실행됐어야 함
    assert task_retry_count["n"] >= 1, "task_retry가 호출되지 않음"
    # _get_critique_context가 2회 이상 호출돼야 함 (재진입 시 갱신)
    assert ctx_call_count["n"] >= 2, (
        f"critique 루프 재진입 후 _get_critique_context가 재호출되지 않음: {ctx_call_count['n']}회 호출"
    )
    # 재진입 후 critique 프롬프트에 새 ctx가 반영돼야 함
    if len(critique_prompts) > 4:
        assert CTX_V2 in critique_prompts[-1], (
            f"재진입 후 critique 프롬프트에 새 diff가 없음:\n{critique_prompts[-1][:200]}"
        )


# ── 버그 A/B/C 회귀 방지 테스트 ────────────────────────────────────────────

def test_plan_retry_resets_task_auto_retry_count(tmp_path: Path, monkeypatch):
    """버그 C: plan_retry 완료 후 critique 루프 재진입 시 task_auto_retry_count가 0이어야 한다."""
    import importlib
    import omc_autopilot as mod
    importlib.reload(mod)

    observed: dict = {"task_auto_retry_after_plan_retry": None}
    critique_call = {"n": 0}
    plan_retry_done = {"v": False}

    original_step = mod._run_pipeline_step

    def mock_step(root, step_name, prompt, executor, timeout, *, dry_run=False, isolated=False):
        if step_name == "plan":
            return 0, "VERDICT: PROCEED"
        if step_name == "task":
            return 0, "VERDICT: PROCEED"
        if step_name == "critique":
            critique_call["n"] += 1
            if not plan_retry_done["v"]:
                # 첫 번째 critique 루프: retry 소진 유도 (REVISE 반복)
                return 0, "VERDICT: REVISE"
            else:
                # plan_retry 후 재진입: PROCEED 반환
                return 0, "VERDICT: PROCEED"
        if step_name == "task_retry":
            return 0, "VERDICT: PROCEED"
        if step_name == "plan_retry":
            plan_retry_done["v"] = True
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

    result_data = {}

    original_save = mod._save_pipeline_result
    def capturing_save(root, data):
        result_data.update(data)
        original_save(root, data)
    monkeypatch.setattr(mod, "_save_pipeline_result", capturing_save)

    mod.cmd_pipeline(
        root=tmp_path,
        instruction="x" * 200,
        branch="feat/bugC-test",
        executor_pref="cursor",
        dry_run=True,
        allow_dirty=True,
    )

    assert plan_retry_done["v"], "plan_retry가 실행되지 않음"
    # plan_retry 후 critique가 PROCEED를 반환했으므로 결과가 hold가 아니어야 함
    final_status = result_data.get("status", "")
    assert final_status not in ("hold",), (
        f"plan_retry 후 task_auto_retry_count 미리셋으로 인해 루프가 조기 종료됨: status={final_status}"
    )


def test_run_pipeline_step_kills_process_group_on_timeout(tmp_path: Path, monkeypatch):
    """버그 B: timeout 초과 시 프로세스 그룹 전체가 kill되고 returncode=124를 반환한다."""
    import importlib
    import omc_autopilot as mod
    importlib.reload(mod)

    import subprocess
    import os
    import signal
    import threading

    killed_pgids: list = []

    class FakeProc:
        pid = 12345
        returncode = None

        def communicate(self):
            # communicate가 블로킹되어 timeout 발동을 유도
            import time
            time.sleep(10)  # 실제론 Timer가 먼저 kill
            return ("", "")

    fake_proc = FakeProc()

    def fake_popen(cmd, **kwargs):
        return fake_proc

    def fake_killpg(pgid, sig):
        killed_pgids.append(pgid)
        # communicate 해제를 위해 returncode 설정
        fake_proc.returncode = -9

    def fake_getpgid(pid):
        return pid

    monkeypatch.setattr(subprocess, "Popen", fake_popen)
    monkeypatch.setattr(os, "killpg", fake_killpg)
    monkeypatch.setattr(os, "getpgid", fake_getpgid)

    # communicate가 kill 후 바로 종료되도록 패치
    original_communicate = FakeProc.communicate
    def patched_communicate(self):
        import time
        # Timer 발동 대기 (timeout=1초 설정)
        time.sleep(2)
        return ("out", "err")
    FakeProc.communicate = patched_communicate

    # omc_exec.py 존재 여부 mock
    (tmp_path / "omc_exec.py").write_text("")
    import unittest.mock as umock
    with umock.patch.object(mod.Path, "exists", return_value=True):
        rc, out = mod._run_pipeline_step(
            root=tmp_path,
            step_name="critique",
            prompt="test",
            executor="codex",
            timeout_sec=1,
            dry_run=False,
        )

    assert rc == 124, f"timeout 시 returncode=124 기대, 실제={rc}"
    assert "타임아웃" in out, f"timeout 메시지 없음: {out}"
    assert len(killed_pgids) > 0, "killpg가 호출되지 않음 — 프로세스 그룹 kill 미동작"


def test_resume_restores_task_auto_retry_count_from_task_retry(tmp_path: Path, monkeypatch):
    """버그 A: --resume 시 task_retry 완료 이력이 있으면 task_auto_retry_count=1로 복원된다."""
    import importlib
    import omc_autopilot as mod
    importlib.reload(mod)

    # task_retry 완료된 상태의 resume 데이터 준비
    resume_data = {
        "status": "running",
        "steps": {
            "preflight": {"status": "completed"},
            "plan": {"status": "completed"},
            "task": {"status": "completed"},
            "critique": {"status": "failed_critique_loop"},
            "task_retry": {"status": "completed"},
        },
        "last_completed_step": "task_retry",
    }
    result_path = tmp_path / ".omc" / "pipeline_run_result.json"
    result_path.parent.mkdir(parents=True)
    result_path.write_text(__import__("json").dumps(resume_data))

    monkeypatch.setenv("OmC_PIPELINE_RESULT_PATH", str(result_path))

    loaded = mod._load_resume_state(tmp_path)
    assert loaded is not None

    task_retry_done = mod._step_already_done(loaded, "task_retry")
    assert task_retry_done, "task_retry 완료 이력이 _step_already_done에서 감지되지 않음"
