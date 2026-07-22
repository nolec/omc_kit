import json
from pathlib import Path

from omc_review_compare import validate_review_boundary_cases


FIXTURE = Path(__file__).parent / "fixtures" / "omc_review_boundary_cases.json"
EXPECTED_CASES = {
    "optional_field_missing",
    "api_field_missing",
    "api_field_order_changed",
    "fallback_value_absent",
}


def test_review_boundary_fixture_covers_known_miss_types():
    payload = json.loads(FIXTURE.read_text(encoding="utf-8"))

    assert payload["source_type"] == "review_boundary_fixture"
    assert {case["case_id"] for case in payload["cases"]} == EXPECTED_CASES
    assert validate_review_boundary_cases(payload) == []


def test_review_boundary_fixture_requires_evidence_fields():
    payload = {
        "source_type": "review_boundary_fixture",
        "cases": [
            {
                "case_id": "optional_field_missing",
                "category": "optional_field_missing",
                "diff": "diff --git a/src/example.ts b/src/example.ts",
                "expected_findings": [{"severity": "P2"}],
            }
        ],
    }

    errors = validate_review_boundary_cases(payload)

    assert "optional_field_missing:expected_finding:missing-file" in errors
    assert "optional_field_missing:expected_finding:missing-line" in errors
    assert "optional_field_missing:expected_finding:missing-reason" in errors
