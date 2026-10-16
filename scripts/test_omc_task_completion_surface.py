"""Exercise the actual task completion CLI and its user-visible report."""
import json
import subprocess
import sys
from pathlib import Path

import pytest
import omc_pipeline_guard as guard

SCRIPT = Path(__file__).with_name('omc_pipeline_guard.py')


def run(root, *args):
    return subprocess.run([sys.executable, str(SCRIPT), '--target', str(root), *args], capture_output=True, text=True)


@pytest.fixture
def project(tmp_path):
    state = tmp_path / '.omc' / 'state'
    state.mkdir(parents=True)
    (state / 'latest.json').write_text(json.dumps({'latest_confirmed_session_id': 'current','latest_session_id':'current'}))
    return tmp_path


def prepare(root):
    proposal = root / 'approved-checks.json'
    proposal.write_text(json.dumps({'session_id':'current','revision':1,'request':'feature',
        'approval_reference':'fixture user approval','checks':[
            {'id':n,'target':n,'method':'run','pass_condition':'passed','target_version':'current'} for n in ['tests','browser']]}))
    if not (root/'.omc/verification-bases/current/1.json').exists():
        assert run(root,'approve-checks','--file',str(proposal)).returncode == 0
    result = run(root, 'contract-done', '--required-checks', 'tests,browser')
    assert result.returncode == 0, result.stderr
    assert run(root, 'red-done', 'test_feature.py').returncode == 0


def finish(root, checks, implementation="applied"):
    path = root / 'verification.json'
    record = {'session_id': 'current', 'checks': checks}
    try:
        basis=guard._load_approved_basis(root,'current')
        record['basis_sha256']=guard._basis_digest(basis)
        for item in basis['checks']:
            if item['id'] in checks:
                checks[item['id']]['definition_sha256']=guard._check_digest(item)
                checks[item['id']]['code_snapshot_sha256']=guard._code_snapshot_digest(root,path)
    except ValueError:
        pass
    if implementation is not None:
        record['implementation'] = {'status': implementation, 'evidence': 'current implementation checked'}
    path.write_text(json.dumps(record))
    return run(root, 'complete-task', '--verification-file', str(path))


def check(status='passed'):
    return {'status': status, 'evidence': 'local verification output'}


def test_missing_registration_cannot_report_done(project):
    result = finish(project, {'tests': check(), 'browser': check()}, implementation=None)
    assert result.returncode == 1
    assert result.stdout.startswith('구현 여부 확인 불가·검증 미완료\n')
    assert '"outcome": "blocked"' in result.stdout


@pytest.mark.parametrize('checks', [
    {'tests': check(), 'browser': check('unrun')},
    {'tests': check()},
    {'tests': check(), 'browser': {'status': 'passed', 'evidence': ''}},
])
def test_required_browser_not_replaced_by_tests(project, checks):
    prepare(project)
    result = finish(project, checks)
    assert result.returncode == 1
    assert result.stdout.startswith('구현됨·검증 미완료\n')
    assert 'VERDICT: BLOCK' in result.stdout


def test_old_session_red_is_not_current_registration(project):
    prepare(project)
    path = project / '.omc' / 'pipeline_session.json'
    state = json.loads(path.read_text())
    state['red_session_id'] = 'previous'
    path.write_text(json.dumps(state))
    result = finish(project, {'tests': check(), 'browser': check()})
    assert result.returncode == 1
    assert 'RED' in result.stdout


def test_all_required_checks_render_done(project):
    prepare(project)
    result = finish(project, {'tests': check(), 'browser': check()})
    assert result.returncode == 0
    assert result.stdout.startswith('필수 검증 통과·작업 완료\n')
    assert '"outcome": "done"' in result.stdout
    assert result.stdout.rstrip().endswith('VERDICT: PROCEED')


def test_unstaged_implementation_does_not_use_empty_staged_gate(project):
    subprocess.run(['git', 'init', '-q'], cwd=project, check=True)
    source = project / 'feature.py'
    source.write_text('value = 1\n')
    subprocess.run(['git', 'add', 'feature.py'], cwd=project, check=True)
    subprocess.run(['git', '-c', 'user.name=Fixture', '-c', 'user.email=fixture@example.invalid', 'commit', '-qm', 'fixture'], cwd=project, check=True)
    prepare(project)
    source.write_text('value = 2\n')
    result = finish(project, {'tests': check(), 'browser': check()})
    assert result.returncode == 1
    assert '미스테이징' in result.stdout
    subprocess.run(['git', 'add', 'feature.py'], cwd=project, check=True)
    assert finish(project, {'tests': check(), 'browser': check()}).returncode == 0


@pytest.mark.parametrize('status,first', [('failed', '검증 실패·수정 필요'), ('mismatch', '증거 불일치·재검증 필요')])
def test_report_status_matches_failed_verification(project, status, first):
    prepare(project)
    result = finish(project, {'tests': check(), 'browser': check(status)})
    assert result.returncode == 1
    assert result.stdout.splitlines()[0] == first


def test_unknown_or_malformed_verification_is_not_success(project):
    prepare(project)
    path = project / 'verification.json'
    path.write_text('[]')
    result = run(project, 'complete-task', '--verification-file', str(path))
    assert result.returncode == 1
    assert 'VERDICT: BLOCK' in result.stdout


def test_untracked_implementation_is_not_an_empty_scope(project):
    subprocess.run(['git', 'init', '-q'], cwd=project, check=True)
    prepare(project)
    (project / 'new_feature.py').write_text('value = 1\n')
    result = finish(project, {'tests': check(), 'browser': check()})
    assert result.returncode == 1
    assert '미스테이징' in result.stdout


def test_verification_from_another_session_is_blocked(project):
    prepare(project)
    path = project / 'verification.json'
    path.write_text(json.dumps({'session_id': 'previous', 'checks': {'tests': check(), 'browser': check()}}))
    result = run(project, 'complete-task', '--verification-file', str(path))
    assert result.returncode == 1
    assert result.stdout.startswith('증거 불일치·재검증 필요\n')


@pytest.mark.parametrize('untracked', [False, True])
def test_preexisting_unrelated_changes_do_not_block_current_task(project, untracked):
    subprocess.run(['git', 'init', '-q'], cwd=project, check=True)
    source = project / 'unrelated.py'
    source.write_text('value = 1\n')
    if not untracked:
        subprocess.run(['git', 'add', 'unrelated.py'], cwd=project, check=True)
        subprocess.run(['git', '-c', 'user.name=Fixture', '-c', 'user.email=fixture@example.invalid', 'commit', '-qm', 'fixture'], cwd=project, check=True)
        source.write_text('value = 2\n')
    prepare(project)
    (project / 'current.py').write_text('current = 1\n')
    subprocess.run(['git', 'add', 'current.py'], cwd=project, check=True)
    result = finish(project, {'tests': check(), 'browser': check()})
    assert result.returncode == 0, result.stdout
    assert source.read_text() == ('value = 1\n' if untracked else 'value = 2\n')
    source.write_text('value = 3\n')
    assert finish(project, {'tests': check(), 'browser': check()}).returncode == 1
    # Re-registering the same contract cannot reclassify current edits as old changes.
    prepare(project)
    assert finish(project, {'tests': check(), 'browser': check()}).returncode == 1


@pytest.mark.parametrize('implementation', [None, 'unknown'])
def test_no_confirmed_implementation_cannot_claim_applied_or_done(project, implementation):
    prepare(project)
    result = finish(project, {'tests': check(), 'browser': check()}, implementation)
    assert result.returncode == 1
    assert result.stdout.startswith('구현 여부 확인 불가·검증 미완료\n')
    assert '"outcome": "blocked"' in result.stdout


def test_empty_implementation_evidence_is_unknown(project):
    prepare(project)
    record = {'session_id': 'current', 'checks': {'tests': check(), 'browser': check()},
              'implementation': {'status': 'applied', 'evidence': ''}}
    basis=guard._load_approved_basis(project,'current')
    record['basis_sha256']=guard._basis_digest(basis)
    for item in basis['checks']:
        record['checks'][item['id']]['definition_sha256']=guard._check_digest(item)
        record['checks'][item['id']]['code_snapshot_sha256']=guard._code_snapshot_digest(project,project/'verification.json')
    path = project / 'verification.json'
    path.write_text(json.dumps(record))
    result = run(project, 'complete-task', '--verification-file', str(path))
    assert result.returncode == 1
    assert result.stdout.startswith('구현 여부 확인 불가·검증 미완료\n')


def test_git_project_without_baseline_does_not_assume_clean_scope(project):
    subprocess.run(['git', 'init', '-q'], cwd=project, check=True)
    prepare(project)
    path = project / '.omc/pipeline_session.json'
    state = json.loads(path.read_text())
    state.pop('worktree_baseline')
    path.write_text(json.dumps(state))
    result = finish(project, {'tests': check(), 'browser': check()})
    assert result.returncode == 1
    assert '변경 기준이 없습니다' in result.stdout


def test_verification_path_is_session_bound_and_internal(project):
    prepare(project)
    result=run(project,'verification-path')
    assert result.returncode==0,result.stderr
    assert Path(result.stdout.strip()) == project/'.omc/verification/current.json'


@pytest.mark.parametrize('bad_json',[False,True])
def test_record_read_error_does_not_erase_real_registrations(project,bad_json):
    prepare(project)
    path=project/'verification.json'
    if bad_json:path.write_text('{bad')
    result=run(project,'complete-task','--verification-file',str(path))
    assert result.returncode==1
    assert '검증 기록을 읽을 수 없습니다' in result.stdout
    assert 'CONTRACT 등록이 없습니다' not in result.stdout
    assert 'RED 등록이 없습니다' not in result.stdout


def test_external_verification_is_refused_without_modification(project,tmp_path):
    prepare(project)
    external=project.parent/(project.name+'-external.json')
    external.write_text('{"keep":true}')
    for path in [external,project/'..'/external.name]:
        result=run(project,'complete-task','--verification-file',str(path))
        assert result.returncode==1
        assert '작업 폴더 밖' in result.stdout
    link=project/'linked.json';link.symlink_to(external)
    result=run(project,'complete-task','--verification-file',str(link))
    assert result.returncode==1
    assert '작업 폴더 밖' in result.stdout
    assert external.read_text()=='{"keep":true}'


def test_verification_path_rejects_external_parent_symlink(project):
    prepare(project)
    outside=project.parent/(project.name+'-records');outside.mkdir()
    (project/'.omc/verification').symlink_to(outside,target_is_directory=True)
    result=run(project,'verification-path')
    assert result.returncode==1
    assert '작업 폴더 밖' in result.stdout
    assert not list(outside.iterdir())


def test_required_checks_cannot_be_reduced_in_same_session(project):
    prepare(project)
    result = run(project, 'contract-done', '--required-checks', 'tests')
    assert result.returncode == 1
    state = json.loads((project/'.omc/pipeline_session.json').read_text())
    assert state['required_checks'] == ['tests', 'browser']
    assert finish(project, {'tests': check()}).returncode == 1


def test_same_checks_are_idempotent_but_new_session_can_set_new_checks(project):
    prepare(project)
    assert run(project, 'contract-done', '--required-checks', 'browser,tests').returncode == 0
    latest = project/'.omc/state/latest.json'
    latest.write_text(json.dumps({'latest_confirmed_session_id': 'next'}))
    assert run(project, 'contract-done', '--required-checks', 'tests').returncode == 0
    state = json.loads((project/'.omc/pipeline_session.json').read_text())
    assert state['required_checks'] == ['tests']
