#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import json
import math
import re
from pathlib import Path
from typing import Any, Callable

from omc_executor_shadow import (
    build_two_child_sequence_grant,
    execute_two_child_sequence_grant_file,
)


OPERATIONAL_ACCEPTANCE_CONTRACT: dict[str, Any] = {
    "schema_version": 1,
    "mode": "two_child_sequential_opt_in",
    "required_metrics": [
        "external_call_count",
        "total_elapsed_sec",
        "total_output_chars",
        "usage_durability",
    ],
    "scenarios": [
        {
            "id": "completed",
            "expected_statuses": ["completed"],
            "expected_reason_codes": ["sequence_completed"],
            "expected_external_calls": [2],
        },
        {
            "id": "first_child_failed",
            "expected_statuses": ["review_required", "indeterminate"],
            "expected_reason_codes": ["child_not_succeeded"],
            "expected_external_calls": [0, 1],
        },
        {
            "id": "second_child_failed",
            "expected_statuses": ["review_required", "indeterminate"],
            "expected_reason_codes": ["child_not_succeeded"],
            "expected_external_calls": [1, 2],
        },
        {
            "id": "expired_before_first_child",
            "expected_statuses": ["blocked"],
            "expected_reason_codes": ["sequence_grant_expired"],
            "expected_external_calls": [0],
        },
        {
            "id": "expired_before_second_child",
            "expected_statuses": ["review_required"],
            "expected_reason_codes": ["sequence_grant_expired"],
            "expected_external_calls": [1],
        },
        {
            "id": "duplicate_claim",
            "expected_statuses": ["blocked", "indeterminate"],
            "expected_reason_codes": ["sequence_already_started"],
            "expected_external_calls": [0],
        },
    ],
    "invariants": {
        "automatic_retry_allowed": False,
        "automatic_redistribution_allowed": False,
        "automatic_fallback_allowed": False,
        "automatic_resume_allowed": False,
    },
}

_SEQUENCE_ID_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")


def _canonical_sha256(value: Any) -> str:
    payload = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def _blocked(reason_code: str) -> dict[str, Any]:
    return {
        "status": "blocked",
        "reason_code": reason_code,
        "external_call_count": 0,
        "total_elapsed_sec": 0.0,
        "total_output_chars": 0,
        "usage_durability": "not_applicable",
        "acceptance_contract_sha256": _canonical_sha256(OPERATIONAL_ACCEPTANCE_CONTRACT),
        "acceptance_status": "not_applicable",
        "acceptance_scenario": None,
        "acceptance_violations": [],
    }


def _runtime_paths(target: Path, sequence_id: str) -> tuple[Path, Path] | None:
    omc_root = target / ".omc"
    executor_root = omc_root / "executor"
    sequences_root = executor_root / "sequences"
    runtime_parts = [omc_root, executor_root, sequences_root]
    try:
        for path in runtime_parts:
            if path.is_symlink():
                return None
            path.mkdir(exist_ok=True)
            if path.resolve().parent != target.resolve() and target.resolve() not in path.resolve().parents:
                return None
    except OSError:
        return None
    return runtime_parts[-1] / f"{sequence_id}.json", runtime_parts[1] / "single-child.json"


def _usage_durability(result: dict[str, Any]) -> str:
    children = result.get("children")
    if not isinstance(children, list):
        return "not_applicable" if result.get("external_call_count", 0) == 0 else "durability_unknown"
    observed = {
        child.get("usage_durability")
        for child in children
        if isinstance(child, dict)
        and child.get("usage_durability")
        in {"durable", "observed_only", "durability_unknown"}
    }
    if not observed:
        return "not_applicable"
    if observed == {"durable"}:
        return "durable"
    if "durability_unknown" in observed:
        return "durability_unknown"
    return "observed_only"


def _acceptance_scenario(
    result: dict[str, Any], *, child_ids: list[str]
) -> str | None:
    reason_code = result.get("reason_code")
    external_calls = result.get("external_call_count")
    if reason_code == "sequence_grant_expired":
        if external_calls == 0:
            return "expired_before_first_child"
        if external_calls == 1:
            return "expired_before_second_child"
        return None
    if reason_code == "sequence_already_started" and external_calls == 0:
        return "duplicate_claim"
    if result.get("status") == "completed":
        return "completed"
    if reason_code == "child_not_succeeded":
        failed_child_id = result.get("failed_child_id")
        if failed_child_id == child_ids[0] and external_calls in {0, 1}:
            return "first_child_failed"
        if failed_child_id == child_ids[1] and external_calls in {1, 2}:
            return "second_child_failed"
    return None


def _acceptance_evidence(
    result: dict[str, Any],
    *,
    child_ids: list[str],
    raw_metric_keys: set[str],
) -> dict[str, Any]:
    violations: list[str] = []
    scenario_id = _acceptance_scenario(result, child_ids=child_ids)
    external_calls = result.get("external_call_count")
    children = result.get("children")

    ordered_children = (
        children
        if isinstance(children, list)
        and len(children) == len(child_ids)
        and all(isinstance(child, dict) for child in children)
        and [child.get("child_id") for child in children] == child_ids
        else None
    )

    if scenario_id not in {"expired_before_first_child", "duplicate_claim"}:
        for metric in ("external_call_count", "total_elapsed_sec", "total_output_chars"):
            if metric not in raw_metric_keys:
                violations.append(f"required_metric_missing:{metric}")
    if (
        not isinstance(external_calls, int)
        or isinstance(external_calls, bool)
        or external_calls < 0
    ):
        violations.append("metric_invalid:external_call_count")
    elapsed = result.get("total_elapsed_sec")
    if (
        not isinstance(elapsed, (int, float))
        or isinstance(elapsed, bool)
        or not math.isfinite(elapsed)
        or elapsed < 0
    ):
        violations.append("metric_invalid:total_elapsed_sec")
    output_chars = result.get("total_output_chars")
    if (
        not isinstance(output_chars, int)
        or isinstance(output_chars, bool)
        or output_chars < 0
    ):
        violations.append("metric_invalid:total_output_chars")
    if result.get("usage_durability") not in {
        "durable",
        "observed_only",
        "durability_unknown",
        "not_applicable",
    }:
        violations.append("metric_invalid:usage_durability")

    scenario = next(
        (
            candidate
            for candidate in OPERATIONAL_ACCEPTANCE_CONTRACT["scenarios"]
            if candidate["id"] == scenario_id
        ),
        None,
    )
    if scenario is None:
        violations.append("scenario_not_preregistered")
    else:
        if result.get("status") not in scenario["expected_statuses"]:
            violations.append("scenario_status_mismatch")
        if result.get("reason_code") not in scenario["expected_reason_codes"]:
            violations.append("scenario_reason_code_mismatch")
        if external_calls not in scenario["expected_external_calls"]:
            violations.append("scenario_external_call_count_mismatch")

    if scenario_id == "completed":
        if result.get("completed_child_ids") != child_ids:
            violations.append("completed_children_mismatch")
        if result.get("pending_child_ids") != [] or result.get("failed_child_id") is not None:
            violations.append("completed_terminal_state_mismatch")
        if ordered_children is None or any(
            child.get("status") != "succeeded" for child in ordered_children
        ):
            violations.append("completed_child_evidence_mismatch")
    elif scenario_id == "first_child_failed":
        if (
            result.get("failed_child_id") != child_ids[0]
            or result.get("completed_child_ids") != []
            or result.get("pending_child_ids") != [child_ids[1]]
        ):
            violations.append("first_child_failure_state_mismatch")
        if (
            ordered_children is None
            or ordered_children[0].get("status")
            not in {"failed", "timeout", "blocked", "indeterminate"}
            or ordered_children[1].get("status") != "not_started"
        ):
            violations.append("first_child_failure_evidence_mismatch")
    elif scenario_id == "second_child_failed":
        if (
            result.get("failed_child_id") != child_ids[1]
            or result.get("completed_child_ids") != [child_ids[0]]
            or result.get("pending_child_ids") != []
        ):
            violations.append("second_child_failure_state_mismatch")
        if (
            ordered_children is None
            or ordered_children[0].get("status") != "succeeded"
            or ordered_children[1].get("status")
            not in {"failed", "timeout", "blocked", "indeterminate"}
        ):
            violations.append("second_child_failure_evidence_mismatch")
    elif scenario_id == "expired_before_second_child":
        if (
            result.get("completed_child_ids") != [child_ids[0]]
            or result.get("pending_child_ids") != [child_ids[1]]
            or result.get("failed_child_id") is not None
        ):
            violations.append("second_child_expiry_state_mismatch")
        if (
            ordered_children is None
            or ordered_children[0].get("status") != "succeeded"
            or ordered_children[1].get("status") != "not_started"
        ):
            violations.append("second_child_expiry_evidence_mismatch")

    return {
        "acceptance_status": "passed" if not violations else "failed",
        "acceptance_scenario": scenario_id,
        "acceptance_violations": violations,
    }


def run_sequence_request(
    request: dict[str, Any],
    *,
    target: str | Path,
    runner: Callable[..., dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Validate and execute one explicitly approved exact two-child request."""
    if not isinstance(request, dict):
        return _blocked("sequence_request_invalid")
    grant = build_two_child_sequence_grant(request)
    if grant.get("status") != "ready":
        return {**_blocked(str(grant.get("reason_code", "sequence_grant_invalid"))), "grant": grant}
    sequence_id = request.get("sequence_id")
    if not isinstance(sequence_id, str) or not _SEQUENCE_ID_PATTERN.fullmatch(sequence_id):
        return _blocked("sequence_id_invalid")
    project_root = Path(target).resolve()
    if not project_root.is_dir():
        return _blocked("sequence_target_invalid")

    paths = _runtime_paths(project_root, sequence_id)
    if paths is None:
        return _blocked("sequence_ledger_path_unsafe")
    sequence_ledger_path, single_child_ledger_path = paths
    result = execute_two_child_sequence_grant_file(
        grant,
        sequence_ledger_path,
        single_child_ledger_path,
        prompts=request["child_prompts"],
        project_root=project_root,
        runner=runner,
    )
    if not isinstance(result, dict):
        return _blocked("sequence_result_invalid")
    enriched = dict(result)
    raw_metric_keys = set(enriched)
    enriched.setdefault("external_call_count", 0)
    enriched.setdefault("total_elapsed_sec", 0.0)
    enriched.setdefault("total_output_chars", 0)
    enriched["usage_durability"] = _usage_durability(enriched)
    enriched["acceptance_contract_sha256"] = _canonical_sha256(
        OPERATIONAL_ACCEPTANCE_CONTRACT
    )
    enriched["ledger_paths"] = {
        "sequence": str(sequence_ledger_path.relative_to(project_root)),
        "single_child": str(single_child_ledger_path.relative_to(project_root)),
    }
    enriched.update(
        _acceptance_evidence(
            enriched,
            child_ids=list(grant["ordered_child_ids"]),
            raw_metric_keys=raw_metric_keys,
        )
    )
    return enriched


def _load_request(path: Path) -> dict[str, Any] | None:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        return None
    return payload if isinstance(payload, dict) else None


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Execute one explicitly approved exact two-child sequence."
    )
    parser.add_argument("--request-file", type=Path, required=True)
    parser.add_argument("--target", type=Path, default=Path.cwd())
    args = parser.parse_args()

    request = _load_request(args.request_file)
    result = (
        run_sequence_request(request, target=args.target)
        if request is not None
        else _blocked("sequence_request_invalid")
    )
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return (
        0
        if result.get("status") == "completed"
        and result.get("acceptance_status") == "passed"
        else 2
    )


if __name__ == "__main__":
    raise SystemExit(main())
