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
        code = omc_autopilot.cmd_run(tmp_path, task_file, recovery_details=True)

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
        code = omc_autopilot.cmd_run(tmp_path, task_file, recovery_details=True)

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
        code = omc_autopilot.cmd_run(tmp_path, task_file, recovery_details=True)

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
        code = omc_autopilot.cmd_run(tmp_path, task_file, recovery_details=True)

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
        code = omc_autopilot.cmd_run(tmp_path, task_file, recovery_details=True)

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
        code = omc_autopilot.cmd_run(tmp_path, task_file, recovery_details=True)

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
        code = omc_autopilot.cmd_run(tmp_path, task_file, recovery_details=True)

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
        code = omc_autopilot.cmd_run(tmp_path, task_file, recovery_details=True)

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
@pytest.mark.parametrize("task_title", ['주문 내역 정리', None])
def test_recovery_guidance_public_cli_preserves_block_and_no_provider_calls(tmp_path, missing_metadata, task_title):
    import os
    import subprocess
    import sys

    task_file, task = _v2_task(tmp_path)
    if task_title is None:
        task.pop('title', None)
    else:
        task['title'] = task_title
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
    result = subprocess.run([sys.executable, str(Path(omc_autopilot.__file__).with_name('omc.py')), 'autopilot', '--recovery-details', '--task-file', str(task_file)], cwd=tmp_path, env={**os.environ, 'PATH': str(bin_dir) + os.pathsep + os.environ['PATH']}, capture_output=True, text=True, timeout=20)
    assert result.returncode == 1
    assert f"확인할 작업: {task_title or '제목 없음'} (작업 ID: gated-workflow)" in result.stdout
    assert ('하려던 작업: 주문 내역 정리' if task_title else '하려던 작업: 작업 목적 정보 없음') in result.stdout
    assert result.stdout.index('하려던 작업:') < result.stdout.index('확인된 진행:') < result.stdout.index('중단 경위:') < result.stdout.index('현재 결과:') < result.stdout.index('다음 행동:')
    assert '다음 행동: 이번에는 다시 실행하지 않고 보류합니다' in result.stdout
    assert '기록이 없는 단계를 미실행으로 단정하지 않습니다' not in result.stdout
    if missing_metadata:
        assert 'external, pending: 현재 시도의 유효한 실행 관측 기록이 없어 이전 실행 여부를 확인할 수 없습니다' in result.stdout
    assert str(state_path) in result.stdout
    assert '외부 실행 기록 위치: OMC에서 확인할 수 없음' in result.stdout
    assert '실행됨 → 다시 실행하지 마세요' in result.stdout
    assert '실행 안 됨 → 미실행 근거를 확인한 사람이 한 단계 재개를 별도 승인' in result.stdout
    assert '모름 → 멈춘 상태를 유지하세요' in result.stdout
    assert result.stdout.index('단계 상태') < result.stdout.index('지금 할 일') < result.stdout.index('승인할 실행 범위')
    assert '후속 단계는 자동 실행하지 않습니다' in result.stdout
    pending_label = '확인 불가' if missing_metadata else '실행 결과 불확실'
    assert f'pending: {pending_label}' in result.stdout
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
    assert '선언된 목표 파일 미지정' in result.stdout
    assert '변경 요약 불가: 목표 파일 미지정' in result.stdout
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
        assert omc_autopilot.cmd_run(tmp_path, task_file, recovery_details=True) == 1
    provider.assert_not_called()
    assert 'task spec hash mismatch' in capsys.readouterr().out
    assert json.loads(state_path.read_text())['failure_reason'] == 'task_spec_hash_mismatch'


def test_recovery_guidance_after_provider_process_group_interrupt_public_cli(tmp_path):
    import os
    import signal
    import subprocess
    import sys
    import time

    task = {'schema_version': 'omc-autopilot-task/v2', 'id': 'interrupted', 'title': '검증용 주문 저장', 'executor': 'claude', 'resume_failed': True, 'max_retries': 0, 'steps': [
        {'id': step, 'title': '주문 저장' if step == 's2' else '주문 준비', 'prompt': 'DO_STAGE_TWO' if step == 's2' else 'DO_STAGE_ONE', 'depends_on': ['s1'] if step == 's2' else [], 'completion': {'validator_id': 'json_object_fields', 'output_path': step + '.json', 'required_fields': ['status']}}
        for step in ['s1', 's2']
    ]}
    task['steps'].append({'id':'later','title':'후속 보고서','prompt':'DO_STAGE_LATER','depends_on':['s2'],
        'completion':{'validator_id':'json_object_fields','output_path':'later.json','required_fields':['status']}})
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
    cmd = [sys.executable, str(Path(omc_autopilot.__file__).with_name('omc.py')), 'autopilot', '--recovery-details', '--task-file', str(task_file)]
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
    assert 's1: 실행기 반환 기록 있음' in result.stdout
    assert '현재 완료 검증 통과' in result.stdout
    assert 's2: 실행 요청 중 중단됨' in result.stdout
    assert '하려던 작업: 검증용 주문 저장' in result.stdout
    assert '확인된 진행: 주문 준비 (s1)의 현재 산출물과 완료 기록 검증을 통과했습니다' in result.stdout
    assert '중단 경위: 주문 저장 (s2)의 실행 요청 기록이 남은 채 이전 실행이 중단됐습니다' in result.stdout
    assert '현재 결과: 주문 저장 (s2), 후속 보고서 (later)의 완료와 반영 여부를 확인하지 못했습니다' in result.stdout
    assert '중단 원인은 확인할 수 없습니다' in result.stdout
    assert '반영 여부를 확인하지 못했습니다' in result.stdout
    assert '이번에는 다시 실행하지 않고 보류합니다' in result.stdout
    assert '기록이 없는 단계' not in result.stdout
    assert '후속 보고서 (later): 현재 시도의 유효한 실행 관측 기록이 없어 이전 실행 여부를 확인할 수 없습니다' in result.stdout
    assert '중단 경위: 주문 저장 (s2)' in result.stdout
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


def test_direct_execution_observation_survives_interrupt(tmp_path, monkeypatch, capsys):
    task_file, task = _v2_task(tmp_path)
    task['steps'][0].pop('approval_gate')
    task_file.write_text(json.dumps(task))
    monkeypatch.setattr(omc_autopilot, '_detect_executor', lambda _: 'codex')
    def interrupted(*args, **kwargs):
        raise KeyboardInterrupt()
    monkeypatch.setattr(omc_autopilot, '_run_step', interrupted)
    with pytest.raises(KeyboardInterrupt):
        omc_autopilot.cmd_run(tmp_path, task_file, recovery_details=True)
    state = omc_autopilot._load_state(tmp_path, task['id'])
    assert state['steps']['external']['execution_observation']['phase'] == 'requested'
    state['pid'] = None
    omc_autopilot._save_state(tmp_path, task['id'], state)
    capsys.readouterr()
    with patch.object(omc_autopilot, '_run_step') as provider:
        assert omc_autopilot.cmd_run(tmp_path, task_file, recovery_details=True) == 1
        provider.assert_not_called()
    output = capsys.readouterr().out
    assert 'OMC 확인 결과' in output
    assert 'external: 실행 요청 중 중단됨' in output
    assert '실제 호출·외부 반영 여부는 확인 불가' in output


def test_direct_execution_observation_returned_and_bound(tmp_path, monkeypatch):
    task_file, task = _v2_task(tmp_path)
    task['steps'][0].pop('approval_gate')
    task['steps'][0]['completion'] = {'validator_id':'json_object_fields','output_path':'result.json','required_fields':['status']}
    task_file.write_text(json.dumps(task))
    monkeypatch.setattr(omc_autopilot, '_detect_executor', lambda _: 'codex')
    def run(*args, **kwargs):
        (tmp_path/'result.json').write_text('{"status":"done"}')
        return 0, 'VERDICT: APPROVE', None, None
    monkeypatch.setattr(omc_autopilot, '_run_step', run)
    assert omc_autopilot.cmd_run(tmp_path, task_file, recovery_details=True) == 0
    state = omc_autopilot._load_state(tmp_path, task['id'])
    observation = state['steps']['external']['execution_observation']
    assert observation['phase'] == 'returned'
    assert observation['task_spec_sha256'] == workflow.task_spec_sha256(task)
    assert observation['step_id'] == 'external'
    assert observation['returncode'] == 0


@pytest.mark.parametrize('mutation', ['digest', 'step', 'request', 'attempt', 'replayed_id', 'missing'])
def test_recovery_assessment_rejects_unbound_observation(tmp_path, mutation):
    task_file, task, _ = _recovery_case(tmp_path)
    state = omc_autopilot._load_state(tmp_path, task['id'])
    record = state['steps']['external']
    record['attempt'] = 1
    observation = workflow.execution_observation(task=task, step=task['steps'][0], attempt=1)
    state['execution_attempt_ids'] = {'external': observation['attempt_id']}
    record['execution_observation'] = observation
    if mutation == 'step': observation['step_id'] = 'other'
    if mutation == 'request': observation['task_spec_sha256'] = '0' * 64
    if mutation == 'attempt': observation['attempt'] = 2
    if mutation == 'replayed_id': observation['attempt_id'] = 'a' * 32
    record['execution_observation_sha256'] = workflow.observation_digest(observation)
    if mutation == 'digest': observation['phase'] = 'returned'
    if mutation == 'missing': record.pop('execution_observation')
    output = ' '.join(workflow.recovery_assessment(tmp_path, task, state))
    assert '확인 불가' in output
    assert '실행 요청 중 중단됨' not in output
    assert '실행기 반환 기록 있음' not in output


def test_observation_write_failure_blocks_dispatch(tmp_path, monkeypatch):
    task_file, task = _v2_task(tmp_path)
    task['steps'][0].pop('approval_gate')
    task_file.write_text(json.dumps(task))
    monkeypatch.setattr(omc_autopilot, '_detect_executor', lambda _: 'codex')
    save = omc_autopilot._save_state
    def fail(root, task_id, state):
        if state.get('steps', {}).get('external', {}).get('execution_observation'):
            raise OSError('observation persistence failed')
        return save(root, task_id, state)
    monkeypatch.setattr(omc_autopilot, '_save_state', fail)
    with patch.object(omc_autopilot, '_run_step') as provider:
        assert omc_autopilot.cmd_run(tmp_path, task_file, recovery_details=True) == 1
        provider.assert_not_called()


@pytest.mark.parametrize('change', ['changed', 'missing', 'receipt'])
def test_assessment_revalidates_current_completion(tmp_path, monkeypatch, change):
    task_file, task = _v2_task(tmp_path)
    task['steps'][0].pop('approval_gate')
    task['steps'][0]['completion'] = {'validator_id':'json_object_fields','output_path':'result.json','required_fields':['status']}
    task_file.write_text(json.dumps(task))
    monkeypatch.setattr(omc_autopilot, '_detect_executor', lambda _: 'codex')
    def run(*args, **kwargs):
        (tmp_path/'result.json').write_text('{"status":"done"}')
        return 0, 'VERDICT: APPROVE', None, None
    monkeypatch.setattr(omc_autopilot, '_run_step', run)
    assert omc_autopilot.cmd_run(tmp_path, task_file, recovery_details=True) == 0
    state = omc_autopilot._load_state(tmp_path, task['id'])
    assert '현재 완료 검증 통과' in ' '.join(workflow.recovery_assessment(tmp_path, task, state))
    if change == 'changed': (tmp_path/'result.json').write_text('{"status":"different"}')
    if change == 'missing': (tmp_path/'result.json').unlink()
    if change == 'receipt': state['steps']['external']['completion_receipt']['receipt_sha256'] = '0' * 64
    assert '현재 완료 검증 통과' not in ' '.join(workflow.recovery_assessment(tmp_path, task, state))
    state.update(status='manual_reconciliation_required', stale_reason='test interrupted subsequent work')
    omc_autopilot._save_state(tmp_path, task['id'], state)
    import subprocess, sys
    result = subprocess.run([sys.executable, str(Path(omc_autopilot.__file__).with_name('omc.py')), 'autopilot', '--recovery-details', '--task-file', str(task_file)], cwd=tmp_path, capture_output=True, text=True, timeout=20)
    assert result.returncode == 1
    assert '현재 완료 검증 통과' not in result.stdout
    assert '현재 완료 확인 불가' in result.stdout


def test_assessment_invalid_predecessor_blocks_downstream_completion(tmp_path, monkeypatch):
    task_file, task = _v2_task(tmp_path)
    task['steps'][0].pop('approval_gate')
    task['steps'][0]['completion'] = {'validator_id':'json_object_fields','output_path':'external.json','required_fields':['status']}
    task['steps'].append({'id':'later','prompt':'later','depends_on':['external'],
        'completion':{'validator_id':'json_object_fields','output_path':'later.json','required_fields':['status']}})
    task_file.write_text(json.dumps(task))
    monkeypatch.setattr(omc_autopilot, '_detect_executor', lambda _: 'codex')
    def run(root, step, **kwargs):
        (root/step['completion']['output_path']).write_text('{"status":"done"}')
        return 0, 'VERDICT: APPROVE', None, None
    monkeypatch.setattr(omc_autopilot, '_run_step', run)
    assert omc_autopilot.cmd_run(tmp_path, task_file, recovery_details=True) == 0
    state = omc_autopilot._load_state(tmp_path, task['id'])
    assert sum('현재 완료 검증 통과' in row for row in workflow.recovery_assessment(tmp_path, task, state)) == 2
    (tmp_path/'external.json').unlink()
    assert all('현재 완료 검증 통과' not in row for row in workflow.recovery_assessment(tmp_path, task, state))


def test_recovery_public_cli_artifact_symlink_loop_is_unknown(tmp_path):
    import subprocess, sys
    task_file, task, _ = _recovery_case(tmp_path)
    task['steps'].append({'id':'later','prompt':'later','depends_on':['external'],
        'completion':{'validator_id':'json_object_fields','output_path':'later.json','required_fields':['status']}})
    task_file.write_text(json.dumps(task))
    state = omc_autopilot._load_state(tmp_path, task['id'])
    state['task_spec_sha256'] = workflow.task_spec_sha256(task)
    omc_autopilot._save_state(tmp_path, task['id'], state)
    artifact = tmp_path/'result.json'
    artifact.unlink()
    artifact.symlink_to('result.json')
    result = subprocess.run([sys.executable,str(Path(omc_autopilot.__file__).with_name('omc.py')),'autopilot','--recovery-details','--task-file',str(task_file)],cwd=tmp_path,capture_output=True,text=True,timeout=20)
    assert result.returncode == 1
    assert 'Traceback' not in result.stderr
    assert '산출물 확인 불가—경로 해석 또는 읽기 실패' in result.stdout
    assert 'later: 확인 불가' in result.stdout
    assert '자동 재시도하지 않습니다' in result.stdout
    assert omc_autopilot._load_state(tmp_path, task['id'])['status'] == 'manual_reconciliation_required'


def test_recovery_assessment_artifact_read_failure_preserves_other_checks(tmp_path, monkeypatch):
    _, task, _ = _recovery_case(tmp_path)
    state = omc_autopilot._load_state(tmp_path, task['id'])
    task['steps'].append({'id':'later','prompt':'later','depends_on':['external'],
        'completion':{'validator_id':'json_object_fields','output_path':'later.json','required_fields':['status']}})
    validate = workflow.validate_completion_artifact
    def unreadable(root, step):
        if step['id'] == 'external':raise PermissionError('test unreadable artifact')
        return validate(root,step)
    monkeypatch.setattr(workflow,'validate_completion_artifact',unreadable)
    output = workflow.recovery_assessment(tmp_path,task,state)
    assert len(output) == 2
    assert '산출물 확인 불가—경로 해석 또는 읽기 실패' in output[0]
    assert 'later: 확인 불가' in output[1]


@pytest.mark.parametrize('file_state, expected', [
    ('match','현재 파일이 선언된 목표 내용과 일치합니다'),
    ('different','현재 파일이 선언된 목표 내용과 다릅니다'),
    ('missing','현재 파일이 없습니다'),
    ('unreadable','현재 파일을 읽거나 경로를 확인할 수 없습니다'),
    ('json_fields','필수 항목만 확인됨—목표 내용 비교 기준 없음'),
    ('json_missing_fields','필수 항목 확인 실패—목표 내용 비교 기준 없음'),
    ('json_invalid','필수 항목 확인 실패—목표 내용 비교 기준 없음'),
])
def test_recovery_public_cli_separates_file_comparison_and_completion(tmp_path, file_state, expected):
    import subprocess, sys, hashlib
    task_file, task, _ = _recovery_case(tmp_path)
    artifact = tmp_path/'result.json'
    task['steps'][0]['completion'] = {'validator_id':'artifact_sha256','output_path':'result.json',
        'expected_sha256':hashlib.sha256(artifact.read_bytes()).hexdigest()}
    if file_state == 'different':artifact.write_text('different')
    if file_state == 'missing':artifact.unlink()
    if file_state == 'unreadable':artifact.unlink();artifact.symlink_to('result.json')
    if file_state.startswith('json_'):
        task['steps'][0]['completion'] = {'validator_id':'json_object_fields','output_path':'result.json','required_fields':['status']}
    if file_state == 'json_missing_fields':artifact.write_text('{}')
    if file_state == 'json_invalid':artifact.write_text('not json')
    task_file.write_text(json.dumps(task))
    state = omc_autopilot._load_state(tmp_path, task['id']);state['task_spec_sha256']=workflow.task_spec_sha256(task)
    omc_autopilot._save_state(tmp_path, task['id'], state)
    result = subprocess.run([sys.executable,str(Path(omc_autopilot.__file__).with_name('omc.py')),'autopilot','--recovery-details','--task-file',str(task_file)],cwd=tmp_path,capture_output=True,text=True,timeout=20)
    assert result.returncode == 1 and 'Traceback' not in result.stderr
    assert '파일 확인:' in result.stdout and expected in result.stdout
    assert 'result.json' in result.stdout
    assert '파일 상태는 이번 실행 여부나 외부 반영 증명이 아닙니다' in result.stdout
    assert '현재 완료 검증 통과' not in result.stdout
    assert '이번에는 다시 실행하지 않고 보류합니다' in result.stdout
    assert omc_autopilot._load_state(tmp_path, task['id'])['status']=='manual_reconciliation_required'


def test_verified_target_diff_public_cli(tmp_path):
    import subprocess, sys, hashlib
    task_file, task, _ = _recovery_case(tmp_path)
    (tmp_path/'result.json').write_text('current\nold\n')
    target = tmp_path/'target.txt';target.write_text('current\nnew\n')
    task['steps'][0]['completion']={'validator_id':'artifact_sha256','output_path':'result.json',
        'target_path':'target.txt','expected_sha256':hashlib.sha256(target.read_bytes()).hexdigest()}
    task_file.write_text(json.dumps(task))
    state=omc_autopilot._load_state(tmp_path,task['id']);state['task_spec_sha256']=workflow.task_spec_sha256(task)
    omc_autopilot._save_state(tmp_path,task['id'],state)
    r=subprocess.run([sys.executable,str(Path(omc_autopilot.__file__).with_name('omc.py')),'autopilot','--recovery-details','--task-file',str(task_file)],cwd=tmp_path,capture_output=True,text=True,timeout=20)
    assert r.returncode==1 and not r.stderr
    assert '--- 현재 내용' in r.stdout and '+++ 목표 내용' in r.stdout
    assert '-old' in r.stdout and '+new' in r.stdout
    assert '선언된 목표와의 비교' in r.stdout
    assert '수정·재개는 별도 승인' in r.stdout
    assert '비교 대상 [external]: 현재 파일 result.json → 선언된 목표 파일 target.txt' in r.stdout
    assert '바꿀 내용' in r.stdout
    assert r.stdout.index('비교 대상 [') < r.stdout.index('바꿀 내용') < r.stdout.index('다음 행동:') < r.stdout.index('--- 현재 내용')


@pytest.mark.parametrize('current,target,label', [(b'a\n',b'a\nb\n','추가할 내용'),(b'a\nb\n',b'a\n','삭제할 내용'),(b'a\n',b'b\n','바꿀 내용'),(b'a\n',b'a\n','추가·삭제·교체할 내용 없음')])
def test_target_diff_change_summary(tmp_path,current,target,label):
    import hashlib
    _,task,_=_recovery_case(tmp_path);step=task['steps'][0]
    (tmp_path/'result.json').write_bytes(current);(tmp_path/'target.txt').write_bytes(target)
    step['completion']={'validator_id':'artifact_sha256','output_path':'result.json','target_path':'target.txt','expected_sha256':hashlib.sha256(target).hexdigest()}
    result=workflow.verified_target_diff(tmp_path,step)
    assert label in '\n'.join(result['summary_lines'])
    combined=result['summary_lines']+result['lines']
    assert len(combined)<=40 and len('\n'.join(combined))<=4000


def test_compact_recovery_public_cli_budget_and_details(tmp_path):
    import subprocess,sys
    task_file,task,_=_recovery_case(tmp_path)
    task['title']='매우 긴 제목\x1b\n'*400
    for index in range(4):
        task['steps'].append({'id':f'later{index}','prompt':'later','depends_on':['external'],
            'completion':{'validator_id':'json_object_fields','output_path':f'later{index}.json','required_fields':['status']}})
    task_file.write_text(json.dumps(task))
    state=omc_autopilot._load_state(tmp_path,task['id']);state['task_spec_sha256']=workflow.task_spec_sha256(task)
    omc_autopilot._save_state(tmp_path,task['id'],state)
    cmd=[sys.executable,str(Path(omc_autopilot.__file__).with_name('omc.py')),'autopilot','--task-file',str(task_file)]
    compact=subprocess.run(cmd,cwd=tmp_path,capture_output=True,text=True,timeout=20)
    assert compact.returncode==1 and not compact.stderr
    assert len(compact.stdout.splitlines())<=8 and len(compact.stdout)<=800
    assert '\x1b' not in compact.stdout
    assert '--recovery-details' in compact.stdout
    assert '일부 생략' in compact.stdout
    assert '다시 실행하지 않고 보류' in compact.stdout
    assert '확인 대상: external' in compact.stdout
    assert f'.omc/state/autopilot/{task["id"]}.json' in compact.stdout
    assert '외부 실행 기록·파일 수정 이력: 미수집' in compact.stdout
    assert '판단 불가이면 보류 유지' in compact.stdout
    assert '미실행이 확인돼도 별도 승인 후 재실행' in compact.stdout
    assert '상세 차이' not in compact.stdout and '완료 산출물 위치' not in compact.stdout
    detail=subprocess.run(cmd+['--recovery-details'],cwd=tmp_path,capture_output=True,text=True,timeout=20)
    assert detail.returncode==1 and not detail.stderr
    assert '완료 산출물 위치' in detail.stdout


def test_compact_check_targets_requested_step_and_missing_evidence():
    facts = [
        {'step_id': 'roadmap', 'name': '로드맵', 'file_path': 'roadmap.md', 'verified': True, 'execution': 'returned',
         'file_status': '목표와 일치', 'target_diff': {'status': 'equal', 'summary_lines': []}},
        {'step_id': 'dashboard', 'name': '대시보드', 'file_path': 'dashboard.md', 'verified': False, 'execution': 'requested',
         'file_status': '목표와 다름', 'target_diff': {'status': 'unavailable', 'summary_lines': []}},
    ]
    output = '\n'.join(workflow.compact_recovery_summary({'title': '상태 반영'}, facts))
    assert '확인 대상: 대시보드 [dashboard.md]' in output
    assert '로드맵: 완료 검증 통과' in output
    assert '판독: 실행 요청 기록만 확인됨; 실제 호출·반영과 완료는 확인 불가' in output
    assert 'OMC 기록 위치 미제공' in output
    assert '외부 실행 기록·파일 수정 이력: 미수집' in output
    assert '판단 불가이면 보류 유지' in output


@pytest.mark.parametrize('execution,expected', [
    ('requested', '실행 요청 기록만 확인됨; 실제 호출·반영과 완료는 확인 불가'),
    ('returned', '실행기 반환 기록 확인됨; 실제 반영과 완료는 확인 불가'),
    ('unknown', '유효 실행 기록 없음; 실행 여부와 완료는 확인 불가'),
])
def test_compact_explains_observed_uncertainty(execution, expected):
    fact = {'name': '대시보드', 'file_path': 'dashboard.md', 'verified': False, 'execution': execution,
            'file_status': '목표와 일치', 'target_diff': {'status': 'equal', 'summary_lines': []}}
    output = '\n'.join(workflow.compact_recovery_summary({'title': '상태 반영'}, [fact]))
    assert '판독: ' + expected in output
    assert '완료 검증 통과' not in output
    assert '다시 실행하지 않고 보류' in output


def test_compact_check_long_locations_keep_budget():
    fact = {'name': '단계' * 300, 'file_path': '파일' * 300, 'verified': False, 'execution': 'requested',
            'file_status': '상태' * 300, 'target_diff': {'status': 'different', 'summary_lines': ['변경' * 300]}}
    output = '\n'.join(workflow.compact_recovery_summary({'title': '제목' * 300}, [fact, fact], record_path='경로' * 300)) + '\n'
    assert len(output.splitlines()) <= 8 and len(output) <= 800
    assert '일부 생략' in output and '--recovery-details' in output


def test_compact_summary_prioritizes_change_after_equal_file():
    facts=[{'file_path':'first.md','file_status':'목표와 일치','target_diff':{'status':'equal','summary_lines':['추가·삭제·교체할 내용 없음']}},
           {'file_path':'second.md','file_status':'목표와 다름','target_diff':{'status':'different','summary_lines':['추가할 내용: 상태 기록']}}]
    output='\n'.join(workflow.compact_recovery_summary({'title':'문서 최신화'},facts))
    assert '필요한 변경: second.md — 추가할 내용: 상태 기록' in output
    assert '필요한 변경: 추가·삭제·교체할 내용 없음' not in output


def test_compact_replace_shows_current_and_target_public_cli(tmp_path):
    import subprocess,sys,hashlib
    task_file,task,_=_recovery_case(tmp_path)
    (tmp_path/'result.json').write_bytes(b'old\n')
    (tmp_path/'target.txt').write_bytes(b'new\n')
    task['steps'][0]['completion']={'validator_id':'artifact_sha256','output_path':'result.json','target_path':'target.txt','expected_sha256':hashlib.sha256(b'new\n').hexdigest()}
    task_file.write_text(json.dumps(task))
    state=omc_autopilot._load_state(tmp_path,task['id']);state['task_spec_sha256']=workflow.task_spec_sha256(task)
    omc_autopilot._save_state(tmp_path,task['id'],state)
    r=subprocess.run([sys.executable,str(Path(omc_autopilot.__file__).with_name('omc.py')),'autopilot','--task-file',str(task_file)],cwd=tmp_path,capture_output=True,text=True,timeout=20)
    assert r.returncode==1 and not r.stderr
    change=next(line for line in r.stdout.splitlines() if line.startswith('필요한 변경:'))
    assert '현재 "old\\n" → 목표 "new\\n"' in change
    assert len(r.stdout.splitlines())<=8 and len(r.stdout)<=800
    assert '다시 실행하지 않고 보류' in r.stdout


def test_public_cli_target_hash_mismatch_does_not_summarize_changes(tmp_path):
    import subprocess,sys,hashlib
    task_file,task,_=_recovery_case(tmp_path)
    target=tmp_path/'target.txt';target.write_bytes(b'changed after declaration')
    task['steps'][0]['completion']={'validator_id':'artifact_sha256','output_path':'result.json','target_path':'target.txt','expected_sha256':hashlib.sha256(b'approved target').hexdigest()}
    task_file.write_text(json.dumps(task))
    state=omc_autopilot._load_state(tmp_path,task['id']);state['task_spec_sha256']=workflow.task_spec_sha256(task)
    omc_autopilot._save_state(tmp_path,task['id'],state)
    r=subprocess.run([sys.executable,str(Path(omc_autopilot.__file__).with_name('omc.py')),'autopilot','--recovery-details','--task-file',str(task_file)],cwd=tmp_path,capture_output=True,text=True,timeout=20)
    assert r.returncode==1 and not r.stderr
    assert '변경 요약 불가: 목표 파일 해시 불일치' in r.stdout
    assert '추가할 내용' not in r.stdout and '바꿀 내용' not in r.stdout


@pytest.mark.parametrize('kind', ['hash','escape','symlink','large','binary','lines'])
def test_target_diff_refuses_invalid_inputs(tmp_path,kind):
    _,task,_=_recovery_case(tmp_path);step=task['steps'][0]
    target=tmp_path/'target.txt';target.write_bytes(b'target\n')
    import hashlib
    step['completion']={'validator_id':'artifact_sha256','output_path':'result.json',
        'target_path':'target.txt','expected_sha256':hashlib.sha256(target.read_bytes()).hexdigest()}
    if kind=='hash':target.write_bytes(b'wrong')
    if kind=='escape':step['completion']['target_path']='../outside.txt'
    if kind=='symlink':target.unlink();target.symlink_to('result.json')
    if kind=='large':target.write_bytes(b'x'*65537)
    if kind=='binary':target.write_bytes(b'\xff')
    if kind=='lines':target.write_bytes(b'x\n'*1001)
    if kind in ['large','binary','lines']:step['completion']['expected_sha256']=hashlib.sha256(target.read_bytes()).hexdigest()
    result=workflow.verified_target_diff(tmp_path,step)
    assert result['status']=='unavailable'
    assert result['lines']==[]


def test_target_diff_limits_and_escapes_output(tmp_path):
    import hashlib
    _,task,_=_recovery_case(tmp_path);step=task['steps'][0]
    (tmp_path/'result.json').write_text('old\n')
    data=('\x1b[31m'+'x'*6000+'\n').encode();(tmp_path/'target.txt').write_bytes(data)
    step['completion']={'validator_id':'artifact_sha256','output_path':'result.json','target_path':'target.txt','expected_sha256':hashlib.sha256(data).hexdigest()}
    result=workflow.verified_target_diff(tmp_path,step)
    text='\n'.join(result['summary_lines']+result['lines'])
    assert '\x1b' not in text and '\\u001b' in text
    assert len(result['summary_lines']+result['lines'])<=40 and len(text)<=4000
    assert '생략' in text


@pytest.mark.parametrize('current, target, side', [(b'same',b'same\n','현재 내용'),(b'same\n',b'same','목표 내용')])
def test_target_diff_marks_missing_final_newline(tmp_path,current,target,side):
    import hashlib
    _,task,_=_recovery_case(tmp_path);step=task['steps'][0]
    (tmp_path/'result.json').write_bytes(current);(tmp_path/'target.txt').write_bytes(target)
    step['completion']={'validator_id':'artifact_sha256','output_path':'result.json','target_path':'target.txt','expected_sha256':hashlib.sha256(target).hexdigest()}
    result=workflow.verified_target_diff(tmp_path,step)
    assert result['status']=='different'
    assert f'[{side}: 마지막 줄바꿈 없음]' in '\n'.join(result['lines'])
    assert len(result['lines'])<=40 and len('\n'.join(result['lines']))<=4000


def test_target_contract_invalid_validator_and_path(tmp_path):
    _,task,_=_recovery_case(tmp_path)
    task['steps'][0]['completion']['target_path']='../target.txt'
    errors=workflow.validate_task_spec(task)
    assert any('target_path_unsafe' in e for e in errors)
    assert any('target_validator_invalid' in e for e in errors)


def test_target_diff_special_file_is_not_opened_blocking(tmp_path):
    import os
    _,task,_=_recovery_case(tmp_path);step=task['steps'][0]
    os.mkfifo(tmp_path/'fifo')
    step['completion']={'validator_id':'artifact_sha256','output_path':'result.json','target_path':'fifo','expected_sha256':'a'*64}
    assert workflow.verified_target_diff(tmp_path,step)['status']=='unavailable'


def test_target_snapshot_detects_change_during_read(tmp_path,monkeypatch):
    import os
    (tmp_path/'target.txt').write_bytes(b'before')
    read=os.read
    def mutate(fd, amount):
        data=read(fd,amount)
        (tmp_path/'target.txt').write_bytes(b'changed-after-read')
        return data
    monkeypatch.setattr(os,'read',mutate)
    with pytest.raises(ValueError,match='파일 변경'):
        workflow._bounded_regular_file(tmp_path,'target.txt')


@pytest.mark.parametrize('later_state, label', [
    ({'status': 'pending'}, '대기—이번 재개에서 실행하지 않음'),
    ({}, '확인 불가'),
    ({'status': 'hold', 'stale_reason': 'dead pid'}, '실행 결과 불확실'),
])
def test_recovery_guidance_distinguishes_recorded_waiting_and_uncertainty(tmp_path, capsys, later_state, label):
    task_file, task, _ = _recovery_case(tmp_path)
    task['steps'].append({'id': 'later', 'prompt': 'later', 'depends_on': ['external'],
        'completion': {'validator_id': 'json_object_fields', 'output_path': 'later.json', 'required_fields': ['status']}})
    task_file.write_text(json.dumps(task))
    state = omc_autopilot._load_state(tmp_path, task['id'])
    state['task_spec_sha256'] = workflow.task_spec_sha256(task)
    state['steps']['later'] = later_state
    omc_autopilot._save_state(tmp_path, task['id'], state)
    with patch.object(omc_autopilot, '_run_step') as provider:
        assert omc_autopilot.cmd_run(tmp_path, task_file, recovery_details=True) == 1
        provider.assert_not_called()
    output = capsys.readouterr().out
    assert f'later: {label}' in output
    if label == '실행 결과 불확실':
        assert '아직 한 단계를 특정할 수 없습니다' in output
        assert 'external, later의 provider 실행 이력' in output


def test_recovery_approval_binding_and_single_step(tmp_path, monkeypatch, capsys):
    task_file, task, evidence = _recovery_case(tmp_path)
    task['steps'].append({'id': 'later', 'prompt': 'later', 'depends_on': ['external'],
        'completion': {'validator_id': 'json_object_fields', 'output_path': 'later.json', 'required_fields': ['status']}})
    task_file.write_text(json.dumps(task))
    state = omc_autopilot._load_state(tmp_path, task['id'])
    state['task_spec_sha256'] = workflow.task_spec_sha256(task)
    omc_autopilot._save_state(tmp_path, task['id'], state)
    receipt = omc_autopilot.cmd_recovery_approve(tmp_path, task_file, 'external', evidence, 'human', True)
    assert receipt == 0
    approval_output = capsys.readouterr().out
    assert '승인된 실행 범위: external 한 단계' in approval_output
    assert '후속 단계는 자동 실행하지 않습니다' in approval_output
    calls = []
    monkeypatch.setattr(omc_autopilot, '_detect_executor', lambda _: 'codex')
    def run(root, step, **kwargs):
        calls.append(step['id'])
        (root / 'result.json').write_text('{"status":"new"}')
        return 0, 'VERDICT: APPROVE', None, None
    monkeypatch.setattr(omc_autopilot, '_run_step', run)
    assert omc_autopilot.cmd_run(tmp_path, task_file, recovery_step='external') == 0
    output = capsys.readouterr().out
    assert 'external: 이번 재개 검증 완료' in output
    assert output.index('단계 상태') < output.index('지금 할 일') < output.index('승인된 실행 범위')
    assert '후속 단계는 자동 실행하지 않습니다' in output
    assert calls == ['external']
    assert omc_autopilot.cmd_run(tmp_path, task_file, recovery_step='external') == 1
    assert omc_autopilot.cmd_run(tmp_path, task_file, recovery_details=True) == 1
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
        assert omc_autopilot.cmd_run(tmp_path, task_file, recovery_details=True) == 1
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
    cmd = [sys.executable, str(Path(omc_autopilot.__file__).with_name('omc.py')), 'autopilot', '--recovery-details', '--task-file', str(task_file), '--recovery-step', 'external']
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
