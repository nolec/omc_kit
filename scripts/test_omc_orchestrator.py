import json
import subprocess
import sys
from pathlib import Path

import omc_orchestrator
import omc_executor_shadow
import pytest


def test_simple_request_stays_single_task_and_read_only():
    plan = omc_orchestrator.build_orchestration_plan("README 오타를 고쳐줘")

    assert plan["classification"] == "single_task"
    assert [stage["skill"] for stage in plan["stages"]] == ["omc-task", "omc-review"]
    assert plan["execution_allowed"] is False
    assert all(stage["model_profile"] for stage in plan["stages"])
    assert all(stage["reason_summary"] for stage in plan["stages"])


def test_complex_request_builds_delegation_graph_without_execution():
    plan = omc_orchestrator.build_orchestration_plan("결제 API를 교체하고 프론트와 백엔드 테스트까지 업데이트해줘")

    assert plan["classification"] == "needs_delegation"
    assert [stage["skill"] for stage in plan["stages"]] == [
        "omc-plan",
        "omc-task",
        "omc-critique",
        "omc-review",
    ]
    assert plan["execution_allowed"] is False
    assert plan["invalid_dependency_rate"] == 0
    assert plan["stages"][2]["model_profile"] == "full_default"


def _valid_shadow_request(**overrides):
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


def test_noop_shadow_record_is_simulated_and_never_executable():
    record = omc_executor_shadow.build_noop_shadow_record(_valid_shadow_request())

    assert record["mode"] == "noop_shadow"
    assert record["status"] == "simulated"
    assert record["approval_status"] == "validated"
    assert record["sandbox_status"] == "not_started"
    assert record["usage_status"] == "unavailable"
    assert record["execution_allowed"] is False
    assert record["retry_count"] == 0
    assert record["cost_recorded"] is False


def test_delegation_shadow_adapter_stays_separate_from_observed_record():
    observed = omc_orchestrator.build_delegation_observed_record(
        {
            "id": "delegation-shadow",
            "request": "결제 API와 프론트 테스트를 함께 변경해줘",
            "evidence_status": "fixture",
        }
    )
    shadow = omc_orchestrator.build_delegation_shadow_record(
        _valid_shadow_request()
    )

    assert observed["recommendation_only"] is True
    assert shadow["mode"] == "noop_shadow"
    assert shadow["execution_allowed"] is False


def test_delegation_shadow_adapter_applies_single_child_pilot_gate():
    request = _valid_shadow_request(
        pilot_mode="single_child",
        child_count=1,
        child_status="ready",
        depends_on=[],
        dependency_statuses={},
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

    shadow = omc_orchestrator.build_delegation_shadow_record(request)

    assert shadow["gate_status"] == "allowed"
    assert shadow["shadow_recorded"] is True
    assert shadow["execution_allowed"] is False


@pytest.mark.parametrize(
    ("override", "status", "reason"),
    [
        ({"approval": None}, "blocked", "approval_missing"),
        (
            {"scope_hash": "scope-other"},
            "blocked",
            "scope_mismatch",
        ),
        (
            {
                "approval": {
                    "approval_id": "approval-1",
                    "session_id": "session-1",
                    "child_id": "child-1",
                    "scope_hash": "scope-abc",
                    "expires_at": "2000-01-01T00:00:00Z",
                }
            },
            "blocked",
            "approval_expired",
        ),
        (
            {"executor": "gemini"},
            "rejected",
            "executor_not_allowed",
        ),
        (
            {
                "policy": {
                    "allowed_executors": ["codex"],
                    "timeout_sec": None,
                    "budget_usd": 0.25,
                    "retry_limit": 0,
                }
            },
            "rejected",
            "guard_metadata_invalid",
        ),
        ({"execution_requested": True}, "rejected", "real_execution_disabled"),
    ],
)
def test_noop_shadow_record_rejects_unsafe_requests(override, status, reason):
    record = omc_executor_shadow.build_noop_shadow_record(
        _valid_shadow_request(**override)
    )

    assert record["status"] == status
    assert record["reason_code"] == reason
    assert record["execution_allowed"] is False


def test_plan_rejects_dependency_cycle():
    assert omc_orchestrator.validate_stage_graph(
        [
            {"id": "a", "depends_on": ["b"]},
            {"id": "b", "depends_on": ["a"]},
        ]
    ) == ["cycle"]


def test_cli_dry_run_prints_json_and_never_executes_llm():
    script = Path(__file__).with_name("omc_orchestrator.py")
    result = subprocess.run(
        [sys.executable, str(script), "--request", "결제 API를 교체해줘", "--dry-run"],
        capture_output=True,
        text=True,
        check=True,
    )

    payload = json.loads(result.stdout)
    assert payload["execution_allowed"] is False
    assert payload["mode"] == "dry-run"
    assert payload["stages"]


def test_omc_entrypoint_routes_orchestrate_dry_run():
    result = subprocess.run(
        [
            sys.executable,
            str(Path(__file__).with_name("omc.py")),
            "orchestrate",
            "--request",
            "README 오타를 고쳐줘",
            "--dry-run",
        ],
        capture_output=True,
        text=True,
        check=True,
    )

    assert json.loads(result.stdout)["execution_allowed"] is False


def test_stage_metadata_comes_from_shared_task_routing(monkeypatch):
    calls = []

    def fake_resolve_task_routing(**kwargs):
        calls.append(kwargs)
        return {
            "model_profile": "shared_profile",
            "recommended_policy_profile": "quality_first",
            "policy_reason_summary": "shared policy reason",
            "policy_confidence": "high",
            "recommended_executor": "claude",
            "executor_reason_summary": "shared executor reason",
            "executor_fallback": "codex",
            "user_selection_needed": True,
            "routing_reason_summary": "shared routing reason",
        }

    monkeypatch.setattr(omc_orchestrator, "resolve_task_routing", fake_resolve_task_routing, raising=False)
    plan = omc_orchestrator.build_orchestration_plan("결제 API를 교체해줘")

    assert calls
    assert all(call["request_text"] == "결제 API를 교체해줘" for call in calls)
    assert all(stage["model_profile"] == "shared_profile" for stage in plan["stages"])
    assert all(stage["recommended_policy_profile"] == "quality_first" for stage in plan["stages"])
    assert all(stage["recommended_executor"] == "claude" for stage in plan["stages"])
    assert all(stage["capability_evidence_status"] == "unverified" for stage in plan["stages"])
    assert all(stage["capability_evidence_sample_count"] == 0 for stage in plan["stages"])
    assert all(stage["execution_allowed"] is False for stage in plan["stages"])
    assert plan["user_selection_needed"] is True
    assert plan["execution_allowed"] is False


def test_simple_request_does_not_add_unnecessary_critique(monkeypatch):
    calls = []

    def fake_resolve_task_routing(**kwargs):
        calls.append(kwargs)
        return {
            "model_profile": "mini_default",
            "recommended_policy_profile": "cost_saver",
            "policy_reason_summary": "fixed low-risk task",
            "policy_confidence": "high",
            "recommended_executor": "codex",
            "executor_reason_summary": "local task",
            "executor_fallback": "codex",
            "user_selection_needed": False,
            "recommended_next_skill": "omc-review",
            "auto_execution_allowed": True,
        }

    monkeypatch.setattr(omc_orchestrator, "resolve_task_routing", fake_resolve_task_routing)
    plan = omc_orchestrator.build_orchestration_plan("README 오타를 고쳐줘")

    assert [stage["id"] for stage in plan["stages"]] == ["task", "review"]
    assert all(call["scope_fixed"] is True for call in calls)
    assert plan["user_selection_needed"] is False


def test_low_confidence_and_selection_are_preserved(monkeypatch):
    def fake_resolve_task_routing(**_kwargs):
        return {
            "model_profile": "mini_high",
            "recommended_policy_profile": "balanced",
            "policy_reason_summary": "ambiguous request",
            "policy_confidence": "low",
            "recommended_executor": "codex",
            "executor_reason_summary": "fallback executor",
            "executor_fallback": "codex",
            "user_selection_needed": True,
            "recommended_next_skill": "omc-plan",
            "auto_execution_allowed": False,
        }

    monkeypatch.setattr(omc_orchestrator, "resolve_task_routing", fake_resolve_task_routing)
    plan = omc_orchestrator.build_orchestration_plan("결제 API를 교체해줘")

    assert all(stage["policy_confidence"] == "low" for stage in plan["stages"])
    assert plan["user_selection_needed"] is True
    assert plan["execution_allowed"] is False


def test_delegation_evidence_pilot_prompts_stay_aligned():
    evidence = json.loads(
        (Path(__file__).parents[1] / "templates/shared_tasks/complex-delegation-evidence-pilot.json").read_text()
    )

    assert [
        omc_orchestrator._classify_request(step["prompt"])[0]
        for step in evidence["steps"]
    ] == ["needs_plan", "needs_plan", "needs_delegation"]


def test_simple_execution_requires_explicit_opt_in_and_builds_existing_task_spec():
    plan = omc_orchestrator.build_orchestration_plan("README 오타를 고쳐줘")

    assert omc_orchestrator.can_auto_execute_simple(plan, user_opt_in=False) is False
    assert omc_orchestrator.can_auto_execute_simple(plan, user_opt_in=True) is True
    task = omc_orchestrator.build_simple_autopilot_task(plan)
    assert task["executor"] == "auto"
    assert [step["id"] for step in task["steps"]] == ["task", "review"]
    assert task["steps"][1]["depends_on"] == ["task"]
    assert "VERDICT: PROCEED" in task["steps"][0]["prompt"]
    assert "VERDICT: BLOCK" in task["steps"][0]["prompt"]
    assert "VERDICT: APPROVE" in task["steps"][1]["prompt"]
    assert "VERDICT: REVISE" in task["steps"][1]["prompt"]


def test_simple_execution_gate_rejects_complex_plan():
    plan = omc_orchestrator.build_orchestration_plan("결제 API를 교체하고 프론트와 백엔드 테스트까지 업데이트해줘")

    assert omc_orchestrator.can_auto_execute_simple(plan, user_opt_in=True) is False
    assert omc_orchestrator.build_simple_autopilot_task(plan) is None


def test_simple_execution_second_gate_rejects_scope_risks():
    plan = omc_orchestrator.build_orchestration_plan("README 오타를 고쳐줘")

    assert omc_orchestrator.can_auto_execute_simple(
        plan, user_opt_in=True, sensitive_paths=["src/payments"]
    ) is False
    assert omc_orchestrator.can_auto_execute_simple(plan, user_opt_in=True, new_files=True) is False
    assert omc_orchestrator.can_auto_execute_simple(plan, user_opt_in=True, api_change=True) is False
    assert omc_orchestrator.can_auto_execute_simple(plan, user_opt_in=True, deletion=True) is False
    assert omc_orchestrator.can_auto_execute_simple(plan, user_opt_in=True, dirty_scope_conflict=True) is False


def test_execute_simple_uses_existing_autopilot_runner(monkeypatch, tmp_path):
    plan = omc_orchestrator.build_orchestration_plan("README 오타를 고쳐줘")
    calls = []

    class Result:
        returncode = 0
        stdout = ""

    def fake_run(command, **kwargs):
        calls.append((command, kwargs))
        return Result()

    monkeypatch.setattr(omc_orchestrator.subprocess, "run", fake_run)
    assert omc_orchestrator.run_simple_autopilot(plan, target=tmp_path) == 0
    autopilot_call = next(call for call in calls if "omc_autopilot.py" in " ".join(call[0]))
    assert autopilot_call[0][-2:] == ["--task", autopilot_call[0][-1]]


def test_cli_scope_signals_block_dirty_or_risky_target(monkeypatch, tmp_path):
    def fake_git_status(*_args, **_kwargs):
        return type("Result", (), {"stdout": " M src/app.py\n", "returncode": 0})()

    monkeypatch.setattr(omc_orchestrator.subprocess, "run", fake_git_status)
    signals = omc_orchestrator.detect_simple_scope_risks(tmp_path, "README 오타를 고쳐줘")

    assert signals["dirty_scope_conflict"] is True
    assert signals["new_files"] is False
    assert omc_orchestrator.can_auto_execute_simple(
        omc_orchestrator.build_orchestration_plan("README 오타를 고쳐줘"),
        user_opt_in=True,
        **signals,
    ) is False


def test_cli_scope_signals_block_when_git_status_fails(monkeypatch, tmp_path):
    result = type("Result", (), {"stdout": "", "returncode": 1})()
    monkeypatch.setattr(omc_orchestrator.subprocess, "run", lambda *_args, **_kwargs: result)

    signals = omc_orchestrator.detect_simple_scope_risks(tmp_path, "README 오타를 고쳐줘")

    assert signals["git_status_unavailable"] is True
    assert signals["dirty_scope_conflict"] is True


def test_decomposition_result_has_valid_child_task_contract():
    plan = omc_orchestrator.build_orchestration_plan(
        "결제 API를 교체하고 프론트와 백엔드 테스트까지 업데이트해줘"
    )
    result = omc_orchestrator.build_decomposition_result(plan)

    assert result["classification"] == "needs_delegation"
    assert result["execution_allowed"] is False
    assert result["decomposition_confidence"] == "high"
    assert omc_orchestrator.validate_decomposition_result(result) == []
    assert all(
        {"id", "goal", "scope", "depends_on", "task_kind", "risk", "expected_output", "handoff_contract"}
        <= set(child)
        for child in result["children"]
    )
    assert [child["id"] for child in result["children"]] == [
        "child-backend",
        "child-frontend",
        "child-verification",
        "child-integration-review",
    ]
    assert result["children"][0]["scope"] == ["backend"]
    assert result["children"][1]["scope"] == ["frontend"]
    assert result["children"][2]["scope"] == ["verification"]
    assert result["recommendation_only"] is True
    assert result["evidence_status"] == "unverified"
    assert result["execution_allowed"] is False
    assert isinstance(result["user_selection_needed"], bool)
    for child in result["children"]:
        assert child["recommended_executor"] in {"codex", "claude", "gemini"}
        assert child["executor_reason_code"]
        assert child["executor_reason_summary"]
        assert child["executor_fallback"] in {"codex", "claude", "gemini"}
        assert child["executor_fallback"] != child["recommended_executor"]
        assert child["recommendation_only"] is True
        assert child["evidence_status"] == "unverified"
        assert child["recommended_policy_profile"] in {"cost_saver", "balanced", "quality_first"}
        assert child["policy_confidence"] in {"low", "high"}


def test_decomposition_result_rejects_dependency_cycle():
    errors = omc_orchestrator.validate_decomposition_result(
        {
            "classification": "needs_delegation",
            "children": [
                {"id": "a", "depends_on": ["b"]},
                {"id": "b", "depends_on": ["a"]},
            ],
        }
    )

    assert "dependency_cycle" in errors


def test_decomposition_fixture_has_ten_evaluable_cases():
    fixture = json.loads(
        (Path(__file__).parent / "fixtures/complex_decomposition_cases.json").read_text()
    )

    assert len(fixture["cases"]) == 10
    for case in fixture["cases"]:
        plan = omc_orchestrator.build_orchestration_plan(case["request"])
        result = omc_orchestrator.build_decomposition_result(plan)
        assert result["classification"] == case["expected_classification"]
        assert len(result["children"]) == case["expected_child_count"]
        assert [child["id"] for child in result["children"]] == case["expected_child_ids"]
        assert omc_orchestrator.validate_decomposition_result(result) == []


def test_decomposition_validator_rejects_malformed_child_contract_fields():
    errors = omc_orchestrator.validate_decomposition_result(
        {
            "classification": "needs_delegation",
            "decomposition_confidence": "medium",
            "children": [
                {
                    "id": "",
                    "goal": "",
                    "scope": "backend",
                    "depends_on": [],
                    "task_kind": "unknown",
                    "risk": "critical",
                    "expected_output": "",
                    "handoff_contract": {"required_fields": []},
                }
            ],
            "execution_allowed": False,
        }
    )

    assert {
        "invalid_child_id",
        "invalid_child_goal",
        "invalid_child_scope",
        "invalid_task_kind",
        "invalid_risk",
        "invalid_expected_output",
        "invalid_handoff_contract",
    } <= set(errors)


def test_decomposition_validator_preserves_single_task_and_checks_delegation_confidence():
    single_task = {
        "classification": "single_task",
        "decomposition_confidence": "high",
        "children": [],
        "execution_allowed": False,
    }
    assert omc_orchestrator.validate_decomposition_result(single_task) == []

    low_with_children = {
        "classification": "needs_delegation",
        "decomposition_confidence": "low",
        "children": [{"id": "child-backend"}],
        "execution_allowed": False,
    }
    assert "confidence_children_mismatch" in omc_orchestrator.validate_decomposition_result(
        low_with_children
    )


def test_decomposition_validator_allows_aggregate_integration_scope():
    plan = omc_orchestrator.build_orchestration_plan(
        "결제 API를 교체하고 프론트와 백엔드 테스트까지 업데이트해줘"
    )
    result = omc_orchestrator.build_decomposition_result(plan)

    assert omc_orchestrator.validate_decomposition_result(result) == []


def test_decomposition_validation_fixture_covers_malformed_contracts():
    fixture = json.loads(
        (Path(__file__).parent / "fixtures/decomposition_validation_cases.json").read_text()
    )

    for case in fixture["cases"]:
        errors = omc_orchestrator.validate_decomposition_result(case["result"])
        assert set(case["expected_errors"]) <= set(errors)


def test_decomposition_domain_boundary_fixture_matches_expected_domains():
    fixture = json.loads(
        (Path(__file__).parent / "fixtures/decomposition_domain_boundary_cases.json").read_text()
    )

    for case in fixture["cases"]:
        plan = omc_orchestrator.build_orchestration_plan(case["request"])
        result = omc_orchestrator.build_decomposition_result(plan)
        assert result["classification"] == case["expected_classification"]
        assert result["decomposition_confidence"] == case["expected_confidence"]
        assert [child["id"] for child in result["children"]] == case["expected_child_ids"]
        assert [child["scope"] for child in result["children"]] == case["expected_scopes"]
        assert result.get("unresolved_questions", []) == case["expected_unresolved_questions"]


def test_executor_recommendation_fixture_is_recommendation_only():
    fixture = json.loads(
        (Path(__file__).parent / "fixtures/executor_recommendation_cases.json").read_text()
    )

    for case in fixture["cases"]:
        plan = omc_orchestrator.build_orchestration_plan(case["request"])
        result = omc_orchestrator.build_decomposition_result(plan)
        assert [child["id"] for child in result["children"]] == case["expected_child_ids"]
        assert result["recommendation_only"] is True
        assert result["evidence_status"] == "unverified"
        assert result["execution_allowed"] is False
        assert all(child["capability_evidence_status"] == "unverified" for child in result["children"])
        assert all(child["execution_allowed"] is False for child in result["children"])
    assert all(
            child["recommendation_only"] is True
            and child["evidence_status"] == "unverified"
            and child["recommended_executor"] in {"codex", "claude", "gemini"}
            and child["executor_fallback"] != child["recommended_executor"]
            for child in result["children"]
        )


def test_decomposition_validator_enforces_parent_recommendation_safety_contract():
    plan = omc_orchestrator.build_orchestration_plan(
        "결제 API를 교체하고 프론트 테스트를 업데이트해줘"
    )

    result = omc_orchestrator.build_decomposition_result(plan)

    missing_parent_fields = dict(result)
    missing_parent_fields.pop("recommendation_only")
    missing_parent_fields.pop("evidence_status")
    missing_parent_fields.pop("user_selection_needed")
    errors = omc_orchestrator.validate_decomposition_result(missing_parent_fields)

    assert "missing_recommendation_only" in errors
    assert "missing_evidence_status" in errors
    assert "missing_user_selection_needed" in errors

    invalid_parent_fields = dict(result)
    invalid_parent_fields["recommendation_only"] = False
    invalid_parent_fields["evidence_status"] = "verified"
    invalid_parent_fields["user_selection_needed"] = "no"
    errors = omc_orchestrator.validate_decomposition_result(invalid_parent_fields)

    assert "recommendation_must_be_only" in errors
    assert "invalid_evidence_status" in errors
    assert "invalid_user_selection_needed" in errors


def test_capability_evidence_normalization_preserves_observation_metadata():
    evidence = {
        "executor": "codex",
        "task_kind": "implementation",
        "domain": "backend",
        "policy_profile": "balanced",
        "source_type": "observed",
        "observed_at": "2026-07-12T22:00:00+09:00",
        "sample_count": 3,
        "environment_fingerprint": "local-codex-v1",
        "success_count": 2,
        "failure_count": 1,
        "cost_estimate": 0.12,
        "availability_attempts": 3,
        "availability_successes": 3,
    }

    normalized = omc_orchestrator.normalize_capability_evidence(evidence)

    assert normalized["evidence_status"] == "observed"
    assert normalized["source_type"] == "observed"
    assert normalized["sample_count"] == 3
    assert normalized["environment_fingerprint"] == "local-codex-v1"
    assert normalized["reason_codes"] == []
    assert normalized["execution_allowed"] is False


def test_capability_candidate_contract_separates_quality_final_success_and_cost():
    normalized = omc_orchestrator.normalize_capability_evidence(
        {
            "executor": "codex",
            "task_kind": "implementation",
            "domain": "backend",
            "policy_profile": "balanced",
            "source_type": "observed",
            "observed_at": "2026-07-13T00:00:00+09:00",
            "sample_count": 2,
            "environment_fingerprint": "local-codex-v1",
            "success_count": 2,
            "failure_count": 0,
        }
    )

    assert normalized["candidate_status"] == "observed_candidate_only"
    assert normalized["quality_success"] == "missing"
    assert normalized["final_success"] == "unknown"
    assert normalized["cost_status"] == "unknown"
    assert normalized["cost_warning"] is False
    assert normalized["execution_allowed"] is False
    assert normalized["approval_required"] is True
    assert normalized["approval_id"] is None
    assert normalized["approved_executor"] is None
    assert normalized["next_action"] == "review_executor_candidate"


def test_capability_candidate_contract_uses_verified_quality_and_known_cost_without_execution():
    normalized = omc_orchestrator.normalize_capability_evidence(
        {
            "executor": "claude",
            "task_kind": "review",
            "domain": "frontend",
            "policy_profile": "mini_high",
            "source_type": "observed",
            "observed_at": "2026-07-13T00:00:00+09:00",
            "sample_count": 1,
            "environment_fingerprint": "local-claude-v1",
            "quality_success": "verified",
            "final_success": "success",
            "cost_estimate": 0.07,
        }
    )

    assert normalized["candidate_status"] == "observed_candidate_only"
    assert normalized["quality_success"] == "verified"
    assert normalized["final_success"] == "success"
    assert normalized["cost_status"] == "known"
    assert normalized["cost_warning"] is False
    assert normalized["next_action"] == "review_executor_candidate"
    assert normalized["execution_allowed"] is False


def test_capability_candidate_contract_binds_approval_to_canonical_scope():
    scope = {
        "task_kind": "implementation",
        "domain": "backend",
        "sensitive_paths": ["src/b.py", "src/a.py"],
        "policy_profile": "balanced",
    }
    fingerprint = omc_orchestrator.build_capability_scope_fingerprint(scope)
    normalized = omc_orchestrator.normalize_capability_evidence(
        {
            "executor": "gemini",
            "task_kind": "implementation",
            "domain": "backend",
            "policy_profile": "balanced",
            "sensitive_paths": ["src/a.py", "src/b.py"],
            "source_type": "observed",
            "observed_at": "2026-07-13T00:00:00+09:00",
            "sample_count": 1,
            "environment_fingerprint": "local-gemini-v1",
            "approval_scope": scope,
        }
    )

    assert normalized["approved_scope_fingerprint"] == fingerprint
    assert normalized["approval_id"] is None
    assert normalized["approved_at"] is None
    assert normalized["expires_at"] is None
    assert normalized["execution_allowed"] is False
    changed_scope = {**scope, "sensitive_paths": ["src/a.py", "src/c.py"]}
    assert not omc_orchestrator.capability_scope_fingerprint_matches(
        changed_scope, normalized["approved_scope_fingerprint"]
    )


def test_capability_candidate_contract_maps_data_states_to_single_next_action():
    insufficient = omc_orchestrator.normalize_capability_evidence(
        {"executor": "codex", "source_type": "observed", "sample_count": 0}
    )
    rejected = omc_orchestrator.normalize_capability_evidence(
        {"executor": "unknown", "source_type": "observed", "sample_count": 1}
    )

    assert insufficient["candidate_status"] == "insufficient_data"
    assert insufficient["next_action"] == "collect_capability_evidence"
    assert rejected["candidate_status"] == "blocked_data_quality"
    assert rejected["next_action"] == "repair_capability_evidence"


def test_executor_eligibility_candidate_fixture_preserves_observation_only_contract():
    cases = json.loads(
        (Path(__file__).parent / "fixtures/executor_eligibility_candidate_cases.json").read_text()
    )

    for case in cases:
        result = omc_orchestrator.normalize_capability_evidence(case["evidence"])
        for field, expected in case["expected"].items():
            assert result[field] == expected, case["id"]
        assert result["execution_allowed"] is False


def test_observed_run_aggregation_exposes_candidate_only_contract():
    evidence = omc_orchestrator.build_capability_evidence_from_runs(
        [
            {
                "executor": "codex",
                "task_kind": "implementation",
                "domain": "backend",
                "policy_profile": "balanced",
                "environment_fingerprint": "local-codex-v1",
                "finished_at": "2026-07-13T00:00:00+09:00",
                "status": "completed",
            }
        ]
    )

    assert evidence[0]["candidate_status"] == "observed_candidate_only"
    assert evidence[0]["quality_success"] == "missing"
    assert evidence[0]["final_success"] == "unknown"
    assert evidence[0]["cost_status"] == "unknown"
    assert evidence[0]["next_action"] == "review_executor_candidate"
    assert evidence[0]["execution_allowed"] is False


def test_in_progress_run_aggregation_exposes_insufficient_candidate_contract():
    evidence = omc_orchestrator.build_capability_evidence_from_runs(
        [
            {
                "executor": "codex",
                "task_kind": "implementation",
                "domain": "backend",
                "policy_profile": "balanced",
                "sensitive_paths": ["src/secure.py", "src/api.py"],
                "environment_fingerprint": "local-codex-v1",
                "started_at": "2026-07-13T00:00:00+09:00",
                "status": "in_progress",
            }
        ]
    )

    assert evidence[0]["candidate_status"] == "insufficient_data"
    assert evidence[0]["quality_success"] == "missing"
    assert evidence[0]["final_success"] == "unknown"
    assert evidence[0]["cost_status"] == "unknown"
    assert evidence[0]["approval_required"] is True
    assert evidence[0]["next_action"] == "collect_capability_evidence"
    assert evidence[0]["approved_scope_fingerprint"] == omc_orchestrator.build_capability_scope_fingerprint(
        {
            "task_kind": "implementation",
            "domain": "backend",
            "sensitive_paths": ["src/secure.py", "src/api.py"],
            "policy_profile": "balanced",
        }
    )
    assert evidence[0]["execution_allowed"] is False


def test_non_finite_cost_is_not_known():
    for cost_estimate in (float("nan"), float("inf"), float("-inf")):
        normalized = omc_orchestrator.normalize_capability_evidence(
            {
                "executor": "codex",
                "task_kind": "implementation",
                "domain": "backend",
                "policy_profile": "balanced",
                "source_type": "observed",
                "observed_at": "2026-07-13T00:00:00+09:00",
                "sample_count": 1,
                "environment_fingerprint": "local-codex-v1",
                "quality_success": "verified",
                "final_success": "success",
                "cost_estimate": cost_estimate,
            }
        )

        assert normalized["cost_status"] == "unknown"
        assert normalized["next_action"] == "compare_executor_cost"


def test_child_handoff_is_proposed_only_when_scope_and_dependencies_are_valid():
    parent_scope = {
        "task_kind": "implementation",
        "domain": "backend",
        "sensitive_paths": ["src/api.py", "src/secure.py"],
        "policy_profile": "balanced",
    }
    child = {
        "child_id": "child-api",
        "parent_id": "root-task",
        "task_kind": "implementation",
        "domain": "backend",
        "sensitive_paths": ["src/api.py"],
        "policy_profile": "balanced",
        "depends_on": ["child-prep"],
    }

    result = omc_orchestrator.build_delegation_handoff(
        parent_scope, child, {"child-prep": "completed"}
    )

    assert result["handoff_status"] == "proposed"
    assert result["dependency_status"] == "satisfied"
    assert result["scope_relation"] == "subset"
    assert result["approval_required"] is True
    assert result["execution_allowed"] is False
    assert result["next_action"] == "review_child_handoff"


def test_child_handoff_blocks_unfinished_dependency_and_scope_mismatch():
    parent_scope = {
        "task_kind": "implementation",
        "domain": "backend",
        "sensitive_paths": ["src/api.py"],
        "policy_profile": "balanced",
    }
    child = {
        "child_id": "child-api",
        "parent_id": "root-task",
        "task_kind": "implementation",
        "domain": "frontend",
        "sensitive_paths": ["src/ui.tsx"],
        "policy_profile": "quality_first",
        "depends_on": ["child-prep"],
    }

    result = omc_orchestrator.build_delegation_handoff(
        parent_scope, child, {"child-prep": "pending"}
    )

    assert result["handoff_status"] == "blocked_dependency"
    assert result["dependency_status"] == "blocked"
    assert result["blocked_by"] == ["child-prep"]
    assert result["scope_relation"] == "mismatch"
    assert result["next_action"] == "resolve_dependency"
    assert result["execution_allowed"] is False


def test_delegation_graph_rejects_missing_dependency_and_cycle():
    missing = omc_orchestrator.validate_delegation_graph(
        [{"child_id": "child-a", "depends_on": ["missing"]}]
    )
    cycle = omc_orchestrator.validate_delegation_graph(
        [
            {"child_id": "child-a", "depends_on": ["child-b"]},
            {"child_id": "child-b", "depends_on": ["child-a"]},
        ]
    )

    assert "missing_dependency" in missing
    assert "cycle" in cycle
    graph = omc_orchestrator.build_delegation_graph(
        [
            {"child_id": "child-b", "depends_on": ["child-a"]},
            {"child_id": "child-a", "depends_on": []},
        ]
    )
    assert graph["errors"] == []
    assert graph["topological_order"] == ["child-a", "child-b"]


def test_delegation_fixture_preserves_handoff_gate_contract():
    cases = json.loads(
        (Path(__file__).parent / "fixtures/executor_delegation_cases.json").read_text()
    )

    for case in cases:
        result = omc_orchestrator.build_delegation_handoff(
            case["parent_scope"], case["child"], case["child_statuses"]
        )
        assert result["handoff_status"] == case["expected_status"]
        assert result["next_action"] == case["expected_next_action"]
        assert result["approval_required"] is True
        assert result["execution_allowed"] is False


def test_delegation_handoff_rejects_malformed_metadata():
    parent_scope = {
        "task_kind": "implementation",
        "domain": "backend",
        "sensitive_paths": ["src/api.py"],
        "policy_profile": "balanced",
    }
    result = omc_orchestrator.build_delegation_handoff(
        parent_scope,
        {
            "child_id": "child-api",
            "parent_id": None,
            "task_kind": "implementation",
            "domain": "backend",
            "sensitive_paths": ["src/api.py"],
            "policy_profile": "balanced",
            "depends_on": "child-prep",
        },
        {},
    )

    assert result["handoff_status"] == "rejected"
    assert result["next_action"] == "repair_delegation_contract"
    assert result["execution_allowed"] is False


def test_child_decision_normalizes_valid_handoff_without_execution():
    handoff = {
        "handoff_status": "proposed",
        "child_id": "child-api",
        "blocked_by": [],
        "next_action": "review_child_handoff",
        "execution_allowed": False,
    }

    result = omc_orchestrator.build_child_decision(
        handoff, attempt_count=0, retry_budget=1
    )

    assert result["decision"] == "ready"
    assert result["next_action"] == "review_child_handoff"
    assert result["child_id"] == "child-api"
    assert result["attempt_count"] == 0
    assert result["retry_budget"] == 1
    assert result["execution_allowed"] is False
    assert result["decision_id"]


def test_child_decision_preserves_blocked_handoff_reason():
    result = omc_orchestrator.build_child_decision(
        {
            "handoff_status": "blocked_dependency",
            "child_id": "child-api",
            "blocked_by": ["child-prep"],
            "next_action": "resolve_dependency",
            "execution_allowed": False,
        },
        attempt_count=0,
        retry_budget=1,
    )

    assert result["decision"] == "blocked"
    assert result["next_action"] == "resolve_dependency"
    assert result["blocked_by"] == ["child-prep"]
    assert result["execution_allowed"] is False


def test_child_decision_holds_when_retry_budget_is_exhausted():
    result = omc_orchestrator.build_child_decision(
        {
            "handoff_status": "proposed",
            "child_id": "child-api",
            "blocked_by": [],
            "next_action": "review_child_handoff",
            "execution_allowed": False,
        },
        attempt_count=2,
        retry_budget=1,
        failure_class="execution_failure",
    )

    assert result["decision"] == "hold"
    assert result["decision_reason"] == "retry_budget_exhausted"
    assert result["next_action"] == "hold_retry_budget"
    assert result["execution_allowed"] is False


def test_child_decision_rejects_invalid_decision_metadata():
    result = omc_orchestrator.build_child_decision(
        {
            "handoff_status": "proposed",
            "child_id": "child-api",
            "blocked_by": [],
            "next_action": "review_child_handoff",
            "execution_allowed": False,
        },
        attempt_count=True,
        retry_budget=1,
    )

    assert result["decision"] == "rejected"
    assert result["decision_reason"] == "decision_metadata_invalid"
    assert result["next_action"] == "repair_delegation_contract"


def test_child_decision_rejects_untrusted_next_action():
    result = omc_orchestrator.build_child_decision(
        {
            "handoff_status": "proposed",
            "child_id": "child-api",
            "blocked_by": [],
            "next_action": "arbitrary_action",
            "execution_allowed": False,
        }
    )

    assert result["decision"] == "rejected"
    assert result["decision_reason"] == "decision_metadata_invalid"
    assert result["next_action"] == "repair_delegation_contract"


def test_child_decision_holds_on_retry_budget_without_failure_class():
    result = omc_orchestrator.build_child_decision(
        {
            "handoff_status": "proposed",
            "child_id": "child-api",
            "blocked_by": [],
            "next_action": "review_child_handoff",
            "execution_allowed": False,
        },
        attempt_count=2,
        retry_budget=1,
    )

    assert result["decision"] == "hold"
    assert result["decision_reason"] == "retry_budget_exhausted"
    assert result["next_action"] == "hold_retry_budget"


def test_delegation_execution_order_prefers_dependencies_then_domain():
    result = omc_orchestrator.build_delegation_execution_order(
        [
            {"child_id": "backend", "domain": "backend", "depends_on": ["frontend"]},
            {"child_id": "frontend", "domain": "frontend", "depends_on": []},
            {"child_id": "backend-b", "domain": "backend", "depends_on": []},
            {"child_id": "backend-a", "domain": "backend", "depends_on": []},
        ]
    )

    assert result["order_status"] == "ready"
    assert result["errors"] == []
    assert [item["child_id"] for item in result["ordered_children"]] == [
        "backend-a",
        "backend-b",
        "frontend",
        "backend",
    ]
    assert all(item["order_status"] == "ready" for item in result["ordered_children"])
    assert result["execution_allowed"] is False


@pytest.mark.parametrize(
    "children",
    [
        [{"child_id": "child-a", "domain": "unknown", "depends_on": []}],
        [
            {"child_id": "child-a", "domain": "backend", "depends_on": ["child-b"]},
            {"child_id": "child-b", "domain": "frontend", "depends_on": ["child-a"]},
        ],
    ],
)
def test_delegation_execution_order_blocks_unknown_domain_and_cycle(children):
    result = omc_orchestrator.build_delegation_execution_order(children)

    assert result["order_status"] == "blocked"
    assert result["errors"]
    assert all(item["order_status"] == "blocked" for item in result["ordered_children"])
    assert result["execution_allowed"] is False


def test_delegation_execution_order_blocks_unhashable_domain():
    result = omc_orchestrator.build_delegation_execution_order(
        [{"child_id": "child-a", "domain": ["backend"], "depends_on": []}]
    )

    assert result["order_status"] == "blocked"
    assert "invalid_domain" in result["errors"]
    assert result["execution_allowed"] is False


@pytest.mark.parametrize(
    ("failure_class", "attempt_count", "max_attempts", "retry_budget", "expected_action", "expected_reason"),
    [
        ("timeout", 0, 1, 1, "retry_same_child", "retryable_failure"),
        ("timeout", 1, 1, 1, "parent_review", "retry_exhausted"),
        ("scope_mismatch", 0, 1, 1, "repair_scope", "scope_mismatch"),
        ("dependency_failed", 0, 1, 1, "hold_dependents", "dependency_failed"),
        ("unknown_failure", 0, 1, 1, "parent_review", "unknown_failure_class"),
    ],
)
def test_child_recovery_decision_is_bounded_and_non_executing(
    failure_class,
    attempt_count,
    max_attempts,
    retry_budget,
    expected_action,
    expected_reason,
):
    result = omc_orchestrator.build_child_recovery_decision(
        child_id="child-api",
        failure_class=failure_class,
        attempt_count=attempt_count,
        max_attempts=max_attempts,
        retry_budget=retry_budget,
        idempotency_key="run-child-api",
        scope_bound=True,
    )

    assert result["action"] == expected_action
    assert result["reason_code"] == expected_reason
    assert result["execution_allowed"] is False
    if expected_action == "retry_same_child":
        assert result["attempt_after"] == 1
        assert result["retry_budget_remaining"] == 0


def test_child_recovery_decision_rejects_unbound_idempotency():
    result = omc_orchestrator.build_child_recovery_decision(
        child_id="child-api",
        failure_class="timeout",
        attempt_count=0,
        max_attempts=1,
        retry_budget=1,
        idempotency_key="",
        scope_bound=False,
    )

    assert result["action"] == "parent_review"
    assert result["reason_code"] == "recovery_metadata_invalid"
    assert result["execution_allowed"] is False


def test_child_decision_exposes_order_and_recovery_surface():
    order = {"order_index": 2, "order_status": "ready", "blocked_by": []}
    recovery = {
        "action": "retry_same_child",
        "reason_code": "retryable_failure",
        "recommendation_only": True,
        "execution_allowed": False,
    }
    result = omc_orchestrator.build_child_decision(
        {
            "handoff_status": "proposed",
            "child_id": "child-api",
            "blocked_by": [],
            "next_action": "review_child_handoff",
            "execution_allowed": False,
        },
        execution_order=order,
        recovery_action=recovery,
    )

    assert result["execution_order"] == order
    assert result["recovery_action"] == recovery
    assert result["execution_allowed"] is False


@pytest.mark.parametrize(
    ("execution_order", "recovery_action"),
    [
        ({"order_status": "ready"}, None),
        (
            None,
            {
                "action": "parent_review",
                "reason_code": "unknown_failure_class",
                "execution_allowed": False,
            },
        ),
    ],
)
def test_child_decision_rejects_incomplete_recommendation_surfaces(
    execution_order, recovery_action
):
    result = omc_orchestrator.build_child_decision(
        {
            "handoff_status": "proposed",
            "child_id": "child-api",
            "blocked_by": [],
            "next_action": "review_child_handoff",
            "execution_allowed": False,
        },
        execution_order=execution_order,
        recovery_action=recovery_action,
    )

    assert result["decision"] == "rejected"
    assert result["decision_reason"] == "decision_metadata_invalid"
    assert result["execution_allowed"] is False


def test_child_decision_rejects_ready_order_with_blocked_dependencies():
    result = omc_orchestrator.build_child_decision(
        {
            "handoff_status": "proposed",
            "child_id": "child-api",
            "blocked_by": [],
            "next_action": "review_child_handoff",
            "execution_allowed": False,
        },
        execution_order={
            "order_status": "ready",
            "order_index": 0,
            "blocked_by": ["child-db"],
        },
    )

    assert result["decision"] == "rejected"
    assert result["decision_reason"] == "decision_metadata_invalid"


def test_delegation_observed_exposes_order_and_recovery_surface():
    result = omc_orchestrator.build_delegation_observed_record(
        {
            "id": "surface-case",
            "evidence_status": "fixture",
            "parent_scope": {
                "task_kind": "implementation",
                "domain": "backend",
                "sensitive_paths": ["src/api.py"],
                "policy_profile": "balanced",
            },
            "child": {
                "child_id": "child-api",
                "parent_id": "parent-1",
                "task_kind": "implementation",
                "domain": "backend",
                "sensitive_paths": ["src/api.py"],
                "policy_profile": "balanced",
                "depends_on": [],
            },
            "child_statuses": {},
            "execution_order": {
                "order_index": 1,
                "order_status": "ready",
                "blocked_by": [],
            },
            "recovery_action": {
                "action": "parent_review",
                "reason_code": "unknown_failure_class",
                "recommendation_only": True,
                "execution_allowed": False,
            },
        }
    )

    decision = result["child_decisions"][0]
    assert decision["execution_order"]["order_index"] == 1
    assert decision["recovery_action"]["action"] == "parent_review"
    assert decision["execution_allowed"] is False


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("execution_order", {"order_status": "ready"}),
        ("recovery_action", {"action": "parent_review"}),
    ],
)
def test_delegation_observed_rejects_malformed_recommendation_surface(field, value):
    case = {
        "id": "malformed-surface-case",
        "evidence_status": "fixture",
        "parent_scope": {
            "task_kind": "implementation",
            "domain": "backend",
            "sensitive_paths": ["src/api.py"],
            "policy_profile": "balanced",
        },
        "child": {
            "child_id": "child-api",
            "parent_id": "parent-1",
            "task_kind": "implementation",
            "domain": "backend",
            "sensitive_paths": ["src/api.py"],
            "policy_profile": "balanced",
            "depends_on": [],
        },
        "child_statuses": {},
        field: value,
    }

    result = omc_orchestrator.build_delegation_observed_record(case)

    assert result["evidence_status"] == "rejected"
    assert result["rejection_reason"] == f"invalid_{field}"
    assert result["child_decisions"] == []


def test_delegation_graph_rejects_non_list_dependencies():
    errors = omc_orchestrator.validate_delegation_graph(
        [{"child_id": "child-a", "depends_on": "child-b"}]
    )

    assert "invalid_dependencies" in errors


def test_operational_delegation_cases_preserve_request_and_handoff_acceptance():
    cases = json.loads(
        (Path(__file__).parent / "fixtures/executor_delegation_operational_cases.json").read_text()
    )

    for case in cases:
        if "request" in case:
            plan = omc_orchestrator.build_orchestration_plan(case["request"])
            assert plan["classification"] == case["expected_classification"]
            result = omc_orchestrator.build_decomposition_result(plan)
            assert len(result["children"]) >= case["expected_min_children"]
            assert result["execution_allowed"] is False
            assert all(child["recommendation_only"] is True for child in result["children"])
        else:
            result = omc_orchestrator.build_delegation_handoff(
                case["parent_scope"], case["child"], case["child_statuses"]
            )
            assert result["handoff_status"] == case["expected_status"]
            assert result["next_action"] == case["expected_next_action"]
            assert result["execution_allowed"] is False


def test_build_delegation_observed_record_is_deterministic_and_read_only():
    record = omc_orchestrator.build_delegation_observed_record(
        {
            "id": "complex-payment-request",
            "request": "결제 API를 교체하고 프론트와 백엔드 테스트까지 업데이트해줘",
            "evidence_status": "fixture",
        }
    )

    assert record["source_type"] == "delegation_observed"
    assert record["case_id"] == "complex-payment-request"
    assert record["classification"] == "needs_delegation"
    assert record["evidence_status"] == "fixture"
    assert record["recommendation_only"] is True
    assert record["execution_allowed"] is False
    assert len(record["children"]) >= 2


def test_build_delegation_observed_record_preserves_blocked_and_scope_edges():
    cases = json.loads(
        (Path(__file__).parent / "fixtures/executor_delegation_operational_cases.json").read_text()
    )

    for case in cases[1:]:
        record = omc_orchestrator.build_delegation_observed_record(
            {**case, "evidence_status": "fixture"}
        )
        handoff = record["handoffs"][0]
        assert handoff["handoff_status"] == case["expected_status"]
        assert handoff["next_action"] == case["expected_next_action"]
        assert handoff["execution_allowed"] is False


def test_build_delegation_observed_record_surfaces_child_decisions():
    cases = json.loads(
        (Path(__file__).parent / "fixtures/executor_delegation_operational_cases.json").read_text()
    )

    for case in cases[1:]:
        record = omc_orchestrator.build_delegation_observed_record(
            {**case, "evidence_status": "fixture"}
        )
        assert len(record["child_decisions"]) == len(record["handoffs"])
        decision = record["child_decisions"][0]
        assert decision["decision"] in {"blocked", "hold", "rejected"}
        assert decision["decision_id"]
        assert decision["execution_allowed"] is False
        assert decision["recommendation_only"] is True

    rejected = omc_orchestrator.build_delegation_observed_record(
        {
            "id": "malformed-handoff",
            "parent_scope": {},
            "child": None,
            "child_statuses": {},
            "evidence_status": "fixture",
        }
    )
    assert rejected["child_decisions"] == []
    assert rejected["evidence_status"] == "rejected"


def test_build_delegation_observed_record_rejects_execution_permission():
    record = omc_orchestrator.build_delegation_observed_record(
        {
            "id": "scope-mismatch-request",
            "parent_scope": {
                "task_kind": "implementation",
                "domain": "backend",
                "sensitive_paths": ["src/api.py"],
                "policy_profile": "balanced",
            },
            "child": {
                "child_id": "child-api",
                "parent_id": "root-task",
                "task_kind": "implementation",
                "domain": "backend",
                "sensitive_paths": ["src/api.py"],
                "policy_profile": "balanced",
                "depends_on": [],
            },
            "child_statuses": {},
            "execution_allowed": True,
            "evidence_status": "observed",
        }
    )

    assert record["evidence_status"] == "rejected"
    assert record["rejection_reason"] == "execution_permission_forbidden"
    assert record["execution_allowed"] is False


def test_build_delegation_observed_record_rejects_unknown_evidence_status():
    record = omc_orchestrator.build_delegation_observed_record(
        {
            "request": "결제 API를 교체하고 프론트와 백엔드 테스트까지 업데이트해줘",
            "evidence_status": "synthetic",
        }
    )

    assert record["evidence_status"] == "rejected"
    assert record["rejection_reason"] == "invalid_evidence_status"


def test_build_delegation_observed_record_falls_back_to_unknown_case_id():
    record = omc_orchestrator.build_delegation_observed_record(
        {
            "request": "결제 API를 교체하고 프론트와 백엔드 테스트까지 업데이트해줘",
            "evidence_status": "fixture",
        }
    )

    assert record["case_id"] == "unknown_case"



def test_capability_evidence_normalization_rejects_partial_or_malformed_records():
    partial = omc_orchestrator.normalize_capability_evidence(
        {"executor": "codex", "source_type": "fixture", "sample_count": 0}
    )
    malformed = omc_orchestrator.normalize_capability_evidence(
        {
            "executor": "unknown",
            "source_type": "observed",
            "observed_at": "not-a-timestamp",
            "sample_count": -1,
        }
    )

    assert partial["evidence_status"] == "insufficient"
    assert "missing_observed_at" in partial["reason_codes"]
    assert partial["execution_allowed"] is False
    assert malformed["evidence_status"] == "rejected"
    assert "invalid_executor" in malformed["reason_codes"]
    assert "invalid_observed_at" in malformed["reason_codes"]
    assert "invalid_sample_count" in malformed["reason_codes"]


def test_capability_evidence_fixture_never_grants_execution():
    cases = json.loads(
        (Path(__file__).parent / "fixtures/executor_capability_evidence_cases.json").read_text()
    )

    assert cases
    for case in cases:
        result = omc_orchestrator.normalize_capability_evidence(case["evidence"])
        assert result["source_type"] == case["expected_source_type"]
        assert result["evidence_status"] == case["expected_status"]
        assert result["execution_allowed"] is False


def test_observed_runs_build_capability_evidence_by_executor_context():
    runs = [
        {
            "executor": "codex",
            "task_kind": "implementation",
            "domain": "backend",
            "policy_profile": "balanced",
            "environment_fingerprint": "local-codex-v1",
            "finished_at": "2026-07-12T21:00:00+09:00",
            "status": "completed",
        },
        {
            "executor": "codex",
            "task_kind": "implementation",
            "domain": "backend",
            "policy_profile": "balanced",
            "environment_fingerprint": "local-codex-v1",
            "finished_at": "2026-07-12T21:10:00+09:00",
            "status": "failed",
        },
    ]

    evidence = omc_orchestrator.build_capability_evidence_from_runs(runs)

    assert len(evidence) == 1
    assert evidence[0]["executor"] == "codex"
    assert evidence[0]["sample_count"] == 2
    assert evidence[0]["success_count"] == 1
    assert evidence[0]["failure_count"] == 1
    assert evidence[0]["evidence_status"] == "observed"
    assert evidence[0]["execution_allowed"] is False


def test_observed_runs_exclude_in_progress_records_from_failure_counts():
    runs = [
        {
            "executor": "codex",
            "task_kind": "implementation",
            "domain": "backend",
            "policy_profile": "balanced",
            "environment_fingerprint": "local-codex-v1",
            "finished_at": "2026-07-12T21:00:00+09:00",
            "status": "completed",
        },
        {
            "executor": "codex",
            "task_kind": "implementation",
            "domain": "backend",
            "policy_profile": "balanced",
            "environment_fingerprint": "local-codex-v1",
            "started_at": "2026-07-12T21:30:00+09:00",
            "status": "running",
        },
    ]

    evidence = omc_orchestrator.build_capability_evidence_from_runs(runs)

    assert evidence[0]["sample_count"] == 1
    assert evidence[0]["success_count"] == 1
    assert evidence[0]["failure_count"] == 0
    assert evidence[0]["in_progress_count"] == 1


def test_observed_runs_do_not_let_stale_samples_poison_fresh_aggregate():
    runs = [
        {
            "executor": "codex",
            "task_kind": "implementation",
            "domain": "backend",
            "policy_profile": "balanced",
            "environment_fingerprint": "local-codex-v1",
            "finished_at": "2026-07-01T10:00:00+09:00",
            "status": "completed",
        },
        {
            "executor": "codex",
            "task_kind": "implementation",
            "domain": "backend",
            "policy_profile": "balanced",
            "environment_fingerprint": "local-codex-v1",
            "finished_at": "2026-07-12T21:00:00+09:00",
            "status": "completed",
        },
    ]

    evidence = omc_orchestrator.build_capability_evidence_from_runs(
        runs,
        now="2026-07-12T22:00:00+09:00",
        freshness_hours=24,
    )

    assert evidence[0]["evidence_status"] == "observed"
    assert evidence[0]["fresh_sample_count"] == 1
    assert evidence[0]["stale_sample_count"] == 1
    assert "stale" in evidence[0]["reason_codes"]


def test_observed_runs_use_current_environment_samples_for_status():
    runs = [
        {
            "executor": "codex",
            "task_kind": "implementation",
            "domain": "backend",
            "policy_profile": "balanced",
            "environment_fingerprint": "old-codex-v1",
            "finished_at": "2026-07-12T21:00:00+09:00",
            "status": "completed",
        },
        {
            "executor": "codex",
            "task_kind": "implementation",
            "domain": "backend",
            "policy_profile": "balanced",
            "environment_fingerprint": "local-codex-v2",
            "finished_at": "2026-07-12T21:30:00+09:00",
            "status": "completed",
        },
    ]

    evidence = omc_orchestrator.build_capability_evidence_from_runs(
        runs,
        current_environment_fingerprint="local-codex-v2",
        now="2026-07-12T22:00:00+09:00",
        freshness_hours=24,
    )

    assert evidence[0]["evidence_status"] == "observed"
    assert evidence[0]["current_environment_sample_count"] == 1
    assert evidence[0]["mismatched_environment_sample_count"] == 1
    assert "environment_mismatch" in evidence[0]["reason_codes"]


def test_observed_runs_reject_invalid_freshness_hours():
    with pytest.raises(ValueError, match="freshness_hours"):
        omc_orchestrator.build_capability_evidence_from_runs([], freshness_hours=-1)


def test_observed_evidence_rejects_timezone_naive_timestamp():
    evidence = omc_orchestrator.normalize_capability_evidence(
        {
            "executor": "codex",
            "source_type": "observed",
            "observed_at": "2026-07-12T22:00:00",
            "sample_count": 1,
            "environment_fingerprint": "local-codex-v1",
        }
    )

    assert evidence["evidence_status"] == "rejected"
    assert "invalid_observed_at_timezone" in evidence["reason_codes"]


def test_observed_runs_mark_stale_and_environment_mismatch_without_execution():
    runs = [
        {
            "executor": "claude",
            "task_kind": "review",
            "domain": "verification",
            "policy_profile": "quality_first",
            "environment_fingerprint": "old-claude-v1",
            "finished_at": "2026-07-01T10:00:00+09:00",
            "status": "completed",
        }
    ]

    evidence = omc_orchestrator.build_capability_evidence_from_runs(
        runs,
        current_environment_fingerprint="local-claude-v2",
        now="2026-07-12T22:00:00+09:00",
        freshness_hours=24,
    )

    assert evidence[0]["evidence_status"] == "environment_mismatch"
    assert "environment_mismatch" in evidence[0]["reason_codes"]
    assert "stale" in evidence[0]["reason_codes"]
    assert evidence[0]["execution_allowed"] is False


def test_observed_runs_reject_missing_runtime_metadata():
    evidence = omc_orchestrator.build_capability_evidence_from_runs(
        [{"executor": "gemini", "status": "completed"}]
    )

    assert evidence[0]["evidence_status"] == "insufficient"
    assert "missing_observed_at" in evidence[0]["reason_codes"]
    assert "missing_environment_fingerprint" in evidence[0]["reason_codes"]
    assert evidence[0]["execution_allowed"] is False


def test_observed_capability_fixture_covers_freshness_boundaries():
    cases = json.loads(
        (Path(__file__).parent / "fixtures/executor_capability_observed_cases.json").read_text()
    )

    for case in cases:
        evidence = omc_orchestrator.build_capability_evidence_from_runs(
            case["runs"], **case.get("options", {})
        )
        assert len(evidence) == 1
        assert evidence[0]["evidence_status"] == case["expected_status"]
        assert evidence[0]["execution_allowed"] is False


def test_capability_evidence_loader_reads_only_run_result_files(tmp_path):
    run_dir = tmp_path / ".omc" / "runs" / "run-1"
    run_dir.mkdir(parents=True)
    (run_dir / "result.json").write_text(
        json.dumps(
            {
                "executor": "codex",
                "task_kind": "implementation",
                "domain": "backend",
                "policy_profile": "balanced",
                "environment_fingerprint": "local-codex-v1",
                "finished_at": "2026-07-12T21:00:00+09:00",
                "status": "completed",
            }
        ),
        encoding="utf-8",
    )
    (run_dir / "not-result.json").write_text("{}", encoding="utf-8")

    evidence = omc_orchestrator.load_capability_evidence_from_runs(tmp_path)

    assert len(evidence) == 1
    assert evidence[0]["executor"] == "codex"
    assert evidence[0]["source_type"] == "observed"
    assert evidence[0]["execution_allowed"] is False


def test_capability_evidence_loader_flattens_step_observations(tmp_path):
    run_dir = tmp_path / ".omc" / "runs" / "run-multi-step"
    run_dir.mkdir(parents=True)
    (run_dir / "result.json").write_text(
        json.dumps(
            {
                "status": "completed",
                "capability_observations": [
                    {
                        "step_id": "plan",
                        "executor": "codex",
                        "task_kind": "planning",
                        "domain": "orchestration",
                        "policy_profile": "balanced",
                        "environment_fingerprint": "local-codex-v1",
                        "observed_at": "2026-07-15T01:00:01+09:00",
                        "status": "completed",
                    },
                    {
                        "step_id": "review",
                        "executor": "claude",
                        "task_kind": "review",
                        "domain": "verification",
                        "policy_profile": "quality_first",
                        "environment_fingerprint": "local-claude-v1",
                        "observed_at": "2026-07-15T01:00:05+09:00",
                        "status": "completed",
                    },
                ],
            }
        ),
        encoding="utf-8",
    )

    evidence = omc_orchestrator.load_capability_evidence_from_runs(tmp_path)

    assert {(item["executor"], item["task_kind"]) for item in evidence} == {
        ("codex", "planning"),
        ("claude", "review"),
    }
    assert all(item["evidence_status"] == "observed" for item in evidence)
    assert all(item["execution_allowed"] is False for item in evidence)


def test_capability_evidence_loader_reports_rejected_run_metadata(tmp_path):
    run_dir = tmp_path / ".omc" / "runs" / "run-1"
    run_dir.mkdir(parents=True)
    (run_dir / "result.json").write_text("not-json", encoding="utf-8")
    (run_dir / "other.json").write_text("{}", encoding="utf-8")

    report = omc_orchestrator.load_capability_evidence_report_from_runs(tmp_path)

    assert report["evidence"] == []
    assert report["rejected_run_count"] == 1
    assert report["rejected_run_reasons"] == {"invalid_json": 1}
