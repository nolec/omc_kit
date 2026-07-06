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
