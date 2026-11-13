"""A: real entry/installation surfaces; no model or sandbox execution."""
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import fcntl

import pytest

ROOT = Path(__file__).resolve().parent.parent
OMC = ROOT / 'scripts/omc.py'


def run_entry(source, parent, *extra, script=OMC):
    return subprocess.run([sys.executable, '-B', str(script), 'workspace-entry',
        '--source', str(source), '--parent', str(parent), *extra],
        cwd=source, capture_output=True, text=True, timeout=30)


def data(result):
    assert result.returncode == 0, result.stdout + result.stderr
    return json.loads(result.stdout)


def repo(path):
    path.mkdir()
    subprocess.run(['git', 'init', '-q', str(path)], check=True)
    for k, v in [('user.email', 'fixture@example.com'), ('user.name', 'fixture')]:
        subprocess.run(['git', '-C', str(path), 'config', k, v], check=True)
    (path/'app.py').write_text('value = 1\n')
    subprocess.run(['git', '-C', str(path), 'add', 'app.py'], check=True)
    subprocess.run(['git', '-C', str(path), 'commit', '-qm', 'fixture'], check=True)
    return path


def snapshot(path):
    return {str(p.relative_to(path)):hashlib.sha256(p.read_bytes()).hexdigest()
            for p in path.rglob('*') if p.is_file() and not p.is_symlink()}


def ticket_args(value):
    return ['--ticket', value['ticket_path'], '--ticket-sha256', value['ticket_sha256']]


@pytest.mark.parametrize('condition', ['fresh', 'corrupt', 'locked'])
def test_start_preserves_source_and_registers_only_one_work(tmp_path, condition):
    source = repo(tmp_path/'source')
    lock = None
    if condition != 'fresh':
        (source/'.omc/state').mkdir(parents=True)
        (source/'.omc/pipeline_session.json').write_text('invalid prior work')
        if condition == 'locked':
            lock = (source/'.omc/state/.lock').open('w')
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    before = snapshot(source)
    try:
        value = data(run_entry(source, tmp_path/'spaces', '--intent', 'start', '--request', '새 작업 fixture'))
    finally:
        if lock: lock.close()
    assert value['preparation_status'] == 'WORKSPACE_READY'
    target = Path(value['workspace'])
    assert (target/'app.py').read_text() == 'value = 1\n'
    pending = json.loads((target/'.omc/state/pending-completion.json').read_text())
    assert value['work_id'] == pending['work_id']
    assert value['session_id'] == pending['session_id']
    assert len(list((target/'.omc/state/sessions').iterdir())) == 1
    assert not json.loads((target/'.omc/pipeline_session.json').read_text())['contract_confirmed']
    assert snapshot(source) == before
    again = data(run_entry(source, tmp_path/'spaces', '--intent', 'start', *ticket_args(value)))
    assert again['workspace'] == value['workspace']
    assert len(list((target/'.omc/state/sessions').iterdir())) == 1


def test_read_and_codex_bootstrap_never_initialize_source(tmp_path):
    source = repo(tmp_path/'source'); parent = tmp_path/'spaces'
    before = snapshot(source)
    value = data(run_entry(source, parent, '--intent', 'read'))
    assert value['preparation_status'] == 'READ_ONLY'
    assert not parent.exists()
    result = subprocess.run(['bash', str(ROOT/'templates/.agent-hooks/omc-session-start.sh'), 'codex'],
                            cwd=source, input='{"event_id":"fixture"}', capture_output=True, text=True)
    assert result.returncode == 0
    assert not (source/'.omc').exists()
    assert snapshot(source) == before


def test_prepare_is_query_only_until_explicit_launch(tmp_path):
    source = repo(tmp_path/'source'); parent = tmp_path/'spaces'
    value = data(run_entry(source, parent, '--intent', 'start', '--request', 'fixture', '--prepare-only'))
    assert value['preparation_status'] == 'RESERVED'
    assert not Path(value['workspace']).exists()
    status = data(run_entry(source, parent, '--intent', 'status', *ticket_args(value)))
    assert status['preparation_status'] == 'RESERVED'
    assert not Path(value['workspace']).exists()
    launched = data(run_entry(source, parent, '--intent', 'start', *ticket_args(value)))
    assert launched['preparation_status'] == 'WORKSPACE_READY'


@pytest.mark.parametrize('state', ['RESERVED', 'CLONING', 'REGISTERING'])
def test_interrupted_ticket_does_not_restart_or_block_other_job(tmp_path, state):
    source = repo(tmp_path/'source'); parent = tmp_path/'spaces'
    value = data(run_entry(source, parent, '--intent', 'start', '--request', 'fixture', '--prepare-only'))
    state_path = Path(value['ticket_path']).with_name('state.json')
    state_value = json.loads(state_path.read_text()); state_value['state'] = state
    state_value['owner'] = {'pid': 99999999, 'boot_id': 'fixture', 'start_token': 'old'}
    state_path.write_text(json.dumps(state_value))
    before = state_path.read_bytes()
    result = run_entry(source, parent, '--intent', 'start', *ticket_args(value))
    assert result.returncode == 1
    observed = json.loads(result.stdout)
    assert observed['preparation_status'] == 'UNVERIFIED'
    assert not Path(value['workspace']).exists()
    assert state_path.read_bytes() == before
    other = data(run_entry(source, parent, '--intent', 'start', '--request', 'different new task'))
    assert other['workspace'] != value['workspace']


def test_registration_before_journal_ready_is_reconciled_without_new_session(tmp_path):
    source = repo(tmp_path/'source'); parent = tmp_path/'spaces'
    value = data(run_entry(source, parent, '--intent', 'start', '--request', 'fixture'))
    state_path = Path(value['ticket_path']).with_name('state.json')
    saved = json.loads(state_path.read_text()); saved['state'] = 'REGISTERING'
    state_path.write_text(json.dumps(saved)); before = state_path.read_bytes()
    result = data(run_entry(source, parent, '--intent', 'status', *ticket_args(value)))
    assert result['preparation_status'] == 'WORKSPACE_READY'
    assert result['observed_only'] is True
    assert state_path.read_bytes() == before
    assert len(list((Path(value['workspace'])/'.omc/state/sessions').iterdir())) == 1


def test_continue_requires_exact_work_and_does_not_create_session(tmp_path):
    source = repo(tmp_path/'source'); parent = tmp_path/'spaces'
    value = data(run_entry(source, parent, '--intent', 'start', '--request', 'fixture'))
    args = ['--intent', 'continue', *ticket_args(value), '--work-id', value['work_id'],
            '--workspace', value['workspace']]
    result = data(run_entry(source, parent, *args))
    assert result['preparation_status'] == 'CONTINUE_READY'
    bad = run_entry(source, parent, *args[:-3], 'not-the-work', '--workspace', value['workspace'])
    assert bad.returncode != 0
    assert len(list((Path(value['workspace'])/'.omc/state/sessions').iterdir())) == 1


def test_bad_ticket_hash_and_changed_request_never_launch(tmp_path):
    source = repo(tmp_path/'source'); parent = tmp_path/'spaces'
    value = data(run_entry(source, parent, '--intent', 'start', '--request', 'fixture', '--prepare-only'))
    bad = run_entry(source, parent, '--intent', 'start', '--ticket', value['ticket_path'], '--ticket-sha256', '0'*64)
    assert bad.returncode == 1
    bad = run_entry(source, parent, '--intent', 'start', *ticket_args(value), '--request', 'changed')
    assert bad.returncode == 1
    assert not Path(value['workspace']).exists()


@pytest.mark.parametrize('relation', ['inside', 'metadata', 'symlink'])
def test_invalid_parent_is_rejected_before_writes(tmp_path, relation):
    source = repo(tmp_path/'source')
    if relation == 'inside': parent = source/'spaces'
    elif relation == 'metadata': parent = source/'.git/spaces'
    else:
        parent = tmp_path/'alias'; parent.symlink_to(source, target_is_directory=True)
    before = snapshot(source)
    result = run_entry(source, parent, '--intent', 'start', '--request', 'fixture')
    assert result.returncode == 1
    assert snapshot(source) == before


def test_codex_prompt_routes_before_output_or_state_write(tmp_path):
    source = repo(tmp_path/'source'); parent = tmp_path/'spaces'; output = source/'must-not-write.md'
    env = dict(os.environ, OMC_EXECUTOR='codex')
    result = subprocess.run([sys.executable, '-B', str(ROOT/'scripts/auto_prompt.py'),
        '--request', '새 작업 fixture', '--workspace-intent', 'start', '--workspace-parent', str(parent),
        '--out', str(output)], cwd=source, env=env, capture_output=True, text=True, timeout=30)
    value = data(result)
    assert value['preparation_status'] == 'WORKSPACE_READY'
    assert not output.exists() and not (source/'.omc').exists()


@pytest.fixture(scope='module')
def installed(tmp_path_factory):
    target = tmp_path_factory.mktemp('entry-install')/'project'; target.mkdir()
    result = subprocess.run([sys.executable, '-B', str(ROOT/'scripts/install.py'), '--target', str(target)],
                            capture_output=True, text=True, timeout=90)
    assert result.returncode == 0, result.stdout + result.stderr
    return target


def test_installed_hook_cli_and_skill_surface_use_new_entry(tmp_path, installed):
    source = repo(tmp_path/'source'); parent = tmp_path/'spaces'
    # Installed executable/hook, independent source; no template-only success.
    hook = installed/'.agent-hooks/omc-session-start.sh'
    result = subprocess.run(['bash', str(hook), 'codex'], cwd=source, input='{}', capture_output=True, text=True)
    assert result.returncode == 0 and not (source/'.omc').exists()
    value = data(run_entry(source, parent, '--intent', 'start', '--request', '새 작업', script=installed/'scripts/omc.py'))
    assert value['preparation_status'] == 'WORKSPACE_READY'
    task = (installed/'.agents/skills/omc-task/SKILL.md').read_text()
    plan = (installed/'.agents/skills/omc-plan/references/workflow.md').read_text()
    agents = (installed/'AGENTS.md').read_text()
    assert 'workspace-entry' in task and 'workspace-entry' in plan
    assert 'OMC_CODEX_READ_ONLY_BOOTSTRAP' in agents


def test_fresh_contract_allows_test_before_red_but_blocks_implementation(tmp_path):
    source = repo(tmp_path/'source'); parent = tmp_path/'spaces'
    value = data(run_entry(source, parent, '--intent', 'start', '--request', 'fixture'))
    target = Path(value['workspace']); pipeline = ROOT/'scripts/omc_pipeline_guard.py'
    def guard(*args):
        return subprocess.run([sys.executable, '-B', str(pipeline), '--target', str(target), *args], capture_output=True, text=True)
    import hashlib
    checks = {'session_id':value['session_id'], 'revision':1, 'request':'fixture feature',
              'approval_reference':'explicit fixture approval', 'checks':[{'id':'tests',
              'target':'feature.py', 'method':'python -m pytest test_feature.py -q',
              'pass_condition':'value=2', 'target_version':value['baseline_oid']}]}
    basis = target/'.omc/checks.json'; basis.write_text(json.dumps(checks))
    assert guard('approve-checks', '--file', str(basis)).returncode == 0
    assert guard('contract-done', '--required-checks', 'tests').returncode == 0
    assert guard('check', 'test_feature.py').returncode == 0
    assert guard('check', 'feature.py').returncode != 0
    test = target/'test_feature.py'
    test.write_text('def test_value():\n    from feature import value\n    assert value == 2\n')
    argv = [sys.executable, '-m', 'pytest', 'test_feature.py', '-q']
    red = subprocess.run(argv, cwd=target, capture_output=True, text=True)
    assert red.returncode == 1 and 'ModuleNotFoundError' in red.stdout
    output = target/'.omc/red.txt'; output.write_text(red.stdout+red.stderr)
    state = json.loads((target/'.omc/pipeline_session.json').read_text())
    evidence = {'session_id':value['session_id'], 'basis_sha256':state['basis_sha256'],
        'test_file':'test_feature.py','test_sha256':hashlib.sha256(test.read_bytes()).hexdigest(),
        'check_id':'tests','case_id':'test_value','expected_failure':'new feature missing',
        'command':' '.join(argv),'exit_code':1,'baseline_failed_cases':[],
        'failed_cases':['test_value'],'output_file':'.omc/red.txt',
        'output_sha256':hashlib.sha256(output.read_bytes()).hexdigest()}
    ep=target/'.omc/red.json'; ep.write_text(json.dumps(evidence))
    assert guard('red-done','test_feature.py','--evidence-file',str(ep)).returncode == 0
    assert guard('check','feature.py').returncode == 0
    (target/'feature.py').write_text('value = 2\n')
    green = subprocess.run(argv,cwd=target,capture_output=True,text=True)
    assert green.returncode == 0,green.stdout+green.stderr


def test_next_independent_job_can_share_parent_without_source_overlap(tmp_path):
    source = repo(tmp_path/'source'); parent = tmp_path/'spaces'
    first = data(run_entry(source, parent, '--intent', 'start', '--request', 'first'))
    current = Path(first['workspace']); before = snapshot(current)
    second = data(run_entry(current, parent, '--intent', 'start', '--request', 'unrelated second'))
    assert second['workspace'] != first['workspace']
    assert snapshot(current) == before


def test_two_launches_of_one_ticket_create_one_session(tmp_path):
    source = repo(tmp_path/'source'); parent = tmp_path/'spaces'
    value = data(run_entry(source, parent, '--intent', 'start', '--request', 'fixture', '--prepare-only'))
    argv = [sys.executable, '-B', str(OMC), 'workspace-entry', '--intent', 'start',
            '--source', str(source), '--parent', str(parent), *ticket_args(value)]
    children = [subprocess.Popen(argv, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True) for _ in range(2)]
    outputs = [c.communicate(timeout=30) for c in children]
    assert all(c.returncode == 0 for c in children), outputs
    assert len(list((Path(value['workspace'])/'.omc/state/sessions').iterdir())) == 1


def test_trusted_kit_inside_source_does_not_write_bytecode(tmp_path):
    import shutil
    source = tmp_path/'source'
    subprocess.run(['git', 'clone', '--no-hardlinks', '--quiet', str(ROOT), str(source)], check=True)
    # Overlay the current implementation, including the not-yet-committed entry.
    for name in ['scripts/omc.py', 'scripts/omc_workspace_entry.py', 'scripts/omc_guard.py']:
        shutil.copy2(ROOT/name, source/name)
    before = snapshot(source)
    result = run_entry(source, tmp_path/'spaces', '--intent', 'start', '--request', 'fixture', script=source/'scripts/omc.py')
    assert data(result)['preparation_status'] == 'WORKSPACE_READY'
    assert snapshot(source) == before


def test_subdirectory_source_ticket_replay_normalizes_git_root(tmp_path):
    source = repo(tmp_path/'source'); (source/'folder').mkdir(); parent = tmp_path/'spaces'
    value = data(run_entry(source/'folder', parent, '--intent', 'start', '--request', 'fixture'))
    again = data(run_entry(source/'folder', parent, '--intent', 'start', *ticket_args(value)))
    assert again['workspace'] == value['workspace']


def test_replaced_workspace_git_directory_is_not_reported_ready(tmp_path):
    source = repo(tmp_path/'source'); parent = tmp_path/'spaces'
    value = data(run_entry(source, parent, '--intent', 'start', '--request', 'fixture'))
    target = Path(value['workspace']); (target/'.git').rename(target/'old-git'); (target/'.git').mkdir()
    result = run_entry(source, parent, '--intent', 'status', *ticket_args(value))
    assert result.returncode == 1
    assert json.loads(result.stdout)['preparation_status'] == 'UNVERIFIED'


def test_main_prompt_surface_uses_explicit_route(tmp_path):
    source = repo(tmp_path/'source'); parent = tmp_path/'spaces'; before = snapshot(source)
    result = subprocess.run([sys.executable, '-B', str(OMC), 'prompt', '새 작업 fixture',
        '--workspace-intent', 'start', '--workspace-parent', str(parent)],
        cwd=source, capture_output=True, text=True, timeout=30)
    assert data(result)['preparation_status'] == 'WORKSPACE_READY'
    assert snapshot(source) == before


def test_codex_prompt_defaults_to_read_before_state_or_output(tmp_path):
    source = repo(tmp_path/'source'); before = snapshot(source)
    result = subprocess.run([sys.executable, '-B', str(ROOT/'scripts/auto_prompt.py'),
        '--request', '상태 조회', '--out', str(source/'unwanted.md')], cwd=source,
        env=dict(os.environ, OMC_EXECUTOR='codex'), capture_output=True, text=True)
    assert data(result)['preparation_status'] == 'READ_ONLY'
    assert snapshot(source) == before


def test_status_does_not_require_old_source_to_still_exist(tmp_path):
    source = repo(tmp_path/'source'); parent = tmp_path/'spaces'
    value = data(run_entry(source, parent, '--intent', 'start', '--request', 'fixture'))
    source.rename(tmp_path/'retired-source')
    result = subprocess.run([sys.executable, '-B', str(OMC), 'workspace-entry', '--source', str(source),
        '--intent', 'status', *ticket_args(value)], cwd=tmp_path, capture_output=True, text=True)
    assert data(result)['preparation_status'] == 'WORKSPACE_READY'


def test_explicit_retry_preserves_failed_attempt_and_uses_new_path(tmp_path):
    source = repo(tmp_path/'source'); parent = tmp_path/'spaces'
    first = data(run_entry(source, parent, '--intent', 'start', '--request', 'fixture', '--prepare-only'))
    state_path = Path(first['ticket_path']).with_name('state.json')
    state_path.write_text(json.dumps({'state':'FAILED','owner':None,'reason_code':'fixture_failed_preparation'}))
    before = state_path.read_bytes()
    retry = data(run_entry(source, parent, '--intent', 'start', *ticket_args(first), '--retry', '--prepare-only'))
    assert retry['preparation_status'] == 'RESERVED'
    assert retry['workspace'] != first['workspace']
    packet = json.loads(Path(retry['ticket_path']).read_text())
    assert packet['attempt'] == 2 and packet['retry_of']['ticket_sha256'] == first['ticket_sha256']
    assert state_path.read_bytes() == before
    assert not Path(first['workspace']).exists() and not Path(retry['workspace']).exists()


def test_retry_does_not_replace_a_ready_job(tmp_path):
    source = repo(tmp_path/'source'); parent = tmp_path/'spaces'
    value = data(run_entry(source, parent, '--intent', 'start', '--request', 'fixture'))
    result = run_entry(source, parent, '--intent', 'start', *ticket_args(value), '--retry')
    assert result.returncode == 1
    assert len(list((parent/'jobs').iterdir())) == 1


def failed_ticket(source, parent):
    value = data(run_entry(source, parent, '--intent', 'start', '--request', 'retry fixture', '--prepare-only'))
    Path(value['ticket_path']).with_name('state.json').write_text(json.dumps(
        {'state': 'FAILED', 'owner': None, 'reason_code': 'fixture_failure'}))
    return value


def test_retry_replay_reuses_one_child_even_after_launch(tmp_path):
    source = repo(tmp_path/'source'); parent = tmp_path/'spaces'
    first = failed_ticket(source, parent)
    old = snapshot(Path(first['ticket_path']).parent)
    retry = data(run_entry(source, parent, '--intent', 'start', *ticket_args(first), '--retry', '--prepare-only'))
    replay = data(run_entry(source, parent, '--intent', 'start', *ticket_args(first), '--retry', '--prepare-only'))
    assert replay['ticket_path'] == retry['ticket_path']
    launched = data(run_entry(source, parent, '--intent', 'start', *ticket_args(first), '--retry'))
    again = data(run_entry(source, parent, '--intent', 'start', *ticket_args(first), '--retry'))
    assert again['workspace'] == launched['workspace'] == retry['workspace']
    assert again['work_id'] == launched['work_id']
    assert len(list((Path(again['workspace'])/'.omc/state/sessions').iterdir())) == 1
    for name, sha in old.items():
        assert hashlib.sha256((Path(first['ticket_path']).parent/name).read_bytes()).hexdigest() == sha
    assert len(list((parent/'jobs').iterdir())) == 2


def test_retry_concurrent_retransmissions_share_one_attempt(tmp_path):
    source = repo(tmp_path/'source'); parent = tmp_path/'spaces'
    first = failed_ticket(source, parent)
    args = [sys.executable, '-B', str(OMC), 'workspace-entry', '--intent', 'start',
            *ticket_args(first), '--retry', '--prepare-only']
    calls = [subprocess.Popen(args, cwd=source, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True) for _ in range(3)]
    values = []
    for call in calls:
        out, err = call.communicate(timeout=10)
        assert call.returncode == 0, out + err
        values.append(json.loads(out))
    assert len({v['ticket_path'] for v in values}) == 1
    assert len(list((parent/'jobs').iterdir())) == 2


def test_continue_returns_current_confirmed_session_after_same_work_reentry(tmp_path):
    source = repo(tmp_path/'source'); parent = tmp_path/'spaces'
    first = data(run_entry(source, parent, '--intent', 'start', '--request', 'fixture'))
    workspace = Path(first['workspace'])
    subprocess.run([sys.executable, '-B', str(ROOT/'scripts/omc_guard.py'), 'sync-require',
        '--target', str(workspace), '--mode', 'autopilot', '--title', 'omc-task',
        '--request', 'same work correction', '--roles', 'senior_coding', '--work-class', 'implementation',
        '--completion-action', 'continue', '--work-id', first['work_id'], '--for', 'task'],
        capture_output=True, text=True, check=True)
    current = json.loads((workspace/'.omc/state/latest.json').read_text())['latest_confirmed_session_id']
    before = snapshot(workspace)
    value = data(run_entry(source, parent, '--intent', 'continue', *ticket_args(first),
        '--workspace', str(workspace), '--work-id', first['work_id']))
    assert current != first['session_id']
    assert value['session_id'] == current
    assert value['preparation_session_id'] == first['session_id']
    assert snapshot(workspace) == before


@pytest.mark.parametrize('record', ['packet.json', 'state.json', 'launch.lock'])
def test_special_file_query_fails_without_waiting_for_fifo_writer(tmp_path, record):
    source = repo(tmp_path/'source'); parent = tmp_path/'spaces'
    first = data(run_entry(source, parent, '--intent', 'start', '--request', 'fixture', '--prepare-only'))
    path = Path(first['ticket_path']).with_name(record)
    if path.exists(): path.unlink()
    os.mkfifo(path)
    result = subprocess.run([sys.executable, '-B', str(OMC), 'workspace-entry', '--intent', 'status',
        *ticket_args(first), '--timeout-sec', '0.1'], cwd=source, capture_output=True, text=True, timeout=2)
    assert result.returncode == 1
    assert json.loads(result.stdout)['reason_code'] == 'regular_record_required'


@pytest.mark.parametrize('failure_point', ['before_directory', 'before_packet', 'before_state'])
def test_retry_reservation_recovers_same_child_after_interruption(tmp_path, monkeypatch, failure_point):
    import omc_workspace_entry as entry
    source = repo(tmp_path/'source'); parent = tmp_path/'spaces'
    first = failed_ticket(source, parent)
    args = entry._parser().parse_args(['--intent', 'start', *ticket_args(first), '--retry'])
    old_path, packet = entry._load_ticket(args)
    save = entry._save
    def interrupted_reserve(child):
        raise OSError('injected_after_retry_intent')
    def interrupted_save(path, value, **kwargs):
        if path.parent != old_path.parent and path.name == ('packet.json' if failure_point == 'before_packet' else 'state.json'):
            raise OSError('injected_reservation_interruption')
        return save(path, value, **kwargs)
    with monkeypatch.context() as patch:
        if failure_point == 'before_directory': patch.setattr(entry, '_reserve_packet', interrupted_reserve)
        else: patch.setattr(entry, '_save', interrupted_save)
        with pytest.raises(OSError): entry.retry_ticket(old_path, packet, args)
    link = json.loads(old_path.with_name('retry.json').read_text())
    child = link['packet']
    recovered = data(run_entry(source, parent, '--intent', 'start', *ticket_args(first), '--retry', '--prepare-only'))
    assert recovered['workspace'] == child['workspace']
    assert recovered['preparation_status'] == 'RESERVED'
    assert not Path(child['workspace']).exists()
    assert len(list((parent/'jobs').iterdir())) == 2
    launched = data(run_entry(source, parent, '--intent', 'start', *ticket_args(first), '--retry'))
    assert launched['workspace'] == recovered['workspace']


def test_further_retry_targets_failed_child_and_keeps_original_baseline(tmp_path):
    source = repo(tmp_path/'source'); parent = tmp_path/'spaces'
    first = failed_ticket(source, parent)
    child = data(run_entry(source, parent, '--intent', 'start', *ticket_args(first), '--retry', '--prepare-only'))
    Path(child['ticket_path']).with_name('state.json').write_text(json.dumps({'state':'FAILED','owner':None}))
    (source/'app.py').write_text('value = 2\n')
    subprocess.run(['git', '-C', str(source), 'commit', '-qam', 'later'], check=True)
    grandchild = data(run_entry(source, parent, '--intent', 'start', *ticket_args(child), '--retry', '--prepare-only'))
    assert grandchild['baseline_oid'] == first['baseline_oid']
    packet = json.loads(Path(grandchild['ticket_path']).read_text())
    assert packet['attempt'] == 3 and packet['origin_job_id'] == first['job_id']
    replay = run_entry(source, parent, '--intent', 'start', *ticket_args(first), '--retry', '--prepare-only')
    assert replay.returncode == 1
    assert json.loads(replay.stdout)['workspace'] == child['workspace']
    assert len(list((parent/'jobs').iterdir())) == 3


@pytest.mark.parametrize('mismatch', ['unconfirmed_latest', 'latest_pointer', 'missing_lineage',
                                    'wrong_session_work', 'wrong_session_id', 'unconfirmed_owner', 'missing_owner'])
def test_continue_rejects_invalid_current_session_connection(tmp_path, mismatch):
    source = repo(tmp_path/'source'); parent = tmp_path/'spaces'
    first = data(run_entry(source, parent, '--intent', 'start', '--request', 'fixture'))
    workspace = Path(first['workspace']); state = workspace/'.omc/state'
    latest_path = state/'latest.json'; pending_path = state/'pending-completion.json'
    session_path = state/'sessions'/first['session_id']/'session.json'
    if mismatch in {'unconfirmed_latest','latest_pointer'}:
        path = latest_path; value = json.loads(path.read_text())
        if mismatch == 'unconfirmed_latest': value['latest_confirmation'] = None
        else: value['latest_session_id'] = 'another_session'
    elif mismatch in {'missing_lineage','missing_owner'}:
        path = pending_path; value = json.loads(path.read_text())
        if mismatch == 'missing_lineage': value['session_ids'] = []
        else: value['session_id'] = '20260101T000000-aaaaaaaa'
    else:
        path = session_path; value = json.loads(path.read_text())
        if mismatch == 'wrong_session_work': value['work_id'] = 'unrelated'
        elif mismatch == 'wrong_session_id': value['session_id'] = 'other'
        else: value['confirmation'] = None
    path.write_text(json.dumps(value)); before = snapshot(workspace)
    result = run_entry(source, parent, '--intent', 'continue', *ticket_args(first),
        '--workspace', str(workspace), '--work-id', first['work_id'])
    assert result.returncode == 1
    assert json.loads(result.stdout)['reason_code'] == 'continue_session_binding_mismatch'
    assert snapshot(workspace) == before


@pytest.mark.parametrize('kind', ['directory', 'device', 'symlink', 'oversize'])
def test_record_reader_rejects_nonregular_link_and_oversize(tmp_path, kind):
    import omc_workspace_entry as entry
    path = tmp_path/'record'
    if kind == 'directory': path.mkdir()
    elif kind == 'device': path = Path('/dev/null')
    elif kind == 'symlink': path.symlink_to('/dev/null')
    else: path.write_bytes(b'x'*(entry.MAX_RECORD_BYTES+1))
    with pytest.raises((ValueError, OSError)):
        entry._read(path)


def test_continue_accepts_confirmed_preserve_session_without_changing_pending_owner(tmp_path):
    source = repo(tmp_path/'source'); parent = tmp_path/'spaces'
    first = data(run_entry(source, parent, '--intent', 'start', '--request', 'fixture'))
    workspace = Path(first['workspace'])
    subprocess.run([sys.executable, '-B', str(OMC), 'state', 'sync-session', '--target', str(workspace),
        '--mode', 'autopilot', '--title', 'read role fixture', '--request', 'same work read role',
        '--roles', 'code_review', '--completion-action', 'preserve-if-present'],
        capture_output=True, text=True, check=True)
    pending = json.loads((workspace/'.omc/state/pending-completion.json').read_text())
    current = json.loads((workspace/'.omc/state/latest.json').read_text())['latest_confirmed_session_id']
    assert current not in pending['session_ids']
    before = snapshot(workspace)
    value = data(run_entry(source, parent, '--intent', 'continue', *ticket_args(first),
        '--workspace', str(workspace), '--work-id', first['work_id']))
    assert value['session_id'] == current
    assert value['pending_owner_session_id'] == pending['session_id'] == first['session_id']
    assert snapshot(workspace) == before


@pytest.mark.parametrize('shared', ['parent', 'jobs'])
def test_independent_requests_can_create_shared_directory_concurrently(tmp_path, monkeypatch, shared):
    import concurrent.futures
    import threading
    import omc_workspace_entry as entry
    source = repo(tmp_path/'source'); parent = tmp_path/'spaces'
    if shared == 'jobs': parent.mkdir(mode=0o700)
    raced = parent if shared == 'parent' else parent/'jobs'
    barrier = threading.Barrier(2)
    mkdir = Path.mkdir
    def synchronized_mkdir(path, *args, **kwargs):
        if path == raced: barrier.wait(timeout=5)
        return mkdir(path, *args, **kwargs)
    monkeypatch.setattr(Path, 'mkdir', synchronized_mkdir)
    before = snapshot(source)
    def prepare(request):
        args = entry._parser().parse_args(['--intent','start','--source',str(source),
            '--parent',str(parent),'--request',request,'--prepare-only'])
        return entry.prepare(args)
    with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(prepare, ['independent A', 'independent B']))
    assert results[0][0] != results[1][0]
    assert len(list((parent/'jobs').iterdir())) == 2
    for path, packet in results:
        assert json.loads(path.with_name('state.json').read_text()) == {'state':'RESERVED','owner':None}
        assert not Path(packet['workspace']).exists()
    assert snapshot(source) == before


@pytest.mark.parametrize('kind', ['unsafe_mode', 'symlink', 'file', 'foreign_owner'])
def test_shared_directory_still_rejects_invalid_existing_entry(tmp_path, monkeypatch, kind):
    import types
    import omc_workspace_entry as entry
    path = tmp_path/'shared'
    if kind == 'symlink':
        target = tmp_path/'target'; target.mkdir(mode=0o700); path.symlink_to(target)
    elif kind == 'file': path.write_text('preserve')
    else: path.mkdir(mode=0o755 if kind == 'unsafe_mode' else 0o700)
    if kind == 'foreign_owner':
        original = Path.stat
        def foreign_stat(p, *a, **kw):
            value = original(p, *a, **kw)
            if p == path: return types.SimpleNamespace(st_mode=value.st_mode, st_uid=os.getuid()+1)
            return value
        monkeypatch.setattr(Path, 'stat', foreign_stat)
    with pytest.raises((ValueError, OSError)): entry._private_directory(path)
    assert path.exists()


def test_shared_directory_rechecks_link_created_during_mkdir(tmp_path, monkeypatch):
    import omc_workspace_entry as entry
    path = tmp_path/'shared'; target = tmp_path/'target'; target.mkdir(mode=0o700)
    mkdir = Path.mkdir
    def create_link(p, *a, **kw):
        if p == path: p.symlink_to(target); return None
        return mkdir(p, *a, **kw)
    monkeypatch.setattr(Path, 'mkdir', create_link)
    with pytest.raises(ValueError, match='workspace_path_symlink'): entry._private_directory(path)
    assert path.is_symlink()
