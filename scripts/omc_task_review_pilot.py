#!/usr/bin/env python3
"""Fail-closed preflight helpers for the task-review product-focus pilot."""
from __future__ import annotations

import hashlib
import json
import os
import stat
from datetime import datetime
from pathlib import Path, PurePath
from typing import Any

from omc_output_contract import OutputContractError, parse_envelope

FROZEN_CASE_FIELDS = (
    "case_id",
    "request",
    "base_commit",
    "dod",
    "verification_command",
    "provider",
    "model",
    "reasoning",
    "timeout_sec",
    "repository_id",
    "dependency_condition",
)


class PilotPreflightError(ValueError):
    """Raised when pilot evidence cannot support a deterministic decision."""


def _present(value: object) -> bool:
    if isinstance(value, str):
        return bool(value.strip())
    if isinstance(value, (list, tuple, dict)):
        return bool(value)
    return value is not None


def preflight_case(case: dict[str, Any]) -> dict[str, object]:
    """Validate fields that must be frozen before either paired arm runs."""
    missing = [field for field in FROZEN_CASE_FIELDS if not _present(case.get(field))]
    if missing:
        raise PilotPreflightError(f"missing_frozen_fields:{','.join(missing)}")
    if (
        isinstance(case["timeout_sec"], bool)
        or not isinstance(case["timeout_sec"], int)
        or case["timeout_sec"] <= 0
    ):
        raise PilotPreflightError("invalid_timeout_sec")
    if not isinstance(case["dod"], list) or not all(
        isinstance(item, str) and item.strip() for item in case["dod"]
    ):
        raise PilotPreflightError("invalid_dod")
    return {
        "case_id": str(case["case_id"]),
        "ready": True,
        "frozen_field_count": len(FROZEN_CASE_FIELDS),
    }


def select_first_eligible_cases(
    sessions: list[dict[str, Any]],
    *,
    limit: int,
    t0: str,
    minimum_repository_count: int,
) -> list[dict[str, Any]]:
    """Select first-N without sorting, so reordered or incomplete input fails closed."""
    if limit <= 0 or minimum_repository_count <= 0:
        raise PilotPreflightError("invalid_selection_limit")
    try:
        t0_at = datetime.fromisoformat(t0)
    except (TypeError, ValueError) as exc:
        raise PilotPreflightError("pilot_t0_invalid") from exc
    if t0_at.tzinfo is None:
        raise PilotPreflightError("pilot_t0_invalid")
    seen: set[str] = set()
    previous: datetime | None = None
    selected: list[dict[str, Any]] = []
    for session in sessions:
        session_id = session.get("session_id")
        created_at = session.get("created_at")
        if not isinstance(session_id, str) or not session_id or session_id in seen:
            raise PilotPreflightError("session_inventory_identity_invalid")
        if not isinstance(created_at, str):
            raise PilotPreflightError("session_inventory_timestamp_invalid")
        try:
            observed_at = datetime.fromisoformat(created_at)
        except ValueError as exc:
            raise PilotPreflightError("session_inventory_timestamp_invalid") from exc
        if observed_at.tzinfo is None:
            raise PilotPreflightError("session_inventory_timestamp_invalid")
        if previous is not None and observed_at < previous:
            raise PilotPreflightError("session_inventory_not_chronological")
        if not isinstance(session.get("eligible"), bool):
            raise PilotPreflightError("session_eligibility_missing")
        seen.add(session_id)
        previous = observed_at
        if observed_at <= t0_at:
            continue
        if session["eligible"] and len(selected) < limit:
            repository_id = session.get("repository_id")
            if not isinstance(repository_id, str) or not repository_id.strip():
                raise PilotPreflightError("session_repository_identity_missing")
            selected_session = dict(session)
            selected_session["repository_id"] = repository_id.strip()
            selected.append(selected_session)
    if len(selected) != limit:
        raise PilotPreflightError("insufficient_eligible_cases")
    if (
        len({str(item["repository_id"]) for item in selected})
        < minimum_repository_count
    ):
        raise PilotPreflightError("insufficient_repository_diversity")
    return selected


def _native_artifact(result: dict[str, Any], *, artifact_root: Path) -> dict[str, Any]:
    execution = result.get("execution_artifacts")
    descriptor = (
        execution.get("durable_artifact") if isinstance(execution, dict) else None
    )
    if not isinstance(descriptor, dict):
        raise PilotPreflightError("review_artifact_descriptor_missing")
    relative_path = descriptor.get("path")
    expected_sha256 = descriptor.get("sha256")
    path_parts = PurePath(relative_path).parts if isinstance(relative_path, str) else ()
    if (
        not isinstance(relative_path, str)
        or not relative_path
        or PurePath(relative_path).is_absolute()
        or len(path_parts) != 1
        or any(part in {"", ".", ".."} for part in path_parts)
    ):
        raise PilotPreflightError("review_artifact_path_invalid")
    root = artifact_root.resolve()
    try:
        root_fd = os.open(root, os.O_RDONLY | os.O_DIRECTORY)
    except OSError as exc:
        raise PilotPreflightError("review_artifact_missing") from exc
    try:
        try:
            artifact_fd = os.open(
                relative_path,
                os.O_RDONLY | os.O_NOFOLLOW,
                dir_fd=root_fd,
            )
        except FileNotFoundError as exc:
            raise PilotPreflightError("review_artifact_missing") from exc
        except OSError as exc:
            raise PilotPreflightError("review_artifact_path_invalid") from exc
        try:
            metadata = os.fstat(artifact_fd)
            if not stat.S_ISREG(metadata.st_mode):
                raise PilotPreflightError("review_artifact_path_invalid")
            chunks: list[bytes] = []
            while chunk := os.read(artifact_fd, 64 * 1024):
                chunks.append(chunk)
            raw = b"".join(chunks)
        finally:
            os.close(artifact_fd)
    finally:
        os.close(root_fd)
    if (
        not isinstance(expected_sha256, str)
        or hashlib.sha256(raw).hexdigest() != expected_sha256
    ):
        raise PilotPreflightError("review_artifact_hash_mismatch")
    try:
        artifact = json.loads(raw)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise PilotPreflightError("review_artifact_invalid") from exc
    if not isinstance(artifact, dict):
        raise PilotPreflightError("review_artifact_invalid")
    return artifact


def _validate_native_artifact(result: dict[str, Any], *, artifact_root: Path) -> str:
    artifact = _native_artifact(result, artifact_root=artifact_root)
    execution = result["execution_artifacts"]
    verdict = result.get("verdict")
    if (
        artifact.get("artifact_version") != 2
        or artifact.get("runner") != result.get("runner")
        or artifact.get("case_id") != result.get("case_id")
        or artifact.get("diff_sha256") != result.get("diff_id")
        or artifact.get("exit_code") != execution.get("exit_code")
        or artifact.get("adapter_verdict") != verdict
    ):
        raise PilotPreflightError("review_artifact_identity_mismatch")
    for stream in ("stdout", "stderr"):
        retained = artifact.get(stream)
        retained_sha256 = artifact.get(f"retained_{stream}_sha256")
        if (
            not isinstance(retained, str)
            or not isinstance(retained_sha256, str)
            or hashlib.sha256(retained.encode("utf-8")).hexdigest() != retained_sha256
        ):
            raise PilotPreflightError("review_artifact_transcript_mismatch")
    return str(verdict)


def normalize_review_outcome(
    arm: str,
    output: str | dict[str, Any],
    *,
    artifact_root: Path | None = None,
) -> str:
    """Normalize OMC and native review evidence without repairing ambiguity."""
    if arm == "omc":
        if not isinstance(output, str):
            raise PilotPreflightError("review_outcome_inconclusive")
        try:
            parsed = parse_envelope(output)
        except OutputContractError as exc:
            raise PilotPreflightError("review_outcome_inconclusive") from exc
        if parsed["stage"] != "review":
            raise PilotPreflightError("review_outcome_inconclusive")
        return "approved" if parsed["outcome"] == "approved" else "blocked"
    if arm == "baseline":
        if not isinstance(output, dict):
            raise PilotPreflightError("review_outcome_inconclusive")
        execution = output.get("execution_artifacts")
        verdict = output.get("verdict")
        if (
            output.get("status") != "completed"
            or not isinstance(execution, dict)
            or execution.get("exit_code") != 0
            or execution.get("native_review") is not True
            or execution.get("durable_output_retained") is not True
            or verdict not in {"APPROVE", "APPROVE WITH NOTES", "REVISE", "BLOCK"}
        ):
            raise PilotPreflightError("review_outcome_inconclusive")
        if artifact_root is None:
            raise PilotPreflightError("review_artifact_root_required")
        verified_verdict = _validate_native_artifact(
            output, artifact_root=artifact_root
        )
        return (
            "approved"
            if verified_verdict in {"APPROVE", "APPROVE WITH NOTES"}
            else "blocked"
        )
    raise PilotPreflightError("unknown_pilot_arm")
