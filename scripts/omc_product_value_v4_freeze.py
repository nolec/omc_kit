#!/usr/bin/env python3
"""Build a deterministic, approval-pending Product Value v4 freeze candidate."""

from __future__ import annotations

import argparse
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
INPUT_SCHEMA_VERSION = "omc-product-value-v4-inputs/v1"
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


def _surface_verification(
    value: Any, *, source_root: Path, source_commit: str
) -> dict[str, str]:
    if (
        not isinstance(value, dict)
        or set(value) != {"path", "sha256"}
        or not _safe_relative(value.get("path"))
        or not preregistration._is_sha256(value.get("sha256"))
    ):
        raise ValueError("freeze_direct_surface_unverified")
    evidence_path = (source_root / value["path"]).resolve(strict=False)
    try:
        committed = subprocess.run(
            ["git", "-C", str(source_root), "show", f"{source_commit}:{value['path']}"],
            check=False,
            capture_output=True,
            timeout=10,
        )
    except (OSError, subprocess.SubprocessError) as error:
        raise ValueError("freeze_direct_surface_unverified") from error
    if (
        source_root not in evidence_path.parents
        or not evidence_path.is_file()
        or file_sha256(evidence_path) != value["sha256"]
        or committed.returncode != 0
        or hashlib.sha256(committed.stdout).hexdigest() != value["sha256"]
    ):
        raise ValueError("freeze_direct_surface_unverified")
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
    surface_verifications: Mapping[str, dict[str, str]],
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
        or not isinstance(packets, Mapping)
        or set(packets) != workload_ids
        or not isinstance(executions, Mapping)
        or set(executions) != workload_ids
        or not isinstance(environments, Mapping)
        or set(environments) != workload_ids
        or not isinstance(surface_verifications, Mapping)
        or set(surface_verifications) != workload_ids
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
        surface_verification = _surface_verification(
            surface_verifications[workload_id],
            source_root=source_root,
            source_commit=workload["source_commit"],
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
            "schema_version": "omc-product-value-execution-packet/v3",
            "omc_execution": execution,
            "baseline_execution_brief": acceptance.build_baseline_execution_brief(
                packet["request"],
                packet["dod"],
                children,
                execution["prompts"],
            ),
            "environment_receipt": environment,
            "direct_surface_verification": surface_verification,
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


def _load_json(path: str | Path) -> Any:
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        raise ValueError("freeze_input_file_invalid") from error


def prepare_v4_inputs(
    *,
    workloads: list[dict[str, Any]],
    executions: Mapping[str, dict[str, Any]],
    environment_specs: Mapping[str, dict[str, Any]],
    source_roots: Mapping[str, dict[str, str]],
    provider_snapshot: dict[str, Any],
    limits: dict[str, int],
) -> dict[str, Any]:
    """Bind approved execution and environment specs before candidate freeze."""
    provider, bounded_limits = _validate_provider_and_limits(
        provider_snapshot, limits
    )
    if not isinstance(workloads, list) or len(workloads) != 6:
        raise ValueError("freeze_input_coverage_invalid")
    workload_by_id = {
        item.get("workload_id"): item
        for item in workloads
        if isinstance(item, dict) and isinstance(item.get("workload_id"), str)
    }
    workload_ids = set(workload_by_id)
    if (
        len(workload_ids) != 6
        or not isinstance(executions, Mapping)
        or set(executions) != workload_ids
        or not isinstance(environment_specs, Mapping)
        or set(environment_specs) != workload_ids
    ):
        raise ValueError("freeze_input_coverage_invalid")
    environments: dict[str, dict[str, Any]] = {}
    prepared_executions: dict[str, dict[str, Any]] = {}
    surface_verifications: dict[str, dict[str, str]] = {}
    for workload_id in sorted(workload_ids):
        workload = workload_by_id[workload_id]
        execution = deepcopy(executions[workload_id])
        if (
            not isinstance(execution, dict)
            or set(execution) != {"grant", "prompts"}
            or not isinstance(execution.get("grant"), dict)
            or not isinstance(execution.get("prompts"), dict)
            or execution["prompts"] != execution["grant"].get("child_prompts")
            or any(
                execution["grant"].get(field) != bounded_limits[field]
                for field in bounded_limits
            )
        ):
            raise ValueError("freeze_execution_invalid")
        spec = environment_specs[workload_id]
        expected_spec_fields = {
            "dependency_lock_path",
            "runtime_identity_path",
            "cache_path",
            "readiness",
            "direct_surface_verification_path",
            "direct_surface_verification_sha256",
        }
        if not isinstance(spec, dict) or set(spec) != expected_spec_fields:
            raise ValueError("freeze_environment_spec_invalid")
        source_root = _source_root(source_roots, workload)
        lock = (source_root / str(spec["dependency_lock_path"])).resolve(
            strict=False
        )
        runtime = Path(str(spec["runtime_identity_path"])).resolve(strict=False)
        cache = Path(str(spec["cache_path"])).resolve(strict=False)
        try:
            environment = {
                "schema_version": "omc-product-value-environment/v3",
                "source_commit": workload["source_commit"],
                "dependency_lock_path": spec["dependency_lock_path"],
                "dependency_lock_sha256": file_sha256(lock),
                "cache_sha256": cache_inventory_sha256(cache),
                "runtime_identity_path": str(runtime),
                "runtime_identity_sha256": file_sha256(runtime),
                "cache_path": str(cache),
                "readiness": deepcopy(spec["readiness"]),
            }
        except OSError as error:
            raise ValueError("freeze_environment_invalid") from error
        environments[workload_id] = _environment(
            environment, workload=workload, source_root=source_root
        )
        prepared_executions[workload_id] = execution
        surface_verifications[workload_id] = _surface_verification(
            {
                "path": spec["direct_surface_verification_path"],
                "sha256": spec["direct_surface_verification_sha256"],
            },
            source_root=source_root,
            source_commit=workload["source_commit"],
        )
    prepared = {
        "schema_version": INPUT_SCHEMA_VERSION,
        "status": "inputs_prepared",
        "executions": prepared_executions,
        "environments": environments,
        "surface_verifications": surface_verifications,
        "provider_snapshot": provider,
        "limits": bounded_limits,
    }
    prepared["inputs_sha256"] = canonical_sha256(prepared)
    return prepared


def write_v4_inputs(value: dict[str, Any], output_root: str | Path) -> dict[str, Any]:
    unsigned = deepcopy(value)
    expected_hash = unsigned.pop("inputs_sha256", None)
    if (
        value.get("schema_version") != INPUT_SCHEMA_VERSION
        or value.get("status") != "inputs_prepared"
        or canonical_sha256(unsigned) != expected_hash
    ):
        raise ValueError("freeze_inputs_invalid")
    destination = Path(output_root).expanduser().resolve(strict=False)
    if destination.exists():
        raise ValueError("freeze_output_exists")
    destination.parent.mkdir(parents=True, exist_ok=True)
    staging = Path(
        tempfile.mkdtemp(prefix=f".{destination.name}-", dir=destination.parent)
    )
    try:
        for name in (
            "executions",
            "environments",
            "surface_verifications",
            "provider_snapshot",
            "limits",
        ):
            _write_json(staging / f"{name.replace('_', '-')}.json", value[name])
        receipt = {
            "schema_version": INPUT_SCHEMA_VERSION,
            "status": "inputs_prepared",
            "inputs_sha256": expected_hash,
        }
        _write_json(staging / "input-receipt.json", receipt)
        os.replace(staging, destination)
        return receipt
    except Exception:
        shutil.rmtree(staging, ignore_errors=True)
        raise


def _load_corpus(
    root: Path,
) -> tuple[list[dict[str, Any]], dict[str, Any], dict[str, Any]]:
    workloads = _load_json(root / "workloads.json")
    source_roots = _load_json(root / "source-roots.json")
    if not isinstance(workloads, list):
        raise ValueError("freeze_input_file_invalid")
    try:
        packets = {
            workload["workload_id"]: _load_json(
                root / "packets" / f"{workload['workload_id']}.json"
            )
            for workload in workloads
        }
    except (KeyError, TypeError) as error:
        raise ValueError("freeze_input_file_invalid") from error
    return workloads, packets, source_roots


def _load_prepared_inputs(root: Path) -> dict[str, Any]:
    value = {
        "schema_version": INPUT_SCHEMA_VERSION,
        "status": "inputs_prepared",
        "executions": _load_json(root / "executions.json"),
        "environments": _load_json(root / "environments.json"),
        "surface_verifications": _load_json(root / "surface-verifications.json"),
        "provider_snapshot": _load_json(root / "provider-snapshot.json"),
        "limits": _load_json(root / "limits.json"),
    }
    receipt = _load_json(root / "input-receipt.json")
    value["inputs_sha256"] = canonical_sha256(value)
    if (
        not isinstance(receipt, dict)
        or receipt.get("schema_version") != INPUT_SCHEMA_VERSION
        or receipt.get("status") != "inputs_prepared"
        or receipt.get("inputs_sha256") != value["inputs_sha256"]
    ):
        raise ValueError("freeze_inputs_invalid")
    return value


def _bundle_from_args(args: argparse.Namespace) -> dict[str, Path]:
    return {name: Path(getattr(args, name)) for name in BUNDLE_PATH_FIELDS}


def _candidate_from_files(args: argparse.Namespace) -> dict[str, Any]:
    workloads, packets, source_roots = _load_corpus(Path(args.corpus_root))
    prepared = _load_prepared_inputs(Path(args.input_root))
    return freeze_v4_candidate(
        workloads=workloads,
        packets=packets,
        executions=prepared["executions"],
        environments=prepared["environments"],
        surface_verifications=prepared["surface_verifications"],
        source_roots=source_roots,
        bundle=_bundle_from_args(args),
        provider_snapshot=prepared["provider_snapshot"],
        limits=prepared["limits"],
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


def validate_v4_candidate(
    candidate: dict[str, Any], candidate_root: str | Path
) -> dict[str, Any]:
    root = Path(candidate_root).expanduser().resolve(strict=False)
    try:
        stored = _load_json(root / "candidate.json")
        stored_workloads = _load_json(root / "workloads.json")
        stored_contract = _load_json(root / "execution-contract.json")
        packet_root = root / "packets"
        expected_packet_names = {
            f"{workload_id}.json" for workload_id in candidate["packets"]
        }
        actual_packet_names = {
            path.name for path in packet_root.iterdir() if path.is_file()
        }
        if actual_packet_names != expected_packet_names:
            raise ValueError("freeze_candidate_artifact_mismatch")
        stored_packets = {
            workload_id: _load_json(packet_root / f"{workload_id}.json")
            for workload_id in candidate["packets"]
        }
        receipt = _load_json(root / "freeze-receipt.json")
    except (KeyError, OSError, TypeError, ValueError) as error:
        if isinstance(error, ValueError) and str(error) == "freeze_candidate_artifact_mismatch":
            raise
        raise ValueError("freeze_candidate_artifact_mismatch") from error
    if stored != candidate:
        raise ValueError("freeze_candidate_mismatch")
    if (
        stored_workloads != candidate["workloads"]
        or stored_contract != candidate["execution_contract"]
        or stored_packets != candidate["packets"]
    ):
        raise ValueError("freeze_candidate_artifact_mismatch")
    expected = candidate.get("candidate_sha256")
    if (
        not isinstance(receipt, dict)
        or receipt.get("schema_version") != SCHEMA_VERSION
        or receipt.get("status") != "candidate_frozen"
        or receipt.get("candidate_sha256") != expected
        or receipt.get("workloads_sha256")
        != canonical_sha256(candidate["workloads"])
        or receipt.get("execution_contract_sha256")
        != canonical_sha256(candidate["execution_contract"])
        or receipt.get("packet_sha256s")
        != {
            workload_id: canonical_sha256(packet)
            for workload_id, packet in sorted(candidate["packets"].items())
        }
    ):
        raise ValueError("freeze_receipt_invalid")
    return {"status": "valid", "candidate_sha256": expected}


def _add_bundle_arguments(parser: argparse.ArgumentParser) -> None:
    for name in BUNDLE_PATH_FIELDS:
        parser.add_argument(f"--{name.replace('_', '-')}", required=True, type=Path)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    prepare_inputs = sub.add_parser("prepare-inputs")
    prepare_inputs.add_argument("--corpus-root", required=True, type=Path)
    prepare_inputs.add_argument("--execution-specs", required=True, type=Path)
    prepare_inputs.add_argument("--environment-specs", required=True, type=Path)
    prepare_inputs.add_argument("--provider-snapshot", required=True, type=Path)
    prepare_inputs.add_argument("--limits", required=True, type=Path)
    prepare_inputs.add_argument("--out", required=True, type=Path)
    prepare = sub.add_parser("prepare")
    validate = sub.add_parser("validate")
    for command in (prepare, validate):
        command.add_argument("--corpus-root", required=True, type=Path)
        command.add_argument("--input-root", required=True, type=Path)
        _add_bundle_arguments(command)
    prepare.add_argument("--out", required=True, type=Path)
    validate.add_argument("--candidate-root", required=True, type=Path)
    args = parser.parse_args(argv)
    if args.command == "prepare-inputs":
        workloads, _packets, source_roots = _load_corpus(args.corpus_root)
        value = prepare_v4_inputs(
            workloads=workloads,
            executions=_load_json(args.execution_specs),
            environment_specs=_load_json(args.environment_specs),
            source_roots=source_roots,
            provider_snapshot=_load_json(args.provider_snapshot),
            limits=_load_json(args.limits),
        )
        result = write_v4_inputs(value, args.out)
    elif args.command == "prepare":
        result = write_v4_candidate(_candidate_from_files(args), args.out)
    else:
        result = validate_v4_candidate(
            _candidate_from_files(args), args.candidate_root
        )
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
