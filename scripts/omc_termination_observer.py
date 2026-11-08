#!/usr/bin/env python3
"""Single-consumer file registration diagnostic, not a descendant cleanup proof.

The creator keeps its direct child handle; the manager independently reads the
registered child's native identity. All mailbox I/O runs in owned subprocesses.
No socket, ps, installation, comparison runner or actual resumption is used.
"""
from __future__ import annotations

import argparse
import ctypes
import hashlib
import json
import os
from pathlib import Path
import platform
import subprocess
import sys
import threading
import time
import uuid

_FIELDS=[(n,ctypes.c_uint32) for n in ('pbi_flags','pbi_status','pbi_xstatus','pbi_pid','pbi_ppid','pbi_uid','pbi_gid','pbi_ruid','pbi_rgid','pbi_svuid','pbi_svgid','rfu_1')]
_FIELDS += [('pbi_comm',ctypes.c_char*16),('pbi_name',ctypes.c_char*32)]
_FIELDS += [(n,ctypes.c_uint32) for n in ('pbi_nfiles','pbi_pgid','pbi_pjobc','e_tdev','e_tpgid')]
_FIELDS += [('pbi_nice',ctypes.c_int32),('pbi_start_tvsec',ctypes.c_uint64),('pbi_start_tvusec',ctypes.c_uint64)]

class BSDInfo(ctypes.Structure):
    _fields_=_FIELDS


def store(path, value):
    """Exclusive evidence file; never overwrite a prior run."""
    with Path(path).open('x') as f:
        json.dump(value,f,ensure_ascii=False,sort_keys=True);f.flush();os.fsync(f.fileno())


def load_abi(path):
    basis=json.loads(Path(path).read_bytes())
    if basis['status']!='passed' or basis['architecture']!=platform.machine():raise ValueError('abi_architecture_unconfirmed')
    if ctypes.sizeof(BSDInfo)!=basis['structure_size'] or ctypes.alignment(BSDInfo)!=basis['structure_alignment']:raise ValueError('abi_size_unconfirmed')
    expected=[{'name':n,'offset':getattr(BSDInfo,n).offset,'width':ctypes.sizeof(t)} for n,t in _FIELDS]
    if expected!=basis['fields']:raise ValueError('abi_fields_unconfirmed')
    for name,pin in basis['headers'].items():
        if hashlib.sha256(Path(name).read_bytes()).hexdigest()!=pin:raise ValueError('abi_header_changed')
    lib=ctypes.CDLL('/usr/lib/libproc.dylib',use_errno=True)
    fn=lib.proc_pidinfo;fn.argtypes=[ctypes.c_int,ctypes.c_int,ctypes.c_uint64,ctypes.c_void_p,ctypes.c_int];fn.restype=ctypes.c_int
    return lib,fn


def identity(pid, native):
    """Native query preserves original errno; no failure is process absence."""
    pgid=os.getpgid(pid);info=BSDInfo();ctypes.set_errno(0)
    count=native(pid,3,0,ctypes.byref(info),ctypes.sizeof(info));err=ctypes.get_errno()
    if count!=ctypes.sizeof(info):raise OSError(err,'proc_pidinfo_size_unconfirmed:'+str(count))
    row={'pid':int(info.pbi_pid),'ppid':int(info.pbi_ppid),'pgid':int(info.pbi_pgid),
         'start_sec':int(info.pbi_start_tvsec),'start_usec':int(info.pbi_start_tvusec)}
    if row['pid']!=pid or row['pgid']!=pgid or row['start_sec']<=0 or not 0<=row['start_usec']<1000000:raise ValueError('native_identity_inconsistent')
    return row


def io_worker(args):
    """Only file I/O; never creates children or sends process signals."""
    if args.delay:time.sleep(args.delay)
    path=Path(args.path)
    if args.io=='write':
        data=json.loads(args.payload);tmp=path.with_name(path.name+'.'+uuid.uuid4().hex+'.tmp')
        store(tmp,data)
        # Run-unique destinations and one writer per job; no existing artifact replaced.
        if path.exists():raise ValueError('mailbox_already_published')
        os.rename(tmp,path)
        print(json.dumps({'published':True}),flush=True)
        return 0
    while time.monotonic()<args.deadline:
        try:
            if path.is_symlink():raise ValueError('mailbox_symlink')
            raw=path.read_bytes()
            if len(raw)>8192:raise ValueError('mailbox_oversize')
            print(json.dumps({'value':json.loads(raw)}),flush=True);return 0
        except FileNotFoundError:time.sleep(.002)
    print(json.dumps({'missing':True}),flush=True);return 1


class Job:
    """Owner retains Popen handle. No filesystem call in poll or cleanup."""
    def __init__(self,kind,path,deadline,payload=None,delay=0):
        cmd=[sys.executable,'-B',str(Path(__file__).resolve()),'--io',kind,'--path',str(path),'--deadline',str(deadline),'--delay',str(delay)]
        if payload is not None:cmd+=['--payload',json.dumps(payload)]
        self.child=subprocess.Popen(cmd,stdout=subprocess.PIPE,stderr=subprocess.PIPE)
        self.kind=kind;self.buffer=b'';self.errors=b'';self.answer=None
        os.set_blocking(self.child.stdout.fileno(),False);os.set_blocking(self.child.stderr.fileno(),False)
    def _drain(self,pipe,field):
        while True:
            try:chunk=os.read(pipe.fileno(),16384)
            except BlockingIOError:return False
            if not chunk:return True
            value=getattr(self,field)+chunk
            if len(value)>16384:raise ValueError('io_output_oversize')
            setattr(self,field,value)
    def poll(self):
        self._drain(self.child.stdout,'buffer')
        self._drain(self.child.stderr,'errors')
        code=self.child.poll()
        if code is not None and self.answer is None:
            # The worker may have written and exited after the first reads.
            # Drain again; EAGAIN is not EOF and must not become empty JSON.
            stdout_eof=self._drain(self.child.stdout,'buffer')
            stderr_eof=self._drain(self.child.stderr,'errors')
            if not stdout_eof or not stderr_eof:return None
            if code==0:
                self.answer=json.loads(self.buffer.decode())
            else:self.answer={'io_error':code,'stderr':self.errors.decode(errors='replace')}
        return self.answer
    def reap(self,deadline):
        signals=[];error=None
        try:
            if self.child.poll() is None:
                self.child.terminate();signals.append('TERM')
                try:self.child.wait(timeout=max(0,min(2,deadline-time.monotonic())))
                except subprocess.TimeoutExpired:
                    if time.monotonic()<deadline:
                        self.child.kill();signals.append('KILL')
                        self.child.wait(timeout=max(0,min(2,deadline-time.monotonic())))
        except (OSError,subprocess.TimeoutExpired) as exc:error={'type':type(exc).__name__,'errno':getattr(exc,'errno',None)}
        code=self.child.poll()
        self.child.stdout.close();self.child.stderr.close()
        return {'pid':self.child.pid,'owner_pid':os.getpid(),'kind':self.kind,'returncode':code,
                'reaped':code is not None,'signals':signals,'error':error}


def wait_job(job, deadline, expired):
    while time.monotonic()<deadline and not expired.is_set():
        answer=job.poll()
        if answer is not None:
            if 'io_error' in answer:raise RuntimeError('io_worker_failed')
            return answer
        time.sleep(.002)
    raise TimeoutError('registration_deadline')


def valid_request(msg, run_id, creation_id, creator_pid, observed, now):
    if msg['run_id']!=run_id or msg['creation_id']!=creation_id:raise ValueError('registration_id_mismatch')
    if not isinstance(msg['t0'],(int,float)) or not 0<msg['deadline']-msg['t0']<=1.000001:raise ValueError('registration_clock_invalid')
    if now>=msg['deadline']:raise TimeoutError('registration_deadline')
    if msg['identity']!=observed or observed['ppid']!=creator_pid or observed['pid']!=observed['pgid']:raise ValueError('identity_mismatch')
    return True


def creator(args):
    out=Path(args.output);lib,native=load_abi(args.abi_basis)
    jobs=[];done=threading.Event();expired=threading.Event();target=None;timer=None
    record={'status':'failed','io_workers':[],'reaped':False,'ownership':'UNKNOWN'}
    try:
        target=subprocess.Popen([sys.executable,'-B','-c','import time;time.sleep(2)'],start_new_session=True)
        t0=time.monotonic();deadline=t0+1;cleanup_deadline=t0+20
        def watch():
            if not done.wait(max(0,deadline-time.monotonic())):expired.set()
        timer=threading.Thread(target=watch,daemon=True);timer.start()
        row=identity(target.pid,native)
        if row['ppid']!=os.getpid() or row['pgid']!=target.pid:raise ValueError('creator_ownership_mismatch')
        msg={'run_id':args.run_id,'creation_id':args.creation_id,'identity':row,'t0':t0,'deadline':deadline}
        record.update(identity=row,t0=t0,deadline=deadline)
        writer=Job('write',out/'request.json',deadline,msg);jobs.append(writer)
        wait_job(writer,deadline,expired)
        reader=Job('read',out/'ack.json',deadline);jobs.append(reader)
        ack=wait_job(reader,deadline,expired)['value']
        received=time.monotonic()
        if expired.is_set() or received>=deadline or ack!=msg:raise TimeoutError('registration_deadline_or_ack_mismatch')
        if target.poll() is not None or identity(target.pid,native)!=row:raise ValueError('creator_identity_changed')
        if time.monotonic()>=deadline:raise TimeoutError('registration_deadline')
        record.update(status='registered',ownership='confirmed',registration_elapsed=time.monotonic()-t0,ack_received=received)
        done.set()
    except (OSError,ValueError,RuntimeError,TimeoutError,KeyError) as exc:
        record['error']={'type':type(exc).__name__,'errno':getattr(exc,'errno',None),'message':str(exc)}
        record['frozen']=True
        if target is not None:record['registration_elapsed']=time.monotonic()-t0
    finally:
        done.set()
        if timer:timer.join(timeout=.05)
        cleanup_end=cleanup_deadline if target is not None else time.monotonic()+20
        for job in jobs:record['io_workers'].append(job.reap(cleanup_end))
        if target is not None:
            try:record['returncode']=target.wait(timeout=max(0,min(3,cleanup_end-time.monotonic())));record['reaped']=True
            except subprocess.TimeoutExpired:record['reaped']=False
        if not record['reaped'] or any(not j['reaped'] for j in record['io_workers']):record.update(status='failed',ownership='UNKNOWN')
        store(out/'creator-result.json',record)
    return 0 if record['status']=='registered' and record.get('returncode')==0 else 1


def base_result(consumer_count=None):
    return {'status':'failed','consumer_count':consumer_count,'verified_consumer_count':0,'io_workers':[],'ownership':'UNKNOWN',
            'next_execution':'NOT_RUN','descendant_group_termination':'UNVERIFIED','comparisons':'NOT_RUN'}


def file_register(args):
    out=Path(args.output).resolve();out.mkdir(parents=True,exist_ok=False)
    lib,native=load_abi(args.abi_basis);result=base_result();jobs=[];owner=None
    run_id=uuid.uuid4().hex;creation_id=uuid.uuid4().hex;start=time.monotonic();end=start+6;cleanup_end=start+20
    expired=threading.Event();done=threading.Event()
    def watch():
        if not done.wait(max(0,end-time.monotonic())):expired.set()
    timer=threading.Thread(target=watch,daemon=True);timer.start()
    try:
        request=Job('read',out/'request.json',end);jobs.append(request)
        with (out/'creator-stdout.txt').open('wb') as stdout,(out/'creator-stderr.txt').open('wb') as stderr:
            owner=subprocess.Popen([sys.executable,'-B',str(Path(__file__).resolve()),'--creator','--output',str(out),'--abi-basis',str(Path(args.abi_basis).resolve()),'--run-id',run_id,'--creation-id',creation_id],stdout=stdout,stderr=stderr)
            result['creator_pid']=owner.pid
            msg=wait_job(request,end,expired)['value'];row=identity(msg['identity']['pid'],native)
            valid_request(msg,run_id,creation_id,owner.pid,row,time.monotonic())
            result.update(manager_identity=row,consumer_pid=row['pid'],consumer_count=1,verified_consumer_count=1)
            # Check current controller state immediately before scheduling the acknowledgment.
            if expired.is_set() or time.monotonic()>=msg['deadline']:raise TimeoutError('registration_deadline')
            ack=Job('write',out/'ack.json',msg['deadline'],msg);jobs.append(ack)
            wait_job(ack,msg['deadline'],expired)
            result['manager_ack_posted']=time.monotonic()
            result['creator_returncode']=owner.wait(timeout=max(0,end-time.monotonic()))
            if result['creator_returncode']!=0:raise RuntimeError('creator_rejected_or_unconfirmed')
            result.update(status='registered',ownership='confirmed')
    except (OSError,ValueError,RuntimeError,TimeoutError,KeyError,subprocess.TimeoutExpired) as exc:
        result['error']={'type':type(exc).__name__,'errno':getattr(exc,'errno',None),'message':str(exc)};result['frozen']=True
    finally:
        done.set();timer.join(timeout=.05)
        for job in jobs:result['io_workers'].append(job.reap(cleanup_end))
        if owner is not None:
            try:result['creator_returncode']=owner.wait(timeout=max(0,min(3,cleanup_end-time.monotonic())))
            except subprocess.TimeoutExpired:result['creator_reap']='UNKNOWN'
        if any(not j['reaped'] for j in result['io_workers']) or result.get('creator_reap')=='UNKNOWN':result.update(status='failed',ownership='UNKNOWN')
        result['elapsed']=time.monotonic()-start
        store(out/'result.json',result)
    return 0 if result['status']=='registered' else 1


def exercise(args):
    """Public synthetic error surface; never creates a consumer or calls native API."""
    out=Path(args.output).resolve();out.mkdir(parents=True,exist_ok=False);r=base_result(consumer_count=0);r['synthetic']=True
    r['frozen']=True;r['registration_elapsed']=0.;jobs=[];cleanup_start=time.monotonic();deadline=cleanup_start+.05
    if args.exercise in ('mismatch','query-error'):
        r['reason']='identity_mismatch' if args.exercise=='mismatch' else 'query_unconfirmed'
        if args.exercise=='query-error':r['query_error']={'errno':13,'proc_pidinfo_return':0,'synthetic':True}
        else:
            msg={'run_id':'r','creation_id':'c','t0':cleanup_start,'deadline':cleanup_start+1,'identity':{'pid':1,'ppid':2,'pgid':1}}
            try:valid_request(msg,'r','c',2,{'pid':1,'ppid':3,'pgid':1},cleanup_start)
            except ValueError as exc:r['reason']=str(exc)
            else:raise AssertionError('mismatch accepted')
    else:
        job=Job('write',out/'late-request.json',deadline,{'synthetic':True},delay=.2 if args.exercise=='late' else 2);jobs.append(job)
        event=threading.Event()
        try:wait_job(job,deadline,event)
        except TimeoutError:r.update(reason='registration_deadline',registration_elapsed=time.monotonic()-cleanup_start)
        else:raise AssertionError('delayed job did not miss deadline')
        # Frozen in controller memory. A late file can be observed but never consumed.
        if args.exercise=='late':
            job.child.wait(timeout=1)
            r['late_publication_observed']=(out/'late-request.json').is_file()
            r['late_publication_ignored']=r['frozen'] and r['late_publication_observed']
    stop=time.monotonic();end=cleanup_start+20
    for job in jobs:r['io_workers'].append(job.reap(end))
    r['cleanup_elapsed']=time.monotonic()-stop
    r['io_termination']='observed' if all(j['reaped'] for j in r['io_workers']) else 'UNKNOWN'
    store(out/'result.json',r);return 1


def main():
    p=argparse.ArgumentParser(description=__doc__)
    modes=p.add_mutually_exclusive_group(required=True)
    modes.add_argument('--file-register',action='store_true',help='one real consumer, native manager verification, file registration')
    modes.add_argument('--exercise',choices=['mismatch','query-error','late','stall'])
    modes.add_argument('--creator',action='store_true',help=argparse.SUPPRESS)
    modes.add_argument('--io',choices=['read','write'],help=argparse.SUPPRESS)
    p.add_argument('--output');p.add_argument('--abi-basis');p.add_argument('--run-id');p.add_argument('--creation-id')
    p.add_argument('--path');p.add_argument('--deadline',type=float,default=0);p.add_argument('--payload');p.add_argument('--delay',type=float,default=0)
    a=p.parse_args()
    if a.io:return io_worker(a)
    if not a.output:p.error('--output required')
    if a.exercise:return exercise(a)
    if not a.abi_basis:p.error('--abi-basis required')
    return creator(a) if a.creator else file_register(a)

# Synthetic preparation surface only: no process, signal or native query APIs.
class TraceBinding:
    names = ('supervise', '_stop_group', '_wait_group_exit', '_group_exists')

    def __init__(self, namespace, sink, clock, worker):
        from types import SimpleNamespace
        if type(namespace) is not SimpleNamespace:
            raise ValueError('synthetic_namespace_required')
        self.namespace, self.sink, self.clock, self.worker = namespace, sink, clock, worker
        self.active = False
        self.originals = {}
        self.record_errors = []

    def _record(self, name, phase, error=None):
        try:
            self.sink({'function': name, 'phase': phase, 'worker': self.worker,
                       'monotonic': self.clock(), 'errno': getattr(error, 'errno', None),
                       'pid': None, 'pgid': None, 'synthetic': True})
        except Exception as exc:
            self.record_errors.append({'type': type(exc).__name__, 'errno': getattr(exc, 'errno', None)})

    def _wrapper(self, name, original):
        def wrapped(*args, **kwargs):
            self._record(name, 'enter')
            try:
                result = original(*args, **kwargs)
            except BaseException as exc:
                self._record(name, 'raise', exc)
                raise
            self._record(name, 'return')
            return result
        return wrapped

    def install(self):
        if self.active or hasattr(self.namespace, '_omc_trace_owner'):
            raise ValueError('already_installed')
        originals = {name: getattr(self.namespace, name) for name in self.names}
        if not all(callable(fn) for fn in originals.values()):
            raise ValueError('synthetic_callable_required')
        self.originals = originals
        self.namespace._omc_trace_owner = self
        self.active = True
        try:
            for name, original in originals.items():
                setattr(self.namespace, name, self._wrapper(name, original))
        except BaseException:
            self.restore()
            raise
        return self

    def restore(self):
        if not self.active:
            return
        for name in reversed(self.names):
            setattr(self.namespace, name, self.originals[name])
        del self.namespace._omc_trace_owner
        self.active = False


def pytest_runtest_call(item):
    # Opt-in synthetic items in the existing test file only. No global patching.
    spec = getattr(item, '_omc_synthetic_trace', None)
    if (spec is None or item.path.name != 'test_omc_termination_observer.py'
            or not item.name.startswith('test_synthetic_trace_')):
        yield
        return
    binding = TraceBinding(spec['namespace'], spec['sink'], spec['clock'], spec['worker'])
    binding.install()
    spec['state']['binding'] = binding
    try:
        yield
    finally:
        binding.restore()


# Keep the standalone registration CLI free of a new pytest import dependency.
if __name__ != '__main__':
    import pytest as _pytest
    pytest_runtest_call = _pytest.hookimpl(hookwrapper=True, tryfirst=True)(pytest_runtest_call)


def summarize_synthetic_workers(expected, reports):
    workers = {name: reports.get(name) for name in expected}
    missing = [name for name, report in workers.items() if report is None]
    incomplete = [name for name, report in workers.items()
                  if report is not None and (report.get('record_errors') or report.get('events') is None)]
    return {'status': 'UNKNOWN' if missing or incomplete else 'COMPLETE',
            'workers': workers, 'missing': missing, 'incomplete': incomplete,
            'synthetic': True, 'actual_worker_validation': 'NOT_RUN'}


def inspect_fake_cleanup(handles, *, now, cleanup_start, overall_deadline, manager_alive=True):
    # A pure contract calculation, never an OS ownership/termination observation.
    import math
    if not all(isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v)
               for v in (now, cleanup_start, overall_deadline)):
        raise ValueError('finite_fake_clock_required')
    if len(handles) > 8:
        raise ValueError('fake_handle_limit')
    shared = min(cleanup_start + 120, overall_deadline)
    rows = []
    for handle in handles:
        identity_ok = (handle.get('owned') is True and handle.get('start_id') is not None
                       and handle.get('start_id') == handle.get('expected_start_id'))
        reaped = identity_ok and handle.get('reaped') is True and handle.get('returncode') is not None
        rows.append({'pid': handle.get('pid'), 'status': 'REAPED' if reaped else 'UNKNOWN',
                     'returncode': handle.get('returncode') if reaped else None,
                     'deadline': min(now + 20, shared)})
    return {'status': 'COMPLETE' if all(h['status'] == 'REAPED' for h in rows) else 'UNKNOWN',
            'handles': rows, 'shared_deadline': shared, 'deadline_reached': now >= shared,
            'cleanup_owner': 'fake-creator' if manager_alive else 'fake-independent-monitor',
            'synthetic': True, 'actual_monitor': 'NOT_RUN', 'actual_termination': 'NOT_RUN',
            'test_children_created': 0}


def _open_reserved_audit(path):
    return path.open('xb')


class AuditRun:
    """A single reserved run; collisions never create alternate IDs or paths."""
    def __init__(self, base, *, run_id=None):
        import re
        self.run_id = uuid.uuid4().hex if run_id is None else run_id
        if not isinstance(self.run_id, str) or not re.fullmatch('[0-9a-f]{32}', self.run_id):
            raise ValueError('invalid_audit_run_id')
        base = Path(base)
        base.mkdir(parents=True, exist_ok=True)
        self.path = base / self.run_id
        self.path.mkdir(exist_ok=False)

    def run_phase(self, phase, trial):
        if phase not in ('baseline', 'red', 'green'):
            raise ValueError('invalid_audit_phase')
        path = self.path / (phase + '-audit.json')
        # This may fail; do not call the trial until exclusive reservation succeeds.
        stream = _open_reserved_audit(path)
        result = {'run_id': self.run_id, 'phase': phase, 'audit_path': str(path),
                  'status': 'incomplete', 'audit_status': 'failed', 'trial_status': 'unrun'}
        try:
            try:
                value = trial()
                result['trial_status'] = 'failed' if isinstance(value, int) and value != 0 else 'passed'
                result['trial_result'] = value
            except Exception as exc:
                result['trial_status'] = 'failed'
                result['trial_error'] = {'type': type(exc).__name__, 'errno': getattr(exc, 'errno', None)}
            # The file is evidence payload, not a persisted publication/success receipt.
            payload = {k: v for k, v in result.items() if k not in ('status', 'audit_status')}
            stream.write(json.dumps(payload, ensure_ascii=False).encode('utf-8'))
            stream.flush()
            os.fsync(stream.fileno())
        except Exception as exc:
            result['storage_error'] = {'type': type(exc).__name__, 'errno': getattr(exc, 'errno', None)}
        finally:
            try:
                stream.close()
            except Exception as exc:
                result.setdefault('storage_error', {'type': type(exc).__name__, 'errno': getattr(exc, 'errno', None)})
        if 'storage_error' not in result:
            result['audit_status'] = 'saved'
            result['status'] = 'complete' if result['trial_status'] == 'passed' else 'failed'
        return result


def run_audit_entry(base, phase, trial, *, run_id=None):
    """New-run entry point; caller receives the sole reserved path and outcome."""
    return AuditRun(base, run_id=run_id).run_phase(phase, trial)


class SyntheticOwnershipContract:
    """Pure fake-clock state transitions; never grants real process creation."""
    def __init__(self, clock, *, work_start=0.):
        self.clock = clock
        self.work_deadline = self._finite(work_start) + 1800
        self.last_now = None
        self.state, self.status, self.reason = 'NEW', 'UNKNOWN', None
        self.t0 = self.registration_deadline = self.trial_deadline = None
        self.cleanup_started = self.cleanup_deadline = None
        self.reap_deadlines = {}
        self.monitor_alive, self.identity_confirmed = True, False
        self.monitor_faulted = False
        self.target_status = self.monitor_status = 'UNKNOWN'

    @staticmethod
    def _finite(value):
        import math
        if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
            raise ValueError('finite_fake_clock_required')
        return value

    def _now(self):
        now = self._finite(self.clock())
        if self.last_now is not None and now < self.last_now:
            raise ValueError('fake_clock_went_backwards')
        self.last_now = now
        return now

    def snapshot(self):
        owner = 'monitor' if self.monitor_alive else None
        return {'state': self.state, 'status': self.status, 'reason': self.reason,
                't0': self.t0, 'registration_deadline': self.registration_deadline,
                'trial_deadline': self.trial_deadline, 'cleanup_started': self.cleanup_started,
                'cleanup_deadline': self.cleanup_deadline, 'reap_deadlines': dict(self.reap_deadlines),
                'cleanup_owner': owner, 'target_reap_owner': owner, 'monitor_reap_owner': 'manager',
                'target_status': self.target_status, 'monitor_status': self.monitor_status,
                'synthetic': True, 'real_creation_allowed': False, 'test_children_created': 0,
                'actual_monitor': 'NOT_RUN', 'actual_termination': 'NOT_RUN',
                'next_execution': 'NOT_RUN'}

    def _cleanup(self, now, reason):
        if self.state != 'CLEANUP':
            self.cleanup_started = now
            self.cleanup_deadline = min(now + 120, self.work_deadline)
            deadline = min(now + 20, self.cleanup_deadline)
            self.reap_deadlines = {'target': deadline, 'monitor': deadline}
        self.state, self.status, self.reason = 'CLEANUP', 'UNKNOWN', reason
        if now >= self.cleanup_deadline:
            self.state = 'TERMINAL'

    def begin_creation(self):
        if self.state != 'NEW':
            raise ValueError('synthetic_creation_already_started')
        # Captured immediately before the simulated creation, never after a delay.
        self.t0 = self._now()
        self.registration_deadline = self.t0 + 1
        self.trial_deadline = self.t0 + 2
        self.state = 'REGISTERING'
        if self.t0 >= self.work_deadline:
            self._cleanup(self.t0, 'work_deadline')
        return self.snapshot()

    def step(self, event, *, identity_matches=False, reaped=False, returncode=None):
        allowed = ('identity_confirmed', 'identity_mismatch', 'identity_unconfirmed', 'ack',
                   'monitor_failed', 'manager_disconnected', 'tick', 'reap_target', 'reap_monitor')
        if event not in allowed:
            raise ValueError('unknown_synthetic_event')
        now = self._now()
        if self.state == 'TERMINAL':
            return self.snapshot()
        # The whole-work bound precedes registration, ACK and trial transitions.
        if now >= self.work_deadline:
            if event == 'monitor_failed':
                self.monitor_alive, self.identity_confirmed = False, False
                self.monitor_faulted = True
            self._cleanup(now, 'work_deadline')
            self.state, self.status = 'TERMINAL', 'UNKNOWN'
            return self.snapshot()
        if event == 'monitor_failed':
            self.monitor_alive, self.identity_confirmed = False, False
            self.monitor_faulted = True
            self._cleanup(now, 'monitor_unavailable')
            return self.snapshot()
        if self.state == 'NEW':
            raise ValueError('synthetic_creation_not_started')
        if self.state == 'REGISTERING' and now >= self.registration_deadline:
            self._cleanup(now, 'registration_deadline')
            return self.snapshot()
        if self.state == 'REGISTERED' and now >= self.trial_deadline:
            self._cleanup(now, 'trial_deadline')
            return self.snapshot()
        if self.state == 'CLEANUP':
            pending = [self.reap_deadlines[role] for role in ('target', 'monitor')
                       if getattr(self, role + '_status') != 'REAPED']
            if now >= self.cleanup_deadline or any(now >= deadline for deadline in pending):
                self.state, self.status, self.reason = 'TERMINAL', 'UNKNOWN', 'cleanup_deadline'
                return self.snapshot()
            if event in ('reap_target', 'reap_monitor'):
                role = event.removeprefix('reap_')
                confirmed = (identity_matches is True and reaped is True
                             and type(returncode) is int and (role == 'monitor' or self.monitor_alive))
                if not confirmed:
                    self.state, self.status = 'TERMINAL', 'UNKNOWN'
                    return self.snapshot()
                setattr(self, role + '_status', 'REAPED')
                if role == 'monitor':
                    self.monitor_alive = False
                    if self.target_status != 'REAPED':
                        # A reaped parent cannot subsequently reap its child.
                        self.monitor_faulted = True
                        self.state, self.status, self.reason = 'TERMINAL', 'UNKNOWN', 'monitor_reaped_before_target'
                        return self.snapshot()
                if self.target_status == self.monitor_status == 'REAPED':
                    self.state = 'TERMINAL'
                    self.status = 'UNKNOWN' if self.monitor_faulted else 'SYNTHETIC_COMPLETE'
            # Identity/ACK events cannot reopen registration or extend cleanup.
            return self.snapshot()
        if event in ('identity_mismatch', 'identity_unconfirmed'):
            self._cleanup(now, event)
        elif event == 'identity_confirmed' and self.state == 'REGISTERING':
            self.identity_confirmed = True
        elif event == 'ack' and self.state == 'REGISTERING':
            if self.identity_confirmed and self.monitor_alive:
                self.state, self.status = 'REGISTERED', 'SYNTHETIC_REGISTERED'
            else:
                self._cleanup(now, 'identity_unconfirmed')
        elif event == 'manager_disconnected':
            self._cleanup(now, 'manager_disconnected')
        return self.snapshot()


if __name__=='__main__':raise SystemExit(main())
