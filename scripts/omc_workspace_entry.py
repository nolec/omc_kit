#!/usr/bin/env python3
"""Prepare independent work. Not an editor, verifier, installer or approval."""
from __future__ import annotations

import argparse
import contextlib
import fcntl
import hashlib
import io
import json
import os
from pathlib import Path
import re
import stat
import subprocess
import sys
import tempfile
import time
import uuid

sys.dont_write_bytecode = True
sys.path.insert(0, str(Path(__file__).resolve().parent))
import omc_guard

STATES = frozenset({'RESERVED', 'CLONING', 'REGISTERING', 'WORKSPACE_READY',
                    'FAILED', 'INTERRUPTED', 'UNVERIFIED'})
ACTIVE_STATES = frozenset({'RESERVED', 'CLONING', 'REGISTERING'})
MAX_RECORD_BYTES = 1024 * 1024


def _kit_digest():
    return _digest(b''.join((Path(__file__).resolve().parent / name).read_bytes()
                            for name in ('omc_workspace_entry.py', 'omc_guard.py', 'omc_state.py')))


def _digest(raw):
    return hashlib.sha256(raw).hexdigest()


def _safe_path(path):
    path = Path(os.path.abspath(path))
    if any(q.is_symlink() for q in [path, *path.parents]):
        raise ValueError('workspace_path_symlink')
    return path


@contextlib.contextmanager
def _regular_file(path, *, writable=False):
    # Nonblocking open precedes fstat: opening a FIFO for reading can otherwise
    # wait forever before we get a descriptor to inspect. Never follow a raced
    # final-component symlink. Descriptor validation also covers special files.
    path = _safe_path(path)
    flags = (os.O_RDWR | os.O_CREAT) if writable else os.O_RDONLY
    fd = os.open(path, flags | os.O_NONBLOCK | os.O_NOFOLLOW, 0o600)
    try:
        if not stat.S_ISREG(os.fstat(fd).st_mode):
            raise ValueError('regular_record_required')
        with os.fdopen(fd, 'r+b' if writable else 'rb', closefd=False) as stream:
            yield stream
    finally:
        os.close(fd)


def _read(path):
    with _regular_file(path) as stream:
        raw = stream.read(MAX_RECORD_BYTES + 1)
    if len(raw) > MAX_RECORD_BYTES:
        raise ValueError('record_too_large')
    value = json.loads(raw)
    if not isinstance(value, dict):
        raise ValueError('record_object_required')
    return raw, value


def _save(path, value, *, exclusive=False):
    path = _safe_path(path)
    raw = json.dumps(value, ensure_ascii=False, sort_keys=True).encode()
    if len(raw) > MAX_RECORD_BYTES:
        raise ValueError('record_too_large')
    if exclusive:
        with path.open('xb') as stream:
            os.chmod(path, 0o600)
            stream.write(raw); stream.flush(); os.fsync(stream.fileno())
    else:
        fd, name = tempfile.mkstemp(prefix='.state-', dir=path.parent)
        try:
            with os.fdopen(fd, 'wb') as stream:
                stream.write(raw); stream.flush(); os.fsync(stream.fileno())
            os.replace(name, path)
        finally:
            if os.path.exists(name):
                os.unlink(name)

    directory = os.open(path.parent, os.O_RDONLY)
    try:
        os.fsync(directory)
    finally:
        os.close(directory)


def _journal(job, event, **fields):
    path = _safe_path(job / 'journal.jsonl')
    with _regular_file(path, writable=True) as stream:
        os.chmod(path, 0o600)
        stream.seek(0, os.SEEK_END)
        stream.write((json.dumps({'event': event, 'at': time.time(), **fields},
                                 ensure_ascii=False, sort_keys=True) + '\n').encode())
        stream.flush(); os.fsync(stream.fileno())


def _source(source, timeout):
    env = omc_guard._isolated_environment()
    deadline = time.monotonic() + timeout
    binding = omc_guard._isolated_source_binding(Path(source).resolve(strict=True),
                                                 env=env, deadline=deadline)
    oid = omc_guard._isolated_git(binding[0], ['rev-parse', '--verify', 'HEAD^{commit}'],
                                  env=env, deadline=deadline)
    return binding, oid


def _private_directory(path):
    path = _safe_path(path)
    if not path.exists():
        # Another independent request may have created the shared directory
        # after our absent check. Accept it only after revalidating below.
        path.mkdir(mode=0o700, exist_ok=True)
    info = _safe_path(path).stat()
    if not stat.S_ISDIR(info.st_mode) or info.st_uid != os.getuid() or stat.S_IMODE(info.st_mode) != 0o700:
        raise ValueError('private_workspace_parent_required')
    return path


def prepare(args, retry_of=None):
    if not args.request or not args.request.strip():
        raise ValueError('request_required')
    if len(args.request.encode()) > 32768:
        raise ValueError('request_too_large')
    binding, oid = _source(args.source, args.timeout_sec)
    parent = _safe_path(args.parent or (Path(tempfile.gettempdir()).resolve() / ('omc-workspaces-' + str(os.getuid()))))
    # Check every Git-discovered boundary before creating even the registry.
    for source in binding:
        if parent.exists():
            if parent == source or source in parent.parents:
                raise ValueError('workspace_parent_overlap')
            for ancestor in [parent, *parent.parents]:
                if ancestor.exists() and os.path.samestat(source.stat(), ancestor.stat()):
                    raise ValueError('workspace_parent_overlap')
        else:
            omc_guard._check_isolated_destination(source, parent)
    for source in binding:
        registry = parent / 'jobs'
        if registry == source or source in registry.parents:
            raise ValueError('workspace_registry_overlap')
    parent = _private_directory(parent)
    jobs = _private_directory(parent / 'jobs')
    job_id = uuid.uuid4().hex
    job = jobs / job_id
    for source in binding:
        omc_guard._check_isolated_destination(source, job)
    if retry_of is not None:
        oid = retry_of[1]['baseline_oid']
    source_identities = [[p.stat().st_dev, p.stat().st_ino] for p in binding]
    packet = {'schema': 'omc-workspace-entry/v1', 'job_id': job_id,
              'source': str(binding[0]), 'source_boundaries': [str(p) for p in binding],
              'source_identities': source_identities, 'baseline_oid': oid,
              'parent': str(parent), 'workspace': str(job / 'workspace'),
              'request': args.request,
              'kit_sha256': _kit_digest(), 'attempt': 1}
    if retry_of is not None:
        old_path, old_packet = retry_of
        packet['attempt'] = old_packet['attempt'] + 1
        packet['origin_job_id'] = old_packet.get('origin_job_id', old_packet['job_id'])
        packet['retry_of'] = {'ticket_path': str(old_path), 'ticket_sha256': _digest(_read(old_path)[0])}
        # Persist the entire child reservation BEFORE any child directory is
        # created. Re-delivery can finish this exact reservation after a crash.
        _save(retry_of[0].parent / 'retry.json', {
            'schema': 'omc-workspace-retry/v1',
            'retry_of': packet['retry_of'], 'packet': packet}, exclusive=True)
    return _reserve_packet(packet)


def _reserve_packet(packet):
    job = _safe_path(Path(packet['workspace']).parent)
    _private_directory(job)
    path = job / 'packet.json'
    if path.exists():
        if _read(path)[1] != packet:
            raise ValueError('retry_packet_mismatch')
    else:
        # Missing state/packet is repairable only before any launch side effect.
        if any(job.iterdir()):
            raise ValueError('retry_partial_reservation_unverified')
        _save(path, packet, exclusive=True)
    if not (job / 'state.json').exists():
        if any(p.name not in {'packet.json', 'journal.jsonl'} for p in job.iterdir()):
            raise ValueError('retry_partial_reservation_unverified')
        _journal(job, 'reserved', workspace=packet['workspace'])
        _save(job / 'state.json', {'state': 'RESERVED', 'owner': None}, exclusive=True)
    return path, packet


def _load_ticket(args):
    if not args.ticket_sha256 or not re.fullmatch('[a-f0-9]{64}', args.ticket_sha256):
        raise ValueError('ticket_hash_required')
    path = _safe_path(args.ticket)
    raw, packet = _read(path)
    if _digest(raw) != args.ticket_sha256:
        raise ValueError('ticket_hash_mismatch')
    if packet.get('schema') != 'omc-workspace-entry/v1' or not re.fullmatch('[a-f0-9]{32}', packet.get('job_id', '')):
        raise ValueError('ticket_shape_invalid')
    parent = _safe_path(packet['parent'])
    expected = parent / 'jobs' / packet['job_id'] / 'packet.json'
    if path != expected or packet['workspace'] != str(expected.parent / 'workspace'):
        raise ValueError('ticket_path_mismatch')
    for directory in (parent, parent / 'jobs', path.parent):
        info = directory.stat()
        if not stat.S_ISDIR(info.st_mode) or info.st_uid != os.getuid() or stat.S_IMODE(info.st_mode) != 0o700:
            raise ValueError('ticket_directory_invalid')
    if args.parent is not None and _safe_path(args.parent) != parent:
        raise ValueError('ticket_parent_mismatch')
    if args.source is not None and Path(args.source).resolve() != Path(packet['source']):
        binding, _ = _source(args.source, args.timeout_sec)
        if not binding[0].samefile(packet['source']):
            raise ValueError('ticket_source_mismatch')
    if args.request is not None and args.request != packet['request']:
        raise ValueError('ticket_request_mismatch')
    return path, packet


def _base(path, packet):
    return {'ticket_path': str(path), 'ticket_sha256': _digest(_read(path)[0]),
            'job_id': packet['job_id'], 'workspace': packet['workspace'],
            'source': packet['source'], 'baseline_oid': packet['baseline_oid'],
            'stage_boundary': 'workspace_preparation_only',
            'requires_contract': True, 'setup': 'NOT_ATTESTED', 'verify': 'NOT_ATTESTED'}


def _registration(packet):
    workspace = _safe_path(packet['workspace'])
    entry_raw, entry = _read(workspace / '.omc/isolated-entry.json')
    git_dir = _safe_path(workspace / '.git')
    if not git_dir.is_dir():
        raise ValueError('workspace_git_changed')
    git_info = git_dir.stat()
    info = workspace.stat()
    if (entry.get('registration') != 'started' or entry.get('baseline_oid') != packet['baseline_oid']
            or Path(entry.get('clone_path', '')) != workspace
            or entry.get('source_root') != packet['source']):
        raise ValueError('workspace_registration_mismatch')
    # Entry is historical. Legitimate later edits/commits need not equal base.
    return {'work_id': entry['work_id'], 'session_id': entry['session_id'],
            'workspace_identity': [info.st_dev, info.st_ino],
            'git_identity': [git_info.st_dev, git_info.st_ino],
            'entry_sha256': _digest(entry_raw)}


def inspect_ticket(path, packet, state, *, locked=False):
    value = _base(path, packet)
    stage = state.get('state')
    if stage not in STATES:
        raise ValueError('workspace_state_invalid')
    value['recorded_state'] = stage
    if locked and stage in ACTIVE_STATES:
        value.update(preparation_status=stage, reason_code='preparation_in_progress')
        return value, 0
    if stage == 'RESERVED' and state.get('owner') is None:
        value['preparation_status'] = 'RESERVED'
        return value, 0
    if stage in ACTIVE_STATES or stage == 'WORKSPACE_READY':
        try:
            registration = _registration(packet)
            if stage == 'WORKSPACE_READY' and (registration != state.get('registration')):
                raise ValueError('workspace_identity_changed')
        except (OSError, ValueError, KeyError, TypeError):
            value.update(preparation_status='UNVERIFIED', reason_code='preserve_partial_and_inspect')
            return value, 1
        value.update(registration, preparation_status='WORKSPACE_READY',
                     observed_only=stage != 'WORKSPACE_READY')
        return value, 0
    value.update(preparation_status=stage, reason_code=state.get('reason_code', 'preserve_partial_and_inspect'))
    return value, 1


def launch(path, packet, args):
    job = path.parent
    lock_path = _safe_path(job / 'launch.lock')
    with _regular_file(lock_path, writable=True) as lock:
        os.chmod(lock_path, 0o600)
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            _, state = _read(job / 'state.json')
            return inspect_ticket(path, packet, state, locked=True)
        _, state = _read(job / 'state.json')
        if state.get('state') != 'RESERVED' or state.get('owner') is not None:
            return inspect_ticket(path, packet, state)
        if packet['kit_sha256'] != _kit_digest():
            raise ValueError('entry_kit_changed')
        for name, identity in zip(packet['source_boundaries'], packet['source_identities']):
            info = _safe_path(name).stat()
            if [info.st_dev, info.st_ino] != identity:
                raise ValueError('source_directory_changed')
        workspace = _safe_path(packet['workspace'])
        if workspace.exists():
            raise ValueError('workspace_already_exists')
        # The exclusive live job lock, not PID/TTL, determines active ownership.
        # An unqueryable old owner never grants takeover or another registration.
        boot_id = None
        try:
            if Path('/proc/sys/kernel/random/boot_id').is_file():
                boot_id = Path('/proc/sys/kernel/random/boot_id').read_text().strip()
            elif sys.platform == 'darwin':
                probe = subprocess.run(['/usr/sbin/sysctl', '-n', 'kern.boottime'],
                                       capture_output=True, text=True, timeout=1)
                if probe.returncode == 0:
                    boot_id = probe.stdout.strip()
        except (OSError, subprocess.SubprocessError):
            pass  # Unknown boot identity grants no recovery/takeover authority.
        state = {'state': 'CLONING', 'owner': {'pid': os.getpid(), 'boot_id': boot_id,
                                              'start_token': uuid.uuid4().hex}}
        _journal(job, 'clone_intent', owner=state['owner'])
        _save(job / 'state.json', state)
        output = io.StringIO()
        try:
            with contextlib.redirect_stdout(output):
                code = omc_guard.start_isolated(argparse.Namespace(
                    source=Path(packet['source']), destination=workspace,
                    request=packet['request'], baseline=packet['baseline_oid'],
                    write_path=[], timeout_sec=args.timeout_sec))
            (job / 'entry-output.txt').write_text(output.getvalue())
            if code:
                raise ValueError('isolated_entry_failed')
            # REGISTERING is reconciliation of the returned registration, not a
            # second session creation or a claim about an unobserved Git phase.
            state['state'] = 'REGISTERING'
            _journal(job, 'registration_observed')
            _save(job / 'state.json', state)
            state['registration'] = _registration(packet)
            state['state'] = 'WORKSPACE_READY'
            _journal(job, 'workspace_ready', registration=state['registration'])
            _save(job / 'state.json', state)
        except (OSError, ValueError, KeyError, TypeError, RuntimeError, subprocess.SubprocessError) as exc:
            state.update(state='FAILED', reason_code=str(exc))
            _journal(job, 'failed', reason_code=str(exc))
            _save(job / 'state.json', state)
        return inspect_ticket(path, packet, state)


def retry_ticket(path, packet, args):
    # A retry is a separately reserved launch attempt, never a takeover or a
    # reset of the failed packet/workspace. Lineage and original OID are kept.
    with _regular_file(path.parent / 'launch.lock', writable=True) as lock:
        # Retry only holds this lock for bounded local reservation I/O. Wait for
        # concurrent retransmissions, never for a live original launcher.
        deadline = time.monotonic() + args.timeout_sec
        while True:
            try:
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except BlockingIOError:
                if (_read(path.parent / 'state.json')[1].get('state') in ACTIVE_STATES
                        or time.monotonic() >= deadline):
                    raise ValueError('retry_owner_still_active')
                time.sleep(min(0.01, max(0, deadline - time.monotonic())))
        _, state = _read(path.parent / 'state.json')
        observed, _ = inspect_ticket(path, packet, state)
        if observed['preparation_status'] not in {'FAILED', 'INTERRUPTED', 'UNVERIFIED'}:
            raise ValueError('retry_requires_failed_or_unverified_attempt')
        link_path = path.parent / 'retry.json'
        if link_path.exists():
            _, link = _read(link_path)
            child = link['packet']
            if not isinstance(child, dict):
                raise ValueError('retry_link_mismatch')
            expected = {'ticket_path': str(path), 'ticket_sha256': _digest(_read(path)[0])}
            child_id = child.get('job_id', '')
            if (link.get('schema') != 'omc-workspace-retry/v1'
                    or link.get('retry_of') != expected or child.get('retry_of') != expected
                    or not isinstance(child_id, str) or not re.fullmatch('[a-f0-9]{32}', child_id)
                    or child_id == packet['job_id']
                    or child.get('schema') != packet['schema']
                    or child.get('parent') != packet['parent']
                    or child.get('workspace') != str(Path(packet['parent']) / 'jobs' / child_id / 'workspace')
                    or child.get('source') != packet['source']
                    or child.get('baseline_oid') != packet['baseline_oid']
                    or child.get('request') != packet['request']
                    or child.get('attempt') != packet['attempt'] + 1
                    or child.get('origin_job_id') != packet.get('origin_job_id', packet['job_id'])):
                raise ValueError('retry_link_mismatch')
            return _reserve_packet(child)
        next_args = argparse.Namespace(**vars(args))
        next_args.source = Path(packet['source'])
        next_args.parent = Path(packet['parent'])
        next_args.request = packet['request']
        return prepare(next_args, retry_of=(path, packet))


def _parser():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--intent', choices=['read', 'start', 'status', 'continue'], default='read')
    parser.add_argument('--source', type=Path)
    parser.add_argument('--parent', type=Path)
    parser.add_argument('--request')
    parser.add_argument('--ticket', type=Path)
    parser.add_argument('--ticket-sha256')
    parser.add_argument('--workspace', type=Path)
    parser.add_argument('--work-id')
    parser.add_argument('--prepare-only', action='store_true')
    parser.add_argument('--retry', action='store_true')
    parser.add_argument('--timeout-sec', type=float, default=120)
    return parser


def main(argv=None):
    args = _parser().parse_args(argv)
    try:
        if not 0 < args.timeout_sec <= 600:
            raise ValueError('invalid_timeout')
        if args.retry and (args.intent != 'start' or args.ticket is None):
            raise ValueError('retry_requires_explicit_failed_ticket')
        if args.intent == 'read':
            binding, oid = _source(args.source or Path.cwd(), args.timeout_sec)
            value, code = {'preparation_status': 'READ_ONLY', 'source': str(binding[0]),
                           'baseline_oid': oid, 'workspace_created': False}, 0
        else:
            if args.ticket is not None:
                path, packet = _load_ticket(args)
                if args.retry:
                    path, packet = retry_ticket(path, packet, args)
            elif args.intent == 'start':
                args.source = args.source or Path.cwd()
                path, packet = prepare(args)
            else:
                raise ValueError('explicit_ticket_required')
            if args.intent == 'start' and not args.prepare_only:
                value, code = launch(path, packet, args)
            else:
                _, state = _read(path.parent / 'state.json')
                lock_path = path.parent / 'launch.lock'
                locked = False
                # Query never creates a lock or changes a state/receipt.
                if lock_path.exists():
                    with _regular_file(lock_path) as lock:
                        try: fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
                        except BlockingIOError: locked = True
                value, code = inspect_ticket(path, packet, state, locked=locked)
            if args.intent == 'continue':
                if code or not args.work_id or args.workspace is None:
                    raise ValueError('explicit_work_binding_required')
                if Path(args.workspace).resolve() != Path(packet['workspace']) or args.work_id != value.get('work_id'):
                    raise ValueError('continue_binding_mismatch')
                _, pending = _read(Path(packet['workspace']) / '.omc/state/pending-completion.json')
                if pending.get('work_id') != args.work_id:
                    raise ValueError('continue_work_not_pending')
                workspace = Path(packet['workspace'])
                _, latest = _read(workspace / '.omc/state/latest.json')
                sid = latest.get('latest_confirmed_session_id')
                owner = pending.get('session_id')
                session_ids = pending.get('session_ids')
                if (not isinstance(sid, str) or not re.fullmatch('[0-9]{8}T[0-9]{6}-[a-f0-9]{8}', sid)
                        or not isinstance(owner, str) or not re.fullmatch('[0-9]{8}T[0-9]{6}-[a-f0-9]{8}', owner)
                        or not isinstance(session_ids, list) or not session_ids or session_ids[-1] != owner
                        or session_ids[0] != pending.get('root_session_id')
                        or latest.get('latest_session_id') != sid
                        or not isinstance(latest.get('latest_confirmation'), dict)
                        or latest['latest_confirmation'].get('status') != 'confirmed'):
                    raise ValueError('continue_session_binding_mismatch')
                for session_id in {sid, owner}:
                    _, session = _read(workspace / '.omc/state/sessions' / session_id / 'session.json')
                    if (session.get('session_id') != session_id or session.get('work_id') != args.work_id
                            or not isinstance(session.get('confirmation'), dict)
                            or session['confirmation'].get('status') != 'confirmed'
                            or (session_id == sid and sid not in session_ids
                                and session.get('completion_action') not in {'preserve', 'preserve-if-present'})):
                        raise ValueError('continue_session_binding_mismatch')
                value.update(preparation_status='CONTINUE_READY',
                             preparation_session_id=value['session_id'],
                             session_id=sid, pending_owner_session_id=owner)
        print(json.dumps(value, ensure_ascii=False))
        return code
    except (OSError, ValueError, KeyError, TypeError, RuntimeError, subprocess.SubprocessError) as exc:
        print(json.dumps({'preparation_status': 'UNVERIFIED', 'reason_code': str(exc),
                          'stage_boundary': 'workspace_preparation_only'}, ensure_ascii=False))
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
