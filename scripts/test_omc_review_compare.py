from __future__ import annotations

import sys
import json
from copy import deepcopy
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))
from omc_review_compare import (
    build_case_report,
    build_report,
    build_verdict,
    export_review_pack,
    format_report_table,
    format_metrics_table,
    load_cases,
    normalize_case,
    normalize_comparison_sample,
    build_comparison_sample_from_envelopes,
    build_comparison_sample_id,
    build_comparison_report,
    build_consistency_verdict,
    build_pilot_report,
    map_review_severity,
    load_comparison_samples,
    summarize_provider,
    build_finding_comparison,
    build_fixture_candidates,
    promote_fixture_candidate,
    normalize_replacement_case,
    build_replacement_gate,
    resolve_observed_candidate_path,
    verify_observed_candidate_hashes,
)
from generate_omc_review_synthetic_report import render_report


FIXTURE_PATH = Path(__file__).resolve().parent / "fixtures" / "omc_review_compare_cases.json"
ADVERSARIAL_FIXTURE_PATH = Path(__file__).resolve().parent / "fixtures" / "omc_review_adversarial_cases.json"
SYNTHETIC_RUNTIME_FIXTURE_PATH = Path(__file__).resolve().parent / "fixtures" / "omc_review_synthetic_runtime_cases.json"
SYNTHETIC_OUTPUTS_PATH = Path(__file__).resolve().parent / "fixtures" / "omc_review_synthetic_runtime_outputs.json"
SYNTHETIC_REPORT_PATH = Path(__file__).resolve().parents[1] / "docs" / "omc_review_synthetic_comparison.md"
OBSERVED_CANDIDATE_MANIFEST_PATH = Path(__file__).resolve().parent / "fixtures" / "omc_review_observed_candidate_manifest.json"
COMPARISON_FIXTURE_PATH = Path(__file__).resolve().parent / "fixtures" / "omc_review_comparison_samples.json"


def _replacement_case() -> dict[str, object]:
    return {
        "case_id": "replacement-01",
        "diff_id": "anon-diff-01",
        "gold_findings": [
            {
                "id": "null-guard",
                "category": "runtime-safety",
                "severity": "중대",
                "file": "src/service.py",
                "line": "12",
                "expected": True,
                "confidence": "confirmed",
            }
        ],
        "adjudication": {
            "status": "confirmed",
            "reviewer": "human-1",
            "decision_reason": "Independent reproduction confirmed the null path.",
            "recorded_at": "2026-07-20T10:00:00+09:00",
            "independence": {
                "provider_outputs_visible": False,
                "reviewer_count": 2,
                "agreement": "agreed",
                "tie_breaker_completed": False,
            },
        },
        "manifest": {
            "batch_id": "batch-01",
            "case_id": "replacement-01",
            "diff_hash": "anon-diff-hash-01",
            "model_identity": "same_model_prompt_comparison",
            "system_context_hash": "anon-system-hash-01",
            "project_rules_hash": "anon-rules-hash-01",
            "prompt_hash": "anon-prompt-hash-01",
            "execution_order": 1,
            "repeat_index": 1,
            "input_tokens": 100,
            "injected_context_tokens": 20,
            "output_tokens": 80,
        },
    }


def test_normalize_replacement_case_requires_adjudicated_gold_and_manifest():
    normalized = normalize_replacement_case(_replacement_case())

    assert normalized["gold_findings"][0]["confidence"] == "confirmed"
    assert normalized["adjudication"]["status"] == "confirmed"
    assert normalized["adjudication"]["independence"]["reviewer_count"] == 2
    assert normalized["manifest"]["case_id"] == normalized["case_id"]


def test_adversarial_review_fixture_covers_false_positive_false_negative_and_evidence_drift():
    cases = load_cases(ADVERSARIAL_FIXTURE_PATH)

    assert len(cases) == 4
    report = build_report(cases)

    assert report["case_count"] == 4
    assert report["providers"]["codex"]["false_positive_count"] == 2
    assert report["providers"]["codex"]["miss_count"] == 3
    assert report["providers"]["omc-review"]["false_positive_count"] == 0
    assert report["providers"]["omc-review"]["hit_count"] == 4
    assert report["providers"]["codex"]["evidence_match_count"] == 0
    assert report["providers"]["omc-review"]["evidence_match_count"] == 4


def test_adversarial_review_fixture_does_not_claim_superiority_without_observed_cases():
    cases = load_cases(ADVERSARIAL_FIXTURE_PATH)

    verdict = build_verdict(cases, min_cases=1)

    assert verdict["verdict"] == "insufficient_evidence"
    assert verdict["reason"] == "observed_output cases required"


def test_synthetic_runtime_fixture_contains_four_completed_provider_pairs():
    cases = load_cases(SYNTHETIC_RUNTIME_FIXTURE_PATH)

    assert len(cases) == 4
    assert {case["source_type"] for case in cases} == {"synthetic"}
    for case in cases:
        assert set(case["providers"]) == {"codex", "omc-review"}
        assert {result["status"] for result in case["providers"].values()} == {"completed"}

    report = build_report(cases)
    assert report["providers"]["codex"]["hit_count"] == 4
    assert report["providers"]["omc-review"]["hit_count"] == 4
    assert report["providers"]["codex"]["evidence_match_count"] == 4
    assert report["providers"]["omc-review"]["evidence_match_count"] == 4

    verdict = build_verdict(cases, min_cases=1)
    assert verdict["verdict"] == "insufficient_evidence"
    assert verdict["reason"] == "observed_output cases required"


def test_synthetic_runtime_outputs_keep_provider_provenance_and_report_metrics():
    payload = json.loads(SYNTHETIC_OUTPUTS_PATH.read_text(encoding="utf-8"))

    assert payload["source_type"] == "synthetic"
    assert payload["comparison_scope"] == "same_diff"
    assert len(payload["cases"]) == 8
    assert len({case["case_id"] for case in payload["cases"]}) == 4
    assert {case["provider"] for case in payload["cases"]} == {"codex", "omc-review"}
    assert {case["provenance"] for case in payload["cases"]} == {"cli_completed", "manual_rule_application"}
    assert all(case["raw_output"].strip() for case in payload["cases"])
    assert all(case.get("raw_output_complete") is True for case in payload["cases"])
    assert all(case.get("recorded_by") for case in payload["cases"] if case["provider"] == "omc-review")
    assert all(case.get("review_basis") == "omc-review-checklist" for case in payload["cases"] if case["provider"] == "omc-review")

    cases = load_cases(SYNTHETIC_RUNTIME_FIXTURE_PATH)
    report = build_report(cases)
    assert report["providers"]["codex"]["hit_count"] == 4
    assert report["providers"]["omc-review"]["hit_count"] == 4


def test_synthetic_comparison_report_declares_and_matches_fixture_metrics():
    report_text = SYNTHETIC_REPORT_PATH.read_text(encoding="utf-8")
    assert "Metrics source: `omc_review_synthetic_runtime_cases.json`" in report_text
    assert "| Codex | 4 | 4 | 0 | 0 | 4 |" in report_text
    assert "| OMC review | 4 | 4 | 0 | 0 | 4 |" in report_text


def test_synthetic_report_renderer_derives_rows_from_fixture():
    cases = load_cases(SYNTHETIC_RUNTIME_FIXTURE_PATH)
    rendered = render_report(cases)

    assert "| Codex | 4 | 4 | 0 | 0 | 4 |" in rendered
    assert "| OMC review | 4 | 4 | 0 | 0 | 4 |" in rendered
    assert "Metrics source: `omc_review_synthetic_runtime_cases.json`" in rendered
    assert "all four controlled cases" not in rendered
    assert "all 4 controlled cases" in rendered


def test_observed_candidate_manifest_contains_six_anonymized_pending_inputs():
    payload = json.loads(OBSERVED_CANDIDATE_MANIFEST_PATH.read_text(encoding="utf-8"))

    assert payload["source_type"] == "observed_output"
    assert payload["status"] == "ready_for_provider_runs"
    assert payload["input_root"] == "/private/tmp/omc-review-observed-candidates"
    assert len(payload["candidates"]) == 6
    assert len({candidate["case_id"] for candidate in payload["candidates"]}) == 6
    assert all(candidate["anonymized"] is True for candidate in payload["candidates"])
    assert all(candidate["anonymization_status"] == "passed" for candidate in payload["candidates"])
    assert all(candidate["gold_status"] == "pending" for candidate in payload["candidates"])
    assert all(candidate["provider_status"] == {"codex": "not_run", "omc-review": "not_run"} for candidate in payload["candidates"])


def test_observed_candidate_paths_resolve_from_configured_runtime_root():
    payload = json.loads(OBSERVED_CANDIDATE_MANIFEST_PATH.read_text(encoding="utf-8"))
    candidate = payload["candidates"][0]

    resolved = resolve_observed_candidate_path(payload, candidate, "/private/tmp/omc-review-observed-candidates")

    assert resolved.name == "health-repository-aware.diff"
    assert resolved.parent == Path("/private/tmp/omc-review-observed-candidates")


def test_observed_candidate_paths_preserve_nested_relative_structure():
    payload = {"input_root": "/private/tmp/omc-review-observed-candidates"}
    candidate = {"diff_path": "omc-review-observed-candidates/nested/health.diff"}

    resolved = resolve_observed_candidate_path(payload, candidate)

    assert resolved == Path("/private/tmp/omc-review-observed-candidates/nested/health.diff")


def test_observed_candidate_path_prefers_environment_root(monkeypatch):
    payload = {"input_root": "/wrong/root"}
    candidate = {"diff_path": "omc-review-observed-candidates/health.diff"}
    monkeypatch.setenv("OMC_REVIEW_OBSERVED_INPUT_ROOT", "/private/tmp/override-root")

    resolved = resolve_observed_candidate_path(payload, candidate)

    assert resolved.parent == Path("/private/tmp/override-root")


def test_observed_candidate_hashes_match_recorded_manifest():
    payload = json.loads(OBSERVED_CANDIDATE_MANIFEST_PATH.read_text(encoding="utf-8"))

    assert verify_observed_candidate_hashes(
        payload,
        "/private/tmp/omc-review-observed-candidates",
    ) == []


def test_observed_candidate_hash_verification_reports_manifest_mismatch():
    payload = json.loads(OBSERVED_CANDIDATE_MANIFEST_PATH.read_text(encoding="utf-8"))
    payload["candidates"][0]["diff_sha256"] = "0" * 64

    assert verify_observed_candidate_hashes(
        payload,
        "/private/tmp/omc-review-observed-candidates",
    ) == ["observed-health-repository-aware"]


def test_normalize_replacement_case_supports_gold_absence_for_false_positive_scoring():
    case = _replacement_case()
    case["gold_findings"][0]["expected"] = False

    normalized = normalize_replacement_case(case)

    assert normalized["gold_findings"][0]["expected"] is False


def test_normalize_replacement_case_accepts_public_pending_adjudication_name():
    case = _replacement_case()
    case["adjudication"]["status"] = "pending"

    normalized = normalize_replacement_case(case)

    assert normalized["adjudication"]["status"] == "pending"


def test_normalize_replacement_case_rejects_visible_provider_outputs():
    case = _replacement_case()
    case["adjudication"]["independence"]["provider_outputs_visible"] = True

    with pytest.raises(ValueError, match="provider outputs must be hidden"):
        normalize_replacement_case(case)


def test_normalize_replacement_case_requires_tie_breaker_for_disagreement():
    case = _replacement_case()
    case["adjudication"]["independence"]["agreement"] = "disputed"

    with pytest.raises(ValueError, match="tie-breaker"):
        normalize_replacement_case(case)


def test_replacement_gate_blocks_pending_adjudication_and_missing_repeats():
    case = _replacement_case()
    case["adjudication"]["status"] = "pending_adjudication"

    report = build_replacement_gate([case], min_cases=1, min_repeats=2)

    assert report["verdict"] == "insufficient_evidence"
    assert "pending adjudication" in report["reasons"]
    assert "repeat coverage" in report["reasons"]


def test_replacement_gate_does_not_call_single_confirmed_case_ready():
    report = build_replacement_gate([_replacement_case()], min_cases=1, min_repeats=1)

    assert report["verdict"] == "replacement_not_ready"
    assert "final replacement gate requires independent evidence" in report["reasons"]


def test_replacement_gate_rejects_duplicate_case_repeat_pairs():
    first = _replacement_case()
    second = deepcopy(first)
    second["manifest"]["execution_order"] = 2

    report = build_replacement_gate([first, second], min_cases=1, min_repeats=1)

    assert report["verdict"] == "insufficient_evidence"
    assert "duplicate case/repeat pair" in report["reasons"]


def _case() -> dict[str, object]:
    return {
        "case_id": "null-guard-1",
        "diff_id": "synthetic-null-guard",
        "source_type": "synthetic",
        "diff": "--- a/src/service.py\n+++ b/src/service.py\n@@\n- old\n+ new",
        "expected_findings": [
            {"id": "null-check", "severity": "중대", "file": "src/service.py", "line": "12"}
        ],
        "providers": {
            "codex": {
                "status": "completed",
                "metrics": {
                    "duration_ms": 1200,
                    "input_tokens": 100,
                    "output_tokens": 80,
                    "cost_usd": 0.004,
                },
                "findings": [
                    {"id": "null-check", "severity": "중대", "file": "src/service.py", "line": "12"}
                ],
            },
            "omc-review": {"status": "not_run", "findings": []},
        },
    }


def _observed_cases(count: int = 5) -> list[dict[str, object]]:
    cases = []
    for index in range(count):
        case = deepcopy(_case())
        case["case_id"] = f"observed-{index}"
        case["source_type"] = "observed_output"
        case["providers"]["omc-review"]["status"] = "completed"
        case["providers"]["omc-review"]["findings"] = deepcopy(case["providers"]["codex"]["findings"])
        case["providers"]["omc-review"]["metrics"] = deepcopy(case["providers"]["codex"]["metrics"])
        for provider in ("codex", "omc-review"):
            case["providers"][provider]["case_id"] = case["case_id"]
            case["providers"][provider]["diff_id"] = case["diff_id"]
        cases.append(case)
    return cases


def test_normalize_case_preserves_not_run_provider_and_expected_findings():
    case = normalize_case(_case())

    assert case["case_id"] == "null-guard-1"
    assert case["source_type"] == "synthetic"
    assert case["providers"]["omc-review"]["status"] == "not_run"
    assert case["providers"]["codex"]["metrics"]["duration_ms"] == 1200
    assert case["expected_findings"][0]["id"] == "null-check"


def test_summarize_provider_counts_hits_misses_and_false_positives():
    case = normalize_case(_case())

    summary = summarize_provider(case, "codex")

    assert summary == {
        "provider": "codex",
        "status": "completed",
        "expected_count": 1,
        "hit_count": 1,
        "miss_count": 0,
        "false_positive_count": 0,
        "evidence_match_count": 1,
        "evidence_complete": True,
    }


def test_completed_provider_with_missing_evidence_is_not_complete():
    case = _case()
    case["providers"]["codex"]["findings"] = [{"id": "null-check"}]

    summary = summarize_provider(normalize_case(case), "codex")

    assert summary["status"] == "completed"
    assert summary["evidence_complete"] is False


def test_completed_provider_with_no_findings_is_not_complete():
    case = _case()
    case["providers"]["codex"]["findings"] = []

    summary = summarize_provider(normalize_case(case), "codex")

    assert summary["status"] == "completed"
    assert summary["evidence_complete"] is False


def test_summarize_provider_separates_id_hits_from_evidence_matches():
    case = _case()
    case["providers"]["codex"]["findings"] = [
        {"id": "null-check", "severity": "중대", "file": "src/other.py", "line": "99"}
    ]

    summary = summarize_provider(normalize_case(case), "codex")

    assert summary["hit_count"] == 1
    assert summary["evidence_match_count"] == 0


def test_incomplete_expected_finding_is_not_an_evidence_match():
    case = _case()
    case["expected_findings"] = [{"id": "null-check"}]
    case["providers"]["codex"]["findings"] = [{"id": "null-check"}]

    summary = summarize_provider(normalize_case(case), "codex")

    assert summary["hit_count"] == 1
    assert summary["evidence_match_count"] == 0


def test_not_run_provider_cannot_be_scored_as_zero_quality():
    case = normalize_case(_case())

    summary = summarize_provider(case, "omc-review")

    assert summary["status"] == "not_run"
    assert summary["evidence_complete"] is False
    assert summary["hit_count"] is None


def test_normalize_case_rejects_unknown_provider_status():
    case = _case()
    case["providers"]["codex"]["status"] = "guessed"

    with pytest.raises(ValueError, match="unsupported provider status"):
        normalize_case(case)


def test_normalize_case_rejects_invalid_provider_metrics():
    case = _case()
    case["providers"]["codex"]["metrics"] = {"duration_ms": -1}

    with pytest.raises(ValueError, match="provider metric requires non-negative number"):
        normalize_case(case)


def test_normalize_case_rejects_provider_finding_without_id():
    case = _case()
    case["providers"]["codex"]["findings"] = [{"severity": "중대"}]

    with pytest.raises(ValueError, match="provider finding requires id"):
        normalize_case(case)


def test_normalize_case_rejects_duplicate_provider_finding_ids():
    case = _case()
    case["providers"]["codex"]["findings"].append(
        {"id": "null-check", "severity": "중대", "file": "src/service.py", "line": "13"}
    )

    with pytest.raises(ValueError, match="duplicate provider finding id"):
        normalize_case(case)


def test_normalize_case_rejects_provider_names_colliding_after_trim():
    case = _case()
    case["providers"][" codex "] = case["providers"]["codex"]

    with pytest.raises(ValueError, match="duplicate provider name"):
        normalize_case(case)


def test_normalize_case_rejects_duplicate_expected_finding_ids():
    case = _case()
    case["expected_findings"].append(
        {"id": "null-check", "severity": "중대", "file": "src/service.py", "line": "13"}
    )

    with pytest.raises(ValueError, match="duplicate expected finding id"):
        normalize_case(case)


def test_normalize_case_rejects_unknown_source_type():
    case = _case()
    case["source_type"] = "production_guess"

    with pytest.raises(ValueError, match="unsupported source type"):
        normalize_case(case)


def test_observed_output_requires_both_review_providers():
    case = _case()
    case["source_type"] = "observed_output"
    del case["providers"]["omc-review"]

    with pytest.raises(ValueError, match="observed output requires providers"):
        normalize_case(case)


def test_observed_output_requires_anonymized_diff():
    case = _case()
    case["source_type"] = "observed_output"
    case.pop("diff")

    with pytest.raises(ValueError, match="observed output requires anonymized diff"):
        normalize_case(case)


def test_observed_output_rejects_empty_diff():
    case = _case()
    case["source_type"] = "observed_output"
    case["diff"] = "  "

    with pytest.raises(ValueError, match="diff requires non-empty text"):
        normalize_case(case)


def test_observed_output_preserves_both_review_providers():
    case = _case()
    case["source_type"] = "observed_output"
    for provider in ("codex", "omc-review"):
        case["providers"][provider]["case_id"] = case["case_id"]
        case["providers"][provider]["diff_id"] = case["diff_id"]

    normalized = normalize_case(case)

    assert set(normalized["providers"]) == {"codex", "omc-review"}


def test_observed_output_rejects_provider_input_identity_mismatch():
    case = _case()
    case["source_type"] = "observed_output"
    for provider in ("codex", "omc-review"):
        case["providers"][provider]["case_id"] = case["case_id"]
        case["providers"][provider]["diff_id"] = case["diff_id"]
    case["providers"]["omc-review"]["diff_id"] = "different-diff"

    with pytest.raises(ValueError, match="provider input identity mismatch"):
        normalize_case(case)


def test_build_verdict_blocks_small_observed_sample():
    verdict = build_verdict(_observed_cases(4))

    assert verdict["verdict"] == "insufficient_evidence"
    assert verdict["case_count"] == 4


def test_build_verdict_blocks_failed_provider_results():
    cases = _observed_cases()
    cases[0]["providers"]["codex"]["status"] = "failed"

    verdict = build_verdict(cases)

    assert verdict["verdict"] == "insufficient_evidence"
    assert "completed" in verdict["reason"]


def test_build_verdict_reports_tie_for_equal_quality_results():
    verdict = build_verdict(_observed_cases())

    assert verdict["verdict"] == "tie"
    assert verdict["recall"]["codex"] == verdict["recall"]["omc-review"]


def test_build_verdict_requires_cost_metric():
    cases = _observed_cases()
    for case in cases:
        del case["providers"]["omc-review"]["metrics"]["cost_usd"]

    verdict = build_verdict(cases)

    assert verdict["verdict"] == "insufficient_evidence"
    assert "cost" in verdict["reason"]


def test_build_verdict_does_not_call_more_expensive_provider_superior():
    cases = _observed_cases()
    for case in cases:
        case["providers"]["codex"]["findings"] = []
        case["providers"]["omc-review"]["metrics"]["cost_usd"] = 0.02

    verdict = build_verdict(cases)

    assert verdict["verdict"] == "tie"
    assert verdict["metrics"]["cost_usd"]["omc-review"] > verdict["metrics"]["cost_usd"]["codex"]


def test_build_report_aggregates_completed_provider_rows_from_fixture():
    report = build_report(load_cases(FIXTURE_PATH))

    assert report["case_count"] == 13
    assert report["providers"]["codex"] == {
        "completed_count": 3,
        "missing_count": 0,
        "not_run_count": 10,
        "failed_count": 0,
        "scored_count": 3,
        "hit_count": 2,
        "miss_count": 1,
        "false_positive_count": 1,
        "evidence_match_count": 2,
        "evidence_complete_count": 2,
        "metrics": {"duration_ms": 0, "input_tokens": 0, "output_tokens": 0, "cost_usd": 0},
    }
    assert report["providers"]["omc-review"]["completed_count"] == 3
    assert report["providers"]["omc-review"]["missing_count"] == 0
    assert report["providers"]["omc-review"]["not_run_count"] == 10


def test_fixture_contains_ten_additional_synthetic_review_cases():
    cases = load_cases(FIXTURE_PATH)

    synthetic_cases = [case for case in cases if case["source_type"] == "synthetic"]

    assert len(synthetic_cases) >= 13
    assert len({case["case_id"] for case in synthetic_cases}) == len(synthetic_cases)
    assert all(case["expected_findings"] for case in synthetic_cases)
    assert all(case.get("diff") for case in synthetic_cases)


def test_build_finding_comparison_separates_shared_and_provider_only_findings():
    case = _case()
    case["providers"]["omc-review"] = {
        "status": "completed",
        "findings": [
            {"id": "different-id", "severity": "중대", "file": "src/service.py", "line": "12"},
            {"id": "omc-only", "severity": "경미", "file": "src/view.py", "line": "4"},
        ],
    }

    comparison = build_finding_comparison(case)

    assert len(comparison["shared"]) == 1
    assert comparison["shared"][0]["match_type"] == "evidence"
    assert [item["id"] for item in comparison["omc_only"]] == ["omc-only"]
    assert comparison["codex_only"] == []


def test_build_finding_comparison_matches_same_file_nearby_line_with_different_id():
    case = _case()
    case["providers"]["omc-review"] = {
        "status": "completed",
        "findings": [
            {"id": "renamed-null-check", "severity": "중대", "file": "src/service.py", "line": "13"}
        ],
    }

    comparison = build_finding_comparison(case)

    assert comparison["shared"][0]["match_type"] == "evidence_proximity"
    assert comparison["unmatched"] == []
    assert comparison["shared"][0]["evidence_match"] is False


def test_build_finding_comparison_marks_same_id_evidence_mismatch():
    case = _case()
    case["providers"]["omc-review"] = {
        "status": "completed",
        "findings": [
            {"id": "null-check", "severity": "중대", "file": "src/other.py", "line": "12"}
        ],
    }

    comparison = build_finding_comparison(case)

    assert comparison["shared"][0]["match_type"] == "id_evidence_mismatch"
    assert comparison["shared"][0]["evidence_match"] is False


def test_build_fixture_candidates_preserves_unmatched_as_pending_candidate():
    comparison = {
        "shared": [],
        "codex_only": [],
        "omc_only": [],
        "unmatched": [
            {
                "codex": {"id": "codex", "severity": "중대", "file": "src/a.py", "line": "2"},
                "omc-review": {"id": "omc", "severity": "중대", "file": "src/a.py", "line": "9"},
                "match_type": "uncertain",
            }
        ],
    }

    candidates = build_fixture_candidates(comparison)

    assert candidates[0]["adjudication_status"] == "pending_adjudication"
    assert candidates[0]["match_type"] == "uncertain"


def test_promote_confirmed_unmatched_candidate_uses_selected_finding():
    candidate = {
        "adjudication_status": "confirmed",
        "match_type": "uncertain",
        "findings": {
            "codex": {"id": "codex", "severity": "중대", "file": "src/a.py", "line": "2"},
            "omc-review": {"id": "omc", "severity": "중대", "file": "src/a.py", "line": "9"},
        },
        "selected_finding": {"id": "codex", "severity": "중대", "file": "src/a.py", "line": "2"},
    }

    assert promote_fixture_candidate(candidate) == candidate["selected_finding"]


def test_promote_unmatched_candidate_rejects_finding_outside_original_pair():
    candidate = {
        "adjudication_status": "confirmed",
        "match_type": "uncertain",
        "findings": {
            "codex": {"id": "codex", "severity": "중대", "file": "src/a.py", "line": "2"},
            "omc-review": {"id": "omc", "severity": "중대", "file": "src/a.py", "line": "9"},
        },
        "selected_finding": {"id": "invented", "severity": "중대", "file": "src/a.py", "line": "4"},
    }

    with pytest.raises(ValueError, match="original candidate"):
        promote_fixture_candidate(candidate)


def test_build_fixture_candidates_default_to_pending_adjudication():
    comparison = {
        "shared": [],
        "codex_only": [{"id": "codex-only", "severity": "중대", "file": "src/a.py", "line": "2"}],
        "omc_only": [],
        "unmatched": [],
    }

    candidates = build_fixture_candidates(comparison)

    assert candidates[0]["adjudication_status"] == "pending_adjudication"
    assert candidates[0]["provider"] == "codex"
    with pytest.raises(ValueError, match="confirmed"):
        promote_fixture_candidate(candidates[0])


def test_promote_fixture_candidate_requires_confirmed_complete_evidence():
    candidate = {
        "provider": "codex",
        "adjudication_status": "confirmed",
        "finding": {"id": "codex-only", "severity": "중대", "file": "src/a.py", "line": "2"},
    }

    assert promote_fixture_candidate(candidate) == candidate["finding"]


def test_format_report_table_has_fixed_provider_columns():
    report = build_report(load_cases(FIXTURE_PATH))

    table = format_report_table(report)

    assert "provider | completed | missing | not_run | failed | scored | hit | miss | false_positive | evidence_match | evidence_complete" in table
    assert "codex | 3 | 0 | 10 | 0 | 3 | 2 | 1 | 1 | 2 | 2" in table
    assert "omc-review | 3 | 0 | 10 | 0 | 3 | 2 | 1 | 2 | 2 | 3" in table


def test_format_metrics_table_has_fixed_cost_quality_columns():
    report = build_report([normalize_case(_case())])

    table = format_metrics_table(report)

    assert "provider | duration_ms | input_tokens | output_tokens | cost_usd" in table
    assert "codex | 1200 | 100 | 80 | 0.004" in table


def test_build_report_keeps_metric_order_deterministic():
    report = build_report([normalize_case(_case())])

    assert list(report["providers"]["codex"]["metrics"]) == [
        "duration_ms",
        "input_tokens",
        "output_tokens",
        "cost_usd",
    ]


def test_build_report_exposes_not_run_and_failed_without_scoring_them():
    case = normalize_case(_case())
    case["providers"]["gemini"] = {"status": "not_run", "findings": []}
    case["providers"]["claude"] = {"status": "failed", "findings": []}

    report = build_report([case])
    table = format_report_table(report)

    assert report["providers"]["gemini"] == {
        "completed_count": 0,
        "missing_count": 0,
        "not_run_count": 1,
        "failed_count": 0,
        "scored_count": 0,
            "hit_count": 0,
            "miss_count": 0,
            "false_positive_count": 0,
            "evidence_match_count": 0,
            "evidence_complete_count": 0,
            "metrics": {"duration_ms": 0, "input_tokens": 0, "output_tokens": 0, "cost_usd": 0},
    }
    assert "claude | 0 | 0 | 0 | 1 | 0 | 0 | 0 | 0 | 0" in table


def test_build_case_report_exposes_provider_finding_ids_and_source():
    case = load_cases(FIXTURE_PATH)[0]

    rows = build_case_report(case)

    codex = next(row for row in rows if row["provider"] == "codex")
    assert codex == {
        "case_id": "null-guard-1",
        "diff_id": "synthetic-null-guard",
        "source_type": "synthetic",
        "provider": "codex",
        "status": "completed",
        "expected_ids": ["null-check"],
        "hit_ids": ["null-check"],
        "miss_ids": [],
        "false_positive_ids": ["unused-import"],
        "evidence_complete": True,
    }


def test_build_report_exposes_provider_missing_from_a_case():
    complete = normalize_case(_case())
    incomplete = normalize_case(_case())
    del incomplete["providers"]["codex"]

    report = build_report([complete, incomplete])

    assert report["providers"]["codex"]["missing_count"] == 1
    assert report["providers"]["codex"]["scored_count"] == 1


def test_build_report_aggregates_provider_execution_metrics():
    report = build_report([normalize_case(_case())])

    assert report["providers"]["codex"]["metrics"] == {
        "duration_ms": 1200,
        "input_tokens": 100,
        "output_tokens": 80,
        "cost_usd": 0.004,
    }


def test_export_review_pack_contains_only_anonymized_baseline_inputs(tmp_path):
    output = tmp_path / "review-pack.json"

    export_review_pack(load_cases(FIXTURE_PATH), output)
    payload = json.loads(output.read_text(encoding="utf-8"))

    assert len(payload) == 13
    assert set(payload[0]) == {"case_id", "diff_id", "source_type", "diff", "expected_findings"}
    assert "providers" not in payload[0]
    assert all(case["source_type"] == "synthetic" for case in payload)


@pytest.mark.parametrize(
    ("field", "value"),
    [("case_id", "/Users/private/case"), ("diff_id", "../private-diff")],
)
def test_normalize_case_rejects_non_anonymized_identifiers(field, value):
    case = _case()
    case[field] = value

    with pytest.raises(ValueError, match="non-anonymized value"):
        normalize_case(case)


def test_normalize_case_rejects_absolute_finding_file_path():
    case = _case()
    case["expected_findings"][0]["file"] = "/Users/private/src/service.py"

    with pytest.raises(ValueError, match="non-anonymized value"):
        normalize_case(case)


def test_normalize_case_rejects_non_anonymized_diff_path():
    case = _case()
    case["diff"] = "--- /Users/private/src/service.py\n+++ b/src/service.py\n@@\n- old\n+ new"

    with pytest.raises(ValueError, match="non-anonymized value for diff path"):
        normalize_case(case)


def test_normalize_case_preserves_anonymized_diff():
    case = _case()
    case["diff"] = "--- a/src/service.py\n+++ b/src/service.py\n@@\n- old\n+ new"

    assert normalize_case(case)["diff"] == case["diff"]


@pytest.mark.parametrize("value", ["ghp_1234567890", "Bearer secret-token", "dev@example.com"])
def test_normalize_case_rejects_secret_like_values(value):
    case = _case()
    case["diff_id"] = value

    with pytest.raises(ValueError, match="sensitive value"):
        normalize_case(case)


def _comparison_sample():
    return {
        "sample_id": "codex-omc-null-guard-1",
        "case_id": "null-guard-1",
        "diff_id": "probe-null-guard",
        "source_type": "comparison_sample",
        "source_kind": "observed_session_diff",
        "evidence_ref": "session-20260719-review-1",
        "selection_reason": "actual Codex review probe",
        "recorded_at": "2026-07-19T23:00:00+09:00",
        "diff": "--- a/src/service.py\n+++ b/src/service.py\n@@\n-return payload.get(\"name\")\n+return payload.get(\"name\").strip()\n",
        "results": {
            "codex": {
                "case_id": "null-guard-1",
                "diff_id": "probe-null-guard",
                "prompt_id": "review-v1",
                "execution_mode": "cli_completed",
                "status": "completed",
                "runner": "codex review",
                "model": "gpt-5.6-luna",
                "findings": [{"id": "null-guard", "severity": "중대", "file": "src/service.py", "line": "2"}],
                "metrics": {},
            },
            "omc-review": {
                "case_id": "null-guard-1",
                "diff_id": "probe-null-guard",
                "prompt_id": "review-v1",
                "execution_mode": "manual_rule_application",
                "status": "completed",
                "runner": "omc-review skill",
                "model": None,
                "findings": [{"id": "null-guard", "severity": "중대", "file": "src/service.py", "line": "2"}],
                "metrics": {},
            },
        },
    }


def test_comparison_sample_requires_provenance_and_keeps_unknown_metrics_unknown():
    normalized = normalize_comparison_sample(_comparison_sample())

    assert normalized["results"]["omc-review"]["execution_mode"] == "manual_rule_application"
    assert normalized["results"]["omc-review"]["metrics"] == {}

    sample = _comparison_sample()
    del sample["results"]["omc-review"]["execution_mode"]
    with pytest.raises(ValueError, match="execution_mode"):
        normalize_comparison_sample(sample)


def test_comparison_sample_does_not_enter_operational_report_or_verdict():
    report = build_comparison_report([_comparison_sample()])

    assert report["sample_count"] == 1
    assert report["operational_case_count"] == 0
    assert report["finding_match_count"] == 1


def test_comparison_sample_requires_matching_result_identity_and_fixed_prompt_id():
    sample = _comparison_sample()
    for result in sample["results"].values():
        result.update({"case_id": sample["case_id"], "diff_id": sample["diff_id"], "prompt_id": "review-v1"})

    normalized = normalize_comparison_sample(sample)
    assert normalized["results"]["codex"]["prompt_id"] == "review-v1"

    sample["results"]["codex"]["diff_id"] = "other-diff"
    with pytest.raises(ValueError, match="input identity mismatch"):
        normalize_comparison_sample(sample)


def test_comparison_sample_preserves_execution_metadata_when_present():
    sample = _comparison_sample()
    metadata = {"snapshot_used": True, "workspace_mutated": False}
    sample["results"]["codex"]["execution_metadata"] = metadata
    sample["results"]["omc-review"]["execution_metadata"] = metadata

    normalized = normalize_comparison_sample(sample)

    assert normalized["results"]["codex"]["execution_metadata"] == metadata
    assert normalized["results"]["omc-review"]["execution_metadata"] == metadata


def test_raw_review_envelopes_build_comparison_sample_with_execution_metadata():
    base = _comparison_sample()
    codex = {"provider": "codex", **base["results"]["codex"]}
    omc_review = {"provider": "omc-review", **base["results"]["omc-review"]}
    metadata = {"snapshot_used": True, "workspace_mutated": False}
    codex["execution_metadata"] = metadata
    omc_review["execution_metadata"] = metadata

    normalized = build_comparison_sample_from_envelopes(
        source_kind=base["source_kind"],
        evidence_ref=base["evidence_ref"],
        selection_reason=base["selection_reason"],
        recorded_at=base["recorded_at"],
        diff=base["diff"],
        codex=codex,
        omc_review=omc_review,
    )

    assert normalized["source_type"] == "comparison_sample"
    assert normalized["case_id"] == base["case_id"]
    assert normalized["results"]["codex"]["execution_metadata"] == metadata


def test_raw_review_envelopes_require_matching_identity_and_metadata():
    base = _comparison_sample()
    codex = {"provider": "codex", **base["results"]["codex"]}
    omc_review = {"provider": "omc-review", **base["results"]["omc-review"]}
    metadata = {"snapshot_used": True, "workspace_mutated": False}
    codex["execution_metadata"] = metadata
    omc_review["execution_metadata"] = metadata

    omc_review["case_id"] = "other-case"
    with pytest.raises(ValueError, match="input identity mismatch"):
        build_comparison_sample_from_envelopes(
            source_kind=base["source_kind"],
            evidence_ref=base["evidence_ref"],
            selection_reason=base["selection_reason"],
            recorded_at=base["recorded_at"],
            diff=base["diff"],
            codex=codex,
            omc_review=omc_review,
        )

    omc_review["case_id"] = base["case_id"]
    del omc_review["execution_metadata"]
    with pytest.raises(ValueError, match="execution_metadata"):
        build_comparison_sample_from_envelopes(
            source_kind=base["source_kind"],
            evidence_ref=base["evidence_ref"],
            selection_reason=base["selection_reason"],
            recorded_at=base["recorded_at"],
            diff=base["diff"],
            codex=codex,
            omc_review=omc_review,
        )


def test_comparison_sample_id_is_deterministic_and_order_independent():
    first = build_comparison_sample_id(
        "null-guard-1", "probe-null-guard", ["codex-v1", "omc-v1"], "2026-07-20T12:00:00+09:00"
    )
    second = build_comparison_sample_id(
        "null-guard-1", "probe-null-guard", ["omc-v1", "codex-v1"], "2026-07-20T12:00:00+09:00"
    )

    assert first == second


def test_comparison_sample_rejects_non_deterministic_sample_id_override():
    base = _comparison_sample()
    codex = {"provider": "codex", **base["results"]["codex"]}
    omc_review = {"provider": "omc-review", **base["results"]["omc-review"]}
    metadata = {"snapshot_used": True, "workspace_mutated": False}
    codex["execution_metadata"] = metadata
    omc_review["execution_metadata"] = metadata

    with pytest.raises(ValueError, match="sample_id must match deterministic"):
        build_comparison_sample_from_envelopes(
            sample_id="manual-id",
            source_kind=base["source_kind"],
            evidence_ref=base["evidence_ref"],
            selection_reason=base["selection_reason"],
            recorded_at=base["recorded_at"],
            diff=base["diff"],
            codex=codex,
            omc_review=omc_review,
        )


def test_comparison_sample_accepts_explicit_not_run_mode():
    sample = _comparison_sample()
    for result in sample["results"].values():
        result["status"] = "not_run"
        result["execution_mode"] = "not_run"

    normalized = normalize_comparison_sample(sample)

    assert normalized["results"]["codex"]["execution_mode"] == "not_run"

def test_comparison_report_counts_evidence_match_not_id_only():
    sample = _comparison_sample()
    sample["results"]["omc-review"]["findings"][0]["file"] = "src/other.py"

    report = build_comparison_report([sample])

    assert report["finding_match_count"] == 0
    assert report["evidence_match_count"] == 0


def test_comparison_fixture_loads_actual_recorded_sample():
    samples = load_comparison_samples(COMPARISON_FIXTURE_PATH)

    assert len(samples) == 5
    assert samples[0]["results"]["codex"]["execution_mode"] == "cli_completed"
    assert samples[0]["results"]["omc-review"]["execution_mode"] == "manual_rule_application"


def test_comparison_report_exposes_provider_only_findings():
    samples = load_comparison_samples(COMPARISON_FIXTURE_PATH)
    report = build_comparison_report(samples)

    assert report["finding_match_count"] == 3
    assert report["codex_only_count"] == 0
    assert report["omc_only_count"] == 2


def test_comparison_sample_requires_observed_provenance_fields():
    sample = _comparison_sample()
    del sample["evidence_ref"]

    with pytest.raises(ValueError, match="evidence_ref"):
        normalize_comparison_sample(sample)


def test_consistency_verdict_blocks_small_sample_without_claiming_superiority():
    verdict = build_consistency_verdict([_comparison_sample()])

    assert verdict == {
        "verdict": "insufficient_sample",
        "sample_count": 1,
        "minimum_sample_count": 5,
        "interpretation": "consistency_probe_only",
    }


def test_consistency_verdict_never_returns_provider_winner():
    samples = []
    for index in range(5):
        sample = _comparison_sample()
        sample["sample_id"] = f"codex-omc-null-guard-{index}"
        samples.append(sample)

    verdict = build_consistency_verdict(samples)

    assert verdict["verdict"] == "consistency_only"
    assert "winner" not in verdict
    assert "superior" not in verdict


def test_consistency_verdict_rejects_duplicate_ids_without_loader():
    with pytest.raises(ValueError, match="duplicate comparison sample id"):
        build_consistency_verdict([_comparison_sample()] * 5)


def test_comparison_sample_requires_provider_specific_execution_modes():
    sample = _comparison_sample()
    sample["results"]["codex"]["execution_mode"] = "manual_rule_application"

    with pytest.raises(ValueError, match="execution mode mismatch"):
        normalize_comparison_sample(sample)


def test_comparison_sample_accepts_actual_omc_cli_execution_mode():
    sample = deepcopy(_comparison_sample())
    sample["results"]["omc-review"]["execution_mode"] = "cli_completed"

    normalized = normalize_comparison_sample(sample)

    assert normalized["results"]["omc-review"]["execution_mode"] == "cli_completed"


def test_comparison_sample_accepts_failed_cli_execution_mode():
    sample = deepcopy(_comparison_sample())
    sample["results"]["omc-review"]["execution_mode"] = "cli_failed"
    sample["results"]["omc-review"]["status"] = "failed"

    normalized = normalize_comparison_sample(sample)

    assert normalized["results"]["omc-review"]["execution_mode"] == "cli_failed"


@pytest.mark.parametrize(
    ("status", "execution_mode"),
    [("completed", "cli_failed"), ("failed", "cli_completed"), ("failed", "manual_rule_application")],
)
def test_comparison_sample_rejects_status_execution_mode_mismatch(status, execution_mode):
    sample = deepcopy(_comparison_sample())
    sample["results"]["omc-review"]["status"] = status
    sample["results"]["omc-review"]["execution_mode"] = execution_mode

    with pytest.raises(ValueError, match="status and execution_mode mismatch"):
        normalize_comparison_sample(sample)


def test_pilot_report_keeps_single_sample_non_decisive_and_exposes_unknown_quality():
    report = build_pilot_report([_comparison_sample()])

    assert report["verdict"] == "insufficient_sample"
    assert report["interpretation"] == "pilot_only"
    assert report["quality_verdict"] == "unknown"
    assert report["sample_count"] == 1
    assert "winner" not in report
    assert "superior" not in report


def test_pilot_report_measures_output_contract_without_claiming_quality():
    sample = _comparison_sample()
    for result in sample["results"].values():
        result["verdict"] = "REVISE"
        result["next_action"] = "omc-task"

    report = build_pilot_report([sample])
    contract = report["output_contract"]

    assert contract["codex"]["verdict_presence"] == "compliant"
    assert contract["codex"]["next_action_presence"] == "compliant"
    assert contract["omc-review"]["file_line_evidence_rate"] == 1.0
    assert report["quality_verdict"] == "unknown"


def test_pilot_report_marks_missing_output_contract_as_unknown_not_success():
    report = build_pilot_report([_comparison_sample()])

    assert report["output_contract"]["codex"]["verdict_presence"] == "unknown"
    assert report["output_contract"]["omc-review"]["next_action_presence"] == "unknown"
    assert report["output_contract"]["omc-review"]["file_line_evidence_rate"] == 1.0


def test_review_severity_mapping_normalizes_codex_and_omc_labels():
    assert map_review_severity("P0", "codex") == "치명"
    assert map_review_severity("P1", "codex") == "중대"
    assert map_review_severity("경미", "omc-review") == "경미"
    assert map_review_severity("P9", "codex") == "unmapped"


def test_pilot_report_exposes_severity_mapping_without_quality_claim():
    sample = _comparison_sample()
    sample["results"]["codex"]["findings"][0]["severity"] = "P1"
    report = build_pilot_report([sample], batch_id="pilot-batch-1")

    assert report["batch_id"] == "pilot-batch-1"
    assert report["severity_mapping"]["codex"]["mapped_count"] == 1
    assert report["severity_mapping"]["omc-review"]["mapped_count"] == 1
    assert report["quality_verdict"] == "unknown"


def test_pilot_report_matches_equivalent_provider_severities_in_evidence():
    sample = _comparison_sample()
    sample["results"]["codex"]["findings"][0]["severity"] = "P1"
    sample["results"]["omc-review"]["findings"][0]["severity"] = "중대"

    report = build_pilot_report([sample], batch_id="pilot-batch-1")

    assert report["comparison"]["evidence_match_count"] == 1


def test_pilot_report_rejects_non_anonymized_batch_id():
    with pytest.raises(ValueError, match="batch_id"):
        build_pilot_report([_comparison_sample()], batch_id="/private/batch")


def test_comparison_fixture_rejects_duplicate_sample_ids():
    samples = json.loads(COMPARISON_FIXTURE_PATH.read_text(encoding="utf-8"))
    samples.append(samples[0])
    path = COMPARISON_FIXTURE_PATH.parent / "duplicate-comparison-samples.json"
    path.write_text(json.dumps(samples), encoding="utf-8")
    try:
        with pytest.raises(ValueError, match="duplicate comparison sample id"):
            load_comparison_samples(path)
    finally:
        path.unlink()


@pytest.mark.parametrize("recorded_at", ["yesterday", "2026-07-19 23:00:00"])
def test_comparison_sample_rejects_non_iso_recorded_at(recorded_at):
    sample = _comparison_sample()
    sample["recorded_at"] = recorded_at

    with pytest.raises(ValueError, match="recorded_at requires ISO-8601"):
        normalize_comparison_sample(sample)
