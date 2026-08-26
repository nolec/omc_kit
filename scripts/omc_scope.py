#!/usr/bin/env python3
"""Canonical, target-bound scope validation for bounded child execution."""

from __future__ import annotations

from copy import deepcopy
import hashlib
import json
from pathlib import Path
import subprocess
import unicodedata
from typing import Any


_GLOB_CHARACTERS = frozenset("*?[]")


def _blocked(reason_code: str) -> dict[str, Any]:
    return {
        "status": "blocked",
        "reason_code": reason_code,
        "execution_allowed": False,
    }


def _normalize_scope_path(value: Any) -> tuple[str | None, str | None]:
    if not isinstance(value, str) or not value or "\\" in value:
        return None, "scope_path_invalid"
    if any(character in value for character in _GLOB_CHARACTERS):
        return None, "scope_glob_forbidden"
    raw = value.rstrip("/")
    if not raw or raw.startswith("/"):
        return None, "scope_path_invalid"
    parts = raw.split("/")
    if any(part in {"", ".", ".."} for part in parts):
        return None, "scope_path_invalid"
    normalized_parts = [unicodedata.normalize("NFC", part) for part in parts]
    return "/".join(normalized_parts), None


def _target_identity(trusted_target: Path) -> str:
    return hashlib.sha256(str(trusted_target.resolve()).encode("utf-8")).hexdigest()


def _repository_commit_identity(
    trusted_target: Path, target_binding: dict[str, Any]
) -> tuple[str | None, str | None]:
    if set(target_binding) != {
        "mode",
        "repository_identity_sha256",
        "source_commit",
    } or target_binding.get("mode") != "repository_commit/v1":
        return None, "scope_target_binding_invalid"
    repository_identity = target_binding.get("repository_identity_sha256")
    source_commit = target_binding.get("source_commit")
    if (
        not isinstance(repository_identity, str)
        or len(repository_identity) != 64
        or any(character not in "0123456789abcdef" for character in repository_identity)
        or not isinstance(source_commit, str)
        or len(source_commit) != 40
        or any(character not in "0123456789abcdef" for character in source_commit)
    ):
        return None, "scope_target_binding_invalid"
    try:
        head = subprocess.run(
            ["git", "-C", str(trusted_target), "rev-parse", "HEAD"],
            check=False,
            capture_output=True,
            text=True,
            timeout=10,
        )
        status = subprocess.run(
            [
                "git",
                "-C",
                str(trusted_target),
                "status",
                "--porcelain",
                "--untracked-files=all",
            ],
            check=False,
            capture_output=True,
            text=True,
            timeout=10,
        )
    except (OSError, subprocess.SubprocessError):
        return None, "scope_target_binding_invalid"
    if (
        head.returncode != 0
        or head.stdout.strip() != source_commit
        or status.returncode != 0
        or status.stdout.strip()
    ):
        return None, "scope_target_binding_mismatch"
    payload = json.dumps(
        target_binding,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest(), None


def _has_symlink_component(path: Path) -> bool:
    absolute = path.absolute()
    current = Path(absolute.anchor)
    for part in absolute.parts[1:]:
        current = current / part
        if current.is_symlink():
            return True
    return False


def _has_symlink_prefix(trusted_target: Path, relative_path: str) -> bool:
    current = trusted_target
    for part in relative_path.split("/"):
        current = current / part
        if current.is_symlink():
            return True
        if not current.exists():
            break
    return False


def canonical_scope_sha256(paths: list[str]) -> str:
    """Hash a canonical scope set independently of caller ordering."""
    normalized: list[str] = []
    for path in paths:
        canonical, reason = _normalize_scope_path(path)
        if reason is not None or canonical is None:
            raise ValueError(reason or "scope_path_invalid")
        normalized.append(canonical)
    payload = json.dumps(
        sorted(normalized),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def canonicalize_child_scopes(
    trusted_target: str | Path,
    children: list[dict[str, Any]],
    *,
    target_binding: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Canonicalize disjoint child scopes below an explicit trusted target."""
    target = Path(trusted_target)
    if (
        not target.exists()
        or not target.is_dir()
        or _has_symlink_component(target)
    ):
        return _blocked("scope_target_invalid")
    if not isinstance(children, list) or not all(
        isinstance(child, dict) for child in children
    ):
        return _blocked("scope_input_invalid")

    normalized_children = deepcopy(children)
    seen: list[tuple[tuple[str, ...], tuple[str, ...]]] = []
    for child in normalized_children:
        scope_paths = child.get("scope_paths")
        if not isinstance(scope_paths, list) or not scope_paths:
            return _blocked("scope_input_invalid")
        canonical_paths: list[str] = []
        for scope_path in scope_paths:
            canonical, reason = _normalize_scope_path(scope_path)
            if reason is not None or canonical is None:
                return _blocked(reason or "scope_path_invalid")
            if _has_symlink_prefix(target, canonical):
                return _blocked("scope_symlink_forbidden")
            parts = tuple(canonical.split("/"))
            alias_parts = tuple(part.casefold() for part in parts)
            unicode_normalized = scope_path.rstrip("/") != canonical
            for existing_parts, existing_alias in seen:
                if parts == existing_parts and unicode_normalized:
                    return _blocked("scope_case_collision")
                if alias_parts == existing_alias and parts != existing_parts:
                    return _blocked("scope_case_collision")
                if (
                    parts[: len(existing_parts)] == existing_parts
                    or existing_parts[: len(parts)] == parts
                ):
                    return _blocked("scope_overlap")
                if (
                    alias_parts[: len(existing_alias)] == existing_alias
                    or existing_alias[: len(alias_parts)] == alias_parts
                ):
                    return _blocked("scope_case_collision")
            seen.append((parts, alias_parts))
            canonical_paths.append(canonical)
        canonical_paths.sort()
        child["scope_paths"] = canonical_paths
        child["scope_hash"] = canonical_scope_sha256(canonical_paths)

    if target_binding is None:
        target_identity = _target_identity(target)
    else:
        target_identity, binding_error = _repository_commit_identity(
            target, target_binding
        )
        if binding_error is not None or target_identity is None:
            return _blocked(binding_error or "scope_target_binding_invalid")
    result = {
        "status": "ready",
        "reason_code": "scope_ready",
        "execution_allowed": False,
        "scope_policy_version": "omc-scope/v1",
        "target_identity_sha256": target_identity,
        "children": normalized_children,
    }
    if target_binding is not None:
        result["target_binding"] = deepcopy(target_binding)
    return result
