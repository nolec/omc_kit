from __future__ import annotations

import json
from pathlib import Path

import omc_autopilot

OBSERVED_TASK_PATH = Path(".omc/tasks/observed-collect.json")


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


def test_cmd_overview_prints_observed_progress_summary(tmp_path: Path, capsys) -> None:
    observed_task = {
        "id": "observed-collect",
        "benchmark_source_type": "observed_request",
        "policy_pair": "baseline->candidate",
    }
    _write_json(tmp_path / ".omc" / "tasks" / "observed-collect.json", observed_task)
    reverse_task = {
        "id": "observed-collect-reverse",
        "benchmark_source_type": "observed_output",
        "policy_pair": "candidate->baseline",
        "comparison_scope": "cross_surface",
        "baseline_response_sample": "baseline output sample",
        "candidate_response_sample": "candidate output sample",
    }
    _write_json(tmp_path / ".omc" / "tasks" / "observed-collect-reverse.json", reverse_task)

    omc_autopilot._save_pipeline_result(
        tmp_path,
        {
            "__run_id": "run-observed-a",
            "task_id": "observed-collect",
            "status": "completed",
            "branch": "feat/observed-a",
            "executor": "codex",
            "started_at": "2026-06-13T09:00:00+09:00",
            "finished_at": "2026-06-13T09:05:00+09:00",
            "steps": {"review": {"status": "completed", "verdict": "APPROVE"}},
        },
    )
    omc_autopilot._save_pipeline_result(
        tmp_path,
        {
            "__run_id": "run-observed-b",
            "task_id": "observed-collect-reverse",
            "status": "completed",
            "branch": "feat/observed-b",
            "executor": "codex",
            "started_at": "2026-06-13T10:00:00+09:00",
            "finished_at": "2026-06-13T10:05:00+09:00",
            "steps": {"review": {"status": "completed", "verdict": "APPROVE"}},
        },
    )

    rc = omc_autopilot.cmd_overview(tmp_path, limit=5)
    out = capsys.readouterr().out

    assert rc == 0
    assert "observed_samples=2" in out
    assert "distinct_policy_pairs=2" in out


def test_cmd_overview_prints_readiness_status_and_blocker_for_observed_progress(
    tmp_path: Path, capsys
) -> None:
    observed_task = {
        "id": "observed-collect",
        "benchmark_source_type": "observed_output",
        "policy_pair": "baseline->candidate",
        "comparison_scope": "cross_surface",
        "baseline_response_sample": "baseline output sample",
        "candidate_response_sample": "candidate output sample",
    }
    _write_json(tmp_path / ".omc" / "tasks" / "observed-collect.json", observed_task)

    for index in range(19):
        omc_autopilot._save_pipeline_result(
            tmp_path,
            {
                "__run_id": f"run-observed-{index}",
                "task_id": "observed-collect",
                "status": "completed",
                "branch": f"feat/observed-{index}",
                "executor": "codex",
                "started_at": "2026-06-13T09:00:00+09:00",
                "finished_at": "2026-06-13T09:05:00+09:00",
                "steps": {"review": {"status": "completed", "verdict": "APPROVE"}},
            },
        )

    rc = omc_autopilot.cmd_overview(tmp_path, limit=5)
    out = capsys.readouterr().out

    assert rc == 0
    assert "observed_samples=19" in out
    assert "readiness_status=not ready: samples 19/20, same-surface 0/1, policy pairs 1/2" in out
    assert "baseline_comparison_status=deferred" in out
    assert "next_kpi_blocker=insufficient_observed_samples" in out
    assert "next_collection_focus=collect_more_observed_runs" in out


def test_cmd_overview_ignores_invalid_observed_output_noise_in_readiness_counts(
    tmp_path: Path, capsys
) -> None:
    valid_forward = {
        "id": "observed-valid-forward",
        "benchmark_source_type": "observed_output",
        "policy_pair": "baseline->candidate",
        "comparison_scope": "cross_surface",
        "baseline_response_sample": "baseline output sample",
        "candidate_response_sample": "candidate output sample",
    }
    valid_reverse = {
        "id": "observed-valid-reverse",
        "benchmark_source_type": "observed_output",
        "policy_pair": "candidate->baseline",
        "comparison_scope": "cross_surface",
        "baseline_response_sample": "baseline output sample",
        "candidate_response_sample": "candidate output sample",
    }
    invalid_same_surface = {
        "id": "observed-invalid-same-surface",
        "benchmark_source_type": "observed_output",
        "policy_pair": "baseline->candidate",
        "comparison_scope": "same_surface",
        "baseline_response_sample": "baseline output sample",
        "candidate_response_sample": "",
    }
    _write_json(tmp_path / ".omc" / "tasks" / "observed-valid-forward.json", valid_forward)
    _write_json(tmp_path / ".omc" / "tasks" / "observed-valid-reverse.json", valid_reverse)
    _write_json(tmp_path / ".omc" / "tasks" / "observed-invalid-same-surface.json", invalid_same_surface)

    for index in range(10):
        omc_autopilot._save_pipeline_result(
            tmp_path,
            {
                "__run_id": f"run-valid-forward-{index}",
                "task_id": "observed-valid-forward",
                "status": "completed",
                "branch": f"feat/valid-forward-{index}",
                "executor": "codex",
                "started_at": "2026-06-13T09:00:00+09:00",
                "finished_at": "2026-06-13T09:05:00+09:00",
                "steps": {"review": {"status": "completed", "verdict": "APPROVE"}},
            },
        )
    for index in range(9):
        omc_autopilot._save_pipeline_result(
            tmp_path,
            {
                "__run_id": f"run-valid-reverse-{index}",
                "task_id": "observed-valid-reverse",
                "status": "completed",
                "branch": f"feat/valid-reverse-{index}",
                "executor": "codex",
                "started_at": "2026-06-13T10:00:00+09:00",
                "finished_at": "2026-06-13T10:05:00+09:00",
                "steps": {"review": {"status": "completed", "verdict": "APPROVE"}},
            },
        )

    omc_autopilot._save_pipeline_result(
        tmp_path,
        {
            "__run_id": "run-invalid-same-surface",
            "task_id": "observed-invalid-same-surface",
            "status": "completed",
            "branch": "feat/invalid-same-surface",
            "executor": "codex",
            "started_at": "2026-06-13T11:00:00+09:00",
            "finished_at": "2026-06-13T11:05:00+09:00",
            "steps": {"review": {"status": "completed", "verdict": "APPROVE"}},
        },
    )

    rc = omc_autopilot.cmd_overview(tmp_path, limit=5)
    out = capsys.readouterr().out

    assert rc == 0
    assert "observed_samples=19" in out
    assert "readiness_status=not ready: samples 19/20, same-surface 0/1, policy pairs 2/2" in out
    assert "baseline_comparison_status=deferred" in out
    assert "next_kpi_blocker=insufficient_observed_samples" in out
    assert "next_collection_focus=collect_more_observed_runs" in out
    assert "rejected_observed_output=1" in out
    assert "rejected_reasons=missing_candidate_response_sample:1" in out


def test_cmd_overview_preserves_baseline_not_ready_blocker_from_saved_runs(
    tmp_path: Path, capsys
) -> None:
    observed_task = {
        "id": "observed-ready-but-drifted",
        "benchmark_source_type": "observed_output",
        "policy_pair": "baseline->candidate",
        "comparison_scope": "same_surface",
        "baseline_response_sample": "baseline output sample",
        "candidate_response_sample": "candidate output sample",
    }
    reverse_task = {
        "id": "observed-ready-but-drifted-reverse",
        "benchmark_source_type": "observed_output",
        "policy_pair": "candidate->baseline",
        "comparison_scope": "same_surface",
        "baseline_response_sample": "baseline output sample",
        "candidate_response_sample": "candidate output sample",
    }
    _write_json(tmp_path / ".omc" / "tasks" / "observed-ready-but-drifted.json", observed_task)
    _write_json(
        tmp_path / ".omc" / "tasks" / "observed-ready-but-drifted-reverse.json",
        reverse_task,
    )

    for index in range(10):
        omc_autopilot._save_pipeline_result(
            tmp_path,
            {
                "__run_id": f"run-drifted-forward-{index}",
                "task_id": "observed-ready-but-drifted",
                "status": "completed",
                "branch": f"feat/drifted-forward-{index}",
                "executor": "codex",
                "started_at": "2026-06-13T09:00:00+09:00",
                "finished_at": "2026-06-13T09:05:00+09:00",
                "steps": {"review": {"status": "completed", "verdict": "APPROVE"}},
                "baseline_comparison_status": "deferred",
                "next_kpi_blocker": "baseline_comparison_not_ready",
                "readiness_status_line": "not ready: baseline comparison input is not ready",
            },
        )
        omc_autopilot._save_pipeline_result(
            tmp_path,
            {
                "__run_id": f"run-drifted-reverse-{index}",
                "task_id": "observed-ready-but-drifted-reverse",
                "status": "completed",
                "branch": f"feat/drifted-reverse-{index}",
                "executor": "codex",
                "started_at": "2026-06-13T10:00:00+09:00",
                "finished_at": "2026-06-13T10:05:00+09:00",
                "steps": {"review": {"status": "completed", "verdict": "APPROVE"}},
                "baseline_comparison_status": "deferred",
                "next_kpi_blocker": "baseline_comparison_not_ready",
                "readiness_status_line": "not ready: baseline comparison input is not ready",
            },
        )

    rc = omc_autopilot.cmd_overview(tmp_path, limit=5)
    out = capsys.readouterr().out

    assert rc == 0
    assert "observed_samples=20" in out
    assert "readiness_status=not ready: baseline comparison input is not ready" in out
    assert "baseline_comparison_status=deferred" in out
    assert "next_kpi_blocker=baseline_comparison_not_ready" in out
    assert "next_collection_focus=stabilize_baseline_comparison_inputs" in out


def test_observed_collect_task_exists_with_real_expect_checks() -> None:
    payload = json.loads((Path.cwd() / OBSERVED_TASK_PATH).read_text(encoding="utf-8"))

    assert payload["id"] == "observed-collect"
    assert payload["executor"] == "auto"
    assert payload.get("require_clean_scope") is True
    assert payload["steps"], "observed collect task should define steps"
    first_step = payload["steps"][0]
    expect = first_step.get("expect") or {}
    checks = expect.get("checks") or []
    assert checks, "observed collect task should have expect checks"
    assert all("echo 'expect 검증 예시" not in check.get("cmd", "") for check in checks)


def test_save_pipeline_result_copies_complete_observed_output_schema_from_task_meta(
    tmp_path: Path,
) -> None:
    _write_json(
        tmp_path / ".omc" / "tasks" / "observed-output.json",
        {
            "id": "observed-output",
            "benchmark_source_type": "observed_output",
            "policy_pair": "baseline->candidate",
            "comparison_scope": "same_surface",
            "baseline_response_sample": "baseline sample",
            "candidate_response_sample": "candidate sample",
        },
    )

    omc_autopilot._save_pipeline_result(
        tmp_path,
        {
            "__run_id": "run-observed-output-complete",
            "task_id": "observed-output",
            "status": "completed",
            "branch": "feat/observed-output-complete",
            "executor": "codex",
            "steps": {"review": {"status": "completed", "verdict": "APPROVE"}},
        },
    )

    saved = json.loads(
        (tmp_path / ".omc" / "runs" / "run-observed-output-complete" / "result.json").read_text(
            encoding="utf-8"
        )
    )
    assert saved["benchmark_source_type"] == "observed_output"
    assert saved["policy_pair"] == "baseline->candidate"
    assert saved["comparison_scope"] == "same_surface"
    assert saved["baseline_response_sample"] == "baseline sample"
    assert saved["candidate_response_sample"] == "candidate sample"


def test_save_pipeline_result_skips_incomplete_observed_output_schema_from_task_meta(
    tmp_path: Path,
) -> None:
    _write_json(
        tmp_path / ".omc" / "tasks" / "observed-output-incomplete.json",
        {
            "id": "observed-output-incomplete",
            "benchmark_source_type": "observed_output",
            "policy_pair": "baseline->candidate",
            "comparison_scope": "same_surface",
            "baseline_response_sample": "baseline sample",
        },
    )

    omc_autopilot._save_pipeline_result(
        tmp_path,
        {
            "__run_id": "run-observed-output-incomplete",
            "task_id": "observed-output-incomplete",
            "status": "completed",
            "branch": "feat/observed-output-incomplete",
            "executor": "codex",
            "steps": {"review": {"status": "completed", "verdict": "APPROVE"}},
        },
    )

    saved = json.loads(
        (tmp_path / ".omc" / "runs" / "run-observed-output-incomplete" / "result.json").read_text(
            encoding="utf-8"
        )
    )
    assert "benchmark_source_type" not in saved
    assert "policy_pair" not in saved
    assert "comparison_scope" not in saved
    assert "baseline_response_sample" not in saved
    assert "candidate_response_sample" not in saved
    assert saved["dataset_rejected_observed_output_case_count"] == 1
    assert saved["dataset_rejected_observed_output_reasons"] == {
        "missing_candidate_response_sample": 1
    }


def test_save_pipeline_result_counts_incomplete_observed_output_as_one_rejected_case(
    tmp_path: Path,
) -> None:
    _write_json(
        tmp_path / ".omc" / "tasks" / "observed-output-missing-all.json",
        {
            "id": "observed-output-missing-all",
            "benchmark_source_type": "observed_output",
            "policy_pair": "baseline->candidate",
        },
    )

    omc_autopilot._save_pipeline_result(
        tmp_path,
        {
            "__run_id": "run-observed-output-missing-all",
            "task_id": "observed-output-missing-all",
            "status": "completed",
            "branch": "feat/observed-output-missing-all",
            "executor": "codex",
            "steps": {"review": {"status": "completed", "verdict": "APPROVE"}},
        },
    )

    saved = json.loads(
        (tmp_path / ".omc" / "runs" / "run-observed-output-missing-all" / "result.json").read_text(
            encoding="utf-8"
        )
    )
    assert saved["dataset_rejected_observed_output_case_count"] == 1
    assert saved["dataset_rejected_observed_output_reasons"] == {
        "missing_comparison_scope": 1,
        "missing_baseline_response_sample": 1,
        "missing_candidate_response_sample": 1,
    }


def test_save_pipeline_result_backfills_missing_observed_output_samples_even_when_source_type_exists(
    tmp_path: Path,
) -> None:
    _write_json(
        tmp_path / ".omc" / "tasks" / "observed-output-backfill.json",
        {
            "id": "observed-output-backfill",
            "benchmark_source_type": "observed_output",
            "policy_pair": "baseline->candidate",
            "comparison_scope": "cross_surface",
            "baseline_response_sample": "baseline sample from task meta",
            "candidate_response_sample": "candidate sample from task meta",
        },
    )

    omc_autopilot._save_pipeline_result(
        tmp_path,
        {
            "__run_id": "run-observed-output-backfill",
            "task_id": "observed-output-backfill",
            "status": "completed",
            "branch": "feat/observed-output-backfill",
            "executor": "codex",
            "benchmark_source_type": "observed_output",
            "policy_pair": "baseline->candidate",
            "steps": {"review": {"status": "completed", "verdict": "APPROVE"}},
        },
    )

    saved = json.loads(
        (tmp_path / ".omc" / "runs" / "run-observed-output-backfill" / "result.json").read_text(
            encoding="utf-8"
        )
    )
    assert saved["benchmark_source_type"] == "observed_output"
    assert saved["policy_pair"] == "baseline->candidate"
    assert saved["comparison_scope"] == "cross_surface"
    assert saved["baseline_response_sample"] == "baseline sample from task meta"
    assert saved["candidate_response_sample"] == "candidate sample from task meta"
