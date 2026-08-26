#!/usr/bin/env python3
"""Fail-closed contracts for durable Autopilot task workflows."""

from __future__ import annotations

import hashlib
import json
import os
import re
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


SCHEMA_VERSION = "omc-autopilot-task/v2"
APPROVAL_REQUIRED_EXIT_CODE = 75
_VALIDATORS = {"artifact_sha256", "json_object_fields"}
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
_STEP_RUNTIME_FIELDS = {
    "executor",
    "task_kind",
    "domain",
    "model_profile",
    "routing_policy",
    "policy_profile",
    "routing_reason_codes",
    "routing_reason_summary",
    "recommended_next_skill",
    "recommended_policy_profile",
    "policy_reason_summary",
    "policy_confidence",
    "user_selection_needed",
    "auto_execution_allowed",
    "comparison_id",
    "variant",
    "environment_fingerprint",
    "elapsed_ms",
    "duration_ms",
    "started_at",
    "finished_at",
    "failed_at",
    "provider_call_count",
    "tool_call_count",
    "tool_call_measurement_status",
    "timeout_sec",
    "skill_path",
    "skill_count",
    "failure_category",
    "provider_exit_status",
    "cost_metadata_status",
}


def _canonical_json(value: Any) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")


def task_spec_sha256(task: dict[str, Any]) -> str:
    return hashlib.sha256(_canonical_json(task)).hexdigest()


def validate_task_spec(task: object) -> list[str]:
    if not isinstance(task, dict):
        return ["task_not_object"]
    if task.get("schema_version") != SCHEMA_VERSION:
        return ["schema_version_invalid"]
    errors: list[str] = []
    task_id = str(task.get("id") or "").strip()
    if not task_id:
        errors.append("id_missing")
    elif not _ID_RE.fullmatch(task_id):
        errors.append("id_invalid")
    steps = task.get("steps")
    if not isinstance(steps, list) or not steps:
        return errors + ["steps_missing"]

    ids: list[str] = []
    for index, step in enumerate(steps):
        if not isinstance(step, dict):
            errors.append(f"steps.{index}.not_object")
            continue
        step_id = str(step.get("id") or "").strip()
        if not step_id:
            errors.append(f"steps.{index}.id_missing")
            continue
        if not _ID_RE.fullmatch(step_id):
            errors.append(f"steps.{index}.id_invalid")
            continue
        ids.append(step_id)
        if not str(step.get("prompt") or "").strip():
            errors.append(f"steps.{step_id}.prompt_missing")
        completion = step.get("completion")
        if not isinstance(completion, dict):
            errors.append(f"steps.{step_id}.completion_missing")
            continue
        validator_id = str(completion.get("validator_id") or "")
        if validator_id not in _VALIDATORS:
            errors.append(f"steps.{step_id}.completion.validator_id_unknown")
        output_path = str(completion.get("output_path") or "").strip()
        if not output_path:
            errors.append(f"steps.{step_id}.completion.output_path_missing")
        elif Path(output_path).is_absolute() or ".." in Path(output_path).parts:
            errors.append(f"steps.{step_id}.completion.output_path_unsafe")
        if validator_id == "artifact_sha256" and not _SHA256_RE.fullmatch(
            str(completion.get("expected_sha256") or "")
        ):
            errors.append(f"steps.{step_id}.completion.expected_sha256_invalid")
        if validator_id == "json_object_fields":
            fields = completion.get("required_fields")
            if not isinstance(fields, list) or not fields or not all(isinstance(field, str) and field for field in fields):
                errors.append(f"steps.{step_id}.completion.required_fields_invalid")
        gate = step.get("approval_gate")
        if gate is not None:
            if not isinstance(gate, dict):
                errors.append(f"steps.{step_id}.approval_gate_invalid")
            else:
                if not str(gate.get("approval_id") or "").strip():
                    errors.append(f"steps.{step_id}.approval_id_missing")
                if not _SHA256_RE.fullmatch(str(gate.get("payload_sha256") or "")):
                    errors.append(f"steps.{step_id}.approval_payload_sha256_invalid")

    if len(ids) != len(set(ids)):
        errors.append("step_ids_not_unique")
    known_ids = set(ids)
    for step in steps:
        if not isinstance(step, dict):
            continue
        step_id = str(step.get("id") or "")
        dependencies = step.get("depends_on", [])
        if not isinstance(dependencies, list) or any(dep not in known_ids for dep in dependencies):
            errors.append(f"steps.{step_id}.depends_on_invalid")
    return errors


def _approval_path(root: Path, task_id: str, step_id: str) -> Path:
    return root / ".omc" / "state" / "autopilot" / "approvals" / task_id / f"{step_id}.json"


def _atomic_write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(
        mode="w", encoding="utf-8", dir=path.parent, prefix=f"{path.name}.", suffix=".tmp", delete=False
    ) as handle:
        handle.write(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True))
        handle.flush()
        os.fsync(handle.fileno())
        temporary = Path(handle.name)
    os.replace(temporary, path)


def write_approval_receipt(
    root: Path,
    *,
    task_id: str,
    task_spec_sha256: str,
    step_id: str,
    approval_id: str,
    payload_sha256: str,
    approved_at: str | None = None,
) -> Path:
    if not _SHA256_RE.fullmatch(task_spec_sha256) or not _SHA256_RE.fullmatch(payload_sha256):
        raise ValueError("approval_hash_invalid")
    receipt = {
        "schema_version": "omc-autopilot-approval/v1",
        "task_id": task_id,
        "task_spec_sha256": task_spec_sha256,
        "step_id": step_id,
        "approval_id": approval_id,
        "payload_sha256": payload_sha256,
        "approved_at": approved_at or datetime.now(timezone.utc).isoformat(),
    }
    path = _approval_path(root, task_id, step_id)
    _atomic_write_json(path, receipt)
    return path


def approval_receipt_valid(root: Path, *, task: dict[str, Any], step: dict[str, Any]) -> bool:
    gate = step.get("approval_gate")
    if gate is None:
        return True
    path = _approval_path(root, str(task["id"]), str(step["id"]))
    try:
        receipt = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return False
    expected = {
        "schema_version": "omc-autopilot-approval/v1",
        "task_id": str(task["id"]),
        "task_spec_sha256": task_spec_sha256(task),
        "step_id": str(step["id"]),
        "approval_id": str(gate["approval_id"]),
        "payload_sha256": str(gate["payload_sha256"]),
    }
    return all(receipt.get(key) == value for key, value in expected.items()) and bool(receipt.get("approved_at"))


def validate_completion_artifact(root: Path, step: dict[str, Any]) -> tuple[str | None, str | None]:
    completion = step["completion"]
    target = (root / str(completion["output_path"])).resolve()
    try:
        target.relative_to(root.resolve())
    except ValueError:
        return None, "completion_artifact_path_escape"
    if not target.is_file():
        return None, "completion_artifact_missing"
    digest = hashlib.sha256(target.read_bytes()).hexdigest()
    validator_id = completion["validator_id"]
    if validator_id == "artifact_sha256" and digest != completion["expected_sha256"]:
        return None, "completion_artifact_hash_mismatch"
    if validator_id == "json_object_fields":
        try:
            payload = json.loads(target.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError):
            return None, "completion_artifact_json_invalid"
        if not isinstance(payload, dict) or any(field not in payload for field in completion["required_fields"]):
            return None, "completion_artifact_fields_missing"
    return digest, None


def completion_artifact_snapshot(root: Path, step: dict[str, Any]) -> dict[str, Any]:
    """Capture enough state to reject an unchanged artifact after execution."""
    target = (root / str(step["completion"]["output_path"])).resolve()
    try:
        target.relative_to(root.resolve())
    except ValueError:
        return {"exists": False, "path_error": "completion_artifact_path_escape"}
    if not target.is_file():
        return {"exists": False}
    stat = target.stat()
    return {
        "exists": True,
        "sha256": hashlib.sha256(target.read_bytes()).hexdigest(),
        "size": stat.st_size,
        "mtime_ns": stat.st_mtime_ns,
        "ctime_ns": stat.st_ctime_ns,
        "inode": stat.st_ino,
    }


def build_completion_receipt(
    *, task: dict[str, Any], step: dict[str, Any], artifact_sha256: str, predecessor_receipts: list[str]
) -> dict[str, Any]:
    base = {
        "schema_version": "omc-autopilot-completion/v1",
        "task_id": str(task["id"]),
        "task_spec_sha256": task_spec_sha256(task),
        "step_id": str(step["id"]),
        "validator_id": str(step["completion"]["validator_id"]),
        "artifact_sha256": artifact_sha256,
        "predecessor_receipts": predecessor_receipts,
    }
    return {**base, "receipt_sha256": hashlib.sha256(_canonical_json(base)).hexdigest()}


def completion_receipt_valid(
    receipt: object, *, task: dict[str, Any], step: dict[str, Any], predecessor_receipts: list[str]
) -> bool:
    if not isinstance(receipt, dict):
        return False
    artifact_sha256 = str(receipt.get("artifact_sha256") or "")
    if not _SHA256_RE.fullmatch(artifact_sha256):
        return False
    expected = build_completion_receipt(
        task=task,
        step=step,
        artifact_sha256=artifact_sha256,
        predecessor_receipts=predecessor_receipts,
    )
    return receipt == expected


def output_diagnostics(output: str) -> dict[str, Any]:
    """Return content-free diagnostics suitable for durable state."""
    return {
        "output_sha256": hashlib.sha256(output.encode("utf-8")).hexdigest(),
        "output_length": len(output),
    }


def sanitize_step_runtime(runtime: dict[str, Any]) -> dict[str, Any]:
    """Keep schema-v2 telemetry while replacing provider output with diagnostics."""
    sanitized = {
        key: value
        for key, value in runtime.items()
        if key in _STEP_RUNTIME_FIELDS
    }
    partial_output = runtime.get("partial_output")
    if isinstance(partial_output, str) and partial_output:
        sanitized["partial_output_diagnostics"] = output_diagnostics(partial_output)
    return sanitized
