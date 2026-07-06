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
