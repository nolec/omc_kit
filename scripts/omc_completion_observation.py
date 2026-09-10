#!/usr/bin/env python3
"""Fail-closed V0 projection over OMC completion-lineage receipts."""

from __future__ import annotations

import argparse
import base64
import binascii
import hashlib
import json
import os
import re
import subprocess
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any

import omc_state
from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import (
    Ed25519PrivateKey,
    Ed25519PublicKey,
)


SCHEMA = "omc-completion-observation/v0"
TAXONOMY = [
    "defect_correction",
    "missing_requirement",
    "persona_mismatch",
    "scope_change",
    "clarification",
    "preference",
]
PRIMARY_CORRECTIONS = TAXONOMY[:3]
LIVE_SCHEMA = "omc-live-completion-observation/v1"


class CaptureError(ValueError):
    """The observation evidence cannot support a result."""


def canonical_sha256(value: object) -> str:
    return hashlib.sha256(
        json.dumps(
            value, ensure_ascii=False, sort_keys=True, separators=(",", ":")
        ).encode("utf-8")
    ).hexdigest()


def _sealed(document: dict[str, Any], field: str) -> dict[str, Any]:
    result = {**document, field: ""}
    result[field] = canonical_sha256(result)
    return result


def _public_key(private_key: Ed25519PrivateKey) -> str:
    return base64.b64encode(private_key.public_key().public_bytes_raw()).decode("ascii")


def _seal_signed(
    document: dict[str, Any], *, private_key: Ed25519PrivateKey, signer: str
) -> dict[str, Any]:
    result = {
        **document,
        "receipt_sha256": "",
        "signoff": {
            "algorithm": "ed25519",
            "signer": signer,
            "signer_public_key": _public_key(private_key),
            "signature": "",
        },
    }
    digest_payload = {**result, "signoff": {**result["signoff"], "signature": ""}}
    result["receipt_sha256"] = canonical_sha256(digest_payload)
    signed_payload = {**result, "signoff": {**result["signoff"], "signature": ""}}
    result["signoff"]["signature"] = base64.b64encode(
        private_key.sign(
            json.dumps(
                signed_payload,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            ).encode("utf-8")
        )
    ).decode("ascii")
    return result


def _verify_signed(
    document: object, *, trusted_public_key: str, signer: str
) -> dict[str, Any]:
    if not isinstance(document, dict) or not isinstance(document.get("signoff"), dict):
        raise CaptureError("signed_receipt_invalid")
    signoff = document["signoff"]
    payload = {**document, "signoff": {**signoff, "signature": ""}}
    digest_payload = {**payload, "receipt_sha256": ""}
    if (
        signoff.get("algorithm") != "ed25519"
        or signoff.get("signer") != signer
        or signoff.get("signer_public_key") != trusted_public_key
        or canonical_sha256(digest_payload) != document.get("receipt_sha256")
    ):
        raise CaptureError("signed_receipt_invalid")
    try:
        Ed25519PublicKey.from_public_bytes(
            base64.b64decode(trusted_public_key, validate=True)
        ).verify(
            base64.b64decode(str(signoff.get("signature")), validate=True),
            json.dumps(
                payload,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            ).encode("utf-8"),
        )
    except (InvalidSignature, TypeError, ValueError) as error:
        raise CaptureError("signed_receipt_invalid") from error
    return document


def _validate_registration(registration: object) -> dict[str, Any]:
    if not isinstance(registration, dict):
        raise CaptureError("registration_invalid")
    expected_selection = {
        "rule": "chronological_first_eligible",
        "count": 10,
        "minimum_repositories": 2,
        "eligible_work_class": "implementation",
    }
    repositories = registration.get("repositories")
    if (
        registration.get("schema_version") != SCHEMA
        or registration.get("artifact_type") != "registration"
        or registration.get("selection") != expected_selection
        or registration.get("taxonomy") != TAXONOMY
        or registration.get("primary_corrections") != PRIMARY_CORRECTIONS
        or registration.get("claim_boundary") != "CAPTURE_FEASIBILITY_ONLY"
        or not isinstance(registration.get("collector_public_key"), str)
        or not registration["collector_public_key"]
        or not isinstance(registration.get("trusted_execution_public_key"), str)
        or not registration["trusted_execution_public_key"]
        or not isinstance(repositories, list)
        or len(repositories) < 2
        or canonical_sha256({**registration, "registration_sha256": ""})
        != registration.get("registration_sha256")
    ):
        raise CaptureError("registration_invalid")
    ids: set[str] = set()
    roots: set[str] = set()
    for item in repositories:
        if (
            not isinstance(item, dict)
            or set(item) != {"repo_id", "root_sha256", "trusted_public_key"}
            or not isinstance(item.get("repo_id"), str)
            or not item["repo_id"]
            or not isinstance(item.get("root_sha256"), str)
            or len(item["root_sha256"]) != 64
            or not isinstance(item.get("trusted_public_key"), str)
            or item["repo_id"] in ids
            or item["root_sha256"] in roots
        ):
            raise CaptureError("registration_invalid")
        ids.add(item["repo_id"])
        roots.add(item["root_sha256"])
    try:
        execution_key = base64.b64decode(
            registration["trusted_execution_public_key"], validate=True
        )
        Ed25519PublicKey.from_public_bytes(execution_key)
    except (TypeError, ValueError) as error:
        raise CaptureError("registration_invalid") from error
    if registration["trusted_execution_public_key"] in {
        registration["collector_public_key"],
        *(item["trusted_public_key"] for item in repositories),
    }:
        raise CaptureError("registration_invalid")
    return registration


def _timestamp(value: object) -> datetime:
    if not isinstance(value, str):
        raise CaptureError("timestamp_invalid")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as error:
        raise CaptureError("timestamp_invalid") from error
    if parsed.tzinfo is None:
        raise CaptureError("timestamp_invalid")
    return parsed


def _validate_reconciliation_timestamp(
    value: object, *, captured_at: object, closed_at: object | None = None
) -> None:
    try:
        reconciled = _timestamp(value)
        captured = _timestamp(captured_at)
        closed = _timestamp(closed_at) if closed_at is not None else None
    except CaptureError as error:
        raise CaptureError("reconciliation_timestamp_invalid") from error
    if reconciled < captured or (closed is not None and reconciled > closed):
        raise CaptureError("reconciliation_timestamp_invalid")


def build_registration(
    *,
    study_id: str,
    frozen_at: str,
    repositories: list[dict[str, str]],
    collector_public_key: str | None = None,
    trusted_execution_public_key: str | None = None,
) -> dict[str, Any]:
    if not study_id or not frozen_at:
        raise CaptureError("registration_invalid")
    inferred_collector = None
    if repositories and isinstance(repositories, list) and isinstance(repositories[0], dict):
        inferred_collector = repositories[0].get("trusted_public_key")
    registration = {
        "schema_version": SCHEMA,
        "artifact_type": "registration",
        "study_id": study_id,
        "frozen_at": frozen_at,
        "repositories": repositories,
        "collector_public_key": collector_public_key or inferred_collector,
        "trusted_execution_public_key": trusted_execution_public_key,
        "selection": {
            "rule": "chronological_first_eligible",
            "count": 10,
            "minimum_repositories": 2,
            "eligible_work_class": "implementation",
        },
        "taxonomy": TAXONOMY,
        "primary_corrections": PRIMARY_CORRECTIONS,
        "claim_boundary": "CAPTURE_FEASIBILITY_ONLY",
        "registration_sha256": "",
    }
    return _validate_registration(_sealed(registration, "registration_sha256"))


def build_candidate(
    registration: dict[str, Any],
    *,
    repo_id: str,
    repository_root_sha256: str,
    terminal: dict[str, Any],
    completion: dict[str, Any],
    lineage: dict[str, Any],
    raw_request: bytes,
    raw_output: bytes,
    execution_receipt: dict[str, Any] | None = None,
) -> dict[str, Any]:
    registration = _validate_registration(registration)
    repository = next(
        (item for item in registration["repositories"] if item["repo_id"] == repo_id),
        None,
    )
    if repository is None or repository["root_sha256"] != repository_root_sha256:
        raise CaptureError("repository_binding_mismatch")
    if not isinstance(completion, dict) or not isinstance(lineage, dict):
        raise CaptureError("bundle_invalid")
    try:
        verified_terminal = omc_state._verify_capture(
            terminal, repository["trusted_public_key"]
        )
    except (AttributeError, KeyError, TypeError, ValueError) as error:
        raise CaptureError("bundle_invalid") from error
    try:
        verified_execution = _verify_signed(
            execution_receipt,
            trusted_public_key=registration["trusted_execution_public_key"],
            signer="completion-observation-execution-v0",
        )
    except CaptureError as error:
        raise CaptureError("execution_receipt_invalid") from error
    request_sha256 = canonical_sha256(raw_request.decode("utf-8"))
    if (
        verified_execution.get("schema_version") != SCHEMA
        or verified_execution.get("artifact_type") != "execution"
        or verified_execution.get("repo_id") != repo_id
        or verified_execution.get("repository_root_sha256") != repository_root_sha256
        or verified_execution.get("work_id") != lineage.get("work_id")
        or verified_execution.get("session_id") != completion.get("session_id")
        or verified_execution.get("terminal_sha256") != canonical_sha256(terminal)
        or verified_execution.get("raw_request_sha256")
        != hashlib.sha256(raw_request).hexdigest()
        or verified_execution.get("raw_output_sha256")
        != hashlib.sha256(raw_output).hexdigest()
    ):
        raise CaptureError("execution_receipt_invalid")
    if (
        completion.get("work_class") != "implementation"
    ):
        raise CaptureError("candidate_ineligible")
    if (
        verified_terminal.get("status") != "COMPLETE"
        or verified_terminal.get("capture_validity") != "VALID"
        or verified_terminal.get("verification_passed") is not True
        or verified_terminal.get("work_id") != lineage.get("work_id")
        or verified_terminal.get("completion_session_id") != completion.get("session_id")
        or verified_terminal.get("completion_request_sha256") != request_sha256
        or completion.get("request_sha256") != request_sha256
        or verified_terminal.get("completion_sha256") != canonical_sha256(completion)
        or verified_terminal.get("completion_lineage_sha256") != canonical_sha256(lineage)
        or verified_terminal.get("session_ids") != lineage.get("session_ids")
        or verified_terminal.get("root_session_id") != lineage.get("root_session_id")
        or verified_terminal.get("rework_count") != lineage.get("rework_count")
        or verified_terminal.get("followup_commit") != completion.get("followup_commit")
    ):
        raise CaptureError("bundle_invalid")
    candidate = {
        "schema_version": SCHEMA,
        "artifact_type": "candidate",
        "study_id": registration["study_id"],
        "registration_sha256": registration["registration_sha256"],
        "repo_id": repo_id,
        "repository_root_sha256": repository_root_sha256,
        "work_id": lineage["work_id"],
        "captured_at": verified_terminal.get("captured_at"),
        "terminal_sha256": canonical_sha256(terminal),
        "completion_sha256": canonical_sha256(completion),
        "completion_lineage_sha256": canonical_sha256(lineage),
        "raw_request_sha256": hashlib.sha256(raw_request).hexdigest(),
        "raw_output_sha256": hashlib.sha256(raw_output).hexdigest(),
        "terminal": terminal,
        "completion": completion,
        "lineage": lineage,
        "execution_receipt": verified_execution,
        "raw_request_base64": base64.b64encode(raw_request).decode("ascii"),
        "raw_output_base64": base64.b64encode(raw_output).decode("ascii"),
        "candidate_sha256": "",
    }
    if _timestamp(candidate["captured_at"]) < _timestamp(registration["frozen_at"]):
        raise CaptureError("bundle_invalid")
    return _sealed(candidate, "candidate_sha256")


def _validate_candidate(registration: dict[str, Any], candidate: object) -> dict[str, Any]:
    if (
        not isinstance(candidate, dict)
        or candidate.get("schema_version") != SCHEMA
        or candidate.get("artifact_type") != "candidate"
        or candidate.get("study_id") != registration["study_id"]
        or candidate.get("registration_sha256") != registration["registration_sha256"]
        or canonical_sha256({**candidate, "candidate_sha256": ""})
        != candidate.get("candidate_sha256")
    ):
        raise CaptureError("candidate_invalid")
    try:
        raw_request = base64.b64decode(candidate["raw_request_base64"], validate=True)
        raw_output = base64.b64decode(candidate["raw_output_base64"], validate=True)
        rebuilt = build_candidate(
            registration,
            repo_id=candidate["repo_id"],
            repository_root_sha256=candidate["repository_root_sha256"],
            terminal=candidate["terminal"],
            completion=candidate["completion"],
            lineage=candidate["lineage"],
            raw_request=raw_request,
            raw_output=raw_output,
            execution_receipt=candidate["execution_receipt"],
        )
    except (KeyError, TypeError, ValueError, CaptureError) as error:
        raise CaptureError("candidate_invalid") from error
    if rebuilt != candidate:
        raise CaptureError("candidate_invalid")
    return candidate


def build_reconciliation(
    registration: dict[str, Any],
    candidate: dict[str, Any],
    *,
    classification: str,
    reconciled_at: str,
    signer_private_key: Ed25519PrivateKey,
) -> dict[str, Any]:
    registration = _validate_registration(registration)
    candidate = _validate_candidate(registration, candidate)
    _require_collector_signer(registration, signer_private_key)
    if classification not in TAXONOMY:
        raise CaptureError("classification_invalid")
    _validate_reconciliation_timestamp(
        reconciled_at, captured_at=candidate["captured_at"]
    )
    receipt = {
        "schema_version": SCHEMA,
        "artifact_type": "reconciliation",
        "study_id": registration["study_id"],
        "registration_sha256": registration["registration_sha256"],
        "work_id": candidate["work_id"],
        "candidate_sha256": candidate["candidate_sha256"],
        "classification": classification,
        "primary_correction": classification in PRIMARY_CORRECTIONS,
        "reconciled_at": reconciled_at,
    }
    return _seal_signed(
        receipt,
        private_key=signer_private_key,
        signer="completion-observation-reconciliation-v0",
    )


def _candidate_inventory(candidate: dict[str, Any]) -> dict[str, Any]:
    return {
        "repo_id": candidate["repo_id"],
        "work_id": candidate["work_id"],
        "captured_at": candidate["captured_at"],
        "terminal_sha256": candidate["terminal_sha256"],
        "completion_sha256": candidate["completion_sha256"],
        "completion_lineage_sha256": candidate["completion_lineage_sha256"],
    }


def _require_collector_signer(
    registration: dict[str, Any], private_key: Ed25519PrivateKey
) -> None:
    if _public_key(private_key) != registration["collector_public_key"]:
        raise CaptureError("collector_signer_mismatch")


def _scan_state_streams(
    registration: dict[str, Any],
    repository_roots: dict[str, Path],
    closed_at: object,
) -> list[dict[str, Any]]:
    closed = _timestamp(closed_at)
    frozen = _timestamp(registration["frozen_at"])
    if set(repository_roots) != {
        item["repo_id"] for item in registration["repositories"]
    }:
        raise CaptureError("state_stream_incomplete")
    inventory: list[dict[str, Any]] = []
    try:
        for repository in registration["repositories"]:
            repo_id = repository["repo_id"]
            root = Path(repository_roots[repo_id]).resolve()
            if hashlib.sha256(str(root).encode()).hexdigest() != repository["root_sha256"]:
                raise CaptureError("state_stream_incomplete")
            sessions = root / ".omc" / "state" / "sessions"
            if sessions.is_symlink() or not sessions.is_dir():
                raise CaptureError("state_stream_incomplete")
            for terminal_path in sessions.glob("*/capture/terminal.json"):
                terminal = omc_state._verify_capture(
                    _read_json(terminal_path), repository["trusted_public_key"]
                )
                captured = _timestamp(terminal.get("captured_at"))
                if captured < frozen or captured > closed:
                    continue
                session_id = terminal.get("completion_session_id")
                if not isinstance(session_id, str) or not session_id:
                    raise CaptureError("state_stream_incomplete")
                session_dir = sessions / session_id
                completion = _read_json(session_dir / "completion.json")
                lineage = _read_json(session_dir / "completion-lineage.json")
                if (
                    terminal.get("status") != "COMPLETE"
                    or terminal.get("capture_validity") != "VALID"
                    or terminal.get("verification_passed") is not True
                    or terminal.get("work_id") != lineage.get("work_id")
                    or terminal.get("completion_session_id") != completion.get("session_id")
                    or terminal.get("completion_sha256") != canonical_sha256(completion)
                    or terminal.get("completion_lineage_sha256") != canonical_sha256(lineage)
                    or terminal.get("session_ids") != lineage.get("session_ids")
                    or terminal.get("root_session_id") != lineage.get("root_session_id")
                    or terminal.get("rework_count") != lineage.get("rework_count")
                    or terminal.get("followup_commit") != completion.get("followup_commit")
                ):
                    raise CaptureError("state_stream_incomplete")
                if completion.get("work_class") != registration["selection"]["eligible_work_class"]:
                    continue
                inventory.append(
                    {
                        "repo_id": repo_id,
                        "work_id": lineage["work_id"],
                        "captured_at": terminal["captured_at"],
                        "terminal_sha256": canonical_sha256(terminal),
                        "completion_sha256": canonical_sha256(completion),
                        "completion_lineage_sha256": canonical_sha256(lineage),
                    }
                )
    except (AttributeError, KeyError, OSError, TypeError, ValueError) as error:
        if isinstance(error, CaptureError):
            raise
        raise CaptureError("state_stream_incomplete") from error
    work_ids = [item["work_id"] for item in inventory]
    if len(work_ids) != len(set(work_ids)):
        raise CaptureError("state_stream_incomplete")
    return sorted(
        inventory,
        key=lambda item: (_timestamp(item["captured_at"]), item["repo_id"], item["work_id"]),
    )


def build_population_completeness_receipt(
    registration: dict[str, Any],
    candidates: list[dict[str, Any]],
    *,
    closed_at: str,
    signer_private_key: Ed25519PrivateKey,
    repository_roots: dict[str, Path],
) -> dict[str, Any]:
    registration = _validate_registration(registration)
    _require_collector_signer(registration, signer_private_key)
    checked = [_validate_candidate(registration, item) for item in candidates]
    inventory = _scan_state_streams(registration, repository_roots, closed_at)
    selected = inventory[: registration["selection"]["count"]]
    if len(selected) < registration["selection"]["count"] or [
        _candidate_inventory(item) for item in checked
    ] != selected:
        raise CaptureError("state_stream_incomplete")
    return _seal_signed(
        {
            "schema_version": SCHEMA,
            "artifact_type": "population_completeness",
            "study_id": registration["study_id"],
            "registration_sha256": registration["registration_sha256"],
            "candidate_sha256s": [item["candidate_sha256"] for item in checked],
            "state_inventory": inventory,
            "closed_at": closed_at,
        },
        private_key=signer_private_key,
        signer="completion-observation-population-v0",
    )


def build_report(
    registration: dict[str, Any],
    candidates: list[dict[str, Any]],
    reconciliations: list[dict[str, Any]],
    *,
    population_completeness_receipt: dict[str, Any] | None = None,
    approved_registration_sha256: str | None = None,
    repository_roots: dict[str, Path] | None = None,
) -> dict[str, Any]:
    registration = _validate_registration(registration)
    reason = None
    try:
        if approved_registration_sha256 != registration["registration_sha256"]:
            raise CaptureError("registration_approval_mismatch")
        try:
            population = _verify_signed(
                population_completeness_receipt,
                trusted_public_key=registration["collector_public_key"],
                signer="completion-observation-population-v0",
            )
        except CaptureError as error:
            raise CaptureError("population_completeness_required") from error
        if (
            population.get("schema_version") != SCHEMA
            or population.get("artifact_type") != "population_completeness"
            or population.get("study_id") != registration["study_id"]
            or population.get("registration_sha256")
            != registration["registration_sha256"]
            or population.get("candidate_sha256s")
            != [item.get("candidate_sha256") for item in candidates]
        ):
            raise CaptureError("population_completeness_invalid")
        inventory = _scan_state_streams(
            registration, repository_roots or {}, population.get("closed_at")
        )
        if population.get("state_inventory") != inventory:
            raise CaptureError("population_completeness_invalid")
        checked = [_validate_candidate(registration, item) for item in candidates]
        work_ids = [item["work_id"] for item in checked]
        candidates_by_work = {item["work_id"]: item for item in checked}
        if len(checked) != 10:
            raise CaptureError("eligible_count_incomplete")
        if len(set(work_ids)) != len(work_ids):
            raise CaptureError("candidate_duplicate")
        if [_candidate_inventory(item) for item in checked] != inventory[:10]:
            raise CaptureError("candidate_order_invalid")
        if len({item["repo_id"] for item in checked}) < 2:
            raise CaptureError("repository_coverage_incomplete")
        by_work: dict[str, dict[str, Any]] = {}
        for item in reconciliations:
            try:
                verified_reconciliation = _verify_signed(
                    item,
                    trusted_public_key=registration["collector_public_key"],
                    signer="completion-observation-reconciliation-v0",
                )
            except CaptureError as error:
                raise CaptureError("reconciliation_invalid") from error
            if (
                verified_reconciliation.get("schema_version") != SCHEMA
                or verified_reconciliation.get("artifact_type") != "reconciliation"
                or verified_reconciliation.get("study_id") != registration["study_id"]
                or verified_reconciliation.get("registration_sha256")
                != registration["registration_sha256"]
                or item.get("classification") not in TAXONOMY
                or item.get("primary_correction")
                != (item.get("classification") in PRIMARY_CORRECTIONS)
                or item.get("work_id") not in candidates_by_work
                or item.get("work_id") in by_work
            ):
                raise CaptureError("reconciliation_invalid")
            try:
                _validate_reconciliation_timestamp(
                    item.get("reconciled_at"),
                    captured_at=candidates_by_work[item["work_id"]]["captured_at"],
                    closed_at=population.get("closed_at"),
                )
            except CaptureError as error:
                raise CaptureError("reconciliation_invalid") from error
            by_work[item["work_id"]] = item
        if set(by_work) != set(work_ids) or any(
            by_work[item["work_id"]].get("candidate_sha256") != item["candidate_sha256"]
            for item in checked
        ):
            raise CaptureError("reconciliation_incomplete")
    except (AttributeError, CaptureError, KeyError, TypeError) as error:
        reason = str(error)
        checked = []
    return {
        "schema_version": SCHEMA,
        "artifact_type": "report",
        "study_id": registration["study_id"],
        "decision": "CAPTURE_FEASIBLE" if reason is None else "CAPTURE_INCOMPLETE",
        "reason": reason,
        "selected_count": len(checked),
        "repository_count": len({item["repo_id"] for item in checked}),
        "primary_correction_count": sum(
            1 for item in reconciliations if item.get("primary_correction") is True
        ) if reason is None else 0,
        "claim_boundary": "CAPTURE_FEASIBILITY_ONLY",
    }


def _read_json(path: Path) -> Any:
    if path.is_symlink() or not path.is_file():
        raise CaptureError("input_not_regular_file")
    return json.loads(path.read_text(encoding="utf-8"))


def _write_once(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.parent / f".{path.name}.{uuid.uuid4().hex}.tmp"
    descriptor: int | None = None
    try:
        descriptor = os.open(
            temporary,
            os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0),
            0o600,
        )
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            descriptor = None
            json.dump(value, handle, ensure_ascii=False, indent=2, sort_keys=True)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.link(temporary, path, follow_symlinks=False)
        directory_fd = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)
    finally:
        if descriptor is not None:
            os.close(descriptor)
        temporary.unlink(missing_ok=True)


def _git_text(project_root: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", "-C", str(project_root), *args],
        check=False,
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        raise CaptureError("git_state_invalid")
    return result.stdout


def _live_root(project_root: Path, work_id: str) -> Path:
    return project_root / ".omc" / "observations" / "live" / work_id


def enable_live_observation(
    project_root: Path, *, study_id: str, executor_surface: str
) -> dict[str, Any]:
    project_root = project_root.resolve()
    if not study_id.strip() or executor_surface != "codex":
        raise CaptureError("live_observation_policy_invalid")
    policy = _live_record(
        {
            "schema_version": LIVE_SCHEMA,
            "artifact_type": "policy",
            "claim_boundary": "CAPTURE_FEASIBILITY_ONLY",
            "enabled": True,
            "study_id": study_id,
            "executor_surface": executor_surface,
            "repository_root_sha256": hashlib.sha256(str(project_root).encode()).hexdigest(),
            "enabled_at": datetime.now().astimezone().isoformat(),
        },
        "policy_sha256",
    )
    _write_once(project_root / ".omc" / "observation-policy.json", policy)
    return policy


def _live_policy(project_root: Path) -> dict[str, Any]:
    try:
        policy = _validate_live_record(
            _read_json(project_root / ".omc" / "observation-policy.json"),
            artifact_type="policy",
            hash_field="policy_sha256",
        )
    except (CaptureError, OSError, ValueError, json.JSONDecodeError) as error:
        raise CaptureError("live_observation_not_enabled") from error
    if (
        policy.get("enabled") is not True
        or policy.get("claim_boundary") != "CAPTURE_FEASIBILITY_ONLY"
        or policy.get("executor_surface") != "codex"
        or policy.get("repository_root_sha256")
        != hashlib.sha256(str(project_root).encode()).hexdigest()
    ):
        raise CaptureError("live_observation_not_enabled")
    return policy


def _live_pending(project_root: Path) -> dict[str, Any]:
    path = project_root / ".omc" / "state" / "pending-completion.json"
    try:
        pending = _read_json(path)
    except (CaptureError, OSError, ValueError, json.JSONDecodeError) as error:
        raise CaptureError("pending_completion_invalid") from error
    required = {
        "schema_version", "session_id", "baseline_head", "request_sha256",
        "work_class", "work_class_locked_at", "work_id", "root_session_id",
        "session_ids", "rework_count",
    }
    session_ids = pending.get("session_ids") if isinstance(pending, dict) else None
    current_head = _git_text(project_root, "rev-parse", "HEAD").strip()
    if (
        not isinstance(pending, dict)
        or set(pending) != required
        or pending.get("schema_version") != 3
        or pending.get("work_class") != "implementation"
        or not isinstance(pending.get("work_id"), str)
        or re.fullmatch(r"[0-9a-f]{32}", pending["work_id"]) is None
        or not isinstance(session_ids, list)
        or not session_ids
        or pending.get("root_session_id") != session_ids[0]
        or pending.get("session_id") != session_ids[-1]
        or pending.get("rework_count") != len(session_ids) - 1
        or len(session_ids) != len(set(session_ids))
        or current_head != pending.get("baseline_head")
        or not omc_state._pending_completion_matches_session(
            project_root, pending, current_head=current_head
        )
    ):
        raise CaptureError("pending_completion_invalid")
    return pending


def _live_record(value: dict[str, Any], hash_field: str) -> dict[str, Any]:
    return _sealed({**value, hash_field: ""}, hash_field)


def _validate_live_record(value: object, *, artifact_type: str, hash_field: str) -> dict[str, Any]:
    if (
        not isinstance(value, dict)
        or value.get("schema_version") != LIVE_SCHEMA
        or value.get("artifact_type") != artifact_type
        or canonical_sha256({**value, hash_field: ""}) != value.get(hash_field)
    ):
        raise CaptureError("live_observation_invalid")
    return value


def start_live_observation(project_root: Path) -> dict[str, Any]:
    project_root = project_root.resolve()
    policy = _live_policy(project_root)
    pending = _live_pending(project_root)
    root = _live_root(project_root, pending["work_id"])
    path = root / "start.json"
    if path.exists():
        existing = _validate_live_record(
            _read_json(path), artifact_type="start", hash_field="start_sha256"
        )
        if (
            existing.get("work_id") != pending["work_id"]
            or existing.get("baseline_commit") != pending["baseline_head"]
        ):
            raise CaptureError("live_observation_binding_mismatch")
        return existing
    record = _live_record(
        {
            "schema_version": LIVE_SCHEMA,
            "artifact_type": "start",
            "claim_boundary": "OBSERVATION_ONLY",
            "study_id": policy["study_id"],
            "status": "COLLECTING",
            "work_id": pending["work_id"],
            "root_session_id": pending["root_session_id"],
            "session_ids": pending["session_ids"],
            "baseline_commit": pending["baseline_head"],
            "repository_root_sha256": hashlib.sha256(str(project_root).encode()).hexdigest(),
            "started_at": datetime.now().astimezone().isoformat(),
        },
        "start_sha256",
    )
    _write_once(path, record)
    return record


def live_observation_status(project_root: Path) -> dict[str, Any]:
    project_root = project_root.resolve()
    pending = _live_pending(project_root)
    root = _live_root(project_root, pending["work_id"])
    start_path = root / "start.json"
    if not start_path.is_file():
        live_parent = root.parent
        reason = (
            "live_observation_binding_mismatch"
            if live_parent.is_dir() and any(live_parent.glob("*/start.json"))
            else "live_observation_not_started"
        )
        raise CaptureError(reason)
    start = _validate_live_record(
        _read_json(start_path), artifact_type="start", hash_field="start_sha256"
    )
    if (
        start.get("work_id") != pending["work_id"]
        or start.get("baseline_commit") != pending["baseline_head"]
        or start.get("repository_root_sha256")
        != hashlib.sha256(str(project_root).encode()).hexdigest()
        or pending["root_session_id"] != start.get("root_session_id")
    ):
        raise CaptureError("live_observation_binding_mismatch")
    terminal = root / "terminal.json"
    completion = root / "first-completion.json"
    if completion.exists():
        completion_record = _validate_live_record(
            _read_json(completion),
            artifact_type="first_completion",
            hash_field="completion_snapshot_sha256",
        )
        if completion_record.get("work_id") != pending["work_id"]:
            raise CaptureError("live_observation_binding_mismatch")
    followups = sorted(root.glob("followup-*.json"))
    if [item.name for item in followups] != [
        f"followup-{index:03d}.json" for index in range(1, len(followups) + 1)
    ]:
        raise CaptureError("live_observation_invalid")
    for followup in followups:
        event = _validate_live_record(
            _read_json(followup), artifact_type="followup", hash_field="followup_sha256"
        )
        if event.get("work_id") != pending["work_id"]:
            raise CaptureError("live_observation_binding_mismatch")
    classifications = sorted(root.glob("classification-*.json"))
    if [item.name for item in classifications] != [
        f"classification-{index:03d}.json"
        for index in range(1, len(classifications) + 1)
    ]:
        raise CaptureError("live_observation_invalid")
    for index, classification_path in enumerate(classifications, 1):
        classification = _validate_live_record(
            _read_json(classification_path),
            artifact_type="classification",
            hash_field="classification_sha256",
        )
        if index > len(followups):
            raise CaptureError("live_observation_invalid")
        followup = _validate_live_record(
            _read_json(followups[index - 1]),
            artifact_type="followup",
            hash_field="followup_sha256",
        )
        try:
            raw_confirmation = base64.b64decode(
                classification.get("raw_confirmation_base64"), validate=True
            )
        except (TypeError, ValueError) as error:
            raise CaptureError("live_observation_invalid") from error
        if (
            classification.get("work_id") != pending["work_id"]
            or classification.get("followup_sha256") != followup["followup_sha256"]
            or classification.get("classification") not in TAXONOMY
            or classification.get("primary_correction")
            is not (classification.get("classification") in PRIMARY_CORRECTIONS)
            or not raw_confirmation.strip()
            or hashlib.sha256(raw_confirmation).hexdigest()
            != classification.get("raw_confirmation_sha256")
        ):
            raise CaptureError("live_observation_binding_mismatch")
    if terminal.exists():
        event = _validate_live_record(
            _read_json(terminal), artifact_type="outcome", hash_field="outcome_sha256"
        )
        if (
            event.get("work_id") != pending["work_id"]
            or event.get("outcome") not in {"accepted", "deferred"}
        ):
            raise CaptureError("live_observation_binding_mismatch")
        if len(classifications) != len(followups):
            raise CaptureError("classification_required")
    status = "CLOSED" if terminal.exists() else (
        "AWAITING_USER_OUTCOME" if completion.exists() else "COLLECTING"
    )
    return {
        "schema_version": LIVE_SCHEMA,
        "claim_boundary": "OBSERVATION_ONLY",
        "status": status,
        "work_id": pending["work_id"],
        "session_ids": pending["session_ids"],
    }


def _changed_paths(project_root: Path) -> list[str]:
    def git_paths(*args: str) -> list[str]:
        result = subprocess.run(
            ["git", "-C", str(project_root), *args],
            check=False,
            capture_output=True,
        )
        if result.returncode != 0:
            raise CaptureError("git_state_invalid")
        try:
            return [item.decode("utf-8") for item in result.stdout.split(b"\0") if item]
        except UnicodeDecodeError as error:
            raise CaptureError("changed_path_invalid") from error

    tracked = git_paths("diff", "--name-only", "-z", "HEAD", "--")
    untracked = git_paths(
        "ls-files", "--others", "--exclude-standard", "-z", "--"
    )
    return sorted(
        path for path in set(tracked + untracked)
        if path and path != ".omc" and not path.startswith(".omc/")
    )


def capture_live_completion(
    project_root: Path,
    *,
    raw_report: bytes,
    raw_verification: bytes,
    unrun_items: list[str],
) -> dict[str, Any]:
    project_root = project_root.resolve()
    status = live_observation_status(project_root)
    if status["status"] != "COLLECTING":
        raise CaptureError("first_completion_already_recorded")
    if not raw_report.strip() or not raw_verification.strip():
        raise CaptureError("first_completion_evidence_required")
    if any(not isinstance(item, str) or not item.strip() for item in unrun_items):
        raise CaptureError("unrun_items_invalid")
    pending = _live_pending(project_root)
    paths = _changed_paths(project_root)
    if not paths:
        raise CaptureError("first_completion_changes_required")
    files: list[dict[str, Any]] = []
    for relative in paths:
        path = project_root / relative
        if path.is_symlink():
            raise CaptureError("changed_path_invalid")
        if path.is_file():
            data = path.read_bytes()
            files.append({"path": relative, "state": "present", "byte_length": len(data), "sha256": hashlib.sha256(data).hexdigest()})
        elif not path.exists():
            files.append({"path": relative, "state": "deleted", "byte_length": 0, "sha256": None})
        else:
            raise CaptureError("changed_path_invalid")
    record = _live_record(
        {
            "schema_version": LIVE_SCHEMA,
            "artifact_type": "first_completion",
            "claim_boundary": "OBSERVATION_ONLY",
            "status": "AWAITING_USER_OUTCOME",
            "work_id": pending["work_id"],
            "baseline_commit": pending["baseline_head"],
            "commit_bound": False,
            "changed_paths": paths,
            "file_snapshots": files,
            "raw_report_base64": base64.b64encode(raw_report).decode("ascii"),
            "raw_report_sha256": hashlib.sha256(raw_report).hexdigest(),
            "raw_verification_base64": base64.b64encode(raw_verification).decode("ascii"),
            "raw_verification_sha256": hashlib.sha256(raw_verification).hexdigest(),
            "unrun_items": unrun_items,
            "captured_at": datetime.now().astimezone().isoformat(),
        },
        "completion_snapshot_sha256",
    )
    _write_once(_live_root(project_root, pending["work_id"]) / "first-completion.json", record)
    return record


def record_live_outcome(
    project_root: Path,
    *,
    outcome: str,
    raw_followup: bytes,
    classification: str | None = None,
) -> dict[str, Any]:
    project_root = project_root.resolve()
    status = live_observation_status(project_root)
    if status["status"] == "COLLECTING":
        raise CaptureError("first_completion_required")
    if status["status"] == "CLOSED":
        raise CaptureError("outcome_already_recorded")
    if outcome not in {"accepted", "correction_required", "deferred"} or not raw_followup.strip():
        raise CaptureError("outcome_invalid")
    if outcome == "correction_required" and classification not in TAXONOMY:
        raise CaptureError("classification_required")
    if outcome != "correction_required" and classification is not None:
        raise CaptureError("classification_invalid")
    pending = _live_pending(project_root)
    root = _live_root(project_root, pending["work_id"])
    if outcome == "correction_required":
        followup = _record_live_followup(project_root, raw_prompt=raw_followup)
        classified = classify_live_followup(
            project_root,
            followup_index=followup["followup_index"],
            classification=str(classification),
            raw_confirmation=raw_followup,
        )
        return {**followup, **classified, "outcome": outcome}
    if len(list(root.glob("classification-*.json"))) != len(
        list(root.glob("followup-*.json"))
    ):
        raise CaptureError("classification_required")
    record = _live_record(
        {
            "schema_version": LIVE_SCHEMA,
            "artifact_type": "outcome",
            "claim_boundary": "OBSERVATION_ONLY",
            "work_id": pending["work_id"],
            "outcome": outcome,
            "classification": classification,
            "raw_followup_base64": base64.b64encode(raw_followup).decode("ascii"),
            "raw_followup_sha256": hashlib.sha256(raw_followup).hexdigest(),
            "recorded_at": datetime.now().astimezone().isoformat(),
        },
        "outcome_sha256",
    )
    _write_once(root / "terminal.json", record)
    return record


def _record_live_followup(project_root: Path, *, raw_prompt: bytes) -> dict[str, Any]:
    if not raw_prompt.strip():
        raise CaptureError("followup_invalid")
    pending = _live_pending(project_root)
    root = _live_root(project_root, pending["work_id"])
    index = len(list(root.glob("followup-*.json"))) + 1
    record = _live_record(
        {
            "schema_version": LIVE_SCHEMA,
            "artifact_type": "followup",
            "claim_boundary": "OBSERVATION_ONLY",
            "work_id": pending["work_id"],
            "followup_index": index,
            "classification_status": "pending_user_confirmation",
            "raw_followup_base64": base64.b64encode(raw_prompt).decode("ascii"),
            "raw_followup_sha256": hashlib.sha256(raw_prompt).hexdigest(),
            "recorded_at": datetime.now().astimezone().isoformat(),
        },
        "followup_sha256",
    )
    _write_once(root / f"followup-{index:03d}.json", record)
    return record


def classify_live_followup(
    project_root: Path,
    *,
    followup_index: int,
    classification: str,
    raw_confirmation: bytes,
) -> dict[str, Any]:
    project_root = project_root.resolve()
    status = live_observation_status(project_root)
    if status["status"] == "CLOSED":
        raise CaptureError("outcome_already_recorded")
    if (
        isinstance(followup_index, bool)
        or followup_index < 1
        or classification not in TAXONOMY
        or not raw_confirmation.strip()
    ):
        raise CaptureError("classification_invalid")
    pending = _live_pending(project_root)
    root = _live_root(project_root, pending["work_id"])
    followup = _validate_live_record(
        _read_json(root / f"followup-{followup_index:03d}.json"),
        artifact_type="followup",
        hash_field="followup_sha256",
    )
    record = _live_record(
        {
            "schema_version": LIVE_SCHEMA,
            "artifact_type": "classification",
            "claim_boundary": "OBSERVATION_ONLY",
            "work_id": pending["work_id"],
            "followup_index": followup_index,
            "followup_sha256": followup["followup_sha256"],
            "classification": classification,
            "primary_correction": classification in PRIMARY_CORRECTIONS,
            "raw_confirmation_base64": base64.b64encode(raw_confirmation).decode("ascii"),
            "raw_confirmation_sha256": hashlib.sha256(raw_confirmation).hexdigest(),
            "classified_at": datetime.now().astimezone().isoformat(),
        },
        "classification_sha256",
    )
    _write_once(root / f"classification-{followup_index:03d}.json", record)
    return record


def _is_skill_control_prompt(value: str) -> bool:
    stripped = value.strip()
    return bool(
        re.fullmatch(r"\$?omc-[a-z-]+", stripped)
        or re.fullmatch(r"/[-a-z]+(?:\s+.*)?", stripped)
        or re.fullmatch(r"\[\$?omc-[^\]]+\]\([^)]*\)", stripped)
    )


def record_live_prompt(
    project_root: Path, *, raw_prompt: bytes, executor_surface: str
) -> dict[str, Any]:
    project_root = project_root.resolve()
    policy = _live_policy(project_root)
    if executor_surface != policy["executor_surface"]:
        raise CaptureError("live_observation_executor_mismatch")
    status = live_observation_status(project_root)
    if status["status"] != "AWAITING_USER_OUTCOME":
        raise CaptureError("live_prompt_not_expected")
    try:
        text = raw_prompt.decode("utf-8")
    except UnicodeDecodeError as error:
        raise CaptureError("followup_invalid") from error
    if _is_skill_control_prompt(text):
        return {
            "schema_version": LIVE_SCHEMA,
            "claim_boundary": "OBSERVATION_ONLY",
            "status": "CONTROL_PROMPT_IGNORED",
        }
    normalized = " ".join(text.strip().lower().split())
    classification_aliases = {
        "defect_correction": "defect_correction",
        "결함 수정": "defect_correction",
        "missing_requirement": "missing_requirement",
        "요구사항 누락": "missing_requirement",
        "persona_mismatch": "persona_mismatch",
        "페르소나 불일치": "persona_mismatch",
        "scope_change": "scope_change",
        "범위 변경": "scope_change",
        "clarification": "clarification",
        "설명 요청": "clarification",
        "preference": "preference",
        "선호 변경": "preference",
    }
    pending = _live_pending(project_root)
    root = _live_root(project_root, pending["work_id"])
    unclassified_index = len(list(root.glob("classification-*.json"))) + 1
    followup_count = len(list(root.glob("followup-*.json")))
    if unclassified_index <= followup_count and normalized in classification_aliases:
        result = classify_live_followup(
            project_root,
            followup_index=unclassified_index,
            classification=classification_aliases[normalized],
            raw_confirmation=raw_prompt,
        )
        return {**result, "status": "CLASSIFICATION_RECORDED"}
    if normalized in {"수용", "accept", "accepted"}:
        result = record_live_outcome(
            project_root, outcome="accepted", raw_followup=raw_prompt
        )
        return {**result, "status": "OUTCOME_RECORDED"}
    if normalized in {"보류", "defer", "deferred"}:
        result = record_live_outcome(
            project_root, outcome="deferred", raw_followup=raw_prompt
        )
        return {**result, "status": "OUTCOME_RECORDED"}
    result = _record_live_followup(project_root, raw_prompt=raw_prompt)
    return {**result, "status": "FOLLOWUP_RECORDED"}


def _repository_roots(values: list[str]) -> dict[str, Path]:
    roots: dict[str, Path] = {}
    for value in values:
        try:
            repo_id, path = value.split("=", 1)
        except ValueError as error:
            raise CaptureError("repository_roots_invalid") from error
        if not repo_id or not path or repo_id in roots:
            raise CaptureError("repository_roots_invalid")
        roots[repo_id] = Path(path)
    return roots


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    register = sub.add_parser("register")
    register.add_argument("--study-id", required=True)
    register.add_argument("--frozen-at", required=True)
    register.add_argument("--repositories", type=Path, required=True)
    register.add_argument("--collector-public-key")
    register.add_argument("--trusted-execution-public-key")
    candidate = sub.add_parser("candidate")
    for command in (candidate,):
        command.add_argument("--registration", type=Path, required=True)
    candidate.add_argument("--repo-id", required=True)
    candidate.add_argument("--repository-root-sha256", required=True)
    candidate.add_argument("--terminal", type=Path, required=True)
    candidate.add_argument("--completion", type=Path, required=True)
    candidate.add_argument("--lineage", type=Path, required=True)
    candidate.add_argument("--raw-request", type=Path, required=True)
    candidate.add_argument("--raw-output", type=Path, required=True)
    candidate.add_argument("--execution-receipt", type=Path, required=True)
    reconcile = sub.add_parser("reconcile")
    reconcile.add_argument("--target", type=Path, default=Path.cwd())
    reconcile.add_argument("--registration", type=Path, required=True)
    reconcile.add_argument("--candidate", type=Path, required=True)
    reconcile.add_argument("--classification", choices=TAXONOMY, required=True)
    reconcile.add_argument("--reconciled-at", required=True)
    population = sub.add_parser("population-close")
    population.add_argument("--target", type=Path, default=Path.cwd())
    population.add_argument("--registration", type=Path, required=True)
    population.add_argument("--candidates", type=Path, required=True)
    population.add_argument("--closed-at", required=True)
    population.add_argument("--repository-root", action="append", required=True)
    report = sub.add_parser("report")
    report.add_argument("--registration", type=Path, required=True)
    report.add_argument("--candidates", type=Path, required=True)
    report.add_argument("--reconciliations", type=Path, required=True)
    report.add_argument("--population-completeness-receipt", type=Path, required=True)
    report.add_argument("--approved-registration-sha256", required=True)
    report.add_argument("--repository-root", action="append", required=True)
    live_start = sub.add_parser("live-start")
    live_enable = sub.add_parser("live-enable")
    live_status = sub.add_parser("live-status")
    live_capture = sub.add_parser("live-capture")
    live_outcome = sub.add_parser("live-outcome")
    live_prompt = sub.add_parser("live-prompt")
    live_classify = sub.add_parser("live-classify")
    for command in (
        live_enable, live_start, live_status, live_capture, live_outcome,
        live_prompt, live_classify,
    ):
        command.add_argument("--target", type=Path, default=Path.cwd())
    live_enable.add_argument("--study-id", required=True)
    live_enable.add_argument("--executor-surface", choices=["codex"], required=True)
    live_prompt.add_argument("--executor-surface", required=True)
    live_capture.add_argument("--report", type=Path, required=True)
    live_capture.add_argument("--verification-output", type=Path, required=True)
    live_capture.add_argument("--unrun", action="append", default=[])
    live_outcome.add_argument(
        "--outcome", choices=["accepted", "correction_required", "deferred"], required=True
    )
    live_outcome.add_argument("--followup", type=Path, required=True)
    live_outcome.add_argument("--classification", choices=TAXONOMY)
    live_classify.add_argument("--followup-index", type=int, required=True)
    live_classify.add_argument("--classification", choices=TAXONOMY, required=True)
    live_classify.add_argument("--confirmation", type=Path, required=True)
    for command in (register, candidate, reconcile, population, report):
        command.add_argument("--out", type=Path, required=True)
    return parser


def main() -> int:
    args = _parser().parse_args()
    live_command = args.command.startswith("live-")
    try:
        if args.command == "live-enable":
            result = enable_live_observation(
                args.target,
                study_id=args.study_id,
                executor_surface=args.executor_surface,
            )
        elif args.command == "live-start":
            result = start_live_observation(args.target)
        elif args.command == "live-status":
            result = live_observation_status(args.target)
        elif args.command == "live-capture":
            result = capture_live_completion(
                args.target,
                raw_report=args.report.read_bytes(),
                raw_verification=args.verification_output.read_bytes(),
                unrun_items=args.unrun,
            )
        elif args.command == "live-outcome":
            result = record_live_outcome(
                args.target,
                outcome=args.outcome,
                raw_followup=args.followup.read_bytes(),
                classification=args.classification,
            )
        elif args.command == "live-prompt":
            encoded_prompt = os.environ.get("OMC_LIVE_PROMPT_BASE64")
            if encoded_prompt is None:
                raise CaptureError("followup_invalid")
            try:
                raw_prompt = base64.b64decode(encoded_prompt, validate=True)
            except (ValueError, binascii.Error) as error:
                raise CaptureError("followup_invalid") from error
            result = record_live_prompt(
                args.target,
                raw_prompt=raw_prompt,
                executor_surface=args.executor_surface,
            )
        elif args.command == "live-classify":
            result = classify_live_followup(
                args.target,
                followup_index=args.followup_index,
                classification=args.classification,
                raw_confirmation=args.confirmation.read_bytes(),
            )
        elif args.command == "register":
            result = build_registration(
                study_id=args.study_id,
                frozen_at=args.frozen_at,
                repositories=_read_json(args.repositories),
                collector_public_key=args.collector_public_key,
                trusted_execution_public_key=args.trusted_execution_public_key
                or os.environ.get("OMC_TASK_REVIEW_PILOT_TRUSTED_EXECUTION_PUBLIC_KEY"),
            )
        elif args.command == "candidate":
            result = build_candidate(
                _read_json(args.registration),
                repo_id=args.repo_id,
                repository_root_sha256=args.repository_root_sha256,
                terminal=_read_json(args.terminal),
                completion=_read_json(args.completion),
                lineage=_read_json(args.lineage),
                raw_request=args.raw_request.read_bytes(),
                raw_output=args.raw_output.read_bytes(),
                execution_receipt=_read_json(args.execution_receipt),
            )
        elif args.command == "reconcile":
            registration = _validate_registration(_read_json(args.registration))
            candidate_document = _validate_candidate(
                registration, _read_json(args.candidate)
            )
            private_key, _ = omc_state._capture_keys(args.target.resolve())
            result = build_reconciliation(
                registration,
                candidate_document,
                classification=args.classification,
                reconciled_at=args.reconciled_at,
                signer_private_key=private_key,
            )
        elif args.command == "population-close":
            private_key, _ = omc_state._capture_keys(args.target.resolve())
            result = build_population_completeness_receipt(
                _read_json(args.registration),
                _read_json(args.candidates),
                closed_at=args.closed_at,
                signer_private_key=private_key,
                repository_roots=_repository_roots(args.repository_root),
            )
        else:
            result = build_report(
                _read_json(args.registration),
                _read_json(args.candidates),
                _read_json(args.reconciliations),
                population_completeness_receipt=_read_json(
                    args.population_completeness_receipt
                ),
                approved_registration_sha256=args.approved_registration_sha256,
                repository_roots=_repository_roots(args.repository_root),
            )
        if not live_command:
            _write_once(args.out, result)
    except (
        CaptureError,
        AttributeError,
        FileExistsError,
        KeyError,
        OSError,
        TypeError,
        UnicodeDecodeError,
        ValueError,
        json.JSONDecodeError,
    ) as error:
        if live_command:
            print(json.dumps({
                "claim_boundary": "OBSERVATION_ONLY",
                "reason": str(error),
                "status": "OBSERVATION_INVALID",
            }, ensure_ascii=False, sort_keys=True))
            return 0
        print(json.dumps({"status": "BLOCKED", "reason": str(error)}, ensure_ascii=False))
        return 2
    print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    return 0 if result.get("decision") != "CAPTURE_INCOMPLETE" else 2


if __name__ == "__main__":
    raise SystemExit(main())
