#!/usr/bin/env python3
"""Domain-neutral preregistration registry and RFC 3161 receipt primitives."""

from __future__ import annotations

from copy import deepcopy
from datetime import datetime
import hashlib
import hmac
import json
from pathlib import Path, PurePosixPath
import subprocess
from typing import Any

import omc_rfc3161_timestamp as rfc3161


REGISTRY_RECORD_FIELDS = {
    "schema_version",
    "batch_id",
    "preregistration_sha256",
}
SIGSTORE_RECEIPT_FIELDS = {
    "schema_version",
    "status",
    "batch_id",
    "preregistration_sha256",
    "registry_commit",
    "registry_path",
    "registered_at",
    "registration_evidence",
    "receipt_sha256",
}
LOWER_HEX = frozenset("0123456789abcdef")


def canonical_digest(value: Any) -> str:
    payload = json.dumps(
        value,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def unsigned_document_digest(document: dict[str, Any], digest_field: str) -> str:
    payload = deepcopy(document)
    payload.pop(digest_field, None)
    return canonical_digest(payload)


def _is_lower_hex(value: Any, length: int) -> bool:
    return (
        isinstance(value, str)
        and len(value) == length
        and set(value) <= LOWER_HEX
    )


def _valid_relative_path(value: Any) -> bool:
    if not isinstance(value, str) or not value.strip():
        return False
    path = PurePosixPath(value)
    return not path.is_absolute() and ".." not in path.parts


def _parse_timestamp(value: Any) -> datetime:
    if not isinstance(value, str):
        raise ValueError("timestamp is invalid")
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError as error:
        raise ValueError("timestamp is invalid") from error
    if parsed.tzinfo is None:
        raise ValueError("timestamp must include timezone")
    return parsed


def _git_output(root: Path, *args: str, strip: bool = True) -> str | None:
    result = subprocess.run(
        ["git", "-C", str(root), *args],
        check=False,
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        return None
    return result.stdout.strip() if strip else result.stdout


def prepare_registry_record(
    *,
    batch_id: str,
    preregistration_sha256: str,
) -> dict[str, Any]:
    if (
        not isinstance(batch_id, str)
        or not batch_id.strip()
        or not _is_lower_hex(preregistration_sha256, 64)
    ):
        raise ValueError("preregistration registry record is invalid")
    return {
        "schema_version": 1,
        "batch_id": batch_id,
        "preregistration_sha256": preregistration_sha256,
    }


def validate_registry_anchor(
    record: dict[str, Any],
    *,
    repository_root: str | Path,
    registry_commit: str,
    registry_path: str,
    required_ancestor_commit: str | None = None,
) -> None:
    root = Path(repository_root)
    if (
        not isinstance(record, dict)
        or set(record) != REGISTRY_RECORD_FIELDS
        or record != prepare_registry_record(
            batch_id=record.get("batch_id"),
            preregistration_sha256=record.get("preregistration_sha256"),
        )
        or not root.is_absolute()
        or not _is_lower_hex(registry_commit, 40)
        or not _valid_relative_path(registry_path)
        or (
            required_ancestor_commit is not None
            and not _is_lower_hex(required_ancestor_commit, 40)
        )
    ):
        raise ValueError("preregistration registry anchor is invalid")
    if _git_output(root, "cat-file", "-e", f"{registry_commit}^{{commit}}") is None:
        raise ValueError("preregistration registry anchor is invalid")
    if required_ancestor_commit is not None and _git_output(
        root,
        "merge-base",
        "--is-ancestor",
        required_ancestor_commit,
        registry_commit,
    ) is None:
        raise ValueError("preregistration registry ancestry is invalid")
    raw_record = _git_output(
        root,
        "show",
        f"{registry_commit}:{registry_path}",
        strip=False,
    )
    try:
        committed_record = json.loads(raw_record) if raw_record is not None else None
    except json.JSONDecodeError as error:
        raise ValueError("preregistration registry record is invalid") from error
    if committed_record != record:
        raise ValueError("preregistration registry record is invalid")


def _validate_sigstore_receipt_envelope(receipt: Any) -> None:
    if (
        not isinstance(receipt, dict)
        or set(receipt) != SIGSTORE_RECEIPT_FIELDS
        or receipt.get("schema_version") != 2
        or receipt.get("status") != "registered"
        or not isinstance(receipt.get("batch_id"), str)
        or not receipt["batch_id"].strip()
        or not _is_lower_hex(receipt.get("preregistration_sha256"), 64)
        or not _is_lower_hex(receipt.get("registry_commit"), 40)
        or not _valid_relative_path(receipt.get("registry_path"))
        or not _is_lower_hex(receipt.get("receipt_sha256"), 64)
    ):
        raise ValueError("preregistration registration receipt is invalid")
    _parse_timestamp(receipt.get("registered_at"))


def prepare_sigstore_registration_receipt(
    *,
    batch_id: str,
    preregistration_sha256: str,
    registry_commit: str,
    registry_path: str,
    registration_authority: dict[str, Any],
    observation_starts_at: str,
    registration_evidence: dict[str, Any],
    trusted_root: dict[str, Any],
    approved_trusted_root_sha256: str,
) -> dict[str, Any]:
    if registration_authority != rfc3161.trust_identity(
        trusted_root,
        expected_trusted_root_sha256=approved_trusted_root_sha256,
    ):
        raise ValueError("Sigstore registration authority mismatch")
    claim = rfc3161.registration_claim(
        batch_id=batch_id,
        preregistration_sha256=preregistration_sha256,
        registry_commit=registry_commit,
        registry_path=registry_path,
    )
    verified_evidence = rfc3161.verify_registration_evidence(
        registration_evidence,
        claim=claim,
        trusted_root=trusted_root,
        expected_trusted_root_sha256=approved_trusted_root_sha256,
        observation_starts_at=observation_starts_at,
    )
    registered_at = verified_evidence.get("gen_time")
    if _parse_timestamp(registered_at) >= _parse_timestamp(observation_starts_at):
        raise ValueError("registration receipt time is invalid")
    receipt = {
        "schema_version": 2,
        "status": "registered",
        "batch_id": batch_id,
        "preregistration_sha256": preregistration_sha256,
        "registry_commit": registry_commit,
        "registry_path": registry_path,
        "registered_at": registered_at,
        "registration_evidence": deepcopy(verified_evidence),
        "receipt_sha256": "",
    }
    receipt["receipt_sha256"] = unsigned_document_digest(
        receipt,
        "receipt_sha256",
    )
    _validate_sigstore_receipt_envelope(receipt)
    return receipt


def validate_sigstore_registration_receipt(
    receipt: dict[str, Any],
    *,
    batch_id: str,
    preregistration_sha256: str,
    registration_authority: dict[str, Any],
    observation_starts_at: str,
    expected_receipt_sha256: str,
    trusted_root: dict[str, Any],
    approved_trusted_root_sha256: str,
) -> None:
    _validate_sigstore_receipt_envelope(receipt)
    if (
        receipt["receipt_sha256"] != expected_receipt_sha256
        or not hmac.compare_digest(
            receipt["receipt_sha256"],
            unsigned_document_digest(receipt, "receipt_sha256"),
        )
    ):
        raise ValueError("preregistration registration receipt digest mismatch")
    if (
        receipt["batch_id"] != batch_id
        or receipt["preregistration_sha256"] != preregistration_sha256
    ):
        raise ValueError("preregistration registration receipt mismatch")
    if _parse_timestamp(receipt["registered_at"]) >= _parse_timestamp(
        observation_starts_at
    ):
        raise ValueError("registration receipt time is invalid")
    if registration_authority != rfc3161.trust_identity(
        trusted_root,
        expected_trusted_root_sha256=approved_trusted_root_sha256,
    ):
        raise ValueError("registration receipt authority mismatch")
    claim = rfc3161.registration_claim(
        batch_id=batch_id,
        preregistration_sha256=preregistration_sha256,
        registry_commit=receipt["registry_commit"],
        registry_path=receipt["registry_path"],
    )
    verified_evidence = rfc3161.verify_registration_evidence(
        receipt["registration_evidence"],
        claim=claim,
        trusted_root=trusted_root,
        expected_trusted_root_sha256=approved_trusted_root_sha256,
        observation_starts_at=observation_starts_at,
    )
    if receipt["registered_at"] != verified_evidence.get("gen_time"):
        raise ValueError("preregistration registration receipt mismatch")
