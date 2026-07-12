import json
import subprocess
import sys
from pathlib import Path

import omc_orchestrator


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
        (Path(__file__).parents[1] / ".omc/tasks/complex-delegation-evidence-pilot.json").read_text()
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
