#!/usr/bin/env python3
"""Create a provenance-safe screening report for the V6 OMC/Codex rerun.

This is deliberately not a replacement verdict. File-and-line overlap is only
a reproducible candidate signal; semantic matching, false-positive labels, and
provider superiority remain blind human-adjudication decisions.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


_LINE_NUMBER_RE = re.compile(r"\d+")


def _timestamp() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _load(path: str | Path) -> dict[str, Any]:
    value = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"expected JSON object: {path}")
    return value


def _cases(payload: dict[str, Any], provider: str | None = None) -> dict[str, dict[str, Any]]:
    cases = payload.get("cases")
    if not isinstance(cases, list):
        raise ValueError("cases must be a list")
    collected: dict[str, dict[str, Any]] = {}
    for case in cases:
        if not isinstance(case, dict):
            raise ValueError("case must be an object")
        case_id = str(case.get("case_id") or "")
        if not case_id or case_id in collected:
            raise ValueError("case id must be unique")
        if provider is not None:
            result = case.get("providers", {}).get(provider)
            if not isinstance(result, dict) or result.get("status") != "completed":
                raise ValueError(f"provider result must be completed: {case_id}/{provider}")
        collected[case_id] = case
    return collected


def _relative_file(path: Any) -> tuple[str, bool]:
    value = str(path or "").replace("\\", "/")
    marker = "/workspace/"
    if marker in value:
        return value.split(marker, 1)[1], True
    return value.lstrip("/"), not value.startswith("/")


def _line_points(value: Any) -> set[int]:
    return {int(item) for item in _LINE_NUMBER_RE.findall(str(value or ""))}


def _line_overlap(left: Any, right: Any) -> bool:
    left_points = _line_points(left)
    right_points = _line_points(right)
    if not left_points or not right_points:
        return False
    # Ranges are encoded as "33-42". Expand only the small intervals used by
    # review evidence and retain comma-separated individual locations.
    def expanded(value: Any) -> set[int]:
        result: set[int] = set()
        for chunk in str(value or "").split(","):
            values = [int(item) for item in _LINE_NUMBER_RE.findall(chunk)]
            if len(values) == 2 and "-" in chunk:
                start, end = sorted(values)
                result.update(range(start, min(end, start + 1000) + 1))
            else:
                result.update(values)
        return result
    return bool(expanded(left) & expanded(right))


def _structural_evidence_complete(finding: dict[str, Any]) -> bool:
    return all(str(finding.get(field) or "").strip() for field in ("file", "line", "message"))


def build_input_provenance(inputs: dict[str, str | Path]) -> dict[str, dict[str, str]]:
    """Bind a persisted screening report to the exact source snapshots used."""
    provenance: dict[str, dict[str, str]] = {}
    project_root = Path.cwd().resolve()
    for name, value in inputs.items():
        path = Path(value).resolve()
        if not path.is_file():
            raise ValueError(f"input file missing: {name}")
        try:
            display_path = str(path.relative_to(project_root))
        except ValueError:
            display_path = f"<external>/{path.name}"
        provenance[name] = {
            "path": display_path,
            "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
        }
    return provenance


def _provider_summary(
    provider: str,
    provider_cases: dict[str, dict[str, Any]],
    gold_cases: dict[str, dict[str, Any]],
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    gold_total = 0
    candidate_count = 0
    finding_total = 0
    unmatched_total = 0
    evidence_complete = 0
    relative_paths = True
    case_rows: list[dict[str, Any]] = []
    for case_id, gold_case in gold_cases.items():
        result = provider_cases[case_id]["providers"][provider]
        findings = result.get("findings", [])
        if not isinstance(findings, list):
            raise ValueError(f"findings must be a list: {case_id}/{provider}")
        gold_findings = gold_case.get("gold_findings", [])
        if not isinstance(gold_findings, list):
            raise ValueError(f"gold findings must be a list: {case_id}")
        gold_total += len(gold_findings)
        finding_total += len(findings)
        matched_gold: set[int] = set()
        matched_findings: set[int] = set()
        for actual_index, actual in enumerate(findings):
            if not isinstance(actual, dict):
                raise ValueError(f"finding must be an object: {case_id}/{provider}")
            file_name, path_is_relative = _relative_file(actual.get("file"))
            relative_paths = relative_paths and path_is_relative
            if _structural_evidence_complete(actual):
                evidence_complete += 1
            for gold_index, gold in enumerate(gold_findings):
                if gold_index in matched_gold or not isinstance(gold, dict):
                    continue
                gold_file, _ = _relative_file(gold.get("file"))
                if file_name == gold_file and _line_overlap(actual.get("line"), gold.get("line")):
                    matched_gold.add(gold_index)
                    matched_findings.add(actual_index)
                    break
        candidate_count += len(matched_gold)
        unmatched_total += len(findings) - len(matched_findings)
        case_rows.append({
            "case_id": case_id,
            "gold_finding_count": len(gold_findings),
            "location_candidate_count": len(matched_gold),
            "unmatched_finding_count": len(findings) - len(matched_findings),
        })
    return ({
        "completed_case_count": len(provider_cases),
        "gold_finding_count": gold_total,
        "gold_location_candidate_count": candidate_count,
        "gold_location_candidate_rate": candidate_count / gold_total if gold_total else 0.0,
        "provider_finding_count": finding_total,
        "unmatched_finding_count": unmatched_total,
        "structural_evidence_complete_count": evidence_complete,
        "relative_file_paths": relative_paths,
    }, case_rows)


def build_screening_report(
    gold: dict[str, Any],
    codex: dict[str, Any],
    omc: dict[str, Any],
    *,
    input_provenance: dict[str, dict[str, str]] | None = None,
) -> dict[str, Any]:
    """Build reproducible candidate metrics without making semantic claims."""
    if gold.get("status") != "signed_off":
        raise ValueError("gold labels must be signed_off")
    if codex.get("status") != "completed_native_provider_runs_pending_adjudication":
        raise ValueError("Codex batch is incomplete")
    if codex.get("clean_baseline") is not True:
        raise ValueError("Codex batch must use a clean baseline")
    if omc.get("status") != "completed_omc_runs_pending_adjudication":
        raise ValueError("OMC batch is incomplete")
    gold_cases = _cases(gold)
    codex_cases = _cases(codex, "codex")
    omc_cases = _cases(omc, "omc-review")
    expected_ids = set(gold_cases)
    if set(codex_cases) != expected_ids or set(omc_cases) != expected_ids:
        raise ValueError("case set mismatch")
    for case_id, gold_case in gold_cases.items():
        expected_hash = gold_case.get("diff_sha256")
        if codex_cases[case_id].get("diff_sha256") != expected_hash or omc_cases[case_id].get("diff_sha256") != expected_hash:
            raise ValueError(f"diff hash mismatch: {case_id}")

    codex_summary, codex_rows = _provider_summary("codex", codex_cases, gold_cases)
    omc_summary, omc_rows = _provider_summary("omc-review", omc_cases, gold_cases)
    by_case = {
        row["case_id"]: {"codex": row}
        for row in codex_rows
    }
    for row in omc_rows:
        by_case[row["case_id"]]["omc-review"] = row
    report = {
        "status": "screening_only_pending_blind_adjudication",
        "recorded_at": _timestamp(),
        "scope": "same_diff_clean_codex_baseline_vs_current_omc",
        "case_count": len(gold_cases),
        "warning": (
            "Location overlap is a candidate signal only. It is not a semantic hit, false-positive, "
            "evidence-accuracy, or provider-replacement verdict."
        ),
        "providers": {"codex": codex_summary, "omc-review": omc_summary},
        "cases": [
            {"case_id": case_id, **by_case[case_id]}
            for case_id in sorted(by_case)
        ],
    }
    if input_provenance is not None:
        report["input_provenance"] = input_provenance
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--gold", required=True, type=Path)
    parser.add_argument("--codex", required=True, type=Path)
    parser.add_argument("--omc", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    report = build_screening_report(
        _load(args.gold),
        _load(args.codex),
        _load(args.omc),
        input_provenance=build_input_provenance({
            "gold": args.gold,
            "codex": args.codex,
            "omc-review": args.omc,
        }),
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"screening report written: {args.output} ({report['case_count']} cases)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
