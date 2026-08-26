#!/usr/bin/env python3
"""Validate and materialize a frozen Product Value execution environment."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import posixpath
import shutil
import tarfile
import tempfile
from typing import Any

import omc_product_value_acceptance as acceptance


ARCHIVE_NAME = "workspace-cache.tar.gz"
MAX_ARCHIVE_ENTRIES = 500_000
MAX_ARCHIVE_BYTES = 2_147_483_648


def _file_sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _safe_relative(value: str) -> bool:
    path = PurePosixPath(value)
    return bool(value) and not path.is_absolute() and ".." not in path.parts


def _validate_archive_member(member: tarfile.TarInfo) -> None:
    path = PurePosixPath(member.name)
    if (
        not _safe_relative(member.name)
        or not path.parts
        or path.parts[0] != "node_modules"
        or not (member.isdir() or member.isfile() or member.issym())
    ):
        raise ValueError("environment_cache_archive_unsafe")
    if member.issym():
        _safe_link_target(member)


def _safe_link_target(member: tarfile.TarInfo) -> PurePosixPath:
    if not member.linkname or PurePosixPath(member.linkname).is_absolute():
        raise ValueError("environment_cache_archive_unsafe")
    normalized = PurePosixPath(
        posixpath.normpath(
            str(PurePosixPath(member.name).parent / member.linkname)
        )
    )
    if not normalized.parts or normalized.parts[0] != "node_modules":
        raise ValueError("environment_cache_archive_unsafe")
    return normalized


def _materialize_archive(archive: Path, workspace: Path) -> None:
    destination = workspace / "node_modules"
    if destination.exists() or destination.is_symlink():
        raise ValueError("environment_workspace_not_clean")
    staging = Path(tempfile.mkdtemp(prefix=".omc-cache-", dir=workspace))
    try:
        with tarfile.open(archive, "r:gz") as stream:
            links = []
            total_size = 0
            for member_count, member in enumerate(stream, start=1):
                if member_count > MAX_ARCHIVE_ENTRIES:
                    raise ValueError("environment_cache_archive_limit")
                _validate_archive_member(member)
                total_size += member.size
                if total_size > MAX_ARCHIVE_BYTES:
                    raise ValueError("environment_cache_archive_limit")
                relative = PurePosixPath(member.name)
                target = staging.joinpath(*relative.parts)
                if member.isdir():
                    target.mkdir(parents=True, exist_ok=True)
                    continue
                if member.issym():
                    links.append(member)
                    continue
                target.parent.mkdir(parents=True, exist_ok=True)
                source = stream.extractfile(member)
                if source is None:
                    raise ValueError("environment_cache_archive_unsafe")
                with source, target.open("wb") as output:
                    shutil.copyfileobj(source, output)
                target.chmod(member.mode & 0o755)
            for member in links:
                relative = PurePosixPath(member.name)
                link = staging.joinpath(*relative.parts)
                linked = staging.joinpath(*_safe_link_target(member).parts)
                if not linked.exists():
                    raise ValueError("environment_cache_archive_unsafe")
                link.parent.mkdir(parents=True, exist_ok=True)
                link.symlink_to(member.linkname)
        materialized = staging / "node_modules"
        if not materialized.is_dir():
            raise ValueError("environment_cache_archive_unsafe")
        os.replace(materialized, destination)
    except (OSError, tarfile.TarError) as error:
        raise ValueError("environment_cache_archive_invalid") from error
    finally:
        shutil.rmtree(staging, ignore_errors=True)


def probe_environment(
    *,
    workspace: str,
    source_commit: str,
    dependency_lock_path: str,
    dependency_lock_sha256: str,
    cache_path: str,
    cache_sha256: str,
    runtime_identity_path: str,
    runtime_identity_sha256: str,
) -> dict[str, Any]:
    root = Path(workspace).expanduser().resolve(strict=False)
    cache = Path(cache_path).expanduser().resolve(strict=False)
    runtime = Path(runtime_identity_path).expanduser().resolve(strict=False)
    if not _safe_relative(dependency_lock_path):
        raise ValueError("environment_probe_mismatch")
    lock = root.joinpath(*PurePosixPath(dependency_lock_path).parts).resolve(
        strict=False
    )
    try:
        measured_cache = acceptance.canonical_cache_inventory_sha256(
            cache,
            require_readonly=True,
        )
        valid = (
            root.is_dir()
            and root in lock.parents
            and lock.is_file()
            and _file_sha256(lock) == dependency_lock_sha256
            and cache.is_dir()
            and measured_cache == cache_sha256
            and runtime.is_file()
            and _file_sha256(runtime) == runtime_identity_sha256
            and isinstance(source_commit, str)
            and len(source_commit) == 40
        )
    except (OSError, ValueError):
        valid = False
    if not valid:
        raise ValueError("environment_probe_mismatch")

    archive = cache / ARCHIVE_NAME
    if archive.exists():
        if not archive.is_file():
            raise ValueError("environment_cache_archive_invalid")
        _materialize_archive(archive, root)

    return {
        "schema_version": "omc-product-value-environment-probe/v1",
        "source_commit": source_commit,
        "dependency_lock_sha256": dependency_lock_sha256,
        "cache_sha256": cache_sha256,
        "runtime_identity_sha256": runtime_identity_sha256,
        "cache_path": str(cache),
        "cache_readonly": True,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workspace", default=".")
    parser.add_argument("--source-commit", required=True)
    parser.add_argument("--dependency-lock-path", required=True)
    parser.add_argument("--dependency-lock-sha256", required=True)
    parser.add_argument("--cache-path", required=True)
    parser.add_argument("--cache-sha256", required=True)
    parser.add_argument("--runtime-identity-path", required=True)
    parser.add_argument("--runtime-identity-sha256", required=True)
    args = parser.parse_args(argv)
    try:
        result = probe_environment(**vars(args))
    except ValueError as error:
        print(json.dumps({"status": "blocked", "reason_code": str(error)}))
        return 2
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
