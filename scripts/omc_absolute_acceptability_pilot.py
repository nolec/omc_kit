#!/usr/bin/env python3
"""Fail-closed evaluator for the v3 absolute acceptability pilot."""

from __future__ import annotations

import argparse
import base64
import hashlib
import json
import stat
import subprocess
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import (
    Ed25519PrivateKey,
    Ed25519PublicKey,
)

import omc_state
import omc_plan_candidate_universe as candidate_universe


SCHEMA = "omc-absolute-acceptability/v3"
EXECUTION_RECEIPT_SCHEMA = "omc-codex-jsonl-execution/v1"
PRIMARY_TAXONOMY = {
    "defect_correction",
    "missing_requirement",
    "persona_mismatch",
    "ambiguous",
}
NON_PRIMARY_TAXONOMY = {"scope_change", "clarification", "preference"}
INVENTORY_SCHEMA = "omc-absolute-acceptability-inventory/v1"
CASE_SCHEMA = "omc-absolute-acceptability-case/v1"
SOURCE_POPULATION_SCHEMA = "omc-absolute-acceptability-source-population/v1"
SOURCE_CASE_SCHEMA = "omc-absolute-acceptability-source-case/v1"
EXECUTOR_START_SCHEMA = "omc-absolute-acceptability-executor-start/v1"
EXECUTOR_START_MAX_DELAY = timedelta(seconds=60)


class PilotEvidenceError(ValueError):
    """Evidence is malformed or cannot support a study conclusion."""


def _canonical_bytes(value: object) -> bytes:
    return json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")


def _sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def canonical_sha256(value: object) -> str:
    return _sha256_bytes(_canonical_bytes(value))


def _public_key(private_key: Ed25519PrivateKey) -> str:
    return base64.b64encode(private_key.public_key().public_bytes_raw()).decode()


def _seal_evidence(
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
    digest_payload = {
        **result,
        "signoff": {**result["signoff"], "signature": ""},
    }
    result["receipt_sha256"] = canonical_sha256(digest_payload)
    signature_payload = {
        **result,
        "signoff": {**result["signoff"], "signature": ""},
    }
    result["signoff"]["signature"] = base64.b64encode(
        private_key.sign(_canonical_bytes(signature_payload))
    ).decode()
    return result


def _verify_evidence(
    document: object,
    *,
    trusted_public_key: str,
    signer: str,
    reason: str,
    detached_fields: set[str] | None = None,
) -> dict[str, Any]:
    if not isinstance(document, dict) or not isinstance(document.get("signoff"), dict):
        raise PilotEvidenceError(reason)
    subject = {
        key: value
        for key, value in document.items()
        if key not in (detached_fields or set())
    }
    signoff = subject.get("signoff")
    if not isinstance(signoff, dict):
        raise PilotEvidenceError(reason)
    digest_payload = {
        **subject,
        "receipt_sha256": "",
        "signoff": {**signoff, "signature": ""},
    }
    signature_payload = {
        **subject,
        "signoff": {**signoff, "signature": ""},
    }
    if (
        signoff.get("algorithm") != "ed25519"
        or signoff.get("signer") != signer
        or signoff.get("signer_public_key") != trusted_public_key
        or subject.get("receipt_sha256") != canonical_sha256(digest_payload)
    ):
        raise PilotEvidenceError(reason)
    try:
        Ed25519PublicKey.from_public_bytes(
            base64.b64decode(trusted_public_key, validate=True)
        ).verify(
            base64.b64decode(str(signoff.get("signature")), validate=True),
            _canonical_bytes(signature_payload),
        )
    except (InvalidSignature, TypeError, ValueError) as error:
        raise PilotEvidenceError(reason) from error
    return document


def _parse_timestamp(value: object, reason: str) -> datetime:
    if not isinstance(value, str):
        raise PilotEvidenceError(reason)
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as error:
        raise PilotEvidenceError(reason) from error
    if parsed.tzinfo is None:
        raise PilotEvidenceError(reason)
    return parsed


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def load_custody_private_key(
    path: Path, *, repository_root: Path
) -> Ed25519PrivateKey:
    """Load an Ed25519 key only from private, out-of-repository custody."""
    resolved_path = path.expanduser().resolve()
    resolved_root = repository_root.resolve()
    if resolved_path == resolved_root or resolved_root in resolved_path.parents:
        raise PilotEvidenceError("custody_key_inside_repository")
    try:
        mode = stat.S_IMODE(resolved_path.stat().st_mode)
        encoded = resolved_path.read_text(encoding="utf-8").strip()
        raw = base64.b64decode(encoded, validate=True)
    except (OSError, ValueError) as error:
        raise PilotEvidenceError("custody_key_invalid") from error
    if mode & 0o077:
        raise PilotEvidenceError("custody_key_permissions_invalid")
    try:
        return Ed25519PrivateKey.from_private_bytes(raw)
    except ValueError as error:
        raise PilotEvidenceError("custody_key_invalid") from error


def build_execution_receipt(
    *,
    private_key: Ed25519PrivateKey,
    work_id: str,
    request: str,
    raw_jsonl: str,
    raw_stderr: str = "",
    exit_code: int,
    produced_at: str,
    executor_start_receipt_sha256: str,
) -> dict[str, Any]:
    """Seal the exact Codex CLI request and JSONL output after execution."""
    if (
        not work_id
        or not request
        or not isinstance(raw_jsonl, str)
        or not isinstance(raw_stderr, str)
        or not isinstance(executor_start_receipt_sha256, str)
        or len(executor_start_receipt_sha256) != 64
        or any(
            character not in "0123456789abcdef"
            for character in executor_start_receipt_sha256
        )
    ):
        raise PilotEvidenceError("execution_receipt_invalid")
    _parse_timestamp(produced_at, "execution_receipt_invalid")
    document: dict[str, Any] = {
        "schema_version": EXECUTION_RECEIPT_SCHEMA,
        "executor": "codex_cli",
        "transport": "exec_json",
        "work_id": work_id,
        "request": request,
        "request_sha256": canonical_sha256(request),
        "raw_jsonl": raw_jsonl,
        "raw_jsonl_sha256": _sha256_bytes(raw_jsonl.encode("utf-8")),
        "raw_stderr": raw_stderr,
        "raw_stderr_sha256": _sha256_bytes(raw_stderr.encode("utf-8")),
        "exit_code": exit_code,
        "produced_at": produced_at,
        "executor_start_receipt_sha256": executor_start_receipt_sha256,
        "receipt_sha256": "",
        "signoff": {
            "algorithm": "ed25519",
            "signer": "codex_cli_execution_sidecar",
            "signer_public_key": _public_key(private_key),
            "signature": "",
        },
    }
    digest_payload = {
        **document,
        "signoff": {**document["signoff"], "signature": ""},
    }
    document["receipt_sha256"] = _sha256_bytes(_canonical_bytes(digest_payload))
    signature_payload = {
        **document,
        "signoff": {**document["signoff"], "signature": ""},
    }
    document["signoff"]["signature"] = base64.b64encode(
        private_key.sign(_canonical_bytes(signature_payload))
    ).decode()
    return document


def build_executor_start_receipt(
    *,
    private_key: Ed25519PrivateKey,
    start_receipt: dict[str, object],
    executor_surface: str,
    recorded_at: str,
) -> dict[str, Any]:
    if (
        executor_surface not in {"codex_cli_json", "claude_code"}
        or not isinstance(start_receipt, dict)
        or start_receipt.get("status") != "READY"
        or not isinstance(start_receipt.get("capture_sha256"), str)
        or not isinstance(start_receipt.get("session_id"), str)
        or not isinstance(start_receipt.get("work_id"), str)
    ):
        raise PilotEvidenceError("executor_start_receipt_invalid")
    recorded = _parse_timestamp(recorded_at, "executor_start_receipt_invalid")
    started = _parse_timestamp(
        start_receipt.get("started_at"), "executor_start_receipt_invalid"
    )
    if not (started <= recorded <= started + EXECUTOR_START_MAX_DELAY):
        raise PilotEvidenceError("executor_start_receipt_invalid")
    return _seal_evidence(
        {
            "schema_version": EXECUTOR_START_SCHEMA,
            "artifact_type": "executor_start",
            "session_id": start_receipt["session_id"],
            "work_id": start_receipt["work_id"],
            "start_capture_sha256": start_receipt["capture_sha256"],
            "executor_surface": executor_surface,
            "recorded_at": recorded_at,
        },
        private_key=private_key,
        signer="acceptability_repository_executor_start",
    )


def run_codex_json_sidecar(
    *,
    registration: object,
    private_key_path: Path,
    repository_root: Path,
    work_id: str,
    request: str,
    output_path: Path,
    executor_start_receipt: object,
    codex_binary: str = "codex",
    produced_at: str | None = None,
) -> dict[str, Any]:
    """Run the real Codex JSON transport and atomically persist its receipt."""
    if output_path.exists():
        raise PilotEvidenceError("receipt_output_exists")
    registered = _validate_registration(registration)
    private_key = _registered_private_key(
        private_key_path, registration=registered,
        expected_public_key=registered["trusted_execution_public_key"],
    )
    matches = [
        start for start in _scan_registered_starts(registered)
        if start["work_id"] == work_id
    ]
    if len(matches) != 1:
        raise PilotEvidenceError("executor_start_receipt_invalid")
    start = matches[0]
    _verify_evidence(
        executor_start_receipt,
        trusted_public_key=registered["trusted_start_public_keys"][start["repo_id"]],
        signer="acceptability_repository_executor_start",
        reason="executor_start_receipt_invalid",
    )
    launched_at = _utc_now()
    executor_started = _parse_timestamp(
        start["executor_start_receipt"]["recorded_at"],
        "executor_start_receipt_invalid",
    )
    if (
        executor_start_receipt != start["executor_start_receipt"]
        or repository_root.resolve()
        != Path(registered["registered_repository_paths"][start["repo_id"]]).resolve()
        or start["executor_surface"] != "codex_cli_json"
        or start["work_class"] != "implementation"
        or not isinstance(request, str)
        or not request.strip()
        or canonical_sha256(request) != start["request_sha256"]
        or executor_started > launched_at
    ):
        raise PilotEvidenceError("executor_start_receipt_invalid")
    if produced_at is not None and _parse_timestamp(
        produced_at, "execution_receipt_invalid"
    ) < launched_at:
        raise PilotEvidenceError("execution_receipt_invalid")
    try:
        completed = subprocess.run(
            [codex_binary, "exec", "--json", request],
            cwd=repository_root,
            check=False,
            capture_output=True,
            text=True,
        )
    except OSError as error:
        raise PilotEvidenceError("codex_execution_failed") from error
    raw_jsonl = completed.stdout
    receipt = build_execution_receipt(
        private_key=private_key,
        work_id=work_id,
        request=request,
        raw_jsonl=raw_jsonl,
        raw_stderr=completed.stderr,
        exit_code=completed.returncode,
        produced_at=produced_at
        or _utc_now().isoformat(),
        executor_start_receipt_sha256=executor_start_receipt["receipt_sha256"],
    )
    output_path.parent.mkdir(parents=True, exist_ok=True)
    try:
        with output_path.open("x", encoding="utf-8") as stream:
            json.dump(receipt, stream, ensure_ascii=False, sort_keys=True, indent=2)
            stream.write("\n")
    except FileExistsError as error:
        raise PilotEvidenceError("receipt_output_exists") from error
    return receipt


def build_inventory_receipt(
    *,
    private_key: Ed25519PrivateKey,
    study_id: str,
    source_kind: str,
    closed_at: str,
    starts: list[dict[str, object]],
    source_population_receipt: dict[str, object],
) -> dict[str, Any]:
    _parse_timestamp(closed_at, "inventory_receipt_invalid")
    source_digest = source_population_receipt.get("receipt_sha256")
    if not isinstance(source_digest, str):
        raise PilotEvidenceError("source_population_receipt_invalid")
    sealed = _seal_evidence(
        {
            "schema_version": INVENTORY_SCHEMA,
            "artifact_type": "population_inventory",
            "study_id": study_id,
            "source_kind": source_kind,
            "closed_at": closed_at,
            "inventory_complete": True,
            "starts": starts,
            "source_population_receipt_sha256": source_digest,
        },
        private_key=private_key,
        signer="acceptability_evidence_collector",
    )
    sealed["source_population_receipt"] = source_population_receipt
    return sealed


def build_source_population_receipt(
    *,
    private_key: Ed25519PrivateKey,
    study_id: str,
    closed_at: str,
    starts: list[dict[str, object]],
) -> dict[str, Any]:
    _parse_timestamp(closed_at, "source_population_receipt_invalid")
    return _seal_evidence(
        {
            "schema_version": SOURCE_POPULATION_SCHEMA,
            "artifact_type": "source_population",
            "study_id": study_id,
            "source_kind": "natural",
            "closed_at": closed_at,
            "sequence_start": 1,
            "sequence_end": len(starts),
            "starts": starts,
        },
        private_key=private_key,
        signer="acceptability_source_collector",
    )


def build_case_receipt(
    *,
    private_key: Ed25519PrivateKey,
    study_id: str,
    work_id: str,
    source_kind: str,
    completed: bool,
    completion_at: str | None,
    verification_status: str,
    verification_raw_output: str,
    incomplete_attribution: str | None,
    correction_observed_through: str | None,
    corrections: list[dict[str, object]],
    execution_receipt_sha256: str,
    source_case_receipt: dict[str, object],
    closed_at: str | None = None,
) -> dict[str, Any]:
    source_digest = source_case_receipt.get("receipt_sha256")
    if not isinstance(source_digest, str):
        raise PilotEvidenceError("source_case_receipt_invalid")
    sealed = _seal_evidence(
        {
            "schema_version": CASE_SCHEMA,
            "artifact_type": "case_observation",
            "study_id": study_id,
            "work_id": work_id,
            "source_kind": source_kind,
            "completed": completed,
            "completion_at": completion_at,
            "verification_status": verification_status,
            "verification_raw_output": verification_raw_output,
            "incomplete_attribution": incomplete_attribution,
            "correction_observed_through": correction_observed_through,
            "corrections": corrections,
            "closed_at": closed_at or correction_observed_through,
            "execution_receipt_sha256": execution_receipt_sha256,
            "source_case_receipt_sha256": source_digest,
        },
        private_key=private_key,
        signer="acceptability_evidence_collector",
    )
    sealed["source_case_receipt"] = source_case_receipt
    return sealed


def build_source_case_receipt(
    *,
    private_key: Ed25519PrivateKey,
    study_id: str,
    work_id: str,
    source_kind: str,
    completed: bool,
    completion_at: str | None,
    verification_status: str,
    verification_raw_output: str,
    incomplete_attribution: str | None,
    correction_observed_through: str | None,
    corrections: list[dict[str, object]],
    closed_at: str | None = None,
) -> dict[str, Any]:
    source_events: list[dict[str, Any]] = [
        {
            "sequence": 1,
            "event_type": "completion",
            "at": completion_at,
            "completed": completed,
            "incomplete_attribution": incomplete_attribution,
        },
        {
            "sequence": 2,
            "event_type": "verification",
            "status": verification_status,
            "raw_output": verification_raw_output,
        },
    ]
    source_events.extend(
        {
            "sequence": index + 3,
            "event_type": "followup",
            "event": correction,
        }
        for index, correction in enumerate(corrections)
    )
    source_events.append({
        "sequence": len(source_events) + 1,
        "event_type": "correction_window_closed",
        "at": correction_observed_through,
    })
    return _seal_evidence(
        {
            "schema_version": SOURCE_CASE_SCHEMA,
            "artifact_type": "source_case_observation",
            "study_id": study_id,
            "work_id": work_id,
            "source_kind": source_kind,
            "completed": completed,
            "completion_at": completion_at,
            "verification_status": verification_status,
            "verification_raw_output": verification_raw_output,
            "incomplete_attribution": incomplete_attribution,
            "correction_observed_through": correction_observed_through,
            "corrections": corrections,
            "closed_at": closed_at or correction_observed_through,
            "source_events": source_events,
        },
        private_key=private_key,
        signer="acceptability_source_collector",
    )


def verify_execution_receipt(
    receipt: object, *, trusted_public_key: str, require_success: bool = True
) -> dict[str, Any]:
    if not isinstance(receipt, dict) or not isinstance(receipt.get("signoff"), dict):
        raise PilotEvidenceError("execution_receipt_invalid")
    signoff = receipt["signoff"]
    request = receipt.get("request")
    raw_jsonl = receipt.get("raw_jsonl")
    raw_stderr = receipt.get("raw_stderr")
    if (
        receipt.get("schema_version") != EXECUTION_RECEIPT_SCHEMA
        or receipt.get("executor") != "codex_cli"
        or receipt.get("transport") != "exec_json"
        or not isinstance(receipt.get("work_id"), str)
        or not isinstance(request, str)
        or not isinstance(raw_jsonl, str)
        or not isinstance(raw_stderr, str)
        or not isinstance(receipt.get("exit_code"), int)
        or not isinstance(receipt.get("executor_start_receipt_sha256"), str)
        or len(receipt["executor_start_receipt_sha256"]) != 64
        or any(
            character not in "0123456789abcdef"
            for character in receipt["executor_start_receipt_sha256"]
        )
        or signoff.get("algorithm") != "ed25519"
        or signoff.get("signer") != "codex_cli_execution_sidecar"
        or signoff.get("signer_public_key") != trusted_public_key
        or receipt.get("request_sha256") != canonical_sha256(request)
        or receipt.get("raw_jsonl_sha256")
        != _sha256_bytes(raw_jsonl.encode("utf-8"))
        or receipt.get("raw_stderr_sha256")
        != _sha256_bytes(raw_stderr.encode("utf-8"))
    ):
        raise PilotEvidenceError("execution_receipt_invalid")
    try:
        events = (
            [
                json.loads(line)
                for line in raw_jsonl.splitlines()
                if line.strip()
            ]
            if require_success
            else []
        )
        _parse_timestamp(receipt.get("produced_at"), "execution_receipt_invalid")
        digest_payload = {
            **receipt,
            "receipt_sha256": "",
            "signoff": {**signoff, "signature": ""},
        }
        if receipt.get("receipt_sha256") != _sha256_bytes(
            _canonical_bytes(digest_payload)
        ):
            raise PilotEvidenceError("execution_receipt_invalid")
        signature_payload = {
            **receipt,
            "signoff": {**signoff, "signature": ""},
        }
        Ed25519PublicKey.from_public_bytes(
            base64.b64decode(trusted_public_key, validate=True)
        ).verify(
            base64.b64decode(str(signoff.get("signature")), validate=True),
            _canonical_bytes(signature_payload),
        )
    except (InvalidSignature, TypeError, ValueError) as error:
        raise PilotEvidenceError("execution_receipt_invalid") from error
    except json.JSONDecodeError as error:
        raise PilotEvidenceError("execution_receipt_invalid") from error
    if require_success and (
        receipt["exit_code"] != 0
        or not events
        or not isinstance(events[-1], dict)
        or events[-1].get("type") != "turn.completed"
    ):
        raise PilotEvidenceError("execution_receipt_unsuccessful")
    return receipt


def _read_regular_json(path: Path, reason: str) -> dict[str, Any]:
    if path.is_symlink() or not path.is_file():
        raise PilotEvidenceError(reason)
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as error:
        raise PilotEvidenceError(reason) from error
    if not isinstance(value, dict):
        raise PilotEvidenceError(reason)
    return value


def _scan_registered_starts(registration: dict[str, Any]) -> list[dict[str, Any]]:
    starts_at = _parse_timestamp(registration["starts_at"], "repository_stream_invalid")
    ends_at = _parse_timestamp(registration["ends_at"], "repository_stream_invalid")
    discovered: list[dict[str, Any]] = []
    try:
        for repo_id, path_text in registration["registered_repository_paths"].items():
            root = Path(path_text).resolve()
            if (
                hashlib.sha256(str(root).encode()).hexdigest()
                != registration["trusted_repository_roots"][repo_id]
            ):
                raise PilotEvidenceError("repository_stream_invalid")
            sessions_dir = root / ".omc" / "state" / "sessions"
            if sessions_dir.is_symlink() or not sessions_dir.is_dir():
                raise PilotEvidenceError("repository_stream_invalid")
            trusted_key = registration["trusted_start_public_keys"][repo_id]
            for session_dir in sessions_dir.iterdir():
                if session_dir.is_symlink():
                    raise PilotEvidenceError("repository_stream_invalid")
                if not session_dir.is_dir():
                    continue
                # Enumerate sessions, not surviving receipts: a missing capture
                # cannot establish that this session was outside the cohort.
                capture_dir = session_dir / "capture"
                if capture_dir.is_symlink() or not capture_dir.is_dir():
                    raise PilotEvidenceError("repository_stream_invalid")
                start_path = capture_dir / "start.json"
                start = omc_state._verify_capture(
                    _read_regular_json(start_path, "repository_stream_invalid"),
                    trusted_key,
                )
                started_at = _parse_timestamp(
                    start.get("started_at"), "repository_stream_invalid"
                )
                if not (starts_at <= started_at < ends_at):
                    continue
                session = _read_regular_json(
                    session_dir / "session.json", "repository_stream_invalid"
                )
                lock = _read_regular_json(
                    session_dir / "work_class_lock.json", "repository_stream_invalid"
                )
                executor_start = _verify_evidence(
                    _read_regular_json(
                        start_path.parent / "executor-start.json",
                        "repository_stream_invalid",
                    ),
                    trusted_public_key=trusted_key,
                    signer="acceptability_repository_executor_start",
                    reason="repository_stream_invalid",
                )
                executor_recorded_at = _parse_timestamp(
                    executor_start.get("recorded_at"), "repository_stream_invalid"
                )
                candidate_universe._validate_work_class_lock_receipt_envelope(
                    lock, expected_status="frozen"
                )
                candidate_universe._verify_document(
                    lock,
                    digest_field="receipt_sha256",
                    trusted_public_keys={trusted_key},
                    expected_digest=str(lock.get("receipt_sha256")),
                    expected_signer="independent-work-class-lock-v1",
                    label="work class lock receipt",
                )
                if (
                    session.get("session_id") != start.get("session_id")
                    or session_dir.name != start.get("session_id")
                    or session.get("work_id") != start.get("work_id")
                    or canonical_sha256(session.get("request"))
                    != start.get("request_sha256")
                    or lock.get("session_id") != start.get("session_id")
                    or lock.get("work_id") != start.get("work_id")
                    or lock.get("work_class") != session.get("work_class")
                    or lock.get("receipt_sha256")
                    != start.get("work_class_lock_sha256")
                    or start.get("repository_root_sha256")
                    != registration["trusted_repository_roots"][repo_id]
                    or executor_start.get("schema_version") != EXECUTOR_START_SCHEMA
                    or executor_start.get("artifact_type") != "executor_start"
                    or executor_start.get("session_id") != start.get("session_id")
                    or executor_start.get("work_id") != start.get("work_id")
                    or executor_start.get("start_capture_sha256")
                    != start.get("capture_sha256")
                    or not (
                        started_at
                        <= executor_recorded_at
                        <= started_at
                        + timedelta(
                            seconds=registration[
                                "executor_start_recording_max_delay_seconds"
                            ]
                        )
                    )
                    or executor_start.get("executor_surface")
                    not in {"codex_cli_json", "claude_code"}
                ):
                    raise PilotEvidenceError("repository_stream_invalid")
                discovered.append({
                    "work_id": start["work_id"],
                    "repo_id": repo_id,
                    "started_at": start["started_at"],
                    "work_class": lock["work_class"],
                    "executor_surface": executor_start["executor_surface"],
                    "source_kind": "natural",
                    "source_session_receipt_sha256": start["capture_sha256"],
                    "source_session_receipt": start,
                    "executor_start_receipt_sha256": executor_start["receipt_sha256"],
                    "executor_start_receipt": executor_start,
                    "request_sha256": start["request_sha256"],
                })
    except (KeyError, OSError, TypeError, ValueError) as error:
        if isinstance(error, PilotEvidenceError):
            raise
        raise PilotEvidenceError("repository_stream_invalid") from error
    work_ids = [item["work_id"] for item in discovered]
    if len(work_ids) != len(set(work_ids)):
        raise PilotEvidenceError("repository_stream_invalid")
    return sorted(discovered, key=lambda item: (
        _parse_timestamp(item["started_at"], "repository_stream_invalid"),
        item["work_id"],
    ))


def _verified_artifact_output(
    capture_dir: Path, verification: dict[str, Any], *, reason: str,
) -> str:
    """Validate and decode the same captured bytes, without reopening artifacts."""
    results = verification.get("results")
    if not isinstance(results, list) or not results:
        raise PilotEvidenceError(reason)
    snapshots: dict[Path, bytes] = {}
    output: list[str] = []
    try:
        for result in results:
            if not isinstance(result, dict):
                raise PilotEvidenceError(reason)
            for stream in ("stdout", "stderr"):
                path = capture_dir / str(result[f"{stream}_path"])
                if path not in snapshots:
                    snapshots[path] = omc_state._regular_bytes(path)
                data = snapshots[path]
                if (
                    len(data) != result.get(f"{stream}_size")
                    or _sha256_bytes(data) != result.get(f"{stream}_sha256")
                ):
                    raise PilotEvidenceError(reason)
                output.append(data.decode("utf-8"))
    except PilotEvidenceError:
        raise
    except (KeyError, OSError, TypeError, ValueError) as error:
        raise PilotEvidenceError(reason) from error
    return "".join(output)


def _repository_source_case(
    registration: dict[str, Any], start: dict[str, Any]
) -> dict[str, Any]:
    repo_id = start.get("repo_id")
    source_start = start.get("source_session_receipt")
    if not isinstance(repo_id, str) or not isinstance(source_start, dict):
        raise PilotEvidenceError("repository_source_case_invalid")
    session_id = source_start.get("session_id")
    if not isinstance(session_id, str) or not session_id:
        raise PilotEvidenceError("repository_source_case_invalid")
    root = Path(registration["registered_repository_paths"][repo_id]).resolve()
    capture_dir = (
        root / ".omc" / "state" / "sessions" / session_id / "capture"
    )
    reason = "repository_source_case_invalid"
    try:
        persisted = _read_regular_json(capture_dir / "source-case.json", reason)
        verification = omc_state._verify_capture(
            _read_regular_json(capture_dir / "verification.json", reason),
            registration["trusted_start_public_keys"][repo_id],
        )
        terminal = _verify_evidence(
            _read_regular_json(capture_dir / "case-terminal.json", reason),
            trusted_public_key=registration["trusted_start_public_keys"][repo_id],
            signer="acceptability_repository_case_closure",
            reason=reason,
        )
        raw_output = _verified_artifact_output(
            capture_dir, verification, reason=reason,
        )
        results = verification.get("results")
        if not isinstance(results, list) or not results:
            raise PilotEvidenceError(reason)
        completed = persisted.get("completed")
        if not isinstance(completed, bool):
            raise PilotEvidenceError(reason)
        if (
            verification.get("status") != "VERIFIED"
            or verification.get("session_id") != session_id
            or verification.get("work_id") != start.get("work_id")
            or verification.get("start_capture_sha256")
            != source_start.get("capture_sha256")
            or not isinstance(verification.get("verification_passed"), bool)
            or not isinstance(verification.get("tree_unchanged"), bool)
            or any(not isinstance(result, dict) for result in results)
            or (
                completed
                and (
                    verification.get("status") != "VERIFIED"
                    or verification.get("verification_passed") is not True
                    or verification.get("tree_unchanged") is not True
                    or any(
                        result.get("exit_code") != 0
                        or result.get("timed_out") is not False
                        or result.get("output_overflow") is not False
                        for result in results
                    )
                )
            )
            or terminal.get("schema_version")
            != "omc-absolute-acceptability-case-closure/v1"
            or terminal.get("status") != "CASE_CLOSED"
            or terminal.get("session_id") != session_id
            or terminal.get("work_id") != start.get("work_id")
            or terminal.get("verification_capture_sha256")
            != verification.get("capture_sha256")
            or terminal.get("completed") != persisted.get("completed")
            or terminal.get("completion_at") != persisted.get("completion_at")
            or terminal.get("incomplete_attribution")
            != persisted.get("incomplete_attribution")
            or terminal.get("correction_observed_through")
            != persisted.get("correction_observed_through")
            or terminal.get("corrections") != persisted.get("corrections")
            or terminal.get("closed_at") != persisted.get("closed_at")
            or persisted.get("verification_status")
            != ("passed" if verification.get("verification_passed") else "failed")
            or persisted.get("verification_raw_output") != raw_output
        ):
            raise PilotEvidenceError(reason)
    except (KeyError, OSError, TypeError, UnicodeDecodeError, ValueError) as error:
        if isinstance(error, PilotEvidenceError):
            raise
        raise PilotEvidenceError(reason) from error
    return persisted


def _validate_registration(registration: object) -> dict[str, Any]:
    if not isinstance(registration, dict):
        raise PilotEvidenceError("registration_invalid")
    required = {
        "schema_version": SCHEMA,
        "status": "registered",
        "selection_count": 10,
        "minimum_repositories": 2,
        "correction_window_hours": 24,
        "executor_start_recording_max_delay_seconds": 60,
        "executor_start_execution_digest_binding_required": True,
        "executor_start_precedes_execution_required": True,
        "incomplete_failure_evidence_allowed": True,
        "incomplete_case_shape_enforced": True,
        "blank_raw_followup_forbidden": True,
    }
    if any(registration.get(key) != value for key, value in required.items()):
        raise PilotEvidenceError("registration_invalid")
    if (
        not isinstance(registration.get("study_id"), str)
        or not isinstance(registration.get("trusted_execution_public_key"), str)
        or not isinstance(registration.get("trusted_evidence_public_key"), str)
        or not isinstance(registration.get("trusted_source_public_key"), str)
        or registration["trusted_execution_public_key"]
        == registration["trusted_evidence_public_key"]
        or not isinstance(registration.get("trusted_start_public_keys"), dict)
        or len(registration["trusted_start_public_keys"]) < 2
        or not isinstance(registration.get("trusted_repository_roots"), dict)
        or set(registration["trusted_repository_roots"])
        != set(registration["trusted_start_public_keys"])
        or not isinstance(registration.get("registered_repository_paths"), dict)
        or set(registration["registered_repository_paths"])
        != set(registration["trusted_start_public_keys"])
    ):
        raise PilotEvidenceError("registration_invalid")
    starts_at = _parse_timestamp(registration.get("starts_at"), "registration_invalid")
    ends_at = _parse_timestamp(registration.get("ends_at"), "registration_invalid")
    if ends_at - starts_at != timedelta(days=14):
        raise PilotEvidenceError("registration_invalid")
    try:
        authority_keys = {
            registration["trusted_execution_public_key"],
            registration["trusted_evidence_public_key"],
            registration["trusted_source_public_key"],
        }
        if len(authority_keys) != 3:
            raise ValueError("authority key reuse")
        Ed25519PublicKey.from_public_bytes(
            base64.b64decode(
                registration["trusted_execution_public_key"], validate=True
            )
        )
        for repo_id, public_key in registration["trusted_start_public_keys"].items():
            if not isinstance(repo_id, str) or not repo_id or not isinstance(public_key, str):
                raise ValueError("start key invalid")
            if public_key in {
                *authority_keys,
            }:
                raise ValueError("start key reuse")
            Ed25519PublicKey.from_public_bytes(
                base64.b64decode(public_key, validate=True)
            )
        Ed25519PublicKey.from_public_bytes(
            base64.b64decode(
                registration["trusted_evidence_public_key"], validate=True
            )
        )
        Ed25519PublicKey.from_public_bytes(
            base64.b64decode(
                registration["trusted_source_public_key"], validate=True
            )
        )
        start_keys = list(registration["trusted_start_public_keys"].values())
        if len(start_keys) != len(set(start_keys)):
            raise ValueError("start key reuse")
        if any(
            not isinstance(root_hash, str)
            or len(root_hash) != 64
            or any(character not in "0123456789abcdef" for character in root_hash)
            for root_hash in registration["trusted_repository_roots"].values()
        ):
            raise ValueError("repository root invalid")
        if any(
            not isinstance(path, str) or not path or not Path(path).is_absolute()
            for path in registration["registered_repository_paths"].values()
        ):
            raise ValueError("repository path invalid")
    except (TypeError, ValueError) as error:
        raise PilotEvidenceError("registration_invalid") from error
    return registration


def activate_preregistration(
    *,
    preregistration: object,
    starts_at: str,
    trusted_execution_public_key: str,
    trusted_evidence_public_key: str,
    trusted_source_public_key: str,
    trusted_start_public_keys: dict[str, str],
    trusted_repository_roots: dict[str, str],
    registered_repository_paths: dict[str, str],
) -> dict[str, Any]:
    if not isinstance(preregistration, dict):
        raise PilotEvidenceError("preregistration_invalid")
    cohort = preregistration.get("cohort")
    window = preregistration.get("observation_window")
    correction = preregistration.get("correction_window")
    execution_evidence = preregistration.get("execution_evidence")
    source_authority = preregistration.get("source_authority")
    collection_contract = preregistration.get("collection_contract")
    if (
        preregistration.get("schema_version") != SCHEMA
        or preregistration.get("study_status") != "draft_unregistered"
        or preregistration.get("observation_allowed") is not False
        or not isinstance(cohort, dict)
        or not isinstance(window, dict)
        or not isinstance(correction, dict)
        or not isinstance(execution_evidence, dict)
        or not isinstance(source_authority, dict)
        or not isinstance(collection_contract, dict)
        or cohort.get("selection_count") != 10
        or cohort.get("minimum_repositories") != 2
        or window.get("fixed_duration_days") != 14
        or correction.get("hours_after_first_completion") != 24
        or execution_evidence.get("executor_start_recording_max_delay_seconds")
        != 60
        or execution_evidence.get("retrospective_executor_classification_forbidden")
        is not True
        or execution_evidence.get("executor_start_execution_digest_binding_required")
        is not True
        or execution_evidence.get("executor_start_precedes_execution_required")
        is not True
        or execution_evidence.get("signed_unsuccessful_execution_allowed_for_incomplete_case")
        is not True
        or source_authority.get("failed_verification_evidence_allowed_for_incomplete_case")
        is not True
        or collection_contract.get("incomplete_case_collection_required") is not True
        or correction.get("blank_raw_followup_forbidden") is not True
        or not str(collection_contract.get("incomplete_case_shape", "")).startswith(
            "completed=false"
        )
    ):
        raise PilotEvidenceError("preregistration_invalid")
    start = _parse_timestamp(starts_at, "preregistration_invalid")
    registration = {
        "schema_version": SCHEMA,
        "study_id": preregistration.get("study_id"),
        "status": "registered",
        "starts_at": start.isoformat(),
        "ends_at": (start + timedelta(days=14)).isoformat(),
        "selection_count": 10,
        "minimum_repositories": 2,
        "correction_window_hours": 24,
        "executor_start_recording_max_delay_seconds": 60,
        "executor_start_execution_digest_binding_required": True,
        "executor_start_precedes_execution_required": True,
        "incomplete_failure_evidence_allowed": True,
        "incomplete_case_shape_enforced": True,
        "blank_raw_followup_forbidden": True,
        "trusted_execution_public_key": trusted_execution_public_key,
        "trusted_evidence_public_key": trusted_evidence_public_key,
        "trusted_source_public_key": trusted_source_public_key,
        "trusted_start_public_keys": trusted_start_public_keys,
        "trusted_repository_roots": trusted_repository_roots,
        "registered_repository_paths": registered_repository_paths,
        "preregistration_sha256": canonical_sha256(preregistration),
    }
    return _validate_registration(registration)


def _blocked(reason_codes: list[str]) -> dict[str, Any]:
    return {
        "schema_version": SCHEMA,
        "outcome": "OBSERVATION_INCONCLUSIVE",
        "reason_codes": sorted(set(reason_codes)),
    }


def evaluate_study(
    *,
    registration: object,
    approved_registration_sha256: str,
    inventory_receipt: object,
    case_receipts: object,
    as_of: object,
) -> dict[str, Any]:
    """Evaluate first-start selection and evidence with fail-closed precedence."""
    if (
        not isinstance(approved_registration_sha256, str)
        or canonical_sha256(registration) != approved_registration_sha256
    ):
        return _blocked(["registration_digest_mismatch"])
    try:
        registered = _validate_registration(registration)
    except PilotEvidenceError as error:
        return _blocked([str(error)])
    try:
        evaluated_at = _parse_timestamp(as_of, "as_of_invalid")
        inventory = _verify_evidence(
            inventory_receipt,
            trusted_public_key=registered["trusted_evidence_public_key"],
            signer="acceptability_evidence_collector",
            reason="inventory_receipt_invalid",
            detached_fields={"source_population_receipt"},
        )
    except PilotEvidenceError as error:
        return _blocked([str(error)])
    starts_at = _parse_timestamp(registered["starts_at"], "registration_invalid")
    ends_at = _parse_timestamp(registered["ends_at"], "registration_invalid")
    if (
        inventory.get("schema_version") != INVENTORY_SCHEMA
        or inventory.get("artifact_type") != "population_inventory"
        or inventory.get("study_id") != registered["study_id"]
        or inventory.get("inventory_complete") is not True
        or not isinstance(inventory.get("starts"), list)
    ):
        return _blocked(["inventory_receipt_invalid"])
    if inventory.get("source_kind") != "natural":
        return _blocked(["synthetic_evidence_forbidden"])
    try:
        source_population = _verify_evidence(
            inventory.get("source_population_receipt"),
            trusted_public_key=registered["trusted_source_public_key"],
            signer="acceptability_source_collector",
            reason="source_population_receipt_invalid",
        )
    except PilotEvidenceError as error:
        return _blocked([str(error)])
    if (
        source_population.get("schema_version") != SOURCE_POPULATION_SCHEMA
        or source_population.get("artifact_type") != "source_population"
        or source_population.get("study_id") != registered["study_id"]
        or source_population.get("source_kind") != "natural"
        or source_population.get("receipt_sha256")
        != inventory.get("source_population_receipt_sha256")
        or source_population.get("starts") != inventory.get("starts")
        or source_population.get("closed_at") != inventory.get("closed_at")
        or source_population.get("sequence_start") != 1
        or source_population.get("sequence_end") != len(inventory["starts"])
    ):
        return _blocked(["source_population_mismatch"])
    try:
        repository_population = _scan_registered_starts(registered)
    except PilotEvidenceError as error:
        return _blocked([str(error)])
    if repository_population != inventory["starts"]:
        return _blocked(["repository_population_mismatch"])
    try:
        closed_at = _parse_timestamp(
            inventory.get("closed_at"), "inventory_receipt_invalid"
        )
    except PilotEvidenceError as error:
        return _blocked([str(error)])
    if closed_at < ends_at or closed_at > evaluated_at:
        return _blocked(["inventory_closure_time_invalid"])

    eligible: list[tuple[datetime, str, dict[str, Any]]] = []
    seen_starts: set[str] = set()
    for start in inventory["starts"]:
        if not isinstance(start, dict):
            return _blocked(["start_receipt_invalid"])
        work_id = start.get("work_id")
        if not isinstance(work_id, str) or work_id in seen_starts:
            return _blocked(["start_receipt_invalid"])
        seen_starts.add(work_id)
        try:
            started_at = _parse_timestamp(
                start.get("started_at"), "start_receipt_invalid"
            )
        except PilotEvidenceError:
            return _blocked(["start_receipt_invalid"])
        if not (starts_at <= started_at < ends_at):
            continue
        if (
            start.get("work_class") == "implementation"
            and start.get("executor_surface") == "codex_cli_json"
        ):
            source_digest = start.get("source_session_receipt_sha256")
            source_receipt = start.get("source_session_receipt")
            repo_id = start.get("repo_id")
            if (
                start.get("source_kind") != "natural"
                or not isinstance(repo_id, str)
                or not isinstance(start.get("request_sha256"), str)
                or not isinstance(source_digest, str)
                or len(source_digest) != 64
            ):
                return _blocked(["start_receipt_invalid"])
            trusted_start_key = registered["trusted_start_public_keys"].get(repo_id)
            try:
                verified_start = omc_state._verify_capture(
                    source_receipt, trusted_start_key
                )
            except (TypeError, ValueError):
                return _blocked(["start_receipt_invalid"])
            if (
                verified_start.get("status") != "READY"
                or verified_start.get("capture_sha256") != source_digest
                or verified_start.get("work_id") != work_id
                or verified_start.get("started_at") != start.get("started_at")
                or verified_start.get("request_sha256") != start.get("request_sha256")
                or verified_start.get("repository_root_sha256")
                != registered["trusted_repository_roots"].get(repo_id)
            ):
                return _blocked(["start_receipt_invalid"])
            eligible.append((started_at, work_id, start))

    eligible.sort(key=lambda item: (item[0], item[1]))
    selected = eligible[: registered["selection_count"]]
    if len(selected) < registered["selection_count"] or len(
        {item[2]["repo_id"] for item in selected}
    ) < registered["minimum_repositories"]:
        return {
            "schema_version": SCHEMA,
            "outcome": "LOW_NATURAL_DEMAND",
            "reason_codes": ["insufficient_natural_demand"],
            "eligible_start_count": len(eligible),
        }

    if not isinstance(case_receipts, list):
        return _blocked(["case_receipt_invalid"])
    case_by_id: dict[str, dict[str, Any]] = {}
    for case in case_receipts:
        if not isinstance(case, dict) or not isinstance(case.get("work_id"), str):
            return _blocked(["case_receipt_invalid"])
        if case["work_id"] in case_by_id:
            return _blocked(["case_receipt_invalid"])
        case_by_id[case["work_id"]] = case

    reasons: list[str] = []
    verified_count = 0
    primary_count = 0
    omc_incomplete_count = 0
    selected_ids = [item[1] for item in selected]
    selected_start_by_id = {item[1]: item[2] for item in selected}
    for work_id in selected_ids:
        case = case_by_id.get(work_id)
        if case is None:
            reasons.append("case_evidence_missing")
            continue
        try:
            _verify_evidence(
                case,
                trusted_public_key=registered["trusted_evidence_public_key"],
                signer="acceptability_evidence_collector",
                reason="case_receipt_invalid",
                detached_fields={"execution_receipt", "source_case_receipt"},
            )
        except PilotEvidenceError:
            reasons.append("case_receipt_invalid")
            continue
        if (
            case.get("schema_version") != CASE_SCHEMA
            or case.get("artifact_type") != "case_observation"
            or case.get("study_id") != registered["study_id"]
            or case.get("source_kind") != "natural"
        ):
            reasons.append(
                "synthetic_evidence_forbidden"
                if case.get("source_kind") != "natural"
                else "case_receipt_invalid"
            )
            continue
        try:
            source_case = _verify_evidence(
                case.get("source_case_receipt"),
                trusted_public_key=registered["trusted_source_public_key"],
                signer="acceptability_source_collector",
                reason="source_case_receipt_invalid",
            )
        except PilotEvidenceError:
            reasons.append("source_case_receipt_invalid")
            continue
        try:
            persisted_source_case = _repository_source_case(
                registered, selected_start_by_id[work_id]
            )
        except (KeyError, PilotEvidenceError):
            reasons.append("repository_source_case_invalid")
            continue
        if persisted_source_case != source_case:
            reasons.append("repository_source_case_mismatch")
            continue
        compared_case_fields = {
            "study_id",
            "work_id",
            "source_kind",
            "completed",
            "completion_at",
            "verification_status",
            "verification_raw_output",
            "incomplete_attribution",
            "correction_observed_through",
            "corrections",
            "closed_at",
        }
        if (
            source_case.get("schema_version") != SOURCE_CASE_SCHEMA
            or source_case.get("artifact_type") != "source_case_observation"
            or not isinstance(source_case.get("corrections"), list)
            or source_case.get("receipt_sha256")
            != case.get("source_case_receipt_sha256")
            or any(source_case.get(field) != case.get(field) for field in compared_case_fields)
            or source_case.get("source_events") != [
                {
                    "sequence": 1,
                    "event_type": "completion",
                    "at": source_case.get("completion_at"),
                    "completed": source_case.get("completed"),
                    "incomplete_attribution": source_case.get("incomplete_attribution"),
                },
                {
                    "sequence": 2,
                    "event_type": "verification",
                    "status": source_case.get("verification_status"),
                    "raw_output": source_case.get("verification_raw_output"),
                },
                *[
                    {
                        "sequence": index + 3,
                        "event_type": "followup",
                        "event": correction,
                    }
                    for index, correction in enumerate(source_case.get("corrections", []))
                ],
                {
                    "sequence": len(source_case.get("corrections", [])) + 3,
                    "event_type": "correction_window_closed",
                    "at": source_case.get("correction_observed_through"),
                },
            ]
        ):
            reasons.append("source_case_mismatch")
            continue
        try:
            receipt = verify_execution_receipt(
                case.get("execution_receipt"),
                trusted_public_key=registered["trusted_execution_public_key"],
                require_success=case.get("completed") is True,
            )
        except PilotEvidenceError:
            reasons.append("execution_receipt_invalid")
            continue
        if (
            receipt.get("work_id") != work_id
            or case.get("execution_receipt_sha256")
            != receipt.get("receipt_sha256")
            or selected_start_by_id[work_id].get("request_sha256")
            != receipt.get("request_sha256")
            or selected_start_by_id[work_id].get("executor_start_receipt_sha256")
            != receipt.get("executor_start_receipt_sha256")
        ):
            reasons.append("execution_receipt_binding_mismatch")
            continue
        try:
            execution_at = _parse_timestamp(
                receipt.get("produced_at"), "case_timeline_invalid"
            )
            selected_started_at = _parse_timestamp(
                selected_start_by_id[work_id].get("started_at"),
                "case_timeline_invalid",
            )
            executor_started_at = _parse_timestamp(
                selected_start_by_id[work_id]["executor_start_receipt"].get(
                    "recorded_at"
                ),
                "case_timeline_invalid",
            )
        except PilotEvidenceError:
            reasons.append("case_timeline_invalid")
            continue
        if (
            execution_at < selected_started_at
            or execution_at < executor_started_at
            or execution_at > evaluated_at
        ):
            reasons.append("case_timeline_invalid")
            continue
        try:
            case_closed_at = _parse_timestamp(
                case.get("closed_at"), "case_timeline_invalid"
            )
        except PilotEvidenceError:
            reasons.append("case_timeline_invalid")
            continue
        if case_closed_at > evaluated_at:
            reasons.append("observation_timestamp_in_future")
            continue
        if execution_at > case_closed_at:
            reasons.append("case_timeline_invalid")
            continue

        if case.get("completed") is True:
            verification_output = case.get("verification_raw_output")
            if (
                case.get("verification_status") == "passed"
                and isinstance(verification_output, str)
                and verification_output
            ):
                verified_count += 1
            elif case.get("verification_status") != "failed":
                reasons.append("verification_evidence_invalid")
            completion_at_value = case.get("completion_at")
            observed_through_value = case.get("correction_observed_through")
            try:
                completion_at = _parse_timestamp(
                    completion_at_value, "case_evidence_invalid"
                )
                observed_through = _parse_timestamp(
                    observed_through_value, "case_evidence_invalid"
                )
            except PilotEvidenceError:
                reasons.append("case_evidence_invalid")
                continue
            if not (
                selected_started_at <= execution_at <= completion_at
                and observed_through == completion_at + timedelta(hours=24)
                and completion_at <= observed_through <= case_closed_at <= evaluated_at
            ):
                reasons.append("case_timeline_invalid")
            if observed_through > evaluated_at:
                reasons.append("observation_timestamp_in_future")
        else:
            attribution = case.get("incomplete_attribution")
            if (
                case.get("completed") is not False
                or case.get("completion_at") is not None
                or case.get("correction_observed_through") is not None
                or case.get("corrections") != []
            ):
                reasons.append("incomplete_case_invalid")
                continue
            if attribution == "omc":
                omc_incomplete_count += 1
            elif not isinstance(attribution, str) or attribution != "non_omc":
                reasons.append("incomplete_attribution_missing")

        corrections = case.get("corrections")
        if not isinstance(corrections, list):
            reasons.append("correction_evidence_invalid")
            continue
        for correction in corrections:
            if not isinstance(correction, dict):
                reasons.append("correction_evidence_invalid")
                continue
            taxonomy = correction.get("taxonomy")
            if (
                not isinstance(taxonomy, str)
                or taxonomy not in PRIMARY_TAXONOMY | NON_PRIMARY_TAXONOMY
                or not isinstance(correction.get("raw_followup"), str)
                or not correction["raw_followup"].strip()
            ):
                reasons.append("correction_evidence_invalid")
                continue
            try:
                correction_at = _parse_timestamp(
                    correction.get("at"), "correction_evidence_invalid"
                )
            except PilotEvidenceError:
                reasons.append("correction_evidence_invalid")
                continue
            if case.get("completed") is True and not (
                completion_at <= correction_at <= completion_at + timedelta(hours=24)
                and correction_at <= observed_through
                and correction_at <= evaluated_at
            ):
                reasons.append("correction_timestamp_outside_window")
                continue
            if taxonomy in PRIMARY_TAXONOMY:
                primary_count += 1

    if reasons:
        return _blocked(reasons)
    acceptable = (
        verified_count == 10
        and primary_count <= 3
        and omc_incomplete_count == 0
    )
    return {
        "schema_version": SCHEMA,
        "outcome": "PRELIMINARY_ACCEPTABLE" if acceptable else "NOT_ACCEPTABLE",
        "reason_codes": [],
        "selected_work_ids": selected_ids,
        "verified_completion_count": verified_count,
        "primary_correction_count": primary_count,
        "omc_attributable_incomplete_count": omc_incomplete_count,
        "claim_boundary": "ABSOLUTE_ACCEPTABILITY_PRELIMINARY_ONLY",
    }


def _load_json(path: Path) -> object:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as error:
        raise PilotEvidenceError("json_input_invalid") from error


def _write_once_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        with path.open("x", encoding="utf-8") as stream:
            json.dump(value, stream, ensure_ascii=False, sort_keys=True, indent=2)
            stream.write("\n")
    except FileExistsError as error:
        raise PilotEvidenceError("receipt_output_exists") from error


def _write_once_or_match(path: Path, value: object) -> None:
    """Create a receipt, or accept an identical receipt from an interrupted retry."""
    try:
        _write_once_json(path, value)
    except PilotEvidenceError as error:
        if str(error) != "receipt_output_exists":
            raise
        try:
            existing = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as read_error:
            raise PilotEvidenceError("receipt_output_exists") from read_error
        if existing != value:
            raise


def _registered_private_key(
    path: Path,
    *,
    registration: dict[str, Any],
    expected_public_key: str,
) -> Ed25519PrivateKey:
    roots = [
        Path(value).resolve()
        for value in registration["registered_repository_paths"].values()
    ]
    key = load_custody_private_key(path, repository_root=roots[0])
    resolved = path.expanduser().resolve()
    if any(resolved == root or root in resolved.parents for root in roots):
        raise PilotEvidenceError("custody_key_inside_repository")
    if _public_key(key) != expected_public_key:
        raise PilotEvidenceError("custody_key_trust_anchor_mismatch")
    return key


def close_population(
    *,
    registration: object,
    closed_at: str,
    source_private_key_path: Path,
    evidence_private_key_path: Path,
    output_path: Path,
) -> dict[str, Any]:
    registered = _validate_registration(registration)
    source_key = _registered_private_key(
        source_private_key_path,
        registration=registered,
        expected_public_key=registered["trusted_source_public_key"],
    )
    evidence_key = _registered_private_key(
        evidence_private_key_path,
        registration=registered,
        expected_public_key=registered["trusted_evidence_public_key"],
    )
    starts = _scan_registered_starts(registered)
    source = build_source_population_receipt(
        private_key=source_key,
        study_id=registered["study_id"],
        closed_at=closed_at,
        starts=starts,
    )
    inventory = build_inventory_receipt(
        private_key=evidence_key,
        study_id=registered["study_id"],
        source_kind="natural",
        closed_at=closed_at,
        starts=starts,
        source_population_receipt=source,
    )
    _write_once_json(output_path, inventory)
    return inventory


def record_executor_start(
    *,
    registration: object,
    repo_id: str,
    session_id: str,
    private_key_path: Path,
    executor_surface: str,
) -> dict[str, Any]:
    registered = _validate_registration(registration)
    try:
        root = Path(registered["registered_repository_paths"][repo_id]).resolve()
        expected_key = registered["trusted_start_public_keys"][repo_id]
    except KeyError as error:
        raise PilotEvidenceError("repository_registration_missing") from error
    private_key = load_custody_private_key(private_key_path, repository_root=root)
    if _public_key(private_key) != expected_key:
        raise PilotEvidenceError("custody_key_trust_anchor_mismatch")
    start_path = root / ".omc/state/sessions" / session_id / "capture/start.json"
    try:
        start = omc_state._verify_capture(
            _read_regular_json(start_path, "start_receipt_invalid"), expected_key
        )
    except PilotEvidenceError:
        raise
    except (KeyError, OSError, TypeError, ValueError) as error:
        raise PilotEvidenceError("start_receipt_invalid") from error
    started_at = _parse_timestamp(start.get("started_at"), "start_receipt_invalid")
    recorded_at = _utc_now()
    if not (
        started_at
        <= recorded_at
        <= started_at
        + timedelta(
            seconds=registered["executor_start_recording_max_delay_seconds"]
        )
    ):
        raise PilotEvidenceError("executor_start_recording_late")
    receipt = build_executor_start_receipt(
        private_key=private_key,
        start_receipt=start,
        executor_surface=executor_surface,
        recorded_at=recorded_at.isoformat(),
    )
    _write_once_json(start_path.parent / "executor-start.json", receipt)
    return receipt


def close_case(
    *,
    registration: object,
    work_id: str,
    completion_at: str | None,
    correction_observed_through: str | None,
    corrections: object,
    execution_receipt: object,
    source_private_key_path: Path,
    evidence_private_key_path: Path,
    repository_private_key_path: Path,
    output_path: Path,
    completed: bool = True,
    incomplete_attribution: str | None = None,
) -> dict[str, Any]:
    registered = _validate_registration(registration)
    if not isinstance(corrections, list):
        raise PilotEvidenceError("correction_evidence_invalid")
    matches = [
        item for item in _scan_registered_starts(registered)
        if item.get("work_id") == work_id
    ]
    if len(matches) != 1:
        raise PilotEvidenceError("start_receipt_invalid")
    start = matches[0]
    repo_id = str(start["repo_id"])
    root = Path(registered["registered_repository_paths"][repo_id]).resolve()
    repository_key = load_custody_private_key(
        repository_private_key_path, repository_root=root
    )
    if _public_key(repository_key) != registered["trusted_start_public_keys"][repo_id]:
        raise PilotEvidenceError("custody_key_trust_anchor_mismatch")
    source_key = _registered_private_key(
        source_private_key_path,
        registration=registered,
        expected_public_key=registered["trusted_source_public_key"],
    )
    evidence_key = _registered_private_key(
        evidence_private_key_path,
        registration=registered,
        expected_public_key=registered["trusted_evidence_public_key"],
    )
    source_start = start["source_session_receipt"]
    capture_dir = (
        root / ".omc/state/sessions" / str(source_start["session_id"]) / "capture"
    )
    # Translate only evidence-loading/validation failures, before any writes.
    # Other collector/programming errors must not be hidden by a CLI-wide catch.
    try:
        verification = omc_state._verify_capture(
            _read_regular_json(capture_dir / "verification.json", "verification_receipt_invalid"),
            registered["trusted_start_public_keys"][repo_id],
        )
        if (
            verification.get("session_id") != source_start["session_id"]
            or verification.get("work_id") != work_id
            or verification.get("start_capture_sha256") != source_start["capture_sha256"]
        ):
            raise PilotEvidenceError("verification_receipt_invalid")
        raw_output = _verified_artifact_output(
            capture_dir, verification, reason="verification_receipt_invalid",
        )
        results = verification.get("results")
        if (
            not isinstance(verification.get("verification_passed"), bool)
            or not isinstance(verification.get("tree_unchanged"), bool)
            or verification.get("status") != "VERIFIED"
            or not isinstance(results, list)
            or not results
            or any(not isinstance(result, dict) for result in results)
            or (
                completed
                and (
                    verification.get("verification_passed") is not True
                    or verification.get("tree_unchanged") is not True
                    or any(
                        result.get("exit_code") != 0
                        or result.get("timed_out") is not False
                        or result.get("output_overflow") is not False
                        for result in results
                    )
                )
            )
        ):
            raise PilotEvidenceError("verification_receipt_invalid")
    except PilotEvidenceError:
        raise
    except (KeyError, OSError, TypeError, ValueError) as error:
        raise PilotEvidenceError("verification_receipt_invalid") from error
    execution = verify_execution_receipt(
        execution_receipt,
        trusted_public_key=registered["trusted_execution_public_key"],
        require_success=completed,
    )
    started = _parse_timestamp(start["started_at"], "case_timeline_invalid")
    executed = _parse_timestamp(execution["produced_at"], "case_timeline_invalid")
    executor_started = _parse_timestamp(
        start["executor_start_receipt"].get("recorded_at"),
        "case_timeline_invalid",
    )
    terminal_path = capture_dir / "case-terminal.json"
    if terminal_path.exists():
        existing_terminal = _verify_evidence(
            _read_regular_json(terminal_path, "case_terminal_invalid"),
            trusted_public_key=registered["trusted_start_public_keys"][repo_id],
            signer="acceptability_repository_case_closure",
            reason="case_terminal_invalid",
        )
        closed_at = _parse_timestamp(
            existing_terminal.get("closed_at"), "case_terminal_invalid"
        )
    else:
        closed_at = _utc_now()
    if (
        execution.get("work_id") != work_id
        or execution.get("request_sha256") != start.get("request_sha256")
        or execution.get("executor_start_receipt_sha256")
        != start.get("executor_start_receipt_sha256")
        or not (started <= executor_started <= executed <= closed_at)
    ):
        raise PilotEvidenceError("case_timeline_invalid")
    if completed:
        completed_at = _parse_timestamp(completion_at, "case_timeline_invalid")
        observed = _parse_timestamp(
            correction_observed_through, "case_timeline_invalid"
        )
        if (
            incomplete_attribution is not None
            or not (executed <= completed_at)
            or observed != completed_at + timedelta(hours=24)
        ):
            raise PilotEvidenceError("case_timeline_invalid")
        if closed_at < observed:
            raise PilotEvidenceError("case_closure_too_early")
        for correction in corrections:
            if (
                not isinstance(correction, dict)
                or not isinstance(correction.get("taxonomy"), str)
                or correction.get("taxonomy")
                not in PRIMARY_TAXONOMY | NON_PRIMARY_TAXONOMY
                or not isinstance(correction.get("raw_followup"), str)
                or not correction["raw_followup"].strip()
            ):
                raise PilotEvidenceError("correction_evidence_invalid")
            correction_at = _parse_timestamp(
                correction.get("at"), "correction_evidence_invalid"
            )
            if not (completed_at <= correction_at <= observed <= closed_at):
                raise PilotEvidenceError("correction_timestamp_outside_window")
    elif (
        completion_at is not None
        or correction_observed_through is not None
        or not isinstance(incomplete_attribution, str)
        or incomplete_attribution not in {"omc", "non_omc"}
        or corrections
    ):
        raise PilotEvidenceError("incomplete_case_invalid")
    closed_at_text = closed_at.isoformat()
    verification_status = (
        "passed" if verification.get("verification_passed") else "failed"
    )
    terminal = _seal_evidence(
        {
            "schema_version": "omc-absolute-acceptability-case-closure/v1",
            "status": "CASE_CLOSED",
            "session_id": source_start["session_id"],
            "work_id": work_id,
            "completed": completed,
            "completion_at": completion_at,
            "incomplete_attribution": incomplete_attribution,
            "correction_observed_through": correction_observed_through,
            "corrections": corrections,
            "closed_at": closed_at_text,
            "verification_capture_sha256": verification["capture_sha256"],
        },
        private_key=repository_key,
        signer="acceptability_repository_case_closure",
    )
    source_case = build_source_case_receipt(
        private_key=source_key,
        study_id=registered["study_id"],
        work_id=work_id,
        source_kind="natural",
        completed=completed,
        completion_at=completion_at,
        verification_status=verification_status,
        verification_raw_output=raw_output,
        incomplete_attribution=incomplete_attribution,
        correction_observed_through=correction_observed_through,
        corrections=corrections,
        closed_at=closed_at_text,
    )
    case = build_case_receipt(
        private_key=evidence_key,
        study_id=registered["study_id"],
        work_id=work_id,
        source_kind="natural",
        completed=completed,
        completion_at=completion_at,
        verification_status=verification_status,
        verification_raw_output=raw_output,
        incomplete_attribution=incomplete_attribution,
        correction_observed_through=correction_observed_through,
        corrections=corrections,
        execution_receipt_sha256=execution["receipt_sha256"],
        source_case_receipt=source_case,
        closed_at=closed_at_text,
    ) | {"execution_receipt": execution}
    _write_once_or_match(terminal_path, terminal)
    _write_once_or_match(capture_dir / "source-case.json", source_case)
    _write_once_or_match(output_path, case)
    return case


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    evaluate = commands.add_parser("evaluate")
    evaluate.add_argument("--registration", type=Path, required=True)
    evaluate.add_argument("--approved-registration-sha256", required=True)
    evaluate.add_argument("--inventory-receipt", type=Path, required=True)
    evaluate.add_argument("--case-receipts", type=Path, required=True)
    evaluate.add_argument("--as-of", required=True)
    sidecar = commands.add_parser("run-codex-sidecar")
    sidecar.add_argument("--registration", type=Path, required=True)
    sidecar.add_argument("--private-key-file", type=Path, required=True)
    sidecar.add_argument("--repository-root", type=Path, required=True)
    sidecar.add_argument("--work-id", required=True)
    sidecar.add_argument("--request", required=True)
    sidecar.add_argument("--executor-start-receipt", type=Path, required=True)
    sidecar.add_argument("--output", type=Path, required=True)
    executor_start = commands.add_parser("executor-start")
    executor_start.add_argument("--registration", type=Path, required=True)
    executor_start.add_argument("--repo-id", required=True)
    executor_start.add_argument("--session-id", required=True)
    executor_start.add_argument("--private-key-file", type=Path, required=True)
    executor_start.add_argument(
        "--executor-surface", choices=["codex_cli_json", "claude_code"], required=True
    )
    population = commands.add_parser("population-close")
    population.add_argument("--registration", type=Path, required=True)
    population.add_argument("--closed-at", required=True)
    population.add_argument("--source-private-key-file", type=Path, required=True)
    population.add_argument("--evidence-private-key-file", type=Path, required=True)
    population.add_argument("--output", type=Path, required=True)
    case = commands.add_parser("case-close")
    case.add_argument("--registration", type=Path, required=True)
    case.add_argument("--work-id", required=True)
    case.add_argument("--completion-at")
    case.add_argument("--correction-observed-through")
    case.add_argument("--incomplete-attribution", choices=["omc", "non_omc"])
    case.add_argument("--corrections", type=Path, required=True)
    case.add_argument("--execution-receipt", type=Path, required=True)
    case.add_argument("--source-private-key-file", type=Path, required=True)
    case.add_argument("--evidence-private-key-file", type=Path, required=True)
    case.add_argument("--repository-private-key-file", type=Path, required=True)
    case.add_argument("--output", type=Path, required=True)
    return parser


def main() -> int:
    args = _parser().parse_args()
    try:
        if args.command == "run-codex-sidecar":
            report = run_codex_json_sidecar(
                registration=_load_json(args.registration),
                private_key_path=args.private_key_file,
                repository_root=args.repository_root,
                work_id=args.work_id,
                request=args.request,
                output_path=args.output,
                executor_start_receipt=_load_json(args.executor_start_receipt),
            )
        elif args.command == "evaluate":
            report = evaluate_study(
                registration=_load_json(args.registration),
                approved_registration_sha256=args.approved_registration_sha256,
                inventory_receipt=_load_json(args.inventory_receipt),
                case_receipts=_load_json(args.case_receipts),
                as_of=args.as_of,
            )
        elif args.command == "executor-start":
            report = record_executor_start(
                registration=_load_json(args.registration),
                repo_id=args.repo_id,
                session_id=args.session_id,
                private_key_path=args.private_key_file,
                executor_surface=args.executor_surface,
            )
        elif args.command == "population-close":
            report = close_population(
                registration=_load_json(args.registration),
                closed_at=args.closed_at,
                source_private_key_path=args.source_private_key_file,
                evidence_private_key_path=args.evidence_private_key_file,
                output_path=args.output,
            )
        else:
            report = close_case(
                registration=_load_json(args.registration),
                work_id=args.work_id,
                completion_at=args.completion_at,
                correction_observed_through=args.correction_observed_through,
                corrections=_load_json(args.corrections),
                execution_receipt=_load_json(args.execution_receipt),
                source_private_key_path=args.source_private_key_file,
                evidence_private_key_path=args.evidence_private_key_file,
                repository_private_key_path=args.repository_private_key_file,
                output_path=args.output,
                completed=args.incomplete_attribution is None,
                incomplete_attribution=args.incomplete_attribution,
            )
    except PilotEvidenceError as error:
        report = _blocked([str(error)])
    print(json.dumps(report, ensure_ascii=False, sort_keys=True))
    return 2 if report.get("outcome") == "OBSERVATION_INCONCLUSIVE" else 0


if __name__ == "__main__":
    raise SystemExit(main())
