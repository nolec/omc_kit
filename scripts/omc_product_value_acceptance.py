#!/usr/bin/env python3
"""Run manifest-bound Product Value pilot and paired acceptance evidence."""

from __future__ import annotations

import argparse
from copy import deepcopy
import fcntl
import hashlib
import json
import math
from pathlib import Path, PurePosixPath
import shutil
import statistics
import subprocess
import tempfile
import time
from typing import Any, Callable

import omc_product_value_preregistration as preregistration
from omc_n_child_scheduler import _run_bounded_adapter_command


SCHEMA_VERSION = "omc-product-value-acceptance/v1"
PACKET_SCHEMA_VERSION = "omc-product-value-execution-packet/v1"
TELEMETRY_SCHEMA_VERSION = "omc-product-value-telemetry/v1"
ARM_PROTOCOL = "omc-product-value-arm/v1"
ARMS = ("omc", "baseline")
REVIEW_SEVERITIES = {"critical", "major", "minor", "suggestion"}


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


def _validate_v3_manifest(payload: Any) -> dict[str, Any]:
    manifest = preregistration.validate_preregistration(payload)
    if manifest.get("schema_version") != preregistration.SCHEMA_VERSION_V3:
        raise ValueError("acceptance_requires_preregistration_v3")
    if manifest["execution_contract"]["runner_schema"] != SCHEMA_VERSION:
        raise ValueError("acceptance_runner_schema_mismatch")
    if manifest["execution_contract"]["telemetry_schema"] != TELEMETRY_SCHEMA_VERSION:
        raise ValueError("acceptance_telemetry_schema_mismatch")
    return manifest


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
    manifest = _validate_v3_manifest(manifest)
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
    if (
        usage["total_tokens"] > limits["max_total_tokens"]
        or result["elapsed_sec"] > limits["max_total_elapsed_sec"]
        or len(result["output"]) > limits["max_output_chars"]
    ):
        bounded["budget_violations"] += 1
    return bounded


def build_process_arm_executor(
    adapter_path: str | Path,
    provider_snapshot: dict[str, Any],
) -> Callable[..., dict[str, Any]]:
    source = Path(adapter_path).expanduser().resolve()
    if not source.is_file() or canonical_file_sha256(source) != provider_snapshot["adapter_sha256"]:
        raise ValueError("provider_snapshot_mismatch")
    runtime = tempfile.TemporaryDirectory(prefix="omc-product-value-adapter-")
    adapter = Path(runtime.name) / "arm-adapter"
    shutil.copy2(source, adapter)
    adapter.chmod(0o500)
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
        proc = _run_bounded_adapter_command(
            [str(adapter), "execute"],
            cwd=kwargs["workspace"],
            input_text=json.dumps(request, ensure_ascii=False),
            timeout_sec=kwargs["limits"]["max_total_elapsed_sec"],
            max_response_bytes=kwargs["limits"]["max_output_chars"] * 6 + 4096,
        )
        if proc["timed_out"]:
            return _failed_arm_result("provider_timeout")
        if proc["limit_exceeded"] or proc["returncode"] != 0:
            return _failed_arm_result("provider_failed")
        try:
            result = json.loads(proc["stdout"])
        except json.JSONDecodeError:
            return _failed_arm_result("provider_result_invalid")
        return result

    return execute


def canonical_file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _failed_arm_result(reason_code: str) -> dict[str, Any]:
    return {
        "status": "parent_review",
        "reason_code": reason_code,
        "elapsed_sec": 0.0,
        "output": "",
        "token_usage": {"input_tokens": 0, "output_tokens": 0, "total_tokens": 0},
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
        provider_started = _clock_value(monotonic, minimum=clone_finished)
        arm_limits = deepcopy(manifest["execution_contract"]["limits"])
        arm_limits["max_total_elapsed_sec"] = _remaining_elapsed(
            deadline, provider_started
        )
        if workspace_ready and arm_limits["max_total_elapsed_sec"] <= 0:
            raw_result = _failed_arm_result("execution_budget_exhausted")
            raw_result["budget_violations"] = 1
        elif workspace_ready:
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
            elif verification["limit_exceeded"]:
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
    manifest = _validate_v3_manifest(manifest)
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
            "schema_version": SCHEMA_VERSION,
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
        index.get("manifest_sha256") != manifest["preregistration_sha256"]
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


def finalize_product_value_acceptance(
    manifest: dict[str, Any], artifact_root: str | Path
) -> dict[str, Any]:
    manifest = _validate_v3_manifest(manifest)
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
    report = {
        "schema_version": SCHEMA_VERSION,
        "status": "finalized",
        "manifest_sha256": manifest["preregistration_sha256"],
        "registration_receipt_sha256": registration[
            "registration_receipt_sha256"
        ],
        "confirmatory_pair_count": 5,
        "metrics": metrics,
        "intervention_ratio": intervention_ratio,
        "checks": checks,
        "verdict": "PASS" if all(checks.values()) else "FAIL",
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
    parser.add_argument("--out", type=Path)
    args = parser.parse_args(argv)
    try:
        manifest = _validate_v3_manifest(_load_json(args.manifest))
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
            executor = build_process_arm_executor(
                args.arm_adapter,
                manifest["execution_contract"]["provider_snapshot"],
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
