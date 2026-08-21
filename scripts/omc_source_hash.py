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


def _is_installable_source_file(source_kit: Path, path: Path) -> bool:
    if not _is_candidate_file(source_kit, path):
        return False
    relative = path.relative_to(source_kit)
    if relative == Path("VERSION"):
        return True
    if relative.parts[0] == "templates":
        return True
    if len(relative.parts) != 2:
        return False
    parent, name = relative.parts
    if parent == "scripts":
        return is_deployed_script_name(name)
    if parent == "docs":
        return name in DEPLOYED_DOCUMENTS
    if parent == "prompts":
        return name in DEPLOYED_PROMPTS
    return False


def source_sha256(source_kit: Path) -> str:
    digest = hashlib.sha256()
    for path in sorted(
        candidate
        for candidate in source_kit.rglob("*")
        if _is_installable_source_file(source_kit, candidate)
    ):
        digest.update(str(path.relative_to(source_kit)).encode("utf-8"))
        digest.update(_sha256_file(path).encode("ascii"))
    return digest.hexdigest()
