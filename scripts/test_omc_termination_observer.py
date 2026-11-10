"""File registration regressions; one real consumer, synthetic faults are separate."""
import json
from pathlib import Path
import subprocess
import sys

import pytest

# The standard pytest collector loads the synthetic hook; no external -p required.
pytest_plugins = ['omc_termination_observer', 'pytester']

ROOT=Path(__file__).resolve().parents[1]
SCRIPT=ROOT/'scripts/omc_termination_observer.py'



def build_abi_basis(folder):
    """One bounded compiler preparation; a consumer starts only after publication."""
    import ctypes
    import hashlib
    import math
    import os
    import platform
    import selectors
    import shlex
    import signal
    import time
    folder=Path(folder).resolve();folder.mkdir(parents=True,exist_ok=True)
    t0=time.monotonic();work_end=t0+20;cleanup_end=t0+25;publish_end=t0+30
    calls=[];output_bytes=0;cleanup_remaining=5.0;compiler=Path('/usr/bin/clang').resolve(strict=True)
    def pin(path):
        if time.monotonic()>=work_end:raise TimeoutError('ABI work deadline')
        return hashlib.sha256(Path(path).read_bytes()).hexdigest()
    compiler_pin=pin(compiler)
    def cleanup_owned(child,row):
        nonlocal cleanup_remaining
        row.update(owned_pid=child.pid,owned_pgid=child.pid)
        started=time.monotonic();group_absent=False;observed=False
        end=min(cleanup_end,started+cleanup_remaining)
        def remaining():
            now=time.monotonic()
            if not math.isfinite(now):raise RuntimeError('ABI cleanup clock unconfirmed')
            return end-now
        def complete():
            nonlocal group_absent
            reaped=child.poll() is not None
            if not group_absent:
                try:os.killpg(child.pid,0)
                except ProcessLookupError:group_absent=True
                except OSError as exc:
                    row['group_query_errno']=exc.errno # Unknown is never absence.
            return reaped and group_absent
        try:
            if remaining()>0:observed=complete()
            for sig,seconds in ((signal.SIGTERM,2.0),(signal.SIGKILL,5.0)):
                if observed or remaining()<=0:break
                if not group_absent:
                    try:os.killpg(child.pid,sig)
                    except ProcessLookupError:group_absent=True
                    except OSError as exc:
                        row.setdefault('signal_errors',[]).append({'target':'group','signal':int(sig),'errno':exc.errno})
                # Retained Popen handle also owns a live parent that changed group.
                if child.poll() is None:
                    try:child.send_signal(sig)
                    except ProcessLookupError:pass
                    except OSError as exc:
                        row.setdefault('signal_errors',[]).append({'target':'parent','signal':int(sig),'errno':exc.errno})
                phase_end=min(end,time.monotonic()+seconds)
                while remaining()>0:
                    if complete():observed=True;break
                    left=phase_end-time.monotonic()
                    if left<=0:break
                    time.sleep(min(.01,left))
            row['group_termination']='observed' if observed else 'unconfirmed'
            if not observed:raise RuntimeError('ABI compiler group termination unconfirmed')
        finally:
            finished=time.monotonic()
            elapsed=finished-started
            if not math.isfinite(elapsed) or elapsed<0:
                cleanup_remaining=0;row['cleanup_elapsed']=None
            else:
                cleanup_remaining=max(0,cleanup_remaining-elapsed);row['cleanup_elapsed']=elapsed
            row.update(end=finished,returncode=child.returncode,reaped=child.returncode is not None)

    def invoke(argv):
        nonlocal output_bytes
        if not math.isfinite(time.monotonic()) or time.monotonic()>=work_end:
            raise TimeoutError('ABI work deadline')
        row={'argv':argv,'cwd':str(folder),'start':time.monotonic(),'stdout':'','stderr':''}
        calls.append(row)
        child=subprocess.Popen(argv,cwd=folder,env={'PATH':'/usr/bin:/bin','LC_ALL':'C'},
                               start_new_session=True,stdout=subprocess.PIPE,stderr=subprocess.PIPE)
        selector=None;chunks={'stdout':bytearray(),'stderr':bytearray()}
        try:
            selector=selectors.DefaultSelector()
            for key in chunks:
                pipe=getattr(child,key);os.set_blocking(pipe.fileno(),False)
                selector.register(pipe,selectors.EVENT_READ,key)
            while selector.get_map():
                remaining=work_end-time.monotonic()
                if remaining<=0:raise TimeoutError('ABI work deadline')
                for item,_ in selector.select(min(.05,remaining)):
                    data=os.read(item.fd,4096)
                    if not data:selector.unregister(item.fileobj);continue
                    output_bytes+=len(data)
                    if output_bytes>65536:raise ValueError('ABI output limit')
                    chunks[item.data].extend(data)
            code=child.wait(timeout=max(.001,work_end-time.monotonic()))
            if code:raise ValueError('ABI compiler/discovery failed: '+str(code))
            row['returncode']=code
            return bytes(chunks['stdout'])
        finally:
            try:
                if selector is not None:selector.close()
            finally:
                try:
                    cleanup_owned(child,row)
                finally:
                    for key,value in chunks.items():row[key]=bytes(value).decode('utf-8','replace')
                    child.stdout.close();child.stderr.close()
    source=folder/'abi.c';deps=folder/'abi.d'
    try:
        if sys.platform!='darwin':raise RuntimeError('macOS ABI fixture requires current supported host')
        sdk=Path(invoke(['/usr/bin/xcrun','--show-sdk-path']).decode().strip()).resolve(strict=True)
        arch=platform.machine();m=_load_observer()
        fields=[{'name':n,'offset':getattr(m.BSDInfo,n).offset,'width':ctypes.sizeof(t)} for n,t in m._FIELDS]
        code=['#include <stddef.h>','#include <sys/proc_info.h>',
              f'_Static_assert(sizeof(struct proc_bsdinfo)=={ctypes.sizeof(m.BSDInfo)},"size");',
              f'_Static_assert(_Alignof(struct proc_bsdinfo)=={ctypes.alignment(m.BSDInfo)},"alignment");']
        for row in fields:
            n=row['name'];code += [f'_Static_assert(offsetof(struct proc_bsdinfo,{n})=={row["offset"]},"offset {n}");',
                                   f'_Static_assert(sizeof(((struct proc_bsdinfo*)0)->{n})=={row["width"]},"width {n}");']
        with source.open('x') as f:f.write('\n'.join(code)+'\n')
        source_pin=pin(source);flags=[str(compiler),'-isysroot',str(sdk),'-arch',arch]
        def closure(raw):
            paths=shlex.split(raw.decode().replace('\\\n',' ').split(':',1)[1])
            return sorted({str(Path(p).resolve(strict=True)) for p in paths if Path(p).resolve()!=source})
        headers=closure(invoke(flags+['-M',str(source)]));before={p:pin(p) for p in headers}
        invoke(flags+['-fsyntax-only','-MD','-MF',str(deps),str(source)])
        if closure(deps.read_bytes())!=headers:raise ValueError('ABI header closure changed')
        if before!={p:pin(p) for p in headers} or source_pin!=pin(source) or compiler_pin!=pin(compiler):
            raise ValueError('ABI compiler/header/source changed')
        basis={'status':'passed','architecture':arch,'structure_size':ctypes.sizeof(m.BSDInfo),
               'structure_alignment':ctypes.alignment(m.BSDInfo),'fields':fields,'headers':before,
               'compiler':str(compiler),'compiler_sha256':compiler_pin,'sdk':str(sdk),
               'source_sha256':source_pin,'calls':calls,'output_bytes':output_bytes}
        if time.monotonic()>=work_end:raise TimeoutError('ABI work deadline')
        path=folder/'abi-basis.json'
        if time.monotonic()>=publish_end:raise TimeoutError('ABI publication deadline')
        with path.open('x') as f:json.dump(basis,f);f.flush();os.fsync(f.fileno())
        if time.monotonic()>=publish_end:raise TimeoutError('ABI publication unconfirmed')
        return path
    except Exception as exc:
        if time.monotonic()<publish_end:
            with (folder/'abi-failure.json').open('x') as f:
                json.dump({'status':'failed','error':type(exc).__name__+': '+str(exc),'calls':calls},f)
        raise


@pytest.fixture(scope='module')
def abi_basis(tmp_path_factory):
    return build_abi_basis(tmp_path_factory.mktemp('native-abi'))


def public(tmp_path, exercise=None, *, abi_basis=None):
    out=tmp_path/'out'
    cmd=[sys.executable,'-B',str(SCRIPT),'--output',str(out)]
    cmd+=['--exercise',exercise] if exercise else ['--file-register','--abi-basis',str(abi_basis)]
    p=subprocess.run(cmd,cwd=ROOT,capture_output=True,text=True,timeout=15)
    assert (out/'result.json').is_file(),(p.returncode,p.stdout,p.stderr)
    return p,json.loads((out/'result.json').read_text()),out


def test_public_help_declares_file_registration():
    # Existing valid CLI invocation succeeds; missing capability is an assertion RED.
    p=subprocess.run([sys.executable,str(SCRIPT),'--help'],capture_output=True,text=True,timeout=5)
    assert p.returncode==0,p.stderr
    assert '--file-register' in p.stdout, 'valid CLI does not expose the required file-registration entry'


def test_one_real_consumer_registers_and_is_reaped(tmp_path, abi_basis):
    p,d,out=public(tmp_path, abi_basis=abi_basis)
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
    """Real pytest hook surface; nested pytest is in-process only, no OS process."""
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


def test_synthetic_trace_surface_restores_after_body_exception(pytester, monkeypatch):
    """Outer PASS requires one exact inner body failure and actual hook restoration."""
    import inspect
    import omc_termination_observer as observer_plugin
    from types import SimpleNamespace
    proof=SimpleNamespace(error=RuntimeError('intentional body failure'),state=None,namespace=None,originals=None,reports=[])
    class ProofPlugin:
        def pytest_runtest_makereport(self, item, call):
            if call.when=='call':
                proof.reports.append(call.excinfo.value if call.excinfo else None)
    # Share only this test's object; no process, signal or native dependency is invoked.
    monkeypatch.setattr(sys,'_omc_trace_proof',proof,raising=False)
    fixture=inspect.getsource(synthetic_trace_case.__wrapped__)
    inner = "import pytest,sys\n"+fixture+"\n"+"""
def test_synthetic_trace_inner_body(synthetic_trace_case):
    m,ns,state,originals=synthetic_trace_case
    proof=sys._omc_trace_proof
    assert state['binding'].active
    proof.state=state;proof.namespace=ns;proof.originals=originals
    raise proof.error
"""
    pytester.makepyfile(test_omc_termination_observer=inner)
    result=pytester.runpytest_inprocess('-q','--import-mode=importlib',plugins=[ProofPlugin(),observer_plugin])
    result.assert_outcomes(failed=1,passed=0,errors=0,skipped=0,xfailed=0,xpassed=0)
    assert proof.reports==[proof.error] and proof.reports[0] is proof.error
    assert proof.state is not None and not proof.state['binding'].active
    assert all(getattr(proof.namespace,n) is value for n,value in proof.originals.items())
    assert not hasattr(proof.namespace,'_omc_trace_owner')


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


def _synthetic_clock_contract(start=10.,work_start=0.):
    import omc_termination_observer as m
    assert hasattr(m,'SyntheticOwnershipContract'), 'approved synthetic ownership/clock contract is missing'
    clock=[start]
    contract=m.SyntheticOwnershipContract(lambda:clock[0],work_start=work_start)
    return contract,clock


def _register_synthetic_clock_contract():
    c,t=_synthetic_clock_contract();c.begin_creation();t[0]=10.1;c.step('identity_confirmed');t[0]=10.2;c.step('ack')
    assert c.snapshot()['state']=='REGISTERED'
    return c,t


@pytest.mark.parametrize('registered',[False,True])
def test_synthetic_clock_contract_monitor_failure_never_grants_real_execution(registered):
    if registered:c,t=_register_synthetic_clock_contract()
    else:c,t=_synthetic_clock_contract();c.begin_creation()
    t[0]=10.3;v=c.step('monitor_failed')
    assert v['status']=='UNKNOWN' and v['cleanup_owner'] is None
    assert v['target_reap_owner'] is None and v['monitor_reap_owner']=='manager'
    assert v['real_creation_allowed'] is False and v['actual_termination']=='NOT_RUN'
    t[0]=10.4;c.step('identity_confirmed');v=c.step('ack')
    assert v['state']!='REGISTERED' and v['status']=='UNKNOWN'
    v=c.step('reap_target',identity_matches=True,reaped=True,returncode=0)
    assert v['target_status']=='UNKNOWN'


@pytest.mark.parametrize('offset,accepted',[(.999,True),(1.,False),(1.001,False)])
def test_synthetic_clock_contract_one_second_ack_boundary(offset,accepted):
    c,t=_synthetic_clock_contract();v=c.begin_creation();assert v['t0']==10. and v['registration_deadline']==11.
    t[0]=10.05;c.step('identity_confirmed');t[0]=10.+offset;v=c.step('ack')
    assert (v['state']=='REGISTERED') is accepted
    assert v['real_creation_allowed'] is False and v['test_children_created']==0
    if not accepted:assert v['cleanup_started']==t[0] and v['reason']=='registration_deadline'


def test_synthetic_clock_contract_creation_delay_does_not_reset_t0():
    c,t=_synthetic_clock_contract();c.begin_creation()
    t[0]=10.8;c.step('identity_confirmed');t[0]=11.1;v=c.step('ack')
    assert v['t0']==10. and v['registration_deadline']==11. and v['status']=='UNKNOWN'


def test_synthetic_clock_contract_late_ack_cannot_revive_terminal_registration():
    c,t=_synthetic_clock_contract();c.begin_creation();t[0]=11.;first=c.step('tick')
    t[0]=11.2;c.step('identity_confirmed');v=c.step('ack')
    assert v['state']=='CLEANUP' and v['cleanup_started']==first['cleanup_started']
    assert v['cleanup_deadline']==first['cleanup_deadline'] and v['status']=='UNKNOWN'


@pytest.mark.parametrize('event',['identity_mismatch','identity_unconfirmed','ack'])
def test_synthetic_clock_contract_unverified_identity_blocks_registration(event):
    c,t=_synthetic_clock_contract();c.begin_creation();t[0]=10.1;v=c.step(event)
    assert v['status']=='UNKNOWN' and v['state']=='CLEANUP' and not v['real_creation_allowed']


def test_synthetic_clock_contract_manager_disconnect_assigns_monitor_only_synthetically():
    c,t=_register_synthetic_clock_contract();t[0]=10.3;v=c.step('manager_disconnected')
    assert v['cleanup_owner']=='monitor' and v['target_reap_owner']=='monitor'
    assert v['monitor_reap_owner']=='manager' and v['cleanup_deadline']==130.3
    t[0]=10.4;v=c.step('reap_target',identity_matches=True,reaped=True,returncode=0)
    assert v['target_status']=='REAPED'
    t[0]=10.5;v=c.step('reap_monitor',identity_matches=True,reaped=True,returncode=0)
    assert v['status']=='SYNTHETIC_COMPLETE' and not v['real_creation_allowed']
    assert v['actual_monitor']=='NOT_RUN' and v['actual_termination']=='NOT_RUN'


def test_synthetic_clock_contract_shared_deadline_capped_by_work_budget():
    c,t=_synthetic_clock_contract(start=1799.);c.begin_creation();t[0]=1799.1;c.step('identity_confirmed');t[0]=1799.2;c.step('ack')
    t[0]=1799.5;v=c.step('manager_disconnected')
    assert v['cleanup_deadline']==1800. and all(x<=1800 for x in v['reap_deadlines'].values())
    t[0]=1800.;v=c.step('reap_target',identity_matches=True,reaped=True,returncode=0)
    assert v['status']=='UNKNOWN' and v['state']=='TERMINAL'


def test_synthetic_clock_contract_each_reap_deadline_has_twenty_second_limit():
    c,t=_synthetic_clock_contract();c.begin_creation();t[0]=11.;v=c.step('tick')
    assert v['cleanup_deadline']==131. and v['reap_deadlines']=={'target':31.,'monitor':31.}
    t[0]=31.;v=c.step('reap_target',identity_matches=True,reaped=True,returncode=0)
    assert v['status']=='UNKNOWN' and v['target_status']=='UNKNOWN'


def test_synthetic_clock_contract_runtime_cap_enters_cleanup():
    c,t=_register_synthetic_clock_contract();t[0]=12.;v=c.step('tick')
    assert v['state']=='CLEANUP' and v['cleanup_started']==12. and v['reason']=='trial_deadline'


@pytest.mark.parametrize('returncode',[None,True])
def test_synthetic_clock_contract_unconfirmed_reap_cannot_complete(returncode):
    c,t=_register_synthetic_clock_contract();t[0]=10.3;c.step('manager_disconnected')
    t[0]=10.4;v=c.step('reap_target',identity_matches=True,reaped=True,returncode=returncode)
    assert v['target_status']=='UNKNOWN' and v['status']=='UNKNOWN'


@pytest.mark.parametrize('state',['REGISTERING','REGISTERED'])
@pytest.mark.parametrize('now',[1799.999,1800.,1800.001])
def test_synthetic_clock_fix_work_deadline_precedes_registration_and_trial(state,now):
    c,t=_synthetic_clock_contract(start=1799.5);c.begin_creation();t[0]=1799.6;c.step('identity_confirmed')
    if state=='REGISTERED':t[0]=1799.7;c.step('ack')
    t[0]=now;v=c.step('ack' if state=='REGISTERING' else 'tick')
    if now<1800.:
        assert v['state']=='REGISTERED'
    else:
        assert v['state']=='TERMINAL' and v['status']=='UNKNOWN'
        assert v['reason']=='work_deadline' and v['cleanup_deadline']==1800.
        assert not v['real_creation_allowed']


def test_synthetic_clock_fix_monitor_reaped_first_freezes_unknown():
    c,t=_register_synthetic_clock_contract();t[0]=10.3;c.step('manager_disconnected')
    t[0]=10.4;v=c.step('reap_monitor',identity_matches=True,reaped=True,returncode=0)
    assert v['monitor_status']=='REAPED' and v['target_status']=='UNKNOWN'
    assert v['state']=='TERMINAL' and v['status']=='UNKNOWN'
    assert v['target_reap_owner'] is None and v['cleanup_owner'] is None
    t[0]=10.5;v=c.step('reap_target',identity_matches=True,reaped=True,returncode=0)
    assert v['status']=='UNKNOWN' and v['target_status']=='UNKNOWN'


def test_synthetic_clock_fix_normal_reap_order_stays_complete_without_live_owner():
    c,t=_register_synthetic_clock_contract();t[0]=10.3;c.step('manager_disconnected')
    t[0]=10.4;c.step('reap_target',identity_matches=True,reaped=True,returncode=0)
    t[0]=10.5;v=c.step('reap_monitor',identity_matches=True,reaped=True,returncode=0)
    assert v['status']=='SYNTHETIC_COMPLETE' and v['state']=='TERMINAL'
    assert v['target_reap_owner'] is None and v['cleanup_owner'] is None
    assert not v['real_creation_allowed'] and v['actual_termination']=='NOT_RUN'


def test_synthetic_clock_fix_monitor_fault_remains_unknown_after_target_reaped():
    c,t=_register_synthetic_clock_contract();t[0]=10.3;c.step('manager_disconnected')
    t[0]=10.4;c.step('reap_target',identity_matches=True,reaped=True,returncode=0)
    t[0]=10.5;c.step('monitor_failed')
    t[0]=10.6;v=c.step('reap_monitor',identity_matches=True,reaped=True,returncode=0)
    assert v['status']=='UNKNOWN' and v['target_reap_owner'] is None


def test_clean_clone_abi_fixture_is_compiler_verified(tmp_path):
    assert 'build_abi_basis' in globals(), 'clean-clone ABI fixture builder missing'
    basis=build_abi_basis(tmp_path)
    value=json.loads(basis.read_text())
    assert value['status']=='passed' and value['headers']
    _load_observer().load_abi(basis)


def test_body_exception_regression_has_no_outer_xfail():
    marks=getattr(test_synthetic_trace_surface_restores_after_body_exception,'pytestmark',[])
    assert all(mark.name!='xfail' for mark in marks)


@pytest.mark.parametrize('fault',['missing','arch','field','header','size'])
def test_explicit_abi_input_faults_fail_closed(tmp_path,abi_basis,fault):
    m=_load_observer();value=json.loads(abi_basis.read_bytes());path=tmp_path/'bad.json'
    if fault=='arch':value['architecture']='wrong-architecture'
    if fault=='field':value['fields'][0]['offset']+=1
    if fault=='size':value['structure_size']+=1
    if fault=='header':value['headers'][next(iter(value['headers']))]='0'*64
    if fault!='missing':path.write_text(json.dumps(value))
    with pytest.raises((FileNotFoundError,ValueError)):m.load_abi(path)


@pytest.mark.parametrize('fault',['nonzero','output','deadline'])
def test_abi_compiler_faults_never_publish_passed_basis(tmp_path,monkeypatch,fault):
    import os,time
    real_popen=subprocess.Popen;real_clock=time.monotonic;spawned=[];shift=[False]
    def launch(argv,**kwargs):
        if Path(argv[0]).name=='clang':
            code={'nonzero':'raise SystemExit(7)',
                  'output':'import sys,time;sys.stdout.write("x"*70000);sys.stdout.flush();time.sleep(30)',
                  'deadline':'import time;time.sleep(30)'}[fault]
            child=real_popen([sys.executable,'-c',code],**kwargs);spawned.append(child)
            if fault=='deadline':shift[0]=True
            return child
        return real_popen(argv,**kwargs)
    monkeypatch.setattr(subprocess,'Popen',launch)
    monkeypatch.setattr(time,'monotonic',lambda:real_clock()+(21 if shift[0] else 0))
    with pytest.raises((ValueError,TimeoutError)):build_abi_basis(tmp_path)
    assert not (tmp_path/'abi-basis.json').exists()
    failure=json.loads((tmp_path/'abi-failure.json').read_text())
    assert failure['status']=='failed' and spawned and all(c.poll() is not None for c in spawned)
    for c in spawned:
        with pytest.raises(ProcessLookupError):os.killpg(c.pid,0)


def test_abi_header_change_between_compile_and_publish_is_unverified(tmp_path,monkeypatch,abi_basis):
    target=Path(next(iter(json.loads(abi_basis.read_bytes())['headers'])))
    read=Path.read_bytes;count=[0]
    def changed(path):
        raw=read(path)
        if path==target:
            count[0]+=1
            if count[0]>1:return raw+b'\n/* injected changed header */'
        return raw
    monkeypatch.setattr(Path,'read_bytes',changed)
    with pytest.raises(ValueError,match='ABI compiler/header/source changed'):build_abi_basis(tmp_path)
    assert not (tmp_path/'abi-basis.json').exists()
    assert json.loads((tmp_path/'abi-failure.json').read_text())['status']=='failed'


@pytest.mark.parametrize('exit_code,force_work_timeout', [(0,False),(7,False),(0,True)])
def test_abi_parent_exit_never_leaves_owned_descendants(tmp_path,monkeypatch,exit_code,force_work_timeout):
    import os,signal,time
    real_popen=subprocess.Popen;real_clock=time.monotonic;owners=[];shift=[False]
    child_file=tmp_path/'descendant.pid'
    def launch(argv,**kwargs):
        if Path(argv[0]).name=='clang':
            # The descendant ignores TERM so cleanup must verify group-wide KILL.
            code=("import subprocess,sys,time;from pathlib import Path;"
                  "p=subprocess.Popen([sys.executable,'-c',"
                  "'import signal,time;signal.signal(signal.SIGTERM,signal.SIG_IGN);print(\\\"ready\\\",flush=True);time.sleep(10)'],"
                  "stdout=subprocess.PIPE,stderr=subprocess.DEVNULL);"
                  "assert p.stdout.readline()==b'ready\\n';"
                  "Path("+repr(str(child_file))+").write_text(str(p.pid));"
                  "p.stdout.close();print('dependency output',flush=True);"
                  "raise SystemExit("+str(exit_code)+")")
            owner=real_popen([sys.executable,'-c',code],**kwargs);owners.append(owner)
            if force_work_timeout:
                # Advance only after the owned parent exits; force the existing
                # work timeout path without spending the real 20s preparation.
                owner.wait(timeout=2);shift[0]=True
            return owner
        return real_popen(argv,**kwargs)
    monkeypatch.setattr(subprocess,'Popen',launch)
    monkeypatch.setattr(time,'monotonic',lambda:real_clock()+(21 if shift[0] else 0))
    try:
        with pytest.raises(Exception):build_abi_basis(tmp_path/'fixture')
        assert owners and child_file.exists()
        failure=json.loads((tmp_path/'fixture/abi-failure.json').read_text())
        assert failure['status']=='failed' and not (tmp_path/'fixture/abi-basis.json').exists()
        assert all(p.poll() is not None for p in owners)
        for p in owners:
            with pytest.raises(ProcessLookupError):os.killpg(p.pid,0)
        compiler=failure['calls'][-1]
        assert compiler['group_termination']=='observed' and compiler['reaped']
        assert sum(call['cleanup_elapsed'] for call in failure['calls'])<=5.05
    finally:
        # A RED must leave no process behind; don't alter the fixture's evidence.
        for p in owners:
            try:os.killpg(p.pid,signal.SIGKILL)
            except ProcessLookupError:pass
            p.wait(timeout=2)
        until=real_clock()+2
        while owners and real_clock()<until:
            try:os.killpg(owners[0].pid,0)
            except ProcessLookupError:break
            except PermissionError:pass
            time.sleep(.01)


def test_abi_unobservable_cleanup_is_bounded_and_closes_parent_streams(tmp_path,monkeypatch):
    import os,time
    real_popen=subprocess.Popen;real_clock=time.monotonic;real_sleep=time.sleep
    offset=[0.0];owners=[]
    def launch(*args,**kwargs):
        child=real_popen(*args,**kwargs);owners.append(child);return child
    def advance(seconds):offset[0]+=seconds;real_sleep(.0001)
    def denied(*args):raise PermissionError(13,'injected group visibility failure')
    monkeypatch.setattr(subprocess,'Popen',launch)
    monkeypatch.setattr(time,'monotonic',lambda:real_clock()+offset[0])
    monkeypatch.setattr(time,'sleep',advance)
    monkeypatch.setattr(os,'killpg',denied)
    with pytest.raises(RuntimeError,match='group termination unconfirmed'):build_abi_basis(tmp_path)
    failure=json.loads((tmp_path/'abi-failure.json').read_text())
    assert failure['status']=='failed' and not (tmp_path/'abi-basis.json').exists()
    row=failure['calls'][-1]
    assert row['group_termination']=='unconfirmed' and row['group_query_errno']==13
    assert 4.99<=sum(call['cleanup_elapsed'] for call in failure['calls'])<=5.05
    assert owners and all(child.returncode is not None and child.stdout.closed and child.stderr.closed for child in owners)


@pytest.mark.parametrize('stage',['create','close'])
def test_abi_selector_failure_still_cleans_owned_group(tmp_path,monkeypatch,stage):
    import os,signal,time,selectors
    real_popen=subprocess.Popen;real_selector=selectors.DefaultSelector;owners=[]
    ready=tmp_path/'ready';child_file=tmp_path/'child.pid'
    child_code='import signal,time;from pathlib import Path;signal.signal(signal.SIGTERM,signal.SIG_IGN);Path('+repr(str(ready))+').write_text("ready");time.sleep(10)'
    parent_code=('import subprocess,sys,time;from pathlib import Path;'
                 'p=subprocess.Popen([sys.executable,"-c",'+repr(child_code)+'],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL);'
                 '\nwhile not Path('+repr(str(ready))+').exists():time.sleep(.001)\n'
                 'Path('+repr(str(child_file))+').write_text(str(p.pid));print("discovery output",flush=True)')
    def launch(argv,**kwargs):
        p=real_popen([sys.executable,'-c',parent_code],**kwargs);owners.append(p);p.wait(timeout=2);return p
    class BrokenClose:
        def __init__(self):self.inner=real_selector()
        def __getattr__(self,name):return getattr(self.inner,name)
        def close(self):self.inner.close();raise OSError(5,'injected selector close failure')
    def broken_create():raise OSError(24,'injected selector allocation failure')
    monkeypatch.setattr(subprocess,'Popen',launch)
    monkeypatch.setattr(selectors,'DefaultSelector',broken_create if stage=='create' else BrokenClose)
    observed=False
    try:
        with pytest.raises(OSError):build_abi_basis(tmp_path/'fixture')
        assert owners and child_file.exists()
        for p in owners:
            with pytest.raises(ProcessLookupError):os.killpg(p.pid,0)
        observed=True
        assert all(p.returncode is not None and p.stdout.closed and p.stderr.closed for p in owners)
        row=json.loads((tmp_path/'fixture/abi-failure.json').read_text())['calls'][-1]
        assert row['group_termination']=='observed' and row['reaped']
    finally:
        if not observed:
            for p in owners:
                try:os.killpg(p.pid,signal.SIGKILL)
                except ProcessLookupError:pass
                p.wait(timeout=2);p.stdout.close();p.stderr.close()
        until=time.monotonic()+2
        while owners and time.monotonic()<until:
            try:os.killpg(owners[0].pid,0)
            except ProcessLookupError:break
            except PermissionError:pass
            time.sleep(.01)
