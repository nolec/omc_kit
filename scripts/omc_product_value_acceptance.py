#!/usr/bin/env python3
"""Run manifest-bound Product Value pilot and paired acceptance evidence."""

from __future__ import annotations

import argparse
from copy import deepcopy
import fcntl
import hashlib
import json
import math
import os
from pathlib import Path, PurePosixPath
import shutil
import stat
import statistics
import subprocess
import tempfile
import time
from typing import Any, Callable

import omc_product_value_preregistration as preregistration
from omc_n_child_scheduler import _run_bounded_adapter_command


SCHEMA_VERSION = "omc-product-value-acceptance/v1"
SCHEMA_VERSION_V2 = "omc-product-value-acceptance/v2"
PACKET_SCHEMA_VERSION = "omc-product-value-execution-packet/v1"
PACKET_SCHEMA_VERSION_V2 = "omc-product-value-execution-packet/v2"
PACKET_SCHEMA_VERSION_V3 = "omc-product-value-execution-packet/v3"
TELEMETRY_SCHEMA_VERSION = "omc-product-value-telemetry/v1"
ARM_PROTOCOL = "omc-product-value-arm/v1"
ARM_TOKEN_ENFORCEMENT = {
    "mode": "provider_enforced_total",
    "request_field": "max_total_tokens",
    "over_limit_behavior": "reject_before_or_during_generation",
}
ARMS = ("omc", "baseline")
REVIEW_SEVERITIES = {"critical", "major", "minor", "suggestion"}
CACHE_INVENTORY_MAX_ENTRIES = 10_000
CACHE_INVENTORY_MAX_BYTES = 1_073_741_824


class _CapabilityBoundArmExecutor:
    __slots__ = ("_execute", "_transport_attestation")

    def __init__(
        self,
        execute: Callable[..., dict[str, Any]],
        transport_attestation: dict[str, Any],
    ) -> None:
        self._execute = execute
        self._transport_attestation = deepcopy(transport_attestation)

    def __call__(self, **kwargs: Any) -> dict[str, Any]:
        return self._execute(**kwargs)

    def transport_attestation(self) -> dict[str, Any]:
        return deepcopy(self._transport_attestation)


def canonical_sha256(value: Any) -> str:
    payload = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def _is_sha256(value: Any) -> bool:
    return (
        isinstance(value, str)
        and len(value) == 64
        and set(value) <= set("0123456789abcdef")
    )


def _load_json(path: Path, reason: str = "acceptance_input_unavailable") -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        raise ValueError(reason) from error


def _write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def _write_envelope(path: Path, payload: dict[str, Any]) -> str:
    digest = canonical_sha256(payload)
    _write_json(path, {"sha256": digest, "payload": payload})
    return digest


def _load_envelope(path: Path) -> tuple[dict[str, Any], str]:
    envelope = _load_json(path, "artifact_unavailable")
    payload = envelope.get("payload") if isinstance(envelope, dict) else None
    digest = envelope.get("sha256") if isinstance(envelope, dict) else None
    if (
        not isinstance(payload, dict)
        or not _is_sha256(digest)
        or canonical_sha256(payload) != digest
    ):
        raise ValueError("artifact_hash_mismatch")
    return payload, digest


def _validate_acceptance_manifest(payload: Any) -> dict[str, Any]:
    manifest = preregistration.validate_preregistration(payload)
    schema = manifest.get("schema_version")
    expected_runner = {
        preregistration.SCHEMA_VERSION_V3: SCHEMA_VERSION,
        preregistration.SCHEMA_VERSION_V4: SCHEMA_VERSION_V2,
        preregistration.SCHEMA_VERSION_V5: SCHEMA_VERSION_V2,
    }.get(schema)
    if expected_runner is None:
        raise ValueError("acceptance_requires_paired_preregistration")
    if manifest["execution_contract"]["runner_schema"] != expected_runner:
        raise ValueError("acceptance_runner_schema_mismatch")
    if manifest["execution_contract"]["telemetry_schema"] != TELEMETRY_SCHEMA_VERSION:
        raise ValueError("acceptance_telemetry_schema_mismatch")
    return manifest


def build_baseline_execution_brief(
    request: str,
    dod: str,
    children: list[dict[str, Any]],
    prompts: dict[str, str],
) -> str:
    if (
        not isinstance(request, str)
        or not request.strip()
        or not isinstance(dod, str)
        or not dod.strip()
        or not isinstance(children, list)
        or not 3 <= len(children) <= 5
        or not isinstance(prompts, dict)
    ):
        raise ValueError("baseline_execution_brief_input_invalid")
    steps: list[dict[str, Any]] = []
    child_ids: list[str] = []
    for child in children:
        if not isinstance(child, dict):
            raise ValueError("baseline_execution_brief_input_invalid")
        child_id = child.get("child_id")
        depends_on = child.get("depends_on")
        scope_paths = child.get("scope_paths")
        if (
            not isinstance(child_id, str)
            or not child_id.strip()
            or not isinstance(depends_on, list)
            or not all(isinstance(item, str) and item for item in depends_on)
            or not isinstance(scope_paths, list)
            or not all(isinstance(item, str) and item for item in scope_paths)
            or not isinstance(prompts.get(child_id), str)
            or not prompts[child_id].strip()
        ):
            raise ValueError("baseline_execution_brief_input_invalid")
        child_ids.append(child_id)
        steps.append({
            "child_id": child_id,
            "depends_on": depends_on,
            "scope_paths": scope_paths,
            "instruction": prompts[child_id],
        })
    if len(set(child_ids)) != len(child_ids) or set(prompts) != set(child_ids):
        raise ValueError("baseline_execution_brief_input_invalid")
    return json.dumps(
        {"request": request, "dod": dod, "steps": steps},
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )


def _default_registration_validator(
    manifest: dict[str, Any], context: dict[str, Any]
) -> dict[str, Any]:
    required = {
        "repository_root",
        "registry_commit",
        "registry_path",
        "required_ancestor_commit",
        "registration_receipt",
        "expected_registration_receipt_sha256",
        "trusted_root",
        "approved_trusted_root_sha256",
    }
    if not isinstance(context, dict) or set(context) != required:
        raise ValueError("registration_context_invalid")
    return preregistration.validate_registered_preregistration(
        manifest,
        repository_root=Path(context["repository_root"]).resolve(),
        registry_commit=context["registry_commit"],
        registry_path=context["registry_path"],
        required_ancestor_commit=context["required_ancestor_commit"],
        registration_receipt=context["registration_receipt"],
        expected_registration_receipt_sha256=(
            context["expected_registration_receipt_sha256"]
        ),
        trusted_root=context["trusted_root"],
        approved_trusted_root_sha256=context["approved_trusted_root_sha256"],
    )


def _registration_gate(
    manifest: dict[str, Any],
    context: dict[str, Any],
    validator: Callable[..., dict[str, Any]],
) -> dict[str, Any]:
    try:
        result = validator(manifest, context)
    except Exception as error:
        raise ValueError("registration_blocked") from error
    if (
        not isinstance(result, dict)
        or result.get("claim_eligible") is not True
        or result.get("status") != "registered"
        or result.get("preregistration_sha256")
        != manifest["preregistration_sha256"]
        or not _is_sha256(result.get("registration_receipt_sha256"))
    ):
        raise ValueError("registration_blocked")
    return deepcopy(result)


def _valid_verification(value: Any) -> bool:
    return (
        isinstance(value, dict)
        and set(value) == {"argv"}
        and isinstance(value["argv"], list)
        and bool(value["argv"])
        and all(isinstance(item, str) and item for item in value["argv"])
    )


def validate_execution_packet(
    manifest: dict[str, Any], workload: dict[str, Any], packet: Any
) -> dict[str, Any]:
    manifest = _validate_acceptance_manifest(manifest)
    if manifest["schema_version"] in {
        preregistration.SCHEMA_VERSION_V4,
        preregistration.SCHEMA_VERSION_V5,
    }:
        packet_schema = packet.get("schema_version") if isinstance(packet, dict) else None
        if packet_schema == PACKET_SCHEMA_VERSION_V2:
            return _validate_execution_packet_v2(manifest, workload, packet)
        if packet_schema == PACKET_SCHEMA_VERSION_V3:
            return _validate_execution_packet_v3(manifest, workload, packet)
        raise ValueError("execution_packet_invalid")
    if (
        not isinstance(packet, dict)
        or set(packet) != {
            "schema_version",
            "workload_id",
            "repo_alias",
            "source_commit",
            "request",
            "dod",
            "verification",
            "arms",
        }
        or packet.get("schema_version") != PACKET_SCHEMA_VERSION
        or packet.get("workload_id") != workload["workload_id"]
        or packet.get("repo_alias") != workload["repo_alias"]
        or packet.get("source_commit") != workload["source_commit"]
        or not isinstance(packet.get("request"), str)
        or not packet["request"].strip()
        or not isinstance(packet.get("dod"), str)
        or not packet["dod"].strip()
        or not _valid_verification(packet.get("verification"))
        or not isinstance(packet.get("arms"), dict)
        or set(packet["arms"]) != set(ARMS)
    ):
        raise ValueError("execution_packet_invalid")
    if (
        canonical_sha256(packet) != workload["execution_packet_sha256"]
        or canonical_sha256(packet["request"]) != workload["request_sha256"]
        or canonical_sha256(packet["dod"]) != workload["dod_sha256"]
        or canonical_sha256(packet["verification"])
        != workload["verification_sha256"]
    ):
        raise ValueError("execution_packet_hash_mismatch")
    for arm in ARMS:
        arm_payload = packet["arms"].get(arm)
        if (
            not isinstance(arm_payload, dict)
            or set(arm_payload) != {"prompt", "mode"}
            or not isinstance(arm_payload.get("prompt"), str)
            or not arm_payload["prompt"].strip()
            or arm_payload.get("mode")
            != ("bounded_n_child" if arm == "omc" else "single_agent")
        ):
            raise ValueError("execution_packet_arm_invalid")
    return deepcopy(packet)


def _validate_execution_packet_v2(
    manifest: dict[str, Any], workload: dict[str, Any], packet: Any
) -> dict[str, Any]:
    return _validate_bounded_execution_packet(
        manifest,
        workload,
        packet,
        packet_schema=PACKET_SCHEMA_VERSION_V2,
        require_surface_verification=False,
    )


def _validate_execution_packet_v3(
    manifest: dict[str, Any], workload: dict[str, Any], packet: Any
) -> dict[str, Any]:
    return _validate_bounded_execution_packet(
        manifest,
        workload,
        packet,
        packet_schema=PACKET_SCHEMA_VERSION_V3,
        require_surface_verification=True,
    )


def _validate_bounded_execution_packet(
    manifest: dict[str, Any],
    workload: dict[str, Any],
    packet: Any,
    *,
    packet_schema: str,
    require_surface_verification: bool,
) -> dict[str, Any]:
    expected_fields = {
        "schema_version",
        "workload_id",
        "repo_alias",
        "source_commit",
        "request",
        "dod",
        "verification",
        "omc_execution",
        "baseline_execution_brief",
        "environment_receipt",
    }
    if require_surface_verification:
        expected_fields.add("direct_surface_verification")
    if (
        not isinstance(packet, dict)
        or set(packet) != expected_fields
        or packet.get("schema_version") != packet_schema
        or packet.get("workload_id") != workload["workload_id"]
        or packet.get("repo_alias") != workload["repo_alias"]
        or packet.get("source_commit") != workload["source_commit"]
        or not isinstance(packet.get("request"), str)
        or not packet["request"].strip()
        or not isinstance(packet.get("dod"), str)
        or not packet["dod"].strip()
        or not _valid_verification(packet.get("verification"))
    ):
        raise ValueError("execution_packet_invalid")
    if require_surface_verification:
        surface_verification = packet.get("direct_surface_verification")
        if (
            not isinstance(surface_verification, dict)
            or set(surface_verification) != {"path", "sha256"}
            or not _safe_relative_path(surface_verification.get("path"))
            or not _is_sha256(surface_verification.get("sha256"))
        ):
            raise ValueError("execution_packet_invalid")
    if (
        canonical_sha256(packet) != workload["execution_packet_sha256"]
        or canonical_sha256(packet["request"]) != workload["request_sha256"]
        or canonical_sha256(packet["dod"]) != workload["dod_sha256"]
        or canonical_sha256(packet["verification"])
        != workload["verification_sha256"]
    ):
        raise ValueError("execution_packet_hash_mismatch")
    execution = packet.get("omc_execution")
    if not isinstance(execution, dict) or set(execution) != {"grant", "prompts"}:
        raise ValueError("execution_packet_omc_input_invalid")
    grant = execution.get("grant")
    prompts = execution.get("prompts")
    if (
        not isinstance(grant, dict)
        or grant.get("schema_version") != "omc-n-child-dag/v2"
        or grant.get("mode") != "n_child_dag_grant"
        or grant.get("status") != "ready"
        or grant.get("execution_allowed") is not True
        or grant.get("scheduler_eligible") is not True
        or not isinstance(grant.get("children"), list)
        or len(grant["children"]) != workload["expected_child_count"]
        or not isinstance(prompts, dict)
        or prompts != grant.get("child_prompts")
    ):
        raise ValueError("execution_packet_omc_input_invalid")
    child_ids = [
        child.get("child_id") if isinstance(child, dict) else None
        for child in grant["children"]
    ]
    if (
        any(not isinstance(child_id, str) or not child_id for child_id in child_ids)
        or len(set(child_ids)) != len(child_ids)
        or set(prompts) != set(child_ids)
        or any(
            not isinstance(prompts[child_id], str) or not prompts[child_id].strip()
            for child_id in child_ids
        )
    ):
        raise ValueError("execution_packet_omc_input_invalid")
    child_id_set = set(child_ids)
    dependencies: dict[str, list[str]] = {}
    for child in grant["children"]:
        depends_on = child.get("depends_on")
        scope_paths = child.get("scope_paths")
        if (
            not isinstance(depends_on, list)
            or any(
                not isinstance(dependency, str)
                or dependency not in child_id_set
                or dependency == child["child_id"]
                for dependency in depends_on
            )
            or len(set(depends_on)) != len(depends_on)
            or not isinstance(scope_paths, list)
            or not scope_paths
            or any(
                not isinstance(scope, str)
                or not scope
                or not _in_scope(scope, workload["scope_paths"])
                for scope in scope_paths
            )
        ):
            raise ValueError("execution_packet_omc_input_invalid")
        dependencies[child["child_id"]] = depends_on
    visiting: set[str] = set()
    visited: set[str] = set()

    def visit(child_id: str) -> None:
        if child_id in visiting:
            raise ValueError("execution_packet_omc_input_invalid")
        if child_id in visited:
            return
        visiting.add(child_id)
        for dependency in dependencies[child_id]:
            visit(dependency)
        visiting.remove(child_id)
        visited.add(child_id)

    for child_id in child_ids:
        visit(child_id)
    limits = manifest["execution_contract"]["limits"]
    if any(
        grant.get(field) != limits[field]
        for field in (
            "max_total_tokens",
            "max_total_elapsed_sec",
            "max_output_chars",
        )
    ):
        raise ValueError("execution_packet_omc_input_invalid")
    try:
        expected_brief = build_baseline_execution_brief(
            packet["request"], packet["dod"], grant["children"], prompts
        )
    except ValueError as error:
        raise ValueError("execution_packet_omc_input_invalid") from error
    if packet.get("baseline_execution_brief") != expected_brief:
        raise ValueError("execution_packet_baseline_brief_invalid")
    environment = packet.get("environment_receipt")
    if (
        not isinstance(environment, dict)
        or set(environment) != {
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
        or environment.get("schema_version")
        != "omc-product-value-environment/v3"
        or environment.get("source_commit") != workload["source_commit"]
        or any(
            not _is_sha256(environment.get(field))
            for field in (
                "dependency_lock_sha256",
                "cache_sha256",
                "runtime_identity_sha256",
            )
        )
        or not isinstance(environment.get("cache_path"), str)
        or not Path(environment["cache_path"]).is_absolute()
        or not isinstance(environment.get("runtime_identity_path"), str)
        or not Path(environment["runtime_identity_path"]).is_absolute()
        or not _valid_verification(environment.get("readiness"))
        or not Path(environment["readiness"]["argv"][0]).is_absolute()
        or Path(environment["runtime_identity_path"]).expanduser().resolve()
        != Path(environment["readiness"]["argv"][0]).expanduser().resolve()
        or not isinstance(environment.get("dependency_lock_path"), str)
        or not _safe_relative_path(environment["dependency_lock_path"])
        or canonical_sha256(environment)
        != workload["environment_receipt_sha256"]
    ):
        raise ValueError("execution_packet_environment_mismatch")
    return deepcopy(packet)


def _git(
    root: Path,
    *args: str,
    timeout: float | None = None,
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", "-C", str(root), *args],
        check=False,
        capture_output=True,
        text=True,
        timeout=timeout,
    )


def _source_root(
    workload: dict[str, Any], source_roots: dict[str, Any]
) -> Path:
    entry = source_roots.get(workload["repo_alias"])
    if (
        not isinstance(entry, dict)
        or set(entry) != {"path", "identity_sha256"}
        or entry.get("identity_sha256")
        != workload["repository_identity_sha256"]
        or not isinstance(entry.get("path"), str)
    ):
        raise ValueError("source_identity_mismatch")
    root = Path(entry["path"]).resolve()
    exists = _git(root, "cat-file", "-e", f"{workload['source_commit']}^{{commit}}")
    if exists.returncode != 0:
        raise ValueError("source_commit_unavailable")
    return root


def _changed_paths(workspace: Path, *, timeout: float) -> list[str]:
    started = time.monotonic()
    changed = _git(workspace, "diff", "--name-only", "HEAD", timeout=timeout)
    remaining = timeout - (time.monotonic() - started)
    if remaining <= 0:
        raise subprocess.TimeoutExpired(["git", "diff", "--name-only", "HEAD"], timeout)
    untracked = _git(
        workspace,
        "ls-files",
        "--others",
        "--exclude-standard",
        timeout=remaining,
    )
    if changed.returncode != 0 or untracked.returncode != 0:
        raise ValueError("workspace_diff_unavailable")
    return sorted(
        {
            item
            for output in (changed.stdout, untracked.stdout)
            for item in output.splitlines()
            if item
        }
    )


def _in_scope(path: str, scopes: list[str]) -> bool:
    candidate = PurePosixPath(path)
    return any(
        candidate == PurePosixPath(scope.rstrip("/"))
        or PurePosixPath(scope.rstrip("/")) in candidate.parents
        for scope in scopes
    )


def _safe_relative_path(path: str) -> bool:
    candidate = PurePosixPath(path)
    return bool(
        path
        and not candidate.is_absolute()
        and candidate.parts
        and all(part not in {"", ".", ".."} for part in candidate.parts)
    )


def _validated_result(result: Any, limits: dict[str, int]) -> dict[str, Any]:
    if not isinstance(result, dict):
        raise ValueError("arm_result_invalid")
    required = {
        "status",
        "reason_code",
        "elapsed_sec",
        "output",
        "token_usage",
        "intervention_events",
        "review_findings",
        "duplicate_executions",
        "budget_violations",
    }
    if not required <= set(result):
        raise ValueError("arm_result_invalid")
    if result["status"] not in {"completed", "parent_review"}:
        raise ValueError("arm_result_invalid")
    if (
        not isinstance(result["elapsed_sec"], (int, float))
        or isinstance(result["elapsed_sec"], bool)
        or not math.isfinite(float(result["elapsed_sec"]))
        or result["elapsed_sec"] < 0
        or not isinstance(result["output"], str)
    ):
        raise ValueError("arm_result_invalid")
    usage = result["token_usage"]
    if (
        not isinstance(usage, dict)
        or set(usage) != {"input_tokens", "output_tokens", "total_tokens"}
        or any(
            not isinstance(usage[field], int)
            or isinstance(usage[field], bool)
            or usage[field] < 0
            for field in usage
        )
        or usage["input_tokens"] + usage["output_tokens"] != usage["total_tokens"]
    ):
        raise ValueError("arm_token_usage_invalid")
    if not isinstance(result["intervention_events"], list) or any(
        not isinstance(item, str) for item in result["intervention_events"]
    ):
        raise ValueError("arm_intervention_events_invalid")
    if not isinstance(result["review_findings"], list) or any(
        not isinstance(item, dict)
        or item.get("severity") not in REVIEW_SEVERITIES
        for item in result["review_findings"]
    ):
        raise ValueError("arm_review_findings_invalid")
    for field in ("duplicate_executions", "budget_violations"):
        if (
            not isinstance(result[field], int)
            or isinstance(result[field], bool)
            or result[field] < 0
        ):
            raise ValueError("arm_metric_invalid")
    bounded = deepcopy(result)
    # Provider output cannot self-attest the transport used by the runner.
    bounded["transport_profile"] = "unknown"
    if (
        usage["total_tokens"] > limits["max_total_tokens"]
        or result["elapsed_sec"] > limits["max_total_elapsed_sec"]
        or len(result["output"]) > limits["max_output_chars"]
    ):
        bounded["budget_violations"] += 1
    return bounded


def _frozen_provider_transport_attestation(
    provider_adapter: Path | None,
    provider_adapter_sha256: str | None,
    provider_backend_sha256: str | None,
    provider_backend: Path | None = None,
) -> dict[str, Any]:
    attestation = {
        "profile": "unknown",
        "adapter_sha256": None,
        "backend_sha256": None,
        "capabilities_sha256": None,
    }
    if (
        provider_adapter is None
        or not _is_sha256(provider_adapter_sha256)
        or not _is_sha256(provider_backend_sha256)
    ):
        return attestation
    backend = provider_backend or provider_adapter
    if provider_adapter_sha256 == provider_backend_sha256:
        bound_backend_sha256 = provider_backend_sha256
    else:
        configured_backend = os.environ.get("OMC_PROVIDER_BACKEND", "").strip()
        backend = (
            provider_backend
            if provider_backend is not None
            else Path(configured_backend).expanduser().resolve(strict=False)
        )
        if (
            (provider_backend is None and not configured_backend)
            or not backend.is_file()
            or canonical_file_sha256(backend) != provider_backend_sha256
        ):
            return attestation
        bound_backend_sha256 = provider_backend_sha256
    if canonical_file_sha256(provider_adapter) != provider_adapter_sha256:
        return attestation
    capability = _run_bounded_adapter_command(
        [str(provider_adapter), "capabilities"],
        timeout_sec=10,
        max_response_bytes=64 * 1024,
        env_overrides={"OMC_PROVIDER_BACKEND": str(backend)},
    )
    try:
        payload = json.loads(capability["stdout"])
    except (TypeError, json.JSONDecodeError):
        return attestation
    if (
        capability["returncode"] != 0
        or capability["timed_out"]
        or capability["limit_exceeded"]
        or not isinstance(payload, dict)
        or payload.get("protocol") != "omc-provider/v1"
    ):
        return attestation
    profile = "unknown"
    if (
        payload.get("hard_total_token_limit") is True
        and payload.get("hard_output_limit") is True
        and payload.get("token_enforcement")
        == {
            "mode": "provider_enforced_total",
            "request_field": "max_total_tokens",
            "over_limit_behavior": "reject_before_or_during_generation",
        }
    ):
        profile = "provider_enforced"
    elif (
        payload.get("execution_profile") == "subscription_bounded"
        and payload.get("hard_total_token_limit") is False
        and payload.get("hard_output_limit") is True
    ):
        profile = "subscription_bounded"
    return {
        "profile": profile,
        "adapter_sha256": provider_adapter_sha256,
        "backend_sha256": bound_backend_sha256,
        "capabilities_sha256": canonical_sha256(payload),
    }


def _arm_executor_transport_attestation(
    arm_executor: Callable[..., dict[str, Any]],
) -> dict[str, Any]:
    if isinstance(arm_executor, _CapabilityBoundArmExecutor):
        return arm_executor.transport_attestation()
    return {
        "profile": "unknown",
        "adapter_sha256": None,
        "backend_sha256": None,
        "capabilities_sha256": None,
    }


def build_process_arm_executor(
    adapter_path: str | Path,
    provider_snapshot: dict[str, Any],
    *,
    adapter_sha256: str | None = None,
    execution_bundle: dict[str, Any] | None = None,
    scheduler_path: str | Path | None = None,
    executor_shadow_path: str | Path | None = None,
    provider_adapter_path: str | Path | None = None,
) -> Callable[..., dict[str, Any]]:
    source = Path(adapter_path).expanduser().resolve()
    bundle_sources: dict[str, Path] = {}
    expected_hashes: dict[str, str] = {}
    if execution_bundle is None:
        expected_sha256 = adapter_sha256 or provider_snapshot.get("adapter_sha256")
        if (
            not _is_sha256(expected_sha256)
            or not source.is_file()
        ):
            raise ValueError("provider_snapshot_mismatch")
        bundle_sources = {"arm_adapter": source}
        expected_hashes = {"arm_adapter": expected_sha256}
    else:
        expected_fields = {
            "acceptance_runner_sha256",
            "arm_adapter_sha256",
            "scheduler_sha256",
            "executor_shadow_sha256",
            "provider_adapter_sha256",
        }
        if (
            set(execution_bundle) != expected_fields
            or scheduler_path is None
            or executor_shadow_path is None
            or provider_adapter_path is None
        ):
            raise ValueError("execution_bundle_mismatch")
        bundle_sources = {
            "acceptance_runner": Path(__file__).resolve(),
            "arm_adapter": source,
            "scheduler": Path(scheduler_path).expanduser().resolve(),
            "executor_shadow": Path(executor_shadow_path).expanduser().resolve(),
            "provider_adapter": Path(provider_adapter_path).expanduser().resolve(),
        }
        for name, path in bundle_sources.items():
            expected_sha256 = execution_bundle[f"{name}_sha256"]
            if (
                not _is_sha256(expected_sha256)
                or not path.is_file()
            ):
                raise ValueError("execution_bundle_mismatch")
            expected_hashes[name] = expected_sha256
        configured_backend = os.environ.get("OMC_PROVIDER_BACKEND", "").strip()
        provider_backend = Path(configured_backend).expanduser().resolve(strict=False)
        provider_backend_sha256 = provider_snapshot.get("backend_sha256")
        if configured_backend:
            if (
                not _is_sha256(provider_backend_sha256)
                or not provider_backend.is_file()
                or canonical_file_sha256(provider_backend) != provider_backend_sha256
            ):
                raise ValueError("provider_snapshot_mismatch")
            bundle_sources["provider_backend"] = provider_backend
            expected_hashes["provider_backend"] = provider_backend_sha256
    runtime = tempfile.TemporaryDirectory(prefix="omc-product-value-adapter-")
    immutable_snapshots: dict[str, Path] = {}
    snapshot_names = {
        "acceptance_runner": "omc_product_value_acceptance.py",
        "arm_adapter": "arm-adapter",
        "scheduler": "omc_n_child_scheduler.py",
        "executor_shadow": "omc_executor_shadow.py",
        "provider_adapter": "provider-adapter",
        "provider_backend": "provider-backend",
    }
    try:
        for name, path in bundle_sources.items():
            snapshot = Path(runtime.name) / snapshot_names[name]
            shutil.copy2(path, snapshot)
            if canonical_file_sha256(snapshot) != expected_hashes[name]:
                raise ValueError(
                    "execution_bundle_mismatch"
                    if execution_bundle is not None
                    else "provider_snapshot_mismatch"
                )
            snapshot.chmod(0o500 if name != "acceptance_runner" else 0o400)
            immutable_snapshots[name] = snapshot
    except (OSError, ValueError):
        runtime.cleanup()
        raise
    adapter = immutable_snapshots["arm_adapter"]
    bundle_snapshots = (
        {
            name: path
            for name, path in immutable_snapshots.items()
            if name != "provider_backend"
        }
        if execution_bundle is not None
        else {}
    )
    capability = _run_bounded_adapter_command(
        [str(adapter), "capabilities"],
        timeout_sec=10,
        max_response_bytes=64 * 1024,
    )
    try:
        payload = json.loads(capability["stdout"])
    except (TypeError, json.JSONDecodeError) as error:
        runtime.cleanup()
        raise ValueError("arm_adapter_capability_invalid") from error
    if (
        capability["returncode"] != 0
        or capability["timed_out"]
        or capability["limit_exceeded"]
        or payload != {
            "protocol": ARM_PROTOCOL,
            "hard_total_token_limit": True,
            "hard_output_limit": True,
            "token_enforcement": ARM_TOKEN_ENFORCEMENT,
            "supported_arms": ["omc", "baseline"],
        }
    ):
        runtime.cleanup()
        raise ValueError("arm_adapter_capability_invalid")

    def execute(**kwargs: Any) -> dict[str, Any]:
        _ = runtime  # Keep the immutable adapter snapshot alive with the closure.
        request = {
            "protocol": ARM_PROTOCOL,
            "arm": kwargs["arm"],
            "packet": kwargs["packet"],
            "provider_snapshot": kwargs["provider_snapshot"],
            "limits": kwargs["limits"],
            "artifact_root": str(kwargs["arm_artifact"].resolve()),
        }
        if bundle_snapshots:
            request["execution_bundle"] = {
                name: str(path) for name, path in bundle_snapshots.items()
            }
        proc = _run_bounded_adapter_command(
            [str(adapter), "execute"],
            cwd=kwargs["workspace"],
            input_text=json.dumps(request, ensure_ascii=False),
            timeout_sec=kwargs["limits"]["max_total_elapsed_sec"],
            max_response_bytes=kwargs["limits"]["max_output_chars"] * 6 + 4096,
            env_overrides={
                "OMC_PROVIDER_BACKEND": str(immutable_snapshots["provider_backend"])
            }
            if "provider_backend" in immutable_snapshots
            else None,
        )
        if proc["timed_out"]:
            return _failed_arm_result("provider_timeout")
        if proc["limit_exceeded"] or proc["returncode"] != 0:
            return _failed_arm_result("provider_failed")
        try:
            result = json.loads(proc["stdout"])
        except json.JSONDecodeError:
            return _failed_arm_result("provider_result_invalid")
        if not isinstance(result, dict):
            return _failed_arm_result("provider_result_invalid")
        return result

    transport_attestation = _frozen_provider_transport_attestation(
        immutable_snapshots.get("provider_adapter"),
        expected_hashes.get("provider_adapter"),
        provider_snapshot.get("backend_sha256"),
        immutable_snapshots.get("provider_backend"),
    )
    return _CapabilityBoundArmExecutor(execute, transport_attestation)


def canonical_file_sha256(
    path: Path,
    *,
    deadline: float | None = None,
    monotonic: Callable[[], float] = time.monotonic,
) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        while True:
            if deadline is not None and monotonic() >= deadline:
                raise ValueError("cache_inventory_timeout")
            chunk = source.read(1024 * 1024)
            if not chunk:
                break
            digest.update(chunk)
    return digest.hexdigest()


def canonical_cache_inventory_sha256(
    root: Path,
    *,
    max_entries: int = CACHE_INVENTORY_MAX_ENTRIES,
    max_total_bytes: int = CACHE_INVENTORY_MAX_BYTES,
    require_readonly: bool = False,
    deadline: float | None = None,
    monotonic: Callable[[], float] = time.monotonic,
) -> str:
    if not root.is_dir() or root.is_symlink():
        raise ValueError("cache_inventory_invalid")
    if require_readonly and stat.S_IMODE(root.stat().st_mode) & (
        stat.S_IWUSR | stat.S_IWGRP | stat.S_IWOTH
    ):
        raise ValueError("cache_inventory_invalid")
    rows: list[dict[str, Any]] = []
    total_bytes = 0
    entry_count = 0
    pending = [root]
    try:
        while pending:
            current = pending.pop()
            with os.scandir(current) as entries:
                for candidate in entries:
                    if deadline is not None and monotonic() >= deadline:
                        raise ValueError("cache_inventory_timeout")
                    entry_count += 1
                    if entry_count > max_entries:
                        raise ValueError("cache_inventory_limit")
                    entry = Path(candidate.path)
                    if candidate.is_symlink():
                        raise ValueError("cache_inventory_invalid")
                    metadata = candidate.stat(follow_symlinks=False)
                    if require_readonly and stat.S_IMODE(metadata.st_mode) & (
                        stat.S_IWUSR | stat.S_IWGRP | stat.S_IWOTH
                    ):
                        raise ValueError("cache_inventory_invalid")
                    relative = entry.relative_to(root).as_posix()
                    if candidate.is_dir(follow_symlinks=False):
                        rows.append({"path": relative, "type": "directory"})
                        pending.append(entry)
                        continue
                    if not candidate.is_file(follow_symlinks=False):
                        raise ValueError("cache_inventory_invalid")
                    total_bytes += metadata.st_size
                    if total_bytes > max_total_bytes:
                        raise ValueError("cache_inventory_limit")
                    rows.append({
                        "path": relative,
                        "type": "file",
                        "size": metadata.st_size,
                        "sha256": canonical_file_sha256(
                            entry,
                            deadline=deadline,
                            monotonic=monotonic,
                        ),
                    })
    except OSError as error:
        raise ValueError("cache_inventory_invalid") from error
    rows.sort(key=lambda row: row["path"])
    return canonical_sha256({
        "schema_version": "omc-product-value-cache-inventory/v1",
        "entries": rows,
        "total_bytes": total_bytes,
    })


def _failed_arm_result(reason_code: str) -> dict[str, Any]:
    return {
        "status": "parent_review",
        "reason_code": reason_code,
        "elapsed_sec": 0.0,
        "output": "",
        "token_usage": {"input_tokens": 0, "output_tokens": 0, "total_tokens": 0},
        "transport_profile": "unknown",
        "intervention_events": [],
        "review_findings": [],
        "duplicate_executions": 0,
        "budget_violations": 0,
    }


def _clock_value(
    monotonic: Callable[[], float],
    *,
    minimum: float,
) -> float:
    value = float(monotonic())
    if not math.isfinite(value) or value < minimum:
        raise ValueError("runner_clock_invalid")
    return value


def _remaining_elapsed(deadline: float, now: float) -> float:
    return max(0.0, deadline - now)


def _mark_arm_failure(
    result: dict[str, Any],
    reason_code: str,
    *,
    budget_violation: bool = False,
) -> dict[str, Any]:
    failed = deepcopy(result)
    failed["status"] = "parent_review"
    failed["reason_code"] = reason_code
    if budget_violation:
        failed["budget_violations"] = max(1, failed["budget_violations"])
    return failed


def _environment_readiness_failure(
    packet: dict[str, Any],
    workspace: Path,
    *,
    timeout_sec: float,
    max_response_bytes: int,
) -> dict[str, Any] | None:
    environment = packet.get("environment_receipt")
    if environment is None:
        return None
    deadline = time.monotonic() + timeout_sec
    try:
        readiness = _run_bounded_adapter_command(
            environment["readiness"]["argv"],
            cwd=workspace,
            timeout_sec=timeout_sec,
            max_response_bytes=max_response_bytes,
        )
    except (OSError, subprocess.SubprocessError):
        return _failed_arm_result("environment_readiness_unavailable")
    if readiness["timed_out"]:
        failure = _failed_arm_result("environment_readiness_timeout")
        failure["budget_violations"] = 1
        return failure
    if readiness["limit_exceeded"]:
        failure = _failed_arm_result("environment_readiness_output_limit")
        failure["budget_violations"] = 1
        return failure
    if readiness["returncode"] != 0:
        return _failed_arm_result("environment_readiness_failed")
    try:
        probe = json.loads(readiness["stdout"])
    except (TypeError, json.JSONDecodeError):
        return _failed_arm_result("environment_readiness_mismatch")
    expected_probe = {
        "schema_version": "omc-product-value-environment-probe/v1",
        "source_commit": environment.get("source_commit"),
        "dependency_lock_sha256": environment.get("dependency_lock_sha256"),
        "cache_sha256": environment.get("cache_sha256"),
        "runtime_identity_sha256": environment.get("runtime_identity_sha256"),
        "cache_path": environment.get("cache_path"),
        "cache_readonly": True,
    }
    if probe != expected_probe:
        return _failed_arm_result("environment_readiness_mismatch")
    measurement_error = _environment_measurement_error(
        environment,
        workspace,
        deadline=deadline,
    )
    if measurement_error == "timeout":
        failure = _failed_arm_result("environment_readiness_timeout")
        failure["budget_violations"] = 1
        return failure
    if measurement_error is not None:
        return _failed_arm_result("environment_readiness_mismatch")
    return None


def _environment_measurement_error(
    environment: dict[str, Any],
    workspace: Path,
    *,
    deadline: float,
    monotonic: Callable[[], float] = time.monotonic,
) -> str | None:
    cache_path = Path(environment["cache_path"])
    try:
        if environment.get("schema_version") == "omc-product-value-environment/v2":
            canonical_cache_inventory_sha256(
                cache_path,
                require_readonly=True,
                deadline=deadline,
                monotonic=monotonic,
            )
            return None
        lock_relative = PurePosixPath(environment["dependency_lock_path"])
        lock_path = workspace.joinpath(*lock_relative.parts)
        runtime_path = Path(environment["runtime_identity_path"])
        if (
            lock_path.is_symlink()
            or not lock_path.is_file()
            or runtime_path.is_symlink()
            or not runtime_path.is_file()
            or canonical_file_sha256(
                lock_path, deadline=deadline, monotonic=monotonic
            )
            != environment["dependency_lock_sha256"]
            or canonical_cache_inventory_sha256(
                cache_path,
                require_readonly=True,
                deadline=deadline,
                monotonic=monotonic,
            )
            != environment["cache_sha256"]
            or canonical_file_sha256(
                runtime_path, deadline=deadline, monotonic=monotonic
            )
            != environment["runtime_identity_sha256"]
        ):
            return "mismatch"
    except ValueError as error:
        return "timeout" if str(error) == "cache_inventory_timeout" else "mismatch"
    except OSError:
        return "mismatch"
    return None


def _acquire_phase_claim(artifact_root: Path, phase: str) -> Any:
    claim = (artifact_root / f".{phase}.lock").open("a+b")
    try:
        fcntl.flock(claim.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError as error:
        claim.close()
        raise ValueError("acceptance_phase_in_progress") from error
    return claim


def _release_phase_claim(claim: Any) -> None:
    try:
        fcntl.flock(claim.fileno(), fcntl.LOCK_UN)
    finally:
        claim.close()


def _run_arm(
    *,
    manifest: dict[str, Any],
    workload: dict[str, Any],
    packet: dict[str, Any],
    registration: dict[str, Any],
    source_root: Path,
    phase_root: Path,
    arm: str,
    arm_executor: Callable[..., dict[str, Any]],
    monotonic: Callable[[], float],
) -> tuple[dict[str, Any], str]:
    transport_attestation = _arm_executor_transport_attestation(arm_executor)
    workload_root = phase_root / workload["workload_id"]
    workload_root.mkdir(parents=True, exist_ok=True)
    arm_artifact = workload_root / f"{arm}-artifacts"
    arm_artifact.mkdir()
    max_elapsed = manifest["execution_contract"]["limits"][
        "max_total_elapsed_sec"
    ]
    execution_started = _clock_value(monotonic, minimum=-math.inf)
    deadline = execution_started + max_elapsed
    with tempfile.TemporaryDirectory(prefix=f"omc-pv-{workload['workload_id']}-{arm}-") as raw:
        workspace = Path(raw) / "workspace"
        workspace_ready = False
        try:
            clone = subprocess.run(
                ["git", "clone", "--no-local", "--quiet", str(source_root), str(workspace)],
                check=False,
                capture_output=True,
                text=True,
                timeout=max_elapsed,
            )
        except subprocess.TimeoutExpired:
            clone = None
            raw_result = _failed_arm_result("source_clone_timeout")
            raw_result["budget_violations"] = 1
        clone_finished = _clock_value(monotonic, minimum=execution_started)
        if clone is not None and clone.returncode != 0:
            raw_result = _failed_arm_result("source_clone_failed")
        elif clone is not None:
            checkout_budget = _remaining_elapsed(deadline, clone_finished)
            if checkout_budget <= 0:
                raw_result = _failed_arm_result("source_checkout_timeout")
                raw_result["budget_violations"] = 1
            else:
                try:
                    checkout = _git(
                        workspace,
                        "checkout",
                        "--quiet",
                        "--detach",
                        workload["source_commit"],
                        timeout=checkout_budget,
                    )
                except subprocess.TimeoutExpired:
                    raw_result = _failed_arm_result("source_checkout_timeout")
                    raw_result["budget_violations"] = 1
                else:
                    if checkout.returncode != 0:
                        raw_result = _failed_arm_result("source_checkout_failed")
                    else:
                        workspace_ready = True
        execution_ready = workspace_ready
        if execution_ready and packet.get("environment_receipt") is not None:
            readiness_started = _clock_value(monotonic, minimum=clone_finished)
            readiness_budget = _remaining_elapsed(deadline, readiness_started)
            if readiness_budget <= 0:
                raw_result = _failed_arm_result("environment_readiness_timeout")
                raw_result["budget_violations"] = 1
                execution_ready = False
            else:
                readiness_failure = _environment_readiness_failure(
                    packet,
                    workspace,
                    timeout_sec=readiness_budget,
                    max_response_bytes=manifest["execution_contract"]["limits"][
                        "max_output_chars"
                    ],
                )
                if readiness_failure is not None:
                    raw_result = readiness_failure
                    execution_ready = False
        provider_started = _clock_value(monotonic, minimum=clone_finished)
        arm_limits = deepcopy(manifest["execution_contract"]["limits"])
        arm_limits["max_total_elapsed_sec"] = _remaining_elapsed(
            deadline, provider_started
        )
        if execution_ready and arm_limits["max_total_elapsed_sec"] <= 0:
            raw_result = _failed_arm_result("execution_budget_exhausted")
            raw_result["budget_violations"] = 1
        elif execution_ready:
            try:
                raw_result = _validated_result(
                    arm_executor(
                        arm=arm,
                        packet=deepcopy(packet),
                        workspace=workspace,
                        arm_artifact=arm_artifact,
                        provider_snapshot=deepcopy(
                            manifest["execution_contract"]["provider_snapshot"]
                        ),
                        limits=arm_limits,
                        expected_child_count=workload["expected_child_count"],
                    ),
                    arm_limits,
                )
            except Exception:
                raw_result = _failed_arm_result("provider_result_invalid")
            if (
                packet.get("environment_receipt") is not None
                and raw_result["status"] == "completed"
                and raw_result.get("environment_receipt_sha256")
                != workload["environment_receipt_sha256"]
            ):
                raw_result = _mark_arm_failure(
                    raw_result,
                    "provider_environment_attestation_mismatch",
                )
        if (
            execution_ready
            and raw_result["status"] == "completed"
            and packet.get("environment_receipt") is not None
        ):
            postcheck_error = _environment_measurement_error(
                packet["environment_receipt"],
                workspace,
                deadline=deadline,
                monotonic=monotonic,
            )
            if postcheck_error is not None:
                raw_result = _mark_arm_failure(
                    raw_result,
                    "environment_changed_after_execution",
                    budget_violation=postcheck_error == "timeout",
                )
        provider_finished = _clock_value(monotonic, minimum=provider_started)
        scheduler_evidence_verified = False
        if arm == "omc" and raw_result["status"] == "completed":
            dag_ledger = arm_artifact / "dag-ledger.json"
            child_ledger = arm_artifact / "child-ledger.json"
            if (
                raw_result.get("executed_child_count")
                != workload["expected_child_count"]
                or not _is_sha256(raw_result.get("dag_ledger_sha256"))
                or not _is_sha256(raw_result.get("child_ledger_sha256"))
                or not dag_ledger.is_file()
                or not child_ledger.is_file()
                or canonical_file_sha256(dag_ledger)
                != raw_result["dag_ledger_sha256"]
                or canonical_file_sha256(child_ledger)
                != raw_result["child_ledger_sha256"]
            ):
                raise ValueError("omc_scheduler_evidence_invalid")
            scheduler_evidence_verified = True
        remaining_elapsed = _remaining_elapsed(deadline, provider_finished)
        verification_returncode = 125
        verification_stdout = ""
        verification_stderr = "verification skipped because execution did not complete"
        if raw_result["status"] == "completed" and remaining_elapsed > 0:
            verification = _run_bounded_adapter_command(
                packet["verification"]["argv"],
                cwd=workspace,
                timeout_sec=remaining_elapsed,
                max_response_bytes=manifest["execution_contract"]["limits"][
                    "max_output_chars"
                ],
            )
            verification_returncode = verification["returncode"]
            verification_stdout = verification["stdout"]
            verification_stderr = verification["stderr"]
            if verification["timed_out"]:
                verification_returncode = 124
            if verification["limit_exceeded"]:
                raw_result = _mark_arm_failure(
                    raw_result,
                    "verification_output_limit_exceeded",
                    budget_violation=True,
                )
                scheduler_evidence_verified = False
        elif raw_result["status"] == "completed":
            verification_returncode = 124
            verification_stderr = "verification skipped because total elapsed budget was exhausted"
        diff_started = _clock_value(monotonic, minimum=provider_finished)
        changed_paths: list[str] = []
        diff_budget = _remaining_elapsed(deadline, diff_started)
        if workspace_ready and diff_budget > 0:
            try:
                changed_paths = _changed_paths(workspace, timeout=diff_budget)
            except subprocess.TimeoutExpired:
                raw_result = _mark_arm_failure(
                    raw_result,
                    "workspace_diff_timeout",
                    budget_violation=True,
                )
                scheduler_evidence_verified = False
            except ValueError:
                raw_result = _mark_arm_failure(
                    raw_result,
                    "workspace_diff_unavailable",
                )
                scheduler_evidence_verified = False
        elif workspace_ready:
            raw_result = _mark_arm_failure(
                raw_result,
                "workspace_diff_timeout",
                budget_violation=True,
            )
            scheduler_evidence_verified = False
        execution_finished = _clock_value(monotonic, minimum=diff_started)
        measured_elapsed = execution_finished - execution_started
        if measured_elapsed > max_elapsed:
            raw_result["budget_violations"] = max(
                1, raw_result["budget_violations"]
            )
    raw_output_path = arm_artifact / "raw-output.txt"
    verification_stdout_path = arm_artifact / "verification-stdout.txt"
    verification_stderr_path = arm_artifact / "verification-stderr.txt"
    raw_output_path.write_text(raw_result["output"], encoding="utf-8")
    verification_stdout_path.write_text(verification_stdout, encoding="utf-8")
    verification_stderr_path.write_text(verification_stderr, encoding="utf-8")
    scope_violations = [
        path for path in changed_paths if not _in_scope(path, workload["scope_paths"])
    ]
    critical_or_major = sum(
        finding.get("severity") in {"critical", "major"}
        for finding in raw_result["review_findings"]
    )
    allowed_events = set(manifest["intervention_contract"]["included_events"]) | set(
        manifest["intervention_contract"]["excluded_events"]
    )
    if any(event not in allowed_events for event in raw_result["intervention_events"]):
        raise ValueError("arm_intervention_event_unknown")
    included_events = set(manifest["intervention_contract"]["included_events"])
    receipt = {
        "schema_version": TELEMETRY_SCHEMA_VERSION,
        "manifest_sha256": manifest["preregistration_sha256"],
        "registration_receipt_sha256": registration["registration_receipt_sha256"],
        "workload_id": workload["workload_id"],
        "pair_id": workload["pair_id"],
        "execution_packet_sha256": workload["execution_packet_sha256"],
        "arm": arm,
        "status": raw_result["status"],
        "reason_code": raw_result["reason_code"],
        "elapsed_sec": measured_elapsed,
        "provider_reported_elapsed_sec": float(raw_result["elapsed_sec"]),
        "output_sha256": canonical_file_sha256(raw_output_path),
        "token_usage": raw_result["token_usage"],
        "transport_profile": transport_attestation["profile"],
        "transport_capability_attestation": transport_attestation,
        "intervention_events": raw_result["intervention_events"],
        "intervention_count": sum(
            event in included_events for event in raw_result["intervention_events"]
        ),
        "review_findings": raw_result["review_findings"],
        "critical_or_major_review_findings": critical_or_major,
        "duplicate_executions": raw_result["duplicate_executions"],
        "budget_violations": raw_result["budget_violations"],
        "changed_paths": changed_paths,
        "scope_violations": scope_violations,
        "verification": {
            "passed": verification_returncode == 0,
            "returncode": verification_returncode,
            "stdout_sha256": canonical_file_sha256(verification_stdout_path),
            "stderr_sha256": canonical_file_sha256(verification_stderr_path),
        },
    }
    if arm == "omc":
        receipt["scheduler_evidence_status"] = (
            "verified" if scheduler_evidence_verified else "unavailable"
        )
        if scheduler_evidence_verified:
            receipt.update({
                "executed_child_count": raw_result["executed_child_count"],
                "dag_ledger_sha256": raw_result["dag_ledger_sha256"],
                "child_ledger_sha256": raw_result["child_ledger_sha256"],
            })
    if packet.get("environment_receipt") is not None:
        receipt["environment_receipt_sha256"] = workload[
            "environment_receipt_sha256"
        ]
    receipt["success"] = (
        receipt["status"] == "completed"
        and receipt["verification"]["passed"]
        and not receipt["scope_violations"]
        and receipt["budget_violations"] == 0
        and receipt["duplicate_executions"] == 0
        and receipt["critical_or_major_review_findings"] == 0
    )
    digest = _write_envelope(workload_root / f"{arm}.json", receipt)
    return receipt, digest


def _phase_workloads(manifest: dict[str, Any], phase: str) -> list[dict[str, Any]]:
    role = "pilot" if phase == "pilot" else "confirmatory"
    return [item for item in manifest["workloads"] if item["evaluation_role"] == role]


def _acceptance_schema_version(manifest: dict[str, Any]) -> str:
    return (
        SCHEMA_VERSION_V2
        if manifest["schema_version"] in {
            preregistration.SCHEMA_VERSION_V4,
            preregistration.SCHEMA_VERSION_V5,
        }
        else SCHEMA_VERSION
    )


def _validate_pilot_gate(
    manifest: dict[str, Any],
    artifact_root: Path,
    registration_receipt_sha256: str | None = None,
) -> None:
    pilot, _ = _load_envelope(artifact_root / "pilot" / "index.json")
    if (
        pilot.get("manifest_sha256") != manifest["preregistration_sha256"]
        or pilot.get("status") != "pilot_passed"
        or pilot.get("workload_count") != 1
        or (
            registration_receipt_sha256 is not None
            and pilot.get("registration_receipt_sha256")
            != registration_receipt_sha256
        )
    ):
        raise ValueError("pilot_blocked")
    receipts = _load_phase_receipts(manifest, artifact_root, "pilot")
    if len(receipts) != 2 or any(receipt.get("success") is not True for receipt in receipts):
        raise ValueError("pilot_blocked")


def _bind_registration_gate(
    manifest: dict[str, Any],
    artifact_root: Path,
    registration: dict[str, Any],
) -> None:
    path = artifact_root / "registration-gate.json"
    if path.exists():
        stored, _ = _load_envelope(path)
        if stored != registration:
            raise ValueError("registration_gate_mismatch")
        return
    if artifact_root.exists() and any(artifact_root.iterdir()):
        raise ValueError("registration_gate_missing")
    artifact_root.mkdir(parents=True, exist_ok=True)
    _write_envelope(path, registration)


def run_product_value_phase(
    manifest: dict[str, Any],
    registration_context: dict[str, Any],
    *,
    packet_root: str | Path,
    source_roots: dict[str, Any],
    artifact_root: str | Path,
    phase: str,
    arm_executor: Callable[..., dict[str, Any]],
    registration_validator: Callable[..., dict[str, Any]] = _default_registration_validator,
    monotonic: Callable[[], float] = time.monotonic,
) -> dict[str, Any]:
    manifest = _validate_acceptance_manifest(manifest)
    if phase not in {"pilot", "confirmatory"}:
        raise ValueError("acceptance_phase_invalid")
    registration = _registration_gate(
        manifest, registration_context, registration_validator
    )
    packet_root = Path(packet_root).resolve()
    artifact_root = Path(artifact_root).resolve()
    _bind_registration_gate(manifest, artifact_root, registration)
    if phase == "confirmatory":
        _validate_pilot_gate(
            manifest,
            artifact_root,
            registration["registration_receipt_sha256"],
        )
    phase_claim = _acquire_phase_claim(artifact_root, phase)
    phase_root = artifact_root / phase
    if phase_root.exists():
        _release_phase_claim(phase_claim)
        raise ValueError("acceptance_phase_already_executed")
    try:
        staging_root = Path(
            tempfile.mkdtemp(prefix=f".{phase}-", dir=artifact_root)
        )
    except Exception:
        _release_phase_claim(phase_claim)
        raise
    try:
        workload_receipts: list[dict[str, Any]] = []
        for workload in _phase_workloads(manifest, phase):
            packet = validate_execution_packet(
                manifest,
                workload,
                _load_json(packet_root / f"{workload['workload_id']}.json"),
            )
            source_root = _source_root(workload, source_roots)
            arm_receipts: dict[str, str] = {}
            arm_success: dict[str, bool] = {}
            for arm in workload["execution_order"]:
                receipt, digest = _run_arm(
                    manifest=manifest,
                    workload=workload,
                    packet=packet,
                    registration=registration,
                    source_root=source_root,
                    phase_root=staging_root,
                    arm=arm,
                    arm_executor=arm_executor,
                    monotonic=monotonic,
                )
                arm_receipts[arm] = digest
                arm_success[arm] = receipt["success"]
            workload_receipts.append({
                "workload_id": workload["workload_id"],
                "pair_id": workload["pair_id"],
                "execution_order": workload["execution_order"],
                "arm_receipt_sha256s": arm_receipts,
                "pair_success": all(arm_success.values()),
            })
        all_passed = all(item["pair_success"] for item in workload_receipts)
        status = (
            "pilot_passed"
            if phase == "pilot" and all_passed
            else "pilot_blocked"
            if phase == "pilot"
            else "confirmatory_completed"
            if all_passed
            else "confirmatory_completed_with_failures"
        )
        result = {
            "schema_version": _acceptance_schema_version(manifest),
            "phase": phase,
            "status": status,
            "manifest_sha256": manifest["preregistration_sha256"],
            "registration_receipt_sha256": registration[
                "registration_receipt_sha256"
            ],
            "workload_count": len(workload_receipts),
            "workloads": workload_receipts,
        }
        result["phase_receipt_sha256"] = _write_envelope(
            staging_root / "index.json", result
        )
        staging_root.replace(phase_root)
        return result
    finally:
        if staging_root.exists():
            shutil.rmtree(staging_root)
        _release_phase_claim(phase_claim)


def _load_phase_receipts(
    manifest: dict[str, Any], artifact_root: Path, phase: str
) -> list[dict[str, Any]]:
    index, _ = _load_envelope(artifact_root / phase / "index.json")
    expected = _phase_workloads(manifest, phase)
    expected_ids = [item["workload_id"] for item in expected]
    listed = index.get("workloads") if isinstance(index, dict) else None
    if (
        index.get("schema_version") != _acceptance_schema_version(manifest)
        or index.get("manifest_sha256") != manifest["preregistration_sha256"]
        or not isinstance(listed, list)
        or [item.get("workload_id") for item in listed] != expected_ids
    ):
        raise ValueError("artifact_catalog_mismatch")
    receipts: list[dict[str, Any]] = []
    for workload, listed_workload in zip(expected, listed):
        hashes = listed_workload.get("arm_receipt_sha256s")
        if not isinstance(hashes, dict) or set(hashes) != set(ARMS):
            raise ValueError("artifact_catalog_mismatch")
        for arm in ARMS:
            receipt, digest = _load_envelope(
                artifact_root / phase / workload["workload_id"] / f"{arm}.json"
            )
            if (
                digest != hashes[arm]
                or receipt.get("manifest_sha256")
                != manifest["preregistration_sha256"]
                or receipt.get("workload_id") != workload["workload_id"]
                or receipt.get("pair_id") != workload["pair_id"]
                or receipt.get("arm") != arm
                or receipt.get("execution_packet_sha256")
                != workload["execution_packet_sha256"]
            ):
                raise ValueError("artifact_binding_mismatch")
            _validate_environment_receipt_binding(manifest, workload, receipt)
            arm_artifact = (
                artifact_root / phase / workload["workload_id"] / f"{arm}-artifacts"
            )
            raw_output = arm_artifact / "raw-output.txt"
            verification_stdout = arm_artifact / "verification-stdout.txt"
            verification_stderr = arm_artifact / "verification-stderr.txt"
            if (
                not raw_output.is_file()
                or not verification_stdout.is_file()
                or not verification_stderr.is_file()
                or canonical_file_sha256(raw_output) != receipt.get("output_sha256")
                or canonical_file_sha256(verification_stdout)
                != receipt.get("verification", {}).get("stdout_sha256")
                or canonical_file_sha256(verification_stderr)
                != receipt.get("verification", {}).get("stderr_sha256")
            ):
                raise ValueError("artifact_raw_evidence_mismatch")
            if arm == "omc":
                if receipt.get("status") == "completed":
                    if receipt.get("scheduler_evidence_status") != "verified":
                        raise ValueError("artifact_scheduler_evidence_mismatch")
                    for name, field in (
                        ("dag-ledger.json", "dag_ledger_sha256"),
                        ("child-ledger.json", "child_ledger_sha256"),
                    ):
                        ledger = arm_artifact / name
                        if (
                            not ledger.is_file()
                            or canonical_file_sha256(ledger) != receipt.get(field)
                        ):
                            raise ValueError("artifact_scheduler_evidence_mismatch")
                elif (
                    receipt.get("scheduler_evidence_status") != "unavailable"
                    or any(
                        field in receipt
                        for field in (
                            "executed_child_count",
                            "dag_ledger_sha256",
                            "child_ledger_sha256",
                        )
                    )
                ):
                    raise ValueError("artifact_scheduler_evidence_mismatch")
            receipts.append(receipt)
    return receipts


def _validate_environment_receipt_binding(
    manifest: dict[str, Any],
    workload: dict[str, Any],
    receipt: dict[str, Any],
) -> None:
    if (
        manifest["schema_version"] in {
            preregistration.SCHEMA_VERSION_V4,
            preregistration.SCHEMA_VERSION_V5,
        }
        and receipt.get("environment_receipt_sha256")
        != workload["environment_receipt_sha256"]
    ):
        raise ValueError("artifact_environment_binding_mismatch")


def _arm_metrics(receipts: list[dict[str, Any]], arm: str) -> dict[str, Any]:
    selected = [item for item in receipts if item["arm"] == arm]
    return {
        "success_rate": sum(item["success"] for item in selected) / len(selected),
        "median_total_tokens": statistics.median(
            item["token_usage"]["total_tokens"] for item in selected
        ),
        "median_elapsed_sec": statistics.median(
            item["elapsed_sec"] for item in selected
        ),
        "intervention_count": sum(item["intervention_count"] for item in selected),
        "scope_violations": sum(len(item["scope_violations"]) for item in selected),
        "budget_violations": sum(item["budget_violations"] for item in selected),
        "duplicate_executions": sum(item["duplicate_executions"] for item in selected),
        "critical_or_major_review_findings": sum(
            item["critical_or_major_review_findings"] for item in selected
        ),
    }


def build_product_value_verdicts(
    checks: dict[str, bool], *, strict_transport_eligible: bool
) -> dict[str, str]:
    if (
        not isinstance(checks, dict)
        or not checks
        or not all(isinstance(value, bool) for value in checks.values())
        or not isinstance(strict_transport_eligible, bool)
    ):
        raise ValueError("acceptance_verdict_contract_invalid")
    operational_passed = all(checks.values())
    strict_certified = operational_passed and strict_transport_eligible
    return {
        "operational_verdict": (
            "OPERATIONALLY_REPLACEABLE"
            if operational_passed
            else "NOT_REPLACEABLE"
        ),
        "strict_certification_verdict": (
            "STRICTLY_CERTIFIED"
            if strict_certified
            else "HOLD_TRANSPORT"
            if operational_passed
            else "NOT_CERTIFIED"
        ),
        # Preserve the v1 consumer contract while making its strict meaning explicit.
        "verdict": "PASS" if strict_certified else "FAIL",
    }


def _strict_transport_receipt_eligible(
    manifest: dict[str, Any], receipt: dict[str, Any]
) -> bool:
    expected_adapter_sha256 = (
        manifest["execution_contract"]
        .get("execution_bundle", {})
        .get("provider_adapter_sha256")
    )
    expected_backend_sha256 = manifest["execution_contract"][
        "provider_snapshot"
    ].get("backend_sha256")
    attestation = receipt.get("transport_capability_attestation")
    return bool(
        _is_sha256(expected_adapter_sha256)
        and isinstance(attestation, dict)
        and attestation.get("profile") == "provider_enforced"
        and attestation.get("adapter_sha256") == expected_adapter_sha256
        and _is_sha256(expected_backend_sha256)
        and attestation.get("backend_sha256") == expected_backend_sha256
        and _is_sha256(attestation.get("capabilities_sha256"))
        and receipt.get("transport_profile") == "provider_enforced"
    )


def finalize_product_value_acceptance(
    manifest: dict[str, Any],
    artifact_root: str | Path,
) -> dict[str, Any]:
    manifest = _validate_acceptance_manifest(manifest)
    artifact_root = Path(artifact_root).resolve()
    registration, _ = _load_envelope(artifact_root / "registration-gate.json")
    if (
        registration.get("claim_eligible") is not True
        or registration.get("status") != "registered"
        or registration.get("preregistration_sha256")
        != manifest["preregistration_sha256"]
        or not _is_sha256(registration.get("registration_receipt_sha256"))
    ):
        raise ValueError("registration_gate_mismatch")
    _validate_pilot_gate(manifest, artifact_root)
    _load_phase_receipts(manifest, artifact_root, "pilot")
    receipts = _load_phase_receipts(manifest, artifact_root, "confirmatory")
    if len(receipts) != 10:
        raise ValueError("confirmatory_receipt_count_invalid")
    metrics = {arm: _arm_metrics(receipts, arm) for arm in ARMS}
    omc = metrics["omc"]
    baseline = metrics["baseline"]
    if baseline["intervention_count"] == 0:
        intervention_ratio = 0.0 if omc["intervention_count"] == 0 else math.inf
    else:
        intervention_ratio = (
            omc["intervention_count"] / baseline["intervention_count"]
        )
    thresholds = manifest["thresholds"]
    checks = {
        "all_confirmatory_arms_succeeded": all(
            receipt.get("success") is True for receipt in receipts
        ),
        "success_rate": omc["success_rate"] >= baseline["success_rate"],
        "median_total_tokens": (
            omc["median_total_tokens"] < baseline["median_total_tokens"]
        ),
        "median_elapsed": omc["median_elapsed_sec"] <= baseline["median_elapsed_sec"],
        "intervention_ratio": intervention_ratio <= thresholds["intervention_ratio_max"],
        "scope_violations": omc["scope_violations"] <= thresholds["max_scope_violations"],
        "budget_violations": omc["budget_violations"] <= thresholds["max_budget_violations"],
        "duplicate_executions": (
            omc["duplicate_executions"] <= thresholds["max_duplicate_executions"]
        ),
        "additional_critical_or_major_review_findings": (
            omc["critical_or_major_review_findings"]
            - baseline["critical_or_major_review_findings"]
            <= thresholds["max_additional_critical_or_major_review_findings"]
        ),
    }
    verdicts = build_product_value_verdicts(
        checks,
        strict_transport_eligible=all(
            _strict_transport_receipt_eligible(manifest, receipt)
            for receipt in receipts
        ),
    )
    report = {
        "schema_version": _acceptance_schema_version(manifest),
        "status": "finalized",
        "manifest_sha256": manifest["preregistration_sha256"],
        "registration_receipt_sha256": registration[
            "registration_receipt_sha256"
        ],
        "confirmatory_pair_count": 5,
        "metrics": metrics,
        "intervention_ratio": intervention_ratio,
        "checks": checks,
        **verdicts,
    }
    report["report_sha256"] = canonical_sha256(report)
    return report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Run manifest-bound Product Value paired acceptance."
    )
    parser.add_argument(
        "command", choices=("validate", "run-pilot", "run-confirmatory", "finalize")
    )
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--registration-context", type=Path)
    parser.add_argument("--packet-root", type=Path)
    parser.add_argument("--source-roots", type=Path)
    parser.add_argument("--artifact-root", type=Path)
    parser.add_argument("--arm-adapter", type=Path)
    parser.add_argument("--scheduler", type=Path)
    parser.add_argument("--executor-shadow", type=Path)
    parser.add_argument("--provider-adapter", type=Path)
    parser.add_argument("--out", type=Path)
    args = parser.parse_args(argv)
    try:
        manifest = _validate_acceptance_manifest(_load_json(args.manifest))
        if args.command == "validate":
            result = {
                "status": "ready",
                "preregistration_sha256": manifest["preregistration_sha256"],
            }
        elif args.command == "finalize":
            if args.artifact_root is None:
                raise ValueError("artifact_root_required")
            result = finalize_product_value_acceptance(manifest, args.artifact_root)
        else:
            if any(
                value is None
                for value in (
                    args.registration_context,
                    args.packet_root,
                    args.source_roots,
                    args.artifact_root,
                    args.arm_adapter,
                )
            ):
                raise ValueError("acceptance_execution_input_required")
            contract = manifest["execution_contract"]
            is_v4 = manifest["schema_version"] in {
                preregistration.SCHEMA_VERSION_V4,
                preregistration.SCHEMA_VERSION_V5,
            }
            if is_v4 and (
                args.scheduler is None
                or args.executor_shadow is None
                or args.provider_adapter is None
            ):
                raise ValueError("acceptance_execution_bundle_input_required")
            executor = build_process_arm_executor(
                args.arm_adapter,
                contract["provider_snapshot"],
                adapter_sha256=(
                    contract["execution_bundle"]["arm_adapter_sha256"]
                    if is_v4
                    else None
                ),
                execution_bundle=contract.get("execution_bundle") if is_v4 else None,
                scheduler_path=args.scheduler,
                executor_shadow_path=args.executor_shadow,
                provider_adapter_path=args.provider_adapter,
            )
            result = run_product_value_phase(
                manifest,
                _load_json(args.registration_context),
                packet_root=args.packet_root,
                source_roots=_load_json(args.source_roots),
                artifact_root=args.artifact_root,
                phase=("pilot" if args.command == "run-pilot" else "confirmatory"),
                arm_executor=executor,
            )
        if args.out is not None:
            _write_json(args.out, result)
        print(json.dumps(result, ensure_ascii=False, sort_keys=True))
        return 0 if result.get("status") not in {"pilot_blocked"} else 2
    except (OSError, ValueError, subprocess.SubprocessError) as error:
        print(
            json.dumps(
                {"status": "blocked", "reason_code": str(error)},
                ensure_ascii=False,
                sort_keys=True,
            )
        )
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
