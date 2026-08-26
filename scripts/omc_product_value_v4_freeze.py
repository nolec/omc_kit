#!/usr/bin/env python3
"""Build a deterministic, approval-pending Product Value v4 freeze candidate."""

from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import shutil
import stat
import subprocess
import tempfile
from typing import Any, Callable, Mapping

import omc_product_value_acceptance as acceptance
import omc_product_value_preregistration as preregistration
from omc_n_child_scheduler import _validate_grant


SCHEMA_VERSION = "omc-product-value-v4-freeze/v1"
BUNDLE_PATH_FIELDS = (
    "acceptance_runner",
    "arm_adapter",
    "scheduler",
    "executor_shadow",
    "provider_adapter",
)


def canonical_sha256(value: Any) -> str:
    return hashlib.sha256(
        json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()


def file_sha256(path: str | Path) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def cache_inventory_sha256(path: str | Path) -> str:
    return acceptance.canonical_cache_inventory_sha256(Path(path).resolve())


def _safe_relative(value: Any) -> bool:
    if not isinstance(value, str) or not value:
        return False
    path = PurePosixPath(value)
    return (
        not path.is_absolute()
        and ".." not in path.parts
        and path.as_posix() == value
    )


def _validate_provider_and_limits(
    provider_snapshot: Any, limits: Any
) -> tuple[dict[str, Any], dict[str, int]]:
    provider_fields = {
        "provider_family",
        "model",
        "reasoning_profile",
        "backend_sha256",
    }
    if (
        not isinstance(provider_snapshot, dict)
        or set(provider_snapshot) != provider_fields
        or any(
            not isinstance(provider_snapshot.get(field), str)
            or not provider_snapshot[field].strip()
            for field in provider_fields - {"backend_sha256"}
        )
        or not preregistration._is_sha256(
            provider_snapshot.get("backend_sha256")
        )
    ):
        raise ValueError("freeze_provider_snapshot_invalid")
    limit_fields = {
        "max_total_tokens",
        "max_total_elapsed_sec",
        "max_output_chars",
    }
    if (
        not isinstance(limits, dict)
        or set(limits) != limit_fields
        or any(
            not isinstance(limits.get(field), int)
            or isinstance(limits[field], bool)
            or limits[field] <= 0
            for field in limit_fields
        )
    ):
        raise ValueError("freeze_limits_invalid")
    return deepcopy(provider_snapshot), deepcopy(limits)


def _bundle_contract(bundle: Mapping[str, str | Path]) -> dict[str, str]:
    if not isinstance(bundle, Mapping) or set(bundle) != set(BUNDLE_PATH_FIELDS):
        raise ValueError("freeze_bundle_invalid")
    result: dict[str, str] = {}
    for name in BUNDLE_PATH_FIELDS:
        path = Path(bundle[name]).expanduser().resolve(strict=False)
        if not path.is_file():
            raise ValueError("freeze_bundle_invalid")
        result[f"{name}_sha256"] = file_sha256(path)
    return result


def _environment(
    value: Any, *, workload: dict[str, Any], source_root: Path
) -> dict[str, Any]:
    fields = {
        "schema_version",
        "source_commit",
        "dependency_lock_path",
        "dependency_lock_sha256",
        "cache_sha256",
        "runtime_identity_path",
        "runtime_identity_sha256",
        "cache_path",
        "readiness",
    }
    if not isinstance(value, dict) or set(value) != fields:
        raise ValueError("freeze_environment_invalid")
    lock_path = value.get("dependency_lock_path")
    runtime = Path(str(value.get("runtime_identity_path", ""))).resolve(
        strict=False
    )
    cache = Path(str(value.get("cache_path", ""))).resolve(strict=False)
    readiness = value.get("readiness")
    if (
        value.get("schema_version") != "omc-product-value-environment/v3"
        or value.get("source_commit") != workload["source_commit"]
        or not _safe_relative(lock_path)
        or not runtime.is_absolute()
        or not runtime.is_file()
        or not cache.is_absolute()
        or not cache.is_dir()
        or cache.stat().st_mode & (stat.S_IWUSR | stat.S_IWGRP | stat.S_IWOTH)
        or not isinstance(readiness, dict)
        or set(readiness) != {"argv"}
        or not isinstance(readiness["argv"], list)
        or not readiness["argv"]
        or any(
            not isinstance(argument, str) or not argument
            for argument in readiness["argv"]
        )
        or readiness["argv"][0] != str(runtime)
    ):
        raise ValueError("freeze_environment_invalid")
    lock = (source_root / str(lock_path)).resolve(strict=False)
    if (
        source_root not in lock.parents
        or not lock.is_file()
        or file_sha256(lock) != value.get("dependency_lock_sha256")
        or file_sha256(runtime) != value.get("runtime_identity_sha256")
        or cache_inventory_sha256(cache) != value.get("cache_sha256")
    ):
        raise ValueError("freeze_environment_hash_mismatch")
    return deepcopy(value)


def _source_root(
    source_roots: Mapping[str, Any], workload: dict[str, Any]
) -> Path:
    entry = source_roots.get(workload["repo_alias"])
    if (
        not isinstance(entry, dict)
        or set(entry) != {"path", "identity_sha256"}
        or entry.get("identity_sha256")
        != workload.get("repository_identity_sha256")
        or not isinstance(entry.get("path"), str)
    ):
        raise ValueError("freeze_source_root_invalid")
    root = Path(entry["path"]).expanduser().resolve(strict=False)
    if not root.is_dir():
        raise ValueError("freeze_source_root_invalid")
    try:
        commit = subprocess.run(
            [
                "git",
                "-C",
                str(root),
                "cat-file",
                "-e",
                f"{workload['source_commit']}^{{commit}}",
            ],
            check=False,
            capture_output=True,
            text=True,
            timeout=10,
        )
    except (OSError, subprocess.SubprocessError) as error:
        raise ValueError("freeze_source_commit_unavailable") from error
    if commit.returncode != 0:
        raise ValueError("freeze_source_commit_unavailable")
    return root


def _default_grant_validator(
    grant: dict[str, Any], source_root: Path, prompts: dict[str, str]
) -> bool:
    _proposal, _expiry, error = _validate_grant(
        grant,
        trusted_target=source_root,
        prompts=prompts,
        now=lambda: datetime.now(timezone.utc),
    )
    return error is None


def freeze_v4_candidate(
    *,
    workloads: list[dict[str, Any]],
    packets: Mapping[str, dict[str, Any]],
    executions: Mapping[str, dict[str, Any]],
    environments: Mapping[str, dict[str, Any]],
    source_roots: Mapping[str, dict[str, str]],
    bundle: Mapping[str, str | Path],
    provider_snapshot: dict[str, Any],
    limits: dict[str, int],
    grant_validator: Callable[[dict[str, Any], Path, dict[str, str]], bool]
    | None = None,
) -> dict[str, Any]:
    """Return a hash-bound candidate without claiming registration or approval."""
    provider, bounded_limits = _validate_provider_and_limits(
        provider_snapshot, limits
    )
    bundle_hashes = _bundle_contract(bundle)
    if not isinstance(workloads, list) or len(workloads) != 6:
        raise ValueError("freeze_input_coverage_invalid")
    workload_ids = {
        item.get("workload_id")
        for item in workloads
        if isinstance(item, dict) and isinstance(item.get("workload_id"), str)
    }
    if (
        len(workload_ids) != 6
        or set(packets) != workload_ids
        or set(executions) != workload_ids
        or set(environments) != workload_ids
    ):
        raise ValueError("freeze_input_coverage_invalid")
    validate_grant = grant_validator or _default_grant_validator
    frozen_packets: dict[str, dict[str, Any]] = {}
    frozen_workloads: list[dict[str, Any]] = []
    for original in sorted(workloads, key=lambda row: row["workload_id"]):
        workload = deepcopy(original)
        workload_id = workload["workload_id"]
        packet = deepcopy(packets[workload_id])
        if (
            not isinstance(packet, dict)
            or packet.get("schema_version")
            != "omc-product-value-execution-packet/v1"
            or packet.get("workload_id") != workload_id
            or packet.get("repo_alias") != workload.get("repo_alias")
            or packet.get("source_commit") != workload.get("source_commit")
            or canonical_sha256(packet) != workload.get("execution_packet_sha256")
        ):
            raise ValueError("freeze_source_packet_invalid")
        source_root = _source_root(source_roots, workload)
        execution = deepcopy(executions[workload_id])
        if (
            not isinstance(execution, dict)
            or set(execution) != {"grant", "prompts"}
            or not isinstance(execution.get("grant"), dict)
            or not isinstance(execution.get("prompts"), dict)
            or execution["prompts"] != execution["grant"].get("child_prompts")
            or len(execution["grant"].get("children", []))
            != workload.get("expected_child_count")
            or any(
                execution["grant"].get(field) != bounded_limits[field]
                for field in bounded_limits
            )
            or validate_grant(
                execution["grant"], source_root, execution["prompts"]
            )
            is not True
        ):
            raise ValueError("freeze_execution_invalid")
        environment = _environment(
            environments[workload_id],
            workload=workload,
            source_root=source_root,
        )
        children = execution["grant"]["children"]
        upgraded = {
            key: deepcopy(packet[key])
            for key in (
                "workload_id",
                "repo_alias",
                "source_commit",
                "request",
                "dod",
                "verification",
            )
        }
        upgraded.update({
            "schema_version": "omc-product-value-execution-packet/v2",
            "omc_execution": execution,
            "baseline_execution_brief": acceptance.build_baseline_execution_brief(
                packet["request"],
                packet["dod"],
                children,
                execution["prompts"],
            ),
            "environment_receipt": environment,
        })
        workload["execution_packet_sha256"] = canonical_sha256(upgraded)
        workload["environment_receipt_sha256"] = canonical_sha256(environment)
        frozen_packets[workload_id] = upgraded
        frozen_workloads.append(workload)
    preregistration._validate_workloads(
        frozen_workloads,
        schema_version=preregistration.SCHEMA_VERSION_V4,
    )
    execution_contract = {
        "provider_snapshot": provider,
        "limits": bounded_limits,
        "runner_schema": "omc-product-value-acceptance/v2",
        "telemetry_schema": "omc-product-value-telemetry/v1",
        "execution_bundle": bundle_hashes,
        "environment_policy": {
            "receipt_schema": "omc-product-value-environment/v3",
            "probe_schema": "omc-product-value-environment-probe/v1",
            "cache_inventory_schema": "omc-product-value-cache-inventory/v1",
            "cache_inventory_max_entries": 10_000,
            "cache_inventory_max_bytes": 1_073_741_824,
            "same_readonly_cache_required": True,
            "preparation_cost_included": False,
        },
    }
    preregistration._validate_execution_contract_v4(execution_contract)
    candidate = {
        "schema_version": SCHEMA_VERSION,
        "status": "candidate_frozen",
        "registration_status": "not_registered",
        "approval_status": "pending",
        "workloads": frozen_workloads,
        "packets": frozen_packets,
        "execution_contract": execution_contract,
    }
    candidate["candidate_sha256"] = canonical_sha256(candidate)
    return candidate


def _write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def write_v4_candidate(
    candidate: dict[str, Any], output_root: str | Path
) -> dict[str, Any]:
    """Atomically persist one complete candidate; never merge with old output."""
    expected_hash = candidate.get("candidate_sha256") if isinstance(candidate, dict) else None
    unsigned = deepcopy(candidate) if isinstance(candidate, dict) else {}
    unsigned.pop("candidate_sha256", None)
    if (
        candidate.get("schema_version") != SCHEMA_VERSION
        or candidate.get("status") != "candidate_frozen"
        or canonical_sha256(unsigned) != expected_hash
        or not isinstance(candidate.get("packets"), dict)
        or len(candidate["packets"]) != 6
    ):
        raise ValueError("freeze_candidate_invalid")
    destination = Path(output_root).expanduser().resolve(strict=False)
    if destination.exists():
        raise ValueError("freeze_output_exists")
    destination.parent.mkdir(parents=True, exist_ok=True)
    staging = Path(
        tempfile.mkdtemp(prefix=f".{destination.name}-", dir=destination.parent)
    )
    try:
        _write_json(staging / "candidate.json", candidate)
        _write_json(staging / "workloads.json", candidate["workloads"])
        _write_json(
            staging / "execution-contract.json",
            candidate["execution_contract"],
        )
        for workload_id, packet in sorted(candidate["packets"].items()):
            _write_json(staging / "packets" / f"{workload_id}.json", packet)
        receipt = {
            "schema_version": SCHEMA_VERSION,
            "status": "candidate_frozen",
            "candidate_sha256": expected_hash,
            "workloads_sha256": canonical_sha256(candidate["workloads"]),
            "execution_contract_sha256": canonical_sha256(
                candidate["execution_contract"]
            ),
            "packet_sha256s": {
                workload_id: canonical_sha256(packet)
                for workload_id, packet in sorted(candidate["packets"].items())
            },
        }
        _write_json(staging / "freeze-receipt.json", receipt)
        os.replace(staging, destination)
        return receipt
    except Exception:
        shutil.rmtree(staging, ignore_errors=True)
        raise
