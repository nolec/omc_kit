from __future__ import annotations

from omc_executor_shadow import build_noop_shadow_record
import pytest


def _request(**overrides):
    request = {
        "parent_id": "parent-1",
        "child_id": "child-1",
        "executor": "codex",
        "scope_hash": "scope-abc",
        "approval": {
            "approval_id": "approval-1",
            "session_id": "session-1",
            "child_id": "child-1",
            "scope_hash": "scope-abc",
            "expires_at": "2099-01-01T00:00:00Z",
        },
        "policy": {
            "allowed_executors": ["codex"],
            "timeout_sec": 30,
            "budget_usd": 0.25,
            "retry_limit": 0,
        },
        "execution_requested": False,
    }
    request.update(overrides)
    return request


def _single_child_pilot_request(**overrides):
    request = _request(
        pilot_mode="single_child",
        child_count=1,
        child_status="ready",
        dependency_statuses={"dependency-1": "completed"},
        depends_on=["dependency-1"],
        sensitive_paths=[],
        plan_fingerprint="plan-abc",
        idempotency_key="run-child-1",
        seen_idempotency_keys=[],
        budget={
            "max_attempts": 1,
            "max_total_elapsed_sec": 120,
            "max_output_chars": 12000,
        },
    )
    request["approval"].update(
        {
            "plan_fingerprint": "plan-abc",
            "idempotency_key": "run-child-1",
            "operator_confirmed": True,
            "approval_status": "approved",
        }
    )
    request.update(overrides)
    return request


def test_shadow_adapter_returns_non_executing_record():
    record = build_noop_shadow_record(_request())

    assert record["mode"] == "noop_shadow"
    assert record["status"] == "simulated"
    assert record["execution_allowed"] is False
    assert record["sandbox_status"] == "not_started"
    assert record["usage_status"] == "unavailable"


def test_single_child_pilot_gate_allows_only_noop_shadow():
    record = build_noop_shadow_record(_single_child_pilot_request())

    assert record["mode"] == "noop_shadow"
    assert record["status"] == "simulated"
    assert record["gate_status"] == "allowed"
    assert record["shadow_recorded"] is True
    assert record["execution_allowed"] is False
    assert record["fallback_action"] == "parent_review"
    assert record["idempotency_key"] == "run-child-1"


@pytest.mark.parametrize(
    ("override", "status", "reason"),
    [
        ({"child_count": 2}, "blocked", "single_child_required"),
        ({"child_status": "blocked"}, "hold", "child_not_ready"),
        ({"sensitive_paths": [".env"]}, "blocked", "sensitive_scope"),
        ({"dependency_statuses": {"dependency-1": "running"}}, "hold", "dependency_not_ready"),
        ({"plan_fingerprint": "plan-other"}, "blocked", "plan_scope_mismatch"),
        ({"seen_idempotency_keys": ["run-child-1"]}, "blocked", "duplicate_idempotency_key"),
        (
            {"budget": {"max_attempts": 2, "max_total_elapsed_sec": 120, "max_output_chars": 12000}},
            "blocked",
            "budget_invalid",
        ),
    ],
)
def test_single_child_pilot_gate_blocks_unsafe_requests(override, status, reason):
    record = build_noop_shadow_record(_single_child_pilot_request(**override))

    assert record["status"] == status
    assert record["reason_code"] == reason
    assert record["execution_allowed"] is False


def test_single_child_pilot_gate_requires_bound_operator_approval():
    request = _single_child_pilot_request()
    request["approval"]["operator_confirmed"] = False

    record = build_noop_shadow_record(request)

    assert record["status"] == "blocked"
    assert record["reason_code"] == "operator_confirmation_missing"


@pytest.mark.parametrize(
    ("missing_field", "reason"),
    [
        ("sensitive_paths", "scope_metadata_missing"),
        ("depends_on", "dependency_metadata_missing"),
        ("dependency_statuses", "dependency_metadata_missing"),
    ],
)
def test_single_child_pilot_rejects_missing_safety_metadata(missing_field, reason):
    request = _single_child_pilot_request()
    request.pop(missing_field)

    record = build_noop_shadow_record(request)

    assert record["status"] == "blocked"
    assert record["reason_code"] == reason
    assert record["execution_allowed"] is False


def test_shadow_adapter_blocks_missing_approval():
    record = build_noop_shadow_record(_request(approval=None))

    assert record["status"] == "blocked"
    assert record["reason_code"] == "approval_missing"
    assert record["execution_allowed"] is False


def test_shadow_adapter_rejects_real_execution_request():
    record = build_noop_shadow_record(_request(execution_requested=True))

    assert record["status"] == "rejected"
    assert record["reason_code"] == "real_execution_disabled"
    assert record["execution_allowed"] is False


def test_shadow_adapter_rejects_timezone_less_expiry():
    request = _request()
    request["approval"]["expires_at"] = "2099-01-01T00:00:00"

    record = build_noop_shadow_record(request)

    assert record["status"] == "rejected"
    assert record["reason_code"] == "approval_expiry_invalid"


def test_shadow_adapter_rejects_non_finite_or_boolean_guard_values():
    request = _request()
    request["policy"]["budget_usd"] = float("nan")
    record = build_noop_shadow_record(request)
    assert record["reason_code"] == "guard_metadata_invalid"

    request = _request()
    request["policy"]["timeout_sec"] = True
    record = build_noop_shadow_record(request)
    assert record["reason_code"] == "guard_metadata_invalid"


def test_shadow_adapter_rejects_empty_identifiers():
    request = _request(parent_id="", executor="")

    record = build_noop_shadow_record(request)

    assert record["status"] == "rejected"
    assert record["reason_code"] == "identifier_missing"


def test_shadow_adapter_rejects_unrepresentable_numeric_guard_values():
    request = _request()
    request["policy"]["timeout_sec"] = 10**1000

    record = build_noop_shadow_record(request)

    assert record["status"] == "rejected"
    assert record["reason_code"] == "guard_metadata_invalid"


def test_shadow_adapter_rejects_non_boolean_execution_flag():
    record = build_noop_shadow_record(_request(execution_requested="true"))

    assert record["status"] == "rejected"
    assert record["reason_code"] == "execution_flag_invalid"
