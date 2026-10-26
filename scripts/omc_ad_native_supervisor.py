#!/usr/bin/env python3
"""POSIX single-attempt operator supervisor; not an advertising task evaluator.

Local records bind evidence, not hostile-tamper resistance. Legacy mode leaves
the detached core child group to core. Absolute mode has explicit ownership handoff.
"""
from __future__ import annotations

import argparse
from contextlib import ExitStack
from datetime import datetime, timezone, timedelta
import hashlib
import json
import math
import os
from pathlib import Path
import selectors
import signal
import subprocess
import sys
import tempfile
import time

LIMIT = 1048576
CHUNK = 65536
CHECK_IDS = ['tests','lint','build-controlled','build-pixels','controlled','pixels-dpr1','pixels-dpr2']


def phase_status(observed, deadline):
    if type(observed) not in (int, float):return 'unknown'
    try:
        if not math.isfinite(observed):return 'unknown'
    except OverflowError:return 'unknown'
    return 'passed' if observed <= deadline else 'failed'


def cleanup_deadline(stop, origin, span=1800):
    return min(stop+12*span/1800, origin+1772*span/1800)



def assess_preparation(path, run_id, worker_pid, origin, deadline, observed):
    """Check the worker's completion observation, not marker presence alone."""
    if path.is_symlink():
        return {'status':'invalid','reason':'prepared_symlink'}
    if not path.exists():
        return {'status':'pending' if observed < deadline else 'late',
                'reason':'prepared_record_missing'}
    try:
        if not path.is_file() or path.stat().st_size > LIMIT:
            raise ValueError('prepared_record_invalid')
        value=json.loads(path.read_bytes())
        # Publication can occur after the caller's sample but before this read.
        # Compare completion against one observation taken after the record.
        observed=time.monotonic()
        if (not isinstance(value,dict) or value.get('run_id')!=run_id
                or type(value.get('worker_pid')) is not int or value['worker_pid']!=worker_pid):
            raise ValueError('prepared_identity_mismatch')
        completed=value.get('monotonic')
        if (type(completed) not in (int,float) or not math.isfinite(completed)
                or not origin<=completed<=observed):
            raise ValueError('prepared_timestamp_invalid')
        return {'status':'passed' if completed<=deadline else 'late',
                'completed_monotonic':completed,'observed_monotonic':observed}
    except (OSError,ValueError,TypeError,OverflowError):
        return {'status':'invalid','reason':'prepared_record_invalid'}


def read_absolute_result(path, run_id):
    """External read-only projection; EOF or an old file cannot mean success."""
    try:
        if path.is_symlink() or path.stat().st_size > LIMIT:raise ValueError('invalid result')
        value=json.loads(path.read_bytes())
        if value.get('schema_version')!='omc-ad-absolute-result/v1' or value.get('run_id')!=run_id:
            raise ValueError('result identity mismatch')
        seal_path=path.with_name('seal.json')
        if seal_path.is_symlink():raise ValueError('seal symlink')
        seal=json.loads(seal_path.read_bytes())
        if (seal['run_id']!=run_id or seal['result_sha256']!=digest(path)
                or phase_status(seal['completed_monotonic'],value['deadline_monotonic'])!='passed'):
            raise ValueError('final seal missing or late')
        return value
    except (OSError, ValueError, TypeError, AttributeError, KeyError):
        return {'status':'unknown','reason':'final_record_missing_or_invalid'}


def process_identity(pid, leader=True):
    """Local POSIX lifetime token; never authorizes a PID from a file alone."""
    if type(pid) is not int or pid<=1:return None
    try:
        fields=subprocess.check_output(['/bin/ps','-p',str(pid),'-o','pgid=','-o','lstart='],
                                       stderr=subprocess.DEVNULL,timeout=.25).decode().split()
        if len(fields)<6 or (leader and int(fields[0])!=pid):return None
        return {'pid':pid,'pgid':int(fields[0]),'started':' '.join(fields[1:])}
    except (OSError, ValueError, subprocess.SubprocessError):return None


def group_members(pgid):
    try:
        rows=subprocess.check_output(['/bin/ps','-axo','pid=,pgid=,lstart='],
                                     stderr=subprocess.DEVNULL,timeout=.25).decode().splitlines()
        return [{'pid':int(f[0]),'pgid':pgid,'started':' '.join(f[2:])}
                for line in rows if len(f:=line.split())>=7 and int(f[1])==pgid]
    except (OSError,ValueError,subprocess.SubprocessError):return []


def publish_ownership(args, pid):
    path=getattr(args,'ownership_file',None)
    if path is None:return
    row=process_identity(pid)
    if row is None:return
    value={'run_id':args.run_id,'worker_pid':os.getpid(),'groups':[]}
    if path.exists():value=json.loads(path.read_bytes())
    if row not in value['groups']:value['groups'].append(row)
    temp=path.with_suffix('.tmp');temp.write_text(json.dumps(value));os.replace(temp,path)


def assess_phase_evidence(args, origin, span):
    """Validate local observations and raw receipts, not an adversarial signature."""
    statuses={k:'unknown' for k in ('edits','checks','completion')}
    try:
        path=owned_path(args.cwd.resolve(),args.phase_evidence)
        if path.is_symlink() or path.stat().st_size>LIMIT:raise ValueError('invalid phase evidence')
        value=json.loads(path.read_bytes())
        if (value['schema_version']!='omc-ad-phase-evidence/v1' or value['run_id']!=args.run_id
                or value['candidate']!=str(args.cwd.resolve()) or value['origin_monotonic']!=origin):
            raise ValueError('phase identity mismatch')
        sources=value['source_sha256']
        if not sources or not isinstance(sources,dict):raise ValueError('source snapshot missing')
        scope=args.cwd.resolve()/'libs/sellerhub-store/src/lib/components/Ad'
        if set(sources)!={str(p.relative_to(args.cwd.resolve())) for p in scope.rglob('*') if p.is_file()}:
            raise ValueError('source snapshot scope incomplete')
        for name,pin in sources.items():
            source=owned_path(args.cwd.resolve(),args.cwd.resolve()/name)
            if not name.startswith('libs/sellerhub-store/src/lib/components/Ad/') or digest(source)!=pin:
                raise ValueError('source snapshot mismatch')
        snapshot=hashlib.sha256(json.dumps(sources,sort_keys=True).encode()).hexdigest()
        edit=value['last_edit_monotonic']
        if type(edit) not in (int,float) or not origin<=edit:raise ValueError('edit observation missing')
        statuses['edits']=phase_status(edit,origin+900*span/1800)
        rows=value['checks']
        if [r['id'] for r in rows]!=CHECK_IDS:raise ValueError('seven receipts missing or duplicate')
        end=edit
        for row in rows:
            if (row['source_snapshot_sha256']!=snapshot or row['run_id']!=args.run_id
                    or type(row['start']) not in (int,float) or type(row['end']) not in (int,float)
                    or not end<=row['start']<=row['end']<=time.monotonic()):raise ValueError('receipt binding or order mismatch')
            limits=dict(zip(CHECK_IDS,[180,120,90,90,90,90,90]))
            if row['end']-row['start']>limits[row['id']]:raise ValueError('command limit exceeded')
            for stream in ('stdout','stderr','result'):
                evidence=owned_path(args.cwd.resolve(),args.cwd.resolve()/row[stream])
                if digest(evidence)!=row[stream+'_sha256']:raise ValueError('raw receipt digest mismatch')
            assessment=json.loads(evidence.read_bytes())
            # An exit or caller's passed label alone is never semantic verification.
            if (assessment.get('run_id')!=args.run_id or assessment.get('check_id')!=row['id']
                    or assessment.get('source_snapshot_sha256')!=snapshot
                    or assessment.get('assessment')!='passed'
                    or assessment.get('stdout_sha256')!=row['stdout_sha256']
                    or assessment.get('stderr_sha256')!=row['stderr_sha256']):
                raise ValueError('semantic assessment missing or mismatched')
            from omc_ad_validation_runtime import assess_common_result
            assess_common_result(args.cwd.resolve(),row['id'],assessment)
            end=row['end']
        statuses['checks']=phase_status(end,origin+1680*span/1800)
        completed=value['completion_monotonic']
        if type(completed) not in (int,float) or not end<=completed<=time.monotonic():raise ValueError('completion order mismatch')
        auxiliary=value['auxiliary']
        if [r['id'] for r in auxiliary]!=['reload-controlled','generate-manifests','bind-results']:
            raise ValueError('auxiliary observations missing')
        if (any(type(r['start']) not in (int,float) or type(r['end']) not in (int,float)
                or not origin<=r['start']<=r['end']<=completed for r in auxiliary)
                or sum(r['end']-r['start'] for r in auxiliary)>30):
            raise ValueError('auxiliary budget or timestamp invalid')
        by_id={r['id']:r for r in rows}
        if (not by_id['build-controlled']['end']<=auxiliary[0]['start']<=auxiliary[0]['end']<=by_id['controlled']['start']
                or not max(by_id[k]['end'] for k in ('build-controlled','build-pixels'))<=auxiliary[1]['start']<=auxiliary[1]['end']<=by_id['pixels-dpr1']['start']
                or not end<=auxiliary[2]['start']):raise ValueError('auxiliary dependency order invalid')
        statuses['completion']=phase_status(completed,origin+1740*span/1800)
        return statuses, 'passed' if all(x=='passed' for x in statuses.values()) else 'failed', None
    except (OSError, ValueError, KeyError, TypeError, AttributeError, OverflowError, StopIteration) as exc:
        return statuses,'unknown',str(exc)


def supervise_absolute(args):
    """Parent watchdog covers worker setup, legacy execution, custody and seal."""
    origin=args.origin_monotonic; deadline=args.deadline_monotonic
    try:
        span=deadline-origin
        now=time.monotonic()
        if (not all(math.isfinite(v) for v in (origin,deadline,span)) or not 0<span<=1800
                or not args.run_id or not args.run_id.strip() or origin>now or deadline<=now):raise ValueError('invalid absolute deadline')
    except (TypeError,OverflowError):raise ValueError('invalid absolute deadline')
    cwd=args.cwd.resolve(strict=True); output=Path(os.path.abspath(args.output))
    if any(p.is_symlink() for p in [output,*output.parents]):raise ValueError('output symlink')
    for record in (args.child_pid_file,args.child_exit_file):owned_path(cwd,record)
    inputs=[args.prompt.absolute(),args.child_pid_file.absolute(),args.child_exit_file.absolute()]
    if args.termination_record:inputs.append(args.termination_record.absolute())
    if output in inputs or any(output in p.parents for p in inputs):raise ValueError('output/input collision')
    output.mkdir(mode=0o700)
    worker_dir=output/'worker';worker_dir.mkdir()
    ownership=worker_dir/'ownership.json'; prepared=worker_dir/'prepared.json'
    interrupted=[None]; previous={}
    def interrupted_by(signum,_frame):interrupted[0]=signum
    for signum in (signal.SIGINT,signal.SIGTERM):
        previous[signum]=signal.signal(signum,interrupted_by)
    worker=os.fork()
    if worker==0:
        try:
            os.setsid()
            args.output=worker_dir/'capture';args.ownership_file=ownership;args.prepared_file=prepared
            args.timeout=max(.001,origin+1760*span/1800-time.monotonic())
            args.drain=min(12,12*span/1800)
            with ExitStack() as resources:code=supervise(args,resources)
            phases,evidence,error=assess_phase_evidence(args,origin,span)
            (worker_dir/'phase.json').write_text(json.dumps({'worker_pid':os.getpid(),'run_id':args.run_id,
                'phases':phases,'evidence':evidence,'error':error}))
            os._exit(code)
        except BaseException as exc:
            try:(worker_dir/'setup-error.json').write_text(json.dumps({'error':str(exc)}))
            finally:os._exit(2)
    code=None; stop=None; reason=None; cleanup_end=None; groups={}; signals=[]; reap_at=None
    worker_identity=None; registration_valid=True; owned_unknown=False
    def observe_groups():
        nonlocal registration_valid
        if not ownership.exists():return
        try:
            if ownership.is_symlink() or ownership.stat().st_size>65536:raise ValueError('invalid ownership')
            record=json.loads(ownership.read_bytes())
            if record['worker_pid']!=worker or record['run_id']!=args.run_id:raise ValueError('ownership identity mismatch')
            for row in record['groups']:
                if set(row)!={'pid','pgid','started'} or row['pid']!=row['pgid'] or type(row['pid']) is not int:
                    raise ValueError('invalid owned group')
                if row['pid'] not in groups:
                    if process_identity(row['pid'])==row:groups[row['pid']]={**row,'members':group_members(row['pid'])}
                    elif not group_gone(row['pid']):registration_valid=False
                elif process_identity(row['pid'])==row:
                    groups[row['pid']]['members']=group_members(row['pid'])
        except (OSError,ValueError,KeyError,TypeError):registration_valid=False
    def send_owned(signum):
        for pid,row in list(groups.items()):
            identity={k:row[k] for k in ('pid','pgid','started')}
            if (process_identity(pid)==identity or any(process_identity(m['pid'],False)==m for m in row['members'])):
                try:os.killpg(pid,signum);signals.append({'pid':pid,'signal':int(signum),'outcome':'sent'})
                except ProcessLookupError:pass
    killed=False
    preparation={'status':'pending'}
    try:
        while True:
            now=time.monotonic()
            if worker_identity is None and code is None:worker_identity=process_identity(worker)
            observe_groups()
            if code is None:
                pid,status=os.waitpid(worker,os.WNOHANG)
                if pid:code=os.waitstatus_to_exitcode(status);reap_at=time.monotonic()
            # Identity queries and wait observations can cross a stage boundary.
            now=time.monotonic()
            preparation=assess_preparation(prepared,args.run_id,worker,origin,
                                           origin+30*span/1800,now)
            now=time.monotonic()  # Record reads also consume the absolute budget.
            if stop is None:
                if interrupted[0]:reason='supervisor_signal'
                elif preparation['status']=='late':reason='preprocessing_deadline'
                elif preparation['status']=='invalid':reason='preprocessing_invalid'
                elif now>=origin+1760*span/1800:reason='normal_exit_deadline'
                elif code is not None:reason='worker_exited'
                if reason:
                    stop=now;cleanup_end=cleanup_deadline(stop,origin,span)
                    send_owned(signal.SIGTERM)
                    if code is None:
                        try:os.kill(worker,signal.SIGTERM)
                        except ProcessLookupError:pass
            gone=all(group_gone(pid) for pid in groups)
            if stop is not None:
                if code is not None and gone:break
                remaining=max(0,cleanup_end-stop)
                if now>=stop+remaining*.6 and not killed:
                    killed=True;send_owned(signal.SIGKILL)
                    if code is None:
                        try:os.kill(worker,signal.SIGKILL)
                        except ProcessLookupError:pass
                if now>=cleanup_end:break
            time.sleep(.005)
    finally:
        for signum,handler in previous.items():signal.signal(signum,handler)
    if code is None:
        pid,status=os.waitpid(worker,os.WNOHANG)
        if pid:code=os.waitstatus_to_exitcode(status);reap_at=time.monotonic()
    worker_result_path=worker_dir/'capture/result.json'; worker_result={}
    try:
        if not worker_result_path.is_file() or worker_result_path.stat().st_size>LIMIT:raise ValueError('invalid worker result')
        worker_result=json.loads(worker_result_path.read_bytes())
        if not isinstance(worker_result,dict):worker_result={}
    except (OSError,ValueError):pass
    # A prepared worker can die in the spawn/registration gap: never infer custody.
    owned_unknown=prepared.exists() and not groups and worker_result.get('child_cleanup_status')!='confirmed'
    cleanup=('confirmed' if code is not None and registration_valid and not owned_unknown
             and all(group_gone(pid) for pid in groups) else 'unconfirmed')
    observed_cleanup=time.monotonic()
    if phase_status(observed_cleanup,cleanup_end)!='passed':cleanup='unconfirmed'
    phases={k:'unknown' for k in ('edits','checks','completion')};evidence='unknown';error='phase evidence missing'
    try:
        phase_path=worker_dir/'phase.json'
        if not phase_path.is_file() or phase_path.stat().st_size>65536:raise ValueError('invalid phase result')
        assessment=json.loads(phase_path.read_bytes())
        if assessment['worker_pid']!=worker or assessment['run_id']!=args.run_id:raise ValueError('phase worker mismatch')
        phases,evidence,error=assessment['phases'],assessment['evidence'],assessment['error']
    except (OSError,ValueError,KeyError,TypeError):pass
    process_normal=(preparation['status']=='passed'
                    and worker_result.get('status')=='normal' and code==0 and reason=='worker_exited'
                    and phase_status(reap_at,origin+1760*span/1800)=='passed' and cleanup=='confirmed')
    result={'schema_version':'omc-ad-absolute-result/v1','run_id':args.run_id,
            'status':'normal' if process_normal else 'incomplete_or_failed',
            'process_result':'normal' if process_normal else 'incomplete_or_failed',
            'cleanup_result':cleanup,'capture_result':worker_result.get('raw_capture_complete',False),
            'preprocessing':preparation,
            'phase_timing':phases,'task_evidence':evidence,'evidence_error':error,
            'full_contract_pass':process_normal and evidence=='passed',
            'origin_monotonic':origin,'deadline_monotonic':deadline,'span_seconds':span,
            'stop_monotonic':stop,'stop_reason':reason,'cleanup_deadline_monotonic':cleanup_end,
            'cleanup_observed_monotonic':observed_cleanup,'worker_pid':worker,'worker_identity':worker_identity,
            'worker_returncode':code,'owned_groups':list(groups.values()),'signals_sent':signals,
            'worker_result':str(worker_result_path),'actual_home':'NOT_VERIFIED','comparison':'HOLD',
            'claude_pid':None,'claude_returncode':None,'final_record_status':'unknown'}
    # Late storage or parent death cannot manufacture a successful seal.
    result['final_record_status']=phase_status(time.monotonic(),deadline)
    if result['final_record_status']!='passed':result['full_contract_pass']=False
    with (output/'result.json').open('x') as stream:json.dump(result,stream,indent=2)
    if time.monotonic()>deadline:
        result['final_record_status']='failed';result['full_contract_pass']=False
        (output/'result.json').write_text(json.dumps(result,indent=2))
    seal={'run_id':args.run_id,'result_sha256':digest(output/'result.json'),
          'completed_monotonic':time.monotonic()}
    with (output/'seal.json').open('x') as stream:json.dump(seal,stream)
    return 0 if process_normal and result['final_record_status']=='passed' else 1


def stamp():
    now = datetime.now(timezone.utc)
    return {'utc': now.isoformat(), 'kst': now.astimezone(timezone(timedelta(hours=9))).isoformat(),
            'monotonic': time.monotonic()}


def digest(path):
    if not Path(path).is_file():raise ValueError('regular file required')
    h = hashlib.sha256()
    with path.open('rb') as stream:
        for chunk in iter(lambda: stream.read(CHUNK), b''):
            h.update(chunk)
    return h.hexdigest()


class TerminalParser:
    """Bound one JSON line; continue raw custody even after parser overflow."""
    def __init__(self):
        self.buffer = bytearray()
        self.dropping = False
        self.complete = True
        self.maximum = 0
        self.normal = 0
        self.results = 0

    def feed(self, raw):
        for piece in raw.splitlines(keepends=True):
            # Only LF is a complete JSONL record boundary.
            if not self.dropping:
                if len(self.buffer)+len(piece) > LIMIT:
                    self.complete = False
                    self.dropping = True
                    self.buffer.clear()
                else:
                    self.buffer.extend(piece)
                    self.maximum = max(self.maximum, len(self.buffer))
            if piece.endswith(b'\n'):
                if not self.dropping:
                    try:
                        row = json.loads(self.buffer)
                        if not isinstance(row, dict):
                            raise ValueError('non-object stream event')
                        if row.get('type') == 'result':
                            self.results += 1
                            if row.get('subtype') == 'success' and row.get('is_error') is False:
                                self.normal += 1
                    except (ValueError, UnicodeError, RecursionError):
                        self.complete = False
                self.buffer.clear()
                self.dropping = False

    def finish(self):
        if self.buffer or self.dropping:
            self.complete = False


def owned_path(root, path):
    """Runtime records must stay in the fresh candidate, without symlink hops."""
    absolute = Path(os.path.abspath(path))
    absolute.relative_to(root)
    if any(p.is_symlink() for p in (absolute, *absolute.parents) if p == root or root in p.parents):
        raise ValueError('runtime record symlink')
    return absolute


def read_integer(path):
    try:
        if path.is_symlink() or not path.is_file() or path.stat().st_size > 32:
            return None
        value = int(path.read_bytes())
        return value
    except (OSError, ValueError):
        return None


def group_gone(pid):
    if pid is None or pid <= 1:
        return False
    try:
        os.killpg(pid, 0)  # Observation only, never sends a terminating signal.
    except ProcessLookupError:
        return True
    except OSError:
        return False
    return False


def resolve_executable(token, cwd):
    """Resolve like POSIX exec in the candidate, including relative PATH entries."""
    if os.path.dirname(token):
        return str((cwd/token).resolve())
    for entry in os.get_exec_path():
        candidate = cwd/entry/token
        if candidate.is_file() and os.access(candidate, os.X_OK):
            return str(candidate.resolve())
    return None


def termination_observation(path, run_id, wrapper_pid, child_pid, shell_code):
    """Local identity binding, not authentication against a hostile producer."""
    result = {'status': 'unknown', 'reason': 'termination_record_missing'}
    try:
        if not path.is_file() or path.is_symlink() or path.stat().st_size > 65536:
            return result
        raw = path.read_bytes()
        row = json.loads(raw)
        if (row['schema_version'] != 'omc-native-termination/v1' or row['run_id'] != run_id
                or type(row['writer_pid']) is not int or row['writer_pid'] != wrapper_pid
                or type(row['direct_child_pid']) is not int or child_pid is None
                or row['direct_child_pid'] != child_pid
                or type(row['direct_child_pgid']) is not int or row['direct_child_pgid'] != child_pid
                or row['claude_pid'] is not None or row['claude_returncode'] is not None):
            raise ValueError('termination_record_identity_mismatch')
        code = row['direct_child_returncode']
        if (type(code) is not int or not -64 <= code <= 255
                or row['direct_child_signal'] != (-code if code < 0 else None)
                or (code < 0 and type(row['direct_child_signal']) is not int)
                or any(type(row[k]) is not bool for k in
                       ('wait_completed', 'process_group_absent', 'drain_complete'))
                or not isinstance(row['stop_reason'], str) or not row['stop_reason']
                or not isinstance(row['signals_sent'], list)
                or type(row['recorded_at']) not in (int, float)
                or not math.isfinite(row['recorded_at'])):
            raise ValueError('termination_record_invalid')
        for event in row['signals_sent']:
            if (not isinstance(event, dict) or type(event.get('signal')) is not int
                    or event['signal'] not in (int(signal.SIGTERM), int(signal.SIGKILL))
                    or event.get('scope') not in ('process_group', 'direct_child')
                    or event.get('outcome') not in ('sent', 'absent', 'error')):
                raise ValueError('termination_signal_record_invalid')
        if shell_code is not None and shell_code != (128-code if code < 0 else code):
            raise ValueError('termination_record_exit_mismatch')
        if not row['wait_completed'] or not row['process_group_absent'] or not row['drain_complete']:
            raise ValueError('termination_record_incomplete')
        if not group_gone(child_pid):
            raise ValueError('termination_group_present_or_unknown')
        return {'status': 'verified', 'reason': None, 'record': row,
                'path': str(path), 'sha256': hashlib.sha256(raw).hexdigest()}
    except (OSError, OverflowError, ValueError, KeyError, TypeError, RecursionError) as exc:
        result['reason'] = str(exc)
        return result


def supervise(args, resources):
    if os.name != 'posix':
        raise ValueError('POSIX required')
    if not (math.isfinite(args.timeout) and 0 < args.timeout <= 1800
            and math.isfinite(args.drain) and 0 < args.drain <= 12):
        raise ValueError('invalid deadline')
    cwd = args.cwd.resolve(strict=True)
    if not cwd.is_dir():
        raise ValueError('cwd must be a directory')
    pid_path = owned_path(cwd, args.child_pid_file)
    exit_path = owned_path(cwd, args.child_exit_file)
    if pid_path == exit_path or any(p.exists() or p.is_symlink() for p in (pid_path, exit_path)):
        raise ValueError('child records must be distinct and new')
    if not pid_path.parent.is_dir() or not exit_path.parent.is_dir():
        raise ValueError('record parents must exist')
    termination_path = None
    if args.termination_record is not None or args.termination_run_id is not None:
        if not args.termination_record or not args.termination_run_id or not args.termination_run_id.strip():
            raise ValueError('termination_record_and_run_id_required')
        termination_path = owned_path(cwd, args.termination_record)
        if (termination_path.relative_to(cwd).parts[0] != '.omc'
                or termination_path in (pid_path, exit_path)
                or termination_path.exists() or not termination_path.parent.is_dir()):
            raise ValueError('termination_record_must_be_runtime_distinct_and_new')
    prompt = args.prompt.resolve(strict=True)
    if not prompt.is_file():
        raise ValueError('prompt must be a file')
    argv = args.launch[1:] if args.launch[:1] == ['--'] else args.launch
    if not argv or not argv[0]:
        raise ValueError('explicit launch argv required')
    output = Path(os.path.abspath(args.output))
    if any(p.is_symlink() for p in (output, *output.parents)):
        raise ValueError('output symlink')
    inputs = (pid_path, exit_path, prompt, *([termination_path] if termination_path else []))
    if (output in inputs or any(output in p.parents for p in inputs)
            or termination_path == prompt):
        raise ValueError('output/input collision')
    # Claim once before opening logs; even spawn failures consume this output.
    output.mkdir(mode=0o700)
    # Read the original once. The bounded-memory private spool is both hashed and
    # delivered; a later replacement of prompt_path cannot change this input.
    input_file = resources.enter_context(tempfile.TemporaryFile(mode='w+b'))
    prompt_hash = hashlib.sha256()
    prompt_bytes = 0
    with prompt.open('rb') as source:
        for raw in iter(lambda: source.read(CHUNK), b''):
            input_file.write(raw)
            prompt_hash.update(raw)
            prompt_bytes += len(raw)
    input_file.seek(0)
    requested_argv = list(argv)
    executable = resolve_executable(argv[0], cwd)
    launch_error = None if executable is not None else 'executable_not_on_path'
    argv = [executable, *argv[1:]] if executable is not None else []
    invocation = {'schema_version': 'omc-ad-supervisor-invocation/v1',
                  'source_sha256': digest(Path(__file__)), 'argv': argv,
                  'requested_argv': requested_argv, 'cwd': str(cwd),
                  'prompt_path': str(prompt), 'prompt_sha256': prompt_hash.hexdigest(),
                  'prompt_bytes': prompt_bytes,
                  'timeout_seconds': args.timeout, 'drain_seconds': args.drain,
                  'child_pid_file': str(pid_path), 'child_exit_file': str(exit_path)}
    if termination_path is not None:
        invocation.update(termination_record=str(termination_path), termination_run_id=args.termination_run_id)
    launch_files = {}
    if argv and Path(argv[0]).is_file():
        launch_files[argv[0]] = digest(Path(argv[0]))
    if len(argv) > 1:
        script = cwd/argv[1]
        if script.is_file():
            launch_files[str(script.resolve())] = digest(script)
    invocation['launch_file_sha256'] = launch_files
    (output/'invocation.json').write_text(json.dumps(invocation, ensure_ascii=False, indent=2)+'\n')
    if getattr(args,'prepared_file',None) is not None:
        temporary=args.prepared_file.with_suffix('.tmp')
        with temporary.open('x') as stream:
            json.dump({'run_id':args.run_id,'worker_pid':os.getpid(),
                       'monotonic':time.monotonic()},stream)
        os.replace(temporary,args.prepared_file)
    streams = {}
    parser = TerminalParser()
    proc = None
    started = stamp()
    deadline = started['monotonic']+args.timeout
    stop = None
    kill_at = None
    collection_end = None
    interrupted = 0
    forced = False
    timed_out = False
    storage_errors = []
    termination_errors = []
    wrapper_started = False
    capture_complete = True
    previous = {}
    selector = selectors.DefaultSelector()
    input_buffer = b''
    input_eof = False
    input_sent = 0
    input_hash = hashlib.sha256()
    input_errors = []
    pid = None
    pid_observed_live_group = False

    def signal_handler(signum, _frame):
        nonlocal interrupted
        if not interrupted:
            interrupted = signum

    def request_stop(reason):
        nonlocal stop, kill_at, collection_end
        if stop is None:
            stop = {'reason': reason, **stamp()}
            collection_end = stop['monotonic']+args.drain
            kill_at = stop['monotonic']+min(10, args.drain*.6)
            if proc is not None and proc.poll() is None:
                try:
                    proc.terminate()  # Only the direct wrapper, core owns its children.
                except OSError as exc:
                    termination_errors.append(str(exc))

    def close_pipe(pipe):
        try:
            selector.unregister(pipe)
        except KeyError:
            pass
        pipe.close()

    try:
        for signum in (signal.SIGINT, signal.SIGTERM):
            previous[signum] = signal.signal(signum, signal_handler)
        for name in ('stdout', 'stderr'):
            streams[name] = {'file': (output/(name+'.raw')).open('xb', buffering=0),
                             'bytes': 0, 'hash': hashlib.sha256(), 'eof': False}
        if launch_error is not None:
            raise FileNotFoundError(launch_error)
        environment = {**os.environ, 'OMC_CHILD_EXIT_RECORD': str(exit_path),
                       'OMC_CHILD_PID_RECORD': str(pid_path)}
        proc = subprocess.Popen(argv, cwd=cwd, env=environment, stdin=subprocess.PIPE,
                                stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                start_new_session=True)
        wrapper_started = True
        publish_ownership(args,proc.pid)
        for pipe, name in ((proc.stdout, 'stdout'), (proc.stderr, 'stderr')):
            os.set_blocking(pipe.fileno(), False)
            selector.register(pipe, selectors.EVENT_READ, name)
        os.set_blocking(proc.stdin.fileno(), False)
        selector.register(proc.stdin, selectors.EVENT_WRITE, 'stdin')
        while True:
            now = time.monotonic()
            if pid is None:
                candidate = read_integer(pid_path)
                if candidate is not None and candidate > 1:
                    pid = candidate
                    try:
                        pid_observed_live_group = os.getpgid(pid) == pid
                        if pid_observed_live_group:publish_ownership(args,pid)
                    except OSError:
                        pass
            if interrupted:
                request_stop('supervisor_signal')
            if now >= deadline and stop is None:
                timed_out = True
                request_stop('execution_deadline')
            code = proc.poll()
            if code is not None:
                if all(streams[n]['eof'] for n in streams):
                    break
                request_stop('wrapper_exited_drain')
            if stop is not None and now >= kill_at and code is None and not forced:
                forced = True
                try:
                    proc.kill()
                except OSError as exc:
                    termination_errors.append(str(exc))
            if stop is not None and now >= collection_end:
                capture_complete = all(s['eof'] for s in streams.values())
                break
            remaining = (collection_end if stop is not None else deadline)-now
            for key, _ in selector.select(min(.02, max(0, remaining))):
                if key.data == 'stdin':
                    if stop is not None or code is not None:
                        close_pipe(key.fileobj)
                        continue
                    try:
                        if not input_buffer and not input_eof:
                            input_buffer = input_file.read(CHUNK)
                            input_eof = not input_buffer
                        if input_buffer:
                            written = os.write(key.fd, input_buffer)
                            input_hash.update(input_buffer[:written])
                            input_sent += written
                            input_buffer = input_buffer[written:]
                            if input_sent == prompt_bytes and not input_buffer:
                                input_eof = True
                                close_pipe(key.fileobj)
                        elif input_eof:
                            close_pipe(key.fileobj)
                    except BlockingIOError:
                        pass
                    except BrokenPipeError:
                        input_errors.append('prompt_delivery_broken_pipe')
                        close_pipe(key.fileobj)
                    except OSError as exc:
                        input_errors.append(str(exc))
                        close_pipe(key.fileobj)
                        request_stop('input_error')
                    continue
                try:
                    raw = os.read(key.fd, CHUNK)
                except BlockingIOError:
                    continue
                stream = streams[key.data]
                if not raw:
                    stream['eof'] = True
                    close_pipe(key.fileobj)
                    continue
                if not storage_errors:
                    try:
                        view = memoryview(raw)
                        while view:
                            written = stream['file'].write(view)
                            if not written:
                                raise OSError('short raw write')
                            stream['bytes'] += written
                            stream['hash'].update(view[:written])
                            view = view[written:]
                    except OSError as exc:
                        storage_errors.append(str(exc))
                        capture_complete = False
                        request_stop('storage_error')
                if key.data == 'stdout':
                    parser.feed(raw)
    except OSError as exc:
        if wrapper_started:
            storage_errors.append(str(exc))
        else:
            launch_error = launch_error or str(exc)
        capture_complete = False
        # Failure before the I/O loop must also avoid leaving the wrapper alive.
        if proc is not None and proc.poll() is None:
            request_stop('supervisor_io_error')
            try:
                proc.wait(timeout=min(args.drain*.6, 10))
            except subprocess.TimeoutExpired:
                forced = True
                proc.kill()
    finally:
        if proc is not None and proc.poll() is None:
            forced = True
            try:
                proc.kill()
            except OSError as exc:
                termination_errors.append(str(exc))
        for key in list(selector.get_map().values()):
            close_pipe(key.fileobj)
        selector.close()
        if input_file is not None:
            input_file.close()
        for stream in streams.values():
            stream['file'].close()
        for signum, handler in previous.items():
            signal.signal(signum, handler)
    parser.finish()
    wrapper_code = proc.poll() if proc is not None else None
    child_code = read_integer(exit_path)
    if child_code is not None and not 0 <= child_code <= 255:
        child_code = None
    # PID file is a trusted local recorder, not proof against concurrent tampering.
    # Missing early observation is permitted only for a reaped normal wrapper with
    # an exit record; group absence remains mandatory, never inferred from EOF.
    cleanup = ('confirmed' if wrapper_code is not None and child_code is not None
               and group_gone(pid or read_integer(pid_path))
               else 'incomplete' if pid is not None and not group_gone(pid) else 'unconfirmed')
    observed = None
    effective_child_code = child_code
    if termination_path is not None:
        observed = termination_observation(termination_path, args.termination_run_id,
            proc.pid if proc is not None else None, pid or read_integer(pid_path), child_code)
        cleanup = ('confirmed' if wrapper_code is not None and observed['status'] == 'verified'
                   else 'incomplete' if pid is not None and not group_gone(pid) else 'unconfirmed')
        effective_child_code = (observed['record']['direct_child_returncode']
                                if observed['status'] == 'verified' else None)
    capture_complete = capture_complete and not storage_errors and all(s['eof'] for s in streams.values())
    input_complete = (input_eof and input_sent == prompt_bytes
                      and input_hash.hexdigest() == prompt_hash.hexdigest() and not input_errors)
    normal = (wrapper_code == effective_child_code == 0 and not timed_out and not interrupted and not forced
              and parser.normal == parser.results == 1 and parser.complete
              and capture_complete and input_complete and cleanup == 'confirmed' and not termination_errors)
    if observed is not None and observed['status'] == 'verified':
        normal = normal and observed['record']['stop_reason'] == 'normal'
    result = {'schema_version': 'omc-ad-supervisor-result/v1', 'status': 'normal' if normal else 'incomplete_or_failed',
              'wrapper_started': wrapper_started, 'attempt_consumed': wrapper_started,
              'launch_error': launch_error,
              'wrapper_pid': proc.pid if proc is not None else None, 'wrapper_exit_code': wrapper_code,
              'child_pid': pid or read_integer(pid_path), 'child_pid_observed_live_group': pid_observed_live_group,
              'child_exit_code': child_code, 'internal_124': child_code == 124,
              'external_timeout': timed_out, 'interrupted': interrupted,
              'forced_termination': forced, 'normal_terminal_result_count': parser.normal,
              'terminal_result_count': parser.results, 'parse_complete': parser.complete,
              'input': {'complete': input_complete, 'eof_sent': input_eof,
                        'bytes_sent': input_sent, 'expected_bytes': prompt_bytes,
                        'sha256_sent': input_hash.hexdigest(),
                        'sha256_expected': prompt_hash.hexdigest(), 'errors': input_errors},
              'parser_max_buffer_bytes': parser.maximum, 'raw_capture_complete': capture_complete,
              'child_cleanup_status': cleanup, 'storage_errors': storage_errors,
              'termination_errors': termination_errors, 'start': started,
              'execution_deadline_monotonic': deadline, 'stop': stop, 'end': stamp(),
              'collection_deadline_monotonic': collection_end,
              'streams': {n: {'bytes': s['bytes'], 'sha256': s['hash'].hexdigest(), 'eof': s['eof']}
                          for n, s in streams.items()}}
    if observed is not None:
        result['termination_observation'] = observed
    try:
        with (output/'result.json').open('x') as stream:
            json.dump(result, stream, ensure_ascii=False, indent=2)
            stream.write('\n')
    except OSError as exc:
        print('result_record_failed: '+str(exc), file=sys.stderr)
        return 1
    return 0 if normal else 1 if wrapper_started else 2


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--cwd', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--prompt', type=Path, required=True)
    parser.add_argument('--child-pid-file', type=Path, required=True)
    parser.add_argument('--child-exit-file', type=Path, required=True)
    parser.add_argument('--timeout', type=float, default=1800)
    parser.add_argument('--drain', type=float, default=12)
    parser.add_argument('--termination-record', type=Path)
    parser.add_argument('--termination-run-id')
    parser.add_argument('--origin-monotonic', type=float)
    parser.add_argument('--deadline-monotonic', type=float)
    parser.add_argument('--run-id')
    parser.add_argument('--phase-evidence', type=Path)
    parser.add_argument('launch', nargs=argparse.REMAINDER)
    args = parser.parse_args()
    try:
        if args.origin_monotonic is not None or args.deadline_monotonic is not None:
            if args.origin_monotonic is None or args.deadline_monotonic is None:
                raise ValueError('origin_and_deadline_required')
            return supervise_absolute(args)
        with ExitStack() as resources:
            return supervise(args, resources)
    except (ValueError, OSError) as exc:
        print('supervisor_input_or_setup_error: '+str(exc), file=sys.stderr)
        return 2


if __name__ == '__main__':
    raise SystemExit(main())
