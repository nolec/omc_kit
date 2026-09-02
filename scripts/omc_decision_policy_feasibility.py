#!/usr/bin/env python3
"""Evidence-bound contracts for the Decision Policy feasibility study."""

from __future__ import annotations

import base64
import binascii
import hashlib
import json
import os
import re
import stat
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from typing import Any

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import (
    Ed25519PrivateKey,
    Ed25519PublicKey,
)


CORPUS_SCHEMA = "omc-decision-policy-failure-corpus/v2"
CAUSAL_RECEIPT_SCHEMA = "omc-decision-policy-causal-review/v1"
POLICY_SCHEMA = "omc-decision-policy-packet/v2"
POLICY_APPROVAL_SCHEMA = "omc-decision-policy-approval/v1"
PAIRED_SCHEMA = "omc-decision-policy-paired-packet/v2"
TARGET_CASE_COUNT = 5

_SHA256_RE = re.compile(r"[0-9a-f]{64}")
_COMMIT_RE = re.compile(r"[0-9a-f]{40}")
_ID_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,127}")
_POLICY_FIELDS = {
    "case_id",
    "decision_priorities",
    "tradeoff_policy",
    "evidence_boundary",
    "stop_conditions",
}
_SUBJECT_FIELDS = {
    "case_id",
    "request_sha256",
    "base_commit",
    "source_tree",
    "runner_sha256",
    "adapter_sha256",
    "tool_contract_sha256",
}
_OBSERVED_FAILURES = {
    "validation_loop",
    "goal_drift",
    "redundant_confirmation",
    "incomplete_delivery",
}


def _canonical_bytes(value: Any) -> bytes:
    return json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")


def canonical_sha256(value: Any) -> str:
    return hashlib.sha256(_canonical_bytes(value)).hexdigest()


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def public_key_b64(private_key: Ed25519PrivateKey) -> str:
    return base64.b64encode(
        private_key.public_key().public_bytes(
            encoding=serialization.Encoding.Raw,
            format=serialization.PublicFormat.Raw,
        )
    ).decode("ascii")


def _require_id(value: object, field: str) -> str:
    text = str(value or "").strip()
    if _ID_RE.fullmatch(text) is None:
        raise ValueError(f"{field}_invalid")
    return text


def _require_digest(
    value: object, field: str, pattern: re.Pattern[str] = _SHA256_RE
) -> str:
    text = str(value or "")
    if pattern.fullmatch(text) is None:
        raise ValueError(f"{field}_invalid")
    return text


def _require_text(value: object, field: str) -> str:
    text = str(value or "").strip()
    if not text:
        raise ValueError(f"{field}_invalid")
    return text


def _require_text_list(value: object, field: str) -> list[str]:
    if not isinstance(value, list) or not value:
        raise ValueError(f"{field}_invalid")
    normalized = [_require_text(item, field) for item in value]
    if len(normalized) != len(set(normalized)):
        raise ValueError(f"{field}_duplicate")
    return normalized


def _parse_timestamp(value: object, field: str) -> datetime:
    text = _require_text(value, field)
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError(f"{field}_invalid") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError(f"{field}_timezone_required")
    return parsed.astimezone(timezone.utc)


def _artifact_parts(value: object, field: str) -> tuple[str, ...]:
    text = str(value or "").strip().replace("\\", "/")
    relative = PurePosixPath(text)
    if (
        not text
        or relative.is_absolute()
        or any(part in {"", ".", ".."} for part in relative.parts)
    ):
        raise ValueError(f"{field}_unsafe")
    return relative.parts


def _read_artifact_once(root: Path, value: object, field: str) -> tuple[Path, bytes]:
    parts = _artifact_parts(value, field)
    resolved_root = root.resolve()
    nofollow = getattr(os, "O_NOFOLLOW", None)
    if nofollow is None:
        raise ValueError(f"{field}_nofollow_unsupported")
    directory_flags = os.O_RDONLY | os.O_DIRECTORY | nofollow
    file_flags = os.O_RDONLY | nofollow
    if hasattr(os, "O_CLOEXEC"):
        directory_flags |= os.O_CLOEXEC
        file_flags |= os.O_CLOEXEC
    directory_fd = -1
    file_fd = -1
    try:
        directory_fd = os.open(resolved_root, directory_flags)
        for component in parts[:-1]:
            next_fd = os.open(component, directory_flags, dir_fd=directory_fd)
            os.close(directory_fd)
            directory_fd = next_fd
        file_fd = os.open(parts[-1], file_flags, dir_fd=directory_fd)
        if not stat.S_ISREG(os.fstat(file_fd).st_mode):
            raise ValueError(f"{field}_untrusted")
        chunks: list[bytes] = []
        while chunk := os.read(file_fd, 1024 * 1024):
            chunks.append(chunk)
        return resolved_root.joinpath(*parts), b"".join(chunks)
    except OSError as exc:
        raise ValueError(f"{field}_untrusted") from exc
    finally:
        if file_fd >= 0:
            os.close(file_fd)
        if directory_fd >= 0:
            os.close(directory_fd)


def _load_json_artifact(
    root: Path, value: object, field: str
) -> tuple[Path, dict[str, Any], str]:
    path, raw = _read_artifact_once(root, value, field)
    try:
        payload = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError(f"{field}_invalid_json") from exc
    if not isinstance(payload, dict):
        raise ValueError(f"{field}_not_object")
    return path, payload, hashlib.sha256(raw).hexdigest()


def _verify_hash(payload: dict[str, Any], field: str) -> None:
    unsigned = dict(payload)
    expected = unsigned.pop(field, None)
    if expected != canonical_sha256(unsigned):
        raise ValueError(f"{field}_mismatch")


def _sign_receipt(
    body: dict[str, Any], *, private_key: Ed25519PrivateKey, hash_field: str
) -> dict[str, Any]:
    signed = dict(body)
    signed["signer_public_key"] = public_key_b64(private_key)
    signed["signature"] = base64.b64encode(
        private_key.sign(_canonical_bytes(signed))
    ).decode("ascii")
    signed[hash_field] = canonical_sha256(signed)
    return signed


def _verify_signed_receipt(
    receipt: object,
    *,
    trusted_public_key: str,
    hash_field: str,
    expected_schema: str,
) -> dict[str, Any]:
    if not isinstance(receipt, dict) or receipt.get("schema_version") != expected_schema:
        raise ValueError("receipt_schema_invalid")
    _verify_hash(receipt, hash_field)
    if receipt.get("signer_public_key") != trusted_public_key:
        raise ValueError("receipt_signer_untrusted")
    signing = dict(receipt)
    signing.pop(hash_field, None)
    signature = signing.pop("signature", None)
    try:
        public_bytes = base64.b64decode(trusted_public_key, validate=True)
        signature_bytes = base64.b64decode(str(signature or ""), validate=True)
        Ed25519PublicKey.from_public_bytes(public_bytes).verify(
            signature_bytes, _canonical_bytes(signing)
        )
    except (binascii.Error, ValueError, InvalidSignature) as exc:
        raise ValueError("receipt_signature_invalid") from exc
    return dict(receipt)


def build_causal_review_receipt(
    *,
    run_id: str,
    result_sha256: str,
    observed_failure: str,
    reviewer_id: str,
    reviewed_at: str,
    private_key: Ed25519PrivateKey,
) -> dict[str, Any]:
    if observed_failure not in _OBSERVED_FAILURES:
        raise ValueError("observed_failure_invalid")
    body = {
        "schema_version": CAUSAL_RECEIPT_SCHEMA,
        "run_id": _require_id(run_id, "run_id"),
        "result_sha256": _require_digest(result_sha256, "result_sha256"),
        "observed_failure": observed_failure,
        "causal_label": "decision_policy_absence",
        "reviewer_id": _require_id(reviewer_id, "reviewer_id"),
        "reviewed_at": reviewed_at,
    }
    _parse_timestamp(reviewed_at, "reviewed_at")
    return _sign_receipt(body, private_key=private_key, hash_field="receipt_sha256")


def _validate_causal_receipt(
    receipt: object,
    *,
    trusted_public_key: str,
    run_id: str,
    result_sha256: str,
) -> dict[str, Any]:
    validated = _verify_signed_receipt(
        receipt,
        trusted_public_key=trusted_public_key,
        hash_field="receipt_sha256",
        expected_schema=CAUSAL_RECEIPT_SCHEMA,
    )
    if (
        validated.get("run_id") != run_id
        or validated.get("result_sha256") != result_sha256
        or validated.get("causal_label") != "decision_policy_absence"
        or validated.get("observed_failure") not in _OBSERVED_FAILURES
    ):
        raise ValueError("causal_receipt_subject_mismatch")
    _require_id(validated.get("reviewer_id"), "reviewer_id")
    _parse_timestamp(validated.get("reviewed_at"), "reviewed_at")
    return validated


def _result_identity(result: dict[str, Any], result_sha256: str) -> dict[str, str]:
    if result.get("schema_version") != "omc-decision-policy-source-result/v1":
        raise ValueError("result_schema_invalid")
    instruction = _require_text(result.get("instruction"), "instruction")
    return {
        "run_id": _require_id(result.get("run_id"), "run_id"),
        "result_sha256": _require_digest(result_sha256, "result_sha256"),
        "request_sha256": hashlib.sha256(instruction.encode("utf-8")).hexdigest(),
        "base_commit": _require_digest(result.get("base_commit"), "base_commit", _COMMIT_RE),
        "source_tree": _require_digest(result.get("source_tree"), "source_tree", _COMMIT_RE),
    }


def validate_failure_corpus(
    payload: object,
    *,
    evidence_root: Path,
    trusted_causal_reviewer_public_key: str,
) -> dict[str, Any]:
    if not isinstance(payload, dict):
        raise ValueError("failure_corpus_not_object")
    expected = {
        "schema_version",
        "status",
        "study_id",
        "created_at",
        "case_count",
        "selection_policy",
        "trusted_causal_reviewer_public_key",
        "cases",
        "corpus_sha256",
    }
    if set(payload) != expected or payload.get("schema_version") != CORPUS_SCHEMA:
        raise ValueError("failure_corpus_fields_invalid")
    if payload.get("status") != "frozen":
        raise ValueError("failure_corpus_status_invalid")
    _require_id(payload.get("study_id"), "study_id")
    created_at = _parse_timestamp(payload.get("created_at"), "created_at")
    recorded_key = _require_text(
        payload.get("trusted_causal_reviewer_public_key"),
        "trusted_causal_reviewer_public_key",
    )
    trusted_key = _require_text(
        trusted_causal_reviewer_public_key,
        "trusted_causal_reviewer_public_key",
    )
    if recorded_key != trusted_key:
        raise ValueError("causal_reviewer_trust_root_mismatch")
    cases = payload.get("cases")
    if not isinstance(cases, list) or len(cases) != TARGET_CASE_COUNT:
        raise ValueError("failure_corpus_case_count_invalid")
    if payload.get("case_count") != TARGET_CASE_COUNT:
        raise ValueError("failure_corpus_case_count_invalid")
    if payload.get("selection_policy") != "fixed_policy_relevant_first_n_v2":
        raise ValueError("selection_policy_invalid")
    case_ids: list[str] = []
    run_ids: list[str] = []
    for case in cases:
        required = {
            "case_id",
            "run_id",
            "result_path",
            "result_sha256",
            "causal_receipt_path",
            "causal_receipt_sha256",
            "request_sha256",
            "base_commit",
            "source_tree",
            "observed_failure",
            "evidence_summary",
        }
        if not isinstance(case, dict) or set(case) != required:
            raise ValueError("case_fields_invalid")
        result_path, result, result_sha256 = _load_json_artifact(
            evidence_root, case.get("result_path"), "result_path"
        )
        if case.get("result_sha256") != result_sha256:
            raise ValueError("result_digest_mismatch")
        identity = _result_identity(result, result_sha256)
        for field in ("run_id", "request_sha256", "base_commit", "source_tree"):
            if case.get(field) != identity[field]:
                raise ValueError(f"result_{field}_mismatch")
        receipt_path, receipt, receipt_sha256 = _load_json_artifact(
            evidence_root, case.get("causal_receipt_path"), "causal_receipt_path"
        )
        if case.get("causal_receipt_sha256") != receipt_sha256:
            raise ValueError("causal_receipt_digest_mismatch")
        validated_receipt = _validate_causal_receipt(
            receipt,
            trusted_public_key=trusted_key,
            run_id=identity["run_id"],
            result_sha256=identity["result_sha256"],
        )
        if _parse_timestamp(validated_receipt["reviewed_at"], "reviewed_at") > created_at:
            raise ValueError("causal_review_after_corpus_freeze")
        if case.get("observed_failure") != validated_receipt["observed_failure"]:
            raise ValueError("observed_failure_mismatch")
        _require_text(case.get("evidence_summary"), "evidence_summary")
        case_ids.append(_require_id(case.get("case_id"), "case_id"))
        run_ids.append(identity["run_id"])
    if len(set(case_ids)) != TARGET_CASE_COUNT or len(set(run_ids)) != TARGET_CASE_COUNT:
        raise ValueError("failure_corpus_duplicate_case")
    _verify_hash(payload, "corpus_sha256")
    return dict(payload)


def build_failure_corpus(
    *,
    evidence_root: Path,
    study_id: str,
    created_at: str,
    trusted_causal_reviewer_public_key: str,
    cases: list[dict[str, object]],
) -> dict[str, Any]:
    frozen_cases: list[dict[str, Any]] = []
    for source in cases:
        result_path, result, result_sha256 = _load_json_artifact(
            evidence_root, source.get("result_path"), "result_path"
        )
        identity = _result_identity(result, result_sha256)
        receipt_path, receipt, receipt_sha256 = _load_json_artifact(
            evidence_root, source.get("causal_receipt_path"), "causal_receipt_path"
        )
        validated_receipt = _validate_causal_receipt(
            receipt,
            trusted_public_key=trusted_causal_reviewer_public_key,
            run_id=identity["run_id"],
            result_sha256=identity["result_sha256"],
        )
        frozen_cases.append(
            {
                "case_id": _require_id(source.get("case_id"), "case_id"),
                **identity,
                "result_path": str(source["result_path"]),
                "causal_receipt_path": str(source["causal_receipt_path"]),
                "causal_receipt_sha256": receipt_sha256,
                "observed_failure": validated_receipt["observed_failure"],
                "evidence_summary": _require_text(
                    source.get("evidence_summary"), "evidence_summary"
                ),
            }
        )
    payload: dict[str, Any] = {
        "schema_version": CORPUS_SCHEMA,
        "status": "frozen",
        "study_id": study_id,
        "created_at": created_at,
        "case_count": len(frozen_cases),
        "selection_policy": "fixed_policy_relevant_first_n_v2",
        "trusted_causal_reviewer_public_key": trusted_causal_reviewer_public_key,
        "cases": frozen_cases,
    }
    payload["corpus_sha256"] = canonical_sha256(payload)
    return validate_failure_corpus(
        payload,
        evidence_root=evidence_root,
        trusted_causal_reviewer_public_key=trusted_causal_reviewer_public_key,
    )


def _policy_content(
    *, corpus: dict[str, Any], authored_at: str, author_id: str, policies: list[dict[str, object]]
) -> dict[str, Any]:
    _parse_timestamp(authored_at, "authored_at")
    normalized: list[dict[str, Any]] = []
    for policy in policies:
        if not isinstance(policy, dict) or set(policy) != _POLICY_FIELDS:
            raise ValueError("policy_fields_invalid")
        normalized.append(
            {
                "case_id": _require_id(policy.get("case_id"), "case_id"),
                **{
                    field: _require_text_list(policy.get(field), field)
                    for field in _POLICY_FIELDS - {"case_id"}
                },
            }
        )
    expected_ids = {case["case_id"] for case in corpus["cases"]}
    actual_ids = [policy["case_id"] for policy in normalized]
    if set(actual_ids) != expected_ids or len(set(actual_ids)) != TARGET_CASE_COUNT:
        raise ValueError("policy_case_coverage_invalid")
    return {
        "corpus_sha256": corpus["corpus_sha256"],
        "authored_at": authored_at,
        "author_id": _require_id(author_id, "author_id"),
        "policies": normalized,
    }


def build_policy_approval_receipt(
    *,
    corpus: dict[str, Any],
    authored_at: str,
    author_id: str,
    policies: list[dict[str, object]],
    approver_id: str,
    approved_at: str,
    private_key: Ed25519PrivateKey,
) -> dict[str, Any]:
    content = _policy_content(
        corpus=corpus, authored_at=authored_at, author_id=author_id, policies=policies
    )
    _parse_timestamp(approved_at, "approved_at")
    body = {
        "schema_version": POLICY_APPROVAL_SCHEMA,
        "corpus_sha256": corpus["corpus_sha256"],
        "policy_content_sha256": canonical_sha256(content),
        "approver_id": _require_id(approver_id, "approver_id"),
        "approved_at": approved_at,
    }
    return _sign_receipt(body, private_key=private_key, hash_field="receipt_sha256")


def validate_policy_packet(
    payload: object,
    *,
    corpus: dict[str, Any],
    evidence_root: Path,
    trusted_causal_reviewer_public_key: str,
    trusted_approver_public_key: str,
    execution_not_before: str | None = None,
) -> dict[str, Any]:
    validate_failure_corpus(
        corpus,
        evidence_root=evidence_root,
        trusted_causal_reviewer_public_key=trusted_causal_reviewer_public_key,
    )
    if not isinstance(payload, dict):
        raise ValueError("policy_packet_not_object")
    expected = {
        "schema_version",
        "status",
        "corpus_sha256",
        "authored_at",
        "author_id",
        "author_constraints",
        "policies",
        "trusted_approver_public_key",
        "approval_receipt",
        "policy_packet_sha256",
    }
    if set(payload) != expected or payload.get("schema_version") != POLICY_SCHEMA:
        raise ValueError("policy_packet_fields_invalid")
    if payload.get("status") != "approved_pre_execution":
        raise ValueError("policy_packet_status_invalid")
    content = _policy_content(
        corpus=corpus,
        authored_at=str(payload.get("authored_at")),
        author_id=str(payload.get("author_id")),
        policies=payload.get("policies"),
    )
    recorded_key = _require_text(
        payload.get("trusted_approver_public_key"), "trusted_approver_public_key"
    )
    trusted_key = _require_text(
        trusted_approver_public_key, "trusted_approver_public_key"
    )
    if recorded_key != trusted_key:
        raise ValueError("policy_approver_trust_root_mismatch")
    approval = _verify_signed_receipt(
        payload.get("approval_receipt"),
        trusted_public_key=trusted_key,
        hash_field="receipt_sha256",
        expected_schema=POLICY_APPROVAL_SCHEMA,
    )
    if (
        approval.get("corpus_sha256") != corpus["corpus_sha256"]
        or approval.get("policy_content_sha256") != canonical_sha256(content)
    ):
        raise ValueError("policy_approval_subject_mismatch")
    authored = _parse_timestamp(payload.get("authored_at"), "authored_at")
    approved = _parse_timestamp(approval.get("approved_at"), "approved_at")
    corpus_created = _parse_timestamp(corpus.get("created_at"), "created_at")
    if authored < corpus_created:
        raise ValueError("policy_authored_before_corpus_freeze")
    if approved < authored:
        raise ValueError("policy_approval_chronology_invalid")
    if execution_not_before is not None and approved >= _parse_timestamp(
        execution_not_before, "execution_not_before"
    ):
        raise ValueError("policy_not_approved_before_execution")
    if payload.get("author_constraints") != {
        "provider_outputs_visible": False,
        "roleplay_forbidden": True,
        "implementation_hints_forbidden": True,
    }:
        raise ValueError("author_constraints_invalid")
    _verify_hash(payload, "policy_packet_sha256")
    return dict(payload)


def build_policy_packet(
    *,
    evidence_root: Path,
    corpus: dict[str, Any],
    authored_at: str,
    author_id: str,
    policies: list[dict[str, object]],
    trusted_approver_public_key: str,
    trusted_causal_reviewer_public_key: str,
    approval_receipt: dict[str, Any],
) -> dict[str, Any]:
    validate_failure_corpus(
        corpus,
        evidence_root=evidence_root,
        trusted_causal_reviewer_public_key=trusted_causal_reviewer_public_key,
    )
    content = _policy_content(
        corpus=corpus, authored_at=authored_at, author_id=author_id, policies=policies
    )
    payload: dict[str, Any] = {
        "schema_version": POLICY_SCHEMA,
        "status": "approved_pre_execution",
        **content,
        "author_constraints": {
            "provider_outputs_visible": False,
            "roleplay_forbidden": True,
            "implementation_hints_forbidden": True,
        },
        "trusted_approver_public_key": trusted_approver_public_key,
        "approval_receipt": approval_receipt,
    }
    payload["policy_packet_sha256"] = canonical_sha256(payload)
    return validate_policy_packet(
        payload,
        corpus=corpus,
        evidence_root=evidence_root,
        trusted_causal_reviewer_public_key=trusted_causal_reviewer_public_key,
        trusted_approver_public_key=trusted_approver_public_key,
    )


def _validate_execution_subject(subject: object) -> dict[str, str]:
    if not isinstance(subject, dict) or set(subject) != _SUBJECT_FIELDS:
        raise ValueError("execution_subject_fields_invalid")
    normalized = {"case_id": _require_id(subject.get("case_id"), "case_id")}
    for field in _SUBJECT_FIELDS - {"case_id", "base_commit", "source_tree"}:
        normalized[field] = _require_digest(subject.get(field), field)
    for field in ("base_commit", "source_tree"):
        normalized[field] = _require_digest(subject.get(field), field, _COMMIT_RE)
    return normalized


def validate_paired_packet(
    payload: object,
    *,
    corpus: dict[str, Any],
    policy_packet: dict[str, Any],
    evidence_root: Path,
    trusted_causal_reviewer_public_key: str,
    trusted_approver_public_key: str,
) -> dict[str, Any]:
    validate_failure_corpus(
        corpus,
        evidence_root=evidence_root,
        trusted_causal_reviewer_public_key=trusted_causal_reviewer_public_key,
    )
    if not isinstance(payload, dict):
        raise ValueError("paired_packet_not_object")
    expected = {
        "schema_version",
        "status",
        "corpus_sha256",
        "policy_packet_sha256",
        "execution_not_before",
        "execution_controls",
        "case_subjects",
        "execution_order",
        "metrics",
        "pass_policy",
        "paired_packet_sha256",
    }
    if set(payload) != expected or payload.get("schema_version") != PAIRED_SCHEMA:
        raise ValueError("paired_packet_fields_invalid")
    if payload.get("status") != "frozen_pre_execution":
        raise ValueError("paired_packet_status_invalid")
    execution_not_before = str(payload.get("execution_not_before"))
    _parse_timestamp(execution_not_before, "execution_not_before")
    validate_policy_packet(
        policy_packet,
        corpus=corpus,
        evidence_root=evidence_root,
        trusted_causal_reviewer_public_key=trusted_causal_reviewer_public_key,
        trusted_approver_public_key=trusted_approver_public_key,
        execution_not_before=execution_not_before,
    )
    if payload.get("corpus_sha256") != corpus["corpus_sha256"]:
        raise ValueError("paired_corpus_mismatch")
    if payload.get("policy_packet_sha256") != policy_packet["policy_packet_sha256"]:
        raise ValueError("paired_policy_mismatch")
    controls = payload.get("execution_controls")
    if not isinstance(controls, dict) or set(controls) != {
        "provider",
        "model",
        "reasoning",
        "timeout_sec",
    }:
        raise ValueError("execution_controls_invalid")
    for field in ("provider", "model", "reasoning"):
        _require_text(controls.get(field), field)
    if not isinstance(controls.get("timeout_sec"), int) or controls["timeout_sec"] <= 0:
        raise ValueError("timeout_sec_invalid")
    subjects = payload.get("case_subjects")
    if not isinstance(subjects, list) or len(subjects) != TARGET_CASE_COUNT:
        raise ValueError("case_subjects_invalid")
    corpus_cases = {case["case_id"]: case for case in corpus["cases"]}
    expected_ids = set(corpus_cases)
    actual_ids: list[str] = []
    for pair in subjects:
        if not isinstance(pair, dict) or set(pair) != {"case_id", "baseline", "policy"}:
            raise ValueError("case_subject_pair_invalid")
        case_id = _require_id(pair.get("case_id"), "case_id")
        baseline = _validate_execution_subject(pair.get("baseline"))
        policy = _validate_execution_subject(pair.get("policy"))
        if baseline != policy:
            raise ValueError("paired_subject_mismatch")
        if baseline["case_id"] != case_id:
            raise ValueError("paired_subject_case_mismatch")
        corpus_case = corpus_cases.get(case_id)
        if corpus_case is None:
            raise ValueError("paired_subject_unknown_case")
        for field in ("request_sha256", "base_commit", "source_tree"):
            if baseline[field] != corpus_case[field]:
                raise ValueError("paired_subject_corpus_mismatch")
        actual_ids.append(case_id)
    if set(actual_ids) != expected_ids or len(set(actual_ids)) != TARGET_CASE_COUNT:
        raise ValueError("case_subject_coverage_invalid")
    expected_order = {
        f"{case_id}:{arm}" for case_id in expected_ids for arm in ("baseline", "policy")
    }
    order = payload.get("execution_order")
    if not isinstance(order, list) or len(order) != 10 or set(order) != expected_order:
        raise ValueError("execution_order_invalid")
    if payload.get("metrics") != [
        "completion",
        "critical_omission",
        "blind_quality",
        "validation_rounds",
        "user_interventions",
        "total_tokens",
    ]:
        raise ValueError("metrics_invalid")
    if payload.get("pass_policy") != {
        "completion_noninferior": True,
        "critical_omission_regressions_allowed": 0,
        "blind_quality_losses_allowed": 0,
        "user_interventions_must_decrease": True,
        "validation_or_tokens_must_improve": True,
    }:
        raise ValueError("pass_policy_invalid")
    _verify_hash(payload, "paired_packet_sha256")
    return dict(payload)


def build_paired_packet(
    *,
    evidence_root: Path,
    corpus: dict[str, Any],
    policy_packet: dict[str, Any],
    provider: str,
    model: str,
    reasoning: str,
    timeout_sec: int,
    execution_not_before: str,
    execution_order: list[str],
    execution_subjects: list[dict[str, object]],
    trusted_causal_reviewer_public_key: str,
    trusted_approver_public_key: str,
) -> dict[str, Any]:
    validate_failure_corpus(
        corpus,
        evidence_root=evidence_root,
        trusted_causal_reviewer_public_key=trusted_causal_reviewer_public_key,
    )
    subjects = [_validate_execution_subject(subject) for subject in execution_subjects]
    case_subjects = [
        {"case_id": subject["case_id"], "baseline": subject, "policy": dict(subject)}
        for subject in subjects
    ]
    payload: dict[str, Any] = {
        "schema_version": PAIRED_SCHEMA,
        "status": "frozen_pre_execution",
        "corpus_sha256": corpus["corpus_sha256"],
        "policy_packet_sha256": policy_packet["policy_packet_sha256"],
        "execution_not_before": execution_not_before,
        "execution_controls": {
            "provider": provider,
            "model": model,
            "reasoning": reasoning,
            "timeout_sec": timeout_sec,
        },
        "case_subjects": case_subjects,
        "execution_order": execution_order,
        "metrics": [
            "completion",
            "critical_omission",
            "blind_quality",
            "validation_rounds",
            "user_interventions",
            "total_tokens",
        ],
        "pass_policy": {
            "completion_noninferior": True,
            "critical_omission_regressions_allowed": 0,
            "blind_quality_losses_allowed": 0,
            "user_interventions_must_decrease": True,
            "validation_or_tokens_must_improve": True,
        },
    }
    payload["paired_packet_sha256"] = canonical_sha256(payload)
    return validate_paired_packet(
        payload,
        corpus=corpus,
        policy_packet=policy_packet,
        evidence_root=evidence_root,
        trusted_causal_reviewer_public_key=trusted_causal_reviewer_public_key,
        trusted_approver_public_key=trusted_approver_public_key,
    )


def diagnose_candidate_artifacts(
    result_paths: list[Path],
    *,
    evidence_root: Path,
    trusted_causal_reviewer_public_key: str,
) -> dict[str, Any]:
    eligible: list[dict[str, str]] = []
    excluded: list[dict[str, str]] = []
    seen_run_ids: set[str] = set()
    seen_result_digests: set[str] = set()
    for raw_path in result_paths:
        try:
            relative = raw_path.resolve().relative_to(evidence_root.resolve()).as_posix()
            result_path, result, result_sha256 = _load_json_artifact(
                evidence_root, relative, "result_path"
            )
            identity = _result_identity(result, result_sha256)
        except (ValueError, OSError):
            excluded.append({"run_id": raw_path.parent.name, "reason": "result_untrusted"})
            continue
        receipt_path = result_path.parent / "causal-review.json"
        if not receipt_path.is_file() or receipt_path.is_symlink():
            excluded.append({"run_id": identity["run_id"], "reason": "causal_receipt_missing"})
            continue
        if (
            identity["run_id"] in seen_run_ids
            or identity["result_sha256"] in seen_result_digests
        ):
            excluded.append({"run_id": identity["run_id"], "reason": "duplicate_result"})
            continue
        try:
            relative_receipt = receipt_path.resolve().relative_to(
                evidence_root.resolve()
            ).as_posix()
            _, receipt, _ = _load_json_artifact(
                evidence_root, relative_receipt, "causal_receipt_path"
            )
            validated = _validate_causal_receipt(
                receipt,
                trusted_public_key=trusted_causal_reviewer_public_key,
                run_id=identity["run_id"],
                result_sha256=identity["result_sha256"],
            )
        except (ValueError, OSError):
            excluded.append(
                {"run_id": identity["run_id"], "reason": "causal_receipt_untrusted"}
            )
            continue
        eligible.append(
            {
                "run_id": identity["run_id"],
                "result_path": relative,
                "causal_receipt_path": relative_receipt,
                "observed_failure": validated["observed_failure"],
            }
        )
        seen_run_ids.add(identity["run_id"])
        seen_result_digests.add(identity["result_sha256"])
    return {
        "schema_version": "omc-decision-policy-candidate-diagnosis/v2",
        "status": (
            "READY"
            if len(eligible) >= TARGET_CASE_COUNT
            else "COLLECTING" if eligible else "BLOCKED_NO_ELIGIBLE_CORPUS"
        ),
        "eligible_count": len(eligible),
        "target_case_count": TARGET_CASE_COUNT,
        "eligible": eligible,
        "excluded": excluded,
    }
