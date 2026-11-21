"""Codex command and installed skill contracts."""
from pathlib import Path

import pytest
from test_omc_output_contract import detached_case
ROOT = Path(__file__).resolve().parents[1]
TEMPLATES = ROOT / 'templates'

def test_deployed_codex_command_reference_links_to_guides(installed_omc_target):
    relative = Path('.codex/commands/omc-commands.md')
    installed = installed_omc_target / relative
    assert installed.read_bytes() == (TEMPLATES / relative).read_bytes() == (ROOT / relative).read_bytes()

    text = installed.read_text(encoding='utf-8')
    for guide in ('omc_quickstart.md', 'omc_explicit_workflow.md'):
        link = f'../../docs/{guide}'
        assert f']({link})' in text
        assert (installed.parent / link).is_file()
    assert '일반 작업은 Superpowers' in text
    assert 'OMC 작업 흐름을 명시적으로 선택' in text
    assert '암묵적 트리거' not in text


def test_deployed_codex_plan_matches_template(installed_omc_target):
    relative = '.agents/skills/omc-plan/SKILL.md'
    assert (installed_omc_target / relative).read_bytes() == (TEMPLATES / relative).read_bytes(), 'deployed Codex plan differs from template'


def test_deployed_autopilot_skill_documents_pipeline_commands(installed_omc_target):
    relative = Path('.agents/skills/omc-autopilot/SKILL.md')
    installed = installed_omc_target / relative
    assert installed.read_bytes() == (TEMPLATES / relative).read_bytes()
    text = installed.read_text(encoding='utf-8')
    assert 'scripts/omc_autopilot.py pipeline ' in text
    assert 'scripts/omc_autopilot.py pipeline-status' in text


@pytest.mark.parametrize('verdict',['APPROVE WITH NOTES','REVISE'])
def test_detached_autopilot_keeps_raw_routing_and_shows_only_korean(detached_case,monkeypatch,capsys,verdict):
    import omc_autopilot as a,omc_output_contract as m,hashlib,json
    root,file,pin,ctx,raw=detached_case
    if verdict=='REVISE':raw='판정: REVISE\n'+m.render_envelope(stage='review',verdict='REVISE',risk='medium',next_skill='omc-task',user_selection_needed=False,reason_code='fixture')
    ref=m.publish_record(root,file,pin,raw)
    display=m.read_record(root,ref['path'],ref['sha256'],ctx)['display']
    execution=root/'.omc/execution.json';execution.write_text(json.dumps({'schema_version':'omc-output-execution/v1','output_mode':'detached_v1',
        'entries':[{'step_name':'review','context':str(file),'context_sha256':pin}]}))
    class Returned:
        returncode=0
        def communicate(self):return display.encode('utf-8'),b''
    calls=[]
    real_spawn=a.subprocess.Popen
    def spawn(*args,**kwargs):
        if any('omc_exec.py' in str(v) for v in args[0]):
            calls.append(args);return Returned()
        return real_spawn(*args,**kwargs)
    monkeypatch.setattr(a.subprocess,'Popen',spawn)
    def run():
        code,output=a._run_pipeline_step(root,'review','review synthetic', 'codex',30)
        assert code==0 and a._grep_verdict(output)==verdict
        assert 'OMC_OUTPUT:' in output
        print(output)  # internal diagnostics must not be displayed directly
        return code
    code=a.detached_pipeline_cli(root,execution,hashlib.sha256(execution.read_bytes()).hexdigest(),run)
    visible=capsys.readouterr()
    assert code==0 and len(calls)==1 and visible.out==display
    assert 'OMC_OUTPUT:' not in visible.out and 'VERDICT:' not in visible.out
    assert any('OMC_OUTPUT:' in p.read_text() for p in (root/'.omc/state/output-records/s/r-diagnostics').glob('stdout.txt'))


def test_detached_autopilot_capability_or_missing_entry_blocks_before_provider(detached_case,monkeypatch):
    import omc_autopilot as a,omc_output_contract as m,hashlib,json,pytest
    root,file,pin,ctx,raw=detached_case
    execution=root/'.omc/execution.json';execution.write_text(json.dumps({'schema_version':'omc-output-execution/v1','output_mode':'detached_v1',
        'entries':[{'step_name':'review','context':str(file),'context_sha256':pin}]}))
    monkeypatch.setattr(m,'capabilities',lambda:{'output_modes':['legacy_inline']})
    calls=[]
    with pytest.raises(m.OutputContractError):a.detached_pipeline_cli(root,execution,hashlib.sha256(execution.read_bytes()).hexdigest(),lambda:calls.append('provider'))
    assert not calls


def test_detached_old_consumer_blocks_before_provider(detached_case,monkeypatch):
    import omc_autopilot as a,omc_workflow_validation as v,omc_output_contract as m,hashlib,json,pytest
    root,file,pin,ctx,raw=detached_case
    execution=root/'.omc/execution.json';execution.write_text(json.dumps({'schema_version':'omc-output-execution/v1','output_mode':'detached_v1',
        'entries':[{'step_name':'review','context':str(file),'context_sha256':pin}]}))
    monkeypatch.setattr(v,'output_capabilities',lambda:{'output_modes':['legacy_inline']})
    calls=[]
    with pytest.raises(m.OutputContractError):a.detached_pipeline_cli(root,execution,hashlib.sha256(execution.read_bytes()).hexdigest(),lambda:calls.append('provider'))
    assert not calls


def test_detached_public_pipeline_rejects_unpinned_execution_before_work(detached_case):
    import subprocess,sys,json
    root,file,pin,ctx,raw=detached_case
    execution=root/'.omc/execution.json';execution.write_text('{}')
    r=subprocess.run([sys.executable,str(ROOT/'scripts/omc_autopilot.py'),'--target',str(root),'pipeline',
        '--instruction','synthetic no provider','--dry-run','--output-execution',str(execution),'--output-execution-sha256','0'*64],
        capture_output=True,text=True,timeout=30)
    assert r.returncode==2 and not r.stdout
    assert '기록 전달을 확인하지 못했습니다' in r.stderr
    assert not (root/'.omc/pipeline_run_result.json').exists()


def test_detached_task_adapter_rejects_a_model_only_completion_claim(detached_case):
    import omc_autopilot as a,omc_output_contract as m,hashlib,json,pytest
    root,file,pin,ctx,raw=detached_case;ctx['stage']='task';file.write_text(json.dumps(ctx));pin=hashlib.sha256(file.read_bytes()).hexdigest()
    raw='필수 검증 통과·작업 완료\n'+m.render_envelope(stage='task',verdict='PROCEED',risk='low',next_skill='omc-review',user_selection_needed=False)
    ref=m.publish_record(root,file,pin,raw);doc=m.read_record(root,ref['path'],ref['sha256'],ctx)
    with pytest.raises(m.OutputContractError,match='task_requires_completion_gate'):
        a.consume_detached_step(root,{'context':str(file),'context_sha256':pin},ctx,doc['display'],native_exit=0)


@pytest.mark.parametrize('kind',['success','failed','mismatch'])
def test_review_fix_provider_raw_streams_and_exit_are_preserved(detached_case,monkeypatch,capsys,kind):
    import omc_autopilot as a,omc_output_contract as m,hashlib,json
    root,file,pin,ctx,raw=detached_case
    ref=m.publish_record(root,file,pin,raw);display=m.read_record(root,ref['path'],ref['sha256'],ctx)['display']
    execution=root/'.omc/execution.json';execution.write_text(json.dumps({'schema_version':'omc-output-execution/v1','output_mode':'detached_v1',
        'entries':[{'step_name':'review','context':str(file),'context_sha256':pin}]}))
    stdout=(display if kind!='mismatch' else 'unmatched stdout\r\n').encode()
    stderr=b' leading diagnostic\r\ntrailing diagnostic \n'
    code=7 if kind=='failed' else 0
    real_spawn=a.subprocess.Popen
    class Returned:
        returncode=code
        def __init__(self,text):self.text=text
        def communicate(self):return (stdout.decode(),stderr.decode()) if self.text else (stdout,stderr)
    def spawn(*args,**kwargs):
        if any('omc_exec.py' in str(v) for v in args[0]):return Returned(kwargs.get('text',False))
        return real_spawn(*args,**kwargs)
    monkeypatch.setattr(a.subprocess,'Popen',spawn)
    observed={}
    def run():
        observed['return']=a._run_pipeline_step(root,'review','synthetic only','codex',30)
        return observed['return'][0]
    result=a.detached_pipeline_cli(root,execution,hashlib.sha256(execution.read_bytes()).hexdigest(),run)
    records=root/'.omc/state/output-provider-records/s/r'
    assert (records/'stdout.bin').is_file(),'provider stdout raw was not archived'
    assert (records/'stdout.bin').read_bytes()==stdout
    assert (records/'stderr.bin').read_bytes()==stderr
    receipt=json.loads((records/'result.json').read_bytes())
    assert receipt['return_code']==code and receipt['capture_complete'] is True
    assert receipt['streams']['stdout']['sha256']==hashlib.sha256(stdout).hexdigest()
    assert receipt['streams']['stderr']['sha256']==hashlib.sha256(stderr).hexdigest()
    shown=capsys.readouterr()
    if kind=='success':assert result==0 and shown.out==display
    else:assert result==2 and not shown.out and '진행 보류' in shown.err
    assert json.loads(m.record_path(root,ctx).read_bytes())['machine_payload']['outcome']=='approved'


@pytest.mark.parametrize('failure',['stderr_write','receipt_write','timeout','binary_stdout','spawn_error'])
def test_review_fix_raw_failure_boundaries_cannot_display_success(detached_case,monkeypatch,capsys,failure):
    import omc_autopilot as a,omc_output_contract as m,hashlib,json
    root,file,pin,ctx,raw=detached_case
    ref=m.publish_record(root,file,pin,raw);manifest=m.record_path(root,ctx);sealed_before=manifest.read_bytes()
    display=m.read_record(root,ref['path'],ref['sha256'],ctx)['display']
    execution=root/'.omc/execution.json';execution.write_text(json.dumps({'schema_version':'omc-output-execution/v1','output_mode':'detached_v1',
        'entries':[{'step_name':'review','context':str(file),'context_sha256':pin}]}))
    out=b'\xffinvalid UTF8\r\n' if failure=='binary_stdout' else display.encode()
    err=b'raw stderr\r\nwith byte \xff\n'
    class Returned:
        returncode=-9 if failure=='timeout' else 0
        pid=987654321
        def communicate(self):return out,err
    real_spawn=a.subprocess.Popen
    def spawn(*args,**kwargs):
        if any('omc_exec.py' in str(v) for v in args[0]):
            if failure=='spawn_error':raise OSError('synthetic spawn denied')
            return Returned()
        return real_spawn(*args,**kwargs)
    monkeypatch.setattr(a.subprocess,'Popen',spawn)
    if failure=='timeout':
        class Timer:
            def __init__(self,seconds,fn):self.fn=fn
            def start(self):self.fn()
            def cancel(self):pass
        monkeypatch.setattr(a.threading,'Timer',Timer)
        monkeypatch.setattr(a.os,'getpgid',lambda pid:987654321)
        monkeypatch.setattr(a.os,'killpg',lambda pgid,sig:None)
    if failure in ('stderr_write','receipt_write'):
        write=m._write_record_file
        def fail(path,data):
            if 'output-provider-records' in path.parts and path.name==('stderr.bin' if failure=='stderr_write' else 'result.tmp'):
                raise OSError('synthetic archive full')
            return write(path,data)
        monkeypatch.setattr(m,'_write_record_file',fail)
    result=a.detached_pipeline_cli(root,execution,hashlib.sha256(execution.read_bytes()).hexdigest(),
        lambda:a._run_pipeline_step(root,'review','synthetic','codex',30)[0])
    shown=capsys.readouterr()
    assert result==2 and not shown.out and '진행 보류' in shown.err
    directory=root/'.omc/state/output-provider-records/s/r'
    if failure=='spawn_error':
        assert not (directory/'stdout.bin').exists()
        errors=json.loads((root/'.omc/state/output-records/s/r-diagnostics/errors.json').read_bytes())
        assert errors[0]['message']=='synthetic spawn denied'
        assert manifest.read_bytes()==sealed_before
        return
    assert (directory/'stdout.bin').read_bytes()==out
    if failure in ('stderr_write','receipt_write'):
        assert not (directory/'result.json').exists()
        error=json.loads((directory/'error.json').read_bytes())
        assert error['message']=='synthetic archive full' and error['provider_return_code']==0
    else:
        receipt=json.loads((directory/'result.json').read_bytes())
        assert receipt['timed_out'] is (failure=='timeout')
        assert receipt['return_code']==(-9 if failure=='timeout' else 0)
        assert (directory/'stderr.bin').read_bytes()==err
    assert manifest.read_bytes()==sealed_before


def test_review_fix_deadline_after_raw_capture_blocks_before_pipeline_advance(detached_case,monkeypatch,capsys):
    import omc_autopilot as a,omc_output_contract as m,hashlib,json
    root,file,pin,ctx,raw=detached_case
    ref=m.publish_record(root,file,pin,raw);display=m.read_record(root,ref['path'],ref['sha256'],ctx)['display']
    execution=root/'.omc/execution.json';execution.write_text(json.dumps({'schema_version':'omc-output-execution/v1','output_mode':'detached_v1',
        'entries':[{'step_name':'review','context':str(file),'context_sha256':pin}]}))
    real_spawn=a.subprocess.Popen
    class Returned:
        returncode=0
        def communicate(self):return display.encode(),b'diagnostic\n'
    def spawn(*args,**kwargs):
        if any('omc_exec.py' in str(v) for v in args[0]):return Returned()
        return real_spawn(*args,**kwargs)
    monkeypatch.setattr(a.subprocess,'Popen',spawn)
    finish=a._finish_provider_capture
    def late(*args,**kwargs):
        ref=finish(*args,**kwargs);monkeypatch.setattr(m.time,'time',lambda:ctx['deadline_epoch']+1);return ref
    monkeypatch.setattr(a,'_finish_provider_capture',late)
    observed={}
    def run():
        observed['code'],_=a._run_pipeline_step(root,'review','synthetic','codex',30)
        observed['would_advance']=observed['code']==0
        return observed['code']
    result=a.detached_pipeline_cli(root,execution,hashlib.sha256(execution.read_bytes()).hexdigest(),run)
    assert result==2 and observed['code']==2 and not observed['would_advance']
    assert not capsys.readouterr().out
