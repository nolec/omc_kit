#!/usr/bin/env python3
"""Candidate-bound home motion evidence from a completed native event prefix.

Local custody, not hostile-tamper resistance or proof of visual quality. The
runner publishes before the final result; the completion gate is read-only.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
from pathlib import Path
import shlex
import subprocess
import sys
import tempfile
import os
import signal
import threading
import time

REQUIREMENT = '.omc/home-motion-requirement.json'
SCHEMA = 'omc-home-motion-observation/v1'


def sha(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def local(root: Path, name: str) -> Path:
    if not isinstance(name, str) or not name or Path(name).is_absolute() or '..' in Path(name).parts:
        raise ValueError('home_path_invalid')
    path = root/name
    path.resolve().relative_to(root.resolve())
    # Do not follow a replaced receipt or stream through internal symlinks either.
    if any(p.is_symlink() for p in [path, *path.parents] if p != root.parent and (p == root or root in p.parents)):
        raise ValueError('home_path_symlink')
    if not Path(name).parts or Path(name).parts[0] != '.omc':
        raise ValueError('home_path_must_be_runtime')
    return path


def requirement(root: Path) -> dict:
    value = json.loads(local(root, REQUIREMENT).read_bytes())
    if not isinstance(value, dict) or value.get('schema_version') != 'omc-home-motion-requirement/v1':
        raise ValueError('home_requirement_invalid')
    for key in ('session_id', 'run_id', 'url', 'preflight_sha256'):
        if not isinstance(value.get(key), str) or not value[key].strip():
            raise ValueError('home_requirement_invalid')
    from urllib.parse import urlparse
    url = urlparse(value['url'])
    if url.scheme != 'http' or url.hostname not in {'localhost', '127.0.0.1', '::1'} or url.username or url.password or url.fragment:
        raise ValueError('home_url_invalid')
    for key in ('events_path', 'receipt_path', 'preflight_path'):
        local(root, value.get(key))
    if len({value['events_path'], value['receipt_path'], value['preflight_path'], REQUIREMENT}) != 4:
        raise ValueError('home_paths_collide')
    return value


def enforce_requirement(root: Path, basis: dict) -> dict | None:
    requested = [c for c in basis.get('checks', []) if 'home_motion_sha256' in c]
    path = root/REQUIREMENT
    if not requested and not path.exists() and not path.is_symlink():
        return None
    req = requirement(root)
    if (len(requested) != 1 or requested[0].get('id') != 'browser-home'
            or requested[0]['home_motion_sha256'] != sha(local(root, REQUIREMENT).read_bytes())
            or basis.get('session_id') != req['session_id'] or basis.get('run_id') != req['run_id']):
        raise ValueError('home_requirement_binding_mismatch')
    return req


def finite_number(value) -> bool:
    try:
        return type(value) in (float, int) and math.isfinite(value)
    except OverflowError:
        return False


def sample_assessment(value: dict) -> dict:
    # Legacy observations omit these fields; their six-sample checks still apply.
    readiness = value.get('readiness')
    if isinstance(readiness, dict) and readiness.get('status') == 'timeout':
        return {'status': 'unknown', 'reason': 'home_ad_not_ready'}
    collection = value.get('collection')
    if isinstance(collection, dict) and collection.get('status') == 'interrupted':
        return {'status': 'unknown', 'reason': 'home_track_replaced'}
    tracks = value.get('tracks')
    if not isinstance(tracks, list) or not tracks:
        return {'status': 'unknown', 'reason': 'home_samples_missing'}
    valid = False
    ids = set()
    for track in tracks:
        if not isinstance(track, dict):
            return {'status': 'unknown', 'reason': 'home_samples_invalid'}
        ident = (track.get('card_id'), track.get('track_id'))
        if any(not isinstance(i, str) or not i for i in ident) or ident in ids:
            return {'status': 'unknown', 'reason': 'home_track_invalid'}
        ids.add(ident)
        samples = track.get('samples')
        if not isinstance(samples, list) or len(samples) != 6:
            continue
        times, states = [], []
        for sample in samples:
            if not isinstance(sample, dict):
                break
            t = sample.get('elapsed_ms')
            state = sample.get('transform')
            if (not finite_number(t) or t < 0
                    or sample.get('connected') is not True
                    or type(sample.get('images_loaded')) is not int or sample['images_loaded'] < 2
                    or not isinstance(state, str) or not state.strip()):
                break
            times.append(t)
            states.append(state)
        if len(times) != 6 or any(b-a < 800 for a, b in zip(times, times[1:])) or times[-1]-times[0] < 4000:
            continue
        valid = True
        if len(set(states)) > 1:
            return {'status': 'passed', 'reason': None}
    return {'status': 'unknown', 'reason': 'home_motion_not_observed' if valid else 'home_samples_invalid'}


def observe(root: Path) -> dict:
    from omc_workflow_preflight import browser_deadline
    started=time.monotonic()
    with browser_deadline(started+120):
        return _observe(root,started)


def _observe(root,started):
    """Same query and server-binding machinery as operator preparation, new output."""
    from omc_pipeline_guard import _code_snapshot_digest
    from omc_workflow_preflight import browser_observation, browser_work_deadline, server_binding
    with browser_work_deadline(started):
        req = requirement(root)
        raw = local(root, req['preflight_path']).read_bytes()
        if sha(raw) != req['preflight_sha256']:
            raise ValueError('home_preflight_digest_mismatch')
        ready = json.loads(raw)
        if ready.get('status') != 'ready' or ready.get('target') != str(root):
            raise ValueError('home_preflight_binding_mismatch')
        binding = ready['server_binding']
        browser = binding['browser']
        if browser['origin']+browser['home_path'] != req['url']:
            raise ValueError('home_preflight_url_mismatch')
        snapshot = _code_snapshot_digest(root)
    if 'browser_evidence_contract' in req:
        from omc_workflow_preflight import browser_contract
        browser_contract(req['browser_evidence_contract'])
        observed=browser_observation(browser,root=root,started=started)
    else:
        observed=browser_observation(browser)
    with browser_work_deadline(started):
        server_binding(root, binding['config'], browser, observed=observed,
                       previous=binding['evidence'], initial=False)
        if snapshot != _code_snapshot_digest(root):
            raise ValueError('home_candidate_changed_during_observation')
    return {**observed, 'schema_version': SCHEMA, 'session_id': req['session_id'],
            'run_id': req['run_id'], 'code_snapshot_sha256': snapshot, 'server_binding': 'passed'}


def _is_observe(command: str, root: Path) -> bool:
    from omc_native_command import single_argv
    tokens = single_argv(command)
    if len(tokens) != 5 or Path(tokens[0]).name not in {'python', 'python3', Path(sys.executable).name}:
        return False
    return (tokens[2:4] == ['observe', '--target']
            and (root/tokens[1]).resolve() == (root/'scripts/omc_home_observation.py').resolve()
            and (root/tokens[4]).resolve() == root.resolve())


def _extract_all(root: Path, raw: bytes, req: dict) -> list[dict]:
    """Consume complete rows and retain each successful observation separately."""
    receipts = []
    calls = {}
    seen_results = set()
    offset = 0
    initialized = False
    for line in raw.splitlines(keepends=True):
        if not line.endswith(b'\n'):
            break
        offset += len(line)
        try:
            row = json.loads(line)
            if not isinstance(row, dict):
                return []
            if row.get('type') == 'system' and row.get('subtype') == 'init':
                if initialized:
                    return []
                initialized = True
            message = row.get('message', {})
            blocks = message.get('content', [])
            if not isinstance(blocks, list):
                return []
            for block in blocks:
                if not isinstance(block, dict):
                    return []
                kind = block.get('type')
                if kind not in {'tool_use', 'tool_result'}:
                    continue
                if not initialized:
                    return []
                if kind == 'tool_use':
                    ident = block.get('id')
                    if row.get('type') != 'assistant' or not isinstance(ident, str) or not ident or ident in calls:
                        return []
                    calls[ident] = block
                else:
                    ident = block.get('tool_use_id')
                    if row.get('type') != 'user' or ident in seen_results or ident not in calls:
                        return []
                    seen_results.add(ident)
                    call = calls[ident]
                    if call.get('name') != 'Bash' or not _is_observe(call.get('input', {}).get('command'), root):
                        continue
                    if block.get('is_error', False) is not False:
                        continue
                    content = block.get('content')
                    if isinstance(content, list):
                        content = ''.join(b['text'] for b in content if b.get('type') == 'text')
                    try:
                        value = json.loads(content)
                    except (ValueError, TypeError):
                        continue
                    if not isinstance(value, dict) or value.get('schema_version') != SCHEMA:
                        continue
                    receipts.append({'schema_version': 'omc-home-motion-receipt/v1',
                            'requirement_sha256': sha(local(root, REQUIREMENT).read_bytes()),
                            'call_id': ident, 'prefix_bytes': offset, 'prefix_sha256': sha(raw[:offset]),
                            'observation': value})
        except (ValueError, TypeError, KeyError, AttributeError):
            return []
    return receipts


def receipt_file(root: Path, req: dict, receipt: dict) -> Path:
    key = sha(json.dumps([receipt['call_id'], receipt['prefix_sha256']], separators=(',', ':')).encode())
    return local(root, req['receipt_path'] + '.' + key + '.json')


def receipt_references(root: Path) -> list[dict]:
    """List immutable choices; do not automatically select the first or latest."""
    req = requirement(root)
    base = local(root, req['receipt_path'])
    refs = []
    for candidate in sorted(base.parent.glob(base.name + '.*.json')):
        path = local(root, str(candidate.relative_to(root)))
        raw = path.read_bytes()
        receipt = json.loads(raw)
        if path != receipt_file(root, req, receipt):
            raise ValueError('home_receipt_path_mismatch')
        refs.append({'path': str(path.relative_to(root)), 'sha256': sha(raw),
                     'call_id': receipt['call_id'], 'prefix_bytes': receipt['prefix_bytes'],
                     'code_snapshot_sha256': receipt['observation']['code_snapshot_sha256']})
    return refs


def publish(root: Path) -> bool:
    """Atomically publish every completed call, never replacing earlier evidence."""
    req = requirement(root)
    receipts = _extract_all(root, local(root, req['events_path']).read_bytes(), req)
    published = False
    for receipt in receipts:
        path = receipt_file(root, req, receipt)
        if path.exists():
            continue
        path.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=path.parent, prefix='.publish-')
        try:
            with os.fdopen(fd, 'w') as stream:
                json.dump(receipt, stream, ensure_ascii=False)
                stream.flush()
                os.fsync(stream.fileno())
            try:
                os.link(tmp, path)
                published = True
            except FileExistsError:
                pass
        finally:
            os.unlink(tmp)
    return published


def assess_completion_home(root: Path, basis: dict, events_file: Path | None = None,
                           submission: dict | None = None) -> dict | None:
    try:
        req = enforce_requirement(root, basis)
        if req is None:
            return None
        if submission is None:
            return {'status': 'unknown', 'reason': 'home_observation_selection_required'}
        if not isinstance(submission, dict):
            raise ValueError('home_selection_invalid')
        receipt_path = local(root, submission['path'])
        if not receipt_path.is_file():
            return {'status': 'unknown', 'reason': 'home_native_receipt_missing'}
        receipt_raw = receipt_path.read_bytes()
        if sha(receipt_raw) != submission.get('sha256'):
            raise ValueError('home_selected_receipt_digest_mismatch')
        receipt = json.loads(receipt_raw)
        if receipt_path != receipt_file(root, req, receipt):
            raise ValueError('home_selected_receipt_path_mismatch')
        raw = local(root, req['events_path']).read_bytes()
        size = receipt.get('prefix_bytes')
        if type(size) is not int or size <= 0 or size > len(raw):
            raise ValueError('home_prefix_mismatch')
        extracted = _extract_all(root, raw[:size], req)
        match = next((item for item in extracted if item['call_id'] == receipt['call_id']
                      and item['prefix_bytes'] == size), None)
        if receipt != match:
            raise ValueError('home_prefix_mismatch')
        if events_file is not None and events_file.read_bytes()[:size] != raw[:size]:
            raise ValueError('home_collected_prefix_mismatch')
        value = receipt['observation']
        from omc_pipeline_guard import _code_snapshot_digest
        expected = {'session_id': req['session_id'], 'run_id': req['run_id'], 'url': req['url'],
                    'code_snapshot_sha256': _code_snapshot_digest(root)}
        if any(value.get(key) != val for key, val in expected.items()):
            raise ValueError('home_observation_binding_mismatch')
        if value.get('server_binding') != 'passed':
            return {'status': 'unknown', 'reason': 'home_server_binding_unverified'}
        if 'browser_evidence_contract' in req:
            from omc_workflow_preflight import browser_contract, read_browser_execution
            browser_contract(req['browser_evidence_contract'])
            read_browser_execution(root,value.get('browser_execution'),expected_ops=['evaluate'],observation=value)
        return sample_assessment(value)
    except FileNotFoundError:
        return {'status': 'unknown', 'reason': 'home_evidence_missing'}
    except (OSError, ValueError, KeyError, TypeError, AttributeError) as exc:
        return {'status': 'mismatch', 'reason': str(exc)}


def initialize(root: Path, preflight: str, run_id: str, session_id: str) -> dict:
    """Pin an already-ready preparation once, then approve the emitted check pin."""
    raw = local(root, preflight).read_bytes()
    ready = json.loads(raw)
    if (ready.get('status') != 'ready' or ready.get('target') != str(root)
            or 'browser-home' not in ready.get('required_check_ids', [])):
        raise ValueError('home_preflight_binding_mismatch')
    browser = ready['server_binding']['browser']
    if not run_id or not session_id:
        raise ValueError('home_run_and_session_required')
    req = {'schema_version': 'omc-home-motion-requirement/v1', 'run_id': run_id,
           'session_id': session_id, 'url': browser['origin']+browser['home_path'],
           'preflight_path': preflight, 'preflight_sha256': sha(raw),
           'events_path': '.omc/home-motion/events.jsonl',
           'receipt_path': '.omc/home-motion/receipt.json'}
    from omc_workflow_preflight import BROWSER_CONTRACT
    req['browser_evidence_contract'] = BROWSER_CONTRACT
    path = local(root, REQUIREMENT)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open('x') as stream:
        json.dump(req, stream, ensure_ascii=False, indent=2)
    requirement(root)
    return {'run_id': run_id, 'session_id': session_id, 'check_id': 'browser-home',
            'home_motion_sha256': sha(path.read_bytes()),
            'observation_command': 'python3 scripts/omc_home_observation.py observe --target .',
            'receipt_listing_command': 'python3 scripts/omc_home_observation.py receipts --target .',
            'verification_field': 'home_motion_result'}


class NativePublisherFailure(ValueError):
    """The native stream remains raw; diagnostics have their own immutable file."""


class NativeRunInterrupted(BaseException):
    def __init__(self, signum):
        self.signum = signum
        super().__init__('native_interrupted:'+str(signum))


def _stop_native(proc, *, immediate=False, signals_sent=None):
    """Bound both grace and final drain, even if an escaped process holds stdout."""
    errors = []
    def stop(hard):
        event = {'signal': int(signal.SIGKILL if hard else signal.SIGTERM),
                 'scope': 'process_group' if os.name == 'posix' else 'direct_child'}
        try:
            if os.name == 'posix':
                # Popen created this session; never signal the caller's group.
                os.killpg(proc.pid, signal.SIGKILL if hard else signal.SIGTERM)
            elif hard:
                proc.kill()
            else:
                proc.terminate()
            event['outcome'] = 'sent'
        except ProcessLookupError:
            event['outcome'] = 'absent'
        except OSError as exc:
            event['outcome'] = 'error'
            errors.append(type(exc).__name__+': '+str(exc))
        if signals_sent is not None:
            signals_sent.append(event)

    stop(immediate)
    complete = True
    try:
        tail, _ = proc.communicate(timeout=1 if immediate else 10)
    except subprocess.TimeoutExpired:
        stop(True)
        try:
            tail, _ = proc.communicate(timeout=1)
        except subprocess.TimeoutExpired as exc:
            tail = exc.output or b''
            complete = False
            # No further wait for EOF from a process outside our owned group.
            if os.name == 'posix':
                proc.stdout.close()
    try:
        proc.wait(timeout=1)
    except subprocess.TimeoutExpired:
        complete = False
    return tail, complete, errors


def run_native(root: Path, argv: list[str]) -> int:
    """Tee a single native invocation; publish tool results while it is running."""
    req = requirement(root)
    from omc_pipeline_guard import _load_approved_basis
    enforce_requirement(root, _load_approved_basis(root, req['session_id']))
    events = local(root, req['events_path'])
    events.parent.mkdir(parents=True, exist_ok=True)
    if receipt_references(root):
        raise ValueError('home_receipt_already_exists')
    from omc_native_red_evidence import policy, publish as publish_red, execution_context, verify_execution_context
    context = execution_context(root)
    pin = policy(root)
    if pin is not None and local(root, pin['events_path']) != events:
        raise ValueError('native_red_home_stream_mismatch')
    def publish_home():
        verify_execution_context(root, context)
        publish(root)
    publishers = [publish_home]
    if pin is not None:
        publishers.append(lambda: publish_red(root, context))
    return tee_native(root, argv, events, publishers, context=context)


def tee_native(root: Path, argv: list[str], events: Path, publishers: list, *, context=None,
               termination_record=None, termination_run_id=None) -> int:
    """Shared complete-line custody for explicitly selected native publishers."""
    if os.name == 'posix' and threading.current_thread() is not threading.main_thread():
        raise ValueError('native_runner_main_thread_required')
    termination = None
    if termination_record is not None or termination_run_id is not None:
        if (not isinstance(termination_run_id, str) or not termination_run_id.strip()
                or not isinstance(termination_record, str)):
            raise ValueError('termination_record_and_run_id_required')
        termination = local(root, termination_record)
        reserved = [events.absolute()]
        reserved += [Path(os.environ[k]).absolute() for k in
                     ('OMC_CHILD_PID_RECORD', 'OMC_CHILD_EXIT_RECORD') if os.environ.get(k)]
        if termination in reserved or termination.exists():
            raise ValueError('termination_record_must_be_distinct_and_new')
        termination.parent.mkdir(parents=True, exist_ok=True)
    events.parent.mkdir(parents=True, exist_ok=True)
    from omc_native_command import NativeStreamAlreadyExists
    try:
        stream = events.open('xb')
    except FileExistsError as exc:
        raise NativeStreamAlreadyExists('native_stream_already_exists') from exc
    with stream as output:
        def forward(raw):
            output.write(raw)
            output.flush()
            sys.stdout.buffer.write(raw)
            sys.stdout.buffer.flush()

        from omc_pipeline_guard import SESSION_START_HANDOFF_ENV, create_session_start_handoff
        child_env = dict(os.environ)
        # An unrelated nested launch must never inherit another launch's approval.
        child_env.pop(SESSION_START_HANDOFF_ENV, None)
        if context is not None:
            from omc_native_red_evidence import write_new, verify_execution_context
            import uuid
            verify_execution_context(root, context)
            write_new(root, '.omc/native-contexts/'+uuid.uuid4().hex+'.json', context)
            handoff = create_session_start_handoff(root, context)
            if handoff is not None:
                child_env[SESSION_START_HANDOFF_ENV] = json.dumps(handoff)
        proc = None
        termination_state = {'stop_reason': 'normal', 'signals_sent': [], 'drain_complete': True}
        pending_signal = None
        stopping = False
        previous_handlers = {}

        def interrupted(signum, _frame):
            nonlocal pending_signal
            pending_signal = signum
            # A signal during Popen creation is handled after its PID is known.
            if proc is not None and not stopping:
                raise NativeRunInterrupted(signum)

        try:
            if os.name == 'posix':
                for signum in (signal.SIGTERM, signal.SIGINT):
                    previous_handlers[signum] = signal.signal(signum, interrupted)
            if pending_signal is not None:
                raise NativeRunInterrupted(pending_signal)
            proc = subprocess.Popen(argv, cwd=root, stdout=subprocess.PIPE, env=child_env,
                                    start_new_session=os.name == 'posix')
            if pending_signal is not None:
                raise NativeRunInterrupted(pending_signal)
            # read1 does not prefetch unforwarded lines into a hidden buffer.
            for chunk in iter(lambda: proc.stdout.read1(65536), b''):
                forward(chunk)
                if b'\n' in chunk:
                    for publisher in publishers:
                        publisher()
            if context is not None:
                verify_execution_context(root, context)
            code = proc.wait()
            if os.name == 'posix':
                try:
                    os.killpg(proc.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
            return code
        except BaseException as exc:
            if proc is None:
                raise
            stopping = True
            termination_state['stop_reason'] = ('signal:'+str(exc.signum)
                if isinstance(exc, NativeRunInterrupted) else type(exc).__name__)
            tail, drain_complete, termination_errors = _stop_native(proc,
                immediate=isinstance(exc, NativeRunInterrupted),
                signals_sent=termination_state['signals_sent'])
            termination_state['drain_complete'] = drain_complete
            forward(tail)
            from omc_native_red_evidence import write_new
            import uuid
            ref = write_new(root, '.omc/native-stream-errors/'+uuid.uuid4().hex+'.json', {
                'schema_version': 'omc-native-stream-error/v1', 'reason': str(exc),
                'error_type': type(exc).__name__, 'context': context,
                'cause': str(exc.__cause__) if exc.__cause__ is not None else None,
                'stream_path': str(events.relative_to(root)),
                'stream_sha256': sha(events.read_bytes()), 'child_returncode': proc.returncode,
                'drain_complete': drain_complete,
                'termination_errors': termination_errors,
                'termination_scope': 'process_group' if os.name == 'posix' else 'direct_child'})
            if isinstance(exc, NativeRunInterrupted):
                raise SystemExit(128+exc.signum)
            if not isinstance(exc, Exception):
                raise
            raise NativePublisherFailure('native_publisher_failed: '+ref['path']) from exc
        finally:
            stopping = True
            if proc is not None and os.name == 'posix':
                # Releasing the wrapper must not leave its detached group alive,
                # including descendants which already closed stdout normally.
                try:
                    os.killpg(proc.pid, signal.SIGKILL)
                    termination_state['signals_sent'].append(
                        {'signal': int(signal.SIGKILL), 'scope': 'process_group', 'outcome': 'sent'})
                except OSError:
                    # Checked normal cleanup and failed attempts are recorded above.
                    pass
            for signum, handler in previous_handlers.items():
                signal.signal(signum, handler)
            if proc is not None and os.name == 'posix' and not proc.stdout.closed:
                proc.stdout.close()
            if termination is not None:
                code = proc.poll() if proc is not None else None
                absent = False
                if proc is not None:
                    try:
                        os.killpg(proc.pid, 0)
                    except ProcessLookupError:
                        absent = True
                    except OSError:
                        pass
                observed = {'schema_version': 'omc-native-termination/v1',
                    'run_id': termination_run_id, 'writer_pid': os.getpid(),
                    'direct_child_pid': proc.pid if proc is not None else None,
                    'direct_child_pgid': proc.pid if proc is not None and os.name == 'posix' else None,
                    **termination_state, 'direct_child_returncode': code,
                    'direct_child_signal': -code if code is not None and code < 0 else None,
                    'wait_completed': code is not None, 'process_group_absent': absent,
                    'claude_pid': None, 'claude_returncode': None, 'recorded_at': time.time()}
                try:
                    # Recheck link boundaries after execution; never replace a historical record.
                    local(root, termination_record)
                    with termination.open('x') as stream:
                        json.dump(observed, stream, ensure_ascii=False, indent=2)
                        stream.write('\n')
                except (OSError, ValueError) as exc:
                    print('termination_record_failed: '+str(exc), file=sys.stderr)
                    raise NativePublisherFailure('termination_record_failed') from exc


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=['init', 'observe', 'assess', 'receipts', 'run'])
    parser.add_argument('--target', type=Path, default=Path.cwd())
    parser.add_argument('--basis', type=Path)
    parser.add_argument('--receipt')
    parser.add_argument('--receipt-sha256')
    parser.add_argument('--preflight')
    parser.add_argument('--run-id')
    parser.add_argument('--session-id')
    parser.add_argument('--launch', nargs=argparse.REMAINDER)
    args = parser.parse_args()
    try:
        root = args.target.resolve()
        if args.action == 'init':
            result = initialize(root, args.preflight, args.run_id, args.session_id)
            code = 0
        elif args.action == 'run':
            if not args.launch:
                raise ValueError('explicit_launch_argv_required')
            return run_native(root, args.launch)
        elif args.action == 'receipts':
            result = receipt_references(root)
            code = 0
        elif args.action == 'observe':
            result = observe(root)
            code = 0  # successful observation, even when no motion was seen
        else:
            if args.basis is None:
                raise ValueError('basis_required')
            selected = {'path': args.receipt, 'sha256': args.receipt_sha256} if args.receipt else None
            result = assess_completion_home(root, json.loads(args.basis.read_bytes()), submission=selected)
            code = 0 if result is None or result['status'] == 'passed' else 1
        print(json.dumps(result, ensure_ascii=False))
        return code
    except (OSError, ValueError, KeyError, TypeError) as exc:
        from omc_native_command import NativeStreamAlreadyExists
        if isinstance(exc, NativeStreamAlreadyExists):
            print(json.dumps({'status': 'unknown', 'reason': 'native_stream_already_exists',
                              'next_action': '기존 스트림은 보존하세요. runner 재실행·기록 삭제 없이 native RED diagnose로 확인하세요.'}, ensure_ascii=False))
            return 1
        print(json.dumps({'status': 'unknown', 'reason': str(exc)}),
              file=sys.stderr if isinstance(exc, NativePublisherFailure) else sys.stdout)
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
