#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path


NEXT_ACTION_LINE = re.compile(r"다음 액션:\s*(.+)")
SKILL_ACTION = re.compile(r"\$omc-[a-z-]+")
RESPONSE_MODES = {"answer-first", "execute-first", "review-first"}
POLICIES = {"baseline", "candidate"}
CASE_VARIANTS = {"baseline", "candidate"}
CASE_SOURCE_TYPES = {"synthetic", "observed_output", "current_contract_sample"}


def _count_question_marks(text: str) -> int:
    return text.count("?")


def _average(values: list[int | float]) -> float:
    return sum(values) / len(values) if values else 0


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


def _extract_next_action_line(text: str) -> str:
    for line in text.splitlines():
        match = NEXT_ACTION_LINE.search(line)
        if match:
            return match.group(1).strip()
    return ""


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

    return {
        "verdict": verdict,
        "passed_checks": passed,
        "total_checks": len(checks),
        "checks": checks,
        "output_growth_rate": output_growth_rate,
        "observed_evidence_guard": observed_evidence_guard,
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

    for case in cases:
        flow_kind = _classify_expensive_flow(case)
        waste_score = _score_expensive_flow(case, flow_kind)
        source_type = str(case.get("source_type", "")) if case.get("source_type") else ""
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
        for field in (
            "expected_next_action",
            "baseline_next_action",
            "candidate_next_action",
            "source_type",
            "evidence",
            "comparison_scope",
        ):
            value = case.get(field)
            if isinstance(value, str) and value:
                flow[field] = value
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
        },
    }


def compare_response_modes(cases: list[dict[str, object]]) -> dict[str, object]:
    compared_cases = [_compare_case(case) for case in cases]
    case_count = len(compared_cases)
    if case_count == 0:
        summary = {
            "case_count": 0,
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
        }
        return {"cases": [], "summary": summary, "decision": _decision_from_summary(summary)}

    baseline_mode_accuracy = _average([1 if item["baseline"]["correct"] else 0 for item in compared_cases])
    candidate_mode_accuracy = _average([1 if item["candidate"]["correct"] else 0 for item in compared_cases])
    baseline_wrong_first_skill_rate = _average([0 if item["baseline"]["correct"] else 1 for item in compared_cases])
    candidate_wrong_first_skill_rate = _average([0 if item["candidate"]["correct"] else 1 for item in compared_cases])
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
    baseline_task_start_delay_avg = _average(
        [item["baseline"]["task_start_delay"] for item in compared_cases]
    )
    candidate_task_start_delay_avg = _average(
        [item["candidate"]["task_start_delay"] for item in compared_cases]
    )
    observed_output_cases = [
        item for item in compared_cases if item.get("source_type") == "observed_output"
    ]
    comparison_scope_counts = _count_comparison_scopes(observed_output_cases)
    observed_same_surface_count = comparison_scope_counts.get("same_surface", 0)

    summary = {
        "case_count": case_count,
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
    if args.command == "top-expensive-flows":
        cases = _load_response_mode_cases(args.input)
        report = build_expensive_flow_report(cases, top_n=args.top_n)
        json.dump(report, sys.stdout, ensure_ascii=False, indent=2)
        sys.stdout.write("\n")
        return 0
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
