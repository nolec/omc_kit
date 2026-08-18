from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone
import hashlib
import json
import multiprocessing

import omc_executor_shadow
from omc_executor_shadow import (
    build_parent_review_recovery,
    build_noop_shadow_record,
    build_single_child_execution_grant,
    build_two_child_sequence_grant,
    execute_reserved_single_child_grant_file,
    execute_two_child_sequence_grant_file,
    finalize_single_child_execution_reservation,
    finalize_single_child_execution_reservation_file,
    record_single_child_parent_review_decision,
    record_single_child_parent_review_decision_file,
    record_single_child_parent_review_followup,
    record_single_child_parent_review_followup_file,
    reserve_single_child_execution_grant,
    reserve_single_child_execution_grant_file,
)
import pytest


@pytest.mark.parametrize(
    (
        "execution_result",
        "recovery_action",
        "expected_execution_status",
        "expected_execution_reason_code",
    ),
    [
        (
            {"status": "failed", "reason_code": "executor_failed"},
            "inspect_child_failure",
            "failed",
            "executor_failed",
        ),
        (
            {"status": "timeout", "reason_code": "executor_timeout"},
            "inspect_timeout_and_partial_output",
            "timeout",
            "executor_timeout",
        ),
        (
            {
                "status": "indeterminate",
                "reason_code": "consumption_ledger_durability_unknown",
                "execution_status": "succeeded",
                "execution_reason_code": "executor_completed",
            },
            "reconcile_execution_ledger",
            "succeeded",
            "executor_completed",
        ),
        (
            {
                "status": "blocked",
                "reason_code": "completion_time_before_reservation",
                "execution_status": "succeeded",
                "execution_reason_code": "executor_completed",
            },
            "reconcile_execution_ledger",
            "succeeded",
            "executor_completed",
        ),
    ],
)
def test_parent_review_recovery_classifies_terminal_failures(
    execution_result,
    recovery_action,
    expected_execution_status,
    expected_execution_reason_code,
):
    result = build_parent_review_recovery(execution_result)

    assert result == {
        "status": "review_required",
        "action": "parent_review",
        "execution_status": expected_execution_status,
        "execution_reason_code": expected_execution_reason_code,
        "recovery_reason_code": execution_result["reason_code"],
        "recovery_action": recovery_action,
        "automatic_retry_allowed": False,
        "automatic_redistribution_allowed": False,
    }


@pytest.mark.parametrize(
    "execution_result",
    [
        {"status": "succeeded", "reason_code": "executor_completed"},
        {"status": "failed"},
        {"status": "unknown", "reason_code": "executor_failed"},
        {"status": "blocked", "reason_code": "execution_reservation_mismatch"},
        {
            "status": "indeterminate",
            "reason_code": "consumption_ledger_durability_unknown",
            "execution_status": "unknown",
        },
        {
            "status": "indeterminate",
            "reason_code": "consumption_ledger_durability_unknown",
            "execution_status": "succeeded",
        },
        None,
    ],
)
def test_parent_review_recovery_rejects_non_failure_or_malformed_results(
    execution_result,
):
    assert build_parent_review_recovery(execution_result) == {
        "status": "blocked",
        "reason_code": "parent_review_input_invalid",
    }


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
                "approval_expires_at": "2099-01-01T00:00:00Z",
                "status": "reserved",
                "reserved_at": "2026-08-16T00:00:00Z",
                "max_attempts": 1,
                "max_total_elapsed_sec": 120,
                "max_output_chars": 12000,
                "fallback_action": "parent_review",
            }
        ],
    }


def _execution_grant(**overrides):
    request = _single_child_pilot_request(
        execution_requested=True,
        execution_mode="single_child_opt_in",
    )
    grant = build_single_child_execution_grant(request)
    grant.update(overrides)
    return grant


def _monotonic_values(*values):
    iterator = iter(values)
    return lambda: next(iterator)


def _canonical_hash(value):
    payload = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def _two_child_sequence_request(**overrides):
    first = _execution_grant()
    second = deepcopy(first)
    second.update(
        {
            "child_id": "child-2",
            "approval_id": "approval-2",
            "idempotency_key": "run-child-2",
            "scope_hash": "scope-def",
        }
    )
    children = [
        {"child_id": "child-1", "depends_on": []},
        {"child_id": "child-2", "depends_on": ["child-1"]},
    ]
    ordered_child_ids = ["child-1", "child-2"]
    grants = [first, second]
    prompts = {"child-1": "implement first", "child-2": "implement second"}
    request = {
        "sequence_id": "sequence-1",
        "execution_mode": "two_child_sequential_opt_in",
        "execution_requested": True,
        "children": children,
        "ordered_child_ids": ordered_child_ids,
        "child_grants": grants,
        "child_prompts": prompts,
        "aggregate_budget": {
            "max_external_calls": 2,
            "max_total_elapsed_sec": 240,
            "max_output_chars": 24000,
        },
        "sequence_approval": {
            "approval_id": "sequence-approval-1",
            "sequence_id": "sequence-1",
            "operator_confirmed": True,
            "expires_at": "2099-01-01T00:00:00Z",
            "graph_sha256": _canonical_hash(children),
            "execution_order_sha256": _canonical_hash(ordered_child_ids),
            "child_grant_sha256s": [_canonical_hash(grant) for grant in grants],
            "prompt_sha256s": {
                child_id: _canonical_hash(prompts[child_id])
                for child_id in ordered_child_ids
            },
        },
    }
    request.update(overrides)
    return request


def test_two_child_sequence_grant_binds_graph_order_and_child_approvals():
    grant = build_two_child_sequence_grant(_two_child_sequence_request())

    assert grant["status"] == "ready"
    assert grant["mode"] == "two_child_sequence_grant"
    assert grant["ordered_child_ids"] == ["child-1", "child-2"]
    assert grant["max_external_calls"] == 2
    assert grant["automatic_retry_allowed"] is False
    assert grant["automatic_redistribution_allowed"] is False
    assert grant["automatic_resume_allowed"] is False


def test_two_child_sequence_grant_blocks_approval_hash_mismatch():
    request = _two_child_sequence_request()
    request["sequence_approval"]["graph_sha256"] = "wrong"

    grant = build_two_child_sequence_grant(request)

    assert grant["status"] == "blocked"
    assert grant["reason_code"] == "sequence_approval_binding_mismatch"


def test_two_child_sequence_grant_blocks_prompt_changed_after_approval():
    request = _two_child_sequence_request()
    request["child_prompts"]["child-2"] = "unapproved replacement prompt"

    grant = build_two_child_sequence_grant(request)

    assert grant["status"] == "blocked"
    assert grant["reason_code"] == "sequence_approval_binding_mismatch"


def test_two_child_sequence_executes_in_order_and_uses_separate_ledger(tmp_path):
    grant = build_two_child_sequence_grant(_two_child_sequence_request())
    calls = []

    result = execute_two_child_sequence_grant_file(
        grant,
        tmp_path / "sequences" / "sequence-1.json",
        tmp_path / "single-child.json",
        prompts={"child-1": "implement first", "child-2": "implement second"},
        project_root=tmp_path,
        runner=lambda **kwargs: calls.append(kwargs["prompt"])
        or {"returncode": 0, "output": "ok"},
        monotonic=_monotonic_values(1.0, 2.0, 3.0, 4.0),
        now=lambda: datetime(2026, 8, 18, 8, 30, tzinfo=timezone.utc),
    )

    persisted = json.loads((tmp_path / "sequences" / "sequence-1.json").read_text())
    assert result["status"] == "completed"
    assert result["completed_child_ids"] == ["child-1", "child-2"]
    assert result["external_call_count"] == 2
    assert result["total_elapsed_sec"] == 2.0
    assert result["total_output_chars"] == 4
    assert calls == ["implement first", "implement second"]
    assert persisted["sequence"]["status"] == "completed"
    assert persisted["sequence"]["children"][1]["status"] == "succeeded"


def test_two_child_sequence_stops_after_first_failure_and_routes_parent_review(tmp_path):
    grant = build_two_child_sequence_grant(_two_child_sequence_request())
    calls = []

    result = execute_two_child_sequence_grant_file(
        grant,
        tmp_path / "sequence.json",
        tmp_path / "single-child.json",
        prompts={"child-1": "implement first", "child-2": "implement second"},
        project_root=tmp_path,
        runner=lambda **kwargs: calls.append(kwargs["prompt"])
        or {"returncode": 1, "output": "failed"},
        monotonic=_monotonic_values(1.0, 2.0),
        now=lambda: datetime(2026, 8, 18, 8, 30, tzinfo=timezone.utc),
    )

    assert result["status"] == "review_required"
    assert result["failed_child_id"] == "child-1"
    assert result["pending_child_ids"] == ["child-2"]
    assert result["parent_review"]["status"] == "review_required"
    assert result["external_call_count"] == 1
    assert result["total_elapsed_sec"] == 1.0
    assert result["total_output_chars"] == 6
    assert calls == ["implement first"]


def test_two_child_sequence_does_not_count_claim_time_expiry_as_external_call(tmp_path):
    request = _two_child_sequence_request()
    request["child_grants"][1]["approval_expires_at"] = "2026-08-18T08:30:30Z"
    request["sequence_approval"]["child_grant_sha256s"] = [
        _canonical_hash(grant) for grant in request["child_grants"]
    ]
    grant = build_two_child_sequence_grant(request)
    current_times = iter(
        [
            datetime(2026, 8, 18, 8, 30, 0, tzinfo=timezone.utc),
            datetime(2026, 8, 18, 8, 30, 1, tzinfo=timezone.utc),
            datetime(2026, 8, 18, 8, 30, 10, tzinfo=timezone.utc),
            datetime(2026, 8, 18, 8, 31, 0, tzinfo=timezone.utc),
        ]
    )
    calls = []

    result = execute_two_child_sequence_grant_file(
        grant,
        tmp_path / "sequence.json",
        tmp_path / "single-child.json",
        prompts=request["child_prompts"],
        project_root=tmp_path,
        runner=lambda **kwargs: calls.append(kwargs["prompt"])
        or {"returncode": 0, "output": "ok"},
        monotonic=_monotonic_values(1.0, 2.0),
        now=lambda: next(current_times),
    )

    assert result["status"] == "review_required"
    assert result["children"][1]["reason_code"] == "grant_expired"
    assert result["external_call_count"] == 1
    assert result["total_elapsed_sec"] == 1.0
    assert result["total_output_chars"] == 2
    assert calls == ["implement first"]


def test_two_child_sequence_rechecks_sequence_approval_before_second_child(tmp_path):
    request = _two_child_sequence_request()
    request["sequence_approval"]["expires_at"] = "2026-08-18T08:30:30Z"
    grant = build_two_child_sequence_grant(request)
    current_times = iter(
        [
            datetime(2026, 8, 18, 8, 30, 0, tzinfo=timezone.utc),
            datetime(2026, 8, 18, 8, 30, 1, tzinfo=timezone.utc),
            datetime(2026, 8, 18, 8, 30, 2, tzinfo=timezone.utc),
            datetime(2026, 8, 18, 8, 30, 40, tzinfo=timezone.utc),
            datetime(2026, 8, 18, 8, 30, 41, tzinfo=timezone.utc),
        ]
    )
    calls = []

    result = execute_two_child_sequence_grant_file(
        grant,
        tmp_path / "sequence.json",
        tmp_path / "single-child.json",
        prompts=request["child_prompts"],
        project_root=tmp_path,
        runner=lambda **kwargs: calls.append(kwargs["prompt"])
        or {"returncode": 0, "output": "ok"},
        monotonic=_monotonic_values(1.0, 2.0, 3.0, 4.0),
        now=lambda: next(current_times),
    )

    assert result["status"] == "review_required"
    assert result["reason_code"] == "sequence_grant_expired"
    assert result["completed_child_ids"] == ["child-1"]
    assert result["pending_child_ids"] == ["child-2"]
    assert result["external_call_count"] == 1
    assert calls == ["implement first"]


def test_two_child_sequence_rechecks_sequence_approval_at_provider_claim(tmp_path):
    request = _two_child_sequence_request()
    request["sequence_approval"]["expires_at"] = "2026-08-18T08:30:30Z"
    grant = build_two_child_sequence_grant(request)
    current_times = iter(
        [
            datetime(2026, 8, 18, 8, 30, 0, tzinfo=timezone.utc),
            datetime(2026, 8, 18, 8, 30, 1, tzinfo=timezone.utc),
            datetime(2026, 8, 18, 8, 30, 2, tzinfo=timezone.utc),
            datetime(2026, 8, 18, 8, 30, 10, tzinfo=timezone.utc),
            datetime(2026, 8, 18, 8, 30, 31, tzinfo=timezone.utc),
            datetime(2026, 8, 18, 8, 30, 32, tzinfo=timezone.utc),
        ]
    )
    calls = []

    result = execute_two_child_sequence_grant_file(
        grant,
        tmp_path / "sequence.json",
        tmp_path / "single-child.json",
        prompts=request["child_prompts"],
        project_root=tmp_path,
        runner=lambda **kwargs: calls.append(kwargs["prompt"])
        or {"returncode": 0, "output": "ok"},
        monotonic=_monotonic_values(1.0, 2.0, 3.0, 4.0),
        now=lambda: next(current_times),
    )

    assert result["status"] == "review_required"
    assert result["children"][1]["reason_code"] == "sequence_grant_expired"
    assert result["external_call_count"] == 1
    assert calls == ["implement first"]


def test_two_child_sequence_preserves_observed_usage_when_finalization_fails(
    tmp_path, monkeypatch
):
    request = _two_child_sequence_request()
    grant = build_two_child_sequence_grant(request)

    monkeypatch.setattr(
        omc_executor_shadow,
        "finalize_single_child_execution_reservation_file",
        lambda *_args, **_kwargs: {
            "status": "blocked",
            "reason_code": "consumption_ledger_write_failed",
            "entry": None,
            "ledger": None,
        },
    )

    result = execute_two_child_sequence_grant_file(
        grant,
        tmp_path / "sequence.json",
        tmp_path / "single-child.json",
        prompts=request["child_prompts"],
        project_root=tmp_path,
        runner=lambda **_kwargs: {"returncode": 0, "output": "used"},
        monotonic=_monotonic_values(1.0, 2.5),
        now=lambda: datetime(2026, 8, 18, 8, 30, tzinfo=timezone.utc),
    )

    assert result["status"] == "review_required"
    assert result["external_call_count"] == 1
    assert result["total_elapsed_sec"] == 1.5
    assert result["total_output_chars"] == 4
    assert result["children"][0]["usage_durability"] == "observed_only"


def test_two_child_sequence_never_automatically_resumes_existing_ledger(tmp_path):
    grant = build_two_child_sequence_grant(_two_child_sequence_request())
    sequence_path = tmp_path / "sequence.json"
    sequence_path.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "revision": 1,
                "sequence": {"sequence_id": "sequence-1", "status": "running"},
            }
        )
    )
    calls = []

    result = execute_two_child_sequence_grant_file(
        grant,
        sequence_path,
        tmp_path / "single-child.json",
        prompts={"child-1": "implement first", "child-2": "implement second"},
        project_root=tmp_path,
        runner=lambda **kwargs: calls.append(kwargs),
    )

    assert result["status"] == "blocked"
    assert result["reason_code"] == "sequence_already_started"
    assert calls == []


def test_two_child_sequence_blocks_grant_tampering_before_execution(tmp_path):
    grant = build_two_child_sequence_grant(_two_child_sequence_request())
    grant["child_grants"][1]["scope_hash"] = "tampered"
    calls = []

    result = execute_two_child_sequence_grant_file(
        grant,
        tmp_path / "sequence.json",
        tmp_path / "single-child.json",
        prompts={"child-1": "implement first", "child-2": "implement second"},
        project_root=tmp_path,
        runner=lambda **kwargs: calls.append(kwargs),
    )

    assert result["status"] == "blocked"
    assert result["reason_code"] == "sequence_grant_binding_mismatch"
    assert calls == []


def test_two_child_sequence_ledger_rejects_non_mapping_without_exception(tmp_path):
    sequence_path = tmp_path / "sequence.json"
    sequence_path.write_text("[]")

    result = omc_executor_shadow._persist_two_child_sequence_state(
        sequence_path,
        {"sequence_id": "sequence-1"},
        create=False,
    )

    assert result["status"] == "indeterminate"
    assert result["reason_code"] == "sequence_ledger_invalid"


def test_two_child_sequence_lock_failure_routes_parent_review(tmp_path, monkeypatch):
    grant = build_two_child_sequence_grant(_two_child_sequence_request())

    def fail_flock(*_args):
        raise OSError("lock unavailable")

    monkeypatch.setattr(omc_executor_shadow.fcntl, "flock", fail_flock)

    result = execute_two_child_sequence_grant_file(
        grant,
        tmp_path / "sequence.json",
        tmp_path / "single-child.json",
        prompts={"child-1": "implement first", "child-2": "implement second"},
        project_root=tmp_path,
    )

    assert result["status"] == "indeterminate"
    assert result["reason_code"] == "sequence_ledger_lock_failed"
    assert result["parent_review"]["status"] == "review_required"


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
def test_single_child_execution_grant_blocks_invalid_reservation(scope_hash, now, reason_code):
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
            "reason_code": "completed" if terminal_status == "succeeded" else "executor_error",
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


def test_single_child_execution_reservation_persists_parent_review_for_failure():
    result = finalize_single_child_execution_reservation(
        _reserved_ledger(),
        idempotency_key="run-child-1",
        outcome={
            "status": "failed",
            "reason_code": "executor_failed",
            "elapsed_sec": 12.5,
            "output_chars": 240,
        },
        expected_ledger_revision=1,
        now=datetime(2026, 8, 16, 0, 1, tzinfo=timezone.utc),
    )

    assert result["entry"]["outcome"]["parent_review"] == {
        "status": "review_required",
        "action": "parent_review",
        "execution_status": "failed",
        "execution_reason_code": "executor_failed",
        "recovery_reason_code": "executor_failed",
        "recovery_action": "inspect_child_failure",
        "automatic_retry_allowed": False,
        "automatic_redistribution_allowed": False,
    }


def test_single_child_execution_reservation_omits_parent_review_for_success():
    result = finalize_single_child_execution_reservation(
        _reserved_ledger(),
        idempotency_key="run-child-1",
        outcome={
            "status": "succeeded",
            "reason_code": "executor_completed",
            "elapsed_sec": 12.5,
            "output_chars": 240,
        },
        expected_ledger_revision=1,
        now=datetime(2026, 8, 16, 0, 1, tzinfo=timezone.utc),
    )

    assert "parent_review" not in result["entry"]["outcome"]


def _terminal_failure_ledger():
    finalized = finalize_single_child_execution_reservation(
        _reserved_ledger(),
        idempotency_key="run-child-1",
        outcome={
            "status": "failed",
            "reason_code": "executor_failed",
            "elapsed_sec": 12.5,
            "output_chars": 240,
        },
        expected_ledger_revision=1,
        now=datetime(2026, 8, 16, 0, 1, tzinfo=timezone.utc),
    )
    return finalized["ledger"]


def _parent_review_approval(**overrides):
    approval = {
        "approval_id": "parent-review-approval-1",
        "approval_status": "approved",
        "operator_confirmed": True,
        "parent_id": "parent-1",
        "child_id": "child-1",
        "scope_hash": "scope-abc",
        "idempotency_key": "run-child-1",
        "decision": "hold",
        "recovery_action": "inspect_child_failure",
        "expires_at": "2099-01-01T00:00:00Z",
    }
    approval.update(overrides)
    return approval


def test_parent_review_decision_records_one_bound_operator_judgment_without_mutation():
    ledger = _terminal_failure_ledger()
    original = deepcopy(ledger)

    result = record_single_child_parent_review_decision(
        ledger,
        idempotency_key="run-child-1",
        approval=_parent_review_approval(),
        expected_ledger_revision=2,
        now=datetime(2026, 8, 16, 0, 2, tzinfo=timezone.utc),
    )

    assert result["status"] == "recorded"
    assert result["reason_code"] == "parent_review_decision_recorded"
    assert result["ledger"]["revision"] == 3
    assert result["entry"]["parent_review_decision"] == {
        "status": "recorded",
        "decision": "hold",
        "approval_id": "parent-review-approval-1",
        "decided_at": "2026-08-16T00:02:00Z",
        "recovery_action": "inspect_child_failure",
        "automatic_retry_allowed": False,
        "automatic_redistribution_allowed": False,
    }
    assert ledger == original


def _mismatch_terminal_outcome(ledger, approval):
    outcome = ledger["entries"][0]["outcome"]
    outcome["status"] = "timeout"
    outcome["parent_review"] = build_parent_review_recovery(
        {"status": "timeout", "reason_code": outcome["reason_code"]}
    )
    approval["recovery_action"] = "inspect_timeout_and_partial_output"


@pytest.mark.parametrize(
    ("mutate", "reason_code"),
    [
        (
            lambda ledger, approval: ledger["entries"][0].update(
                {"status": "succeeded"}
            ),
            "parent_review_not_required",
        ),
        (
            lambda ledger, approval: ledger["entries"][0]["outcome"].pop(
                "parent_review"
            ),
            "parent_review_not_required",
        ),
        (
            lambda ledger, approval: approval.update({"scope_hash": "other"}),
            "parent_review_approval_binding_mismatch",
        ),
        (
            lambda ledger, approval: approval.update({"decision": "retry"}),
            "parent_review_decision_invalid",
        ),
        (
            lambda ledger, approval: approval.update({"decision": []}),
            "parent_review_decision_invalid",
        ),
        (
            _mismatch_terminal_outcome,
            "parent_review_not_required",
        ),
        (
            lambda ledger, approval: approval.update(
                {"recovery_action": "redistribute_child"}
            ),
            "parent_review_approval_binding_mismatch",
        ),
    ],
)
def test_parent_review_decision_fails_closed_without_changing_ledger(
    mutate,
    reason_code,
):
    ledger = _terminal_failure_ledger()
    approval = _parent_review_approval()
    mutate(ledger, approval)
    original = deepcopy(ledger)

    result = record_single_child_parent_review_decision(
        ledger,
        idempotency_key="run-child-1",
        approval=approval,
        expected_ledger_revision=2,
        now=datetime(2026, 8, 16, 0, 2, tzinfo=timezone.utc),
    )

    assert result["status"] == "blocked"
    assert result["reason_code"] == reason_code
    assert result["ledger"] == original
    assert ledger == original


def test_parent_review_decision_file_persists_once(tmp_path):
    ledger_path = tmp_path / "execution-grants.json"
    ledger_path.write_text(json.dumps(_terminal_failure_ledger()))
    approval = _parent_review_approval()

    first = record_single_child_parent_review_decision_file(
        ledger_path,
        idempotency_key="run-child-1",
        approval=approval,
        now=datetime(2026, 8, 16, 0, 2, tzinfo=timezone.utc),
    )
    second = record_single_child_parent_review_decision_file(
        ledger_path,
        idempotency_key="run-child-1",
        approval=approval,
        now=datetime(2026, 8, 16, 0, 2, tzinfo=timezone.utc),
    )

    persisted = json.loads(ledger_path.read_text())
    assert first["status"] == "recorded"
    assert second["status"] == "blocked"
    assert second["reason_code"] == "parent_review_already_decided"
    assert persisted["revision"] == 3
    assert persisted["entries"][0]["parent_review_decision"] == first["decision"]


def test_parent_review_decision_file_blocks_tempfile_creation_failure(
    tmp_path,
    monkeypatch,
):
    ledger_path = tmp_path / "execution-grants.json"
    original = _terminal_failure_ledger()
    ledger_path.write_text(json.dumps(original))

    def fail_mkstemp(**_kwargs):
        raise OSError("disk full")

    monkeypatch.setattr(omc_executor_shadow.tempfile, "mkstemp", fail_mkstemp)

    result = record_single_child_parent_review_decision_file(
        ledger_path,
        idempotency_key="run-child-1",
        approval=_parent_review_approval(),
        now=datetime(2026, 8, 16, 0, 2, tzinfo=timezone.utc),
    )

    assert result["status"] == "blocked"
    assert result["reason_code"] == "consumption_ledger_write_failed"
    assert json.loads(ledger_path.read_text()) == original


def _decided_parent_review_ledger():
    decided = record_single_child_parent_review_decision(
        _terminal_failure_ledger(),
        idempotency_key="run-child-1",
        approval=_parent_review_approval(),
        expected_ledger_revision=2,
        now=datetime(2026, 8, 16, 0, 2, tzinfo=timezone.utc),
    )
    return decided["ledger"]


def _parent_review_followup(**overrides):
    followup = {
        "followup_id": "parent-review-followup-1",
        "approval_id": "parent-review-approval-1",
        "operator_confirmed": True,
        "parent_id": "parent-1",
        "child_id": "child-1",
        "scope_hash": "scope-abc",
        "idempotency_key": "run-child-1",
        "outcome": "still_blocked",
        "reason_code": "dependency_fix_required",
        "automatic_retry_performed": False,
        "automatic_redistribution_performed": False,
    }
    followup.update(overrides)
    return followup


def test_parent_review_followup_records_one_bound_manual_outcome_without_mutation():
    ledger = _decided_parent_review_ledger()
    original = deepcopy(ledger)

    result = record_single_child_parent_review_followup(
        ledger,
        idempotency_key="run-child-1",
        followup=_parent_review_followup(),
        expected_ledger_revision=3,
        now=datetime(2026, 8, 16, 0, 3, tzinfo=timezone.utc),
    )

    assert result["status"] == "recorded"
    assert result["reason_code"] == "parent_review_followup_recorded"
    assert result["ledger"]["revision"] == 4
    assert result["entry"]["parent_review_followup"] == {
        "status": "recorded",
        "followup_id": "parent-review-followup-1",
        "approval_id": "parent-review-approval-1",
        "outcome": "still_blocked",
        "reason_code": "dependency_fix_required",
        "observed_at": "2026-08-16T00:03:00Z",
        "automatic_retry_performed": False,
        "automatic_redistribution_performed": False,
    }
    assert ledger == original


@pytest.mark.parametrize(
    ("mutate", "reason_code"),
    [
        (
            lambda ledger, followup: ledger["entries"][0].pop(
                "parent_review_decision"
            ),
            "parent_review_decision_missing",
        ),
        (
            lambda ledger, followup: followup.update({"approval_id": "other"}),
            "parent_review_followup_binding_mismatch",
        ),
        (
            lambda ledger, followup: followup.update({"outcome": "retried"}),
            "parent_review_followup_invalid",
        ),
        (
            lambda ledger, followup: followup.update({"outcome": []}),
            "parent_review_followup_invalid",
        ),
        (
            lambda ledger, followup: ledger["entries"][0][
                "parent_review_decision"
            ].update({"decision": []}),
            "parent_review_decision_missing",
        ),
        (
            lambda ledger, followup: ledger["entries"][0].update(
                {"status": "succeeded"}
            ),
            "parent_review_decision_missing",
        ),
        (
            lambda ledger, followup: followup.update(
                {"automatic_retry_performed": True}
            ),
            "parent_review_followup_forbidden_automation",
        ),
        (
            lambda ledger, followup: followup.update(
                {"automatic_redistribution_performed": True}
            ),
            "parent_review_followup_forbidden_automation",
        ),
    ],
)
def test_parent_review_followup_fails_closed_without_changing_ledger(
    mutate,
    reason_code,
):
    ledger = _decided_parent_review_ledger()
    followup = _parent_review_followup()
    mutate(ledger, followup)
    original = deepcopy(ledger)

    result = record_single_child_parent_review_followup(
        ledger,
        idempotency_key="run-child-1",
        followup=followup,
        expected_ledger_revision=3,
        now=datetime(2026, 8, 16, 0, 3, tzinfo=timezone.utc),
    )

    assert result["status"] == "blocked"
    assert result["reason_code"] == reason_code
    assert result["ledger"] == original
    assert ledger == original


def test_parent_review_followup_file_persists_once(tmp_path):
    ledger_path = tmp_path / "execution-grants.json"
    ledger_path.write_text(json.dumps(_decided_parent_review_ledger()))
    followup = _parent_review_followup()

    first = record_single_child_parent_review_followup_file(
        ledger_path,
        idempotency_key="run-child-1",
        followup=followup,
        now=datetime(2026, 8, 16, 0, 3, tzinfo=timezone.utc),
    )
    second = record_single_child_parent_review_followup_file(
        ledger_path,
        idempotency_key="run-child-1",
        followup=followup,
        now=datetime(2026, 8, 16, 0, 3, tzinfo=timezone.utc),
    )

    persisted = json.loads(ledger_path.read_text())
    assert first["status"] == "recorded"
    assert second["status"] == "blocked"
    assert second["reason_code"] == "parent_review_followup_already_recorded"
    assert persisted["revision"] == 4
    assert persisted["entries"][0]["parent_review_followup"] == first["followup"]


def test_parent_review_followup_file_blocks_tempfile_creation_failure(
    tmp_path,
    monkeypatch,
):
    ledger_path = tmp_path / "execution-grants.json"
    original = _decided_parent_review_ledger()
    ledger_path.write_text(json.dumps(original))

    def fail_mkstemp(**_kwargs):
        raise OSError("disk full")

    monkeypatch.setattr(omc_executor_shadow.tempfile, "mkstemp", fail_mkstemp)

    result = record_single_child_parent_review_followup_file(
        ledger_path,
        idempotency_key="run-child-1",
        followup=_parent_review_followup(),
        now=datetime(2026, 8, 16, 0, 3, tzinfo=timezone.utc),
    )

    assert result["status"] == "blocked"
    assert result["reason_code"] == "consumption_ledger_write_failed"
    assert json.loads(ledger_path.read_text()) == original


def test_parent_review_followup_file_blocks_lock_acquisition_failure(
    tmp_path,
    monkeypatch,
):
    ledger_path = tmp_path / "execution-grants.json"
    original = _decided_parent_review_ledger()
    ledger_path.write_text(json.dumps(original))

    def fail_flock(*_args):
        raise OSError("lock unavailable")

    monkeypatch.setattr(omc_executor_shadow.fcntl, "flock", fail_flock)

    result = record_single_child_parent_review_followup_file(
        ledger_path,
        idempotency_key="run-child-1",
        followup=_parent_review_followup(),
        now=datetime(2026, 8, 16, 0, 3, tzinfo=timezone.utc),
    )

    assert result["status"] == "blocked"
    assert result["reason_code"] == "consumption_ledger_lock_failed"
    assert json.loads(ledger_path.read_text()) == original


def test_parent_review_operational_sample_records_manual_followup_without_retry(
    tmp_path,
):
    ledger_path = tmp_path / "execution-grants.json"
    ledger_path.write_text(json.dumps(_reserved_ledger()))
    calls = []

    execution = execute_reserved_single_child_grant_file(
        _execution_grant(),
        ledger_path,
        prompt="implement child",
        project_root=tmp_path,
        runner=lambda **kwargs: calls.append(kwargs)
        or {"returncode": 1, "output": "failed"},
        monotonic=_monotonic_values(1.0, 2.0),
        now=lambda: datetime(2026, 8, 16, 0, 1, tzinfo=timezone.utc),
    )
    decision = record_single_child_parent_review_decision_file(
        ledger_path,
        idempotency_key="run-child-1",
        approval=_parent_review_approval(),
        now=datetime(2026, 8, 16, 0, 2, tzinfo=timezone.utc),
    )
    followup = record_single_child_parent_review_followup_file(
        ledger_path,
        idempotency_key="run-child-1",
        followup=_parent_review_followup(),
        now=datetime(2026, 8, 16, 0, 3, tzinfo=timezone.utc),
    )

    persisted = json.loads(ledger_path.read_text())
    assert execution["status"] == "failed"
    assert execution["parent_review"]["status"] == "review_required"
    assert decision["status"] == "recorded"
    assert followup["status"] == "recorded"
    assert len(calls) == 1
    assert persisted["revision"] == 5
    assert persisted["entries"][0]["parent_review_decision"]["decision"] == "hold"
    assert persisted["entries"][0]["parent_review_followup"]["outcome"] == (
        "still_blocked"
    )
    assert persisted["entries"][0]["parent_review_followup"][
        "automatic_retry_performed"
    ] is False
    assert persisted["entries"][0]["parent_review_followup"][
        "automatic_redistribution_performed"
    ] is False


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
def test_single_child_execution_reservation_fails_closed(revision, outcome, reason_code):
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


def test_single_child_execution_reservation_file_reports_post_replace_uncertainty(tmp_path, monkeypatch):
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


def test_reserved_single_child_adapter_executes_once_and_finalizes(tmp_path):
    ledger_path = tmp_path / "execution-grants.json"
    ledger_path.write_text(json.dumps(_reserved_ledger()))
    calls = []

    def runner(**kwargs):
        calls.append(kwargs)
        return {"returncode": 0, "output": "completed"}

    result = execute_reserved_single_child_grant_file(
        _execution_grant(),
        ledger_path,
        prompt="implement child",
        project_root=tmp_path,
        runner=runner,
        monotonic=_monotonic_values(10.0, 14.5),
        now=lambda: datetime(2026, 8, 16, 0, 1, tzinfo=timezone.utc),
    )

    persisted = json.loads(ledger_path.read_text())
    assert result["status"] == "succeeded"
    assert result["reason_code"] == "executor_completed"
    assert "parent_review" not in result
    assert len(calls) == 1
    assert calls[0] == {
        "executor": "codex",
        "prompt": "implement child",
        "project_root": tmp_path,
        "timeout_sec": 120,
    }
    assert persisted["revision"] == 3
    assert persisted["entries"][0]["status"] == "succeeded"
    assert persisted["entries"][0]["attempt_count"] == 1
    assert persisted["entries"][0]["outcome"]["elapsed_sec"] == 4.5
    assert persisted["entries"][0]["outcome"]["output_chars"] == 9


def test_reserved_single_child_adapter_truncates_output_to_grant_budget(tmp_path):
    ledger = _reserved_ledger()
    ledger["entries"][0]["max_output_chars"] = 4
    ledger_path = tmp_path / "execution-grants.json"
    ledger_path.write_text(json.dumps(ledger))
    grant = _execution_grant(max_output_chars=4)

    result = execute_reserved_single_child_grant_file(
        grant,
        ledger_path,
        prompt="implement child",
        project_root=tmp_path,
        runner=lambda **_: {"returncode": 0, "output": "abcdef"},
        monotonic=_monotonic_values(1.0, 2.0),
        now=lambda: datetime(2026, 8, 16, 0, 1, tzinfo=timezone.utc),
    )

    persisted = json.loads(ledger_path.read_text())
    assert result["output"] == "abcd"
    assert result["output_truncated"] is True
    assert persisted["entries"][0]["outcome"]["output_chars"] == 4


def test_reserved_single_child_adapter_terminalizes_non_string_runner_output(tmp_path):
    ledger_path = tmp_path / "execution-grants.json"
    ledger_path.write_text(json.dumps(_reserved_ledger()))

    result = execute_reserved_single_child_grant_file(
        _execution_grant(),
        ledger_path,
        prompt="implement child",
        project_root=tmp_path,
        runner=lambda **_: {"returncode": 0, "output": 123},
        monotonic=_monotonic_values(1.0, 2.0),
        now=lambda: datetime(2026, 8, 16, 0, 1, tzinfo=timezone.utc),
    )

    persisted = json.loads(ledger_path.read_text())
    assert result["status"] == "failed"
    assert result["reason_code"] == "executor_exception"
    assert result["output"] == ""
    assert result["output_truncated"] is False
    assert persisted["entries"][0]["status"] == "failed"


@pytest.mark.parametrize(
    ("runner", "status", "reason_code", "recovery_action"),
    [
        (
            lambda **_: {"returncode": 7, "output": "failed"},
            "failed",
            "executor_failed",
            "inspect_child_failure",
        ),
        (
            lambda **_: (_ for _ in ()).throw(TimeoutError("deadline")),
            "timeout",
            "executor_timeout",
            "inspect_timeout_and_partial_output",
        ),
        (
            lambda **_: (_ for _ in ()).throw(RuntimeError("boom")),
            "failed",
            "executor_exception",
            "inspect_child_failure",
        ),
    ],
)
def test_reserved_single_child_adapter_terminalizes_runner_failures(
    tmp_path,
    runner,
    status,
    reason_code,
    recovery_action,
):
    ledger_path = tmp_path / "execution-grants.json"
    ledger_path.write_text(json.dumps(_reserved_ledger()))

    result = execute_reserved_single_child_grant_file(
        _execution_grant(),
        ledger_path,
        prompt="implement child",
        project_root=tmp_path,
        runner=runner,
        monotonic=_monotonic_values(1.0, 2.0),
        now=lambda: datetime(2026, 8, 16, 0, 1, tzinfo=timezone.utc),
    )

    persisted = json.loads(ledger_path.read_text())
    assert result["status"] == status
    assert result["reason_code"] == reason_code
    assert result["parent_review"] == {
        "status": "review_required",
        "action": "parent_review",
        "execution_status": status,
        "execution_reason_code": reason_code,
        "recovery_reason_code": reason_code,
        "recovery_action": recovery_action,
        "automatic_retry_allowed": False,
        "automatic_redistribution_allowed": False,
    }
    assert persisted["entries"][0]["status"] == status
    assert persisted["entries"][0]["outcome"]["parent_review"] == result["parent_review"]


def test_reserved_single_child_adapter_requires_parent_review_when_finalization_blocks(
    tmp_path,
):
    ledger_path = tmp_path / "execution-grants.json"
    ledger_path.write_text(json.dumps(_reserved_ledger()))
    calls = []
    times = iter(
        [
            datetime(2026, 8, 16, 0, 1, tzinfo=timezone.utc),
            datetime(2026, 8, 15, 23, 59, 59, tzinfo=timezone.utc),
        ]
    )

    result = execute_reserved_single_child_grant_file(
        _execution_grant(),
        ledger_path,
        prompt="implement child",
        project_root=tmp_path,
        runner=lambda **kwargs: calls.append(kwargs)
        or {"returncode": 0, "output": "completed"},
        monotonic=_monotonic_values(1.0, 2.0),
        now=lambda: next(times),
    )

    assert len(calls) == 1
    assert result["status"] == "blocked"
    assert result["reason_code"] == "completion_time_before_reservation"
    assert result["execution_status"] == "succeeded"
    assert result["execution_reason_code"] == "executor_completed"
    assert result["parent_review"] == {
        "status": "review_required",
        "action": "parent_review",
        "execution_status": "succeeded",
        "execution_reason_code": "executor_completed",
        "recovery_reason_code": "completion_time_before_reservation",
        "recovery_action": "reconcile_execution_ledger",
        "automatic_retry_allowed": False,
        "automatic_redistribution_allowed": False,
    }


def test_reserved_single_child_adapter_blocks_before_runner_on_binding_mismatch(
    tmp_path,
):
    ledger_path = tmp_path / "execution-grants.json"
    ledger_path.write_text(json.dumps(_reserved_ledger()))
    calls = []

    result = execute_reserved_single_child_grant_file(
        _execution_grant(scope_hash="other-scope"),
        ledger_path,
        prompt="implement child",
        project_root=tmp_path,
        runner=lambda **kwargs: calls.append(kwargs),
    )

    assert result["status"] == "blocked"
    assert result["reason_code"] == "execution_reservation_mismatch"
    assert "parent_review" not in result
    assert calls == []


def test_reserved_single_child_adapter_fails_closed_on_malformed_ledger(tmp_path):
    ledger_path = tmp_path / "execution-grants.json"
    ledger_path.write_text("[]")
    calls = []

    result = execute_reserved_single_child_grant_file(
        _execution_grant(),
        ledger_path,
        prompt="implement child",
        project_root=tmp_path,
        runner=lambda **kwargs: calls.append(kwargs),
        now=lambda: datetime(2026, 8, 16, 0, 1, tzinfo=timezone.utc),
    )

    assert result["status"] == "blocked"
    assert result["reason_code"] == "consumption_ledger_invalid"
    assert calls == []


def test_reserved_single_child_adapter_blocks_expired_grant_before_runner(tmp_path):
    ledger_path = tmp_path / "execution-grants.json"
    ledger = _reserved_ledger()
    ledger["entries"][0]["approval_expires_at"] = "2026-08-15T23:59:59Z"
    ledger_path.write_text(json.dumps(ledger))
    calls = []

    result = execute_reserved_single_child_grant_file(
        _execution_grant(approval_expires_at="2026-08-15T23:59:59Z"),
        ledger_path,
        prompt="implement child",
        project_root=tmp_path,
        runner=lambda **kwargs: calls.append(kwargs),
        now=lambda: datetime(2026, 8, 16, 0, 1, tzinfo=timezone.utc),
    )

    assert result["status"] == "blocked"
    assert result["reason_code"] == "grant_expired"
    assert calls == []


def test_reserved_single_child_adapter_rechecks_expiry_after_lock(tmp_path, monkeypatch):
    expires_at = "2026-08-16T00:00:30Z"
    ledger = _reserved_ledger()
    ledger["entries"][0]["approval_expires_at"] = expires_at
    ledger_path = tmp_path / "execution-grants.json"
    ledger_path.write_text(json.dumps(ledger))
    grant = _execution_grant(approval_expires_at=expires_at)
    calls = []
    clock = {"current": datetime(2026, 8, 16, 0, 0, tzinfo=timezone.utc)}

    def acquire_after_expiry(*_args):
        clock["current"] = datetime(2026, 8, 16, 0, 1, tzinfo=timezone.utc)

    monkeypatch.setattr(omc_executor_shadow.fcntl, "flock", acquire_after_expiry)

    result = execute_reserved_single_child_grant_file(
        grant,
        ledger_path,
        prompt="implement child",
        project_root=tmp_path,
        runner=lambda **kwargs: calls.append(kwargs),
        monotonic=_monotonic_values(1.0, 2.0),
        now=lambda: clock["current"],
    )

    persisted = json.loads(ledger_path.read_text())
    assert result["status"] == "blocked"
    assert result["reason_code"] == "grant_expired"
    assert persisted["entries"][0]["status"] == "reserved"
    assert calls == []


def test_reserved_single_child_adapter_blocks_second_execution(tmp_path):
    ledger_path = tmp_path / "execution-grants.json"
    ledger_path.write_text(json.dumps(_reserved_ledger()))
    calls = []

    first = execute_reserved_single_child_grant_file(
        _execution_grant(),
        ledger_path,
        prompt="implement child",
        project_root=tmp_path,
        runner=lambda **kwargs: calls.append(kwargs) or {"returncode": 0, "output": "ok"},
        monotonic=_monotonic_values(1.0, 2.0),
        now=lambda: datetime(2026, 8, 16, 0, 1, tzinfo=timezone.utc),
    )
    second = execute_reserved_single_child_grant_file(
        _execution_grant(),
        ledger_path,
        prompt="implement child",
        project_root=tmp_path,
        runner=lambda **kwargs: calls.append(kwargs) or {"returncode": 0, "output": "again"},
    )

    assert first["status"] == "succeeded"
    assert second["status"] == "blocked"
    assert second["reason_code"] == "execution_already_claimed"
    assert len(calls) == 1


def test_reserved_single_child_adapter_does_not_run_after_uncertain_claim(tmp_path, monkeypatch):
    ledger_path = tmp_path / "execution-grants.json"
    ledger_path.write_text(json.dumps(_reserved_ledger()))
    calls = []
    real_fsync = omc_executor_shadow.os.fsync
    fsync_calls = 0

    def fail_claim_directory_fsync(fd):
        nonlocal fsync_calls
        fsync_calls += 1
        if fsync_calls == 2:
            raise OSError("directory fsync failed")
        return real_fsync(fd)

    monkeypatch.setattr(omc_executor_shadow.os, "fsync", fail_claim_directory_fsync)

    result = execute_reserved_single_child_grant_file(
        _execution_grant(),
        ledger_path,
        prompt="implement child",
        project_root=tmp_path,
        runner=lambda **kwargs: calls.append(kwargs),
        now=lambda: datetime(2026, 8, 16, 0, 1, tzinfo=timezone.utc),
    )

    persisted = json.loads(ledger_path.read_text())
    assert result["status"] == "indeterminate"
    assert result["reason_code"] == "execution_claim_durability_unknown"
    assert result["parent_review"]["recovery_action"] == (
        "reconcile_execution_ledger"
    )
    assert result["parent_review"]["execution_status"] == "indeterminate"
    assert result["parent_review"]["execution_reason_code"] == (
        "execution_claim_durability_unknown"
    )
    assert result["parent_review"]["recovery_reason_code"] == (
        "execution_claim_durability_unknown"
    )
    assert persisted["entries"][0]["status"] == "running"
    assert calls == []


def test_reserved_single_child_adapter_propagates_uncertain_terminal_write(tmp_path, monkeypatch):
    ledger_path = tmp_path / "execution-grants.json"
    ledger_path.write_text(json.dumps(_reserved_ledger()))
    real_fsync = omc_executor_shadow.os.fsync
    fsync_calls = 0

    def fail_terminal_directory_fsync(fd):
        nonlocal fsync_calls
        fsync_calls += 1
        if fsync_calls == 4:
            raise OSError("terminal directory fsync failed")
        return real_fsync(fd)

    monkeypatch.setattr(omc_executor_shadow.os, "fsync", fail_terminal_directory_fsync)

    result = execute_reserved_single_child_grant_file(
        _execution_grant(),
        ledger_path,
        prompt="implement child",
        project_root=tmp_path,
        runner=lambda **_: {"returncode": 0, "output": "completed"},
        monotonic=_monotonic_values(1.0, 2.0),
        now=lambda: datetime(2026, 8, 16, 0, 1, tzinfo=timezone.utc),
    )

    persisted = json.loads(ledger_path.read_text())
    assert result["status"] == "indeterminate"
    assert result["reason_code"] == "consumption_ledger_durability_unknown"
    assert result["parent_review"]["recovery_action"] == (
        "reconcile_execution_ledger"
    )
    assert result["parent_review"]["execution_status"] == "succeeded"
    assert result["parent_review"]["execution_reason_code"] == "executor_completed"
    assert result["parent_review"]["recovery_reason_code"] == (
        "consumption_ledger_durability_unknown"
    )
    assert result["execution_status"] == "succeeded"
    assert result["execution_reason_code"] == "executor_completed"
    assert persisted["entries"][0]["status"] == "succeeded"


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
