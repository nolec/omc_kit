from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest

import omc_closure
import omc_closure_decision


def _contract() -> dict:
    envelope = {
        "schema_version": "omc-closure/v1",
        "work_unit": {
            "session_id": "session-1",
            "task_id": "task-1",
            "request_digest": "a" * 64,
        },
        "scope": {
            "deliverables": ["decision engine"],
            "definition_of_done": ["verified and accepted"],
            "non_goals": ["automatic transition"],
        },
        "validation": {
            "max_total_rounds": 3,
            "max_revisions_per_issue": 2,
        },
        "acceptance": {"authority": "user", "rule_id": None},
    }
    envelope["contract_sha256"] = omc_closure.envelope_sha256(envelope)
    return envelope


def _decision_input(*, verification: str = "pending", issues: list | None = None) -> dict:
    contract = _contract()
    return {
        "schema_version": "omc-closure-decision-input/v1",
        "work_unit": copy.deepcopy(contract["work_unit"]),
        "contract_sha256": contract["contract_sha256"],
        "scope": {"change_required": False, "change_approved": False},
        "validation": {"rounds_consumed": 0},
        "verification": {
            "status": verification,
            "receipt_sha256": "b" * 64 if verification in {"passed", "failed"} else None,
        },
        "issues": issues or [],
        "acceptance_decision_id": None,
    }


def _issue(
    issue_id: str = "issue-1",
    *,
    severity: str = "minor",
    status: str = "open",
    revision: int = 1,
    validation_round: int = 1,
    previous_fingerprint: str | None = None,
    change_reason: str | None = None,
) -> dict:
    return {
        "issue_id": issue_id,
        "surface": "delivery",
        "invariant": "result remains usable",
        "failure_mode": "result is incomplete",
        "severity": severity,
        "status": status,
        "revision": revision,
        "validation_round": validation_round,
        "previous_fingerprint": previous_fingerprint,
        "change_reason": change_reason,
    }


def _receipt(input_value: dict, *, issue_ids: list[str] | None = None) -> dict:
    binding = omc_closure_decision.acceptance_binding(
        input_value,
        accepted_residual_issue_ids=issue_ids or [],
    )
    return {
        "schema_version": "omc-closure-acceptance-receipt/v1",
        "decision_id": "accept-1",
        "session_id": "session-1",
        "action": "closure_accept",
        "status": "consumed",
        "binding": binding,
        "binding_sha256": omc_closure_decision.canonical_sha256(binding),
    }


def _consumed_acceptance(tmp_path: Path, receipt: dict):
    path = tmp_path / ".omc" / "state" / "latest.json"
    path.parent.mkdir(parents=True)
    path.write_text(
        json.dumps(
            {
                "pending_decision": {
                    "decision_id": receipt["decision_id"],
                    "action": "closure_accept",
                    "status": "consumed",
                    "acceptance_receipt": receipt,
                }
            }
        ),
        encoding="utf-8",
    )
    return omc_closure_decision.load_consumed_acceptance(
        tmp_path,
        decision_id=receipt["decision_id"],
    )


@pytest.mark.parametrize(
    ("verification", "rounds", "issues", "expected"),
    [
        ("pending", 0, [], "IN_PROGRESS"),
        ("pending", 3, [], "INCONCLUSIVE"),
        ("failed", 0, [], "BLOCKED"),
        ("passed", 1, [_issue(severity="major")], "BLOCKED"),
        ("passed", 0, [], "AWAITING_ACCEPTANCE"),
    ],
)
def test_decision_state_table(verification, rounds, issues, expected):
    value = _decision_input(verification=verification, issues=issues)
    value["validation"]["rounds_consumed"] = rounds

    result = omc_closure_decision.decide_closure(_contract(), value)

    assert result["closure_state"] == expected
    assert result["terminal"] is (expected in {"BLOCKED", "INCONCLUSIVE"})
    assert result["usable_now"] is False
    assert result["evidence_state"] == "UNMEASURED"
    assert "next_skill" not in result
    assert "command" not in result


def test_passed_verification_and_consumed_acceptance_complete_work_unit(tmp_path: Path):
    value = _decision_input(verification="passed")
    value["acceptance_decision_id"] = "accept-1"

    result = omc_closure_decision.decide_closure(
        _contract(),
        value,
        acceptance_receipt=_consumed_acceptance(tmp_path, _receipt(value)),
    )

    assert result["closure_state"] == "DONE"
    assert result["terminal"] is True
    assert result["usable_now"] is True
    assert result["evidence_state"] == "UNMEASURED"


def test_minor_issue_requires_exact_consumed_acceptance(tmp_path: Path):
    issue = _issue(status="accepted")
    value = _decision_input(verification="passed", issues=[issue])
    value["validation"]["rounds_consumed"] = 1
    value["acceptance_decision_id"] = "accept-1"

    without_issue = omc_closure_decision.decide_closure(
        _contract(), value, acceptance_receipt=_receipt(value)
    )
    with_issue = omc_closure_decision.decide_closure(
        _contract(),
        value,
        acceptance_receipt=_consumed_acceptance(
            tmp_path, _receipt(value, issue_ids=["issue-1"])
        ),
    )

    assert without_issue["closure_state"] == "BLOCKED"
    assert with_issue["closure_state"] == "DONE_WITH_RISK"
    assert with_issue["residual_issue_ids"] == ["issue-1"]


def test_raw_or_unconsumed_acceptance_cannot_complete():
    value = _decision_input(verification="passed")
    value["acceptance_decision_id"] = "accept-1"
    receipt = _receipt(value)
    receipt["status"] = "acknowledged"

    result = omc_closure_decision.decide_closure(
        _contract(), value, acceptance_receipt=receipt
    )

    assert result["closure_state"] == "BLOCKED"
    assert result["reason_code"] == "acceptance_receipt_invalid"


def test_invalid_acceptance_receipt_blocks_even_while_verification_is_pending(
    tmp_path: Path,
):
    value = _decision_input()
    value["acceptance_decision_id"] = "accept-1"
    receipt = _receipt(value)
    receipt["binding_sha256"] = "0" * 64

    result = omc_closure_decision.decide_closure(
        _contract(),
        value,
        acceptance_receipt=_consumed_acceptance(tmp_path, receipt),
    )

    assert result["closure_state"] == "BLOCKED"
    assert result["reason_code"] == "acceptance_receipt_invalid"


def test_acceptance_binding_change_blocks_completion(tmp_path: Path):
    value = _decision_input(verification="passed")
    value["acceptance_decision_id"] = "accept-1"
    receipt = _receipt(value)
    receipt["binding"]["contract_sha256"] = "c" * 64

    result = omc_closure_decision.decide_closure(
        _contract(),
        value,
        acceptance_receipt=_consumed_acceptance(tmp_path, receipt),
    )

    assert result["closure_state"] == "BLOCKED"
    assert result["reason_code"] == "acceptance_receipt_invalid"


def test_scope_change_invalidates_previous_acceptance(tmp_path: Path):
    value = _decision_input(verification="passed")
    value["acceptance_decision_id"] = "accept-1"
    acceptance = _consumed_acceptance(tmp_path, _receipt(value))
    value["scope"] = {"change_required": True, "change_approved": True}

    result = omc_closure_decision.decide_closure(
        _contract(), value, acceptance_receipt=acceptance
    )

    assert result["closure_state"] == "BLOCKED"
    assert result["reason_code"] == "acceptance_receipt_invalid"


def test_residual_issue_change_invalidates_previous_acceptance(tmp_path: Path):
    first = _issue(status="accepted")
    value = _decision_input(verification="passed", issues=[first])
    value["validation"]["rounds_consumed"] = 1
    value["acceptance_decision_id"] = "accept-1"
    acceptance = _consumed_acceptance(
        tmp_path, _receipt(value, issue_ids=["issue-1"])
    )
    second = _issue(
        status="accepted",
        revision=2,
        validation_round=2,
        previous_fingerprint=omc_closure_decision.issue_fingerprint(first),
        change_reason="risk changed",
    )
    second["failure_mode"] = "result loses material data"
    value["issues"].append(second)
    value["validation"]["rounds_consumed"] = 2

    result = omc_closure_decision.decide_closure(
        _contract(), value, acceptance_receipt=acceptance
    )

    assert result["closure_state"] == "BLOCKED"
    assert result["reason_code"] == "acceptance_receipt_invalid"


def test_acceptance_loader_rejects_symlinked_state_parent(tmp_path: Path):
    value = _decision_input(verification="passed")
    value["acceptance_decision_id"] = "accept-1"
    receipt = _receipt(value)
    external = tmp_path / "external"
    path = external / "state" / "latest.json"
    path.parent.mkdir(parents=True)
    path.write_text(
        json.dumps(
            {
                "pending_decision": {
                    "decision_id": "accept-1",
                    "action": "closure_accept",
                    "status": "consumed",
                    "acceptance_receipt": receipt,
                }
            }
        ),
        encoding="utf-8",
    )
    (tmp_path / ".omc").symlink_to(external, target_is_directory=True)

    with pytest.raises(
        omc_closure_decision.ClosureDecisionError,
        match="acceptance_state_symlink",
    ):
        omc_closure_decision.load_consumed_acceptance(
            tmp_path, decision_id="accept-1"
        )


def test_issue_lineage_preserves_budget_across_fingerprint_change():
    first = _issue()
    fingerprint = omc_closure_decision.issue_fingerprint(first)
    second = _issue(
        revision=2,
        validation_round=2,
        previous_fingerprint=fingerprint,
        change_reason="clarified failure mode",
    )
    second["failure_mode"] = "result omits a required field"
    value = _decision_input(issues=[first, second])
    value["validation"]["rounds_consumed"] = 2

    result = omc_closure_decision.decide_closure(_contract(), value)

    assert result["closure_state"] == "INCONCLUSIVE"
    assert result["reason_code"] == "issue_revision_budget_exhausted"


def test_validation_rounds_cannot_underreport_issue_events():
    first = _issue()
    second = _issue(
        revision=2,
        validation_round=2,
        previous_fingerprint=omc_closure_decision.issue_fingerprint(first),
        change_reason="rechecked",
    )
    value = _decision_input(issues=[first, second])
    value["validation"]["rounds_consumed"] = 1

    with pytest.raises(
        omc_closure_decision.ClosureDecisionError,
        match="validation_rounds_underreported",
    ):
        omc_closure_decision.decide_closure(_contract(), value)


def test_multiple_issues_may_be_reported_in_one_validation_round():
    first = _issue("issue-1")
    second = _issue("issue-2")
    second["surface"] = "verification"
    value = _decision_input(issues=[first, second])
    value["validation"]["rounds_consumed"] = 1

    result = omc_closure_decision.decide_closure(_contract(), value)

    assert result["closure_state"] == "IN_PROGRESS"
    assert result["reason_code"] == "verification_pending"


def test_issue_revision_must_advance_validation_round():
    first = _issue()
    second = _issue(
        revision=2,
        validation_round=1,
        previous_fingerprint=omc_closure_decision.issue_fingerprint(first),
        change_reason="rechecked without advancing round",
    )
    value = _decision_input(issues=[first, second])
    value["validation"]["rounds_consumed"] = 1

    result = omc_closure_decision.decide_closure(_contract(), value)

    assert result["closure_state"] == "BLOCKED"
    assert result["reason_code"] == "issue_history_invalid"


def test_open_issue_at_revision_limit_is_inconclusive():
    first = _issue()
    second = _issue(
        revision=2,
        validation_round=2,
        previous_fingerprint=omc_closure_decision.issue_fingerprint(first),
        change_reason="rechecked",
    )
    value = _decision_input(issues=[first, second])
    value["validation"]["rounds_consumed"] = 2

    result = omc_closure_decision.decide_closure(_contract(), value)

    assert result["closure_state"] == "INCONCLUSIVE"
    assert result["reason_code"] == "issue_revision_budget_exhausted"


def test_semantic_issue_cannot_reset_revision_budget_with_new_id():
    first = _issue("issue-1")
    renamed = _issue("issue-2")
    value = _decision_input(issues=[first, renamed])
    value["validation"]["rounds_consumed"] = 2

    result = omc_closure_decision.decide_closure(_contract(), value)

    assert result["closure_state"] == "BLOCKED"
    assert result["reason_code"] == "issue_history_invalid"


def test_issue_lineage_cannot_change_semantic_identity():
    first = _issue()
    second = _issue(
        revision=2,
        previous_fingerprint=omc_closure_decision.issue_fingerprint(first),
        change_reason="changed invariant",
    )
    second["invariant"] = "a different result remains usable"
    value = _decision_input(issues=[first, second])
    value["validation"]["rounds_consumed"] = 2

    result = omc_closure_decision.decide_closure(_contract(), value)

    assert result["closure_state"] == "BLOCKED"
    assert result["reason_code"] == "issue_history_invalid"


@pytest.mark.parametrize(
    "issues",
    [
        [_issue(), _issue()],
        [_issue(revision=2, previous_fingerprint="c" * 64, change_reason="rename")],
        [_issue(severity="major", status="accepted")],
        [_issue(revision=3, previous_fingerprint="c" * 64, change_reason="too many")],
    ],
)
def test_invalid_issue_history_fails_closed(issues):
    result = omc_closure_decision.decide_closure(
        _contract(), _decision_input(issues=issues)
    )

    assert result["closure_state"] == "BLOCKED"
    assert result["reason_code"] == "issue_history_invalid"


def test_decision_input_is_exact_and_verification_receipt_is_coherent():
    value = _decision_input()
    value["unexpected"] = True
    with pytest.raises(
        omc_closure_decision.ClosureDecisionError,
        match="decision_input_fields_invalid",
    ):
        omc_closure_decision.decide_closure(_contract(), value)

    value = _decision_input(verification="passed")
    value["verification"]["receipt_sha256"] = None
    with pytest.raises(
        omc_closure_decision.ClosureDecisionError,
        match="verification_receipt_required",
    ):
        omc_closure_decision.decide_closure(_contract(), value)


def test_decision_digest_is_deterministic_and_self_excluding():
    value = _decision_input()

    first = omc_closure_decision.decide_closure(_contract(), value)
    second = omc_closure_decision.decide_closure(_contract(), value)

    assert first == second
    assert first["decision_sha256"] == omc_closure_decision.decision_sha256(first)
