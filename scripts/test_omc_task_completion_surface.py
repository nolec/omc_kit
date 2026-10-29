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
    from test_omc_pipeline_link_red import red_proof
    proof = red_proof(root, json.loads(proposal.read_text()))
    assert run(root, 'red-done', 'test_app.py', '--evidence-file', str(proof)).returncode == 0


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


def preserved_work(root, role='code_review', title='omc-review'):
    """Real pending schema and role transition; no approval/session mocks."""
    import omc_state as state
    subprocess.run(['git', 'init', '-q'], cwd=root, check=True)
    (root/'app.py').write_text('value = 1\n')
    subprocess.run(['git', 'add', 'app.py'], cwd=root, check=True)
    subprocess.run(['git', '-c', 'core.hooksPath=/dev/null', '-c', 'user.name=Fixture',
                    '-c', 'user.email=fixture@localhost', 'commit', '-qm', 'base'], cwd=root, check=True)
    head = subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=root).decode().strip()
    task = {'session_id':'current', 'role_ids':['senior_coding'], 'request':'feature',
            'confirmation':{'status':'confirmed'}, 'git':{'head':head},
            'work_id':'work', 'work_class':'implementation', 'created_at':'fixture-time',
            'lineage_root_session_id':'current', 'lineage_index':0,
            'lineage_previous_session_id':None}
    folder = root/'.omc/state/sessions/current'; folder.mkdir(parents=True)
    (folder/'session.json').write_text(json.dumps(task))
    pending = {'schema_version':3, 'session_id':'current', 'baseline_head':head,
               'request_sha256':state._canonical_sha256('feature'), 'work_id':'work',
               'work_class':'implementation', 'work_class_locked_at':'fixture-time',
               'root_session_id':'current', 'session_ids':['current'], 'rework_count':0}
    (root/'.omc/state/pending-completion.json').write_text(json.dumps(pending))
    prepare(root)
    assert finish(root, {'tests':check(), 'browser':check()}).returncode == 0
    observer = state.record_session(root, mode='autopilot', title=title,
                                    request='inspect same work', role_ids=[role], confirmed=True)
    return observer


@pytest.mark.parametrize('role,title', [('code_review','omc-review'),
                         ('analysis','omc-investigate'), ('directive','omc-ship')])
def test_completion_uses_pending_owner_after_preserving_role(project, role, title):
    observer = preserved_work(project, role, title)
    assert observer['work_id'] == 'work'
    assert observer['completion_action'] == 'preserve-if-present'
    result = run(project, 'complete-task', '--verification-file', str(project/'verification.json'))
    assert result.returncode == 0, result.stdout + result.stderr
    # Collector pins the evidence owner, not the latest observer.
    assert guard.evaluate_task_completion(project, project/'verification.json',
                                         expected_session_id='current')['outcome'] == 'done'
    assert guard.evaluate_task_completion(project, project/'verification.json',
                                         expected_session_id=observer['session_id'])['outcome'] == 'blocked'


@pytest.mark.parametrize('defect', ['work', 'lineage', 'baseline', 'request',
                                   'unconfirmed', 'code', 'pipeline-owner', 'foreign-evidence',
                                   'observer-work', 'lineage-link', 'missing-pending'])
def test_completion_preserving_role_cannot_reuse_other_evidence(project, defect):
    observer = preserved_work(project)
    pending_path = project/'.omc/state/pending-completion.json'
    pending = json.loads(pending_path.read_text())
    if defect in {'work','lineage','baseline','request'}:
        key = {'work':'work_id','lineage':'session_ids','baseline':'baseline_head',
               'request':'request_sha256'}[defect]
        pending[key] = ['foreign','current'] if defect == 'lineage' else 'foreign'
        pending_path.write_text(json.dumps(pending))
    elif defect == 'unconfirmed':
        path = project/'.omc/state/latest.json'; latest = json.loads(path.read_text())
        latest['latest_confirmed_session_id'] = 'current'; path.write_text(json.dumps(latest))
    elif defect == 'observer-work':
        path = project/'.omc/state/sessions'/observer['session_id']/'session.json'
        data = json.loads(path.read_text()); data['work_id'] = 'foreign'
        path.write_text(json.dumps(data))
    elif defect == 'lineage-link':
        path = project/'.omc/state/sessions/current/session.json'
        data = json.loads(path.read_text()); data['lineage_previous_session_id'] = 'foreign'
        path.write_text(json.dumps(data))
    elif defect == 'missing-pending':
        pending_path.unlink()
    elif defect == 'code':
        (project/'app.py').write_text('value = 2\n')
    elif defect == 'pipeline-owner':
        path = project/'.omc/pipeline_session.json'; data = json.loads(path.read_text())
        data['session_id'] = 'foreign'; path.write_text(json.dumps(data))
    else:
        path = project/'verification.json'; data = json.loads(path.read_text())
        data['session_id'] = 'foreign'; path.write_text(json.dumps(data))
    result = run(project, 'complete-task', '--verification-file', str(project/'verification.json'))
    assert result.returncode == 1, result.stdout
    assert 'VERDICT: BLOCK' in result.stdout


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


def test_fixed_validation_template_requires_structured_test_receipt(project):
    prepare(project)
    proposal = json.loads((project/'approved-checks.json').read_text())
    proposal.update(revision=2, run_id='run', validation_commands_policy_sha256='a'*64)
    path = project/'.omc/fixed-proposal.json'; path.write_text(json.dumps(proposal))
    assert run(project,'approve-checks','--file',str(path)).returncode == 0
    assert run(project,'contract-done','--required-checks','tests,browser').returncode == 0
    template = run(project,'verification-template')
    assert template.returncode == 0, template.stdout
    item = json.loads(template.stdout)['checks']['tests']
    assert 'validation_receipt' in item and item['validation_receipt'] is None


@pytest.mark.parametrize('name', ['templates/.agents/skills/omc-task/SKILL.md',
    'templates/.agents/skills/omc-task/references/verification.md',
    'templates/.claude/commands/task.md'])
def test_fixed_test_receipt_instructions_match_template_schema(name):
    text = (SCRIPT.parent.parent/name).read_text()
    assert 'validation_commands_policy_sha256' in text
    assert 'validation_receipt' in text


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


def test_expired_completion_reports_ttl_not_missing_evidence(project):
    import time
    prepare(project)
    assert finish(project,{'tests':check(),'browser':check()}).returncode==0
    path=guard._state_path(project);state=json.loads(path.read_bytes())
    state['started_at']=time.time()-guard._SESSION_TTL_SECONDS-1;path.write_text(json.dumps(state))
    before=path.read_bytes();basis=project/'.omc/verification-bases/current/1.json';basis_raw=basis.read_bytes()
    result=run(project,'complete-task','--verification-file',str(project/'verification.json'))
    assert result.returncode==1
    assert 'pipeline_ttl_expired' in result.stdout
    assert '등록이 없습니다' not in result.stdout and '증거 불일치' not in result.stdout
    assert '"reason_code": "pipeline_ttl_expired"' in result.stdout
    assert path.read_bytes()==before and basis.read_bytes()==basis_raw


# Completion-only reapproval: every issuance below belongs to a disposable fixture.
def reapproval_project(root):
    import time, hashlib
    state_dir=root/'.omc/state';state_dir.mkdir(parents=True,exist_ok=True)
    (state_dir/'latest.json').write_text(json.dumps({'latest_session_id':'current','latest_confirmed_session_id':'current'}))
    preserved_work(root)
    record_path=root/'verification.json'
    record=json.loads(record_path.read_bytes())
    for name,item in record['checks'].items():
        evidence=root/'.omc'/('observed-'+name+'.txt')
        evidence.write_text('fixture observation: '+name)
        item['evidence_files']=[{'path':str(evidence.relative_to(root)),
                                'sha256':hashlib.sha256(evidence.read_bytes()).hexdigest()}]
    record_path.write_text(json.dumps(record))
    pipeline=guard._state_path(root); state=json.loads(pipeline.read_bytes())
    state['started_at']=time.time()-guard._SESSION_TTL_SECONDS-10
    pipeline.write_text(json.dumps(state))
    proposal={'schema_version':'omc-completion-reapproval-request/v1','session_id':'current',
        'work_id':'work','approval_reference':'fixture explicit completion-only reapproval',
        'pipeline_sha256':hashlib.sha256(pipeline.read_bytes()).hexdigest(),
        'basis_sha256':state['basis_sha256'],
        'verification_file':'verification.json',
        'verification_sha256':hashlib.sha256(record_path.read_bytes()).hexdigest(),
        'code_snapshot_sha256':guard._code_snapshot_digest(root,record_path)}
    path=root/'.omc/reapproval-request.json';path.write_text(json.dumps(proposal))
    return path,record_path


def test_completion_reapproval_public_issue_and_completion_preserve_originals(project):
    path,verification=reapproval_project(project)
    originals={p:p.read_bytes() for p in (project/'.omc').rglob('*') if p.is_file()}
    blocked=run(project,'complete-task','--verification-file',str(verification))
    assert 'pipeline_ttl_expired' in blocked.stdout
    result=run(project,'reapprove-completion','--file',str(path))
    assert result.returncode==0,result.stdout+result.stderr
    assert all(p.read_bytes()==raw for p,raw in originals.items())
    assert run(project,'complete-task','--verification-file',str(verification)).returncode==0
    # No execution/edit permission and no second issuance, even via another proposal.
    assert run(project,'contract-done').returncode==1
    assert run(project,'check-edit','app.py').returncode==1
    assert run(project,'reapprove-completion','--file',str(path)).returncode==1


@pytest.mark.parametrize('defect',['raw','missing','code','verification','approval','red','unrun','failed','unstaged'])
def test_completion_reapproval_never_bypasses_other_checks(project,defect):
    path,verification=reapproval_project(project)
    assert run(project,'reapprove-completion','--file',str(path)).returncode==0
    if defect=='raw': (project/'.omc/observed-tests.txt').write_text('changed output')
    elif defect=='missing': (project/'.omc/observed-tests.txt').unlink()
    elif defect=='code': (project/'app.py').write_text('changed code')
    elif defect=='approval': (project/'.omc/verification-bases/current/1.json').write_text('{}')
    elif defect=='red': (project/'.omc/red/output.txt').write_text('changed RED')
    elif defect=='unstaged': (project/'new_impl.py').write_text('value=2')
    else:
        value=json.loads(verification.read_bytes())
        if defect in ('failed','unrun'):value['checks']['tests']['status']=defect
        else:value['implementation']['evidence']='changed'
        verification.write_text(json.dumps(value))
    result=run(project,'complete-task','--verification-file',str(verification))
    assert result.returncode==1 and '작업 완료' not in result.stdout


@pytest.mark.parametrize('defect',['string-only','external','symlink','work','session','future-request','extra-field'])
def test_completion_reapproval_rejects_unbound_inputs_without_issuing(project,defect):
    import hashlib
    path,verification=reapproval_project(project);request=json.loads(path.read_bytes())
    if defect in ('string-only','external','symlink'):
        record=json.loads(verification.read_bytes())
        if defect=='string-only':record['checks']['tests'].pop('evidence_files')
        elif defect=='external':record['checks']['tests']['evidence_files'][0]['path']='/etc/hosts'
        else:
            link=project/'.omc/link.txt';link.symlink_to(project/'.omc/observed-tests.txt')
            record['checks']['tests']['evidence_files'][0]['path']='.omc/link.txt'
        verification.write_text(json.dumps(record));request['verification_sha256']=hashlib.sha256(verification.read_bytes()).hexdigest()
    elif defect=='work':request['work_id']='foreign'
    elif defect=='session':request['session_id']='foreign'
    elif defect=='future-request':request['issued_at']=999999999999
    else:request['unexpected']=True
    path.write_text(json.dumps(request))
    assert run(project,'reapprove-completion','--file',str(path)).returncode==1
    assert not list((project/'.omc/completion-reapprovals').glob('*.json'))


@pytest.mark.parametrize('status',['failed','unrun','mismatch'])
def test_reapproval_issue_does_not_make_failed_or_unrun_evidence_pass(project,status):
    import hashlib
    path,verification=reapproval_project(project)
    value=json.loads(verification.read_bytes());value['checks']['tests']['status']=status
    verification.write_text(json.dumps(value));request=json.loads(path.read_bytes())
    request['verification_sha256']=hashlib.sha256(verification.read_bytes()).hexdigest();path.write_text(json.dumps(request))
    result=run(project,'reapprove-completion','--file',str(path))
    assert result.returncode==1 and 'completion_checks_blocked' in result.stdout
    assert not list((project/'.omc/completion-reapprovals').glob('*.json'))


@pytest.mark.parametrize('digest',[None,'','0'*63,True,{'sha256':'0'*64},'g'*64])
def test_reapproval_rejects_explicit_invalid_raw_digest_on_public_cli(project,digest):
    import hashlib
    request,verification=reapproval_project(project)
    value=json.loads(verification.read_bytes())
    value['checks']['tests']['evidence_files'][0]['sha256']=digest
    verification.write_text(json.dumps(value))
    proposal=json.loads(request.read_bytes())
    proposal['verification_sha256']=hashlib.sha256(verification.read_bytes()).hexdigest()
    request.write_text(json.dumps(proposal))
    originals={p:p.read_bytes() for p in (project/'.omc').rglob('*') if p.is_file()}
    result=run(project,'reapprove-completion','--file',str(request))
    assert result.returncode==1,result.stdout+result.stderr
    assert 'reapproval_evidence_digest_invalid' in result.stdout
    assert not list((project/'.omc/completion-reapprovals').glob('*.json'))
    assert all(p.read_bytes()==raw for p,raw in originals.items())


@pytest.mark.parametrize('phase',['binding','checks'])
def test_reapproval_expiry_during_validation_blocks_real_cli(project,phase):
    request,verification=reapproval_project(project)
    assert run(project,'reapprove-completion','--file',str(request)).returncode==0
    receipt=next((project/'.omc/completion-reapprovals').glob('*.json'))
    before=receipt.read_bytes();pipeline=guard._state_path(project).read_bytes()
    # Run the real argument parser/CLI; only elapsed time is controlled.
    source = """
import sys,json,types
from pathlib import Path
sys.path.insert(0,sys.argv[1])
import omc_pipeline_guard as g
root,verification,phase=Path(sys.argv[2]),Path(sys.argv[3]),sys.argv[4]
record=json.loads(next((root/'.omc/completion-reapprovals').glob('*.json')).read_bytes())
clock=[record['expires_at']-0.001]
g.time=types.SimpleNamespace(time=lambda:clock[0])
name='_reapproval_binding' if phase=='binding' else '_evaluate_completion_checks'
original=getattr(g,name)
def delayed(*args,**kwargs):
    value=original(*args,**kwargs)
    clock[0]=record['expires_at']
    return value
setattr(g,name,delayed)
sys.argv=['omc_pipeline_guard.py','--target',str(root),'complete-task','--verification-file',str(verification)]
sys.exit(g.main())
"""
    result=subprocess.run([sys.executable,'-c',source,str(SCRIPT.parent),str(project),str(verification),phase],capture_output=True,text=True)
    assert result.returncode==1,result.stdout+result.stderr
    assert 'pipeline_ttl_expired' in result.stdout and 'VERDICT: BLOCK' in result.stdout
    assert receipt.read_bytes()==before and guard._state_path(project).read_bytes()==pipeline

# Verified-commit transfer fixtures use independent tiny Git repositories only.
TRANSFER = 'verified_commit_transfer'
REVIEW = SCRIPT.with_name('omc_review_snapshot.py')


def transfer_git(root, *args):
    return subprocess.check_output(['git','-c','core.hooksPath=/dev/null',*args],cwd=root,text=True).strip()


def transfer_review(root, base, commit, paths, outside):
    def tool(*args):
        p=subprocess.run([sys.executable,str(REVIEW),*args],capture_output=True,text=True)
        assert p.returncode==0,p.stdout+p.stderr
        return json.loads(p.stdout)
    snapshot=tool('capture-review','--target',str(root),'--base-commit',base,'--candidate-commit',commit)
    raw=outside/(root.name+'-review.txt');raw.write_text('APPROVE controlled transfer fixture; exact paths inspected')
    sealed=tool('seal-review-output','--target',str(root),'--review-snapshot',snapshot['snapshot_path'],
                '--review-snapshot-sha256',snapshot['snapshot_sha256'],'--review-output',str(raw))
    args=['record-review','--target',str(root),'--review-snapshot',snapshot['snapshot_path'],
          '--review-snapshot-sha256',snapshot['snapshot_sha256'],'--review-evidence',sealed['evidence_path'],
          '--review-evidence-sha256',sealed['evidence_sha256'],'--verdict','APPROVE']
    for path in paths:args+=['--expected-path',path]
    return tool(*args)


@pytest.fixture
def transfer_case(tmp_path, request):
    source=tmp_path/'source';source.mkdir();transfer_git(source,'init','-q')
    transfer_git(source,'config','user.name','Fixture');transfer_git(source,'config','user.email','fixture@localhost')
    (source/'.gitignore').write_text('.omc/\n')
    if getattr(request,'param',None) in ('delete','rename'):
        (source/'old/deep').mkdir(parents=True)
        (source/'old/deep/only.txt').write_text('retired\n')
    (source/'app.py').write_text('value=1\n');transfer_git(source,'add','.');transfer_git(source,'commit','-qm','base')
    base=transfer_git(source,'rev-parse','HEAD')
    st=source/'.omc/state';st.mkdir(parents=True);(st/'latest.json').write_text(json.dumps({'latest_session_id':'current','latest_confirmed_session_id':'current'}))
    prepare(source)
    session=st/'sessions/current/session.json';session.parent.mkdir(parents=True)
    session.write_text(json.dumps({'session_id':'current','work_id':'source-work','confirmation':{'status':'confirmed'}}))
    if getattr(request,'param',None) in ('delete','rename'):
        transfer_git(source,'rm','old/deep/only.txt')
    (source/'app.py').write_text('value=2\n');transfer_git(source,'add','app.py','test_app.py','approved-checks.json');transfer_git(source,'commit','-qm','candidate')
    paths=['app.py','test_app.py','approved-checks.json']
    if getattr(request,'param',None) in ('delete','rename'):
        paths.append('old/deep/only.txt')
    if getattr(request,'param',None) in ('nested','rename'):
        (source/'newdir/deep').mkdir(parents=True)
        (source/'newdir/deep/new.py').write_text('value=3\n')
        transfer_git(source,'add','newdir/deep/new.py');transfer_git(source,'commit','--amend','--no-edit','-q')
        paths.append('newdir/deep/new.py')
    commit=transfer_git(source,'rev-parse','HEAD')
    result=run(source,'verification-template');assert result.returncode==0
    record=json.loads(result.stdout);record['implementation']={'status':'applied','evidence':'.omc/source-evidence.txt'}
    (source/'.omc/source-evidence.txt').write_text('controlled fixture verification output; not a production run')
    import hashlib
    for row in record['checks'].values():row.update(status='passed',evidence='.omc/source-evidence.txt',evidence_sha256=hashlib.sha256((source/'.omc/source-evidence.txt').read_bytes()).hexdigest())
    verification=source/'.omc/verification/source.json';verification.parent.mkdir(exist_ok=True);verification.write_text(json.dumps(record))
    assert run(source,'complete-task','--verification-file',str(verification)).returncode==0
    transfer_review(source,base,commit,paths,tmp_path)
    target=tmp_path/'target';subprocess.run(['git','clone','-q',str(source),str(target)],check=True)
    transfer_git(target,'checkout','--detach',base)
    (target/'.omc').mkdir();(target/'.omc/keep.json').write_text('preserved')
    op=tmp_path/'operator';(op/'.omc/state/sessions/transfer').mkdir(parents=True)
    (op/'.omc/state/latest.json').write_text(json.dumps({'latest_session_id':'transfer','latest_confirmed_session_id':'transfer'}))
    (op/'.omc/state/sessions/transfer/session.json').write_text(json.dumps({'session_id':'transfer','work_id':'transfer-work','confirmation':{'status':'confirmed'}}))
    policy={'source_root':str(source),'target_root':str(target),'base_commit':base,'target_commit':commit,
            'expected_paths':paths,'source_session_id':'current','source_verification':'.omc/verification/source.json',
            'protected_paths':[str(target/'.omc/keep.json')],'allow_review_pointer_update':True}
    basis={'session_id':'transfer','revision':1,'request':'same commit transfer','approval_reference':'fixture explicit approval',
           'completion_kind':TRANSFER,'transfer':policy,'checks':[{'id':'transfer','target':'fixed commit',
              'method':'Git and evidence comparison','pass_condition':'all match','target_version':commit}]}
    proposal=op/'.omc/proposal.json';proposal.write_text(json.dumps(basis))
    assert run(op,'approve-checks','--file',str(proposal)).returncode==0
    contract_result=run(op,'contract-done','--required-checks','transfer')
    assert contract_result.returncode==0
    return {'contract_stdout':contract_result.stdout,'operator':op,'source':source,'target':target,'base':base,'commit':commit,'paths':paths,'basis':basis,'proposal':proposal,'outside':tmp_path}


def transfer_register(c):
    return run(c['operator'],'register-transfer-baseline')


def transfer_apply(c):
    transfer_git(c['target'],'merge','--ff-only',c['commit'])
    transfer_review(c['target'],c['base'],c['commit'],c['paths'],c['outside'])


def transfer_finish(c):
    op=c['operator'];p=run(op,'verification-template');assert p.returncode==0,p.stdout
    v=json.loads(p.stdout);v['implementation']={'status':'applied','evidence':'registered fixed-commit transfer'}
    for row in v['checks'].values():row.update(status='passed',evidence='actual Git state and immutable baseline')
    path=op/'.omc/verification/transfer.json';path.parent.mkdir(exist_ok=True);path.write_text(json.dumps(v))
    return run(op,'complete-transfer','--verification-file',str(path)),path


def test_transfer_kind_cannot_replace_implementation_approval(project):
    prepare(project)
    basis=guard._load_approved_basis(project,'current');basis.update(revision=2,completion_kind=TRANSFER,
      transfer={'source_root':str(project/'source'),'target_root':str(project/'target'),'base_commit':'1'*40,
                'target_commit':'2'*40,'expected_paths':['app.py'],'source_session_id':'s',
                'source_verification':'.omc/v.json','protected_paths':[],'allow_review_pointer_update':True})
    p=project/'.omc/new-kind.json';p.write_text(json.dumps(basis))
    result=run(project,'approve-checks','--file',str(p))
    assert result.returncode==1 and 'completion_kind_immutable' in result.stdout


def test_transfer_public_cli_normal_without_current_red(transfer_case):
    c=transfer_case;p=transfer_register(c);assert p.returncode==0,p.stdout+p.stderr
    transfer_apply(c);result,_=transfer_finish(c)
    assert result.returncode==0,result.stdout+result.stderr
    assert result.stdout.startswith('필수 검증 통과·작업 완료')
    state=json.loads((c['operator']/'.omc/pipeline_session.json').read_text())
    assert not state.get('red_done_tests')


def test_transfer_rejects_registration_after_apply(transfer_case):
    c=transfer_case;transfer_apply(c);p=transfer_register(c)
    assert p.returncode==1 and 'transfer_already_applied' in p.stdout


@pytest.mark.parametrize('defect',['source-failed','source-unrun','source-mismatch','source-missing','source-evidence',
                                  'source-review','target-staged','target-link','wrong-parent'])
def test_transfer_registration_rejects_invalid_proof(transfer_case,defect):
    c=transfer_case;v=c['source']/'.omc/verification/source.json'
    if defect.startswith('source-') and defect.split('-',1)[1] in ['failed','unrun','mismatch']:
        data=json.loads(v.read_text());data['checks']['tests']['status']=defect.split('-',1)[1];v.write_text(json.dumps(data))
    elif defect=='source-missing':v.unlink()
    elif defect=='source-evidence':(c['source']/'.omc/source-evidence.txt').unlink()
    elif defect=='source-review':(c['source']/'.omc/state/review-snapshots/current.json').unlink()
    elif defect=='target-staged':
        (c['target']/'app.py').write_text('unrelated staged');transfer_git(c['target'],'add','app.py')
    elif defect=='target-link':(c['target']/'link').symlink_to(c['source']/'app.py')
    else:
        data=json.loads(c['proposal'].read_text());data['transfer']['base_commit']='0'*40
        # A separate session's approved input fixture; no live record is silently edited.
        (c['operator']/'.omc/verification-bases/transfer/1.json').write_text(json.dumps(data))
    p=transfer_register(c);assert p.returncode==1,p.stdout+p.stderr


@pytest.mark.parametrize('defect',['file','staged','flag','protected','pointer','head','source-proof','late-kind','baseline-digest'])
def test_transfer_completion_rejects_post_baseline_change(transfer_case,defect):
    c=transfer_case;assert transfer_register(c).returncode==0;transfer_apply(c)
    early_path=transfer_finish(c)[1] if defect=='late-kind' else None
    if defect=='file':(c['target']/'app.py').write_text('changed after transfer')
    elif defect=='staged':
        (c['target']/'extra.txt').write_text('extra');transfer_git(c['target'],'add','extra.txt')
    elif defect=='flag':transfer_git(c['target'],'update-index','--assume-unchanged','app.py')
    elif defect=='protected':(c['target']/'.omc/keep.json').write_text('changed')
    elif defect=='pointer':(c['target']/'.omc/state/review-snapshots/current.json').write_text('{}')
    elif defect=='head':transfer_git(c['target'],'checkout','--detach',c['base'])
    elif defect=='source-proof':(c['source']/'.omc/source-evidence.txt').write_text('changed')
    elif defect=='late-kind':
        data=json.loads(c['proposal'].read_text());data['completion_kind']='implementation'
        (c['operator']/'.omc/verification-bases/transfer/1.json').write_text(json.dumps(data))
    else:
        state=c['operator']/'.omc/pipeline_session.json';data=json.loads(state.read_text());data['transfer_baseline_sha256']='0'*64;state.write_text(json.dumps(data))
    p=run(c['operator'],'complete-transfer','--verification-file',str(early_path)) if early_path else transfer_finish(c)[0]
    assert p.returncode==1,p.stdout+p.stderr


def test_transfer_wrong_entry_does_not_bypass_red(transfer_case):
    c=transfer_case;assert transfer_register(c).returncode==0;transfer_apply(c);p,path=transfer_finish(c);assert p.returncode==0
    wrong=run(c['operator'],'complete-task','--verification-file',str(path))
    assert wrong.returncode==1 and 'completion_kind_entry_mismatch' in wrong.stdout


@pytest.mark.parametrize('change',['head','index','file'])
def test_transfer_registration_detects_mid_capture_change(transfer_case,change):
    c=transfer_case
    injected=('original=g._transfer_source_proof\ndef capture(policy):\n result=original(policy)\n '
      +('g._transfer_git(g.Path(policy["target_root"]),"merge","--ff-only",policy["target_commit"])' if change=='head'
        else 'g._transfer_git(g.Path(policy["target_root"]),"update-index","--assume-unchanged",".gitignore")' if change=='index'
        else '(g.Path(policy["target_root"])/".omc/keep.json").write_text("mid-capture")')
      +'\n return result\ng._transfer_source_proof=capture\n')
    code='import sys;sys.path.insert(0,'+repr(str(SCRIPT.parent))+');import omc_pipeline_guard as g\n'+injected+'sys.exit(g.main())'
    p=subprocess.run([sys.executable,'-c',code,'--target',str(c['operator']),'register-transfer-baseline'],capture_output=True,text=True)
    assert p.returncode==1 and 'transfer_changed_during_registration' in p.stdout
    assert not (c['operator']/'.omc/state/transfer-baselines/transfer.json').exists()


def test_transfer_registration_is_write_once_and_policy_is_fixed(transfer_case):
    c=transfer_case;assert transfer_register(c).returncode==0
    assert 'transfer_baseline_write_once' in transfer_register(c).stdout
    doc=dict(c['basis']);doc['revision']=2;doc['transfer']={**doc['transfer'],'protected_paths':[]}
    path=c['operator']/'.omc/expanded.json';path.write_text(json.dumps(doc))
    p=run(c['operator'],'approve-checks','--file',str(path));assert p.returncode==1


def test_legacy_completion_never_captures_transfer(project,monkeypatch):
    prepare(project)
    monkeypatch.setattr(guard,'_transfer_source_proof',lambda *a: (_ for _ in ()).throw(AssertionError('unexpected capture')))
    assert finish(project,{'tests':check(),'browser':check()}).returncode==0
    assert not (project/'.omc/state/transfer-baselines').exists()


def test_legacy_kind_cannot_change_via_preserving_session(project):
    prepare(project)
    old=project/'.omc/state/sessions/current/session.json';old.parent.mkdir(parents=True)
    old.write_text(json.dumps({'session_id':'current','work_id':'same','confirmation':{'status':'confirmed'}}))
    new=project/'.omc/state/sessions/observer/session.json';new.parent.mkdir(parents=True)
    new.write_text(json.dumps({'session_id':'observer','work_id':'same','confirmation':{'status':'confirmed'}}))
    (project/'.omc/state/latest.json').write_text(json.dumps({'latest_session_id':'observer','latest_confirmed_session_id':'observer'}))
    (project/'.omc/state/pending-completion.json').write_text(json.dumps({'work_id':'same','session_ids':['current','observer']}))
    doc=guard._load_approved_basis(project,'current');doc.update(session_id='observer',revision=1,completion_kind=TRANSFER,
      transfer={'source_root':str(project/'source'),'target_root':str(project/'target'),'base_commit':'1'*40,
                'target_commit':'2'*40,'expected_paths':['app.py'],'source_session_id':'s',
                'source_verification':'.omc/v.json','protected_paths':[],'allow_review_pointer_update':True})
    p=project/'.omc/observer.json';p.write_text(json.dumps(doc));result=run(project,'approve-checks','--file',str(p))
    assert result.returncode==1 and 'completion_kind_immutable' in result.stdout


@pytest.mark.parametrize('defect',['missing-digest','wrong-digest'])
def test_transfer_requires_source_evidence_digest_connection(transfer_case,defect):
    c=transfer_case;p=c['source']/'.omc/verification/source.json';record=json.loads(p.read_text())
    if defect=='missing-digest':record['checks']['tests'].pop('evidence_sha256',None)
    else:record['checks']['tests']['evidence_sha256']='0'*64
    p.write_text(json.dumps(record))
    result=transfer_register(c)
    assert result.returncode==1 and 'transfer_source_evidence_digest' in result.stdout


@pytest.mark.parametrize('phase',['before-register','after-register'])
def test_transfer_cannot_exempt_new_operator_implementation(transfer_case,phase):
    c=transfer_case
    if phase=='after-register':
        assert transfer_register(c).returncode==0;transfer_apply(c)
    (c['operator']/'new_feature.py').write_text('new_logic=True\n')
    p=transfer_register(c) if phase=='before-register' else transfer_finish(c)[0]
    assert p.returncode==1 and 'transfer_operator_code_changed' in p.stdout



def test_transfer_source_work_and_committed_bytes_are_required(transfer_case):
    c=transfer_case
    transfer_git(c['source'],'update-index','--assume-unchanged','app.py')
    (c['source']/'app.py').write_text('hidden uncommitted code')
    p=transfer_register(c)
    assert p.returncode==1 and 'transfer_source_tree_mismatch' in p.stdout


def test_transfer_preserves_directory_permissions(transfer_case):
    c=transfer_case;assert transfer_register(c).returncode==0;transfer_apply(c)
    (c['target']/'.omc').chmod(0o700)
    p,_=transfer_finish(c)
    assert p.returncode==1 and 'transfer_file_preservation_mismatch' in p.stdout



def test_transfer_baseline_preserves_previous_pointer_and_receipt(transfer_case):
    import shutil
    c=transfer_case;src=c['source']/'.omc/state/review-snapshots';dst=c['target']/'.omc/state/review-snapshots';dst.mkdir(parents=True)
    old=json.loads((src/'current.json').read_text());old_path=dst/old['receipt_name'];shutil.copy2(src/old['receipt_name'],old_path)
    shutil.copy2(src/'current.json',dst/'current.json');raw=(dst/'current.json').read_bytes()
    assert transfer_register(c).returncode==0
    baseline=json.loads((c['operator']/'.omc/state/transfer-baselines/transfer.json').read_text())
    assert bytes.fromhex(baseline['old_pointer_hex'])==raw
    transfer_apply(c);assert transfer_finish(c)[0].returncode==0
    old_path.write_bytes(old_path.read_bytes()+b' ')
    p,_=transfer_finish(c);assert p.returncode==1 and 'transfer_file_preservation_mismatch' in p.stdout


def test_transfer_source_work_ledger_is_required(transfer_case):
    c=transfer_case;(c['source']/'.omc/state/sessions/current/session.json').unlink()
    assert transfer_register(c).returncode==1


def test_transfer_cannot_reuse_another_work_baseline(transfer_case):
    c=transfer_case;assert transfer_register(c).returncode==0;transfer_apply(c)
    path=c['operator']/'.omc/state/sessions/transfer/session.json';obj=json.loads(path.read_text());obj['work_id']='different-work';path.write_text(json.dumps(obj))
    assert transfer_finish(c)[0].returncode==1


@pytest.mark.parametrize('status,first',[('failed','검증 실패·수정 필요'),('mismatch','증거 불일치·재검증 필요'),('unrun','구현됨·검증 미완료')])
def test_transfer_reports_exact_verification_state(transfer_case,status,first):
    c=transfer_case;assert transfer_register(c).returncode==0;transfer_apply(c);_,path=transfer_finish(c)
    value=json.loads(path.read_text());value['checks']['transfer']['status']=status;path.write_text(json.dumps(value))
    p=run(c['operator'],'complete-transfer','--verification-file',str(path));assert p.returncode==1
    assert p.stdout.startswith(first)


# Review fixes: preserve unrelated index/worktree bytes, permit only required parents.
def stage_transfer_unrelated(c):
    target=c['target']
    (target/'.gitignore').write_text('.omc/\n# staged baseline\n')
    transfer_git(target,'add','.gitignore')
    (target/'.gitignore').write_text('.omc/\n# staged baseline\n# unstaged baseline\n')
    path=target/'kept.txt';path.write_text('unrelated staged file\n');path.chmod(0o755)
    transfer_git(target,'add','kept.txt')
    transfer_git(target,'update-index','--assume-unchanged','.gitignore')
    return guard._transfer_index(target),{name:guard._transfer_file_row(target/name) for name in ['.gitignore','kept.txt']}


def apply_transfer_with_frozen_source_review(c):
    # Reuse immutable reviewed commit artifacts, never edit an existing receipt.
    import shutil
    transfer_git(c['target'],'merge','--ff-only',c['commit'])
    src=c['source']/'.omc/state/review-snapshots';dst=c['target']/'.omc/state/review-snapshots'
    shutil.copytree(src,dst,dirs_exist_ok=True)


def test_transfer_preserves_existing_unrelated_staging(transfer_case):
    c=transfer_case;old,files=stage_transfer_unrelated(c)
    result=transfer_register(c);assert result.returncode==0,result.stdout
    apply_transfer_with_frozen_source_review(c)
    result,_=transfer_finish(c);assert result.returncode==0,result.stdout
    now=guard._transfer_index(c['target'])
    assert all(now[name]==old[name] for name in ['.gitignore','kept.txt'])
    assert all(guard._transfer_file_row(c['target']/name)==row for name,row in files.items())
    from omc_review_snapshot import validate_ship_candidate
    assert validate_ship_candidate(c['target'])['status']=='BLOCKED' # Generic ship remains strict.


@pytest.mark.parametrize('defect',['index','file','mode','flag'])
def test_transfer_rejects_changes_to_preserved_staging(transfer_case,defect):
    c=transfer_case;stage_transfer_unrelated(c)
    result=transfer_register(c);assert result.returncode==0,result.stdout
    apply_transfer_with_frozen_source_review(c)
    if defect=='index':
        (c['target']/'kept.txt').write_text('changed');transfer_git(c['target'],'add','kept.txt')
    elif defect=='file':(c['target']/'kept.txt').write_text('changed')
    elif defect=='mode':(c['target']/'kept.txt').chmod(0o644)
    else:transfer_git(c['target'],'update-index','--no-assume-unchanged','.gitignore')
    assert transfer_finish(c)[0].returncode==1


@pytest.mark.parametrize('defect',['staged','hidden','untracked'])
def test_transfer_registration_rejects_scope_conflicts(transfer_case,defect):
    c=transfer_case
    if defect=='untracked':(c['target']/'test_app.py').write_text('existing work')
    else:
        (c['target']/'app.py').write_text('existing scope edit')
        if defect=='staged':transfer_git(c['target'],'add','app.py')
        else:transfer_git(c['target'],'update-index','--assume-unchanged','app.py')
    result=transfer_register(c);assert result.returncode==1,result.stdout
    assert not (c['operator']/'.omc/state/transfer-baselines/transfer.json').exists()


@pytest.mark.parametrize('transfer_case',['nested'],indirect=True)
@pytest.mark.parametrize('extra',[False,True])
def test_transfer_accepts_only_approved_new_parent_directories(transfer_case,extra):
    c=transfer_case
    result=transfer_register(c);assert result.returncode==0,result.stdout
    transfer_apply(c)
    if extra:(c['target']/'newdir/unapproved-empty').mkdir()
    result,_=transfer_finish(c)
    assert result.returncode==(1 if extra else 0),result.stdout
    if extra:assert 'transfer_unapproved_added_path' in result.stdout


@pytest.mark.parametrize('transfer_case',['delete','rename'],indirect=True)
def test_transfer_allows_parents_emptied_by_approved_deletion(transfer_case):
    c=transfer_case
    result=transfer_register(c);assert result.returncode==0,result.stdout
    transfer_apply(c)
    assert not (c['target']/'old').exists()
    result,_=transfer_finish(c);assert result.returncode==0,result.stdout


@pytest.mark.parametrize('transfer_case',['delete'],indirect=True)
@pytest.mark.parametrize('keep',['file','empty-directory'])
@pytest.mark.parametrize('tamper',[False,True])
def test_transfer_preserves_unrelated_descendants_of_deleted_parents(transfer_case,keep,tamper):
    c=transfer_case;target=c['target'];path=target/'old/keep'
    if keep=='file':path.write_text('unrelated original bytes')
    else:path.mkdir()
    before=guard._transfer_inventory(target)
    result=transfer_register(c);assert result.returncode==0,result.stdout
    transfer_apply(c)
    assert path.exists() and not (target/'old/deep').exists()
    if tamper:
        path.unlink() if keep=='file' else path.rmdir()
        (target/'old').rmdir()
    result,_=transfer_finish(c)
    assert result.returncode==(1 if tamper else 0),result.stdout
    if not tamper:
        after=guard._transfer_inventory(target)
        assert after['old']==before['old'] and after['old/keep']==before['old/keep']
    else:assert 'transfer_file_preservation_mismatch' in result.stdout


@pytest.mark.parametrize('transfer_case',['delete'],indirect=True)
def test_transfer_kept_parent_permissions_are_still_required(transfer_case):
    c=transfer_case;target=c['target'];(target/'old/keep').mkdir()
    result=transfer_register(c);assert result.returncode==0,result.stdout
    transfer_apply(c);(target/'old').chmod(0o700)
    result,_=transfer_finish(c)
    assert result.returncode==1 and 'transfer_file_preservation_mismatch' in result.stdout
