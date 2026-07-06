#!/usr/bin/env python3
from __future__ import annotations


def build_next_priority_input(
    *,
    blocker: str,
    observed_reason_signals_present: bool,
    baseline_comparison_status: str,
    extension: dict[str, object] | None = None,
) -> dict[str, object]:
    return {
        "core": {
            "blocker": blocker,
            "observed_reason_signals_present": observed_reason_signals_present,
            "baseline_comparison_status": baseline_comparison_status,
        },
        "extension": dict(extension or {}),
    }


def build_next_priority_surface_input(
    *,
    blocker: str,
    observed_reason_signals_present: bool,
    baseline_comparison_status: str,
    source_surface: str,
    extension: dict[str, object] | None = None,
) -> dict[str, object]:
    merged_extension = {"source_surface": source_surface}
    if extension:
        merged_extension.update(dict(extension))
    return build_next_priority_input(
        blocker=blocker,
        observed_reason_signals_present=observed_reason_signals_present,
        baseline_comparison_status=baseline_comparison_status,
        extension=merged_extension,
    )


def resolve_next_priority(
    *,
    blocker: str,
    observed_reason_signals_present: bool,
    baseline_comparison_status: str,
) -> tuple[str, str]:
    if blocker == "insufficient_observed_samples":
        return "collect_more_observed_runs", "need more observed samples"
    if blocker == "insufficient_same_surface_evidence":
        return "add_same_surface_observed_evidence", "need more same-surface evidence"
    if blocker == "insufficient_policy_pairs":
        return "expand_policy_pair_coverage", "need more policy pair coverage"
    if blocker == "baseline_comparison_not_ready":
        return "stabilize_baseline_comparison_inputs", "baseline comparison input is not ready"
    if baseline_comparison_status == "ready" and observed_reason_signals_present:
        return (
            "validate_operator_bottlenecks_from_observed_runs",
            "reason signals observed in ready dataset",
        )
    return "maintain_policy_comparison_confidence", "readiness requirements are currently satisfied"


def resolve_next_priority_from_input(decision_input: dict[str, object]) -> tuple[str, str]:
    core = decision_input.get("core")
    if not isinstance(core, dict):
        core = decision_input
    return resolve_next_priority(
        blocker=str(core.get("blocker") or ""),
        observed_reason_signals_present=bool(core.get("observed_reason_signals_present")),
        baseline_comparison_status=str(core.get("baseline_comparison_status") or ""),
    )


def build_status_followup_input(
    *,
    request_kind: str,
    returncode: int | None = None,
    extension: dict[str, object] | None = None,
) -> dict[str, object]:
    return {
        "core": {
            "request_kind": str(request_kind or "build"),
            "returncode": returncode,
        },
        "extension": dict(extension or {}),
    }


def resolve_status_followup_from_input(decision_input: dict[str, object]) -> tuple[str, str]:
    core = decision_input.get("core")
    if not isinstance(core, dict):
        core = decision_input

    returncode = core.get("returncode")
    if returncode is not None:
        reason = f"exit_code={returncode}"
    else:
        reason = "failed"

    next_steps = {
        "debug": "실패 지점 로그와 재현 경로를 먼저 다시 확인",
        "review": "검토 범위와 실패한 체크 포인트를 먼저 다시 고정",
        "design": "가정한 계약과 실제 구현/입력 조건 차이를 먼저 점검",
        "domain": "비용 가정, 검증 구간, 실행 금지 조건부터 다시 점검",
        "research": "입력 범위와 필요한 근거 형식을 먼저 다시 고정",
        "build": "실패 지점과 완료 조건 차이를 먼저 다시 확인",
    }
    request_kind = str(core.get("request_kind") or "build")
    return reason, next_steps.get(request_kind, next_steps["build"])


def build_plan_followup_input(
    *,
    request_text: str,
    extension: dict[str, object] | None = None,
) -> dict[str, object]:
    request_text = str(request_text or "").strip()
    lowered = request_text.lower()
    return {
        "core": {
            "request_text": request_text,
            "contains_question": "?" in request_text,
            "contains_plan_wording": "plan" in lowered or "플랜" in request_text,
            "roadmap_sync_intent_present": (
                "로드맵" in request_text
                and any(keyword in request_text for keyword in ("최신화", "싱크", "동기화", "업데이트"))
            ),
            "progress_check_intent_present": any(
                keyword in request_text for keyword in ("어디까지", "진행된", "진행 상태", "작업 체크")
            ),
            "explanation_request_present": any(
                keyword in request_text
                for keyword in ("왜", "맞아", "해야하나", "해도 되나", "추천", "정리해줘", "쪼개서")
            ),
        },
        "extension": dict(extension or {}),
    }


def resolve_plan_followup_from_input(decision_input: dict[str, object]) -> tuple[str, str]:
    core = decision_input.get("core")
    if not isinstance(core, dict):
        core = decision_input

    roadmap_sync_intent_present = bool(core.get("roadmap_sync_intent_present"))
    progress_check_intent_present = bool(core.get("progress_check_intent_present"))
    contains_question = bool(core.get("contains_question"))
    contains_plan_wording = bool(core.get("contains_plan_wording"))
    explanation_request_present = bool(core.get("explanation_request_present"))

    if roadmap_sync_intent_present:
        return "$omc-plan", "roadmap sync should align before the next implementation step"
    if progress_check_intent_present and not contains_question:
        return "$omc-plan", "progress check should refresh plan state before implementation"
    if contains_plan_wording or contains_question or explanation_request_present:
        return "사용자 선택 대기", "plan wording or explanation intent should pause for user selection"
    return "$omc-task", "implementation progression is still the default follow-up"


def build_run_overview_followup_input(
    *,
    status: str,
    stale: bool,
    failure_reason: str,
    current_step: str,
    extension: dict[str, object] | None = None,
) -> dict[str, object]:
    return {
        "core": {
            "status": str(status or "unknown"),
            "stale": bool(stale),
            "failure_reason": str(failure_reason or ""),
            "current_step": str(current_step or ""),
        },
        "extension": dict(extension or {}),
    }


def resolve_run_overview_followup_from_input(decision_input: dict[str, object]) -> tuple[str, str]:
    core = decision_input.get("core")
    if not isinstance(core, dict):
        core = decision_input

    status = str(core.get("status") or "unknown")
    stale = bool(core.get("stale"))
    failure_reason = str(core.get("failure_reason") or "")
    current_step = str(core.get("current_step") or "")

    if stale:
        return "recover stale pipeline", "stale running pipeline should be recovered before further inspection"
    if status == "completed":
        return "review final output", "completed run should move to final output review"
    if status in {"hold", "auto_hold", "blocked"}:
        if "critique" in failure_reason:
            return "inspect critique findings", "critique-related hold should inspect critique findings first"
        return "inspect blocked step", "blocked run should inspect the blocked step first"
    if status in {"failed", "retry_exhausted", "timeout", "aborted"}:
        if current_step.startswith("review"):
            return "inspect review failures", "review-stage failure should inspect review failures first"
        if current_step.startswith("task"):
            return "fix task failure and retry", "task-stage failure should return to task retry guidance"
        return "inspect failed step", "failed run should inspect the failed step first"
    if status == "running":
        return "wait for next update", "active run should wait for the next update"
    return "inspect run details", "unknown run state should fall back to run detail inspection"


def build_operator_priority_input(
    *,
    flow_kind_counts: dict[str, int],
    observed_reason_signal_counts: dict[str, int],
    extension: dict[str, object] | None = None,
) -> dict[str, object]:
    return {
        "core": {
            "flow_kind_counts": dict(flow_kind_counts),
            "observed_reason_signal_counts": dict(observed_reason_signal_counts),
        },
        "extension": dict(extension or {}),
    }


def resolve_operator_priority_from_input(decision_input: dict[str, object]) -> tuple[str, str]:
    core = decision_input.get("core")
    if not isinstance(core, dict):
        core = decision_input

    flow_kind_counts = core.get("flow_kind_counts")
    if not isinstance(flow_kind_counts, dict):
        flow_kind_counts = {}
    observed_reason_signal_counts = core.get("observed_reason_signal_counts")
    if not isinstance(observed_reason_signal_counts, dict):
        observed_reason_signal_counts = {}

    wrong_next_step_count = int(flow_kind_counts.get("wrong_next_step", 0))
    over_stage_entry_count = int(flow_kind_counts.get("over_stage_entry", 0))
    reroute_loop_count = int(flow_kind_counts.get("reroute_loop", 0))
    output_bloat_count = int(flow_kind_counts.get("output_bloat", 0))

    if wrong_next_step_count > 0:
        return (
            "tighten_next_action_routing",
            "wrong next step remains the dominant expensive flow",
        )
    if reroute_loop_count > 0 or int(observed_reason_signal_counts.get("reroute_reason", 0)) > 0:
        return (
            "reduce_reroute_loops",
            "reroute signals are still present in observed operator flows",
        )
    if over_stage_entry_count > 0:
        return (
            "reduce_over_stage_entry",
            "requests still enter execution/review later than necessary",
        )
    if output_bloat_count > 0 or int(observed_reason_signal_counts.get("output_bloat_reason", 0)) > 0:
        return (
            "compress_operator_outputs",
            "output bloat is still visible in operator-facing responses",
        )
    return (
        "maintain_operator_experience_quality",
        "no dominant expensive operator flow stands out right now",
    )


def build_output_bloat_validation_input(
    *,
    flow_kind_counts: dict[str, int],
    observed_reason_signal_counts: dict[str, int],
    dominant_flow_kind: str,
    operator_next_priority: str,
    extension: dict[str, object] | None = None,
) -> dict[str, object]:
    return {
        "core": {
            "flow_kind_counts": dict(flow_kind_counts),
            "observed_reason_signal_counts": dict(observed_reason_signal_counts),
            "dominant_flow_kind": str(dominant_flow_kind or ""),
            "operator_next_priority": str(operator_next_priority or ""),
        },
        "extension": dict(extension or {}),
    }


def resolve_output_bloat_validation_from_input(
    decision_input: dict[str, object],
) -> tuple[str, bool, str]:
    core = decision_input.get("core")
    if not isinstance(core, dict):
        core = decision_input

    flow_kind_counts = core.get("flow_kind_counts")
    if not isinstance(flow_kind_counts, dict):
        flow_kind_counts = {}
    observed_reason_signal_counts = core.get("observed_reason_signal_counts")
    if not isinstance(observed_reason_signal_counts, dict):
        observed_reason_signal_counts = {}
    dominant_flow_kind = str(core.get("dominant_flow_kind") or "")
    operator_next_priority = str(core.get("operator_next_priority") or "")

    output_bloat_count = int(flow_kind_counts.get("output_bloat", 0))
    observed_output_bloat = int(observed_reason_signal_counts.get("output_bloat_reason", 0))

    if output_bloat_count <= 0 and observed_output_bloat <= 0:
        return (
            "not_observed",
            False,
            "output_bloat is not currently observed in operator validation data",
        )
    if dominant_flow_kind == "output_bloat" or operator_next_priority == "compress_operator_outputs":
        return (
            "needs_followup",
            True,
            "output_bloat is a primary operator bottleneck and needs follow-up",
        )
    return (
        "ready_to_close",
        False,
        "output_bloat observed but not dominant; keep focus on wrong_next_step",
    )


def build_operator_explanation_input(
    *,
    dominant_flow_kind: str,
    flow_kind_counts: dict[str, int],
    observed_reason_signal_counts: dict[str, int],
    operator_validation_status: str,
    operator_next_priority: str,
    extension: dict[str, object] | None = None,
) -> dict[str, object]:
    return {
        "core": {
            "dominant_flow_kind": str(dominant_flow_kind or ""),
            "flow_kind_counts": dict(flow_kind_counts),
            "observed_reason_signal_counts": dict(observed_reason_signal_counts),
            "operator_validation_status": str(operator_validation_status or ""),
            "operator_next_priority": str(operator_next_priority or ""),
        },
        "extension": dict(extension or {}),
    }


def resolve_operator_explanation_from_input(decision_input: dict[str, object]) -> dict[str, str]:
    core = decision_input.get("core")
    if not isinstance(core, dict):
        core = decision_input

    dominant_flow_kind = str(core.get("dominant_flow_kind") or "")
    flow_kind_counts = core.get("flow_kind_counts")
    if not isinstance(flow_kind_counts, dict):
        flow_kind_counts = {}
    observed_reason_signal_counts = core.get("observed_reason_signal_counts")
    if not isinstance(observed_reason_signal_counts, dict):
        observed_reason_signal_counts = {}
    operator_validation_status = str(core.get("operator_validation_status") or "")
    operator_next_priority = str(core.get("operator_next_priority") or "")

    if int(flow_kind_counts.get("reroute_loop", 0)) > 0 or int(
        observed_reason_signal_counts.get("reroute_reason", 0)
    ) > 0:
        reroute_reason_line = "reroute reason: user requested path change after baseline misroute"
    else:
        reroute_reason_line = "reroute reason: no reroute signal is currently dominant"

    if int(flow_kind_counts.get("over_stage_entry", 0)) > 0:
        delay_reason_line = "delay reason: execution/review entry was delayed by baseline over-stage flow"
    else:
        delay_reason_line = "delay reason: no over-stage delay signal is currently dominant"

    output_bloat_observed = int(flow_kind_counts.get("output_bloat", 0)) > 0 or int(
        observed_reason_signal_counts.get("output_bloat_reason", 0)
    ) > 0
    output_bloat_is_primary = dominant_flow_kind == "output_bloat" or (
        operator_next_priority == "compress_operator_outputs"
    )
    if output_bloat_observed and output_bloat_is_primary:
        output_bloat_reason_line = (
            "output_bloat reason: baseline output still needs compression follow-up and is now the current top bottleneck"
        )
        output_bloat_priority_line = (
            "output_bloat priority: treat output_bloat compression as the current follow-up target"
        )
    elif output_bloat_observed:
        output_bloat_reason_line = (
            "output_bloat reason: baseline output still needs compression follow-up, but it is not the current top bottleneck"
        )
        output_bloat_priority_line = (
            "output_bloat priority: keep focus on wrong_next_step before taking output_bloat follow-up"
        )
    else:
        output_bloat_reason_line = (
            "output_bloat reason: no output_bloat follow-up is currently required"
        )
        output_bloat_priority_line = (
            "output_bloat priority: no output_bloat action is currently prioritized"
        )

    if operator_next_priority == "tighten_next_action_routing":
        resume_condition_line = (
            "resume condition: keep wrong_next_step at 0 before closing reroute/output_bloat follow-ups"
        )
    elif operator_next_priority == "reduce_reroute_loops":
        resume_condition_line = "resume condition: reduce reroute_loop before closing follow-ups"
    elif operator_next_priority == "reduce_over_stage_entry":
        resume_condition_line = "resume condition: reduce over_stage_entry before closing follow-ups"
    elif operator_next_priority == "compress_operator_outputs":
        resume_condition_line = "resume condition: reduce output_bloat before closing follow-ups"
    elif operator_validation_status == "ready_to_close":
        resume_condition_line = (
            "resume condition: keep the current bottleneck stable before closing follow-ups"
        )
    else:
        resume_condition_line = (
            "resume condition: reduce the current dominant bottleneck before closing follow-ups"
        )

    return {
        "current_bottleneck": dominant_flow_kind or "general_overhead",
        "reroute_reason_line": reroute_reason_line,
        "delay_reason_line": delay_reason_line,
        "output_bloat_reason_line": output_bloat_reason_line,
        "output_bloat_priority_line": output_bloat_priority_line,
        "resume_condition_line": resume_condition_line,
    }
