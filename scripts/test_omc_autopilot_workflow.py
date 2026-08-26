from __future__ import annotations

import hashlib
import json
from pathlib import Path
from unittest.mock import patch

import omc_autopilot
import omc_autopilot_workflow as workflow
import pytest


def _write_task(root: Path, task: dict) -> Path:
    path = root / ".omc" / "tasks" / f"{task['id']}.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(task), encoding="utf-8")
    return path


def _v2_task(root: Path) -> tuple[Path, dict]:
    artifact = root / "result.json"
    artifact.write_text('{"status":"ready"}', encoding="utf-8")
    artifact_hash = hashlib.sha256(artifact.read_bytes()).hexdigest()
    task = {
        "schema_version": "omc-autopilot-task/v2",
        "id": "gated-workflow",
        "title": "Gated workflow",
        "executor": "codex",
        "max_retries": 0,
        "steps": [
            {
                "id": "external",
                "prompt": "run external operation",
                "depends_on": [],
                "approval_gate": {
                    "approval_id": "external-send",
                    "payload_sha256": "a" * 64,
                },
                "completion": {
                    "validator_id": "artifact_sha256",
                    "output_path": "result.json",
                    "expected_sha256": artifact_hash,
                },
            }
        ],
    }
    return _write_task(root, task), task


def test_v2_schema_rejects_unknown_validator():
    task = {
        "schema_version": "omc-autopilot-task/v2",
        "id": "bad",
        "steps": [
            {
                "id": "s1",
                "prompt": "x",
                "depends_on": [],
                "completion": {"validator_id": "run_any_shell"},
            }
        ],
    }

    assert "steps.s1.completion.validator_id_unknown" in workflow.validate_task_spec(task)


def test_unapproved_step_stops_before_provider_call(tmp_path):
    task_file, _task = _v2_task(tmp_path)

    with patch.object(omc_autopilot, "_detect_executor", return_value="codex"), patch.object(
        omc_autopilot, "_run_step"
    ) as run_step:
        code = omc_autopilot.cmd_run(tmp_path, task_file)

    assert code == workflow.APPROVAL_REQUIRED_EXIT_CODE
    run_step.assert_not_called()
    state = json.loads(
        (tmp_path / ".omc/state/autopilot/gated-workflow.json").read_text(encoding="utf-8")
    )
    assert state["status"] == "waiting_step_approval"
    assert state["steps"]["external"]["status"] == "waiting_approval"


def test_approved_step_records_hash_bound_completion_receipt(tmp_path):
    task_file, task = _v2_task(tmp_path)
    task_hash = workflow.task_spec_sha256(task)
    workflow.write_approval_receipt(
        tmp_path,
        task_id="gated-workflow",
        task_spec_sha256=task_hash,
        step_id="external",
        approval_id="external-send",
        payload_sha256="a" * 64,
        approved_at="2026-08-26T00:00:00Z",
    )

    def replace_artifact(_root, _step, **_kwargs):
        artifact = tmp_path / "result.json"
        artifact.unlink()
        artifact.write_text('{"status":"ready"}', encoding="utf-8")
        return 0, "secret_token=do-not-store", None, {"provider_call_count": 1}

    with patch.object(omc_autopilot, "_detect_executor", return_value="codex"), patch.object(
        omc_autopilot,
        "_run_step",
        side_effect=replace_artifact,
    ):
        code = omc_autopilot.cmd_run(tmp_path, task_file)

    assert code == 0
    state = json.loads(
        (tmp_path / ".omc/state/autopilot/gated-workflow.json").read_text(encoding="utf-8")
    )
    completed = state["steps"]["external"]
    assert completed["status"] == "completed"
    assert completed["completion_receipt"]["task_spec_sha256"] == task_hash
    assert completed["completion_receipt"]["artifact_sha256"] == task["steps"][0]["completion"]["expected_sha256"]
    assert completed["attempts"][0]["output_sha256"] == hashlib.sha256(
        b"secret_token=do-not-store"
    ).hexdigest()
    assert "output_tail" not in completed["attempts"][0]
    assert "do-not-store" not in json.dumps(state)


def test_v2_schema_rejects_completion_path_escape_before_provider_call(tmp_path):
    task_file, task = _v2_task(tmp_path)
    task["steps"][0]["completion"]["output_path"] = "../outside.json"
    task_file.write_text(json.dumps(task), encoding="utf-8")

    with patch.object(omc_autopilot, "_detect_executor", return_value="codex"), patch.object(
        omc_autopilot, "_run_step"
    ) as run_step:
        code = omc_autopilot.cmd_run(tmp_path, task_file)

    assert code == 1
    run_step.assert_not_called()


def test_v2_completed_step_without_receipt_is_not_reused(tmp_path):
    task_file, task = _v2_task(tmp_path)
    state_path = tmp_path / ".omc/state/autopilot/gated-workflow.json"
    state_path.parent.mkdir(parents=True, exist_ok=True)
    state_path.write_text(
        json.dumps(
            {
                "task_id": "gated-workflow",
                "task_spec_sha256": workflow.task_spec_sha256(task),
                "status": "completed",
                "steps": {"external": {"status": "completed"}},
            }
        ),
        encoding="utf-8",
    )

    with patch.object(omc_autopilot, "_detect_executor", return_value="codex"), patch.object(
        omc_autopilot, "_run_step"
    ) as run_step:
        code = omc_autopilot.cmd_run(tmp_path, task_file)

    assert code == 1
    run_step.assert_not_called()
    state = json.loads(state_path.read_text(encoding="utf-8"))
    assert state["status"] == "failed"
    assert state["failure_reason"] == "completion_receipt_missing_or_invalid"


def test_approve_rejects_hash_not_bound_to_task(tmp_path):
    task_file, _task = _v2_task(tmp_path)

    code = omc_autopilot.cmd_approve(
        tmp_path,
        task_file=task_file,
        step_id="external",
        payload_sha256="b" * 64,
    )

    assert code == 1
    assert not (
        tmp_path / ".omc/state/autopilot/approvals/gated-workflow/external.json"
    ).exists()


def test_pipeline_task_prompt_contains_exact_plan_output():
    prompt = omc_autopilot._build_pipeline_task_prompt("original request", "PLAN-RECEIPT-123")

    assert "original request" in prompt
    assert "PLAN-RECEIPT-123" in prompt
    assert "PLAN 밖 완료 상태를 만들지 마세요" in prompt


def test_resume_plan_output_requires_matching_hash():
    plan_output = "exact approved plan\nVERDICT: PROCEED"
    plan_state = omc_autopilot._pipeline_plan_output_payload(plan_output)

    assert omc_autopilot._resume_plan_output({"steps": {"plan": plan_state}}) == plan_output

    plan_state["output"] = "tampered plan\nVERDICT: PROCEED"
    with pytest.raises(ValueError, match="resume_plan_output_hash_mismatch"):
        omc_autopilot._resume_plan_output({"steps": {"plan": plan_state}})


def test_v2_timeout_runtime_does_not_persist_partial_output_secret(tmp_path):
    task_file, task = _v2_task(tmp_path)
    task["steps"][0].pop("approval_gate")
    task_file.write_text(json.dumps(task), encoding="utf-8")
    runtime = {
        "provider_call_count": 1,
        "failure_category": "timeout",
        "partial_output": "api_key=TOP-SECRET",
    }

    with patch.object(omc_autopilot, "_detect_executor", return_value="codex"), patch.object(
        omc_autopilot,
        "_run_step",
        return_value=(1, "[ERROR] timeout\napi_key=TOP-SECRET", None, runtime),
    ):
        code = omc_autopilot.cmd_run(tmp_path, task_file)

    assert code == 1
    state = json.loads(
        (tmp_path / ".omc/state/autopilot/gated-workflow.json").read_text(encoding="utf-8")
    )
    assert "TOP-SECRET" not in json.dumps(state)
    diagnostics = state["steps"]["external"]["partial_output_diagnostics"]
    assert diagnostics["output_sha256"] == hashlib.sha256(b"api_key=TOP-SECRET").hexdigest()


@pytest.mark.parametrize(
    "provider_output",
    [
        "Authorization: Bearer TOP-SECRET",
        '{"api_key":"TOP-SECRET"}',
    ],
)
def test_v2_output_diagnostics_never_persist_provider_text(provider_output):
    diagnostics = workflow.output_diagnostics(provider_output)

    assert "TOP-SECRET" not in json.dumps(diagnostics)
    assert "output_tail" not in diagnostics


def test_v2_completion_rejects_unchanged_preexisting_artifact(tmp_path):
    task_file, task = _v2_task(tmp_path)
    task["steps"][0].pop("approval_gate")
    task_file.write_text(json.dumps(task), encoding="utf-8")

    with patch.object(omc_autopilot, "_detect_executor", return_value="codex"), patch.object(
        omc_autopilot,
        "_run_step",
        return_value=(0, "VERDICT: PROCEED", None, {"provider_call_count": 1}),
    ):
        code = omc_autopilot.cmd_run(tmp_path, task_file)

    assert code == 1
    state = json.loads(
        (tmp_path / ".omc/state/autopilot/gated-workflow.json").read_text(encoding="utf-8")
    )
    assert state["steps"]["external"]["completion_error"] == "completion_artifact_unchanged"


def test_stale_v2_provider_execution_requires_manual_reconciliation(tmp_path, monkeypatch):
    task_file, task = _v2_task(tmp_path)
    state_path = tmp_path / ".omc/state/autopilot/gated-workflow.json"
    state_path.parent.mkdir(parents=True, exist_ok=True)
    state_path.write_text(
        json.dumps(
            {
                "task_id": "gated-workflow",
                "task_spec_sha256": workflow.task_spec_sha256(task),
                "status": "running",
                "pid": 12345,
                "steps": {"external": {"status": "running"}},
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(omc_autopilot, "_is_pid_running", lambda _pid: False)

    with patch.object(omc_autopilot, "_detect_executor", return_value="codex"), patch.object(
        omc_autopilot, "_run_step"
    ) as run_step:
        code = omc_autopilot.cmd_run(tmp_path, task_file)

    assert code == 1
    run_step.assert_not_called()
    state = json.loads(state_path.read_text(encoding="utf-8"))
    assert state["status"] == "manual_reconciliation_required"


def test_product_value_six_stage_workflow_uses_receipt_chain_and_exact_approvals(tmp_path):
    stage_ids = ["freeze", "register", "pilot", "confirmatory", "compare", "finalize"]
    approval_steps = {"register", "pilot", "confirmatory"}
    steps = []
    for index, step_id in enumerate(stage_ids):
        artifact = tmp_path / f"{step_id}.json"
        artifact_content = json.dumps({"stage": step_id, "status": "ready"})
        step = {
            "id": step_id,
            "prompt": f"execute {step_id}",
            "depends_on": [] if index == 0 else [stage_ids[index - 1]],
            "completion": {
                "validator_id": "artifact_sha256",
                "output_path": artifact.name,
                "expected_sha256": hashlib.sha256(artifact_content.encode()).hexdigest(),
            },
        }
        if step_id in approval_steps:
            step["approval_gate"] = {
                "approval_id": f"approve-{step_id}",
                "payload_sha256": hashlib.sha256(step_id.encode()).hexdigest(),
            }
        steps.append(step)
    task = {
        "schema_version": "omc-autopilot-task/v2",
        "id": "product-value-six-stage",
        "title": "Product Value acceptance fixture",
        "executor": "codex",
        "max_retries": 0,
        "steps": steps,
    }
    task_file = _write_task(tmp_path, task)
    task_hash = workflow.task_spec_sha256(task)
    for step in steps:
        gate = step.get("approval_gate")
        if gate:
            workflow.write_approval_receipt(
                tmp_path,
                task_id=task["id"],
                task_spec_sha256=task_hash,
                step_id=step["id"],
                approval_id=gate["approval_id"],
                payload_sha256=gate["payload_sha256"],
            )

    def create_stage_artifact(_root, step, **_kwargs):
        step_id = step["id"]
        (tmp_path / f"{step_id}.json").write_text(
            json.dumps({"stage": step_id, "status": "ready"}),
            encoding="utf-8",
        )
        return 0, "VERDICT: PROCEED", None, {"provider_call_count": 1}

    with patch.object(omc_autopilot, "_detect_executor", return_value="codex"), patch.object(
        omc_autopilot,
        "_run_step",
        side_effect=create_stage_artifact,
    ) as run_step:
        code = omc_autopilot.cmd_run(tmp_path, task_file)

    assert code == 0
    assert run_step.call_count == 6
    state = json.loads(
        (tmp_path / ".omc/state/autopilot/product-value-six-stage.json").read_text(encoding="utf-8")
    )
    previous_receipt = None
    for step_id in stage_ids:
        receipt = state["steps"][step_id]["completion_receipt"]
        assert receipt["predecessor_receipts"] == ([] if previous_receipt is None else [previous_receipt])
        previous_receipt = receipt["receipt_sha256"]
