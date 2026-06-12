from __future__ import annotations

import json
from pathlib import Path

import omc_autopilot


def _make_run(tmp_path: Path, run_id: str, *, branch: str, status: str, verdict: str | None = None) -> None:
    run_dir = tmp_path / ".omc" / "runs" / run_id
    run_dir.mkdir(parents=True)
    data = {
        "status": status,
        "branch": branch,
        "executor": "gemini",
        "started_at": f"2026-06-12T10:00:00Z",
        "finished_at": f"2026-06-12T10:05:00Z",
        "steps": {
            "review": {"status": "completed", "verdict": verdict} if verdict else {"status": "completed"},
        },
    }
    (run_dir / "result.json").write_text(json.dumps(data), encoding="utf-8")


# ── 태스크 1: cmd_runs()가 최근 N개 목록 출력 ──────────────────────────────

def test_cmd_runs_lists_recent_results(tmp_path: Path, capsys) -> None:
    _make_run(tmp_path, "run-a", branch="feat/a", status="completed")
    _make_run(tmp_path, "run-b", branch="feat/b", status="failed")

    rc = omc_autopilot.cmd_runs(tmp_path, limit=20)
    out = capsys.readouterr().out

    assert rc == 0
    assert "run-a" in out or "feat/a" in out
    assert "run-b" in out or "feat/b" in out


def test_cmd_runs_empty_directory(tmp_path: Path, capsys) -> None:
    (tmp_path / ".omc" / "runs").mkdir(parents=True)
    rc = omc_autopilot.cmd_runs(tmp_path, limit=20)
    assert rc == 0
    out = capsys.readouterr().out
    assert out.strip() != ""  # 안내 메시지 있어야 함


def test_cmd_runs_no_runs_dir(tmp_path: Path, capsys) -> None:
    rc = omc_autopilot.cmd_runs(tmp_path, limit=20)
    assert rc == 0


# ── 태스크 2: --branch / --status 필터 ───────────────────────────────────

def test_cmd_runs_filters_by_branch(tmp_path: Path, capsys) -> None:
    _make_run(tmp_path, "run-a", branch="feat/login", status="completed")
    _make_run(tmp_path, "run-b", branch="fix/typo", status="completed")

    rc = omc_autopilot.cmd_runs(tmp_path, limit=20, branch_filter="feat/login")
    out = capsys.readouterr().out

    assert rc == 0
    assert "feat/login" in out
    assert "fix/typo" not in out


def test_cmd_runs_filters_by_status(tmp_path: Path, capsys) -> None:
    _make_run(tmp_path, "run-a", branch="feat/a", status="completed")
    _make_run(tmp_path, "run-b", branch="feat/b", status="failed")

    rc = omc_autopilot.cmd_runs(tmp_path, limit=20, status_filter="failed")
    out = capsys.readouterr().out

    assert rc == 0
    assert "failed" in out
    assert "feat/a" not in out


def test_cmd_runs_limit_respected(tmp_path: Path, capsys) -> None:
    for i in range(5):
        _make_run(tmp_path, f"run-{i:02d}", branch=f"feat/{i}", status="completed")

    rc = omc_autopilot.cmd_runs(tmp_path, limit=2)
    out = capsys.readouterr().out

    assert rc == 0
    # 최대 2개만 표시되어야 함
    shown = [line for line in out.splitlines() if "feat/" in line]
    assert len(shown) <= 2
