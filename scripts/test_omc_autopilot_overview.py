from __future__ import annotations

import json
from pathlib import Path

import omc_autopilot


def _write_json(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data), encoding="utf-8")


def _make_run(
    tmp_path: Path,
    run_id: str,
    *,
    branch: str,
    status: str,
    steps: dict | None = None,
    failure_category: str | None = None,
) -> None:
    _write_json(
        tmp_path / ".omc" / "runs" / run_id / "result.json",
        {
            "status": status,
            "branch": branch,
            "executor": "codex",
            "started_at": "2026-06-13T09:00:00+09:00",
            "finished_at": "2026-06-13T09:05:00+09:00",
            "steps": steps or {},
            "failure_category": failure_category,
        },
    )


def test_summarize_run_marks_stale_running_pipeline() -> None:
    summary = omc_autopilot._summarize_run_record(
        "run-stale",
        {
            "status": "running",
            "branch": "feat/stale",
            "executor": "codex",
            "steps": {"task": {"status": "completed"}},
            "stale_reason": "pipeline pid not running: 99999",
        },
    )

    assert summary["stale"] is True
    assert summary["next_action"] == "recover stale pipeline"
    assert summary["current_step"] == "task"


def test_summarize_run_recommends_review_for_hold() -> None:
    summary = omc_autopilot._summarize_run_record(
        "run-hold",
        {
            "status": "hold",
            "branch": "feat/hold",
            "executor": "claude",
            "failure_category": "critique:hold",
            "steps": {"critique": {"status": "failed"}},
        },
    )

    assert summary["stale"] is False
    assert summary["failure_reason"] == "critique:hold"
    assert summary["next_action"] == "inspect critique findings"


def test_cmd_overview_prints_one_screen_summary(tmp_path: Path, capsys) -> None:
    _make_run(
        tmp_path,
        "run-completed",
        branch="feat/completed",
        status="completed",
        steps={"review": {"status": "completed", "verdict": "APPROVE"}},
    )
    _make_run(
        tmp_path,
        "run-failed",
        branch="feat/failed",
        status="failed",
        steps={"task": {"status": "failed"}},
        failure_category="task:failed",
    )
    _write_json(
        tmp_path / ".omc" / "pipeline_run_result.json",
        {
            "status": "running",
            "branch": "feat/live",
            "executor": "gemini",
            "pid": 12345,
            "steps": {"review": {"status": "completed"}},
        },
    )

    rc = omc_autopilot.cmd_overview(tmp_path, limit=5)
    out = capsys.readouterr().out

    assert rc == 0
    assert "OMC Autopilot Overview" in out
    assert "feat/live" in out
    assert "feat/completed" in out
    assert "feat/failed" in out
    assert "next_action" in out


def test_cmd_overview_handles_no_runs(tmp_path: Path, capsys) -> None:
    rc = omc_autopilot.cmd_overview(tmp_path, limit=5)
    out = capsys.readouterr().out

    assert rc == 0
    assert "실행 기록 없음" in out
