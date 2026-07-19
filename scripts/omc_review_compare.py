"""Provider-neutral comparison contract for anonymized review fixtures."""
from __future__ import annotations

import json
import re
import math
from datetime import datetime
from pathlib import Path
from typing import Any


PROVIDER_STATUSES = {"completed", "not_run", "failed"}
SOURCE_TYPES = {"synthetic", "observed_output", "current_contract_sample"}
COMPARISON_SOURCE_TYPE = "comparison_sample"
COMPARISON_EXECUTION_MODES = {"cli_completed", "manual_rule_application"}
COMPARISON_PROVIDER_MODES = {
    "codex": "cli_completed",
    "omc-review": "manual_rule_application",
}
SEVERITY_MAP = {
    "P0": "치명",
    "P1": "중대",
    "P2": "경미",
    "P3": "제안",
    "치명": "치명",
    "중대": "중대",
    "경미": "경미",
    "제안": "제안",
}
PROVIDER_METRICS = ("duration_ms", "input_tokens", "output_tokens", "cost_usd")
PROVIDER_METRIC_SET = set(PROVIDER_METRICS)
ADJUDICATION_STATUSES = {"pending_adjudication", "confirmed", "rejected"}
SENSITIVE_VALUE_PATTERNS = (
    re.compile(r"\bghp_[A-Za-z0-9_]+\b"),
    re.compile(r"\bgithub_pat_[A-Za-z0-9_]+\b"),
    re.compile(r"\bAKIA[0-9A-Z]{8,}\b"),
    re.compile(r"\bBearer\s+\S+", re.IGNORECASE),
    re.compile(r"\b[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}\b"),
)


def _validate_anonymized_value(value: str, label: str) -> None:
    normalized = value.replace("\\", "/")
    if normalized.startswith("/") or ":/" in normalized or ".." in normalized.split("/"):
        raise ValueError(f"non-anonymized value for {label}: {value}")
    if any(pattern.search(value) for pattern in SENSITIVE_VALUE_PATTERNS):
        raise ValueError(f"sensitive value for {label}")


def _validate_anonymized_diff(diff: str) -> None:
    if not isinstance(diff, str) or not diff.strip():
        raise ValueError("diff requires non-empty text")
    for line in diff.splitlines():
        if line.startswith(("--- ", "+++ ")):
            path = line[4:].strip()
            _validate_anonymized_value(path, "diff path")
    if any(pattern.search(diff) for pattern in SENSITIVE_VALUE_PATTERNS):
        raise ValueError("sensitive value for diff")


def _finding_ids(findings: list[dict[str, Any]]) -> set[str]:
    return {str(finding.get("id") or "").strip() for finding in findings}


def map_review_severity(value: str, provider: str) -> str:
    """Normalize provider severity labels without inventing an unknown mapping."""
    del provider
    return SEVERITY_MAP.get(str(value or "").strip(), "unmapped")


def _normalize_comparison_findings(
    findings: list[dict[str, Any]], provider: str
) -> list[dict[str, Any]]:
    return [
        {**finding, "severity": map_review_severity(finding.get("severity"), provider)}
        for finding in findings
    ]


def _evidence_match_count(expected: list[dict[str, Any]], actual: list[dict[str, Any]]) -> int:
    actual_by_id = {str(finding.get("id") or "").strip(): finding for finding in actual}
    return sum(
        1
        for finding in expected
        if all(str(finding.get(field) or "").strip() for field in ("severity", "file", "line"))
        and all(
            str(finding.get(field) or "").strip()
            == str(actual_by_id.get(str(finding.get("id") or "").strip(), {}).get(field) or "").strip()
            for field in ("severity", "file", "line")
        )
    )


def _evidence_is_complete(findings: list[dict[str, Any]]) -> bool:
    if not findings:
        return False
    return all(
        all(str(finding.get(field) or "").strip() for field in ("severity", "file", "line"))
        for finding in findings
    )


def normalize_case(case: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(case, dict):
        raise ValueError("review comparison case must be an object")
    case_id = str(case.get("case_id") or "").strip()
    diff_id = str(case.get("diff_id") or "").strip()
    source_type = str(case.get("source_type") or "synthetic").strip()
    _validate_anonymized_value(case_id, "case_id")
    _validate_anonymized_value(diff_id, "diff_id")
    if source_type not in SOURCE_TYPES:
        raise ValueError(f"unsupported source type: {source_type}")
    diff = case.get("diff")
    if diff is not None:
        _validate_anonymized_diff(diff)
    if source_type == "observed_output" and diff is None:
        raise ValueError("observed output requires anonymized diff")
    expected = case.get("expected_findings")
    providers = case.get("providers")
    if not case_id or not diff_id or not isinstance(expected, list) or not isinstance(providers, dict):
        raise ValueError("case requires case_id, diff_id, expected_findings, and providers")
    if source_type == "observed_output" and not {"codex", "omc-review"}.issubset(providers):
        raise ValueError("observed output requires providers: codex and omc-review")
    expected_ids: set[str] = set()
    for finding in expected:
        if not isinstance(finding, dict) or not str(finding.get("id") or "").strip():
            raise ValueError("expected finding requires id")
        finding_id = str(finding["id"]).strip()
        if finding.get("file") is not None:
            _validate_anonymized_value(str(finding["file"]).strip(), "expected finding file")
        if finding_id in expected_ids:
            raise ValueError(f"duplicate expected finding id: {finding_id}")
        expected_ids.add(finding_id)

    normalized_providers: dict[str, dict[str, Any]] = {}
    for provider, result in providers.items():
        provider_name = str(provider).strip()
        if not provider_name:
            raise ValueError("provider name requires value")
        if not isinstance(result, dict):
            raise ValueError("provider result must be an object")
        status = str(result.get("status") or "").strip()
        if status not in PROVIDER_STATUSES:
            raise ValueError(f"unsupported provider status: {status}")
        findings = result.get("findings", [])
        if not isinstance(findings, list) or any(not isinstance(item, dict) for item in findings):
            raise ValueError("provider findings must be a list of objects")
        metrics = result.get("metrics", {})
        if not isinstance(metrics, dict):
            raise ValueError("provider metrics must be an object")
        unknown_metrics = set(metrics) - PROVIDER_METRIC_SET
        if unknown_metrics:
            raise ValueError(f"unsupported provider metric: {sorted(unknown_metrics)[0]}")
        for metric, value in metrics.items():
            if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or value < 0:
                raise ValueError(f"provider metric requires non-negative number: {metric}")
        finding_ids: set[str] = set()
        for finding in findings:
            finding_id = str(finding.get("id") or "").strip()
            if not finding_id:
                raise ValueError("provider finding requires id")
            if finding_id in finding_ids:
                raise ValueError(f"duplicate provider finding id: {finding_id}")
            finding_ids.add(finding_id)
            if finding.get("file") is not None:
                _validate_anonymized_value(str(finding["file"]).strip(), "provider finding file")
        if provider_name in normalized_providers:
            raise ValueError(f"duplicate provider name: {provider_name}")
        input_case_id = str(result.get("case_id") or "").strip()
        input_diff_id = str(result.get("diff_id") or "").strip()
        if source_type == "observed_output" and (input_case_id != case_id or input_diff_id != diff_id):
            raise ValueError(f"provider input identity mismatch: {provider_name}")
        normalized_result = {"status": status, "findings": findings, "metrics": metrics}
        if input_case_id:
            normalized_result["case_id"] = input_case_id
        if input_diff_id:
            normalized_result["diff_id"] = input_diff_id
        normalized_providers[provider_name] = normalized_result
    normalized_case = {
        "case_id": case_id,
        "diff_id": diff_id,
        "source_type": source_type,
        "expected_findings": expected,
        "providers": normalized_providers,
    }
    if diff is not None:
        normalized_case["diff"] = diff
    return normalized_case


def normalize_comparison_sample(sample: dict[str, Any]) -> dict[str, Any]:
    """Normalize a provenance-aware sample without making it an operational case."""
    if not isinstance(sample, dict):
        raise ValueError("comparison sample must be an object")
    sample_id = str(sample.get("sample_id") or "").strip()
    case_id = str(sample.get("case_id") or "").strip()
    diff_id = str(sample.get("diff_id") or "").strip()
    if not sample_id or not case_id or not diff_id:
        raise ValueError("comparison sample requires sample_id, case_id, and diff_id")
    for value, label in ((sample_id, "sample_id"), (case_id, "case_id"), (diff_id, "diff_id")):
        _validate_anonymized_value(value, label)
    if sample.get("source_type") != COMPARISON_SOURCE_TYPE:
        raise ValueError("comparison sample requires source_type: comparison_sample")
    source_kind = str(sample.get("source_kind") or "").strip()
    evidence_ref = str(sample.get("evidence_ref") or "").strip()
    selection_reason = str(sample.get("selection_reason") or "").strip()
    if not source_kind:
        raise ValueError("comparison sample requires source_kind")
    if not evidence_ref:
        raise ValueError("comparison sample requires evidence_ref")
    if not selection_reason:
        raise ValueError("comparison sample requires selection_reason")
    for value, label in ((source_kind, "source_kind"), (evidence_ref, "evidence_ref"), (selection_reason, "selection_reason")):
        _validate_anonymized_value(value, label)
    recorded_at = str(sample.get("recorded_at") or "").strip()
    if not recorded_at:
        raise ValueError("comparison sample requires recorded_at")
    try:
        parsed_recorded_at = datetime.fromisoformat(recorded_at)
    except ValueError as exc:
        raise ValueError("recorded_at requires ISO-8601") from exc
    if "T" not in recorded_at or parsed_recorded_at.tzinfo is None:
        raise ValueError("recorded_at requires ISO-8601")
    diff = sample.get("diff")
    _validate_anonymized_diff(diff)
    results = sample.get("results")
    if not isinstance(results, dict) or not {"codex", "omc-review"}.issubset(results):
        raise ValueError("comparison sample requires results: codex and omc-review")

    normalized_results: dict[str, dict[str, Any]] = {}
    for provider, result in results.items():
        if not isinstance(result, dict):
            raise ValueError("comparison result must be an object")
        execution_mode = str(result.get("execution_mode") or "").strip()
        if execution_mode not in COMPARISON_EXECUTION_MODES:
            raise ValueError(f"unsupported or missing execution_mode: {provider}")
        expected_mode = COMPARISON_PROVIDER_MODES.get(provider)
        if expected_mode and execution_mode != expected_mode:
            raise ValueError(f"comparison execution mode mismatch: {provider}")
        status = str(result.get("status") or "").strip()
        if status not in PROVIDER_STATUSES:
            raise ValueError(f"unsupported comparison status: {status}")
        result_case_id = str(result.get("case_id") or "").strip()
        result_diff_id = str(result.get("diff_id") or "").strip()
        prompt_id = str(result.get("prompt_id") or "").strip()
        if result_case_id != case_id or result_diff_id != diff_id:
            raise ValueError(f"comparison input identity mismatch: {provider}")
        if not prompt_id:
            raise ValueError(f"comparison prompt_id requires value: {provider}")
        _validate_anonymized_value(prompt_id, "prompt_id")
        findings = result.get("findings", [])
        if not isinstance(findings, list) or any(not isinstance(item, dict) for item in findings):
            raise ValueError("comparison findings must be a list of objects")
        finding_ids: set[str] = set()
        for finding in findings:
            finding_id = str(finding.get("id") or "").strip()
            if not finding_id:
                raise ValueError("comparison finding requires id")
            if finding_id in finding_ids:
                raise ValueError(f"duplicate comparison finding id: {finding_id}")
            finding_ids.add(finding_id)
            if finding.get("file") is not None:
                _validate_anonymized_value(str(finding["file"]).strip(), "comparison finding file")
        metrics = result.get("metrics", {})
        if not isinstance(metrics, dict):
            raise ValueError("comparison metrics must be an object")
        unknown_metrics = set(metrics) - PROVIDER_METRIC_SET
        if unknown_metrics:
            raise ValueError(f"unsupported comparison metric: {sorted(unknown_metrics)[0]}")
        normalized_results[provider] = {
            "case_id": result_case_id,
            "diff_id": result_diff_id,
            "prompt_id": prompt_id,
            "execution_mode": execution_mode,
            "status": status,
            "runner": str(result.get("runner") or "").strip(),
            "model": result.get("model"),
            "findings": findings,
            "metrics": metrics,
        }
        for output_field in ("verdict", "next_action"):
            output_value = str(result.get(output_field) or "").strip()
            if output_value:
                normalized_results[provider][output_field] = output_value
    return {
        "sample_id": sample_id,
        "case_id": case_id,
        "diff_id": diff_id,
        "source_type": COMPARISON_SOURCE_TYPE,
        "source_kind": source_kind,
        "evidence_ref": evidence_ref,
        "selection_reason": selection_reason,
        "recorded_at": recorded_at,
        "diff": diff,
        "results": normalized_results,
    }


def build_comparison_report(samples: list[dict[str, Any]]) -> dict[str, Any]:
    """Report finding agreement for reference samples only.

    Comparison samples deliberately expose no provider score or cost totals used by
    ``build_report``/``build_verdict``.
    """
    normalized = [normalize_comparison_sample(sample) for sample in samples]
    matched = 0
    evidence_matched = 0
    codex_only = 0
    omc_only = 0
    for sample in normalized:
        codex = _normalize_comparison_findings(sample["results"]["codex"]["findings"], "codex")
        omc = _normalize_comparison_findings(sample["results"]["omc-review"]["findings"], "omc-review")
        codex_ids = _finding_ids(codex)
        omc_ids = _finding_ids(omc)
        matched += len(codex_ids & omc_ids)
        codex_only += len(codex_ids - omc_ids)
        omc_only += len(omc_ids - codex_ids)
        omc_by_id = {str(finding.get("id") or "").strip(): finding for finding in omc}
        evidence_matched += sum(
            1
            for finding in codex
            if _finding_evidence_key(finding)
            == _finding_evidence_key(omc_by_id.get(str(finding.get("id") or "").strip(), {}))
        )
    return {
        "sample_count": len(normalized),
        "operational_case_count": 0,
        "id_match_count": matched,
        "finding_match_count": evidence_matched,
        "evidence_match_count": evidence_matched,
        "codex_only_count": codex_only,
        "omc_only_count": omc_only,
    }


def build_consistency_verdict(samples: list[dict[str, Any]], min_samples: int = 5) -> dict[str, Any]:
    """Return a non-superiority verdict for provenance-aware comparison samples."""
    normalized = [normalize_comparison_sample(sample) for sample in samples]
    sample_ids = [sample["sample_id"] for sample in normalized]
    if len(sample_ids) != len(set(sample_ids)):
        raise ValueError("duplicate comparison sample id")
    base = {
        "sample_count": len(normalized),
        "minimum_sample_count": min_samples,
        "interpretation": "consistency_probe_only",
    }
    if len(normalized) < min_samples:
        return {"verdict": "insufficient_sample", **base}
    report = build_comparison_report(normalized)
    return {
        "verdict": "consistency_only",
        **base,
        "evidence_match_count": report["evidence_match_count"],
        "id_match_count": report["id_match_count"],
    }


def _output_contract_metrics(result: dict[str, Any]) -> dict[str, Any]:
    """Measure observable review-output shape without judging review quality."""
    findings = result["findings"]
    evidence_complete = [
        finding
        for finding in findings
        if all(str(finding.get(field) or "").strip() for field in ("severity", "file", "line"))
    ]
    return {
        "status": result["status"],
        "verdict_presence": "compliant" if result.get("verdict") else "unknown",
        "next_action_presence": "compliant" if result.get("next_action") else "unknown",
        "file_line_evidence_rate": (
            1.0 if not findings else len(evidence_complete) / len(findings)
        ),
    }


def build_pilot_report(
    samples: list[dict[str, Any]], min_samples: int = 5, batch_id: str | None = None
) -> dict[str, Any]:
    """Return policy-pilot telemetry, never a provider superiority decision.

    The pilot has no reference adjudication input, so quality remains unknown even
    when both providers produce matching findings. This is intentionally separate
    from the operational KPI report and verdict.
    """
    normalized = [normalize_comparison_sample(sample) for sample in samples]
    if batch_id is not None:
        _validate_anonymized_value(batch_id, "batch_id")
    sample_ids = [sample["sample_id"] for sample in normalized]
    if len(sample_ids) != len(set(sample_ids)):
        raise ValueError("duplicate comparison sample id")
    comparison = build_comparison_report(normalized)
    output_contract: dict[str, dict[str, Any]] = {}
    severity_mapping: dict[str, dict[str, int]] = {}
    for provider in ("codex", "omc-review"):
        results = [sample["results"][provider] for sample in normalized]
        provider_metrics = [_output_contract_metrics(result) for result in results]
        severities = [
            map_review_severity(finding.get("severity"), provider)
            for result in results
            for finding in result["findings"]
        ]
        severity_mapping[provider] = {
            "mapped_count": sum(severity != "unmapped" for severity in severities),
            "unmapped_count": sum(severity == "unmapped" for severity in severities),
        }
        output_contract[provider] = {
            "verdict_presence": (
                "compliant" if provider_metrics and all(item["verdict_presence"] == "compliant" for item in provider_metrics)
                else "unknown"
            ),
            "next_action_presence": (
                "compliant" if provider_metrics and all(item["next_action_presence"] == "compliant" for item in provider_metrics)
                else "unknown"
            ),
            "file_line_evidence_rate": (
                sum(item["file_line_evidence_rate"] for item in provider_metrics) / len(provider_metrics)
                if provider_metrics else None
            ),
        }
    report = {
        "verdict": "insufficient_sample" if len(normalized) < min_samples else "pilot_only",
        "interpretation": "pilot_only",
        "quality_verdict": "unknown",
        "sample_count": len(normalized),
        "minimum_sample_count": min_samples,
        "comparison": comparison,
        "output_contract": output_contract,
        "severity_mapping": severity_mapping,
    }
    if batch_id is not None:
        report["batch_id"] = batch_id
    return report


def load_comparison_samples(path: str | Path) -> list[dict[str, Any]]:
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(payload, list):
        raise ValueError("comparison sample fixture must contain a list")
    normalized = [normalize_comparison_sample(sample) for sample in payload]
    sample_ids = [sample["sample_id"] for sample in normalized]
    if len(sample_ids) != len(set(sample_ids)):
        raise ValueError("duplicate comparison sample id")
    return normalized


def summarize_provider(case: dict[str, Any], provider: str) -> dict[str, Any]:
    normalized = normalize_case(case)
    result = normalized["providers"].get(provider)
    if result is None:
        raise ValueError(f"provider not found: {provider}")
    if result["status"] != "completed":
        return {
            "provider": provider,
            "status": result["status"],
            "expected_count": len(_finding_ids(normalized["expected_findings"])),
            "hit_count": None,
            "miss_count": None,
            "false_positive_count": None,
            "evidence_match_count": None,
            "evidence_complete": False,
        }

    expected_ids = _finding_ids(normalized["expected_findings"])
    actual_ids = _finding_ids(result["findings"])
    return {
        "provider": provider,
        "status": result["status"],
        "expected_count": len(expected_ids),
        "hit_count": len(expected_ids & actual_ids),
        "miss_count": len(expected_ids - actual_ids),
        "false_positive_count": len(actual_ids - expected_ids),
        "evidence_match_count": _evidence_match_count(
            normalized["expected_findings"], result["findings"]
        ),
        "evidence_complete": _evidence_is_complete(result["findings"]),
    }


def _finding_evidence_key(finding: dict[str, Any]) -> tuple[str, str, str]:
    return tuple(str(finding.get(field) or "").strip() for field in ("severity", "file", "line"))


def _line_distance(left: dict[str, Any], right: dict[str, Any]) -> int | None:
    try:
        return abs(int(str(left.get("line") or "").strip()) - int(str(right.get("line") or "").strip()))
    except (TypeError, ValueError):
        return None


def build_finding_comparison(
    case: dict[str, Any], codex_provider: str = "codex", omc_provider: str = "omc-review"
) -> dict[str, list[dict[str, Any]]]:
    normalized = normalize_case(case)
    codex_result = normalized["providers"].get(codex_provider)
    omc_result = normalized["providers"].get(omc_provider)
    if not codex_result or not omc_result or codex_result["status"] != "completed" or omc_result["status"] != "completed":
        raise ValueError("finding comparison requires completed codex and omc-review results")

    codex_findings = list(codex_result["findings"])
    omc_findings = list(omc_result["findings"])
    shared: list[dict[str, Any]] = []
    unmatched: list[dict[str, Any]] = []
    used_omc: set[int] = set()

    for codex_finding in codex_findings:
        codex_id = str(codex_finding.get("id") or "").strip()
        match_index = next(
            (index for index, finding in enumerate(omc_findings) if index not in used_omc and str(finding.get("id") or "").strip() == codex_id),
            None,
        )
        if match_index is not None:
            used_omc.add(match_index)
            omc_finding = omc_findings[match_index]
            evidence_match = _finding_evidence_key(codex_finding) == _finding_evidence_key(omc_finding)
            shared.append(
                {
                    "codex": codex_finding,
                    "omc-review": omc_finding,
                    "match_type": "id" if evidence_match else "id_evidence_mismatch",
                    "evidence_match": evidence_match,
                }
            )

    for codex_finding in codex_findings:
        if any(item["codex"] is codex_finding for item in shared):
            continue
        match_index = next(
            (
                index
                for index, finding in enumerate(omc_findings)
                if index not in used_omc and _finding_evidence_key(finding) == _finding_evidence_key(codex_finding)
            ),
            None,
        )
        if match_index is not None:
            used_omc.add(match_index)
            shared.append(
                {
                    "codex": codex_finding,
                    "omc-review": omc_findings[match_index],
                    "match_type": "evidence",
                    "evidence_match": True,
                }
            )

    remaining_codex = [finding for finding in codex_findings if not any(item["codex"] is finding for item in shared)]
    remaining_omc = [finding for index, finding in enumerate(omc_findings) if index not in used_omc]
    for codex_finding in list(remaining_codex):
        nearby = []
        for finding in remaining_omc:
            distance = _line_distance(codex_finding, finding)
            if finding.get("severity") == codex_finding.get("severity") and finding.get("file") == codex_finding.get("file") and distance is not None and distance <= 2:
                nearby.append(finding)
        if len(nearby) == 1:
            omc_finding = nearby[0]
            shared.append(
                {
                    "codex": codex_finding,
                    "omc-review": omc_finding,
                    "match_type": "evidence_proximity",
                    "evidence_match": False,
                }
            )
            remaining_codex.remove(codex_finding)
            remaining_omc.remove(omc_finding)

    for codex_finding in list(remaining_codex):
        same_location = [finding for finding in remaining_omc if finding.get("severity") == codex_finding.get("severity") and finding.get("file") == codex_finding.get("file")]
        if len(same_location) == 1:
            omc_finding = same_location[0]
            unmatched.append({"codex": codex_finding, "omc-review": omc_finding, "match_type": "uncertain"})
            remaining_codex.remove(codex_finding)
            remaining_omc.remove(omc_finding)

    return {
        "shared": shared,
        "codex_only": remaining_codex,
        "omc_only": remaining_omc,
        "unmatched": unmatched,
    }


def build_fixture_candidates(comparison: dict[str, list[dict[str, Any]]]) -> list[dict[str, Any]]:
    candidates = []
    for provider in ("codex", "omc-review"):
        for finding in comparison.get(f"{provider}_only", []):
            candidates.append(
                {
                    "provider": provider,
                    "adjudication_status": "pending_adjudication",
                    "finding": finding,
                    "reason": "provider_unique_finding",
                }
            )
    for pair in comparison.get("unmatched", []):
        candidates.append(
            {
                "adjudication_status": "pending_adjudication",
                "match_type": "uncertain",
                "findings": {"codex": pair["codex"], "omc-review": pair["omc-review"]},
                "selected_finding": None,
                "reason": "uncertain_provider_match",
            }
        )
    return candidates


def promote_fixture_candidate(candidate: dict[str, Any]) -> dict[str, Any]:
    if candidate.get("adjudication_status") != "confirmed":
        raise ValueError("fixture candidate must be confirmed before promotion")
    finding = candidate.get("selected_finding") if candidate.get("match_type") == "uncertain" else candidate.get("finding")
    if not isinstance(finding, dict) or not all(str(finding.get(field) or "").strip() for field in ("id", "severity", "file", "line")):
        raise ValueError("confirmed fixture candidate requires complete finding evidence")
    if candidate.get("match_type") == "uncertain":
        original_findings = candidate.get("findings", {})
        if not any(finding == original_findings.get(provider) for provider in ("codex", "omc-review")):
            raise ValueError("selected finding must come from original candidate")
    return {field: str(finding[field]).strip() for field in ("id", "severity", "file", "line")}


def build_case_report(case: dict[str, Any]) -> list[dict[str, Any]]:
    normalized = normalize_case(case)
    expected_ids = sorted(_finding_ids(normalized["expected_findings"]))
    rows: list[dict[str, Any]] = []
    for provider in sorted(normalized["providers"]):
        result = normalized["providers"][provider]
        actual_ids = _finding_ids(result["findings"])
        completed = result["status"] == "completed"
        rows.append(
            {
                "case_id": normalized["case_id"],
                "diff_id": normalized["diff_id"],
                "source_type": normalized["source_type"],
                "provider": provider,
                "status": result["status"],
                "expected_ids": expected_ids,
                "hit_ids": sorted(set(expected_ids) & actual_ids if completed else []),
                "miss_ids": sorted(set(expected_ids) - actual_ids if completed else []),
                "false_positive_ids": sorted(actual_ids - set(expected_ids) if completed else []),
                "evidence_complete": _evidence_is_complete(result["findings"]) if completed else False,
            }
        )
    return rows


def export_review_pack(cases: list[dict[str, Any]], path: str | Path) -> None:
    normalized_cases = [normalize_case(case) for case in cases]
    pack = []
    for case in normalized_cases:
        entry = {
            "case_id": case["case_id"],
            "diff_id": case["diff_id"],
            "source_type": case["source_type"],
            "expected_findings": [
                {key: finding[key] for key in ("id", "severity", "file", "line") if key in finding}
                for finding in case["expected_findings"]
            ],
        }
        if case.get("diff") is not None:
            entry["diff"] = case["diff"]
        pack.append(entry)
    Path(path).write_text(json.dumps(pack, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def load_cases(path: str | Path) -> list[dict[str, Any]]:
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(payload, list):
        raise ValueError("review comparison fixture must be a list")
    return [normalize_case(case) for case in payload]


def build_report(cases: list[dict[str, Any]]) -> dict[str, Any]:
    normalized_cases = [normalize_case(case) for case in cases]
    provider_names = sorted({provider for case in normalized_cases for provider in case["providers"]})
    providers: dict[str, dict[str, Any]] = {}
    for provider in provider_names:
        rows = [summarize_provider(case, provider) for case in normalized_cases if provider in case["providers"]]
        completed = [row for row in rows if row["status"] == "completed"]
        completed_metrics = [
            normalized_case["providers"][provider].get("metrics", {})
            for normalized_case in normalized_cases
            if provider in normalized_case["providers"]
            and normalized_case["providers"][provider]["status"] == "completed"
        ]
        providers[provider] = {
            "completed_count": len(completed),
            "missing_count": len(normalized_cases) - len(rows),
            "not_run_count": sum(1 for row in rows if row["status"] == "not_run"),
            "failed_count": sum(1 for row in rows if row["status"] == "failed"),
            "scored_count": len(completed),
            "hit_count": sum(int(row["hit_count"] or 0) for row in completed),
            "miss_count": sum(int(row["miss_count"] or 0) for row in completed),
            "false_positive_count": sum(int(row["false_positive_count"] or 0) for row in completed),
            "evidence_match_count": sum(int(row["evidence_match_count"] or 0) for row in completed),
            "evidence_complete_count": sum(1 for row in completed if row["evidence_complete"]),
            "metrics": {
                metric: sum(float(metrics.get(metric, 0)) for metrics in completed_metrics)
                for metric in PROVIDER_METRICS
            },
        }
    return {"case_count": len(normalized_cases), "providers": providers}


def build_verdict(
    cases: list[dict[str, Any]],
    min_cases: int = 5,
    max_cost_ratio: float = 1.0,
    max_duration_ratio: float = 1.25,
) -> dict[str, Any]:
    normalized_cases = [normalize_case(case) for case in cases]
    report = build_report(normalized_cases)
    if len(normalized_cases) < min_cases:
        return {"verdict": "insufficient_evidence", "case_count": len(normalized_cases), "reason": "minimum observed cases not met"}
    if any(case["source_type"] != "observed_output" for case in normalized_cases):
        return {"verdict": "insufficient_evidence", "case_count": len(normalized_cases), "reason": "observed_output cases required"}

    required_providers = {"codex", "omc-review"}
    if set(report["providers"]) != required_providers:
        return {"verdict": "insufficient_evidence", "case_count": len(normalized_cases), "reason": "codex and omc-review results required"}
    for provider in required_providers:
        summary = report["providers"][provider]
        if summary["completed_count"] != len(normalized_cases) or summary["not_run_count"] or summary["failed_count"]:
            return {"verdict": "insufficient_evidence", "case_count": len(normalized_cases), "reason": "all provider results must be completed"}
        for case in normalized_cases:
            metrics = case["providers"][provider].get("metrics", {})
            if any(metric not in metrics for metric in PROVIDER_METRICS):
                reason = "cost metrics required" if "cost_usd" not in metrics else "execution metrics required"
                return {"verdict": "insufficient_evidence", "case_count": len(normalized_cases), "reason": reason}

    scores: dict[str, dict[str, float]] = {}
    for provider, summary in report["providers"].items():
        expected_total = summary["hit_count"] + summary["miss_count"]
        scores[provider] = {
            "recall": summary["hit_count"] / expected_total if expected_total else 0.0,
            "precision": summary["hit_count"] / (summary["hit_count"] + summary["false_positive_count"])
            if summary["hit_count"] + summary["false_positive_count"]
            else 0.0,
            "evidence_match_rate": summary["evidence_match_count"] / expected_total if expected_total else 0.0,
        }

    omc = scores["omc-review"]
    codex = scores["codex"]
    metrics = {provider: report["providers"][provider]["metrics"] for provider in required_providers}
    omc_quality_superior = (
        omc["recall"] >= codex["recall"] + 0.10
        and omc["evidence_match_rate"] >= codex["evidence_match_rate"] + 0.10
        and omc["precision"] >= codex["precision"]
    )
    codex_quality_superior = (
        codex["recall"] >= omc["recall"] + 0.10
        and codex["evidence_match_rate"] >= omc["evidence_match_rate"] + 0.10
        and codex["precision"] >= omc["precision"]
    )
    omc_within_limits = (
        metrics["omc-review"]["cost_usd"] <= metrics["codex"]["cost_usd"] * max_cost_ratio
        and metrics["omc-review"]["duration_ms"] <= metrics["codex"]["duration_ms"] * max_duration_ratio
    )
    codex_within_limits = (
        metrics["codex"]["cost_usd"] <= metrics["omc-review"]["cost_usd"] * max_cost_ratio
        and metrics["codex"]["duration_ms"] <= metrics["omc-review"]["duration_ms"] * max_duration_ratio
    )
    if omc == codex:
        verdict = "tie"
    elif omc_quality_superior and omc_within_limits:
        verdict = "omc-superior"
    elif codex_quality_superior and codex_within_limits:
        verdict = "codex-superior"
    else:
        verdict = "tie"
    return {
        "verdict": verdict,
        "case_count": len(normalized_cases),
        "recall": {provider: score["recall"] for provider, score in scores.items()},
        "precision": {provider: score["precision"] for provider, score in scores.items()},
        "evidence_match_rate": {provider: score["evidence_match_rate"] for provider, score in scores.items()},
        "metrics": {
            metric: {provider: metrics[provider][metric] for provider in sorted(required_providers)}
            for metric in PROVIDER_METRICS
        },
    }


def format_report_table(report: dict[str, Any]) -> str:
    header = (
        "provider | completed | missing | not_run | failed | scored | hit | miss | "
        "false_positive | evidence_match | evidence_complete"
    )
    rows = [header]
    for provider, summary in report["providers"].items():
        rows.append(
            f"{provider} | {summary['completed_count']} | {summary['missing_count']} | "
            f"{summary['not_run_count']} | {summary['failed_count']} | {summary['scored_count']} | "
            f"{summary['hit_count']} | "
            f"{summary['miss_count']} | {summary['false_positive_count']} | "
            f"{summary['evidence_match_count']} | {summary['evidence_complete_count']}"
        )
    return "\n".join(rows)


def format_metrics_table(report: dict[str, Any]) -> str:
    header = "provider | duration_ms | input_tokens | output_tokens | cost_usd"
    rows = [header]
    for provider, summary in report["providers"].items():
        metrics = summary["metrics"]
        rows.append(
            f"{provider} | {metrics['duration_ms']:g} | {metrics['input_tokens']:g} | "
            f"{metrics['output_tokens']:g} | {metrics['cost_usd']:g}"
        )
    return "\n".join(rows)
