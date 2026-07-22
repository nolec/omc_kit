import pytest

from omc_review_compare import (
    validate_gold_adjudication_cases,
    validate_independent_adjudication,
)


def _adjudication(**overrides):
    value = {
        "status": "confirmed",
        "reviewer": "reviewer-a,reviewer-b",
        "provider_outputs_visible": False,
        "reviewer_count": 2,
        "agreement": "agreed",
        "tie_breaker_completed": False,
    }
    value.update(overrides)
    return value


def test_independent_adjudication_requires_hidden_provider_outputs_and_two_reviewers():
    assert validate_independent_adjudication(_adjudication()) == []
    assert "provider_outputs_visible" in validate_independent_adjudication(
        _adjudication(provider_outputs_visible=True)
    )
    assert "reviewer_count" in validate_independent_adjudication(
        _adjudication(reviewer_count=1)
    )


def test_disputed_adjudication_requires_completed_tie_breaker():
    assert validate_independent_adjudication(
        _adjudication(agreement="disputed", tie_breaker_completed=True)
    ) == []
    assert "tie_breaker_completed" in validate_independent_adjudication(
        _adjudication(agreement="disputed", tie_breaker_completed=False)
    )


def test_pending_adjudication_still_requires_contract_metadata():
    errors = validate_independent_adjudication(
        _adjudication(
            status="pending",
            reviewer="",
            reviewer_count=0,
            agreement="unknown",
        )
    )

    assert {"reviewer", "reviewer_count", "agreement"}.issubset(errors)


def test_invalid_adjudication_input_is_reported_without_throwing():
    with pytest.raises(ValueError, match="adjudication"):
        validate_independent_adjudication(None)


def test_gold_worksheet_enforces_nested_independence_metadata():
    worksheet = {
        "cases": [
            {
                "case_id": "case-1",
                "adjudication_status": "confirmed",
                "gold_findings": [],
                "adjudication": {
                    "status": "confirmed",
                    "reviewer": "reviewer-a",
                    "decision_reason": "confirmed from the diff",
                    "independence": _adjudication(
                        provider_outputs_visible=True,
                    ),
                },
            }
        ]
    }

    assert validate_gold_adjudication_cases(worksheet) == [
        "case-1:adjudication:provider_outputs_visible"
    ]


def test_gold_worksheet_rejects_status_mismatch():
    worksheet = {
        "cases": [
            {
                "case_id": "case-1",
                "adjudication_status": "confirmed",
                "gold_findings": [],
                "adjudication": {
                    "status": "pending",
                    "independence": _adjudication(status="pending"),
                },
            }
        ]
    }

    assert validate_gold_adjudication_cases(worksheet) == [
        "case-1:adjudication:status-mismatch"
    ]


def test_independent_adjudication_rejects_reviewer_count_mismatch():
    errors = validate_independent_adjudication(
        _adjudication(reviewer="reviewer-a", reviewer_count=2)
    )

    assert "reviewer_count" in errors
