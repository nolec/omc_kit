#!/usr/bin/env python3
"""Identity-bound work-unit closure contracts.

This module only freezes and retrieves closure inputs. It deliberately does not
choose or execute the next OMC skill; closure decisions are a separate layer.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import uuid
from pathlib import Path
from typing import Any


SCHEMA_VERSION = "omc-closure/v1"
ENROLLMENT_SCHEMA_VERSION = "omc-closure-enrollment/v1"
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_IDENTITY_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")


class ClosureContractError(ValueError):
    """Raised when a closure contract cannot be trusted."""


def _canonical_json(value: Any) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def _unsigned_envelope(envelope: dict[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in envelope.items() if key != "contract_sha256"}


def envelope_sha256(envelope: dict[str, Any]) -> str:
    return hashlib.sha256(_canonical_json(_unsigned_envelope(envelope))).hexdigest()


def _require_exact_keys(value: Any, expected: set[str], field: str) -> dict[str, Any]:
    if not isinstance(value, dict) or set(value) != expected:
        raise ClosureContractError(f"{field}_fields_invalid")
    return value


def _require_identity_segment(value: Any, field: str) -> str:
    if not isinstance(value, str) or not _IDENTITY_RE.fullmatch(value):
        raise ClosureContractError(f"identity_segment_invalid:{field}")
    return value


def _require_nonempty_string_list(value: Any, field: str) -> list[str]:
    if (
        not isinstance(value, list)
        or not value
        or any(not isinstance(item, str) or not item.strip() for item in value)
    ):
        raise ClosureContractError(f"{field}_invalid")
    return value


def validate_envelope(envelope: Any, *, require_digest: bool = False) -> dict[str, Any]:
    if not isinstance(envelope, dict):
        raise ClosureContractError("closure_contract_not_object")

    expected_top = {
        "schema_version",
        "work_unit",
        "scope",
        "validation",
        "acceptance",
    }
    if require_digest:
        expected_top.add("contract_sha256")
    _require_exact_keys(envelope, expected_top, "closure_contract")

    if envelope["schema_version"] != SCHEMA_VERSION:
        raise ClosureContractError("schema_version_invalid")

    work_unit = _require_exact_keys(
        envelope["work_unit"],
        {"session_id", "task_id", "request_digest"},
        "work_unit",
    )
    _require_identity_segment(work_unit["session_id"], "session_id")
    _require_identity_segment(work_unit["task_id"], "task_id")
    if not isinstance(work_unit["request_digest"], str) or not _SHA256_RE.fullmatch(
        work_unit["request_digest"]
    ):
        raise ClosureContractError("request_digest_invalid")

    scope = _require_exact_keys(
        envelope["scope"],
        {"deliverables", "definition_of_done", "non_goals"},
        "scope",
    )
    _require_nonempty_string_list(scope["deliverables"], "deliverables")
    _require_nonempty_string_list(scope["definition_of_done"], "definition_of_done")
    if not isinstance(scope["non_goals"], list) or any(
        not isinstance(item, str) or not item.strip() for item in scope["non_goals"]
    ):
        raise ClosureContractError("non_goals_invalid")

    validation = _require_exact_keys(
        envelope["validation"],
        {"max_total_rounds", "max_revisions_per_issue"},
        "validation",
    )
    for field in ("max_total_rounds", "max_revisions_per_issue"):
        value = validation[field]
        if isinstance(value, bool) or not isinstance(value, int) or value < 1:
            raise ClosureContractError(f"{field}_invalid")

    acceptance = _require_exact_keys(
        envelope["acceptance"],
        {"authority", "rule_id"},
        "acceptance",
    )
    authority = acceptance["authority"]
    rule_id = acceptance["rule_id"]
    if authority == "user":
        if rule_id is not None:
            raise ClosureContractError("user_acceptance_rule_id_invalid")
    else:
        raise ClosureContractError("acceptance_authority_unsupported")

    if require_digest:
        digest = envelope["contract_sha256"]
        if not isinstance(digest, str) or not _SHA256_RE.fullmatch(digest):
            raise ClosureContractError("contract_digest_invalid")
        if digest != envelope_sha256(envelope):
            raise ClosureContractError("contract_digest_mismatch")

    return envelope


def envelope_path(project_root: Path, session_id: str, task_id: str) -> Path:
    safe_session = _require_identity_segment(session_id, "session_id")
    safe_task = _require_identity_segment(task_id, "task_id")
    return (
        Path(project_root)
        / ".omc"
        / "state"
        / "sessions"
        / safe_session
        / "closure"
        / f"{safe_task}.json"
    )


def enrollment_path(project_root: Path, session_id: str, task_id: str) -> Path:
    safe_session = _require_identity_segment(session_id, "session_id")
    safe_task = _require_identity_segment(task_id, "task_id")
    return (
        Path(project_root)
        / ".omc"
        / "state"
        / "sessions"
        / safe_session
        / "closure-required"
        / f"{safe_task}.json"
    )


def _reject_symlinked_storage(project_root: Path, path: Path) -> None:
    current = Path(project_root)
    for segment in path.relative_to(project_root).parts:
        current /= segment
        if current.is_symlink():
            raise ClosureContractError("closure_storage_symlink")


def _publish_json_no_replace(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    try:
        with temporary.open("x", encoding="utf-8") as handle:
            json.dump(payload, handle, ensure_ascii=False, indent=2)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.link(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def _enrollment_marker(envelope: dict[str, Any]) -> dict[str, Any]:
    return {
        "schema_version": ENROLLMENT_SCHEMA_VERSION,
        "work_unit": envelope["work_unit"],
        "contract_sha256": envelope["contract_sha256"],
    }


def _read_enrollment_marker(path: Path) -> dict[str, Any]:
    if path.is_symlink():
        raise ClosureContractError("closure_enrollment_symlink")
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ClosureContractError("closure_enrollment_invalid_json") from exc
    if not isinstance(payload, dict) or set(payload) != {
        "schema_version",
        "work_unit",
        "contract_sha256",
    }:
        raise ClosureContractError("closure_enrollment_fields_invalid")
    if payload["schema_version"] != ENROLLMENT_SCHEMA_VERSION:
        raise ClosureContractError("closure_enrollment_schema_invalid")
    work_unit = _require_exact_keys(
        payload["work_unit"],
        {"session_id", "task_id", "request_digest"},
        "closure_enrollment_work_unit",
    )
    _require_identity_segment(work_unit["session_id"], "session_id")
    _require_identity_segment(work_unit["task_id"], "task_id")
    if not isinstance(work_unit["request_digest"], str) or not _SHA256_RE.fullmatch(
        work_unit["request_digest"]
    ):
        raise ClosureContractError("request_digest_invalid")
    if not isinstance(payload["contract_sha256"], str) or not _SHA256_RE.fullmatch(
        payload["contract_sha256"]
    ):
        raise ClosureContractError("contract_digest_invalid")
    return payload


def _ensure_enrollment_marker(path: Path, expected: dict[str, Any]) -> None:
    if path.exists() or path.is_symlink():
        if _read_enrollment_marker(path) != expected:
            raise ClosureContractError("closure_contract_already_frozen")
        return
    try:
        _publish_json_no_replace(path, expected)
    except FileExistsError:
        if _read_enrollment_marker(path) != expected:
            raise ClosureContractError("closure_contract_already_frozen")


def store_envelope(project_root: Path, envelope: Any) -> dict[str, Any]:
    validated = validate_envelope(envelope)
    stored = json.loads(json.dumps(validated, ensure_ascii=False))
    stored["contract_sha256"] = envelope_sha256(stored)
    identity = stored["work_unit"]
    path = envelope_path(project_root, identity["session_id"], identity["task_id"])
    marker_path = enrollment_path(
        project_root,
        identity["session_id"],
        identity["task_id"],
    )
    _reject_symlinked_storage(Path(project_root), path.parent)
    _reject_symlinked_storage(Path(project_root), marker_path)
    _ensure_enrollment_marker(marker_path, _enrollment_marker(stored))

    if path.exists() or path.is_symlink():
        existing = _read_stored_envelope(path)
        if existing != stored:
            raise ClosureContractError("closure_contract_already_frozen")
        return existing

    try:
        _publish_json_no_replace(path, stored)
        return stored
    except FileExistsError:
        existing = _read_stored_envelope(path)
        if existing != stored:
            raise ClosureContractError("closure_contract_already_frozen")
        return existing


def _read_stored_envelope(path: Path) -> dict[str, Any]:
    if path.is_symlink():
        raise ClosureContractError("closure_contract_symlink")
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ClosureContractError("closure_contract_invalid_json") from exc
    return validate_envelope(payload, require_digest=True)


def load_envelope(
    project_root: Path,
    *,
    session_id: str,
    task_id: str,
    request_digest: str,
) -> dict[str, Any]:
    if not isinstance(request_digest, str) or not _SHA256_RE.fullmatch(request_digest):
        raise ClosureContractError("request_digest_invalid")
    path = envelope_path(project_root, session_id, task_id)
    marker_path = enrollment_path(project_root, session_id, task_id)
    _reject_symlinked_storage(Path(project_root), path.parent)
    _reject_symlinked_storage(Path(project_root), marker_path)
    marker = None
    if marker_path.exists() or marker_path.is_symlink():
        marker = _read_enrollment_marker(marker_path)
        if marker["work_unit"] != {
            "session_id": session_id,
            "task_id": task_id,
            "request_digest": request_digest,
        }:
            raise ClosureContractError("work_unit_identity_mismatch")
    if not path.exists() and not path.is_symlink():
        if marker is not None:
            raise ClosureContractError("closure_contract_missing")
        return {"status": "absent", "mode": "legacy", "envelope": None}

    envelope = _read_stored_envelope(path)
    if marker is not None and marker != _enrollment_marker(envelope):
        raise ClosureContractError("closure_enrollment_contract_mismatch")
    expected_identity = {
        "session_id": session_id,
        "task_id": task_id,
        "request_digest": request_digest,
    }
    if envelope["work_unit"] != expected_identity:
        raise ClosureContractError("work_unit_identity_mismatch")
    return {"status": "valid", "mode": "closure", "envelope": envelope}
