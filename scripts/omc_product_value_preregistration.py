#!/usr/bin/env python3
"""Prepare a frozen workload universe for Product Value paired evaluation."""

from __future__ import annotations

import argparse
from copy import deepcopy
from datetime import datetime
import hashlib
import json
from pathlib import Path, PurePosixPath
import re
from typing import Any

import omc_preregistration_registry as registry
import omc_rfc3161_timestamp as rfc3161


SCHEMA_VERSION_V1 = "omc-product-value-preregistration/v1"
SCHEMA_VERSION_V2 = "omc-product-value-preregistration/v2"
SCHEMA_VERSION = SCHEMA_VERSION_V1
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
WORKLOAD_V2_FIELDS = WORKLOAD_FIELDS | {"evaluation_role"}
SELECTION_POLICY = {
    "mode": "prospective_fixed_universe",
    "required_workload_count": 5,
    "minimum_repository_count": 2,
    "minimum_implementation_type_count": 2,
    "allowed_work_classes": ["implementation"],
    "posthoc_exclusions_allowed": False,
    "provider_outputs_available_during_selection": False,
}
SELECTION_POLICY_V2 = {
    **SELECTION_POLICY,
    "required_workload_count": 6,
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
MANIFEST_V2_FIELDS = MANIFEST_FIELDS | {
    "observation_window",
    "registration_authority",
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


def _parse_timestamp(value: Any) -> datetime:
    if not isinstance(value, str):
        raise ValueError("observation_window_invalid")
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError as error:
        raise ValueError("observation_window_invalid") from error
    if parsed.tzinfo is None:
        raise ValueError("observation_window_invalid")
    return parsed


def _validate_observation_window(value: Any) -> dict[str, str]:
    if not isinstance(value, dict) or set(value) != {
        "observed_from",
        "observed_through",
    }:
        raise ValueError("observation_window_invalid")
    observed_from = _parse_timestamp(value.get("observed_from"))
    observed_through = _parse_timestamp(value.get("observed_through"))
    if observed_from >= observed_through:
        raise ValueError("observation_window_invalid")
    return {
        "observed_from": value["observed_from"],
        "observed_through": value["observed_through"],
    }


def _validate_registration_authority(value: Any) -> dict[str, Any]:
    try:
        rfc3161.validate_trust_identity(value)
    except (TypeError, ValueError) as error:
        raise ValueError("registration_authority_invalid") from error
    return deepcopy(value)


def _validate_workloads(
    workloads: Any,
    *,
    schema_version: str = SCHEMA_VERSION_V1,
) -> list[dict[str, Any]]:
    is_v2 = schema_version == SCHEMA_VERSION_V2
    required_count = 6 if is_v2 else 5
    expected_fields = WORKLOAD_V2_FIELDS if is_v2 else WORKLOAD_FIELDS
    if not isinstance(workloads, list) or len(workloads) != required_count:
        raise ValueError("workload_count_invalid")
    workload_ids: set[str] = set()
    repository_aliases: set[str] = set()
    repository_identities: set[str] = set()
    alias_identities: dict[str, set[str]] = {}
    identity_aliases: dict[str, set[str]] = {}
    implementation_types: set[str] = set()
    evaluation_roles: list[str] = []
    source_request_pairs: set[tuple[str, str]] = set()
    validated: list[dict[str, Any]] = []
    for workload in workloads:
        if not isinstance(workload, dict) or set(workload) != expected_fields:
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
        if is_v2:
            evaluation_role = workload.get("evaluation_role")
            if evaluation_role not in {"pilot", "confirmatory"}:
                raise ValueError("evaluation_role_invalid")
            evaluation_roles.append(evaluation_role)
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
    if is_v2 and (
        evaluation_roles.count("pilot") != 1
        or evaluation_roles.count("confirmatory") != 5
    ):
        raise ValueError("evaluation_role_coverage_invalid")
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


def build_preregistration_v2(
    batch_id: str,
    workloads: Any,
    *,
    observed_from: str,
    observed_through: str,
    registration_authority: dict[str, Any],
) -> dict[str, Any]:
    if not isinstance(batch_id, str) or not batch_id.strip():
        raise ValueError("batch_id_invalid")
    manifest = {
        "schema_version": SCHEMA_VERSION_V2,
        "status": "frozen",
        "batch_id": batch_id.strip(),
        "claim_scope": "product_value_paired_v2",
        "selection_policy": deepcopy(SELECTION_POLICY_V2),
        "pilot_contract": deepcopy(PILOT_CONTRACT),
        "comparison_contract": deepcopy(COMPARISON_CONTRACT),
        "intervention_contract": deepcopy(INTERVENTION_CONTRACT),
        "thresholds": deepcopy(THRESHOLDS),
        "workloads": _validate_workloads(
            workloads,
            schema_version=SCHEMA_VERSION_V2,
        ),
        "observation_window": _validate_observation_window({
            "observed_from": observed_from,
            "observed_through": observed_through,
        }),
        "registration_authority": _validate_registration_authority(
            registration_authority
        ),
    }
    manifest["preregistration_sha256"] = canonical_sha256(manifest)
    return validate_preregistration(manifest)


def validate_preregistration(payload: Any) -> dict[str, Any]:
    if not isinstance(payload, dict):
        raise ValueError("preregistration_schema_invalid")
    schema_version = payload.get("schema_version")
    fields = (
        MANIFEST_FIELDS
        if schema_version == SCHEMA_VERSION_V1
        else MANIFEST_V2_FIELDS
        if schema_version == SCHEMA_VERSION_V2
        else None
    )
    if fields is None or set(payload) != fields:
        raise ValueError("preregistration_schema_invalid")
    expected_hash = payload.get("preregistration_sha256")
    unsigned = deepcopy(payload)
    unsigned.pop("preregistration_sha256", None)
    if not _is_sha256(expected_hash) or canonical_sha256(unsigned) != expected_hash:
        raise ValueError("preregistration_hash_mismatch")
    expected_status = (
        "prepared" if schema_version == SCHEMA_VERSION_V1 else "frozen"
    )
    if payload.get("status") != expected_status:
        raise ValueError("preregistration_state_invalid")
    if not isinstance(payload.get("batch_id"), str) or not payload["batch_id"].strip():
        raise ValueError("batch_id_invalid")
    expected_claim_scope = (
        "product_value_paired_v1"
        if schema_version == SCHEMA_VERSION_V1
        else "product_value_paired_v2"
    )
    if payload.get("claim_scope") != expected_claim_scope:
        raise ValueError("claim_scope_invalid")
    fixed_contracts = {
        "selection_policy": (
            SELECTION_POLICY
            if schema_version == SCHEMA_VERSION_V1
            else SELECTION_POLICY_V2
        ),
        "pilot_contract": PILOT_CONTRACT,
        "comparison_contract": COMPARISON_CONTRACT,
        "intervention_contract": INTERVENTION_CONTRACT,
        "thresholds": THRESHOLDS,
    }
    for field, expected in fixed_contracts.items():
        if payload.get(field) != expected:
            raise ValueError(f"{field}_invalid")
    validated_workloads = _validate_workloads(
        payload.get("workloads"),
        schema_version=schema_version,
    )
    if payload["workloads"] != validated_workloads:
        raise ValueError("workload_order_invalid")
    if schema_version == SCHEMA_VERSION_V2:
        _validate_observation_window(payload.get("observation_window"))
        _validate_registration_authority(payload.get("registration_authority"))
    validated = deepcopy(payload)
    return validated


def _validated_v2(payload: Any) -> dict[str, Any]:
    validated = validate_preregistration(payload)
    if validated["schema_version"] != SCHEMA_VERSION_V2:
        raise ValueError("registration_requires_v2")
    return validated


def prepare_registry_record(payload: Any) -> dict[str, Any]:
    validated = _validated_v2(payload)
    return registry.prepare_registry_record(
        batch_id=validated["batch_id"],
        preregistration_sha256=validated["preregistration_sha256"],
    )


def prepare_registration_receipt(
    payload: Any,
    *,
    registry_commit: str,
    registry_path: str,
    registration_evidence: dict[str, Any],
    trusted_root: dict[str, Any],
    approved_trusted_root_sha256: str,
) -> dict[str, Any]:
    validated = _validated_v2(payload)
    return registry.prepare_sigstore_registration_receipt(
        batch_id=validated["batch_id"],
        preregistration_sha256=validated["preregistration_sha256"],
        registry_commit=registry_commit,
        registry_path=registry_path,
        registration_authority=validated["registration_authority"],
        observation_starts_at=validated["observation_window"]["observed_from"],
        registration_evidence=registration_evidence,
        trusted_root=trusted_root,
        approved_trusted_root_sha256=approved_trusted_root_sha256,
    )


def validate_registered_preregistration(
    payload: Any,
    *,
    repository_root: str | Path,
    registry_commit: str,
    registry_path: str,
    required_ancestor_commit: str,
    registration_receipt: Any,
    expected_registration_receipt_sha256: str,
    trusted_root: dict[str, Any],
    approved_trusted_root_sha256: str,
) -> dict[str, Any]:
    validated = _validated_v2(payload)
    registry.validate_registry_anchor(
        prepare_registry_record(validated),
        repository_root=repository_root,
        registry_commit=registry_commit,
        registry_path=registry_path,
        required_ancestor_commit=required_ancestor_commit,
    )
    if not isinstance(registration_receipt, dict):
        raise ValueError("registration_receipt_invalid")
    if (
        registration_receipt.get("registry_commit") != registry_commit
        or registration_receipt.get("registry_path") != registry_path
    ):
        raise ValueError("registration_anchor_mismatch")
    registry.validate_sigstore_registration_receipt(
        registration_receipt,
        batch_id=validated["batch_id"],
        preregistration_sha256=validated["preregistration_sha256"],
        registration_authority=validated["registration_authority"],
        observation_starts_at=validated["observation_window"]["observed_from"],
        expected_receipt_sha256=expected_registration_receipt_sha256,
        trusted_root=trusted_root,
        approved_trusted_root_sha256=approved_trusted_root_sha256,
    )
    return {
        "claim_eligible": True,
        "preregistration_sha256": validated["preregistration_sha256"],
        "registration_receipt_sha256": registration_receipt["receipt_sha256"],
        "status": "registered",
    }


def _load_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def _write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Prepare Product Value workload preregistration.")
    sub = parser.add_subparsers(dest="command", required=True)
    prepare = sub.add_parser("prepare")
    prepare.add_argument("--batch-id", required=True)
    prepare.add_argument("--workloads", type=Path, required=True)
    prepare.add_argument("--out", type=Path, required=True)
    prepare_v2 = sub.add_parser("prepare-v2")
    prepare_v2.add_argument("--batch-id", required=True)
    prepare_v2.add_argument("--workloads", type=Path, required=True)
    prepare_v2.add_argument("--observed-from", required=True)
    prepare_v2.add_argument("--observed-through", required=True)
    prepare_v2.add_argument("--registration-authority", type=Path, required=True)
    prepare_v2.add_argument("--out", type=Path, required=True)
    validate = sub.add_parser("validate")
    validate.add_argument("--manifest", type=Path, required=True)
    registry_record = sub.add_parser("registry-record")
    registry_record.add_argument("--manifest", type=Path, required=True)
    registry_record.add_argument("--out", type=Path, required=True)
    prepare_receipt = sub.add_parser("prepare-receipt")
    prepare_receipt.add_argument("--manifest", type=Path, required=True)
    prepare_receipt.add_argument("--registry-commit", required=True)
    prepare_receipt.add_argument("--registry-path", required=True)
    prepare_receipt.add_argument("--registration-evidence", type=Path, required=True)
    prepare_receipt.add_argument("--trusted-root", type=Path, required=True)
    prepare_receipt.add_argument("--approved-trusted-root-sha256", required=True)
    prepare_receipt.add_argument("--out", type=Path, required=True)
    validate_registration = sub.add_parser("validate-registration")
    validate_registration.add_argument("--manifest", type=Path, required=True)
    validate_registration.add_argument("--repository-root", type=Path, required=True)
    validate_registration.add_argument("--registry-commit", required=True)
    validate_registration.add_argument("--registry-path", required=True)
    validate_registration.add_argument("--required-ancestor-commit", required=True)
    validate_registration.add_argument(
        "--registration-receipt", type=Path, required=True
    )
    validate_registration.add_argument(
        "--expected-registration-receipt-sha256", required=True
    )
    validate_registration.add_argument("--trusted-root", type=Path, required=True)
    validate_registration.add_argument(
        "--approved-trusted-root-sha256", required=True
    )
    args = parser.parse_args(argv)
    try:
        if args.command == "prepare":
            manifest = build_preregistration(args.batch_id, _load_json(args.workloads))
            _write_json(args.out, manifest)
            result = {
                "claim_eligible": False,
                "preregistration_sha256": manifest["preregistration_sha256"],
                "registration_required": True,
                "status": manifest["status"],
            }
        elif args.command == "prepare-v2":
            manifest = build_preregistration_v2(
                args.batch_id,
                _load_json(args.workloads),
                observed_from=args.observed_from,
                observed_through=args.observed_through,
                registration_authority=_load_json(args.registration_authority),
            )
            _write_json(args.out, manifest)
            result = {
                "claim_eligible": False,
                "preregistration_sha256": manifest["preregistration_sha256"],
                "registration_required": True,
                "status": manifest["status"],
            }
        elif args.command == "validate":
            manifest = validate_preregistration(_load_json(args.manifest))
            result = {
                "claim_eligible": False,
                "preregistration_sha256": manifest["preregistration_sha256"],
                "registration_required": True,
                "status": manifest["status"],
            }
        elif args.command == "registry-record":
            manifest = validate_preregistration(_load_json(args.manifest))
            record = prepare_registry_record(manifest)
            _write_json(args.out, record)
            result = {
                "claim_eligible": False,
                "preregistration_sha256": manifest["preregistration_sha256"],
                "registration_required": True,
                "status": "registry_record_prepared",
            }
        elif args.command == "prepare-receipt":
            manifest = validate_preregistration(_load_json(args.manifest))
            receipt = prepare_registration_receipt(
                manifest,
                registry_commit=args.registry_commit,
                registry_path=args.registry_path,
                registration_evidence=_load_json(args.registration_evidence),
                trusted_root=_load_json(args.trusted_root),
                approved_trusted_root_sha256=(
                    args.approved_trusted_root_sha256
                ),
            )
            _write_json(args.out, receipt)
            result = {
                "claim_eligible": False,
                "preregistration_sha256": manifest["preregistration_sha256"],
                "registration_receipt_sha256": receipt["receipt_sha256"],
                "registration_required": True,
                "status": "receipt_prepared",
            }
        else:
            result = validate_registered_preregistration(
                _load_json(args.manifest),
                repository_root=args.repository_root.resolve(),
                registry_commit=args.registry_commit,
                registry_path=args.registry_path,
                required_ancestor_commit=args.required_ancestor_commit,
                registration_receipt=_load_json(args.registration_receipt),
                expected_registration_receipt_sha256=(
                    args.expected_registration_receipt_sha256
                ),
                trusted_root=_load_json(args.trusted_root),
                approved_trusted_root_sha256=(
                    args.approved_trusted_root_sha256
                ),
            )
        print(json.dumps(result, sort_keys=True))
        return 0
    except (OSError, json.JSONDecodeError, ValueError) as exc:
        print(json.dumps({"status": "blocked", "reason_code": str(exc)}, sort_keys=True))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
