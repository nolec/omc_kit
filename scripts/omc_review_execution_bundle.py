#!/usr/bin/env python3
"""Prepare, run, and collect durable external OMC Review executions.

The desktop agent can be interrupted while an external provider is still
running. This adapter keeps each case independent: it writes immutable prompt
inputs first, records a running marker before provider invocation, and only
lets complete artifacts enter the existing same-diff ingestion contract.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from omc_review_provider_batch import (
    ProviderBatchError,
    _verify_execution_batch_skill,
    _verify_execution_batch_workspaces,
    ingest_omc_execution_results,
)
from omc_review_runner import (
    _as_text,
    _extract_codex_review_output,
    _isolated_review_workspace,
    _redact_output,
)


class ExecutionBundleError(ValueError):
    """Raised when a durable OMC execution bundle is incomplete or invalid."""


def _timestamp() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _write_json(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temporary.replace(path)


def _load_json(path: Path, *, label: str) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise ExecutionBundleError(f"invalid {label}: {path}") from error
    if not isinstance(value, dict):
        raise ExecutionBundleError(f"invalid {label}: {path}")
    return value


def _validate_batch(batch: dict[str, Any]) -> None:
    if batch.get("status") != "ready_for_omc_execution":
        raise ExecutionBundleError("execution batch is not ready")
    cases = batch.get("cases")
    skill = batch.get("skill")
    prompt = batch.get("prompt")
    if not isinstance(cases, list) or not cases or not isinstance(skill, dict) or not isinstance(prompt, dict):
        raise ExecutionBundleError("invalid execution batch")
    if not isinstance(prompt.get("text"), str) or not prompt["text"].strip():
        raise ExecutionBundleError("execution batch prompt is missing")
    try:
        _verify_execution_batch_skill(skill)
        _verify_execution_batch_workspaces(cases)
    except ProviderBatchError as error:
        raise ExecutionBundleError(str(error)) from error


def _case_by_id(batch: dict[str, Any], case_id: str) -> dict[str, Any]:
    for case in batch["cases"]:
        if isinstance(case, dict) and case.get("case_id") == case_id:
            return case
    raise ExecutionBundleError(f"unknown case: {case_id}")


def prepare_execution_bundle(execution_batch: dict[str, Any], *, output_dir: str | Path) -> dict[str, Any]:
    """Write immutable per-case prompt inputs before provider execution begins."""
    _validate_batch(execution_batch)
    root = Path(output_dir)
    skill_text = Path(execution_batch["skill"]["path"]).read_text(encoding="utf-8").rstrip()
    prompt_text = str(execution_batch["prompt"]["text"]).strip()
    _write_json(root / "execution-batch.json", execution_batch)

    cases: list[dict[str, Any]] = []
    for packet in execution_batch["cases"]:
        case_id = str(packet["case_id"])
        (root / "prompts").mkdir(parents=True, exist_ok=True)
        (root / "prompts" / f"{case_id}.txt").write_text(
            f"{skill_text}\n\n{prompt_text}\n", encoding="utf-8"
        )
        cases.append({"case_id": case_id, "status": "pending", "artifact": f"artifacts/{case_id}.json"})

    bundle = {
        "status": "ready_for_external_execution",
        "created_at": _timestamp(),
        "batch_id": execution_batch["batch_id"],
        "execution_batch": "execution-batch.json",
        "cases": cases,
    }
    _write_json(root / "bundle.json", bundle)
    return bundle


def run_execution_case(
    bundle_dir: str | Path,
    *,
    case_id: str,
    codex_binary: str | Path,
    timeout_sec: int = 1800,
) -> dict[str, Any]:
    """Run one case from a user terminal and retain its result even on failure."""
    root = Path(bundle_dir)
    batch = _load_json(root / "execution-batch.json", label="execution batch")
    _validate_batch(batch)
    packet = _case_by_id(batch, case_id)
    prompt_path = root / "prompts" / f"{case_id}.txt"
    if not prompt_path.is_file():
        raise ExecutionBundleError(f"prompt missing: {case_id}")
    binary = Path(codex_binary)
    if not binary.is_file():
        raise ExecutionBundleError(f"codex binary missing: {binary}")
    if timeout_sec <= 0:
        raise ExecutionBundleError("timeout_sec requires a positive integer")

    artifact_path = root / "artifacts" / f"{case_id}.json"
    running_path = root / "artifacts" / f"{case_id}.running.json"
    _write_json(running_path, {"case_id": case_id, "status": "running", "started_at": _timestamp()})
    started = time.monotonic()
    stdout = ""
    stderr = ""
    exit_code = 1
    try:
        with _isolated_review_workspace(packet["workspace"], exclude_project_instructions=True) as snapshot:
            completed = subprocess.run(
                [str(binary), "exec", "--ephemeral", "--json", "--sandbox", "read-only", "-"],
                cwd=snapshot.path,
                input=prompt_path.read_text(encoding="utf-8"),
                text=True,
                capture_output=True,
                timeout=timeout_sec,
                check=False,
            )
            stdout, _ = _extract_codex_review_output(completed.stdout)
            stderr = completed.stderr
            exit_code = completed.returncode
    except subprocess.TimeoutExpired as error:
        stdout = _as_text(error.stdout)
        stderr = _as_text(error.stderr) + "\nprovider execution timed out"
        exit_code = 124
    except (OSError, ValueError, subprocess.SubprocessError) as error:
        # A normal subprocess/setup failure is still a completed local attempt.
        # Persist it so an operator can diagnose and selectively rerun the case.
        stderr = f"provider execution failed: {error}"
    finally:
        duration_ms = round((time.monotonic() - started) * 1000)

    artifact = {
        "case_id": case_id,
        "status": "completed" if exit_code == 0 and stdout.strip() else "failed",
        "diff_sha256": packet["diff_sha256"],
        "model": batch["model"],
        "skill_sha256": batch["skill"]["sha256"],
        "prompt_sha256": batch["prompt"]["sha256"],
        "recorded_at": _timestamp(),
        "duration_ms": duration_ms,
        "stdout": _redact_output(_as_text(stdout)),
        "stderr": _redact_output(_as_text(stderr)),
        "exit_code": exit_code,
        "runner": "external_omc_review_bundle",
        "clean_baseline": True,
    }
    _write_json(artifact_path, artifact)
    running_path.unlink(missing_ok=True)
    return artifact


def run_execution_bundle(
    bundle_dir: str | Path,
    *,
    codex_binary: str | Path,
    timeout_sec: int = 1800,
) -> list[dict[str, Any]]:
    """Resume every incomplete case while preserving already completed artifacts."""
    root = Path(bundle_dir)
    batch = _load_json(root / "execution-batch.json", label="execution batch")
    _validate_batch(batch)
    outcomes: list[dict[str, Any]] = []
    for packet in batch["cases"]:
        case_id = str(packet["case_id"])
        artifact_path = root / "artifacts" / f"{case_id}.json"
        if artifact_path.is_file():
            artifact = _load_json(artifact_path, label=f"artifact for {case_id}")
            if artifact.get("status") == "completed":
                outcomes.append({"case_id": case_id, "status": "completed", "resumed": False})
                continue
        artifact = run_execution_case(
            root,
            case_id=case_id,
            codex_binary=codex_binary,
            timeout_sec=timeout_sec,
        )
        outcomes.append({"case_id": case_id, "status": artifact["status"], "resumed": True})
    return outcomes


def collect_execution_bundle(bundle_dir: str | Path) -> dict[str, Any]:
    """Validate all durable artifacts, then reuse the shared OMC ingestion gate."""
    root = Path(bundle_dir)
    batch = _load_json(root / "execution-batch.json", label="execution batch")
    results: list[dict[str, Any]] = []
    for packet in batch.get("cases", []):
        if not isinstance(packet, dict):
            raise ExecutionBundleError("invalid execution packet case")
        case_id = str(packet.get("case_id") or "")
        artifact_path = root / "artifacts" / f"{case_id}.json"
        if not artifact_path.is_file():
            raise ExecutionBundleError(f"missing completed artifact: {case_id}")
        artifact = _load_json(artifact_path, label=f"artifact for {case_id}")
        if artifact.get("status") != "completed":
            raise ExecutionBundleError(f"incomplete artifact: {case_id}")
        results.append(artifact)
    try:
        return ingest_omc_execution_results(batch, results)
    except ProviderBatchError as error:
        raise ExecutionBundleError(str(error)) from error


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    command = parser.add_subparsers(dest="command", required=True)
    prepare = command.add_parser("prepare")
    prepare.add_argument("--execution-batch", required=True, type=Path)
    prepare.add_argument("--output-dir", required=True, type=Path)
    run_case = command.add_parser("run-case")
    run_case.add_argument("--bundle-dir", required=True, type=Path)
    run_case.add_argument("--case-id", required=True)
    run_case.add_argument("--codex-binary", required=True, type=Path)
    run_case.add_argument("--timeout-sec", type=int, default=1800)
    run_all = command.add_parser("run-all")
    run_all.add_argument("--bundle-dir", required=True, type=Path)
    run_all.add_argument("--codex-binary", required=True, type=Path)
    run_all.add_argument("--timeout-sec", type=int, default=1800)
    collect = command.add_parser("collect")
    collect.add_argument("--bundle-dir", required=True, type=Path)
    collect.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()

    if args.command == "prepare":
        bundle = prepare_execution_bundle(
            _load_json(args.execution_batch, label="execution batch"), output_dir=args.output_dir
        )
        print(f"execution bundle ready: {args.output_dir} ({len(bundle['cases'])} cases)")
        return 0
    if args.command == "run-case":
        artifact = run_execution_case(
            args.bundle_dir,
            case_id=args.case_id,
            codex_binary=args.codex_binary,
            timeout_sec=args.timeout_sec,
        )
        print(f"case {args.case_id}: {artifact['status']}")
        return 0 if artifact["status"] == "completed" else 1
    if args.command == "run-all":
        outcomes = run_execution_bundle(
            args.bundle_dir,
            codex_binary=args.codex_binary,
            timeout_sec=args.timeout_sec,
        )
        completed = sum(outcome["status"] == "completed" for outcome in outcomes)
        resumed = sum(outcome["resumed"] for outcome in outcomes)
        print(f"cases completed: {completed}/{len(outcomes)} (executed now: {resumed})")
        return 0 if completed == len(outcomes) else 1

    collected = collect_execution_bundle(args.bundle_dir)
    _write_json(args.output, collected)
    print(f"OMC execution results written: {args.output} ({len(collected['cases'])} cases)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
