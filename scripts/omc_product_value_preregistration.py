#!/usr/bin/env python3
"""Prepare a frozen workload universe for Product Value paired evaluation."""

from __future__ import annotations

import argparse
from copy import deepcopy
import hashlib
import json
from pathlib import Path, PurePosixPath
import re
from typing import Any


SCHEMA_VERSION = "omc-product-value-preregistration/v1"
WORKLOAD_FIELDS = {
    "workload_id",
    "repo_alias",
    "repository_identity_sha256",
    "implementation_type",
    "work_class",
    "source_commit",
    "request_sha256",
    "dod_sha256",
    "verification_sha256",
    "expected_child_count",
    "scope_paths",
}
SELECTION_POLICY = {
    "mode": "prospective_fixed_universe",
    "required_workload_count": 5,
    "minimum_repository_count": 2,
    "minimum_implementation_type_count": 2,
    "allowed_work_classes": ["implementation"],
    "posthoc_exclusions_allowed": False,
    "provider_outputs_available_during_selection": False,
}
PILOT_CONTRACT = {
    "workload_count": 1,
    "claim_eligible": False,
    "purpose": "contract_gap_detection_only",
}
COMPARISON_CONTRACT = {
    "isolated_clones_required": True,
    "same_source_commit": True,
    "same_request_and_dod": True,
    "same_provider_family": True,
    "same_reasoning_profile": True,
    "same_total_token_cap": True,
    "same_total_elapsed_cap": True,
    "provider_call_count_metric_only": True,
}
INTERVENTION_CONTRACT = {
    "measurement_start": "initial_execution_approval_completed",
    "measurement_end": "final_outcome_recorded",
    "included_events": [
        "additional_requirement_question",
        "reapproval_request",
        "retry_or_recovery_request",
        "user_executed_command",
        "scope_or_branch_restatement",
    ],
    "excluded_events": [
        "initial_execution_approval",
        "external_transmission_consent",
        "benchmark_signer_approval",
    ],
    "raw_events_required": True,
}
THRESHOLDS = {
    "success_rate_relation": "gte_baseline",
    "median_total_tokens_relation": "lt_baseline",
    "median_elapsed_relation": "lte_baseline",
    "intervention_ratio_max": 0.5,
    "max_scope_violations": 0,
    "max_budget_violations": 0,
    "max_duplicate_executions": 0,
    "max_additional_critical_or_major_review_findings": 0,
}
MANIFEST_FIELDS = {
    "schema_version",
    "status",
    "batch_id",
    "claim_scope",
    "selection_policy",
    "pilot_contract",
    "comparison_contract",
    "intervention_contract",
    "thresholds",
    "workloads",
    "preregistration_sha256",
}


def canonical_sha256(value: Any) -> str:
    payload = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def _is_sha256(value: Any) -> bool:
    return isinstance(value, str) and re.fullmatch(r"[0-9a-f]{64}", value) is not None


def _valid_scope_paths(value: Any) -> bool:
    if not isinstance(value, list) or not value:
        return False
    for item in value:
        if not isinstance(item, str) or not item.strip():
            return False
        path = PurePosixPath(item)
        if path.is_absolute() or ".." in path.parts:
            return False
    return len(set(value)) == len(value)


def _validate_workloads(workloads: Any) -> list[dict[str, Any]]:
    if not isinstance(workloads, list) or len(workloads) != 5:
        raise ValueError("workload_count_invalid")
    workload_ids: set[str] = set()
    repository_aliases: set[str] = set()
    repository_identities: set[str] = set()
    alias_identities: dict[str, set[str]] = {}
    identity_aliases: dict[str, set[str]] = {}
    implementation_types: set[str] = set()
    source_request_pairs: set[tuple[str, str]] = set()
    validated: list[dict[str, Any]] = []
    for workload in workloads:
        if not isinstance(workload, dict) or set(workload) != WORKLOAD_FIELDS:
            raise ValueError("workload_schema_invalid")
        workload_id = workload.get("workload_id")
        repo_alias = workload.get("repo_alias")
        repository_identity = workload.get("repository_identity_sha256")
        implementation_type = workload.get("implementation_type")
        if not isinstance(workload_id, str) or not workload_id.strip() or workload_id in workload_ids:
            raise ValueError("workload_id_invalid")
        if not isinstance(repo_alias, str) or not repo_alias.strip():
            raise ValueError("repository_alias_invalid")
        if not _is_sha256(repository_identity):
            raise ValueError("repository_identity_invalid")
        if not isinstance(implementation_type, str) or not implementation_type.strip():
            raise ValueError("implementation_type_invalid")
        if workload.get("work_class") != "implementation":
            raise ValueError("workload_work_class_invalid")
        if not isinstance(workload.get("source_commit"), str) or re.fullmatch(
            r"[0-9a-f]{40}", workload["source_commit"]
        ) is None:
            raise ValueError("source_commit_invalid")
        for field in ("request_sha256", "dod_sha256", "verification_sha256"):
            if not _is_sha256(workload.get(field)):
                raise ValueError(f"{field}_invalid")
        child_count = workload.get("expected_child_count")
        if not isinstance(child_count, int) or isinstance(child_count, bool) or not 3 <= child_count <= 5:
            raise ValueError("expected_child_count_invalid")
        if not _valid_scope_paths(workload.get("scope_paths")):
            raise ValueError("scope_paths_invalid")
        source_request_pair = (
            workload["source_commit"],
            workload["request_sha256"],
        )
        if source_request_pair in source_request_pairs:
            raise ValueError("workload_source_request_duplicate")
        workload_ids.add(workload_id)
        repository_aliases.add(repo_alias)
        repository_identities.add(repository_identity)
        alias_identities.setdefault(repo_alias, set()).add(repository_identity)
        identity_aliases.setdefault(repository_identity, set()).add(repo_alias)
        implementation_types.add(implementation_type)
        source_request_pairs.add(source_request_pair)
        validated.append(deepcopy(workload))
    if len(repository_aliases) < 2 or len(repository_identities) < 2:
        raise ValueError("repository_coverage_invalid")
    if any(len(identities) != 1 for identities in alias_identities.values()) or any(
        len(aliases) != 1 for aliases in identity_aliases.values()
    ):
        raise ValueError("repository_identity_mapping_invalid")
    if len(implementation_types) < 2:
        raise ValueError("implementation_type_coverage_invalid")
    return sorted(validated, key=lambda item: item["workload_id"])


def build_preregistration(batch_id: str, workloads: Any) -> dict[str, Any]:
    if not isinstance(batch_id, str) or not batch_id.strip():
        raise ValueError("batch_id_invalid")
    manifest = {
        "schema_version": SCHEMA_VERSION,
        "status": "prepared",
        "batch_id": batch_id.strip(),
        "claim_scope": "product_value_paired_v1",
        "selection_policy": deepcopy(SELECTION_POLICY),
        "pilot_contract": deepcopy(PILOT_CONTRACT),
        "comparison_contract": deepcopy(COMPARISON_CONTRACT),
        "intervention_contract": deepcopy(INTERVENTION_CONTRACT),
        "thresholds": deepcopy(THRESHOLDS),
        "workloads": _validate_workloads(workloads),
    }
    manifest["preregistration_sha256"] = canonical_sha256(manifest)
    return validate_preregistration(manifest)


def validate_preregistration(payload: Any) -> dict[str, Any]:
    if not isinstance(payload, dict) or set(payload) != MANIFEST_FIELDS:
        raise ValueError("preregistration_schema_invalid")
    expected_hash = payload.get("preregistration_sha256")
    unsigned = deepcopy(payload)
    unsigned.pop("preregistration_sha256", None)
    if not _is_sha256(expected_hash) or canonical_sha256(unsigned) != expected_hash:
        raise ValueError("preregistration_hash_mismatch")
    if payload.get("schema_version") != SCHEMA_VERSION or payload.get("status") != "prepared":
        raise ValueError("preregistration_state_invalid")
    if not isinstance(payload.get("batch_id"), str) or not payload["batch_id"].strip():
        raise ValueError("batch_id_invalid")
    if payload.get("claim_scope") != "product_value_paired_v1":
        raise ValueError("claim_scope_invalid")
    fixed_contracts = {
        "selection_policy": SELECTION_POLICY,
        "pilot_contract": PILOT_CONTRACT,
        "comparison_contract": COMPARISON_CONTRACT,
        "intervention_contract": INTERVENTION_CONTRACT,
        "thresholds": THRESHOLDS,
    }
    for field, expected in fixed_contracts.items():
        if payload.get(field) != expected:
            raise ValueError(f"{field}_invalid")
    validated_workloads = _validate_workloads(payload.get("workloads"))
    if payload["workloads"] != validated_workloads:
        raise ValueError("workload_order_invalid")
    validated = deepcopy(payload)
    return validated


def _load_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Prepare Product Value workload preregistration.")
    sub = parser.add_subparsers(dest="command", required=True)
    prepare = sub.add_parser("prepare")
    prepare.add_argument("--batch-id", required=True)
    prepare.add_argument("--workloads", type=Path, required=True)
    prepare.add_argument("--out", type=Path, required=True)
    validate = sub.add_parser("validate")
    validate.add_argument("--manifest", type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        if args.command == "prepare":
            manifest = build_preregistration(args.batch_id, _load_json(args.workloads))
            args.out.parent.mkdir(parents=True, exist_ok=True)
            args.out.write_text(
                json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
                encoding="utf-8",
            )
        else:
            manifest = validate_preregistration(_load_json(args.manifest))
        print(json.dumps({
            "claim_eligible": False,
            "preregistration_sha256": manifest["preregistration_sha256"],
            "registration_required": True,
            "status": manifest["status"],
        }, sort_keys=True))
        return 0
    except (OSError, json.JSONDecodeError, ValueError) as exc:
        print(json.dumps({"status": "blocked", "reason_code": str(exc)}, sort_keys=True))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
