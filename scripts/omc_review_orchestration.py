"""Run review callbacks in isolated workspaces without changing result contracts."""
from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from collections.abc import Iterable
from typing import Any

from omc_review_adapter import (
    DEFAULT_MAX_BYTES,
    _iter_snapshot_files,
    build_review_execution_envelope,
    isolated_snapshot,
)


def _workspace_size(root: Path, ignored_dirs: Iterable[str] | None = None) -> int:
    return sum(path.stat().st_size for path in _iter_snapshot_files(root, ignored_dirs))


def run_review_in_snapshot(
    source: str | Path,
    review: Callable[[Path], Any],
    *,
    max_bytes: int | None = DEFAULT_MAX_BYTES,
    ignored_dirs: Iterable[str] | None = None,
    envelope_context: dict[str, Any] | None = None,
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

    callback_error: Exception | None = None
    with isolated_snapshot(source_path, ignored_dirs=ignored_dirs) as snapshot:
        try:
            result = review(snapshot.path)
        except Exception as exc:
            if envelope_context is None:
                raise
            callback_error = exc
            result = {"findings": [], "metrics": {}, "error_type": type(exc).__name__}
    execution_metadata = {
        "snapshot_used": True,
        "workspace_mutated": snapshot.mutated,
    }
    if envelope_context is None:
        return {"result": result, "execution_metadata": execution_metadata}
    required_context = {"provider", "case_id", "diff_id", "prompt_id", "status", "execution_mode"}
    if set(envelope_context) - required_context - {"runner", "model"}:
        raise ValueError("envelope_context contains unsupported fields")
    if not required_context.issubset(envelope_context):
        raise ValueError("envelope_context requires provider, identity, status, and execution mode")
    status = "failed" if callback_error is not None else envelope_context["status"]
    execution_mode = "cli_failed" if callback_error is not None else envelope_context["execution_mode"]
    return build_review_execution_envelope(
        provider=envelope_context["provider"],
        case_id=envelope_context["case_id"],
        diff_id=envelope_context["diff_id"],
        prompt_id=envelope_context["prompt_id"],
        status=status,
        execution_mode=execution_mode,
        runner=envelope_context.get("runner", ""),
        model=envelope_context.get("model"),
        result=result,
        execution_metadata=execution_metadata,
    )
