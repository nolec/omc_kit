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
