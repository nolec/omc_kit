#!/usr/bin/env python3
"""Prepare approval-bound Product Value decompositions and v2 DAG grants."""

from __future__ import annotations

import argparse
from copy import deepcopy
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import tempfile
from typing import Any, Mapping

from omc_executor_shadow import (
    build_n_child_dag_proposal,
    build_n_child_dag_v2_grant,
    build_single_child_execution_grant,
)
from omc_scope import canonicalize_child_scopes
import omc_product_value_v4_freeze as freeze


SCHEMA_VERSION = "omc-product-value-v4-decomposition-approval/v1"


def _target_binding(workload: dict[str, Any]) -> dict[str, str]:
    return {
        "mode": "repository_commit/v1",
        "repository_identity_sha256": workload["repository_identity_sha256"],
        "source_commit": workload["source_commit"],
    }


def _has_valid_target_binding(decomposition: Any) -> bool:
    if not isinstance(decomposition, dict):
        return False
    repository_identity = decomposition.get("repository_identity_sha256")
    source_commit = decomposition.get("source_commit")
    if (
        not freeze.preregistration._is_sha256(repository_identity)
        or not isinstance(source_commit, str)
        or len(source_commit) != 40
        or any(character not in "0123456789abcdef" for character in source_commit)
    ):
        return False
    expected = {
        "mode": "repository_commit/v1",
        "repository_identity_sha256": repository_identity,
        "source_commit": source_commit,
    }
    return (
        decomposition.get("target_binding") == expected
        and decomposition.get("target_identity_sha256")
        == freeze.canonical_sha256(expected)
    )


def _parse_future_timestamp(value: Any) -> datetime:
    if not isinstance(value, str):
        raise ValueError("decomposition_approval_expiry_invalid")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as error:
        raise ValueError("decomposition_approval_expiry_invalid") from error
    if (
        parsed.tzinfo is None
        or parsed.utcoffset() is None
        or parsed <= datetime.now(timezone.utc)
    ):
        raise ValueError("decomposition_approval_expiry_invalid")
    return parsed


def _source_root(
    source_roots: Mapping[str, dict[str, str]], workload: dict[str, Any]
) -> Path:
    try:
        return freeze._source_root(source_roots, workload)
    except ValueError as error:
        raise ValueError("decomposition_source_root_invalid") from error


def _validated_limits(value: Any) -> dict[str, int]:
    fields = {
        "max_total_tokens",
        "max_total_elapsed_sec",
        "max_output_chars",
    }
    if (
        not isinstance(value, dict)
        or set(value) != fields
        or any(
            not isinstance(value.get(field), int)
            or isinstance(value[field], bool)
            or value[field] <= 0
            for field in fields
        )
    ):
        raise ValueError("decomposition_limits_invalid")
    return deepcopy(value)


def _corpus_approval(value: Any, workload_count: int) -> dict[str, Any]:
    required = {
        "schema_version",
        "decision",
        "batch_id",
        "public_payload_sha256",
        "workload_count",
    }
    if (
        not isinstance(value, dict)
        or not required.issubset(value)
        or value.get("schema_version")
        != "omc-product-value-corpus-approval/v1"
        or value.get("decision") != "approved"
        or not isinstance(value.get("batch_id"), str)
        or not value["batch_id"].strip()
        or not freeze.preregistration._is_sha256(
            value.get("public_payload_sha256")
        )
        or value.get("workload_count") != workload_count
    ):
        raise ValueError("decomposition_corpus_approval_invalid")
    return deepcopy(value)


def _decomposition(
    *,
    workload: dict[str, Any],
    packet: dict[str, Any],
    spec: Any,
    source_root: Path,
    limits: dict[str, int],
) -> dict[str, Any]:
    if not isinstance(spec, dict) or set(spec) != {
        "dag_id",
        "max_parallelism",
        "children",
    }:
        raise ValueError("decomposition_spec_invalid")
    children = spec.get("children")
    expected_count = workload.get("expected_child_count")
    if (
        not isinstance(children, list)
        or len(children) != expected_count
        or not 3 <= len(children) <= 5
        or not all(isinstance(child, dict) for child in children)
    ):
        raise ValueError("decomposition_spec_invalid")
    child_fields = {
        "child_id",
        "depends_on",
        "scope_paths",
        "executor",
        "prompt",
        "max_total_tokens",
        "max_total_elapsed_sec",
        "max_output_chars",
    }
    if any(set(child) != child_fields for child in children):
        raise ValueError("decomposition_spec_invalid")
    graph_input = [
        {
            "child_id": child["child_id"],
            "depends_on": deepcopy(child["depends_on"]),
            "scope_paths": deepcopy(child["scope_paths"]),
        }
        for child in children
    ]
    target_binding = _target_binding(workload)
    scope_result = canonicalize_child_scopes(
        source_root,
        graph_input,
        target_binding=target_binding,
    )
    if scope_result.get("status") != "ready":
        raise ValueError("decomposition_proposal_invalid")
    normalized = scope_result["children"]
    child_ids = [child["child_id"] for child in normalized]
    if (
        len(set(child_ids)) != len(child_ids)
        or any(
            not isinstance(child.get("executor"), str)
            or not child["executor"].strip()
            or not isinstance(child.get("prompt"), str)
            or not child["prompt"].strip()
            or any(
                not isinstance(child.get(field), int)
                or isinstance(child[field], bool)
                or child[field] <= 0
                for field in (
                    "max_total_tokens",
                    "max_total_elapsed_sec",
                    "max_output_chars",
                )
            )
            or child["max_total_elapsed_sec"] > 120
            for child in children
        )
        or not isinstance(spec.get("dag_id"), str)
        or not spec["dag_id"].strip()
        or not isinstance(spec.get("max_parallelism"), int)
        or isinstance(spec["max_parallelism"], bool)
        or not 1 <= spec["max_parallelism"] <= len(children)
    ):
        raise ValueError("decomposition_spec_invalid")
    child_by_id = {child["child_id"]: child for child in children}
    normalized_by_id = {child["child_id"]: child for child in normalized}
    if any(
        not isinstance(child["depends_on"], list)
        or any(dependency not in child_ids for dependency in child["depends_on"])
        for child in children
    ):
        raise ValueError("decomposition_proposal_invalid")
    visiting: set[str] = set()
    elapsed_by_child: dict[str, int] = {}

    def critical_elapsed(child_id: str) -> int:
        if child_id in elapsed_by_child:
            return elapsed_by_child[child_id]
        if child_id in visiting:
            raise ValueError("decomposition_proposal_invalid")
        visiting.add(child_id)
        child = child_by_id[child_id]
        dependency_elapsed = max(
            (critical_elapsed(dependency) for dependency in child["depends_on"]),
            default=0,
        )
        visiting.remove(child_id)
        elapsed_by_child[child_id] = (
            dependency_elapsed + child["max_total_elapsed_sec"]
        )
        return elapsed_by_child[child_id]

    if max(critical_elapsed(child_id) for child_id in child_ids) > limits[
        "max_total_elapsed_sec"
    ]:
        raise ValueError("decomposition_budget_invalid")
    total_tokens = sum(child["max_total_tokens"] for child in children)
    total_output = sum(child["max_output_chars"] for child in children)
    if total_tokens > limits["max_total_tokens"] or total_output > limits[
        "max_output_chars"
    ]:
        raise ValueError("decomposition_budget_invalid")
    result_children = []
    for child_id in child_ids:
        source = child_by_id[child_id]
        canonical = normalized_by_id[child_id]
        result_children.append(
            {
                **canonical,
                "executor": source["executor"],
                "prompt": source["prompt"],
                "max_total_tokens": source["max_total_tokens"],
                "max_total_elapsed_sec": source["max_total_elapsed_sec"],
                "max_output_chars": source["max_output_chars"],
            }
        )
    result = {
        "status": "ready_for_approval",
        "execution_allowed": False,
        "workload_id": workload["workload_id"],
        "repo_alias": workload["repo_alias"],
        "repository_identity_sha256": workload["repository_identity_sha256"],
        "source_commit": workload["source_commit"],
        "dag_id": spec["dag_id"],
        "request": packet.get("request"),
        "dod": packet.get("dod"),
        "verification": deepcopy(packet.get("verification")),
        "scope_policy_version": scope_result["scope_policy_version"],
        "target_identity_sha256": scope_result["target_identity_sha256"],
        "target_binding": target_binding,
        "max_parallelism": spec["max_parallelism"],
        "children": result_children,
    }
    result["decomposition_sha256"] = freeze.canonical_sha256(result)
    return result


def prepare_decomposition_approval(
    *,
    corpus_approval: dict[str, Any],
    workloads: list[dict[str, Any]],
    packets: Mapping[str, dict[str, Any]],
    source_roots: Mapping[str, dict[str, str]],
    decomposition_specs: Mapping[str, dict[str, Any]],
    limits: dict[str, int],
    approval_expires_at: str,
) -> dict[str, Any]:
    """Return an immutable, non-executable packet for explicit approval."""
    if not isinstance(workloads, list) or len(workloads) != 6:
        raise ValueError("decomposition_input_coverage_invalid")
    workload_ids = {
        workload.get("workload_id")
        for workload in workloads
        if isinstance(workload, dict)
    }
    if (
        len(workload_ids) != 6
        or not isinstance(packets, Mapping)
        or set(packets) != workload_ids
        or not isinstance(decomposition_specs, Mapping)
        or set(decomposition_specs) != workload_ids
    ):
        raise ValueError("decomposition_input_coverage_invalid")
    _parse_future_timestamp(approval_expires_at)
    bounded_limits = _validated_limits(limits)
    approved_corpus = _corpus_approval(corpus_approval, len(workloads))
    decompositions = {}
    dag_ids: set[str] = set()
    child_ids: set[str] = set()
    for workload in sorted(workloads, key=lambda value: value["workload_id"]):
        workload_id = workload["workload_id"]
        decompositions[workload_id] = _decomposition(
            workload=workload,
            packet=packets[workload_id],
            spec=decomposition_specs[workload_id],
            source_root=_source_root(source_roots, workload),
            limits=bounded_limits,
        )
        decomposition = decompositions[workload_id]
        current_child_ids = {
            child["child_id"] for child in decomposition["children"]
        }
        if (
            decomposition["dag_id"] in dag_ids
            or current_child_ids & child_ids
        ):
            raise ValueError("decomposition_identifier_collision")
        dag_ids.add(decomposition["dag_id"])
        child_ids.update(current_child_ids)
    packet = {
        "schema_version": SCHEMA_VERSION,
        "status": "approval_pending",
        "execution_allowed": False,
        "batch_id": approved_corpus["batch_id"],
        "corpus_approval_sha256": freeze.canonical_sha256(approved_corpus),
        "public_payload_sha256": approved_corpus["public_payload_sha256"],
        "workloads_sha256": freeze.canonical_sha256(workloads),
        "limits": bounded_limits,
        "approval_expires_at": approval_expires_at,
        "decompositions": decompositions,
    }
    packet["approval_packet_sha256"] = freeze.canonical_sha256(packet)
    return packet


def validate_approval_receipt(
    approval_packet: Any, approval_receipt: Any
) -> dict[str, Any]:
    if not isinstance(approval_packet, dict):
        raise ValueError("decomposition_approval_packet_invalid")
    unsigned = deepcopy(approval_packet)
    packet_hash = unsigned.pop("approval_packet_sha256", None)
    decompositions = approval_packet.get("decompositions")
    if (
        approval_packet.get("schema_version") != SCHEMA_VERSION
        or approval_packet.get("status") != "approval_pending"
        or approval_packet.get("execution_allowed") is not False
        or not isinstance(decompositions, dict)
        or len(decompositions) != 6
        or any(
            not _has_valid_target_binding(decomposition)
            for decomposition in decompositions.values()
        )
        or packet_hash != freeze.canonical_sha256(unsigned)
    ):
        raise ValueError("decomposition_approval_packet_invalid")
    receipt_fields = {
        "schema_version",
        "status",
        "operator_confirmed",
        "approval_id",
        "approval_packet_sha256",
        "approval_expires_at",
    }
    if (
        not isinstance(approval_receipt, dict)
        or set(approval_receipt) != receipt_fields
        or approval_receipt.get("schema_version") != SCHEMA_VERSION
        or approval_receipt.get("status") != "approved"
        or approval_receipt.get("operator_confirmed") is not True
        or not isinstance(approval_receipt.get("approval_id"), str)
        or not approval_receipt["approval_id"].strip()
        or approval_receipt.get("approval_packet_sha256") != packet_hash
        or approval_receipt.get("approval_expires_at")
        != approval_packet.get("approval_expires_at")
    ):
        raise ValueError("decomposition_approval_receipt_invalid")
    _parse_future_timestamp(approval_receipt["approval_expires_at"])
    return deepcopy(approval_receipt)


def _child_grant(
    *,
    child: dict[str, Any],
    dag_id: str,
    approval_id: str,
    expires_at: str,
) -> dict[str, Any]:
    child_id = child["child_id"]
    fingerprint = freeze.canonical_sha256(child)
    idempotency_key = f"{dag_id}:{child_id}:{fingerprint[:16]}"
    child_approval_id = f"{approval_id}:{child_id}"
    request = {
        "parent_id": dag_id,
        "child_id": child_id,
        "executor": child["executor"],
        "scope_hash": child["scope_hash"],
        "approval": {
            "approval_id": child_approval_id,
            "session_id": approval_id,
            "child_id": child_id,
            "scope_hash": child["scope_hash"],
            "expires_at": expires_at,
            "plan_fingerprint": fingerprint,
            "idempotency_key": idempotency_key,
            "operator_confirmed": True,
            "approval_status": "approved",
        },
        "policy": {
            "allowed_executors": [child["executor"]],
            "timeout_sec": child["max_total_elapsed_sec"],
            "budget_usd": 0,
            "retry_limit": 0,
        },
        "pilot_mode": "single_child",
        "child_count": 1,
        "child_status": "ready",
        "dependency_statuses": {},
        "depends_on": [],
        "sensitive_paths": [],
        "plan_fingerprint": fingerprint,
        "idempotency_key": idempotency_key,
        "seen_idempotency_keys": [],
        "budget": {
            "max_attempts": 1,
            "max_total_elapsed_sec": child["max_total_elapsed_sec"],
            "max_output_chars": child["max_output_chars"],
        },
        "execution_requested": True,
        "execution_mode": "single_child_opt_in",
    }
    grant = build_single_child_execution_grant(request)
    if grant.get("status") != "ready":
        raise ValueError("decomposition_child_grant_invalid")
    grant["max_total_tokens"] = child["max_total_tokens"]
    return grant


def issue_approved_executions(
    *,
    approval_packet: dict[str, Any],
    approval_receipt: dict[str, Any],
    source_roots: Mapping[str, dict[str, str]],
) -> dict[str, dict[str, Any]]:
    """Issue executable v2 grants only after exact packet approval."""
    receipt = validate_approval_receipt(approval_packet, approval_receipt)
    limits = _validated_limits(approval_packet.get("limits"))
    executions = {}
    for workload_id, decomposition in sorted(
        approval_packet.get("decompositions", {}).items()
    ):
        repo_alias = decomposition.get("repo_alias")
        source_root = _source_root(
            source_roots,
            {
                "repo_alias": repo_alias,
                "repository_identity_sha256": decomposition.get(
                    "repository_identity_sha256"
                ),
                "source_commit": decomposition.get("source_commit"),
            },
        )
        scope_result = canonicalize_child_scopes(
            source_root,
            [
                {
                    "child_id": child["child_id"],
                    "depends_on": deepcopy(child["depends_on"]),
                    "scope_paths": deepcopy(child["scope_paths"]),
                }
                for child in decomposition["children"]
            ],
            target_binding=decomposition.get("target_binding"),
        )
        if (
            scope_result.get("status") != "ready"
            or scope_result.get("target_identity_sha256")
            != decomposition.get("target_identity_sha256")
        ):
            raise ValueError("decomposition_source_root_invalid")
        grants = [
            _child_grant(
                child=child,
                dag_id=decomposition["dag_id"],
                approval_id=receipt["approval_id"],
                expires_at=receipt["approval_expires_at"],
            )
            for child in decomposition["children"]
        ]
        children = scope_result["children"]
        prompts = {
            child["child_id"]: child["prompt"]
            for child in decomposition["children"]
        }
        request = {
            "schema_version": "omc-n-child-dag/v2",
            "dag_id": decomposition["dag_id"],
            "execution_mode": "n_child_dag_opt_in",
            "execution_requested": True,
            "children": children,
            "child_grants": grants,
            "child_prompts": prompts,
            "aggregate_budget": {
                "max_external_calls": len(children),
                "max_parallelism": decomposition["max_parallelism"],
                **limits,
            },
            "target_binding": deepcopy(decomposition["target_binding"]),
        }
        proposal = build_n_child_dag_proposal(source_root, request)
        if proposal.get("status") != "ready":
            raise ValueError("decomposition_proposal_invalid")
        grant = build_n_child_dag_v2_grant(
            source_root,
            proposal,
            {
                "approval_id": f"{receipt['approval_id']}:{workload_id}:dag",
                "dag_id": decomposition["dag_id"],
                "operator_confirmed": True,
                "expires_at": receipt["approval_expires_at"],
                "proposal_sha256": proposal["proposal_sha256"],
            },
        )
        if grant.get("status") != "ready":
            raise ValueError("decomposition_dag_grant_invalid")
        executions[workload_id] = {"grant": grant, "prompts": prompts}
    if len(executions) != 6:
        raise ValueError("decomposition_input_coverage_invalid")
    return executions


def _load_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        raise ValueError("decomposition_input_invalid") from error


def _write_json(path: Path, value: Any) -> None:
    path = path.expanduser().resolve(strict=False)
    if path.exists():
        raise ValueError("decomposition_output_exists")
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, raw_temp = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    temp = Path(raw_temp)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            json.dump(value, stream, ensure_ascii=False, indent=2, sort_keys=True)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temp, path)
    except Exception:
        temp.unlink(missing_ok=True)
        raise


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    prepare = sub.add_parser("prepare")
    prepare.add_argument("--corpus-root", required=True, type=Path)
    prepare.add_argument("--decomposition-specs", required=True, type=Path)
    prepare.add_argument("--limits", required=True, type=Path)
    prepare.add_argument("--approval-expires-at", required=True)
    prepare.add_argument("--out", required=True, type=Path)
    issue = sub.add_parser("issue")
    issue.add_argument("--approval-packet", required=True, type=Path)
    issue.add_argument("--approval-receipt", required=True, type=Path)
    issue.add_argument("--source-roots", required=True, type=Path)
    issue.add_argument("--out", required=True, type=Path)
    args = parser.parse_args(argv)
    if args.command == "prepare":
        workloads, packets, source_roots = freeze._load_corpus(args.corpus_root)
        result = prepare_decomposition_approval(
            corpus_approval=_load_json(args.corpus_root / "corpus-approval.json"),
            workloads=workloads,
            packets=packets,
            source_roots=source_roots,
            decomposition_specs=_load_json(args.decomposition_specs),
            limits=_load_json(args.limits),
            approval_expires_at=args.approval_expires_at,
        )
    else:
        result = issue_approved_executions(
            approval_packet=_load_json(args.approval_packet),
            approval_receipt=_load_json(args.approval_receipt),
            source_roots=_load_json(args.source_roots),
        )
    _write_json(args.out, result)
    print(json.dumps({"status": "written", "path": str(args.out)}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
