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


def canonical_digest(value: Any) -> str:
    return runtime.canonical_digest(value)


def public_key_text(private_key: Ed25519PrivateKey) -> str:
    raw = private_key.public_key().public_bytes(
        encoding=serialization.Encoding.Raw,
        format=serialization.PublicFormat.Raw,
    )
    return base64.b64encode(raw).decode("ascii")


def _is_lower_hex(value: Any, length: int) -> bool:
    return (
        isinstance(value, str)
        and len(value) == length
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


def _validate_inventory_envelope(
    inventory: dict[str, Any],
    *,
    expected_status: str,
) -> None:
    fields = {
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
    if not isinstance(inventory, dict) or set(inventory) != fields:
        raise ValueError("private inventory fields are invalid")
    window = inventory.get("observation_window")
    audit = inventory.get("audit")
    accepted = inventory.get("accepted_records")
    rejected = inventory.get("rejected_records")
    if (
        inventory.get("schema_version") != 1
        or inventory.get("status") != expected_status
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
        or record["reason"] != "invalid_provenance"
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
        != ({"invalid_provenance": len(rejected)} if rejected else {})
    ):
        raise ValueError("private inventory audit is invalid")


def seal_private_inventory(
    inventory: dict[str, Any],
    collector_private_key: Ed25519PrivateKey,
    *,
    expected_inventory_sha256: str,
) -> dict[str, Any]:
    _validate_inventory_envelope(inventory, expected_status="collected")
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


def _verify_private_inventory(
    inventory: dict[str, Any],
    *,
    trusted_collector_public_keys: set[str],
    expected_inventory_sha256: str,
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


def sign_label_receipt(
    inventory: dict[str, Any],
    signer_private_key: Ed25519PrivateKey,
    *,
    trusted_collector_public_keys: set[str],
    expected_inventory_sha256: str,
) -> dict[str, Any]:
    _verify_private_inventory(
        inventory,
        trusted_collector_public_keys=trusted_collector_public_keys,
        expected_inventory_sha256=expected_inventory_sha256,
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
    previous_anchor_registry: dict[str, Any] | None = None,
) -> dict[str, Any]:
    _verify_private_inventory(
        inventory,
        trusted_collector_public_keys=trusted_collector_public_keys,
        expected_inventory_sha256=expected_inventory_sha256,
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
            set(completion) != COMPLETION_RECEIPT_FIELDS
            or completion.get("schema_version") != 1
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

    collect = subparsers.add_parser("collect-sessions")
    collect.add_argument("manifest")
    collect.add_argument("--private-key", required=True)
    collect.add_argument("--output", required=True)

    labels = subparsers.add_parser("sign-labels")
    labels.add_argument("inventory")
    labels.add_argument("--private-key", required=True)
    labels.add_argument("--trusted-collector-public-key", action="append", required=True)
    labels.add_argument("--expected-inventory-sha256", required=True)
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
    if args.command == "collect-sessions":
        draft = collect_inventory_from_session_manifest(_read_json(args.manifest))
        result = seal_private_inventory(
            draft,
            _read_private_key(args.private_key),
            expected_inventory_sha256=draft["inventory_sha256"],
        )
    elif args.command == "sign-labels":
        result = sign_label_receipt(
            _read_json(args.inventory),
            _read_private_key(args.private_key),
            trusted_collector_public_keys=set(args.trusted_collector_public_key),
            expected_inventory_sha256=args.expected_inventory_sha256,
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
