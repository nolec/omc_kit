#!/usr/bin/env python3
from __future__ import annotations

import hashlib
import os
from pathlib import Path
from typing import Iterator


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
DEPLOYED_SCRIPT_EXTRAS = {
    "compose_prompt.py",
    "install.py",
    "omc.py",
}
DEPLOYED_DOCUMENTS = {
    "agent_behavior.md",
    "kit_map.md",
    "next_project_pack.md",
    "omc_quality_gates.md",
    "omc_n_child_acceptance.md",
    "omc_versioning.md",
    "omc_workflow.md",
    "quickstart_kr.md",
    "verification_checklist.md",
}
DEPLOYED_PROMPTS = {
    "MODE_AUTOPILOT.md",
    "MODE_DEEP_INTERVIEW.md",
    "MODE_RALPH.md",
    "MODE_TEAM.md",
    "MODE_ULTRAWORK.md",
    "README.md",
    "ROLE_ANALYSIS_ASSISTANT.md",
    "ROLE_CODE_REVIEW_ASSISTANT.md",
    "ROLE_ORCHESTRATOR.md",
    "ROLE_SEARCH_ASSISTANT.md",
    "ROLE_SENIOR_CODING_ASSISTANT.md",
    "team.json",
}


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _is_candidate_file(source_kit: Path, path: Path) -> bool:
    relative = path.relative_to(source_kit)
    return (
        path.is_file()
        and not any(part in _EXCLUDED_DIRECTORIES for part in relative.parts)
        and path.name not in _EXCLUDED_FILES
        and not path.name.startswith(".coverage.")
        and path.suffix not in _EXCLUDED_SUFFIXES
    )


def is_deployed_script_name(name: str) -> bool:
    return name.startswith("omc_") or name in DEPLOYED_SCRIPT_EXTRAS


def _raise_scan_error(error: OSError) -> None:
    raise error


def _iter_template_files(source_kit: Path) -> Iterator[Path]:
    templates = source_kit / "templates"
    if not templates.is_dir():
        return
    for root, directories, files in os.walk(templates, onerror=_raise_scan_error):
        directories[:] = sorted(
            name for name in directories if name not in _EXCLUDED_DIRECTORIES
        )
        root_path = Path(root)
        for name in sorted(files):
            path = root_path / name
            if _is_candidate_file(source_kit, path):
                yield path


def _iter_installable_source_files(source_kit: Path) -> Iterator[Path]:
    version = source_kit / "VERSION"
    if _is_candidate_file(source_kit, version):
        yield version

    yield from _iter_template_files(source_kit)

    scripts = source_kit / "scripts"
    if scripts.is_dir():
        for path in scripts.iterdir():
            if is_deployed_script_name(path.name) and _is_candidate_file(source_kit, path):
                yield path

    for parent, names in (("docs", DEPLOYED_DOCUMENTS), ("prompts", DEPLOYED_PROMPTS)):
        for name in names:
            path = source_kit / parent / name
            if _is_candidate_file(source_kit, path):
                yield path


def source_sha256(source_kit: Path) -> str:
    digest = hashlib.sha256()
    for path in sorted(_iter_installable_source_files(source_kit)):
        digest.update(str(path.relative_to(source_kit)).encode("utf-8"))
        digest.update(_sha256_file(path).encode("ascii"))
    return digest.hexdigest()
