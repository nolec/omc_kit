#!/usr/bin/env python3
"""Publish and recover immutable Product Value evidence bundles."""

from __future__ import annotations

import argparse
import base64
import binascii
import ctypes
from copy import deepcopy
from datetime import date, datetime
import errno
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import secrets
import shutil
import stat
import sys
import tempfile
from typing import Any, Mapping

import omc_preregistration_registry as preregistration_registry


SCHEMA_VERSION = "omc-product-value-evidence-bundle/v1"
INDEX_FILENAME = "bundle-index.json"
REQUIRED_PATHS = {
    "preregistration.json",
    "registration-receipt.json",
    "runner/acceptance.py",
    "runner/arm-adapter.py",
    "runner/scheduler.py",
    "runner/executor-shadow.py",
    "runner/provider-adapter.py",
}
CLOSURE_PATHS = {
    "closure-subject.json",
    "closure-receipt.json",
    "registry-record.json",
}
CLOSURE_MISSING_ARTIFACTS = {
    "manifest",
    "workload_inventory",
    "execution_packets",
}
AUTHORITY_RECEIPT_SCHEMA_VERSION = "omc-product-value-authority-receipt/v1"


class MaterializedEvidenceBundle:
    """Own a verified, private snapshot for one acceptance process."""

    def __init__(
        self,
        runtime: tempfile.TemporaryDirectory[str],
        index: dict[str, Any],
        paths: dict[str, Path],
    ) -> None:
        self._runtime = runtime
        self.index = index
        self.paths = paths
        self.root = Path(runtime.name)

    def cleanup(self) -> None:
        self._runtime.cleanup()


def _canonical_sha256(value: Any) -> str:
    return hashlib.sha256(_canonical_bytes(value)).hexdigest()


def _canonical_bytes(value: Any) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")


def _is_sha256(value: Any) -> bool:
    return (
        isinstance(value, str)
        and len(value) == 64
        and set(value) <= set("0123456789abcdef")
    )


def prepare_failure_closure_subject(
    *,
    repository_root: str | Path,
    batch_id: str,
    preregistration_sha256: str,
    registry_path: str,
    registry_commit: str,
    diagnostic_receipt_sha256: str,
    closure_authority_sha256: str,
    closed_at: str,
    final_decision_deadline: str,
    missing_artifacts: tuple[str, ...],
) -> dict[str, Any]:
    """Prepare the immutable subject for an externally signed failed batch."""
    registry_record = _load_registry_record(
        repository_root,
        batch_id=batch_id,
        preregistration_sha256=preregistration_sha256,
        registry_path=registry_path,
        registry_commit=registry_commit,
    )
    subject = {
        "batch_id": batch_id,
        "preregistration_sha256": preregistration_sha256,
        "registry_record_sha256": _canonical_sha256(registry_record),
        "registry_path": registry_path,
        "registry_commit": registry_commit,
        "status": "BLOCKED",
        "reason_code": "evidence_loss",
        "missing_artifacts": list(missing_artifacts),
        "diagnostic_receipt_sha256": diagnostic_receipt_sha256,
        "closed_at": closed_at,
        "final_decision_deadline": final_decision_deadline,
        "closure_authority_sha256": closure_authority_sha256,
        "execution_eligible": False,
    }
    _validate_failure_closure_subject(subject)
    _validate_closure_registry_record(registry_record, subject)
    return subject


def _load_registry_record(
    repository_root: str | Path,
    *,
    batch_id: str,
    preregistration_sha256: str,
    registry_path: str,
    registry_commit: str,
    registry_fd: int | None = None,
) -> dict[str, Any]:
    try:
        root = Path(repository_root).expanduser().resolve(strict=True)
    except OSError as error:
        raise ValueError("preregistration registry anchor is invalid") from error
    if not root.is_dir():
        raise ValueError("preregistration registry anchor is invalid")
    expected_path = f".omc/registry/{batch_id}.json"
    if registry_path != expected_path:
        raise ValueError("preregistration registry anchor is invalid")
    owned_fd = registry_fd is None
    if registry_fd is None:
        _, registry_fd = _open_registry_directory(
            root,
            "preregistration registry anchor is invalid",
        )
    try:
        record = _load_regular_json_at(
            registry_fd,
            f"{batch_id}.json",
            missing_ok=False,
            reason="preregistration registry record is invalid",
        )
    finally:
        if owned_fd:
            os.close(registry_fd)
    if (
        not isinstance(record, dict)
        or record.get("batch_id") != batch_id
        or record.get("preregistration_sha256") != preregistration_sha256
    ):
        raise ValueError("preregistration registry record is invalid")
    preregistration_registry.validate_registry_anchor(
        record,
        repository_root=root,
        registry_commit=registry_commit,
        registry_path=registry_path,
    )
    return record


def _validate_failure_closure_subject(subject: Any) -> dict[str, Any]:
    expected_fields = {
        "batch_id",
        "preregistration_sha256",
        "registry_record_sha256",
        "registry_path",
        "registry_commit",
        "status",
        "reason_code",
        "missing_artifacts",
        "diagnostic_receipt_sha256",
        "closed_at",
        "final_decision_deadline",
        "closure_authority_sha256",
        "execution_eligible",
    }
    if not isinstance(subject, dict) or set(subject) != expected_fields:
        raise ValueError("failure_closure_subject_invalid")
    try:
        closed_at = datetime.fromisoformat(subject["closed_at"])
        deadline = date.fromisoformat(subject["final_decision_deadline"])
    except (TypeError, ValueError) as error:
        raise ValueError("failure_closure_subject_invalid") from error
    if (
        not isinstance(subject["batch_id"], str)
        or _validate_batch_id(subject["batch_id"]) != subject["batch_id"]
        or not _is_sha256(subject["preregistration_sha256"])
        or not _is_sha256(subject["registry_record_sha256"])
        or subject["registry_path"]
        != f".omc/registry/{subject['batch_id']}.json"
        or not isinstance(subject["registry_commit"], str)
        or len(subject["registry_commit"]) != 40
        or set(subject["registry_commit"]) - set("0123456789abcdef")
        or subject["status"] != "BLOCKED"
        or subject["reason_code"] != "evidence_loss"
        or not isinstance(subject["missing_artifacts"], list)
        or any(not isinstance(item, str) for item in subject["missing_artifacts"])
        or set(subject["missing_artifacts"]) != CLOSURE_MISSING_ARTIFACTS
        or len(subject["missing_artifacts"]) != len(CLOSURE_MISSING_ARTIFACTS)
        or not _is_sha256(subject["diagnostic_receipt_sha256"])
        or not _is_sha256(subject["closure_authority_sha256"])
        or subject["execution_eligible"] is not False
        or closed_at.tzinfo is None
        or closed_at.date() > deadline
    ):
        raise ValueError("failure_closure_subject_invalid")
    return deepcopy(subject)


def _validate_closure_registry_record(
    registry_record: Any,
    subject: Mapping[str, Any],
) -> dict[str, Any]:
    if (
        not isinstance(registry_record, dict)
        or set(registry_record)
        != {"schema_version", "batch_id", "preregistration_sha256"}
        or registry_record.get("schema_version") != 1
        or registry_record.get("batch_id") != subject["batch_id"]
        or registry_record.get("preregistration_sha256")
        != subject["preregistration_sha256"]
        or _canonical_sha256(registry_record)
        != subject["registry_record_sha256"]
    ):
        raise ValueError("failure_closure_registry_invalid")
    return deepcopy(registry_record)


def validate_failure_closure(
    subject: Any,
    receipt: Any,
    registry_record: Any,
) -> dict[str, Any]:
    """Verify an externally signed closure receipt against its exact subject."""
    subject = _validate_failure_closure_subject(subject)
    registry_record = _validate_closure_registry_record(registry_record, subject)
    if (
        not isinstance(receipt, dict)
        or set(receipt)
        != {
            "schema_version",
            "role",
            "signer_public_key",
            "subject_sha256",
            "signature",
        }
        or receipt.get("schema_version") != AUTHORITY_RECEIPT_SCHEMA_VERSION
        or receipt.get("role") != "closure"
        or receipt.get("subject_sha256") != _canonical_sha256(subject)
        or not isinstance(receipt.get("signer_public_key"), str)
        or not isinstance(receipt.get("signature"), str)
    ):
        raise ValueError("failure_closure_receipt_invalid")
    try:
        from cryptography.exceptions import InvalidSignature
        from cryptography.hazmat.primitives.asymmetric.ed25519 import (
            Ed25519PublicKey,
        )
    except ImportError as error:
        raise ValueError("failure_closure_crypto_unavailable") from error
    try:
        public_raw = base64.b64decode(receipt["signer_public_key"], validate=True)
        signature = base64.b64decode(receipt["signature"], validate=True)
        if (
            len(public_raw) != 32
            or hashlib.sha256(public_raw).hexdigest()
            != subject["closure_authority_sha256"]
        ):
            raise ValueError("failure_closure_authority_invalid")
        signed = {key: value for key, value in receipt.items() if key != "signature"}
        Ed25519PublicKey.from_public_bytes(public_raw).verify(
            signature,
            _canonical_bytes(signed),
        )
    except InvalidSignature as error:
        raise ValueError("failure_closure_signature_invalid") from error
    except (binascii.Error, TypeError, ValueError) as error:
        reason = str(error)
        if reason == "failure_closure_authority_invalid":
            raise
        raise ValueError("failure_closure_receipt_invalid") from error
    return {
        "subject": subject,
        "receipt": deepcopy(receipt),
        "registry_record": registry_record,
    }


def _closure_marker_path(registry_root: Path, batch_id: str) -> Path:
    return registry_root / f"{_validate_batch_id(batch_id)}.closure.json"


def _open_registry_directory(
    repository: Path,
    reason: str,
    *,
    missing_ok: bool = False,
) -> tuple[Path, int] | None:
    omc_root = repository / ".omc"
    registry_root = omc_root / "registry"
    try:
        omc_stat = omc_root.lstat()
    except FileNotFoundError:
        if missing_ok:
            return None
        raise ValueError(reason)
    except OSError as error:
        raise ValueError(reason) from error
    if not stat.S_ISDIR(omc_stat.st_mode):
        raise ValueError(reason)
    try:
        registry_stat = registry_root.lstat()
    except FileNotFoundError:
        if missing_ok:
            return None
        raise ValueError(reason)
    except OSError as error:
        raise ValueError(reason) from error
    if not stat.S_ISDIR(registry_stat.st_mode):
        raise ValueError(reason)
    flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(
        os,
        "O_NOFOLLOW",
        0,
    )
    try:
        descriptor = os.open(registry_root, flags)
    except OSError as error:
        raise ValueError(reason) from error
    opened_stat = os.fstat(descriptor)
    if (
        not stat.S_ISDIR(opened_stat.st_mode)
        or opened_stat.st_dev != registry_stat.st_dev
        or opened_stat.st_ino != registry_stat.st_ino
    ):
        os.close(descriptor)
        raise ValueError(reason)
    return registry_root, descriptor


def _load_regular_json_at(
    directory_fd: int,
    name: str,
    *,
    missing_ok: bool,
    reason: str,
) -> Any:
    """Read one regular registry JSON file without following symlinks."""
    try:
        marker_stat = os.stat(name, dir_fd=directory_fd, follow_symlinks=False)
    except FileNotFoundError:
        if missing_ok:
            return None
        raise ValueError(reason)
    except OSError as error:
        raise ValueError(reason) from error
    if not stat.S_ISREG(marker_stat.st_mode):
        raise ValueError(reason)

    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(name, flags, dir_fd=directory_fd)
    except OSError as error:
        raise ValueError(reason) from error
    try:
        opened_stat = os.fstat(descriptor)
        if (
            not stat.S_ISREG(opened_stat.st_mode)
            or opened_stat.st_dev != marker_stat.st_dev
            or opened_stat.st_ino != marker_stat.st_ino
        ):
            raise ValueError(reason)
        with os.fdopen(descriptor, "r", encoding="utf-8") as handle:
            descriptor = -1
            return json.load(handle)
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        raise ValueError(reason) from error
    finally:
        if descriptor >= 0:
            os.close(descriptor)


def _create_registry_staging_file(
    registry_fd: int,
    marker_name: str,
) -> tuple[int, str]:
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    for _ in range(100):
        staging_name = f".{marker_name}.{secrets.token_hex(8)}.tmp"
        try:
            descriptor = os.open(
                staging_name,
                flags,
                0o600,
                dir_fd=registry_fd,
            )
            return descriptor, staging_name
        except FileExistsError:
            continue
    raise FileExistsError("failure_closure_staging_collision")


def record_failure_closure(
    repository_root: str | Path,
    *,
    subject: Any,
    receipt: Any,
) -> Path:
    """Record one validated closure marker without replacing prior custody."""
    normalized_subject = _validate_failure_closure_subject(subject)
    repository = Path(repository_root).expanduser().resolve(strict=True)
    root, registry_fd = _open_registry_directory(
        repository,
        "preregistration registry anchor is invalid",
    )
    marker = _closure_marker_path(root, normalized_subject["batch_id"])
    marker_name = marker.name
    payload = {
        "subject": normalized_subject,
        "receipt": deepcopy(receipt),
    }
    descriptor = -1
    staging_name: str | None = None
    try:
        registry_record = _load_registry_record(
            repository,
            batch_id=normalized_subject["batch_id"],
            preregistration_sha256=normalized_subject["preregistration_sha256"],
            registry_path=normalized_subject["registry_path"],
            registry_commit=normalized_subject["registry_commit"],
            registry_fd=registry_fd,
        )
        current = _load_regular_json_at(
            registry_fd,
            marker_name,
            missing_ok=True,
            reason="failure_closure_marker_exists",
        )
        if current is not None:
            if current != payload:
                raise ValueError("failure_closure_marker_exists")
            validate_failure_closure(
                current["subject"],
                current["receipt"],
                registry_record,
            )
            return marker
        validate_failure_closure(normalized_subject, receipt, registry_record)
        descriptor, staging_name = _create_registry_staging_file(
            registry_fd,
            marker_name,
        )
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            descriptor = -1
            json.dump(payload, handle, ensure_ascii=False, indent=2, sort_keys=True)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        try:
            os.link(
                staging_name,
                marker_name,
                src_dir_fd=registry_fd,
                dst_dir_fd=registry_fd,
                follow_symlinks=False,
            )
            os.unlink(staging_name, dir_fd=registry_fd)
            staging_name = None
            os.fsync(registry_fd)
        except FileExistsError:
            current = _load_regular_json_at(
                registry_fd,
                marker_name,
                missing_ok=False,
                reason="failure_closure_marker_exists",
            )
            if current != payload:
                raise ValueError("failure_closure_marker_exists")
            validate_failure_closure(
                current["subject"],
                current["receipt"],
                registry_record,
            )
        return marker
    finally:
        if descriptor >= 0:
            os.close(descriptor)
        if staging_name is not None:
            try:
                os.unlink(staging_name, dir_fd=registry_fd)
            except FileNotFoundError:
                pass
        os.close(registry_fd)


def load_failure_closure_marker(
    repository_root: str | Path,
    *,
    batch_id: str,
    preregistration_sha256: str,
) -> dict[str, Any] | None:
    """Load and verify a batch closure marker against its registry record."""
    repository = Path(repository_root).expanduser().resolve(strict=False)
    opened_registry = _open_registry_directory(
        repository,
        "failure_closure_marker_invalid",
        missing_ok=True,
    )
    if opened_registry is None:
        return None
    root, registry_fd = opened_registry
    marker = _closure_marker_path(root, batch_id)
    try:
        payload = _load_regular_json_at(
            registry_fd,
            marker.name,
            missing_ok=True,
            reason="failure_closure_marker_invalid",
        )
        if payload is None:
            return None
        if not isinstance(payload, dict) or set(payload) != {"subject", "receipt"}:
            raise ValueError("failure_closure_marker_invalid")
        normalized_subject = _validate_failure_closure_subject(payload["subject"])
        registry_record = _load_registry_record(
            repository,
            batch_id=batch_id,
            preregistration_sha256=preregistration_sha256,
            registry_path=normalized_subject["registry_path"],
            registry_commit=normalized_subject["registry_commit"],
            registry_fd=registry_fd,
        )
        validated = validate_failure_closure(
            payload["subject"],
            payload["receipt"],
            registry_record,
        )
        if validated["subject"]["preregistration_sha256"] != preregistration_sha256:
            raise ValueError("failure_closure_marker_invalid")
        return validated
    finally:
        os.close(registry_fd)


def _file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _temporary_roots() -> tuple[Path, ...]:
    candidates = {
        Path(tempfile.gettempdir()).resolve(strict=False),
        Path("/tmp").resolve(strict=False),
        Path("/private/tmp").resolve(strict=False),
    }
    return tuple(sorted(candidates, key=str))


def _is_within(path: Path, root: Path) -> bool:
    return path == root or root in path.parents


def _validate_evidence_root(value: str | Path) -> Path:
    candidate = Path(value).expanduser()
    if not candidate.is_absolute():
        raise ValueError("evidence_root_invalid")
    unresolved = candidate.resolve(strict=False)
    if any(
        _is_within(unresolved, temporary)
        for temporary in _temporary_roots()
    ):
        raise ValueError("evidence_root_ephemeral")
    try:
        root = candidate.resolve(strict=True)
    except OSError as error:
        raise ValueError("evidence_root_invalid") from error
    if not root.is_dir() or candidate.is_symlink():
        raise ValueError("evidence_root_invalid")
    return root


def _safe_relative_path(value: Any) -> bool:
    if not isinstance(value, str) or not value:
        return False
    path = PurePosixPath(value)
    return (
        not path.is_absolute()
        and ".." not in path.parts
        and path.as_posix() == value
        and value != INDEX_FILENAME
    )


def _validate_batch_id(value: Any) -> str:
    if (
        not isinstance(value, str)
        or not value
        or not _safe_relative_path(value)
        or "/" in value
        or value.startswith(".")
    ):
        raise ValueError("evidence_bundle_batch_id_invalid")
    return value


def _validate_artifacts(
    artifacts: Mapping[str, str | Path],
    *,
    repository_root: str | Path | None = None,
) -> dict[str, Path]:
    if not isinstance(artifacts, Mapping):
        raise ValueError("evidence_bundle_artifacts_invalid")
    paths = set(artifacts)
    if not _valid_artifact_profile(paths):
        raise ValueError("evidence_bundle_artifacts_invalid")
    normalized: dict[str, Path] = {}
    for relative, raw_source in artifacts.items():
        source = Path(raw_source).expanduser()
        if source.is_symlink():
            raise ValueError("evidence_bundle_artifacts_invalid")
        try:
            source = source.resolve(strict=True)
        except OSError as error:
            raise ValueError("evidence_bundle_artifacts_invalid") from error
        if not source.is_file():
            raise ValueError("evidence_bundle_artifacts_invalid")
        normalized[relative] = source
    if paths == CLOSURE_PATHS:
        _validate_closure_files(normalized, repository_root=repository_root)
    return normalized


def _valid_artifact_profile(paths: set[str]) -> bool:
    if any(not _safe_relative_path(path) for path in paths):
        return False
    if paths == CLOSURE_PATHS:
        return True
    return REQUIRED_PATHS.issubset(paths) and any(
        path.startswith("packets/") and path.endswith(".json")
        for path in paths
    )


def _load_json_file(path: Path, reason: str) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        raise ValueError(reason) from error


def _validate_closure_files(
    paths: Mapping[str, Path],
    *,
    repository_root: str | Path | None,
) -> dict[str, Any]:
    validated = validate_failure_closure(
        _load_json_file(
            paths["closure-subject.json"],
            "failure_closure_subject_invalid",
        ),
        _load_json_file(
            paths["closure-receipt.json"],
            "failure_closure_receipt_invalid",
        ),
        _load_json_file(
            paths["registry-record.json"],
            "failure_closure_registry_invalid",
        ),
    )
    if repository_root is None:
        raise ValueError("preregistration registry anchor is invalid")
    subject = validated["subject"]
    anchored_record = _load_registry_record(
        repository_root,
        batch_id=subject["batch_id"],
        preregistration_sha256=subject["preregistration_sha256"],
        registry_path=subject["registry_path"],
        registry_commit=subject["registry_commit"],
    )
    if anchored_record != validated["registry_record"]:
        raise ValueError("failure_closure_registry_invalid")
    return validated


def _write_json(path: Path, value: Any) -> None:
    with path.open("w", encoding="utf-8") as handle:
        json.dump(value, handle, ensure_ascii=False, indent=2, sort_keys=True)
        handle.write("\n")
        handle.flush()
        os.fsync(handle.fileno())


def _fsync_directory(path: Path) -> None:
    descriptor = os.open(path, os.O_RDONLY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _build_index(batch_id: str, staging: Path, paths: set[str]) -> dict[str, Any]:
    artifacts = {
        relative: {
            "sha256": _file_sha256(staging / relative),
            "size_bytes": (staging / relative).stat().st_size,
        }
        for relative in sorted(paths)
    }
    index = {
        "schema_version": SCHEMA_VERSION,
        "batch_id": batch_id,
        "artifacts": artifacts,
        "bundle_sha256": "",
    }
    index["bundle_sha256"] = _canonical_sha256(
        {key: value for key, value in index.items() if key != "bundle_sha256"}
    )
    return index


def _atomic_rename_no_replace(source: Path, destination: Path) -> None:
    """Publish one directory atomically without replacing an existing path."""
    libc = ctypes.CDLL(None, use_errno=True)
    source_bytes = os.fsencode(source)
    destination_bytes = os.fsencode(destination)
    result: int
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
        raise OSError(
            errno.ENOTSUP,
            "atomic no-replace directory rename is unavailable",
            str(destination),
        )
    if result == 0:
        return
    error_number = ctypes.get_errno()
    if error_number in {errno.EEXIST, errno.ENOTEMPTY}:
        raise ValueError("evidence_bundle_exists")
    raise OSError(error_number, os.strerror(error_number), str(destination))


def publish_evidence_bundle(
    evidence_root: str | Path,
    *,
    batch_id: str,
    artifacts: Mapping[str, str | Path],
    repository_root: str | Path | None = None,
) -> dict[str, Any]:
    """Copy verified artifacts and atomically publish one immutable batch."""
    root = _validate_evidence_root(evidence_root)
    normalized_batch_id = _validate_batch_id(batch_id)
    sources = _validate_artifacts(artifacts, repository_root=repository_root)
    destination = root / normalized_batch_id
    if destination.exists():
        raise ValueError("evidence_bundle_exists")
    staging = Path(
        tempfile.mkdtemp(prefix=f".{normalized_batch_id}-", dir=root)
    )
    published = False
    try:
        for relative, source in sorted(sources.items()):
            target = staging / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(source, target)
            if (
                target.stat().st_size != source.stat().st_size
                or _file_sha256(target) != _file_sha256(source)
            ):
                raise ValueError("evidence_bundle_copy_mismatch")
            with target.open("rb") as handle:
                os.fsync(handle.fileno())
        index = _build_index(normalized_batch_id, staging, set(sources))
        _write_json(staging / INDEX_FILENAME, index)
        for directory in sorted(
            {path.parent for path in staging.rglob("*") if path.is_file()},
            key=lambda path: len(path.parts),
            reverse=True,
        ):
            _fsync_directory(directory)
        _atomic_rename_no_replace(staging, destination)
        published = True
        _fsync_directory(destination)
        _fsync_directory(root)
        return index
    except Exception as error:
        shutil.rmtree(staging, ignore_errors=True)
        if published:
            raise ValueError("evidence_bundle_durability_indeterminate") from error
        raise


def load_evidence_bundle(
    evidence_root: str | Path,
    *,
    batch_id: str,
    repository_root: str | Path | None = None,
) -> dict[str, Any]:
    """Reload a published bundle and verify every indexed artifact."""
    root = _validate_evidence_root(evidence_root)
    normalized_batch_id = _validate_batch_id(batch_id)
    bundle_root = root / normalized_batch_id
    index_path = bundle_root / INDEX_FILENAME
    if bundle_root.is_symlink() or index_path.is_symlink():
        raise ValueError("evidence_bundle_invalid")
    try:
        index = json.loads(index_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        raise ValueError("evidence_bundle_invalid") from error
    if (
        not isinstance(index, dict)
        or set(index) != {
            "schema_version",
            "batch_id",
            "artifacts",
            "bundle_sha256",
        }
        or index.get("schema_version") != SCHEMA_VERSION
        or index.get("batch_id") != normalized_batch_id
        or not isinstance(index.get("artifacts"), dict)
        or index.get("bundle_sha256")
        != _canonical_sha256(
            {key: value for key, value in index.items() if key != "bundle_sha256"}
        )
    ):
        raise ValueError("evidence_bundle_invalid")
    artifact_paths = set(index["artifacts"])
    if not _valid_artifact_profile(artifact_paths):
        raise ValueError("evidence_bundle_invalid")
    actual_paths = {
        path.relative_to(bundle_root).as_posix()
        for path in bundle_root.rglob("*")
        if path.is_file() and path != index_path
    }
    if actual_paths != artifact_paths:
        raise ValueError("evidence_bundle_invalid")
    for relative, metadata in index["artifacts"].items():
        artifact = bundle_root / relative
        if (
            artifact.is_symlink()
            or not isinstance(metadata, dict)
            or set(metadata) != {"sha256", "size_bytes"}
            or not artifact.is_file()
            or artifact.stat().st_size != metadata.get("size_bytes")
            or _file_sha256(artifact) != metadata.get("sha256")
        ):
            raise ValueError("evidence_bundle_digest_mismatch")
    if artifact_paths == CLOSURE_PATHS:
        _validate_closure_files({
            relative: bundle_root / relative for relative in CLOSURE_PATHS
        }, repository_root=repository_root)
    return index


def materialize_evidence_bundle(
    evidence_root: str | Path,
    *,
    batch_id: str,
    repository_root: str | Path | None = None,
) -> MaterializedEvidenceBundle:
    """Verify and copy a bundle into a process-private read-only snapshot."""
    root = _validate_evidence_root(evidence_root)
    normalized_batch_id = _validate_batch_id(batch_id)
    index = load_evidence_bundle(
        root,
        batch_id=normalized_batch_id,
        repository_root=repository_root,
    )
    source_root = root / normalized_batch_id
    runtime = tempfile.TemporaryDirectory(prefix="omc-product-value-evidence-")
    snapshot_root = Path(runtime.name)
    paths: dict[str, Path] = {}
    try:
        for relative, metadata in index["artifacts"].items():
            source = source_root.joinpath(*PurePosixPath(relative).parts)
            snapshot = snapshot_root.joinpath(*PurePosixPath(relative).parts)
            snapshot.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(source, snapshot)
            if (
                snapshot.stat().st_size != metadata["size_bytes"]
                or _file_sha256(snapshot) != metadata["sha256"]
            ):
                raise ValueError("evidence_bundle_digest_mismatch")
            snapshot.chmod(0o400)
            paths[relative] = snapshot
        for directory in sorted(
            (path for path in snapshot_root.rglob("*") if path.is_dir()),
            key=lambda path: len(path.parts),
            reverse=True,
        ):
            directory.chmod(0o500)
        snapshot_root.chmod(0o500)
        return MaterializedEvidenceBundle(runtime, deepcopy(index), paths)
    except (OSError, ValueError):
        runtime.cleanup()
        raise


def _load_artifact_map(path: Path) -> dict[str, Path]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        raise ValueError("evidence_bundle_artifact_map_invalid") from error
    if not isinstance(payload, dict) or any(
        not isinstance(key, str) or not isinstance(value, str)
        for key, value in payload.items()
    ):
        raise ValueError("evidence_bundle_artifact_map_invalid")
    return {key: Path(value) for key, value in payload.items()}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    for command in ("publish", "verify"):
        action = sub.add_parser(command)
        action.add_argument("--evidence-root", type=Path, required=True)
        action.add_argument("--batch-id", required=True)
        action.add_argument("--repository-root", type=Path)
        if command == "publish":
            action.add_argument("--artifacts", type=Path, required=True)
    prepare_closure = sub.add_parser("prepare-closure")
    prepare_closure.add_argument("--repository-root", type=Path, required=True)
    prepare_closure.add_argument("--batch-id", required=True)
    prepare_closure.add_argument("--preregistration-sha256", required=True)
    prepare_closure.add_argument("--registry-path", required=True)
    prepare_closure.add_argument("--registry-commit", required=True)
    prepare_closure.add_argument("--diagnostic-receipt-sha256", required=True)
    prepare_closure.add_argument("--closure-authority-sha256", required=True)
    prepare_closure.add_argument("--closed-at", required=True)
    prepare_closure.add_argument("--final-decision-deadline", required=True)
    prepare_closure.add_argument("--out", type=Path, required=True)
    validate_closure = sub.add_parser("validate-closure")
    validate_closure.add_argument("--repository-root", type=Path, required=True)
    validate_closure.add_argument("--subject", type=Path, required=True)
    validate_closure.add_argument("--receipt", type=Path, required=True)
    validate_closure.add_argument("--registry-record", type=Path, required=True)
    record_closure = sub.add_parser("record-closure")
    record_closure.add_argument("--repository-root", type=Path, required=True)
    record_closure.add_argument("--subject", type=Path, required=True)
    record_closure.add_argument("--receipt", type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        if args.command == "publish":
            index = publish_evidence_bundle(
                args.evidence_root,
                batch_id=args.batch_id,
                artifacts=_load_artifact_map(args.artifacts),
                repository_root=args.repository_root,
            )
            result = {
                "status": "published",
                "batch_id": index["batch_id"],
                "bundle_sha256": index["bundle_sha256"],
                "artifact_count": len(index["artifacts"]),
            }
        elif args.command == "verify":
            index = load_evidence_bundle(
                args.evidence_root,
                batch_id=args.batch_id,
                repository_root=args.repository_root,
            )
            result = {
                "status": "verified",
                "batch_id": index["batch_id"],
                "bundle_sha256": index["bundle_sha256"],
                "artifact_count": len(index["artifacts"]),
            }
        elif args.command == "prepare-closure":
            subject = prepare_failure_closure_subject(
                repository_root=args.repository_root,
                batch_id=args.batch_id,
                preregistration_sha256=args.preregistration_sha256,
                registry_path=args.registry_path,
                registry_commit=args.registry_commit,
                diagnostic_receipt_sha256=args.diagnostic_receipt_sha256,
                closure_authority_sha256=args.closure_authority_sha256,
                closed_at=args.closed_at,
                final_decision_deadline=args.final_decision_deadline,
                missing_artifacts=tuple(sorted(CLOSURE_MISSING_ARTIFACTS)),
            )
            _write_json(args.out, subject)
            result = {
                "status": "closure_prepared",
                "batch_id": subject["batch_id"],
                "subject_sha256": _canonical_sha256(subject),
            }
        elif args.command == "validate-closure":
            validated = _validate_closure_files(
                {
                    "closure-subject.json": args.subject,
                    "closure-receipt.json": args.receipt,
                    "registry-record.json": args.registry_record,
                },
                repository_root=args.repository_root,
            )
            result = {
                "status": "closure_valid",
                "batch_id": validated["subject"]["batch_id"],
                "subject_sha256": _canonical_sha256(validated["subject"]),
            }
        else:
            marker = record_failure_closure(
                args.repository_root,
                subject=_load_json_file(
                    args.subject,
                    "failure_closure_subject_invalid",
                ),
                receipt=_load_json_file(
                    args.receipt,
                    "failure_closure_receipt_invalid",
                ),
            )
            result = {
                "status": "closure_recorded",
                "marker": marker.name,
            }
        print(json.dumps(result, ensure_ascii=False, sort_keys=True))
        return 0
    except (OSError, ValueError) as error:
        print(json.dumps({
            "status": "blocked",
            "reason_code": str(error),
        }, ensure_ascii=False, sort_keys=True))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
