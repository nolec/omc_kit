#!/usr/bin/env python3
"""Fail-closed preflight helpers for the task-review product-focus pilot."""
from __future__ import annotations

import argparse
import base64
import binascii
import hashlib
import json
import os
import re
import stat
import subprocess
from copy import deepcopy
from datetime import datetime, timedelta
from pathlib import Path, PurePath
from statistics import median
from typing import Any
from urllib.parse import urlsplit

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

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

TRUSTED_EXECUTION_PUBLIC_KEY_ENV = (
    "OMC_TASK_REVIEW_PILOT_TRUSTED_EXECUTION_PUBLIC_KEY"
)
TRUSTED_RECONCILIATION_PUBLIC_KEY_ENV = (
    "OMC_TASK_REVIEW_PILOT_TRUSTED_RECONCILIATION_PUBLIC_KEY"
)
TRUSTED_PERSONA_ADJUDICATION_PUBLIC_KEY_ENV = (
    "OMC_TASK_REVIEW_PERSONA_TRUSTED_ADJUDICATION_PUBLIC_KEY"
)
TRUSTED_PERSONA_STUDY_PUBLIC_KEY_ENV = (
    "OMC_TASK_REVIEW_PERSONA_TRUSTED_STUDY_PUBLIC_KEY"
)
PERSONA_STUDY_ID = "task-review-persona-effectiveness-20260904-v1"
PERSONA_PREREGISTRATION_SHA256 = (
    "0dc5f8d1270162bfc97841525e9191d863c7e6809fbd6d5d8a24e33d5da0ceab"
)
PERSONA_CONTRACT_REVISION = 2
PERSONA_MINIMUM_RELATIVE_REDUCTION = 0.30
PERSONA_MINIMUM_BASELINE_CORRECTION_EVENTS = 3
PERSONA_DECISION_RULES = (
    {"condition": "fatal_violation", "outcome": "STOP", "reason": "fatal_violation"},
    {
        "condition": "provider_execution_absent",
        "outcome": "INCONCLUSIVE",
        "reason": "provider_execution_absent",
    },
    {
        "condition": "completion_noninferiority_failed",
        "outcome": "STOP",
        "reason": "completion_noninferiority_failed",
    },
    {
        "condition": "verification_noninferiority_failed",
        "outcome": "STOP",
        "reason": "verification_noninferiority_failed",
    },
    {
        "condition": "total_intervention_noninferiority_failed",
        "outcome": "STOP",
        "reason": "total_intervention_noninferiority_failed",
    },
    {
        "condition": "wall_clock_noninferiority_failed",
        "outcome": "STOP",
        "reason": "wall_clock_noninferiority_failed",
    },
    {
        "condition": "insufficient_baseline_correction_events",
        "outcome": "INCONCLUSIVE",
        "reason": "insufficient_baseline_correction_events",
    },
    {
        "condition": "correction_reduction_target_missed",
        "outcome": "REDUCE",
        "reason": "correction_reduction_target_missed",
    },
    {"condition": "all_gates_passed", "outcome": "CONTINUE", "reason": None},
)


def _validated_persona_decision_rules() -> tuple[dict[str, Any], ...]:
    rules = PERSONA_DECISION_RULES
    conditions = [rule.get("condition") for rule in rules if isinstance(rule, dict)]
    if (
        len(rules) != 9
        or any(
            not isinstance(rule, dict)
            or set(rule) != {"condition", "outcome", "reason"}
            for rule in rules
        )
        or len(conditions) != len(rules)
        or any(not isinstance(condition, str) for condition in conditions)
        or len(set(conditions)) != len(conditions)
        or conditions[-1] != "all_gates_passed"
        or any(
            not isinstance(rule["outcome"], str)
            or rule["outcome"] not in {"STOP", "INCONCLUSIVE", "REDUCE", "CONTINUE"}
            for rule in rules
        )
        or any(not isinstance(rule["reason"], str) for rule in rules[:-1])
        or rules[-1]["reason"] is not None
    ):
        raise PilotPreflightError("persona_decision_policy_invalid")
    return rules


def _evaluate_persona_decision(signals: dict[str, bool]) -> dict[str, Any]:
    rules = _validated_persona_decision_rules()
    expected = {rule["condition"] for rule in rules[:-1]}
    if set(signals) != expected or any(
        type(value) is not bool for value in signals.values()
    ):
        raise PilotPreflightError("persona_decision_signals_invalid")
    for rule in rules[:-1]:
        if signals[rule["condition"]]:
            return {"status": rule["outcome"], "reason": rule["reason"]}
    return {"status": rules[-1]["outcome"]}


def _persona_pre_metric_decision(terminals: list[dict[str, Any]]) -> dict[str, str]:
    rules = _validated_persona_decision_rules()
    signals = {rule["condition"]: False for rule in rules[:-1]}
    signals["fatal_violation"] = any(
        item["arms"][arm]["fatal_violation"]
        for item in terminals
        for arm in ("omc", "baseline")
    )
    signals["provider_execution_absent"] = any(
        item["arms"][arm]["provider_call_count"] == 0
        for item in terminals
        for arm in ("omc", "baseline")
    )
    return _evaluate_persona_decision(signals)
_RECONCILIATION_EXECUTION_SCHEMAS = {
    "omc-task-review-pilot-readiness/v2": "readiness",
    "omc-task-review-pilot-terminal/v1": "terminal",
    "omc-task-review-pilot-decision/v1": "decision",
}
_RECONCILIATION_EXECUTION_EVIDENCE = ("readiness", "terminal", "decision")


def build_execution_capability_matrix(
    *, source_repository: Path, source_commit: str, pilot_contract_sha256: str
) -> dict[str, Any]:
    """Describe which frozen pilot requirements existing execution surfaces prove."""
    if not re.fullmatch(r"[0-9a-f]{40}", source_commit):
        raise PilotPreflightError("pilot_source_commit_invalid")
    if not re.fullmatch(r"[0-9a-f]{64}", pilot_contract_sha256):
        raise PilotPreflightError("pilot_contract_hash_invalid")
    source_root = Path(
        _git(source_repository, "rev-parse", "--show-toplevel")
    ).resolve()
    execution_root = Path(
        _git(Path(__file__).resolve().parent, "rev-parse", "--show-toplevel")
    ).resolve()
    if source_root != execution_root:
        raise PilotPreflightError("pilot_source_repository_mismatch")
    if not _execution_source_is_clean(execution_root):
        raise PilotPreflightError("pilot_execution_source_dirty")
    if _git(source_root, "rev-parse", "HEAD") != source_commit:
        raise PilotPreflightError("pilot_source_commit_mismatch")
    capabilities = [
        {
            "requirement_id": "R1_ISOLATED_WORKSPACE",
            "status": "SUPPORTED",
            "evidence": "omc_autopilot_workspace.materialize_isolated_clone",
        },
        {
            "requirement_id": "R2_APPROVED_FROZEN_INPUT",
            "status": "ADAPTER_REQUIRED",
            "evidence": "safe pipeline binds instruction/base commit but not pilot DoD/provider/model/reasoning/timeout",
        },
        {
            "requirement_id": "R3_OMC_TASK_REVIEW",
            "status": "ADAPTER_REQUIRED",
            "evidence": "safe pipeline runs task/review prompts, not the $omc-task/$omc-review skill contracts",
        },
        {
            "requirement_id": "R4_BASELINE_ARM",
            "status": "ADAPTER_REQUIRED",
            "evidence": "normalize_review_outcome validates native evidence but does not execute a baseline arm",
        },
        {
            "requirement_id": "R5_COUNTERBALANCED_ORDER",
            "status": "ADAPTER_REQUIRED",
            "evidence": "safe pipeline executes one arm and has no paired arm scheduler",
        },
        {
            "requirement_id": "R6_PAIRED_TERMINAL_RECEIPT",
            "status": "ADAPTER_REQUIRED",
            "evidence": "safe pipeline returns one candidate result, not a sealed two-arm metric receipt",
        },
        {
            "requirement_id": "R7_SHARED_PROVIDER_CONFIGURATION",
            "status": "ADAPTER_REQUIRED",
            "evidence": "safe pipeline confines the codex executor but does not bind provider/model/reasoning across arms",
        },
        {
            "requirement_id": "R8_BOUNDED_ARM_RETRY",
            "status": "ADAPTER_REQUIRED",
            "evidence": "pilot contract allows one retry per arm but safe pipeline has no paired retry ledger",
        },
    ]
    matrix: dict[str, Any] = {
        "schema_version": "omc-task-review-pilot-capability/v1",
        "source_commit": source_commit,
        "pilot_contract_sha256": pilot_contract_sha256,
        "capabilities": capabilities,
    }
    matrix["capability_matrix_sha256"] = _canonical_sha256(matrix)
    return matrix


class PilotPreflightError(ValueError):
    """Raised when pilot evidence cannot support a deterministic decision."""


def _canonical_sha256(value: object) -> str:
    return hashlib.sha256(
        json.dumps(
            value,
            ensure_ascii=True,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()


def _canonical_bytes(value: object) -> bytes:
    return json.dumps(value, ensure_ascii=True, sort_keys=True, separators=(",", ":")).encode("utf-8")


def _valid_public_key(value: object) -> bool:
    if not isinstance(value, str):
        return False
    try:
        return len(base64.b64decode(value, validate=True)) == 32
    except (binascii.Error, ValueError):
        return False


def _trusted_execution_public_key() -> str:
    """Load the executor key from operator-controlled configuration, never receipts."""
    value = os.environ.get(TRUSTED_EXECUTION_PUBLIC_KEY_ENV)
    if not _valid_public_key(value):
        raise PilotPreflightError("trusted_execution_authority_missing")
    return value


def _trusted_reconciliation_public_key() -> str:
    """Load the reconciliation authority from operator-controlled configuration."""
    value = os.environ.get(TRUSTED_RECONCILIATION_PUBLIC_KEY_ENV)
    if not _valid_public_key(value):
        raise PilotPreflightError("reconciliation_authority_missing")
    return value


def _trusted_persona_adjudication_public_key() -> str:
    """Load the blind adjudicator key from independent operator custody."""
    value = os.environ.get(TRUSTED_PERSONA_ADJUDICATION_PUBLIC_KEY_ENV)
    if not _valid_public_key(value):
        raise PilotPreflightError("persona_adjudication_authority_missing")
    if value == _trusted_execution_public_key():
        raise PilotPreflightError("persona_adjudication_authority_not_independent")
    reconciliation_key = os.environ.get(TRUSTED_RECONCILIATION_PUBLIC_KEY_ENV)
    if reconciliation_key is not None and value == reconciliation_key:
        raise PilotPreflightError("persona_adjudication_authority_not_independent")
    return value


def _persona_adjudication_signed_bytes(receipt: dict[str, Any]) -> bytes:
    signed = deepcopy(receipt)
    signed["signature"] = ""
    return _canonical_bytes(signed)


def _persona_arm_mapping_signed_bytes(receipt: dict[str, Any]) -> bytes:
    signed = deepcopy(receipt)
    signed["signature"] = ""
    return _canonical_bytes(signed)


def _persona_study_binding_signed_bytes(receipt: dict[str, Any]) -> bytes:
    signed = deepcopy(receipt)
    signed["signature"] = ""
    signed["reconciliation_signature"] = ""
    return _canonical_bytes(signed)


def _persona_registration_signed_bytes(receipt: dict[str, Any]) -> bytes:
    signed = deepcopy(receipt)
    signed["signature"] = ""
    signed["reconciliation_signature"] = ""
    return _canonical_bytes(signed)


def _persona_enrollment_signed_bytes(receipt: dict[str, Any]) -> bytes:
    signed = deepcopy(receipt)
    signed["signature"] = ""
    return _canonical_bytes(signed)


def _persona_collection_close_signed_bytes(receipt: dict[str, Any]) -> bytes:
    signed = deepcopy(receipt)
    signed["signature"] = ""
    return _canonical_bytes(signed)


def build_persona_signing_payload(
    receipt: Any, *, kind: str,
) -> dict[str, Any]:
    if not isinstance(receipt, dict):
        raise PilotPreflightError("persona_signing_payload_invalid")
    builders = {
        "registration": _persona_registration_signed_bytes,
        "arm_mapping": _persona_arm_mapping_signed_bytes,
        "enrollment": _persona_enrollment_signed_bytes,
        "adjudication": _persona_adjudication_signed_bytes,
        "collection_close": _persona_collection_close_signed_bytes,
    }
    builder = builders.get(kind)
    if builder is None:
        raise PilotPreflightError("persona_signing_payload_kind_invalid")
    required_fields = {
        "registration": {
            "schema_version", "study_id", "t0", "collection_deadline",
            "case_count", "minimum_repository_count",
            "maximum_cases_per_repository", "wall_clock_noninferiority_ratio",
            "preregistration_sha256", "contract_revision",
            "minimum_relative_reduction", "minimum_baseline_correction_events",
            "selection_policy", "arm_order_policy", "arm_mapping_sha256",
            "repositories", "execution_public_key", "study_public_key",
            "reconciliation_public_key", "adjudication_public_key",
            "initial_previous_enrollment_sha256", "user_approved",
            "registered_at", "signer_public_key", "reconciliation_signature",
            "signature",
        },
        "arm_mapping": {
            "schema_version", "study_id", "arm_a", "arm_b", "registered_at",
            "signer_public_key", "signature",
        },
        "enrollment": {
            "schema_version", "study_id", "registration_sha256",
            "previous_enrollment_sha256", "sequence", "session_id",
            "session_created_at", "enrolled_at", "repository_id", "base_commit",
            "work_class", "request_sha256", "dod_sha256", "verification_sha256",
            "state_evidence", "signer_public_key", "signature",
        },
        "adjudication": {
            "schema_version", "signer", "signer_public_key", "study_id", "cases",
            "signature",
        },
        "collection_close": {
            "schema_version", "study_id", "registration_sha256",
            "enrollment_count", "final_enrollment_sha256", "observed_at",
            "signer_public_key", "signature",
        },
    }
    if set(receipt) != required_fields[kind]:
        raise PilotPreflightError("persona_signing_payload_invalid")
    expected_schemas = {
        "registration": "omc-task-review-persona-study-registration/v1",
        "arm_mapping": "omc-task-review-persona-arm-mapping/v1",
        "enrollment": "omc-task-review-persona-enrollment/v1",
        "adjudication": "omc-task-review-persona-adjudication/v2",
        "collection_close": "omc-task-review-persona-collection-close/v1",
    }
    if (
        receipt.get("schema_version") != expected_schemas[kind]
        or receipt.get("study_id") != PERSONA_STUDY_ID
        or receipt.get("signature") != ""
        or not isinstance(receipt.get("signer_public_key"), str)
    ):
        raise PilotPreflightError("persona_signing_payload_invalid")
    if kind == "registration" and (
        receipt.get("reconciliation_signature") != ""
        or receipt.get("preregistration_sha256") != PERSONA_PREREGISTRATION_SHA256
        or receipt.get("contract_revision") != PERSONA_CONTRACT_REVISION
        or receipt.get("minimum_relative_reduction")
        != PERSONA_MINIMUM_RELATIVE_REDUCTION
        or receipt.get("minimum_baseline_correction_events")
        != PERSONA_MINIMUM_BASELINE_CORRECTION_EVENTS
        or receipt.get("wall_clock_noninferiority_ratio") != 1.15
        or receipt.get("user_approved") is not True
    ):
        raise PilotPreflightError("persona_signing_payload_invalid")
    if kind == "arm_mapping":
        arm_a = receipt.get("arm_a")
        arm_b = receipt.get("arm_b")
        if (
            not isinstance(arm_a, str)
            or not isinstance(arm_b, str)
            or arm_a not in {"omc", "baseline"}
            or arm_b not in {"omc", "baseline"}
            or arm_a == arm_b
            or not isinstance(receipt.get("registered_at"), str)
        ):
            raise PilotPreflightError("persona_signing_payload_invalid")
    if kind == "enrollment" and (
        type(receipt.get("sequence")) is not int
        or receipt.get("sequence", 0) < 1
        or receipt.get("work_class") != "implementation"
        or not isinstance(receipt.get("state_evidence"), dict)
    ):
        raise PilotPreflightError("persona_signing_payload_invalid")
    if kind == "adjudication" and (
        receipt.get("signer")
        != "omc-task-review-persona-blind-adjudicator-v1"
        or not isinstance(receipt.get("cases"), list)
    ):
        raise PilotPreflightError("persona_signing_payload_invalid")
    if kind == "collection_close" and (
        type(receipt.get("enrollment_count")) is not int
        or receipt.get("enrollment_count", -1) < 0
        or not isinstance(receipt.get("observed_at"), str)
    ):
        raise PilotPreflightError("persona_signing_payload_invalid")
    payload = builder(receipt)
    return {
        "schema_version": "omc-task-review-persona-signing-payload/v1",
        "artifact_kind": kind,
        "payload_base64": base64.b64encode(payload).decode("ascii"),
        "payload_sha256": hashlib.sha256(payload).hexdigest(),
    }


def _trusted_persona_study_public_key() -> str:
    value = os.environ.get(TRUSTED_PERSONA_STUDY_PUBLIC_KEY_ENV)
    if not _valid_public_key(value):
        raise PilotPreflightError("persona_study_authority_missing")
    other_keys = {
        _trusted_execution_public_key(),
        _trusted_persona_adjudication_public_key(),
    }
    reconciliation_key = _trusted_reconciliation_public_key()
    other_keys.add(reconciliation_key)
    if value in other_keys:
        raise PilotPreflightError("persona_study_authority_not_independent")
    return value


def _reconciliation_authority_signed_bytes(receipt: dict[str, Any]) -> bytes:
    signed = deepcopy(receipt)
    signed["signature"] = ""
    return _canonical_bytes(signed)


def _snapshot_reconciliation_root(root_id: str, path: Path) -> dict[str, Any]:
    if not isinstance(root_id, str) or not re.fullmatch(r"[a-z0-9][a-z0-9-]{0,63}", root_id):
        raise PilotPreflightError("reconciliation_root_id_invalid")
    root = path.resolve()
    if not root.is_dir() or path.is_symlink():
        raise PilotPreflightError("reconciliation_root_missing")
    files: list[dict[str, str]] = []
    evidence: set[str] = set()
    for candidate in sorted(root.rglob("*")):
        if candidate.is_symlink():
            raise PilotPreflightError("reconciliation_root_path_invalid")
        if not candidate.is_file():
            continue
        relative = candidate.relative_to(root).as_posix()
        payload = candidate.read_bytes()
        files.append({"path": relative, "sha256": hashlib.sha256(payload).hexdigest()})
        if candidate.suffix != ".json":
            continue
        try:
            value = json.loads(payload.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise PilotPreflightError("reconciliation_root_json_invalid") from exc
        if isinstance(value, dict):
            evidence_type = _RECONCILIATION_EXECUTION_SCHEMAS.get(
                value.get("schema_version")
            )
            if evidence_type is not None:
                evidence.add(evidence_type)
    manifest = {"files": files}
    return {
        "root_id": root_id,
        "root_path_sha256": hashlib.sha256(str(root).encode("utf-8")).hexdigest(),
        "files": files,
        "root_manifest_sha256": _canonical_sha256(manifest),
        "execution_evidence": sorted(evidence),
    }


def prepare_reconciliation_subject(
    *, pilot_id: str, declared_roots: list[dict[str, Any]], observed_at: str
) -> dict[str, Any]:
    """Snapshot declared roots; this is not a claim about undeclared evidence."""
    if not isinstance(pilot_id, str) or not pilot_id:
        raise PilotPreflightError("reconciliation_pilot_id_invalid")
    try:
        observed = datetime.fromisoformat(observed_at)
    except (TypeError, ValueError) as exc:
        raise PilotPreflightError("reconciliation_observed_at_invalid") from exc
    if observed.tzinfo is None or not isinstance(declared_roots, list) or not declared_roots:
        raise PilotPreflightError("reconciliation_subject_invalid")
    roots: list[dict[str, Any]] = []
    root_ids: set[str] = set()
    for descriptor in declared_roots:
        if not isinstance(descriptor, dict) or set(descriptor) != {"root_id", "path"}:
            raise PilotPreflightError("reconciliation_root_descriptor_invalid")
        root_id = descriptor["root_id"]
        raw_path = descriptor["path"]
        if not isinstance(raw_path, (str, Path)):
            raise PilotPreflightError("reconciliation_root_descriptor_invalid")
        if root_id in root_ids:
            raise PilotPreflightError("reconciliation_root_id_duplicate")
        root_ids.add(root_id)
        roots.append(_snapshot_reconciliation_root(root_id, Path(raw_path)))
    evidence = {
        item for root in roots for item in root["execution_evidence"]
    }
    missing = [
        evidence_type
        for evidence_type in _RECONCILIATION_EXECUTION_EVIDENCE
        if evidence_type not in evidence
    ]
    status = (
        "NO_EXECUTION_EVIDENCE_IN_DECLARED_ROOTS"
        if len(missing) == 3
        else "LOCAL_ARTIFACT_SNAPSHOT_INCOMPLETE"
    )
    subject: dict[str, Any] = {
        "schema_version": "omc-task-review-pilot-reconciliation-subject/v1",
        "pilot_id": pilot_id,
        "observed_at": observed.isoformat(),
        "declared_roots": roots,
        "status": status,
        "missing_execution_evidence": missing,
    }
    subject["reconciliation_subject_sha256"] = _canonical_sha256(subject)
    return subject


def record_reconciliation_receipt(
    subject: dict[str, Any], authority: dict[str, Any]
) -> dict[str, Any]:
    """Attach a trusted external signature to one immutable root observation."""
    if not isinstance(subject, dict) or set(subject) != {
        "schema_version",
        "pilot_id",
        "observed_at",
        "declared_roots",
        "status",
        "missing_execution_evidence",
        "reconciliation_subject_sha256",
    }:
        raise PilotPreflightError("reconciliation_subject_invalid")
    if (
        subject.get("schema_version")
        != "omc-task-review-pilot-reconciliation-subject/v1"
        or not isinstance(subject.get("pilot_id"), str)
        or not subject["pilot_id"]
        or not isinstance(subject.get("declared_roots"), list)
        or not subject["declared_roots"]
        or not isinstance(subject.get("missing_execution_evidence"), list)
    ):
        raise PilotPreflightError("reconciliation_subject_invalid")
    try:
        observed_at = datetime.fromisoformat(str(subject["observed_at"]))
    except ValueError as exc:
        raise PilotPreflightError("reconciliation_subject_invalid") from exc
    if observed_at.tzinfo is None:
        raise PilotPreflightError("reconciliation_subject_invalid")
    root_ids: set[str] = set()
    observed_evidence: set[str] = set()
    for root in subject["declared_roots"]:
        if (
            not isinstance(root, dict)
            or set(root) != {
                "root_id",
                "root_path_sha256",
                "files",
                "root_manifest_sha256",
                "execution_evidence",
            }
            or not isinstance(root.get("root_id"), str)
            or not re.fullmatch(r"[a-z0-9][a-z0-9-]{0,63}", root["root_id"])
            or root["root_id"] in root_ids
            or not re.fullmatch(r"[0-9a-f]{64}", str(root.get("root_path_sha256") or ""))
            or not isinstance(root.get("files"), list)
            or not isinstance(root.get("execution_evidence"), list)
        ):
            raise PilotPreflightError("reconciliation_subject_invalid")
        root_ids.add(root["root_id"])
        files = root["files"]
        if (
            any(
                not isinstance(item, dict)
                or set(item) != {"path", "sha256"}
                or not isinstance(item.get("path"), str)
                or not item["path"]
                or not re.fullmatch(r"[0-9a-f]{64}", str(item.get("sha256") or ""))
                for item in files
            )
            or files != sorted(files, key=lambda item: item["path"])
            or root.get("root_manifest_sha256") != _canonical_sha256({"files": files})
        ):
            raise PilotPreflightError("reconciliation_subject_invalid")
        evidence = root["execution_evidence"]
        if (
            any(
                not isinstance(item, str)
                or item not in _RECONCILIATION_EXECUTION_EVIDENCE
                for item in evidence
            )
            or evidence != sorted(set(evidence))
        ):
            raise PilotPreflightError("reconciliation_subject_invalid")
        observed_evidence.update(evidence)
    expected_missing = [
        evidence_type
        for evidence_type in _RECONCILIATION_EXECUTION_EVIDENCE
        if evidence_type not in observed_evidence
    ]
    expected_status = (
        "NO_EXECUTION_EVIDENCE_IN_DECLARED_ROOTS"
        if len(expected_missing) == len(_RECONCILIATION_EXECUTION_EVIDENCE)
        else "LOCAL_ARTIFACT_SNAPSHOT_INCOMPLETE"
    )
    if (
        subject["missing_execution_evidence"] != expected_missing
        or subject.get("status") != expected_status
    ):
        raise PilotPreflightError("reconciliation_subject_invalid")
    expected_subject_hash = subject.get("reconciliation_subject_sha256")
    if expected_subject_hash != _canonical_sha256(
        {key: value for key, value in subject.items() if key != "reconciliation_subject_sha256"}
    ):
        raise PilotPreflightError("reconciliation_subject_hash_mismatch")
    if subject.get("status") not in {
        "NO_EXECUTION_EVIDENCE_IN_DECLARED_ROOTS",
        "LOCAL_ARTIFACT_SNAPSHOT_INCOMPLETE",
    }:
        raise PilotPreflightError("reconciliation_subject_invalid")
    if (
        not isinstance(authority, dict)
        or set(authority) != {
            "schema_version",
            "signer",
            "signer_public_key",
            "subject_sha256",
            "signature",
        }
        or authority.get("schema_version")
        != "omc-task-review-pilot-reconciliation-authority/v1"
        or authority.get("signer") != "omc-task-review-pilot-reconciliation-v1"
        or authority.get("subject_sha256") != expected_subject_hash
        or authority.get("signer_public_key") != _trusted_reconciliation_public_key()
    ):
        raise PilotPreflightError("reconciliation_authority_mismatch")
    try:
        Ed25519PublicKey.from_public_bytes(
            base64.b64decode(authority["signer_public_key"], validate=True)
        ).verify(
            base64.b64decode(authority["signature"], validate=True),
            _reconciliation_authority_signed_bytes(authority),
        )
    except (InvalidSignature, binascii.Error, ValueError, TypeError) as exc:
        raise PilotPreflightError("reconciliation_authority_signature_invalid") from exc
    receipt: dict[str, Any] = {
        "schema_version": "omc-task-review-pilot-reconciliation/v1",
        "pilot_id": subject["pilot_id"],
        "observed_at": subject["observed_at"],
        "declared_roots": subject["declared_roots"],
        "status": subject["status"],
        "missing_execution_evidence": subject["missing_execution_evidence"],
        "reconciliation_subject_sha256": expected_subject_hash,
        "authority": authority,
    }
    receipt["reconciliation_sha256"] = _canonical_sha256(receipt)
    return receipt


def _parse_reconciliation_root(value: str) -> dict[str, Any]:
    root_id, separator, path = value.partition("=")
    if not separator or not path:
        raise PilotPreflightError("reconciliation_root_descriptor_invalid")
    return {"root_id": root_id, "path": Path(path)}


def _execution_unsigned_digest(receipt: dict[str, Any]) -> str:
    unsigned = deepcopy(receipt)
    unsigned.pop("execution_receipt_sha256", None)
    signoff = unsigned.get("signoff")
    if isinstance(signoff, dict):
        signoff["signature"] = ""
    return _canonical_sha256(unsigned)


def _execution_signed_bytes(receipt: dict[str, Any]) -> bytes:
    signed = deepcopy(receipt)
    signed["signoff"]["signature"] = ""
    return _canonical_bytes(signed)


def _validated_execution_authority(
    value: Any, *, require_trusted_key: bool = False
) -> dict[str, str]:
    """Accept only the executor identity sealed before a pilot case is frozen."""
    if (
        not isinstance(value, dict)
        or set(value) != {
            "schema_version",
            "executor_public_key",
            "execution_authority_sha256",
        }
        or value.get("schema_version")
        != "omc-task-review-pilot-execution-authority/v1"
        or not _valid_public_key(value.get("executor_public_key"))
        or value.get("execution_authority_sha256")
        != _canonical_sha256(
            {
                "schema_version": value.get("schema_version"),
                "executor_public_key": value.get("executor_public_key"),
            }
        )
    ):
        raise PilotPreflightError("execution_authority_invalid")
    if require_trusted_key and value["executor_public_key"] != _trusted_execution_public_key():
        raise PilotPreflightError("trusted_execution_authority_mismatch")
    return {
        "schema_version": value["schema_version"],
        "executor_public_key": value["executor_public_key"],
        "execution_authority_sha256": value["execution_authority_sha256"],
    }


def _validated_execution_readiness(receipt: Any) -> dict[str, Any]:
    if not isinstance(receipt, dict):
        raise PilotPreflightError("execution_readiness_invalid")
    expected_hash = receipt.get("readiness_sha256")
    if expected_hash != _canonical_sha256(
        {key: value for key, value in receipt.items() if key != "readiness_sha256"}
    ):
        raise PilotPreflightError("execution_readiness_hash_mismatch")
    if (
        receipt.get("schema_version") != "omc-task-review-pilot-readiness/v2"
        or receipt.get("status") != "PILOT_READY"
        or receipt.get("provider_call_count") != 0
        or not re.fullmatch(r"[0-9a-f]{64}", str(receipt.get("roster_sha256") or ""))
        or not re.fullmatch(r"[0-9a-f]{64}", str(receipt.get("inventory_sha256") or ""))
    ):
        raise PilotPreflightError("execution_readiness_invalid")
    authority = _validated_execution_authority(
        receipt.get("execution_authority"), require_trusted_key=True
    )
    return {
        "readiness_sha256": expected_hash,
        "execution_authority": authority,
        "roster_sha256": receipt["roster_sha256"],
        "inventory_sha256": receipt["inventory_sha256"],
        "t0": receipt["t0"],
    }


def _pilot_binding(readiness: dict[str, Any]) -> dict[str, str]:
    return {
        "readiness_sha256": readiness["readiness_sha256"],
        "roster_sha256": readiness["roster_sha256"],
        "inventory_sha256": readiness["inventory_sha256"],
        "t0": readiness["t0"],
    }


def _validated_pilot_binding(value: Any) -> dict[str, str]:
    if (
        not isinstance(value, dict)
        or set(value) != {
            "readiness_sha256",
            "roster_sha256",
            "inventory_sha256",
            "t0",
        }
        or any(
            not re.fullmatch(r"[0-9a-f]{64}", str(value.get(field) or ""))
            for field in ("readiness_sha256", "roster_sha256", "inventory_sha256")
        )
    ):
        raise PilotPreflightError("pilot_binding_invalid")
    try:
        parsed = datetime.fromisoformat(str(value["t0"]))
    except ValueError as exc:
        raise PilotPreflightError("pilot_binding_invalid") from exc
    if parsed.tzinfo is None:
        raise PilotPreflightError("pilot_binding_invalid")
    return {field: str(value[field]) for field in value}


def _git(repo: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", "-C", str(repo), *args],
        check=False,
        capture_output=True,
        text=True,
    )
    if result.returncode != 0 or not result.stdout.strip():
        raise PilotPreflightError("repository_git_evidence_unavailable")
    return result.stdout.strip()


def _execution_source_is_clean(repo: Path) -> bool:
    result = subprocess.run(
        ["git", "-C", str(repo), "diff", "--quiet", "HEAD"],
        check=False,
        capture_output=True,
    )
    if result.returncode == 0:
        return True
    if result.returncode == 1:
        return False
    raise PilotPreflightError("repository_git_evidence_unavailable")


def _canonical_origin(raw: str) -> str:
    value = raw.strip()
    scp_match = re.fullmatch(r"(?:[^@/]+@)?([^:]+):(.+)", value)
    if scp_match and "://" not in value:
        host, path = scp_match.groups()
    else:
        parsed = urlsplit(value)
        host = parsed.hostname or ""
        path = parsed.path
    normalized_path = path.strip("/")
    if normalized_path.endswith(".git"):
        normalized_path = normalized_path[:-4]
    if not host or not normalized_path:
        raise PilotPreflightError("repository_origin_invalid")
    return f"{host.casefold()}/{normalized_path}"


def canonical_repository_identity(repo: Path) -> dict[str, str]:
    """Derive a clone-stable identity without trusting caller supplied labels."""
    try:
        origin = _git(repo, "remote", "get-url", "origin")
    except PilotPreflightError as exc:
        raise PilotPreflightError("repository_origin_missing") from exc
    canonical_origin = _canonical_origin(origin)
    roots = _git(repo, "rev-list", "--max-parents=0", "HEAD").splitlines()
    if len(roots) != 1 or not re.fullmatch(r"[0-9a-f]{40}", roots[0]):
        raise PilotPreflightError("repository_root_commit_invalid")
    root_commit = roots[0]
    repository_id = hashlib.sha256(
        f"{canonical_origin}\n{root_commit}".encode("utf-8")
    ).hexdigest()
    return {
        "repository_id": repository_id,
        "canonical_origin": canonical_origin,
        "root_commit": root_commit,
    }


def _is_canonical_repository_origin(value: Any) -> bool:
    if not isinstance(value, str) or not value:
        return False
    try:
        return _canonical_origin(f"https://{value}") == value
    except PilotPreflightError:
        return False


def _session_checkpoint(state_root: Path) -> dict[str, str] | None:
    sessions_root = state_root / "sessions"
    sessions: list[tuple[datetime, str, str]] = []
    if not sessions_root.is_dir():
        return None
    for path in sessions_root.glob("*/session.json"):
        try:
            value = json.loads(path.read_text(encoding="utf-8"))
            session_id = value["session_id"]
            created_at = value["created_at"]
            parsed = datetime.fromisoformat(created_at)
        except (OSError, KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
            raise PilotPreflightError("session_checkpoint_invalid") from exc
        if (
            not isinstance(session_id, str)
            or not session_id
            or not isinstance(created_at, str)
            or parsed.tzinfo is None
        ):
            raise PilotPreflightError("session_checkpoint_invalid")
        sessions.append((parsed, session_id, created_at))
    if not sessions:
        return None
    _, session_id, created_at = max(sessions, key=lambda item: (item[0], item[1]))
    return {"created_at": created_at, "session_id": session_id}


def build_pilot_roster(
    repositories: list[Path],
    *,
    pilot_id: str,
    pilot_contract_sha256: str,
    source_commit: str,
) -> dict[str, Any]:
    if not pilot_id.strip():
        raise PilotPreflightError("pilot_id_missing")
    if not re.fullmatch(r"[0-9a-f]{64}", pilot_contract_sha256):
        raise PilotPreflightError("pilot_contract_hash_invalid")
    if not re.fullmatch(r"[0-9a-f]{40}", source_commit):
        raise PilotPreflightError("pilot_source_commit_invalid")
    entries: list[dict[str, Any]] = []
    identities: set[str] = set()
    for raw_repo in repositories:
        repo = raw_repo.resolve()
        identity = canonical_repository_identity(repo)
        repository_id = identity["repository_id"]
        if repository_id in identities:
            raise PilotPreflightError("repository_identity_duplicate")
        identities.add(repository_id)
        state_root = repo / ".omc" / "state"
        entries.append(
            {
                **identity,
                "repository_root": str(repo),
                "state_root": str(state_root),
                "checkpoint": _session_checkpoint(state_root),
            }
        )
    if len(entries) < 2:
        raise PilotPreflightError("insufficient_roster_repositories")
    payload: dict[str, Any] = {
        "schema_version": "omc-task-review-pilot-roster/v1",
        "pilot_id": pilot_id.strip(),
        "pilot_contract_sha256": pilot_contract_sha256,
        "source_commit": source_commit,
        "repositories": sorted(entries, key=lambda item: item["repository_id"]),
    }
    payload["roster_sha256"] = _canonical_sha256(payload)
    return payload


def validate_pilot_start_receipt(
    receipt: dict[str, Any], *, expected_binding: dict[str, Any]
) -> dict[str, Any]:
    consumed_at = receipt.get("consumed_at")
    try:
        parsed = datetime.fromisoformat(str(consumed_at))
    except ValueError as exc:
        raise PilotPreflightError("pilot_start_receipt_invalid") from exc
    receipt_hash = receipt.get("receipt_sha256")
    hash_payload = {
        key: value for key, value in receipt.items() if key != "receipt_sha256"
    }
    if receipt_hash != _canonical_sha256(hash_payload):
        raise PilotPreflightError("pilot_start_receipt_hash_mismatch")
    if (
        receipt.get("schema_version") != "omc-task-review-pilot-start/v1"
        or receipt.get("action") != "task_review_pilot_start"
        or receipt.get("status") != "consumed"
        or receipt.get("binding") != expected_binding
        or parsed.tzinfo is None
    ):
        raise PilotPreflightError("pilot_start_receipt_invalid")
    return {"binding": dict(expected_binding), "t0": str(consumed_at)}


def _expected_start_binding(
    start_receipt: dict[str, Any], roster: dict[str, Any]
) -> dict[str, Any]:
    """Keep the approval-bound execution authority when constructing readiness."""
    binding = start_receipt.get("binding")
    if not isinstance(binding, dict):
        raise PilotPreflightError("pilot_start_receipt_invalid")
    expected = {
        "session_id": binding.get("session_id"),
        "roster_sha256": roster.get("roster_sha256"),
        "pilot_contract_sha256": roster.get("pilot_contract_sha256"),
        "source_commit": roster.get("source_commit"),
    }
    if "execution_authority" in binding:
        expected["execution_authority"] = binding["execution_authority"]
    return expected


def _git_changed_paths(repo: Path, baseline: str, followup: str) -> list[str]:
    result = subprocess.run(
        ["git", "-C", str(repo), "diff", "--name-only", "-z", baseline, followup],
        check=False,
        capture_output=True,
    )
    if result.returncode != 0:
        raise PilotPreflightError("completion_commit_evidence_invalid")
    return sorted(
        item.decode("utf-8") for item in result.stdout.split(b"\0") if item
    )


def build_inventory_dry_run(
    roster: dict[str, Any], *, t0: str, observed_at: str | None = None
) -> dict[str, Any]:
    try:
        t0_at = datetime.fromisoformat(t0)
    except (TypeError, ValueError) as exc:
        raise PilotPreflightError("pilot_t0_invalid") from exc
    if t0_at.tzinfo is None:
        raise PilotPreflightError("pilot_t0_invalid")
    try:
        observation_at = (
            datetime.now(t0_at.tzinfo)
            if observed_at is None
            else datetime.fromisoformat(observed_at)
        )
    except (TypeError, ValueError) as exc:
        raise PilotPreflightError("pilot_observed_at_invalid") from exc
    if observation_at.tzinfo is None or observation_at < t0_at:
        raise PilotPreflightError("pilot_observed_at_invalid")
    collection_deadline = t0_at + timedelta(days=7)
    raw_repositories = roster.get("repositories")
    if not isinstance(raw_repositories, list) or len(raw_repositories) < 2:
        raise PilotPreflightError("pilot_roster_invalid")
    expected_hash = roster.get("roster_sha256")
    actual_hash = _canonical_sha256(
        {key: value for key, value in roster.items() if key != "roster_sha256"}
    )
    if expected_hash != actual_hash:
        raise PilotPreflightError("pilot_roster_hash_mismatch")

    inventory: list[dict[str, Any]] = []
    terminal_cursors: dict[str, dict[str, str] | None] = {}
    seen_repository_ids: set[str] = set()
    seen_sessions: set[tuple[str, str]] = set()
    for entry in raw_repositories:
        if not isinstance(entry, dict):
            raise PilotPreflightError("pilot_roster_invalid")
        repo = Path(str(entry.get("repository_root", ""))).resolve()
        identity = canonical_repository_identity(repo)
        if any(identity[key] != entry.get(key) for key in identity):
            raise PilotPreflightError("repository_identity_changed")
        repository_id = identity["repository_id"]
        if repository_id in seen_repository_ids:
            raise PilotPreflightError("repository_identity_duplicate")
        seen_repository_ids.add(repository_id)
        state_root = Path(str(entry.get("state_root", ""))).resolve()
        if state_root != repo / ".omc" / "state":
            raise PilotPreflightError("repository_state_root_changed")
        checkpoint = entry.get("checkpoint")
        checkpoint_key = (
            (datetime.min.replace(tzinfo=t0_at.tzinfo), "")
            if checkpoint is None
            else (datetime.fromisoformat(checkpoint["created_at"]), checkpoint["session_id"])
        )
        observed: list[tuple[datetime, str, Path, dict[str, Any]]] = []
        sessions_root = state_root / "sessions"
        for session_path in sessions_root.glob("*/session.json") if sessions_root.is_dir() else ():
            try:
                session = json.loads(session_path.read_text(encoding="utf-8"))
                session_id = session["session_id"]
                created_at = datetime.fromisoformat(session["created_at"])
            except (OSError, KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
                raise PilotPreflightError("session_inventory_invalid") from exc
            if created_at.tzinfo is None or not isinstance(session_id, str):
                raise PilotPreflightError("session_inventory_invalid")
            if (created_at, session_id) <= checkpoint_key or created_at <= t0_at:
                continue
            if session_path.parent.name != session_id:
                raise PilotPreflightError("session_directory_mismatch")
            session_key = (repository_id, session_id)
            if session_key in seen_sessions:
                raise PilotPreflightError("session_identity_duplicate")
            seen_sessions.add(session_key)
            observed.append((created_at, session_id, session_path, session))
        observed.sort(key=lambda item: (item[0], item[1]))
        terminal_cursors[repository_id] = (
            {"created_at": observed[-1][0].isoformat(), "session_id": observed[-1][1]}
            if observed
            else checkpoint
        )
        for created_at, session_id, session_path, session in observed:
            item: dict[str, Any] = {
                "session_id": session_id,
                "created_at": created_at.isoformat(),
                "repository_id": repository_id,
                "eligible": False,
            }
            completion_path = session_path.with_name("completion.json")
            if created_at > observation_at:
                item["disposition"] = "future_session_timestamp"
            elif created_at > collection_deadline:
                item["disposition"] = "collection_window_expired"
            elif session.get("work_class") != "implementation":
                item["disposition"] = "classification_review_required"
            elif not completion_path.is_file():
                item["disposition"] = "completion_receipt_missing"
            else:
                try:
                    completion = json.loads(completion_path.read_text(encoding="utf-8"))
                    baseline = completion["baseline_commit"]
                    followup = completion["followup_commit"]
                    changed_paths = completion["changed_paths"]
                except (OSError, KeyError, TypeError, json.JSONDecodeError):
                    item["disposition"] = "completion_receipt_invalid"
                else:
                    if (
                        completion.get("session_id") != session_id
                        or completion.get("work_class") != "implementation"
                    ):
                        item["disposition"] = "classification_review_required"
                    else:
                        try:
                            actual_paths = _git_changed_paths(repo, baseline, followup)
                        except PilotPreflightError:
                            item["disposition"] = "completion_commit_evidence_invalid"
                        else:
                            if not actual_paths or actual_paths != sorted(changed_paths):
                                item["disposition"] = "completion_changed_paths_mismatch"
                            else:
                                item.update(
                                    {
                                        "eligible": True,
                                        "disposition": "eligible",
                                        "baseline_commit": baseline,
                                        "followup_commit": followup,
                                        "changed_paths": actual_paths,
                                    }
                                )
            inventory.append(item)
    inventory.sort(key=lambda item: (item["created_at"], item["session_id"]))
    eligible = [item for item in inventory if item["eligible"]][:3]
    diverse = len({item["repository_id"] for item in eligible}) >= 2
    status = (
        "PILOT_READY"
        if len(eligible) == 3 and diverse
        else (
            "STOP_ELIGIBILITY_DIVERSITY"
            if len(eligible) == 3
            else (
                "STOP_COLLECTION_WINDOW_EXPIRED"
                if observation_at > collection_deadline
                else "WAITING_FOR_CASES"
            )
        )
    )
    report: dict[str, Any] = {
        "schema_version": "omc-task-review-pilot-inventory/v1",
        "roster_sha256": expected_hash,
        "t0": t0,
        "observed_at": observation_at.isoformat(),
        "collection_deadline": collection_deadline.isoformat(),
        "status": status,
        "provider_call_count": 0,
        "inventory": inventory,
        "scanned_session_ids": [item["session_id"] for item in inventory],
        "terminal_cursors": terminal_cursors,
        "selected_cases": eligible if status == "PILOT_READY" else [],
    }
    report["inventory_sha256"] = _canonical_sha256(report)
    return report


def build_readiness_receipt(
    roster: dict[str, Any],
    start: dict[str, Any],
    inventory: dict[str, Any],
) -> dict[str, Any]:
    roster_hash = roster.get("roster_sha256")
    if roster_hash != _canonical_sha256(
        {key: value for key, value in roster.items() if key != "roster_sha256"}
    ):
        raise PilotPreflightError("readiness_roster_hash_mismatch")
    inventory_hash = inventory.get("inventory_sha256")
    if inventory_hash != _canonical_sha256(
        {key: value for key, value in inventory.items() if key != "inventory_sha256"}
    ):
        raise PilotPreflightError("readiness_inventory_hash_mismatch")
    if inventory.get("provider_call_count") != 0:
        raise PilotPreflightError("readiness_provider_call_detected")
    if inventory.get("t0") != start.get("t0"):
        raise PilotPreflightError("readiness_t0_mismatch")
    try:
        t0_at = datetime.fromisoformat(str(start.get("t0")))
        deadline = datetime.fromisoformat(str(inventory.get("collection_deadline")))
        observed_at = datetime.fromisoformat(str(inventory.get("observed_at")))
    except ValueError as exc:
        raise PilotPreflightError("readiness_time_window_invalid") from exc
    if (
        t0_at.tzinfo is None
        or deadline.tzinfo is None
        or observed_at.tzinfo is None
        or deadline != t0_at + timedelta(days=7)
        or observed_at < t0_at
    ):
        raise PilotPreflightError("readiness_time_window_invalid")
    raw_inventory = inventory.get("inventory")
    selected_cases = inventory.get("selected_cases")
    if not isinstance(raw_inventory, list) or not isinstance(selected_cases, list):
        raise PilotPreflightError("readiness_selected_cases_invalid")
    repository_ids = {
        entry.get("repository_id")
        for entry in roster.get("repositories", [])
        if isinstance(entry, dict)
    }
    eligible: list[dict[str, Any]] = []
    seen_case_ids: set[tuple[object, object]] = set()
    previous_key: tuple[datetime, str] | None = None
    for item in raw_inventory:
        if not isinstance(item, dict):
            raise PilotPreflightError("readiness_selected_case_invalid")
        try:
            created_at = datetime.fromisoformat(str(item.get("created_at")))
        except ValueError as exc:
            raise PilotPreflightError("readiness_selected_case_invalid") from exc
        session_id = item.get("session_id")
        repository_id = item.get("repository_id")
        if (
            created_at.tzinfo is None
            or not isinstance(session_id, str)
            or not session_id
            or repository_id not in repository_ids
        ):
            raise PilotPreflightError("readiness_selected_case_invalid")
        order_key = (created_at, session_id)
        if previous_key is not None and order_key < previous_key:
            raise PilotPreflightError("readiness_inventory_not_chronological")
        previous_key = order_key
        case_id = (repository_id, session_id)
        if case_id in seen_case_ids:
            raise PilotPreflightError("readiness_selected_case_duplicate")
        seen_case_ids.add(case_id)
        if item.get("eligible") is True:
            if not (t0_at < created_at <= min(deadline, observed_at)):
                raise PilotPreflightError("readiness_selected_case_invalid")
            eligible.append(item)
    expected_cases = eligible[:3]
    if len(expected_cases) != 3 or selected_cases != expected_cases:
        raise PilotPreflightError("readiness_selected_cases_invalid")
    if len({item["repository_id"] for item in expected_cases}) < 2:
        raise PilotPreflightError("readiness_repository_diversity_invalid")
    if (
        inventory.get("status") != "PILOT_READY"
        or start.get("binding", {}).get("roster_sha256") != roster_hash
        or inventory.get("roster_sha256") != roster_hash
        or inventory.get("schema_version") != "omc-task-review-pilot-inventory/v1"
        or not re.fullmatch(r"[0-9a-f]{64}", str(inventory_hash or ""))
    ):
        raise PilotPreflightError("readiness_binding_mismatch")
    authority = _validated_execution_authority(
        start.get("binding", {}).get("execution_authority"), require_trusted_key=True
    )
    receipt: dict[str, Any] = {
        "schema_version": "omc-task-review-pilot-readiness/v2",
        "status": "PILOT_READY",
        "roster_sha256": roster_hash,
        "inventory_sha256": inventory["inventory_sha256"],
        "t0": start.get("t0"),
        "provider_call_count": 0,
        "execution_authority": authority,
    }
    receipt["readiness_sha256"] = _canonical_sha256(receipt)
    return receipt


def write_json_no_replace(path: Path, value: dict[str, Any]) -> None:
    """Publish canonical pilot evidence once and durably."""
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = (
        json.dumps(value, ensure_ascii=True, sort_keys=True, separators=(",", ":"))
        + "\n"
    ).encode("utf-8")
    try:
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except FileExistsError as exc:
        raise PilotPreflightError("pilot_evidence_already_exists") from exc
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        parent_fd = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(parent_fd)
        finally:
            os.close(parent_fd)
    except Exception:
        path.unlink(missing_ok=True)
        raise


def _read_json_object(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise PilotPreflightError("pilot_evidence_invalid") from exc
    if not isinstance(value, dict):
        raise PilotPreflightError("pilot_evidence_invalid")
    return value


def freeze_case(
    case: dict[str, Any], *, readiness_receipt: dict[str, Any]
) -> dict[str, Any]:
    """Bind every execution input before either paired arm can be prepared."""
    readiness = _validated_execution_readiness(readiness_receipt)
    preflight_case(case)
    # Serialize once so later caller mutations cannot alter the frozen receipt.
    frozen_case = json.loads(json.dumps(case, ensure_ascii=True, sort_keys=True))
    # A caller-provided executor key must never become a trust anchor.
    frozen_case.pop("execution_signer_public_key", None)
    receipt: dict[str, Any] = {
        "schema_version": "omc-task-review-pilot-case/v2",
        "readiness_sha256": readiness["readiness_sha256"],
        "execution_authority": readiness["execution_authority"],
        "pilot_binding": _pilot_binding(readiness),
        "case": frozen_case,
    }
    receipt["case_sha256"] = _canonical_sha256(receipt)
    return receipt


def freeze_persona_case(
    case: dict[str, Any], *, registration_receipt: dict[str, Any],
    arm_mapping_receipt: dict[str, Any],
) -> dict[str, Any]:
    """Freeze one persona case against the fresh study registration, not v2 readiness."""
    registration = _validated_persona_registration(
        registration_receipt, arm_mapping_receipt=arm_mapping_receipt
    )
    preflight_case(case)
    authority = {
        "schema_version": "omc-task-review-pilot-execution-authority/v1",
        "executor_public_key": registration["execution_public_key"],
    }
    authority["execution_authority_sha256"] = _canonical_sha256(authority)
    receipt: dict[str, Any] = {
        "schema_version": "omc-task-review-persona-case/v1",
        "registration_sha256": _canonical_sha256(registration),
        "execution_authority": authority,
        "case": json.loads(json.dumps(case, ensure_ascii=True, sort_keys=True)),
    }
    receipt["case_sha256"] = _canonical_sha256(receipt)
    return receipt


def _validated_persona_frozen_case(
    receipt: Any, *, registration_sha256: str
) -> dict[str, Any]:
    if not isinstance(receipt, dict) or set(receipt) != {
        "schema_version", "registration_sha256", "execution_authority", "case",
        "case_sha256",
    }:
        raise PilotPreflightError("persona_frozen_case_invalid")
    if (
        receipt.get("schema_version") != "omc-task-review-persona-case/v1"
        or receipt.get("registration_sha256") != registration_sha256
        or receipt.get("case_sha256")
        != _canonical_sha256(
            {key: value for key, value in receipt.items() if key != "case_sha256"}
        )
    ):
        raise PilotPreflightError("persona_frozen_case_invalid")
    case = receipt.get("case")
    if not isinstance(case, dict):
        raise PilotPreflightError("persona_frozen_case_invalid")
    preflight_case(case)
    authority = _validated_execution_authority(
        receipt.get("execution_authority"), require_trusted_key=True
    )
    return {"case": case, "execution_authority": authority}


def _validated_frozen_case(receipt: dict[str, Any]) -> dict[str, Any]:
    if receipt.get("schema_version") != "omc-task-review-pilot-case/v2":
        raise PilotPreflightError("frozen_case_schema_invalid")
    if not re.fullmatch(r"[0-9a-f]{64}", str(receipt.get("readiness_sha256") or "")):
        raise PilotPreflightError("readiness_hash_invalid")
    expected_hash = receipt.get("case_sha256")
    actual_hash = _canonical_sha256(
        {key: value for key, value in receipt.items() if key != "case_sha256"}
    )
    if expected_hash != actual_hash:
        raise PilotPreflightError("frozen_case_hash_mismatch")
    case = receipt.get("case")
    if not isinstance(case, dict):
        raise PilotPreflightError("frozen_case_invalid")
    preflight_case(case)
    authority = _validated_execution_authority(
        receipt.get("execution_authority"), require_trusted_key=True
    )
    pilot_binding = _validated_pilot_binding(receipt.get("pilot_binding"))
    if receipt["readiness_sha256"] != pilot_binding["readiness_sha256"]:
        raise PilotPreflightError("pilot_binding_mismatch")
    return {
        "case": case,
        "execution_authority": authority,
        "pilot_binding": pilot_binding,
    }


def _build_paired_dry_run(
    receipt: dict[str, Any], *, case_position: int, maximum_position: int,
    schema_version: str, study_binding_sha256: str | None = None,
) -> dict[str, Any]:
    """Prepare, but never execute, the two arms under identical frozen inputs."""
    if not isinstance(case_position, int) or isinstance(case_position, bool) or not 1 <= case_position <= maximum_position:
        raise PilotPreflightError("invalid_case_position")
    frozen = _validated_frozen_case(receipt)
    case = frozen["case"]
    configuration = {
        key: case[key]
        for key in ("provider", "model", "reasoning", "timeout_sec", "verification_command")
    }
    arm_order = ["omc", "baseline"] if case_position % 2 else ["baseline", "omc"]
    arms = [
        {
            "arm": arm,
            "configuration": dict(configuration),
            "execution_status": "NOT_EXECUTED",
        }
        for arm in arm_order
    ]
    dry_run: dict[str, Any] = {
        "schema_version": schema_version,
        "case_sha256": receipt["case_sha256"],
        "execution_authority": frozen["execution_authority"],
        "execution_signer_public_key": frozen["execution_authority"]["executor_public_key"],
        "pilot_binding": frozen["pilot_binding"],
        "case_position": case_position,
        "arm_order": arm_order,
        "arms": arms,
        "provider_call_count": 0,
    }
    if schema_version == "omc-task-review-persona-paired-dry-run/v1":
        if not re.fullmatch(r"[0-9a-f]{64}", str(study_binding_sha256 or "")):
            raise PilotPreflightError("persona_study_binding_hash_invalid")
        dry_run["study_binding_sha256"] = study_binding_sha256
        dry_run["case_source"] = {
            "repository_id": case["repository_id"],
            "base_commit": case["base_commit"],
        }
    dry_run["dry_run_sha256"] = _canonical_sha256(dry_run)
    if schema_version == "omc-task-review-persona-paired-dry-run/v1":
        return _validated_paired_dry_run(dry_run)
    return dry_run


def build_paired_dry_run(
    receipt: dict[str, Any], *, case_position: int
) -> dict[str, Any]:
    return _build_paired_dry_run(
        receipt, case_position=case_position, maximum_position=3,
        schema_version="omc-task-review-pilot-paired-dry-run/v1",
    )


def build_persona_paired_dry_run(
    receipt: dict[str, Any], *, case_position: int, study_binding_sha256: str
) -> dict[str, Any]:
    return _build_paired_dry_run(
        receipt, case_position=case_position, maximum_position=10,
        schema_version="omc-task-review-persona-paired-dry-run/v1",
        study_binding_sha256=study_binding_sha256,
    )


def build_enrolled_persona_paired_dry_run(
    receipt: dict[str, Any], *, case_position: int,
    registration_receipt: dict[str, Any],
    enrollment_receipts: list[dict[str, Any]],
    arm_mapping_receipt: dict[str, Any], artifact_root: Path,
) -> dict[str, Any]:
    """Prepare one persona pair only after its chronological enrollment is proven."""
    enrollments = validate_persona_enrollment_chain(
        registration_receipt,
        enrollment_receipts,
        arm_mapping_receipt=arm_mapping_receipt,
        artifact_root=artifact_root,
        require_complete=False,
    )
    if len(enrollments) != case_position:
        raise PilotPreflightError("persona_enrollment_position_invalid")
    enrollment = enrollments[-1]
    registration_sha256 = _canonical_sha256(registration_receipt)
    frozen_receipt = _validated_persona_frozen_case(
        receipt, registration_sha256=registration_sha256
    )
    frozen = frozen_receipt["case"]
    expected = {
        "repository_id": frozen["repository_id"],
        "base_commit": frozen["base_commit"],
        "request_sha256": _canonical_sha256(frozen["request"]),
        "dod_sha256": _canonical_sha256(frozen["dod"]),
        "verification_sha256": _canonical_sha256(frozen["verification_command"]),
    }
    if any(enrollment.get(key) != value for key, value in expected.items()):
        raise PilotPreflightError("persona_enrollment_case_binding_mismatch")
    configuration = {
        key: frozen[key]
        for key in ("provider", "model", "reasoning", "timeout_sec", "verification_command")
    }
    arm_order = ["omc", "baseline"] if case_position % 2 else ["baseline", "omc"]
    dry_run: dict[str, Any] = {
        "schema_version": "omc-task-review-persona-paired-dry-run/v1",
        "case_sha256": receipt["case_sha256"],
        "execution_authority": frozen_receipt["execution_authority"],
        "execution_signer_public_key": frozen_receipt["execution_authority"]["executor_public_key"],
        "case_position": case_position,
        "arm_order": arm_order,
        "arms": [
            {"arm": arm, "configuration": dict(configuration), "execution_status": "NOT_EXECUTED"}
            for arm in arm_order
        ],
        "provider_call_count": 0,
        "study_binding_sha256": registration_sha256,
        "registration_sha256": registration_sha256,
        "enrollment_sha256": _canonical_sha256(enrollment),
        "enrollment_session_id": enrollment["session_id"],
        "case_source": {
            "repository_id": frozen["repository_id"],
            "base_commit": frozen["base_commit"],
        },
    }
    dry_run["dry_run_sha256"] = _canonical_sha256(dry_run)
    return _validated_paired_dry_run(dry_run)


def _validated_paired_dry_run(receipt: Any) -> dict[str, Any]:
    if (
        not isinstance(receipt, dict)
        or receipt.get("schema_version") not in {
            "omc-task-review-pilot-paired-dry-run/v1",
            "omc-task-review-persona-paired-dry-run/v1",
        }
        or not re.fullmatch(r"[0-9a-f]{64}", str(receipt.get("case_sha256") or ""))
    ):
        raise PilotPreflightError("paired_dry_run_schema_invalid")
    expected_hash = receipt.get("dry_run_sha256")
    actual_hash = _canonical_sha256(
        {key: value for key, value in receipt.items() if key != "dry_run_sha256"}
    )
    if expected_hash != actual_hash:
        raise PilotPreflightError("paired_dry_run_hash_mismatch")
    if receipt.get("provider_call_count") != 0:
        raise PilotPreflightError("paired_dry_run_provider_call_detected")
    if not _valid_public_key(receipt.get("execution_signer_public_key")):
        raise PilotPreflightError("paired_dry_run_execution_signer_invalid")
    authority = _validated_execution_authority(
        receipt.get("execution_authority"), require_trusted_key=True
    )
    if receipt["execution_signer_public_key"] != authority["executor_public_key"]:
        raise PilotPreflightError("paired_dry_run_execution_authority_mismatch")
    persona_enrolled = (
        receipt.get("schema_version") == "omc-task-review-persona-paired-dry-run/v1"
        and "registration_sha256" in receipt
    )
    if persona_enrolled:
        if (
            receipt.get("study_binding_sha256") != receipt.get("registration_sha256")
            or not re.fullmatch(r"[0-9a-f]{64}", str(receipt.get("enrollment_sha256") or ""))
            or not isinstance(receipt.get("enrollment_session_id"), str)
            or not receipt["enrollment_session_id"]
        ):
            raise PilotPreflightError("persona_enrollment_binding_invalid")
    else:
        _validated_pilot_binding(receipt.get("pilot_binding"))
    position = receipt.get("case_position")
    maximum_position = (
        10 if receipt["schema_version"] == "omc-task-review-persona-paired-dry-run/v1" else 3
    )
    if not isinstance(position, int) or isinstance(position, bool) or not 1 <= position <= maximum_position:
        raise PilotPreflightError("invalid_case_position")
    if (
        receipt["schema_version"] == "omc-task-review-persona-paired-dry-run/v1"
        and (
            not re.fullmatch(
                r"[0-9a-f]{64}", str(receipt.get("study_binding_sha256") or "")
            )
            or not isinstance(receipt.get("case_source"), dict)
            or set(receipt["case_source"]) != {"repository_id", "base_commit"}
            or not isinstance(receipt["case_source"].get("repository_id"), str)
            or not re.fullmatch(
                r"[0-9a-f]{40}", str(receipt["case_source"].get("base_commit") or "")
            )
        )
    ):
        raise PilotPreflightError("persona_study_binding_hash_invalid")
    arms = receipt.get("arms")
    if (
        not isinstance(arms, list)
        or len(arms) != 2
        or not all(
            isinstance(arm, dict)
            and isinstance(arm.get("arm"), str)
            and isinstance(arm.get("configuration"), dict)
            for arm in arms
        )
        or {arm["arm"] for arm in arms} != {"omc", "baseline"}
    ):
        raise PilotPreflightError("paired_dry_run_arms_invalid")
    expected_order = ["omc", "baseline"] if position % 2 else ["baseline", "omc"]
    if receipt.get("arm_order") != expected_order or [arm["arm"] for arm in arms] != expected_order:
        raise PilotPreflightError("paired_dry_run_order_invalid")
    configurations = [arm["configuration"] for arm in arms]
    if configurations[0] != configurations[1]:
        raise PilotPreflightError("paired_configuration_mismatch")
    return receipt


def _read_bound_artifact(
    result: dict[str, Any], *, artifact_root: Path
) -> tuple[str, str, bytes]:
    """Read one bound regular file without following paths outside its root."""
    relative_path = result.get("raw_output_path")
    parts = PurePath(relative_path).parts if isinstance(relative_path, str) else ()
    if (
        not isinstance(relative_path, str)
        or not relative_path
        or PurePath(relative_path).is_absolute()
        or len(parts) != 1
        or any(part in {"", ".", ".."} for part in parts)
    ):
        raise PilotPreflightError("runner_output_path_invalid")
    try:
        root_fd = os.open(artifact_root.resolve(), os.O_RDONLY | os.O_DIRECTORY)
    except OSError as exc:
        raise PilotPreflightError("runner_artifact_root_missing") from exc
    try:
        try:
            output_fd = os.open(relative_path, os.O_RDONLY | os.O_NOFOLLOW, dir_fd=root_fd)
        except FileNotFoundError as exc:
            raise PilotPreflightError("runner_output_missing") from exc
        except OSError as exc:
            raise PilotPreflightError("runner_output_path_invalid") from exc
        try:
            if not stat.S_ISREG(os.fstat(output_fd).st_mode):
                raise PilotPreflightError("runner_output_path_invalid")
            chunks: list[bytes] = []
            while chunk := os.read(output_fd, 64 * 1024):
                chunks.append(chunk)
        finally:
            os.close(output_fd)
    finally:
        os.close(root_fd)
    payload = b"".join(chunks)
    return relative_path, hashlib.sha256(payload).hexdigest(), payload


def _read_runner_output(result: dict[str, Any], *, artifact_root: Path) -> tuple[str, str]:
    """Read one runner-owned output file without following paths outside its root."""
    relative_path, payload_sha256, _payload = _read_bound_artifact(
        result, artifact_root=artifact_root
    )
    return relative_path, payload_sha256


def _read_execution_receipt_file(
    path: Path, *, artifact_root: Path
) -> tuple[str, str, dict[str, Any]]:
    """Load a signed receipt from the runner root without path traversal or symlinks."""
    root = artifact_root.resolve()
    candidate = Path(path)
    if candidate.is_absolute():
        try:
            relative_path = str(candidate.relative_to(root))
        except ValueError as exc:
            raise PilotPreflightError("execution_receipt_path_invalid") from exc
    else:
        relative_path = str(candidate)
    parts = PurePath(relative_path).parts
    if (
        not relative_path
        or len(parts) != 1
        or PurePath(relative_path).is_absolute()
        or any(part in {"", ".", ".."} for part in parts)
    ):
        raise PilotPreflightError("execution_receipt_path_invalid")
    root_fd: int | None = None
    try:
        root_fd = os.open(root, os.O_RDONLY | os.O_DIRECTORY)
        receipt_fd = os.open(relative_path, os.O_RDONLY | os.O_NOFOLLOW, dir_fd=root_fd)
    except FileNotFoundError as exc:
        raise PilotPreflightError("execution_receipt_missing") from exc
    except OSError as exc:
        raise PilotPreflightError("execution_receipt_path_invalid") from exc
    finally:
        if root_fd is not None:
            os.close(root_fd)
    try:
        if not stat.S_ISREG(os.fstat(receipt_fd).st_mode):
            raise PilotPreflightError("execution_receipt_path_invalid")
        chunks: list[bytes] = []
        while chunk := os.read(receipt_fd, 64 * 1024):
            chunks.append(chunk)
    finally:
        os.close(receipt_fd)
    payload = b"".join(chunks)
    try:
        value = json.loads(payload.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise PilotPreflightError("execution_receipt_invalid") from exc
    if not isinstance(value, dict):
        raise PilotPreflightError("execution_receipt_invalid")
    return relative_path, hashlib.sha256(payload).hexdigest(), value


def _validated_execution_receipt(
    receipt: dict[str, Any], *, prepared: dict[str, Any], artifact_root: Path
) -> dict[str, Any]:
    """Validate the artifact written by an execution adapter, never CLI metrics."""
    if receipt.get("schema_version") != "omc-task-review-pilot-execution/v2":
        raise PilotPreflightError("execution_receipt_schema_invalid")
    signoff = receipt.get("signoff")
    expected_public_key = prepared["execution_signer_public_key"]
    if (
        not isinstance(signoff, dict)
        or set(signoff) != {"signer", "signer_public_key", "signature"}
        or signoff.get("signer") != "omc-task-review-pilot-executor-v1"
        or signoff.get("signer_public_key") != expected_public_key
        or not _valid_public_key(expected_public_key)
    ):
        raise PilotPreflightError("execution_receipt_signoff_invalid")
    expected_hash = receipt.get("execution_receipt_sha256")
    actual_hash = _execution_unsigned_digest(receipt)
    if expected_hash != actual_hash:
        raise PilotPreflightError("execution_receipt_hash_mismatch")
    try:
        Ed25519PublicKey.from_public_bytes(
            base64.b64decode(expected_public_key, validate=True)
        ).verify(
            base64.b64decode(signoff["signature"], validate=True),
            _execution_signed_bytes(receipt),
        )
    except (InvalidSignature, binascii.Error, ValueError, TypeError) as exc:
        raise PilotPreflightError("execution_receipt_signature_invalid") from exc
    arm = receipt.get("arm")
    configurations = {item["arm"]: item["configuration"] for item in prepared["arms"]}
    if (
        arm not in configurations
        or receipt.get("dry_run_sha256") != prepared["dry_run_sha256"]
        or receipt.get("case_sha256") != prepared["case_sha256"]
        or receipt.get("configuration") != configurations[arm]
    ):
        raise PilotPreflightError("execution_receipt_binding_mismatch")
    result = receipt.get("result")
    if not isinstance(result, dict):
        raise PilotPreflightError("execution_receipt_result_invalid")
    if (
        isinstance(result.get("provider_call_count"), bool)
        or not isinstance(result.get("provider_call_count"), int)
        or result["provider_call_count"] < 0
        or any(not isinstance(result.get(key), bool) for key in ("verification_passed", "fatal_violation"))
        or result.get("review_outcome") not in {"approved", "blocked"}
        or isinstance(result.get("elapsed_seconds"), bool)
        or not isinstance(result.get("elapsed_seconds"), (int, float))
        or result["elapsed_seconds"] < 0
        or any(
            isinstance(result.get(key), bool)
            or not isinstance(result.get(key), int)
            or result[key] < 0
            for key in ("user_intervention", "rework_count")
        )
    ):
        raise PilotPreflightError("execution_receipt_result_invalid")
    _path, output_sha256 = _read_runner_output(result, artifact_root=artifact_root)
    if output_sha256 != result.get("raw_output_sha256"):
        raise PilotPreflightError("runner_output_hash_mismatch")
    return receipt


def build_runner_arm_receipt(
    dry_run: dict[str, Any], execution_receipt_path: Path, *, artifact_root: Path
) -> dict[str, Any]:
    """Create an arm receipt from an execution-adapter artifact and frozen config."""
    prepared = _validated_paired_dry_run(dry_run)
    receipt_path, receipt_sha256, execution_receipt = _read_execution_receipt_file(
        execution_receipt_path, artifact_root=artifact_root
    )
    execution = _validated_execution_receipt(
        execution_receipt, prepared=prepared, artifact_root=artifact_root
    )
    arm = execution["arm"]
    configuration = execution["configuration"]
    normalized = dict(execution["result"])
    receipt: dict[str, Any] = {
        "schema_version": "omc-task-review-pilot-runner-arm/v1",
        "dry_run_sha256": prepared["dry_run_sha256"],
        "case_sha256": prepared["case_sha256"],
        "arm": arm,
        "artifact_root": str(artifact_root.resolve()),
        "configuration": dict(configuration),
        "configuration_sha256": _canonical_sha256(configuration),
        "execution_receipt": {
            "path": receipt_path,
            "file_sha256": receipt_sha256,
            "execution_receipt_sha256": execution["execution_receipt_sha256"],
        },
        "result": normalized,
    }
    receipt["arm_receipt_sha256"] = _canonical_sha256(receipt)
    return receipt


def _validated_runner_arm_receipt(
    receipt: dict[str, Any], *, prepared: dict[str, Any]
) -> dict[str, Any]:
    if receipt.get("schema_version") != "omc-task-review-pilot-runner-arm/v1":
        raise PilotPreflightError("terminal_arm_receipt_invalid")
    expected_hash = receipt.get("arm_receipt_sha256")
    if expected_hash != _canonical_sha256({key: value for key, value in receipt.items() if key != "arm_receipt_sha256"}):
        raise PilotPreflightError("runner_arm_receipt_hash_mismatch")
    arm = receipt.get("arm")
    expected_configurations = {item["arm"]: item["configuration"] for item in prepared["arms"]}
    if (
        arm not in expected_configurations
        or receipt.get("dry_run_sha256") != prepared["dry_run_sha256"]
        or receipt.get("case_sha256") != prepared["case_sha256"]
        or receipt.get("configuration") != expected_configurations[arm]
        or receipt.get("configuration_sha256") != _canonical_sha256(expected_configurations[arm])
    ):
        raise PilotPreflightError("runner_arm_receipt_binding_mismatch")
    result = receipt.get("result")
    if not isinstance(result, dict) or not re.fullmatch(r"[0-9a-f]{64}", str(result.get("raw_output_sha256") or "")):
        raise PilotPreflightError("terminal_arm_receipt_invalid")
    artifact_root = receipt.get("artifact_root")
    if not isinstance(artifact_root, str) or not artifact_root:
        raise PilotPreflightError("terminal_arm_receipt_invalid")
    output_path, output_sha256 = _read_runner_output(result, artifact_root=Path(artifact_root))
    if output_path != result.get("raw_output_path") or output_sha256 != result.get("raw_output_sha256"):
        raise PilotPreflightError("runner_output_hash_mismatch")
    execution_descriptor = receipt.get("execution_receipt")
    if (
        not isinstance(execution_descriptor, dict)
        or set(execution_descriptor) != {
            "path",
            "file_sha256",
            "execution_receipt_sha256",
        }
        or not re.fullmatch(r"[0-9a-f]{64}", str(execution_descriptor.get("file_sha256") or ""))
        or not re.fullmatch(r"[0-9a-f]{64}", str(execution_descriptor.get("execution_receipt_sha256") or ""))
    ):
        raise PilotPreflightError("terminal_arm_receipt_invalid")
    receipt_path, receipt_sha256, execution_receipt = _read_execution_receipt_file(
        Path(str(execution_descriptor["path"])), artifact_root=Path(artifact_root)
    )
    if (
        receipt_path != execution_descriptor["path"]
        or receipt_sha256 != execution_descriptor["file_sha256"]
    ):
        raise PilotPreflightError("execution_receipt_file_hash_mismatch")
    execution = _validated_execution_receipt(
        execution_receipt, prepared=prepared, artifact_root=Path(artifact_root)
    )
    if (
        execution["execution_receipt_sha256"]
        != execution_descriptor["execution_receipt_sha256"]
        or execution["result"] != result
    ):
        raise PilotPreflightError("execution_receipt_binding_mismatch")
    return receipt


def build_terminal_receipt(
    dry_run: dict[str, Any], arm_receipts: list[dict[str, Any]]
) -> dict[str, Any]:
    """Seal two runner-generated arm receipts; this function never invokes a provider."""
    prepared = _validated_paired_dry_run(dry_run)
    expected_arms = {"omc", "baseline"}
    if not isinstance(arm_receipts, list) or {item.get("arm") for item in arm_receipts if isinstance(item, dict)} != expected_arms:
        raise PilotPreflightError("terminal_arm_set_invalid")
    if len(arm_receipts) != 2:
        raise PilotPreflightError("terminal_arm_set_invalid")
    normalized: dict[str, dict[str, Any]] = {}
    for arm_receipt in arm_receipts:
        validated = _validated_runner_arm_receipt(arm_receipt, prepared=prepared)
        arm = validated["arm"]
        result = validated["result"]
        normalized[arm] = {
            key: result[key]
            for key in (
                "verification_passed",
                "review_outcome",
                "elapsed_seconds",
                "user_intervention",
                "rework_count",
                "fatal_violation",
                "provider_call_count",
                "raw_output_sha256",
            )
        }
    receipt: dict[str, Any] = {
        "schema_version": "omc-task-review-pilot-terminal/v1",
        "dry_run": deepcopy(prepared),
        "dry_run_sha256": prepared["dry_run_sha256"],
        "case_sha256": prepared["case_sha256"],
        "arm_receipts": deepcopy(arm_receipts),
        "arms": normalized,
        "completion": {
            arm: data["verification_passed"] and data["review_outcome"] == "approved"
            for arm, data in normalized.items()
        },
        "provider_call_count": sum(
            data["provider_call_count"] for data in normalized.values()
        ),
    }
    if "registration_sha256" in prepared:
        receipt["persona_registration_sha256"] = prepared["registration_sha256"]
    else:
        receipt["pilot_binding"] = deepcopy(prepared["pilot_binding"])
    receipt["terminal_sha256"] = _canonical_sha256(receipt)
    return receipt


def _validated_terminal_receipt(
    receipt: dict[str, Any], *, expected_pilot_binding: dict[str, str] | None = None,
    expected_persona_registration_sha256: str | None = None,
) -> dict[str, Any]:
    if receipt.get("schema_version") != "omc-task-review-pilot-terminal/v1":
        raise PilotPreflightError("terminal_schema_invalid")
    expected_hash = receipt.get("terminal_sha256")
    actual_hash = _canonical_sha256(
        {key: value for key, value in receipt.items() if key != "terminal_sha256"}
    )
    if expected_hash != actual_hash:
        raise PilotPreflightError("terminal_hash_mismatch")
    prepared = _validated_paired_dry_run(receipt.get("dry_run"))
    if expected_persona_registration_sha256 is not None:
        binding_valid = (
            prepared.get("registration_sha256")
            == expected_persona_registration_sha256
            and receipt.get("persona_registration_sha256")
            == expected_persona_registration_sha256
            and "pilot_binding" not in receipt
        )
    else:
        pilot_binding = _validated_pilot_binding(receipt.get("pilot_binding"))
        binding_valid = (
            pilot_binding == expected_pilot_binding
            and prepared.get("pilot_binding") == pilot_binding
            and "persona_registration_sha256" not in receipt
        )
    if (
        not binding_valid
        or receipt.get("dry_run_sha256") != prepared["dry_run_sha256"]
        or receipt.get("case_sha256") != prepared["case_sha256"]
    ):
        raise PilotPreflightError("terminal_pilot_binding_mismatch")
    arm_receipts = receipt.get("arm_receipts")
    if (
        not isinstance(arm_receipts, list)
        or len(arm_receipts) != 2
        or not all(
            isinstance(item, dict) and isinstance(item.get("arm"), str)
            for item in arm_receipts
        )
    ):
        raise PilotPreflightError("terminal_arm_bundle_invalid")
    validated_arms = {
        item["arm"]: _validated_runner_arm_receipt(item, prepared=prepared)
        for item in arm_receipts
        if isinstance(item, dict)
    }
    if set(validated_arms) != {"omc", "baseline"}:
        raise PilotPreflightError("terminal_arm_bundle_invalid")
    arms = receipt.get("arms")
    completion = receipt.get("completion")
    if not isinstance(arms, dict) or not isinstance(completion, dict) or set(arms) != {"omc", "baseline"}:
        raise PilotPreflightError("terminal_arm_set_invalid")
    for arm, data in arms.items():
        if not isinstance(data, dict) or completion.get(arm) != (
            data.get("verification_passed") is True and data.get("review_outcome") == "approved"
        ):
            raise PilotPreflightError("terminal_completion_invalid")
        if (
            isinstance(data.get("provider_call_count"), bool)
            or not isinstance(data.get("provider_call_count"), int)
            or data["provider_call_count"] < 0
            or not re.fullmatch(r"[0-9a-f]{64}", str(data.get("raw_output_sha256") or ""))
        ):
            raise PilotPreflightError("terminal_provider_evidence_invalid")
    if receipt.get("provider_call_count") != sum(
        data["provider_call_count"] for data in arms.values()
    ):
        raise PilotPreflightError("terminal_provider_call_count_mismatch")
    expected_arms = {
        arm: {
            key: validated_arms[arm]["result"][key]
            for key in (
                "verification_passed",
                "review_outcome",
                "elapsed_seconds",
                "user_intervention",
                "rework_count",
                "fatal_violation",
                "provider_call_count",
                "raw_output_sha256",
            )
        }
        for arm in ("omc", "baseline")
    }
    expected_completion = {
        arm: data["verification_passed"] and data["review_outcome"] == "approved"
        for arm, data in expected_arms.items()
    }
    if arms != expected_arms or completion != expected_completion:
        raise PilotPreflightError("terminal_arm_bundle_mismatch")
    return receipt


def build_pilot_decision(
    terminal_receipts: list[dict[str, Any]], *, readiness_receipt: dict[str, Any]
) -> dict[str, Any]:
    """Apply the frozen three-case pilot rule without upgrading it to a superiority claim."""
    if len(terminal_receipts) != 3:
        return {
            "schema_version": "omc-task-review-pilot-decision/v1",
            "status": "INCONCLUSIVE",
            "reason": "terminal_receipt_count_invalid",
            "terminal_receipt_count": len(terminal_receipts),
        }
    readiness = _validated_execution_readiness(readiness_receipt)
    expected_pilot_binding = _pilot_binding(readiness)
    terminals = [
        _validated_terminal_receipt(
            receipt, expected_pilot_binding=expected_pilot_binding
        )
        for receipt in terminal_receipts
    ]
    if len({receipt.get("case_sha256") for receipt in terminals}) != 3:
        raise PilotPreflightError("terminal_case_duplicate")
    if any(
        receipt["arms"][arm]["provider_call_count"] == 0
        for receipt in terminals
        for arm in ("omc", "baseline")
    ):
        return {
            "schema_version": "omc-task-review-pilot-decision/v1",
            "status": "INCONCLUSIVE",
            "reason": "provider_execution_absent",
            "terminal_receipt_count": 3,
        }
    arm_values = {
        arm: {
            "completion": sum(receipt["completion"][arm] for receipt in terminals),
            "elapsed_seconds": median(receipt["arms"][arm]["elapsed_seconds"] for receipt in terminals),
            "user_intervention": median(receipt["arms"][arm]["user_intervention"] for receipt in terminals),
            "rework_count": median(receipt["arms"][arm]["rework_count"] for receipt in terminals),
        }
        for arm in ("omc", "baseline")
    }
    fatal = any(data["fatal_violation"] for receipt in terminals for data in receipt["arms"].values())
    over_intervention = any(
        data["user_intervention"] > 3
        for receipt in terminals
        for data in receipt["arms"].values()
    )
    omc, baseline = arm_values["omc"], arm_values["baseline"]
    if fatal or omc["completion"] < baseline["completion"]:
        status = "STOP"
    else:
        time_improvement = baseline["elapsed_seconds"] > 0 and omc["elapsed_seconds"] <= baseline["elapsed_seconds"] * 0.85
        intervention_improvement = baseline["user_intervention"] > 0 and omc["user_intervention"] <= baseline["user_intervention"] * 0.85
        if (
            omc["completion"] >= 2
            and omc["elapsed_seconds"] <= baseline["elapsed_seconds"]
            and omc["user_intervention"] <= baseline["user_intervention"]
            and omc["rework_count"] <= baseline["rework_count"]
            and not over_intervention
            and (time_improvement or intervention_improvement)
        ):
            status = "CONTINUE"
        else:
            status = "REDUCE"
    decision: dict[str, Any] = {
        "schema_version": "omc-task-review-pilot-decision/v1",
        "status": status,
        "terminal_receipt_count": 3,
        "metrics": arm_values,
        "fatal_violation": fatal,
        "over_intervention": over_intervention,
        "provider_call_count": sum(
            receipt["provider_call_count"] for receipt in terminals
        ),
    }
    decision["decision_sha256"] = _canonical_sha256(decision)
    return decision


def _validated_persona_adjudication(
    receipt: Any, *, terminals: list[dict[str, Any]], artifact_root: Path
) -> dict[str, Any]:
    required = {
        "schema_version", "signer", "signer_public_key", "study_id",
        "cases", "signature",
    }
    if not isinstance(receipt, dict) or set(receipt) != required:
        raise PilotPreflightError("persona_adjudication_invalid")
    trusted_key = _trusted_persona_adjudication_public_key()
    if (
        receipt.get("schema_version")
        != "omc-task-review-persona-adjudication/v2"
        or receipt.get("signer")
        != "omc-task-review-persona-blind-adjudicator-v1"
        or receipt.get("signer_public_key") != trusted_key
        or receipt.get("study_id") != PERSONA_STUDY_ID
    ):
        raise PilotPreflightError("persona_adjudication_invalid")
    cases = receipt.get("cases")
    case_fields = {
        "terminal_sha256", "case_sha256",
        "arm_a_additional_correction_required",
        "arm_b_additional_correction_required",
        "arm_a_correction_evidence",
        "arm_b_correction_evidence",
    }
    if (
        not isinstance(cases, list)
        or len(cases) != 10
        or not all(
            isinstance(item, dict)
            and set(item) == case_fields
            and isinstance(item.get("arm_a_additional_correction_required"), bool)
            and isinstance(item.get("arm_b_additional_correction_required"), bool)
            and isinstance(item.get("arm_a_correction_evidence"), dict)
            and isinstance(item.get("arm_b_correction_evidence"), dict)
            for item in cases
        )
    ):
        raise PilotPreflightError("persona_adjudication_invalid")
    expected_bindings = [
        {"terminal_sha256": terminal["terminal_sha256"], "case_sha256": terminal["case_sha256"]}
        for terminal in terminals
    ]
    observed_bindings = [
        {"terminal_sha256": item["terminal_sha256"], "case_sha256": item["case_sha256"]}
        for item in cases
    ]
    if observed_bindings != expected_bindings:
        raise PilotPreflightError("persona_adjudication_binding_mismatch")
    for item in cases:
        for alias in ("arm_a", "arm_b"):
            descriptor = item[f"{alias}_correction_evidence"]
            if set(descriptor) != {"path", "sha256"}:
                raise PilotPreflightError("persona_adjudication_invalid")
            observed_path, observed_sha256 = _read_runner_output(
                {
                    "raw_output_path": descriptor.get("path"),
                    "raw_output_sha256": descriptor.get("sha256"),
                },
                artifact_root=artifact_root,
            )
            if observed_path != descriptor.get("path") or observed_sha256 != descriptor.get("sha256"):
                raise PilotPreflightError("persona_correction_evidence_hash_mismatch")
    try:
        Ed25519PublicKey.from_public_bytes(
            base64.b64decode(trusted_key, validate=True)
        ).verify(
            base64.b64decode(str(receipt["signature"]), validate=True),
            _persona_adjudication_signed_bytes(receipt),
        )
    except (InvalidSignature, binascii.Error, ValueError) as exc:
        raise PilotPreflightError("persona_adjudication_signature_invalid") from exc
    return deepcopy(receipt)


def _verify_persona_authority_receipt(
    receipt: Any, *, schema_version: str, trusted_key: str, error: str
) -> dict[str, Any]:
    if not isinstance(receipt, dict) or receipt.get("schema_version") != schema_version:
        raise PilotPreflightError(error)
    if receipt.get("signer_public_key") != trusted_key or receipt.get("study_id") != PERSONA_STUDY_ID:
        raise PilotPreflightError(error)
    try:
        signed_bytes = (
            _persona_arm_mapping_signed_bytes(receipt)
            if schema_version.endswith("arm-mapping/v1")
            else _persona_study_binding_signed_bytes(receipt)
        )
        Ed25519PublicKey.from_public_bytes(base64.b64decode(trusted_key, validate=True)).verify(
            base64.b64decode(str(receipt.get("signature") or ""), validate=True), signed_bytes
        )
    except (InvalidSignature, binascii.Error, ValueError) as exc:
        raise PilotPreflightError(f"{error}_signature_invalid") from exc
    return deepcopy(receipt)


def _validated_persona_arm_mapping(receipt: Any, *, trusted_key: str) -> dict[str, Any]:
    mapping = _verify_persona_authority_receipt(
        receipt,
        schema_version="omc-task-review-persona-arm-mapping/v1",
        trusted_key=trusted_key,
        error="persona_arm_mapping_invalid",
    )
    if set(mapping) != {
        "schema_version", "study_id", "arm_a", "arm_b",
        "registered_at", "signer_public_key", "signature",
    }:
        raise PilotPreflightError("persona_arm_mapping_invalid")
    arm_a = mapping.get("arm_a")
    arm_b = mapping.get("arm_b")
    if (
        not isinstance(arm_a, str)
        or not isinstance(arm_b, str)
        or arm_a not in ("omc", "baseline")
        or arm_b not in ("omc", "baseline")
        or arm_a == arm_b
    ):
        raise PilotPreflightError("persona_arm_mapping_invalid")
    return mapping


def _aware_timestamp(value: Any, *, error: str) -> datetime:
    if not isinstance(value, str):
        raise PilotPreflightError(error)
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError as exc:
        raise PilotPreflightError(error) from exc
    if parsed.tzinfo is None:
        raise PilotPreflightError(error)
    return parsed


def _validated_persona_registration(
    receipt: Any, *, arm_mapping_receipt: dict[str, Any]
) -> dict[str, Any]:
    required = {
        "schema_version", "study_id", "t0", "collection_deadline",
        "case_count", "minimum_repository_count",
        "maximum_cases_per_repository", "wall_clock_noninferiority_ratio",
        "preregistration_sha256", "contract_revision",
        "minimum_relative_reduction", "minimum_baseline_correction_events",
        "selection_policy", "arm_order_policy", "arm_mapping_sha256",
        "repositories",
        "execution_public_key", "study_public_key",
        "reconciliation_public_key", "adjudication_public_key",
        "initial_previous_enrollment_sha256", "user_approved",
        "registered_at", "signer_public_key", "reconciliation_signature",
        "signature",
    }
    if not isinstance(receipt, dict) or set(receipt) != required:
        raise PilotPreflightError("persona_study_registration_invalid")
    study_key = _trusted_persona_study_public_key()
    reconciliation_key = _trusted_reconciliation_public_key()
    mapping = _validated_persona_arm_mapping(
        arm_mapping_receipt, trusted_key=study_key
    )
    if (
        receipt.get("schema_version")
        != "omc-task-review-persona-study-registration/v1"
        or receipt.get("study_id") != PERSONA_STUDY_ID
        or receipt.get("case_count") != 10
        or receipt.get("minimum_repository_count") != 2
        or receipt.get("maximum_cases_per_repository") != 7
        or receipt.get("wall_clock_noninferiority_ratio") != 1.15
        or receipt.get("preregistration_sha256")
        != PERSONA_PREREGISTRATION_SHA256
        or receipt.get("contract_revision") != PERSONA_CONTRACT_REVISION
        or receipt.get("minimum_relative_reduction")
        != PERSONA_MINIMUM_RELATIVE_REDUCTION
        or receipt.get("minimum_baseline_correction_events")
        != PERSONA_MINIMUM_BASELINE_CORRECTION_EVENTS
        or receipt.get("selection_policy")
        != "chronological_first_eligible_implementation_no_replacement"
        or receipt.get("arm_order_policy")
        != "odd_omc_first_even_direct_first"
        or receipt.get("arm_mapping_sha256") != _canonical_sha256(mapping)
        or receipt.get("execution_public_key") != _trusted_execution_public_key()
        or receipt.get("study_public_key") != study_key
        or receipt.get("signer_public_key") != study_key
        or receipt.get("reconciliation_public_key") != reconciliation_key
        or receipt.get("adjudication_public_key")
        != _trusted_persona_adjudication_public_key()
        or receipt.get("initial_previous_enrollment_sha256") != "0" * 64
        or receipt.get("user_approved") is not True
    ):
        raise PilotPreflightError("persona_study_registration_invalid")
    t0 = _aware_timestamp(receipt.get("t0"), error="persona_registration_time_invalid")
    deadline = _aware_timestamp(
        receipt.get("collection_deadline"), error="persona_registration_time_invalid"
    )
    registered_at = _aware_timestamp(
        receipt.get("registered_at"), error="persona_registration_time_invalid"
    )
    mapping_at = _aware_timestamp(
        mapping.get("registered_at"), error="persona_mapping_registered_at_invalid"
    )
    if deadline != t0 + timedelta(days=21) or registered_at != t0 or mapping_at != t0:
        raise PilotPreflightError("persona_registration_time_invalid")
    repositories = receipt.get("repositories")
    repository_fields = {"repository_id", "canonical_origin", "root_commit"}
    if not isinstance(repositories, list) or len(repositories) < 2:
        raise PilotPreflightError("persona_repository_roster_invalid")
    repository_ids: list[str] = []
    canonical_pairs: set[tuple[str, str]] = set()
    for repository in repositories:
        if not isinstance(repository, dict) or set(repository) != repository_fields:
            raise PilotPreflightError("persona_repository_roster_invalid")
        repository_id = repository.get("repository_id")
        canonical_origin = repository.get("canonical_origin")
        root_commit = repository.get("root_commit")
        if (
            not isinstance(repository_id, str)
            or not _is_canonical_repository_origin(canonical_origin)
            or not isinstance(root_commit, str)
            or not re.fullmatch(r"[0-9a-f]{40}", root_commit)
            or repository_id
            != hashlib.sha256(
                f"{canonical_origin}\n{root_commit}".encode("utf-8")
            ).hexdigest()
            or (canonical_origin, root_commit) in canonical_pairs
        ):
            raise PilotPreflightError("persona_repository_roster_invalid")
        repository_ids.append(repository_id)
        canonical_pairs.add((canonical_origin, root_commit))
    if len(set(repository_ids)) != len(repository_ids) or repository_ids != sorted(repository_ids):
        raise PilotPreflightError("persona_repository_roster_invalid")
    signed = _persona_registration_signed_bytes(receipt)
    try:
        Ed25519PublicKey.from_public_bytes(base64.b64decode(study_key, validate=True)).verify(
            base64.b64decode(str(receipt.get("signature") or ""), validate=True), signed
        )
        Ed25519PublicKey.from_public_bytes(
            base64.b64decode(reconciliation_key, validate=True)
        ).verify(
            base64.b64decode(
                str(receipt.get("reconciliation_signature") or ""), validate=True
            ),
            signed,
        )
    except (InvalidSignature, binascii.Error, ValueError) as exc:
        raise PilotPreflightError("persona_study_registration_signature_invalid") from exc
    return deepcopy(receipt)


def validate_persona_enrollment_chain(
    registration_receipt: dict[str, Any],
    enrollment_receipts: list[dict[str, Any]], *,
    arm_mapping_receipt: dict[str, Any], artifact_root: Path,
    require_complete: bool = True,
) -> list[dict[str, Any]]:
    """Reopen and verify the append-only first-eligible evidence for all ten cases."""
    registration = _validated_persona_registration(
        registration_receipt, arm_mapping_receipt=arm_mapping_receipt
    )
    if (
        not isinstance(enrollment_receipts, list)
        or len(enrollment_receipts) > 10
        or (require_complete and len(enrollment_receipts) != 10)
    ):
        raise PilotPreflightError("persona_enrollment_count_invalid")
    required = {
        "schema_version", "study_id", "registration_sha256",
        "previous_enrollment_sha256", "sequence", "session_id",
        "session_created_at", "enrolled_at", "repository_id", "base_commit",
        "work_class", "request_sha256", "dod_sha256", "verification_sha256",
        "state_evidence", "signer_public_key", "signature",
    }
    evidence_session_fields = {
        "session_id", "created_at", "repository_id", "base_commit",
        "work_class", "eligible", "request_sha256", "dod_sha256",
        "verification_sha256",
    }
    registration_sha256 = _canonical_sha256(registration)
    previous_hash = registration["initial_previous_enrollment_sha256"]
    previous_cursor: str | None = None
    previous_created_at: datetime | None = None
    seen_sessions: set[str] = set()
    validated: list[dict[str, Any]] = []
    t0 = _aware_timestamp(registration["t0"], error="persona_registration_time_invalid")
    deadline = _aware_timestamp(
        registration["collection_deadline"], error="persona_registration_time_invalid"
    )
    reconciliation_key = _trusted_reconciliation_public_key()
    registered_repository_ids = {
        repository["repository_id"] for repository in registration["repositories"]
    }
    for expected_sequence, receipt in enumerate(enrollment_receipts, start=1):
        if not isinstance(receipt, dict) or set(receipt) != required:
            raise PilotPreflightError("persona_enrollment_invalid")
        descriptor = receipt.get("state_evidence")
        if (
            receipt.get("schema_version") != "omc-task-review-persona-enrollment/v1"
            or receipt.get("study_id") != PERSONA_STUDY_ID
            or receipt.get("registration_sha256") != registration_sha256
            or receipt.get("previous_enrollment_sha256") != previous_hash
            or type(receipt.get("sequence")) is not int
            or receipt.get("sequence") != expected_sequence
            or receipt.get("work_class") != "implementation"
            or receipt.get("repository_id") not in registered_repository_ids
            or receipt.get("signer_public_key") != reconciliation_key
            or not isinstance(descriptor, dict)
            or set(descriptor) != {"path", "sha256"}
        ):
            raise PilotPreflightError("persona_enrollment_invalid")
        for digest_field in ("request_sha256", "dod_sha256", "verification_sha256"):
            if not re.fullmatch(r"[0-9a-f]{64}", str(receipt.get(digest_field) or "")):
                raise PilotPreflightError("persona_enrollment_invalid")
        if not re.fullmatch(r"[0-9a-f]{40}", str(receipt.get("base_commit") or "")):
            raise PilotPreflightError("persona_enrollment_invalid")
        created_at = _aware_timestamp(
            receipt.get("session_created_at"), error="persona_enrollment_time_invalid"
        )
        enrolled_at = _aware_timestamp(
            receipt.get("enrolled_at"), error="persona_enrollment_time_invalid"
        )
        if (
            not t0 < created_at <= enrolled_at <= deadline
            or (previous_created_at is not None and created_at <= previous_created_at)
        ):
            raise PilotPreflightError("persona_enrollment_time_invalid")
        try:
            Ed25519PublicKey.from_public_bytes(
                base64.b64decode(reconciliation_key, validate=True)
            ).verify(
                base64.b64decode(str(receipt.get("signature") or ""), validate=True),
                _persona_enrollment_signed_bytes(receipt),
            )
        except (InvalidSignature, binascii.Error, ValueError) as exc:
            raise PilotPreflightError("persona_enrollment_signature_invalid") from exc
        observed_path, observed_sha256, payload = _read_bound_artifact(
            {
                "raw_output_path": descriptor.get("path"),
                "raw_output_sha256": descriptor.get("sha256"),
            },
            artifact_root=artifact_root,
        )
        if observed_path != descriptor.get("path") or observed_sha256 != descriptor.get("sha256"):
            raise PilotPreflightError("persona_enrollment_state_hash_mismatch")
        try:
            evidence = json.loads(payload.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise PilotPreflightError("persona_enrollment_state_invalid") from exc
        if (
            not isinstance(evidence, dict)
            or set(evidence) != {
                "schema_version", "study_id", "previous_terminal_cursor",
                "terminal_cursor", "sessions",
            }
            or evidence.get("schema_version")
            != "omc-task-review-persona-state-evidence/v1"
            or evidence.get("study_id") != PERSONA_STUDY_ID
            or evidence.get("previous_terminal_cursor") != previous_cursor
            or not isinstance(evidence.get("terminal_cursor"), str)
            or not evidence["terminal_cursor"]
            or not isinstance(evidence.get("sessions"), list)
            or not evidence["sessions"]
        ):
            raise PilotPreflightError("persona_enrollment_state_invalid")
        sessions = evidence["sessions"]
        if not all(isinstance(item, dict) and set(item) == evidence_session_fields for item in sessions):
            raise PilotPreflightError("persona_enrollment_state_invalid")
        if not all(type(item.get("eligible")) is bool for item in sessions):
            raise PilotPreflightError("persona_enrollment_state_invalid")
        selected = sessions[-1]
        interval_previous_created_at = previous_created_at
        for item in sessions:
            item_created_at = _aware_timestamp(
                item.get("created_at"), error="persona_enrollment_state_invalid"
            )
            session_id = item.get("session_id")
            if (
                not isinstance(session_id, str)
                or not session_id
                or session_id in seen_sessions
                or item.get("repository_id") not in registered_repository_ids
                or not re.fullmatch(
                    r"[0-9a-f]{40}", str(item.get("base_commit") or "")
                )
                or not isinstance(item.get("work_class"), str)
                or any(
                    not re.fullmatch(r"[0-9a-f]{64}", str(item.get(field) or ""))
                    for field in (
                        "request_sha256", "dod_sha256", "verification_sha256"
                    )
                )
                or (
                    interval_previous_created_at is not None
                    and item_created_at <= interval_previous_created_at
                )
                or item_created_at > created_at
            ):
                raise PilotPreflightError("persona_enrollment_state_invalid")
            seen_sessions.add(session_id)
            interval_previous_created_at = item_created_at
        if any(item.get("eligible") is True for item in sessions[:-1]):
            raise PilotPreflightError("persona_enrollment_not_first_eligible")
        expected_selected = {
            "session_id": receipt.get("session_id"),
            "created_at": receipt.get("session_created_at"),
            "repository_id": receipt.get("repository_id"),
            "base_commit": receipt.get("base_commit"),
            "work_class": receipt.get("work_class"),
            "eligible": True,
            "request_sha256": receipt.get("request_sha256"),
            "dod_sha256": receipt.get("dod_sha256"),
            "verification_sha256": receipt.get("verification_sha256"),
        }
        if selected != expected_selected:
            raise PilotPreflightError("persona_enrollment_selected_case_mismatch")
        validated.append(deepcopy(receipt))
        previous_hash = _canonical_sha256(receipt)
        previous_cursor = evidence["terminal_cursor"]
        previous_created_at = created_at
    repository_counts: dict[str, int] = {}
    for receipt in validated:
        repository_id = str(receipt["repository_id"])
        repository_counts[repository_id] = repository_counts.get(repository_id, 0) + 1
    if (
        repository_counts
        and max(repository_counts.values())
        > registration["maximum_cases_per_repository"]
    ) or (
        require_complete
        and len(repository_counts) < registration["minimum_repository_count"]
    ):
        raise PilotPreflightError("persona_repository_distribution_invalid")
    return validated


def _persona_metric_decision(
    terminals: list[dict[str, Any]], *, correction_events: dict[str, int],
    wall_clock_noninferiority_ratio: float, minimum_relative_reduction: float,
    minimum_baseline_correction_events: int,
) -> dict[str, Any]:
    completion = {
        "direct_codex": sum(item["completion"]["baseline"] for item in terminals),
        "omc_persona": sum(item["completion"]["omc"] for item in terminals),
    }
    verification = {
        "direct_codex": sum(item["arms"]["baseline"]["verification_passed"] for item in terminals),
        "omc_persona": sum(item["arms"]["omc"]["verification_passed"] for item in terminals),
    }
    interventions = {
        "direct_codex": sum(item["arms"]["baseline"]["user_intervention"] for item in terminals),
        "omc_persona": sum(item["arms"]["omc"]["user_intervention"] for item in terminals),
    }
    elapsed = {
        "direct_codex": median(item["arms"]["baseline"]["elapsed_seconds"] for item in terminals),
        "omc_persona": median(item["arms"]["omc"]["elapsed_seconds"] for item in terminals),
    }
    baseline_events = correction_events["direct_codex"]
    relative_reduction = None if baseline_events < minimum_baseline_correction_events else (
        baseline_events - correction_events["omc_persona"]
    ) / baseline_events
    outcome = _evaluate_persona_decision(
        {
            "fatal_violation": any(
                item["arms"][arm]["fatal_violation"]
                for item in terminals
                for arm in ("omc", "baseline")
            ),
            "provider_execution_absent": False,
            "completion_noninferiority_failed": (
                completion["omc_persona"] < completion["direct_codex"]
            ),
            "verification_noninferiority_failed": (
                verification["omc_persona"] < verification["direct_codex"]
            ),
            "total_intervention_noninferiority_failed": (
                interventions["omc_persona"] > interventions["direct_codex"]
            ),
            "wall_clock_noninferiority_failed": (
                elapsed["omc_persona"]
                > elapsed["direct_codex"] * wall_clock_noninferiority_ratio
            ),
            "insufficient_baseline_correction_events": (
                baseline_events < minimum_baseline_correction_events
            ),
            "correction_reduction_target_missed": (
                relative_reduction is not None
                and relative_reduction < minimum_relative_reduction
            ),
        }
    )
    result: dict[str, Any] = {
        "status": outcome["status"],
        "relative_reduction": relative_reduction,
        "completion": completion,
        "verification": verification,
        "total_human_interventions": interventions,
        "median_wall_clock_seconds": elapsed,
    }
    if "reason" in outcome:
        result["reason"] = outcome["reason"]
    return result


def build_persona_study_decision(
    terminal_receipts: list[dict[str, Any]], *,
    adjudication_receipt: dict[str, Any], readiness_receipt: dict[str, Any],
    study_binding_receipt: dict[str, Any], arm_mapping_receipt: dict[str, Any],
    artifact_root: Path, expected_study_binding_sha256: str | None = None,
) -> dict[str, Any]:
    """Decide the frozen ten-pair persona study from independently signed evidence."""
    if not isinstance(terminal_receipts, list) or len(terminal_receipts) != 10:
        raise PilotPreflightError("persona_terminal_receipt_count_invalid")
    readiness = _validated_execution_readiness(readiness_receipt)
    study_key = _trusted_persona_study_public_key()
    mapping = _validated_persona_arm_mapping(
        arm_mapping_receipt, trusted_key=study_key
    )
    binding = _verify_persona_authority_receipt(
        study_binding_receipt,
        schema_version="omc-task-review-persona-study-binding/v1",
        trusted_key=study_key,
        error="persona_study_binding_invalid",
    )
    if set(binding) != {
        "schema_version", "study_id", "readiness_sha256", "source_snapshot_sha256",
        "source_snapshot", "adjudication_public_key", "arm_mapping_sha256",
        "registered_at", "signer_public_key", "reconciliation_public_key",
        "reconciliation_signature", "signature",
    } or (
        binding.get("readiness_sha256") != readiness["readiness_sha256"]
        or not re.fullmatch(r"[0-9a-f]{64}", str(binding.get("source_snapshot_sha256") or ""))
        or binding.get("adjudication_public_key") != _trusted_persona_adjudication_public_key()
        or binding.get("arm_mapping_sha256") != _canonical_sha256(mapping)
        or binding.get("reconciliation_public_key")
        != _trusted_reconciliation_public_key()
    ):
        raise PilotPreflightError("persona_study_binding_invalid")
    source_snapshot = binding.get("source_snapshot")
    if not isinstance(source_snapshot, dict) or set(source_snapshot) != {"path", "sha256"}:
        raise PilotPreflightError("persona_source_snapshot_invalid")
    observed_path, observed_sha256, source_payload = _read_bound_artifact(
        {
            "raw_output_path": source_snapshot.get("path"),
            "raw_output_sha256": source_snapshot.get("sha256"),
        },
        artifact_root=artifact_root,
    )
    if (
        observed_path != source_snapshot.get("path")
        or observed_sha256 != source_snapshot.get("sha256")
        or observed_sha256 != binding.get("source_snapshot_sha256")
    ):
        raise PilotPreflightError("persona_source_snapshot_hash_mismatch")
    try:
        source_manifest = json.loads(source_payload.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise PilotPreflightError("persona_source_snapshot_invalid") from exc
    if (
        not isinstance(source_manifest, dict)
        or set(source_manifest) != {"schema_version", "study_id", "cases"}
        or source_manifest.get("schema_version")
        != "omc-task-review-persona-source-snapshot/v1"
        or source_manifest.get("study_id") != PERSONA_STUDY_ID
        or not isinstance(source_manifest.get("cases"), list)
        or len(source_manifest["cases"]) != 10
        or not all(
            isinstance(item, dict)
            and set(item) == {"case_position", "repository_id", "base_commit"}
            for item in source_manifest["cases"]
        )
    ):
        raise PilotPreflightError("persona_source_snapshot_invalid")
    try:
        Ed25519PublicKey.from_public_bytes(
            base64.b64decode(_trusted_reconciliation_public_key(), validate=True)
        ).verify(
            base64.b64decode(
                str(binding.get("reconciliation_signature") or ""), validate=True
            ),
            _persona_study_binding_signed_bytes(binding),
        )
    except (InvalidSignature, binascii.Error, ValueError) as exc:
        raise PilotPreflightError(
            "persona_study_reconciliation_signature_invalid"
        ) from exc
    study_binding_sha256 = (
        expected_study_binding_sha256 or _canonical_sha256(binding)
    )
    expected_pilot_binding = _pilot_binding(readiness)
    terminals = [
        _validated_terminal_receipt(receipt, expected_pilot_binding=expected_pilot_binding)
        for receipt in terminal_receipts
    ]
    if len({terminal.get("case_sha256") for terminal in terminals}) != 10:
        raise PilotPreflightError("persona_terminal_case_duplicate")
    if len({terminal.get("terminal_sha256") for terminal in terminals}) != 10:
        raise PilotPreflightError("persona_terminal_receipt_duplicate")
    positions = [terminal["dry_run"]["case_position"] for terminal in terminals]
    if positions != list(range(1, 11)):
        raise PilotPreflightError("persona_terminal_order_invalid")
    if any(
        terminal["dry_run"].get("schema_version")
        != "omc-task-review-persona-paired-dry-run/v1"
        or terminal["dry_run"].get("study_binding_sha256")
        != study_binding_sha256
        for terminal in terminals
    ):
        raise PilotPreflightError("persona_terminal_study_binding_mismatch")
    expected_sources = [
        {
            "case_position": terminal["dry_run"]["case_position"],
            **terminal["dry_run"]["case_source"],
        }
        for terminal in terminals
    ]
    if source_manifest["cases"] != expected_sources:
        raise PilotPreflightError("persona_source_snapshot_case_mismatch")
    mapping_at = _aware_timestamp(
        mapping.get("registered_at"), error="persona_mapping_registered_at_invalid"
    )
    binding_at = _aware_timestamp(
        binding.get("registered_at"), error="persona_study_registered_at_invalid"
    )
    readiness_t0 = _aware_timestamp(readiness["t0"], error="persona_readiness_t0_invalid")
    if (
        mapping_at != binding_at
        or mapping_at < readiness_t0
    ):
        raise PilotPreflightError("persona_mapping_not_preregistered")
    adjudication = _validated_persona_adjudication(
        adjudication_receipt, terminals=terminals, artifact_root=artifact_root
    )
    pre_metric_decision = _persona_pre_metric_decision(terminals)
    if pre_metric_decision.get("reason") == "provider_execution_absent":
        return {
            "schema_version": "omc-task-review-persona-decision/v1",
            "study_id": PERSONA_STUDY_ID,
            "status": "INCONCLUSIVE",
            "reason": "provider_execution_absent",
        }

    correction_by_execution_arm = {
        mapping[alias]: sum(
            item[f"{alias}_additional_correction_required"]
            for item in adjudication["cases"]
        )
        for alias in ("arm_a", "arm_b")
    }
    correction_events = {
        "direct_codex": correction_by_execution_arm["baseline"],
        "omc_persona": correction_by_execution_arm["omc"],
    }
    metric_decision = _persona_metric_decision(
        terminals,
        correction_events=correction_events,
        wall_clock_noninferiority_ratio=1.15,
        minimum_relative_reduction=PERSONA_MINIMUM_RELATIVE_REDUCTION,
        minimum_baseline_correction_events=(
            PERSONA_MINIMUM_BASELINE_CORRECTION_EVENTS
        ),
    )
    completion = metric_decision["completion"]
    verification = metric_decision["verification"]
    metrics = {
        arm: {
            "additional_correction_events": correction_events[arm],
            "additional_correction_rate": correction_events[arm] / 10,
            "completion_count": completion[arm],
            "verification_pass_count": verification[arm],
            "total_human_interventions": metric_decision["total_human_interventions"][arm],
            "median_wall_clock_seconds": metric_decision["median_wall_clock_seconds"][arm],
        }
        for arm in ("direct_codex", "omc_persona")
    }
    status = metric_decision["status"]
    reason = metric_decision.get("reason")
    relative_reduction = metric_decision["relative_reduction"]
    decision: dict[str, Any] = {
        "schema_version": "omc-task-review-persona-decision/v1",
        "study_id": PERSONA_STUDY_ID,
        "status": status,
        "terminal_receipt_count": 10,
        "metrics": metrics,
        "relative_reduction": relative_reduction,
        "completion_non_inferior": completion["omc_persona"] >= completion["direct_codex"],
        "verification_non_inferior": verification["omc_persona"] >= verification["direct_codex"],
        "total_intervention_non_inferior": metric_decision["total_human_interventions"]["omc_persona"] <= metric_decision["total_human_interventions"]["direct_codex"],
        "wall_clock_non_inferior": metric_decision["median_wall_clock_seconds"]["omc_persona"] <= metric_decision["median_wall_clock_seconds"]["direct_codex"] * 1.15,
        "adjudication_signature": adjudication["signature"],
        "study_binding_signature": binding["signature"],
        "arm_mapping_sha256": binding["arm_mapping_sha256"],
    }
    if reason is not None:
        decision["reason"] = reason
    decision["decision_sha256"] = _canonical_sha256(decision)
    return decision


def build_enrolled_persona_study_decision(
    terminal_receipts: list[dict[str, Any]], *,
    adjudication_receipt: dict[str, Any], arm_mapping_receipt: dict[str, Any],
    registration_receipt: dict[str, Any],
    enrollment_receipts: list[dict[str, Any]], artifact_root: Path,
) -> dict[str, Any]:
    """Operational decision path: require the prospective enrollment chain."""
    registration = _validated_persona_registration(
        registration_receipt, arm_mapping_receipt=arm_mapping_receipt
    )
    mapping = _validated_persona_arm_mapping(
        arm_mapping_receipt, trusted_key=_trusted_persona_study_public_key()
    )
    enrollments = validate_persona_enrollment_chain(
        registration,
        enrollment_receipts,
        arm_mapping_receipt=arm_mapping_receipt,
        artifact_root=artifact_root,
    )
    if not isinstance(terminal_receipts, list) or len(terminal_receipts) != 10:
        raise PilotPreflightError("persona_terminal_receipt_count_invalid")
    registration_sha256 = _canonical_sha256(registration)
    terminals = [
        _validated_terminal_receipt(
            terminal,
            expected_persona_registration_sha256=registration_sha256,
        )
        for terminal in terminal_receipts
    ]
    if len({terminal["case_sha256"] for terminal in terminals}) != 10:
        raise PilotPreflightError("persona_terminal_case_duplicate")
    if len({terminal["terminal_sha256"] for terminal in terminals}) != 10:
        raise PilotPreflightError("persona_terminal_receipt_duplicate")
    for position, (terminal, enrollment) in enumerate(zip(terminals, enrollments, strict=True), start=1):
        dry_run = terminal["dry_run"]
        if (
            dry_run.get("case_position") != position
            or dry_run.get("registration_sha256") != registration_sha256
            or dry_run.get("study_binding_sha256") != registration_sha256
            or dry_run.get("enrollment_sha256") != _canonical_sha256(enrollment)
            or dry_run.get("enrollment_session_id") != enrollment["session_id"]
            or dry_run.get("case_source")
            != {
                "repository_id": enrollment["repository_id"],
                "base_commit": enrollment["base_commit"],
            }
        ):
            raise PilotPreflightError("persona_terminal_enrollment_binding_invalid")
    adjudication = _validated_persona_adjudication(
        adjudication_receipt, terminals=terminals, artifact_root=artifact_root
    )
    pre_metric_decision = _persona_pre_metric_decision(terminals)
    correction_by_execution_arm = {
        mapping[alias]: sum(
            item[f"{alias}_additional_correction_required"]
            for item in adjudication["cases"]
        )
        for alias in ("arm_a", "arm_b")
    }
    correction_events = {
        "direct_codex": correction_by_execution_arm["baseline"],
        "omc_persona": correction_by_execution_arm["omc"],
    }
    metric_decision = _persona_metric_decision(
        terminals,
        correction_events=correction_events,
        wall_clock_noninferiority_ratio=registration[
            "wall_clock_noninferiority_ratio"
        ],
        minimum_relative_reduction=registration[
            "minimum_relative_reduction"
        ],
        minimum_baseline_correction_events=registration[
            "minimum_baseline_correction_events"
        ],
    )
    metrics = {
        arm: {
            "additional_correction_events": correction_events[arm],
            "additional_correction_rate": correction_events[arm] / 10,
            "completion_count": metric_decision["completion"][arm],
            "verification_pass_count": metric_decision["verification"][arm],
            "total_human_interventions": metric_decision["total_human_interventions"][arm],
            "median_wall_clock_seconds": metric_decision["median_wall_clock_seconds"][arm],
        }
        for arm in ("direct_codex", "omc_persona")
    }
    outcome = (
        pre_metric_decision
        if pre_metric_decision.get("reason") == "provider_execution_absent"
        else metric_decision
    )
    decision: dict[str, Any] = {
        "schema_version": "omc-task-review-persona-decision/v2",
        "study_id": PERSONA_STUDY_ID,
        "status": outcome["status"],
        "terminal_receipt_count": 10,
        "enrollment_count": 10,
        "metrics": metrics,
        "relative_reduction": metric_decision["relative_reduction"],
        "registration_sha256": registration_sha256,
        "final_enrollment_sha256": _canonical_sha256(enrollments[-1]),
        "adjudication_signature": adjudication["signature"],
        "arm_mapping_sha256": registration["arm_mapping_sha256"],
        "evidence_bundle": {
            "registration": registration,
            "arm_mapping": mapping,
            "enrollments": deepcopy(enrollments),
            "terminals": deepcopy(terminals),
            "adjudication": adjudication,
        },
    }
    if "reason" in outcome:
        decision["reason"] = outcome["reason"]
    decision["decision_sha256"] = _canonical_sha256(decision)
    return decision


def verify_enrolled_persona_study_decision(
    decision_receipt: Any, *, artifact_root: Path,
) -> dict[str, Any]:
    try:
        if (
            not isinstance(decision_receipt, dict)
            or decision_receipt.get("schema_version")
            != "omc-task-review-persona-decision/v2"
            or decision_receipt.get("decision_sha256")
            != _canonical_sha256(
                {
                    key: value
                    for key, value in decision_receipt.items()
                    if key != "decision_sha256"
                }
            )
        ):
            raise PilotPreflightError("persona_decision_bundle_invalid")
        bundle = decision_receipt.get("evidence_bundle")
        if not isinstance(bundle, dict) or set(bundle) != {
            "registration", "arm_mapping", "enrollments", "terminals", "adjudication"
        }:
            raise PilotPreflightError("persona_decision_bundle_invalid")
        rebuilt = build_enrolled_persona_study_decision(
            bundle["terminals"],
            adjudication_receipt=bundle["adjudication"],
            arm_mapping_receipt=bundle["arm_mapping"],
            registration_receipt=bundle["registration"],
            enrollment_receipts=bundle["enrollments"],
            artifact_root=artifact_root,
        )
        if rebuilt != decision_receipt:
            raise PilotPreflightError("persona_decision_reverification_failed")
        return rebuilt
    except PilotPreflightError as exc:
        if str(exc) == "persona_decision_reverification_failed":
            raise
        raise PilotPreflightError("persona_decision_bundle_invalid") from exc
    except (KeyError, TypeError, AttributeError) as exc:
        raise PilotPreflightError("persona_decision_bundle_invalid") from exc


def build_persona_collection_close_decision(
    registration_receipt: dict[str, Any],
    enrollment_receipts: list[dict[str, Any]], *,
    arm_mapping_receipt: dict[str, Any],
    collection_close_receipt: dict[str, Any],
    artifact_root: Path,
) -> dict[str, Any]:
    """Close an expired, incomplete prospective collection as INCONCLUSIVE."""
    registration = _validated_persona_registration(
        registration_receipt, arm_mapping_receipt=arm_mapping_receipt
    )
    enrollments = validate_persona_enrollment_chain(
        registration,
        enrollment_receipts,
        arm_mapping_receipt=arm_mapping_receipt,
        artifact_root=artifact_root,
        require_complete=False,
    )
    required = {
        "schema_version", "study_id", "registration_sha256",
        "enrollment_count", "final_enrollment_sha256", "observed_at",
        "signer_public_key", "signature",
    }
    if not isinstance(collection_close_receipt, dict) or set(collection_close_receipt) != required:
        raise PilotPreflightError("persona_collection_close_invalid")
    registration_sha256 = _canonical_sha256(registration)
    final_enrollment_sha256 = (
        _canonical_sha256(enrollments[-1])
        if enrollments
        else registration["initial_previous_enrollment_sha256"]
    )
    reconciliation_key = _trusted_reconciliation_public_key()
    if (
        collection_close_receipt.get("schema_version")
        != "omc-task-review-persona-collection-close/v1"
        or collection_close_receipt.get("study_id") != PERSONA_STUDY_ID
        or collection_close_receipt.get("registration_sha256") != registration_sha256
        or type(collection_close_receipt.get("enrollment_count")) is not int
        or collection_close_receipt.get("enrollment_count") != len(enrollments)
        or collection_close_receipt.get("final_enrollment_sha256")
        != final_enrollment_sha256
        or collection_close_receipt.get("signer_public_key") != reconciliation_key
        or len(enrollments) >= registration["case_count"]
    ):
        raise PilotPreflightError("persona_collection_close_invalid")
    observed_at = _aware_timestamp(
        collection_close_receipt.get("observed_at"),
        error="persona_collection_close_time_invalid",
    )
    deadline = _aware_timestamp(
        registration["collection_deadline"], error="persona_registration_time_invalid"
    )
    if observed_at < deadline:
        raise PilotPreflightError("persona_collection_close_time_invalid")
    try:
        Ed25519PublicKey.from_public_bytes(
            base64.b64decode(reconciliation_key, validate=True)
        ).verify(
            base64.b64decode(
                str(collection_close_receipt.get("signature") or ""), validate=True
            ),
            _persona_collection_close_signed_bytes(collection_close_receipt),
        )
    except (InvalidSignature, binascii.Error, ValueError) as exc:
        raise PilotPreflightError("persona_collection_close_signature_invalid") from exc
    decision: dict[str, Any] = {
        "schema_version": "omc-task-review-persona-decision/v2",
        "study_id": PERSONA_STUDY_ID,
        "status": "INCONCLUSIVE",
        "reason": "deadline_or_sample_shortfall",
        "registration_sha256": registration_sha256,
        "enrollment_count": len(enrollments),
        "final_enrollment_sha256": final_enrollment_sha256,
        "collection_close_signature": collection_close_receipt["signature"],
        "collection_close_sha256": _canonical_sha256(collection_close_receipt),
        "collection_close_receipt": deepcopy(collection_close_receipt),
    }
    decision["decision_sha256"] = _canonical_sha256(decision)
    return decision


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    roster = sub.add_parser("prepare-roster")
    roster.add_argument("--repository", type=Path, action="append", required=True)
    roster.add_argument("--pilot-id", required=True)
    roster.add_argument("--pilot-contract-sha256", required=True)
    roster.add_argument("--source-commit", required=True)
    roster.add_argument("--output", type=Path, required=True)
    inventory = sub.add_parser("inventory-dry-run")
    inventory.add_argument("--roster", type=Path, required=True)
    inventory.add_argument("--start-receipt", type=Path, required=True)
    inventory.add_argument("--output", type=Path, required=True)
    readiness = sub.add_parser("readiness")
    readiness.add_argument("--roster", type=Path, required=True)
    readiness.add_argument("--start-receipt", type=Path, required=True)
    readiness.add_argument("--inventory", type=Path, required=True)
    readiness.add_argument("--output", type=Path, required=True)
    capability_matrix = sub.add_parser("capability-matrix")
    capability_matrix.add_argument("--source-repository", type=Path, required=True)
    capability_matrix.add_argument("--source-commit", required=True)
    capability_matrix.add_argument("--pilot-contract-sha256", required=True)
    capability_matrix.add_argument("--output", type=Path, required=True)
    freeze_case_parser = sub.add_parser("freeze-case")
    freeze_case_parser.add_argument("--case", type=Path, required=True)
    freeze_case_parser.add_argument("--readiness", type=Path, required=True)
    freeze_case_parser.add_argument("--output", type=Path, required=True)
    persona_freeze_case = sub.add_parser("persona-freeze-case")
    persona_freeze_case.add_argument("--case", type=Path, required=True)
    persona_freeze_case.add_argument("--registration", type=Path, required=True)
    persona_freeze_case.add_argument("--arm-mapping", type=Path, required=True)
    persona_freeze_case.add_argument("--output", type=Path, required=True)
    paired_dry_run = sub.add_parser("paired-dry-run")
    paired_dry_run.add_argument("--case-receipt", type=Path, required=True)
    paired_dry_run.add_argument("--case-position", type=int, required=True)
    paired_dry_run.add_argument("--output", type=Path, required=True)
    persona_paired_dry_run = sub.add_parser("persona-paired-dry-run")
    persona_paired_dry_run.add_argument("--case-receipt", type=Path, required=True)
    persona_paired_dry_run.add_argument("--case-position", type=int, required=True)
    persona_paired_dry_run.add_argument("--registration", type=Path, required=True)
    persona_paired_dry_run.add_argument(
        "--enrollment-receipt", type=Path, action="append", required=True
    )
    persona_paired_dry_run.add_argument("--arm-mapping", type=Path, required=True)
    persona_paired_dry_run.add_argument("--artifact-root", type=Path, required=True)
    persona_paired_dry_run.add_argument("--output", type=Path, required=True)
    arm_receipt = sub.add_parser("arm-receipt")
    arm_receipt.add_argument("--dry-run", type=Path, required=True)
    arm_receipt.add_argument("--execution-receipt", type=Path, required=True)
    arm_receipt.add_argument("--artifact-root", type=Path, required=True)
    arm_receipt.add_argument("--output", type=Path, required=True)
    terminal_receipt = sub.add_parser("terminal-receipt")
    terminal_receipt.add_argument("--dry-run", type=Path, required=True)
    terminal_receipt.add_argument("--arm-receipt", type=Path, action="append", required=True)
    terminal_receipt.add_argument("--output", type=Path, required=True)
    decision = sub.add_parser("decide")
    decision.add_argument("--terminal-receipt", type=Path, action="append", required=True)
    decision.add_argument("--readiness", type=Path, required=True)
    decision.add_argument("--output", type=Path, required=True)
    persona_decision = sub.add_parser("persona-decision")
    persona_decision.add_argument(
        "--terminal-receipt", type=Path, action="append", required=True
    )
    persona_decision.add_argument("--adjudication-receipt", type=Path, required=True)
    persona_decision.add_argument("--arm-mapping", type=Path, required=True)
    persona_decision.add_argument("--registration", type=Path, required=True)
    persona_decision.add_argument(
        "--enrollment-receipt", type=Path, action="append", required=True
    )
    persona_decision.add_argument("--artifact-root", type=Path, required=True)
    persona_decision.add_argument("--output", type=Path, required=True)
    collection_close = sub.add_parser("persona-collection-close")
    collection_close.add_argument("--registration", type=Path, required=True)
    collection_close.add_argument(
        "--enrollment-receipt", type=Path, action="append", default=[]
    )
    collection_close.add_argument("--arm-mapping", type=Path, required=True)
    collection_close.add_argument("--collection-close-receipt", type=Path, required=True)
    collection_close.add_argument("--artifact-root", type=Path, required=True)
    collection_close.add_argument("--output", type=Path, required=True)
    signing_payload = sub.add_parser("persona-signing-payload")
    signing_payload.add_argument("--kind", required=True)
    signing_payload.add_argument("--receipt", type=Path, required=True)
    signing_payload.add_argument("--output", type=Path, required=True)
    verify_decision = sub.add_parser("persona-verify-decision")
    verify_decision.add_argument("--decision", type=Path, required=True)
    verify_decision.add_argument("--artifact-root", type=Path, required=True)
    verify_decision.add_argument("--output", type=Path, required=True)
    prepare_reconciliation = sub.add_parser("prepare-reconciliation")
    prepare_reconciliation.add_argument("--pilot-id", required=True)
    prepare_reconciliation.add_argument(
        "--artifact-root", action="append", required=True,
        help="declared root in root-id=/absolute/or/relative/path form",
    )
    prepare_reconciliation.add_argument("--observed-at", required=True)
    prepare_reconciliation.add_argument("--output", type=Path, required=True)
    record_reconciliation = sub.add_parser("record-reconciliation")
    record_reconciliation.add_argument("--subject", type=Path, required=True)
    record_reconciliation.add_argument("--authority-receipt", type=Path, required=True)
    record_reconciliation.add_argument("--output", type=Path, required=True)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        if args.command == "prepare-roster":
            value = build_pilot_roster(
                args.repository,
                pilot_id=args.pilot_id,
                pilot_contract_sha256=args.pilot_contract_sha256,
                source_commit=args.source_commit,
            )
        elif args.command == "inventory-dry-run":
            roster = _read_json_object(args.roster)
            start_receipt = _read_json_object(args.start_receipt)
            binding = _expected_start_binding(start_receipt, roster)
            start = validate_pilot_start_receipt(
                start_receipt, expected_binding=binding
            )
            value = build_inventory_dry_run(roster, t0=start["t0"])
        elif args.command == "readiness":
            roster = _read_json_object(args.roster)
            start_receipt = _read_json_object(args.start_receipt)
            binding = _expected_start_binding(start_receipt, roster)
            start = validate_pilot_start_receipt(
                start_receipt, expected_binding=binding
            )
            value = build_readiness_receipt(
                roster, start, _read_json_object(args.inventory)
            )
        elif args.command == "freeze-case":
            value = freeze_case(
                _read_json_object(args.case), readiness_receipt=_read_json_object(args.readiness)
            )
        elif args.command == "persona-freeze-case":
            value = freeze_persona_case(
                _read_json_object(args.case),
                registration_receipt=_read_json_object(args.registration),
                arm_mapping_receipt=_read_json_object(args.arm_mapping),
            )
        elif args.command == "paired-dry-run":
            value = build_paired_dry_run(
                _read_json_object(args.case_receipt), case_position=args.case_position
            )
        elif args.command == "persona-paired-dry-run":
            value = build_enrolled_persona_paired_dry_run(
                _read_json_object(args.case_receipt),
                case_position=args.case_position,
                registration_receipt=_read_json_object(args.registration),
                enrollment_receipts=[
                    _read_json_object(path) for path in args.enrollment_receipt
                ],
                arm_mapping_receipt=_read_json_object(args.arm_mapping),
                artifact_root=args.artifact_root,
            )
        elif args.command == "arm-receipt":
            value = build_runner_arm_receipt(
                _read_json_object(args.dry_run),
                args.execution_receipt,
                artifact_root=args.artifact_root,
            )
        elif args.command == "terminal-receipt":
            value = build_terminal_receipt(
                _read_json_object(args.dry_run),
                [_read_json_object(path) for path in args.arm_receipt],
            )
        elif args.command == "decide":
            value = build_pilot_decision(
                [_read_json_object(path) for path in args.terminal_receipt],
                readiness_receipt=_read_json_object(args.readiness),
            )
        elif args.command == "persona-decision":
            value = build_enrolled_persona_study_decision(
                [_read_json_object(path) for path in args.terminal_receipt],
                adjudication_receipt=_read_json_object(args.adjudication_receipt),
                arm_mapping_receipt=_read_json_object(args.arm_mapping),
                registration_receipt=_read_json_object(args.registration),
                enrollment_receipts=[
                    _read_json_object(path) for path in args.enrollment_receipt
                ],
                artifact_root=args.artifact_root,
            )
        elif args.command == "persona-collection-close":
            value = build_persona_collection_close_decision(
                _read_json_object(args.registration),
                [_read_json_object(path) for path in args.enrollment_receipt],
                arm_mapping_receipt=_read_json_object(args.arm_mapping),
                collection_close_receipt=_read_json_object(
                    args.collection_close_receipt
                ),
                artifact_root=args.artifact_root,
            )
        elif args.command == "persona-signing-payload":
            value = build_persona_signing_payload(
                _read_json_object(args.receipt), kind=args.kind
            )
        elif args.command == "persona-verify-decision":
            value = verify_enrolled_persona_study_decision(
                _read_json_object(args.decision), artifact_root=args.artifact_root
            )
        elif args.command == "prepare-reconciliation":
            value = prepare_reconciliation_subject(
                pilot_id=args.pilot_id,
                declared_roots=[
                    _parse_reconciliation_root(root)
                    for root in args.artifact_root
                ],
                observed_at=args.observed_at,
            )
        elif args.command == "record-reconciliation":
            value = record_reconciliation_receipt(
                _read_json_object(args.subject),
                _read_json_object(args.authority_receipt),
            )
        else:
            value = build_execution_capability_matrix(
                source_repository=args.source_repository,
                source_commit=args.source_commit,
                pilot_contract_sha256=args.pilot_contract_sha256,
            )
        write_json_no_replace(args.output, value)
    except PilotPreflightError as exc:
        print(json.dumps({"status": "blocked", "reason": str(exc)}))
        return 2
    print(json.dumps(value, ensure_ascii=False, sort_keys=True))
    return 0


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


if __name__ == "__main__":
    raise SystemExit(main())
