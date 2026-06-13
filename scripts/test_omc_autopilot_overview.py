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


def test_summarize_run_recommends_review_failure_action() -> None:
    summary = omc_autopilot._summarize_run_record(
        "run-review-failed",
        {
            "status": "failed",
            "branch": "feat/review-failed",
            "executor": "codex",
            "steps": {"review": {"status": "failed"}},
            "failure_category": "review:failed",
        },
    )

    assert summary["next_action"] == "inspect review failures"


def test_summarize_run_recommends_task_failure_action() -> None:
    summary = omc_autopilot._summarize_run_record(
        "run-task-failed",
        {
            "status": "failed",
            "branch": "feat/task-failed",
            "executor": "codex",
            "steps": {"task": {"status": "failed"}},
            "failure_category": "task:failed",
        },
    )

    assert summary["next_action"] == "fix task failure and retry"


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


def test_cmd_overview_orders_problem_runs_first(tmp_path: Path, capsys) -> None:
    _make_run(
        tmp_path,
        "run-completed",
        branch="feat/completed",
        status="completed",
        steps={"review": {"status": "completed"}},
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
            "status": "hold",
            "branch": "feat/current-hold",
            "executor": "claude",
            "started_at": "2026-06-13T10:00:00+09:00",
            "steps": {"critique": {"status": "failed"}},
            "failure_category": "critique:hold",
        },
    )

    rc = omc_autopilot.cmd_overview(tmp_path, limit=5)
    out = capsys.readouterr().out

    assert rc == 0
    rows = [line for line in out.splitlines() if " | " in line and not line.startswith("run_id |")]
    assert rows[0].startswith("current | feat/current-hold | hold"), rows
    assert "feat/failed" in rows[1], rows
    assert "feat/completed" in rows[-1], rows


def test_cmd_overview_places_failed_before_current_completed(tmp_path: Path, capsys) -> None:
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
            "status": "completed",
            "branch": "feat/current-completed",
            "executor": "codex",
            "started_at": "2026-06-13T11:00:00+09:00",
            "finished_at": "2026-06-13T11:05:00+09:00",
            "steps": {"review": {"status": "completed"}},
        },
    )

    rc = omc_autopilot.cmd_overview(tmp_path, limit=5)
    out = capsys.readouterr().out

    assert rc == 0
    rows = [line for line in out.splitlines() if " | " in line and not line.startswith("run_id |")]
    assert rows[0].startswith("run-failed | feat/failed | failed"), rows
    assert rows[1].startswith("current | feat/current-completed | completed"), rows


def test_cmd_overview_orders_same_status_by_latest_timestamp(tmp_path: Path, capsys) -> None:
    _make_run(
        tmp_path,
        "run-older",
        branch="feat/older",
        status="failed",
        steps={"task": {"status": "failed"}},
        failure_category="task:failed",
    )
    _write_json(
        tmp_path / ".omc" / "runs" / "run-newer" / "result.json",
        {
            "status": "failed",
            "branch": "feat/newer",
            "executor": "codex",
            "started_at": "2026-06-13T12:00:00+09:00",
            "finished_at": "2026-06-13T12:10:00+09:00",
            "steps": {"task": {"status": "failed"}},
            "failure_category": "task:failed",
        },
    )

    rc = omc_autopilot.cmd_overview(tmp_path, limit=5)
    out = capsys.readouterr().out

    assert rc == 0
    rows = [line for line in out.splitlines() if " | " in line and not line.startswith("run_id |")]
    assert rows[0].startswith("run-newer | feat/newer | failed"), rows
    assert rows[1].startswith("run-older | feat/older | failed"), rows


def test_cmd_overview_handles_no_runs(tmp_path: Path, capsys) -> None:
    rc = omc_autopilot.cmd_overview(tmp_path, limit=5)
    out = capsys.readouterr().out

    assert rc == 0
    assert "실행 기록 없음" in out
