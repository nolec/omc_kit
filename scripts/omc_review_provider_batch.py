#!/usr/bin/env python3
"""Collect reproducible Codex review results for approved observed diff cases."""

from __future__ import annotations

import argparse
import hashlib
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
from omc_review_runner import (
    _native_review_verdict,
    normalize_review_result,
    run_codex_review,
    run_native_codex_review,
)


class ProviderBatchError(ValueError):
    """Raised when provider execution inputs cannot prove same-diff identity."""


def _workspace_review_diff_sha256(workspace: Path) -> str:
    result = subprocess.run(
        ["git", "diff", "--binary", "HEAD"],
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


def _prepare_verified_workspaces(
    manifest: dict[str, Any], *, diff_root: Path, workspace_root: Path
) -> list[tuple[dict[str, Any], Path, str]]:
    """Prove all approved workspaces before either provider consumes a review."""
    candidates = _validate_manifest(manifest, diff_root)
    prepared: list[tuple[dict[str, Any], Path, str]] = []
    for candidate in candidates:
        case_id = str(candidate["case_id"])
        workspace = workspace_root / case_id
        if not workspace.is_dir():
            raise ProviderBatchError(f"workspace missing: {case_id}")
        expected_hash = str(candidate["diff_sha256"])
        approved_diff = resolve_observed_candidate_path(manifest, candidate, diff_root).read_bytes()
        if _workspace_review_diff_sha256(workspace) != canonical_review_diff_sha256(approved_diff):
            raise ProviderBatchError(f"workspace diff hash mismatch: {case_id}")
        prepared.append((candidate, workspace, expected_hash))
    return prepared


def _sha256_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _execution_batch_id(*, cases: list[tuple[dict[str, Any], Path, str]], model: str, skill_sha256: str, prompt_sha256: str) -> str:
    identity = {
        "case_hashes": [[str(candidate["case_id"]), expected_hash] for candidate, _, expected_hash in cases],
        "model": model,
        "prompt_sha256": prompt_sha256,
        "skill_sha256": skill_sha256,
    }
    return "omc-review-" + _sha256_text(json.dumps(identity, sort_keys=True, separators=(",", ":")))[:16]


def _verify_execution_batch_workspaces(cases: list[dict[str, Any]]) -> None:
    """Reject results when a manual-review workspace drifted after preflight."""
    for case in cases:
        case_id = str(case.get("case_id") or "")
        workspace_value = case.get("workspace")
        expected_hash = case.get("workspace_diff_sha256")
        if not case_id or not isinstance(workspace_value, str) or not isinstance(expected_hash, str):
            raise ProviderBatchError("invalid OMC execution packet case")
        workspace = Path(workspace_value)
        if not workspace.is_dir():
            raise ProviderBatchError(f"workspace missing: {case_id}")
        if _workspace_review_diff_sha256(workspace) != expected_hash:
            raise ProviderBatchError(f"workspace diff hash mismatch: {case_id}")


def _verify_execution_batch_skill(skill: dict[str, Any]) -> None:
    """Keep manual OMC results tied to the exact review-skill revision."""
    path_value = skill.get("path")
    expected_hash = skill.get("sha256")
    if not isinstance(path_value, str) or not isinstance(expected_hash, str):
        raise ProviderBatchError("invalid OMC execution packet skill")
    path = Path(path_value)
    if not path.is_file():
        raise ProviderBatchError("review skill missing")
    if hashlib.sha256(path.read_bytes()).hexdigest() != expected_hash:
        raise ProviderBatchError("review skill hash mismatch")


def build_omc_execution_batch(
    manifest: dict[str, Any],
    *,
    diff_root: str | Path,
    workspace_root: str | Path,
    skill_path: str | Path,
    model: str,
    prompt: str,
) -> dict[str, Any]:
    """Prepare a manual OMC review packet without claiming that OMC executed.

    OMC review is interactive and model-neutral. This packet records the exact
    same-diff input, skill revision, model choice, and execution prompt that a
    human/operator must use before its output can be ingested.
    """
    if not isinstance(model, str) or not model.strip():
        raise ProviderBatchError("model is required for OMC execution batch")
    if not isinstance(prompt, str) or not prompt.strip():
        raise ProviderBatchError("prompt is required for OMC execution batch")
    skill = Path(skill_path).resolve()
    if not skill.is_file():
        raise ProviderBatchError("OMC skill file is required for execution batch")

    prepared = _prepare_verified_workspaces(
        manifest, diff_root=Path(diff_root), workspace_root=Path(workspace_root)
    )
    normalized_model = model.strip()
    normalized_prompt = prompt.strip()
    skill_sha256 = hashlib.sha256(skill.read_bytes()).hexdigest()
    prompt_sha256 = _sha256_text(normalized_prompt)
    batch_id = _execution_batch_id(
        cases=prepared,
        model=normalized_model,
        skill_sha256=skill_sha256,
        prompt_sha256=prompt_sha256,
    )
    return {
        "status": "ready_for_omc_execution",
        "recorded_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "source_type": "observed_output",
        "provider": "omc-review",
        "batch_id": batch_id,
        "model": normalized_model,
        "skill": {"path": str(skill), "sha256": skill_sha256},
        "prompt": {"sha256": prompt_sha256, "text": normalized_prompt},
        "cases": [
            {
                "case_id": str(candidate["case_id"]),
                "diff_sha256": expected_hash,
                "workspace_diff_sha256": _workspace_review_diff_sha256(workspace),
                "workspace": str(workspace),
            }
            for candidate, workspace, expected_hash in prepared
        ],
    }


def ingest_omc_execution_results(
    execution_batch: dict[str, Any], results: list[dict[str, Any]]
) -> dict[str, Any]:
    """Normalize only operator-captured OMC output matching an execution packet."""
    if execution_batch.get("status") != "ready_for_omc_execution":
        raise ProviderBatchError("OMC execution batch is not ready")
    if not isinstance(results, list):
        raise ProviderBatchError("OMC execution results must be a list")
    expected_cases = execution_batch.get("cases")
    skill = execution_batch.get("skill")
    prompt = execution_batch.get("prompt")
    model = execution_batch.get("model")
    batch_id = execution_batch.get("batch_id")
    if (
        not isinstance(expected_cases, list)
        or not isinstance(skill, dict)
        or not isinstance(prompt, dict)
        or not isinstance(model, str)
        or not isinstance(batch_id, str)
    ):
        raise ProviderBatchError("invalid OMC execution batch")
    expected = {str(case.get("case_id")): case for case in expected_cases if isinstance(case, dict)}
    if len(expected) != len(expected_cases) or len(results) != len(expected):
        raise ProviderBatchError("OMC execution result case count mismatch")
    _verify_execution_batch_skill(skill)
    _verify_execution_batch_workspaces(expected_cases)

    collected: dict[str, dict[str, Any]] = {}
    for result in results:
        if not isinstance(result, dict):
            raise ProviderBatchError("OMC execution result must be an object")
        case_id = str(result.get("case_id") or "")
        if not case_id or case_id in collected or case_id not in expected:
            raise ProviderBatchError("OMC execution result case_id mismatch")
        packet = expected[case_id]
        if result.get("diff_sha256") != packet.get("diff_sha256"):
            raise ProviderBatchError(f"diff hash mismatch: {case_id}")
        if result.get("model") != model:
            raise ProviderBatchError(f"model mismatch: {case_id}")
        if result.get("skill_sha256") != skill.get("sha256"):
            raise ProviderBatchError(f"skill hash mismatch: {case_id}")
        if result.get("prompt_sha256") != prompt.get("sha256"):
            raise ProviderBatchError(f"prompt hash mismatch: {case_id}")
        if not isinstance(result.get("recorded_at"), str) or not result["recorded_at"].strip():
            raise ProviderBatchError(f"recorded_at missing: {case_id}")
        stdout = result.get("stdout")
        if not isinstance(stdout, str) or not stdout.strip():
            raise ProviderBatchError(f"stdout missing: {case_id}")
        duration_ms = result.get("duration_ms")
        if isinstance(duration_ms, bool) or not isinstance(duration_ms, (int, float)) or duration_ms < 0:
            raise ProviderBatchError(f"duration_ms invalid: {case_id}")
        try:
            normalized = normalize_review_result(
                provider="omc-review",
                case_id=case_id,
                diff_id=str(packet["diff_sha256"]),
                batch_id=batch_id,
                status="completed",
                stdout=stdout,
                stderr=str(result.get("stderr") or ""),
                duration_ms=duration_ms,
                runner="manual_omc_review",
                model=model,
            )
        except ValueError as error:
            raise ProviderBatchError(f"invalid OMC review output: {case_id}: {error}") from error
        normalized["raw_output_sha256"] = _sha256_text(stdout)
        normalized["recorded_at"] = result["recorded_at"]
        collected[case_id] = normalized

    return {
        "status": "completed_omc_runs_pending_adjudication",
        "recorded_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "source_type": "observed_output",
        "batch_id": batch_id,
        "cases": [
            {
                "case_id": case_id,
                "diff_sha256": str(packet["diff_sha256"]),
                "providers": {"omc-review": collected[case_id]},
            }
            for case_id, packet in ((str(case["case_id"]), case) for case in expected_cases)
        ],
    }


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


def _recover_native_artifact(
    artifact_path: Path,
    *,
    case_id: str,
    diff_sha256: str,
    source_commit: str | None,
    clean_baseline: bool,
) -> dict[str, Any] | None:
    """Recover a completed native review when a caller died after artifact write."""
    if not artifact_path.is_file():
        return None
    try:
        artifact = json.loads(artifact_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if (
        artifact.get("provider") != "codex"
        or artifact.get("runner") != "codex native review"
        or artifact.get("case_id") != case_id
        or artifact.get("diff_sha256") != diff_sha256
        or artifact.get("source_commit") != source_commit
        # V1 artifacts predate clean-baseline support and are compatible only
        # with the legacy, instruction-bearing execution mode.
        or artifact.get("clean_baseline", False) is not clean_baseline
    ):
        return None
    stdout = artifact.get("stdout")
    stderr = artifact.get("stderr")
    exit_code = artifact.get("exit_code")
    duration_ms = artifact.get("duration_ms", 0)
    if (
        not isinstance(stdout, str)
        or not isinstance(stderr, str)
        or not isinstance(exit_code, int)
        or isinstance(duration_ms, bool)
        or not isinstance(duration_ms, (int, float))
        or duration_ms < 0
    ):
        return None
    verdict = artifact.get("adapter_verdict") or _native_review_verdict(stdout)
    if verdict not in {"APPROVE", "REVISE"}:
        return None
    status = "completed" if exit_code == 0 else "failed"
    normalized_stdout = f"{stdout.rstrip()}\nVERDICT: {verdict}"
    result = normalize_review_result(
        provider="codex",
        case_id=case_id,
        diff_id=diff_sha256,
        status=status,
        stdout=normalized_stdout,
        stderr=stderr,
        duration_ms=duration_ms,
        runner="codex native review",
        verdict_override=verdict,
    )
    result["execution_artifacts"] = {
        "event_stream_captured": True,
        "final_message_captured": bool(stdout.strip()),
        "exit_code": exit_code,
        "snapshot_used": True,
        "workspace_mutated": False,
        "native_review": True,
        "clean_baseline": clean_baseline,
        "verdict_source": "recovered_from_durable_artifact",
        "durable_output_retained": True,
        "durable_artifact": {
            "path": artifact_path.name,
            "sha256": hashlib.sha256(artifact_path.read_bytes()).hexdigest(),
            "captured_stdout_sha256": artifact.get("captured_stdout_sha256"),
            "retained_stdout_sha256": artifact.get("retained_stdout_sha256"),
        },
    }
    return result


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
    prepared = _prepare_verified_workspaces(
        manifest, diff_root=Path(diff_root), workspace_root=root
    )

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


def build_native_codex_review_batch(
    manifest: dict[str, Any],
    *,
    diff_root: str | Path,
    workspace_root: str | Path,
    artifact_dir: str | Path,
    checkpoint_dir: str | Path | None = None,
    timeout_sec: int,
    clean_baseline: bool = False,
    run_review: Callable[..., dict[str, Any]] = run_native_codex_review,
) -> dict[str, Any]:
    """Run native Codex review and retain every provider output before comparison.

    A completed CLI process without a durable per-case artifact is intentionally
    incomplete evidence: it cannot support a review-agent replacement claim.
    """
    if isinstance(timeout_sec, bool) or not isinstance(timeout_sec, int) or timeout_sec <= 0:
        raise ProviderBatchError("timeout_sec requires a positive integer")
    artifacts = Path(artifact_dir)
    if artifacts.resolve().is_relative_to(Path("/private/tmp")):
        raise ProviderBatchError("native review artifacts must not use an ephemeral directory")
    checkpoints = Path(checkpoint_dir) if checkpoint_dir is not None else None
    prepared = _prepare_verified_workspaces(
        manifest, diff_root=Path(diff_root), workspace_root=Path(workspace_root)
    )

    cases: list[dict[str, Any]] = []
    all_completed = True
    for candidate, workspace, expected_hash in prepared:
        case_id = str(candidate["case_id"])
        source_commit = str(candidate.get("source_commit") or "") or None
        artifact_path = artifacts / f"{case_id}.json"
        case = _load_completed_checkpoint(checkpoints, case_id, expected_hash)
        codex = case.get("providers", {}).get("codex", {}) if case is not None else {}
        if (
            case is not None
            and codex.get("runner") == "codex native review"
            and codex.get("execution_artifacts", {}).get("clean_baseline") is clean_baseline
            and codex.get("execution_artifacts", {}).get("durable_output_retained") is True
        ):
            cases.append(case)
            continue
        recovered = _recover_native_artifact(
            artifact_path,
            case_id=case_id,
            diff_sha256=expected_hash,
            source_commit=source_commit,
            clean_baseline=clean_baseline,
        )
        if recovered is not None:
            case = {
                "case_id": case_id,
                "diff_sha256": expected_hash,
                "providers": {
                    "codex": recovered,
                    "omc-review": {"status": "not_run", "execution_mode": "not_run"},
                },
            }
            _write_checkpoint(checkpoints, case)
            cases.append(case)
            continue
        codex_result = run_review(
            workspace,
            case_id=case_id,
            diff_id=expected_hash,
            timeout_sec=timeout_sec,
            artifact_path=artifact_path,
            source_commit=source_commit,
            clean_baseline=clean_baseline,
        )
        durable = codex_result.get("execution_artifacts", {}).get("durable_output_retained") is True
        if codex_result.get("status") != "completed" or not durable:
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
            "completed_native_provider_runs_pending_adjudication"
            if all_completed
            else "native_provider_runs_incomplete"
        ),
        "recorded_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "source_type": "observed_output",
        "provider_runner": "codex native review",
        "clean_baseline": clean_baseline,
        "artifact_dir": str(artifacts),
        "cases": cases,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument(
        "--prepare-omc",
        action="store_true",
        help="write a same-diff OMC execution packet without running OMC",
    )
    mode.add_argument(
        "--ingest-omc-results",
        type=Path,
        help="normalize operator-captured OMC results against an execution packet",
    )
    mode.add_argument(
        "--native-codex-review",
        action="store_true",
        help="run native codex review and retain durable per-case provider artifacts",
    )
    parser.add_argument("--manifest", type=Path)
    parser.add_argument("--diff-root", type=Path)
    parser.add_argument("--workspace-root", type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--checkpoint-dir", type=Path)
    parser.add_argument("--artifact-dir", type=Path)
    parser.add_argument(
        "--clean-baseline",
        action="store_true",
        help="exclude project-level AI instructions from native Codex review snapshots",
    )
    parser.add_argument("--timeout-sec", type=int, default=300)
    parser.add_argument("--skill-path", type=Path)
    parser.add_argument("--model")
    parser.add_argument("--prompt")
    parser.add_argument("--execution-batch", type=Path)
    args = parser.parse_args()

    if args.ingest_omc_results is not None:
        if args.execution_batch is None:
            parser.error("--ingest-omc-results requires --execution-batch")
        execution_batch = json.loads(args.execution_batch.read_text(encoding="utf-8"))
        results = json.loads(args.ingest_omc_results.read_text(encoding="utf-8"))
        batch = ingest_omc_execution_results(execution_batch, results)
    else:
        if args.manifest is None or args.diff_root is None or args.workspace_root is None:
            parser.error("--manifest, --diff-root, and --workspace-root are required")
        manifest = json.loads(args.manifest.read_text(encoding="utf-8"))
        if args.prepare_omc:
            if args.skill_path is None or args.model is None or args.prompt is None:
                parser.error("--prepare-omc requires --skill-path, --model, and --prompt")
            batch = build_omc_execution_batch(
                manifest,
                diff_root=args.diff_root,
                workspace_root=args.workspace_root,
                skill_path=args.skill_path,
                model=args.model,
                prompt=args.prompt,
            )
        elif args.native_codex_review:
            if args.artifact_dir is None:
                parser.error("--native-codex-review requires --artifact-dir")
            batch = build_native_codex_review_batch(
                manifest,
                diff_root=args.diff_root,
                workspace_root=args.workspace_root,
                artifact_dir=args.artifact_dir,
                checkpoint_dir=args.checkpoint_dir or args.output.with_suffix(".cases"),
                timeout_sec=args.timeout_sec,
                clean_baseline=args.clean_baseline,
            )
        else:
            batch = build_codex_provider_batch(
                manifest,
                diff_root=args.diff_root,
                workspace_root=args.workspace_root,
                checkpoint_dir=args.checkpoint_dir or args.output.with_suffix(".cases"),
                timeout_sec=args.timeout_sec,
            )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(batch, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"provider batch written: {args.output} ({len(batch['cases'])} cases; {batch['status']})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
