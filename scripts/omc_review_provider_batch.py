#!/usr/bin/env python3
"""Collect reproducible Codex review results for approved observed diff cases."""

from __future__ import annotations

import argparse
import json
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from omc_review_compare import (
    canonical_review_diff_sha256,
    resolve_observed_candidate_path,
    verify_observed_candidate_hashes,
)
from omc_review_runner import run_codex_review


class ProviderBatchError(ValueError):
    """Raised when provider execution inputs cannot prove same-diff identity."""


def _workspace_review_diff_sha256(workspace: Path) -> str:
    result = subprocess.run(
        ["git", "diff", "--binary"],
        cwd=workspace,
        check=False,
        capture_output=True,
    )
    if result.returncode:
        raise ProviderBatchError(f"cannot read workspace diff: {workspace.name}")
    return canonical_review_diff_sha256(result.stdout)


def _validate_manifest(manifest: dict[str, Any], diff_root: Path) -> list[dict[str, Any]]:
    if manifest.get("source_type") != "observed_output":
        raise ProviderBatchError("provider batch requires observed_output manifest")
    if manifest.get("status") != "approved_for_provider_execution":
        raise ProviderBatchError("provider batch requires approved manifest")
    candidates = manifest.get("candidates")
    if not isinstance(candidates, list) or not candidates:
        raise ProviderBatchError("provider batch requires candidates")
    if any(not isinstance(candidate, dict) for candidate in candidates):
        raise ProviderBatchError("provider batch candidates must be objects")
    if len({candidate.get("case_id") for candidate in candidates}) != len(candidates):
        raise ProviderBatchError("provider batch case_id must be unique")
    failures = verify_observed_candidate_hashes(manifest, diff_root)
    if failures:
        raise ProviderBatchError("candidate hash verification failed: " + ", ".join(failures))
    return candidates


def _checkpoint_path(checkpoint_dir: Path, case_id: str) -> Path:
    return checkpoint_dir / f"{case_id}.json"


def _load_completed_checkpoint(
    checkpoint_dir: Path | None, case_id: str, expected_hash: str
) -> dict[str, Any] | None:
    if checkpoint_dir is None:
        return None
    path = _checkpoint_path(checkpoint_dir, case_id)
    if not path.is_file():
        return None
    try:
        checkpoint = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    codex = checkpoint.get("providers", {}).get("codex", {})
    if (
        checkpoint.get("case_id") != case_id
        or checkpoint.get("diff_sha256") != expected_hash
        or codex.get("status") != "completed"
    ):
        return None
    return checkpoint


def _write_checkpoint(checkpoint_dir: Path | None, case: dict[str, Any]) -> None:
    if checkpoint_dir is None:
        return
    checkpoint_dir.mkdir(parents=True, exist_ok=True)
    path = _checkpoint_path(checkpoint_dir, str(case["case_id"]))
    temporary = path.with_name(f".{path.name}.tmp")
    temporary.write_text(json.dumps(case, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temporary.replace(path)


def build_codex_provider_batch(
    manifest: dict[str, Any],
    *,
    diff_root: str | Path,
    workspace_root: str | Path,
    checkpoint_dir: str | Path | None = None,
    timeout_sec: int,
    run_review: Callable[..., dict[str, Any]] = run_codex_review,
) -> dict[str, Any]:
    """Execute Codex only after confirming every workspace has the approved diff.

    OMC output is deliberately recorded as ``not_run``. A Codex-only collection is
    useful evidence, but is never sufficient to claim provider replacement.
    """
    if isinstance(timeout_sec, bool) or not isinstance(timeout_sec, int) or timeout_sec <= 0:
        raise ProviderBatchError("timeout_sec requires a positive integer")
    root = Path(workspace_root)
    checkpoints = Path(checkpoint_dir) if checkpoint_dir is not None else None
    candidates = _validate_manifest(manifest, Path(diff_root))
    prepared: list[tuple[dict[str, Any], Path, str]] = []
    # Prove every input before spending provider budget on the first case.
    for candidate in candidates:
        case_id = str(candidate["case_id"])
        workspace = root / case_id
        if not workspace.is_dir():
            raise ProviderBatchError(f"workspace missing: {case_id}")
        expected_hash = str(candidate["diff_sha256"])
        approved_diff = resolve_observed_candidate_path(manifest, candidate, diff_root).read_bytes()
        if _workspace_review_diff_sha256(workspace) != canonical_review_diff_sha256(approved_diff):
            raise ProviderBatchError(f"workspace diff hash mismatch: {case_id}")
        prepared.append((candidate, workspace, expected_hash))

    cases: list[dict[str, Any]] = []
    all_completed = True
    for candidate, workspace, expected_hash in prepared:
        case_id = str(candidate["case_id"])
        case = _load_completed_checkpoint(checkpoints, case_id, expected_hash)
        if case is not None:
            cases.append(case)
            continue
        codex_result = run_review(workspace, case_id=case_id, diff_id=expected_hash, timeout_sec=timeout_sec)
        if codex_result.get("status") != "completed":
            all_completed = False
        case = {
            "case_id": case_id,
            "diff_sha256": expected_hash,
            "providers": {
                "codex": codex_result,
                "omc-review": {"status": "not_run", "execution_mode": "not_run"},
            },
        }
        _write_checkpoint(checkpoints, case)
        cases.append(case)
    return {
        "status": (
            "completed_provider_runs_pending_adjudication"
            if all_completed
            else "provider_runs_incomplete"
        ),
        "recorded_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "source_type": "observed_output",
        "cases": cases,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", required=True, type=Path)
    parser.add_argument("--diff-root", required=True, type=Path)
    parser.add_argument("--workspace-root", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--checkpoint-dir", type=Path)
    parser.add_argument("--timeout-sec", type=int, default=300)
    args = parser.parse_args()

    manifest = json.loads(args.manifest.read_text(encoding="utf-8"))
    batch = build_codex_provider_batch(
        manifest,
        diff_root=args.diff_root,
        workspace_root=args.workspace_root,
        checkpoint_dir=args.checkpoint_dir or args.output.with_suffix(".cases"),
        timeout_sec=args.timeout_sec,
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(batch, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"provider batch written: {args.output} ({len(batch['cases'])} cases)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
