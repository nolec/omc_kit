"""Safe, isolated workspace primitives for review comparison runs."""
from __future__ import annotations

import hashlib
import shutil
import tempfile
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from collections.abc import Iterable
from typing import Iterator


IGNORED_DIR_NAMES = frozenset(
    {".git", ".omc", "node_modules", ".venv", "dist", "build", "coverage", "__pycache__", ".next"}
)
DEFAULT_MAX_BYTES = 100 * 1024 * 1024
ENVELOPE_PROVIDERS = {"codex", "omc-review"}
ENVELOPE_STATUSES = {"completed", "failed", "not_run"}
ENVELOPE_EXECUTION_MODES = {"cli_completed", "cli_failed", "manual_rule_application", "not_run"}
ENVELOPE_METADATA_FIELDS = {"snapshot_used", "workspace_mutated"}


@dataclass
class Snapshot:
    path: Path
    initial_hash: str
    final_hash: str | None = None
    mutated: bool = False


def build_review_execution_envelope(
    *,
    provider: str,
    case_id: str,
    diff_id: str,
    prompt_id: str,
    status: str,
    execution_mode: str,
    result: dict[str, object],
    execution_metadata: dict[str, object],
    runner: str = "",
    model: str | None = None,
) -> dict[str, object]:
    """Attach explicit execution context to a review callback result."""
    if provider not in ENVELOPE_PROVIDERS:
        raise ValueError("unsupported provider")
    for value, label in ((case_id, "case_id"), (diff_id, "diff_id"), (prompt_id, "prompt_id")):
        if not isinstance(value, str) or not value.strip():
            raise ValueError(f"{label} requires value")
    if status not in ENVELOPE_STATUSES:
        raise ValueError("unsupported status")
    if execution_mode not in ENVELOPE_EXECUTION_MODES:
        raise ValueError("unsupported execution_mode")
    if status == "failed" and execution_mode != "cli_failed":
        raise ValueError("failed status requires cli_failed execution_mode")
    if status == "completed" and execution_mode == "cli_failed":
        raise ValueError("completed status cannot use cli_failed execution_mode")
    if status == "not_run" and execution_mode != "not_run":
        raise ValueError("not_run status requires not_run execution_mode")
    if status != "not_run" and execution_mode == "not_run":
        raise ValueError("not_run execution_mode requires not_run status")
    if not isinstance(result, dict):
        raise ValueError("review result must be an object")
    if not isinstance(execution_metadata, dict):
        raise ValueError("execution_metadata must be an object")
    if set(execution_metadata) != ENVELOPE_METADATA_FIELDS:
        raise ValueError("execution_metadata requires snapshot_used and workspace_mutated")
    if any(not isinstance(value, bool) for value in execution_metadata.values()):
        raise ValueError("execution_metadata values must be boolean")
    findings = result.get("findings", [])
    metrics = result.get("metrics", {})
    if not isinstance(findings, list) or not isinstance(metrics, dict):
        raise ValueError("review result findings and metrics must be collections")
    envelope: dict[str, object] = {
        "provider": provider,
        "case_id": case_id.strip(),
        "diff_id": diff_id.strip(),
        "prompt_id": prompt_id.strip(),
        "status": status,
        "execution_mode": execution_mode,
        "runner": runner,
        "model": model,
        "findings": findings,
        "metrics": metrics,
        "execution_metadata": dict(execution_metadata),
    }
    for field in ("verdict", "next_action"):
        if result.get(field):
            envelope[field] = result[field]
    if result.get("error_type"):
        envelope["error_type"] = result["error_type"]
    return envelope


def _assert_no_symlinks(root: Path, ignored_dirs: Iterable[str] | None = None) -> None:
    ignored_dirs = _normalize_ignored_dirs(ignored_dirs)
    for path in root.rglob("*"):
        if any(part in ignored_dirs for part in path.relative_to(root).parts):
            continue
        if path.is_symlink():
            raise ValueError(f"snapshot source cannot contain symlinks: {path.name}")


def _normalize_ignored_dirs(ignored_dirs: Iterable[str] | None) -> frozenset[str]:
    return IGNORED_DIR_NAMES | frozenset(ignored_dirs or ())


def _iter_snapshot_files(
    root: Path, ignored_dirs: Iterable[str] | None = None
) -> Iterator[Path]:
    ignored = _normalize_ignored_dirs(ignored_dirs)
    for path in sorted(root.rglob("*")):
        relative = path.relative_to(root)
        if any(part in ignored for part in relative.parts) or not path.is_file():
            continue
        yield path


def _snapshot_ignore(ignored_dirs: frozenset[str]):
    return lambda _directory, names: [name for name in names if name in ignored_dirs]


def _tree_hash(root: Path, ignored_dirs: Iterable[str] | None = None) -> str:
    digest = hashlib.sha256()
    for path in _iter_snapshot_files(root, ignored_dirs):
        relative = path.relative_to(root)
        digest.update(str(relative).encode("utf-8"))
        digest.update(b"\0")
        digest.update(str(path.stat().st_mode & 0o7777).encode("ascii"))
        digest.update(b"\0")
        digest.update(path.read_bytes())
        digest.update(b"\0")
    return digest.hexdigest()


@contextmanager
def isolated_snapshot(
    source: str | Path,
    *,
    ignored_dirs: Iterable[str] | None = None,
) -> Iterator[Snapshot]:
    """Yield a temporary copy and report mutations before cleanup.

    ``ignored_dirs`` adds project-specific directories to the protected default
    policy; it cannot re-enable built-in protected directories.
    """
    source_path = Path(source)
    if not source_path.is_dir():
        raise ValueError("source directory is required")
    ignored = _normalize_ignored_dirs(ignored_dirs)
    _assert_no_symlinks(source_path, ignored)
    with tempfile.TemporaryDirectory(prefix="omc-review-") as temp_dir:
        snapshot_path = Path(temp_dir) / "workspace"
        shutil.copytree(
            source_path,
            snapshot_path,
            symlinks=False,
            ignore=_snapshot_ignore(ignored),
        )
        snapshot = Snapshot(
            path=snapshot_path,
            initial_hash=_tree_hash(snapshot_path, ignored),
        )
        try:
            yield snapshot
        finally:
            snapshot.final_hash = _tree_hash(snapshot_path, ignored)
            snapshot.mutated = snapshot.final_hash != snapshot.initial_hash
