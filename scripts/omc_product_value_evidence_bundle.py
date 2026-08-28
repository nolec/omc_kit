#!/usr/bin/env python3
"""Publish and recover immutable Product Value evidence bundles."""

from __future__ import annotations

import ctypes
import errno
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import shutil
import sys
import tempfile
from typing import Any, Mapping


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


def _canonical_sha256(value: Any) -> str:
    payload = json.dumps(
        value,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


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
) -> dict[str, Path]:
    if not isinstance(artifacts, Mapping):
        raise ValueError("evidence_bundle_artifacts_invalid")
    paths = set(artifacts)
    if (
        not REQUIRED_PATHS.issubset(paths)
        or not any(
            path.startswith("packets/") and path.endswith(".json")
            for path in paths
        )
        or any(not _safe_relative_path(path) for path in paths)
    ):
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
    return normalized


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
) -> dict[str, Any]:
    """Copy verified artifacts and atomically publish one immutable batch."""
    root = _validate_evidence_root(evidence_root)
    normalized_batch_id = _validate_batch_id(batch_id)
    sources = _validate_artifacts(artifacts)
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
    if (
        not REQUIRED_PATHS.issubset(artifact_paths)
        or not any(
            path.startswith("packets/") and path.endswith(".json")
            for path in artifact_paths
        )
        or any(not _safe_relative_path(path) for path in artifact_paths)
    ):
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
    return index
