from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from omc_review_v6_screening import build_input_provenance, build_screening_report


def test_screening_report_matches_only_same_file_overlapping_line_evidence():
    gold = {
        "status": "signed_off",
        "cases": [{
            "case_id": "case-1",
            "diff_sha256": "diff-1",
            "gold_findings": [{
                "id": "gold-1",
                "severity": "P1",
                "file": "src/service.py",
                "line": "33-42",
                "reason": "A regression.",
            }],
        }],
    }
    codex = {
        "status": "completed_native_provider_runs_pending_adjudication",
        "clean_baseline": True,
        "cases": [{
            "case_id": "case-1",
            "diff_sha256": "diff-1",
            "providers": {"codex": {
                "status": "completed",
                "findings": [{
                    "severity": "P1",
                    "file": "/tmp/snapshot/workspace/src/service.py",
                    "line": "40",
                    "message": "Potential regression.",
                }],
            }},
        }],
    }
    omc = {
        "status": "completed_omc_runs_pending_adjudication",
        "cases": [{
            "case_id": "case-1",
            "diff_sha256": "diff-1",
            "providers": {"omc-review": {
                "status": "completed",
                "findings": [{
                    "severity": "중대",
                    "file": "src/service.py",
                    "line": "80",
                    "message": "Different concern.",
                    "evidence_class": "behavioral_direct",
                    "evidence": "The changed branch is reachable.",
                }],
            }},
        }],
    }

    report = build_screening_report(gold, codex, omc)

    assert report["status"] == "screening_only_pending_blind_adjudication"
    assert report["providers"]["codex"]["gold_location_candidate_count"] == 1
    assert report["providers"]["omc-review"]["gold_location_candidate_count"] == 0
    assert report["providers"]["omc-review"]["unmatched_finding_count"] == 1
    assert report["providers"]["codex"]["relative_file_paths"] is True


def test_screening_report_rejects_case_or_hash_mismatches():
    gold = {"status": "signed_off", "cases": [{"case_id": "case-1", "diff_sha256": "diff-1", "gold_findings": []}]}
    codex = {"status": "completed_native_provider_runs_pending_adjudication", "clean_baseline": True, "cases": []}
    omc = {"status": "completed_omc_runs_pending_adjudication", "cases": []}

    try:
        build_screening_report(gold, codex, omc)
    except ValueError as error:
        assert "case set mismatch" in str(error)
    else:
        raise AssertionError("expected a case set mismatch")


def test_screening_report_marks_unrecognized_absolute_paths_as_non_relative():
    gold = {"status": "signed_off", "cases": [{"case_id": "case-1", "diff_sha256": "diff-1", "gold_findings": []}]}
    codex = {"status": "completed_native_provider_runs_pending_adjudication", "clean_baseline": True, "cases": [{
        "case_id": "case-1", "diff_sha256": "diff-1", "providers": {"codex": {
            "status": "completed", "findings": [{"file": "/tmp/private.py", "line": "1", "message": "issue"}],
        }},
    }]}
    omc = {"status": "completed_omc_runs_pending_adjudication", "cases": [{
        "case_id": "case-1", "diff_sha256": "diff-1", "providers": {"omc-review": {"status": "completed", "findings": []}},
    }]}

    report = build_screening_report(gold, codex, omc)

    assert report["providers"]["codex"]["relative_file_paths"] is False


def test_input_provenance_records_absolute_path_and_content_hash(tmp_path: Path):
    source = tmp_path / "gold.json"
    source.write_text('{"version": 1}\n', encoding="utf-8")

    provenance = build_input_provenance({"gold": source})

    assert provenance["gold"]["path"] == "<external>/gold.json"
    assert len(provenance["gold"]["sha256"]) == 64
