#!/usr/bin/env python3
"""User-local, consent-based registry of successful OMC installations."""
from __future__ import annotations

import argparse
import fcntl
import hashlib
import json
import os
import stat
import sys
from datetime import UTC, datetime
from contextlib import contextmanager
from pathlib import Path
from typing import Any

from omc_version import capture_source_identity


SCHEMA_VERSION = 1
CONSENT_FILE = "installation-registry-consent-v1.json"
LEDGER_FILE = "installation-registry-v1.jsonl"
LOCK_FILE = "installation-registry-v1.lock"
LEGACY_ARTIFACTS = (
    Path(".omc") / "observed-completion-v1.json",
    Path(".omc") / "observed-completion-v1.jsonl",
)


class InstallationRegistryError(ValueError):
    """A registry input or local-state contract was not satisfied."""


def _canonical_json(payload: dict[str, Any]) -> bytes:
    return json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")


def _sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _registry_dir() -> Path:
    raw = os.environ.get("OMC_INSTALLATION_REGISTRY_DIR")
    path = Path(raw) if raw else Path.home() / ".local" / "share" / "omc"
    if not path.is_absolute():
        raise InstallationRegistryError("state_directory_invalid")
    return path


def _validate_state_dir(*, create: bool) -> Path:
    state_dir = _registry_dir()
    if state_dir.is_symlink():
        raise InstallationRegistryError("state_directory_invalid")
    if state_dir.exists():
        if not state_dir.is_dir():
            raise InstallationRegistryError("state_directory_invalid")
    elif create:
        state_dir.mkdir(mode=0o700, parents=True)
    return state_dir


def _regular_file(path: Path) -> bool:
    try:
        return stat.S_ISREG(path.lstat().st_mode) and not path.is_symlink()
    except OSError:
        return False


def _write_private_json_once(path: Path, payload: dict[str, Any]) -> None:
    if path.exists() and not _regular_file(path):
        raise InstallationRegistryError("registry_artifact_invalid")
    temp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    try:
        fd = os.open(temp, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, "wb") as handle:
            handle.write(_canonical_json(payload) + b"\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp, path)
        os.chmod(path, 0o600)
    finally:
        if temp.exists():
            temp.unlink(missing_ok=True)


def _read_json(path: Path) -> dict[str, Any] | None:
    if not path.exists():
        return None
    if not _regular_file(path):
        raise InstallationRegistryError("registry_artifact_invalid")
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise InstallationRegistryError("registry_artifact_invalid") from exc
    if not isinstance(payload, dict):
        raise InstallationRegistryError("registry_artifact_invalid")
    return payload


def _is_enabled(*, tolerate_state_error: bool = False) -> bool:
    try:
        state_dir = _validate_state_dir(create=False)
    except InstallationRegistryError:
        if tolerate_state_error:
            return False
        raise
    payload = _read_json(state_dir / CONSENT_FILE)
    return payload == {"enabled": True, "schema_version": SCHEMA_VERSION}


def enable() -> dict[str, Any]:
    state_dir = _validate_state_dir(create=True)
    consent = state_dir / CONSENT_FILE
    existing = _read_json(consent)
    if existing is not None and existing != {"enabled": True, "schema_version": SCHEMA_VERSION}:
        raise InstallationRegistryError("registry_consent_invalid")
    if existing is None:
        _write_private_json_once(consent, {"enabled": True, "schema_version": SCHEMA_VERSION})
    return {"enabled": True, "status": "enabled"}


def _target_path(target: Path) -> Path:
    if not target.is_absolute() or target.is_symlink() or not target.is_dir():
        raise InstallationRegistryError("target_invalid")
    return target.resolve(strict=True)


def _target_fingerprint(target: Path) -> str:
    return _sha256_bytes(str(target).encode("utf-8"))


def _read_records() -> list[dict[str, Any]]:
    state_dir = _validate_state_dir(create=False)
    ledger = state_dir / LEDGER_FILE
    if not ledger.exists():
        return []
    if not _regular_file(ledger):
        raise InstallationRegistryError("registry_artifact_invalid")
    records: list[dict[str, Any]] = []
    previous: str | None = None
    try:
        lines = ledger.read_text(encoding="utf-8").splitlines()
    except (OSError, UnicodeDecodeError) as exc:
        raise InstallationRegistryError("registry_artifact_invalid") from exc
    for raw in lines:
        try:
            record = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise InstallationRegistryError("registry_integrity_invalid") from exc
        if not isinstance(record, dict):
            raise InstallationRegistryError("registry_integrity_invalid")
        digest = record.get("entry_sha256")
        unsigned = {key: value for key, value in record.items() if key != "entry_sha256"}
        if (
            record.get("schema_version") != SCHEMA_VERSION
            or not isinstance(digest, str)
            or record.get("previous_entry_sha256") != previous
            or _sha256_bytes(_canonical_json(unsigned)) != digest
        ):
            raise InstallationRegistryError("registry_integrity_invalid")
        records.append(record)
        previous = digest
    return records


def _append_record(record: dict[str, Any]) -> None:
    state_dir = _validate_state_dir(create=True)
    ledger = state_dir / LEDGER_FILE
    if ledger.exists() and not _regular_file(ledger):
        raise InstallationRegistryError("registry_artifact_invalid")
    fd = os.open(ledger, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
    try:
        with os.fdopen(fd, "ab") as handle:
            handle.write(_canonical_json(record) + b"\n")
            handle.flush()
            os.fsync(handle.fileno())
    finally:
        if ledger.exists():
            os.chmod(ledger, 0o600)


@contextmanager
def _registry_lock() -> Any:
    state_dir = _validate_state_dir(create=True)
    lock_path = state_dir / LOCK_FILE
    if lock_path.exists() and not _regular_file(lock_path):
        raise InstallationRegistryError("registry_artifact_invalid")
    fd = os.open(lock_path, os.O_WRONLY | os.O_CREAT, 0o600)
    try:
        with os.fdopen(fd, "a") as handle:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
            yield
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
    finally:
        if lock_path.exists():
            os.chmod(lock_path, 0o600)


def record_successful_setup(*, target: Path, source_kit: Path) -> dict[str, Any]:
    """Append an entry only after the root setup command has passed strict audit."""
    if not _is_enabled(tolerate_state_error=True):
        return {"status": "disabled"}
    canonical_target = _target_path(target)
    source = capture_source_identity(source_kit.resolve(strict=True))
    fingerprint = _target_fingerprint(canonical_target)
    with _registry_lock():
        records = _read_records()
        latest = next((item for item in reversed(records) if item["target_fingerprint"] == fingerprint), None)
        if latest and latest["source_sha256"] == source.sha256:
            return {"status": "unchanged"}
        unsigned: dict[str, Any] = {
            "schema_version": SCHEMA_VERSION,
            "recorded_at": datetime.now(UTC).isoformat(),
            "target_path": str(canonical_target),
            "target_fingerprint": fingerprint,
            "source_version": source.version,
            "source_sha256": source.sha256,
            "source_revision": source.revision,
            "previous_entry_sha256": records[-1]["entry_sha256"] if records else None,
        }
        record = {**unsigned, "entry_sha256": _sha256_bytes(_canonical_json(unsigned))}
        _append_record(record)
        return {"status": "recorded", "registry_id": fingerprint}


def status() -> dict[str, Any]:
    if not _is_enabled(tolerate_state_error=True):
        return {"enabled": False, "registered_target_count": 0, "status": "disabled"}
    latest_by_target = {item["target_fingerprint"]: item for item in _read_records()}
    return {
        "enabled": True,
        "registered_target_count": len(latest_by_target),
        "status": "ok",
    }


def audit_legacy() -> dict[str, Any]:
    if not _is_enabled(tolerate_state_error=True):
        return {"schema_version": SCHEMA_VERSION, "status": "disabled", "targets": []}
    latest_by_target = {item["target_fingerprint"]: item for item in _read_records()}
    targets: list[dict[str, str]] = []
    for fingerprint, record in sorted(latest_by_target.items()):
        target = Path(record["target_path"])
        if target.is_symlink() or not target.is_dir():
            state = "SOURCE_UNAVAILABLE"
        else:
            artifacts = [target / artifact for artifact in LEGACY_ARTIFACTS]
            if any(path.exists() and not _regular_file(path) for path in artifacts):
                state = "INTEGRITY_INVALID"
            elif any(_regular_file(path) for path in artifacts):
                state = "LEGACY_PRESENT"
            else:
                state = "LEGACY_MISSING"
        targets.append({"registry_id": fingerprint, "state": state})
    return {"schema_version": SCHEMA_VERSION, "status": "ok", "targets": targets}


def _emit(payload: dict[str, Any]) -> None:
    print(json.dumps(payload, sort_keys=True))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("enable")
    sub.add_parser("status")
    sub.add_parser("audit-legacy")
    record = sub.add_parser("record-successful-setup")
    record.add_argument("--target", type=Path, required=True)
    record.add_argument("--source-kit", type=Path, required=True)
    args = parser.parse_args()
    try:
        if args.command == "enable":
            _emit(enable())
        elif args.command == "status":
            _emit(status())
        elif args.command == "audit-legacy":
            _emit(audit_legacy())
        else:
            _emit(record_successful_setup(target=args.target, source_kit=args.source_kit))
    except InstallationRegistryError as exc:
        _emit({"status": "blocked", "reason_code": str(exc)})
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
