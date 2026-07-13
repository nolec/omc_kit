from __future__ import annotations

from omc_executor_shadow import build_noop_shadow_record


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


def test_shadow_adapter_returns_non_executing_record():
    record = build_noop_shadow_record(_request())

    assert record["mode"] == "noop_shadow"
    assert record["status"] == "simulated"
    assert record["execution_allowed"] is False
    assert record["sandbox_status"] == "not_started"
    assert record["usage_status"] == "unavailable"


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
