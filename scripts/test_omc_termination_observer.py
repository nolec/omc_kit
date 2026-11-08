"""File registration regressions; one real consumer, synthetic faults are separate."""
import json
from pathlib import Path
import subprocess
import sys

import pytest

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
