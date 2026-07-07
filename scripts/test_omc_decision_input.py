from __future__ import annotations

import sys
from pathlib import Path


sys.path.insert(0, str(Path(__file__).resolve().parent))
import omc_decision_input as mod


def test_build_next_priority_input_keeps_core_shape_stable():
    decision_input = mod.build_next_priority_input(
        blocker="insufficient_policy_pairs",
        observed_reason_signals_present=False,
        baseline_comparison_status="deferred",
        extension={"readiness_policy_pair_count": 1},
    )

    assert decision_input["core"] == {
        "blocker": "insufficient_policy_pairs",
        "observed_reason_signals_present": False,
        "baseline_comparison_status": "deferred",
    }
    assert decision_input["extension"] == {"readiness_policy_pair_count": 1}


def test_build_next_priority_surface_input_adds_source_surface_to_extension():
    decision_input = mod.build_next_priority_surface_input(
        blocker="none",
        observed_reason_signals_present=True,
        baseline_comparison_status="ready",
        source_surface="overview_summary",
        extension={"policy_comparison_summary": "ready"},
    )

    assert decision_input["core"] == {
        "blocker": "none",
        "observed_reason_signals_present": True,
        "baseline_comparison_status": "ready",
    }
    assert decision_input["extension"] == {
        "source_surface": "overview_summary",
        "policy_comparison_summary": "ready",
    }


def test_resolve_next_priority_returns_sample_gap_and_ready_operator_cases():
    sample_gap = mod.resolve_next_priority(
        blocker="insufficient_observed_samples",
        observed_reason_signals_present=False,
        baseline_comparison_status="deferred",
    )
    ready_operator = mod.resolve_next_priority(
        blocker="none",
        observed_reason_signals_present=True,
        baseline_comparison_status="ready",
    )

    assert sample_gap == ("collect_more_observed_runs", "need more observed samples")
    assert ready_operator == (
        "validate_operator_bottlenecks_from_observed_runs",
        "reason signals observed in ready dataset",
    )


def test_plan_followup_input_prefers_plan_for_roadmap_sync_and_progress_check():
    decision_input = mod.build_plan_followup_input(
        request_text="현재 로드맵 최신화하고 어디까지 진행된건지 체크해"
    )

    assert decision_input["core"]["roadmap_sync_intent_present"] is True
    assert decision_input["core"]["progress_check_intent_present"] is True
    assert mod.resolve_plan_followup_from_input(decision_input) == (
        "$omc-plan",
        "roadmap sync should align before the next implementation step",
    )


def test_plan_followup_input_keeps_plan_review_question_in_user_selection_wait():
    decision_input = mod.build_plan_followup_input(
        request_text="이거 클로드코드로 실행한건데 이거 제대로 진행된 거 맞아? plan"
    )

    assert decision_input["core"]["contains_plan_wording"] is True
    assert decision_input["core"]["contains_question"] is True
    assert mod.resolve_plan_followup_from_input(decision_input) == (
        "사용자 선택 대기",
        "plan wording or explanation intent should pause for user selection",
    )


def test_run_overview_followup_prefers_recover_for_stale_running_pipeline():
    decision_input = mod.build_run_overview_followup_input(
        status="running",
        stale=True,
        failure_reason="pipeline pid not running: 99999",
        current_step="task",
    )

    assert decision_input["core"] == {
        "status": "running",
        "stale": True,
        "failure_reason": "pipeline pid not running: 99999",
        "current_step": "task",
    }
    assert mod.resolve_run_overview_followup_from_input(decision_input) == (
        "recover stale pipeline",
        "stale running pipeline should be recovered before further inspection",
    )


def test_run_overview_followup_prefers_task_retry_for_task_failure():
    decision_input = mod.build_run_overview_followup_input(
        status="failed",
        stale=False,
        failure_reason="task:failed",
        current_step="task",
    )

    assert mod.resolve_run_overview_followup_from_input(decision_input) == (
        "fix task failure and retry",
        "task-stage failure should return to task retry guidance",
    )


def test_operator_priority_input_keeps_core_shape_stable():
    decision_input = mod.build_operator_priority_input(
        flow_kind_counts={
            "wrong_next_step": 2,
            "reroute_loop": 1,
            "over_stage_entry": 0,
            "output_bloat": 1,
        },
        observed_reason_signal_counts={
            "reroute_reason": 1,
            "output_bloat_reason": 1,
            "compression_signal": 1,
        },
        extension={"source_surface": "expensive_flow_summary"},
    )

    assert decision_input["core"] == {
        "flow_kind_counts": {
            "wrong_next_step": 2,
            "reroute_loop": 1,
            "over_stage_entry": 0,
            "output_bloat": 1,
        },
        "observed_reason_signal_counts": {
            "reroute_reason": 1,
            "output_bloat_reason": 1,
            "compression_signal": 1,
        },
    }
    assert decision_input["extension"] == {"source_surface": "expensive_flow_summary"}


def test_operator_priority_input_prefers_wrong_next_step_ahead_of_output_bloat():
    decision_input = mod.build_operator_priority_input(
        flow_kind_counts={
            "wrong_next_step": 1,
            "reroute_loop": 0,
            "over_stage_entry": 0,
            "output_bloat": 1,
        },
        observed_reason_signal_counts={
            "reroute_reason": 0,
            "output_bloat_reason": 1,
            "compression_signal": 1,
        },
    )

    assert mod.resolve_operator_priority_from_input(decision_input) == (
        "tighten_next_action_routing",
        "wrong next step remains the dominant expensive flow",
    )


def test_output_bloat_validation_input_prefers_ready_to_close_when_not_dominant():
    decision_input = mod.build_output_bloat_validation_input(
        flow_kind_counts={
            "wrong_next_step": 2,
            "reroute_loop": 0,
            "over_stage_entry": 0,
            "output_bloat": 1,
        },
        observed_reason_signal_counts={
            "output_bloat_reason": 1,
            "compression_signal": 1,
        },
        dominant_flow_kind="wrong_next_step",
        operator_next_priority="tighten_next_action_routing",
    )

    assert mod.resolve_output_bloat_validation_from_input(decision_input) == (
        "ready_to_close",
        False,
        "output_bloat observed but not dominant; keep focus on wrong_next_step",
    )


def test_output_bloat_validation_input_keeps_source_surface_extension():
    decision_input = mod.build_output_bloat_validation_input(
        flow_kind_counts={"wrong_next_step": 1, "output_bloat": 1},
        observed_reason_signal_counts={"output_bloat_reason": 1},
        dominant_flow_kind="wrong_next_step",
        operator_next_priority="tighten_next_action_routing",
        extension={"source_surface": "expensive_flow_summary"},
    )

    assert decision_input["extension"] == {"source_surface": "expensive_flow_summary"}


def test_operator_explanation_input_prefers_over_stage_resume_condition():
    decision_input = mod.build_operator_explanation_input(
        dominant_flow_kind="over_stage_entry",
        flow_kind_counts={
            "wrong_next_step": 0,
            "reroute_loop": 0,
            "over_stage_entry": 2,
            "output_bloat": 0,
        },
        observed_reason_signal_counts={},
        operator_validation_status="needs_followup",
        operator_next_priority="reduce_over_stage_entry",
    )

    explanation = mod.resolve_operator_explanation_from_input(decision_input)

    assert explanation["current_bottleneck"] == "over_stage_entry"
    assert explanation["resume_condition_line"] == (
        "resume condition: reduce over_stage_entry before closing follow-ups"
    )


def test_operator_explanation_input_keeps_source_surface_extension():
    decision_input = mod.build_operator_explanation_input(
        dominant_flow_kind="wrong_next_step",
        flow_kind_counts={"wrong_next_step": 1, "output_bloat": 1},
        observed_reason_signal_counts={"output_bloat_reason": 1},
        operator_validation_status="ready_to_close",
        operator_next_priority="tighten_next_action_routing",
        extension={"source_surface": "expensive_flow_summary"},
    )

    assert decision_input["extension"] == {"source_surface": "expensive_flow_summary"}
