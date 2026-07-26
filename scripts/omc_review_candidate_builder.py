#!/usr/bin/env python3
"""Build a reviewable anonymized candidate set from private observed diffs."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import subprocess
import tempfile
from collections.abc import Callable
from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from omc_review_compare import (
    _validate_anonymized_diff,
    _validate_anonymized_value,
    verify_observed_candidate_hashes,
)


class CandidateBuildError(ValueError):
    """Raised when a candidate cannot safely enter the anonymized input set."""


_CASE_ID_PATTERN = re.compile(r"^[a-z0-9][a-z0-9-]*$")


def _read_git_diff(candidate: dict[str, Any]) -> str:
    source_diff_path = candidate.get("source_diff_path")
    if isinstance(source_diff_path, str) and source_diff_path:
        path = Path(source_diff_path)
        if not path.is_file():
            raise CandidateBuildError("candidate source_diff_path does not exist")
        return path.read_text(encoding="utf-8")
    source_repo = candidate.get("source_repo")
    source_commit = candidate.get("source_commit")
    if not isinstance(source_repo, str) or not source_repo:
        raise CandidateBuildError("candidate requires private source_repo")
    if not isinstance(source_commit, str) or not source_commit:
        raise CandidateBuildError("candidate requires source_commit")
    result = subprocess.run(
        ["git", "-C", source_repo, "show", "--format=", "--binary", source_commit],
        check=False,
        capture_output=True,
        text=True,
    )
    if result.returncode:
        raise CandidateBuildError(f"failed to read source commit {source_commit}: {result.stderr.strip()}")
    return result.stdout


def _normalize_candidate(
    candidate: dict[str, Any],
    read_diff: Callable[[dict[str, Any]], str],
) -> tuple[dict[str, Any], str]:
    case_id = candidate.get("case_id")
    source_commit = candidate.get("source_commit")
    source_title = candidate.get("source_title")
    if not isinstance(case_id, str) or not _CASE_ID_PATTERN.fullmatch(case_id):
        raise CandidateBuildError("candidate case_id must use lowercase letters, digits, and hyphens")
    if not isinstance(source_commit, str) or not source_commit.strip():
        raise CandidateBuildError(f"{case_id}: source_commit is required")
    if not isinstance(source_title, str) or not source_title.strip():
        raise CandidateBuildError(f"{case_id}: source_title is required")
    try:
        _validate_anonymized_value(source_title, "source_title")
    except ValueError as error:
        raise CandidateBuildError(f"{case_id}: {error}") from error

    diff = read_diff(candidate)
    if not isinstance(diff, str):
        raise CandidateBuildError(f"{case_id}: source diff must be text")
    redactions = candidate.get("redactions", {})
    if not isinstance(redactions, dict) or any(
        not isinstance(source, str) or not isinstance(replacement, str)
        for source, replacement in redactions.items()
    ):
        raise CandidateBuildError(f"{case_id}: redactions must be a string-to-string mapping")
    for source, replacement in redactions.items():
        if not source or source not in diff:
            raise CandidateBuildError(f"{case_id}: redaction source was not found")
        diff = diff.replace(source, replacement)

    try:
        _validate_anonymized_diff(diff)
    except ValueError as error:
        raise CandidateBuildError(f"{case_id}: {error}") from error

    output = {
        "case_id": case_id,
        "source_commit": source_commit,
        "source_title": source_title,
        "diff_path": f"{case_id}.diff",
        "diff_sha256": hashlib.sha256(diff.encode("utf-8")).hexdigest(),
        "anonymized": False,
        "anonymization_status": "pending_review",
    }
    return output, diff


def build_candidate_bundle(
    spec: dict[str, Any],
    output_root: str | Path,
    *,
    read_diff: Callable[[dict[str, Any]], str] | None = None,
) -> dict[str, Any]:
    """Write validated diffs and return a provider-neutral pending-review manifest.

    The private input spec may contain ``source_repo`` and literal redactions. Neither
    is persisted in the resulting manifest, which must still receive human
    anonymization approval before provider execution.
    """
    if spec.get("source_type") != "observed_output":
        raise CandidateBuildError("candidate spec source_type must be observed_output")
    candidates = spec.get("candidates")
    if not isinstance(candidates, list) or not candidates:
        raise CandidateBuildError("candidate spec requires a non-empty candidates list")
    if any(not isinstance(candidate, dict) for candidate in candidates):
        raise CandidateBuildError("candidate spec entries must be objects")

    reader = read_diff or _read_git_diff
    staged: list[tuple[dict[str, Any], str]] = []
    case_ids: set[str] = set()
    for candidate in candidates:
        normalized, diff = _normalize_candidate(candidate, reader)
        case_id = normalized["case_id"]
        if case_id in case_ids:
            raise CandidateBuildError(f"duplicate case_id: {case_id}")
        case_ids.add(case_id)
        staged.append((normalized, diff))

    root = Path(output_root)
    private_source_paths = {
        Path(str(candidate["source_diff_path"])).resolve()
        for candidate in candidates
        if isinstance(candidate.get("source_diff_path"), str) and candidate["source_diff_path"]
    }
    for candidate, _ in staged:
        if (root / candidate["diff_path"]).resolve() in private_source_paths:
            raise CandidateBuildError(
                f"{candidate['case_id']}: output must not overwrite a private source diff"
            )
    root.mkdir(parents=True, exist_ok=True)
    for candidate, diff in staged:
        (root / candidate["diff_path"]).write_text(diff, encoding="utf-8")

    return {
        "schema_version": 1,
        "source_type": "observed_output",
        "status": "pending_anonymization_review",
        "input_root": str(root),
        "candidates": [candidate for candidate, _ in staged],
    }


def approve_candidate_manifest(
    manifest: dict[str, Any],
    diff_root: str | Path,
    *,
    approved_by: str,
    approved_at: str | None = None,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Promote a fully hash-verified pending set after human anonymization review."""
    if manifest.get("source_type") != "observed_output":
        raise CandidateBuildError("candidate manifest source_type must be observed_output")
    if manifest.get("status") != "pending_anonymization_review":
        raise CandidateBuildError("candidate manifest must be pending anonymization review")
    if not isinstance(approved_by, str) or not approved_by.strip():
        raise CandidateBuildError("approved_by is required")
    try:
        _validate_anonymized_value(approved_by, "approved_by")
    except ValueError as error:
        raise CandidateBuildError(str(error)) from error

    approved = deepcopy(manifest)
    candidates = approved.get("candidates")
    if not isinstance(candidates, list) or not candidates:
        raise CandidateBuildError("candidate manifest requires a non-empty candidates list")
    failures = verify_observed_candidate_hashes(approved, diff_root)
    if failures:
        raise CandidateBuildError("candidate hash verification failed: " + ", ".join(failures))
    if any(
        candidate.get("anonymized") is not False
        or candidate.get("anonymization_status") != "pending_review"
        for candidate in candidates
        if isinstance(candidate, dict)
    ) or any(not isinstance(candidate, dict) for candidate in candidates):
        raise CandidateBuildError("all candidates must be pending anonymization review")

    timestamp = approved_at or datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    for candidate in candidates:
        candidate["anonymized"] = True
        candidate["anonymization_status"] = "passed"
    approved["status"] = "approved_for_provider_execution"
    approved["anonymization_approval"] = {
        "approved_by": approved_by,
        "approved_at": timestamp,
    }
    audit = {
        "event": "anonymization_approved",
        "approved_by": approved_by,
        "approved_at": timestamp,
        "input_root": str(Path(diff_root)),
        "cases": [
            {"case_id": candidate["case_id"], "diff_sha256": candidate["diff_sha256"]}
            for candidate in candidates
        ],
    }
    return approved, audit


def build_private_provenance_audit(spec: dict[str, Any], manifest: dict[str, Any]) -> dict[str, Any]:
    """Return private source locators without exposing them in the public manifest."""
    source_candidates = spec.get("candidates")
    manifest_candidates = manifest.get("candidates")
    if not isinstance(source_candidates, list) or not isinstance(manifest_candidates, list):
        raise CandidateBuildError("spec and manifest require candidate lists")
    source_by_id = {
        candidate.get("case_id"): candidate
        for candidate in source_candidates
        if isinstance(candidate, dict) and isinstance(candidate.get("case_id"), str)
    }
    cases: list[dict[str, str]] = []
    for candidate in manifest_candidates:
        if not isinstance(candidate, dict):
            raise CandidateBuildError("manifest candidate must be an object")
        case_id = candidate.get("case_id")
        source = source_by_id.get(case_id)
        if source is None:
            raise CandidateBuildError(f"source provenance missing for {case_id}")
        locator = source.get("source_diff_path") or source.get("source_repo")
        if not isinstance(locator, str) or not locator:
            raise CandidateBuildError(f"source locator missing for {case_id}")
        cases.append(
            {
                "case_id": case_id,
                "source_commit": str(source.get("source_commit") or ""),
                "source_locator": locator,
            }
        )
    return {"event": "candidate_bundle_built", "cases": cases}


def _append_audit_record(path: Path, record: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as audit_file:
        audit_file.write(json.dumps(record, ensure_ascii=False) + "\n")
        audit_file.flush()
        os.fsync(audit_file.fileno())


def _write_manifest_atomically(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            dir=path.parent,
            prefix=f".{path.name}.",
            suffix=".tmp",
            delete=False,
        ) as temp_file:
            temp_path = Path(temp_file.name)
            json.dump(payload, temp_file, ensure_ascii=False, indent=2)
            temp_file.write("\n")
            temp_file.flush()
            os.fsync(temp_file.fileno())
        os.replace(temp_path, path)
    except Exception:
        if temp_path is not None:
            temp_path.unlink(missing_ok=True)
        raise


def persist_manifest_after_audit(
    manifest_path: Path,
    manifest: dict[str, Any],
    audit_path: Path,
    audit: dict[str, Any],
    *,
    append_audit: Callable[[Path, dict[str, Any]], None] = _append_audit_record,
    write_manifest: Callable[[Path, dict[str, Any]], None] = _write_manifest_atomically,
) -> None:
    """Persist audit first so a failed write cannot enable an unaudited run."""
    append_audit(audit_path, audit)
    write_manifest(manifest_path, manifest)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--spec", type=Path, help="Private source/review spec JSON")
    parser.add_argument("--output-root", type=Path)
    parser.add_argument("--manifest", required=True, type=Path)
    parser.add_argument("--approve", action="store_true", help="Approve an existing pending manifest")
    parser.add_argument("--diff-root", type=Path)
    parser.add_argument("--approved-by")
    parser.add_argument("--audit", type=Path, help="Private JSONL audit output")
    args = parser.parse_args()

    if args.approve:
        if not args.diff_root or not args.approved_by or not args.audit:
            parser.error("--approve requires --diff-root, --approved-by, and --audit")
        manifest = json.loads(args.manifest.read_text(encoding="utf-8"))
        approved, audit = approve_candidate_manifest(
            manifest,
            args.diff_root,
            approved_by=args.approved_by,
        )
        persist_manifest_after_audit(args.manifest, approved, args.audit, audit)
        print(f"candidate manifest approved: {args.manifest} ({len(approved['candidates'])} cases)")
        return 0

    if not args.spec or not args.output_root or not args.audit:
        parser.error("candidate build requires --spec, --output-root, and --audit")
    spec = json.loads(args.spec.read_text(encoding="utf-8"))
    manifest = build_candidate_bundle(spec, args.output_root)
    persist_manifest_after_audit(
        args.manifest,
        manifest,
        args.audit,
        build_private_provenance_audit(spec, manifest),
    )
    print(f"candidate manifest written: {args.manifest} ({len(manifest['candidates'])} cases)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
