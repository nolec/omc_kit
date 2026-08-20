#!/usr/bin/env python3
from __future__ import annotations

import hashlib
from pathlib import Path


_EXCLUDED_DIRECTORIES = {
    ".git",
    ".omc",
    ".benchmarks",
    ".codex-artifacts",
    ".private-artifacts",
    ".pytest_cache",
    ".mypy_cache",
    ".ruff_cache",
    ".tox",
    ".nox",
    ".venv",
    "venv",
    "htmlcov",
    "node_modules",
    "__pycache__",
}
_EXCLUDED_FILES = {".DS_Store", ".coverage", "coverage.xml", "junit.xml"}
_EXCLUDED_SUFFIXES = {".pyc", ".pyo"}


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _is_source_file(source_kit: Path, path: Path) -> bool:
    relative = path.relative_to(source_kit)
    return (
        path.is_file()
        and not any(part in _EXCLUDED_DIRECTORIES for part in relative.parts)
        and path.name not in _EXCLUDED_FILES
        and not path.name.startswith(".coverage.")
        and path.suffix not in _EXCLUDED_SUFFIXES
    )


def source_sha256(source_kit: Path) -> str:
    digest = hashlib.sha256()
    for path in sorted(
        candidate
        for candidate in source_kit.rglob("*")
        if _is_source_file(source_kit, candidate)
    ):
        digest.update(str(path.relative_to(source_kit)).encode("utf-8"))
        digest.update(_sha256_file(path).encode("ascii"))
    return digest.hexdigest()
