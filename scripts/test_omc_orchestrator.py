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
