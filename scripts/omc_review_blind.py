#!/usr/bin/env python3
"""Build provider-neutral review inputs for independent adjudication."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from typing import Any


_FORBIDDEN_METADATA_KEYS = {
    "adjudication_status",
    "findings",
    "gold_findings",
    "providers",
    "raw_evidence_refs",
    "verdict",
}


def _resolve_diff_path(diff_root: Path, diff_path: str) -> Path:
    candidate = diff_root / diff_path
    if candidate.is_file():
        return candidate
    basename_candidate = diff_root / Path(diff_path).name
    if basename_candidate.is_file():
        return basename_candidate
    raise ValueError(f"diff file not found: {diff_path}")


def _validate_candidate_metadata(candidate: dict[str, Any]) -> None:
    leaked = sorted(_FORBIDDEN_METADATA_KEYS.intersection(candidate))
    if leaked:
        raise ValueError(f"provider result metadata is not allowed: {', '.join(leaked)}")
    required = {"case_id", "diff_path", "diff_sha256", "source_commit"}
    missing = sorted(required.difference(candidate))
    if missing:
        raise ValueError(f"candidate missing required fields: {', '.join(missing)}")


def build_blind_pack(
    manifest: dict[str, Any], diff_root: str | Path, case_ids: list[str]
) -> dict[str, Any]:
    """Return only diff and source identity data, never provider observations."""

    if manifest.get("source_type") != "observed_output":
        raise ValueError("blind pack requires observed_output source_type")
    if len(case_ids) != len(set(case_ids)):
        raise ValueError("duplicate case_id requested")

    candidates = manifest.get("candidates")
    if not isinstance(candidates, list):
        raise ValueError("manifest candidates must be a list")
    by_id: dict[str, dict[str, Any]] = {}
    for candidate in candidates:
        if not isinstance(candidate, dict):
            raise ValueError("manifest candidate must be an object")
        _validate_candidate_metadata(candidate)
        case_id = candidate["case_id"]
        if case_id in by_id:
            raise ValueError(f"duplicate case_id in manifest: {case_id}")
        by_id[case_id] = candidate

    missing = [case_id for case_id in case_ids if case_id not in by_id]
    if missing:
        raise ValueError(f"requested case_id not found: {', '.join(missing)}")

    root = Path(diff_root)
    cases: list[dict[str, str]] = []
    for case_id in case_ids:
        candidate = by_id[case_id]
        path = _resolve_diff_path(root, candidate["diff_path"])
        raw = path.read_bytes()
        actual_sha256 = hashlib.sha256(raw).hexdigest()
        if actual_sha256 != candidate["diff_sha256"]:
            raise ValueError(
                f"sha256 mismatch for {case_id}: expected {candidate['diff_sha256']}, "
                f"got {actual_sha256}"
            )
        cases.append(
            {
                "case_id": case_id,
                "source_commit": candidate["source_commit"],
                "diff_sha256": actual_sha256,
                "diff": raw.decode("utf-8"),
            }
        )

    return {
        "status": "ready_for_independent_adjudication",
        "source_type": "observed_output",
        "case_count": len(cases),
        "cases": cases,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", required=True, type=Path)
    parser.add_argument("--diff-root", required=True, type=Path)
    parser.add_argument("--case-id", action="append", required=True, dest="case_ids")
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()

    manifest = json.loads(args.manifest.read_text(encoding="utf-8"))
    pack = build_blind_pack(manifest, args.diff_root, args.case_ids)
    args.output.write_text(json.dumps(pack, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"blind pack written: {args.output} ({pack['case_count']} cases)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
