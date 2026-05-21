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
from pathlib import Path

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


@pytest.mark.skipif(_MODULE_PRESENT, reason="모듈 없음 — RED 단계 확인용")
def test_module_missing():
    """omc_autopilot.py 가 없으면 이 테스트가 실패해야 합니다."""
    assert False, "omc_autopilot.py 가 없습니다 — 구현 후 재실행 필요"


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
