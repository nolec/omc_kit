"""Public CLI regression for bounded same-work edit resumption."""
import json
import subprocess
import sys
from pathlib import Path

SCRIPT = Path(__file__).with_name("omc_edit_resume.py")


def test_missing_approval_is_closed(tmp_path):
    result = subprocess.run([sys.executable, str(SCRIPT), "--target", str(tmp_path),
                             "approve-edit-resume", "--file", str(tmp_path / "missing.json")],
                            text=True, capture_output=True)
    assert result.returncode == 1, result.stderr
    assert "edit_resume_blocked" in result.stdout
    assert not list(tmp_path.rglob("slots/*.json"))


def test_capabilities_surface():
    result = subprocess.run([sys.executable, str(SCRIPT), "capabilities"], text=True, capture_output=True)
    assert result.returncode == 0, result.stderr
    value = json.loads(result.stdout)
    assert value["single_slot_per_work"] is True
    assert value["ttl_seconds"] == 1800
    assert value["response_delivery_required"] is False


def test_expired_reset_preserves_ledger(tmp_path):
    folder = tmp_path / ".omc"
    folder.mkdir()
    ledger = folder / "pipeline_session.json"
    ledger.write_text(json.dumps({"started_at": 1, "session_id": "old", "owner_work_id": "work",
                                 "contract_confirmed": True, "red_done_tests": []}))
    original = ledger.read_bytes()
    result = subprocess.run([sys.executable, str(SCRIPT.with_name("omc_pipeline_guard.py")),
                             "--target", str(tmp_path), "reset"], text=True, capture_output=True)
    assert result.returncode == 1, result.stdout
    assert ledger.read_bytes() == original

import os
import hashlib
import time
import pytest
import omc_edit_resume as resume
import omc_pipeline_guard as guard
import omc_state

ROOT = SCRIPT.parents[1]


def call(root, script, *args, env=None, input=None, sample=('fixture-boot',1000.0)):
    # Test-only injected clock in a separate CLI process; production exposes no clock flag/env.
    program = "import sys,runpy;sys.path.insert(0," + repr(str(ROOT/'scripts')) + ");import omc_edit_resume as r;r.clock=lambda:" + repr(sample) + ";sys.argv=sys.argv[1:];import importlib,pathlib;m=importlib.import_module(pathlib.Path(sys.argv[0]).stem);raise SystemExit(m.main() if hasattr(m,'main') else m.output_cli())"
    options = [args[0], '--target', str(root), *args[1:]] if script == 'omc_output_contract.py' else ['--target', str(root), *args]
    return subprocess.run([sys.executable, '-c', program, str(ROOT / 'scripts' / script), *options],
                          capture_output=True, text=True, env=env, input=input, timeout=30)


@pytest.fixture
def expired(tmp_path, monkeypatch):
    monkeypatch.setattr(resume, 'clock', lambda: ('fixture-boot', 1000.0))
    root = tmp_path / 'repo'
    subprocess.run(['git', 'clone', '--no-hardlinks', '--quiet', str(ROOT), str(root)], check=True)
    (root / 'app.py').write_text('value = 1\n')
    (root / 'test_app.py').write_text('from app import value\ndef test_value():\n    assert value == 2\n')
    omc_state.init_state(root)
    first = omc_state.record_session(root, mode='autopilot', title='fixture', request='fixture original work',
                                    role_ids=['senior_coding'], confirmed=True, work_class='implementation',
                                    completion_action='start')
    current = omc_state.record_session(root, mode='autopilot', title='fixture', request='same work repair',
                                      role_ids=['senior_coding'], confirmed=True, work_class='implementation',
                                      completion_action='continue', work_id=first['work_id'])
    checks = {'session_id':current['session_id'], 'revision':1, 'request':'fixture repair',
              'approval_reference':'explicit fixture approval', 'checks':[{'id':'tests', 'target':'app.py',
              'method':'pytest test_app.py', 'pass_condition':'pass', 'target_version':'fixture'}]}
    file = root / '.omc/checks.json'; file.write_text(json.dumps(checks))
    assert guard.cmd_approve_checks(root, file) == 0
    state = resume.original(root)
    state['started_at'] = time.time() - 28801
    (root / '.omc/pipeline_session.json').write_text(json.dumps(state))
    request = root / '.omc/request.json'
    request.write_text(json.dumps({'grant_id':'fixture-grant', 'approval_reference':'explicit fixture user approval',
                                  'purpose':'fixture repair', 'allowed_files':['app.py','test_app.py'],
                                  'required_checks':['tests']}))
    value=json.loads(request.read_text())
    value.update(expected_binding_sha256=resume.digest(json.dumps(resume.ownership(root),sort_keys=True).encode()),
                 expected_code_sha256=resume.digest(json.dumps(resume.inventory(root,[]),sort_keys=True).encode()),
                 expected_index_sha256=resume.digest(resume.git(root,'ls-files','--stage')))
    request.write_text(json.dumps(value))
    return root, request


def test_issuance_and_missing_context_surface(expired, monkeypatch):
    root, request = expired
    protected = {str(p.relative_to(root)):p.read_bytes() for p in (root/'.omc').rglob('*.json')}
    result = call(root, 'omc_edit_resume.py', 'approve-edit-resume', '--file', str(request))
    assert result.returncode == 0, result.stdout + result.stderr
    ref = json.loads(result.stdout)
    blocked = call(root, 'omc_pipeline_guard.py', 'contract-done', '--required-checks', 'tests')
    assert blocked.returncode == 1 and 'explicit_resume_context_required' in blocked.stdout
    env = dict(os.environ, **{resume.ENV:json.dumps(ref)})
    contract = call(root, 'omc_pipeline_guard.py', 'contract-done', '--required-checks', 'tests', env=env)
    assert contract.returncode == 0, contract.stdout + contract.stderr
    edit = call(root, 'omc_pipeline_guard.py', 'check-edit', 'app.py', env=env)
    assert edit.returncode == 1 and 'new_red_required' in edit.stdout
    for name, raw in protected.items():
        assert (root/name).read_bytes() == raw
    repeat = call(root, 'omc_edit_resume.py', 'approve-edit-resume', '--file', str(request))
    assert repeat.returncode == 1 and 'already_consumed' in repeat.stdout


@pytest.mark.parametrize('offset,allowed',[(-.001,True),(0,False),(.001,False)])
def test_fixed_deadline(expired,monkeypatch,offset,allowed):
    root, request = expired
    monkeypatch.setattr(resume,'clock',lambda:('boot',1000.0))
    ref=resume.issue(root,request);monkeypatch.setenv(resume.ENV,json.dumps(ref))
    monkeypatch.setattr(resume,'clock',lambda:('boot',2800.0+offset))
    if allowed:
        assert resume.load(root)['deadline']==2800
    else:
        with pytest.raises(ValueError,match='expired'):resume.load(root)


@pytest.mark.parametrize('sample',[('different-boot',1010),('boot',999)])
def test_boot_and_clock_regression(expired,monkeypatch,sample):
    root,request=expired;monkeypatch.setattr(resume,'clock',lambda:('boot',1000.0))
    ref=resume.issue(root,request);monkeypatch.setenv(resume.ENV,json.dumps(ref))
    monkeypatch.setattr(resume,'clock',lambda:sample)
    with pytest.raises(ValueError,match='clock_changed'):resume.load(root)


def test_scope_and_binding_fail_closed(expired,monkeypatch):
    root,request=expired;ref=resume.issue(root,request);monkeypatch.setenv(resume.ENV,json.dumps(ref))
    (root/'app.py').write_text('value = 2\n')
    assert resume.load(root)
    (root/'README.md').write_text('unapproved change')
    with pytest.raises(ValueError,match='scope'):resume.load(root)


def test_response_loss_and_inspect_read_only(expired,monkeypatch):
    root,request=expired;ref=resume.issue(root,request)
    before=Path(ref['path']).read_bytes()
    inspection=resume.inspect(root,resume.work(root))
    assert inspection['activated'] is False
    monkeypatch.setenv(resume.ENV,json.dumps({'path':inspection['path'],'sha256':inspection['sha256']}))
    assert resume.load(root)['grant_id']=='fixture-grant'
    assert Path(ref['path']).read_bytes()==before


def test_two_different_grant_ids_share_one_slot(expired):
    root,request=expired
    other=root/'.omc/other.json';data=json.loads(request.read_text());data['grant_id']='other';other.write_text(json.dumps(data))
    program="import sys,runpy;sys.path.insert(0,"+repr(str(ROOT/'scripts'))+");import omc_edit_resume as r;r.clock=lambda:('fixture-boot',1000.0);sys.argv=sys.argv[1:];import importlib,pathlib;m=importlib.import_module(pathlib.Path(sys.argv[0]).stem);raise SystemExit(m.main() if hasattr(m,'main') else m.output_cli())"
    processes=[subprocess.Popen([sys.executable,'-c',program,str(SCRIPT),'--target',str(root),'approve-edit-resume','--file',str(p)],
                                stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True) for p in (request,other)]
    outputs=[p.communicate(timeout=30) for p in processes]
    assert sorted(p.returncode for p in processes)==[0,1],outputs
    assert len(list((root/'.omc/edit-resumptions/slots').glob('*.json')))==1


def test_context_pin_mismatch(expired,monkeypatch):
    root,request=expired;ref=resume.issue(root,request);ref['sha256']='0'*64
    monkeypatch.setenv(resume.ENV,json.dumps(ref))
    with pytest.raises(ValueError,match='sha_mismatch'):resume.load(root)


def test_generation_full_cli_red_green_completion(expired,monkeypatch):
    root,request=expired
    protected=resume.ownership(root)['protected']
    ref=resume.issue(root,request);monkeypatch.setenv(resume.ENV,json.dumps(ref));env=dict(os.environ)
    value=resume.load(root);gen=root/value['generation']
    assert call(root,'omc_pipeline_guard.py','contract-done','--required-checks','tests',env=env).returncode==0
    red=subprocess.run([sys.executable,'-m','pytest','test_app.py','-q'],cwd=root,text=True,capture_output=True)
    assert red.returncode==1 and 'assert 1 == 2' in red.stdout
    out=gen/'red-output.txt';out.write_text(red.stdout+red.stderr)
    state=json.loads((gen/'pipeline.json').read_text())
    evidence={'edit_resume':{'grant_id':value['grant_id'],'generation':value['generation']},
              'session_id':state['session_id'],'basis_sha256':state['basis_sha256'],
              'test_file':'test_app.py','test_sha256':resume.digest((root/'test_app.py').read_bytes()),
              'check_id':'tests','case_id':'test_app.py::test_value','expected_failure':'value must equal 2',
              'command':'python3 -m pytest test_app.py -q','exit_code':1,'baseline_failed_cases':[],
              'failed_cases':['test_app.py::test_value'],'output_file':str(out.relative_to(root)),
              'output_sha256':resume.digest(out.read_bytes())}
    evidence_file=gen/'red.json';evidence_file.write_text(json.dumps(evidence))
    result=call(root,'omc_pipeline_guard.py','red-done','test_app.py','--evidence-file',str(evidence_file),env=env)
    assert result.returncode==0,result.stdout+result.stderr
    applied=call(root,'omc_edit_resume.py','apply','--file','app.py',env=env,input='value = 2\n')
    assert applied.returncode==0,applied.stdout+applied.stderr
    green=subprocess.run([sys.executable,'-m','pytest','test_app.py','-q'],cwd=root,text=True,capture_output=True)
    assert green.returncode==0,green.stdout+green.stderr
    subprocess.run(['git','add','app.py','test_app.py'],cwd=root,check=True)
    template=call(root,'omc_pipeline_guard.py','verification-template',env=env)
    assert template.returncode==0,template.stdout+template.stderr
    verification=json.loads(template.stdout)
    verification['implementation']={'status':'applied','evidence':'new generation app.py = 2'}
    verification['checks']['tests'].update(status='passed',evidence=green.stdout)
    verification_file=gen/'verification.json';verification_file.write_text(json.dumps(verification))
    prep=call(root,'omc_output_contract.py','prepare','--stage','task','--report-id','generation-complete',
              '--budget-sec','480',env=env)
    assert prep.returncode==0,prep.stdout+prep.stderr
    context=json.loads(prep.stdout)
    done=call(root,'omc_pipeline_guard.py','complete-task','--verification-file',str(verification_file),
              '--output-mode','detached_v1','--output-context',context['output_context'],
              '--output-context-sha256',context['output_context_sha256'],env=env)
    assert done.returncode==0,done.stdout+done.stderr
    assert done.stdout.startswith('필수 검증 통과·작업 완료')
    from omc_output_contract import read_record
    manifest=Path(context['record_path'])
    read_record(root,manifest,resume.digest(manifest.read_bytes()),json.loads(Path(context['output_context']).read_bytes()))
    assert value['generation'] in str(manifest)
    bad=dict(ref,sha256='0'*64);monkeypatch.setenv(resume.ENV,json.dumps(bad))
    with pytest.raises(ValueError):
        read_record(root,manifest,resume.digest(manifest.read_bytes()),json.loads(Path(context['output_context']).read_bytes()))
    monkeypatch.setenv(resume.ENV,json.dumps(ref))
    for name,pin in protected.items():assert resume.digest((root/name).read_bytes())==pin


@pytest.mark.parametrize('point',['before_publish','after_publish','after_fsync'])
def test_issuance_interruption_keeps_slot_and_deadline(expired,monkeypatch,point):
    root,request=expired
    real_link=resume.os.link;real_durable=resume.durable_generation
    if point=='before_publish':
        monkeypatch.setattr(resume.os,'link',lambda *a,**k:(_ for _ in ()).throw(OSError('crash before publish')))
    elif point=='after_publish':
        monkeypatch.setattr(resume,'durable_generation',lambda *a:(_ for _ in ()).throw(OSError('crash after publish')))
    else:
        def fail_after(*args):
            real_durable(*args)
            raise OSError('response lost after fsync')
        monkeypatch.setattr(resume,'durable_generation',fail_after)
    with pytest.raises(OSError):resume.issue(root,request)
    monkeypatch.setattr(resume.os,'link',real_link);monkeypatch.setattr(resume,'durable_generation',real_durable)
    slot=resume.slot_path(root,resume.work(root))
    if point=='before_publish':
        assert not slot.exists()
        ref=resume.issue(root,request)
    else:
        ref={'path':str(slot),'sha256':resume.digest(slot.read_bytes())}
        before=slot.read_bytes()
        with pytest.raises(ValueError,match='already_consumed'):resume.issue(root,request)
        assert slot.read_bytes()==before
    monkeypatch.setenv(resume.ENV,json.dumps(ref))
    assert resume.load(root)['deadline']==2800.0


def test_consumer_durability_failure_is_closed(expired,monkeypatch):
    root,request=expired;ref=resume.issue(root,request);monkeypatch.setenv(resume.ENV,json.dumps(ref))
    monkeypatch.setattr(resume,'fsync_file',lambda p:(_ for _ in ()).throw(OSError('fsync unavailable')))
    with pytest.raises(OSError):resume.load(root)


def test_lifecycle_commands_cannot_mutate_old_ledger(expired,monkeypatch):
    root,request=expired;ref=resume.issue(root,request);monkeypatch.setenv(resume.ENV,json.dumps(ref));env=dict(os.environ)
    pins=resume.ownership(root)['protected']
    for command in ('reset','session-start','approve-checks'):
        args=('--file',str(root/'.omc/checks.json')) if command=='approve-checks' else ()
        result=call(root,'omc_pipeline_guard.py',command,*args,env=env)
        assert result.returncode==1 and 'lifecycle_frozen' in result.stdout
    with pytest.raises(ValueError,match='lifecycle_frozen'):
        omc_state.record_session(root,mode='autopilot',title='attempt',request='new session',role_ids=['analysis'])
    for name,pin in pins.items():assert resume.digest((root/name).read_bytes())==pin


def test_supervisor_stops_actual_process_before_expiry(expired,monkeypatch):
    root,request=expired;ref=resume.issue(root,request);monkeypatch.setenv(resume.ENV,json.dumps(ref))
    assert guard.cmd_contract_done(root,required_checks='tests')==0
    monkeypatch.setattr(resume,'clock',lambda:('fixture-boot',2794.9))
    start=time.monotonic()
    with pytest.raises(ValueError,match='supervised_execution_timeout'):
        resume.supervise(root,[sys.executable,'-m','pytest','test_app.py'],profile='tests')
    assert time.monotonic()-start<4


def test_consumer_cannot_use_slot_while_issuer_holds_lock(expired,monkeypatch):
    root,request=expired;ref=resume.issue(root,request);env=dict(os.environ,**{resume.ENV:json.dumps(ref)})
    with resume.lock(root,resume.work(root)):
        result=call(root,'omc_edit_resume.py','validate',env=env)
        assert result.returncode==1
    assert call(root,'omc_edit_resume.py','validate',env=env).returncode==0


def test_no_operational_clock_override():
    result=subprocess.run([sys.executable,str(SCRIPT),'validate','--clock','1000'],text=True,capture_output=True)
    assert result.returncode==2 and 'unrecognized arguments' in result.stderr


@pytest.mark.parametrize('field',['approval_reference','purpose','allowed_files','required_checks','expected_binding_sha256','expected_code_sha256','expected_index_sha256'])
def test_incomplete_approval_never_consumes_slot(expired,field):
    root,request=expired;value=json.loads(request.read_text());value.pop(field);request.write_text(json.dumps(value))
    with pytest.raises((ValueError,KeyError)):resume.issue(root,request)
    assert not resume.slot_path(root,resume.work(root)).exists()


@pytest.mark.parametrize('name',['.omc/state/latest.json','.omc/state/pending-completion.json'])
def test_owner_ledger_change_blocks_consumption(expired,monkeypatch,name):
    root,request=expired;ref=resume.issue(root,request);monkeypatch.setenv(resume.ENV,json.dumps(ref))
    p=root/name;value=json.loads(p.read_text());value['tampered']=True;p.write_text(json.dumps(value))
    with pytest.raises(ValueError,match='binding_changed|lineage_invalid'):resume.load(root)


def test_unsupported_clock_does_not_issue(expired,monkeypatch):
    root,request=expired
    monkeypatch.setattr(resume,'clock',lambda:(_ for _ in ()).throw(ValueError('clock unavailable')))
    with pytest.raises(ValueError,match='clock unavailable'):resume.issue(root,request)
    assert not resume.slot_path(root,resume.work(root)).exists()


def test_other_generation_context_is_not_accepted(expired,monkeypatch):
    root,request=expired;ref=resume.issue(root,request)
    copy=root/'.omc/copied-slot.json';copy.write_bytes(Path(ref['path']).read_bytes())
    monkeypatch.setenv(resume.ENV,json.dumps({'path':str(copy),'sha256':ref['sha256']}))
    with pytest.raises(ValueError,match='wrong_work_slot'):resume.load(root)


def test_no_scope_or_new_file_escape(expired,monkeypatch):
    root,request=expired;ref=resume.issue(root,request);monkeypatch.setenv(resume.ENV,json.dumps(ref));env=dict(os.environ)
    assert guard.cmd_contract_done(root,required_checks='tests')==0
    for name in ('README.md','new_file.py'):
        result=call(root,'omc_edit_resume.py','apply','--file',name,input='changed',env=env)
        assert result.returncode==1
    assert not (root/'new_file.py').exists()


def test_generation_starts_without_old_pass_or_red(expired,monkeypatch):
    root,request=expired;ref=resume.issue(root,request);monkeypatch.setenv(resume.ENV,json.dumps(ref))
    state=resume.read(root,Path(resume.load(root)['generation'])/'pipeline.json')
    assert state['contract_confirmed'] is False and state['red_done_tests']==[]
    assert state.get('red_evidence') is None
    assert not (root/resume.load(root)['generation']/'verification.json').exists()


def test_wall_clock_rollback_does_not_change_monotonic_deadline(expired,monkeypatch):
    from types import SimpleNamespace
    root,request=expired;ref=resume.issue(root,request);monkeypatch.setenv(resume.ENV,json.dumps(ref))
    monkeypatch.setattr(resume,'time',SimpleNamespace(time=lambda:1.0))
    assert resume.load(root)['deadline']==2800


def test_allowed_file_permission_change_is_blocked(expired,monkeypatch):
    root,request=expired;ref=resume.issue(root,request);monkeypatch.setenv(resume.ENV,json.dumps(ref))
    (root/'app.py').chmod(0o755)
    with pytest.raises(ValueError,match='scope'):resume.load(root)


def test_old_red_and_verification_cannot_be_reused(expired,monkeypatch):
    root,request=expired;ref=resume.issue(root,request);monkeypatch.setenv(resume.ENV,json.dumps(ref))
    assert guard.cmd_contract_done(root,required_checks='tests')==0
    gen=root/resume.load(root)['generation']
    old=gen/'old-red.json';old.write_text('{}')
    assert guard.cmd_red_done(root,'test_app.py',old)==1
    with pytest.raises(ValueError):guard._internal_verification_path(root,root/'.omc/verification/old.json')
    record={'session_id':resume.ownership(root)['session_id'],'checks':{}}
    state=resume.read(root,gen/'pipeline.json')
    problems=guard.approved_verification_problems(root,state['session_id'],state,record)
    assert 'new_generation_verification_required' in problems


@pytest.mark.parametrize('context',['missing','wrong_pin','expired'])
def test_mutating_cli_entries_share_closed_context(expired,context):
    root,request=expired;ref=resume.issue(root,request);env=dict(os.environ)
    if context=='wrong_pin':ref['sha256']='0'*64
    if context=='missing':env.pop(resume.ENV,None)
    else:env[resume.ENV]=json.dumps(ref)
    sample=('fixture-boot',2800.0 if context=='expired' else 1000.0)
    original=(root/'.omc/pipeline_session.json').read_bytes()
    commands=[('contract-done','--required-checks','tests'),('red-done','test_app.py'),
              ('check','app.py'),('check-edit','app.py'),('verification-path',),('verification-template',),
              ('reset',),('session-start',),('allow','app.py'),('approve-checks','--file',str(root/'.omc/checks.json')),
              ('complete-task','--verification-file',str(root/'.omc/missing-verification.json'))]
    for args in commands:
        result=call(root,'omc_pipeline_guard.py',*args,env=env,sample=sample)
        assert result.returncode==1,(context,args,result.stdout,result.stderr)
        assert (root/'.omc/pipeline_session.json').read_bytes()==original


@pytest.mark.parametrize('point,changed',[('lock_enter','code'),('slot_ready','code'),('lock_enter','index')])
def test_approval_snapshot_never_advances_during_issuance(expired,monkeypatch,point,changed):
    import contextlib
    root,request=expired
    real_lock=resume.lock;real_write=resume.write_new
    def mutate():
        if changed=='code':(root/'app.py').write_text('value = 999\n')
        else:subprocess.run(['git','update-index','--force-remove','README.md'],cwd=root,check=True)
    if point=='lock_enter':
        @contextlib.contextmanager
        def race(target,work_id):
            mutate()
            with real_lock(target,work_id):yield
        monkeypatch.setattr(resume,'lock',race)
    else:
        def race(path,value):
            real_write(path,value)
            if path.name=='slot.json':mutate()
        monkeypatch.setattr(resume,'write_new',race)
    with pytest.raises(ValueError,match='approval_snapshot_mismatch|issuance_binding_changed'):
        resume.issue(root,request)
    assert not resume.slot_path(root,resume.work(root)).exists()


def _approve_changed_test(root,request,text):
    (root/'test_app.py').write_text(text)
    value=json.loads(request.read_text())
    value['expected_code_sha256']=resume.digest(json.dumps(resume.inventory(root,[]),sort_keys=True).encode())
    request.write_text(json.dumps(value))


def test_supervisor_rejects_and_cleans_children_after_parent_success(expired,monkeypatch):
    root,request=expired
    child="import time,pathlib;time.sleep(2);pathlib.Path('.omc/leftover-marker').write_text('late')"
    test="import subprocess,sys\ndef test_spawn():\n    subprocess.Popen([sys.executable,'-c',"+repr(child)+"],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)\n    assert True\n"
    _approve_changed_test(root,request,test)
    ref=resume.issue(root,request);monkeypatch.setenv(resume.ENV,json.dumps(ref));env=dict(os.environ)
    assert guard.cmd_contract_done(root,required_checks='tests')==0
    result=call(root,'omc_edit_resume.py','run','--profile','tests',sys.executable,'-m','pytest','test_app.py',env=env)
    # Also let the unfixed implementation's child finish, so a RED leaves no job behind.
    time.sleep(2.5)
    value=resume.load(root);audits=[json.loads(p.read_text()) for p in (root/value['generation']/'executions').glob('*.json')]
    assert result.returncode==1,result.stdout+result.stderr
    assert not (root/'.omc/leftover-marker').exists()
    assert audits and all(a['status']=='incomplete' for a in audits)
    assert audits[-1]['termination']=='observed'
    trace=audits[-1]['cleanup_trace']
    assert trace['owned_pid']==trace['owned_pgid'] and trace['member_states']=='unknown'
    assert any(e['operation']=='killpg_signal' for e in trace['events'])
    assert trace['clock_domain']=='monotonic' and len(trace['events'])<=32


def test_supervisor_accepts_parent_and_children_that_finish(expired,monkeypatch):
    root,request=expired
    test="import subprocess,sys\ndef test_child():\n    subprocess.run([sys.executable,'-c','pass'],check=True)\n"
    _approve_changed_test(root,request,test)
    ref=resume.issue(root,request);monkeypatch.setenv(resume.ENV,json.dumps(ref));env=dict(os.environ)
    assert guard.cmd_contract_done(root,required_checks='tests')==0
    result=call(root,'omc_edit_resume.py','run','--profile','tests',sys.executable,'-m','pytest','test_app.py',env=env)
    assert result.returncode==0,result.stdout+result.stderr
    value=resume.load(root);audits=[json.loads(p.read_text()) for p in (root/value['generation']/'executions').glob('*.json')]
    assert audits[-1]['status']=='passed'


def test_supervisor_does_not_pass_unverifiable_group_exit(expired,monkeypatch):
    root,request=expired
    _approve_changed_test(root,request,'def test_finished():\n    assert True\n')
    ref=resume.issue(root,request);monkeypatch.setenv(resume.ENV,json.dumps(ref))
    assert guard.cmd_contract_done(root,required_checks='tests')==0
    def unavailable(*args):raise PermissionError('group visibility unavailable')
    monkeypatch.setattr(resume.os,'killpg',unavailable)
    with pytest.raises(ValueError,match='process_group_unverifiable'):
        resume.supervise(root,[sys.executable,'-m','pytest','test_app.py'],profile='tests')
    value=resume.load(root)
    audits=[json.loads(p.read_text()) for p in (root/value['generation']/'executions').glob('*.json')]
    assert audits[-1]['status']=='incomplete' and audits[-1]['termination']=='unknown'
    assert any(e['operation']=='killpg_query' and e['exists']=='unknown' for e in audits[-1]['cleanup_trace']['events'])


@pytest.fixture
def retiring(tmp_path):
    """A real start session, rather than the continue-only resumption fixture."""
    root = tmp_path / 'retiring'
    root.mkdir()
    subprocess.run(['git', 'init', '-q', str(root)], check=True)
    (root / 'app.py').write_text('value = 1\n')
    subprocess.run(['git', 'add', 'app.py'], cwd=root, check=True)
    subprocess.run(['git', '-c', 'core.hooksPath=/dev/null', '-c', 'commit.gpgsign=false',
                    '-c', 'user.name=Fixture', '-c', 'user.email=fixture@localhost',
                    'commit', '-qm', 'fixture'], cwd=root, check=True)
    session = omc_state.record_session(root, mode='autopilot', title='fixture',
        request='unfinished original work', role_ids=['senior_coding'], confirmed=True,
        work_class='implementation', completion_action='start')
    state = resume.original(root)
    state['started_at'] = time.time() - 28801
    (root / '.omc/pipeline_session.json').write_text(json.dumps(state))
    return root, session


def _state_bytes(root):
    return {str(p.relative_to(root)): p.read_bytes()
            for p in (root / '.omc').rglob('*') if p.is_file() and p.suffix != '.lock'}


def _retire(root, session):
    return resume.retire_expired_work(root, session['work_id'],
                                      approval_reference='explicit fixture retirement approval')


def test_retirement_frozen_status_is_read_only(retiring):
    root, _ = retiring
    (root / '.omc/notepad.md').unlink()
    (root / '.omc/summary.md').unlink()
    before = _state_bytes(root)
    result = subprocess.run([sys.executable, str(ROOT / 'scripts/omc.py'),
                             'state', 'status', '--target', str(root)],
                            capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stdout + result.stderr
    assert 'lifecycle: FROZEN' in result.stdout
    assert _state_bytes(root) == before


def test_retirement_cli_archives_and_starts_independent_work(retiring):
    root, session = retiring
    before = _state_bytes(root)
    index = (root / '.git/index').read_bytes()
    head = resume.git(root, 'rev-parse', 'HEAD')
    result = call(root, 'omc_edit_resume.py', 'retire-expired-work',
                  '--work-id', session['work_id'], '--approval-reference', 'fixture user approval')
    assert result.returncode == 0, result.stdout + result.stderr
    receipt = json.loads(result.stdout)
    archive = root / receipt['archive']
    assert receipt['previous_work_status'] == 'EXPIRED_UNFINISHED'
    for name, expected in before.items():
        path = archive / 'originals' / name
        if name in receipt['retired_paths']:
            assert path.read_bytes() == expected
            assert not (root / name).exists()
        else:
            assert (root / name).read_bytes() == expected
    assert (root / '.git/index').read_bytes() == index
    assert resume.git(root, 'rev-parse', 'HEAD') == head
    new = omc_state.record_session(root, mode='autopilot', title='omc-task',
        request='independent document update', role_ids=['senior_coding'], confirmed=True,
        work_class='document_only', completion_action='start')
    assert new['work_id'] != session['work_id']
    assert not resume.frozen(root)
    state = resume.original(root)
    assert not state['contract_confirmed'] and not state['red_done_tests']
    with pytest.raises(ValueError, match='승인된 검증 기준이 없습니다'):
        guard._load_approved_basis(root, new['session_id'])
    old_session = root / '.omc/state/sessions' / session['session_id'] / 'session.json'
    assert old_session.read_bytes() == before[str(old_session.relative_to(root))]
    with pytest.raises(ValueError):
        omc_state.record_session(root, mode='autopilot', title='old continuation', request='revive old',
            role_ids=['senior_coding'], confirmed=True, work_class='implementation',
            completion_action='continue', work_id=session['work_id'])


@pytest.mark.parametrize('invalid', ['work', 'approval', 'fresh', 'resume-context'])
def test_retirement_rejects_unapproved_or_live_work(retiring, monkeypatch, invalid):
    root, session = retiring
    if invalid == 'fresh':
        state = resume.original(root)
        state['started_at'] = time.time()
        (root / '.omc/pipeline_session.json').write_text(json.dumps(state))
    if invalid == 'resume-context':
        monkeypatch.setenv(resume.ENV, '{}')
    before = _state_bytes(root)
    with pytest.raises(ValueError):
        resume.retire_expired_work(root, 'other' if invalid == 'work' else session['work_id'],
            approval_reference='' if invalid == 'approval' else 'fixture user approval')
    assert _state_bytes(root) == before


def test_retirement_interruption_stays_frozen_and_resumes(retiring, monkeypatch):
    root, session = retiring
    before = _state_bytes(root)
    rename = resume.os.rename
    def interrupt(src, dst):
        if Path(src).name == 'latest.json':
            raise OSError('injected retirement interruption')
        return rename(src, dst)
    monkeypatch.setattr(resume.os, 'rename', interrupt)
    with pytest.raises(OSError, match='injected retirement interruption'):
        _retire(root, session)
    assert resume.frozen(root)
    with pytest.raises(ValueError, match='lifecycle_frozen'):
        omc_state.record_session(root, mode='autopilot', title='attempt', request='unsafe partial start',
                                role_ids=['analysis'])
    assert 'lifecycle: FROZEN' in omc_state.status(root)
    monkeypatch.setattr(resume.os, 'rename', rename)
    receipt = _retire(root, session)
    for name in receipt['retired_paths']:
        assert (root / receipt['archive'] / 'originals' / name).read_bytes() == before[name]
    assert not resume.frozen(root)


def test_retirement_old_slot_does_not_freeze_new_work(expired, monkeypatch):
    root, proposal = expired
    ref = resume.issue(root, proposal)
    old_slot = Path(ref['path']).read_bytes()
    sid = resume.read(root, '.omc/state/latest.json')['latest_confirmed_session_id']
    session = resume.read(root, f'.omc/state/sessions/{sid}/session.json')
    with pytest.raises(ValueError, match='retirement_resume_still_active'):
        _retire(root, session)
    monkeypatch.setattr(resume, 'clock', lambda: ('fixture-boot', 2800.0))
    _retire(root, session)
    new = omc_state.record_session(root, mode='autopilot', title='new', request='independent work',
        role_ids=['senior_coding'], confirmed=True, work_class='implementation', completion_action='start')
    assert new['work_id'] != session['work_id'] and not resume.frozen(root)
    assert Path(ref['path']).read_bytes() == old_slot
    monkeypatch.setenv(resume.ENV, json.dumps(ref))
    with pytest.raises(ValueError):
        resume.load(root)


@pytest.mark.parametrize('drift', ['active', 'snapshot', 'approval'])
def test_retirement_interrupted_evidence_cannot_advance(retiring, monkeypatch, drift):
    root, session = retiring
    rename = resume.os.rename
    def interrupt(src, dst):
        if Path(src).name == 'latest.json':
            raise OSError('injected retirement interruption')
        return rename(src, dst)
    monkeypatch.setattr(resume.os, 'rename', interrupt)
    with pytest.raises(OSError):
        _retire(root, session)
    monkeypatch.setattr(resume.os, 'rename', rename)
    descriptor = resume.read(root, resume.RETIREMENT_PENDING)
    archive = root / descriptor['archive']
    if drift == 'active':
        (root / '.omc/state/latest.json').write_text('{}')
    elif drift == 'snapshot':
        (archive / 'snapshots/.omc/state/latest.json').write_text('{}')
    with pytest.raises(ValueError, match='retirement_'):
        resume.retire_expired_work(root, session['work_id'],
            approval_reference='changed' if drift == 'approval' else descriptor['approval_reference'])
    assert resume.frozen(root)
    assert (root / '.omc/state/pending-completion.json').exists()
    assert not (archive / 'completed.json').exists()


def test_retirement_active_run_is_not_inferred_terminated(retiring):
    root, session = retiring
    latest = resume.read(root, '.omc/state/latest.json')
    latest['active_run_id'] = 'unresolved'
    (root / '.omc/state/latest.json').write_text(json.dumps(latest))
    (root / '.omc/state/runs/unresolved.json').write_text(json.dumps({'status': 'running'}))
    before = _state_bytes(root)
    with pytest.raises(ValueError, match='active_run_unresolved'):
        _retire(root, session)
    assert _state_bytes(root) == before


def test_retirement_rejects_symlink_without_touching_target(retiring, tmp_path):
    root, session = retiring
    outside = tmp_path / 'outside.json'
    outside.write_text((root / '.omc/state/latest.json').read_text())
    expected = outside.read_bytes()
    (root / '.omc/state/latest.json').unlink()
    (root / '.omc/state/latest.json').symlink_to(outside)
    with pytest.raises(ValueError, match='unsupported_link_or_path'):
        _retire(root, session)
    assert outside.read_bytes() == expected
    assert (root / '.omc/pipeline_session.json').exists()


@pytest.mark.parametrize('value', [None, [], 'scalar', 7, True])
def test_retirement_malformed_pending_freezes_without_crashing(tmp_path, value):
    folder = tmp_path / '.omc/state'
    folder.mkdir(parents=True)
    pending = folder / 'pending-completion.json'
    pending.write_text(json.dumps(value))
    before = pending.read_bytes()
    assert resume.frozen(tmp_path) is True
    assert pending.read_bytes() == before


def _review_ready_generation(expired, monkeypatch):
    root, proposal = expired
    ref = resume.issue(root, proposal)
    monkeypatch.setenv(resume.ENV, json.dumps(ref))
    env = dict(os.environ)
    value = resume.load(root)
    gen = root / value['generation']
    assert guard.cmd_contract_done(root, required_checks='tests') == 0
    red = subprocess.run([sys.executable, '-m', 'pytest', 'test_app.py', '-q'],
                         cwd=root, capture_output=True, text=True, timeout=30)
    assert red.returncode == 1 and 'assert 1 == 2' in red.stdout
    output = gen / 'review-red-output.txt'
    output.write_text(red.stdout + red.stderr)
    state = resume.read(root, gen / 'pipeline.json')
    evidence = {'edit_resume': {'grant_id': value['grant_id'], 'generation': value['generation']},
        'session_id': state['session_id'], 'basis_sha256': state['basis_sha256'],
        'test_file': 'test_app.py', 'test_sha256': resume.digest((root / 'test_app.py').read_bytes()),
        'check_id': 'tests', 'case_id': 'test_app.py::test_value', 'expected_failure': 'value must equal 2',
        'command': 'python3 -m pytest test_app.py -q', 'exit_code': 1, 'baseline_failed_cases': [],
        'failed_cases': ['test_app.py::test_value'], 'output_file': str(output.relative_to(root)),
        'output_sha256': resume.digest(output.read_bytes())}
    path = gen / 'review-red.json'
    path.write_text(json.dumps(evidence))
    result = call(root, 'omc_pipeline_guard.py', 'red-done', 'test_app.py',
                  '--evidence-file', str(path), env=env)
    assert result.returncode == 0, result.stdout + result.stderr
    return root, ref


def test_review_apply_fences_retirement_until_file_commit(expired, monkeypatch):
    root, _ = _review_ready_generation(expired, monkeypatch)
    before = (root / '.omc/pipeline_session.json').read_bytes()
    work_id = resume.work(root)
    rename = resume.os.replace
    attempts = []
    env = dict(os.environ)
    env.pop(resume.ENV)

    def at_file_commit(src, dst):
        if Path(dst) == root / 'app.py':
            attempts.append(call(root, 'omc_edit_resume.py', 'retire-expired-work',
                '--work-id', work_id, '--approval-reference', 'fixture explicit retirement',
                env=env, sample=('fixture-boot', 2800.0)))
        return rename(src, dst)

    monkeypatch.setattr(resume.os, 'replace', at_file_commit)
    error = None
    try:
        resume.apply(root, 'app.py', b'value = 2\n')
    except (ValueError, OSError) as exc:
        error = str(exc)
    assert len(attempts) == 1
    assert attempts[0].returncode == 1, attempts[0].stdout + attempts[0].stderr
    assert error is None, error
    assert (root / 'app.py').read_text() == 'value = 2\n'
    assert (root / '.omc/pipeline_session.json').read_bytes() == before
    assert not list((root / '.omc/edit-resumptions/retirements').glob('*/completed.json'))


def test_review_failed_file_commit_releases_retirement_lock(expired, monkeypatch):
    root, _ = _review_ready_generation(expired, monkeypatch)
    work_id = resume.work(root)
    rename = resume.os.replace

    def denied(src, dst):
        if Path(dst) == root / 'app.py':
            raise PermissionError('fixture denied file commit')
        return rename(src, dst)

    monkeypatch.setattr(resume.os, 'replace', denied)
    with pytest.raises(PermissionError, match='fixture denied file commit'):
        resume.apply(root, 'app.py', b'value = 2\n')
    assert (root / 'app.py').read_text() == 'value = 1\n'
    monkeypatch.delenv(resume.ENV)
    monkeypatch.setattr(resume, 'clock', lambda: ('fixture-boot', 2800.0))
    receipt = resume.retire_expired_work(root, work_id, approval_reference='fixture explicit retirement')
    assert receipt['new_work_started'] is False


def test_review_frozen_status_preserves_git_index(retiring, monkeypatch):
    root, _ = retiring
    monkeypatch.setenv('GIT_OPTIONAL_LOCKS', '1')
    app = root / 'app.py'
    metadata = app.stat()
    os.utime(app, ns=(metadata.st_atime_ns, metadata.st_mtime_ns + 10_000_000_000))
    before = (root / '.git/index').read_bytes()
    governance = _state_bytes(root)
    assert 'lifecycle: FROZEN' in omc_state.status(root)
    assert _state_bytes(root) == governance
    assert (root / '.git/index').read_bytes() == before


@pytest.mark.parametrize('value', [None, [], 'scalar', 7, True])
def test_commit_review_invalid_resume_output_context_is_closed(expired, monkeypatch, value):
    import omc_output_contract as output
    root, proposal = expired
    ref = resume.issue(root, proposal)
    monkeypatch.setenv(resume.ENV, json.dumps(ref))
    context = root / '.omc/invalid-output-context.json'
    context.write_text(json.dumps(value))
    source = root / '.omc/review-body.txt'
    source.write_text('판정: 승인\n' + output.render_envelope(stage='review', verdict='APPROVE',
                      risk='low', next_skill=None, user_selection_needed=True))
    result = call(root, 'omc_output_contract.py', 'publish', '--output-context', str(context),
        '--output-context-sha256', resume.digest(context.read_bytes()), '--source-file', str(source),
        '--source-sha256', resume.digest(source.read_bytes()), env=dict(os.environ))
    assert result.returncode == 2, result.stdout + result.stderr
    assert 'Traceback' not in result.stderr
    assert not list((root / '.omc/state/output-records').rglob('manifest.json'))


def test_cleanup_trace_preserves_query_errno_and_is_bounded(monkeypatch):
    assert hasattr(resume,'_ExecutionTrace'), 'bounded cleanup trace is missing'
    trace=resume._ExecutionTrace(1234)
    token=resume._execution_trace.set(trace)
    def denied(*args):raise PermissionError(13,'denied')
    monkeypatch.setattr(resume.os,'killpg',denied)
    try:
        with pytest.raises(PermissionError):resume._group_exists(1234)
        for _ in range(100):resume._trace_event('sample',pid=1234)
    finally:resume._execution_trace.reset(token)
    value=trace.payload()
    assert len(value['events'])<=32 and value['truncated']>0
    assert len(json.dumps(value).encode())<=16384
    row=value['events'][0]
    assert row['operation']=='killpg_query' and row['errno']==13 and row['exists']=='unknown'
    assert value['clock_domain']=='monotonic' and value['owned_pgid']==1234


def test_cleanup_diagnostic_clock_failure_does_not_change_query_result(monkeypatch):
    trace=resume._ExecutionTrace(5678);token=resume._execution_trace.set(trace)
    monkeypatch.setattr(resume.os,'killpg',lambda *args:None)
    monkeypatch.setattr(resume.time,'monotonic',lambda:float('nan'))
    try:assert resume._group_exists(5678) is True
    finally:resume._execution_trace.reset(token)
    assert trace.payload()['record_errors']==1 and trace.payload()['events']==[]


def test_cleanup_signal_errno_preserves_unknown_and_no_extra_queries(monkeypatch):
    from types import SimpleNamespace
    trace=resume._ExecutionTrace(4321);token=resume._execution_trace.set(trace);calls=[]
    def denied(pgid,sig):calls.append((pgid,sig));raise PermissionError(13,'denied')
    monkeypatch.setattr(resume.os,'killpg',denied)
    try:assert resume._stop_group(SimpleNamespace(pid=4321)) is False
    finally:resume._execution_trace.reset(token)
    assert len(calls)==1
    assert trace.events[0]['operation']=='killpg_signal' and trace.events[0]['errno']==13


@pytest.mark.parametrize('signal_eperm', [False, True])
def test_cleanup_transient_eperm_rechecks_before_confirming_absence(monkeypatch,signal_eperm):
    from types import SimpleNamespace
    import errno
    ticks=[0.0];queries=[];signals=[];polls=[]
    monkeypatch.setattr(resume.time,'monotonic',lambda:ticks[0])
    monkeypatch.setattr(resume.time,'sleep',lambda seconds:ticks.__setitem__(0,ticks[0]+seconds))
    def killpg(pgid,sig):
        assert pgid==4321
        if sig:
            signals.append(sig)
            if signal_eperm:raise PermissionError(errno.EPERM,'transient zombie group')
        else:
            queries.append(ticks[0])
            if len(queries)==1:raise PermissionError(errno.EPERM,'transient zombie group')
            raise ProcessLookupError(errno.ESRCH,'group reaped')
    monkeypatch.setattr(resume.os,'killpg',killpg)
    def poll():polls.append(ticks[0]);return 0
    assert resume._stop_group(SimpleNamespace(pid=4321,poll=poll)) is True
    assert len(queries)==2 and len(polls)>=2 and signals==[resume.signal.SIGTERM]
    assert 0<ticks[0]<=.02, 'EPERM must not itself prove absence'


@pytest.mark.parametrize('signal_eperm', [False, True])
def test_cleanup_persistent_eperm_remains_unknown_with_original_budget(monkeypatch,signal_eperm):
    from types import SimpleNamespace
    import errno
    ticks=[0.0];signals=[];queries=[]
    monkeypatch.setattr(resume.time,'monotonic',lambda:ticks[0])
    monkeypatch.setattr(resume.time,'sleep',lambda seconds:ticks.__setitem__(0,ticks[0]+seconds))
    def denied(pgid,sig):
        if sig:
            signals.append((sig,ticks[0]))
            if signal_eperm:raise PermissionError(errno.EPERM,'denied')
        else:
            queries.append(ticks[0]);raise PermissionError(errno.EPERM,'denied')
    monkeypatch.setattr(resume.os,'killpg',denied)
    assert resume._stop_group(SimpleNamespace(pid=1234,poll=lambda:0)) is False
    assert [sig for sig,_ in signals]==[resume.signal.SIGTERM,resume.signal.SIGKILL]
    assert 1.99<=signals[1][1]<=2 and 3.99<=ticks[0]<=4.001
    assert len(queries)>2 # Repeated errors must not restart a phase deadline.


def test_cleanup_native_poll_to_query_zombie_race_is_reaped():
    """Force exit after a stale poll result, without editing the production clock."""
    import ctypes
    child=subprocess.Popen([sys.executable,'-c','import time;print("ready",flush=True);time.sleep(5)'],
                           start_new_session=True,stdout=subprocess.PIPE)
    import selectors
    ready=selectors.DefaultSelector();ready.register(child.stdout,selectors.EVENT_READ)
    try:
        assert ready.select(2) and child.stdout.readline()==b'ready\n'
        lib=ctypes.CDLL(None,use_errno=True)
        lib.waitid.argtypes=[ctypes.c_uint,ctypes.c_uint,ctypes.c_void_p,ctypes.c_int]
        class Owned:
            pid=child.pid
            def poll(self):
                stale=child.poll()
                if stale is None:
                    until=time.monotonic()+1
                    while True:
                        info=ctypes.create_string_buffer(128)
                        code=lib.waitid(os.P_PID,child.pid,info,os.WEXITED|os.WNOWAIT|os.WNOHANG)
                        assert code==0,ctypes.get_errno()
                        if any(info.raw):break
                        assert time.monotonic()<until;time.sleep(.001)
                return stale
        assert resume._stop_group(Owned()) is True
        assert child.returncode is not None
        with pytest.raises(ProcessLookupError):os.killpg(child.pid,0)
    finally:
        ready.close();child.stdout.close()
        if child.poll() is None:
            child.kill();child.wait(timeout=2)


@pytest.mark.parametrize('number',[5,13])
def test_cleanup_other_query_errors_do_not_become_absence(monkeypatch,number):
    from types import SimpleNamespace
    calls=[]
    def failed(pgid,sig):
        calls.append(sig)
        if not sig:raise OSError(number,'unverifiable')
    monkeypatch.setattr(resume.os,'killpg',failed)
    assert resume._stop_group(SimpleNamespace(pid=1234,poll=lambda:0)) is False
    assert calls==[resume.signal.SIGTERM,0]


def test_cleanup_live_group_after_eperm_still_requires_kill_and_absence(monkeypatch):
    from types import SimpleNamespace
    import errno
    ticks=[0.0];signals=[];query_count=[0]
    monkeypatch.setattr(resume.time,'monotonic',lambda:ticks[0])
    monkeypatch.setattr(resume.time,'sleep',lambda seconds:ticks.__setitem__(0,ticks[0]+seconds))
    def group(pgid,sig):
        if sig:signals.append(sig);return
        query_count[0]+=1
        if query_count[0]==1:raise PermissionError(errno.EPERM,'unconfirmed')
        if resume.signal.SIGKILL in signals:raise ProcessLookupError(errno.ESRCH,'absent')
        # Direct parent exit does not prove that its surviving group is absent.
    monkeypatch.setattr(resume.os,'killpg',group)
    assert resume._stop_group(SimpleNamespace(pid=1234,poll=lambda:0)) is True
    assert signals==[resume.signal.SIGTERM,resume.signal.SIGKILL] and 1.99<=ticks[0]<=2.001


def test_cleanup_signal_esrch_does_not_resignal_a_reappearing_group(monkeypatch):
    from types import SimpleNamespace
    import errno
    signals=[]
    def replaced(pgid,sig):
        if sig:
            signals.append(sig);raise ProcessLookupError(errno.ESRCH,'owned group gone')
        # The old group was absent at signal time; this later group is unowned.
    monkeypatch.setattr(resume.os,'killpg',replaced)
    # Fail without waiting in the broken version, so this regression is bounded.
    monkeypatch.setattr(resume,'_wait_group_exit',lambda *args:False)
    assert resume._stop_group(SimpleNamespace(pid=4321,poll=lambda:0)) is False
    assert signals==[resume.signal.SIGTERM]


def _commit_completed_work(root, session):
    state = resume.original(root)
    state['started_at'] = time.time()
    (root / '.omc/pipeline_session.json').write_text(json.dumps(state))
    (root / 'app.py').write_text('value = 2\n')
    subprocess.run(['git', 'add', 'app.py'], cwd=root, check=True)
    subprocess.run(['git', '-c', 'core.hooksPath=/dev/null', '-c', 'commit.gpgsign=false',
                    '-c', 'user.name=Fixture', '-c', 'user.email=fixture@localhost',
                    'commit', '-qm', 'completed work'], cwd=root, check=True)
    result = omc_state.record_completion_receipt(root)
    assert result['status'] == 'ok', result
    state['started_at'] = time.time() - 28801
    (root / '.omc/pipeline_session.json').write_text(json.dumps(state))
    return root / '.omc/state/sessions' / session['session_id'] / 'completion.json'


def test_completed_commit_does_not_freeze_after_pipeline_expiry(retiring):
    root, session = retiring
    _commit_completed_work(root, session)
    before = _state_bytes(root)
    index = (root / '.git/index').read_bytes()
    assert not resume.frozen(root)
    assert _state_bytes(root) == before
    assert (root / '.git/index').read_bytes() == index
    new = omc_state.record_session(root, mode='autopilot', title='next task',
        request='independent next work', role_ids=['senior_coding'], confirmed=True,
        work_class='implementation', completion_action='start')
    assert new['work_id'] != session['work_id']


def test_completed_commit_allows_descendant_head(retiring):
    root, session = retiring
    _commit_completed_work(root, session)
    subprocess.run(['git', '-c', 'core.hooksPath=/dev/null', '-c', 'commit.gpgsign=false',
                    '-c', 'user.name=Fixture', '-c', 'user.email=fixture@localhost',
                    'commit', '--allow-empty', '-qm', 'later commit'], cwd=root, check=True)
    assert not resume.frozen(root)


@pytest.mark.parametrize('damage', ['missing', 'request', 'session', 'paths', 'base',
                                   'followup', 'pending', 'owner', 'symlink', 'shape'])
def test_completed_commit_invalid_evidence_stays_frozen(retiring, damage):
    root, session = retiring
    path = _commit_completed_work(root, session)
    receipt = json.loads(path.read_bytes())
    if damage == 'missing':
        path.unlink()
    elif damage == 'pending':
        (root / '.omc/state/pending-completion.json').write_text('{}')
    elif damage == 'owner':
        state = resume.original(root); state['owner_work_id'] = 'other'
        (root / '.omc/pipeline_session.json').write_text(json.dumps(state))
    elif damage == 'symlink':
        other = root / 'receipt.json'; path.rename(other); path.symlink_to(other)
    else:
        key, value = {'request': ('request_sha256', 'bad'), 'session': ('session_id', 'other'),
                      'paths': ('changed_paths', ['other.py']), 'base': ('baseline_commit', '0'*40),
                      'followup': ('followup_commit', '0'*40), 'shape': ('schema_version', 999)}[damage]
        receipt[key] = value; path.write_text(json.dumps(receipt))
    before = _state_bytes(root)
    assert resume.frozen(root)
    assert _state_bytes(root) == before


def test_completed_commit_stays_closed_after_session_start(retiring):
    root, session = retiring
    _commit_completed_work(root, session)
    assert guard.cmd_session_start(root) == 0
    state = resume.original(root)
    assert state['session_id'] == ''
    assert state['contract_confirmed'] is False
    assert state['contract_hash'] == ''
    before = _state_bytes(root)
    index = (root / '.git/index').read_bytes()
    assert not resume.frozen(root)
    assert _state_bytes(root) == before
    assert (root / '.git/index').read_bytes() == index
    new = omc_state.record_session(root, mode='autopilot', title='next task',
        request='independent work after session start', role_ids=['senior_coding'],
        confirmed=True, work_class='implementation', completion_action='start')
    assert new['work_id'] != session['work_id']
    assert resume.original(root)['contract_confirmed'] is False


@pytest.mark.parametrize('damage', ['foreign_session', 'missing_reset_reason',
                                 'approval_flag', 'approval_hash', 'receipt_paths'])
def test_completed_commit_reset_does_not_accept_invalid_binding(retiring, damage):
    root, session = retiring
    receipt_path = _commit_completed_work(root, session)
    assert guard.cmd_session_start(root) == 0
    state = resume.original(root)
    if damage == 'foreign_session':
        state['session_id'] = 'unrelated-session'
    elif damage == 'missing_reset_reason':
        state.pop('contract_reset_reason')
    elif damage == 'approval_flag':
        state['contract_confirmed'] = True
    elif damage == 'approval_hash':
        state['contract_hash'] = 'unrelated-contract'
    else:
        receipt = json.loads(receipt_path.read_text())
        receipt['changed_paths'] = ['other.py']
        receipt_path.write_text(json.dumps(receipt))
    (root / '.omc/pipeline_session.json').write_text(json.dumps(state))
    before = _state_bytes(root)
    assert resume.frozen(root)
    assert _state_bytes(root) == before


@pytest.mark.parametrize('reset', [False, True])
@pytest.mark.parametrize('off_branch', [False, True])
def test_retire_expired_committed_preserves_evidence(retiring, reset, off_branch):
    root, session = retiring
    receipt_path = _commit_completed_work(root, session)
    if reset:
        assert guard.cmd_session_start(root) == 0
    if off_branch:
        base = json.loads(receipt_path.read_text())['baseline_commit']
        subprocess.run(['git', 'checkout', '--detach', base], cwd=root, check=True,
                       capture_output=True)
        assert resume.frozen(root)
    before = _state_bytes(root)
    index = (root / '.git/index').read_bytes()
    head = resume.git(root, 'rev-parse', 'HEAD')
    result = call(root, 'omc_edit_resume.py', 'retire-expired-work',
                  '--work-id', session['work_id'], '--approval-reference', 'fixture user approval')
    assert result.returncode == 0, result.stdout + result.stderr
    receipt = json.loads(result.stdout)
    assert receipt['previous_work_status'] == 'EXPIRED_COMMITTED'
    assert '.omc/state/pending-completion.json' not in receipt['retired_paths']
    archive = root / receipt['archive']
    for name, raw in before.items():
        path = archive / 'originals' / name if name in receipt['retired_paths'] else root / name
        assert path.read_bytes() == raw
    assert receipt_path.exists()
    assert (archive / 'snapshots' / receipt_path.relative_to(root)).read_bytes() == receipt_path.read_bytes()
    assert (root / '.git/index').read_bytes() == index
    assert resume.git(root, 'rev-parse', 'HEAD') == head
    assert not resume.frozen(root)


@pytest.mark.parametrize('damage', ['pending', 'evidence', 'status', 'missing_pin'])
def test_committed_retirement_interruption_rejects_changed_evidence(retiring, monkeypatch, damage):
    root, session = retiring
    receipt_path = _commit_completed_work(root, session)
    rename = resume.os.rename
    def interrupt(source, destination):
        if str(source).endswith('latest.json'):
            raise OSError('interrupted committed retirement')
        return rename(source, destination)
    monkeypatch.setattr(resume.os, 'rename', interrupt)
    with pytest.raises(OSError, match='interrupted committed retirement'):
        _retire(root, session)
    monkeypatch.setattr(resume.os, 'rename', rename)
    if damage == 'pending':
        (root / '.omc/state/pending-completion.json').write_text('{}')
    elif damage == 'evidence':
        receipt_path.write_text('{}')
    else:
        marker = root / resume.RETIREMENT_PENDING
        descriptor = json.loads(marker.read_text())
        manifest_path = root / descriptor['archive'] / 'manifest.json'
        manifest = json.loads(manifest_path.read_text())
        if damage == 'status':
            manifest['previous_work_status'] = 'UNKNOWN'
        else:
            manifest['files'].pop(str(receipt_path.relative_to(root)))
        manifest_path.write_text(json.dumps(manifest))
        descriptor['manifest_sha256'] = resume.digest(manifest_path.read_bytes())
        marker.write_text(json.dumps(descriptor))
    before = _state_bytes(root)
    with pytest.raises(ValueError, match='retirement_(evidence_changed|manifest_invalid)'):
        _retire(root, session)
    assert _state_bytes(root) == before


@pytest.mark.parametrize('point', ['completion', 'fsync'])
def test_committed_retirement_interruption_resumes(retiring, monkeypatch, point):
    root, session = retiring
    _commit_completed_work(root, session)
    real = resume.write_new
    real_fsync = resume.fsync_file
    def interrupt_fsync(path):
        if '/originals/' in str(path):
            raise OSError('interrupted completion publication')
        return real_fsync(path)
    def interrupt(path, value):
        if point == 'completion' and str(path).endswith('completed.json'):
            raise OSError('interrupted completion publication')
        return real(path, value)
    monkeypatch.setattr(resume, 'write_new', interrupt)
    if point == 'fsync':
        monkeypatch.setattr(resume, 'fsync_file', interrupt_fsync)
    with pytest.raises(OSError, match='interrupted completion publication'):
        _retire(root, session)
    assert resume.frozen(root)
    monkeypatch.setattr(resume, 'write_new', real)
    monkeypatch.setattr(resume, 'fsync_file', real_fsync)
    result = _retire(root, session)
    assert result['previous_work_status'] == 'EXPIRED_COMMITTED'
    assert not resume.frozen(root)
