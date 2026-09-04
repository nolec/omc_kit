from __future__ import annotations

import hashlib
import json
from pathlib import Path


V1_PATH = Path("docs/real_use_product_observation_preregistration_v1.json")
V2_PATH = Path("docs/real_use_product_observation_preregistration_v2.json")
V1_SUPERSESSION_PATH = Path(
    "docs/real_use_product_observation_v1_supersession.json"
)


def _load(path: Path) -> dict[str, object]:
    return json.loads(path.read_text(encoding="utf-8"))


def test_v1_remains_immutable_and_is_closed_by_a_separate_supersession() -> None:
    v1_bytes = V1_PATH.read_bytes()
    supersession = _load(V1_SUPERSESSION_PATH)

    assert hashlib.sha256(v1_bytes).hexdigest() == (
        "16aa508b5ec85f302e6d157895bf5e31bfb5d7007f9aaba3c449a4cbff45bc2c"
    )
    assert supersession["schema_version"] == "omc-study-supersession/v1"
    assert supersession["superseded_artifact"] == V1_PATH.as_posix()
    assert supersession["superseded_artifact_sha256"] == hashlib.sha256(
        v1_bytes
    ).hexdigest()
    assert supersession["terminal_status"] == "superseded_before_observation"
    assert supersession["observed_candidate_count"] == 0
    assert supersession["observation_allowed"] is False
    assert supersession["successor_artifact"] == V2_PATH.as_posix()
    assert supersession["mutation_policy"] == "immutable_git_blob_after_commit"
    assert supersession["write_once_authority"] == "git_commit_object"
    assert supersession["effective_when"] == (
        "artifact and roadmap hash binding are committed together"
    )


def test_v2_is_a_non_executable_contract_until_source_registration() -> None:
    preregistration = _load(V2_PATH)

    assert preregistration["schema_version"] == (
        "omc-real-use-observation-preregistration/v2"
    )
    assert preregistration["study_status"] == "draft_unregistered"
    assert preregistration["claim_eligible"] is False
    assert preregistration["observation_allowed"] is False
    assert preregistration["source_freeze"]["omc_kit_commit"] is None
    assert preregistration["registration"]["required_before_observation"] is True
    assert preregistration["registration"]["required_receipts"] == [
        "immutable_git_registry_record",
        "rfc3161_timestamp_receipt",
        "repository_enrollment_receipt",
    ]
    assert preregistration["observation_window"]["activation_policy"] == (
        "freeze exact timestamps and source hashes before immutable registry "
        "registration; enroll repositories afterward; observation starts only "
        "after final enrollment plus buffer"
    )


def test_v2_uses_one_cohort_with_two_independent_outcome_receipts() -> None:
    preregistration = _load(V2_PATH)
    cohort = preregistration["cohort"]
    axes = preregistration["outcome_axes"]

    assert cohort["relationship"] == "shared_cohort_independent_outcomes"
    assert cohort["selection_policy"] == "prospective_chronological_first_n"
    assert cohort["selection_count"] == 6
    assert cohort["replacement_allowed"] is False
    assert cohort["minimum_repositories"] == 2
    assert set(axes) == {"completion_reliability", "operator_experience"}
    assert axes["completion_reliability"]["detached_receipt_required"] is True
    assert axes["operator_experience"]["detached_receipt_required"] is True


def test_v2_excludes_research_execution_from_the_user_workflow_cohort() -> None:
    preregistration = _load(V2_PATH)
    cohort = preregistration["cohort"]

    assert cohort["research_execution_policy"] == (
        "exclude_research_and_benchmark_execution_without_replacement"
    )


def test_v2_freezes_exact_completion_and_operator_thresholds() -> None:
    preregistration = _load(V2_PATH)
    axes = preregistration["outcome_axes"]
    terminal = preregistration["terminal_contract"]
    completion = axes["completion_reliability"]["thresholds"]
    operator = axes["operator_experience"]

    assert completion == {
        "verified_and_user_accepted_minimum_count": 5,
        "manual_takeover_maximum_count": 1,
        "abandonment_maximum_count": 0,
        "missing_evidence_maximum_count": 0,
        "skipped_candidate_maximum_count": 0,
        "overlapping_mutating_session_maximum_count": 0,
    }
    assert operator["baseline"] == "none_by_design"
    assert operator["comparative_improvement_claim_allowed"] is False
    assert operator["thresholds"] == {
        "recorded_decision_count_maximum_median": 2,
        "friction_acceptable_minimum_count": 5,
        "manual_takeover_maximum_count": 1,
        "abandonment_maximum_count": 0,
        "missing_observer_event_maximum_count": 0,
        "session_start_observer_p95_maximum_ms": 100,
        "terminal_observer_p95_maximum_ms": 100,
    }
    assert operator["task_wall_time_policy"] == "report_only"
    assert operator["metric_cutoff"] == (
        "observer_and_decision_metrics_at_task_terminal_before_user_signoff"
    )
    assert operator["post_cutoff_required_metrics"] == [
        "friction_acceptable_from_final_user_signoff"
    ]
    assert "metric_cutoff" not in terminal
    assert terminal["task_terminal_metric_cutoff"] == (
        "observer_and_decision_metrics_at_task_terminal_before_user_signoff"
    )
    assert terminal["post_cutoff_required_metrics"] == [
        "user_acceptance_from_final_user_signoff",
        "friction_acceptable_from_final_user_signoff",
    ]
    assert terminal["required_every_terminal_fields"] == [
        "decision_receipt_count",
        "decision_stream_complete",
    ]
    assert "user_signoff_recorded_after_metric_cutoff" not in terminal
    assert terminal[
        "user_signoff_recorded_after_task_terminal_metric_cutoff"
    ] is True


def test_v2_freezes_metric_sources_populations_and_statistics() -> None:
    preregistration = _load(V2_PATH)
    definitions = preregistration["metric_definitions"]

    decision = definitions["recorded_decision_count"]
    assert decision["source"] == "TASK_TERMINAL.decision_receipt_count"
    assert decision["reconciliation_source"] == (
        "unique durable decision_receipt events"
    )
    assert decision["population"] == "all six selected eligible tasks"
    assert decision["window"] == "eligibility_recorded_at through task_terminal"
    assert decision["deduplication_key"] == "decision_receipt_id"
    assert decision["zero_count_policy"] == (
        "valid only when decision_receipt_count == 0 and "
        "decision_stream_complete == true"
    )
    assert decision["stream_completeness_policy"] == (
        "decision_stream_complete must be true for every selected task; false or "
        "missing is OPERATOR_EXPERIENCE_SAMPLE_INCONCLUSIVE"
    )
    assert decision["reconciliation_policy"] == (
        "terminal count must equal unique durable event count; mismatch is "
        "OPERATOR_EXPERIENCE_SAMPLE_INCONCLUSIVE"
    )
    assert decision["missing_policy"] == "OPERATOR_EXPERIENCE_SAMPLE_INCONCLUSIVE"
    assert decision["median_method"] == (
        "sort ascending; for even N use arithmetic mean of the two middle values"
    )

    friction = definitions["friction_acceptable"]
    assert friction["source"] == (
        "FINAL_USER_SIGNOFF.case_assessments[].friction_acceptable"
    )
    assert friction["allowed_values"] == [True, False]
    assert friction["required_signer_role"] == "final_user_signer"
    assert friction["population"] == "all six selected eligible tasks"
    assert friction["missing_policy"] == "OPERATOR_EXPERIENCE_SAMPLE_INCONCLUSIVE"
    assert friction["collection_timing"] == "after_task_terminal_metric_cutoff"

    latency = definitions["observer_latency_ms"]
    assert latency["source"] == "observer monotonic_ns end minus start"
    assert latency["conversion"] == "duration_ns / 1000000"
    assert latency["quantile_method"] == (
        "nearest-rank: sorted_values[ceil(0.95 * N) - 1]"
    )
    assert latency["preflight_population"] == (
        "200 invocations per surface minus the first 10 warmups"
    )
    assert latency["runtime_population"] == (
        "one session_start and one task_terminal event for each selected task"
    )

    wall_time = definitions["task_wall_time_ms"]
    assert wall_time["use_for_pass_fail"] is False
    assert wall_time["missing_policy"] == "REPORT_ONLY_MISSING_NO_AXIS_EFFECT"

    operator = preregistration["outcome_axes"]["operator_experience"]
    assert operator["metric_cutoff"] == (
        "observer_and_decision_metrics_at_task_terminal_before_user_signoff"
    )
    assert operator["post_cutoff_required_metrics"] == [
        "friction_acceptable_from_final_user_signoff"
    ]


def test_v2_freezes_fail_close_outcome_precedence() -> None:
    preregistration = _load(V2_PATH)
    precedence = preregistration["outcome_precedence"]

    assert precedence["global"] == [
        "INVALID_REGISTRATION_OR_SOURCE_FREEZE -> OBSERVATION_INCONCLUSIVE",
        "INVENTORY_MISSING_SKIPPED_OR_OVERLAPPING -> OBSERVATION_INCONCLUSIVE",
        "SELECTED_ELIGIBLE_TASKS_BELOW_6_OR_REPOSITORIES_BELOW_2 -> LOW_NATURAL_DEMAND",
        "EVALUATE_AXIS_OUTCOMES",
    ]
    assert precedence["completion_reliability"] == [
        "MISSING_REQUIRED_EVIDENCE -> COMPLETION_SAMPLE_INCONCLUSIVE",
        "ALL_THRESHOLDS_PASS -> COMPLETION_SAMPLE_READY",
        "OTHERWISE -> COMPLETION_SAMPLE_NOT_READY",
    ]
    assert precedence["operator_experience"] == [
        "MISSING_OBSERVER_OR_SIGNOFF_EVIDENCE -> OPERATOR_EXPERIENCE_SAMPLE_INCONCLUSIVE",
        "ALL_THRESHOLDS_PASS -> OPERATOR_EXPERIENCE_SAMPLE_READY",
        "OTHERWISE -> OPERATOR_EXPERIENCE_SAMPLE_NOT_READY",
    ]
    assert precedence["aggregation"] == [
        "ANY_AXIS_INCONCLUSIVE -> OBSERVATION_INCONCLUSIVE",
        "EXACTLY_ONE_AXIS_READY_AND_ONE_NOT_READY -> OBSERVATION_INCONCLUSIVE",
        "BOTH_AXES_NOT_READY -> PRODUCT_WORKFLOW_NOT_READY",
        "BOTH_AXES_READY_AND_VALID_FINAL_USER_SIGNOFF -> OPERATIONAL_SAMPLE_READY",
        "OTHERWISE -> OBSERVATION_INCONCLUSIVE",
    ]


def test_v2_requires_inventory_reconciliation_and_distinct_authorities() -> None:
    preregistration = _load(V2_PATH)
    inventory = preregistration["population_inventory"]
    authority = preregistration["authority_separation"]

    assert inventory["baseline_checkpoint_required"] is True
    assert inventory["complete_session_stream_reconciliation_required"] is True
    assert inventory["missing_session_policy"] == "OBSERVATION_INCONCLUSIVE"
    assert inventory["overlapping_mutating_session_policy"] == (
        "continue_user_work_and_invalidate_study"
    )
    assert authority["roles"] == [
        "observer_collector",
        "completion_validator",
        "operator_validator",
        "final_user_signer",
    ]
    assert authority["key_reuse_allowed"] is False


def test_v2_aggregate_ready_requires_both_axes_and_user_signoff() -> None:
    preregistration = _load(V2_PATH)
    aggregation = preregistration["aggregation"]

    assert aggregation["ready_outcome"] == "OPERATIONAL_SAMPLE_READY"
    assert aggregation["required_inputs"] == [
        "COMPLETION_SAMPLE_READY",
        "OPERATOR_EXPERIENCE_SAMPLE_READY",
        "VALID_FINAL_USER_SIGNOFF",
    ]
    assert aggregation["partial_pass_policy"] == "OBSERVATION_INCONCLUSIVE"
    assert aggregation["single_axis_ready_allowed"] is False
