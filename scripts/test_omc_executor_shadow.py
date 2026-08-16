from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone
import json
import multiprocessing

import omc_executor_shadow
from omc_executor_shadow import (
    build_noop_shadow_record,
    build_single_child_execution_grant,
    finalize_single_child_execution_reservation,
    finalize_single_child_execution_reservation_file,
    reserve_single_child_execution_grant,
    reserve_single_child_execution_grant_file,
)
import pytest


def _reserve_grant_worker(grant, ledger_path, queue):
    result = reserve_single_child_execution_grant_file(
        grant,
        ledger_path,
        expected_scope_hash="scope-abc",
        now=datetime(2026, 8, 16, tzinfo=timezone.utc),
    )
    queue.put((result["status"], result["reason_code"]))


def _finalize_reservation_worker(ledger_path, queue):
    result = finalize_single_child_execution_reservation_file(
        ledger_path,
        idempotency_key="run-child-1",
        outcome={
            "status": "succeeded",
            "reason_code": "completed",
            "elapsed_sec": 4.5,
            "output_chars": 320,
        },
        now=datetime(2026, 8, 16, 0, 1, tzinfo=timezone.utc),
    )
    queue.put((result["status"], result["reason_code"]))


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


def _reserved_ledger():
    return {
        "schema_version": 1,
        "revision": 1,
        "entries": [
            {
                "parent_id": "parent-1",
                "child_id": "child-1",
                "executor": "codex",
                "approval_id": "approval-1",
                "session_id": "session-1",
                "idempotency_key": "run-child-1",
                "scope_hash": "scope-abc",
                "status": "reserved",
                "reserved_at": "2026-08-16T00:00:00Z",
                "max_attempts": 1,
                "max_total_elapsed_sec": 120,
                "max_output_chars": 12000,
                "fallback_action": "parent_review",
            }
        ],
    }


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


def test_single_child_execution_grant_requires_explicit_opt_in():
    request = _single_child_pilot_request(
        execution_requested=True,
        execution_mode="single_child_opt_in",
    )

    grant = build_single_child_execution_grant(request)

    assert grant["mode"] == "single_child_execution_grant"
    assert grant["status"] == "ready"
    assert grant["execution_allowed"] is True
    assert grant["max_attempts"] == 1
    assert grant["fallback_action"] == "parent_review"
    assert grant["idempotency_key"] == "run-child-1"
    assert grant["scope_hash"] == "scope-abc"
    assert grant["approval_expires_at"] == "2099-01-01T00:00:00Z"


def test_single_child_execution_grant_blocks_missing_opt_in():
    grant = build_single_child_execution_grant(_single_child_pilot_request())

    assert grant["status"] == "blocked"
    assert grant["reason_code"] == "execution_opt_in_missing"
    assert grant["execution_allowed"] is False


def test_single_child_execution_grant_reuses_shadow_safety_gate():
    request = _single_child_pilot_request(
        execution_requested=True,
        execution_mode="single_child_opt_in",
        seen_idempotency_keys=["run-child-1"],
    )

    grant = build_single_child_execution_grant(request)

    assert grant["status"] == "blocked"
    assert grant["reason_code"] == "duplicate_idempotency_key"
    assert grant["execution_allowed"] is False


def test_single_child_execution_grant_is_reserved_once_without_mutating_input():
    grant = build_single_child_execution_grant(
        _single_child_pilot_request(
            execution_requested=True,
            execution_mode="single_child_opt_in",
        )
    )
    ledger = {"schema_version": 1, "revision": 0, "entries": []}
    original = deepcopy(ledger)

    result = reserve_single_child_execution_grant(
        grant,
        ledger,
        expected_scope_hash="scope-abc",
        expected_ledger_revision=0,
        now=datetime(2026, 8, 16, tzinfo=timezone.utc),
    )

    assert result["status"] == "reserved"
    assert result["reason_code"] == "grant_reserved"
    assert result["reservation"]["idempotency_key"] == "run-child-1"
    assert result["reservation"]["max_attempts"] == 1
    assert result["ledger"]["entries"] == [result["reservation"]]
    assert result["ledger"]["revision"] == 1
    assert ledger == original


def test_single_child_execution_grant_rejects_duplicate_reservation():
    grant = build_single_child_execution_grant(
        _single_child_pilot_request(
            execution_requested=True,
            execution_mode="single_child_opt_in",
        )
    )
    ledger = {
        "schema_version": 1,
        "revision": 1,
        "entries": [{"idempotency_key": "run-child-1"}],
    }

    result = reserve_single_child_execution_grant(
        grant,
        ledger,
        expected_scope_hash="scope-abc",
        expected_ledger_revision=1,
        now=datetime(2026, 8, 16, tzinfo=timezone.utc),
    )

    assert result["status"] == "blocked"
    assert result["reason_code"] == "duplicate_grant_consumption"
    assert result["ledger"] == ledger


@pytest.mark.parametrize(
    ("scope_hash", "now", "reason_code"),
    [
        (
            "scope-other",
            datetime(2026, 8, 16, tzinfo=timezone.utc),
            "grant_scope_mismatch",
        ),
        (
            "scope-abc",
            datetime(2100, 1, 1, tzinfo=timezone.utc),
            "grant_expired",
        ),
    ],
)
def test_single_child_execution_grant_blocks_invalid_reservation(
    scope_hash, now, reason_code
):
    grant = build_single_child_execution_grant(
        _single_child_pilot_request(
            execution_requested=True,
            execution_mode="single_child_opt_in",
        )
    )

    result = reserve_single_child_execution_grant(
        grant,
        {"schema_version": 1, "revision": 0, "entries": []},
        expected_scope_hash=scope_hash,
        expected_ledger_revision=0,
        now=now,
    )

    assert result["status"] == "blocked"
    assert result["reason_code"] == reason_code
    assert result["reservation"] is None


@pytest.mark.parametrize(
    ("mutate", "reason_code"),
    [
        (
            lambda grant: grant.pop("max_output_chars"),
            "execution_grant_invalid",
        ),
        (
            lambda grant: grant.update({"approval_expires_at": "2099-01-01T00:00:00"}),
            "execution_grant_invalid",
        ),
    ],
)
def test_single_child_execution_grant_fails_closed_on_tampering(mutate, reason_code):
    grant = build_single_child_execution_grant(
        _single_child_pilot_request(
            execution_requested=True,
            execution_mode="single_child_opt_in",
        )
    )
    mutate(grant)

    result = reserve_single_child_execution_grant(
        grant,
        {"schema_version": 1, "revision": 0, "entries": []},
        expected_scope_hash="scope-abc",
        expected_ledger_revision=0,
        now=datetime(2026, 8, 16, tzinfo=timezone.utc),
    )

    assert result["status"] == "blocked"
    assert result["reason_code"] == reason_code


def test_single_child_execution_grant_blocks_stale_ledger_revision():
    grant = build_single_child_execution_grant(
        _single_child_pilot_request(
            execution_requested=True,
            execution_mode="single_child_opt_in",
        )
    )

    result = reserve_single_child_execution_grant(
        grant,
        {"schema_version": 1, "revision": 2, "entries": []},
        expected_scope_hash="scope-abc",
        expected_ledger_revision=1,
        now=datetime(2026, 8, 16, tzinfo=timezone.utc),
    )

    assert result["status"] == "blocked"
    assert result["reason_code"] == "consumption_ledger_stale"


def test_single_child_execution_grant_file_reserves_only_once(tmp_path):
    grant = build_single_child_execution_grant(
        _single_child_pilot_request(
            execution_requested=True,
            execution_mode="single_child_opt_in",
        )
    )
    ledger_path = tmp_path / "execution-grants.json"
    now = datetime(2026, 8, 16, tzinfo=timezone.utc)

    first = reserve_single_child_execution_grant_file(
        grant,
        ledger_path,
        expected_scope_hash="scope-abc",
        now=now,
    )
    second = reserve_single_child_execution_grant_file(
        grant,
        ledger_path,
        expected_scope_hash="scope-abc",
        now=now,
    )

    persisted = json.loads(ledger_path.read_text())
    assert first["status"] == "reserved"
    assert second["status"] == "blocked"
    assert second["reason_code"] == "duplicate_grant_consumption"
    assert persisted["revision"] == 1
    assert len(persisted["entries"]) == 1


def test_single_child_execution_grant_file_fails_closed_on_malformed_ledger(
    tmp_path,
):
    ledger_path = tmp_path / "execution-grants.json"
    ledger_path.write_text("not-json")
    grant = build_single_child_execution_grant(
        _single_child_pilot_request(
            execution_requested=True,
            execution_mode="single_child_opt_in",
        )
    )

    result = reserve_single_child_execution_grant_file(
        grant,
        ledger_path,
        expected_scope_hash="scope-abc",
        now=datetime(2026, 8, 16, tzinfo=timezone.utc),
    )

    assert result["status"] == "blocked"
    assert result["reason_code"] == "consumption_ledger_read_failed"
    assert ledger_path.read_text() == "not-json"


def test_single_child_execution_grant_file_serializes_processes(tmp_path):
    grant = build_single_child_execution_grant(
        _single_child_pilot_request(
            execution_requested=True,
            execution_mode="single_child_opt_in",
        )
    )
    ledger_path = tmp_path / "execution-grants.json"
    context = multiprocessing.get_context("fork")
    queue = context.Queue()
    processes = [
        context.Process(
            target=_reserve_grant_worker,
            args=(grant, ledger_path, queue),
        )
        for _ in range(2)
    ]

    for process in processes:
        process.start()
    for process in processes:
        process.join(timeout=5)

    outcomes = sorted(queue.get(timeout=1) for _ in processes)
    assert all(process.exitcode == 0 for process in processes)
    assert outcomes == [
        ("blocked", "duplicate_grant_consumption"),
        ("reserved", "grant_reserved"),
    ]


@pytest.mark.parametrize("terminal_status", ["succeeded", "failed", "timeout"])
def test_single_child_execution_reservation_records_terminal_outcome_without_mutation(
    terminal_status,
):
    ledger = _reserved_ledger()
    original = deepcopy(ledger)

    result = finalize_single_child_execution_reservation(
        ledger,
        idempotency_key="run-child-1",
        outcome={
            "status": terminal_status,
            "reason_code": "completed"
            if terminal_status == "succeeded"
            else "executor_error",
            "elapsed_sec": 12.5,
            "output_chars": 240,
        },
        expected_ledger_revision=1,
        now=datetime(2026, 8, 16, 0, 1, tzinfo=timezone.utc),
    )

    assert result["status"] == "finalized"
    assert result["reason_code"] == "execution_outcome_recorded"
    assert result["entry"]["status"] == terminal_status
    assert result["entry"]["completed_at"] == "2026-08-16T00:01:00Z"
    assert result["ledger"]["revision"] == 2
    assert ledger == original


def test_single_child_execution_reservation_blocks_duplicate_finalization():
    ledger = _reserved_ledger()
    ledger["entries"][0]["status"] = "succeeded"

    result = finalize_single_child_execution_reservation(
        ledger,
        idempotency_key="run-child-1",
        outcome={
            "status": "failed",
            "reason_code": "executor_error",
            "elapsed_sec": 1,
            "output_chars": 0,
        },
        expected_ledger_revision=1,
    )

    assert result["status"] == "blocked"
    assert result["reason_code"] == "execution_already_finalized"
    assert result["ledger"] == ledger


def test_single_child_execution_reservation_blocks_completion_before_reservation():
    ledger = _reserved_ledger()

    result = finalize_single_child_execution_reservation(
        ledger,
        idempotency_key="run-child-1",
        outcome={
            "status": "succeeded",
            "reason_code": "completed",
            "elapsed_sec": 1,
            "output_chars": 1,
        },
        expected_ledger_revision=1,
        now=datetime(2026, 8, 15, 23, 59, tzinfo=timezone.utc),
    )

    assert result["status"] == "blocked"
    assert result["reason_code"] == "completion_time_before_reservation"
    assert result["ledger"] == ledger


@pytest.mark.parametrize(
    ("revision", "outcome", "reason_code"),
    [
        (
            0,
            {
                "status": "succeeded",
                "reason_code": "completed",
                "elapsed_sec": 1,
                "output_chars": 1,
            },
            "consumption_ledger_stale",
        ),
        (
            1,
            {
                "status": "running",
                "reason_code": "started",
                "elapsed_sec": 1,
                "output_chars": 1,
            },
            "execution_outcome_invalid",
        ),
        (
            1,
            {
                "status": [],
                "reason_code": "started",
                "elapsed_sec": 1,
                "output_chars": 1,
            },
            "execution_outcome_invalid",
        ),
        (
            1,
            {
                "status": "failed",
                "reason_code": "executor_error",
                "elapsed_sec": float("nan"),
                "output_chars": 1,
            },
            "execution_outcome_invalid",
        ),
        (
            1,
            {
                "status": "timeout",
                "reason_code": "deadline",
                "elapsed_sec": 121,
                "output_chars": 1,
            },
            "execution_budget_exceeded",
        ),
        (
            1,
            {
                "status": "succeeded",
                "reason_code": "completed",
                "elapsed_sec": 1,
                "output_chars": 12001,
            },
            "execution_budget_exceeded",
        ),
    ],
)
def test_single_child_execution_reservation_fails_closed(
    revision, outcome, reason_code
):
    result = finalize_single_child_execution_reservation(
        _reserved_ledger(),
        idempotency_key="run-child-1",
        outcome=outcome,
        expected_ledger_revision=revision,
    )

    assert result["status"] == "blocked"
    assert result["reason_code"] == reason_code


def test_single_child_execution_reservation_file_persists_once(tmp_path):
    ledger_path = tmp_path / "execution-grants.json"
    ledger_path.write_text(json.dumps(_reserved_ledger()))
    outcome = {
        "status": "succeeded",
        "reason_code": "completed",
        "elapsed_sec": 4.5,
        "output_chars": 320,
    }

    first = finalize_single_child_execution_reservation_file(
        ledger_path,
        idempotency_key="run-child-1",
        outcome=outcome,
        now=datetime(2026, 8, 16, 0, 1, tzinfo=timezone.utc),
    )
    second = finalize_single_child_execution_reservation_file(
        ledger_path,
        idempotency_key="run-child-1",
        outcome=outcome,
    )

    persisted = json.loads(ledger_path.read_text())
    assert first["status"] == "finalized"
    assert second["status"] == "blocked"
    assert second["reason_code"] == "execution_already_finalized"
    assert persisted["revision"] == 2
    assert persisted["entries"][0]["status"] == "succeeded"


def test_single_child_execution_reservation_file_reports_post_replace_uncertainty(
    tmp_path, monkeypatch
):
    ledger_path = tmp_path / "execution-grants.json"
    ledger_path.write_text(json.dumps(_reserved_ledger()))
    real_fsync = omc_executor_shadow.os.fsync
    call_count = 0

    def fail_directory_fsync(fd):
        nonlocal call_count
        call_count += 1
        if call_count == 2:
            raise OSError("directory fsync failed")
        return real_fsync(fd)

    monkeypatch.setattr(omc_executor_shadow.os, "fsync", fail_directory_fsync)

    result = finalize_single_child_execution_reservation_file(
        ledger_path,
        idempotency_key="run-child-1",
        outcome={
            "status": "succeeded",
            "reason_code": "completed",
            "elapsed_sec": 4.5,
            "output_chars": 320,
        },
        now=datetime(2026, 8, 16, 0, 1, tzinfo=timezone.utc),
    )

    persisted = json.loads(ledger_path.read_text())
    assert result["status"] == "indeterminate"
    assert result["reason_code"] == "consumption_ledger_durability_unknown"
    assert result["ledger"] == persisted
    assert result["entry"] == persisted["entries"][0]
    assert persisted["entries"][0]["status"] == "succeeded"


def test_single_child_execution_reservation_file_serializes_processes(tmp_path):
    ledger_path = tmp_path / "execution-grants.json"
    ledger_path.write_text(json.dumps(_reserved_ledger()))
    context = multiprocessing.get_context("fork")
    queue = context.Queue()
    processes = [
        context.Process(
            target=_finalize_reservation_worker,
            args=(ledger_path, queue),
        )
        for _ in range(2)
    ]

    for process in processes:
        process.start()
    for process in processes:
        process.join(timeout=5)

    outcomes = sorted(queue.get(timeout=1) for _ in processes)
    assert all(process.exitcode == 0 for process in processes)
    assert outcomes == [
        ("blocked", "execution_already_finalized"),
        ("finalized", "execution_outcome_recorded"),
    ]


@pytest.mark.parametrize(
    ("override", "status", "reason"),
    [
        ({"child_count": 2}, "blocked", "single_child_required"),
        ({"child_status": "blocked"}, "hold", "child_not_ready"),
        ({"sensitive_paths": [".env"]}, "blocked", "sensitive_scope"),
        (
            {"dependency_statuses": {"dependency-1": "running"}},
            "hold",
            "dependency_not_ready",
        ),
        ({"plan_fingerprint": "plan-other"}, "blocked", "plan_scope_mismatch"),
        (
            {"seen_idempotency_keys": ["run-child-1"]},
            "blocked",
            "duplicate_idempotency_key",
        ),
        (
            {
                "budget": {
                    "max_attempts": 2,
                    "max_total_elapsed_sec": 120,
                    "max_output_chars": 12000,
                }
            },
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
