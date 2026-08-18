#!/usr/bin/env python3
"""Build a provenance-bound, post-freeze-seeded Plan candidate selection."""

from __future__ import annotations

import argparse
import base64
import binascii
import json
import subprocess
from collections import Counter, defaultdict
from copy import deepcopy
from datetime import datetime
from pathlib import Path
from pathlib import PurePosixPath
from typing import Any

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import (
    Ed25519PrivateKey,
    Ed25519PublicKey,
)

import omc_plan_runtime_pilot as runtime
import omc_plan_context_selection as context_selection
import omc_rfc3161_timestamp as rfc3161


SOURCE_RECORD_FIELDS = {
    "source_record_id",
    "session_id",
    "confirmed_request",
    "confirmed_at",
    "repo_alias",
    "repository_root",
    "baseline_commit",
    "followup_commit",
    "completed_at",
    "changed_paths",
    "context_candidate_paths",
    "surface",
    "ambiguity",
    "selected_object",
    "request_source",
    "completion_source",
    "provider_outputs_available",
}
HEX_DIGITS = frozenset("0123456789abcdef")
SESSION_SOURCE_FIELDS = {
    "source_record_id",
    "repo_alias",
    "repository_root",
    "session_id",
    "context_candidate_paths",
    "surface",
    "ambiguity",
    "selected_object",
}
PREREGISTERED_SESSION_SOURCE_FIELDS = SESSION_SOURCE_FIELDS | {"work_class"}
PREREGISTERED_SOURCE_MANIFEST_FIELDS = {
    "schema_version",
    "status",
    "preregistration_sha256",
    "preregistration_registry",
    "registration_receipt_sha256",
    "completeness_attestation",
    "sources",
    "signoff",
    "source_snapshot_sha256",
}
PREREGISTERED_SOURCE_MANIFEST_V3_FIELDS = (
    PREREGISTERED_SOURCE_MANIFEST_FIELDS | {"work_class_locks"}
)
WORK_CLASS_LOCK_EVIDENCE_FIELDS = {
    "session_id",
    "receipt_sha256",
    "signer_public_key",
    "signed_at",
}
PREREGISTRATION_REGISTRY_RECORD_FIELDS = {
    "schema_version",
    "batch_id",
    "preregistration_sha256",
}
PREREGISTRATION_REGISTRY_ANCHOR_FIELDS = {"commit", "path"}
PREREGISTRATION_RECEIPT_FIELDS = {
    "schema_version",
    "status",
    "batch_id",
    "preregistration_sha256",
    "registry_commit",
    "registry_path",
    "registered_at",
    "signoff",
    "receipt_sha256",
}
SIGSTORE_PREREGISTRATION_RECEIPT_FIELDS = {
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
PREREGISTERED_WORK_CLASSES = {
    "implementation",
    "synthetic",
    "document_only",
    "benchmark_maintenance",
}
SOURCE_SNAPSHOT_COMPLETENESS_ATTESTATION = {
    "scope": "all_sessions_observed_before_provider_cutoff",
    "omissions_allowed": False,
    "work_class_locked_before_collection": True,
}
INVENTORY_REJECTION_REASONS = {
    "invalid_provenance",
    "pilot_observed",
    "outside_window",
    "synthetic_task",
    "document_only_task",
    "benchmark_maintenance_task",
    "collection_limit_exceeded",
}
COMPLETION_RECEIPT_FIELDS = {
    "schema_version",
    "session_id",
    "request_sha256",
    "baseline_commit",
    "followup_commit",
    "completed_at",
    "changed_paths",
    "provider_outputs_available",
}
COMPLETION_RECEIPT_V2_FIELDS = COMPLETION_RECEIPT_FIELDS | {
    "work_class",
    "work_class_locked_at",
}
WORK_CLASS_LOCK_RECEIPT_FIELDS = {
    "schema_version",
    "status",
    "session_id",
    "work_class",
    "work_class_locked_at",
    "signed_at",
    "request_sha256",
    "baseline_commit",
    "signoff",
    "receipt_sha256",
}
UNIVERSE_FIELDS = {
    "schema_version",
    "status",
    "batch_id",
    "eligibility_policy",
    "selection_policy",
    "provenance",
    "audit",
    "candidates",
    "signoff",
    "universe_sha256",
}
UNIVERSE_PROVENANCE_FIELDS = {
    "private_inventory_sha256",
    "source_snapshot_sha256",
    "label_receipt_sha256",
    "provider_ledger_cutoff",
    "prior_anchor_registry_sha256",
    "prior_snapshot_sha256",
}
UNIVERSE_AUDIT_FIELDS = {
    "discovered_count",
    "accepted_count",
    "rejected_count",
    "rejected_reason_counts",
}
UNIVERSE_SELECTION_POLICY_FIELDS = {
    "algorithm_version",
    "seed_mode",
    "tie_breaker",
    "provider_outputs_available_during_selection",
    "prior_registry_sha256",
    "prior_anchor_registry_sha256",
    "required_surface_counts",
    "required_ambiguity_counts",
    "maximum_selected_object_cases",
}
COLLECTION_PREREGISTRATION_FIELDS = {
    "schema_version",
    "status",
    "batch_id",
    "collection_anchor_commit",
    "collection_anchor_committed_at",
    "observation_window",
    "provider_ledger_cutoff",
    "sampling_policy",
    "pilot_session_ids",
    "registration_authority_public_key",
    "signoff",
    "preregistration_sha256",
}
SIGSTORE_COLLECTION_PREREGISTRATION_FIELDS = (
    COLLECTION_PREREGISTRATION_FIELDS
    - {"registration_authority_public_key"}
    | {"registration_authority"}
)
COLLECTION_SAMPLING_POLICY = {
    "mode": "prospective_chronological_first_n",
    "maximum_accepted_receipts": 15,
    "provider_outputs_available_during_collection": False,
    "synthetic_tasks_allowed": False,
    "document_only_tasks_allowed": False,
    "benchmark_maintenance_tasks_allowed": False,
}
REVOKED_COLLECTION_PREREGISTRATION_SHA256S = frozenset({
    # Local authority key custody invalidated this historical Batch B.
    "bf651249b7d2d3c5e159f6e53ebfb9d623a7979c2ecb272cc97055e11e11c434",
})


def canonical_digest(value: Any) -> str:
    return runtime.canonical_digest(value)


def public_key_text(private_key: Ed25519PrivateKey) -> str:
    raw = private_key.public_key().public_bytes(
        encoding=serialization.Encoding.Raw,
        format=serialization.PublicFormat.Raw,
    )
    return base64.b64encode(raw).decode("ascii")


def _valid_public_key_text(value: Any) -> bool:
    if not isinstance(value, str):
        return False
    try:
        return len(base64.b64decode(value, validate=True)) == 32
    except (ValueError, TypeError):
        return False


def _is_lower_hex(value: Any, length: int) -> bool:
    return (
        isinstance(value, str)
        and len(value) == length
        and set(value) <= HEX_DIGITS
    )


def _is_git_commit_prefix(value: Any) -> bool:
    return (
        isinstance(value, str)
        and 7 <= len(value) <= 40
        and set(value) <= HEX_DIGITS
    )


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


def _relative_paths(value: Any) -> bool:
    if not isinstance(value, list) or not value:
        return False
    for item in value:
        if not isinstance(item, str) or not item.strip():
            return False
        path = PurePosixPath(item)
        if path.is_absolute() or ".." in path.parts:
            return False
    return True


def _git_output(root: Path, *args: str, strip: bool = True) -> str | None:
    try:
        result = subprocess.run(
            ["git", "-C", str(root), *args],
            check=True,
            capture_output=True,
            text=True,
        )
    except (OSError, subprocess.CalledProcessError):
        return None
    return result.stdout.strip() if strip else result.stdout


def _git_receipt_matches(
    root: Path,
    *,
    session_head: str,
    completion: dict[str, Any],
    context_candidate_paths: list[str],
) -> bool:
    baseline = completion.get("baseline_commit")
    followup = completion.get("followup_commit")
    changed_paths = completion.get("changed_paths")
    if (
        not root.is_absolute()
        or not _is_lower_hex(baseline, 40)
        or not _is_lower_hex(followup, 40)
        or not isinstance(session_head, str)
        or not session_head.strip()
        or not _relative_paths(changed_paths)
        or len(changed_paths) != len(set(changed_paths))
        or not _relative_paths(context_candidate_paths)
    ):
        return False
    resolved_baseline = _git_output(root, "rev-parse", f"{baseline}^{{commit}}")
    resolved_followup = _git_output(root, "rev-parse", f"{followup}^{{commit}}")
    resolved_session_head = _git_output(
        root, "rev-parse", f"{session_head}^{{commit}}"
    )
    if (
        resolved_baseline != baseline
        or resolved_followup != followup
        or resolved_session_head != baseline
        or _git_output(root, "merge-base", "--is-ancestor", baseline, followup)
        is None
    ):
        return False
    actual_changed = _git_output(
        root,
        "diff",
        "--name-only",
        "-z",
        "--diff-filter=ACDMRT",
        baseline,
        followup,
        "--",
        strip=False,
    )
    if actual_changed is None or sorted(
        path for path in actual_changed.split("\0") if path
    ) != sorted(
        changed_paths
    ):
        return False
    return all(
        _git_output(root, "cat-file", "-e", f"{baseline}:{path}") is not None
        for path in context_candidate_paths
    )


def _valid_source_record(record: Any, *, cutoff: datetime) -> bool:
    if not isinstance(record, dict) or set(record) != SOURCE_RECORD_FIELDS:
        return False
    try:
        confirmed_at = _parse_timestamp(record["confirmed_at"])
        completed_at = _parse_timestamp(record["completed_at"])
    except ValueError:
        return False
    return (
        isinstance(record["source_record_id"], str)
        and bool(record["source_record_id"].strip())
        and isinstance(record["session_id"], str)
        and bool(record["session_id"].strip())
        and isinstance(record["confirmed_request"], str)
        and bool(record["confirmed_request"].strip())
        and isinstance(record["repo_alias"], str)
        and bool(record["repo_alias"].strip())
        and isinstance(record["repository_root"], str)
        and record["repository_root"].startswith("/")
        and _is_lower_hex(record["baseline_commit"], 40)
        and _is_lower_hex(record["followup_commit"], 40)
        and record["baseline_commit"] != record["followup_commit"]
        and _relative_paths(record["changed_paths"])
        and _relative_paths(record["context_candidate_paths"])
        and record["surface"] in runtime.FROZEN_CONFIRMATORY_SURFACE_COUNTS
        and record["ambiguity"]
        in runtime.FROZEN_CONFIRMATORY_AMBIGUITY_COUNTS
        and type(record["selected_object"]) is bool
        and record["request_source"] == "confirmed_session_record"
        and record["completion_source"] == "explicit_completion_receipt"
        and record["provider_outputs_available"] is False
        and confirmed_at <= completed_at <= cutoff
    )


def _valid_session_source(source: Any) -> bool:
    return (
        isinstance(source, dict)
        and set(source) == SESSION_SOURCE_FIELDS
        and all(
            isinstance(source[field], str) and bool(source[field].strip())
            for field in (
                "source_record_id",
                "repo_alias",
                "repository_root",
                "session_id",
            )
        )
        and Path(source["repository_root"]).is_absolute()
        and _relative_paths(source["context_candidate_paths"])
        and source["surface"] in runtime.FROZEN_CONFIRMATORY_SURFACE_COUNTS
        and source["ambiguity"] in runtime.FROZEN_CONFIRMATORY_AMBIGUITY_COUNTS
        and type(source["selected_object"]) is bool
    )


def _inventory_digest(inventory: dict[str, Any]) -> str:
    return _signed_digest(inventory, "inventory_sha256")


def collect_private_inventory(
    records: list[dict[str, Any]],
    *,
    observed_from: str,
    observed_through: str,
    provider_ledger_cutoff: str,
) -> dict[str, Any]:
    """Validate explicit completion receipts without inferring missing evidence."""
    if not isinstance(records, list) or not records:
        raise ValueError("observed source records are required")
    start = datetime.fromisoformat(f"{observed_from}T00:00:00+00:00").date()
    end = datetime.fromisoformat(f"{observed_through}T00:00:00+00:00").date()
    if start > end:
        raise ValueError("observation window is invalid")
    cutoff = _parse_timestamp(provider_ledger_cutoff)

    accepted: list[dict[str, Any]] = []
    rejected: list[dict[str, str]] = []
    for record in records:
        if _valid_source_record(record, cutoff=cutoff):
            accepted.append(deepcopy(record))
            continue
        source_record_id = (
            record.get("source_record_id")
            if isinstance(record, dict)
            and isinstance(record.get("source_record_id"), str)
            else "unknown"
        )
        rejected.append({
            "source_record_id": source_record_id,
            "source_record_sha256": canonical_digest(record),
            "reason": "invalid_provenance",
        })

    accepted.sort(key=lambda item: item["source_record_id"])
    rejected.sort(key=lambda item: (item["source_record_id"], item["source_record_sha256"]))
    ids = [record["source_record_id"] for record in accepted]
    sessions = [record["session_id"] for record in accepted]
    if len(ids) != len(set(ids)) or len(sessions) != len(set(sessions)):
        raise ValueError("observed source identity must be unique")
    reason_counts = dict(sorted(Counter(
        record["reason"] for record in rejected
    ).items()))
    inventory = {
        "schema_version": 1,
        "status": "collected",
        "observation_window": {
            "observed_from": observed_from,
            "observed_through": observed_through,
        },
        "provider_ledger_cutoff": provider_ledger_cutoff,
        "accepted_records": accepted,
        "rejected_records": rejected,
        "audit": {
            "discovered_count": len(records),
            "accepted_count": len(accepted),
            "rejected_count": len(rejected),
            "rejected_reason_counts": reason_counts,
        },
        "signoff": {
            "signer": "observed-inventory-collector-v1",
            "signer_public_key": "",
            "signature": "",
        },
        "inventory_sha256": "",
    }
    inventory["inventory_sha256"] = _inventory_digest(inventory)
    return inventory


def _signed_digest(document: dict[str, Any], digest_field: str) -> str:
    payload = deepcopy(document)
    payload.pop(digest_field, None)
    payload["signoff"]["signature"] = ""
    return canonical_digest(payload)


def _signed_payload(document: dict[str, Any]) -> bytes:
    payload = deepcopy(document)
    payload["signoff"]["signature"] = ""
    return json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def _seal_document(
    document: dict[str, Any],
    *,
    private_key: Ed25519PrivateKey,
    digest_field: str,
) -> dict[str, Any]:
    sealed = deepcopy(document)
    sealed["signoff"]["signer_public_key"] = public_key_text(private_key)
    sealed["signoff"]["signature"] = ""
    sealed[digest_field] = _signed_digest(sealed, digest_field)
    sealed["signoff"]["signature"] = base64.b64encode(
        private_key.sign(_signed_payload(sealed))
    ).decode("ascii")
    return sealed


def _verify_document(
    document: dict[str, Any],
    *,
    digest_field: str,
    trusted_public_keys: set[str],
    expected_digest: str,
    expected_signer: str,
    label: str,
) -> None:
    signoff = document.get("signoff")
    signoff_fields = {"signer", "signer_public_key", "signature"}
    if (
        not isinstance(signoff, dict)
        or set(signoff) != signoff_fields
        or any(
            not isinstance(signoff[field], str) or not signoff[field]
            for field in signoff_fields
        )
    ):
        raise ValueError(f"{label} signoff is invalid")
    if signoff["signer"] != expected_signer:
        raise ValueError(f"{label} signer is invalid")
    public_key = signoff["signer_public_key"]
    if public_key not in trusted_public_keys:
        raise ValueError(f"{label} signer is not trusted")
    if document.get(digest_field) != expected_digest:
        raise ValueError(f"{label} expected hash mismatch")
    if document[digest_field] != _signed_digest(document, digest_field):
        raise ValueError(f"{label} digest mismatch")
    try:
        Ed25519PublicKey.from_public_bytes(
            base64.b64decode(public_key, validate=True)
        ).verify(
            base64.b64decode(signoff["signature"], validate=True),
            _signed_payload(document),
        )
    except (InvalidSignature, binascii.Error, ValueError) as error:
        raise ValueError(f"{label} signature is invalid") from error


def _validate_collection_preregistration_envelope(
    preregistration: dict[str, Any],
    *,
    expected_status: str,
) -> None:
    if not isinstance(preregistration, dict):
        raise ValueError("collection preregistration fields are invalid")
    schema_version = preregistration.get("schema_version")
    expected_fields = (
        COLLECTION_PREREGISTRATION_FIELDS
        if schema_version == 1
        else SIGSTORE_COLLECTION_PREREGISTRATION_FIELDS
        if schema_version == 2
        else None
    )
    if expected_fields is None or set(preregistration) != expected_fields:
        raise ValueError("collection preregistration fields are invalid")
    window = preregistration.get("observation_window")
    pilot_session_ids = preregistration.get("pilot_session_ids")
    if (
        preregistration.get("status") != expected_status
        or not isinstance(preregistration.get("batch_id"), str)
        or not preregistration["batch_id"].strip()
        or not _is_lower_hex(preregistration.get("collection_anchor_commit"), 40)
        or not isinstance(window, dict)
        or set(window) != {"observed_from", "observed_through"}
        or preregistration.get("sampling_policy") != COLLECTION_SAMPLING_POLICY
        or not isinstance(pilot_session_ids, list)
        or not pilot_session_ids
        or len(pilot_session_ids) != len(set(pilot_session_ids))
        or any(
            not isinstance(session_id, str) or not session_id.strip()
            for session_id in pilot_session_ids
        )
        or not _is_lower_hex(preregistration.get("preregistration_sha256"), 64)
    ):
        raise ValueError("collection preregistration provenance is invalid")
    if schema_version == 1:
        if not _valid_public_key_text(
            preregistration.get("registration_authority_public_key")
        ):
            raise ValueError("collection preregistration provenance is invalid")
    else:
        try:
            rfc3161.validate_trust_identity(
                preregistration.get("registration_authority")
            )
        except ValueError as error:
            raise ValueError(
                "collection preregistration provenance is invalid"
            ) from error
    try:
        anchor_committed_at = _parse_timestamp(
            preregistration["collection_anchor_committed_at"]
        )
        start = _parse_timestamp(window["observed_from"])
        end = _parse_timestamp(window["observed_through"])
        cutoff = _parse_timestamp(preregistration["provider_ledger_cutoff"])
    except (KeyError, TypeError, ValueError) as error:
        raise ValueError("collection preregistration provenance is invalid") from error
    if anchor_committed_at >= start or start > end or end > cutoff:
        raise ValueError("collection preregistration observation window is invalid")


def _verified_collection_anchor(
    repository_root: str,
    collection_anchor_commit: str,
) -> datetime:
    root = Path(repository_root)
    if not root.is_absolute() or not _is_lower_hex(collection_anchor_commit, 40):
        raise ValueError("collection anchor is invalid")
    resolved = _git_output(
        root, "rev-parse", f"{collection_anchor_commit}^{{commit}}"
    )
    committed_at = _git_output(
        root, "show", "-s", "--format=%cI", collection_anchor_commit
    )
    if resolved != collection_anchor_commit or committed_at is None:
        raise ValueError("collection anchor is invalid")
    try:
        return _parse_timestamp(committed_at)
    except ValueError as error:
        raise ValueError("collection anchor is invalid") from error


def _validate_pilot_session_ids(pilot_session_ids: Any) -> list[str]:
    if (
        not isinstance(pilot_session_ids, list)
        or not pilot_session_ids
        or any(
            not isinstance(session_id, str) or not session_id.strip()
            for session_id in pilot_session_ids
        )
        or len(pilot_session_ids) != len(set(pilot_session_ids))
    ):
        raise ValueError("pilot session ids are invalid")
    return sorted(pilot_session_ids)


def _reject_revoked_collection_preregistration(
    preregistration: Any,
) -> None:
    digest = (
        preregistration.get("preregistration_sha256")
        if isinstance(preregistration, dict)
        else None
    )
    if digest in REVOKED_COLLECTION_PREREGISTRATION_SHA256S:
        raise ValueError("collection preregistration is revoked")


def _preregistration_actor_public_keys(
    preregistration: dict[str, Any],
) -> set[str]:
    keys = {preregistration["signoff"]["signer_public_key"]}
    if preregistration["schema_version"] == 1:
        keys.add(preregistration["registration_authority_public_key"])
    return keys


def prepare_collection_preregistration(
    *,
    batch_id: str,
    collection_anchor_commit: str,
    collection_anchor_repository_root: str,
    observed_from: str,
    observed_through: str,
    provider_ledger_cutoff: str,
    pilot_session_ids: list[str],
    registration_authority_public_key: str,
) -> dict[str, Any]:
    """Prepare a prospective collection contract before confirmatory receipts exist."""
    sorted_pilot_session_ids = _validate_pilot_session_ids(pilot_session_ids)
    anchor_committed_at = _verified_collection_anchor(
        collection_anchor_repository_root, collection_anchor_commit
    )
    document = {
        "schema_version": 1,
        "status": "draft",
        "batch_id": batch_id,
        "collection_anchor_commit": collection_anchor_commit,
        "collection_anchor_committed_at": anchor_committed_at.isoformat(),
        "observation_window": {
            "observed_from": observed_from,
            "observed_through": observed_through,
        },
        "provider_ledger_cutoff": provider_ledger_cutoff,
        "sampling_policy": deepcopy(COLLECTION_SAMPLING_POLICY),
        "pilot_session_ids": sorted_pilot_session_ids,
        "registration_authority_public_key": (
            registration_authority_public_key
        ),
        "signoff": {
            "signer": "prospective-collection-preregistration-v1",
            "signer_public_key": "",
            "signature": "",
        },
        "preregistration_sha256": "",
    }
    document["preregistration_sha256"] = _signed_digest(
        document, "preregistration_sha256"
    )
    _validate_collection_preregistration_envelope(
        document, expected_status="draft"
    )
    return document


def prepare_sigstore_collection_preregistration(
    *,
    batch_id: str,
    collection_anchor_commit: str,
    collection_anchor_repository_root: str,
    observed_from: str,
    observed_through: str,
    provider_ledger_cutoff: str,
    pilot_session_ids: list[str],
    trusted_root: dict[str, Any],
    approved_trusted_root_sha256: str,
) -> dict[str, Any]:
    """Prepare a confirmatory preregistration bound to Sigstore TUF trust."""
    sorted_pilot_session_ids = _validate_pilot_session_ids(pilot_session_ids)
    anchor_committed_at = _verified_collection_anchor(
        collection_anchor_repository_root, collection_anchor_commit
    )
    document = {
        "schema_version": 2,
        "status": "draft",
        "batch_id": batch_id,
        "collection_anchor_commit": collection_anchor_commit,
        "collection_anchor_committed_at": anchor_committed_at.isoformat(),
        "observation_window": {
            "observed_from": observed_from,
            "observed_through": observed_through,
        },
        "provider_ledger_cutoff": provider_ledger_cutoff,
        "sampling_policy": deepcopy(COLLECTION_SAMPLING_POLICY),
        "pilot_session_ids": sorted_pilot_session_ids,
        "registration_authority": rfc3161.trust_identity(
            trusted_root,
            expected_trusted_root_sha256=approved_trusted_root_sha256,
        ),
        "signoff": {
            "signer": "prospective-collection-preregistration-v2",
            "signer_public_key": "",
            "signature": "",
        },
        "preregistration_sha256": "",
    }
    document["preregistration_sha256"] = _signed_digest(
        document, "preregistration_sha256"
    )
    _validate_collection_preregistration_envelope(
        document, expected_status="draft"
    )
    return document


def seal_collection_preregistration(
    preregistration: dict[str, Any],
    signer_private_key: Ed25519PrivateKey,
    *,
    collection_anchor_repository_root: str,
    expected_preregistration_sha256: str,
) -> dict[str, Any]:
    _validate_collection_preregistration_envelope(
        preregistration, expected_status="draft"
    )
    anchor_committed_at = _verified_collection_anchor(
        collection_anchor_repository_root,
        preregistration["collection_anchor_commit"],
    )
    if (
        preregistration["schema_version"] == 1
        and
        public_key_text(signer_private_key)
        == preregistration["registration_authority_public_key"]
    ):
        raise ValueError("registration authority must be independent")
    if (
        anchor_committed_at.isoformat()
        != preregistration["collection_anchor_committed_at"]
        or
        preregistration["preregistration_sha256"]
        != expected_preregistration_sha256
        or preregistration["preregistration_sha256"]
        != _signed_digest(preregistration, "preregistration_sha256")
    ):
        raise ValueError("collection preregistration draft digest mismatch")
    frozen = deepcopy(preregistration)
    frozen["status"] = "frozen"
    return _seal_document(
        frozen,
        private_key=signer_private_key,
        digest_field="preregistration_sha256",
    )


def validate_collection_preregistration(
    preregistration: dict[str, Any],
    *,
    trusted_preregistration_public_keys: set[str],
    expected_preregistration_sha256: str,
    approved_trusted_root_sha256: str | None = None,
) -> None:
    _reject_revoked_collection_preregistration(preregistration)
    _validate_collection_preregistration_envelope(
        preregistration, expected_status="frozen"
    )
    _verify_document(
        preregistration,
        digest_field="preregistration_sha256",
        trusted_public_keys=trusted_preregistration_public_keys,
        expected_digest=expected_preregistration_sha256,
        expected_signer=(
            "prospective-collection-preregistration-v1"
            if preregistration["schema_version"] == 1
            else "prospective-collection-preregistration-v2"
        ),
        label="collection preregistration",
    )
    if preregistration["schema_version"] == 2 and (
        not _is_lower_hex(approved_trusted_root_sha256, 64)
        or preregistration["registration_authority"]["trusted_root_sha256"]
        != approved_trusted_root_sha256
    ):
        raise ValueError("collection preregistration trusted root is not approved")
    if (
        preregistration["schema_version"] == 1
        and
        preregistration["signoff"]["signer_public_key"]
        == preregistration["registration_authority_public_key"]
    ):
        raise ValueError("registration authority must be independent")


def prepare_preregistration_registry_record(
    preregistration: dict[str, Any],
) -> dict[str, Any]:
    """Build the immutable record that must be committed before observation."""
    _validate_collection_preregistration_envelope(
        preregistration, expected_status="frozen"
    )
    return {
        "schema_version": 1,
        "batch_id": preregistration["batch_id"],
        "preregistration_sha256": preregistration["preregistration_sha256"],
    }


def validate_preregistration_registry_anchor(
    preregistration: dict[str, Any],
    *,
    repository_root: str,
    registry_commit: str,
    registry_path: str,
) -> None:
    """Prove that Git contains the exact signed preregistration record."""
    _validate_collection_preregistration_envelope(
        preregistration, expected_status="frozen"
    )
    root = Path(repository_root)
    if (
        not root.is_absolute()
        or not _is_lower_hex(registry_commit, 40)
        or not _relative_paths([registry_path])
    ):
        raise ValueError("preregistration registry anchor is invalid")
    _verified_collection_anchor(root, registry_commit)
    if _git_output(
        root,
        "merge-base",
        "--is-ancestor",
        preregistration["collection_anchor_commit"],
        registry_commit,
    ) is None:
        raise ValueError("preregistration registry ancestry is invalid")
    raw_record = _git_output(
        root, "show", f"{registry_commit}:{registry_path}", strip=False
    )
    try:
        record = json.loads(raw_record) if raw_record is not None else None
    except json.JSONDecodeError as error:
        raise ValueError("preregistration registry record is invalid") from error
    if (
        not isinstance(record, dict)
        or set(record) != PREREGISTRATION_REGISTRY_RECORD_FIELDS
        or record != prepare_preregistration_registry_record(preregistration)
    ):
        raise ValueError("preregistration registry record is invalid")


def _validate_preregistration_registration_receipt_envelope(
    receipt: dict[str, Any],
) -> None:
    if not isinstance(receipt, dict):
        raise ValueError("preregistration registration receipt is invalid")
    schema_version = receipt.get("schema_version")
    fields = (
        PREREGISTRATION_RECEIPT_FIELDS
        if schema_version == 1
        else SIGSTORE_PREREGISTRATION_RECEIPT_FIELDS
        if schema_version == 2
        else None
    )
    if (
        fields is None
        or set(receipt) != fields
        or receipt.get("status") != "registered"
        or not isinstance(receipt.get("batch_id"), str)
        or not receipt["batch_id"].strip()
        or not _is_lower_hex(receipt.get("preregistration_sha256"), 64)
        or not _is_lower_hex(receipt.get("registry_commit"), 40)
        or not _relative_paths([receipt.get("registry_path")])
        or not _is_lower_hex(receipt.get("receipt_sha256"), 64)
    ):
        raise ValueError("preregistration registration receipt is invalid")
    _parse_timestamp(receipt.get("registered_at"))


def _unsigned_document_digest(document: dict[str, Any], digest_field: str) -> str:
    payload = deepcopy(document)
    payload.pop(digest_field, None)
    return canonical_digest(payload)


def prepare_sigstore_registration_receipt(
    preregistration: dict[str, Any],
    *,
    registry_commit: str,
    registry_path: str,
    registration_evidence: dict[str, Any],
    trusted_root: dict[str, Any],
    approved_trusted_root_sha256: str,
) -> dict[str, Any]:
    _validate_collection_preregistration_envelope(
        preregistration, expected_status="frozen"
    )
    if preregistration["schema_version"] != 2:
        raise ValueError("Sigstore registration requires preregistration v2")
    if preregistration["registration_authority"]["trusted_root_sha256"] != (
        approved_trusted_root_sha256
    ):
        raise ValueError("Sigstore registration trusted root is not approved")
    if preregistration["registration_authority"] != rfc3161.trust_identity(
        trusted_root,
        expected_trusted_root_sha256=approved_trusted_root_sha256,
    ):
        raise ValueError("Sigstore registration authority mismatch")
    claim = rfc3161.registration_claim(
        batch_id=preregistration["batch_id"],
        preregistration_sha256=preregistration["preregistration_sha256"],
        registry_commit=registry_commit,
        registry_path=registry_path,
    )
    verified_evidence = rfc3161.verify_registration_evidence(
        registration_evidence,
        claim=claim,
        trusted_root=trusted_root,
        expected_trusted_root_sha256=approved_trusted_root_sha256,
        observation_starts_at=(
            preregistration["observation_window"]["observed_from"]
        ),
    )
    receipt = {
        "schema_version": 2,
        "status": "registered",
        "batch_id": preregistration["batch_id"],
        "preregistration_sha256": preregistration["preregistration_sha256"],
        "registry_commit": registry_commit,
        "registry_path": registry_path,
        "registered_at": verified_evidence["gen_time"],
        "registration_evidence": deepcopy(verified_evidence),
        "receipt_sha256": "",
    }
    receipt["receipt_sha256"] = _unsigned_document_digest(
        receipt, "receipt_sha256"
    )
    _validate_preregistration_registration_receipt_envelope(receipt)
    return receipt


def validate_preregistration_registration_receipt(
    receipt: dict[str, Any],
    *,
    preregistration: dict[str, Any],
    trusted_receipt_public_keys: set[str],
    expected_receipt_sha256: str,
    trusted_root: dict[str, Any] | None = None,
    approved_trusted_root_sha256: str | None = None,
) -> None:
    _reject_revoked_collection_preregistration(preregistration)
    _validate_preregistration_registration_receipt_envelope(receipt)
    if receipt["schema_version"] == 2:
        if preregistration.get("schema_version") != 2 or trusted_root is None:
            raise ValueError("Sigstore registration trust is required")
        if receipt["receipt_sha256"] != expected_receipt_sha256 or receipt[
            "receipt_sha256"
        ] != _unsigned_document_digest(receipt, "receipt_sha256"):
            raise ValueError("preregistration registration receipt digest mismatch")
        if preregistration["registration_authority"]["trusted_root_sha256"] != (
            approved_trusted_root_sha256
        ):
            raise ValueError("registration receipt trusted root is not approved")
        if preregistration["registration_authority"] != rfc3161.trust_identity(
            trusted_root,
            expected_trusted_root_sha256=approved_trusted_root_sha256,
        ):
            raise ValueError("registration receipt authority mismatch")
        claim = rfc3161.registration_claim(
            batch_id=receipt["batch_id"],
            preregistration_sha256=receipt["preregistration_sha256"],
            registry_commit=receipt["registry_commit"],
            registry_path=receipt["registry_path"],
        )
        evidence = rfc3161.verify_registration_evidence(
            receipt["registration_evidence"],
            claim=claim,
            trusted_root=trusted_root,
            expected_trusted_root_sha256=approved_trusted_root_sha256,
            observation_starts_at=(
                preregistration["observation_window"]["observed_from"]
            ),
        )
        if receipt["registered_at"] != evidence["gen_time"]:
            raise ValueError("preregistration registration receipt mismatch")
    else:
        _verify_document(
            receipt,
            digest_field="receipt_sha256",
            trusted_public_keys=trusted_receipt_public_keys,
            expected_digest=expected_receipt_sha256,
            expected_signer="independent-preregistration-timestamp-v1",
            label="preregistration registration receipt",
        )
        authority_public_key = (
            preregistration.get("registration_authority_public_key")
            if isinstance(preregistration, dict)
            else None
        )
        if receipt["signoff"]["signer_public_key"] != authority_public_key:
            raise ValueError("registration receipt authority mismatch")
    if (
        receipt["batch_id"] != preregistration["batch_id"]
        or receipt["preregistration_sha256"]
        != preregistration["preregistration_sha256"]
        or _parse_timestamp(receipt["registered_at"])
        >= _parse_timestamp(
            preregistration["observation_window"]["observed_from"]
        )
    ):
        raise ValueError("preregistration registration receipt mismatch")


def _validate_preregistered_sources(sources: Any) -> None:
    if not isinstance(sources, list) or not sources:
        raise ValueError("preregistered session sources are required")
    if any(
        not isinstance(source, dict)
        or set(source) != PREREGISTERED_SESSION_SOURCE_FIELDS
        or source.get("work_class") not in PREREGISTERED_WORK_CLASSES
        or not _valid_session_source({
            field: source.get(field) for field in SESSION_SOURCE_FIELDS
        })
        for source in sources
    ):
        raise ValueError("preregistered session source is invalid")
    source_ids = [source["source_record_id"] for source in sources]
    session_ids = [source["session_id"] for source in sources]
    if (
        len(source_ids) != len(set(source_ids))
        or len(session_ids) != len(set(session_ids))
    ):
        raise ValueError("preregistered session source identity must be unique")


def _completion_receipt_fields(receipt: Any) -> set[str] | None:
    if not isinstance(receipt, dict):
        return None
    if receipt.get("schema_version") == 1:
        return COMPLETION_RECEIPT_FIELDS
    if receipt.get("schema_version") == 2:
        return COMPLETION_RECEIPT_V2_FIELDS
    return None


def prepare_work_class_lock_receipt(
    session: dict[str, Any],
) -> dict[str, Any]:
    git = session.get("git") if isinstance(session, dict) else None
    if (
        not isinstance(session, dict)
        or not isinstance(session.get("session_id"), str)
        or not isinstance(session.get("request"), str)
        or session.get("work_class") not in PREREGISTERED_WORK_CLASSES
        or not isinstance(session.get("created_at"), str)
        or not isinstance(git, dict)
        or not _is_lower_hex(git.get("head"), 40)
    ):
        raise ValueError("work class lock source session is invalid")
    locked_at = _parse_timestamp(session["created_at"])
    signed_at = _current_time().replace(microsecond=0)
    if signed_at.tzinfo is None:
        raise ValueError("work class lock current time must be timezone-aware")
    signed_timestamp = signed_at
    if signed_timestamp < locked_at:
        raise ValueError("work class lock signature predates session")
    receipt = {
        "schema_version": 1,
        "status": "draft",
        "session_id": session["session_id"],
        "work_class": session["work_class"],
        "work_class_locked_at": session["created_at"],
        "signed_at": signed_at.isoformat(),
        "request_sha256": canonical_digest(session["request"]),
        "baseline_commit": git["head"],
        "signoff": {
            "signer": "independent-work-class-lock-v1",
            "signer_public_key": "",
            "signature": "",
        },
        "receipt_sha256": "",
    }
    receipt["receipt_sha256"] = _signed_digest(receipt, "receipt_sha256")
    return receipt


def _validate_work_class_lock_receipt_envelope(
    receipt: Any,
    *,
    expected_status: str,
) -> None:
    if (
        not isinstance(receipt, dict)
        or set(receipt) != WORK_CLASS_LOCK_RECEIPT_FIELDS
        or receipt.get("schema_version") != 1
        or receipt.get("status") != expected_status
        or not isinstance(receipt.get("session_id"), str)
        or not receipt["session_id"].strip()
        or receipt.get("work_class") not in PREREGISTERED_WORK_CLASSES
        or not _is_lower_hex(receipt.get("request_sha256"), 64)
        or not _is_lower_hex(receipt.get("baseline_commit"), 40)
        or not _is_lower_hex(receipt.get("receipt_sha256"), 64)
    ):
        raise ValueError("work class lock receipt is invalid")
    _parse_timestamp(receipt.get("work_class_locked_at"))
    signed_at = _parse_timestamp(receipt.get("signed_at"))
    if signed_at < _parse_timestamp(receipt["work_class_locked_at"]):
        raise ValueError("work class lock receipt is invalid")


def seal_work_class_lock_receipt(
    receipt: dict[str, Any],
    signer_private_key: Ed25519PrivateKey,
    *,
    expected_receipt_sha256: str,
) -> dict[str, Any]:
    _validate_work_class_lock_receipt_envelope(
        receipt,
        expected_status="draft",
    )
    if (
        receipt.get("receipt_sha256") != expected_receipt_sha256
        or receipt["receipt_sha256"]
        != _signed_digest(receipt, "receipt_sha256")
        or receipt.get("signoff") != {
            "signer": "independent-work-class-lock-v1",
            "signer_public_key": "",
            "signature": "",
        }
    ):
        raise ValueError("work class lock receipt draft is invalid")
    sealed = deepcopy(receipt)
    sealed["status"] = "frozen"
    signed_at = _current_time().replace(microsecond=0)
    if signed_at.tzinfo is None:
        raise ValueError("work class lock current time must be timezone-aware")
    sealed["signed_at"] = signed_at.isoformat()
    if signed_at < _parse_timestamp(sealed["work_class_locked_at"]):
        raise ValueError("work class lock signature predates session")
    return _seal_document(
        sealed,
        private_key=signer_private_key,
        digest_field="receipt_sha256",
    )


def load_work_class_lock_private_key(
    private_key_path: str | Path,
    *,
    project_root: str | Path,
    trusted_public_key: str,
) -> Ed25519PrivateKey:
    """Load a repository-external key and bind it to the pinned signer."""
    key_path = Path(private_key_path).expanduser().resolve()
    repository = Path(project_root).resolve()
    try:
        key_path.relative_to(repository)
    except ValueError:
        pass
    else:
        raise ValueError(
            "work class lock private key must be outside the repository"
        )
    private_key = _read_private_key(str(key_path))
    if public_key_text(private_key) != trusted_public_key.strip():
        raise ValueError(
            "work class lock private key does not match trusted public key"
        )
    return private_key


def _resolved_work_class_lock_session(
    project_root: str | Path,
    session: dict[str, Any],
) -> dict[str, Any]:
    resolved = deepcopy(session)
    git = resolved.get("git") if isinstance(resolved, dict) else None
    recorded_head = git.get("head") if isinstance(git, dict) else None
    if not _is_git_commit_prefix(recorded_head):
        raise ValueError("work class lock baseline commit is invalid")
    result = subprocess.run(
        ["git", "-C", str(project_root), "rev-parse", "HEAD^{commit}"],
        check=False,
        capture_output=True,
        text=True,
    )
    full_head = result.stdout.strip()
    if (
        result.returncode != 0
        or not _is_lower_hex(full_head, 40)
        or not full_head.startswith(recorded_head)
    ):
        raise ValueError("work class lock baseline commit is invalid")
    resolved["git"] = {**git, "head": full_head}
    return resolved


def seal_session_work_class_lock(
    project_root: str | Path,
    session: dict[str, Any],
    signer_private_key: Ed25519PrivateKey,
) -> dict[str, Any]:
    """Seal a session's work class before prospective work can continue."""
    lock_session = _resolved_work_class_lock_session(project_root, session)
    draft = prepare_work_class_lock_receipt(lock_session)
    sealed = seal_work_class_lock_receipt(
        draft,
        signer_private_key,
        expected_receipt_sha256=draft["receipt_sha256"],
    )
    destination = (
        Path(project_root)
        / ".omc"
        / "state"
        / "sessions"
        / session["session_id"]
        / "work_class_lock.json"
    )
    if destination.exists():
        raise ValueError("work class lock receipt already exists")
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_suffix(".json.tmp")
    temporary.write_text(
        json.dumps(sealed, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    temporary.replace(destination)
    return sealed


def _validated_work_class_lock(
    source: dict[str, Any],
    *,
    trusted_work_class_lock_public_keys: set[str],
) -> tuple[str, dict[str, str]]:
    session_dir = (
        Path(source["repository_root"])
        / ".omc"
        / "state"
        / "sessions"
        / source["session_id"]
    )
    try:
        receipt = json.loads(
            (session_dir / "completion.json").read_text(encoding="utf-8")
        )
        session = json.loads(
            (session_dir / "session.json").read_text(encoding="utf-8")
        )
        lock_receipt = json.loads(
            (session_dir / "work_class_lock.json").read_text(encoding="utf-8")
        )
    except (OSError, json.JSONDecodeError) as error:
        raise ValueError("source work class is not locked") from error
    locked_at = receipt.get("work_class_locked_at")
    try:
        _validate_work_class_lock_receipt_envelope(
            lock_receipt,
            expected_status="frozen",
        )
    except ValueError as error:
        raise ValueError("source work class is not locked") from error
    session_git = session.get("git") if isinstance(session, dict) else None
    session_head = session_git.get("head") if isinstance(session_git, dict) else None
    baseline_commit = receipt.get("baseline_commit")
    _verify_document(
        lock_receipt,
        digest_field="receipt_sha256",
        trusted_public_keys=trusted_work_class_lock_public_keys,
        expected_digest=lock_receipt.get("receipt_sha256"),
        expected_signer="independent-work-class-lock-v1",
        label="work class lock receipt",
    )
    if (
        _completion_receipt_fields(receipt) != COMPLETION_RECEIPT_V2_FIELDS
        or set(receipt) != COMPLETION_RECEIPT_V2_FIELDS
        or receipt.get("session_id") != source["session_id"]
        or receipt.get("work_class") not in PREREGISTERED_WORK_CLASSES
        or not isinstance(session, dict)
        or session.get("session_id") != source["session_id"]
        or session.get("work_class") != receipt.get("work_class")
        or session.get("created_at") != locked_at
        or not isinstance(session.get("request"), str)
        or canonical_digest(session["request"]) != receipt.get("request_sha256")
        or not _is_git_commit_prefix(session_head)
        or not _is_lower_hex(baseline_commit, 40)
        or not baseline_commit.startswith(session_head)
        or lock_receipt.get("session_id") != source["session_id"]
        or lock_receipt.get("work_class") != receipt.get("work_class")
        or lock_receipt.get("work_class_locked_at") != locked_at
        or lock_receipt.get("request_sha256") != receipt.get("request_sha256")
        or lock_receipt.get("baseline_commit")
        != receipt.get("baseline_commit")
    ):
        raise ValueError("source work class is not locked")
    try:
        signed_at = _parse_timestamp(lock_receipt["signed_at"])
        if (
            _parse_timestamp(locked_at) > signed_at
            or signed_at > _parse_timestamp(receipt["completed_at"])
        ):
            raise ValueError("source work class is not locked")
    except (TypeError, ValueError) as error:
        raise ValueError("source work class is not locked") from error
    return receipt["work_class"], {
        "session_id": source["session_id"],
        "receipt_sha256": lock_receipt["receipt_sha256"],
        "signer_public_key": lock_receipt["signoff"]["signer_public_key"],
        "signed_at": lock_receipt["signed_at"],
    }


def _locked_completion_work_class(
    source: dict[str, Any],
    *,
    trusted_work_class_lock_public_keys: set[str],
) -> str:
    return _validated_work_class_lock(
        source,
        trusted_work_class_lock_public_keys=(
            trusted_work_class_lock_public_keys
        ),
    )[0]


def _validate_work_class_lock_independence(
    evidence: list[dict[str, str]],
    *,
    preregistration_actor_public_keys: set[str],
    registration_receipt_public_key: str,
) -> None:
    forbidden_keys = {
        *preregistration_actor_public_keys,
        registration_receipt_public_key,
    }
    if any(item["signer_public_key"] in forbidden_keys for item in evidence):
        raise ValueError("work class lock signer must be independent")


def _work_class_lock_signer_keys(
    source_snapshot: dict[str, Any],
) -> set[str]:
    if source_snapshot.get("schema_version") != 3:
        return set()
    return {
        lock["signer_public_key"]
        for lock in source_snapshot["work_class_locks"]
    }


def _current_time() -> datetime:
    return datetime.now().astimezone()


def _validate_preregistered_source_snapshot_envelope(
    source_snapshot: dict[str, Any],
    *,
    expected_status: str,
    validate_payload: bool = True,
) -> None:
    schema_version = source_snapshot.get("schema_version") if isinstance(
        source_snapshot, dict
    ) else None
    expected_fields = (
        PREREGISTERED_SOURCE_MANIFEST_FIELDS
        if schema_version == 2
        else PREREGISTERED_SOURCE_MANIFEST_V3_FIELDS
        if schema_version == 3
        else None
    )
    if (
        not isinstance(source_snapshot, dict)
        or expected_fields is None
        or set(source_snapshot) != expected_fields
        or source_snapshot.get("status") != expected_status
        or not _is_lower_hex(
            source_snapshot.get("preregistration_sha256"), 64
        )
        or not _is_lower_hex(
            source_snapshot.get("source_snapshot_sha256"), 64
        )
        or not _is_lower_hex(
            source_snapshot.get("registration_receipt_sha256"), 64
        )
        or source_snapshot.get("completeness_attestation")
        != SOURCE_SNAPSHOT_COMPLETENESS_ATTESTATION
    ):
        raise ValueError("preregistered source snapshot is invalid")
    if not validate_payload:
        return
    if schema_version == 3:
        locks = source_snapshot.get("work_class_locks")
        source_session_ids = {
            source["session_id"] for source in source_snapshot.get("sources", [])
            if isinstance(source, dict) and "session_id" in source
        }
        if (
            not isinstance(locks, list)
            or len(locks) != len(source_snapshot.get("sources", []))
            or any(
                not isinstance(lock, dict)
                or set(lock) != WORK_CLASS_LOCK_EVIDENCE_FIELDS
                or not _is_lower_hex(lock.get("receipt_sha256"), 64)
                or not _valid_public_key_text(lock.get("signer_public_key"))
                for lock in locks
            )
            or {lock["session_id"] for lock in locks} != source_session_ids
        ):
            raise ValueError("source snapshot work class locks are invalid")
        for lock in locks:
            _parse_timestamp(lock.get("signed_at"))
    registry = source_snapshot.get("preregistration_registry")
    if (
        not isinstance(registry, dict)
        or set(registry) != PREREGISTRATION_REGISTRY_ANCHOR_FIELDS
        or not _is_lower_hex(registry.get("commit"), 40)
        or not _relative_paths([registry.get("path")])
    ):
        raise ValueError("preregistration source registry is invalid")
    _validate_preregistered_sources(source_snapshot.get("sources"))


def prepare_preregistered_source_snapshot(
    *,
    sources: list[dict[str, Any]],
    preregistration: dict[str, Any],
    trusted_preregistration_public_keys: set[str],
    expected_preregistration_sha256: str,
    preregistration_registry_repository_root: str,
    preregistration_registry_commit: str,
    preregistration_registry_path: str,
    registration_receipt: dict[str, Any],
    trusted_registration_receipt_public_keys: set[str],
    expected_registration_receipt_sha256: str,
    trusted_work_class_lock_public_keys: set[str],
    trusted_registration_root: dict[str, Any] | None = None,
    approved_trusted_registration_root_sha256: str | None = None,
) -> dict[str, Any]:
    """Prepare the complete session-ledger snapshot for independent signoff."""
    validate_collection_preregistration(
        preregistration,
        trusted_preregistration_public_keys=(
            trusted_preregistration_public_keys
        ),
        expected_preregistration_sha256=expected_preregistration_sha256,
        approved_trusted_root_sha256=(
            approved_trusted_registration_root_sha256
        ),
    )
    validate_preregistration_registry_anchor(
        preregistration,
        repository_root=preregistration_registry_repository_root,
        registry_commit=preregistration_registry_commit,
        registry_path=preregistration_registry_path,
    )
    validate_preregistration_registration_receipt(
        registration_receipt,
        preregistration=preregistration,
        trusted_receipt_public_keys=(
            trusted_registration_receipt_public_keys
        ),
        expected_receipt_sha256=expected_registration_receipt_sha256,
        trusted_root=trusted_registration_root,
        approved_trusted_root_sha256=(
            approved_trusted_registration_root_sha256
        ),
    )
    if (
        registration_receipt["registry_commit"]
        != preregistration_registry_commit
        or registration_receipt["registry_path"]
        != preregistration_registry_path
    ):
        raise ValueError("registration receipt registry anchor mismatch")
    observed_through = _parse_timestamp(
        preregistration["observation_window"]["observed_through"]
    )
    now = _current_time()
    if now.tzinfo is None:
        raise ValueError("source snapshot current time must be timezone-aware")
    if now <= observed_through:
        raise ValueError("observation window has not closed")
    _validate_preregistered_sources(sources)
    work_class_locks: list[dict[str, str]] = []
    for source in sources:
        locked_work_class, lock_evidence = _validated_work_class_lock(
            source,
            trusted_work_class_lock_public_keys=(
                trusted_work_class_lock_public_keys
            ),
        )
        if locked_work_class != source["work_class"]:
            raise ValueError("source work class does not match locked completion")
        work_class_locks.append(lock_evidence)
    _validate_work_class_lock_independence(
        work_class_locks,
        preregistration_actor_public_keys=(
            _preregistration_actor_public_keys(preregistration)
        ),
        registration_receipt_public_key=(
            registration_receipt["signoff"]["signer_public_key"]
        ),
    )
    document = {
        "schema_version": 3,
        "status": "draft",
        "preregistration_sha256": expected_preregistration_sha256,
        "preregistration_registry": {
            "commit": preregistration_registry_commit,
            "path": preregistration_registry_path,
        },
        "registration_receipt_sha256": (
            expected_registration_receipt_sha256
        ),
        "completeness_attestation": deepcopy(
            SOURCE_SNAPSHOT_COMPLETENESS_ATTESTATION
        ),
        "sources": sorted(
            deepcopy(sources), key=lambda source: source["source_record_id"]
        ),
        "work_class_locks": sorted(
            deepcopy(work_class_locks), key=lambda lock: lock["session_id"]
        ),
        "signoff": {
            "signer": "complete-session-ledger-snapshot-v1",
            "signer_public_key": "",
            "signature": "",
        },
        "source_snapshot_sha256": "",
    }
    document["source_snapshot_sha256"] = _signed_digest(
        document, "source_snapshot_sha256"
    )
    _validate_preregistered_source_snapshot_envelope(
        document, expected_status="draft"
    )
    return document


def seal_preregistered_source_snapshot(
    source_snapshot: dict[str, Any],
    signer_private_key: Ed25519PrivateKey,
    *,
    expected_source_snapshot_sha256: str,
) -> dict[str, Any]:
    _validate_preregistered_source_snapshot_envelope(
        source_snapshot, expected_status="draft"
    )
    if (
        source_snapshot["source_snapshot_sha256"]
        != expected_source_snapshot_sha256
        or source_snapshot["source_snapshot_sha256"]
        != _signed_digest(source_snapshot, "source_snapshot_sha256")
    ):
        raise ValueError("source snapshot draft digest mismatch")
    frozen = deepcopy(source_snapshot)
    frozen["status"] = "frozen"
    return _seal_document(
        frozen,
        private_key=signer_private_key,
        digest_field="source_snapshot_sha256",
    )


def validate_preregistered_source_snapshot(
    source_snapshot: dict[str, Any],
    *,
    trusted_source_snapshot_public_keys: set[str],
    expected_source_snapshot_sha256: str,
    expected_preregistration_sha256: str,
) -> None:
    _validate_preregistered_source_snapshot_envelope(
        source_snapshot,
        expected_status="frozen",
        validate_payload=False,
    )
    _verify_document(
        source_snapshot,
        digest_field="source_snapshot_sha256",
        trusted_public_keys=trusted_source_snapshot_public_keys,
        expected_digest=expected_source_snapshot_sha256,
        expected_signer="complete-session-ledger-snapshot-v1",
        label="source snapshot",
    )
    _validate_preregistered_source_snapshot_envelope(
        source_snapshot, expected_status="frozen"
    )
    if (
        source_snapshot["preregistration_sha256"]
        != expected_preregistration_sha256
    ):
        raise ValueError("source snapshot preregistration mismatch")


def validate_preregistered_provenance_chain(
    *,
    preregistration: dict[str, Any],
    trusted_preregistration_public_keys: set[str],
    expected_preregistration_sha256: str,
    source_snapshot: dict[str, Any],
    trusted_source_snapshot_public_keys: set[str],
    expected_source_snapshot_sha256: str,
    preregistration_registry_repository_root: str,
    registration_receipt: dict[str, Any],
    trusted_registration_receipt_public_keys: set[str],
    expected_registration_receipt_sha256: str,
    trusted_registration_root: dict[str, Any] | None = None,
    approved_trusted_registration_root_sha256: str | None = None,
) -> None:
    if source_snapshot.get("schema_version") != 3:
        raise ValueError("source snapshot schema v3 is required")
    validate_collection_preregistration(
        preregistration,
        trusted_preregistration_public_keys=trusted_preregistration_public_keys,
        expected_preregistration_sha256=expected_preregistration_sha256,
        approved_trusted_root_sha256=(
            approved_trusted_registration_root_sha256
        ),
    )
    validate_preregistered_source_snapshot(
        source_snapshot,
        trusted_source_snapshot_public_keys=(
            trusted_source_snapshot_public_keys
        ),
        expected_source_snapshot_sha256=expected_source_snapshot_sha256,
        expected_preregistration_sha256=expected_preregistration_sha256,
    )
    preregistration_actor_keys = _preregistration_actor_public_keys(
        preregistration
    )
    if source_snapshot["signoff"]["signer_public_key"] in (
        preregistration_actor_keys
        | _work_class_lock_signer_keys(source_snapshot)
    ):
        raise ValueError("source snapshot signer must be independent")
    registry = source_snapshot["preregistration_registry"]
    validate_preregistration_registry_anchor(
        preregistration,
        repository_root=preregistration_registry_repository_root,
        registry_commit=registry["commit"],
        registry_path=registry["path"],
    )
    validate_preregistration_registration_receipt(
        registration_receipt,
        preregistration=preregistration,
        trusted_receipt_public_keys=(
            trusted_registration_receipt_public_keys
        ),
        expected_receipt_sha256=expected_registration_receipt_sha256,
        trusted_root=trusted_registration_root,
        approved_trusted_root_sha256=(
            approved_trusted_registration_root_sha256
        ),
    )
    if (
        source_snapshot["registration_receipt_sha256"]
        != expected_registration_receipt_sha256
        or registration_receipt["registry_commit"] != registry["commit"]
        or registration_receipt["registry_path"] != registry["path"]
    ):
        raise ValueError("registration receipt source snapshot mismatch")


def classify_preregistered_sessions(
    preregistration: dict[str, Any],
    *,
    sessions: list[dict[str, Any]],
    trusted_preregistration_public_keys: set[str],
    expected_preregistration_sha256: str,
    approved_trusted_root_sha256: str | None = None,
) -> list[dict[str, str]]:
    validate_collection_preregistration(
        preregistration,
        trusted_preregistration_public_keys=trusted_preregistration_public_keys,
        expected_preregistration_sha256=expected_preregistration_sha256,
        approved_trusted_root_sha256=approved_trusted_root_sha256,
    )
    window = preregistration["observation_window"]
    start = _parse_timestamp(window["observed_from"])
    end = _parse_timestamp(window["observed_through"])
    if not isinstance(sessions, list):
        raise ValueError("preregistered sessions are invalid")
    work_class_exclusions = {
        "synthetic": "synthetic_task",
        "document_only": "document_only_task",
        "benchmark_maintenance": "benchmark_maintenance_task",
    }
    allowed_work_classes = {"implementation", *work_class_exclusions}
    pilot_ids = set(preregistration["pilot_session_ids"])
    classifications: list[dict[str, str]] = []
    eligible: list[tuple[datetime, str, int]] = []
    seen_session_ids: set[str] = set()
    for session in sessions:
        if (
            not isinstance(session, dict)
            or set(session) != {"session_id", "completed_at", "work_class"}
            or not isinstance(session.get("session_id"), str)
            or not session["session_id"].strip()
            or session["session_id"] in seen_session_ids
            or session.get("work_class") not in allowed_work_classes
        ):
            raise ValueError("preregistered sessions are invalid")
        session_id = session["session_id"]
        seen_session_ids.add(session_id)
        completed_at = _parse_timestamp(session["completed_at"])
        if session_id in pilot_ids:
            disposition = "pilot_observed"
        elif not start <= completed_at <= end:
            disposition = "outside_window"
        elif session["work_class"] in work_class_exclusions:
            disposition = work_class_exclusions[session["work_class"]]
        else:
            disposition = "pending_first_n"
            eligible.append((completed_at, session_id, len(classifications)))
        classifications.append({
            "session_id": session_id,
            "disposition": disposition,
        })

    maximum = preregistration["sampling_policy"]["maximum_accepted_receipts"]
    eligible.sort(key=lambda item: (item[0], item[1]))
    for rank, (_, _, index) in enumerate(eligible, start=1):
        classifications[index]["disposition"] = (
            "confirmatory_candidate"
            if rank <= maximum
            else "collection_limit_exceeded"
        )
    return classifications


def _validate_inventory_envelope(
    inventory: dict[str, Any],
    *,
    expected_status: str,
) -> None:
    base_fields = {
        "schema_version",
        "status",
        "observation_window",
        "provider_ledger_cutoff",
        "accepted_records",
        "rejected_records",
        "audit",
        "signoff",
        "inventory_sha256",
    }
    schema_version = inventory.get("schema_version") if isinstance(
        inventory, dict
    ) else None
    expected_fields = (
        base_fields
        if schema_version == 1
        else base_fields | {
            "preregistration_sha256",
            "source_snapshot_sha256",
            "collector_public_key",
        }
    )
    if (
        not isinstance(inventory, dict)
        or schema_version not in {1, 2}
        or set(inventory) != expected_fields
    ):
        raise ValueError("private inventory fields are invalid")
    window = inventory.get("observation_window")
    audit = inventory.get("audit")
    accepted = inventory.get("accepted_records")
    rejected = inventory.get("rejected_records")
    if (
        inventory.get("status") != expected_status
        or not isinstance(window, dict)
        or set(window) != {"observed_from", "observed_through"}
        or not isinstance(accepted, list)
        or not isinstance(rejected, list)
        or not isinstance(audit, dict)
        or set(audit) != {
            "discovered_count",
            "accepted_count",
            "rejected_count",
            "rejected_reason_counts",
        }
    ):
        raise ValueError("private inventory provenance is invalid")
    if schema_version == 2 and (
        not _is_lower_hex(inventory.get("preregistration_sha256"), 64)
        or not _is_lower_hex(inventory.get("source_snapshot_sha256"), 64)
        or not _valid_public_key_text(inventory.get("collector_public_key"))
    ):
        raise ValueError("private inventory provenance is invalid")
    try:
        start = datetime.fromisoformat(
            f"{window['observed_from']}T00:00:00+00:00"
        ).date()
        end = datetime.fromisoformat(
            f"{window['observed_through']}T00:00:00+00:00"
        ).date()
        cutoff = _parse_timestamp(inventory["provider_ledger_cutoff"])
    except (KeyError, TypeError, ValueError) as error:
        raise ValueError("private inventory provenance is invalid") from error
    if start > end or any(
        not _valid_source_record(record, cutoff=cutoff) for record in accepted
    ):
        raise ValueError("private inventory provenance is invalid")
    if any(
        not isinstance(record, dict)
        or set(record) != {"source_record_id", "source_record_sha256", "reason"}
        or not isinstance(record["source_record_id"], str)
        or not _is_lower_hex(record["source_record_sha256"], 64)
        or record["reason"] not in (
            {"invalid_provenance"}
            if schema_version == 1
            else INVENTORY_REJECTION_REASONS
        )
        for record in rejected
    ):
        raise ValueError("private inventory audit is invalid")
    accepted_ids = [record["source_record_id"] for record in accepted]
    session_ids = [record["session_id"] for record in accepted]
    counts = (
        audit["discovered_count"],
        audit["accepted_count"],
        audit["rejected_count"],
    )
    if (
        len(accepted_ids) != len(set(accepted_ids))
        or len(session_ids) != len(set(session_ids))
        or any(type(value) is not int or value < 0 for value in counts)
        or counts[0] != counts[1] + counts[2]
        or counts[1] != len(accepted)
        or counts[2] != len(rejected)
        or audit["rejected_reason_counts"]
        != dict(sorted(Counter(
            record["reason"] for record in rejected
        ).items()))
    ):
        raise ValueError("private inventory audit is invalid")


def _seal_validated_private_inventory(
    inventory: dict[str, Any],
    collector_private_key: Ed25519PrivateKey,
    *,
    expected_inventory_sha256: str,
) -> dict[str, Any]:
    if (
        inventory.get("inventory_sha256") != expected_inventory_sha256
        or inventory["inventory_sha256"] != _inventory_digest(inventory)
    ):
        raise ValueError("private inventory draft digest mismatch")
    approved = deepcopy(inventory)
    approved["status"] = "approved"
    return _seal_document(
        approved,
        private_key=collector_private_key,
        digest_field="inventory_sha256",
    )


def seal_private_inventory(
    inventory: dict[str, Any],
    collector_private_key: Ed25519PrivateKey,
    *,
    expected_inventory_sha256: str,
) -> dict[str, Any]:
    _validate_inventory_envelope(inventory, expected_status="collected")
    if inventory["schema_version"] != 1:
        raise ValueError("preregistered inventory requires source snapshot")
    return _seal_validated_private_inventory(
        inventory,
        collector_private_key,
        expected_inventory_sha256=expected_inventory_sha256,
    )


def seal_preregistered_inventory(
    inventory: dict[str, Any],
    collector_private_key: Ed25519PrivateKey,
    *,
    source_snapshot: dict[str, Any],
    trusted_source_snapshot_public_keys: set[str],
    expected_source_snapshot_sha256: str,
    expected_preregistration_sha256: str,
    expected_inventory_sha256: str,
) -> dict[str, Any]:
    _validate_inventory_envelope(inventory, expected_status="collected")
    if inventory["schema_version"] != 2:
        raise ValueError("preregistered inventory schema is required")
    if source_snapshot.get("schema_version") != 3:
        raise ValueError("source snapshot schema v3 is required")
    validate_preregistered_source_snapshot(
        source_snapshot,
        trusted_source_snapshot_public_keys=(
            trusted_source_snapshot_public_keys
        ),
        expected_source_snapshot_sha256=expected_source_snapshot_sha256,
        expected_preregistration_sha256=expected_preregistration_sha256,
    )
    if (
        inventory["source_snapshot_sha256"]
        != expected_source_snapshot_sha256
        or inventory["preregistration_sha256"]
        != expected_preregistration_sha256
    ):
        raise ValueError("preregistered inventory source snapshot mismatch")
    if public_key_text(collector_private_key) in (
        {source_snapshot["signoff"]["signer_public_key"]}
        | _work_class_lock_signer_keys(source_snapshot)
    ):
        raise ValueError("inventory collector must be independent")
    if public_key_text(collector_private_key) != inventory["collector_public_key"]:
        raise ValueError("inventory collector identity mismatch")
    return _seal_validated_private_inventory(
        inventory,
        collector_private_key,
        expected_inventory_sha256=expected_inventory_sha256,
    )


def _verify_private_inventory(
    inventory: dict[str, Any],
    *,
    trusted_collector_public_keys: set[str],
    expected_inventory_sha256: str,
    source_snapshot: dict[str, Any] | None = None,
    trusted_source_snapshot_public_keys: set[str] | None = None,
    expected_source_snapshot_sha256: str | None = None,
    expected_preregistration_sha256: str | None = None,
    preregistration: dict[str, Any] | None = None,
    trusted_preregistration_public_keys: set[str] | None = None,
    preregistration_registry_repository_root: str | None = None,
    registration_receipt: dict[str, Any] | None = None,
    trusted_registration_receipt_public_keys: set[str] | None = None,
    expected_registration_receipt_sha256: str | None = None,
    trusted_registration_root: dict[str, Any] | None = None,
    approved_trusted_registration_root_sha256: str | None = None,
) -> None:
    try:
        _validate_inventory_envelope(inventory, expected_status="approved")
    except ValueError as error:
        raise ValueError("collector inventory is invalid") from error
    _verify_document(
        inventory,
        digest_field="inventory_sha256",
        trusted_public_keys=trusted_collector_public_keys,
        expected_digest=expected_inventory_sha256,
        expected_signer="observed-inventory-collector-v1",
        label="collector inventory",
    )
    if inventory["schema_version"] == 1:
        return
    if (
        source_snapshot is None
        or not trusted_source_snapshot_public_keys
        or expected_source_snapshot_sha256 is None
        or expected_preregistration_sha256 is None
    ):
        raise ValueError("preregistered inventory source snapshot evidence is required")
    if (
        preregistration is None
        or not trusted_preregistration_public_keys
        or preregistration_registry_repository_root is None
        or registration_receipt is None
        or expected_registration_receipt_sha256 is None
    ):
        raise ValueError("preregistered inventory complete provenance evidence is required")
    if preregistration["schema_version"] == 1:
        if not trusted_registration_receipt_public_keys:
            raise ValueError(
                "preregistered inventory complete provenance evidence is required"
            )
    elif trusted_registration_root is None:
        raise ValueError("Sigstore registration trust is required")
    validate_preregistered_provenance_chain(
        preregistration=preregistration,
        trusted_preregistration_public_keys=(
            trusted_preregistration_public_keys
        ),
        expected_preregistration_sha256=expected_preregistration_sha256,
        source_snapshot=source_snapshot,
        trusted_source_snapshot_public_keys=trusted_source_snapshot_public_keys,
        expected_source_snapshot_sha256=expected_source_snapshot_sha256,
        preregistration_registry_repository_root=(
            preregistration_registry_repository_root
        ),
        registration_receipt=registration_receipt,
        trusted_registration_receipt_public_keys=(
            trusted_registration_receipt_public_keys
        ),
        expected_registration_receipt_sha256=(
            expected_registration_receipt_sha256
        ),
        trusted_registration_root=trusted_registration_root,
        approved_trusted_registration_root_sha256=(
            approved_trusted_registration_root_sha256
        ),
    )
    if (
        inventory["source_snapshot_sha256"]
        != expected_source_snapshot_sha256
        or inventory["preregistration_sha256"]
        != expected_preregistration_sha256
    ):
        raise ValueError("preregistered inventory source snapshot mismatch")
    collector_public_key = inventory["signoff"]["signer_public_key"]
    if collector_public_key != inventory["collector_public_key"]:
        raise ValueError("inventory collector identity mismatch")
    disallowed_collector_keys = (
        _preregistration_actor_public_keys(preregistration)
        | {source_snapshot["signoff"]["signer_public_key"]}
        | _work_class_lock_signer_keys(source_snapshot)
    )
    if collector_public_key in disallowed_collector_keys:
        raise ValueError("inventory collector must be independent")


def sign_label_receipt(
    inventory: dict[str, Any],
    signer_private_key: Ed25519PrivateKey,
    *,
    trusted_collector_public_keys: set[str],
    expected_inventory_sha256: str,
    source_snapshot: dict[str, Any] | None = None,
    trusted_source_snapshot_public_keys: set[str] | None = None,
    expected_source_snapshot_sha256: str | None = None,
    expected_preregistration_sha256: str | None = None,
    preregistration: dict[str, Any] | None = None,
    trusted_preregistration_public_keys: set[str] | None = None,
    preregistration_registry_repository_root: str | None = None,
    registration_receipt: dict[str, Any] | None = None,
    trusted_registration_receipt_public_keys: set[str] | None = None,
    expected_registration_receipt_sha256: str | None = None,
    trusted_registration_root: dict[str, Any] | None = None,
    approved_trusted_registration_root_sha256: str | None = None,
) -> dict[str, Any]:
    _verify_private_inventory(
        inventory,
        trusted_collector_public_keys=trusted_collector_public_keys,
        expected_inventory_sha256=expected_inventory_sha256,
        source_snapshot=source_snapshot,
        trusted_source_snapshot_public_keys=trusted_source_snapshot_public_keys,
        expected_source_snapshot_sha256=expected_source_snapshot_sha256,
        expected_preregistration_sha256=expected_preregistration_sha256,
        preregistration=preregistration,
        trusted_preregistration_public_keys=trusted_preregistration_public_keys,
        preregistration_registry_repository_root=(
            preregistration_registry_repository_root
        ),
        registration_receipt=registration_receipt,
        trusted_registration_receipt_public_keys=(
            trusted_registration_receipt_public_keys
        ),
        expected_registration_receipt_sha256=(
            expected_registration_receipt_sha256
        ),
        trusted_registration_root=trusted_registration_root,
        approved_trusted_registration_root_sha256=(
            approved_trusted_registration_root_sha256
        ),
    )
    labels = [{
        "source_record_id": record["source_record_id"],
        "surface": record["surface"],
        "ambiguity": record["ambiguity"],
        "selected_object": record["selected_object"],
    } for record in inventory["accepted_records"]]
    document = {
        "schema_version": 1,
        "status": "approved",
        "private_inventory_sha256": inventory["inventory_sha256"],
        "rubric_version": "plan-confirmatory-label-rubric-v1",
        "provider_outputs_available": False,
        "labels": labels,
        "signoff": {
            "signer": "independent-label-reviewer-v1",
            "signer_public_key": "",
            "signature": "",
        },
        "receipt_sha256": "",
    }
    return _seal_document(
        document,
        private_key=signer_private_key,
        digest_field="receipt_sha256",
    )


def _validate_label_receipt(
    receipt: dict[str, Any],
    *,
    trusted_label_public_keys: set[str],
    expected_label_receipt_sha256: str,
) -> None:
    expected_fields = {
        "schema_version",
        "status",
        "private_inventory_sha256",
        "rubric_version",
        "provider_outputs_available",
        "labels",
        "signoff",
        "receipt_sha256",
    }
    labels = receipt.get("labels") if isinstance(receipt, dict) else None
    if (
        not isinstance(receipt, dict)
        or set(receipt) != expected_fields
        or receipt.get("schema_version") != 1
        or receipt.get("status") != "approved"
        or not _is_lower_hex(receipt.get("private_inventory_sha256"), 64)
        or receipt.get("rubric_version") != "plan-confirmatory-label-rubric-v1"
        or receipt.get("provider_outputs_available") is not False
        or not isinstance(labels, list)
        or any(
            not isinstance(label, dict)
            or set(label) != {
                "source_record_id",
                "surface",
                "ambiguity",
                "selected_object",
            }
            or not isinstance(label["source_record_id"], str)
            or not label["source_record_id"].strip()
            or label["surface"] not in runtime.FROZEN_CONFIRMATORY_SURFACE_COUNTS
            or label["ambiguity"]
            not in runtime.FROZEN_CONFIRMATORY_AMBIGUITY_COUNTS
            or type(label["selected_object"]) is not bool
            for label in labels or []
        )
    ):
        raise ValueError("label receipt fields are invalid")
    _verify_document(
        receipt,
        digest_field="receipt_sha256",
        trusted_public_keys=trusted_label_public_keys,
        expected_digest=expected_label_receipt_sha256,
        expected_signer="independent-label-reviewer-v1",
        label="label receipt",
    )


def sign_prior_commit_snapshot(
    prior_commits: list[str],
    *,
    anchor_registry_sha256: str,
    signer_private_key: Ed25519PrivateKey,
) -> dict[str, Any]:
    if (
        not isinstance(prior_commits, list)
        or not prior_commits
        or len(prior_commits) != len(set(prior_commits))
        or any(not _is_lower_hex(commit, 40) for commit in prior_commits)
        or not _is_lower_hex(anchor_registry_sha256, 64)
    ):
        raise ValueError("prior commit snapshot is invalid")
    document = {
        "schema_version": 1,
        "status": "approved",
        "anchor_registry_sha256": anchor_registry_sha256,
        "commits": deepcopy(prior_commits),
        "signoff": {
            "signer": "confirmatory-prior-snapshot-v1",
            "signer_public_key": "",
            "signature": "",
        },
        "snapshot_sha256": "",
    }
    return _seal_document(
        document,
        private_key=signer_private_key,
        digest_field="snapshot_sha256",
    )


def _case_from_record(record: dict[str, Any]) -> dict[str, Any]:
    return {
        "case_id": record["source_record_id"],
        "repo_alias": record["repo_alias"],
        "baseline_commit": record["baseline_commit"],
        "followup_commit": record["followup_commit"],
        "request": record["confirmed_request"],
        "context_candidate_paths": deepcopy(record["context_candidate_paths"]),
        "surface": record["surface"],
        "ambiguity": record["ambiguity"],
        "selected_object": record["selected_object"],
    }


def _validate_prior_snapshot(
    *,
    prior_snapshot: dict[str, Any],
    trusted_prior_snapshot_public_keys: set[str],
    expected_prior_snapshot_sha256: str,
    expected_anchor_registry_sha256: str,
) -> list[str]:
    if not isinstance(prior_snapshot, dict) or set(prior_snapshot) != {
        "schema_version",
        "status",
        "anchor_registry_sha256",
        "commits",
        "signoff",
        "snapshot_sha256",
    }:
        raise ValueError("prior snapshot fields are invalid")
    _verify_document(
        prior_snapshot,
        digest_field="snapshot_sha256",
        trusted_public_keys=trusted_prior_snapshot_public_keys,
        expected_digest=expected_prior_snapshot_sha256,
        expected_signer="confirmatory-prior-snapshot-v1",
        label="prior snapshot",
    )
    prior_commits = prior_snapshot.get("commits")
    if (
        prior_snapshot.get("schema_version") != 1
        or prior_snapshot.get("status") != "approved"
        or prior_snapshot.get("anchor_registry_sha256")
        != expected_anchor_registry_sha256
        or not isinstance(prior_commits, list)
        or not prior_commits
        or len(prior_commits) != len(set(prior_commits))
        or any(not _is_lower_hex(commit, 40) for commit in prior_commits)
    ):
        raise ValueError("prior snapshot provenance mismatch")
    return prior_commits


def _validate_prior_evidence(
    *,
    prior_snapshot: dict[str, Any],
    trusted_prior_snapshot_public_keys: set[str],
    expected_prior_snapshot_sha256: str,
    prior_anchor_registry: dict[str, Any],
    trusted_anchor_public_keys: set[str],
    expected_anchor_registry_sha256: str,
    previous_anchor_registry: dict[str, Any] | None = None,
) -> list[str]:
    prior_commits = _validate_prior_snapshot(
        prior_snapshot=prior_snapshot,
        trusted_prior_snapshot_public_keys=trusted_prior_snapshot_public_keys,
        expected_prior_snapshot_sha256=expected_prior_snapshot_sha256,
        expected_anchor_registry_sha256=expected_anchor_registry_sha256,
    )
    context_selection.validate_confirmatory_anchor_registry(
        prior_anchor_registry,
        trusted_root_public_keys=trusted_anchor_public_keys,
        expected_registry_sha256=expected_anchor_registry_sha256,
        previous_registry=previous_anchor_registry,
    )
    return prior_commits


def _validate_universe_envelope(
    universe: dict[str, Any],
    *,
    expected_status: str,
) -> None:
    if not isinstance(universe, dict) or set(universe) != UNIVERSE_FIELDS:
        raise ValueError("candidate universe fields are invalid")
    provenance = universe.get("provenance")
    audit = universe.get("audit")
    policy = universe.get("selection_policy")
    candidates = universe.get("candidates")
    signoff = universe.get("signoff")
    if (
        universe.get("schema_version") != 2
        or universe.get("status") != expected_status
        or not isinstance(universe.get("batch_id"), str)
        or not universe["batch_id"].strip()
        or not isinstance(provenance, dict)
        or set(provenance) != UNIVERSE_PROVENANCE_FIELDS
        or any(
            not _is_lower_hex(provenance[field], 64)
            for field in (
                "private_inventory_sha256",
                "source_snapshot_sha256",
                "label_receipt_sha256",
                "prior_anchor_registry_sha256",
                "prior_snapshot_sha256",
            )
        )
        or not isinstance(audit, dict)
        or set(audit) != UNIVERSE_AUDIT_FIELDS
        or not isinstance(policy, dict)
        or set(policy) != UNIVERSE_SELECTION_POLICY_FIELDS
        or not isinstance(candidates, list)
        or not _is_lower_hex(universe.get("universe_sha256"), 64)
        or not isinstance(signoff, dict)
        or set(signoff) != {"signer", "signer_public_key", "signature"}
        or signoff["signer"] != "candidate-universe-signer-v1"
    ):
        raise ValueError("candidate universe provenance is invalid")
    try:
        _parse_timestamp(provenance["provider_ledger_cutoff"])
    except ValueError as error:
        raise ValueError("candidate universe provenance is invalid") from error
    counts = (
        audit["discovered_count"],
        audit["accepted_count"],
        audit["rejected_count"],
    )
    reason_counts = audit["rejected_reason_counts"]
    if (
        any(
            isinstance(value, bool) or not isinstance(value, int) or value < 0
            for value in counts
        )
        or audit["discovered_count"]
        != audit["accepted_count"] + audit["rejected_count"]
        or audit["accepted_count"] != len(candidates)
        or not isinstance(reason_counts, dict)
        or any(
            not isinstance(reason, str)
            or not reason
            or isinstance(count, bool)
            or not isinstance(count, int)
            or count < 1
            for reason, count in reason_counts.items()
        )
        or sum(reason_counts.values()) != audit["rejected_count"]
        or policy.get("prior_anchor_registry_sha256")
        != provenance["prior_anchor_registry_sha256"]
    ):
        raise ValueError("candidate universe audit is invalid")
def prepare_frozen_universe(
    inventory: dict[str, Any],
    *,
    trusted_collector_public_keys: set[str],
    expected_inventory_sha256: str,
    label_receipt: dict[str, Any],
    trusted_label_public_keys: set[str],
    prior_snapshot: dict[str, Any],
    trusted_prior_snapshot_public_keys: set[str],
    expected_prior_snapshot_sha256: str,
    prior_anchor_registry: dict[str, Any],
    trusted_anchor_public_keys: set[str],
    expected_anchor_registry_sha256: str,
    batch_id: str,
    source_snapshot_sha256: str,
    source_snapshot: dict[str, Any] | None = None,
    trusted_source_snapshot_public_keys: set[str] | None = None,
    expected_preregistration_sha256: str | None = None,
    preregistration: dict[str, Any] | None = None,
    trusted_preregistration_public_keys: set[str] | None = None,
    preregistration_registry_repository_root: str | None = None,
    registration_receipt: dict[str, Any] | None = None,
    trusted_registration_receipt_public_keys: set[str] | None = None,
    expected_registration_receipt_sha256: str | None = None,
    trusted_registration_root: dict[str, Any] | None = None,
    approved_trusted_registration_root_sha256: str | None = None,
    previous_anchor_registry: dict[str, Any] | None = None,
) -> dict[str, Any]:
    _verify_private_inventory(
        inventory,
        trusted_collector_public_keys=trusted_collector_public_keys,
        expected_inventory_sha256=expected_inventory_sha256,
        source_snapshot=source_snapshot,
        trusted_source_snapshot_public_keys=trusted_source_snapshot_public_keys,
        expected_source_snapshot_sha256=source_snapshot_sha256,
        expected_preregistration_sha256=expected_preregistration_sha256,
        preregistration=preregistration,
        trusted_preregistration_public_keys=trusted_preregistration_public_keys,
        preregistration_registry_repository_root=(
            preregistration_registry_repository_root
        ),
        registration_receipt=registration_receipt,
        trusted_registration_receipt_public_keys=(
            trusted_registration_receipt_public_keys
        ),
        expected_registration_receipt_sha256=(
            expected_registration_receipt_sha256
        ),
        trusted_registration_root=trusted_registration_root,
        approved_trusted_registration_root_sha256=(
            approved_trusted_registration_root_sha256
        ),
    )
    _validate_label_receipt(
        label_receipt,
        trusted_label_public_keys=trusted_label_public_keys,
        expected_label_receipt_sha256=label_receipt.get("receipt_sha256", "")
        if isinstance(label_receipt, dict)
        else "",
    )
    if (
        label_receipt["private_inventory_sha256"] != inventory["inventory_sha256"]
        or label_receipt["provider_outputs_available"] is not False
    ):
        raise ValueError("label receipt provenance mismatch")
    expected_labels = [{
        "source_record_id": record["source_record_id"],
        "surface": record["surface"],
        "ambiguity": record["ambiguity"],
        "selected_object": record["selected_object"],
    } for record in inventory["accepted_records"]]
    if label_receipt["labels"] != expected_labels:
        raise ValueError("label receipt labels mismatch")
    if not _is_lower_hex(source_snapshot_sha256, 64):
        raise ValueError("source snapshot hash is invalid")

    prior_commits = _validate_prior_evidence(
        prior_snapshot=prior_snapshot,
        trusted_prior_snapshot_public_keys=trusted_prior_snapshot_public_keys,
        expected_prior_snapshot_sha256=expected_prior_snapshot_sha256,
        prior_anchor_registry=prior_anchor_registry,
        trusted_anchor_public_keys=trusted_anchor_public_keys,
        expected_anchor_registry_sha256=expected_anchor_registry_sha256,
        previous_anchor_registry=previous_anchor_registry,
    )

    prior_set = set(prior_commits)
    if (
        not prior_commits
        or any(not _is_lower_hex(commit, 40) for commit in prior_commits)
        or len(prior_set) != len(prior_commits)
    ):
        raise ValueError("prior commit registry is invalid")
    records = inventory["accepted_records"]
    path_owners: dict[tuple[str, str], list[str]] = defaultdict(list)
    for record in records:
        for path in record["context_candidate_paths"]:
            path_owners[(record["repo_alias"], PurePosixPath(path).as_posix())].append(
                record["source_record_id"]
            )
    for owners in path_owners.values():
        owners.sort()

    start = datetime.fromisoformat(
        f"{inventory['observation_window']['observed_from']}T00:00:00+00:00"
    ).date()
    end = datetime.fromisoformat(
        f"{inventory['observation_window']['observed_through']}T00:00:00+00:00"
    ).date()
    candidates = []
    for record in records:
        observed_at = _parse_timestamp(record["completed_at"]).date()
        reason = None
        if record["followup_commit"] in prior_set:
            reason = "prior_overlap"
        elif not start <= observed_at <= end:
            reason = "outside_window"
        elif any(
            path_owners[(record["repo_alias"], PurePosixPath(path).as_posix())][0]
            != record["source_record_id"]
            for path in record["context_candidate_paths"]
        ):
            reason = "duplicate_context"
        candidates.append({
            "case": _case_from_record(record),
            "observed_at": observed_at.isoformat(),
            "eligible": reason is None,
            "exclusion_reason": reason,
        })

    document = {
        "schema_version": 2,
        "status": "draft",
        "batch_id": batch_id,
        "eligibility_policy": {
            "repository_aliases": sorted({
                record["repo_alias"] for record in records
            }),
            **deepcopy(inventory["observation_window"]),
            "excluded_case_reasons": [
                "duplicate_context",
                "outside_window",
                "prior_overlap",
            ],
        },
        "selection_policy": {
            "algorithm_version": runtime.CONFIRMATORY_CANDIDATE_SELECTION_ALGORITHM,
            "seed_mode": "post_freeze_signed_receipt",
            "tie_breaker": runtime.CONFIRMATORY_CANDIDATE_TIE_BREAKER,
            "provider_outputs_available_during_selection": False,
            "prior_registry_sha256": canonical_digest(prior_commits),
            "prior_anchor_registry_sha256": expected_anchor_registry_sha256,
            "required_surface_counts": deepcopy(
                runtime.FROZEN_CONFIRMATORY_SURFACE_COUNTS
            ),
            "required_ambiguity_counts": deepcopy(
                runtime.FROZEN_CONFIRMATORY_AMBIGUITY_COUNTS
            ),
            "maximum_selected_object_cases": (
                runtime.FROZEN_CONFIRMATORY_MAX_SELECTED_OBJECT_CASES
            ),
        },
        "provenance": {
            "private_inventory_sha256": inventory["inventory_sha256"],
            "source_snapshot_sha256": source_snapshot_sha256,
            "label_receipt_sha256": label_receipt["receipt_sha256"],
            "provider_ledger_cutoff": inventory["provider_ledger_cutoff"],
            "prior_anchor_registry_sha256": expected_anchor_registry_sha256,
            "prior_snapshot_sha256": expected_prior_snapshot_sha256,
        },
        "audit": deepcopy(inventory["audit"]),
        "candidates": candidates,
        "signoff": {
            "signer": "candidate-universe-signer-v1",
            "signer_public_key": "",
            "signature": "",
        },
        "universe_sha256": "",
    }
    document["universe_sha256"] = _signed_digest(
        document, "universe_sha256"
    )
    return document


def seal_frozen_universe(
    draft: dict[str, Any],
    signer_private_key: Ed25519PrivateKey,
    *,
    expected_draft_sha256: str,
    prior_snapshot: dict[str, Any],
    trusted_prior_snapshot_public_keys: set[str],
    expected_prior_snapshot_sha256: str,
) -> dict[str, Any]:
    _validate_universe_envelope(draft, expected_status="draft")
    if (
        not _is_lower_hex(expected_draft_sha256, 64)
        or draft.get("universe_sha256") != expected_draft_sha256
        or draft["universe_sha256"] != _signed_digest(draft, "universe_sha256")
    ):
        raise ValueError("candidate universe draft digest mismatch")
    if draft["provenance"]["prior_snapshot_sha256"] != (
        expected_prior_snapshot_sha256
    ):
        raise ValueError("candidate universe prior snapshot mismatch")
    prior_commits = _validate_prior_snapshot(
        prior_snapshot=prior_snapshot,
        trusted_prior_snapshot_public_keys=trusted_prior_snapshot_public_keys,
        expected_prior_snapshot_sha256=expected_prior_snapshot_sha256,
        expected_anchor_registry_sha256=draft["provenance"][
            "prior_anchor_registry_sha256"
        ],
    )
    runtime.validate_confirmatory_candidate_universe(
        _materialize_runtime_universe(draft, seed="validation-only-seed"),
        trusted_prior_commits=prior_commits,
    )
    frozen = deepcopy(draft)
    frozen["status"] = "frozen"
    return _seal_document(
        frozen,
        private_key=signer_private_key,
        digest_field="universe_sha256",
    )


def unseal_frozen_universe(universe: dict[str, Any]) -> dict[str, Any]:
    draft = deepcopy(universe)
    draft["status"] = "draft"
    draft["signoff"]["signer_public_key"] = ""
    draft["signoff"]["signature"] = ""
    draft["universe_sha256"] = _signed_digest(draft, "universe_sha256")
    return draft


def _materialize_runtime_universe(
    universe: dict[str, Any],
    *,
    seed: str,
) -> dict[str, Any]:
    policy = universe["selection_policy"]
    runtime_universe = {
        "schema_version": 1,
        "status": "preregistered",
        "batch_id": universe["batch_id"],
        "eligibility_policy": deepcopy(universe["eligibility_policy"]),
        "selection_policy": {
            "algorithm_version": policy["algorithm_version"],
            "seed": seed,
            "tie_breaker": policy["tie_breaker"],
            "provider_outputs_available_during_selection": policy[
                "provider_outputs_available_during_selection"
            ],
            "prior_registry_sha256": policy["prior_registry_sha256"],
            "required_surface_counts": deepcopy(policy["required_surface_counts"]),
            "required_ambiguity_counts": deepcopy(
                policy["required_ambiguity_counts"]
            ),
            "maximum_selected_object_cases": policy[
                "maximum_selected_object_cases"
            ],
        },
        "candidates": deepcopy(universe["candidates"]),
    }
    runtime_universe["universe_sha256"] = canonical_digest(runtime_universe)
    return runtime_universe


def validate_frozen_universe(
    universe: dict[str, Any],
    *,
    trusted_universe_public_keys: set[str],
    expected_universe_sha256: str,
    prior_snapshot: dict[str, Any],
    trusted_prior_snapshot_public_keys: set[str],
    expected_prior_snapshot_sha256: str,
    prior_anchor_registry: dict[str, Any],
    trusted_anchor_public_keys: set[str],
    expected_anchor_registry_sha256: str,
    previous_anchor_registry: dict[str, Any] | None = None,
) -> None:
    _validate_universe_envelope(universe, expected_status="frozen")
    _verify_document(
        universe,
        digest_field="universe_sha256",
        trusted_public_keys=trusted_universe_public_keys,
        expected_digest=expected_universe_sha256,
        expected_signer="candidate-universe-signer-v1",
        label="universe",
    )
    prior_commits = _validate_prior_evidence(
        prior_snapshot=prior_snapshot,
        trusted_prior_snapshot_public_keys=trusted_prior_snapshot_public_keys,
        expected_prior_snapshot_sha256=expected_prior_snapshot_sha256,
        prior_anchor_registry=prior_anchor_registry,
        trusted_anchor_public_keys=trusted_anchor_public_keys,
        expected_anchor_registry_sha256=expected_anchor_registry_sha256,
        previous_anchor_registry=previous_anchor_registry,
    )
    if universe["selection_policy"].get("prior_anchor_registry_sha256") != (
        expected_anchor_registry_sha256
    ):
        raise ValueError("candidate universe anchor registry mismatch")
    if universe["provenance"].get("prior_snapshot_sha256") != (
        expected_prior_snapshot_sha256
    ):
        raise ValueError("candidate universe prior snapshot mismatch")
    if universe["selection_policy"].get("seed_mode") != (
        "post_freeze_signed_receipt"
    ):
        raise ValueError("candidate universe seed mode is invalid")
    if "seed" in universe["selection_policy"]:
        raise ValueError("candidate universe must be seedless")
    runtime.validate_confirmatory_candidate_universe(
        _materialize_runtime_universe(universe, seed="validation-only-seed"),
        trusted_prior_commits=prior_commits,
    )


def issue_seed_receipt(
    universe: dict[str, Any],
    *,
    approved_universe_sha256: str,
    seed: str,
    signer_private_key: Ed25519PrivateKey,
) -> dict[str, Any]:
    if universe.get("universe_sha256") != approved_universe_sha256:
        raise ValueError("approved universe hash mismatch")
    if universe.get("status") != "frozen" or not isinstance(seed, str) or not seed.strip():
        raise ValueError("frozen universe and seed are required")
    document = {
        "schema_version": 1,
        "status": "approved",
        "universe_sha256": approved_universe_sha256,
        "seed": seed,
        "signoff": {
            "signer": "post-freeze-seed-signer-v1",
            "signer_public_key": "",
            "signature": "",
        },
        "receipt_sha256": "",
    }
    return _seal_document(
        document,
        private_key=signer_private_key,
        digest_field="receipt_sha256",
    )


def _validate_seed_receipt(
    receipt: dict[str, Any],
    *,
    trusted_seed_public_keys: set[str],
    expected_seed_receipt_sha256: str,
) -> None:
    if (
        not isinstance(receipt, dict)
        or set(receipt) != {
            "schema_version",
            "status",
            "universe_sha256",
            "seed",
            "signoff",
            "receipt_sha256",
        }
        or receipt.get("schema_version") != 1
        or receipt.get("status") != "approved"
        or not _is_lower_hex(receipt.get("universe_sha256"), 64)
        or not isinstance(receipt.get("seed"), str)
        or not receipt["seed"].strip()
    ):
        raise ValueError("seed receipt fields are invalid")
    _verify_document(
        receipt,
        digest_field="receipt_sha256",
        trusted_public_keys=trusted_seed_public_keys,
        expected_digest=expected_seed_receipt_sha256,
        expected_signer="post-freeze-seed-signer-v1",
        label="seed receipt",
    )


def build_selection_bundle(
    universe: dict[str, Any],
    *,
    seed_receipt: dict[str, Any],
    prior_snapshot: dict[str, Any],
    trusted_prior_snapshot_public_keys: set[str],
    expected_prior_snapshot_sha256: str,
    prior_anchor_registry: dict[str, Any],
    trusted_anchor_public_keys: set[str],
    expected_anchor_registry_sha256: str,
    trusted_universe_public_keys: set[str],
    trusted_seed_public_keys: set[str],
    expected_universe_sha256: str,
    expected_seed_receipt_sha256: str,
    previous_anchor_registry: dict[str, Any] | None = None,
) -> dict[str, Any]:
    validate_frozen_universe(
        universe,
        trusted_universe_public_keys=trusted_universe_public_keys,
        expected_universe_sha256=expected_universe_sha256,
        prior_snapshot=prior_snapshot,
        trusted_prior_snapshot_public_keys=trusted_prior_snapshot_public_keys,
        expected_prior_snapshot_sha256=expected_prior_snapshot_sha256,
        prior_anchor_registry=prior_anchor_registry,
        trusted_anchor_public_keys=trusted_anchor_public_keys,
        expected_anchor_registry_sha256=expected_anchor_registry_sha256,
        previous_anchor_registry=previous_anchor_registry,
    )
    _validate_seed_receipt(
        seed_receipt,
        trusted_seed_public_keys=trusted_seed_public_keys,
        expected_seed_receipt_sha256=expected_seed_receipt_sha256,
    )
    if seed_receipt.get("universe_sha256") != universe["universe_sha256"]:
        raise ValueError("seed receipt universe mismatch")
    prior_commits = prior_snapshot["commits"]
    runtime_universe = _materialize_runtime_universe(
        universe,
        seed=seed_receipt["seed"],
    )
    selection = runtime.build_confirmatory_candidate_selection(
        runtime_universe,
        trusted_prior_commits=prior_commits,
    )
    bundle = {
        "schema_version": 1,
        "status": "frozen",
        "batch_id": universe["batch_id"],
        "frozen_universe_sha256": universe["universe_sha256"],
        "seed_receipt_sha256": seed_receipt["receipt_sha256"],
        "runtime_universe_sha256": runtime_universe["universe_sha256"],
        "selection": selection,
    }
    bundle["bundle_sha256"] = canonical_digest(bundle)
    return bundle


def collect_inventory_from_session_manifest(
    manifest: dict[str, Any],
) -> dict[str, Any]:
    """Collect only sessions carrying an explicit, request-bound completion receipt."""
    if not isinstance(manifest, dict) or set(manifest) != {
        "schema_version",
        "observed_from",
        "observed_through",
        "provider_ledger_cutoff",
        "sources",
    } or manifest.get("schema_version") != 1:
        raise ValueError("session source manifest is invalid")
    sources = manifest["sources"]
    if not isinstance(sources, list) or not sources:
        raise ValueError("session sources are required")

    records: list[dict[str, Any]] = []
    for source in sources:
        source_id = (
            source.get("source_record_id", "unknown")
            if isinstance(source, dict)
            else "unknown"
        )
        if not _valid_session_source(source):
            records.append({"source_record_id": source_id})
            continue
        root = Path(source["repository_root"])
        session_dir = root / ".omc" / "state" / "sessions" / source["session_id"]
        session_path = session_dir / "session.json"
        completion_path = session_dir / "completion.json"
        try:
            session = json.loads(session_path.read_text(encoding="utf-8"))
            completion = json.loads(completion_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            records.append({"source_record_id": source_id})
            continue
        if not isinstance(session, dict) or not isinstance(completion, dict):
            records.append({"source_record_id": source_id})
            continue
        git = session.get("git")
        confirmation = session.get("confirmation")
        request = session.get("request")
        baseline = completion.get("baseline_commit")
        session_head = git.get("head") if isinstance(git, dict) else None
        if (
            _completion_receipt_fields(completion) is None
            or set(completion) != _completion_receipt_fields(completion)
            or completion.get("session_id") != source["session_id"]
            or session.get("session_id") != source["session_id"]
            or not isinstance(confirmation, dict)
            or confirmation.get("status") != "confirmed"
            or not isinstance(request, str)
            or not request.strip()
            or completion.get("request_sha256") != canonical_digest(request)
            or not isinstance(session_head, str)
            or not isinstance(baseline, str)
            or not baseline.startswith(session_head)
            or not _git_receipt_matches(
                root,
                session_head=session_head,
                completion=completion,
                context_candidate_paths=source["context_candidate_paths"],
            )
        ):
            records.append({"source_record_id": source_id})
            continue
        records.append({
            "source_record_id": source["source_record_id"],
            "session_id": source["session_id"],
            "confirmed_request": request,
            "confirmed_at": session.get("created_at"),
            "repo_alias": source["repo_alias"],
            "repository_root": source["repository_root"],
            "baseline_commit": completion["baseline_commit"],
            "followup_commit": completion["followup_commit"],
            "completed_at": completion["completed_at"],
            "changed_paths": completion["changed_paths"],
            "context_candidate_paths": source["context_candidate_paths"],
            "surface": source["surface"],
            "ambiguity": source["ambiguity"],
            "selected_object": source["selected_object"],
            "request_source": "confirmed_session_record",
            "completion_source": "explicit_completion_receipt",
            "provider_outputs_available": completion[
                "provider_outputs_available"
            ],
        })
    return collect_private_inventory(
        records,
        observed_from=manifest["observed_from"],
        observed_through=manifest["observed_through"],
        provider_ledger_cutoff=manifest["provider_ledger_cutoff"],
    )


def collect_preregistered_inventory_from_session_manifest(
    source_snapshot: dict[str, Any],
    *,
    preregistration: dict[str, Any],
    trusted_preregistration_public_keys: set[str],
    expected_preregistration_sha256: str,
    trusted_source_snapshot_public_keys: set[str],
    expected_source_snapshot_sha256: str,
    collector_public_key: str,
    preregistration_registry_repository_root: str,
    registration_receipt: dict[str, Any],
    trusted_registration_receipt_public_keys: set[str],
    expected_registration_receipt_sha256: str,
    trusted_registration_root: dict[str, Any] | None = None,
    approved_trusted_registration_root_sha256: str | None = None,
) -> dict[str, Any]:
    """Collect a signed prospective first-N cohort from explicit receipts."""
    validate_preregistered_provenance_chain(
        preregistration=preregistration,
        trusted_preregistration_public_keys=(
            trusted_preregistration_public_keys
        ),
        expected_preregistration_sha256=expected_preregistration_sha256,
        source_snapshot=source_snapshot,
        trusted_source_snapshot_public_keys=(
            trusted_source_snapshot_public_keys
        ),
        expected_source_snapshot_sha256=expected_source_snapshot_sha256,
        preregistration_registry_repository_root=(
            preregistration_registry_repository_root
        ),
        registration_receipt=registration_receipt,
        trusted_registration_receipt_public_keys=(
            trusted_registration_receipt_public_keys
        ),
        expected_registration_receipt_sha256=(
            expected_registration_receipt_sha256
        ),
        trusted_registration_root=trusted_registration_root,
        approved_trusted_registration_root_sha256=(
            approved_trusted_registration_root_sha256
        ),
    )
    if not _valid_public_key_text(collector_public_key):
        raise ValueError("inventory collector public key is invalid")
    disallowed_collector_keys = (
        _preregistration_actor_public_keys(preregistration)
        | {source_snapshot["signoff"]["signer_public_key"]}
        | _work_class_lock_signer_keys(source_snapshot)
    )
    if collector_public_key in disallowed_collector_keys:
        raise ValueError("inventory collector must be independent")
    sources = source_snapshot["sources"]

    window = preregistration["observation_window"]
    legacy_manifest = {
        "schema_version": 1,
        "observed_from": _parse_timestamp(
            window["observed_from"]
        ).date().isoformat(),
        "observed_through": _parse_timestamp(
            window["observed_through"]
        ).date().isoformat(),
        "provider_ledger_cutoff": preregistration["provider_ledger_cutoff"],
        "sources": [{
            field: source[field] for field in SESSION_SOURCE_FIELDS
        } for source in sources],
    }
    inventory = collect_inventory_from_session_manifest(legacy_manifest)
    work_class_by_session = {
        source["session_id"]: source["work_class"] for source in sources
    }
    classifications = classify_preregistered_sessions(
        preregistration,
        sessions=[{
            "session_id": record["session_id"],
            "completed_at": record["completed_at"],
            "work_class": work_class_by_session[record["session_id"]],
        } for record in inventory["accepted_records"]],
        trusted_preregistration_public_keys=(
            trusted_preregistration_public_keys
        ),
        expected_preregistration_sha256=expected_preregistration_sha256,
        approved_trusted_root_sha256=(
            approved_trusted_registration_root_sha256
        ),
    )
    disposition_by_session = {
        item["session_id"]: item["disposition"] for item in classifications
    }
    accepted: list[dict[str, Any]] = []
    rejected = deepcopy(inventory["rejected_records"])
    for record in inventory["accepted_records"]:
        disposition = disposition_by_session[record["session_id"]]
        if disposition == "confirmatory_candidate":
            accepted.append(record)
            continue
        rejected.append({
            "source_record_id": record["source_record_id"],
            "source_record_sha256": canonical_digest(record),
            "reason": disposition,
        })

    accepted.sort(key=lambda item: item["source_record_id"])
    rejected.sort(
        key=lambda item: (
            item["source_record_id"], item["source_record_sha256"]
        )
    )
    reason_counts = dict(sorted(Counter(
        record["reason"] for record in rejected
    ).items()))
    inventory["accepted_records"] = accepted
    inventory["rejected_records"] = rejected
    inventory["schema_version"] = 2
    inventory["preregistration_sha256"] = expected_preregistration_sha256
    inventory["source_snapshot_sha256"] = expected_source_snapshot_sha256
    inventory["collector_public_key"] = collector_public_key
    inventory["audit"] = {
        "discovered_count": len(sources),
        "accepted_count": len(accepted),
        "rejected_count": len(rejected),
        "rejected_reason_counts": reason_counts,
    }
    inventory["inventory_sha256"] = _inventory_digest(inventory)
    _validate_inventory_envelope(inventory, expected_status="collected")
    return inventory


def session_has_explicit_completion_receipt(session: dict[str, Any]) -> bool:
    git = session.get("git") if isinstance(session, dict) else None
    return (
        isinstance(git, dict)
        and _is_lower_hex(git.get("head"), 40)
        and _is_lower_hex(git.get("followup_commit"), 40)
        and isinstance(git.get("changed_paths"), list)
        and bool(git["changed_paths"])
    )


def _read_json(path: str) -> Any:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def _write_json(path: str, value: Any) -> None:
    destination = Path(path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(
        json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def _read_private_key(path: str) -> Ed25519PrivateKey:
    try:
        raw = base64.b64decode(Path(path).read_text().strip(), validate=True)
    except (OSError, binascii.Error, ValueError) as error:
        raise ValueError("private key file is invalid") from error
    if len(raw) != 32:
        raise ValueError("private key file is invalid")
    return Ed25519PrivateKey.from_private_bytes(raw)


def _read_optional_json(path: str | None) -> dict[str, Any] | None:
    if path is None:
        return None
    document = _read_json(path)
    if not isinstance(document, dict):
        raise ValueError("JSON object is required")
    return document


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Build a provenance-bound OMC Plan candidate universe."
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    preregister = subparsers.add_parser("prepare-preregistration")
    preregister.add_argument("--batch-id", required=True)
    preregister.add_argument("--collection-anchor-commit", required=True)
    preregister.add_argument(
        "--collection-anchor-repository-root", required=True
    )
    preregister.add_argument("--observed-from", required=True)
    preregister.add_argument("--observed-through", required=True)
    preregister.add_argument("--provider-ledger-cutoff", required=True)
    preregister.add_argument("--pilot-session-id", action="append", required=True)
    preregister.add_argument(
        "--registration-authority-public-key", required=True
    )
    preregister.add_argument("--output", required=True)

    sigstore_preregister = subparsers.add_parser(
        "prepare-sigstore-preregistration"
    )
    sigstore_preregister.add_argument("--batch-id", required=True)
    sigstore_preregister.add_argument("--collection-anchor-commit", required=True)
    sigstore_preregister.add_argument(
        "--collection-anchor-repository-root", required=True
    )
    sigstore_preregister.add_argument("--observed-from", required=True)
    sigstore_preregister.add_argument("--observed-through", required=True)
    sigstore_preregister.add_argument("--provider-ledger-cutoff", required=True)
    sigstore_preregister.add_argument(
        "--pilot-session-id", action="append", required=True
    )
    sigstore_preregister.add_argument("--trusted-registration-root", required=True)
    sigstore_preregister.add_argument(
        "--approved-trusted-root-sha256", required=True
    )
    sigstore_preregister.add_argument("--output", required=True)

    seal_preregistration = subparsers.add_parser("seal-preregistration")
    seal_preregistration.add_argument("preregistration")
    seal_preregistration.add_argument("--private-key", required=True)
    seal_preregistration.add_argument(
        "--collection-anchor-repository-root", required=True
    )
    seal_preregistration.add_argument(
        "--approved-preregistration-sha256", required=True
    )
    seal_preregistration.add_argument("--output", required=True)

    prepare_registry_record = subparsers.add_parser(
        "prepare-preregistration-registry-record"
    )
    prepare_registry_record.add_argument("preregistration")
    prepare_registry_record.add_argument("--output", required=True)

    prepare_sigstore_receipt = subparsers.add_parser(
        "prepare-sigstore-registration-receipt"
    )
    prepare_sigstore_receipt.add_argument("preregistration")
    prepare_sigstore_receipt.add_argument("registration_evidence")
    prepare_sigstore_receipt.add_argument("--registry-commit", required=True)
    prepare_sigstore_receipt.add_argument("--registry-path", required=True)
    prepare_sigstore_receipt.add_argument(
        "--trusted-registration-root", required=True
    )
    prepare_sigstore_receipt.add_argument(
        "--approved-trusted-root-sha256", required=True
    )
    prepare_sigstore_receipt.add_argument("--output", required=True)

    prepare_work_class_lock = subparsers.add_parser(
        "prepare-work-class-lock"
    )
    prepare_work_class_lock.add_argument("session")
    prepare_work_class_lock.add_argument("--repository-root", required=True)
    prepare_work_class_lock.add_argument("--output", required=True)

    seal_work_class_lock = subparsers.add_parser("seal-work-class-lock")
    seal_work_class_lock.add_argument("receipt")
    seal_work_class_lock.add_argument("--private-key", required=True)
    seal_work_class_lock.add_argument(
        "--approved-receipt-sha256", required=True
    )
    seal_work_class_lock.add_argument("--output", required=True)

    prepare_source_snapshot = subparsers.add_parser(
        "prepare-source-snapshot"
    )
    prepare_source_snapshot.add_argument("sources")
    prepare_source_snapshot.add_argument("preregistration")
    prepare_source_snapshot.add_argument("registration_receipt")
    prepare_source_snapshot.add_argument(
        "--trusted-preregistration-public-key",
        action="append",
        required=True,
    )
    prepare_source_snapshot.add_argument(
        "--expected-preregistration-sha256", required=True
    )
    prepare_source_snapshot.add_argument(
        "--preregistration-registry-repository-root", required=True
    )
    prepare_source_snapshot.add_argument(
        "--preregistration-registry-commit", required=True
    )
    prepare_source_snapshot.add_argument(
        "--preregistration-registry-path", required=True
    )
    prepare_source_snapshot.add_argument(
        "--trusted-registration-receipt-public-key",
        action="append",
    )
    prepare_source_snapshot.add_argument("--trusted-registration-root")
    prepare_source_snapshot.add_argument(
        "--approved-trusted-registration-root-sha256"
    )
    prepare_source_snapshot.add_argument(
        "--expected-registration-receipt-sha256", required=True
    )
    prepare_source_snapshot.add_argument(
        "--trusted-work-class-lock-public-key",
        action="append",
        required=True,
    )
    prepare_source_snapshot.add_argument("--output", required=True)

    seal_source_snapshot = subparsers.add_parser("seal-source-snapshot")
    seal_source_snapshot.add_argument("source_snapshot")
    seal_source_snapshot.add_argument("--private-key", required=True)
    seal_source_snapshot.add_argument(
        "--approved-source-snapshot-sha256", required=True
    )
    seal_source_snapshot.add_argument("--output", required=True)

    collect = subparsers.add_parser("collect-sessions")
    collect.add_argument("manifest")
    collect.add_argument("--private-key", required=True)
    collect.add_argument("--output", required=True)

    collect_preregistered = subparsers.add_parser(
        "collect-preregistered-sessions"
    )
    collect_preregistered.add_argument("manifest")
    collect_preregistered.add_argument("preregistration")
    collect_preregistered.add_argument("registration_receipt")
    collect_preregistered.add_argument(
        "--trusted-preregistration-public-key",
        action="append",
        required=True,
    )
    collect_preregistered.add_argument(
        "--expected-preregistration-sha256", required=True
    )
    collect_preregistered.add_argument(
        "--trusted-source-snapshot-public-key",
        action="append",
        required=True,
    )
    collect_preregistered.add_argument(
        "--expected-source-snapshot-sha256", required=True
    )
    collect_preregistered.add_argument(
        "--preregistration-registry-repository-root", required=True
    )
    collect_preregistered.add_argument(
        "--trusted-registration-receipt-public-key",
        action="append",
    )
    collect_preregistered.add_argument("--trusted-registration-root")
    collect_preregistered.add_argument(
        "--approved-trusted-registration-root-sha256"
    )
    collect_preregistered.add_argument(
        "--expected-registration-receipt-sha256", required=True
    )
    collect_preregistered.add_argument("--private-key", required=True)
    collect_preregistered.add_argument("--output", required=True)

    labels = subparsers.add_parser("sign-labels")
    labels.add_argument("inventory")
    labels.add_argument("--private-key", required=True)
    labels.add_argument("--trusted-collector-public-key", action="append", required=True)
    labels.add_argument("--expected-inventory-sha256", required=True)
    labels.add_argument("--source-snapshot")
    labels.add_argument("--trusted-source-snapshot-public-key", action="append")
    labels.add_argument("--expected-source-snapshot-sha256")
    labels.add_argument("--expected-preregistration-sha256")
    labels.add_argument("--preregistration")
    labels.add_argument("--trusted-preregistration-public-key", action="append")
    labels.add_argument("--preregistration-registry-repository-root")
    labels.add_argument("--registration-receipt")
    labels.add_argument(
        "--trusted-registration-receipt-public-key", action="append"
    )
    labels.add_argument("--expected-registration-receipt-sha256")
    labels.add_argument("--trusted-registration-root")
    labels.add_argument("--approved-trusted-registration-root-sha256")
    labels.add_argument("--output", required=True)

    prior = subparsers.add_parser("sign-prior-snapshot")
    prior.add_argument("prior_commits")
    prior.add_argument("--anchor-registry-sha256", required=True)
    prior.add_argument("--private-key", required=True)
    prior.add_argument("--output", required=True)

    prepare = subparsers.add_parser("prepare-universe")
    prepare.add_argument("inventory")
    prepare.add_argument("label_receipt")
    prepare.add_argument("prior_snapshot")
    prepare.add_argument("prior_anchor_registry")
    prepare.add_argument("--batch-id", required=True)
    prepare.add_argument("--source-snapshot-sha256", required=True)
    prepare.add_argument("--trusted-label-public-key", action="append", required=True)
    prepare.add_argument("--trusted-collector-public-key", action="append", required=True)
    prepare.add_argument("--expected-inventory-sha256", required=True)
    prepare.add_argument("--source-snapshot")
    prepare.add_argument("--trusted-source-snapshot-public-key", action="append")
    prepare.add_argument("--expected-preregistration-sha256")
    prepare.add_argument("--preregistration")
    prepare.add_argument("--trusted-preregistration-public-key", action="append")
    prepare.add_argument("--preregistration-registry-repository-root")
    prepare.add_argument("--registration-receipt")
    prepare.add_argument(
        "--trusted-registration-receipt-public-key", action="append"
    )
    prepare.add_argument("--expected-registration-receipt-sha256")
    prepare.add_argument("--trusted-registration-root")
    prepare.add_argument("--approved-trusted-registration-root-sha256")
    prepare.add_argument("--trusted-prior-snapshot-public-key", action="append", required=True)
    prepare.add_argument("--expected-prior-snapshot-sha256", required=True)
    prepare.add_argument("--trusted-anchor-public-key", action="append", required=True)
    prepare.add_argument("--expected-anchor-registry-sha256", required=True)
    prepare.add_argument("--previous-anchor-registry")
    prepare.add_argument("--output", required=True)

    seal = subparsers.add_parser("seal-universe")
    seal.add_argument("draft")
    seal.add_argument("prior_snapshot")
    seal.add_argument("--private-key", required=True)
    seal.add_argument("--approved-draft-sha256", required=True)
    seal.add_argument("--trusted-prior-snapshot-public-key", action="append", required=True)
    seal.add_argument("--expected-prior-snapshot-sha256", required=True)
    seal.add_argument("--output", required=True)

    seed = subparsers.add_parser("issue-seed")
    seed.add_argument("universe")
    seed.add_argument("--approved-universe-sha256", required=True)
    seed.add_argument("--seed", required=True)
    seed.add_argument("--private-key", required=True)
    seed.add_argument("--output", required=True)

    select = subparsers.add_parser("select")
    select.add_argument("universe")
    select.add_argument("seed_receipt")
    select.add_argument("prior_snapshot")
    select.add_argument("prior_anchor_registry")
    select.add_argument("--trusted-universe-public-key", action="append", required=True)
    select.add_argument("--trusted-seed-public-key", action="append", required=True)
    select.add_argument("--trusted-prior-snapshot-public-key", action="append", required=True)
    select.add_argument("--expected-prior-snapshot-sha256", required=True)
    select.add_argument("--trusted-anchor-public-key", action="append", required=True)
    select.add_argument("--expected-anchor-registry-sha256", required=True)
    select.add_argument("--previous-anchor-registry")
    select.add_argument("--expected-universe-sha256", required=True)
    select.add_argument("--expected-seed-receipt-sha256", required=True)
    select.add_argument("--output", required=True)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    if args.command == "prepare-preregistration":
        result = prepare_collection_preregistration(
            batch_id=args.batch_id,
            collection_anchor_commit=args.collection_anchor_commit,
            collection_anchor_repository_root=(
                args.collection_anchor_repository_root
            ),
            observed_from=args.observed_from,
            observed_through=args.observed_through,
            provider_ledger_cutoff=args.provider_ledger_cutoff,
            pilot_session_ids=args.pilot_session_id,
            registration_authority_public_key=(
                args.registration_authority_public_key
            ),
        )
    elif args.command == "prepare-sigstore-preregistration":
        result = prepare_sigstore_collection_preregistration(
            batch_id=args.batch_id,
            collection_anchor_commit=args.collection_anchor_commit,
            collection_anchor_repository_root=(
                args.collection_anchor_repository_root
            ),
            observed_from=args.observed_from,
            observed_through=args.observed_through,
            provider_ledger_cutoff=args.provider_ledger_cutoff,
            pilot_session_ids=args.pilot_session_id,
            trusted_root=_read_json(args.trusted_registration_root),
            approved_trusted_root_sha256=args.approved_trusted_root_sha256,
        )
    elif args.command == "seal-preregistration":
        result = seal_collection_preregistration(
            _read_json(args.preregistration),
            _read_private_key(args.private_key),
            collection_anchor_repository_root=(
                args.collection_anchor_repository_root
            ),
            expected_preregistration_sha256=(
                args.approved_preregistration_sha256
            ),
        )
    elif args.command == "prepare-preregistration-registry-record":
        result = prepare_preregistration_registry_record(
            _read_json(args.preregistration)
        )
    elif args.command == "prepare-sigstore-registration-receipt":
        result = prepare_sigstore_registration_receipt(
            _read_json(args.preregistration),
            registry_commit=args.registry_commit,
            registry_path=args.registry_path,
            registration_evidence=_read_json(args.registration_evidence),
            trusted_root=_read_json(args.trusted_registration_root),
            approved_trusted_root_sha256=args.approved_trusted_root_sha256,
        )
    elif args.command == "prepare-work-class-lock":
        result = prepare_work_class_lock_receipt(
            _resolved_work_class_lock_session(
                args.repository_root,
                _read_json(args.session),
            )
        )
    elif args.command == "seal-work-class-lock":
        result = seal_work_class_lock_receipt(
            _read_json(args.receipt),
            _read_private_key(args.private_key),
            expected_receipt_sha256=args.approved_receipt_sha256,
        )
    elif args.command == "prepare-source-snapshot":
        source_document = _read_json(args.sources)
        if (
            not isinstance(source_document, dict)
            or set(source_document) != {"schema_version", "sources"}
            or source_document.get("schema_version") != 1
        ):
            raise ValueError("source ledger input is invalid")
        result = prepare_preregistered_source_snapshot(
            sources=source_document["sources"],
            preregistration=_read_json(args.preregistration),
            trusted_preregistration_public_keys=set(
                args.trusted_preregistration_public_key
            ),
            expected_preregistration_sha256=(
                args.expected_preregistration_sha256
            ),
            preregistration_registry_repository_root=(
                args.preregistration_registry_repository_root
            ),
            preregistration_registry_commit=(
                args.preregistration_registry_commit
            ),
            preregistration_registry_path=args.preregistration_registry_path,
            registration_receipt=_read_json(args.registration_receipt),
            trusted_registration_receipt_public_keys=set(
                args.trusted_registration_receipt_public_key or []
            ),
            expected_registration_receipt_sha256=(
                args.expected_registration_receipt_sha256
            ),
            trusted_work_class_lock_public_keys=set(
                args.trusted_work_class_lock_public_key
            ),
            trusted_registration_root=_read_optional_json(
                args.trusted_registration_root
            ),
            approved_trusted_registration_root_sha256=(
                args.approved_trusted_registration_root_sha256
            ),
        )
    elif args.command == "seal-source-snapshot":
        result = seal_preregistered_source_snapshot(
            _read_json(args.source_snapshot),
            _read_private_key(args.private_key),
            expected_source_snapshot_sha256=(
                args.approved_source_snapshot_sha256
            ),
        )
    elif args.command == "collect-sessions":
        draft = collect_inventory_from_session_manifest(_read_json(args.manifest))
        result = seal_private_inventory(
            draft,
            _read_private_key(args.private_key),
            expected_inventory_sha256=draft["inventory_sha256"],
        )
    elif args.command == "collect-preregistered-sessions":
        source_snapshot = _read_json(args.manifest)
        collector_private_key = _read_private_key(args.private_key)
        draft = collect_preregistered_inventory_from_session_manifest(
            source_snapshot,
            preregistration=_read_json(args.preregistration),
            trusted_preregistration_public_keys=set(
                args.trusted_preregistration_public_key
            ),
            expected_preregistration_sha256=(
                args.expected_preregistration_sha256
            ),
            trusted_source_snapshot_public_keys=set(
                args.trusted_source_snapshot_public_key
            ),
            expected_source_snapshot_sha256=(
                args.expected_source_snapshot_sha256
            ),
            collector_public_key=public_key_text(collector_private_key),
            preregistration_registry_repository_root=(
                args.preregistration_registry_repository_root
            ),
            registration_receipt=_read_json(args.registration_receipt),
            trusted_registration_receipt_public_keys=set(
                args.trusted_registration_receipt_public_key or []
            ),
            expected_registration_receipt_sha256=(
                args.expected_registration_receipt_sha256
            ),
            trusted_registration_root=_read_optional_json(
                args.trusted_registration_root
            ),
            approved_trusted_registration_root_sha256=(
                args.approved_trusted_registration_root_sha256
            ),
        )
        result = seal_preregistered_inventory(
            draft,
            collector_private_key,
            source_snapshot=source_snapshot,
            trusted_source_snapshot_public_keys=set(
                args.trusted_source_snapshot_public_key
            ),
            expected_source_snapshot_sha256=(
                args.expected_source_snapshot_sha256
            ),
            expected_preregistration_sha256=(
                args.expected_preregistration_sha256
            ),
            expected_inventory_sha256=draft["inventory_sha256"],
        )
    elif args.command == "sign-labels":
        result = sign_label_receipt(
            _read_json(args.inventory),
            _read_private_key(args.private_key),
            trusted_collector_public_keys=set(args.trusted_collector_public_key),
            expected_inventory_sha256=args.expected_inventory_sha256,
            source_snapshot=_read_optional_json(args.source_snapshot),
            trusted_source_snapshot_public_keys=set(
                args.trusted_source_snapshot_public_key or []
            ),
            expected_source_snapshot_sha256=(
                args.expected_source_snapshot_sha256
            ),
            expected_preregistration_sha256=(
                args.expected_preregistration_sha256
            ),
            preregistration=_read_optional_json(args.preregistration),
            trusted_preregistration_public_keys=set(
                args.trusted_preregistration_public_key or []
            ),
            preregistration_registry_repository_root=(
                args.preregistration_registry_repository_root
            ),
            registration_receipt=_read_optional_json(
                args.registration_receipt
            ),
            trusted_registration_receipt_public_keys=set(
                args.trusted_registration_receipt_public_key or []
            ),
            expected_registration_receipt_sha256=(
                args.expected_registration_receipt_sha256
            ),
            trusted_registration_root=_read_optional_json(
                args.trusted_registration_root
            ),
            approved_trusted_registration_root_sha256=(
                args.approved_trusted_registration_root_sha256
            ),
        )
    elif args.command == "sign-prior-snapshot":
        prior_document = _read_json(args.prior_commits)
        if (
            not isinstance(prior_document, dict)
            or set(prior_document) != {"schema_version", "commits"}
            or prior_document.get("schema_version") != 1
        ):
            raise ValueError("prior commit registry is invalid")
        result = sign_prior_commit_snapshot(
            prior_document.get("commits"),
            anchor_registry_sha256=args.anchor_registry_sha256,
            signer_private_key=_read_private_key(args.private_key),
        )
    elif args.command == "prepare-universe":
        result = prepare_frozen_universe(
            _read_json(args.inventory),
            trusted_collector_public_keys=set(args.trusted_collector_public_key),
            expected_inventory_sha256=args.expected_inventory_sha256,
            label_receipt=_read_json(args.label_receipt),
            trusted_label_public_keys=set(args.trusted_label_public_key),
            prior_snapshot=_read_json(args.prior_snapshot),
            trusted_prior_snapshot_public_keys=set(
                args.trusted_prior_snapshot_public_key
            ),
            expected_prior_snapshot_sha256=args.expected_prior_snapshot_sha256,
            prior_anchor_registry=_read_json(args.prior_anchor_registry),
            trusted_anchor_public_keys=set(args.trusted_anchor_public_key),
            expected_anchor_registry_sha256=args.expected_anchor_registry_sha256,
            previous_anchor_registry=_read_optional_json(
                args.previous_anchor_registry
            ),
            batch_id=args.batch_id,
            source_snapshot_sha256=args.source_snapshot_sha256,
            source_snapshot=_read_optional_json(args.source_snapshot),
            trusted_source_snapshot_public_keys=set(
                args.trusted_source_snapshot_public_key or []
            ),
            expected_preregistration_sha256=(
                args.expected_preregistration_sha256
            ),
            preregistration=_read_optional_json(args.preregistration),
            trusted_preregistration_public_keys=set(
                args.trusted_preregistration_public_key or []
            ),
            preregistration_registry_repository_root=(
                args.preregistration_registry_repository_root
            ),
            registration_receipt=_read_optional_json(
                args.registration_receipt
            ),
            trusted_registration_receipt_public_keys=set(
                args.trusted_registration_receipt_public_key or []
            ),
            expected_registration_receipt_sha256=(
                args.expected_registration_receipt_sha256
            ),
            trusted_registration_root=_read_optional_json(
                args.trusted_registration_root
            ),
            approved_trusted_registration_root_sha256=(
                args.approved_trusted_registration_root_sha256
            ),
        )
    elif args.command == "seal-universe":
        result = seal_frozen_universe(
            _read_json(args.draft),
            _read_private_key(args.private_key),
            expected_draft_sha256=args.approved_draft_sha256,
            prior_snapshot=_read_json(args.prior_snapshot),
            trusted_prior_snapshot_public_keys=set(
                args.trusted_prior_snapshot_public_key
            ),
            expected_prior_snapshot_sha256=args.expected_prior_snapshot_sha256,
        )
    elif args.command == "issue-seed":
        result = issue_seed_receipt(
            _read_json(args.universe),
            approved_universe_sha256=args.approved_universe_sha256,
            seed=args.seed,
            signer_private_key=_read_private_key(args.private_key),
        )
    else:
        result = build_selection_bundle(
            _read_json(args.universe),
            seed_receipt=_read_json(args.seed_receipt),
            prior_snapshot=_read_json(args.prior_snapshot),
            trusted_prior_snapshot_public_keys=set(
                args.trusted_prior_snapshot_public_key
            ),
            expected_prior_snapshot_sha256=args.expected_prior_snapshot_sha256,
            prior_anchor_registry=_read_json(args.prior_anchor_registry),
            trusted_anchor_public_keys=set(args.trusted_anchor_public_key),
            expected_anchor_registry_sha256=args.expected_anchor_registry_sha256,
            previous_anchor_registry=_read_optional_json(
                args.previous_anchor_registry
            ),
            trusted_universe_public_keys=set(args.trusted_universe_public_key),
            trusted_seed_public_keys=set(args.trusted_seed_public_key),
            expected_universe_sha256=args.expected_universe_sha256,
            expected_seed_receipt_sha256=args.expected_seed_receipt_sha256,
        )
    _write_json(args.output, result)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
