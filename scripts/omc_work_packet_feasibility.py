#!/usr/bin/env python3
"""Prospective evidence capture for the OMC Work Packet feasibility study."""

from __future__ import annotations

import argparse
import base64
import binascii
import ctypes
from copy import deepcopy
import errno
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from typing import Any, Iterator, Sequence

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

import omc_preregistration_registry as preregistry


LEGACY_PREREGISTRATION_SCHEMA = "omc-work-packet-preregistration/v1"
PREREGISTRATION_SCHEMA = "omc-work-packet-preregistration/v2"
EVIDENCE_SCHEMA = "omc-work-packet-case-evidence/v1"
RECEIPT_SCHEMA = "omc-work-packet-case-receipt/v1"
REPORT_SCHEMA = "omc-work-packet-capture-report/v1"
REGISTRATION_RECEIPT_SCHEMA = "omc-work-packet-registration-receipt/v1"
COMPLETION_RECEIPT_SCHEMA = "omc-work-packet-completion-receipt/v1"
EXECUTION_RECEIPT_SCHEMA = "omc-work-packet-execution-receipt/v1"
COMPLETION_LEDGER_SCHEMA = "omc-work-packet-completion-ledger/v1"
SOURCE_SNAPSHOT_SCHEMA = "omc-work-packet-source-snapshot/v1"
FAILURE_SCHEMA = "omc-work-packet-study-failure/v1"
STUDY_BUNDLE_SCHEMA = "omc-work-packet-study-bundle/v1"
STUDY_BUNDLE_INDEX = "study-bundle-index.json"
TARGET_CASE_COUNT = 5
REGISTRATION_SAFETY_WINDOW_SECONDS = 15 * 60
_CASE_ID_RE = re.compile(r"[a-z0-9][a-z0-9._-]{0,63}")
_SHA256_RE = re.compile(r"[0-9a-f]{64}")
_COMMIT_RE = re.compile(r"[0-9a-f]{40}")
_LEGACY_ELIGIBLE_WORK_CLASSES = ("implementation", "benchmark_maintenance")
_ELIGIBLE_WORK_CLASSES = ("implementation",)
_SIGNOFF_FIELDS = {"signer", "signer_public_key", "signature"}
_AUTHORITY_ROLES = {
    "registration",
    "source_snapshot",
    "completion_collector",
    "executor",
}


def _canonical_bytes(value: Any) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def canonical_sha256(value: Any) -> str:
    payload = (
        bytes(value)
        if isinstance(value, (bytes, bytearray))
        else _canonical_bytes(value)
    )
    return hashlib.sha256(payload).hexdigest()


def _parse_timestamp(value: Any, *, error: str) -> datetime:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(error)
    normalized = value.strip()
    if normalized.endswith("Z"):
        normalized = normalized[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(normalized)
    except ValueError as exc:
        raise ValueError(error) from exc
    if parsed.tzinfo is None:
        raise ValueError(error)
    return parsed.astimezone(timezone.utc)


def _write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    rendered = json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    temporary.write_text(rendered, encoding="utf-8")
    os.replace(temporary, path)


def _load_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"invalid_json:{path.name}") from exc


def _valid_public_key(value: Any) -> bool:
    if not isinstance(value, str):
        return False
    try:
        return len(base64.b64decode(value, validate=True)) == 32
    except (binascii.Error, ValueError):
        return False


def build_preregistration(
    *,
    study_id: str,
    created_at: str,
    registration_authority_public_key: str,
    registration_authority: dict[str, Any],
    completion_collector_public_key: str,
    executor_public_key: str,
    source_snapshot_public_key: str,
    source_inventory: dict[str, Any],
    observation_ends_at: str | None = None,
    authority_custody: dict[str, Any] | None = None,
    restart_parent: dict[str, Any] | None = None,
) -> dict[str, Any]:
    if not isinstance(study_id, str) or _CASE_ID_RE.fullmatch(study_id) is None:
        raise ValueError("study_id_invalid")
    _parse_timestamp(created_at, error="preregistration_timestamp_invalid")
    if not _valid_public_key(registration_authority_public_key):
        raise ValueError("registration_authority_public_key_invalid")
    if not _valid_public_key(completion_collector_public_key):
        raise ValueError("completion_collector_public_key_invalid")
    if not _valid_public_key(executor_public_key):
        raise ValueError("executor_public_key_invalid")
    if not _valid_public_key(source_snapshot_public_key):
        raise ValueError("source_snapshot_public_key_invalid")
    try:
        preregistry.rfc3161.validate_trust_identity(registration_authority)
    except ValueError as exc:
        raise ValueError("registration_authority_invalid")
    _validate_source_inventory_contract(source_inventory)
    v2_requested = any(
        value is not None
        for value in (observation_ends_at, authority_custody, restart_parent)
    )
    if v2_requested and (observation_ends_at is None or authority_custody is None):
        raise ValueError("preregistration_v2_contract_incomplete")
    if v2_requested:
        observation_end = _parse_timestamp(
            observation_ends_at, error="observation_end_invalid"
        )
        if observation_end <= _parse_timestamp(
            created_at, error="preregistration_timestamp_invalid"
        ):
            raise ValueError("observation_window_invalid")
        _validate_authority_custody(authority_custody)
        _validate_restart_parent(restart_parent)
    authority_keys = {
        registration_authority_public_key,
        completion_collector_public_key,
        executor_public_key,
        source_snapshot_public_key,
    }
    if len(authority_keys) != 4:
        raise ValueError("preregistration_authority_keys_reused")
    eligible_work_classes = (
        list(_ELIGIBLE_WORK_CLASSES)
        if v2_requested
        else list(_LEGACY_ELIGIBLE_WORK_CLASSES)
    )
    manifest: dict[str, Any] = {
        "schema_version": (
            PREREGISTRATION_SCHEMA if v2_requested else LEGACY_PREREGISTRATION_SCHEMA
        ),
        "status": "preregistered",
        "study_id": study_id,
        "created_at": created_at,
        "trusted_authorities": {
            "registration_authority_public_key": registration_authority_public_key,
            "registration_authority": registration_authority,
            "completion_collector_public_key": completion_collector_public_key,
            "executor_public_key": executor_public_key,
            "source_snapshot_public_key": source_snapshot_public_key,
        },
        "source_inventory": source_inventory,
        "selection_policy": {
            "mode": "next_eligible_completion_v1",
            "target_case_count": TARGET_CASE_COUNT,
            "eligible_work_classes": eligible_work_classes,
            "excluded": ["synthetic", "document_only", "roadmap_only"],
            "merge_same_feature_followups": True,
        },
        "claim_policy": {
            "retrospective_inputs": "diagnostic_only",
            "five_case_result": "prototype_or_stop_only",
            "competitive_superiority_claim_allowed": False,
        },
    }
    if v2_requested:
        manifest.update(
            {
                "study_purpose": "capture_feasibility_only",
                "observation_ends_at": observation_ends_at,
                "authority_custody": authority_custody,
                "restart_policy": {
                    "automatic_restart": False,
                    "parent": restart_parent,
                },
            }
        )
        manifest["claim_policy"].update(
            {
                "quality_projection_allowed": False,
                "replacement_claim_allowed": False,
            }
        )
    else:
        manifest["metrics"] = {
            "minimum_time_improvement_ratio": 0.20,
            "accuracy_must_not_regress": True,
            "missing_evidence_detection_must_not_regress": True,
            "major_fact_errors_allowed": 0,
            "minimum_packet_preferences": 3,
        }
    manifest["preregistration_sha256"] = canonical_sha256(manifest)
    return validate_preregistration(manifest)


def validate_preregistration(payload: Any) -> dict[str, Any]:
    if (
        not isinstance(payload, dict)
        or payload.get("schema_version")
        not in {LEGACY_PREREGISTRATION_SCHEMA, PREREGISTRATION_SCHEMA}
    ):
        raise ValueError("preregistration_schema_invalid")
    expected = payload.get("preregistration_sha256")
    unsigned = dict(payload)
    unsigned.pop("preregistration_sha256", None)
    if not isinstance(expected, str) or expected != canonical_sha256(unsigned):
        raise ValueError("preregistration_hash_mismatch")
    if payload.get("status") != "preregistered":
        raise ValueError("preregistration_status_invalid")
    if _CASE_ID_RE.fullmatch(str(payload.get("study_id") or "")) is None:
        raise ValueError("study_id_invalid")
    _parse_timestamp(
        payload.get("created_at"), error="preregistration_timestamp_invalid"
    )
    authorities = payload.get("trusted_authorities")
    if not isinstance(authorities, dict) or set(authorities) != {
        "registration_authority_public_key",
        "registration_authority",
        "completion_collector_public_key",
        "executor_public_key",
        "source_snapshot_public_key",
    }:
        raise ValueError("preregistration_authorities_invalid")
    authority_keys = {
        authorities["registration_authority_public_key"],
        authorities["completion_collector_public_key"],
        authorities["executor_public_key"],
        authorities["source_snapshot_public_key"],
    }
    registration_authority = authorities["registration_authority"]
    try:
        preregistry.rfc3161.validate_trust_identity(registration_authority)
    except ValueError as exc:
        raise ValueError("preregistration_authorities_invalid")
    if len(authority_keys) != 4 or not all(
        _valid_public_key(key) for key in authority_keys
    ):
        raise ValueError("preregistration_authorities_invalid")
    _validate_source_inventory_contract(payload.get("source_inventory"))
    is_v2 = payload["schema_version"] == PREREGISTRATION_SCHEMA
    if is_v2:
        observation_end = _parse_timestamp(
            payload.get("observation_ends_at"), error="observation_end_invalid"
        )
        if observation_end <= _parse_timestamp(
            payload["created_at"], error="preregistration_timestamp_invalid"
        ):
            raise ValueError("observation_window_invalid")
        if payload.get("study_purpose") != "capture_feasibility_only":
            raise ValueError("study_purpose_invalid")
        _validate_authority_custody(payload.get("authority_custody"))
        restart_policy = payload.get("restart_policy")
        if (
            not isinstance(restart_policy, dict)
            or set(restart_policy) != {"automatic_restart", "parent"}
            or restart_policy["automatic_restart"] is not False
        ):
            raise ValueError("restart_policy_invalid")
        _validate_restart_parent(restart_policy["parent"])
    policy = payload.get("selection_policy")
    eligible_work_classes = (
        list(_ELIGIBLE_WORK_CLASSES)
        if is_v2
        else list(_LEGACY_ELIGIBLE_WORK_CLASSES)
    )
    if not isinstance(policy, dict) or policy != {
        "mode": "next_eligible_completion_v1",
        "target_case_count": TARGET_CASE_COUNT,
        "eligible_work_classes": eligible_work_classes,
        "excluded": ["synthetic", "document_only", "roadmap_only"],
        "merge_same_feature_followups": True,
    }:
        raise ValueError("preregistration_selection_policy_invalid")
    claim_policy = payload.get("claim_policy")
    if not isinstance(claim_policy, dict):
        raise ValueError("preregistration_contract_invalid")
    if is_v2:
        if (
            "metrics" in payload
            or claim_policy.get("quality_projection_allowed") is not False
            or claim_policy.get("replacement_claim_allowed") is not False
        ):
            raise ValueError("preregistration_claim_policy_invalid")
    elif not isinstance(payload.get("metrics"), dict):
        raise ValueError("preregistration_contract_invalid")
    return payload


def _validate_authority_custody(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict) or set(value) != _AUTHORITY_ROLES:
        raise ValueError("preregistration_authority_custody_invalid")
    operators: set[str] = set()
    custody_ids: set[str] = set()
    for role in sorted(_AUTHORITY_ROLES):
        identity = value[role]
        if (
            not isinstance(identity, dict)
            or set(identity) != {"operator_id", "custody_id"}
            or not isinstance(identity["operator_id"], str)
            or not identity["operator_id"].strip()
            or not isinstance(identity["custody_id"], str)
            or not identity["custody_id"].strip()
        ):
            raise ValueError("preregistration_authority_custody_invalid")
        operators.add(identity["operator_id"])
        custody_ids.add(identity["custody_id"])
    if len(operators) != len(_AUTHORITY_ROLES) or len(custody_ids) != len(
        _AUTHORITY_ROLES
    ):
        raise ValueError("preregistration_authority_custody_reused")
    return value


def _validate_restart_parent(value: Any) -> dict[str, Any] | None:
    if value is None:
        return None
    if (
        not isinstance(value, dict)
        or set(value) != {"study_id", "failure_receipt_sha256", "approval_id"}
        or _CASE_ID_RE.fullmatch(str(value.get("study_id") or "")) is None
        or _SHA256_RE.fullmatch(str(value.get("failure_receipt_sha256") or ""))
        is None
        or not isinstance(value.get("approval_id"), str)
        or not value["approval_id"].strip()
    ):
        raise ValueError("restart_parent_invalid")
    return value


def _validate_source_inventory_contract(value: Any) -> dict[str, Any]:
    expected = {"source_id", "inventory_path", "commit_policy"}
    if not isinstance(value, dict) or set(value) != expected:
        raise ValueError("source_inventory_contract_invalid")
    path = PurePosixPath(str(value.get("inventory_path") or ""))
    if (
        not isinstance(value.get("source_id"), str)
        or not value["source_id"].strip()
        or not isinstance(value.get("inventory_path"), str)
        or path.is_absolute()
        or ".." in path.parts
        or str(path) in {"", "."}
        or value.get("commit_policy") != "git_commit_required"
    ):
        raise ValueError("source_inventory_contract_invalid")
    return value


def _unsigned_receipt_digest(receipt: dict[str, Any]) -> str:
    unsigned = deepcopy(receipt)
    unsigned.pop("receipt_sha256", None)
    unsigned["signoff"]["signature"] = ""
    return canonical_sha256(unsigned)


def _signed_receipt_bytes(receipt: dict[str, Any]) -> bytes:
    signing = deepcopy(receipt)
    signing["signoff"]["signature"] = ""
    return _canonical_bytes(signing)


def _verify_signed_receipt(
    receipt: Any,
    *,
    schema_version: str,
    expected_signer: str,
    trusted_public_keys: set[str],
    expected_fields: set[str],
    error_prefix: str,
) -> dict[str, Any]:
    if not isinstance(receipt, dict) or set(receipt) != expected_fields:
        raise ValueError(f"{error_prefix}_invalid")
    if receipt.get("schema_version") != schema_version:
        raise ValueError(f"{error_prefix}_invalid")
    signoff = receipt.get("signoff")
    if not isinstance(signoff, dict) or set(signoff) != _SIGNOFF_FIELDS:
        raise ValueError(f"{error_prefix}_signoff_invalid")
    if signoff.get("signer") != expected_signer:
        raise ValueError(f"{error_prefix}_signer_invalid")
    public_key = signoff.get("signer_public_key")
    if not _valid_public_key(public_key) or public_key not in trusted_public_keys:
        raise ValueError(f"{error_prefix}_signer_untrusted")
    if receipt.get("receipt_sha256") != _unsigned_receipt_digest(receipt):
        raise ValueError(f"{error_prefix}_digest_mismatch")
    try:
        Ed25519PublicKey.from_public_bytes(
            base64.b64decode(public_key, validate=True)
        ).verify(
            base64.b64decode(signoff.get("signature", ""), validate=True),
            _signed_receipt_bytes(receipt),
        )
    except (InvalidSignature, binascii.Error, ValueError) as exc:
        raise ValueError(f"{error_prefix}_signature_invalid") from exc
    return receipt


def _validate_registration_receipt(
    receipt: Any,
    *,
    manifest: dict[str, Any],
) -> dict[str, Any]:
    validated = _verify_signed_receipt(
        receipt,
        schema_version=REGISTRATION_RECEIPT_SCHEMA,
        expected_signer="work-packet-registration-authority-v1",
        trusted_public_keys={
            manifest["trusted_authorities"]["registration_authority_public_key"]
        },
        expected_fields={
            "schema_version",
            "study_id",
            "preregistration_sha256",
            "registered_at",
            "observation_starts_at",
            "receipt_sha256",
            "signoff",
        },
        error_prefix="registration_receipt",
    )
    if (
        validated["study_id"] != manifest["study_id"]
        or validated["preregistration_sha256"] != manifest["preregistration_sha256"]
        or validated["observation_starts_at"] != manifest["created_at"]
    ):
        raise ValueError("registration_receipt_binding_mismatch")
    registered_at = _parse_timestamp(
        validated["registered_at"], error="registration_timestamp_invalid"
    )
    observation_start = _parse_timestamp(
        manifest["created_at"], error="preregistration_timestamp_invalid"
    )
    if registered_at > observation_start:
        raise ValueError("registration_after_observation_start")
    if (
        manifest["schema_version"] == PREREGISTRATION_SCHEMA
        and (observation_start - registered_at).total_seconds()
        < REGISTRATION_SAFETY_WINDOW_SECONDS
    ):
        raise ValueError("registration_safety_window_too_short")
    return validated


def _validate_external_registration_proof(
    proof: Any,
    *,
    manifest: dict[str, Any],
) -> dict[str, Any]:
    expected_fields = {
        "registry_record",
        "repository_root",
        "registration_receipt",
        "trusted_root",
        "approved_trusted_root_sha256",
    }
    if not isinstance(proof, dict) or set(proof) != expected_fields:
        raise ValueError("registration_proof_invalid")
    receipt = proof["registration_receipt"]
    approved_root = proof["approved_trusted_root_sha256"]
    authority = manifest["trusted_authorities"]["registration_authority"]
    if (
        not isinstance(receipt, dict)
        or not isinstance(proof["registry_record"], dict)
        or not isinstance(proof["repository_root"], str)
        or not isinstance(proof["trusted_root"], dict)
        or authority.get("trusted_root_sha256") != approved_root
    ):
        raise ValueError("registration_proof_invalid")
    preregistry.validate_registry_anchor(
        proof["registry_record"],
        repository_root=proof["repository_root"],
        registry_commit=receipt.get("registry_commit"),
        registry_path=receipt.get("registry_path"),
    )
    preregistry.validate_sigstore_registration_receipt(
        receipt,
        batch_id=manifest["study_id"],
        preregistration_sha256=manifest["preregistration_sha256"],
        registration_authority=authority,
        observation_starts_at=manifest["created_at"],
        expected_receipt_sha256=receipt.get("receipt_sha256"),
        trusted_root=proof["trusted_root"],
        approved_trusted_root_sha256=approved_root,
    )
    if manifest["schema_version"] == PREREGISTRATION_SCHEMA:
        registered_at = _parse_timestamp(
            receipt.get("registered_at"),
            error="external_registration_timestamp_invalid",
        )
        observation_start = _parse_timestamp(
            manifest["created_at"], error="preregistration_timestamp_invalid"
        )
        if (
            observation_start - registered_at
        ).total_seconds() < REGISTRATION_SAFETY_WINDOW_SECONDS:
            raise ValueError("external_registration_safety_window_too_short")
    return proof


_LEDGER_ENTRY_FIELDS = {
    "feature_id",
    "work_class",
    "session_id",
    "started_at",
    "completed_at",
}
_CHAINED_LEDGER_ENTRY_FIELDS = _LEDGER_ENTRY_FIELDS | {
    "sequence",
    "previous_entry_sha256",
}


def _validate_source_snapshot(
    receipt: Any,
    *,
    manifest: dict[str, Any],
    captured_at: datetime,
    repository_root: str,
    required_ancestor_commit: str,
    expected_previous_snapshot_sha256: str | None = None,
) -> dict[str, Any]:
    expected_fields = {
        "schema_version",
        "study_id",
        "preregistration_sha256",
        "source_id",
        "inventory_commit",
        "inventory_path",
        "inventory_sha256",
        "observed_from",
        "observed_through",
        "entry_count",
        "entries_sha256",
        "entries",
        "receipt_sha256",
        "signoff",
    }
    if manifest["schema_version"] == PREREGISTRATION_SCHEMA:
        expected_fields.add("previous_source_snapshot_sha256")
    validated = _verify_signed_receipt(
        receipt,
        schema_version=SOURCE_SNAPSHOT_SCHEMA,
        expected_signer="work-packet-source-snapshot-authority-v1",
        trusted_public_keys={
            manifest["trusted_authorities"]["source_snapshot_public_key"]
        },
        expected_fields=expected_fields,
        error_prefix="source_snapshot",
    )
    if (
        validated["study_id"] != manifest["study_id"]
        or validated["preregistration_sha256"] != manifest["preregistration_sha256"]
        or validated["source_id"] != manifest["source_inventory"]["source_id"]
        or validated["inventory_path"] != manifest["source_inventory"]["inventory_path"]
        or _COMMIT_RE.fullmatch(str(validated["inventory_commit"] or "")) is None
        or _SHA256_RE.fullmatch(str(validated["inventory_sha256"] or "")) is None
        or validated["observed_from"] != manifest["created_at"]
        or (
            manifest["schema_version"] == PREREGISTRATION_SCHEMA
            and validated["previous_source_snapshot_sha256"]
            != expected_previous_snapshot_sha256
        )
    ):
        raise ValueError("source_snapshot_binding_mismatch")
    observed_through = _parse_timestamp(
        validated["observed_through"], error="source_snapshot_timestamp_invalid"
    )
    if observed_through > captured_at:
        raise ValueError("source_snapshot_after_capture")
    entries = validated["entries"]
    if (
        not isinstance(entries, list)
        or not entries
        or isinstance(validated["entry_count"], bool)
        or validated["entry_count"] != len(entries)
        or validated["entries_sha256"] != canonical_sha256(entries)
    ):
        raise ValueError("source_snapshot_entries_invalid")
    _validate_ledger_entries(
        entries,
        manifest=manifest,
        observed_through=observed_through,
        error_prefix="source_snapshot",
    )
    if validated["inventory_sha256"] != canonical_sha256(entries):
        raise ValueError("source_inventory_digest_mismatch")
    _validate_source_inventory_anchor(
        validated,
        repository_root=repository_root,
        required_ancestor_commit=required_ancestor_commit,
    )
    return validated


def _validate_source_inventory_anchor(
    snapshot: dict[str, Any],
    *,
    repository_root: str,
    required_ancestor_commit: str,
) -> None:
    root = Path(repository_root)
    if (
        not root.is_absolute()
        or _COMMIT_RE.fullmatch(str(required_ancestor_commit or "")) is None
    ):
        raise ValueError("source_inventory_anchor_invalid")
    try:
        subprocess.run(
            [
                "git",
                "-C",
                str(root),
                "merge-base",
                "--is-ancestor",
                required_ancestor_commit,
                snapshot["inventory_commit"],
            ],
            check=True,
            capture_output=True,
            text=True,
            timeout=30,
        )
        completed = subprocess.run(
            [
                "git",
                "-C",
                str(root),
                "show",
                f"{snapshot['inventory_commit']}:{snapshot['inventory_path']}",
            ],
            check=True,
            capture_output=True,
            text=True,
            timeout=30,
        )
        committed_entries = json.loads(completed.stdout)
    except (
        OSError,
        subprocess.CalledProcessError,
        subprocess.TimeoutExpired,
        json.JSONDecodeError,
    ) as exc:
        raise ValueError("source_inventory_anchor_invalid") from exc
    if (
        committed_entries != snapshot["entries"]
        or canonical_sha256(committed_entries) != snapshot["inventory_sha256"]
    ):
        raise ValueError("source_inventory_anchor_invalid")


def _validate_ledger_entries(
    entries: Any,
    *,
    manifest: dict[str, Any],
    observed_through: datetime,
    error_prefix: str,
) -> None:
    if not isinstance(entries, list) or not entries:
        raise ValueError(f"{error_prefix}_entries_invalid")
    preregistered_at = _parse_timestamp(
        manifest["created_at"], error="preregistration_timestamp_invalid"
    )
    previous_completed_at: datetime | None = None
    previous_entry: dict[str, Any] | None = None
    session_ids: set[str] = set()
    expected_fields = (
        _CHAINED_LEDGER_ENTRY_FIELDS
        if manifest["schema_version"] == PREREGISTRATION_SCHEMA
        else _LEDGER_ENTRY_FIELDS
    )
    for entry in entries:
        if not isinstance(entry, dict) or set(entry) != expected_fields:
            raise ValueError(f"{error_prefix}_entry_invalid")
        if manifest["schema_version"] == PREREGISTRATION_SCHEMA:
            expected_sequence = 1 if previous_entry is None else previous_entry["sequence"] + 1
            expected_previous_hash = (
                None if previous_entry is None else canonical_sha256(previous_entry)
            )
            if (
                isinstance(entry["sequence"], bool)
                or entry["sequence"] != expected_sequence
                or entry["previous_entry_sha256"] != expected_previous_hash
            ):
                raise ValueError(f"{error_prefix}_sequence_invalid")
        if (
            not isinstance(entry["feature_id"], str)
            or _CASE_ID_RE.fullmatch(entry["feature_id"]) is None
            or not isinstance(entry["work_class"], str)
            or not entry["work_class"].strip()
            or not isinstance(entry["session_id"], str)
            or not entry["session_id"].strip()
            or entry["session_id"] in session_ids
        ):
            raise ValueError(f"{error_prefix}_entry_invalid")
        started_at = _parse_timestamp(
            entry["started_at"], error=f"{error_prefix}_timestamp_invalid"
        )
        completed_at = _parse_timestamp(
            entry["completed_at"], error=f"{error_prefix}_timestamp_invalid"
        )
        if (
            started_at <= preregistered_at
            or completed_at < started_at
            or completed_at > observed_through
            or (
                previous_completed_at is not None
                and completed_at < previous_completed_at
            )
        ):
            raise ValueError(f"{error_prefix}_order_invalid")
        previous_completed_at = completed_at
        previous_entry = entry
        session_ids.add(entry["session_id"])


def _validate_completion_ledger(
    receipt: Any,
    *,
    manifest: dict[str, Any],
    captured_at: datetime,
    previous_entries: list[dict[str, Any]],
    source_snapshot: dict[str, Any],
) -> dict[str, Any]:
    validated = _verify_signed_receipt(
        receipt,
        schema_version=COMPLETION_LEDGER_SCHEMA,
        expected_signer="work-packet-completion-collector-v1",
        trusted_public_keys={
            manifest["trusted_authorities"]["completion_collector_public_key"]
        },
        expected_fields={
            "schema_version",
            "study_id",
            "preregistration_sha256",
            "observed_through",
            "entries",
            "source_snapshot_sha256",
            "receipt_sha256",
            "signoff",
        },
        error_prefix="completion_ledger",
    )
    if (
        validated["study_id"] != manifest["study_id"]
        or validated["preregistration_sha256"] != manifest["preregistration_sha256"]
    ):
        raise ValueError("completion_ledger_binding_mismatch")
    observed_through = _parse_timestamp(
        validated["observed_through"], error="completion_ledger_timestamp_invalid"
    )
    if observed_through > captured_at:
        raise ValueError("completion_ledger_after_capture")
    entries = validated["entries"]
    _validate_ledger_entries(
        entries,
        manifest=manifest,
        observed_through=observed_through,
        error_prefix="completion_ledger",
    )
    if (
        validated["source_snapshot_sha256"] != source_snapshot["receipt_sha256"]
        or entries != source_snapshot["entries"]
        or validated["observed_through"] != source_snapshot["observed_through"]
    ):
        raise ValueError("completion_ledger_source_mismatch")
    if entries[: len(previous_entries)] != previous_entries:
        raise ValueError("completion_ledger_prefix_mismatch")
    return validated


def _eligible_completion_entries(
    ledger: dict[str, Any],
    *,
    manifest: dict[str, Any],
) -> list[dict[str, Any]]:
    selected: list[dict[str, Any]] = []
    selected_features: set[str] = set()
    for entry in ledger["entries"]:
        if (
            entry["work_class"]
            not in manifest["selection_policy"]["eligible_work_classes"]
            or entry["feature_id"] in selected_features
        ):
            continue
        selected.append(entry)
        selected_features.add(entry["feature_id"])
    return selected


def _validate_completion_receipt(
    receipt: Any,
    *,
    manifest: dict[str, Any],
    evidence: dict[str, Any],
    expected_ordinal: int,
    expected_previous_receipt_sha256: str | None,
    previous_completed_at: datetime | None,
) -> dict[str, Any]:
    validated = _verify_signed_receipt(
        receipt,
        schema_version=COMPLETION_RECEIPT_SCHEMA,
        expected_signer="work-packet-completion-collector-v1",
        trusted_public_keys={
            manifest["trusted_authorities"]["completion_collector_public_key"]
        },
        expected_fields={
            "schema_version",
            "study_id",
            "preregistration_sha256",
            "ordinal",
            "previous_receipt_sha256",
            "feature_id",
            "work_class",
            "session_id",
            "started_at",
            "completed_at",
            "receipt_sha256",
            "signoff",
        },
        error_prefix="completion_receipt",
    )
    if (
        isinstance(validated["ordinal"], bool)
        or not isinstance(validated["ordinal"], int)
        or validated["ordinal"] != expected_ordinal
    ):
        raise ValueError("case_completion_ordinal_mismatch")
    if validated["previous_receipt_sha256"] != expected_previous_receipt_sha256:
        raise ValueError("case_completion_chain_mismatch")
    bindings = ("feature_id", "work_class", "session_id", "started_at", "completed_at")
    if (
        validated["study_id"] != manifest["study_id"]
        or validated["preregistration_sha256"] != manifest["preregistration_sha256"]
        or any(validated[field] != evidence[field] for field in bindings)
    ):
        raise ValueError("completion_receipt_binding_mismatch")
    completed_at = _parse_timestamp(
        validated["completed_at"], error="case_timestamp_invalid"
    )
    if previous_completed_at is not None and completed_at < previous_completed_at:
        raise ValueError("case_completion_order_mismatch")
    return validated


def _validate_execution_receipt(
    receipt: Any,
    *,
    manifest: dict[str, Any],
    evidence: dict[str, Any],
    request: bytes,
    baseline_output: bytes,
) -> dict[str, Any]:
    validated = _verify_signed_receipt(
        receipt,
        schema_version=EXECUTION_RECEIPT_SCHEMA,
        expected_signer="work-packet-executor-v1",
        trusted_public_keys={manifest["trusted_authorities"]["executor_public_key"]},
        expected_fields={
            "schema_version",
            "study_id",
            "preregistration_sha256",
            "execution_id",
            "session_id",
            "request_sha256",
            "raw_output_sha256",
            "completed_at",
            "receipt_sha256",
            "signoff",
        },
        error_prefix="execution_receipt",
    )
    if (
        validated["study_id"] != manifest["study_id"]
        or validated["preregistration_sha256"] != manifest["preregistration_sha256"]
        or validated["session_id"] != evidence["session_id"]
        or validated["completed_at"] != evidence["completed_at"]
        or not isinstance(validated["execution_id"], str)
        or not validated["execution_id"].strip()
    ):
        raise ValueError("execution_receipt_binding_mismatch")
    if validated["request_sha256"] != canonical_sha256(request):
        raise ValueError("execution_request_hash_mismatch")
    if validated["raw_output_sha256"] != canonical_sha256(baseline_output):
        raise ValueError("execution_raw_output_hash_mismatch")
    return validated


def _validate_case_evidence(
    evidence: Any,
    *,
    preregistered_at: datetime,
    captured_at: datetime,
    eligible_work_classes: Sequence[str] = _LEGACY_ELIGIBLE_WORK_CLASSES,
    observation_ends_at: datetime | None = None,
) -> dict[str, Any]:
    if (
        not isinstance(evidence, dict)
        or evidence.get("schema_version") != EVIDENCE_SCHEMA
    ):
        raise ValueError("case_evidence_schema_invalid")
    feature_id = evidence.get("feature_id")
    if not isinstance(feature_id, str) or _CASE_ID_RE.fullmatch(feature_id) is None:
        raise ValueError("case_feature_id_invalid")
    if evidence.get("work_class") not in eligible_work_classes:
        raise ValueError("case_work_class_ineligible")
    started_at = _parse_timestamp(
        evidence.get("started_at"), error="case_timestamp_invalid"
    )
    completed_at = _parse_timestamp(
        evidence.get("completed_at"), error="case_timestamp_invalid"
    )
    if started_at <= preregistered_at:
        raise ValueError("case_not_prospective")
    if (
        completed_at < started_at
        or captured_at < completed_at
        or (observation_ends_at is not None and completed_at > observation_ends_at)
        or (observation_ends_at is not None and captured_at > observation_ends_at)
    ):
        raise ValueError("case_timestamp_invalid")
    if not str(evidence.get("session_id") or "").strip():
        raise ValueError("case_session_missing")

    git = evidence.get("git")
    if not isinstance(git, dict):
        raise ValueError("case_git_evidence_missing")
    if _COMMIT_RE.fullmatch(str(git.get("commit") or "")) is None:
        raise ValueError("case_commit_invalid")
    if _SHA256_RE.fullmatch(str(git.get("diff_sha256") or "")) is None:
        raise ValueError("case_diff_hash_invalid")
    changed_file_count = git.get("changed_file_count")
    if (
        isinstance(changed_file_count, bool)
        or not isinstance(changed_file_count, int)
        or changed_file_count < 1
    ):
        raise ValueError("case_change_missing")

    verification = evidence.get("verification")
    if not isinstance(verification, list) or not verification:
        raise ValueError("case_verification_missing")
    for item in verification:
        if not isinstance(item, dict) or not str(item.get("command") or "").strip():
            raise ValueError("case_verification_invalid")
        if isinstance(item.get("exit_code"), bool) or not isinstance(
            item.get("exit_code"), int
        ):
            raise ValueError("case_verification_invalid")
        if not isinstance(item.get("stdout"), str) or not isinstance(
            item.get("stderr"), str
        ):
            raise ValueError("case_verification_output_missing")

    review = evidence.get("review")
    if not isinstance(review, dict) or not str(review.get("verdict") or "").strip():
        raise ValueError("case_review_missing")
    if (
        not isinstance(review.get("raw_output"), str)
        or not review["raw_output"].strip()
    ):
        raise ValueError("case_review_output_missing")
    return evidence


def _observation_end(manifest: dict[str, Any]) -> datetime | None:
    if manifest["schema_version"] != PREREGISTRATION_SCHEMA:
        return None
    return _parse_timestamp(
        manifest["observation_ends_at"], error="observation_end_invalid"
    )


@contextmanager
def _capture_lock(study_root: Path) -> Iterator[None]:
    study_root.mkdir(parents=True, exist_ok=True)
    lock = study_root / ".capture.lock"
    try:
        lock.mkdir()
    except FileExistsError as exc:
        raise ValueError("study_capture_locked") from exc
    try:
        yield
    finally:
        try:
            lock.rmdir()
        except OSError:
            pass


def _validate_failure_receipt(
    receipt: Any,
    *,
    manifest: dict[str, Any],
    expected_case_receipt_sha256s: list[str],
    latest_case_captured_at: datetime | None = None,
) -> dict[str, Any]:
    validated = _verify_signed_receipt(
        receipt,
        schema_version=FAILURE_SCHEMA,
        expected_signer="work-packet-registration-authority-v1",
        trusted_public_keys={
            manifest["trusted_authorities"]["registration_authority_public_key"]
        },
        expected_fields={
            "schema_version",
            "study_id",
            "preregistration_sha256",
            "failed_at",
            "status",
            "reason_code",
            "case_receipt_sha256s",
            "receipt_sha256",
            "signoff",
        },
        error_prefix="study_failure",
    )
    failed_at = _parse_timestamp(
        validated["failed_at"], error="study_failure_timestamp_invalid"
    )
    if (
        validated["study_id"] != manifest["study_id"]
        or validated["preregistration_sha256"]
        != manifest["preregistration_sha256"]
        or validated["status"] not in {"FAILED", "INDETERMINATE"}
        or not isinstance(validated["reason_code"], str)
        or not validated["reason_code"].strip()
        or validated["case_receipt_sha256s"]
        != expected_case_receipt_sha256s
        or failed_at
        < _parse_timestamp(
            manifest["created_at"], error="preregistration_timestamp_invalid"
        )
    ):
        raise ValueError("study_failure_binding_mismatch")
    if latest_case_captured_at is not None and failed_at < latest_case_captured_at:
        raise ValueError("study_failure_chronology_invalid")
    return validated


def record_study_failure(
    *,
    manifest: dict[str, Any],
    study_root: Path,
    failure_receipt: dict[str, Any],
) -> dict[str, Any]:
    manifest = validate_preregistration(manifest)
    study_root = Path(study_root)
    with _capture_lock(study_root):
        failure_path = study_root / "failure.json"
        case_receipts = []
        latest_case_captured_at: datetime | None = None
        cases_root = study_root / "cases"
        if cases_root.exists():
            case_paths = sorted(
                (path for path in cases_root.iterdir() if path.is_dir()),
                key=_stored_completion_ordinal,
            )
            for path in case_paths:
                case_receipt = _load_json(path / "receipt.json")
                if (
                    not isinstance(case_receipt, dict)
                    or case_receipt.get("schema_version") != RECEIPT_SCHEMA
                    or case_receipt.get("case_id") != path.name
                    or case_receipt.get("study_id") != manifest["study_id"]
                    or case_receipt.get("preregistration_sha256")
                    != manifest["preregistration_sha256"]
                ):
                    raise ValueError("case_receipt_invalid")
                unsigned = dict(case_receipt)
                receipt_sha256 = unsigned.pop("receipt_sha256", None)
                if receipt_sha256 != canonical_sha256(unsigned):
                    raise ValueError("case_receipt_hash_mismatch")
                captured_at = _parse_timestamp(
                    case_receipt.get("captured_at"), error="case_timestamp_invalid"
                )
                case_receipts.append(receipt_sha256)
                if (
                    latest_case_captured_at is None
                    or captured_at > latest_case_captured_at
                ):
                    latest_case_captured_at = captured_at
        if failure_path.exists():
            existing = _validate_failure_receipt(
                _load_json(failure_path),
                manifest=manifest,
                expected_case_receipt_sha256s=case_receipts,
                latest_case_captured_at=latest_case_captured_at,
            )
            if existing != failure_receipt:
                raise ValueError("study_failure_already_recorded")
            return existing
        validated = _validate_failure_receipt(
            failure_receipt,
            manifest=manifest,
            expected_case_receipt_sha256s=case_receipts,
            latest_case_captured_at=latest_case_captured_at,
        )
        manifest_path = study_root / "preregistration.json"
        if manifest_path.exists() and _load_json(manifest_path) != manifest:
            raise ValueError("study_preregistration_mismatch")
        if not manifest_path.exists():
            _write_json(manifest_path, manifest)
        _write_json(failure_path, validated)
        return validated


def _temporary_roots() -> tuple[Path, ...]:
    return tuple(
        {
            Path(tempfile.gettempdir()).resolve(strict=False),
            Path("/tmp").resolve(strict=False),
            Path("/private/tmp").resolve(strict=False),
        }
    )


def _is_within(path: Path, root: Path) -> bool:
    return path == root or root in path.parents


def _validate_durable_root(value: Path) -> Path:
    candidate = Path(value).expanduser()
    if not candidate.is_absolute() or candidate.is_symlink():
        raise ValueError("study_bundle_root_invalid")
    unresolved = candidate.resolve(strict=False)
    if any(_is_within(unresolved, root) for root in _temporary_roots()):
        raise ValueError("study_bundle_root_ephemeral")
    try:
        resolved = candidate.resolve(strict=True)
    except OSError as exc:
        raise ValueError("study_bundle_root_invalid") from exc
    if not resolved.is_dir():
        raise ValueError("study_bundle_root_invalid")
    return resolved


def _file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _fsync_directory(path: Path) -> None:
    descriptor = os.open(path, os.O_RDONLY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _fsync_file(path: Path) -> None:
    descriptor = os.open(path, os.O_RDONLY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _atomic_rename_no_replace(source: Path, destination: Path) -> None:
    libc = ctypes.CDLL(None, use_errno=True)
    source_bytes = os.fsencode(source)
    destination_bytes = os.fsencode(destination)
    if sys.platform == "darwin" and hasattr(libc, "renameatx_np"):
        rename = libc.renameatx_np
        rename.argtypes = [
            ctypes.c_int,
            ctypes.c_char_p,
            ctypes.c_int,
            ctypes.c_char_p,
            ctypes.c_uint,
        ]
        rename.restype = ctypes.c_int
        result = rename(-2, source_bytes, -2, destination_bytes, 0x00000004)
    elif sys.platform.startswith("linux") and hasattr(libc, "renameat2"):
        rename = libc.renameat2
        rename.argtypes = [
            ctypes.c_int,
            ctypes.c_char_p,
            ctypes.c_int,
            ctypes.c_char_p,
            ctypes.c_uint,
        ]
        rename.restype = ctypes.c_int
        result = rename(-100, source_bytes, -100, destination_bytes, 0x1)
    else:
        raise OSError(errno.ENOTSUP, "atomic no-replace rename unavailable")
    if result == 0:
        return
    error_number = ctypes.get_errno()
    if error_number in {errno.EEXIST, errno.ENOTEMPTY}:
        raise ValueError("study_bundle_exists")
    raise OSError(error_number, os.strerror(error_number))


def _study_artifact_paths(study_root: Path) -> dict[str, Path]:
    if not study_root.is_dir() or study_root.is_symlink():
        raise ValueError("study_bundle_source_invalid")
    artifacts: dict[str, Path] = {}
    for path in study_root.rglob("*"):
        if path.is_symlink():
            raise ValueError("study_bundle_source_invalid")
        if path.is_file() and path.name != ".capture.lock":
            relative = path.relative_to(study_root).as_posix()
            if relative == STUDY_BUNDLE_INDEX:
                raise ValueError("study_bundle_reserved_index_conflict")
            artifacts[relative] = path
    if "preregistration.json" not in artifacts:
        raise ValueError("study_bundle_source_invalid")
    return artifacts


def _validate_bundle_relative_path(value: Any) -> PurePosixPath:
    if not isinstance(value, str):
        raise ValueError("study_bundle_invalid")
    relative = PurePosixPath(value)
    if relative.is_absolute() or ".." in relative.parts or str(relative) in {"", "."}:
        raise ValueError("study_bundle_invalid")
    return relative


def publish_study_bundle(
    *,
    study_root: Path,
    evidence_root: Path,
    study_id: str,
) -> dict[str, Any]:
    root = _validate_durable_root(Path(evidence_root))
    if _CASE_ID_RE.fullmatch(str(study_id or "")) is None:
        raise ValueError("study_bundle_study_id_invalid")
    artifacts = _study_artifact_paths(Path(study_root))
    manifest = validate_preregistration(_load_json(artifacts["preregistration.json"]))
    if manifest["study_id"] != study_id:
        raise ValueError("study_bundle_study_id_mismatch")
    destination = root / study_id
    if destination.exists():
        raise ValueError("study_bundle_exists")
    staging = Path(tempfile.mkdtemp(prefix=f".{study_id}-", dir=root))
    try:
        metadata: dict[str, dict[str, Any]] = {}
        for relative, source in sorted(artifacts.items()):
            target = staging / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(source, target)
            _fsync_file(target)
            metadata[relative] = {
                "sha256": _file_sha256(target),
                "size_bytes": target.stat().st_size,
            }
        index: dict[str, Any] = {
            "schema_version": STUDY_BUNDLE_SCHEMA,
            "study_id": study_id,
            "artifacts": metadata,
        }
        index["bundle_sha256"] = canonical_sha256(index)
        _write_json(staging / STUDY_BUNDLE_INDEX, index)
        _fsync_file(staging / STUDY_BUNDLE_INDEX)
        for directory in sorted(
            {path.parent for path in staging.rglob("*") if path.is_file()},
            key=lambda item: len(item.parts),
            reverse=True,
        ):
            _fsync_directory(directory)
        _atomic_rename_no_replace(staging, destination)
        _fsync_directory(destination)
        _fsync_directory(root)
        return index
    except Exception:
        shutil.rmtree(staging, ignore_errors=True)
        raise


def load_study_bundle(*, evidence_root: Path, study_id: str) -> dict[str, Any]:
    root = _validate_durable_root(Path(evidence_root))
    if _CASE_ID_RE.fullmatch(str(study_id or "")) is None:
        raise ValueError("study_bundle_study_id_invalid")
    bundle_root = root / study_id
    if bundle_root.is_symlink() or not bundle_root.is_dir():
        raise ValueError("study_bundle_invalid")
    index = _load_json(bundle_root / STUDY_BUNDLE_INDEX)
    if (
        not isinstance(index, dict)
        or set(index) != {
            "schema_version",
            "study_id",
            "artifacts",
            "bundle_sha256",
        }
        or index.get("schema_version") != STUDY_BUNDLE_SCHEMA
        or index.get("study_id") != study_id
        or index.get("bundle_sha256")
        != canonical_sha256(
            {key: value for key, value in index.items() if key != "bundle_sha256"}
        )
        or not isinstance(index.get("artifacts"), dict)
    ):
        raise ValueError("study_bundle_invalid")
    actual = {
        path.relative_to(bundle_root).as_posix()
        for path in bundle_root.rglob("*")
        if path.is_file() and path != bundle_root / STUDY_BUNDLE_INDEX
    }
    if actual != set(index["artifacts"]):
        raise ValueError("study_bundle_invalid")
    for relative, metadata in index["artifacts"].items():
        safe_relative = _validate_bundle_relative_path(relative)
        artifact = bundle_root.joinpath(*safe_relative.parts)
        if (
            artifact.is_symlink()
            or not isinstance(metadata, dict)
            or set(metadata) != {"sha256", "size_bytes"}
            or not artifact.is_file()
            or artifact.stat().st_size != metadata["size_bytes"]
            or _file_sha256(artifact) != metadata["sha256"]
        ):
            raise ValueError("study_bundle_digest_mismatch")
    return index


def _bind_study(
    study_root: Path,
    manifest: dict[str, Any],
    registration_receipt: dict[str, Any],
    registration_proof: dict[str, Any],
) -> None:
    manifest_path = study_root / "preregistration.json"
    if manifest_path.exists():
        if _load_json(manifest_path) != manifest:
            raise ValueError("study_preregistration_mismatch")
    else:
        _write_json(manifest_path, manifest)
    receipt_path = study_root / "registration-receipt.json"
    if receipt_path.exists():
        if _load_json(receipt_path) != registration_receipt:
            raise ValueError("study_registration_receipt_mismatch")
    else:
        _write_json(receipt_path, registration_receipt)
    proof_path = study_root / "registration-proof.json"
    if proof_path.exists():
        if _load_json(proof_path) != registration_proof:
            raise ValueError("study_registration_proof_mismatch")
    else:
        _write_json(proof_path, registration_proof)


def _stored_completion_ordinal(case_root: Path) -> int:
    ordinal = _load_json(case_root / "completion-receipt.json").get("ordinal")
    if isinstance(ordinal, bool) or not isinstance(ordinal, int):
        raise ValueError("case_completion_ordinal_mismatch")
    return ordinal


def capture_case(
    *,
    manifest: dict[str, Any],
    study_root: Path,
    case_id: str,
    request: bytes,
    baseline_output: bytes,
    evidence: dict[str, Any],
    captured_at: str,
    registration_receipt: dict[str, Any],
    registration_proof: dict[str, Any],
    source_snapshot: dict[str, Any],
    completion_ledger: dict[str, Any],
    completion_receipt: dict[str, Any],
    execution_receipt: dict[str, Any],
) -> dict[str, Any]:
    manifest = validate_preregistration(manifest)
    study_root = Path(study_root)
    if (study_root / "failure.json").exists():
        raise ValueError("study_failed")
    registration_receipt = _validate_registration_receipt(
        registration_receipt,
        manifest=manifest,
    )
    registration_proof = _validate_external_registration_proof(
        registration_proof,
        manifest=manifest,
    )
    if _CASE_ID_RE.fullmatch(str(case_id or "")) is None:
        raise ValueError("case_id_invalid")
    if not isinstance(request, bytes) or not request:
        raise ValueError("case_request_missing")
    if not isinstance(baseline_output, bytes) or not baseline_output:
        raise ValueError("case_baseline_output_missing")
    captured = _parse_timestamp(captured_at, error="case_timestamp_invalid")
    preregistered = _parse_timestamp(
        manifest["created_at"], error="preregistration_timestamp_invalid"
    )
    evidence = _validate_case_evidence(
        evidence,
        preregistered_at=preregistered,
        captured_at=captured,
        eligible_work_classes=manifest["selection_policy"][
            "eligible_work_classes"
        ],
        observation_ends_at=_observation_end(manifest),
    )

    with _capture_lock(study_root):
        if (study_root / "failure.json").exists():
            raise ValueError("study_failed")
        _bind_study(study_root, manifest, registration_receipt, registration_proof)
        cases_root = study_root / "cases"
        cases_root.mkdir(parents=True, exist_ok=True)
        target = cases_root / case_id
        if target.exists():
            raise ValueError("case_already_captured")
        existing = [path for path in cases_root.iterdir() if path.is_dir()]
        if len(existing) >= TARGET_CASE_COUNT:
            raise ValueError("study_case_limit_reached")
        existing.sort(key=_stored_completion_ordinal)
        existing_receipts: list[dict[str, Any]] = []
        previous_ledger_entries: list[dict[str, Any]] = []
        previous_completion_hash: str | None = None
        previous_source_snapshot_hash: str | None = None
        previous_completed_at: datetime | None = None
        for ordinal, path in enumerate(existing, start=1):
            case_receipt = _validate_captured_case(
                path,
                manifest=manifest,
                registration_receipt=registration_receipt,
                registration_proof=registration_proof,
                expected_ordinal=ordinal,
                expected_previous_receipt_sha256=previous_completion_hash,
                previous_completed_at=previous_completed_at,
                previous_ledger_entries=previous_ledger_entries,
                expected_previous_source_snapshot_sha256=(
                    previous_source_snapshot_hash
                ),
            )
            existing_receipts.append(case_receipt)
            stored_completion = _load_json(path / "completion-receipt.json")
            previous_completion_hash = stored_completion["receipt_sha256"]
            previous_completed_at = _parse_timestamp(
                stored_completion["completed_at"], error="case_timestamp_invalid"
            )
            previous_ledger_entries = _load_json(path / "completion-ledger.json")[
                "entries"
            ]
            previous_source_snapshot_hash = _load_json(
                path / "source-snapshot.json"
            )["receipt_sha256"]
        if any(
            receipt["feature_id"] == evidence["feature_id"]
            for receipt in existing_receipts
        ):
            raise ValueError("case_feature_already_captured")
        source_snapshot = _validate_source_snapshot(
            source_snapshot,
            manifest=manifest,
            captured_at=captured,
            repository_root=registration_proof["repository_root"],
            required_ancestor_commit=registration_proof["registration_receipt"][
                "registry_commit"
            ],
            expected_previous_snapshot_sha256=previous_source_snapshot_hash,
        )
        completion_ledger = _validate_completion_ledger(
            completion_ledger,
            manifest=manifest,
            captured_at=captured,
            previous_entries=previous_ledger_entries,
            source_snapshot=source_snapshot,
        )
        selected_entries = _eligible_completion_entries(
            completion_ledger, manifest=manifest
        )
        next_ordinal = len(existing) + 1
        if len(selected_entries) < next_ordinal:
            raise ValueError("completion_ledger_eligible_entry_missing")
        expected_entry = selected_entries[next_ordinal - 1]
        if any(
            expected_entry[field] != evidence[field] for field in _LEDGER_ENTRY_FIELDS
        ):
            raise ValueError("completion_ledger_selection_mismatch")
        completion_receipt = _validate_completion_receipt(
            completion_receipt,
            manifest=manifest,
            evidence=evidence,
            expected_ordinal=next_ordinal,
            expected_previous_receipt_sha256=previous_completion_hash,
            previous_completed_at=previous_completed_at,
        )
        execution_receipt = _validate_execution_receipt(
            execution_receipt,
            manifest=manifest,
            evidence=evidence,
            request=request,
            baseline_output=baseline_output,
        )

        receipt: dict[str, Any] = {
            "schema_version": RECEIPT_SCHEMA,
            "study_id": manifest["study_id"],
            "preregistration_sha256": manifest["preregistration_sha256"],
            "case_id": case_id,
            "feature_id": evidence["feature_id"],
            "completion_ordinal": completion_receipt["ordinal"],
            "captured_at": captured_at,
            "request_sha256": canonical_sha256(request),
            "baseline_output_sha256": canonical_sha256(baseline_output),
            "evidence_sha256": canonical_sha256(evidence),
            "registration_receipt_sha256": registration_receipt["receipt_sha256"],
            "external_registration_receipt_sha256": registration_proof[
                "registration_receipt"
            ]["receipt_sha256"],
            "source_snapshot_sha256": source_snapshot["receipt_sha256"],
            "completion_receipt_sha256": completion_receipt["receipt_sha256"],
            "completion_ledger_sha256": completion_ledger["receipt_sha256"],
            "execution_receipt_sha256": execution_receipt["receipt_sha256"],
        }
        receipt["receipt_sha256"] = canonical_sha256(receipt)

        staging = Path(tempfile.mkdtemp(prefix=f".{case_id}.", dir=cases_root))
        try:
            (staging / "request.txt").write_bytes(request)
            (staging / "baseline-output.txt").write_bytes(baseline_output)
            _write_json(staging / "evidence.json", evidence)
            _write_json(staging / "source-snapshot.json", source_snapshot)
            _write_json(staging / "completion-ledger.json", completion_ledger)
            _write_json(staging / "completion-receipt.json", completion_receipt)
            _write_json(staging / "execution-receipt.json", execution_receipt)
            _write_json(staging / "receipt.json", receipt)
            staging.rename(target)
        except Exception:
            shutil.rmtree(staging, ignore_errors=True)
            raise
    return receipt


def _validate_captured_case(
    case_root: Path,
    *,
    manifest: dict[str, Any],
    registration_receipt: dict[str, Any],
    registration_proof: dict[str, Any],
    expected_ordinal: int,
    expected_previous_receipt_sha256: str | None,
    previous_completed_at: datetime | None,
    previous_ledger_entries: list[dict[str, Any]],
    expected_previous_source_snapshot_sha256: str | None,
) -> dict[str, Any]:
    receipt = _load_json(case_root / "receipt.json")
    if not isinstance(receipt, dict) or receipt.get("schema_version") != RECEIPT_SCHEMA:
        raise ValueError("case_receipt_invalid")
    unsigned = dict(receipt)
    expected_receipt_hash = unsigned.pop("receipt_sha256", None)
    if expected_receipt_hash != canonical_sha256(unsigned):
        raise ValueError("case_receipt_hash_mismatch")
    if receipt.get("case_id") != case_root.name:
        raise ValueError("case_receipt_id_mismatch")
    if (
        receipt.get("study_id") != manifest["study_id"]
        or receipt.get("preregistration_sha256") != manifest["preregistration_sha256"]
    ):
        raise ValueError("case_preregistration_mismatch")

    try:
        request = (case_root / "request.txt").read_bytes()
        baseline = (case_root / "baseline-output.txt").read_bytes()
    except OSError as exc:
        raise ValueError("case_raw_input_missing") from exc
    evidence = _load_json(case_root / "evidence.json")
    source_snapshot = _load_json(case_root / "source-snapshot.json")
    completion_ledger = _load_json(case_root / "completion-ledger.json")
    completion_receipt = _load_json(case_root / "completion-receipt.json")
    execution_receipt = _load_json(case_root / "execution-receipt.json")
    captured = _parse_timestamp(
        receipt.get("captured_at"), error="case_timestamp_invalid"
    )
    preregistered = _parse_timestamp(
        manifest["created_at"], error="preregistration_timestamp_invalid"
    )
    _validate_case_evidence(
        evidence,
        preregistered_at=preregistered,
        captured_at=captured,
        eligible_work_classes=manifest["selection_policy"][
            "eligible_work_classes"
        ],
        observation_ends_at=_observation_end(manifest),
    )
    source_snapshot = _validate_source_snapshot(
        source_snapshot,
        manifest=manifest,
        captured_at=captured,
        repository_root=registration_proof["repository_root"],
        required_ancestor_commit=registration_proof["registration_receipt"][
            "registry_commit"
        ],
        expected_previous_snapshot_sha256=(
            expected_previous_source_snapshot_sha256
        ),
    )
    completion_ledger = _validate_completion_ledger(
        completion_ledger,
        manifest=manifest,
        captured_at=captured,
        previous_entries=previous_ledger_entries,
        source_snapshot=source_snapshot,
    )
    selected_entries = _eligible_completion_entries(completion_ledger, manifest=manifest)
    if len(selected_entries) < expected_ordinal or any(
        selected_entries[expected_ordinal - 1][field] != evidence[field]
        for field in _LEDGER_ENTRY_FIELDS
    ):
        raise ValueError("completion_ledger_selection_mismatch")
    completion_receipt = _validate_completion_receipt(
        completion_receipt,
        manifest=manifest,
        evidence=evidence,
        expected_ordinal=expected_ordinal,
        expected_previous_receipt_sha256=expected_previous_receipt_sha256,
        previous_completed_at=previous_completed_at,
    )
    execution_receipt = _validate_execution_receipt(
        execution_receipt,
        manifest=manifest,
        evidence=evidence,
        request=request,
        baseline_output=baseline,
    )
    if receipt.get("feature_id") != evidence["feature_id"]:
        raise ValueError("case_feature_id_mismatch")
    if receipt.get("request_sha256") != canonical_sha256(request):
        raise ValueError("case_request_hash_mismatch")
    if receipt.get("baseline_output_sha256") != canonical_sha256(baseline):
        raise ValueError("case_baseline_output_hash_mismatch")
    if receipt.get("evidence_sha256") != canonical_sha256(evidence):
        raise ValueError("case_evidence_hash_mismatch")
    if (
        receipt.get("completion_ordinal") != expected_ordinal
        or receipt.get("registration_receipt_sha256")
        != registration_receipt["receipt_sha256"]
        or receipt.get("external_registration_receipt_sha256")
        != registration_proof["registration_receipt"]["receipt_sha256"]
        or receipt.get("source_snapshot_sha256") != source_snapshot["receipt_sha256"]
        or receipt.get("completion_receipt_sha256")
        != completion_receipt["receipt_sha256"]
        or receipt.get("completion_ledger_sha256")
        != completion_ledger["receipt_sha256"]
        or receipt.get("execution_receipt_sha256")
        != execution_receipt["receipt_sha256"]
    ):
        raise ValueError("case_provenance_receipt_mismatch")
    return receipt


def validate_study(
    *,
    manifest: dict[str, Any],
    study_root: Path,
    registration_receipt: dict[str, Any],
    registration_proof: dict[str, Any],
) -> dict[str, Any]:
    manifest = validate_preregistration(manifest)
    registration_receipt = _validate_registration_receipt(
        registration_receipt,
        manifest=manifest,
    )
    registration_proof = _validate_external_registration_proof(
        registration_proof,
        manifest=manifest,
    )
    study_root = Path(study_root)
    bound_manifest_path = study_root / "preregistration.json"
    if bound_manifest_path.exists() and _load_json(bound_manifest_path) != manifest:
        raise ValueError("study_preregistration_mismatch")
    bound_registration_path = study_root / "registration-receipt.json"
    if (
        bound_registration_path.exists()
        and _load_json(bound_registration_path) != registration_receipt
    ):
        raise ValueError("study_registration_receipt_mismatch")
    bound_proof_path = study_root / "registration-proof.json"
    if bound_proof_path.exists() and _load_json(bound_proof_path) != registration_proof:
        raise ValueError("study_registration_proof_mismatch")
    cases_root = study_root / "cases"
    case_roots = (
        sorted(path for path in cases_root.iterdir() if path.is_dir())
        if cases_root.exists()
        else []
    )
    if len(case_roots) > TARGET_CASE_COUNT:
        raise ValueError("study_case_limit_exceeded")
    case_roots.sort(key=_stored_completion_ordinal)
    receipts: list[dict[str, Any]] = []
    previous_ledger_entries: list[dict[str, Any]] = []
    previous_completion_hash: str | None = None
    previous_source_snapshot_hash: str | None = None
    previous_completed_at: datetime | None = None
    for ordinal, case_root in enumerate(case_roots, start=1):
        receipts.append(
            _validate_captured_case(
                case_root,
                manifest=manifest,
                registration_receipt=registration_receipt,
                registration_proof=registration_proof,
                expected_ordinal=ordinal,
                expected_previous_receipt_sha256=previous_completion_hash,
                previous_completed_at=previous_completed_at,
                previous_ledger_entries=previous_ledger_entries,
                expected_previous_source_snapshot_sha256=(
                    previous_source_snapshot_hash
                ),
            )
        )
        completion_receipt = _load_json(case_root / "completion-receipt.json")
        previous_completion_hash = completion_receipt["receipt_sha256"]
        previous_completed_at = _parse_timestamp(
            completion_receipt["completed_at"], error="case_timestamp_invalid"
        )
        previous_ledger_entries = _load_json(case_root / "completion-ledger.json")[
            "entries"
        ]
        previous_source_snapshot_hash = _load_json(
            case_root / "source-snapshot.json"
        )["receipt_sha256"]
    feature_ids = [receipt["feature_id"] for receipt in receipts]
    if len(feature_ids) != len(set(feature_ids)):
        raise ValueError("study_duplicate_feature")
    failure_path = study_root / "failure.json"
    failure = None
    if failure_path.exists():
        latest_case_captured_at = max(
            (
                _parse_timestamp(
                    receipt["captured_at"], error="case_timestamp_invalid"
                )
                for receipt in receipts
            ),
            default=None,
        )
        failure = _validate_failure_receipt(
            _load_json(failure_path),
            manifest=manifest,
            expected_case_receipt_sha256s=[
                receipt["receipt_sha256"] for receipt in receipts
            ],
            latest_case_captured_at=latest_case_captured_at,
        )
    status = (
        failure["status"].lower()
        if failure is not None
        else (
            "capture_complete"
            if len(receipts) == TARGET_CASE_COUNT
            else "collecting"
        )
    )
    report: dict[str, Any] = {
        "schema_version": REPORT_SCHEMA,
        "study_id": manifest["study_id"],
        "preregistration_sha256": manifest["preregistration_sha256"],
        "registration_receipt_sha256": registration_receipt["receipt_sha256"],
        "external_registration_receipt_sha256": registration_proof[
            "registration_receipt"
        ]["receipt_sha256"],
        "status": status,
        "case_count": len(receipts),
        "target_case_count": TARGET_CASE_COUNT,
        "case_receipt_sha256s": [receipt["receipt_sha256"] for receipt in receipts],
    }
    if failure is not None:
        report["failure_receipt_sha256"] = failure["receipt_sha256"]
        report["failure_reason_code"] = failure["reason_code"]
    report["report_sha256"] = canonical_sha256(report)
    return report


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)

    preregister = subparsers.add_parser("preregister")
    preregister.add_argument("--study-id", required=True)
    preregister.add_argument("--created-at", required=True)
    preregister.add_argument("--registration-authority-public-key", required=True)
    preregister.add_argument("--registration-authority", required=True)
    preregister.add_argument("--completion-collector-public-key", required=True)
    preregister.add_argument("--executor-public-key", required=True)
    preregister.add_argument("--source-snapshot-public-key", required=True)
    preregister.add_argument("--source-inventory", required=True)
    preregister.add_argument("--observation-ends-at")
    preregister.add_argument("--authority-custody")
    preregister.add_argument("--restart-parent")
    preregister.add_argument("--output", type=Path, required=True)

    capture = subparsers.add_parser("capture")
    capture.add_argument("--manifest", type=Path, required=True)
    capture.add_argument("--study-root", type=Path, required=True)
    capture.add_argument("--case-id", required=True)
    capture.add_argument("--request", type=Path, required=True)
    capture.add_argument("--baseline-output", type=Path, required=True)
    capture.add_argument("--evidence", type=Path, required=True)
    capture.add_argument("--registration-receipt", type=Path, required=True)
    capture.add_argument("--registration-proof", type=Path, required=True)
    capture.add_argument("--source-snapshot", type=Path, required=True)
    capture.add_argument("--completion-ledger", type=Path, required=True)
    capture.add_argument("--completion-receipt", type=Path, required=True)
    capture.add_argument("--execution-receipt", type=Path, required=True)
    capture.add_argument("--captured-at", required=True)

    validate = subparsers.add_parser("validate")
    validate.add_argument("--manifest", type=Path, required=True)
    validate.add_argument("--study-root", type=Path, required=True)
    validate.add_argument("--registration-receipt", type=Path, required=True)
    validate.add_argument("--registration-proof", type=Path, required=True)
    validate.add_argument("--output", type=Path)

    fail = subparsers.add_parser("fail")
    fail.add_argument("--manifest", type=Path, required=True)
    fail.add_argument("--study-root", type=Path, required=True)
    fail.add_argument("--failure-receipt", type=Path, required=True)

    publish = subparsers.add_parser("publish")
    publish.add_argument("--study-root", type=Path, required=True)
    publish.add_argument("--evidence-root", type=Path, required=True)
    publish.add_argument("--study-id", required=True)

    load = subparsers.add_parser("load")
    load.add_argument("--evidence-root", type=Path, required=True)
    load.add_argument("--study-id", required=True)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    if args.command == "preregister":
        try:
            registration_authority = json.loads(args.registration_authority)
            source_inventory = json.loads(args.source_inventory)
            authority_custody = (
                json.loads(args.authority_custody)
                if args.authority_custody is not None
                else None
            )
            restart_parent = (
                json.loads(args.restart_parent)
                if args.restart_parent is not None
                else None
            )
        except json.JSONDecodeError as exc:
            raise ValueError("preregistration_json_argument_invalid") from exc
        payload = build_preregistration(
            study_id=args.study_id,
            created_at=args.created_at,
            registration_authority_public_key=(args.registration_authority_public_key),
            registration_authority=registration_authority,
            completion_collector_public_key=args.completion_collector_public_key,
            executor_public_key=args.executor_public_key,
            source_snapshot_public_key=args.source_snapshot_public_key,
            source_inventory=source_inventory,
            observation_ends_at=args.observation_ends_at,
            authority_custody=authority_custody,
            restart_parent=restart_parent,
        )
        _write_json(args.output, payload)
        print(json.dumps(payload, ensure_ascii=False, sort_keys=True))
        return 0
    if args.command == "publish":
        index = publish_study_bundle(
            study_root=args.study_root,
            evidence_root=args.evidence_root,
            study_id=args.study_id,
        )
        print(json.dumps(index, ensure_ascii=False, sort_keys=True))
        return 0
    if args.command == "load":
        index = load_study_bundle(
            evidence_root=args.evidence_root,
            study_id=args.study_id,
        )
        print(json.dumps(index, ensure_ascii=False, sort_keys=True))
        return 0
    manifest = validate_preregistration(_load_json(args.manifest))
    if args.command == "fail":
        receipt = record_study_failure(
            manifest=manifest,
            study_root=args.study_root,
            failure_receipt=_load_json(args.failure_receipt),
        )
        print(json.dumps(receipt, ensure_ascii=False, sort_keys=True))
        return 0
    if args.command == "capture":
        receipt = capture_case(
            manifest=manifest,
            study_root=args.study_root,
            case_id=args.case_id,
            request=args.request.read_bytes(),
            baseline_output=args.baseline_output.read_bytes(),
            evidence=_load_json(args.evidence),
            captured_at=args.captured_at,
            registration_receipt=_load_json(args.registration_receipt),
            registration_proof=_load_json(args.registration_proof),
            source_snapshot=_load_json(args.source_snapshot),
            completion_ledger=_load_json(args.completion_ledger),
            completion_receipt=_load_json(args.completion_receipt),
            execution_receipt=_load_json(args.execution_receipt),
        )
        print(json.dumps(receipt, ensure_ascii=False, sort_keys=True))
        return 0
    report = validate_study(
        manifest=manifest,
        study_root=args.study_root,
        registration_receipt=_load_json(args.registration_receipt),
        registration_proof=_load_json(args.registration_proof),
    )
    if args.output:
        _write_json(args.output, report)
    print(json.dumps(report, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
