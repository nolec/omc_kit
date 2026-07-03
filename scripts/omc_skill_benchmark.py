#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path


NEXT_ACTION_LABELS = (
    "다음 액션",
    "추천 다음 스킬",
    "다음 스킬",
    "다음 단계",
    "next action",
    "next step",
    "recommended next skill",
)
NEXT_ACTION_SEPARATORS = ("=>", ":", "=", "-", "—", "→")
NEXT_ACTION_LINE = re.compile(
    rf"^(?P<label>{'|'.join(map(re.escape, NEXT_ACTION_LABELS))})"
    rf"\s*(?P<separator>{'|'.join(map(re.escape, NEXT_ACTION_SEPARATORS))})\s*"
    rf"(?P<payload>.+)$",
    re.IGNORECASE,
)
SKILL_ACTION = re.compile(r"\$omc-[a-z-]+")
RESPONSE_MODES = {"answer-first", "execute-first", "review-first"}
POLICIES = {"baseline", "candidate"}
CASE_VARIANTS = {"baseline", "candidate"}
CASE_SOURCE_TYPES = {"synthetic", "observed_output", "current_contract_sample"}
KPI_MIN_SAMPLE_COUNT = 20
KPI_MIN_SAME_SURFACE_COUNT = 1
KPI_MIN_POLICY_PAIR_COUNT = 2


def _readiness_deferred_reason_map() -> dict[str, str]:
    return {
        "insufficient_observed_samples": "need more observed samples",
        "insufficient_same_surface_evidence": "need more same-surface evidence",
        "insufficient_policy_pairs": "need more policy pair coverage",
        "baseline_comparison_not_ready": "baseline comparison input is not ready",
        "none": "baseline comparison wording can be enabled",
    }


def _resolve_readiness_blocker(
    *,
    sample_gap: int,
    same_surface_gap: int,
    policy_pair_count: int,
    baseline_comparison_ready: bool,
) -> tuple[str, str]:
    if sample_gap > 0:
        blocker = "insufficient_observed_samples"
    elif same_surface_gap > 0:
        blocker = "insufficient_same_surface_evidence"
    elif policy_pair_count < KPI_MIN_POLICY_PAIR_COUNT:
        blocker = "insufficient_policy_pairs"
    elif not baseline_comparison_ready:
        blocker = "baseline_comparison_not_ready"
    else:
        blocker = "none"

    deferred_reason_map = _readiness_deferred_reason_map()
    blocker_line = (
        "ready: baseline comparison wording can be enabled"
        if blocker == "none"
        else "pending: " + deferred_reason_map.get(blocker, "readiness requirements are not met")
    )
    return blocker, blocker_line


def _resolve_next_priority(
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


def _count_question_marks(text: str) -> int:
    return text.count("?")


def _average(values: list[int | float]) -> float:
    return sum(values) / len(values) if values else 0


def _case_participates_in_decision_metric(case: dict[str, object], metric: str) -> bool:
    if str(case.get("source_type") or "").strip() == "observed_output" and metric in {
        "mode_accuracy",
        "task_start_delay",
    }:
        return False
    excluded = case.get("decision_metric_exclusions")
    if not isinstance(excluded, list):
        return True
    return metric not in excluded


def _count_policy_pairs(cases: list[dict[str, object]]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for case in cases:
        baseline_policy = str(case.get("baseline_policy") or "").strip()
        candidate_policy = str(case.get("candidate_policy") or "").strip()
        if not baseline_policy or not candidate_policy:
            continue
        pair = f"{baseline_policy}->{candidate_policy}"
        counts[pair] = counts.get(pair, 0) + 1
    return counts


def _count_readiness_policy_pairs(cases: list[dict[str, object]]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for case in cases:
        if bool(case.get("neutral_seed")):
            continue
        baseline_policy = str(case.get("baseline_policy") or "").strip()
        candidate_policy = str(case.get("candidate_policy") or "").strip()
        if not baseline_policy or not candidate_policy:
            continue
        pair = f"{baseline_policy}->{candidate_policy}"
        counts[pair] = counts.get(pair, 0) + 1
    return counts


def _count_observed_samples(cases: list[dict[str, object]]) -> int:
    count = 0
    for case in cases:
        source_type = str(case.get("source_type") or "").strip()
        if bool(case.get("neutral_seed")):
            continue
        if source_type in {"observed_request", "observed_output"}:
            count += 1
    return count


def _count_readiness_same_surface_observed_samples(cases: list[dict[str, object]]) -> int:
    count = 0
    for case in cases:
        if bool(case.get("neutral_seed")):
            continue
        if str(case.get("source_type") or "").strip() != "observed_output":
            continue
        if str(case.get("comparison_scope") or "").strip() == "same_surface":
            count += 1
    return count


def _summarize_multi_run_kpis(run_records: list[dict[str, object]]) -> dict[str, object]:
    total_run_count = len(run_records)
    reroute_run_count = 0
    recovered_retry_run_count = 0
    successful_costs: list[float] = []

    for record in run_records:
        steps = record.get("steps")
        if not isinstance(steps, dict):
            steps = {}

        had_reroute = any(
            isinstance(step, dict)
            and (
                str(step.get("decision") or "").strip().lower() == "reroute"
                or bool(str(step.get("reroute_target") or "").strip())
            )
            for step in steps.values()
        )
        retry_step_count = sum(1 for name in steps if "retry" in str(name))
        if had_reroute:
            reroute_run_count += 1
        if retry_step_count > 0 and str(record.get("status") or "").strip() == "completed":
            recovered_retry_run_count += 1

        if str(record.get("status") or "").strip() == "completed":
            total_cost_usd = sum(
                float(step.get("cost_estimate"))
                for step in steps.values()
                if isinstance(step, dict) and isinstance(step.get("cost_estimate"), (int, float))
            )
            if total_cost_usd > 0:
                successful_costs.append(total_cost_usd)

    reroute_rate = (reroute_run_count / total_run_count) if total_run_count else None
    retry_to_success_rate = (
        recovered_retry_run_count / reroute_run_count if reroute_run_count else None
    )
    cost_per_successful_task = (
        sum(successful_costs) / len(successful_costs) if successful_costs else None
    )
    return {
        "total_run_count": total_run_count,
        "reroute_rate": reroute_rate,
        "retry_to_success_rate": retry_to_success_rate,
        "cost_per_successful_task": (
            round(cost_per_successful_task, 10)
            if isinstance(cost_per_successful_task, (int, float))
            else None
        ),
    }


def _summarize_readiness_thresholds(
    cases: list[dict[str, object]],
    *,
    min_samples: int = KPI_MIN_SAMPLE_COUNT,
    min_same_surface: int = KPI_MIN_SAME_SURFACE_COUNT,
    min_policy_pairs: int = KPI_MIN_POLICY_PAIR_COUNT,
) -> dict[str, object]:
    observed_sample_count = _count_observed_samples(cases)
    same_surface_count = _count_readiness_same_surface_observed_samples(cases)
    distinct_policy_pair_count = len(_count_readiness_policy_pairs(cases))
    sample_gap = max(min_samples - observed_sample_count, 0)
    same_surface_gap = max(min_same_surface - same_surface_count, 0)
    policy_pair_gap = max(min_policy_pairs - distinct_policy_pair_count, 0)
    return {
        "observed_sample_count": observed_sample_count,
        "same_surface_count": same_surface_count,
        "distinct_policy_pair_count": distinct_policy_pair_count,
        "sample_gap": sample_gap,
        "same_surface_gap": same_surface_gap,
        "policy_pair_gap": policy_pair_gap,
        "baseline_comparison_ready": sample_gap == 0 and same_surface_gap == 0 and policy_pair_gap == 0,
    }


def _fixture_taxonomy_counts_from_readiness(cases: list[dict[str, object]]) -> dict[str, int]:
    current = _summarize_readiness_thresholds(cases)
    stricter_same_surface = _summarize_readiness_thresholds(
        cases,
        min_same_surface=KPI_MIN_SAME_SURFACE_COUNT + 1,
    )
    current_ready = bool(current["baseline_comparison_ready"])
    stricter_ready = bool(stricter_same_surface["baseline_comparison_ready"])
    return {
        "ready_expected": 1 if current_ready else 0,
        "pending_expected": 1 if (current_ready and not stricter_ready) or not current_ready else 0,
        "ambiguous": 1 if current_ready and stricter_ready else 0,
    }


def compare_response_mode_threshold_candidates(
    cases: list[dict[str, object]],
    *,
    thresholds: list[dict[str, object]],
    fixture_taxonomy: dict[str, int] | None = None,
) -> dict[str, object]:
    taxonomy = fixture_taxonomy or _fixture_taxonomy_counts_from_readiness(cases)
    expected_ready_count = int(taxonomy.get("ready_expected", 0))
    expected_pending_count = int(taxonomy.get("pending_expected", 0))
    ambiguous_count = int(taxonomy.get("ambiguous", 0))
    expected_ready = expected_ready_count > 0
    expected_pending = expected_pending_count > 0
    ambiguous = ambiguous_count > 0

    candidates: list[dict[str, object]] = []
    for threshold in thresholds:
        summary = _summarize_readiness_thresholds(
            cases,
            min_samples=int(threshold.get("min_samples", KPI_MIN_SAMPLE_COUNT)),
            min_same_surface=int(threshold.get("min_same_surface", KPI_MIN_SAME_SURFACE_COUNT)),
            min_policy_pairs=int(threshold.get("min_policy_pairs", KPI_MIN_POLICY_PAIR_COUNT)),
        )
        actual_ready = bool(summary["baseline_comparison_ready"])
        false_ready_count = 0
        false_pending_count = 0
        if not ambiguous:
            if actual_ready and expected_pending and not expected_ready:
                false_ready_count = expected_pending_count
            if not actual_ready and expected_ready and not expected_pending:
                false_pending_count = expected_ready_count
        candidates.append(
            {
                "label": str(threshold.get("label") or "").strip() or "candidate",
                "min_samples": int(threshold.get("min_samples", KPI_MIN_SAMPLE_COUNT)),
                "min_same_surface": int(threshold.get("min_same_surface", KPI_MIN_SAME_SURFACE_COUNT)),
                "min_policy_pairs": int(threshold.get("min_policy_pairs", KPI_MIN_POLICY_PAIR_COUNT)),
                **summary,
                "false_ready_count": false_ready_count,
                "false_pending_count": false_pending_count,
            }
        )

    return {"fixture_taxonomy": taxonomy, "candidates": candidates}


def _distinct_policies(cases: list[dict[str, object]]) -> list[str]:
    policies: set[str] = set()
    for case in cases:
        for field in ("baseline_policy", "candidate_policy"):
            value = str(case.get(field) or "").strip()
            if value:
                policies.add(value)
    return sorted(policies)


def _primary_policy_pair(policy_pair_counts: dict[str, int]) -> str:
    if not policy_pair_counts:
        return ""
    return sorted(policy_pair_counts.items(), key=lambda item: (-item[1], item[0]))[0][0]


def _contains_user_reroute(trace: list[str]) -> bool:
    reroute_markers = ("user: 아니", "user: 다시", "user: 말한 건", "user: 정정", "user: 리뷰해줘", "user: 구현해줘")
    lowered = [item.lower() for item in trace]
    return any(any(marker in item for marker in reroute_markers) for item in lowered)


def _infer_response_mode(request: str, policy: str) -> str:
    text = request.lower()
    review_keywords = ("리뷰", "review", "diff", "pr", "치명 이슈", "코드 봐줘")
    execute_keywords = (
        "구현",
        "수정",
        "고치",
        "추가",
        "커밋",
        "빌드",
        "테스트",
        "개발",
        "만들어",
    )

    if policy == "candidate":
        if any(keyword in text for keyword in review_keywords):
            return "review-first"
        if any(keyword in text for keyword in execute_keywords):
            return "execute-first"
        return "answer-first"

    if any(keyword in text for keyword in review_keywords):
        return "review-first"
    return "answer-first"


def _infer_expected_response_mode(request: str) -> str:
    text = request.lower()
    review_keywords = ("리뷰", "review", "diff", "pr", "치명 이슈", "코드 봐줘")
    execute_keywords = (
        "구현",
        "수정",
        "고치",
        "추가",
        "커밋",
        "빌드",
        "테스트",
        "개발",
        "만들어",
    )
    if any(keyword in text for keyword in review_keywords):
        return "review-first"
    if any(keyword in text for keyword in execute_keywords):
        return "execute-first"
    return "answer-first"


def _extract_next_action_line(text: str) -> str:
    for line in text.splitlines():
        match = NEXT_ACTION_LINE.search(line.strip())
        if match:
            return match.group("payload").strip()
    return ""


def _split_next_action_spec(line: str) -> tuple[str, str, str]:
    match = NEXT_ACTION_LINE.search(line.strip())
    if not match:
        return "", "", ""
    return (
        match.group("label").lower().strip(),
        match.group("separator").strip(),
        match.group("payload").strip(),
    )


def _extract_next_action_candidates(text: str) -> list[str]:
    line = _extract_next_action_line(text)
    if not line:
        return []

    actions = SKILL_ACTION.findall(line)
    if "사용자 선택 대기" in line:
        actions.append("사용자 선택 대기")
    return actions


def _missing_markers(text: str, markers: list[str]) -> list[str]:
    return [marker for marker in markers if marker not in text]


def _score_case(metrics: dict[str, object]) -> dict[str, object]:
    percent = 100

    if not metrics["next_action_single"]:
        percent -= 30
    if metrics["expected_next_action_hit"] is False:
        percent -= 25

    percent -= min(int(metrics["question_count"]) * 10, 20)
    percent -= min(int(metrics["missing_markers_count"]) * 15, 45)
    percent = max(percent, 0)

    return {
        "percent": percent,
        "verdict": "good" if percent >= 85 else ("mixed" if percent >= 60 else "weak"),
    }


def evaluate_case(case: dict[str, object]) -> dict[str, object]:
    response = str(case.get("response", ""))
    expected_next_actions = [str(item) for item in case.get("expected_next_actions", [])]
    required_markers = [str(item) for item in case.get("required_markers", [])]
    next_actions = _extract_next_action_candidates(response)
    missing_markers = _missing_markers(response, required_markers)

    expected_hit: bool | None
    if expected_next_actions:
        expected_hit = len(next_actions) == 1 and any(
            action in next_actions for action in expected_next_actions
        )
    else:
        expected_hit = None

    metrics = {
        "output_chars": len(response),
        "next_action_count": len(next_actions),
        "next_action_single": len(next_actions) == 1,
        "next_actions": next_actions,
        "expected_next_action_hit": expected_hit,
        "question_count": _count_question_marks(response),
        "missing_markers_count": len(missing_markers),
        "missing_markers": missing_markers,
    }

    scored = {
        "skill": str(case.get("skill", "")),
        "request": str(case.get("request", "")),
        "metrics": metrics,
        "score": _score_case(metrics),
    }
    for field in ("source_type", "evidence", "comparison_id", "variant"):
        value = case.get(field)
        if isinstance(value, str) and value:
            scored[field] = value
    return scored


def build_report(cases: list[dict[str, object]]) -> dict[str, object]:
    scored_cases = [evaluate_case(case) for case in cases]
    case_count = len(scored_cases)
    if case_count == 0:
        return {
            "cases": [],
            "summary": {
                "case_count": 0,
                "avg_output_chars": 0,
                "next_action_single_rate": 0,
                "expected_next_action_hit_rate": 0,
                "avg_question_count": 0,
                "avg_missing_markers_count": 0,
                "avg_score_percent": 0,
                "source_type_counts": {},
            },
        }

    next_action_single_hits = sum(1 for item in scored_cases if item["metrics"]["next_action_single"])
    expected_hits = [
        item["metrics"]["expected_next_action_hit"]
        for item in scored_cases
        if item["metrics"]["expected_next_action_hit"] is not None
    ]

    summary = {
        "case_count": case_count,
        "avg_output_chars": sum(item["metrics"]["output_chars"] for item in scored_cases) / case_count,
        "next_action_single_rate": next_action_single_hits / case_count,
        "expected_next_action_hit_rate": (
            sum(1 for hit in expected_hits if hit) / len(expected_hits) if expected_hits else 0
        ),
        "avg_question_count": sum(item["metrics"]["question_count"] for item in scored_cases) / case_count,
        "avg_missing_markers_count": (
            sum(item["metrics"]["missing_markers_count"] for item in scored_cases) / case_count
        ),
        "avg_score_percent": sum(item["score"]["percent"] for item in scored_cases) / case_count,
        "source_type_counts": _count_source_types(scored_cases),
    }
    report = {"cases": scored_cases, "summary": summary}
    comparisons = _build_case_comparisons(scored_cases)
    if comparisons:
        report["comparisons"] = comparisons
        report["comparison_summary"] = _build_comparison_summary(comparisons)
    return report


def _compare_case(case: dict[str, object]) -> dict[str, object]:
    expected_mode = str(case["expected_mode"])
    expected_next_action = case.get("expected_next_action")
    baseline_mode = _infer_response_mode(str(case["request"]), str(case["baseline_policy"]))
    candidate_mode = _infer_response_mode(str(case["request"]), str(case["candidate_policy"]))
    baseline_output_chars = int(case["baseline_output_chars"])
    candidate_output_chars = int(case["candidate_output_chars"])
    baseline_task_start_delay = int(case["baseline_task_start_delay"])
    candidate_task_start_delay = int(case["candidate_task_start_delay"])
    baseline_reroute = _contains_user_reroute([str(item) for item in case["baseline_trace"]])
    candidate_reroute = _contains_user_reroute([str(item) for item in case["candidate_trace"]])
    baseline_next_action = case.get("baseline_next_action")
    candidate_next_action = case.get("candidate_next_action")

    compared = {
        "request": str(case["request"]),
        "expected_mode": expected_mode,
        "baseline": {
            "mode": baseline_mode,
            "correct": baseline_mode == expected_mode,
            "reroute": baseline_reroute,
            "output_chars": baseline_output_chars,
            "task_start_delay": baseline_task_start_delay,
        },
        "candidate": {
            "mode": candidate_mode,
            "correct": candidate_mode == expected_mode,
            "reroute": candidate_reroute,
            "output_chars": candidate_output_chars,
            "task_start_delay": candidate_task_start_delay,
        },
        "delta": {
            "output_chars": candidate_output_chars - baseline_output_chars,
            "task_start_delay": candidate_task_start_delay - baseline_task_start_delay,
            "reroute_improved": baseline_reroute and not candidate_reroute,
            "mode_correctness_improved": (
                baseline_mode != expected_mode and candidate_mode == expected_mode
            ),
        },
    }
    if isinstance(expected_next_action, str) and expected_next_action:
        compared["expected_next_action"] = expected_next_action
        if isinstance(baseline_next_action, str) and baseline_next_action:
            compared["baseline"]["next_action"] = baseline_next_action
            compared["baseline"]["next_action_correct"] = baseline_next_action == expected_next_action
        if isinstance(candidate_next_action, str) and candidate_next_action:
            compared["candidate"]["next_action"] = candidate_next_action
            compared["candidate"]["next_action_correct"] = candidate_next_action == expected_next_action
    for field in ("source_type", "evidence"):
        value = case.get(field)
        if isinstance(value, str) and value:
            compared[field] = value
    comparison_scope = case.get("comparison_scope")
    if isinstance(comparison_scope, str) and comparison_scope:
        compared["comparison_scope"] = comparison_scope
    baseline_response_sample = case.get("baseline_response_sample")
    if isinstance(baseline_response_sample, str) and baseline_response_sample:
        compared["baseline"]["response_sample"] = baseline_response_sample
    candidate_response_sample = case.get("candidate_response_sample")
    if isinstance(candidate_response_sample, str) and candidate_response_sample:
        compared["candidate"]["response_sample"] = candidate_response_sample
    return compared


def _decision_from_summary(summary: dict[str, object]) -> dict[str, object]:
    baseline_output_chars = float(summary["baseline_output_chars_avg"])
    output_delta = float(summary["candidate_output_chars_delta"])
    output_growth_rate = (output_delta / baseline_output_chars) if baseline_output_chars else 0
    observed_output_count = int(summary.get("observed_output_count", 0))
    observed_same_surface_count = int(summary.get("observed_same_surface_count", 0))
    rejected_observed_output_case_count = int(summary.get("rejected_observed_output_case_count", 0))
    rejected_observed_output_reasons = summary.get("rejected_observed_output_reasons", {})

    checks = {
        "mode_accuracy_up": float(summary["mode_accuracy_delta"]) >= 0.15,
        "reroute_rate_down": float(summary["reroute_rate_delta"]) <= -0.30,
        "task_start_delay_not_worse": float(summary["candidate_task_start_delay_delta"]) <= 0,
        "output_growth_within_budget": output_growth_rate <= 0.10,
    }
    if int(summary.get("next_action_case_count", 0)) > 0:
        checks["next_step_accuracy_not_worse"] = (
            float(summary.get("candidate_wrong_next_step_rate", 0)) == 0
            and float(summary.get("wrong_next_step_rate_delta", 0)) <= 0
        )
    passed = sum(1 for ok in checks.values() if ok)
    if passed >= 3:
        verdict = "adopt"
    elif passed >= 2:
        verdict = "revise"
    else:
        verdict = "hold"

    observed_evidence_guard = "ok"
    if observed_output_count > 0 and observed_same_surface_count == 0 and verdict == "adopt":
        verdict = "revise"
        observed_evidence_guard = "insufficient_same_surface"
    if checks.get("next_step_accuracy_not_worse") is False and verdict == "adopt":
        verdict = "revise"

    readiness_sample_count = int(summary.get("readiness_observed_sample_count", 0))
    readiness_same_surface_count = int(summary.get("readiness_same_surface_case_count", 0))
    distinct_policy_pair_count = int(
        summary.get(
            "readiness_distinct_policy_pair_count",
            summary.get("distinct_policy_pair_count", 0),
        )
    )
    baseline_comparison_ready = bool(summary.get("baseline_comparison_ready", False))
    observed_reason_signals_present = bool(summary.get("observed_reason_signals_present", False))
    deferred_reason_map = _readiness_deferred_reason_map()
    kpi_readiness = "ready"
    readiness_status_line = (
        "not ready: "
        f"samples {readiness_sample_count}/{KPI_MIN_SAMPLE_COUNT}, "
        f"same-surface {readiness_same_surface_count}/1, "
        f"policy pairs {distinct_policy_pair_count}/2"
    )
    next_kpi_blocker = "none"
    if readiness_sample_count < KPI_MIN_SAMPLE_COUNT:
        kpi_readiness = "incomplete"
        next_kpi_blocker = "insufficient_observed_samples"
    elif readiness_same_surface_count < 1:
        kpi_readiness = "incomplete"
        next_kpi_blocker = "insufficient_same_surface_evidence"
    elif distinct_policy_pair_count < 2:
        kpi_readiness = "incomplete"
        next_kpi_blocker = "insufficient_policy_pairs"
    else:
        readiness_status_line = "ready: baseline comparison wording can be enabled"

    if not baseline_comparison_ready and next_kpi_blocker == "none":
        kpi_readiness = "incomplete"
        next_kpi_blocker = "baseline_comparison_not_ready"
        readiness_status_line = "not ready: baseline comparison input is not ready"

    _, readiness_blocker_line = _resolve_readiness_blocker(
        sample_gap=max(KPI_MIN_SAMPLE_COUNT - readiness_sample_count, 0),
        same_surface_gap=max(KPI_MIN_SAME_SURFACE_COUNT - readiness_same_surface_count, 0),
        policy_pair_count=distinct_policy_pair_count,
        baseline_comparison_ready=baseline_comparison_ready,
    )

    baseline_comparison_status = "ready" if baseline_comparison_ready and kpi_readiness == "ready" else "deferred"
    if baseline_comparison_status == "ready":
        mode_delta = float(summary.get("mode_accuracy_delta", 0))
        reroute_delta = float(summary.get("reroute_rate_delta", 0))
        delay_delta = float(summary.get("candidate_task_start_delay_delta", 0))

        mode_direction = "improves" if mode_delta >= 0 else "worsens"
        reroute_direction = "improves" if reroute_delta <= 0 else "worsens"
        delay_direction = "improves" if delay_delta <= 0 else "worsens"
        baseline_comparison_line = (
            "baseline comparison ready: "
            f"candidate {mode_direction} mode accuracy by {abs(mode_delta) * 100:.1f}pp, "
            f"{reroute_direction} reroute rate by {abs(reroute_delta) * 100:.1f}pp, "
            f"and {delay_direction} task start delay by {abs(delay_delta):.1f}"
        )
    else:
        baseline_comparison_line = (
            "baseline comparison deferred: "
            + deferred_reason_map.get(next_kpi_blocker, "readiness requirements are not met")
        )
    if baseline_comparison_status == "ready":
        policy_comparison_summary = "policy comparison ready: baseline comparison wording can be enabled"
    else:
        policy_comparison_summary = (
            "policy comparison pending: "
            + deferred_reason_map.get(next_kpi_blocker, "readiness requirements are not met")
        )
    bottleneck_summary = (
        "policy comparison bottleneck: "
        + deferred_reason_map.get(next_kpi_blocker, "readiness requirements are not met")
    )
    if rejected_observed_output_case_count > 0:
        rejected_suffix = f"; rejected observed_output={rejected_observed_output_case_count}"
        if isinstance(rejected_observed_output_reasons, dict) and rejected_observed_output_reasons:
            reason_parts: list[str] = []
            for key in sorted(rejected_observed_output_reasons):
                count = rejected_observed_output_reasons.get(key)
                if isinstance(count, int):
                    reason_parts.append(f"{key}:{count}")
            if reason_parts:
                rejected_suffix += f" ({','.join(reason_parts)})"
        policy_comparison_summary += rejected_suffix
        bottleneck_summary += rejected_suffix
    if observed_reason_signals_present:
        policy_comparison_summary += "; reason signals observed"

    next_priority_recommendation, next_priority_reason = _resolve_next_priority(
        blocker=next_kpi_blocker,
        observed_reason_signals_present=observed_reason_signals_present,
        baseline_comparison_status=baseline_comparison_status,
    )

    return {
        "verdict": verdict,
        "passed_checks": passed,
        "total_checks": len(checks),
        "checks": checks,
        "output_growth_rate": output_growth_rate,
        "observed_evidence_guard": observed_evidence_guard,
        "kpi_readiness": kpi_readiness,
        "readiness_status_line": readiness_status_line,
        "readiness_blocker_line": readiness_blocker_line,
        "next_kpi_blocker": next_kpi_blocker,
        "baseline_comparison_status": baseline_comparison_status,
        "baseline_comparison_line": baseline_comparison_line,
        "policy_comparison_summary": policy_comparison_summary,
        "policy_comparison_bottleneck_summary": bottleneck_summary,
        "next_priority_recommendation": next_priority_recommendation,
        "next_priority_reason": next_priority_reason,
    }


def _classify_expensive_flow(case: dict[str, object]) -> str:
    expected_next_action = case.get("expected_next_action")
    baseline_next_action = case.get("baseline_next_action")
    baseline_trace = [str(item) for item in case.get("baseline_trace", [])]
    expected_mode = str(case["expected_mode"])
    baseline_output_chars = int(case["baseline_output_chars"])
    candidate_output_chars = int(case["candidate_output_chars"])
    baseline_task_start_delay = int(case["baseline_task_start_delay"])
    candidate_task_start_delay = int(case["candidate_task_start_delay"])

    if (
        isinstance(expected_next_action, str)
        and expected_next_action
        and isinstance(baseline_next_action, str)
        and baseline_next_action
        and baseline_next_action != expected_next_action
    ):
        return "wrong_next_step"
    if expected_mode in {"execute-first", "review-first"} and (
        baseline_task_start_delay > candidate_task_start_delay
    ):
        return "over_stage_entry"
    if _contains_user_reroute(baseline_trace):
        return "reroute_loop"
    if baseline_output_chars > candidate_output_chars:
        return "output_bloat"
    return "general_overhead"


def _score_expensive_flow(case: dict[str, object], flow_kind: str) -> int:
    baseline_output_chars = int(case["baseline_output_chars"])
    candidate_output_chars = int(case["candidate_output_chars"])
    baseline_task_start_delay = int(case["baseline_task_start_delay"])
    candidate_task_start_delay = int(case["candidate_task_start_delay"])
    baseline_trace = [str(item) for item in case.get("baseline_trace", [])]
    expected_next_action = case.get("expected_next_action")
    baseline_next_action = case.get("baseline_next_action")

    score = max(baseline_output_chars - candidate_output_chars, 0)
    score += max(baseline_task_start_delay - candidate_task_start_delay, 0) * 120
    if _contains_user_reroute(baseline_trace):
        score += 180
    if (
        isinstance(expected_next_action, str)
        and expected_next_action
        and isinstance(baseline_next_action, str)
        and baseline_next_action
        and baseline_next_action != expected_next_action
    ):
        score += 220
    if flow_kind == "over_stage_entry":
        score += 80
    return score


def build_expensive_flow_report(
    cases: list[dict[str, object]], top_n: int = 5
) -> dict[str, object]:
    flows: list[dict[str, object]] = []
    flow_kind_counts: dict[str, int] = {}
    observed_case_count = 0
    observed_reason_signal_counts: dict[str, int] = {}

    for case in cases:
        flow_kind = _classify_expensive_flow(case)
        waste_score = _score_expensive_flow(case, flow_kind)
        source_type = str(case.get("source_type", "")) if case.get("source_type") else ""
        expected_next_action = case.get("expected_next_action")
        if source_type.startswith("observed_"):
            observed_case_count += 1
        flow_kind_counts[flow_kind] = flow_kind_counts.get(flow_kind, 0) + 1

        flow = {
            "request": str(case["request"]),
            "expected_mode": str(case["expected_mode"]),
            "flow_kind": flow_kind,
            "waste_score": waste_score,
            "baseline_output_chars": int(case["baseline_output_chars"]),
            "candidate_output_chars": int(case["candidate_output_chars"]),
            "output_chars_saved": (
                int(case["baseline_output_chars"]) - int(case["candidate_output_chars"])
            ),
            "baseline_task_start_delay": int(case["baseline_task_start_delay"]),
            "candidate_task_start_delay": int(case["candidate_task_start_delay"]),
            "task_start_saved": (
                int(case["baseline_task_start_delay"])
                - int(case["candidate_task_start_delay"])
            ),
            "baseline_reroute": _contains_user_reroute(
                [str(item) for item in case.get("baseline_trace", [])]
            ),
        }
        if flow["baseline_reroute"]:
            flow["reroute_reason"] = "user_correction_after_baseline"
            flow["reroute_signal"] = "user_requested_path_change"
        if flow_kind == "output_bloat":
            flow["output_bloat_reason"] = "baseline_output_exceeds_candidate"
            flow["compression_signal"] = "char_reduction_confirmed"
        if isinstance(expected_next_action, str) and expected_next_action:
            baseline_next_action = case.get("baseline_next_action")
            candidate_next_action = case.get("candidate_next_action")
            flow["expected_next_action"] = expected_next_action
            next_action_incomplete = False
            if isinstance(baseline_next_action, str) and baseline_next_action:
                flow["baseline_next_action"] = baseline_next_action
                flow["baseline_next_action_correct"] = baseline_next_action == expected_next_action
            else:
                next_action_incomplete = True
            if isinstance(candidate_next_action, str) and candidate_next_action:
                flow["candidate_next_action"] = candidate_next_action
                flow["candidate_next_action_correct"] = candidate_next_action == expected_next_action
            else:
                next_action_incomplete = True
            flow["next_action_incomplete"] = next_action_incomplete
            flow["next_action_gap"] = (
                next_action_incomplete
                or flow.get("baseline_next_action_correct") is False
                or flow.get("candidate_next_action_correct") is False
            )
        for field in (
            "source_type",
            "evidence",
            "comparison_scope",
        ):
            value = case.get(field)
            if isinstance(value, str) and value:
                flow[field] = value
        if source_type.startswith("observed_"):
            for signal_key in ("reroute_reason", "output_bloat_reason", "compression_signal"):
                if signal_key in flow:
                    observed_reason_signal_counts[signal_key] = (
                        observed_reason_signal_counts.get(signal_key, 0) + 1
                    )
        flows.append(flow)

    ranked_flows = sorted(flows, key=lambda item: (-int(item["waste_score"]), item["request"]))
    top_flows = ranked_flows[:top_n]
    return {
        "flows": top_flows,
        "summary": {
            "case_count": len(cases),
            "top_flow_count": len(top_flows),
            "observed_case_count": observed_case_count,
            "flow_kind_counts": flow_kind_counts,
            "observed_reason_signal_counts": observed_reason_signal_counts,
        },
    }


def compare_response_modes(cases: list[dict[str, object]]) -> dict[str, object]:
    compared_cases = [_compare_case(case) for case in cases]
    case_count = len(compared_cases)
    dataset_run_counts = [
        int(case.get("dataset_total_run_count", 0))
        for case in cases
        if isinstance(case.get("dataset_total_run_count"), int)
    ]
    dataset_total_run_count = max(dataset_run_counts) if dataset_run_counts else 0
    dataset_reroute_rate = next(
        (
            float(case["dataset_reroute_rate"])
            for case in cases
            if isinstance(case.get("dataset_reroute_rate"), (int, float))
        ),
        None,
    )
    dataset_retry_to_success_rate = next(
        (
            float(case["dataset_retry_to_success_rate"])
            for case in cases
            if isinstance(case.get("dataset_retry_to_success_rate"), (int, float))
        ),
        None,
    )
    dataset_cost_per_successful_task = next(
        (
            float(case["dataset_cost_per_successful_task"])
            for case in cases
            if isinstance(case.get("dataset_cost_per_successful_task"), (int, float))
        ),
        None,
    )
    distinct_policies = _distinct_policies(cases)
    policy_pair_counts = _count_policy_pairs(cases)
    readiness_policy_pair_counts = _count_readiness_policy_pairs(cases)
    primary_policy_pair = _primary_policy_pair(policy_pair_counts)
    sample_case_count = len(cases)
    readiness = _summarize_readiness_thresholds(cases)
    observed_sample_case_count = int(readiness["observed_sample_count"])
    readiness_observed_sample_count = observed_sample_case_count
    distinct_policy_pair_count = len(policy_pair_counts)
    readiness_distinct_policy_pair_count = len(readiness_policy_pair_counts)
    sample_requirement_met = int(readiness["sample_gap"]) == 0
    policy_requirement_met = int(readiness["policy_pair_gap"]) == 0
    if case_count == 0:
        summary = {
            "total_run_count": dataset_total_run_count,
            "reroute_rate": dataset_reroute_rate,
            "retry_to_success_rate": dataset_retry_to_success_rate,
            "cost_per_successful_task": dataset_cost_per_successful_task,
            "case_count": 0,
            "sample_case_count": sample_case_count,
            "observed_sample_case_count": observed_sample_case_count,
            "readiness_observed_sample_count": readiness_observed_sample_count,
            "readiness_sample_gap": max(KPI_MIN_SAMPLE_COUNT - readiness_observed_sample_count, 0),
            "sample_requirement_met": sample_requirement_met,
            "distinct_policy_count": len(distinct_policies),
            "distinct_policies": distinct_policies,
            "distinct_policy_pair_count": distinct_policy_pair_count,
            "policy_requirement_met": policy_requirement_met,
            "policy_pair_counts": policy_pair_counts,
            "primary_policy_pair": primary_policy_pair,
            "baseline_mode_accuracy": 0,
            "candidate_mode_accuracy": 0,
            "mode_accuracy_delta": 0,
            "baseline_wrong_first_skill_rate": 0,
            "candidate_wrong_first_skill_rate": 0,
            "wrong_first_skill_rate_delta": 0,
            "next_action_case_count": 0,
            "next_action_incomplete_case_count": 0,
            "baseline_wrong_next_step_rate": 0,
            "candidate_wrong_next_step_rate": 0,
            "wrong_next_step_rate_delta": 0,
            "baseline_reroute_rate": 0,
            "candidate_reroute_rate": 0,
            "reroute_rate_delta": 0,
            "baseline_output_chars_avg": 0,
            "candidate_output_chars_avg": 0,
            "candidate_output_chars_delta": 0,
            "baseline_task_start_delay_avg": 0,
            "candidate_task_start_delay_avg": 0,
            "candidate_task_start_delay_delta": 0,
            "source_type_counts": {},
            "comparison_scope_counts": {},
            "observed_output_count": 0,
            "observed_same_surface_count": 0,
            "readiness_same_surface_case_count": 0,
            "readiness_same_surface_gap": KPI_MIN_SAME_SURFACE_COUNT,
            "baseline_comparison_ready": False,
        }
        return {"cases": [], "summary": summary, "decision": _decision_from_summary(summary)}

    mode_cases = [
        item for item in compared_cases if _case_participates_in_decision_metric(item, "mode_accuracy")
    ]
    baseline_mode_accuracy = _average([1 if item["baseline"]["correct"] else 0 for item in mode_cases])
    candidate_mode_accuracy = _average([1 if item["candidate"]["correct"] else 0 for item in mode_cases])
    baseline_wrong_first_skill_rate = _average([0 if item["baseline"]["correct"] else 1 for item in mode_cases])
    candidate_wrong_first_skill_rate = _average([0 if item["candidate"]["correct"] else 1 for item in mode_cases])
    next_action_cases = [
        item
        for item in compared_cases
        if item.get("expected_next_action")
        and "next_action_correct" in item["baseline"]
        and "next_action_correct" in item["candidate"]
    ]
    next_action_incomplete_case_count = sum(
        1
        for item in compared_cases
        if item.get("expected_next_action")
        and (
            "next_action_correct" not in item["baseline"]
            or "next_action_correct" not in item["candidate"]
        )
    )
    baseline_wrong_next_step_rate = _average(
        [0 if item["baseline"]["next_action_correct"] else 1 for item in next_action_cases]
    )
    candidate_wrong_next_step_rate = _average(
        [0 if item["candidate"]["next_action_correct"] else 1 for item in next_action_cases]
    )
    baseline_reroute_rate = _average([1 if item["baseline"]["reroute"] else 0 for item in compared_cases])
    candidate_reroute_rate = _average([1 if item["candidate"]["reroute"] else 0 for item in compared_cases])
    baseline_output_chars_avg = _average([item["baseline"]["output_chars"] for item in compared_cases])
    candidate_output_chars_avg = _average([item["candidate"]["output_chars"] for item in compared_cases])
    task_delay_cases = [
        item for item in compared_cases if _case_participates_in_decision_metric(item, "task_start_delay")
    ]
    baseline_task_start_delay_avg = _average(
        [item["baseline"]["task_start_delay"] for item in task_delay_cases]
    )
    candidate_task_start_delay_avg = _average(
        [item["candidate"]["task_start_delay"] for item in task_delay_cases]
    )
    observed_output_cases = [
        item for item in compared_cases if item.get("source_type") == "observed_output"
    ]
    comparison_scope_counts = _count_comparison_scopes(observed_output_cases)
    observed_same_surface_count = comparison_scope_counts.get("same_surface", 0)
    readiness_same_surface_case_count = observed_same_surface_count
    readiness_sample_gap = int(readiness["sample_gap"])
    readiness_same_surface_gap = int(readiness["same_surface_gap"])
    baseline_comparison_ready = bool(readiness["baseline_comparison_ready"])
    rejected_observed_output_case_count = max(
        int(case.get("dataset_rejected_observed_output_case_count", 0)) for case in cases
    )
    rejected_observed_output_reasons: dict[str, int] = {}
    for case in cases:
        raw_reasons = case.get("dataset_rejected_observed_output_reasons")
        if not isinstance(raw_reasons, dict):
            continue
        for key, count in raw_reasons.items():
            if isinstance(key, str) and isinstance(count, int):
                rejected_observed_output_reasons[key] = max(
                    rejected_observed_output_reasons.get(key, 0),
                    count,
                )

    summary_blocker, readiness_blocker_line = _resolve_readiness_blocker(
        sample_gap=readiness_sample_gap,
        same_surface_gap=readiness_same_surface_gap,
        policy_pair_count=readiness_distinct_policy_pair_count,
        baseline_comparison_ready=baseline_comparison_ready,
    )

    observed_reason_signals_present = any(
        item.get("source_type") == "observed_request"
        and (
            bool(item["baseline"]["reroute"])
            or int(item["baseline"]["output_chars"]) > int(item["candidate"]["output_chars"])
        )
        for item in compared_cases
    )

    summary = {
        "total_run_count": dataset_total_run_count,
        "reroute_rate": dataset_reroute_rate,
        "retry_to_success_rate": dataset_retry_to_success_rate,
        "cost_per_successful_task": dataset_cost_per_successful_task,
        "case_count": case_count,
        "sample_case_count": sample_case_count,
        "observed_sample_case_count": observed_sample_case_count,
        "readiness_observed_sample_count": readiness_observed_sample_count,
        "readiness_sample_gap": readiness_sample_gap,
        "sample_requirement_met": sample_requirement_met,
        "distinct_policy_count": len(distinct_policies),
        "distinct_policies": distinct_policies,
        "distinct_policy_pair_count": distinct_policy_pair_count,
        "readiness_distinct_policy_pair_count": readiness_distinct_policy_pair_count,
        "policy_requirement_met": policy_requirement_met,
        "policy_pair_counts": policy_pair_counts,
        "readiness_policy_pair_counts": readiness_policy_pair_counts,
        "primary_policy_pair": _primary_policy_pair(policy_pair_counts),
        "baseline_mode_accuracy": baseline_mode_accuracy,
        "candidate_mode_accuracy": candidate_mode_accuracy,
        "mode_accuracy_delta": candidate_mode_accuracy - baseline_mode_accuracy,
        "baseline_wrong_first_skill_rate": baseline_wrong_first_skill_rate,
        "candidate_wrong_first_skill_rate": candidate_wrong_first_skill_rate,
        "wrong_first_skill_rate_delta": (
            candidate_wrong_first_skill_rate - baseline_wrong_first_skill_rate
        ),
        "next_action_case_count": len(next_action_cases),
        "next_action_incomplete_case_count": next_action_incomplete_case_count,
        "baseline_wrong_next_step_rate": baseline_wrong_next_step_rate,
        "candidate_wrong_next_step_rate": candidate_wrong_next_step_rate,
        "wrong_next_step_rate_delta": (
            candidate_wrong_next_step_rate - baseline_wrong_next_step_rate
        ),
        "baseline_reroute_rate": baseline_reroute_rate,
        "candidate_reroute_rate": candidate_reroute_rate,
        "reroute_rate_delta": candidate_reroute_rate - baseline_reroute_rate,
        "baseline_output_chars_avg": baseline_output_chars_avg,
        "candidate_output_chars_avg": candidate_output_chars_avg,
        "candidate_output_chars_delta": candidate_output_chars_avg - baseline_output_chars_avg,
        "baseline_task_start_delay_avg": baseline_task_start_delay_avg,
        "candidate_task_start_delay_avg": candidate_task_start_delay_avg,
        "candidate_task_start_delay_delta": (
            candidate_task_start_delay_avg - baseline_task_start_delay_avg
        ),
        "source_type_counts": _count_source_types(compared_cases),
        "comparison_scope_counts": comparison_scope_counts,
        "observed_output_count": len(observed_output_cases),
        "observed_same_surface_count": observed_same_surface_count,
        "readiness_same_surface_case_count": readiness_same_surface_case_count,
        "readiness_same_surface_gap": readiness_same_surface_gap,
        "baseline_comparison_ready": baseline_comparison_ready,
        "readiness_blocker_line": readiness_blocker_line,
        "rejected_observed_output_case_count": rejected_observed_output_case_count,
        "rejected_observed_output_reasons": rejected_observed_output_reasons,
        "observed_reason_signals_present": observed_reason_signals_present,
    }
    return {"cases": compared_cases, "summary": summary, "decision": _decision_from_summary(summary)}


def _count_source_types(cases: list[dict[str, object]]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for case in cases:
        source_type = case.get("source_type")
        if not isinstance(source_type, str) or not source_type:
            continue
        counts[source_type] = counts.get(source_type, 0) + 1
    return counts


def _build_case_comparisons(scored_cases: list[dict[str, object]]) -> list[dict[str, object]]:
    grouped: dict[str, dict[str, dict[str, object]]] = {}
    for case in scored_cases:
        comparison_id = case.get("comparison_id")
        variant = case.get("variant")
        if not isinstance(comparison_id, str) or not comparison_id:
            continue
        if not isinstance(variant, str) or variant not in CASE_VARIANTS:
            continue
        grouped.setdefault(comparison_id, {})[variant] = case

    comparisons: list[dict[str, object]] = []
    for comparison_id, variants in grouped.items():
        baseline = variants.get("baseline")
        candidate = variants.get("candidate")
        if baseline is None or candidate is None:
            continue

        baseline_output_chars = int(baseline["metrics"]["output_chars"])
        candidate_output_chars = int(candidate["metrics"]["output_chars"])
        baseline_score_percent = int(baseline["score"]["percent"])
        candidate_score_percent = int(candidate["score"]["percent"])
        baseline_expected_hit = baseline["metrics"]["expected_next_action_hit"]
        candidate_expected_hit = candidate["metrics"]["expected_next_action_hit"]
        baseline_missing_markers_count = int(baseline["metrics"]["missing_markers_count"])
        candidate_missing_markers_count = int(candidate["metrics"]["missing_markers_count"])
        baseline_source_type = str(baseline.get("source_type") or "synthetic")
        candidate_source_type = str(candidate.get("source_type") or "synthetic")

        output_chars_delta = candidate_output_chars - baseline_output_chars
        output_reduction_rate = (
            (baseline_output_chars - candidate_output_chars) / baseline_output_chars
            if baseline_output_chars
            else 0
        )
        if baseline_source_type == "observed_output" and candidate_source_type == "observed_output":
            evidence_level = "observed_pair"
        elif "observed_output" in {baseline_source_type, candidate_source_type}:
            evidence_level = "mixed_pair"
        else:
            evidence_level = "synthetic_pair"

        comparison: dict[str, object] = {
            "comparison_id": comparison_id,
            "skill": str(candidate.get("skill") or baseline.get("skill") or ""),
            "request": str(candidate.get("request") or baseline.get("request") or ""),
            "baseline_source_type": baseline_source_type,
            "candidate_source_type": candidate_source_type,
            "evidence_level": evidence_level,
            "baseline_output_chars": baseline_output_chars,
            "candidate_output_chars": candidate_output_chars,
            "output_chars_delta": output_chars_delta,
            "output_reduction_rate": output_reduction_rate,
            "baseline_score_percent": baseline_score_percent,
            "candidate_score_percent": candidate_score_percent,
            "score_percent_delta": candidate_score_percent - baseline_score_percent,
            "baseline_missing_markers_count": baseline_missing_markers_count,
            "candidate_missing_markers_count": candidate_missing_markers_count,
            "missing_markers_improved": (
                candidate_missing_markers_count <= baseline_missing_markers_count
            ),
        }

        if baseline_expected_hit is not None and candidate_expected_hit is not None:
            comparison["baseline_expected_next_action_hit"] = baseline_expected_hit
            comparison["candidate_expected_next_action_hit"] = candidate_expected_hit
            comparison["next_action_preserved"] = (
                bool(baseline_expected_hit) and bool(candidate_expected_hit)
            )

        comparisons.append(comparison)

    return comparisons


def _build_comparison_summary(comparisons: list[dict[str, object]]) -> dict[str, object]:
    pair_count = len(comparisons)
    next_action_checks = [
        bool(item["next_action_preserved"])
        for item in comparisons
        if "next_action_preserved" in item
    ]
    return {
        "pair_count": pair_count,
        "avg_output_chars_delta": _average([float(item["output_chars_delta"]) for item in comparisons]),
        "avg_output_reduction_rate": _average(
            [float(item["output_reduction_rate"]) for item in comparisons]
        ),
        "avg_score_percent_delta": _average(
            [float(item["score_percent_delta"]) for item in comparisons]
        ),
        "missing_markers_preserved_rate": _average(
            [1 if bool(item["missing_markers_improved"]) else 0 for item in comparisons]
        ),
        "next_action_preserved_rate": (
            _average([1 if ok else 0 for ok in next_action_checks]) if next_action_checks else 0
        ),
        "evidence_level_counts": _count_comparison_evidence_levels(comparisons),
    }


def _count_comparison_scopes(cases: list[dict[str, object]]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for case in cases:
        scope = case.get("comparison_scope")
        if not isinstance(scope, str) or not scope:
            continue
        counts[scope] = counts.get(scope, 0) + 1
    return counts


def _count_comparison_evidence_levels(comparisons: list[dict[str, object]]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for item in comparisons:
        level = item.get("evidence_level")
        if not isinstance(level, str) or not level:
            continue
        counts[level] = counts.get(level, 0) + 1
    return counts


def _require_string_field(case: dict[str, object], index: int, field: str) -> str:
    value = case.get(field)
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"case[{index}].{field} is required")
    return value


def _optional_string_list(case: dict[str, object], index: int, field: str) -> list[str]:
    value = case.get(field, [])
    if value is None:
        return []
    if not isinstance(value, list) or any(not isinstance(item, str) for item in value):
        raise ValueError(f"case[{index}].{field} must be a list of strings")
    return value


def _optional_string_field(case: dict[str, object], index: int, field: str) -> str | None:
    value = case.get(field)
    if value is None:
        return None
    if not isinstance(value, str):
        raise ValueError(f"case[{index}].{field} must be a string")
    return value


def _normalize_case(case: object, index: int) -> dict[str, object]:
    if not isinstance(case, dict):
        raise ValueError(f"case[{index}] must be an object")

    normalized: dict[str, object] = {
        "response": _require_string_field(case, index, "response"),
        "expected_next_actions": _optional_string_list(case, index, "expected_next_actions"),
        "required_markers": _optional_string_list(case, index, "required_markers"),
    }

    for field in ("skill", "request", "source_type", "evidence", "comparison_id", "variant"):
        value = case.get(field)
        if value is None:
            continue
        if not isinstance(value, str):
            raise ValueError(f"case[{index}].{field} must be a string")
        normalized[field] = value

    source_type = normalized.get("source_type")
    if source_type is not None and source_type not in CASE_SOURCE_TYPES:
        raise ValueError(
            f"case[{index}].source_type must be one of {sorted(CASE_SOURCE_TYPES)}"
        )
    if source_type in {"observed_output", "current_contract_sample"} and not str(
        normalized.get("evidence", "")
    ).strip():
        raise ValueError(f"case[{index}].evidence is required for {source_type}")
    comparison_id = normalized.get("comparison_id")
    variant = normalized.get("variant")
    if (comparison_id is None) != (variant is None):
        raise ValueError(f"case[{index}] comparison_id and variant must be provided together")
    if variant is not None and variant not in CASE_VARIANTS:
        raise ValueError(f"case[{index}].variant must be one of {sorted(CASE_VARIANTS)}")

    return normalized


def _load_cases(path: Path) -> list[dict[str, object]]:
    data = json.loads(path.read_text(encoding="utf-8"))
    if isinstance(data, list):
        cases = data
    elif isinstance(data, dict) and isinstance(data.get("cases"), list):
        cases = data["cases"]
    else:
        raise ValueError("input JSON must be a list or an object with a 'cases' list")
    return [_normalize_case(case, index) for index, case in enumerate(cases)]


def _require_bool_field(case: dict[str, object], index: int, field: str) -> bool:
    value = case.get(field)
    if not isinstance(value, bool):
        raise ValueError(f"case[{index}].{field} must be a boolean")
    return value


def _require_int_field(case: dict[str, object], index: int, field: str) -> int:
    value = case.get(field)
    if not isinstance(value, int) or isinstance(value, bool):
        raise ValueError(f"case[{index}].{field} must be an integer")
    return value


def _require_mode_field(case: dict[str, object], index: int, field: str) -> str:
    value = _require_string_field(case, index, field)
    if value not in RESPONSE_MODES:
        raise ValueError(f"case[{index}].{field} must be one of {sorted(RESPONSE_MODES)}")
    return value


def _require_policy_field(case: dict[str, object], index: int, field: str) -> str:
    value = _require_string_field(case, index, field)
    if value not in POLICIES:
        raise ValueError(f"case[{index}].{field} must be one of {sorted(POLICIES)}")
    return value


def _normalize_response_mode_case(case: object, index: int) -> dict[str, object]:
    if not isinstance(case, dict):
        raise ValueError(f"case[{index}] must be an object")
    normalized = {
        "request": _require_string_field(case, index, "request"),
        "expected_mode": _require_mode_field(case, index, "expected_mode"),
        "baseline_policy": _require_policy_field(case, index, "baseline_policy"),
        "candidate_policy": _require_policy_field(case, index, "candidate_policy"),
        "baseline_trace": _optional_string_list(case, index, "baseline_trace"),
        "candidate_trace": _optional_string_list(case, index, "candidate_trace"),
        "baseline_output_chars": _require_int_field(case, index, "baseline_output_chars"),
        "candidate_output_chars": _require_int_field(case, index, "candidate_output_chars"),
        "baseline_task_start_delay": _require_int_field(case, index, "baseline_task_start_delay"),
        "candidate_task_start_delay": _require_int_field(case, index, "candidate_task_start_delay"),
    }
    source_type = _optional_string_field(case, index, "source_type") or "synthetic"
    if source_type not in {"synthetic", "observed_request", "observed_output"}:
        raise ValueError(
            f"case[{index}].source_type must be synthetic, observed_request, or observed_output"
        )
    normalized["source_type"] = source_type

    evidence = _optional_string_field(case, index, "evidence")
    if source_type in {"observed_request", "observed_output"} and not str(evidence or "").strip():
        raise ValueError(f"case[{index}].evidence is required for {source_type}")
    if evidence:
        normalized["evidence"] = evidence

    baseline_response_sample = _optional_string_field(case, index, "baseline_response_sample")
    candidate_response_sample = _optional_string_field(case, index, "candidate_response_sample")
    expected_next_action = _optional_string_field(case, index, "expected_next_action")
    baseline_next_action = _optional_string_field(case, index, "baseline_next_action")
    candidate_next_action = _optional_string_field(case, index, "candidate_next_action")
    if source_type == "observed_output":
        comparison_scope = _optional_string_field(case, index, "comparison_scope")
        if comparison_scope not in {"same_surface", "cross_surface"}:
            raise ValueError(
                f"case[{index}].comparison_scope must be same_surface or cross_surface for observed_output"
            )
        normalized["comparison_scope"] = comparison_scope
        if not str(baseline_response_sample or "").strip():
            raise ValueError(f"case[{index}].baseline_response_sample is required for observed_output")
        if not str(candidate_response_sample or "").strip():
            raise ValueError(f"case[{index}].candidate_response_sample is required for observed_output")
    if baseline_response_sample:
        normalized["baseline_response_sample"] = baseline_response_sample
    if candidate_response_sample:
        normalized["candidate_response_sample"] = candidate_response_sample
    if expected_next_action:
        normalized["expected_next_action"] = expected_next_action
    if baseline_next_action:
        normalized["baseline_next_action"] = baseline_next_action
    if candidate_next_action:
        normalized["candidate_next_action"] = candidate_next_action
    rejected_case_count = case.get("dataset_rejected_observed_output_case_count")
    if isinstance(rejected_case_count, int) and rejected_case_count >= 0:
        normalized["dataset_rejected_observed_output_case_count"] = rejected_case_count
    rejected_reasons = case.get("dataset_rejected_observed_output_reasons")
    if isinstance(rejected_reasons, dict):
        normalized_rejected_reasons: dict[str, int] = {}
        for key, count in rejected_reasons.items():
            if isinstance(key, str) and isinstance(count, int):
                normalized_rejected_reasons[key] = count
        if normalized_rejected_reasons:
            normalized["dataset_rejected_observed_output_reasons"] = normalized_rejected_reasons
    total_run_count = case.get("dataset_total_run_count")
    if isinstance(total_run_count, int) and total_run_count >= 0:
        normalized["dataset_total_run_count"] = total_run_count
    for field in (
        "dataset_reroute_rate",
        "dataset_retry_to_success_rate",
        "dataset_cost_per_successful_task",
    ):
        value = case.get(field)
        if isinstance(value, (int, float)):
            normalized[field] = float(value)
    return normalized


def _load_response_mode_cases(path: Path) -> list[dict[str, object]]:
    data = json.loads(path.read_text(encoding="utf-8"))
    if isinstance(data, list):
        cases = data
    elif isinstance(data, dict) and isinstance(data.get("cases"), list):
        cases = data["cases"]
    else:
        raise ValueError("input JSON must be a list or an object with a 'cases' list")
    return [_normalize_response_mode_case(case, index) for index, case in enumerate(cases)]


def _parse_policy_pair(value: object) -> tuple[str, str] | None:
    if not isinstance(value, str):
        return None
    raw = value.strip()
    if not raw or "->" not in raw:
        return None
    baseline_policy, candidate_policy = (part.strip() for part in raw.split("->", 1))
    if baseline_policy not in POLICIES or candidate_policy not in POLICIES:
        return None
    return baseline_policy, candidate_policy


def _build_observed_request_case_from_run_record(
    record: dict[str, object], *, run_id: str
) -> dict[str, object] | None:
    source_type = str(record.get("benchmark_source_type") or "").strip()
    if source_type != "observed_request":
        return None

    request = str(record.get("instruction") or "").strip()
    policy_pair = _parse_policy_pair(record.get("policy_pair"))
    if not request or policy_pair is None:
        return None

    baseline_policy, candidate_policy = policy_pair
    task_id = str(record.get("task_id") or "").strip() or "unknown"
    status = str(record.get("status") or "").strip() or "unknown"
    last_completed_step = str(record.get("last_completed_step") or "").strip() or "unknown"
    trace_line = f"run_status={status} last_step={last_completed_step}"
    baseline_trace = _optional_string_list(record, 0, "baseline_trace")
    candidate_trace = _optional_string_list(record, 0, "candidate_trace")
    baseline_output_chars = record.get("baseline_output_chars")
    candidate_output_chars = record.get("candidate_output_chars")
    baseline_task_start_delay = record.get("baseline_task_start_delay")
    candidate_task_start_delay = record.get("candidate_task_start_delay")

    has_rich_metadata = all(
        [
            isinstance(baseline_output_chars, int) and not isinstance(baseline_output_chars, bool),
            isinstance(candidate_output_chars, int) and not isinstance(candidate_output_chars, bool),
            isinstance(baseline_task_start_delay, int)
            and not isinstance(baseline_task_start_delay, bool),
            isinstance(candidate_task_start_delay, int)
            and not isinstance(candidate_task_start_delay, bool),
        ]
    )
    if not baseline_trace:
        baseline_trace = [trace_line]
    if not candidate_trace:
        candidate_trace = [trace_line]
    if not has_rich_metadata:
        baseline_output_chars = len(request)
        candidate_output_chars = len(request)
        baseline_task_start_delay = 0
        candidate_task_start_delay = 0

    return {
        "request": request,
        "expected_mode": _infer_expected_response_mode(request),
        "baseline_policy": baseline_policy,
        "candidate_policy": candidate_policy,
        "baseline_trace": baseline_trace,
        "candidate_trace": candidate_trace,
        "baseline_output_chars": baseline_output_chars,
        "candidate_output_chars": candidate_output_chars,
        "baseline_task_start_delay": baseline_task_start_delay,
        "candidate_task_start_delay": candidate_task_start_delay,
        "source_type": source_type,
        "neutral_seed": not has_rich_metadata,
        "evidence": f"run={run_id} task={task_id} source={source_type}",
    }


def _build_observed_output_case_from_run_record(
    record: dict[str, object], *, run_id: str
) -> dict[str, object] | None:
    source_type = str(record.get("benchmark_source_type") or "").strip()
    if source_type != "observed_output":
        return None

    request = str(record.get("instruction") or "").strip()
    policy_pair = _parse_policy_pair(record.get("policy_pair"))
    comparison_scope = str(record.get("comparison_scope") or "").strip()
    baseline_response_sample = str(record.get("baseline_response_sample") or "").strip()
    candidate_response_sample = str(record.get("candidate_response_sample") or "").strip()
    if (
        not request
        or policy_pair is None
        or comparison_scope not in {"same_surface", "cross_surface"}
        or not baseline_response_sample
        or not candidate_response_sample
    ):
        return None

    baseline_policy, candidate_policy = policy_pair
    task_id = str(record.get("task_id") or "").strip() or "unknown"
    status = str(record.get("status") or "").strip() or "unknown"
    last_completed_step = str(record.get("last_completed_step") or "").strip() or "unknown"
    trace_line = f"run_status={status} last_step={last_completed_step}"

    return {
        "request": request,
        "expected_mode": _infer_expected_response_mode(request),
        "baseline_policy": baseline_policy,
        "candidate_policy": candidate_policy,
        "baseline_trace": [trace_line],
        "candidate_trace": [trace_line],
        "baseline_output_chars": len(baseline_response_sample),
        "candidate_output_chars": len(candidate_response_sample),
        "baseline_task_start_delay": 0,
        "candidate_task_start_delay": 0,
        "source_type": source_type,
        "comparison_scope": comparison_scope,
        "decision_metric_exclusions": ["mode_accuracy", "task_start_delay"],
        "baseline_response_sample": baseline_response_sample,
        "candidate_response_sample": candidate_response_sample,
        "evidence": f"run={run_id} task={task_id} source={source_type}",
    }


def _validate_observed_output_run_record(record: dict[str, object]) -> str | None:
    source_type = str(record.get("benchmark_source_type") or "").strip()
    if source_type != "observed_output":
        return None
    request = str(record.get("instruction") or "").strip()
    if not request:
        return "missing_instruction"
    if _parse_policy_pair(record.get("policy_pair")) is None:
        return "invalid_policy_pair"
    comparison_scope = str(record.get("comparison_scope") or "").strip()
    if comparison_scope not in {"same_surface", "cross_surface"}:
        return "invalid_comparison_scope"
    baseline_response_sample = str(record.get("baseline_response_sample") or "").strip()
    if not baseline_response_sample:
        return "missing_baseline_response_sample"
    candidate_response_sample = str(record.get("candidate_response_sample") or "").strip()
    if not candidate_response_sample:
        return "missing_candidate_response_sample"
    return None


def collect_observed_response_mode_cases(runs_dir: Path) -> dict[str, object]:
    if not runs_dir.exists():
        return {
            "cases": [],
            "summary": {
                "total_run_count": 0,
                "reroute_rate": None,
                "retry_to_success_rate": None,
                "cost_per_successful_task": None,
                "case_count": 0,
                "observed_sample_case_count": 0,
                "neutral_seed_case_count": 0,
                "observed_output_case_count": 0,
                "same_surface_case_count": 0,
                "cross_surface_case_count": 0,
                "readiness_observed_sample_count": 0,
                "readiness_same_surface_case_count": 0,
                "distinct_policy_pair_count": 0,
                "policy_pair_counts": {},
                "rejected_observed_output_case_count": 0,
                "rejected_observed_output_reasons": {},
                "observed_data_bottleneck_summary": "observed data bottleneck: need more observed samples",
            },
        }

    cases: list[dict[str, object]] = []
    run_records: list[dict[str, object]] = []
    rejected_observed_output_reasons: dict[str, int] = {}
    for run_dir in sorted(runs_dir.iterdir()):
        result_path = run_dir / "result.json"
        if not result_path.exists():
            continue
        try:
            record = json.loads(result_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            continue
        if not isinstance(record, dict):
            continue
        run_records.append(record)
        case = _build_observed_request_case_from_run_record(record, run_id=run_dir.name)
        if case is None:
            rejection_reason = _validate_observed_output_run_record(record)
            case = _build_observed_output_case_from_run_record(record, run_id=run_dir.name)
            if case is None and rejection_reason:
                rejected_observed_output_reasons[rejection_reason] = (
                    rejected_observed_output_reasons.get(rejection_reason, 0) + 1
                )
        if case is not None:
            cases.append(case)

    multi_run_kpis = _summarize_multi_run_kpis(run_records)
    for case in cases:
        case["dataset_total_run_count"] = multi_run_kpis["total_run_count"]
        case["dataset_reroute_rate"] = multi_run_kpis["reroute_rate"]
        if multi_run_kpis["retry_to_success_rate"] is not None:
            case["dataset_retry_to_success_rate"] = multi_run_kpis["retry_to_success_rate"]
        if multi_run_kpis["cost_per_successful_task"] is not None:
            case["dataset_cost_per_successful_task"] = multi_run_kpis["cost_per_successful_task"]

    comparison_scope_counts = _count_comparison_scopes(cases)
    policy_pair_counts = _count_policy_pairs(cases)
    readiness_policy_pair_counts = _count_readiness_policy_pairs(cases)
    readiness = _summarize_readiness_thresholds(cases)
    readiness_observed_sample_count = int(readiness["observed_sample_count"])
    readiness_same_surface_case_count = int(readiness["same_surface_count"])
    readiness_sample_gap = max(KPI_MIN_SAMPLE_COUNT - readiness_observed_sample_count, 0)
    readiness_same_surface_gap = max(
        KPI_MIN_SAME_SURFACE_COUNT - readiness_same_surface_case_count,
        0,
    )
    baseline_comparison_ready = (
        readiness_sample_gap == 0
        and readiness_same_surface_gap == 0
        and len(readiness_policy_pair_counts) >= KPI_MIN_POLICY_PAIR_COUNT
    )
    readiness_blocker, readiness_blocker_line = _resolve_readiness_blocker(
        sample_gap=readiness_sample_gap,
        same_surface_gap=readiness_same_surface_gap,
        policy_pair_count=len(readiness_policy_pair_counts),
        baseline_comparison_ready=baseline_comparison_ready,
    )
    baseline_comparison_status = "ready" if baseline_comparison_ready else "deferred"
    observed_reason_signals_present = any(
        str(case.get("source_type") or "").strip() == "observed_request"
        and not bool(case.get("neutral_seed"))
        for case in cases
    )
    observed_data_bottleneck_summary = "observed data bottleneck: need more observed samples"
    if readiness_observed_sample_count >= KPI_MIN_SAMPLE_COUNT:
        if readiness_same_surface_case_count < KPI_MIN_SAME_SURFACE_COUNT:
            observed_data_bottleneck_summary = (
                "observed data bottleneck: need more same-surface evidence"
            )
        elif len(readiness_policy_pair_counts) < KPI_MIN_POLICY_PAIR_COUNT:
            observed_data_bottleneck_summary = (
                "observed data bottleneck: need more policy pair coverage"
            )
        else:
            observed_data_bottleneck_summary = "observed data bottleneck: baseline comparison input is ready"
    rejected_observed_output_case_count = sum(rejected_observed_output_reasons.values())
    if rejected_observed_output_case_count > 0:
        reason_parts: list[str] = []
        for key in sorted(rejected_observed_output_reasons):
            count = rejected_observed_output_reasons.get(key)
            if isinstance(count, int):
                reason_parts.append(f"{key}:{count}")
        if reason_parts:
            observed_data_bottleneck_summary += (
                f"; rejected observed_output={rejected_observed_output_case_count} "
                f"({','.join(reason_parts)})"
            )
    next_priority_recommendation, next_priority_reason = _resolve_next_priority(
        blocker=readiness_blocker,
        observed_reason_signals_present=observed_reason_signals_present,
        baseline_comparison_status=baseline_comparison_status,
    )
    for case in cases:
        case["dataset_rejected_observed_output_case_count"] = rejected_observed_output_case_count
        case["dataset_rejected_observed_output_reasons"] = dict(rejected_observed_output_reasons)
    return {
        "cases": cases,
        "summary": {
            "total_run_count": multi_run_kpis["total_run_count"],
            "reroute_rate": multi_run_kpis["reroute_rate"],
            "retry_to_success_rate": multi_run_kpis["retry_to_success_rate"],
            "cost_per_successful_task": multi_run_kpis["cost_per_successful_task"],
            "case_count": len(cases),
            "observed_sample_case_count": readiness_observed_sample_count,
            "neutral_seed_case_count": sum(1 for case in cases if bool(case.get("neutral_seed"))),
            "observed_output_case_count": sum(
                1 for case in cases if str(case.get("source_type") or "").strip() == "observed_output"
            ),
            "same_surface_case_count": comparison_scope_counts.get("same_surface", 0),
            "cross_surface_case_count": comparison_scope_counts.get("cross_surface", 0),
            "readiness_observed_sample_count": readiness_observed_sample_count,
            "readiness_same_surface_case_count": readiness_same_surface_case_count,
            "readiness_sample_gap": readiness_sample_gap,
            "readiness_same_surface_gap": readiness_same_surface_gap,
            "distinct_policy_pair_count": len(policy_pair_counts),
            "readiness_distinct_policy_pair_count": len(readiness_policy_pair_counts),
            "baseline_comparison_ready": baseline_comparison_ready,
            "readiness_blocker_line": readiness_blocker_line,
            "observed_reason_signals_present": observed_reason_signals_present,
            "policy_pair_counts": policy_pair_counts,
            "readiness_policy_pair_counts": readiness_policy_pair_counts,
            "fixture_taxonomy_counts": _fixture_taxonomy_counts_from_readiness(cases),
            "rejected_observed_output_case_count": rejected_observed_output_case_count,
            "rejected_observed_output_reasons": rejected_observed_output_reasons,
            "observed_data_bottleneck_summary": observed_data_bottleneck_summary,
            "next_priority_recommendation": next_priority_recommendation,
            "next_priority_reason": next_priority_reason,
        },
    }


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Score OMC skill outputs with compact benchmark metrics.")
    sub = parser.add_subparsers(dest="command", required=True)

    score = sub.add_parser("score", help="Read cases JSON and output score report.")
    score.add_argument("--input", type=Path, required=True, help="Input JSON path")
    score.add_argument("--format", choices=["json"], default="json", help="Output format")

    response_mode = sub.add_parser(
        "compare-response-modes",
        help="Compare baseline and candidate response-mode policy outputs.",
    )
    response_mode.add_argument("--input", type=Path, required=True, help="Input JSON path")

    collect_observed = sub.add_parser(
        "collect-observed-response-modes",
        help="Collect neutral observed_request seed cases from .omc/runs result history.",
    )
    collect_observed.add_argument(
        "--runs-dir", type=Path, required=True, help="Runs directory path (for example .omc/runs)"
    )

    expensive_flows = sub.add_parser(
        "top-expensive-flows",
        help="Rank the most expensive response-mode flows.",
    )
    expensive_flows.add_argument("--input", type=Path, required=True, help="Input JSON path")
    expensive_flows.add_argument(
        "--top-n", type=int, default=5, help="Number of expensive flows to return"
    )
    return parser


def main() -> int:
    args = _parser().parse_args()
    if args.command == "score":
        cases = _load_cases(args.input)
        report = build_report(cases)
        json.dump(report, sys.stdout, ensure_ascii=False, indent=2)
        sys.stdout.write("\n")
        return 0
    if args.command == "compare-response-modes":
        cases = _load_response_mode_cases(args.input)
        report = compare_response_modes(cases)
        json.dump(report, sys.stdout, ensure_ascii=False, indent=2)
        sys.stdout.write("\n")
        return 0
    if args.command == "collect-observed-response-modes":
        report = collect_observed_response_mode_cases(args.runs_dir)
        json.dump(report, sys.stdout, ensure_ascii=False, indent=2)
        sys.stdout.write("\n")
        return 0
    if args.command == "top-expensive-flows":
        cases = _load_response_mode_cases(args.input)
        report = build_expensive_flow_report(cases, top_n=args.top_n)
        json.dump(report, sys.stdout, ensure_ascii=False, indent=2)
        sys.stdout.write("\n")
        return 0
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
