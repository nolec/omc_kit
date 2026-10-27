#!/usr/bin/env python3
"""Bounded, single-invocation integration finalization. No provider/model calls."""
import argparse
import hashlib
import json
import math
import os
from pathlib import Path
import signal
import subprocess
import sys
import time

FILE_LIMIT = 1024 * 1024
TOTAL_LIMIT = 8 * FILE_LIMIT


class Clock:
    def now(self):
        return time.monotonic()

    def phase(self, name):
        pass


def digest(data):
    return hashlib.sha256(data).hexdigest()


def write_json(path, value):
    """A new path only; caller publishes the final envelope separately."""
    with path.open('x', encoding='utf-8') as stream:
        json.dump(value, stream, ensure_ascii=False, indent=2, allow_nan=False)
        stream.flush()
        os.fsync(stream.fileno())



def write_buffer(path, data):
    """Copy exactly the bytes used for the source digest; never reread to pin."""
    with path.open('xb') as stream:
        if stream.write(data) != len(data):
            raise OSError('short frozen-buffer write: ' + str(path))
        stream.flush()
        os.fsync(stream.fileno())


def inside(root, name):
    p = root / name
    resolved = p.resolve(strict=True)
    if not resolved.is_relative_to(root) or p.is_symlink() or not resolved.is_file():
        raise ValueError('external, linked or missing input: ' + str(p))
    return resolved


def bounded_read(path, limit=TOTAL_LIMIT):
    with path.open('rb') as stream:
        data = stream.read(limit + 1)
    if len(data) > limit:
        raise ValueError('input exceeds byte budget: ' + str(path))
    return data


def group_gone(pgid):
    try:
        os.killpg(pgid, 0)
    except ProcessLookupError:
        return True
    except OSError:
        return False
    return False


def process(argv, target, out, label, deadline, cleanup_deadline, clock, cleanup_seconds=None):
    row = {'status':'failed', 'returncode':None, 'cleanup':'confirmed', 'signals':[], 'errors':[]}
    proc = None
    with (out/'raw'/f'{label}.stdout').open('xb') as stdout, (out/'raw'/f'{label}.stderr').open('xb') as stderr:
        try:
            remaining = deadline-clock.now()
            if remaining <= 0:
                row['status'] = 'deadline_exceeded'
                return row
            proc = subprocess.Popen(argv, cwd=target, stdout=stdout, stderr=stderr, start_new_session=True)
            row.update(pid=proc.pid, pgid=proc.pid, started=clock.now())
            clock.phase(label+'_spawned')
            try:
                proc.wait(timeout=max(.001, deadline-clock.now()))
                row['status'] = 'passed' if proc.returncode == 0 else 'failed'
            except subprocess.TimeoutExpired:
                row['status'] = 'timeout'
        except OSError as exc:
            row['errors'].append(repr(exc))
        finally:
            if proc is not None:
                if cleanup_seconds is not None:
                    cleanup_deadline = min(cleanup_deadline, clock.now()+cleanup_seconds)
                # The group may outlive a successfully returned group leader.
                if not group_gone(proc.pid):
                    for sig, allowance in [(signal.SIGTERM, 5), (signal.SIGKILL, None)]:
                        if clock.now() >= cleanup_deadline:
                            break
                        try:
                            os.killpg(proc.pid, sig); row['signals'].append(int(sig))
                        except ProcessLookupError:
                            break
                        except OSError as exc:
                            row['errors'].append(repr(exc))
                        until = min(cleanup_deadline, clock.now()+min(allowance, max(0, (cleanup_deadline-clock.now())/2))) if allowance else cleanup_deadline
                        while clock.now() < until:
                            proc.poll()
                            if group_gone(proc.pid):
                                break
                            time.sleep(min(.01, max(0, until-clock.now())))
                        if group_gone(proc.pid):
                            break
                proc.poll()
                row.update(returncode=proc.returncode, cleanup='confirmed' if proc.returncode is not None and group_gone(proc.pid) else 'unconfirmed')
    row['ended'] = clock.now()
    return row


def guard_passed(data):
    text = data.decode('utf-8', errors='replace')
    try:
        marker = text.split('<!-- OMC_OUTPUT: ', 1)[1].split(' -->', 1)[0]
        return (text.startswith('필수 검증 통과·작업 완료\n') and
                json.loads(marker)['outcome'] == 'done' and text.rstrip().endswith('VERDICT: PROCEED'))
    except (IndexError, KeyError, ValueError):
        return False


def partial_copy(out, clock, deadline):
    rows, budget = [], TOTAL_LIMIT
    for source in sorted((out/'raw').iterdir()):
        if clock.now() >= deadline or budget <= 0:
            break
        if source.is_symlink() or not source.is_file():
            continue
        observed = source.stat().st_size
        limit = min(observed, FILE_LIMIT, budget)
        dest = out/'partial'/source.name
        data = bytearray()
        # Never follow growth or wait for EOF from a still-running writer.
        with source.open('rb') as inp, dest.open('xb') as target:
            while len(data) < limit and clock.now() < deadline:
                chunk = inp.read(min(65536, limit-len(data)))
                if not chunk:
                    break
                target.write(chunk); data.extend(chunk)
        budget -= len(data)
        rows.append({'path':str(dest.relative_to(out)), 'source':str(source), 'observed_size':observed,
                     'size':len(data), 'sha256':digest(data), 'captured':clock.now(), 'status':'partial'})
    return rows


def consume(out):
    """Only a completed publication with intact evidence is authoritative."""
    try:
        if (out/'pending').exists():
            raise ValueError('publication pending')
        raw = bounded_read(out/'final.json')
        value = json.loads(raw); pub = json.loads(bounded_read(out/'publication.json'))
        if pub['final_sha256'] != digest(raw) or pub['run_id'] != value['run_id']:
            raise ValueError('publication mismatch')
        times = [value['completed'], pub['completed'], value['record_deadline']]
        if any(type(x) not in (float, int) or not math.isfinite(x) for x in times):
            raise ValueError('invalid publication time')
        if not value['completed'] <= pub['completed'] <= value['record_deadline']:
            raise ValueError('publication late')
        for row in value['files']:
            data = bounded_read(inside(out, row['path']))
            if len(data) != row['size'] or digest(data) != row['sha256']:
                raise ValueError('evidence mismatch')
        if not value['overall_complete'] or not value['archive_complete']:
            raise ValueError('work incomplete')
        return {'status':'complete', 'result':value}
    except (OSError, ValueError, KeyError, TypeError) as exc:
        return {'status':'incomplete', 'reason':str(exc)}


def execute(config, out, clock):
    origin = clock.now()
    target = Path(config['target']).resolve(strict=True)
    # Independent Git directory, not a worktree sharing the protected index.
    if not (target/'.git').is_dir() or (target/'.git').is_symlink():
        raise ValueError('independent candidate Git directory required')
    protected = [Path(__file__).resolve().parent.parent] + [Path(p).resolve() for p in config['protected_roots']]
    if any(target == p or target.is_relative_to(p) or p.is_relative_to(target) for p in protected):
        raise ValueError('target must be separate from protected roots')
    sid = config['session_id']
    if not isinstance(sid, str) or not sid or any(c not in 'abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-_' for c in sid):
        raise ValueError('invalid session identifier')
    session_path = inside(target, f'.omc/state/sessions/{sid}/session.json')
    session_raw = bounded_read(session_path)
    session = json.loads(session_raw)
    if session['session_id'] != sid or session['confirmation']['status'] != 'confirmed':
        raise ValueError('target session not confirmed')
    verification_path = inside(target, config['verification_file'])
    argv = config['test_argv']
    if not isinstance(argv, list) or not argv or any(not isinstance(x,str) or not x for x in argv):
        raise ValueError('test_argv must be a nonempty argument list; shell is not used')
    duration, cleanup = config['test_timeout_seconds'], config['cleanup_seconds']
    if any(type(x) not in (int,float) or not math.isfinite(x) or x <= 0 for x in [duration,cleanup]) or duration>900 or cleanup>60:
        raise ValueError('invalid bounded execution budget')
    out = out.absolute()
    if not out.resolve().is_relative_to(target/'.omc'):
        raise ValueError('output must be inside candidate .omc')
    if out.is_symlink() or any(out == p or p.is_relative_to(out) for p in protected):
        raise ValueError('output cannot contain protected roots')
    out.mkdir(exist_ok=False, parents=True)
    for name in ['raw','frozen','partial']:
        (out/name).mkdir()
    (out/'pending').touch(exist_ok=False)
    result = {'schema':'omc-integration-final/v1','run_id':out.name,'target':str(target),'session_id':sid,
              'overall_complete':False,'archive_complete':False,'raw_final_sha256':None,
              'guard':{'status':'not_called'},'partial_evidence':[],'files':[],'origin':origin}
    write_json(out/'invocation.json', {'config':config,'origin':origin,'target':str(target),'session_id':sid})
    deadline = min(clock.now()+duration, origin+1500)
    # Cleanup is shared, including early stop; process computes no renewed budget.
    test = process(argv,target,out,'test',deadline,min(deadline+cleanup,origin+1560),clock,cleanup_seconds=cleanup)
    result['test'] = test
    cleanup_end = clock.now(); record_deadline = min(cleanup_end+60,origin+1620)
    result['record_deadline'] = record_deadline
    write_json(out/'minimum.json', {'test':test,'guard':'not_called','record_deadline':record_deadline})
    try:
        if test['cleanup'] != 'confirmed':
            result['guard']['status'] = 'writer_unconfirmed'
        elif test['status'] != 'passed':
            result['guard']['status'] = 'test_not_passed'
        elif bounded_read(session_path) != session_raw:
            result['guard']['status'] = 'target_session_changed'
        else:
            verification_raw = bounded_read(verification_path)
            verification = json.loads(verification_raw)
            if verification['session_id'] != sid:
                raise ValueError('verification session mismatch')
            frozen = out/'frozen/verification.json'
            write_buffer(frozen, verification_raw)
            buffers = [(verification_path, frozen, verification_raw)]
            for i,name in enumerate(config.get('evidence_files',[])):
                src = inside(target,name)
                if any(src == existing for existing,_,_ in buffers):
                    raise ValueError('duplicate input source: ' + str(src))
                dst=out/'frozen'/f'evidence-{i}'
                data=bounded_read(src); write_buffer(dst,data); buffers.append((src,dst,data))
            frozen_hashes = {str(dst):digest(data) for _,dst,data in buffers}
            source_hashes = {str(src):digest(data) for src,_,data in buffers}
            result['guard_input_sha256'] = frozen_hashes[str(frozen)]
            result['input_sources'] = source_hashes
            clock.phase('frozen')
            remaining = record_deadline-clock.now()
            if any(digest(bounded_read(Path(p)))!=h for p,h in {**source_hashes,**frozen_hashes}.items()):
                result['guard']['status']='evidence_changed'
            elif remaining <= 50:
                result['guard']['status'] = 'deadline_exceeded' if remaining <= 0 else 'insufficient_remaining_budget'
            else:
                guard_deadline = record_deadline-30
                guard_script = inside(target, 'scripts/omc_pipeline_guard.py')
                # Recheck immediately before invocation; never infer another session.
                if bounded_read(session_path) != session_raw:
                    result['guard']['status']='target_session_changed'
                else:
                    clock.phase('guard_started')
                    # Admission is checked again at the actual launch boundary.
                    if any(digest(bounded_read(Path(p)))!=h for p,h in {**source_hashes,**frozen_hashes}.items()):
                        result['guard']['status']='evidence_changed'
                        guard=None
                    elif record_deadline-clock.now() <= 50:
                        result['guard']['status']='insufficient_remaining_budget' if clock.now()<record_deadline else 'deadline_exceeded'
                        guard=None
                    else:
                        guard = process([sys.executable,str(guard_script),'complete-task','--verification-file',str(frozen)],target,out,'guard',guard_deadline,record_deadline-20,clock)
                    if guard is not None:
                        result['guard']=guard
                    guard=result['guard']
                    clock.phase('guard_returned')
                    if guard['status']=='passed':
                        if clock.now()>guard_deadline:guard['status']='late_return'
                        elif not guard_passed(bounded_read(out/'raw/guard.stdout')):guard['status']='invalid_result'
                    if any(digest(bounded_read(Path(p)))!=h for p,h in {**source_hashes,**frozen_hashes}.items()):
                        guard['status']='evidence_changed'
        unconfirmed = test['cleanup']!='confirmed' or result['guard'].get('cleanup','confirmed')!='confirmed'
        if unconfirmed:
            result['partial_evidence']=partial_copy(out,clock,record_deadline)
        else:
            raw_hashes={}
            for file in sorted(list((out/'raw').iterdir())+list((out/'frozen').iterdir())+[out/'minimum.json',out/'invocation.json']):
                if clock.now()>=record_deadline:break
                data=bounded_read(file);row={'path':str(file.relative_to(out)),'size':len(data),'sha256':digest(data)}
                result['files'].append(row)
                if file.parent.name=='raw':raw_hashes[row['path']]=row['sha256']
            result['raw_final_sha256']=raw_hashes
            expected=len(list((out/'raw').iterdir()))+len(list((out/'frozen').iterdir()))+2
            result['archive_complete']=len(result['files'])==expected and clock.now()<record_deadline
        result['overall_complete']=(test['status']=='passed' and result['guard']['status']=='passed' and
                                    not unconfirmed and result['archive_complete'])
    except (OSError,ValueError,KeyError,TypeError) as exc:
        result['error']=repr(exc);result['overall_complete']=False;result['archive_complete']=False
        print(json.dumps({'status':'incomplete','error':repr(exc),'raw_location':str(out/'raw')}),flush=True)
    result['completed']=clock.now()
    result['overall_complete'] &= result['completed']<=record_deadline
    result['archive_complete'] &= result['completed']<=record_deadline
    try:
        write_json(out/'final.tmp',result)
        os.replace(out/'final.tmp',out/'final.json')
        clock.phase('published')
        completed=clock.now()
        if completed>record_deadline:
            raise TimeoutError('final publication exceeded record deadline')
        write_json(out/'publication.json',{'run_id':out.name,'final_sha256':digest(bounded_read(out/'final.json')),'completed':completed})
        if clock.now()>record_deadline:
            raise TimeoutError('publication close exceeded record deadline')
        (out/'pending').unlink()
    except (OSError,ValueError) as exc:
        print(json.dumps({'status':'incomplete','error':repr(exc),'raw_location':str(out/'raw')}),flush=True)
        return 1
    answer=consume(out);print(json.dumps(answer,ensure_ascii=False),flush=True)
    return 0 if answer['status']=='complete' else 1


def main(argv=None, clock=None):
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--config',type=Path);parser.add_argument('--output',type=Path)
    parser.add_argument('--read-result',type=Path)
    args=parser.parse_args(argv)
    if args.read_result:
        result=consume(args.read_result.resolve());print(json.dumps(result,ensure_ascii=False))
        return 0 if result['status']=='complete' else 1
    if not args.config or not args.output:parser.error('--config and --output required')
    try:
        return execute(json.loads(bounded_read(args.config)),args.output,clock or Clock())
    except (OSError,ValueError,KeyError,TypeError) as exc:
        print(json.dumps({'status':'incomplete','error':repr(exc),'raw_location':str(args.output/'raw')},ensure_ascii=False))
        return 1


if __name__=='__main__':
    sys.exit(main())
