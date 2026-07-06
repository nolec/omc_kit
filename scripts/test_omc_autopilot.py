"""
omc_autopilot.py 단위 테스트 (TDD — RED 단계)

테스트 대상:
  - _tokenize / _resolve_order / _detect_executor
  - cmd_new: 태스크 파일 생성
  - cmd_status: 상태 파일 읽기
  - cmd_run --dry-run: 실제 LLM 없이 계획 실행
"""
from __future__ import annotations

import json
import sys
import tempfile
import subprocess
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest

# omc_autopilot.py 가 아직 없으므로 import 자체가 실패해야 RED 상태
sys.path.insert(0, str(Path(__file__).resolve().parent))

# --------------------------------------------------------------------------
# RED: 모듈이 존재하지 않아 ImportError → 테스트 FAIL
# --------------------------------------------------------------------------
try:
    import omc_autopilot  # noqa: F401
    _MODULE_PRESENT = True
except ImportError:
    _MODULE_PRESENT = False

try:
    import omc_exec  # noqa: F401
    _EXEC_MODULE_PRESENT = True
except ImportError:
    _EXEC_MODULE_PRESENT = False


@pytest.mark.skipif(_MODULE_PRESENT, reason="모듈 없음 — RED 단계 확인용")
def test_module_missing():
    """omc_autopilot.py 가 없으면 이 테스트가 실패해야 합니다."""
    assert False, "omc_autopilot.py 가 없습니다 — 구현 후 재실행 필요"


@pytest.mark.skipif(not _EXEC_MODULE_PRESENT, reason="omc_exec.py 없음")
def test_detects_codex_network_transport_failure():
    raw = (
        '{"type":"error","message":"Reconnecting... 2/5 '
        '(stream disconnected before completion: failed to lookup address information: '
        'nodename nor servname provided, or not known)"}'
    )
    assert omc_exec._is_headless_network_failure(raw) is True


@pytest.mark.skipif(not _EXEC_MODULE_PRESENT, reason="omc_exec.py 없음")
def test_run_codex_headless_falls_back_to_gemini_on_network_failure(tmp_path, monkeypatch):
    class _Runtime:
        def cleanup(self):
            return None

    monkeypatch.setattr(
        omc_exec,
        "_prepare_codex_headless_runtime",
        lambda model_profile="mini_default": (_Runtime(), {}),
    )
    monkeypatch.setattr(
        omc_exec,
        "_codex_headless_command",
        lambda *args, **kwargs: ["codex", "exec"],
    )
    monkeypatch.setattr(omc_exec, "_codex_supports_reasoning_effort", lambda: True)
    monkeypatch.setattr(omc_exec, "_write_raw_output_if_requested", lambda raw_text: None)
    monkeypatch.setattr(
        omc_exec,
        "_record_headless_cost",
        lambda *args, **kwargs: None,
    )
    monkeypatch.setattr(omc_exec, "_read_text_if_exists", lambda path: "")
    monkeypatch.setattr(omc_exec.shutil, "which", lambda exe: "/usr/bin/fake" if exe in {"gemini", "codex"} else None)
    monkeypatch.setattr(
        omc_exec.subprocess,
        "run",
        lambda *args, **kwargs: SimpleNamespace(
            returncode=1,
            stdout='{"type":"error","message":"failed to lookup address information"}',
            stderr="",
        ),
    )
    fallback_calls: list[str] = []
    monkeypatch.setattr(
        omc_exec,
        "_run_gemini_headless",
        lambda *args, **kwargs: fallback_calls.append("gemini") or 0,
    )

    rc = omc_exec._run_codex_headless(
        tmp_path,
        "prompt",
        timeout_sec=5,
        model_profile="mini_default",
        task_kind="task",
    )

    assert rc == 0
    assert fallback_calls == ["gemini"]


@pytest.mark.skipif(not _EXEC_MODULE_PRESENT, reason="omc_exec.py 없음")
def test_run_codex_headless_does_not_print_codex_failure_output_when_fallback_succeeds(
    tmp_path, monkeypatch, capsys
):
    class _Runtime:
        def cleanup(self):
            return None

    monkeypatch.setattr(
        omc_exec,
        "_prepare_codex_headless_runtime",
        lambda model_profile="mini_default": (_Runtime(), {}),
    )
    monkeypatch.setattr(
        omc_exec,
        "_codex_headless_command",
        lambda *args, **kwargs: ["codex", "exec"],
    )
    monkeypatch.setattr(omc_exec, "_codex_supports_reasoning_effort", lambda: True)
    monkeypatch.setattr(omc_exec, "_write_raw_output_if_requested", lambda raw_text: None)
    monkeypatch.setattr(
        omc_exec,
        "_record_headless_cost",
        lambda *args, **kwargs: None,
    )
    monkeypatch.setattr(omc_exec, "_read_text_if_exists", lambda path: "")
    monkeypatch.setattr(
        omc_exec.shutil,
        "which",
        lambda exe: "/usr/bin/fake" if exe in {"gemini", "codex"} else None,
    )
    monkeypatch.setattr(
        omc_exec.subprocess,
        "run",
        lambda *args, **kwargs: SimpleNamespace(
            returncode=1,
            stdout='{"type":"error","message":"failed to lookup address information"}',
            stderr="",
        ),
    )
    monkeypatch.setattr(
        omc_exec,
        "_run_gemini_headless",
        lambda *args, **kwargs: print("gemini-success") or 0,
    )

    rc = omc_exec._run_codex_headless(
        tmp_path,
        "prompt",
        timeout_sec=5,
        model_profile="mini_default",
        task_kind="task",
    )
    captured = capsys.readouterr()

    assert rc == 0
    assert "gemini-success" in captured.out
    assert "failed to lookup address information" not in captured.out


# --------------------------------------------------------------------------
# GREEN 이후 실행될 테스트 (모듈 있을 때만)
# --------------------------------------------------------------------------

@pytest.mark.skipif(not _MODULE_PRESENT, reason="omc_autopilot.py 없음")
class TestResolveOrder:
    def test_no_deps(self):
        steps = [{"id": "s1", "depends_on": []}, {"id": "s2", "depends_on": []}]
        result = omc_autopilot._resolve_order(steps)
        assert [s["id"] for s in result] == ["s1", "s2"]

    def test_linear_deps(self):
        steps = [
            {"id": "s2", "depends_on": ["s1"]},
            {"id": "s1", "depends_on": []},
        ]
        result = omc_autopilot._resolve_order(steps)
        ids = [s["id"] for s in result]
        assert ids.index("s1") < ids.index("s2")

    def test_diamond_deps(self):
        steps = [
            {"id": "s4", "depends_on": ["s2", "s3"]},
            {"id": "s3", "depends_on": ["s1"]},
            {"id": "s2", "depends_on": ["s1"]},
            {"id": "s1", "depends_on": []},
        ]
        result = omc_autopilot._resolve_order(steps)
        ids = [s["id"] for s in result]
        assert ids[0] == "s1"
        assert ids[-1] == "s4"

    def test_unknown_dependency_raises_value_error(self):
        steps = [{"id": "s1", "depends_on": ["missing"]}]
        with pytest.raises(ValueError, match="unknown dependency"):
            omc_autopilot._resolve_order(steps)

    def test_cycle_dependency_raises_value_error(self):
        steps = [
            {"id": "s1", "depends_on": ["s2"]},
            {"id": "s2", "depends_on": ["s1"]},
        ]
        with pytest.raises(ValueError, match="cycle"):
            omc_autopilot._resolve_order(steps)


@pytest.mark.skipif(not _MODULE_PRESENT, reason="omc_autopilot.py 없음")
class TestNormalizeStepMetadata:
    def test_defaults_metadata_when_fields_missing(self):
        normalized = omc_autopilot._normalize_step_metadata({"id": "s1", "prompt": "작업"})
        assert normalized["task_kind"] == "task"
        assert normalized["complexity"] == "medium"
        assert normalized["risk"] == "medium"
        assert normalized["sensitive_paths"] == []
        assert normalized["preferred_profile"] is None
        assert normalized["escalation_policy"] == "default"

    def test_invalid_metadata_falls_back_to_safe_defaults(self):
        normalized = omc_autopilot._normalize_step_metadata(
            {
                "id": "s1",
                "task_kind": "weird-kind",
                "complexity": "extreme",
                "risk": "unknown",
                "preferred_profile": "gpt-9",
                "escalation_policy": "panic",
            }
        )
        assert normalized["task_kind"] == "task"
        assert normalized["complexity"] == "medium"
        assert normalized["risk"] == "medium"
        assert normalized["preferred_profile"] is None
        assert normalized["escalation_policy"] == "default"

    def test_sensitive_paths_non_list_becomes_empty_list(self):
        normalized = omc_autopilot._normalize_step_metadata(
            {"id": "review", "sensitive_paths": "scripts/"}
        )
        assert normalized["task_kind"] == "review"
        assert normalized["sensitive_paths"] == []

    def test_sensitive_paths_filters_non_string_entries(self):
        normalized = omc_autopilot._normalize_step_metadata(
            {"id": "review", "sensitive_paths": ["scripts/", 1, None, "src/api/"]}
        )
        assert normalized["sensitive_paths"] == ["scripts/", "src/api/"]


@pytest.mark.skipif(not _MODULE_PRESENT, reason="omc_autopilot.py 없음")
class TestCmdNew:
    def test_creates_task_file(self, tmp_path):
        code = omc_autopilot.cmd_new(tmp_path, "feat-x", "X 기능")
        assert code == 0
        f = tmp_path / ".omc" / "tasks" / "feat-x.json"
        assert f.exists()
        data = json.loads(f.read_text())
        assert data["id"] == "feat-x"
        assert len(data["steps"]) >= 1

    def test_rejects_duplicate(self, tmp_path):
        omc_autopilot.cmd_new(tmp_path, "dup", "중복")
        code = omc_autopilot.cmd_new(tmp_path, "dup", "중복")
        assert code == 1


@pytest.mark.skipif(not _MODULE_PRESENT, reason="omc_autopilot.py 없음")
class TestCmdRunDryRun:
    def _make_task(self, tmp_path: Path) -> Path:
        tasks_dir = tmp_path / ".omc" / "tasks"
        tasks_dir.mkdir(parents=True, exist_ok=True)
        task = {
            "id": "test-task",
            "title": "테스트 태스크",
            "executor": "auto",
            "max_retries": 0,
            "steps": [
                {"id": "s1", "prompt": "안녕", "depends_on": [], "timeout_sec": 10},
                {"id": "s2", "prompt": "세계", "depends_on": ["s1"], "timeout_sec": 10},
            ],
        }
        p = tasks_dir / "test-task.json"
        p.write_text(json.dumps(task), encoding="utf-8")
        return p

    def test_dry_run_completes(self, tmp_path):
        task_file = self._make_task(tmp_path)
        code = omc_autopilot.cmd_run(tmp_path, task_file, dry_run=True)
        assert code == 0

    def test_state_file_created(self, tmp_path):
        task_file = self._make_task(tmp_path)
        omc_autopilot.cmd_run(tmp_path, task_file, dry_run=True)
        state_file = tmp_path / ".omc" / "state" / "autopilot" / "test-task.json"
        assert state_file.exists()
        state = json.loads(state_file.read_text())
        assert state["status"] == "completed"
        assert state["steps"]["s1"]["status"] == "completed"
        assert state["steps"]["s2"]["status"] == "completed"

    def test_dry_run_state_marks_simulation_metadata(self, tmp_path):
        tasks_dir = tmp_path / ".omc" / "tasks"
        tasks_dir.mkdir(parents=True, exist_ok=True)
        task = {
            "id": "observed-collect",
            "title": "Observed Run Collection",
            "executor": "auto",
            "completion_requires_real_runs": True,
            "benchmark_source_type": "observed_request",
            "policy_pair": "baseline->candidate",
            "max_retries": 0,
            "steps": [
                {"id": "s1", "prompt": "실행", "depends_on": [], "timeout_sec": 10},
            ],
        }
        task_file = tasks_dir / "observed-collect.json"
        task_file.write_text(json.dumps(task), encoding="utf-8")

        code = omc_autopilot.cmd_run(tmp_path, task_file, dry_run=True)

        assert code == 0
        state = json.loads(
            (tmp_path / ".omc" / "state" / "autopilot" / "observed-collect.json").read_text(
                encoding="utf-8"
            )
        )
        assert state.get("simulated") is True
        assert state.get("completion_requires_real_runs") is True
        assert state["steps"]["s1"].get("simulated") is True

    def test_real_run_reexecutes_step_completed_only_by_dry_run_state(self, tmp_path):
        tasks_dir = tmp_path / ".omc" / "tasks"
        tasks_dir.mkdir(parents=True, exist_ok=True)
        task = {
            "id": "observed-collect",
            "title": "Observed Run Collection",
            "executor": "auto",
            "completion_requires_real_runs": True,
            "benchmark_source_type": "observed_request",
            "policy_pair": "baseline->candidate",
            "max_retries": 0,
            "steps": [
                {"id": "s1", "prompt": "실행", "depends_on": [], "timeout_sec": 10},
            ],
        }
        task_file = tasks_dir / "observed-collect.json"
        task_file.write_text(json.dumps(task), encoding="utf-8")

        state_dir = tmp_path / ".omc" / "state" / "autopilot"
        state_dir.mkdir(parents=True, exist_ok=True)
        (state_dir / "observed-collect.json").write_text(
            json.dumps(
                {
                    "task_id": "observed-collect",
                    "title": "Observed Run Collection",
                    "status": "completed",
                    "executor": "codex",
                    "simulated": True,
                    "completion_requires_real_runs": True,
                    "steps": {
                        "s1": {
                            "status": "completed",
                            "attempt": 1,
                            "simulated": True,
                            "last_output": "[DRY-RUN] 시뮬레이션 성공",
                        }
                    },
                }
            ),
            encoding="utf-8",
        )

        with patch.object(omc_autopilot, "_detect_executor", return_value="codex"), patch.object(
            omc_autopilot.subprocess,
            "run",
            return_value=SimpleNamespace(returncode=0, stdout="ok", stderr=""),
        ):
            code = omc_autopilot.cmd_run(tmp_path, task_file, dry_run=False)

        assert code == 0
        state = json.loads((state_dir / "observed-collect.json").read_text(encoding="utf-8"))
        assert state.get("simulated") is False
        assert state["steps"]["s1"].get("simulated") is not True
        assert state["steps"]["s1"].get("last_output") == "ok"

    def test_real_run_persists_routing_reason_metadata(self, tmp_path):
        tasks_dir = tmp_path / ".omc" / "tasks"
        tasks_dir.mkdir(parents=True, exist_ok=True)
        task = {
            "id": "routing-metadata",
            "title": "Routing Metadata",
            "executor": "auto",
            "max_retries": 0,
            "steps": [
                {
                    "id": "plan",
                    "prompt": "계획",
                    "depends_on": [],
                    "timeout_sec": 10,
                },
            ],
        }
        task_file = tasks_dir / "routing-metadata.json"
        task_file.write_text(json.dumps(task), encoding="utf-8")

        fake_routing = {
            "task_kind": "plan",
            "routing_policy": "balanced",
            "model_profile": "mini_high",
            "routing_reason_codes": ["plan_or_review_work"],
            "routing_reason_summary": "plan/review/investigate work prefers broader context",
        }

        with patch.object(omc_autopilot, "_detect_executor", return_value="codex"), patch.object(
            omc_autopilot.omc_exec,
            "resolve_task_routing",
            return_value=fake_routing,
        ), patch.object(
            omc_autopilot.subprocess,
            "run",
            return_value=SimpleNamespace(returncode=0, stdout="ok", stderr=""),
        ):
            code = omc_autopilot.cmd_run(tmp_path, task_file, dry_run=False)

        assert code == 0
        state = json.loads(
            (tmp_path / ".omc" / "state" / "autopilot" / "routing-metadata.json").read_text(
                encoding="utf-8"
            )
        )
        assert state["steps"]["plan"]["model_profile"] == "mini_high"
        assert state["steps"]["plan"]["routing_policy"] == "balanced"
        assert state["steps"]["plan"]["routing_reason_codes"] == ["plan_or_review_work"]
        assert (
            state["steps"]["plan"]["routing_reason_summary"]
            == "plan/review/investigate work prefers broader context"
        )

    def test_blocked_step_on_failed_dep(self, tmp_path):
        tasks_dir = tmp_path / ".omc" / "tasks"
        tasks_dir.mkdir(parents=True, exist_ok=True)
        task = {
            "id": "blocked-task",
            "title": "블록 테스트",
            "executor": "auto",
            "max_retries": 0,
            "steps": [
                {"id": "s1", "prompt": "실패", "depends_on": [], "timeout_sec": 10},
                {"id": "s2", "prompt": "의존", "depends_on": ["s1"], "timeout_sec": 10},
            ],
        }
        # s1이 이미 실패한 상태를 주입
        state_dir = tmp_path / ".omc" / "state" / "autopilot"
        state_dir.mkdir(parents=True, exist_ok=True)
        (state_dir / "blocked-task.json").write_text(
            json.dumps({
                "task_id": "blocked-task",
                "status": "running",
                "steps": {"s1": {"status": "failed"}},
            }),
            encoding="utf-8",
        )
        p = tasks_dir / "blocked-task.json"
        p.write_text(json.dumps(task), encoding="utf-8")
        code = omc_autopilot.cmd_run(tmp_path, p, dry_run=True)
        assert code == 1  # s2 블록되어 전체 실패

    def test_failed_step_is_retried_when_resume_failed_enabled(self, tmp_path):
        tasks_dir = tmp_path / ".omc" / "tasks"
        tasks_dir.mkdir(parents=True, exist_ok=True)
        task = {
            "id": "resume-failed-task",
            "title": "실패 재실행 테스트",
            "executor": "auto",
            "max_retries": 0,
            "steps": [
                {"id": "s1", "prompt": "재실행", "depends_on": [], "timeout_sec": 10},
            ],
        }
        state_dir = tmp_path / ".omc" / "state" / "autopilot"
        state_dir.mkdir(parents=True, exist_ok=True)
        (state_dir / "resume-failed-task.json").write_text(
            json.dumps(
                {
                    "task_id": "resume-failed-task",
                    "status": "failed",
                    "steps": {"s1": {"status": "failed"}},
                }
            ),
            encoding="utf-8",
        )
        p = tasks_dir / "resume-failed-task.json"
        p.write_text(json.dumps(task), encoding="utf-8")

        code = omc_autopilot.cmd_run(tmp_path, p, dry_run=True, resume_failed=True)
        assert code == 0
        state = json.loads((state_dir / "resume-failed-task.json").read_text(encoding="utf-8"))
        assert state["steps"]["s1"]["status"] == "completed"

    def test_failed_step_remains_skipped_when_resume_failed_disabled(self, tmp_path):
        tasks_dir = tmp_path / ".omc" / "tasks"
        tasks_dir.mkdir(parents=True, exist_ok=True)
        task = {
            "id": "skip-failed-task",
            "title": "실패 스킵 테스트",
            "executor": "auto",
            "max_retries": 0,
            "steps": [
                {"id": "s1", "prompt": "스킵", "depends_on": [], "timeout_sec": 10},
            ],
        }
        state_dir = tmp_path / ".omc" / "state" / "autopilot"
        state_dir.mkdir(parents=True, exist_ok=True)
        (state_dir / "skip-failed-task.json").write_text(
            json.dumps(
                {
                    "task_id": "skip-failed-task",
                    "status": "failed",
                    "steps": {"s1": {"status": "failed"}},
                }
            ),
            encoding="utf-8",
        )
        p = tasks_dir / "skip-failed-task.json"
        p.write_text(json.dumps(task), encoding="utf-8")

        code = omc_autopilot.cmd_run(tmp_path, p, dry_run=True, resume_failed=False)
        assert code == 1

    def test_failed_step_is_retried_when_task_opt_in_resume_failed(self, tmp_path):
        tasks_dir = tmp_path / ".omc" / "tasks"
        tasks_dir.mkdir(parents=True, exist_ok=True)
        task = {
            "id": "task-opt-in-resume-failed",
            "title": "태스크 opt-in 실패 재실행 테스트",
            "executor": "auto",
            "resume_failed": True,
            "max_retries": 0,
            "steps": [
                {"id": "s1", "prompt": "재실행", "depends_on": [], "timeout_sec": 10},
            ],
        }
        state_dir = tmp_path / ".omc" / "state" / "autopilot"
        state_dir.mkdir(parents=True, exist_ok=True)
        (state_dir / "task-opt-in-resume-failed.json").write_text(
            json.dumps(
                {
                    "task_id": "task-opt-in-resume-failed",
                    "status": "failed",
                    "steps": {"s1": {"status": "failed"}},
                }
            ),
            encoding="utf-8",
        )
        p = tasks_dir / "task-opt-in-resume-failed.json"
        p.write_text(json.dumps(task), encoding="utf-8")

        code = omc_autopilot.cmd_run(tmp_path, p, dry_run=True, resume_failed=False)
        assert code == 0
        state = json.loads((state_dir / "task-opt-in-resume-failed.json").read_text(encoding="utf-8"))
        assert state["steps"]["s1"]["status"] == "completed"

    def test_dry_run_dependency_error_is_messageized_and_returns_one(self, tmp_path, capsys):
        tasks_dir = tmp_path / ".omc" / "tasks"
        tasks_dir.mkdir(parents=True, exist_ok=True)
        task = {
            "id": "bad-deps-task",
            "title": "의존성 에러 테스트",
            "executor": "auto",
            "max_retries": 0,
            "steps": [
                {"id": "s1", "prompt": "hello", "depends_on": ["missing-step"], "timeout_sec": 10},
            ],
        }
        p = tasks_dir / "bad-deps-task.json"
        p.write_text(json.dumps(task), encoding="utf-8")

        code = omc_autopilot.cmd_run(tmp_path, p, dry_run=True)
        out = capsys.readouterr().out
        assert code == 1
        assert "unknown dependency" in out


@pytest.mark.skipif(not _MODULE_PRESENT, reason="omc_autopilot.py 없음")
class TestCmdRunRealExecution:
    def test_non_dry_run_executes_step_without_name_error(self, tmp_path):
        tasks_dir = tmp_path / ".omc" / "tasks"
        tasks_dir.mkdir(parents=True, exist_ok=True)
        task = {
            "id": "real-run-task",
            "title": "실행 경로 테스트",
            "executor": "auto",
            "max_retries": 0,
            "steps": [
                {"id": "s1", "prompt": "실행", "depends_on": [], "timeout_sec": 10},
            ],
        }
        task_file = tasks_dir / "real-run-task.json"
        task_file.write_text(json.dumps(task), encoding="utf-8")

        with patch.object(omc_autopilot, "_detect_executor", return_value="codex"), patch.object(
            omc_autopilot.subprocess,
            "run",
            return_value=SimpleNamespace(returncode=0, stdout="ok", stderr=""),
        ):
            code = omc_autopilot.cmd_run(tmp_path, task_file, dry_run=False)

        assert code == 0

    def test_non_dry_run_expect_only_step_skips_executor_and_runs_checks(self, tmp_path):
        tasks_dir = tmp_path / ".omc" / "tasks"
        tasks_dir.mkdir(parents=True, exist_ok=True)
        task = {
            "id": "expect-only-task",
            "title": "Expect Only Task",
            "executor": "auto",
            "max_retries": 0,
            "steps": [
                {
                    "id": "s1",
                    "prompt": "검증만",
                    "expect_only": True,
                    "depends_on": [],
                    "timeout_sec": 10,
                    "expect": {"checks": [{"cmd": "echo ok", "label": "dummy"}]},
                },
            ],
        }
        task_file = tasks_dir / "expect-only-task.json"
        task_file.write_text(json.dumps(task), encoding="utf-8")

        with patch.object(omc_autopilot, "_detect_executor", return_value="codex"), patch.object(
            omc_autopilot,
            "_run_step",
            side_effect=AssertionError("executor should not be called for expect_only step"),
        ), patch.object(
            omc_autopilot,
            "_run_expect_checks",
            return_value=[{"label": "dummy", "ok": True}],
        ):
            code = omc_autopilot.cmd_run(tmp_path, task_file, dry_run=False)

        assert code == 0
        saved = json.loads(
            (tmp_path / ".omc" / "state" / "autopilot" / "expect-only-task.json").read_text(
                encoding="utf-8"
            )
        )
        assert saved["status"] == "completed"
        assert saved["steps"]["s1"]["status"] == "completed"


def test_v4b_operational_maintenance_task_opt_in_resume_failed_and_observed_metadata():
    task_path = Path(".omc/tasks/v4b-operational-maintenance.json")
    payload = json.loads(task_path.read_text(encoding="utf-8"))

    assert payload["resume_failed"] is True
    assert payload["completion_requires_real_runs"] is True
    assert payload["benchmark_source_type"] == "observed_request"
    assert payload["policy_pair"] == "baseline->candidate"


def test_v4b_operational_maintenance_collect_step_is_expect_only():
    task_path = Path(".omc/tasks/v4b-operational-maintenance.json")
    payload = json.loads(task_path.read_text(encoding="utf-8"))

    first_step = payload["steps"][0]

    assert first_step["id"] == "collect_fresh_observed_runs"
    assert first_step["expect_only"] is True


def test_v4b_operational_maintenance_verify_step_is_expect_only():
    task_path = Path(".omc/tasks/v4b-operational-maintenance.json")
    payload = json.loads(task_path.read_text(encoding="utf-8"))

    second_step = payload["steps"][1]

    assert second_step["id"] == "verify_ready_surfaces_hold"
    assert second_step["expect_only"] is True

    def test_real_run_persists_running_state_before_step_execution(self, tmp_path):
        tasks_dir = tmp_path / ".omc" / "tasks"
        tasks_dir.mkdir(parents=True, exist_ok=True)
        task = {
            "id": "running-state-task",
            "title": "Running State Task",
            "executor": "auto",
            "max_retries": 0,
            "steps": [
                {"id": "s1", "prompt": "실행", "depends_on": [], "timeout_sec": 10},
            ],
        }
        task_file = tasks_dir / "running-state-task.json"
        task_file.write_text(json.dumps(task), encoding="utf-8")

        def _fake_run_step(root, step, **kwargs):
            state_path = tmp_path / ".omc" / "state" / "autopilot" / "running-state-task.json"
            saved = json.loads(state_path.read_text(encoding="utf-8"))
            assert saved["status"] == "running"
            assert saved["steps"]["s1"]["status"] == "running"
            return 0, "ok", None, None

        with patch.object(omc_autopilot, "_detect_executor", return_value="codex"), patch.object(
            omc_autopilot,
            "_run_step",
            side_effect=_fake_run_step,
        ):
            code = omc_autopilot.cmd_run(tmp_path, task_file, dry_run=False)

        assert code == 0
        saved = json.loads(
            (
                tmp_path / ".omc" / "state" / "autopilot" / "running-state-task.json"
            ).read_text(encoding="utf-8")
        )
        assert saved["status"] == "completed"
        assert saved["steps"]["s1"]["status"] == "completed"

    def test_real_run_recovers_legacy_running_state_before_reexecution(self, tmp_path):
        tasks_dir = tmp_path / ".omc" / "tasks"
        tasks_dir.mkdir(parents=True, exist_ok=True)
        task = {
            "id": "legacy-running-task",
            "title": "Legacy Running Task",
            "executor": "auto",
            "max_retries": 0,
            "steps": [
                {"id": "s1", "prompt": "실행", "depends_on": [], "timeout_sec": 10},
            ],
        }
        task_file = tasks_dir / "legacy-running-task.json"
        task_file.write_text(json.dumps(task), encoding="utf-8")

        state_dir = tmp_path / ".omc" / "state" / "autopilot"
        state_dir.mkdir(parents=True, exist_ok=True)
        state_path = state_dir / "legacy-running-task.json"
        state_path.write_text(
            json.dumps(
                {
                    "task_id": "legacy-running-task",
                    "status": "running",
                    "started_at": "2026-07-05T23:59:54Z",
                    "steps": {
                        "s1": {
                            "status": "running",
                            "started_at": "2026-07-05T23:59:54Z",
                            "attempt": 0,
                        }
                    },
                }
            ),
            encoding="utf-8",
        )

        def _fake_run_step(root, step, **kwargs):
            saved = json.loads(state_path.read_text(encoding="utf-8"))
            assert saved["status"] == "running"
            assert saved["started_at"] != "2026-07-05T23:59:54Z"
            assert saved["steps"]["stale_recovery"]["status"] == "auto_hold"
            assert saved["steps"]["stale_recovery"]["reason"] == "missing pid before re-run"
            assert saved["steps"]["s1"]["status"] == "running"
            assert saved["steps"]["s1"]["stale_reason"] == "missing pid before re-run"
            return 0, "ok", None, None

        with patch.object(omc_autopilot, "_detect_executor", return_value="codex"), patch.object(
            omc_autopilot,
            "_run_step",
            side_effect=_fake_run_step,
        ):
            code = omc_autopilot.cmd_run(tmp_path, task_file, dry_run=False)

        assert code == 0
        saved = json.loads(state_path.read_text(encoding="utf-8"))
        assert saved["status"] == "completed"
        assert saved["steps"]["stale_recovery"]["status"] == "auto_hold"
        assert saved["steps"]["stale_recovery"]["reason"] == "missing pid before re-run"
        assert saved["steps"]["s1"]["status"] == "completed"

    def test_real_run_recovers_dead_pid_running_state_with_distinct_reason(self, tmp_path):
        tasks_dir = tmp_path / ".omc" / "tasks"
        tasks_dir.mkdir(parents=True, exist_ok=True)
        task = {
            "id": "dead-pid-task",
            "title": "Dead Pid Task",
            "executor": "auto",
            "max_retries": 0,
            "steps": [
                {"id": "s1", "prompt": "실행", "depends_on": [], "timeout_sec": 10},
            ],
        }
        task_file = tasks_dir / "dead-pid-task.json"
        task_file.write_text(json.dumps(task), encoding="utf-8")

        state_dir = tmp_path / ".omc" / "state" / "autopilot"
        state_dir.mkdir(parents=True, exist_ok=True)
        state_path = state_dir / "dead-pid-task.json"
        state_path.write_text(
            json.dumps(
                {
                    "task_id": "dead-pid-task",
                    "status": "running",
                    "pid": 424242,
                    "started_at": "2026-07-05T23:59:54Z",
                    "steps": {
                        "s1": {
                            "status": "running",
                            "started_at": "2026-07-05T23:59:54Z",
                            "attempt": 0,
                        }
                    },
                }
            ),
            encoding="utf-8",
        )

        def _fake_run_step(root, step, **kwargs):
            saved = json.loads(state_path.read_text(encoding="utf-8"))
            assert saved["steps"]["stale_recovery"]["reason"] == "dead pid before re-run: 424242"
            assert saved["steps"]["s1"]["stale_reason"] == "dead pid before re-run: 424242"
            return 0, "ok", None, None

        with patch.object(omc_autopilot, "_detect_executor", return_value="codex"), patch.object(
            omc_autopilot,
            "_is_pid_running",
            return_value=False,
        ), patch.object(
            omc_autopilot,
            "_run_step",
            side_effect=_fake_run_step,
        ):
            code = omc_autopilot.cmd_run(tmp_path, task_file, dry_run=False)

        assert code == 0
        saved = json.loads(state_path.read_text(encoding="utf-8"))
        assert saved["status"] == "completed"
        assert saved["steps"]["stale_recovery"]["reason"] == "dead pid before re-run: 424242"

    def test_real_run_blocks_when_existing_running_state_pid_is_alive(self, tmp_path):
        tasks_dir = tmp_path / ".omc" / "tasks"
        tasks_dir.mkdir(parents=True, exist_ok=True)
        task = {
            "id": "live-running-task",
            "title": "Live Running Task",
            "executor": "auto",
            "max_retries": 0,
            "steps": [
                {"id": "s1", "prompt": "실행", "depends_on": [], "timeout_sec": 10},
            ],
        }
        task_file = tasks_dir / "live-running-task.json"
        task_file.write_text(json.dumps(task), encoding="utf-8")

        state_dir = tmp_path / ".omc" / "state" / "autopilot"
        state_dir.mkdir(parents=True, exist_ok=True)
        state_path = state_dir / "live-running-task.json"
        original = {
            "task_id": "live-running-task",
            "status": "running",
            "pid": 424242,
            "started_at": "2026-07-05T23:59:54Z",
            "steps": {
                "s1": {
                    "status": "running",
                    "started_at": "2026-07-05T23:59:54Z",
                    "attempt": 0,
                }
            },
        }
        state_path.write_text(json.dumps(original), encoding="utf-8")

        with patch.object(omc_autopilot, "_detect_executor", return_value="codex"), patch.object(
            omc_autopilot,
            "_is_pid_running",
            return_value=True,
        ), patch.object(
            omc_autopilot,
            "_run_step",
            side_effect=AssertionError("executor should not run while existing pid is alive"),
        ):
            code = omc_autopilot.cmd_run(tmp_path, task_file, dry_run=False)

        assert code == 1
        saved = json.loads(state_path.read_text(encoding="utf-8"))
        assert saved == original

    def test_completion_requires_real_runs_fails_when_observed_dataset_does_not_increase(self, tmp_path):
        tasks_dir = tmp_path / ".omc" / "tasks"
        tasks_dir.mkdir(parents=True, exist_ok=True)
        task = {
            "id": "observed-required",
            "title": "Observed Required",
            "executor": "auto",
            "completion_requires_real_runs": True,
            "max_retries": 0,
            "steps": [
                {"id": "s1", "prompt": "실행", "depends_on": [], "timeout_sec": 10},
            ],
        }
        task_file = tasks_dir / "observed-required.json"
        task_file.write_text(json.dumps(task), encoding="utf-8")

        with patch.object(omc_autopilot, "_detect_executor", return_value="codex"), patch.object(
            omc_autopilot,
            "_run_step",
            return_value=(0, "ok", None, None),
        ):
            code = omc_autopilot.cmd_run(tmp_path, task_file, dry_run=False)

        assert code == 1
        state = json.loads(
            (tmp_path / ".omc" / "state" / "autopilot" / "observed-required.json").read_text(
                encoding="utf-8"
            )
        )
        assert state["status"] == "failed"
        assert state["failure_reason"] == "completion_requires_real_runs_unsatisfied"

        saved = json.loads(
            (tmp_path / ".omc" / "pipeline_run_result.json").read_text(encoding="utf-8")
        )
        assert saved["status"] == "failed"
        assert saved["failure_category"] == "completion_requires_real_runs_unsatisfied"

    def test_completion_requires_real_runs_succeeds_when_task_saves_observed_run_metadata(self, tmp_path):
        tasks_dir = tmp_path / ".omc" / "tasks"
        tasks_dir.mkdir(parents=True, exist_ok=True)
        task = {
            "id": "observed-request-task",
            "title": "Observed Request Task",
            "executor": "auto",
            "completion_requires_real_runs": True,
            "benchmark_source_type": "observed_request",
            "policy_pair": "baseline->candidate",
            "max_retries": 0,
            "steps": [
                {"id": "s1", "prompt": "실행", "depends_on": [], "timeout_sec": 10},
            ],
        }
        task_file = tasks_dir / "observed-request-task.json"
        task_file.write_text(json.dumps(task), encoding="utf-8")

        with patch.object(omc_autopilot, "_detect_executor", return_value="codex"), patch.object(
            omc_autopilot,
            "_run_step",
            return_value=(0, "ok", None, None),
        ):
            code = omc_autopilot.cmd_run(tmp_path, task_file, dry_run=False)

        assert code == 0
        state = json.loads(
            (tmp_path / ".omc" / "state" / "autopilot" / "observed-request-task.json").read_text(
                encoding="utf-8"
            )
        )
        assert state["status"] == "completed"

        saved = json.loads(
            (tmp_path / ".omc" / "pipeline_run_result.json").read_text(encoding="utf-8")
        )
        assert saved["status"] == "completed"
        assert saved["benchmark_source_type"] == "observed_request"
        assert saved["policy_pair"] == "baseline->candidate"


@pytest.mark.skipif(not _MODULE_PRESENT, reason="omc_autopilot.py 없음")
class TestCmdStatus:
    def test_no_history(self, tmp_path, capsys):
        code = omc_autopilot.cmd_status(tmp_path)
        assert code == 0
        captured = capsys.readouterr()
        assert "없음" in captured.out

    def test_shows_task(self, tmp_path, capsys):
        state_dir = tmp_path / ".omc" / "state" / "autopilot"
        state_dir.mkdir(parents=True, exist_ok=True)
        (state_dir / "my-task.json").write_text(
            json.dumps({
                "task_id": "my-task",
                "title": "내 태스크",
                "status": "completed",
                "executor": "gemini",
                "started_at": "2026-05-16T000000Z",
                "finished_at": "2026-05-16T000100Z",
                "steps": {"s1": {"status": "completed", "attempt": 1}},
            }),
            encoding="utf-8",
        )
        code = omc_autopilot.cmd_status(tmp_path)
        assert code == 0
        captured = capsys.readouterr()
        assert "내 태스크" in captured.out
        assert "completed" in captured.out

    def test_marks_legacy_dry_run_completed_as_simulated(self, tmp_path, capsys):
        state_dir = tmp_path / ".omc" / "state" / "autopilot"
        state_dir.mkdir(parents=True, exist_ok=True)
        (state_dir / "observed-collect.json").write_text(
            json.dumps(
                {
                    "task_id": "observed-collect",
                    "title": "Observed Run Collection",
                    "status": "completed",
                    "executor": "codex",
                    "started_at": "2026-07-03T19:48:02Z",
                    "finished_at": "2026-07-03T19:48:44Z",
                    "completion_requires_real_runs": True,
                    "steps": {
                        "collect_observed_request": {
                            "status": "completed",
                            "attempt": 1,
                            "last_output": "[DRY-RUN] 시뮬레이션 성공",
                        }
                    },
                }
            ),
            encoding="utf-8",
        )

        code = omc_autopilot.cmd_status(tmp_path)
        captured = capsys.readouterr()

        assert code == 0
        assert "Observed Run Collection" in captured.out
        assert "completed (dry-run)" in captured.out


@pytest.mark.skipif(not _MODULE_PRESENT, reason="omc_autopilot.py 없음")
class TestExpectChecks:
    def test_file_exists_pass(self, tmp_path):
        (tmp_path / "target.txt").write_text("ok")
        results = omc_autopilot._run_expect_checks(
            tmp_path, {"files": ["target.txt"], "checks": []}
        )
        assert len(results) == 1
        assert results[0]["ok"] is True

    def test_file_exists_fail(self, tmp_path):
        results = omc_autopilot._run_expect_checks(
            tmp_path, {"files": ["missing.txt"], "checks": []}
        )
        assert results[0]["ok"] is False
        assert "missing.txt" in results[0]["output"]

    def test_shell_check_pass(self, tmp_path):
        results = omc_autopilot._run_expect_checks(
            tmp_path, {"files": [], "checks": [{"cmd": "echo ok", "label": "에코"}]}
        )
        assert results[0]["ok"] is True

    def test_shell_check_fail(self, tmp_path):
        results = omc_autopilot._run_expect_checks(
            tmp_path, {"files": [], "checks": [{"cmd": "exit 1", "label": "실패 커맨드"}]}
        )
        assert results[0]["ok"] is False

    def test_unsafe_shell_operator_is_blocked(self, tmp_path):
        results = omc_autopilot._run_expect_checks(
            tmp_path,
            {"files": [], "checks": [{"cmd": "echo ok; exit 0", "label": "unsafe"}]},
        )
        assert results[0]["ok"] is False
        assert "허용되지 않은" in results[0]["output"]

    def test_pytest_k_expression_with_parentheses_is_allowed(self, tmp_path):
        results = omc_autopilot._run_expect_checks(
            tmp_path,
            {
                "files": [],
                "checks": [
                    {
                        "cmd": 'pytest -k "(smoke or regression)" --help',
                        "label": "pytest-k",
                    }
                ],
            },
        )
        assert results[0]["ok"] is True

    def test_pytest_k_regex_anchor_dollar_is_allowed(self, tmp_path):
        results = omc_autopilot._run_expect_checks(
            tmp_path,
            {
                "files": [],
                "checks": [
                    {
                        "cmd": 'pytest -k "^foo$" --help',
                        "label": "pytest-k-anchor",
                    }
                ],
            },
        )
        assert results[0]["ok"] is True

    def test_quoted_pipe_literal_is_blocked_with_python_runner(self, tmp_path):
        results = omc_autopilot._run_expect_checks(
            tmp_path,
            {
                "files": [],
                "checks": [
                    {
                        "cmd": "python3 -c 'print(\"a|b\")'",
                        "label": "quoted-pipe",
                    }
                ],
            },
        )
        assert results[0]["ok"] is False
        assert "허용되지 않은 커맨드" in results[0]["output"]

    def test_git_destructive_subcommand_is_blocked(self, tmp_path):
        results = omc_autopilot._run_expect_checks(
            tmp_path,
            {"files": [], "checks": [{"cmd": "git reset --hard", "label": "git-danger"}]},
        )
        assert results[0]["ok"] is False
        assert "허용되지 않은 git 서브커맨드" in results[0]["output"]

    def test_git_remote_readonly_subcommand_allowed(self, tmp_path):
        subprocess.run(["git", "init"], cwd=tmp_path, check=True, capture_output=True, text=True)
        results = omc_autopilot._run_expect_checks(
            tmp_path,
            {"files": [], "checks": [{"cmd": "git remote -v", "label": "git-remote"}]},
        )
        assert results[0]["ok"] is True

    def test_git_remote_mutation_subcommand_blocked(self, tmp_path):
        subprocess.run(["git", "init"], cwd=tmp_path, check=True, capture_output=True, text=True)
        results = omc_autopilot._run_expect_checks(
            tmp_path,
            {"files": [], "checks": [{"cmd": "git remote add origin https://example.com/repo.git", "label": "git-remote-add"}]},
        )
        assert results[0]["ok"] is False
        assert "허용되지 않은 git remote 인자" in results[0]["output"]

    def test_git_allowlist_from_policy_blocks_custom_subcommand(self, tmp_path):
        subprocess.run(["git", "init"], cwd=tmp_path, check=True, capture_output=True, text=True)
        omc_dir = tmp_path / ".omc"
        omc_dir.mkdir(parents=True, exist_ok=True)
        (omc_dir / "policy.json").write_text(
            json.dumps({"autopilot": {"allowed_git_subcommands": ["status", "remote", "fetch"]}}),
            encoding="utf-8",
        )
        with patch.dict("os.environ", {"OMC_POLICY_PATH": str(omc_dir / "policy.json")}):
            results = omc_autopilot._run_expect_checks(
                tmp_path,
                {"files": [], "checks": [{"cmd": "git fetch --dry-run", "label": "git-fetch"}]},
            )
        assert results[0]["ok"] is False
        assert "허용되지 않은 git 서브커맨드" in results[0]["output"]

    def test_env_prefix_is_blocked(self, tmp_path):
        results = omc_autopilot._run_expect_checks(
            tmp_path,
            {"files": [], "checks": [{"cmd": "env FOO=bar echo ok", "label": "env-echo"}]},
        )
        assert results[0]["ok"] is False
        assert "허용되지 않은 커맨드" in results[0]["output"]

    def test_bash_lc_is_blocked(self, tmp_path):
        results = omc_autopilot._run_expect_checks(
            tmp_path,
            {"files": [], "checks": [{"cmd": "bash -lc 'echo ok'", "label": "bash-lc"}]},
        )
        assert results[0]["ok"] is False
        assert "허용되지 않은 커맨드" in results[0]["output"]

    def test_python_c_is_blocked(self, tmp_path):
        results = omc_autopilot._run_expect_checks(
            tmp_path,
            {"files": [], "checks": [{"cmd": "python3 -c 'print(1)'", "label": "python-c"}]},
        )
        assert results[0]["ok"] is False
        assert "허용되지 않은 커맨드" in results[0]["output"]

    def test_node_e_is_blocked(self, tmp_path):
        results = omc_autopilot._run_expect_checks(
            tmp_path,
            {"files": [], "checks": [{"cmd": "node -e 'console.log(1)'", "label": "node-e"}]},
        )
        assert results[0]["ok"] is False
        assert "허용되지 않은 커맨드" in results[0]["output"]

    def test_policy_allowed_commands_can_enable_python3(self, tmp_path):
        omc_dir = tmp_path / ".omc"
        omc_dir.mkdir(parents=True, exist_ok=True)
        (omc_dir / "policy.json").write_text(
            json.dumps({"autopilot": {"allowed_commands": ["python3"]}}),
            encoding="utf-8",
        )
        with patch.dict("os.environ", {"OMC_POLICY_PATH": str(omc_dir / "policy.json")}):
            results = omc_autopilot._run_expect_checks(
                tmp_path,
                {"files": [], "checks": [{"cmd": "python3 --version", "label": "python3-version"}]},
            )
        assert results[0]["ok"] is True

    def test_policy_allowed_commands_can_enable_node(self, tmp_path):
        omc_dir = tmp_path / ".omc"
        omc_dir.mkdir(parents=True, exist_ok=True)
        (omc_dir / "policy.json").write_text(
            json.dumps({"autopilot": {"allowed_commands": ["node"]}}),
            encoding="utf-8",
        )
        with patch.dict("os.environ", {"OMC_POLICY_PATH": str(omc_dir / "policy.json")}):
            results = omc_autopilot._run_expect_checks(
                tmp_path,
                {"files": [], "checks": [{"cmd": "node --version", "label": "node-version"}]},
            )
        assert results[0]["ok"] is True

    def test_bash_lc_blocks_shell_operators(self, tmp_path):
        results = omc_autopilot._run_expect_checks(
            tmp_path,
            {"files": [], "checks": [{"cmd": "bash -lc 'echo a; echo b'", "label": "bash-unsafe"}]},
        )
        assert results[0]["ok"] is False
        assert "허용되지 않은 커맨드" in results[0]["output"]

    def test_policy_parse_failure_warns_and_fallbacks(self, tmp_path, capsys):
        subprocess.run(["git", "init"], cwd=tmp_path, check=True, capture_output=True, text=True)
        omc_dir = tmp_path / ".omc"
        omc_dir.mkdir(parents=True, exist_ok=True)
        (omc_dir / "policy.json").write_text("{bad json", encoding="utf-8")
        with patch.dict("os.environ", {"OMC_POLICY_PATH": str(omc_dir / "policy.json")}):
            results = omc_autopilot._run_expect_checks(
                tmp_path,
                {"files": [], "checks": [{"cmd": "git remote -v", "label": "git-remote"}]},
            )
        captured = capsys.readouterr()
        assert "policy parse failed" in captured.out
        assert results[0]["ok"] is True

    def test_policy_parse_failure_warns_only_once_for_same_error(self, tmp_path, capsys):
        subprocess.run(["git", "init"], cwd=tmp_path, check=True, capture_output=True, text=True)
        omc_dir = tmp_path / ".omc"
        omc_dir.mkdir(parents=True, exist_ok=True)
        policy_path = omc_dir / "policy.json"
        policy_path.write_text("{bad json", encoding="utf-8")
        with patch.dict("os.environ", {"OMC_POLICY_PATH": str(policy_path)}):
            omc_autopilot._run_expect_checks(
                tmp_path,
                {"files": [], "checks": [{"cmd": "git remote -v", "label": "git-remote"}]},
            )
            omc_autopilot._run_expect_checks(
                tmp_path,
                {"files": [], "checks": [{"cmd": "git remote -v", "label": "git-remote"}]},
            )
        captured = capsys.readouterr()
        assert captured.out.count("policy parse failed") == 1

    def test_policy_missing_file_is_silent_and_fallbacks(self, tmp_path, capsys):
        subprocess.run(["git", "init"], cwd=tmp_path, check=True, capture_output=True, text=True)
        missing_policy = tmp_path / ".omc" / "missing-policy.json"
        with patch.dict("os.environ", {"OMC_POLICY_PATH": str(missing_policy)}):
            results = omc_autopilot._run_expect_checks(
                tmp_path,
                {"files": [], "checks": [{"cmd": "git remote -v", "label": "git-remote"}]},
            )
        captured = capsys.readouterr()
        assert "policy parse failed" not in captured.out
        assert results[0]["ok"] is True

    def test_dry_run_always_pass(self, tmp_path):
        results = omc_autopilot._run_expect_checks(
            tmp_path,
            {"files": ["nonexistent.txt"], "checks": [{"cmd": "exit 1", "label": "fail"}]},
            dry_run=True,
        )
        assert all(r["ok"] for r in results)

    def test_build_retry_prompt_injects_failures(self):
        failures = [
            {"label": "테스트 실패", "output": "Expected true but got false"},
        ]
        result = omc_autopilot._build_retry_prompt("원래 프롬프트", 1, failures)
        assert "이전 시도 1회 실패" in result
        assert "테스트 실패" in result
        assert "원래 프롬프트" in result

    def test_build_retry_prompt_no_failures(self):
        result = omc_autopilot._build_retry_prompt("원래 프롬프트", 1, [])
        assert result == "원래 프롬프트"

    def test_build_retry_prompt_does_not_leak_non_string_prev_verdict(self):
        result = omc_autopilot._build_retry_prompt(
            "원래 프롬프트",
            1,
            [],
            prev_verdict=object(),  # 내부 sentinel 같은 비문자 값이 와도 노출되면 안 된다.
        )
        assert result == "원래 프롬프트"


@pytest.mark.skipif(not _MODULE_PRESENT, reason="omc_autopilot.py 없음")
def test_build_benchmark_report_explicitly_marks_invalid_started_at():
    report = omc_autopilot._build_benchmark_report(
        {
            "status": "failed",
            "started_at": "not-a-date",
            "finished_at": "2026-05-31T00:00:00Z",
            "steps": {"task": {"status": "failed"}},
        }
    )
    assert report["data_quality_status"] == "invalid_started_at"
    assert report["duration_sec"] is None


@pytest.mark.skipif(not _MODULE_PRESENT, reason="omc_autopilot.py 없음")
def test_build_benchmark_report_preserves_operational_validation_metadata():
    report = omc_autopilot._build_benchmark_report(
        {
            "status": "completed",
            "started_at": "2026-07-05T18:14:23Z",
            "finished_at": "2026-07-05T18:41:06Z",
            "operational_validation_stage": "operator_experience_validation",
            "operational_validation_goal": "validate_operator_experience_explanatory_power",
            "steps": {},
        }
    )
    assert report["operational_validation_stage"] == "operator_experience_validation"
    assert report["operational_validation_goal"] == "validate_operator_experience_explanatory_power"


@pytest.mark.skipif(not _MODULE_PRESENT, reason="omc_autopilot.py 없음")
def test_pipeline_status_icons_cover_all_operational_states():
    expected_states = {
        "completed",
        "failed",
        "running",
        "aborted",
        "canceled",
        "cancelled",
        "timeout",
        "pending",
        "paused",
    }
    icon_map = getattr(omc_autopilot, "_STATUS_ICON_MAP", {})
    assert expected_states.issubset(set(icon_map.keys()))


@pytest.mark.skipif(not _MODULE_PRESENT, reason="omc_autopilot.py 없음")
def test_status_recent_limit_env_default_and_override(monkeypatch, tmp_path, capsys):
    state_dir = tmp_path / ".omc" / "state" / "autopilot"
    state_dir.mkdir(parents=True, exist_ok=True)
    for i in range(25):
        (state_dir / f"t-{i:02d}.json").write_text(
            json.dumps(
                {
                    "task_id": f"t-{i:02d}",
                    "title": f"task-{i:02d}",
                    "status": "completed",
                    "started_at": f"2026-05-31T00:{i:02d}:00Z",
                    "finished_at": f"2026-05-31T00:{i:02d}:30Z",
                    "steps": {},
                }
            ),
            encoding="utf-8",
        )
    monkeypatch.delenv("OMC_AUTOPILOT_STATUS_LIMIT", raising=False)
    omc_autopilot.cmd_status(tmp_path)
    captured = capsys.readouterr()
    assert captured.out.count("task-") == 20

    monkeypatch.setenv("OMC_AUTOPILOT_STATUS_LIMIT", "5")
    omc_autopilot.cmd_status(tmp_path)
    captured = capsys.readouterr()
    assert captured.out.count("task-") == 5


@pytest.mark.skipif(not _MODULE_PRESENT, reason="omc_autopilot.py 없음")
def test_status_task_id_searches_before_limit(monkeypatch, tmp_path, capsys):
    state_dir = tmp_path / ".omc" / "state" / "autopilot"
    state_dir.mkdir(parents=True, exist_ok=True)
    for i in range(25):
        task_id = f"t-{i:02d}"
        (state_dir / f"{task_id}.json").write_text(
            json.dumps(
                {
                    "task_id": task_id,
                    "title": f"task-{i:02d}",
                    "status": "completed",
                    "started_at": f"2026-05-31T00:{i:02d}:00Z",
                    "finished_at": f"2026-05-31T00:{i:02d}:30Z",
                    "steps": {},
                }
            ),
            encoding="utf-8",
        )
    monkeypatch.setenv("OMC_AUTOPILOT_STATUS_LIMIT", "5")
    code = omc_autopilot.cmd_status(tmp_path, task_id="t-00")
    captured = capsys.readouterr()
    assert code == 0
    assert "task-00" in captured.out


@pytest.mark.skipif(not _MODULE_PRESENT, reason="omc_autopilot.py 없음")
def test_status_reports_invalid_json_instead_of_silent_skip(tmp_path, capsys):
    state_dir = tmp_path / ".omc" / "state" / "autopilot"
    state_dir.mkdir(parents=True, exist_ok=True)
    (state_dir / "broken.json").write_text("{bad json", encoding="utf-8")

    code = omc_autopilot.cmd_status(tmp_path)
    captured = capsys.readouterr()

    assert code == 0
    assert "JSON 파싱 실패" in captured.out


@pytest.mark.skipif(not _MODULE_PRESENT, reason="omc_autopilot.py 없음")
class TestCmdRunWithExpect:
    def _make_task_with_expect(self, tmp_path: Path, expect: dict) -> Path:
        tasks_dir = tmp_path / ".omc" / "tasks"
        tasks_dir.mkdir(parents=True, exist_ok=True)
        task = {
            "id": "expect-task",
            "title": "expect 테스트",
            "executor": "auto",
            "max_retries": 0,
            "steps": [
                {
                    "id": "s1",
                    "prompt": "파일을 만드세요",
                    "depends_on": [],
                    "timeout_sec": 10,
                    "expect": expect,
                }
            ],
        }
        p = tasks_dir / "expect-task.json"
        p.write_text(json.dumps(task), encoding="utf-8")
        return p

    def test_expect_file_pass_dry_run(self, tmp_path):
        task_file = self._make_task_with_expect(
            tmp_path, {"files": ["nonexistent.txt"], "checks": []}
        )
        # dry-run은 expect도 항상 통과
        code = omc_autopilot.cmd_run(tmp_path, task_file, dry_run=True)
        assert code == 0

    def test_expect_shell_pass(self, tmp_path):
        task_file = self._make_task_with_expect(
            tmp_path, {"files": [], "checks": [{"cmd": "echo ok", "label": "에코"}]}
        )
        # LLM을 실제 호출하지 않도록 dry_run=True
        code = omc_autopilot.cmd_run(tmp_path, task_file, dry_run=True)
        assert code == 0

    def test_cmd_run_blocks_when_task_requires_clean_scope_and_staged_changes_exist(
        self, tmp_path, monkeypatch
    ):
        subprocess.run(["git", "init"], cwd=tmp_path, check=True, capture_output=True, text=True)
        subprocess.run(
            ["git", "config", "user.name", "OMC Test"],
            cwd=tmp_path,
            check=True,
            capture_output=True,
            text=True,
        )
        subprocess.run(
            ["git", "config", "user.email", "omc@example.com"],
            cwd=tmp_path,
            check=True,
            capture_output=True,
            text=True,
        )
        tracked = tmp_path / "tracked.py"
        tracked.write_text("print('dirty')\n", encoding="utf-8")
        subprocess.run(["git", "add", "tracked.py"], cwd=tmp_path, check=True, capture_output=True, text=True)

        tasks_dir = tmp_path / ".omc" / "tasks"
        tasks_dir.mkdir(parents=True, exist_ok=True)
        task_file = tasks_dir / "observed-collect.json"
        task_file.write_text(
            json.dumps(
                {
                    "id": "observed-collect",
                    "title": "Observed Run Collection",
                    "executor": "auto",
                    "require_clean_scope": True,
                    "max_retries": 0,
                    "steps": [
                        {
                            "id": "s1",
                            "prompt": "collect observed request",
                            "depends_on": [],
                            "timeout_sec": 10,
                        }
                    ],
                }
            ),
            encoding="utf-8",
        )

        monkeypatch.setattr(omc_autopilot, "_detect_executor", lambda _pref: "codex")

        code = omc_autopilot.cmd_run(tmp_path, task_file, dry_run=True)
        assert code == 1


@pytest.mark.skipif(not _MODULE_PRESENT, reason="omc_autopilot.py 없음")
def test_detect_executor_fail_fast_when_no_executor(monkeypatch):
    monkeypatch.setenv("OMC_EXECUTOR", "")
    monkeypatch.setattr("shutil.which", lambda _name: None)
    with pytest.raises(RuntimeError, match="executor"):
        omc_autopilot._detect_executor("auto")


@pytest.mark.skipif(not _MODULE_PRESENT, reason="omc_autopilot.py 없음")
def test_detect_executor_fail_fast_when_preferred_invalid(monkeypatch):
    monkeypatch.setattr("shutil.which", lambda _name: None)
    with pytest.raises(RuntimeError, match="executor"):
        omc_autopilot._detect_executor("foo-executor")


@pytest.mark.skipif(not _MODULE_PRESENT, reason="omc_autopilot.py 없음")
def test_detect_executor_env_choice_must_be_resolvable(monkeypatch):
    monkeypatch.setenv("OMC_EXECUTOR", "codex")
    monkeypatch.setattr("shutil.which", lambda _name: None)
    with pytest.raises(RuntimeError, match="executor not found"):
        omc_autopilot._detect_executor("auto")


@pytest.mark.skipif(not _MODULE_PRESENT, reason="omc_autopilot.py 없음")
def test_checkout_new_branch_switches_when_branch_already_exists(tmp_path):
    subprocess.run(["git", "init"], cwd=tmp_path, check=True, capture_output=True, text=True)
    subprocess.run(["git", "checkout", "-b", "feat/x"], cwd=tmp_path, check=True, capture_output=True, text=True)
    subprocess.run(["git", "checkout", "-b", "main"], cwd=tmp_path, check=True, capture_output=True, text=True)
    selected = omc_autopilot._checkout_new_branch(tmp_path, "feat/x")
    assert selected == "feat/x"


@pytest.mark.skipif(not _MODULE_PRESENT, reason="omc_autopilot.py 없음")
def test_checkout_new_branch_raises_permission_error(monkeypatch, tmp_path):
    class _R:
        def __init__(self, code: int, stderr: str = "", stdout: str = ""):
            self.returncode = code
            self.stderr = stderr
            self.stdout = stdout

    calls = {"count": 0}

    def fake_run(cmd, capture_output=True, text=True, cwd=None):
        calls["count"] += 1
        if cmd[:4] == ["git", "rev-parse", "--verify", "--quiet"]:
            return _R(1)
        if cmd[:3] == ["git", "checkout", "-b"]:
            return _R(128, "fatal: cannot lock ref 'refs/heads/x': Operation not permitted")
        return _R(1, "unexpected")

    monkeypatch.setattr(omc_autopilot.subprocess, "run", fake_run)
    with pytest.raises(RuntimeError, match="failed_branch_permission"):
        omc_autopilot._checkout_new_branch(tmp_path, "feat/x")
    assert calls["count"] >= 2


@pytest.mark.skipif(not _MODULE_PRESENT, reason="omc_autopilot.py 없음")
def test_pipeline_status_is_read_only_for_stale_running_with_dead_pid(monkeypatch, tmp_path):
    omc_dir = tmp_path / ".omc"
    omc_dir.mkdir(parents=True, exist_ok=True)
    result_path = omc_dir / "pipeline_run_result.json"
    result_path.write_text(
        json.dumps(
            {
                "status": "running",
                "mode": "full",
                "branch": "feat/x",
                "executor": "codex",
                "pid": 999999,
                "started_at": "2026-06-01T00:00:00Z",
                "finished_at": None,
                "steps": {"task": {"status": "completed"}},
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(omc_autopilot, "_is_pid_running", lambda _pid: False)
    code = omc_autopilot._cmd_pipeline_status_once(tmp_path)
    assert code == 0
    updated = json.loads(result_path.read_text(encoding="utf-8"))
    assert updated["status"] == "running"
    assert updated["finished_at"] is None
    assert "stale_recovery" not in updated["steps"]


@pytest.mark.skipif(not _MODULE_PRESENT, reason="omc_autopilot.py 없음")
def test_pipeline_status_is_read_only_for_legacy_running_without_pid(tmp_path):
    omc_dir = tmp_path / ".omc"
    omc_dir.mkdir(parents=True, exist_ok=True)
    result_path = omc_dir / "pipeline_run_result.json"
    result_path.write_text(
        json.dumps(
            {
                "status": "running",
                "mode": "full",
                "branch": "feat/x",
                "executor": "codex",
                "started_at": "2026-06-01T00:00:00Z",
                "finished_at": None,
                "steps": {"task": {"status": "completed"}},
            }
        ),
        encoding="utf-8",
    )
    code = omc_autopilot._cmd_pipeline_status_once(tmp_path)
    assert code == 0
    updated = json.loads(result_path.read_text(encoding="utf-8"))
    assert updated["status"] == "running"
    assert "stale_recovery" not in updated["steps"]


@pytest.mark.skipif(not _MODULE_PRESENT, reason="omc_autopilot.py 없음")
def test_is_pid_running_treats_permission_error_as_alive(monkeypatch):
    def _raise_permission(_pid, _sig):
        raise PermissionError("EPERM")

    monkeypatch.setattr(omc_autopilot.os, "kill", _raise_permission)
    assert omc_autopilot._is_pid_running(12345) is True


@pytest.mark.skipif(not _MODULE_PRESENT, reason="omc_autopilot.py 없음")
def test_pipeline_status_does_not_overwrite_when_pid_state_unknown(monkeypatch, tmp_path):
    omc_dir = tmp_path / ".omc"
    omc_dir.mkdir(parents=True, exist_ok=True)
    result_path = omc_dir / "pipeline_run_result.json"
    result_path.write_text(
        json.dumps(
            {
                "status": "running",
                "mode": "full",
                "branch": "feat/x",
                "executor": "codex",
                "pid": 999999,
                "started_at": "2026-06-01T00:00:00Z",
                "finished_at": None,
                "steps": {"task": {"status": "completed"}},
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(omc_autopilot, "_is_pid_running", lambda _pid: None)
    code = omc_autopilot._cmd_pipeline_status_once(tmp_path)
    assert code == 0
    updated = json.loads(result_path.read_text(encoding="utf-8"))
    assert updated["status"] == "running"
    assert "stale_recovery" not in updated["steps"]


@pytest.mark.skipif(not _MODULE_PRESENT, reason="omc_autopilot.py 없음")
def test_ensure_staged_never_uses_git_add_all(monkeypatch, tmp_path):
    calls = []

    class _R:
        def __init__(self, out=""):
            self.stdout = out
            self.returncode = 0

    def fake_run(cmd, capture_output=False, text=False, cwd=None):
        calls.append(cmd)
        if cmd[:3] == ["git", "diff", "--name-only"]:
            return _R("scripts/a.py\n.cursor/rules/omc-always.md\n")
        if cmd[:4] == ["git", "diff", "--staged", "--name-only"]:
            return _R("")
        return _R("")

    monkeypatch.setattr(omc_autopilot.subprocess, "run", fake_run)
    omc_autopilot._ensure_staged(tmp_path, dry_run=False, label="TASK")

    add_calls = [c for c in calls if len(c) >= 2 and c[0] == "git" and c[1] == "add"]
    assert add_calls, "git add call is expected"
    for c in add_calls:
        assert c != ["git", "add", "-A"], "git add -A must not be used"
    # only safe path should be staged
    assert any("scripts/a.py" in c for c in add_calls)
    assert not any(".cursor/rules/omc-always.md" in c for c in add_calls)


@pytest.mark.skipif(not _MODULE_PRESENT, reason="omc_autopilot.py 없음")
def test_ensure_staged_blocks_custom_paths_by_default(monkeypatch, tmp_path):
    calls = []

    class _R:
        def __init__(self, out=""):
            self.stdout = out
            self.returncode = 0

    def fake_run(cmd, capture_output=False, text=False, cwd=None):
        calls.append(cmd)
        if cmd[:3] == ["git", "diff", "--name-only"]:
            return _R("custom/file.txt\n")
        if cmd[:4] == ["git", "diff", "--staged", "--name-only"]:
            return _R("")
        return _R("")

    monkeypatch.setattr(omc_autopilot.subprocess, "run", fake_run)
    omc_autopilot._ensure_staged(tmp_path, dry_run=False, label="TASK")

    add_calls = [c for c in calls if len(c) >= 2 and c[0] == "git" and c[1] == "add"]
    assert not any("custom/file.txt" in c for c in add_calls), "custom path should be blocked by default allowlist"


@pytest.mark.skipif(not _MODULE_PRESENT, reason="omc_autopilot.py 없음")
def test_ensure_staged_allows_custom_prefix_from_env(monkeypatch, tmp_path):
    calls = []

    class _R:
        def __init__(self, out=""):
            self.stdout = out
            self.returncode = 0

    def fake_run(cmd, capture_output=False, text=False, cwd=None):
        calls.append(cmd)
        if cmd[:3] == ["git", "diff", "--name-only"]:
            return _R("custom/file.txt\n")
        if cmd[:4] == ["git", "diff", "--staged", "--name-only"]:
            return _R("")
        return _R("")

    monkeypatch.setattr(omc_autopilot.subprocess, "run", fake_run)
    monkeypatch.setenv("OMC_PIPELINE_STAGE_ALLOW_PREFIXES", "custom/")
    omc_autopilot._ensure_staged(tmp_path, dry_run=False, label="TASK")

    add_calls = [c for c in calls if len(c) >= 2 and c[0] == "git" and c[1] == "add"]
    assert any("custom/file.txt" in c for c in add_calls), "custom path should be stageable via explicit allow prefix"
