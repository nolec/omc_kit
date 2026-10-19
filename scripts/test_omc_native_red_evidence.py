"""Direct Jest, public registration CLI and immutable native-result custody."""
import hashlib
import importlib
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest
import omc_pipeline_guard as guard

SCRIPTS=Path(__file__).parent
DEPENDENCIES=Path(os.environ.get('OMC_NATIVE_RED_NODE_MODULES', '/Users/noseunglae/Downloads/dev/sixshop/sixshop3-storefront-fe/node_modules'))
TEST='libs/sellerhub-store/src/lib/components/Ad/Ad.test.tsx'
CONFIG='libs/sellerhub-store/jest.config.ts'
ARGV=['./node_modules/.bin/jest','--config',CONFIG,'--runInBand','--runTestsByPath',TEST]


def module():
    return importlib.import_module('omc_native_red_evidence')


def cli(root, *args):
    return subprocess.run([sys.executable,str(SCRIPTS/'omc_native_red_evidence.py'),*args,'--target',str(root)],capture_output=True,text=True)


def guard_cli(root,*args):
    return subprocess.run([sys.executable,str(SCRIPTS/'omc_pipeline_guard.py'),*args],cwd=root,capture_output=True,text=True,env={**os.environ,'PYTHONPATH':str(SCRIPTS)})


@pytest.fixture
def project(tmp_path):
    root=tmp_path/'candidate';root.mkdir()
    if not (DEPENDENCIES/'jest/bin/jest.js').is_file():
        pytest.fail('actual Jest dependency required; no simulated execution fallback')
    (root/'node_modules').symlink_to(DEPENDENCIES,target_is_directory=True)
    (root/TEST).parent.mkdir(parents=True)
    (root/CONFIG).write_text("module.exports = {testEnvironment:'node',transform:{},testMatch:['**/Ad.test.tsx']};\n")
    (root/TEST).write_text("test('old failure',()=>expect(1).toBe(2)); test('passed case',()=>expect(1).toBe(1));\n")
    (root/Path(TEST).parent/'Ad.tsx').write_text('baseline')
    (root/'.omc/state').mkdir(parents=True)
    (root/'.omc/state/latest.json').write_text(json.dumps({'latest_confirmed_session_id':'s','latest_session_id':'s'}))
    return root


def configured(root):
    result=cli(root,'baseline','--run-id','run')
    assert result.returncode==0,result.stdout+result.stderr
    baseline=json.loads(result.stdout)
    pin={'schema_version':'omc-native-red-policy/v1','argv':ARGV,'test_file':TEST,'run_id':'run',
         'events_path':'.omc/native-red/events.jsonl','product_paths':[str(Path(TEST).parent)],'baseline':baseline}
    basis={'session_id':'s','run_id':'run','revision':1,'request':'Ad behavior','approval_reference':'test approval',
           'checks':[{'id':'tests','target':TEST,'method':'Jest','pass_condition':'new RED before edit','target_version':'candidate','native_red':pin}]}
    proposal=root/'.omc/proposal.json';proposal.write_text(json.dumps(basis))
    assert guard.cmd_approve_checks(root,proposal)==0
    assert guard.cmd_contract_done(root,required_checks='tests')==0
    (root/TEST).write_text((root/TEST).read_text()+"test('new failure',()=>expect(2).toBe(3));\n")
    return basis


def observed(root):
    result=cli(root,'observe')
    assert result.returncode==0,result.stdout+result.stderr
    value=json.loads(result.stdout)
    call={'type':'assistant','message':{'content':[{'type':'tool_use','id':'red-call','name':'Bash','input':{'command':'python3 scripts/omc_native_red_evidence.py observe --target .'}}]}}
    returned={'type':'user','message':{'content':[{'type':'tool_result','tool_use_id':'red-call','content':result.stdout}]}}
    events=root/'.omc/native-red/events.jsonl';events.parent.mkdir(exist_ok=True)
    events.write_text('\n'.join(json.dumps(r) for r in [{'type':'system','subtype':'init'},call,returned])+'\n')
    assert module().publish(root)
    refs=module().references(root)
    assert len(refs)==1
    return value, refs[0]


def evidence(root,ref,case='new failure'):
    result=cli(root,'evidence','--receipt',ref['path'],'--receipt-sha256',ref['sha256'],'--case-id',case)
    if result.returncode:
        return result,None
    path=root/json.loads(result.stdout)['path']
    return result,path


def test_actual_jest_new_failure_and_historical_registration(project):
    root=project;configured(root)
    assert guard_cli(root,'check',str(Path(TEST).parent/'Ad.tsx')).returncode==1
    assert guard_cli(root,'check-edit',str(Path(TEST).parent/'Ad.tsx')).returncode==1
    assert guard_cli(root,'check',TEST).returncode==0
    value,ref=observed(root)
    assert value['test_exit_code']==1
    assert set(value['failed_cases'])=={'old failure','new failure'}
    result,path=evidence(root,ref);assert result.returncode==0,result.stdout
    assert guard_cli(root,'red-done',TEST,'--evidence-file',str(path)).returncode==0
    assert guard_cli(root,'check',str(Path(TEST).parent/'Ad.tsx')).returncode==0
    (root/TEST).write_text("test('green',()=>expect(1).toBe(1));")
    (root/Path(TEST).parent/'Ad.tsx').write_text('green implementation')
    assert guard._red_evidence_problems(root,guard._load_state(root))==[]


@pytest.mark.parametrize('case',['passed case','old failure'])
def test_public_cli_rejects_passed_and_baseline_cases(project,case):
    configured(project);_,ref=observed(project)
    result,path=evidence(project,ref,case)
    assert result.returncode!=0 and path is None


def test_manual_red_and_changed_test_rejected(project):
    configured(project);_,ref=observed(project)
    _,path=evidence(project,ref)
    value=json.loads(path.read_text());value.pop('native_receipt')
    manual=project/'.omc/manual.json';manual.write_text(json.dumps(value))
    assert guard_cli(project,'red-done',TEST,'--evidence-file',str(manual)).returncode==1
    (project/TEST).write_text('// changed after observation')
    assert guard_cli(project,'red-done',TEST,'--evidence-file',str(path)).returncode==1


@pytest.mark.parametrize('damage',['output','result','call','prefix','baseline'])
def test_corrupted_execution_or_native_custody_rejected(project,damage):
    basis=configured(project);value,ref=observed(project)
    if damage in {'output','result'}:
        record=module().validate_execution(project,value['execution'])
        (project/record['stdout_path' if damage=='output' else 'jest_path']).write_text('{}')
    elif damage=='baseline':
        (project/basis['checks'][0]['native_red']['baseline']['path']).write_text('{}')
    else:
        events=project/'.omc/native-red/events.jsonl'
        events.write_text(events.read_text().replace('red-call','other-call') if damage=='call' else events.read_text()[:-20])
    result,path=evidence(project,ref)
    assert result.returncode!=0 and path is None


def test_execution_error_is_not_red(project):
    configured(project)
    (project/TEST).write_text('this is not valid JavaScript !!!')
    result=cli(project,'observe');assert result.returncode!=0
    assert module().references(project)==[]


def test_reobserving_keeps_first_receipt(project):
    configured(project);_,ref=observed(project)
    original=(project/ref['path']).read_bytes()
    result=cli(project,'observe');assert result.returncode==0
    rows=[{'type':'assistant','message':{'content':[{'type':'tool_use','id':'second','name':'Bash','input':{'command':'python3 scripts/omc_native_red_evidence.py observe --target .'}}]}},
          {'type':'user','message':{'content':[{'type':'tool_result','tool_use_id':'second','content':result.stdout}]}}]
    with (project/'.omc/native-red/events.jsonl').open('a') as stream:
        stream.write(''.join(json.dumps(r)+'\n' for r in rows))
    assert module().publish(project)
    assert len(module().references(project))==2
    assert (project/ref['path']).read_bytes()==original


def test_direct_hook_uses_installed_source_and_scoped_red(project):
    configured(project)
    scripts=project/'scripts';scripts.mkdir()
    # The same deployment routine used by install.py, rather than a fake guard.
    import install
    for path in SCRIPTS.glob('omc_*.py'):
        install._copy(path,scripts/path.name,force=False)
    hook=Path(__file__).parents[1]/'templates/.agent-hooks/omc-pipeline-check.sh'
    installed=project/'.agent-hooks/omc-pipeline-check.sh'
    install._copy(hook,installed,force=False)
    assert installed.read_bytes()==(Path(__file__).parents[1]/'.agent-hooks/omc-pipeline-check.sh').read_bytes()
    assert (scripts/'omc_native_red_evidence.py').read_bytes()==(SCRIPTS/'omc_native_red_evidence.py').read_bytes()
    product=project/Path(TEST).parent/'Ad.tsx';original=product.read_bytes()
    def hook_call(path):
        return subprocess.run(['sh',str(installed)],cwd=project,input=json.dumps({'tool_name':'Edit','tool_input':{'file_path':str(path)}}),text=True,capture_output=True)
    assert hook_call(product).returncode==2
    assert hook_call(TEST).returncode==0
    styled=project/Path(TEST).parent/'Ad.styled.ts';styled.write_text('style')
    assert hook_call(styled).returncode==2
    _,ref=observed(project);_,path=evidence(project,ref)
    assert guard_cli(project,'red-done',TEST,'--evidence-file',str(path)).returncode==0
    assert hook_call(product).returncode==0
    assert product.read_bytes()==original


def test_timeout_missing_executable_and_changed_candidate_are_unknown(project,monkeypatch):
    configured(project)
    def timeout(*args,**kwargs):
        if isinstance(args[0],list) and args[0][0]==ARGV[0]:
            raise subprocess.TimeoutExpired(args[0],120,output=b'partial')
        return real(*args,**kwargs)
    real=subprocess.run
    monkeypatch.setattr(subprocess,'run',timeout)
    with pytest.raises(ValueError,match='execution_unknown'):
        module().execute(project,'observe',None)
    monkeypatch.setattr(subprocess,'run',real)
    # Changing a source inside an actual test process invalidates its snapshot.
    (project/TEST).write_text("const fs=require('fs'); test('new failure',()=>{fs.writeFileSync('libs/sellerhub-store/src/lib/components/Ad/Ad.tsx','changed');expect(1).toBe(2)});")
    assert cli(project,'observe').returncode==1
    (project/'node_modules').unlink()
    assert cli(project,'observe').returncode==1


def test_tee_wrapper_publishes_completed_call_once(project):
    configured(project)
    result=cli(project,'observe');assert result.returncode==0
    rows=[{'type':'system','subtype':'init'},
          {'type':'assistant','message':{'content':[{'type':'tool_use','id':'wrapped','name':'Bash','input':{'command':module().COMMAND}}]}},
          {'type':'user','message':{'content':[{'type':'tool_result','tool_use_id':'wrapped','content':result.stdout}]}}]
    emit='import sys;sys.stdout.write('+repr(''.join(json.dumps(r)+'\n' for r in rows))+')'
    wrapped=subprocess.run([sys.executable,str(SCRIPTS/'omc_native_red_evidence.py'),'run','--target',str(project),'--launch',sys.executable,'-c',emit],capture_output=True,text=True)
    assert wrapped.returncode==0,wrapped.stderr
    assert len(module().references(project))==1
    ref=module().references(project)[0]
    module().validate_receipt(project,ref)
    again=subprocess.run([sys.executable,str(SCRIPTS/'omc_native_red_evidence.py'),'run','--target',str(project),'--launch',sys.executable,'-c',emit],capture_output=True,text=True)
    assert again.returncode!=0
    assert module().references(project)==[ref]


@pytest.mark.parametrize('contract_suffix', ['', '\npython3 -c "print(1)"', ';python3 -c "print(1)"'])
def test_public_collector_accepts_order_and_rejects_forged_case(project,contract_suffix):
    configured(project)
    # Construct a captured native trace with real execution/registration outputs.
    contract=[{'type':'system','subtype':'init'},
              {'type':'assistant','message':{'content':[{'type':'tool_use','id':'contract','name':'Bash','input':{'command':'python3 scripts/omc_pipeline_guard.py contract-done --required-checks tests'+contract_suffix}}]}},
              {'type':'user','message':{'content':[{'type':'tool_result','tool_use_id':'contract','content':'CONTRACT 확인 완료'}]}}]
    _,ref=observed(project)
    events=project/'.omc/native-red/events.jsonl'
    observed_rows=[json.loads(line) for line in events.read_text().splitlines()][1:]
    events.write_text(''.join(json.dumps(r)+'\n' for r in contract+observed_rows))
    # Prefix changed, so explicitly publish/select a new immutable receipt.
    module().publish(project)
    ref=next(r for r in module().references(project) if module().selected(project,r)['prefix_bytes']==len(events.read_bytes()))
    _,path=evidence(project,ref)
    result=guard_cli(project,'red-done',TEST,'--evidence-file',str(path));assert result.returncode==0,result.stdout
    red=[{'type':'assistant','message':{'content':[{'type':'tool_use','id':'register','name':'Bash','input':{'command':'python3 scripts/omc_pipeline_guard.py red-done '+TEST+' --evidence-file '+str(path.relative_to(project))}}]}},
         {'type':'user','message':{'content':[{'type':'tool_result','tool_use_id':'register','content':result.stdout}]}}]
    edit=[{'type':'assistant','message':{'content':[{'type':'tool_use','id':'edit','name':'Edit','input':{'file_path':str(Path(TEST).parent/'Ad.tsx')}}]}},
          {'type':'user','message':{'content':[{'type':'tool_result','tool_use_id':'edit','content':'updated'}]}}]
    stage=[{'type':'assistant','message':{'content':[{'type':'tool_use','id':'stage','name':'Bash','input':{'command':'git add -- '+str(Path(TEST).parent/'Ad.tsx')}}]}},
           {'type':'user','message':{'content':[{'type':'tool_result','tool_use_id':'stage','content':''}]}}]
    rows=contract+observed_rows+red+edit+stage+[{'type':'result','subtype':'success','result':'incomplete'}]
    events.write_text(''.join(json.dumps(r)+'\n' for r in rows))
    import omc_workflow_validation as collector
    basis=guard._load_approved_basis(project,'s')
    expected_status=collector.assess_native_procedure(project,rows,basis,events)['status']
    verification=project/'.omc/verification.json';verification.write_text('{}')
    destination=project/'.omc/collected'
    def public():
        subprocess.run([sys.executable,str(SCRIPTS/'omc_workflow_validation.py'),'--target',str(project),'--events-file',str(events),'--session-id','s','--verification-file',str(verification),'--expected-outcome','blocked','--output-dir',str(destination)],capture_output=True)
        return json.loads((destination/'assessment.json').read_bytes())['required']['native_procedure']
    if contract_suffix:
        assert expected_status=='unknown'
        assert public()=='unknown'
        return
    assert expected_status=='passed'
    assert public()=='passed'
    doc=json.loads(path.read_bytes());doc['case_id']='passed case';path.write_text(json.dumps(doc))
    assert public()=='failed'


def test_native_policy_cannot_be_removed_by_new_revision(project):
    basis=configured(project)
    basis['revision']=2;basis['checks'][0].pop('native_red')
    path=project/'.omc/downgrade.json';path.write_text(json.dumps(basis))
    assert guard.cmd_approve_checks(project,path)==1


def test_native_red_cannot_be_lowered_by_styled_filename_before_contract(project):
    configured(project)
    state=guard._load_state(project);state.update(contract_confirmed=False,basis_sha256='')
    guard._save_state(project,state)
    path=Path(TEST).parent/'Ad.styled.ts'
    (project/path).write_text('style')
    assert guard_cli(project,'check',str(project/path)).returncode==1


def test_public_installer_and_installed_registration_cli(project):
    install=subprocess.run([sys.executable,str(SCRIPTS/'install.py'),'--target',str(project)],capture_output=True,text=True)
    assert install.returncode==0,install.stdout+install.stderr
    installed=project/'scripts/omc_native_red_evidence.py'
    assert installed.read_bytes()==(SCRIPTS/installed.name).read_bytes()
    assert (project/'.agent-hooks/omc-pipeline-check.sh').read_bytes()==(SCRIPTS.parent/'templates/.agent-hooks/omc-pipeline-check.sh').read_bytes()
    # Reuse real public setup/runner helpers, then register with the deployed CLI.
    configured(project)
    actual=subprocess.run([sys.executable,str(installed),'observe','--target',str(project)],capture_output=True,text=True)
    assert actual.returncode==0,actual.stdout+actual.stderr
    value=json.loads(actual.stdout)
    rows=[{'type':'system','subtype':'init'},
          {'type':'assistant','message':{'content':[{'type':'tool_use','id':'installed','name':'Bash','input':{'command':module().COMMAND}}]}},
          {'type':'user','message':{'content':[{'type':'tool_result','tool_use_id':'installed','content':actual.stdout}]}}]
    (project/'.omc/native-red/events.jsonl').write_text(''.join(json.dumps(r)+'\n' for r in rows))
    module().publish(project)
    ref=module().references(project)[0];_,path=evidence(project,ref)
    result=subprocess.run([sys.executable,str(project/'scripts/omc_pipeline_guard.py'),'red-done',TEST,'--evidence-file',str(path)],cwd=project,capture_output=True,text=True)
    assert result.returncode==0,result.stdout+result.stderr
    assert value['failed_cases']==['new failure','old failure']


def test_native_wrapper_preserves_home_publisher_when_both_are_selected(project):
    basis=configured(project)
    import omc_home_observation as home
    req={'schema_version':'omc-home-motion-requirement/v1','session_id':'s','run_id':'run','url':'http://localhost:3035/shop',
         'events_path':basis['checks'][0]['native_red']['events_path'],'receipt_path':'.omc/home/receipt.json',
         'preflight_path':'.omc/home/ready.json','preflight_sha256':'a'*64}
    path=project/home.REQUIREMENT;path.write_text(json.dumps(req))
    basis['revision']=2
    basis['checks'].append({'id':'browser-home','target':'home','method':'observe','pass_condition':'six samples','target_version':'candidate','home_motion_sha256':hashlib.sha256(path.read_bytes()).hexdigest()})
    proposal=project/'.omc/both.json';proposal.write_text(json.dumps(basis))
    assert guard.cmd_approve_checks(project,proposal)==0
    assert guard.cmd_contract_done(project,required_checks='tests,browser-home')==0
    result=cli(project,'observe');assert result.returncode==0,result.stdout
    observation={'schema_version':home.SCHEMA,'session_id':'s','run_id':'run','url':req['url'],'code_snapshot_sha256':guard._code_snapshot_digest(project),'server_binding':'passed','tracks':[]}
    rows=[{'type':'system','subtype':'init'}]
    for ident,cmd,text in [('red',module().COMMAND,result.stdout),('home','python3 scripts/omc_home_observation.py observe --target .',json.dumps(observation))]:
        rows.extend([{'type':'assistant','message':{'content':[{'type':'tool_use','id':ident,'name':'Bash','input':{'command':cmd}}]}},
                     {'type':'user','message':{'content':[{'type':'tool_result','tool_use_id':ident,'content':text}]}}])
    emit='import sys;sys.stdout.write('+repr(''.join(json.dumps(r)+'\n' for r in rows))+')'
    wrapped=subprocess.run([sys.executable,str(SCRIPTS/'omc_native_red_evidence.py'),'run','--target',str(project),'--launch',sys.executable,'-c',emit],capture_output=True,text=True)
    assert wrapped.returncode==0,wrapped.stdout+wrapped.stderr
    assert len(module().references(project))==1
    assert len(home.receipt_references(project))==1
