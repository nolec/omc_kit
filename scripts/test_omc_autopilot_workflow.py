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


@pytest.mark.parametrize("missing_metadata", [False, True])
def test_recovery_guidance_public_cli_preserves_block_and_no_provider_calls(tmp_path, missing_metadata):
    import os
    import subprocess
    import sys

    task_file, task = _v2_task(tmp_path)
    task['steps'][0].pop('approval_gate')
    task['steps'].append({
        'id': 'pending', 'prompt': 'uncertain operation', 'depends_on': ['external'],
        'completion': {'validator_id': 'json_object_fields', 'output_path': 'pending.json', 'required_fields': ['status']},
    })
    task_file.write_text(json.dumps(task), encoding='utf-8')
    # Persisted pre-crash input; completion is a stored claim, not fresh verification.
    steps = {} if missing_metadata else {'external': {'status': 'completed'}, 'pending': {'status': 'running'}}
    state_path = tmp_path / '.omc/state/autopilot/gated-workflow.json'
    state_path.parent.mkdir(parents=True, exist_ok=True)
    state_path.write_text(json.dumps({'task_id': task['id'], 'task_spec_sha256': workflow.task_spec_sha256(task), 'status': 'running', 'pid': None, 'steps': steps}), encoding='utf-8')
    bin_dir = tmp_path / 'bin'
    bin_dir.mkdir()
    provider = bin_dir / 'codex'
    provider.write_text('#!' + sys.executable + '\nfrom pathlib import Path\nPath("provider-called").touch()\nraise SystemExit(99)\n', encoding='utf-8')
    provider.chmod(0o755)
    result = subprocess.run([sys.executable, str(Path(omc_autopilot.__file__).with_name('omc.py')), 'autopilot', '--task-file', str(task_file)], cwd=tmp_path, env={**os.environ, 'PATH': str(bin_dir) + os.pathsep + os.environ['PATH']}, capture_output=True, text=True, timeout=20)
    assert result.returncode == 1
    assert not (tmp_path / 'provider-called').exists()
    saved = json.loads(state_path.read_text())
    assert saved['status'] == 'manual_reconciliation_required'
    assert saved['failure_reason'] == 'stale_external_execution_requires_reconciliation'
    assert '기록상 완료' in result.stdout
    assert '현재 재검증 완료를 뜻하지 않습니다' in result.stdout
    assert '실행 결과 불확실' in result.stdout
    assert '.omc/state/autopilot/gated-workflow.json' in result.stdout
    assert '1. 상태 기록' in result.stdout and '2. provider' in result.stdout and '3. 근거' in result.stdout
    assert '자동 재시도하지 않습니다' in result.stdout
    assert '외부 provider 실행 기록: 미수집—별도 확인 필요' in result.stdout
    assert '단일 단계 재개 V0' in result.stdout
    assert '--recovery-evidence' in result.stdout and '--confirm-not-executed' in result.stdout
    assert '차단 유지 이유: 이전 provider 실행의 완료 여부를 확정하지 못했습니다' in result.stdout
    assert '산출물 존재 여부는 내용 검증이나 완료 판정이 아닙니다' in result.stdout
    assert 'external: result.json [존재]' in result.stdout
    assert 'pending: pending.json [없음]' in result.stdout
    assert ('external: 실행 응답 진단 미수집' in result.stdout) == (not missing_metadata)
    if missing_metadata:
        assert '기록상 완료: 확인 불가' in result.stdout
        assert '실행 결과 불확실: 확인 불가' in result.stdout
    else:
        assert '기록상 완료: external' in result.stdout
        assert '실행 결과 불확실: pending' in result.stdout
        assert 'result.json' in result.stdout and 'pending.json' in result.stdout
        assert saved['steps']['external']['status'] == 'completed'
        assert saved['steps']['pending']['status'] == 'hold'


def test_recovery_guidance_does_not_override_task_hash_guard(tmp_path, monkeypatch, capsys):
    task_file, task = _v2_task(tmp_path)
    state_path = tmp_path / '.omc/state/autopilot/gated-workflow.json'
    state_path.parent.mkdir(parents=True, exist_ok=True)
    state_path.write_text(json.dumps({'status': 'running', 'pid': None, 'task_spec_sha256': 'a' * 64, 'steps': {'external': {'status': 'running'}}}))
    monkeypatch.setattr(omc_autopilot, '_detect_executor', lambda _preferred: 'codex')
    with patch.object(omc_autopilot, '_run_step') as provider:
        assert omc_autopilot.cmd_run(tmp_path, task_file) == 1
    provider.assert_not_called()
    assert 'task spec hash mismatch' in capsys.readouterr().out
    assert json.loads(state_path.read_text())['failure_reason'] == 'task_spec_hash_mismatch'


def test_recovery_guidance_after_provider_process_group_interrupt_public_cli(tmp_path):
    import os
    import signal
    import subprocess
    import sys
    import time

    task = {'schema_version': 'omc-autopilot-task/v2', 'id': 'interrupted', 'executor': 'claude', 'resume_failed': True, 'max_retries': 0, 'steps': [
        {'id': step, 'prompt': 'DO_STAGE_TWO' if step == 's2' else 'DO_STAGE_ONE', 'depends_on': ['s1'] if step == 's2' else [], 'completion': {'validator_id': 'json_object_fields', 'output_path': step + '.json', 'required_fields': ['status']}}
        for step in ['s1', 's2']
    ]}
    task_file = _write_task(tmp_path, task)
    bin_dir = tmp_path / 'bin'
    bin_dir.mkdir()
    provider = bin_dir / 'claude'
    provider.write_text('#!' + sys.executable + '''
from pathlib import Path
import sys,time
stage='s2' if 'DO_STAGE_TWO' in ' '.join(sys.argv) else 's1'
with Path('calls.txt').open('a') as f:f.write(stage+'\\n')
if stage=='s2':
 Path('s2-started').touch()
 while True:time.sleep(.05)
Path(stage+'.json').write_text('{"status":"done"}')
print('VERDICT: APPROVE')
''', encoding='utf-8')
    provider.chmod(0o755)
    env = {**os.environ, 'PATH': str(bin_dir) + os.pathsep + os.environ['PATH']}
    cmd = [sys.executable, str(Path(omc_autopilot.__file__).with_name('omc.py')), 'autopilot', '--task-file', str(task_file)]
    with (tmp_path / 'initial.stdout').open('w') as out, (tmp_path / 'initial.stderr').open('w') as err:
        child = subprocess.Popen(cmd, cwd=tmp_path, env=env, stdout=out, stderr=err, start_new_session=True)
        try:
            deadline = time.monotonic() + 15
            while child.poll() is None and not (tmp_path / 's2-started').exists() and time.monotonic() < deadline:
                time.sleep(.05)
            assert (tmp_path / 's2-started').exists(), (tmp_path / 'initial.stderr').read_text()
            assert (tmp_path / 's1.json').exists()
        finally:
            if child.poll() is None:
                os.killpg(child.pid, signal.SIGKILL)
            child.wait(timeout=5)
    result = subprocess.run(cmd, cwd=tmp_path, env=env, capture_output=True, text=True, timeout=20)
    assert result.returncode == 1
    assert '기록상 완료: s1' in result.stdout
    assert '실행 결과 불확실: s2' in result.stdout
    assert 's1.json' in result.stdout and 's2.json' in result.stdout
    assert '자동 재시도하지 않습니다' in result.stdout
    assert 's1: s1.json [존재]' in result.stdout
    assert 's2: s2.json [없음]' in result.stdout
    assert 's1: 실행 응답 진단 수집됨' in result.stdout
    assert 's2: 실행 응답 진단 미수집' in result.stdout
    assert (tmp_path / 'calls.txt').read_text().splitlines() == ['s1', 's2']
    state = json.loads((tmp_path / '.omc/state/autopilot/interrupted.json').read_text())
    assert state['status'] == 'manual_reconciliation_required'
    assert state['steps']['s1']['status'] == 'completed'
    assert state['steps']['s2']['status'] == 'hold'


def _recovery_case(root):
    task_file, task = _v2_task(root)
    task['steps'][0].pop('approval_gate')
    task['steps'][0]['completion'] = {'validator_id': 'json_object_fields', 'output_path': 'result.json', 'required_fields': ['status']}
    task_file.write_text(json.dumps(task))
    state = {'task_id': task['id'], 'task_spec_sha256': workflow.task_spec_sha256(task),
             'status': 'manual_reconciliation_required', 'stale_reason': 'dead pid',
             'steps': {'external': {'status': 'hold', 'stale_reason': 'dead pid'}}}
    omc_autopilot._save_state(root, task['id'], state)
    evidence = root / 'external-evidence.json'
    evidence.write_text(json.dumps({'execution_id': 'provider-run-1', 'source': 'provider-history',
        'checked_at': '2026-10-11T00:00:00Z', 'judgment': 'not_executed'}))
    return task_file, task, evidence


def test_recovery_approval_binding_and_single_step(tmp_path, monkeypatch):
    task_file, task, evidence = _recovery_case(tmp_path)
    task['steps'].append({'id': 'later', 'prompt': 'later', 'depends_on': ['external'],
        'completion': {'validator_id': 'json_object_fields', 'output_path': 'later.json', 'required_fields': ['status']}})
    task_file.write_text(json.dumps(task))
    state = omc_autopilot._load_state(tmp_path, task['id'])
    state['task_spec_sha256'] = workflow.task_spec_sha256(task)
    omc_autopilot._save_state(tmp_path, task['id'], state)
    receipt = omc_autopilot.cmd_recovery_approve(tmp_path, task_file, 'external', evidence, 'human', True)
    assert receipt == 0
    calls = []
    monkeypatch.setattr(omc_autopilot, '_detect_executor', lambda _: 'codex')
    def run(root, step, **kwargs):
        calls.append(step['id'])
        (root / 'result.json').write_text('{"status":"new"}')
        return 0, 'VERDICT: APPROVE', None, None
    monkeypatch.setattr(omc_autopilot, '_run_step', run)
    assert omc_autopilot.cmd_run(tmp_path, task_file, recovery_step='external') == 0
    assert calls == ['external']
    assert omc_autopilot.cmd_run(tmp_path, task_file, recovery_step='external') == 1
    assert omc_autopilot.cmd_run(tmp_path, task_file) == 1
    assert calls == ['external']


@pytest.mark.parametrize('mutation', ['evidence', 'state', 'task'])
def test_recovery_changed_binding_rejects_without_calls(tmp_path, monkeypatch, mutation):
    task_file, task, evidence = _recovery_case(tmp_path)
    assert omc_autopilot.cmd_recovery_approve(tmp_path, task_file, 'external', evidence, 'human', True) == 0
    if mutation == 'evidence': evidence.write_text('{}')
    elif mutation == 'task': task['title'] = 'changed'; task_file.write_text(json.dumps(task))
    else:
        state = omc_autopilot._load_state(tmp_path, task['id']); state['stale_reason'] = 'new interruption'
        omc_autopilot._save_state(tmp_path, task['id'], state)
    with patch.object(omc_autopilot, '_run_step') as provider:
        assert omc_autopilot.cmd_run(tmp_path, task_file, recovery_step='external') == 1
        provider.assert_not_called()


def test_recovery_after_intent_interrupt_blocks_reuse(tmp_path, monkeypatch):
    task_file, task, evidence = _recovery_case(tmp_path)
    assert omc_autopilot.cmd_recovery_approve(tmp_path, task_file, 'external', evidence, 'human', True) == 0
    monkeypatch.setattr(omc_autopilot, '_detect_executor', lambda _: 'codex')
    monkeypatch.setattr(omc_autopilot, '_run_step', lambda *a, **k: (_ for _ in ()).throw(KeyboardInterrupt()))
    with pytest.raises(KeyboardInterrupt): omc_autopilot.cmd_run(tmp_path, task_file, recovery_step='external')
    state = omc_autopilot._load_state(tmp_path, task['id'])
    assert state['recovery_intent']['consumed'] is True
    with patch.object(omc_autopilot, '_run_step') as provider:
        assert omc_autopilot.cmd_run(tmp_path, task_file, recovery_step='external') == 1
        assert omc_autopilot.cmd_run(tmp_path, task_file) == 1
        provider.assert_not_called()


def test_recovery_public_cli_approval_and_resume(tmp_path):
    import subprocess, sys, os
    task_file, task, evidence = _recovery_case(tmp_path)
    bin_dir = tmp_path / 'bin'; bin_dir.mkdir()
    provider = bin_dir / 'codex'
    provider.write_text('#!' + sys.executable + '\nimport sys\nif "--help" in sys.argv or "--version" in sys.argv or "features" in sys.argv: raise SystemExit(0)\nfrom pathlib import Path\nwith Path("calls").open("a") as f:f.write("called\\n")\nPath("result.json").write_text(\'{"status":"new"}\')\nprint("VERDICT: APPROVE")\n')
    provider.chmod(0o755)
    env = {**os.environ, 'PATH': str(bin_dir) + os.pathsep + os.environ['PATH']}
    cli = Path(omc_autopilot.__file__).with_name('omc.py')
    base = [sys.executable, str(cli), 'autopilot', '--task-file', str(task_file), '--recovery-step', 'external']
    approved = subprocess.run(base + ['--recovery-evidence', str(evidence), '--recovery-reviewer', 'human', '--confirm-not-executed'], cwd=tmp_path, env=env, capture_output=True, text=True)
    assert approved.returncode == 0, approved.stdout + approved.stderr
    assert not (tmp_path / 'calls').exists()
    result = subprocess.run(base, cwd=tmp_path, env=env, capture_output=True, text=True)
    assert result.returncode == 0, result.stdout + result.stderr
    assert '후속 자동 실행 없음' in result.stdout
    again = subprocess.run(base, cwd=tmp_path, env=env, capture_output=True, text=True)
    assert again.returncode == 1
    assert (tmp_path / 'calls').read_text().splitlines() == ['called']


def test_recovery_common_lock_blocks_other_process(tmp_path):
    import subprocess, sys
    task_file, task, evidence = _recovery_case(tmp_path)
    cli = Path(omc_autopilot.__file__)
    with omc_autopilot._task_execution_lock(tmp_path, task['id']):
        for extra in [[], ['--recovery-step', 'external']]:
            result = subprocess.run([sys.executable, str(cli), '--target', str(tmp_path), 'run', '--task', str(task_file)] + extra, capture_output=True, text=True)
            assert result.returncode == 1
            assert 'task_execution_locked' in result.stdout


@pytest.mark.parametrize('invalid', ['missing_confirmation', 'missing_execution', 'unknown_judgment', 'original_gate'])
def test_recovery_approval_rejects_invalid_evidence_or_gate(tmp_path, invalid):
    task_file, task, evidence = _recovery_case(tmp_path)
    if invalid == 'original_gate':
        task['steps'][0]['approval_gate'] = {'approval_id': 'original', 'payload_sha256': 'a'*64}
        task_file.write_text(json.dumps(task))
        state = omc_autopilot._load_state(tmp_path, task['id']); state['task_spec_sha256'] = workflow.task_spec_sha256(task)
        omc_autopilot._save_state(tmp_path, task['id'], state)
    payload = json.loads(evidence.read_text())
    if invalid == 'missing_execution': payload.pop('execution_id')
    if invalid == 'unknown_judgment': payload['judgment'] = 'unknown'
    evidence.write_text(json.dumps(payload))
    assert omc_autopilot.cmd_recovery_approve(tmp_path, task_file, 'external', evidence, 'human', invalid != 'missing_confirmation') == 1
    assert not (tmp_path / '.omc/state/autopilot/recovery-approvals' / (task['id']+'.json')).exists()


def test_recovery_new_review_round_preserves_consumed_intent(tmp_path):
    task_file, task, evidence = _recovery_case(tmp_path)
    assert omc_autopilot.cmd_recovery_approve(tmp_path, task_file, 'external', evidence, 'human', True) == 0
    state = omc_autopilot._load_state(tmp_path, task['id'])
    state['recovery_intent'] = {'consumed': True, 'step_id': 'external', 'recorded_at': 'earlier'}
    omc_autopilot._save_state(tmp_path, task['id'], state)
    assert omc_autopilot.cmd_run(tmp_path, task_file, recovery_step='external') == 1
    assert omc_autopilot.cmd_recovery_approve(tmp_path, task_file, 'external', evidence, 'human-new-review', True) == 0
    state = omc_autopilot._load_state(tmp_path, task['id'])
    assert state['recovery_history'][0]['consumed'] is True
    assert 'recovery_intent' not in state
    assert state['recovery_round_at']


@pytest.mark.parametrize("changed", [False, True])
def test_recovery_completed_artifact_change_blocks_before_intent(tmp_path, monkeypatch, changed):
    task_file, task, evidence = _recovery_case(tmp_path)
    first = {'id': 'first', 'prompt': 'first', 'depends_on': [], 'completion': {'validator_id':'json_object_fields','output_path':'first.json','required_fields':['status']}}
    task['steps'].insert(0, first); task['steps'][1]['depends_on'] = ['first']; task_file.write_text(json.dumps(task))
    (tmp_path / 'first.json').write_text('{"status":"done"}')
    digest, _ = workflow.validate_completion_artifact(tmp_path, first)
    state = omc_autopilot._load_state(tmp_path, task['id']); state['task_spec_sha256'] = workflow.task_spec_sha256(task)
    state['steps']['first'] = {'status':'completed','completion_receipt':workflow.build_completion_receipt(task=task, step=first, artifact_sha256=digest, predecessor_receipts=[])}
    omc_autopilot._save_state(tmp_path, task['id'], state)
    assert omc_autopilot.cmd_recovery_approve(tmp_path, task_file, 'external', evidence, 'human', True) == 0
    if changed:
        (tmp_path / 'first.json').write_text('{"status":"changed"}')
        with patch.object(omc_autopilot, '_run_step') as provider:
            assert omc_autopilot.cmd_run(tmp_path, task_file, recovery_step='external') == 1
            provider.assert_not_called()
        assert 'recovery_intent' not in omc_autopilot._load_state(tmp_path, task['id'])
    else:
        monkeypatch.setattr(omc_autopilot, '_detect_executor', lambda _: 'codex')
        calls = []
        def run(root, step, **kwargs):
            calls.append(step['id'])
            (root / 'result.json').write_text('{"status":"new"}')
            return 0, 'VERDICT: APPROVE', None, None
        monkeypatch.setattr(omc_autopilot, '_run_step', run)
        assert omc_autopilot.cmd_run(tmp_path, task_file, recovery_step='external') == 0
        assert calls == ['external']
        assert omc_autopilot._load_state(tmp_path, task['id'])['steps']['first']['status'] == 'completed'



def test_recovery_real_process_kill_after_intent(tmp_path):
    import subprocess, sys, os, signal, time
    task_file, task, evidence = _recovery_case(tmp_path)
    assert omc_autopilot.cmd_recovery_approve(tmp_path, task_file, 'external', evidence, 'human', True) == 0
    bin_dir = tmp_path / 'bin'; bin_dir.mkdir()
    provider = bin_dir / 'codex'
    provider.write_text('#!' + sys.executable + '\nimport sys,time\nfrom pathlib import Path\nif "--help" in sys.argv or "--version" in sys.argv or "features" in sys.argv: raise SystemExit(0)\nwith Path("calls").open("a") as f:f.write("call\\n")\nPath("started").touch()\nwhile True:time.sleep(.05)\n')
    provider.chmod(0o755)
    env = {**os.environ, 'PATH': str(bin_dir)+os.pathsep+os.environ['PATH']}
    cmd = [sys.executable, str(Path(omc_autopilot.__file__).with_name('omc.py')), 'autopilot', '--task-file', str(task_file), '--recovery-step', 'external']
    with (tmp_path/'stdout').open('w') as out, (tmp_path/'stderr').open('w') as err:
        child = subprocess.Popen(cmd, cwd=tmp_path, env=env, stdout=out, stderr=err, start_new_session=True)
        try:
            deadline = time.monotonic()+10
            while child.poll() is None and not (tmp_path/'started').exists() and time.monotonic()<deadline: time.sleep(.05)
            assert (tmp_path/'started').exists(), (tmp_path/'stderr').read_text()
        finally:
            if child.poll() is None: os.killpg(child.pid, signal.SIGKILL)
            child.wait(timeout=5)
    state = omc_autopilot._load_state(tmp_path, task['id'])
    assert state['recovery_intent']['consumed'] is True
    retry = subprocess.run(cmd,cwd=tmp_path,env=env,capture_output=True,text=True,timeout=10)
    assert retry.returncode == 1
    assert (tmp_path/'calls').read_text().splitlines() == ['call']


def test_recovery_durability_failure_after_intent_no_calls(tmp_path, monkeypatch):
    task_file, task, evidence = _recovery_case(tmp_path)
    assert omc_autopilot.cmd_recovery_approve(tmp_path, task_file, 'external', evidence, 'human', True) == 0
    import os
    real_fsync = os.fsync
    def fail_directory(fd):
        import stat
        if stat.S_ISDIR(os.fstat(fd).st_mode): raise OSError('directory sync failed')
        real_fsync(fd)
    monkeypatch.setattr(os, 'fsync', fail_directory)
    with patch.object(omc_autopilot, '_run_step') as provider:
        assert omc_autopilot.cmd_run(tmp_path, task_file, recovery_step='external') == 1
        provider.assert_not_called()
    assert omc_autopilot._load_state(tmp_path, task['id'])['recovery_intent']['consumed']


@pytest.mark.parametrize('mutation', ['id', 'prompt'])
def test_recovery_uses_approved_task_when_file_changes_after_intent(tmp_path, monkeypatch, mutation):
    task_file, task, evidence = _recovery_case(tmp_path)
    assert omc_autopilot.cmd_recovery_approve(tmp_path, task_file, 'external', evidence, 'human', True) == 0
    save = omc_autopilot._save_state
    def change_after_intent(root, task_id, state):
        save(root, task_id, state)
        if state.get('recovery_intent') and task_id == task['id']:
            changed = json.loads(json.dumps(task))
            if mutation == 'id': changed['id'] = 'unapproved-task'
            else: changed['steps'][0]['prompt'] = 'UNAPPROVED PROMPT'
            task_file.write_text(json.dumps(changed))
    monkeypatch.setattr(omc_autopilot, '_save_state', change_after_intent)
    monkeypatch.setattr(omc_autopilot, '_detect_executor', lambda _: 'codex')
    calls = []
    def provider(root, step, **kwargs):
        calls.append(step['prompt'])
        (root / 'result.json').write_text('{"status":"new"}')
        return 0, 'VERDICT: APPROVE', None, None
    monkeypatch.setattr(omc_autopilot, '_run_step', provider)
    assert omc_autopilot.cmd_run(tmp_path, task_file, recovery_step='external') == 0
    assert calls == [task['steps'][0]['prompt']]
    assert not (tmp_path / '.omc/state/autopilot/unapproved-task.json').exists()
    assert omc_autopilot._load_state(tmp_path, task['id'])['recovery_intent']['outcome'] == 'completed'
