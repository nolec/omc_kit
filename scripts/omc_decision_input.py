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
