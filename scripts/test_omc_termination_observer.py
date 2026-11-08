"""File registration regressions; one real consumer, synthetic faults are separate."""
import json
from pathlib import Path
import subprocess
import sys

import pytest

# The standard pytest collector loads the synthetic hook; no external -p required.
pytest_plugins = ['omc_termination_observer']

ROOT=Path(__file__).resolve().parents[1]
SCRIPT=ROOT/'scripts/omc_termination_observer.py'
BASIS=ROOT/'.omc/file-link/abi-basis.json'


def public(tmp_path, exercise=None):
    out=tmp_path/'out'
    cmd=[sys.executable,'-B',str(SCRIPT),'--output',str(out)]
    cmd+=['--exercise',exercise] if exercise else ['--file-register','--abi-basis',str(BASIS)]
    p=subprocess.run(cmd,cwd=ROOT,capture_output=True,text=True,timeout=15)
    assert (out/'result.json').is_file(),(p.returncode,p.stdout,p.stderr)
    return p,json.loads((out/'result.json').read_text()),out


def test_public_help_declares_file_registration():
    # Existing valid CLI invocation succeeds; missing capability is an assertion RED.
    p=subprocess.run([sys.executable,str(SCRIPT),'--help'],capture_output=True,text=True,timeout=5)
    assert p.returncode==0,p.stderr
    assert '--file-register' in p.stdout, 'valid CLI does not expose the required file-registration entry'


def test_one_real_consumer_registers_and_is_reaped(tmp_path):
    p,d,out=public(tmp_path)
    assert p.returncode==0,(p.stdout,p.stderr,d)
    assert d['status']=='registered' and d['consumer_count']==1
    creator=json.loads((out/'creator-result.json').read_text())
    assert creator['identity']==d['manager_identity']
    assert creator['identity']['ppid']==d['creator_pid']
    assert creator['identity']['pid']==creator['identity']['pgid']==d['consumer_pid']
    assert creator['registration_elapsed']<=1
    assert creator['status']=='registered' and creator['returncode']==0 and creator['reaped']
    assert creator['identity']['start_sec']>0 and 0<=creator['identity']['start_usec']<1000000
    workers=d['io_workers']+creator['io_workers']
    assert workers and all(x['reaped'] and x['returncode']==0 for x in workers)
    assert d['descendant_group_termination']=='UNVERIFIED' and d['comparisons']=='NOT_RUN'


@pytest.mark.parametrize('case,reason',[('mismatch','identity_mismatch'),('query-error','query_unconfirmed')])
def test_public_invalid_identity_never_registers(tmp_path,case,reason):
    p,d,_=public(tmp_path,case)
    assert p.returncode==1 and d['status']=='failed'
    assert d['reason']==reason and d['consumer_count']==0
    assert d['next_execution']=='NOT_RUN' and d['ownership']=='UNKNOWN'
    if case=='query-error':assert d['query_error']['errno']==13


def test_public_late_publication_cannot_revive_terminal_id(tmp_path):
    p,d,out=public(tmp_path,'late')
    assert p.returncode==1 and d['status']=='failed'
    assert d['reason']=='registration_deadline' and d['frozen']
    assert d['late_publication_observed'] and d['late_publication_ignored']
    assert d['registration_elapsed']<.3 and d['next_execution']=='NOT_RUN'
    assert (out/'late-request.json').is_file()
    assert all(x['reaped'] for x in d['io_workers'])


def test_public_stalled_io_has_bounded_owner_cleanup(tmp_path):
    p,d,_=public(tmp_path,'stall')
    assert p.returncode==1 and d['status']=='failed' and d['frozen']
    assert d['registration_elapsed']<.3 and d['cleanup_elapsed']<20
    assert d['io_workers'] and all(x['reaped'] for x in d['io_workers'])
    assert d['io_termination']=='observed' and d['next_execution']=='NOT_RUN'


def _load_observer():
    import importlib.util
    spec=importlib.util.spec_from_file_location('file_registration_review_fix',SCRIPT)
    module=importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_job_poll_drains_json_written_between_read_and_exit():
    from types import SimpleNamespace
    m=_load_observer();state={'exited':False,'delivered':False}
    def read(fd,size):
        if fd==22:return b''
        if not state['exited']:raise BlockingIOError()
        if not state['delivered']:
            state['delivered']=True;return b'{"published": true}\n'
        return b''
    def poll():state['exited']=True;return 0
    m.os=SimpleNamespace(read=read)
    job=object.__new__(m.Job)
    job.child=SimpleNamespace(stdout=SimpleNamespace(fileno=lambda:11),stderr=SimpleNamespace(fileno=lambda:22),poll=poll)
    job.buffer=b'';job.errors=b'';job.answer=None
    assert job.poll()=={'published':True}
    assert state['delivered']


def _actual_manager_failure(tmp_path, *, late=False):
    """Inject at dependencies, then execute real identity/manager/error handling.

    No native API or consumer is created by this deterministic error regression.
    The existing normal public CLI test still uses one real consumer.
    """
    import ctypes
    import os
    import time
    from types import SimpleNamespace
    m=_load_observer();context={'jobs':[],'native_calls':0}
    row={'pid':1234,'ppid':4321,'pgid':1234,'start_sec':100,'start_usec':20}
    def native(pid,flavor,arg,buffer,size):
        context['native_calls']+=1
        if not late:ctypes.set_errno(13);return 0
        value=ctypes.cast(buffer,ctypes.POINTER(m.BSDInfo)).contents
        value.pbi_pid=row['pid'];value.pbi_ppid=row['ppid'];value.pbi_pgid=row['pgid']
        value.pbi_start_tvsec=row['start_sec'];value.pbi_start_tvusec=row['start_usec']
        return ctypes.sizeof(value)
    m.load_abi=lambda path:(None,native)
    m.os=SimpleNamespace(**vars(os));m.os.getpgid=lambda pid:row['pgid']
    def popen(argv,**kwargs):
        context['run_id']=argv[argv.index('--run-id')+1]
        context['creation_id']=argv[argv.index('--creation-id')+1]
        return SimpleNamespace(pid=4321,wait=lambda timeout:0)
    m.subprocess=SimpleNamespace(Popen=popen,TimeoutExpired=subprocess.TimeoutExpired)
    class Job:
        def __init__(self,kind,path,deadline,payload=None,delay=0):self.kind=kind;context['jobs'].append(kind)
        def poll(self):
            t0=time.monotonic()-(2 if late else 0)
            return {'value':{'run_id':context['run_id'],'creation_id':context['creation_id'],'identity':row,'t0':t0,'deadline':t0+1}}
        def reap(self,deadline):return {'reaped':True,'returncode':0,'synthetic_dependency':True}
    m.Job=Job
    out=tmp_path/'manager'
    code=m.file_register(SimpleNamespace(output=str(out),abi_basis='injected ABI dependency'))
    return code,json.loads((out/'result.json').read_text()),context,out


def test_actual_manager_query_error_preserves_unknown_count(tmp_path):
    code,d,context,_=_actual_manager_failure(tmp_path)
    assert code==1 and context['native_calls']==1
    assert d['error']['errno']==13 and d['ownership']=='UNKNOWN'
    assert d['consumer_count'] is None, 'ownership query failure cannot prove zero created consumers'
    assert d['verified_consumer_count']==0 and d['frozen']
    assert context['jobs']==['read'] and d['next_execution']=='NOT_RUN'


def test_actual_manager_expired_request_cannot_publish_ack_or_revive(tmp_path):
    code,d,context,out=_actual_manager_failure(tmp_path,late=True)
    assert code==1 and context['native_calls']==1
    assert d['error']['type']=='TimeoutError' and d['frozen']
    assert context['jobs']==['read'] and not (out/'ack.json').exists()
    assert d['consumer_count'] is None and d['verified_consumer_count']==0
    before=(out/'result.json').read_bytes()
    (out/'ack.json').write_text('{"late":true}')
    assert (out/'result.json').read_bytes()==before
    assert json.loads(before)['status']=='failed' and d['next_execution']=='NOT_RUN'


@pytest.fixture
def synthetic_trace_case(request):
    """The outer pytest call is the only surface; no nested pytest/process."""
    from types import SimpleNamespace
    import omc_termination_observer as observer
    state={'events':[],'calls':[],'sink_fails':False,'answer':object(),'error':None}
    def original(*args,**kwargs):
        state['calls'].append((args,kwargs))
        if state['error'] is not None:raise state['error']
        return state['answer']
    namespace=SimpleNamespace(**{name:original for name in ('supervise','_stop_group','_wait_group_exit','_group_exists')})
    def sink(event):
        if state['sink_fails']:raise OSError(28,'injected record failure')
        state['events'].append(event)
    originals={name:getattr(namespace,name) for name in vars(namespace)}
    request.node._omc_synthetic_trace={'namespace':namespace,'sink':sink,'clock':lambda:10.,'state':state,'worker':'synthetic-worker'}
    yield observer,namespace,state,originals
    if 'binding' in state:
        assert all(getattr(namespace,name) is value for name,value in originals.items())
        assert not hasattr(namespace,'_omc_trace_owner')
        assert not state['binding'].active


@pytest.mark.parametrize('name',['supervise','_stop_group','_wait_group_exit','_group_exists'])
def test_synthetic_trace_surface_returns_same_object_once(synthetic_trace_case,name):
    m,ns,state,originals=synthetic_trace_case
    assert hasattr(m,'TraceBinding'),'synthetic pytest installation capability missing'
    value=object();assert getattr(ns,name) is not originals[name]
    assert getattr(ns,name)(value,flag=value) is state['answer']
    assert state['calls']==[((value,),{'flag':value})]
    assert [e['phase'] for e in state['events']]==['enter','return']
    assert all(e['worker']=='synthetic-worker' and e['synthetic'] for e in state['events'])


def test_synthetic_trace_surface_exception_identity_and_errno(synthetic_trace_case):
    m,ns,state,_=synthetic_trace_case
    assert hasattr(m,'TraceBinding'),'synthetic pytest installation capability missing'
    error=PermissionError(13,'injected');state['error']=error
    with pytest.raises(PermissionError) as caught:ns.supervise('input')
    assert caught.value is error and len(state['calls'])==1
    assert state['events'][-1]['phase']=='raise' and state['events'][-1]['errno']==13


def test_synthetic_trace_surface_record_failure_is_separate(synthetic_trace_case):
    m,ns,state,_=synthetic_trace_case
    assert hasattr(m,'TraceBinding'),'synthetic pytest installation capability missing'
    state['sink_fails']=True
    assert ns.supervise() is state['answer']
    assert len(state['calls'])==1 and len(state['binding'].record_errors)==2
    assert state['binding'].record_errors[0]['errno']==28


@pytest.mark.xfail(raises=RuntimeError,strict=True,reason='intentional test body exception; fixture teardown checks hook finally restoration')
def test_synthetic_trace_surface_restores_after_body_exception(synthetic_trace_case):
    m,ns,state,_=synthetic_trace_case
    assert hasattr(m,'TraceBinding'),'synthetic pytest installation capability missing'
    assert state['binding'].active
    raise RuntimeError('intentional body failure')


def test_synthetic_trace_surface_duplicate_install_rejected(synthetic_trace_case):
    m,ns,state,_=synthetic_trace_case
    assert hasattr(m,'TraceBinding'),'synthetic pytest installation capability missing'
    with pytest.raises(ValueError,match='already_installed'):state['binding'].install()
    other=m.TraceBinding(ns,state['events'].append,lambda:10.,'other')
    with pytest.raises(ValueError,match='already_installed'):other.install()
    assert ns.supervise() is state['answer'] and len(state['calls'])==1


def test_synthetic_trace_worker_missing_is_unknown():
    import omc_termination_observer as m
    assert hasattr(m,'summarize_synthetic_workers'),'worker trace completeness capability missing'
    value=m.summarize_synthetic_workers(['a','b'],{'a':{'events':2,'record_errors':[]}})
    assert value['status']=='UNKNOWN' and value['workers']['b'] is None and value['missing']==['b']


def test_synthetic_trace_fake_handles_share_deadline_and_identity():
    import omc_termination_observer as m
    assert hasattr(m,'inspect_fake_cleanup'),'fake cleanup contract capability missing'
    rows=[{'pid':11,'start_id':1,'expected_start_id':1,'owned':True,'reaped':True,'returncode':0},
          {'pid':12,'start_id':2,'expected_start_id':3,'owned':True,'reaped':False,'returncode':None}]
    value=m.inspect_fake_cleanup(rows,now=105.,cleanup_start=100.,overall_deadline=115.)
    assert value['shared_deadline']==115 and value['status']=='UNKNOWN'
    assert value['handles'][0]['status']=='REAPED' and value['handles'][1]['status']=='UNKNOWN'
    assert all(h['deadline']<=115 for h in value['handles'])
    assert value['actual_termination']=='NOT_RUN' and value['test_children_created']==0


def test_synthetic_trace_fake_handles_expiry_and_takeover():
    import omc_termination_observer as m
    assert hasattr(m,'inspect_fake_cleanup'),'fake cleanup contract capability missing'
    row={'pid':1,'start_id':1,'expected_start_id':1,'owned':True,'reaped':False,'returncode':None}
    value=m.inspect_fake_cleanup([row],now=131.,cleanup_start=0.,overall_deadline=1800.,manager_alive=False)
    assert value['shared_deadline']==120 and value['handles'][0]['status']=='UNKNOWN'
    assert value['cleanup_owner']=='fake-independent-monitor' and value['deadline_reached']
    assert value['actual_monitor']=='NOT_RUN' and value['actual_termination']=='NOT_RUN'


def test_synthetic_trace_standard_collection_registers_plugin(synthetic_trace_case,request):
    """Plain pytest collection must register the hook without a -p CLI flag."""
    m,ns,state,originals=synthetic_trace_case
    assert request.config.pluginmanager.hasplugin('omc_termination_observer'), 'standard pytest collection must register the tracing plugin'
    assert ns.supervise is not originals['supervise'] and state['binding'].active
    assert ns.supervise() is state['answer'] and len(state['calls'])==1


def _exclusive_audit_api():
    import omc_termination_observer as m
    assert hasattr(m,'AuditRun') and hasattr(m,'run_audit_entry'), 'exclusive audit runner contract is missing'
    return m


def test_exclusive_audit_entry_creates_distinct_runs_and_shared_phase_paths(tmp_path):
    m=_exclusive_audit_api();calls=[]
    first=m.AuditRun(tmp_path);second=m.AuditRun(tmp_path)
    assert first.path!=second.path
    for phase in ['baseline','red','green']:
        result=first.run_phase(phase,lambda:calls.append(phase) or 0)
        assert result['status']=='complete' and result['audit_status']=='saved'
        path=Path(result['audit_path']);assert path.parent==first.path
        assert json.loads(path.read_bytes())['phase']==phase
    assert calls==['baseline','red','green']


def test_exclusive_audit_entry_run_collision_blocks_trial(tmp_path):
    m=_exclusive_audit_api();calls=[];rid='a'*32
    m.AuditRun(tmp_path,run_id=rid)
    with pytest.raises(FileExistsError):m.run_audit_entry(tmp_path,'green',lambda:calls.append(1),run_id=rid)
    assert calls==[] and len(list(tmp_path.iterdir()))==1


def test_exclusive_audit_entry_existing_file_blocks_trial_and_keeps_bytes(tmp_path):
    m=_exclusive_audit_api();run=m.AuditRun(tmp_path);p=run.path/'green-audit.json';p.write_bytes(b'prior')
    calls=[]
    with pytest.raises(FileExistsError):run.run_phase('green',lambda:calls.append(1))
    assert calls==[] and p.read_bytes()==b'prior'


@pytest.mark.parametrize('stage',['write','flush','fsync','close'])
def test_exclusive_audit_entry_storage_failure_is_incomplete(tmp_path,monkeypatch,stage):
    import os
    from types import SimpleNamespace
    m=_exclusive_audit_api();calls=[];real_open=m._open_reserved_audit;handles=[]
    class Failing:
        def __init__(self,raw):self.raw=raw
        def write(self,data):
            if stage=='write':self.raw.write(data[:4]);raise OSError(28,'injected write')
            return self.raw.write(data)
        def flush(self):
            if stage=='flush':raise OSError(28,'injected flush')
            return self.raw.flush()
        def fileno(self):return self.raw.fileno()
        def close(self):
            self.raw.close()
            if stage=='close':raise OSError(28,'injected close')
    def opening(path):
        f=real_open(path);handles.append(f);return Failing(f)
    monkeypatch.setattr(m,'_open_reserved_audit',opening)
    if stage=='fsync':
        def fail_sync(fd):raise OSError(28,'injected fsync')
        monkeypatch.setattr(m,'os',SimpleNamespace(**{**vars(os),'fsync':fail_sync}))
    result=m.run_audit_entry(tmp_path,'green',lambda:calls.append(1) or 0)
    assert result['status']=='incomplete' and result['audit_status']=='failed'
    assert result['trial_status']=='passed' and calls==[1]
    assert result['storage_error']['errno']==28 and all(f.closed for f in handles)
    p=Path(result['audit_path']);before=p.read_bytes();calls.clear()
    with pytest.raises(FileExistsError):m.AuditRun(tmp_path,run_id=result['run_id'])
    assert p.read_bytes()==before and calls==[]


def test_exclusive_audit_entry_open_failure_blocks_trial(tmp_path,monkeypatch):
    m=_exclusive_audit_api();calls=[]
    def failed_open(path):raise PermissionError(13,'injected open')
    monkeypatch.setattr(m,'_open_reserved_audit',failed_open)
    with pytest.raises(PermissionError):m.run_audit_entry(tmp_path,'green',lambda:calls.append(1))
    assert calls==[]


def test_exclusive_audit_entry_trial_error_does_not_retry(tmp_path):
    m=_exclusive_audit_api();calls=[]
    def trial():calls.append(1);raise PermissionError(13,'injected trial')
    result=m.run_audit_entry(tmp_path,'red',trial)
    assert calls==[1] and result['status']=='failed' and result['audit_status']=='saved'
    assert result['trial_error']['errno']==13


def test_exclusive_audit_entry_invalid_path_components_block_trial(tmp_path):
    m=_exclusive_audit_api();calls=[]
    with pytest.raises(ValueError):m.run_audit_entry(tmp_path,'green',lambda:calls.append(1),run_id='../old')
    run=m.AuditRun(tmp_path)
    with pytest.raises(ValueError):run.run_phase('../old',lambda:calls.append(1))
    assert calls==[]
