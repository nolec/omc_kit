import hashlib
import json
import subprocess
import sys
from copy import deepcopy
from pathlib import Path

import omc_sequence


def _canonical_hash(value):
    payload = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def _child_grant(child_id, approval_id, idempotency_key, scope_hash):
    return {
        "mode": "single_child_execution_grant",
        "status": "ready",
        "reason_code": "execution_grant_ready",
        "execution_allowed": True,
        "parent_id": "parent-1",
        "child_id": child_id,
        "executor": "codex",
        "scope_hash": scope_hash,
        "approval_id": approval_id,
        "session_id": "session-1",
        "approval_expires_at": "2099-01-01T00:00:00Z",
        "idempotency_key": idempotency_key,
        "max_attempts": 1,
        "max_total_elapsed_sec": 30.0,
        "max_output_chars": 1000,
        "fallback_action": "parent_review",
    }


def _sequence_request(**overrides):
    children = [
        {"child_id": "child-1", "depends_on": []},
        {"child_id": "child-2", "depends_on": ["child-1"]},
    ]
    ordered_child_ids = ["child-1", "child-2"]
    child_grants = [
        _child_grant("child-1", "approval-1", "run-child-1", "scope-1"),
        _child_grant("child-2", "approval-2", "run-child-2", "scope-2"),
    ]
    prompts = {"child-1": "implement first", "child-2": "implement second"}
    request = {
        "sequence_id": "sequence-1",
        "execution_mode": "two_child_sequential_opt_in",
        "execution_requested": True,
        "children": children,
        "ordered_child_ids": ordered_child_ids,
        "child_grants": child_grants,
        "child_prompts": prompts,
        "aggregate_budget": {
            "max_external_calls": 2,
            "max_total_elapsed_sec": 60.0,
            "max_output_chars": 2000,
        },
        "sequence_approval": {
            "approval_id": "sequence-approval-1",
            "sequence_id": "sequence-1",
            "operator_confirmed": True,
            "expires_at": "2099-01-01T00:00:00Z",
            "graph_sha256": _canonical_hash(children),
            "execution_order_sha256": _canonical_hash(ordered_child_ids),
            "child_grant_sha256s": [_canonical_hash(grant) for grant in child_grants],
            "prompt_sha256s": {
                child_id: _canonical_hash(prompts[child_id])
                for child_id in ordered_child_ids
            },
        },
    }
    request.update(overrides)
    return request


def test_operational_acceptance_contract_is_preregistered_and_bounded():
    contract = omc_sequence.OPERATIONAL_ACCEPTANCE_CONTRACT

    assert contract["schema_version"] == 1
    assert contract["mode"] == "two_child_sequential_opt_in"
    assert contract["required_metrics"] == [
        "external_call_count",
        "total_elapsed_sec",
        "total_output_chars",
        "usage_durability",
    ]
    assert {scenario["id"] for scenario in contract["scenarios"]} == {
        "completed",
        "first_child_failed",
        "second_child_failed",
        "expired_before_first_child",
        "expired_before_second_child",
        "duplicate_claim",
    }
    assert {
        scenario["id"]: scenario["expected_reason_codes"]
        for scenario in contract["scenarios"]
    } == {
        "completed": ["sequence_completed"],
        "first_child_failed": ["child_not_succeeded"],
        "second_child_failed": ["child_not_succeeded"],
        "expired_before_first_child": ["sequence_grant_expired"],
        "expired_before_second_child": ["sequence_grant_expired"],
        "duplicate_claim": ["sequence_already_started"],
    }
    assert {
        scenario["id"]: scenario["expected_external_calls"]
        for scenario in contract["scenarios"]
    } == {
        "completed": [2],
        "first_child_failed": [0, 1],
        "second_child_failed": [1, 2],
        "expired_before_first_child": [0],
        "expired_before_second_child": [1],
        "duplicate_claim": [0],
    }
    assert contract["invariants"] == {
        "automatic_retry_allowed": False,
        "automatic_redistribution_allowed": False,
        "automatic_fallback_allowed": False,
        "automatic_resume_allowed": False,
    }


def test_run_sequence_request_executes_two_children_in_order(tmp_path):
    calls = []

    result = omc_sequence.run_sequence_request(
        _sequence_request(),
        target=tmp_path,
        runner=lambda **kwargs: calls.append(kwargs["prompt"])
        or {"returncode": 0, "output": "ok"},
    )

    assert result["status"] == "completed"
    assert result["acceptance_status"] == "passed"
    assert result["acceptance_scenario"] == "completed"
    assert result["external_call_count"] == 2
    assert result["ledger_paths"] == {
        "sequence": ".omc/executor/sequences/sequence-1.json",
        "single_child": ".omc/executor/single-child.json",
    }
    assert len(result["acceptance_contract_sha256"]) == 64
    assert calls == ["implement first", "implement second"]


def test_run_sequence_request_fails_acceptance_when_completed_metrics_are_missing(
    tmp_path, monkeypatch
):
    monkeypatch.setattr(
        omc_sequence,
        "execute_two_child_sequence_grant_file",
        lambda *args, **kwargs: {
            "status": "completed",
            "reason_code": "sequence_completed",
        },
    )

    result = omc_sequence.run_sequence_request(_sequence_request(), target=tmp_path)

    assert result["status"] == "completed"
    assert result["acceptance_status"] == "failed"
    assert result["acceptance_scenario"] == "completed"
    assert "required_metric_missing:external_call_count" in result["acceptance_violations"]


def test_run_sequence_request_fails_acceptance_when_completed_children_conflict(
    tmp_path, monkeypatch
):
    monkeypatch.setattr(
        omc_sequence,
        "execute_two_child_sequence_grant_file",
        lambda *args, **kwargs: {
            "status": "completed",
            "reason_code": "sequence_completed",
            "external_call_count": 2,
            "total_elapsed_sec": 0.5,
            "total_output_chars": 4,
            "completed_child_ids": ["child-1", "child-2"],
            "pending_child_ids": [],
            "failed_child_id": None,
            "children": [
                {
                    "child_id": "child-1",
                    "status": "succeeded",
                    "usage_durability": "durable",
                },
                {
                    "child_id": "child-2",
                    "status": "failed",
                    "usage_durability": "durable",
                },
            ],
        },
    )

    result = omc_sequence.run_sequence_request(_sequence_request(), target=tmp_path)

    assert result["acceptance_status"] == "failed"
    assert "completed_child_evidence_mismatch" in result["acceptance_violations"]


def test_run_sequence_request_fails_acceptance_when_completed_reason_conflicts(
    tmp_path, monkeypatch
):
    monkeypatch.setattr(
        omc_sequence,
        "execute_two_child_sequence_grant_file",
        lambda *args, **kwargs: {
            "status": "completed",
            "reason_code": "child_not_succeeded",
            "external_call_count": 2,
            "total_elapsed_sec": 0.5,
            "total_output_chars": 4,
            "completed_child_ids": ["child-1", "child-2"],
            "pending_child_ids": [],
            "failed_child_id": None,
            "children": [
                {
                    "child_id": "child-1",
                    "status": "succeeded",
                    "usage_durability": "durable",
                },
                {
                    "child_id": "child-2",
                    "status": "succeeded",
                    "usage_durability": "durable",
                },
            ],
        },
    )

    result = omc_sequence.run_sequence_request(_sequence_request(), target=tmp_path)

    assert result["acceptance_status"] == "failed"
    assert "scenario_reason_code_mismatch" in result["acceptance_violations"]


def test_run_sequence_request_accepts_expiry_before_second_child(
    tmp_path, monkeypatch
):
    monkeypatch.setattr(
        omc_sequence,
        "execute_two_child_sequence_grant_file",
        lambda *args, **kwargs: {
            "status": "review_required",
            "reason_code": "sequence_grant_expired",
            "external_call_count": 1,
            "total_elapsed_sec": 0.5,
            "total_output_chars": 2,
            "completed_child_ids": ["child-1"],
            "pending_child_ids": ["child-2"],
            "failed_child_id": None,
            "children": [
                {
                    "child_id": "child-1",
                    "status": "succeeded",
                    "usage_durability": "durable",
                },
                {
                    "child_id": "child-2",
                    "status": "not_started",
                    "usage_durability": "not_applicable",
                },
            ],
        },
    )

    result = omc_sequence.run_sequence_request(_sequence_request(), target=tmp_path)

    assert result["acceptance_status"] == "passed"
    assert result["acceptance_scenario"] == "expired_before_second_child"
    assert result["external_call_count"] == 1


def test_run_sequence_request_accepts_expiry_before_first_child(
    tmp_path, monkeypatch
):
    monkeypatch.setattr(
        omc_sequence,
        "execute_two_child_sequence_grant_file",
        lambda *args, **kwargs: {
            "status": "blocked",
            "reason_code": "sequence_grant_expired",
        },
    )

    result = omc_sequence.run_sequence_request(_sequence_request(), target=tmp_path)

    assert result["acceptance_status"] == "passed"
    assert result["acceptance_scenario"] == "expired_before_first_child"
    assert result["external_call_count"] == 0


def test_main_fails_closed_when_completed_result_violates_acceptance(
    tmp_path, monkeypatch, capsys
):
    request_file = tmp_path / "request.json"
    request_file.write_text(json.dumps(_sequence_request()), encoding="utf-8")
    monkeypatch.setattr(
        omc_sequence,
        "execute_two_child_sequence_grant_file",
        lambda *args, **kwargs: {
            "status": "completed",
            "reason_code": "sequence_completed",
        },
    )
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "omc_sequence.py",
            "--request-file",
            str(request_file),
            "--target",
            str(tmp_path),
        ],
    )

    assert omc_sequence.main() == 2
    assert json.loads(capsys.readouterr().out)["acceptance_status"] == "failed"


def test_run_sequence_request_stops_after_first_failure(tmp_path):
    calls = []

    result = omc_sequence.run_sequence_request(
        _sequence_request(),
        target=tmp_path,
        runner=lambda **kwargs: calls.append(kwargs["prompt"])
        or {"returncode": 1, "output": "failed"},
    )

    assert result["status"] == "review_required"
    assert result["acceptance_status"] == "passed"
    assert result["acceptance_scenario"] == "first_child_failed"
    assert result["external_call_count"] == 1
    assert result["pending_child_ids"] == ["child-2"]
    assert calls == ["implement first"]


def test_run_sequence_request_accepts_first_child_pre_call_block(tmp_path):
    request = _sequence_request()
    first_calls = []
    second_calls = []
    first = omc_sequence.run_sequence_request(
        request,
        target=tmp_path,
        runner=lambda **kwargs: first_calls.append(kwargs["prompt"])
        or {"returncode": 0, "output": "ok"},
    )
    retry = deepcopy(request)
    retry["sequence_id"] = "sequence-2"
    retry["sequence_approval"]["sequence_id"] = "sequence-2"

    second = omc_sequence.run_sequence_request(
        retry,
        target=tmp_path,
        runner=lambda **kwargs: second_calls.append(kwargs["prompt"])
        or {"returncode": 0, "output": "ok"},
    )

    assert first["status"] == "completed"
    assert second["status"] == "review_required"
    assert second["acceptance_status"] == "passed"
    assert second["acceptance_scenario"] == "first_child_failed"
    assert second["external_call_count"] == 0
    assert second["failed_child_id"] == "child-1"
    assert first_calls == ["implement first", "implement second"]
    assert second_calls == []


def test_run_sequence_request_accepts_second_child_failure_as_bounded_review(
    tmp_path,
):
    calls = []

    def runner(**kwargs):
        calls.append(kwargs["prompt"])
        return {
            "returncode": 0 if len(calls) == 1 else 1,
            "output": "ok" if len(calls) == 1 else "failed",
        }

    result = omc_sequence.run_sequence_request(
        _sequence_request(),
        target=tmp_path,
        runner=runner,
    )

    assert result["status"] == "review_required"
    assert result["acceptance_status"] == "passed"
    assert result["acceptance_scenario"] == "second_child_failed"
    assert result["external_call_count"] == 2
    assert result["completed_child_ids"] == ["child-1"]
    assert result["failed_child_id"] == "child-2"
    assert calls == ["implement first", "implement second"]


def test_run_sequence_request_accepts_second_child_pre_call_block(
    tmp_path, monkeypatch
):
    monkeypatch.setattr(
        omc_sequence,
        "execute_two_child_sequence_grant_file",
        lambda *args, **kwargs: {
            "status": "review_required",
            "reason_code": "child_not_succeeded",
            "external_call_count": 1,
            "total_elapsed_sec": 0.5,
            "total_output_chars": 2,
            "completed_child_ids": ["child-1"],
            "pending_child_ids": [],
            "failed_child_id": "child-2",
            "children": [
                {
                    "child_id": "child-1",
                    "status": "succeeded",
                    "usage_durability": "durable",
                },
                {
                    "child_id": "child-2",
                    "status": "blocked",
                    "usage_durability": "not_applicable",
                },
            ],
        },
    )

    result = omc_sequence.run_sequence_request(_sequence_request(), target=tmp_path)

    assert result["acceptance_status"] == "passed"
    assert result["acceptance_scenario"] == "second_child_failed"
    assert result["external_call_count"] == 1


def test_run_sequence_request_rejects_duplicate_without_external_call(tmp_path):
    request = _sequence_request()
    first_calls = []
    second_calls = []
    first = omc_sequence.run_sequence_request(
        request,
        target=tmp_path,
        runner=lambda **kwargs: first_calls.append(kwargs["prompt"])
        or {"returncode": 0, "output": "ok"},
    )
    second = omc_sequence.run_sequence_request(
        request,
        target=tmp_path,
        runner=lambda **kwargs: second_calls.append(kwargs["prompt"])
        or {"returncode": 0, "output": "ok"},
    )

    assert first["status"] == "completed"
    assert second["status"] in {"blocked", "indeterminate"}
    assert second["acceptance_status"] == "passed"
    assert second["acceptance_scenario"] == "duplicate_claim"
    assert second["external_call_count"] == 0
    assert first_calls == ["implement first", "implement second"]
    assert second_calls == []


def test_run_sequence_request_rejects_unsafe_sequence_id(tmp_path):
    calls = []
    request = _sequence_request(sequence_id="../escape")
    request["sequence_approval"]["sequence_id"] = "../escape"

    result = omc_sequence.run_sequence_request(
        request,
        target=tmp_path,
        runner=lambda **kwargs: calls.append(kwargs) or {"returncode": 0, "output": "ok"},
    )

    assert result["status"] == "blocked"
    assert result["reason_code"] == "sequence_id_invalid"
    assert result["external_call_count"] == 0
    assert calls == []
    assert not (tmp_path.parent / "escape").exists()


def test_run_sequence_request_rejects_symlinked_runtime_directory(tmp_path):
    outside = tmp_path.parent / "outside-ledgers"
    outside.mkdir()
    (tmp_path / ".omc").mkdir()
    (tmp_path / ".omc" / "executor").symlink_to(outside, target_is_directory=True)

    result = omc_sequence.run_sequence_request(
        _sequence_request(),
        target=tmp_path,
        runner=lambda **kwargs: {"returncode": 0, "output": "ok"},
    )

    assert result["status"] == "blocked"
    assert result["reason_code"] == "sequence_ledger_path_unsafe"
    assert result["external_call_count"] == 0
    assert list(outside.iterdir()) == []


def test_omc_entrypoint_exposes_sequence_command_and_fails_closed(tmp_path):
    request_file = tmp_path / "request.json"
    request_file.write_text(json.dumps({"execution_requested": False}), encoding="utf-8")

    result = subprocess.run(
        [
            sys.executable,
            str(Path(__file__).with_name("omc.py")),
            "execute-sequence",
            "--request-file",
            str(request_file),
            "--target",
            str(tmp_path),
        ],
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 2
    payload = json.loads(result.stdout)
    assert payload["status"] == "blocked"
    assert payload["reason_code"] == "sequence_opt_in_missing"
    assert payload["external_call_count"] == 0


def test_setup_deploys_sequence_adapter():
    import install

    assert "omc_sequence.py" in install._deployed_script_names(Path(__file__).parents[1])
