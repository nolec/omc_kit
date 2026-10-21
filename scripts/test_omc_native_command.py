"""Direct shell, public CLI, custody and report-surface regressions (no model)."""
import hashlib
import importlib
import json
from pathlib import Path
import subprocess
import sys

import pytest
import omc_pipeline_guard as guard
from test_omc_native_red_evidence import project, configured, cli, evidence, TEST, SCRIPTS
from test_omc_workflow_validation import collect, response
from test_omc_home_observation import home, assess as assess_home, rows as home_rows


def command_module():
    return importlib.import_module('omc_native_command')


@pytest.mark.parametrize('suffix', ['', ' 2>&1'])
def test_fixed_validation_profile_call_is_literal(suffix):
    assert command_module().validation_profile('python3 scripts/omc_validation_command.py run --target . --profile tests'+suffix)=='tests'


@pytest.mark.parametrize('tail',['; true',' --command x','\ntrue',' --profile lint',' $(pwd)'])
def test_fixed_validation_profile_call_rejects_extra_input(tail):
    assert command_module().validation_profile('python3 scripts/omc_validation_command.py run --target . --profile tests'+tail) is None


@pytest.mark.parametrize('tool', ['cat', 'sha256sum'])
@pytest.mark.parametrize('suffix', ['', ' 2>&1'])
def test_bounded_read_recognizes_regular_candidate_files(tmp_path, tool, suffix):
    (tmp_path/'one.md').write_text('one')
    (tmp_path/'two words.md').write_text('two')
    assert command_module().read_command_reason(tmp_path, f'{tool} one.md "two words.md"'+suffix) is None


@pytest.mark.parametrize('command,reason', [
    ('cat one.md | head -1', 'syntax_unsupported'),
    ('cat $(pwd)', 'syntax_unsupported'),
    ('cat *.md', 'syntax_unsupported'),
    ('cat -n one.md', 'command_unsupported'),
    ('cat', 'command_unsupported'),
    ('cat -', 'command_unsupported'),
    ('ls one.md', 'command_unsupported'),
    ('python3 -c "print(1)"', 'command_unsupported'),
    ('cat missing.md', 'path_unverified'),
    ('cat .', 'path_unverified'),
    ('cat ../outside.md', 'path_unverified'),
])
def test_bounded_read_rejects_unapproved_forms(tmp_path, command, reason):
    (tmp_path/'one.md').write_text('one')
    assert command_module().read_command_reason(tmp_path, command) == reason


@pytest.mark.parametrize('operand', ['link.md', 'linked/file.md', 'linked/../one.md'])
def test_bounded_read_rejects_symlink_components(tmp_path, operand):
    (tmp_path/'one.md').write_text('one')
    folder=tmp_path/'dir';folder.mkdir();(folder/'file.md').write_text('file')
    (tmp_path/'link.md').symlink_to('one.md')
    (tmp_path/'linked').symlink_to('dir', target_is_directory=True)
    assert command_module().read_command_reason(tmp_path, 'cat '+operand) == 'path_unverified'


def test_historical_read_never_uses_current_files(tmp_path, monkeypatch):
    (tmp_path/'one.md').write_text('current content is not historical evidence')
    def forbidden(*args, **kwargs):
        raise AssertionError('historical audit must not consult present paths')
    monkeypatch.setattr(Path, 'lstat', forbidden)
    assert command_module().read_command_reason(tmp_path, 'cat one.md', historical=True) == 'historical_path_unverified'


@pytest.mark.parametrize('suffix',['',' 2>&1','\t2>&1  '])
def test_fixed_red_suffix_is_recognized(suffix):
    import omc_native_red_evidence as red
    assert red.exact_observe(red.COMMAND+suffix)


@pytest.mark.parametrize('tail',[' "2>&1"'," '2>&1'",' 2>out',' 2>&1; true',' && true',
                                ' | cat','\ntrue',' $(echo x)',' `echo x`',' 2>&1 2>&1',' 2 >&1'])
def test_shell_composites_are_not_receipts(tail):
    import omc_native_red_evidence as red
    assert not red.exact_observe(red.COMMAND+tail)


@pytest.mark.parametrize('interpreter',['python','python3',sys.executable])
@pytest.mark.parametrize('suffix',['',' 2>&1'])
def test_home_existing_paths_and_interpreters_survive(tmp_path,interpreter,suffix):
    import omc_home_observation as home
    script=tmp_path/'scripts/omc_home_observation.py'
    assert home._is_observe(f'{interpreter} {script} observe --target {tmp_path}'+suffix,tmp_path)


@pytest.mark.parametrize('text',['echo "2>&1"','echo \';\'','python3 x.py 2>&1'])
def test_parser_keeps_quoted_literals_and_removes_only_terminal_redirection(text):
    args=command_module().single_argv(text)
    assert args==({'echo "2>&1"':['echo','2>&1'],"echo ';'":['echo',';'],
                  'python3 x.py 2>&1':['python3','x.py']}[text])


def observed_shell(root,command=None):
    import omc_native_red_evidence as red
    command=command or red.COMMAND+' 2>&1'
    result=subprocess.run(['/bin/sh','-c',command],cwd=root,capture_output=True,text=True)
    assert result.returncode==0,result.stdout+result.stderr
    rows=[{'type':'system','subtype':'init'},
          {'type':'assistant','message':{'content':[{'type':'tool_use','id':'contract','name':'Bash','input':{'command':'python3 scripts/omc_pipeline_guard.py contract-done --required-checks tests 2>&1'}}]}},
          {'type':'user','message':{'content':[{'type':'tool_result','tool_use_id':'contract','content':'CONTRACT 확인 완료'}]}},
          {'type':'assistant','message':{'content':[{'type':'tool_use','id':'red','name':'Bash','input':{'command':command}}]}},
          {'type':'user','message':{'content':[{'type':'tool_result','tool_use_id':'red','content':result.stdout}]}},
          {'type':'result','subtype':'success','result':'replayed local CLI returns, not a model run'}]
    path=root/'.omc/native-red/events.jsonl';path.parent.mkdir(exist_ok=True)
    path.write_text(''.join(json.dumps(x)+'\n' for x in rows))
    red.publish(root)
    return rows,path


def install_scripts(root):
    # Minimal independent CLI fixture: actual installed script files, real Jest.
    (root/'scripts').mkdir(exist_ok=True)
    for p in SCRIPTS.glob('omc_*.py'):
        (root/'scripts'/p.name).write_bytes(p.read_bytes())


def test_real_shell_red_receipt_register_green_and_gate(project):
    root=project;configured(root);install_scripts(root)
    import omc_native_red_evidence as red
    rows,events=observed_shell(root)
    refs=red.references(root);assert len(refs)==1
    result,path=evidence(root,refs[0]);assert result.returncode==0,result.stdout
    registered=subprocess.run([sys.executable,'scripts/omc_pipeline_guard.py','red-done',TEST,'--evidence-file',str(path)],cwd=root,capture_output=True,text=True)
    assert registered.returncode==0,registered.stdout
    assert subprocess.run([sys.executable,'scripts/omc_pipeline_guard.py','check-edit',str(root/Path(TEST).parent/'Ad.tsx')],cwd=root,capture_output=True).returncode==0
    rows=rows[:-1]+[
        {'type':'assistant','message':{'content':[{'type':'tool_use','id':'register','name':'Bash','input':{'command':f'python3 scripts/omc_pipeline_guard.py red-done {TEST} --evidence-file {path.relative_to(root)} 2>&1'}}]}},
        {'type':'user','message':{'content':[{'type':'tool_result','tool_use_id':'register','content':registered.stdout}]}},
        {'type':'assistant','message':{'content':[{'type':'tool_use','id':'edit','name':'Edit','input':{'file_path':str(root/Path(TEST).parent/'Ad.tsx')}}]}},
        {'type':'user','message':{'content':[{'type':'tool_result','tool_use_id':'edit','content':'updated'}]}},
        {'type':'result','subtype':'success','result':'local replay'}]
    events.write_text(''.join(json.dumps(x)+'\n' for x in rows))
    from omc_workflow_validation import assess_native_procedure
    audit=assess_native_procedure(root,rows,guard._load_approved_basis(root,'s'),events)
    assert audit['status']=='passed',audit
    (root/TEST).write_text("test('green',()=>expect(1).toBe(1));")
    (root/Path(TEST).parent/'Ad.tsx').write_text('green')
    assert subprocess.run(['./node_modules/.bin/jest','--config','libs/sellerhub-store/jest.config.ts','--runInBand','--runTestsByPath',TEST],cwd=root,capture_output=True).returncode==0
    verification=json.loads(subprocess.check_output([sys.executable,'scripts/omc_pipeline_guard.py','verification-template'],cwd=root,text=True))
    verification['implementation']={'status':'applied','evidence':'real local edit after registration'}
    verification['checks']['tests'].update(status='passed',evidence='real Jest GREEN')
    v=root/'.omc/verification/final.json';v.parent.mkdir(exist_ok=True);v.write_text(json.dumps(verification))
    done=subprocess.run([sys.executable,'scripts/omc_pipeline_guard.py','complete-task','--verification-file',str(v)],cwd=root,capture_output=True,text=True)
    assert done.returncode==0,done.stdout
    assert done.stdout.splitlines()[0]=='필수 검증 통과·작업 완료'


def test_diagnose_public_cli_is_read_only_and_receipts_stay_array(project):
    root=project;configured(root);install_scripts(root)
    import omc_native_red_evidence as red
    observed_shell(root,red.COMMAND+'; true')
    before={str(p):hashlib.sha256(p.read_bytes()).hexdigest() for p in (root/'.omc').rglob('*') if p.is_file()}
    result=cli(root,'diagnose');assert result.returncode==0,result.stdout
    diagnostic=json.loads(result.stdout)
    assert diagnostic['read_only'] is True
    assert 'command_mismatch' in diagnostic['reason_codes']
    assert diagnostic['receipts']==[]
    assert diagnostic['next_action']=='지원되는 observe 명령을 단독 호출하고 반환 뒤 receipts를 확인하세요. runner 재실행이나 기록 삭제는 하지 마세요.'
    assert json.loads(cli(root,'receipts').stdout)==[]
    after={str(p):hashlib.sha256(p.read_bytes()).hexdigest() for p in (root/'.omc').rglob('*') if p.is_file()}
    assert before==after


def test_nested_runner_error_preserves_stream(project):
    root=project;configured(root);install_scripts(root)
    p=root/'.omc/native-red/events.jsonl';p.parent.mkdir(exist_ok=True);p.write_text('original stream')
    result=subprocess.run([sys.executable,str(SCRIPTS/'omc_native_red_evidence.py'),'run','--target',str(root),'--launch',sys.executable,'-c','print("must not run")'],capture_output=True,text=True)
    assert result.returncode==1
    d=json.loads(result.stdout);assert d['reason']=='native_stream_already_exists'
    assert '삭제' in d['next_action'] and '재실행' in d['next_action']
    assert 'must not run' not in result.stdout and p.read_text()=='original stream'


@pytest.mark.parametrize('defect,expected,first',[(None,'done','필수 검증 통과·작업 완료'),('unrun','blocked','구현됨·검증 미완료'),('failed','blocked','검증 실패·수정 필요'),('mismatch','blocked','증거 불일치·재검증 필요')])
def test_official_and_model_report_are_separate_public_surfaces(tmp_path,defect,expected,first):
    raw=response(first=first,outcome=expected)
    result,dest=collect(tmp_path,raw,expected=expected,scope_defect=defect)
    assert result.returncode==0,result.stdout
    comparison=json.loads((dest/'report-comparison.json').read_text())
    assert comparison['official_state']==first and comparison['model_state']==first
    assert comparison['state_match'] is True
    assert (dest/'final.txt').read_text()==raw
    assert '공식 판정' in (dest/'report-comparison.txt').read_text()


def test_mismatched_model_state_is_preserved_and_holds(tmp_path):
    raw=response(first='구현됨·검증 미완료')
    result,dest=collect(tmp_path,raw,scope_defect='failed')
    assert result.returncode==1
    comparison=json.loads((dest/'report-comparison.json').read_text())
    assert comparison['official_state']=='검증 실패·수정 필요'
    assert comparison['model_state']=='구현됨·검증 미완료'
    assert comparison['state_match'] is False
    assert (dest/'final.txt').read_text()==raw


def test_unverified_implementation_cli_uses_exact_official_state(tmp_path):
    _,dest=collect(tmp_path,response())
    root=tmp_path/'work';v=root/'.omc/verification/s.json';d=json.loads(v.read_text());d['implementation']['status']='unverified';v.write_text(json.dumps(d))
    result=subprocess.run([sys.executable,str(SCRIPTS/'omc_pipeline_guard.py'),'complete-task','--verification-file',str(v)],cwd=root,capture_output=True,text=True)
    assert result.returncode==1
    assert result.stdout.splitlines()[0]=='구현 여부 확인 불가·검증 미완료'
    source=tmp_path/'events.jsonl';events=[json.loads(x) for x in source.read_text().splitlines()]
    for row in events:
        for b in row.get('message',{}).get('content',[]):
            if b.get('type')=='tool_result' and b['tool_use_id']=='2':
                b['content']=result.stdout
        if row.get('type')=='result':row['result']=response(first=result.stdout.splitlines()[0])
    source.write_text(''.join(json.dumps(x)+'\n' for x in events))
    subprocess.run([sys.executable,str(SCRIPTS/'omc_workflow_validation.py'),'--target',str(root),'--events-file',str(source),'--session-id','s','--verification-file',str(v),'--expected-outcome','blocked','--output-dir',str(dest)],capture_output=True)
    comparison=json.loads((dest/'report-comparison.json').read_text())
    assert comparison['official_state']==comparison['model_state']=='구현 여부 확인 불가·검증 미완료'


def test_home_receipt_surface_accepts_suffix_without_rewriting_prefix(home):
    values=home_rows(home[2],command='python3 scripts/omc_home_observation.py observe --target . 2>&1')
    assert assess_home(home,values)['status']=='passed'
    raw=(home[0]/'.omc/native.jsonl').read_text()
    assert '2>&1' in raw


@pytest.mark.parametrize('tail',['; true','\ntrue',' | cat',' "2>&1"'])
def test_home_receipt_surface_rejects_compound_commands(home,tail):
    values=home_rows(home[2],command='python3 scripts/omc_home_observation.py observe --target .'+tail)
    assert assess_home(home,values)['status']!='passed'


@pytest.mark.parametrize('kind,expected',[('missing','stream_missing'),('pending','call_return_missing'),('malformed','stream_unreadable')])
def test_diagnostic_uncertainty_does_not_guess_a_hook_cause(project,kind,expected):
    root=project;configured(root);install_scripts(root)
    stream=root/'.omc/native-red/events.jsonl';stream.parent.mkdir(exist_ok=True)
    if kind=='pending':stream.write_text(json.dumps({'type':'assistant','message':{'content':[{'type':'tool_use','id':'x','name':'Bash','input':{'command':'python3 scripts/omc_native_red_evidence.py observe --target .'}}]}})+'\n')
    elif kind=='malformed':stream.write_text('not-json\n')
    result=cli(root,'diagnose');assert result.returncode==0,result.stdout
    d=json.loads(result.stdout)
    assert expected in d['reason_codes']+d['unknown']
    assert 'session_hook' not in result.stdout and '세션 훅' not in result.stdout


def test_install_manifest_includes_shared_recognizer():
    import install
    assert 'omc_native_command.py' in install._deployed_script_names(SCRIPTS.parent)


def test_task_templates_describe_diagnostics_and_official_report(installed_omc_target):
    root=SCRIPTS.parent
    pairs=[('templates/.agents/skills/omc-task/SKILL.md','.agents/skills/omc-task/SKILL.md'),
           ('templates/.claude/commands/task.md','.claude/commands/task.md')]
    for template,deployed in pairs:
        content=(root/template).read_bytes()
        assert content==(installed_omc_target/deployed).read_bytes()
        assert b'report-comparison.txt' in content and b'diagnose --target .' in content


@pytest.mark.parametrize('earlier_receipt',[False,True])
def test_pending_diagnostic_waits_for_existing_call_before_new_observation(project,earlier_receipt):
    root=project;configured(root);install_scripts(root)
    import omc_native_red_evidence as red
    if earlier_receipt:
        _,stream=observed_shell(root)
    else:
        stream=root/'.omc/native-red/events.jsonl';stream.parent.mkdir(exist_ok=True)
    with stream.open('a') as out:
        out.write(json.dumps({'type':'assistant','message':{'content':[{'type':'tool_use','id':'still-pending','name':'Bash','input':{'command':red.COMMAND}}]}})+'\n')
    before={str(p):hashlib.sha256(p.read_bytes()).hexdigest() for p in (root/'.omc').rglob('*') if p.is_file()}
    result=cli(root,'diagnose');assert result.returncode==0,result.stdout
    d=json.loads(result.stdout)
    assert 'call_return_missing' in d['reason_codes']
    assert d['next_action']=='기존 관측 호출의 상태·반환을 먼저 확인하세요. 반환 여부를 확정하기 전에는 새 observe 호출·runner 재실행·기록 삭제를 하지 마세요.'
    assert len(d['receipts'])==int(earlier_receipt)
    after={str(p):hashlib.sha256(p.read_bytes()).hexdigest() for p in (root/'.omc').rglob('*') if p.is_file()}
    assert before==after
