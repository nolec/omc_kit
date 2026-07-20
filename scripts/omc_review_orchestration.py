"""Run review callbacks in isolated workspaces without changing result contracts."""
from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from collections.abc import Iterable
from typing import Any

from omc_review_adapter import DEFAULT_MAX_BYTES, _iter_snapshot_files, isolated_snapshot


def _workspace_size(root: Path, ignored_dirs: Iterable[str] | None = None) -> int:
    return sum(path.stat().st_size for path in _iter_snapshot_files(root, ignored_dirs))


def run_review_in_snapshot(
    source: str | Path,
    review: Callable[[Path], Any],
    *,
    max_bytes: int | None = DEFAULT_MAX_BYTES,
    ignored_dirs: Iterable[str] | None = None,
) -> dict[str, Any]:
    """Execute a review callback in a temporary snapshot and return metadata.

    ``ignored_dirs`` adds project-specific exclusions to the default protected
    directories.
    """
    if max_bytes is not None and (
        isinstance(max_bytes, bool) or not isinstance(max_bytes, int) or max_bytes < 0
    ):
        raise ValueError("snapshot size limit requires a non-negative integer")

    source_path = Path(source)
    if max_bytes is not None and _workspace_size(source_path, ignored_dirs) > max_bytes:
        raise ValueError("snapshot size limit exceeded")

    with isolated_snapshot(source_path, ignored_dirs=ignored_dirs) as snapshot:
        result = review(snapshot.path)
    return {
        "result": result,
        "execution_metadata": {
            "snapshot_used": True,
            "workspace_mutated": snapshot.mutated,
        },
    }
