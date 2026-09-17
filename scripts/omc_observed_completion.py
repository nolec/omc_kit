#!/usr/bin/env python3
"""Local-only, raw-free completion observations across opted-in repositories."""
from __future__ import annotations

import argparse
import base64
import hashlib
import json
import os
import re
import subprocess
import sys
import uuid
from pathlib import Path
from typing import Any

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import (
    Ed25519PrivateKey,
    Ed25519PublicKey,
)

import omc_state


SCHEMA_VERSION = 1
CONFIG_NAME = "observed-completion-v1.json"
LEDGER_NAME = "observed-completion-v1.jsonl"
_HEX40 = re.compile(r"[0-9a-f]{40}")
_WORK_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9_-]{2,127}")
_OUTCOMES = frozenset({"accepted", "correction", "deferred", "unknown"})
_REVIEW_VERDICTS = frozenset({"APPROVE", "REVISE", "BLOCK", "NOT_RUN"})
_REVIEW_SIGNER = "observed-completion-review-v1"


class CompletionObservationError(ValueError):
    """A local observation is unusable and must not contribute to a report."""


def _canonical_bytes(value: object) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")


def _sha256(value: object) -> str:
    return hashlib.sha256(_canonical_bytes(value)).hexdigest()


def _review_digest(document: dict[str, Any]) -> str:
    payload = json.loads(json.dumps(document))
    payload["review_sha256"] = ""
    payload["signoff"]["signature"] = ""
    return _sha256(payload)


def _decode_public_key(value: object) -> bytes:
    if not isinstance(value, str):
        raise CompletionObservationError("trusted_key_invalid")
    try:
        raw = base64.b64decode(value, validate=True)
    except (ValueError, TypeError) as error:
        raise CompletionObservationError("trusted_key_invalid") from error
    if len(raw) != 32:
        raise CompletionObservationError("trusted_key_invalid")
    return raw


def _regular_file(path: Path) -> None:
    if path.is_symlink() or not path.is_file():
        raise CompletionObservationError("not_regular_file")


def _load_private_key(path: Path) -> Ed25519PrivateKey:
    _regular_file(path)
    try:
        encoded = path.read_text(encoding="utf-8").strip()
        raw = base64.b64decode(encoded, validate=True)
        if len(raw) != 32:
            raise ValueError("unexpected private key length")
        return Ed25519PrivateKey.from_private_bytes(raw)
    except (OSError, UnicodeDecodeError, ValueError, TypeError) as error:
        raise CompletionObservationError("signer_private_key_invalid") from error


def _read_json(path: Path) -> dict[str, Any]:
    _regular_file(path)
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as error:
        raise CompletionObservationError("invalid_json") from error
    if not isinstance(value, dict):
        raise CompletionObservationError("invalid_json_object")
    return value


def _write_once(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists() or path.is_symlink():
        raise CompletionObservationError("write_once_conflict")
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
            handle.write(json.dumps(value, ensure_ascii=False, sort_keys=True))
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


def config_path(project_root: Path) -> Path:
    return project_root / ".omc" / CONFIG_NAME


def ledger_path(project_root: Path) -> Path:
    return project_root / ".omc" / LEDGER_NAME


def _omc_directory(project_root: Path, *, create: bool) -> Path:
    directory = project_root / ".omc"
    if directory.is_symlink() or (directory.exists() and not directory.is_dir()):
        raise CompletionObservationError("omc_directory_invalid")
    if create:
        directory.mkdir(parents=False, exist_ok=True)
    return directory


def repository_fingerprint(project_root: Path) -> str:
    """Hash only the canonical origin identity; never persist the origin itself."""
    if project_root.is_symlink() or not project_root.is_dir():
        raise CompletionObservationError("source_not_regular_directory")
    result = subprocess.run(
        ["git", "-C", str(project_root), "config", "--get", "remote.origin.url"],
        check=False,
        capture_output=True,
        text=True,
    )
    remote = result.stdout.strip()
    if result.returncode != 0 or not remote:
        raise CompletionObservationError("repository_identity_unavailable")
    return hashlib.sha256(remote.encode("utf-8")).hexdigest()


def _validate_config(
    project_root: Path, *, allow_legacy_read: bool = False
) -> tuple[dict[str, Any], bool]:
    _omc_directory(project_root, create=False)
    path = config_path(project_root)
    if not path.exists() and not path.is_symlink():
        raise CompletionObservationError("repository_not_enabled")
    config = _read_json(path)
    expected = {
        "schema_version", "enabled", "repo_fingerprint", "trusted_terminal_public_key",
        "trusted_review_public_key",
    }
    fingerprint = config.get("repo_fingerprint")
    trusted_key = config.get("trusted_terminal_public_key")
    trusted_review_key = config.get("trusted_review_public_key")
    if (
        set(config) != expected
        or config.get("schema_version") != SCHEMA_VERSION
        or config.get("enabled") is not True
        or not isinstance(fingerprint, str)
        or re.fullmatch(r"[0-9a-f]{64}", fingerprint) is None
        or fingerprint != repository_fingerprint(project_root)
    ):
        raise CompletionObservationError("repository_config_invalid")
    try:
        terminal_public_key = _decode_public_key(trusted_key)
        review_public_key = _decode_public_key(trusted_review_key)
    except CompletionObservationError as error:
        raise CompletionObservationError("repository_config_invalid") from error
    if terminal_public_key == review_public_key:
        if not allow_legacy_read:
            raise CompletionObservationError("review_trust_anchor_not_independent")
        return config, True
    return config, False


def enable(
    project_root: Path,
    *,
    trusted_terminal_public_key: str,
    trusted_review_public_key: str,
) -> dict[str, Any]:
    if project_root.is_symlink():
        raise CompletionObservationError("source_not_regular_directory")
    project_root = project_root.resolve(strict=True)
    if _decode_public_key(trusted_terminal_public_key) == _decode_public_key(trusted_review_public_key):
        raise CompletionObservationError("review_trust_anchor_not_independent")
    _omc_directory(project_root, create=True)
    expected = {
        "schema_version": SCHEMA_VERSION,
        "enabled": True,
        "repo_fingerprint": repository_fingerprint(project_root),
        "trusted_terminal_public_key": trusted_terminal_public_key,
        "trusted_review_public_key": trusted_review_public_key,
    }
    path = config_path(project_root)
    if path.exists() or path.is_symlink():
        if _read_json(path) != expected:
            raise CompletionObservationError("repository_config_conflict")
        return expected
    _write_once(path, expected)
    return expected


def _derived_terminal_state(
    *, verification_passed: bool, review_verdict: str
) -> tuple[str, str | None]:
    if not verification_passed:
        return "OBSERVED_INCOMPLETE", "verification_failed"
    if review_verdict != "APPROVE":
        return "OBSERVED_INCOMPLETE", "review_not_approved"
    return "OBSERVED_COMPLETE", None


def _validate_event(
    event: object,
    *,
    expected_fingerprint: str,
    previous_hash: str | None,
    allow_legacy_read: bool = False,
) -> dict[str, Any]:
    if not isinstance(event, dict):
        raise CompletionObservationError("event_not_object")
    outcome_field = "terminal_reported_outcome"
    modern_expected_keys = {
        "schema_version", "event_id", "observed_at", "repo_fingerprint", "work_id",
        "baseline_commit", "terminal_sha256", "review_sha256", "status", "verification", "review", outcome_field,
        "incomplete_reason", "previous_event_sha256", "event_sha256",
    }
    expected_keys = modern_expected_keys
    if allow_legacy_read:
        outcome_field = "user_outcome"
        expected_keys = (modern_expected_keys - {"terminal_reported_outcome"}) | {outcome_field}
    if set(event) != expected_keys:
        if allow_legacy_read and set(event) == modern_expected_keys:
            raise CompletionObservationError("review_trust_anchor_not_independent")
        raise CompletionObservationError("event_schema_invalid")
    verification = event.get("verification")
    review = event.get("review")
    if (
        event.get("schema_version") != SCHEMA_VERSION
        or not isinstance(event.get("event_id"), str)
        or not isinstance(event.get("observed_at"), str)
        or event.get("repo_fingerprint") != expected_fingerprint
        or not isinstance(event.get("work_id"), str)
        or _WORK_ID.fullmatch(event["work_id"]) is None
        or not isinstance(event.get("baseline_commit"), str)
        or _HEX40.fullmatch(event["baseline_commit"]) is None
        or not isinstance(event.get("terminal_sha256"), str)
        or re.fullmatch(r"[0-9a-f]{64}", event["terminal_sha256"]) is None
        or not isinstance(event.get("review_sha256"), str)
        or re.fullmatch(r"[0-9a-f]{64}", event["review_sha256"]) is None
        or not isinstance(verification, dict)
        or set(verification) != {"id", "passed"}
        or verification.get("id") != "sealed_terminal"
        or not isinstance(verification.get("passed"), bool)
        or not isinstance(review, dict)
        or set(review) != {"verdict"}
        or review.get("verdict") not in _REVIEW_VERDICTS
        or event.get(outcome_field) not in _OUTCOMES
        or event.get("previous_event_sha256") != previous_hash
        or not isinstance(event.get("event_sha256"), str)
        or re.fullmatch(r"[0-9a-f]{64}", event["event_sha256"]) is None
    ):
        raise CompletionObservationError("event_contract_invalid")
    status, reason = _derived_terminal_state(
        verification_passed=verification["passed"], review_verdict=review["verdict"]
    )
    if event.get("status") != status or event.get("incomplete_reason") != reason:
        raise CompletionObservationError("event_status_invalid")
    unsigned = dict(event)
    unsigned.pop("event_sha256")
    if _sha256(unsigned) != event["event_sha256"]:
        raise CompletionObservationError("event_hash_invalid")
    return event


def _load_events(
    project_root: Path, *, require_ledger: bool, allow_legacy_read: bool = False
) -> list[dict[str, Any]]:
    config, legacy_config = _validate_config(
        project_root, allow_legacy_read=allow_legacy_read
    )
    path = ledger_path(project_root)
    if not path.exists() and not path.is_symlink():
        if require_ledger:
            raise CompletionObservationError("ledger_missing")
        return []
    _regular_file(path)
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except (OSError, UnicodeDecodeError) as error:
        raise CompletionObservationError("ledger_unreadable") from error
    if not lines:
        raise CompletionObservationError("ledger_empty")
    events: list[dict[str, Any]] = []
    previous_hash: str | None = None
    work_ids: set[str] = set()
    for line in lines:
        try:
            event = json.loads(line)
        except json.JSONDecodeError as error:
            raise CompletionObservationError("ledger_json_invalid") from error
        checked = _validate_event(
            event,
            expected_fingerprint=config["repo_fingerprint"],
            previous_hash=previous_hash,
            allow_legacy_read=legacy_config,
        )
        if checked["work_id"] in work_ids:
            raise CompletionObservationError("duplicate_work_id")
        work_ids.add(checked["work_id"])
        previous_hash = checked["event_sha256"]
        events.append(checked)
    return events


def _append_event(path: Path, event: dict[str, Any]) -> None:
    if path.parent.is_symlink() or not path.parent.is_dir():
        raise CompletionObservationError("omc_directory_invalid")
    if path.is_symlink():
        raise CompletionObservationError("ledger_not_regular_file")
    descriptor = os.open(
        path,
        os.O_WRONLY | os.O_CREAT | os.O_APPEND | getattr(os, "O_NOFOLLOW", 0),
        0o600,
    )
    try:
        payload = _canonical_bytes(event) + b"\n"
        os.write(descriptor, payload)
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def seal_review(
    *,
    work_id: str,
    terminal_sha256: str,
    verdict: str,
    private_key: Ed25519PrivateKey,
    reviewed_at: str,
) -> dict[str, Any]:
    if (
        _WORK_ID.fullmatch(work_id) is None
        or re.fullmatch(r"[0-9a-f]{64}", terminal_sha256) is None
        or verdict not in _REVIEW_VERDICTS
        or not reviewed_at
    ):
        raise CompletionObservationError("review_receipt_invalid")
    public_key = base64.b64encode(private_key.public_key().public_bytes_raw()).decode("ascii")
    receipt: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "work_id": work_id,
        "terminal_sha256": terminal_sha256,
        "verdict": verdict,
        "reviewed_at": reviewed_at,
        "review_sha256": "",
        "signoff": {
            "signer": _REVIEW_SIGNER,
            "signer_public_key": public_key,
            "signature": "",
        },
    }
    receipt["review_sha256"] = _review_digest(receipt)
    signed = json.loads(json.dumps(receipt))
    signed["signoff"]["signature"] = ""
    receipt["signoff"]["signature"] = base64.b64encode(
        private_key.sign(_canonical_bytes(signed))
    ).decode("ascii")
    return receipt


def _verify_review_receipt(document: object, trusted_public_key: str) -> dict[str, Any]:
    if not isinstance(document, dict):
        raise CompletionObservationError("review_receipt_invalid")
    expected = {
        "schema_version", "work_id", "terminal_sha256", "verdict", "reviewed_at",
        "review_sha256", "signoff",
    }
    signoff = document.get("signoff")
    if (
        set(document) != expected
        or document.get("schema_version") != SCHEMA_VERSION
        or not isinstance(document.get("work_id"), str)
        or _WORK_ID.fullmatch(document["work_id"]) is None
        or not isinstance(document.get("terminal_sha256"), str)
        or re.fullmatch(r"[0-9a-f]{64}", document["terminal_sha256"]) is None
        or document.get("verdict") not in _REVIEW_VERDICTS
        or not isinstance(document.get("reviewed_at"), str)
        or not isinstance(document.get("review_sha256"), str)
        or re.fullmatch(r"[0-9a-f]{64}", document["review_sha256"]) is None
        or not isinstance(signoff, dict)
        or set(signoff) != {"signer", "signer_public_key", "signature"}
        or signoff.get("signer") != _REVIEW_SIGNER
        or signoff.get("signer_public_key") != trusted_public_key
        or document["review_sha256"] != _review_digest(document)
    ):
        raise CompletionObservationError("review_receipt_invalid")
    signed = json.loads(json.dumps(document))
    signed["signoff"]["signature"] = ""
    try:
        Ed25519PublicKey.from_public_bytes(_decode_public_key(trusted_public_key)).verify(
            base64.b64decode(str(signoff.get("signature")), validate=True),
            _canonical_bytes(signed),
        )
    except (InvalidSignature, ValueError, TypeError) as error:
        raise CompletionObservationError("review_receipt_invalid") from error
    return document


def _terminal_projection(path: Path, trusted_public_key: str) -> dict[str, Any]:
    _regular_file(path)
    try:
        terminal_bytes = path.read_bytes()
        terminal = json.loads(terminal_bytes.decode("utf-8"))
        verified = omc_state._verify_capture(terminal, trusted_public_key)
    except (OSError, UnicodeDecodeError, json.JSONDecodeError, TypeError, ValueError) as error:
        raise CompletionObservationError("terminal_receipt_invalid") from error
    outcome = verified.get("user_outcome")
    review_by_outcome = {
        "accepted": ("APPROVE", "accepted"),
        "correction_required": ("REVISE", "correction"),
        "abandoned": ("BLOCK", "deferred"),
    }
    if (
        verified.get("status") != "COMPLETE"
        or verified.get("capture_validity") not in {"VALID", "INVALID"}
        or not isinstance(verified.get("work_id"), str)
        or not isinstance(verified.get("baseline_commit"), str)
        or _HEX40.fullmatch(verified["baseline_commit"]) is None
        or not isinstance(verified.get("verification_passed"), bool)
        or outcome not in review_by_outcome
        or not isinstance(verified.get("captured_at"), str)
    ):
        raise CompletionObservationError("terminal_receipt_invalid")
    _unused_review_verdict, terminal_reported_outcome = review_by_outcome[outcome]
    return {
        "work_id": verified["work_id"],
        "baseline_commit": verified["baseline_commit"],
        "terminal_sha256": hashlib.sha256(terminal_bytes).hexdigest(),
        "observed_at": verified["captured_at"],
        "verification": {"id": "sealed_terminal", "passed": verified["verification_passed"]},
        "terminal_reported_outcome": terminal_reported_outcome,
    }


def _review_projection(
    path: Path | None,
    *,
    trusted_public_key: str,
    terminal: dict[str, Any],
) -> dict[str, Any]:
    if path is None:
        raise CompletionObservationError("review_receipt_required")
    _regular_file(path)
    try:
        review_bytes = path.read_bytes()
        review = _verify_review_receipt(
            json.loads(review_bytes.decode("utf-8")), trusted_public_key
        )
    except (OSError, UnicodeDecodeError, json.JSONDecodeError, TypeError, ValueError) as error:
        if isinstance(error, CompletionObservationError):
            raise
        raise CompletionObservationError("review_receipt_invalid") from error
    if (
        review["work_id"] != terminal["work_id"]
        or review["terminal_sha256"] != terminal["terminal_sha256"]
    ):
        raise CompletionObservationError("review_receipt_invalid")
    return {"review": {"verdict": review["verdict"]}, "review_sha256": review["review_sha256"]}


def record_terminal(
    project_root: Path,
    *,
    terminal: Path,
    review: Path | None,
) -> dict[str, Any]:
    if project_root.is_symlink():
        raise CompletionObservationError("source_not_regular_directory")
    project_root = project_root.resolve(strict=True)
    with omc_state._omc_lock(project_root):
        config, _legacy_config = _validate_config(project_root)
        projection = _terminal_projection(terminal, config["trusted_terminal_public_key"])
        projection.update(_review_projection(
            review,
            trusted_public_key=config["trusted_review_public_key"],
            terminal=projection,
        ))
        existing = _load_events(project_root, require_ledger=False)
        if any(event["work_id"] == projection["work_id"] for event in existing):
            raise CompletionObservationError("duplicate_work_id")
        status, reason = _derived_terminal_state(
            verification_passed=projection["verification"]["passed"],
            review_verdict=projection["review"]["verdict"],
        )
        event: dict[str, Any] = {
            "schema_version": SCHEMA_VERSION,
            "event_id": uuid.uuid4().hex,
            "repo_fingerprint": config["repo_fingerprint"],
            "status": status,
            "incomplete_reason": reason,
            "previous_event_sha256": existing[-1]["event_sha256"] if existing else None,
            **projection,
        }
        event["event_sha256"] = _sha256(event)
        _validate_event(
            event,
            expected_fingerprint=config["repo_fingerprint"],
            previous_hash=event["previous_event_sha256"],
        )
        _append_event(ledger_path(project_root), event)
        return event


def _source_report(source: Path) -> dict[str, Any]:
    try:
        if not source.is_absolute() or source.is_symlink() or not source.is_dir():
            raise CompletionObservationError("source_not_regular_directory")
        root = source.resolve(strict=True)
        config = config_path(root)
        if not config.exists() and not config.is_symlink():
            return {"state": "UNOBSERVED"}
        events = _load_events(root, require_ledger=False, allow_legacy_read=True)
        if not events:
            return {"state": "UNOBSERVED"}
        latest = events[-1]
        return {
            "state": latest["status"],
            "repo_fingerprint": latest["repo_fingerprint"],
            "event_count": len(events),
            "incomplete_reasons": [
                event["incomplete_reason"] for event in events if event["incomplete_reason"] is not None
            ],
        }
    except CompletionObservationError as error:
        return {"state": "INTEGRITY_INVALID", "reason_code": str(error)}


def aggregate(sources: list[Path]) -> dict[str, Any]:
    if not sources:
        raise CompletionObservationError("source_required")
    reports = [_source_report(source) for source in sources]
    incomplete_reasons: dict[str, int] = {}
    for report in reports:
        for reason in report.get("incomplete_reasons", []):
            incomplete_reasons[reason] = incomplete_reasons.get(reason, 0) + 1
    return {
        "schema_version": SCHEMA_VERSION,
        "network_used": False,
        "sources": reports,
        "aggregate": {
            "observed_complete": sum(report["state"] == "OBSERVED_COMPLETE" for report in reports),
            "observed_incomplete": sum(report["state"] == "OBSERVED_INCOMPLETE" for report in reports),
            "unobserved": sum(report["state"] == "UNOBSERVED" for report in reports),
            "integrity_invalid": sum(report["state"] == "INTEGRITY_INVALID" for report in reports),
            "incomplete_reasons": dict(sorted(incomplete_reasons.items())),
        },
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    enable_cmd = sub.add_parser("enable", help="Opt one repository into raw-free local observation.")
    enable_cmd.add_argument("--target", type=Path, required=True)
    enable_cmd.add_argument("--trusted-terminal-public-key", required=True)
    enable_cmd.add_argument("--trusted-review-public-key", required=True)
    record = sub.add_parser("record-terminal", help="Append one terminal-only observation.")
    record.add_argument("--target", type=Path, required=True)
    record.add_argument("--terminal", type=Path, required=True)
    record.add_argument("--review", type=Path, required=True)
    seal = sub.add_parser("seal-review", help="Create one signed review receipt from a custody key file.")
    seal.add_argument("--work-id", required=True)
    seal.add_argument("--terminal-sha256", required=True)
    seal.add_argument("--verdict", required=True)
    seal.add_argument("--reviewed-at", required=True)
    seal.add_argument("--signer-private-key-file", type=Path, required=True)
    seal.add_argument("--out", type=Path, required=True)
    report = sub.add_parser("completion-report", help="Aggregate explicitly selected local repositories.")
    report.add_argument("--source", type=Path, action="append", required=True)
    args = parser.parse_args(argv)
    try:
        if args.command == "enable":
            print(json.dumps(enable(
                args.target,
                trusted_terminal_public_key=args.trusted_terminal_public_key,
                trusted_review_public_key=args.trusted_review_public_key,
            ), ensure_ascii=False, sort_keys=True))
        elif args.command == "record-terminal":
            print(json.dumps(record_terminal(
                args.target, terminal=args.terminal, review=args.review
            ), ensure_ascii=False, sort_keys=True))
        elif args.command == "seal-review":
            receipt = seal_review(
                work_id=args.work_id,
                terminal_sha256=args.terminal_sha256,
                verdict=args.verdict,
                private_key=_load_private_key(args.signer_private_key_file),
                reviewed_at=args.reviewed_at,
            )
            _write_once(args.out, receipt)
            print(json.dumps(receipt, ensure_ascii=False, sort_keys=True))
        else:
            print(json.dumps(aggregate(args.source), ensure_ascii=False, sort_keys=True))
    except CompletionObservationError as error:
        print(json.dumps({"status": "blocked", "reason_code": str(error)}, ensure_ascii=False))
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
