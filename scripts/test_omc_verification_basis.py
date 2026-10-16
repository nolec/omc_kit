"""Approved scope must survive registration, completion and collection."""
import hashlib
import json
import sys
import subprocess
import pytest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import omc_pipeline_guard as guard


def basis(root, revision=1):
    (root / '.omc/state').mkdir(parents=True, exist_ok=True)
    (root / '.omc/state/latest.json').write_text(json.dumps({'latest_session_id':'s','latest_confirmed_session_id':'s'}))
    return {'session_id':'s','revision':revision,'request':'UI 유지보수 및 실제 홈 검수',
            'approval_reference':'사용자: 이 검증 항목으로 진행',
            'checks':[{'id':name,'target':target,'method':'직접 관측','pass_condition':'요구값 일치','target_version':'candidate-1'}
                      for name,target in [('tests','Ad tests'),('browser-home','실제 로그인 홈')]]}


def register(root, doc):
    p=root/'proposal.json';p.write_text(json.dumps(doc))
    return guard.cmd_approve_checks(root,p)


def record(root, doc):
    stored=guard._load_approved_basis(root,'s')
    state=guard._load_state(root)
    state.update(red_session_id='s',red_done_tests=['test_app.py'])
    guard._save_state(root,state)
    checks={c['id']:{'status':'passed','evidence':'observed output','definition_sha256':guard._check_digest(c),
                     'code_snapshot_sha256':guard._code_snapshot_digest(root)} for c in doc['checks']}
    p=root/'.omc/verification/result.json';p.parent.mkdir(exist_ok=True)
    p.write_text(json.dumps({'session_id':'s','basis_sha256':guard._basis_digest(stored),
                            'implementation':{'status':'applied','evidence':'patch'},'checks':checks}))
    return p


def test_first_contract_cannot_omit_home(tmp_path,capsys):
    assert register(tmp_path,basis(tmp_path))==0
    assert guard.cmd_contract_done(tmp_path,required_checks='tests')==1
    assert 'browser-home' in capsys.readouterr().out


def test_approval_requires_reference_and_scope(tmp_path):
    doc=basis(tmp_path);doc['approval_reference']=''
    assert register(tmp_path,doc)==1
    doc=basis(tmp_path);del doc['checks'][1]['target']
    assert register(tmp_path,doc)==1


def test_revisions_preserve_history_and_invalidate_old_completion(tmp_path,capsys):
    doc=basis(tmp_path);assert register(tmp_path,doc)==0
    assert guard.cmd_contract_done(tmp_path,required_checks='tests,browser-home')==0
    p=record(tmp_path,doc)
    assert guard.cmd_complete_task(tmp_path,p)==0
    revised=basis(tmp_path,2);revised['checks'][1]['target']='변경된 실제 홈'
    assert register(tmp_path,revised)==0
    assert guard.cmd_complete_task(tmp_path,p)==1
    assert '증거 불일치' in capsys.readouterr().out
    assert register(tmp_path,doc)==1


def test_controlled_surface_cannot_replace_home_evidence(tmp_path,capsys):
    doc=basis(tmp_path);assert register(tmp_path,doc)==0
    guard.cmd_contract_done(tmp_path,required_checks='tests,browser-home')
    p=record(tmp_path,doc);data=json.loads(p.read_text())
    data['checks']['browser-home']['definition_sha256']=hashlib.sha256(b'controlled harness').hexdigest()
    p.write_text(json.dumps(data))
    assert guard.cmd_complete_task(tmp_path,p)==1
    assert '증거 불일치' in capsys.readouterr().out


def test_legacy_can_edit_but_cannot_claim_completion(tmp_path,capsys):
    doc=basis(tmp_path)
    guard.cmd_contract_done(tmp_path,required_checks='tests,browser-home')
    state=guard._load_state(tmp_path);state.update(red_session_id='s',red_done_tests=['test.py']);guard._save_state(tmp_path,state)
    p=tmp_path/'result.json';p.write_text(json.dumps({'session_id':'s','implementation':{'status':'applied','evidence':'patch'},'checks':{n:{'status':'passed','evidence':'ok'} for n in ['tests','browser-home']}}))
    assert guard.cmd_complete_task(tmp_path,p)==1
    assert '승인된 검증 기준' in capsys.readouterr().out


def test_unrun_home_is_incomplete_not_success(tmp_path,capsys):
    doc=basis(tmp_path);register(tmp_path,doc)
    guard.cmd_contract_done(tmp_path,required_checks='tests,browser-home')
    p=record(tmp_path,doc);data=json.loads(p.read_text());data['checks']['browser-home']['status']='unrun';p.write_text(json.dumps(data))
    assert guard.cmd_complete_task(tmp_path,p)==1
    assert '구현됨·검증 미완료' in capsys.readouterr().out


def test_public_template_preserves_separate_surface_ids_and_defaults_unrun(tmp_path):
    doc=basis(tmp_path);register(tmp_path,doc)
    result=subprocess.run([sys.executable,str(Path(guard.__file__)),'--target',str(tmp_path),'verification-template'],capture_output=True,text=True)
    assert result.returncode==0,result.stderr
    data=json.loads(result.stdout)
    assert set(data['checks'])=={'tests','browser-home'}
    assert data['checks']['browser-home']['status']=='unrun'
    assert data['basis_sha256']==guard._basis_digest(doc)


def test_approved_reduction_needs_new_revision_and_contract(tmp_path):
    doc=basis(tmp_path);register(tmp_path,doc)
    guard.cmd_contract_done(tmp_path,required_checks='tests,browser-home')
    changed=basis(tmp_path,2);changed['checks']=changed['checks'][:1]
    changed['approval_reference']='사용자: 실제 홈 검수를 이번 범위에서 제외 승인'
    assert register(tmp_path,changed)==0
    assert guard.cmd_contract_done(tmp_path,required_checks='tests')==0
    assert guard.cmd_complete_task(tmp_path,record(tmp_path,changed))==0


def test_other_session_basis_is_rejected(tmp_path):
    doc=basis(tmp_path);doc['session_id']='previous'
    assert register(tmp_path,doc)==1


def test_staged_code_change_invalidates_already_passed_checks(tmp_path,capsys):
    subprocess.run(['git','init','-q'],cwd=tmp_path,check=True)
    source=tmp_path/'app.py';source.write_text('value=1\n')
    subprocess.run(['git','add','app.py'],cwd=tmp_path,check=True)
    doc=basis(tmp_path);register(tmp_path,doc)
    guard.cmd_contract_done(tmp_path,required_checks='tests,browser-home')
    p=record(tmp_path,doc)
    assert guard.cmd_complete_task(tmp_path,p)==0
    source.write_text('value=2\n')
    subprocess.run(['git','add','app.py'],cwd=tmp_path,check=True)
    assert guard.cmd_complete_task(tmp_path,p)==1
    assert '코드 스냅샷' in capsys.readouterr().out
    result=subprocess.run([sys.executable,str(Path(guard.__file__)),'--target',str(tmp_path),
                           'complete-task','--verification-file',str(p)],capture_output=True,text=True)
    assert result.returncode==1
    assert result.stdout.startswith('증거 불일치·재검증 필요\n')


@pytest.mark.parametrize('change',['content','delete','mode','add','rename'])
def test_snapshot_covers_worktree_mutations(tmp_path,change):
    subprocess.run(['git','init','-q'],cwd=tmp_path,check=True)
    source=tmp_path/'app.py';source.write_text('value=1\n')
    subprocess.run(['git','add','app.py'],cwd=tmp_path,check=True)
    before=guard._code_snapshot_digest(tmp_path)
    if change=='content':source.write_text('value=2\n')
    elif change=='delete':source.unlink()
    elif change=='mode':source.chmod(0o755)
    elif change=='add':(tmp_path/'other.py').write_text('other=1\n')
    else:source.rename(tmp_path/'renamed.py')
    assert guard._code_snapshot_digest(tmp_path)!=before


def test_staging_unchanged_bytes_does_not_invalidate_snapshot(tmp_path):
    subprocess.run(['git','init','-q'],cwd=tmp_path,check=True)
    source=tmp_path/'app.py';source.write_text('value=1\n')
    before=guard._code_snapshot_digest(tmp_path)
    subprocess.run(['git','add','app.py'],cwd=tmp_path,check=True)
    assert guard._code_snapshot_digest(tmp_path)==before


def test_source_symlink_target_bytes_are_bound(tmp_path):
    subprocess.run(['git','init','-q'],cwd=tmp_path,check=True)
    target=tmp_path/'payload.data';target.write_text('value=1\n')
    (tmp_path/'app.py').symlink_to('payload.data')
    before=guard._code_snapshot_digest(tmp_path)
    target.write_text('value=2\n')
    assert guard._code_snapshot_digest(tmp_path)!=before


def test_passed_evidence_without_code_snapshot_is_incomplete(tmp_path,capsys):
    doc=basis(tmp_path);register(tmp_path,doc)
    guard.cmd_contract_done(tmp_path,required_checks='tests,browser-home')
    p=record(tmp_path,doc);data=json.loads(p.read_text())
    del data['checks']['browser-home']['code_snapshot_sha256'];p.write_text(json.dumps(data))
    assert guard.cmd_complete_task(tmp_path,p)==1
    assert '코드 스냅샷' in capsys.readouterr().out


@pytest.mark.parametrize('suffix',['.json','.cjs','.mjs','.asset'])
def test_untracked_runtime_input_change_invalidates_completion(tmp_path,suffix,capsys):
    subprocess.run(['git','init','-q'],cwd=tmp_path,check=True)
    (tmp_path/'app.js').write_text('/* consumes settings'+suffix+' */\n')
    subprocess.run(['git','add','app.js'],cwd=tmp_path,check=True)
    runtime=tmp_path/('settings'+suffix);runtime.write_text('old')
    doc=basis(tmp_path);register(tmp_path,doc)
    guard.cmd_contract_done(tmp_path,required_checks='tests,browser-home')
    p=record(tmp_path,doc)
    assert guard.cmd_complete_task(tmp_path,p)==0
    runtime.write_text('new')
    assert guard.cmd_complete_task(tmp_path,p)==1
    assert '코드 스냅샷' in capsys.readouterr().out
