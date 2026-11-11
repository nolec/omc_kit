#!/usr/bin/env python3
"""Bounded, explicitly approved, same-work edit generations.

Local custody only: these records do not authenticate a human or contain hostile
local writers. No historical ledger is rewritten to revive its approval clock.
"""
from __future__ import annotations
import argparse
import contextlib
import contextvars
import errno
import math
import ctypes
import fcntl
import hashlib
import json
import os
from pathlib import Path
import signal
import stat
import subprocess
import sys
import time
import uuid

ENV = 'OMC_EDIT_RESUME_CONTEXT'
TTL = 1800
RETIREMENT_PENDING = '.omc/edit-resumptions/retirement-pending.json'
RETIREMENT_PATHS = (
    '.omc/pipeline_session.json',
    '.omc/state/latest.json',
    '.omc/state/pending-completion.json',
)


def digest(data):
    return hashlib.sha256(data).hexdigest()


def clock():
    """Suspend-inclusive clock, tied to the running boot; no wall-clock fallback."""
    if sys.platform.startswith('linux') and hasattr(time, 'CLOCK_BOOTTIME'):
        return Path('/proc/sys/kernel/random/boot_id').read_text().strip(), time.clock_gettime(time.CLOCK_BOOTTIME)
    if sys.platform == 'darwin':
        lib = ctypes.CDLL('/usr/lib/libSystem.B.dylib')
        class Timebase(ctypes.Structure):
            _fields_ = [('numer', ctypes.c_uint32), ('denom', ctypes.c_uint32)]
        base = Timebase()
        if lib.mach_timebase_info(ctypes.byref(base)) or not base.denom:
            raise ValueError('clock_unavailable')
        lib.mach_continuous_time.restype = ctypes.c_uint64
        boot = subprocess.check_output(['/usr/sbin/sysctl', '-n', 'kern.boottime'], text=True).strip()
        if not boot:
            raise ValueError('boot_identity_unavailable')
        return boot, lib.mach_continuous_time() * base.numer / base.denom / 1e9
    raise ValueError('suspend_inclusive_clock_unavailable')


def local(root, name, *, exists=True):
    root = Path(root).resolve()
    p = Path(name)
    p = p if p.is_absolute() else root / p
    p.relative_to(root)
    if '..' in p.parts or any(x.is_symlink() for x in (p, *p.parents)):
        raise ValueError('unsupported_link_or_path')
    if exists and not p.is_file():
        raise ValueError('regular_file_required')
    if exists and not stat.S_ISREG(p.stat().st_mode):
        raise ValueError('regular_file_required')
    return p


def read(root, name):
    return json.loads(local(root, name).read_bytes())


def git(root, *args):
    return subprocess.check_output(['git', *args], cwd=root)


def original(root):
    p = Path(root) / '.omc/pipeline_session.json'
    return read(root, p) if p.exists() else {}


def work(root):
    return read(root, '.omc/state/pending-completion.json')['work_id']


def slot_path(root, work_id):
    return Path(root) / '.omc/edit-resumptions/slots' / (digest(work_id.encode()) + '.json')


def committed_work_closed(root, state, pending_path):
    """Recognize a consumed completion receipt without reviving its approval.

    Local records are checked against Git and the original owning session.
    Missing, changed or ambiguous evidence keeps the old lifecycle closed.
    """
    if pending_path.exists() or pending_path.is_symlink():
        return False
    sid = state.get('owner_session_id')
    if not isinstance(sid, str) or not sid or '/' in sid or '\\' in sid or sid in ('.', '..'):
        return False
    owner = read(root, f'.omc/state/sessions/{sid}/session.json')
    receipt = read(root, f'.omc/state/sessions/{sid}/completion.json')
    from omc_state import _canonical_sha256
    # Session start clears active approval, not the historical completion owner.
    # Recognize only that exact reset shape; never revive its CONTRACT approval.
    approval_reset = (
        state.get('session_id') == ''
        and state.get('contract_reset_reason') == 'session_start'
        and state.get('contract_confirmed') is False
        and state.get('contract_hash') == ''
    )
    if (not isinstance(owner, dict) or not isinstance(receipt, dict)
            or receipt.get('schema_version') != 2
            or owner.get('session_id') != sid or receipt.get('session_id') != sid
            or (state.get('session_id') != sid and not approval_reset)
            or owner.get('work_id') != state.get('owner_work_id')
            or not owner.get('work_id')
            or not isinstance(owner.get('role_ids'), list)
            or 'senior_coding' not in owner['role_ids']
            or not isinstance(owner.get('request'), str) or not owner['request'].strip()
            or owner.get('confirmation', {}).get('status') != 'confirmed'
            or receipt.get('request_sha256') != _canonical_sha256(owner.get('request'))
            or receipt.get('work_class') != owner.get('work_class')
            or receipt.get('work_class_locked_at') != owner.get('created_at')
            or receipt.get('baseline_commit') != owner.get('git', {}).get('head')):
        return False
    base, head = receipt.get('baseline_commit'), receipt.get('followup_commit')
    if any(not isinstance(oid, str) or len(oid) not in (40, 64)
           or any(c not in '0123456789abcdef' for c in oid) for oid in (base, head)):
        return False
    try:
        parents = git(root, 'rev-list', '--parents', '-n', '1', head).decode().split()
        if parents != [head, base]:
            return False
        subprocess.run(['git', 'merge-base', '--is-ancestor', head, 'HEAD'],
                       cwd=root, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        paths = git(root, 'diff-tree', '--no-commit-id', '--name-only', '-r', '-z', head).decode().rstrip('\0').split('\0')
        return bool(paths and paths != [''] and paths == receipt.get('changed_paths'))
    except (subprocess.SubprocessError, UnicodeError):
        return False


def frozen(root):
    """Freeze the active work; historical slots cannot freeze unrelated work."""
    marker = Path(root) / RETIREMENT_PENDING
    if marker.exists() or marker.is_symlink():
        return True
    try:
        state = original(root)
        pending_path = Path(root) / '.omc/state/pending-completion.json'
        if pending_path.is_symlink():
            return True
        pending = read(root, pending_path) if pending_path.exists() else {}
        if not isinstance(state, dict) or not isinstance(pending, dict):
            return True
        identified_work = state.get('owner_work_id') or pending.get('work_id')
        if identified_work:
            if not isinstance(identified_work, str):
                return True
            slot = slot_path(root, identified_work)
            if slot.exists() or slot.is_symlink():
                return True
        return bool(state and identified_work and time.time() - state.get('started_at', 0) > 28800
                    and not committed_work_closed(root, state, pending_path))
    except (OSError, ValueError, TypeError, AttributeError):
        return True  # Malformed governance is a closed state, not a CLI traceback.


def retire_expired_work(root, work_id, *, approval_reference):
    """Explicitly detach unfinished work, preserving bytes and never granting approval.

    The pending marker freezes partial transitions. Repeating the same request
    can finish the moves, but changed evidence must be investigated separately.
    """
    if not isinstance(work_id, str) or not work_id.strip():
        raise ValueError('explicit_work_id_required')
    if not isinstance(approval_reference, str) or not approval_reference.strip():
        raise ValueError('explicit_approval_required')
    if os.environ.get(ENV):
        raise ValueError('retirement_cannot_use_resume_context')
    root = Path(root).resolve()
    local(root, '.omc/state/.lock', exists=False)
    from omc_state import _omc_lock
    with _omc_lock(root), lock(root, work_id):
        marker = local(root, RETIREMENT_PENDING, exists=False)
        if marker.exists():
            descriptor = read(root, marker)
            if (descriptor.get('work_id') != work_id
                    or descriptor.get('approval_reference') != approval_reference
                    or descriptor.get('root') != str(root)):
                raise ValueError('retirement_request_mismatch')
            archive = local(root, descriptor['archive'], exists=False)
            if archive.parent != root / '.omc/edit-resumptions/retirements':
                raise ValueError('retirement_archive_invalid')
            manifest_path = local(root, archive / 'manifest.json')
            if digest(manifest_path.read_bytes()) != descriptor['manifest_sha256']:
                raise ValueError('retirement_manifest_changed')
            manifest = read(root, manifest_path)
        else:
            state = original(root)
            pending = read(root, '.omc/state/pending-completion.json')
            latest = read(root, '.omc/state/latest.json')
            if (state.get('owner_work_id') != work_id or pending.get('work_id') != work_id
                    or state.get('session_id') not in pending.get('session_ids', [])
                    or latest.get('latest_session_id') != pending.get('session_id')
                    or latest.get('latest_confirmed_session_id') != pending.get('session_id')):
                raise ValueError('retirement_owner_mismatch')
            if not time.time() - state.get('started_at', time.time()) > 28800:
                raise ValueError('retirement_requires_expired_work')
            slot = local(root, slot_path(root, work_id), exists=False)
            if slot.exists():
                generation = read(root, slot)
                boot, now = clock()
                if boot == generation['boot'] and now < generation['deadline']:
                    raise ValueError('retirement_resume_still_active')
            if latest.get('active_run_id'):
                run = read(root, f".omc/state/runs/{latest['active_run_id']}.json")
                if run.get('status') == 'running':
                    raise ValueError('retirement_active_run_unresolved')
            names = [*RETIREMENT_PATHS]
            for name in ('.omc/project-memory.json', '.omc/notepad.md', '.omc/summary.md'):
                if (root / name).exists():
                    names.append(name)
            # Read and validate every source before creating any archival files.
            originals = {name: (local(root, name).read_bytes(),
                                stat.S_IMODE(local(root, name).stat().st_mode)) for name in names}
            archive = local(root, '.omc/edit-resumptions/retirements/' + uuid.uuid4().hex, exists=False)
            pins = {}
            for name, (raw, mode) in originals.items():
                path = archive / 'snapshots' / name
                path.parent.mkdir(parents=True, exist_ok=True)
                with path.open('xb') as stream:
                    stream.write(raw)
                    stream.flush()
                    os.fsync(stream.fileno())
                os.chmod(path, mode)
                fsync_file(path)
                pins[name] = {'sha256': digest(raw), 'mode': mode}
            manifest = {'schema_version': 1, 'root': str(root), 'work_id': work_id,
                        'approval_reference': approval_reference,
                        'previous_work_status': 'EXPIRED_UNFINISHED',
                        'retired_paths': list(RETIREMENT_PATHS), 'files': pins}
            write_new(archive / 'manifest.json', manifest)
            # Persist the copies before publishing the fail-closed transition marker.
            for directory in sorted((p for p in archive.rglob('*') if p.is_dir()),
                                    key=lambda p: len(p.parts), reverse=True):
                fsync_file(directory)
            for directory in (archive, *archive.parents):
                fsync_file(directory)
                if directory == root:
                    break
            descriptor = {'root': str(root), 'work_id': work_id,
                          'approval_reference': approval_reference,
                          'archive': str(archive.relative_to(root)),
                          'manifest_sha256': digest((archive / 'manifest.json').read_bytes())}
            write_new(marker, descriptor)
        if (manifest.get('root') != str(root) or manifest.get('work_id') != work_id
                or manifest.get('retired_paths') != list(RETIREMENT_PATHS)):
            raise ValueError('retirement_manifest_invalid')
        # Validate the full transition before moving any remaining active pointer.
        for name, pin in manifest['files'].items():
            snapshot = local(root, archive / 'snapshots' / name)
            active = local(root, name, exists=False)
            path = active if active.exists() else local(root, archive / 'originals' / name)
            if any(digest(p.read_bytes()) != pin['sha256']
                   or stat.S_IMODE(p.stat().st_mode) != pin['mode'] for p in (snapshot, path)):
                raise ValueError('retirement_evidence_changed')
        for name in RETIREMENT_PATHS:
            active = local(root, name, exists=False)
            retired = local(root, archive / 'originals' / name, exists=False)
            if active.exists():
                if retired.exists():
                    raise ValueError('retirement_destination_exists')
                retired.parent.mkdir(parents=True, exist_ok=True)
                os.rename(active, retired)
            fsync_file(retired)
            fsync_file(retired.parent)
            fsync_file(active.parent)
        receipt = {'archive': str(archive.relative_to(root)), 'work_id': work_id,
                   'previous_work_status': 'EXPIRED_UNFINISHED',
                   'retired_paths': list(RETIREMENT_PATHS), 'new_work_started': False}
        completed = archive / 'completed.json'
        if completed.exists():
            if read(root, completed) != receipt:
                raise ValueError('retirement_completion_changed')
        else:
            write_new(completed, receipt)
        os.rename(marker, archive / 'retirement-pointer.json')
        fsync_file(marker.parent)
        fsync_file(archive)
        return receipt


@contextlib.contextmanager
def lock(root, work_id):
    p = Path(root) / '.omc/edit-resumptions/locks' / (digest(work_id.encode()) + '.lock')
    local(root, p, exists=False)
    p.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(p, os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        yield
    finally:
        os.close(fd)


def fsync_file(p):
    fd = os.open(p, os.O_RDONLY | os.O_NOFOLLOW)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def durable_generation(root, gen, slot):
    for file in (gen / 'pipeline.json', gen / 'slot.json', slot):
        fsync_file(file)
    directories = set()
    for file in (gen / 'pipeline.json', slot):
        for parent in file.parents:
            directories.add(parent)
            if parent == Path(root).resolve():
                break
    for directory in sorted(directories, key=lambda p:len(p.parts), reverse=True):
        fsync_file(directory)


def write_new(p, value):
    p.parent.mkdir(parents=True, exist_ok=True)
    with p.open('x') as stream:
        json.dump(value, stream, sort_keys=True)
        stream.flush()
        os.fsync(stream.fileno())
    fsync_file(p.parent)


def inventory(root, allowed):
    names = git(root, 'ls-files', '-co', '--exclude-standard', '-z').decode().split('\0')
    result = {}
    for name in sorted(set(names) - {''}):
        if name.startswith('.omc/'):
            continue
        p = local(root, name)
        result[name] = {'mode': stat.S_IMODE(p.stat().st_mode)}
        if name not in allowed:
            result[name]['sha256'] = digest(p.read_bytes())
    return result


def index(root, allowed):
    lines = git(root, 'ls-files', '--stage').decode().splitlines()
    for line in lines:
        metadata, name = line.split('\t', 1)
        if name in allowed:
            mode, _, stage = metadata.split()
            expected = '100755' if local(root, name).stat().st_mode & 0o111 else '100644'
            if mode != expected or stage != '0':
                raise ValueError('unsupported_index_mode_or_stage')
    return [line for line in lines if line.split('\t', 1)[-1] not in allowed]


def ownership(root, *, require_expired=True):
    root = Path(root).resolve()
    latest = read(root, '.omc/state/latest.json')
    sid = latest['latest_confirmed_session_id']
    if latest['latest_session_id'] != sid:
        raise ValueError('session_unconfirmed')
    session = read(root, f'.omc/state/sessions/{sid}/session.json')
    pending = read(root, '.omc/state/pending-completion.json')
    old = original(root)
    if (session.get('confirmation', {}).get('status') != 'confirmed'
            or session.get('completion_action') != 'continue'
            or 'senior_coding' not in session.get('role_ids', [])
            or session.get('work_class') != 'implementation'
            or pending.get('work_class') != 'implementation'
            or session.get('work_id') != pending.get('work_id')
            or old.get('owner_work_id') != pending.get('work_id')
            or old.get('session_id') not in pending.get('session_ids', [])
            or (require_expired and not time.time() - old.get('started_at', time.time()) > 28800)):
        raise ValueError('expired_work_owner_binding_invalid')
    head = git(root, 'rev-parse', 'HEAD').decode().strip()
    from omc_state import _pending_completion_matches_session
    if not _pending_completion_matches_session(root, pending, current_head=head):
        raise ValueError('pending_lineage_invalid')
    files = ['.omc/pipeline_session.json', '.omc/state/latest.json', '.omc/state/pending-completion.json']
    for i, member in enumerate(pending['session_ids']):
        name = f'.omc/state/sessions/{member}/session.json'
        entry = read(root, name)
        if (entry.get('work_id') != pending['work_id'] or entry.get('lineage_index') != i
                or entry.get('lineage_root_session_id') != pending['root_session_id']
                or entry.get('lineage_previous_session_id') != (pending['session_ids'][i-1] if i else None)
                or entry.get('confirmation', {}).get('status') != 'confirmed'
                or 'senior_coding' not in entry.get('role_ids', [])
                or entry.get('git', {}).get('head') != head):
            raise ValueError('owner_lineage_invalid')
        files.append(name)
    folder = root / '.omc/verification-bases' / sid
    files.extend(str(p.relative_to(root)) for p in sorted(folder.glob('*.json')))
    from omc_pipeline_guard import _load_approved_basis
    basis = _load_approved_basis(root, sid)
    if (basis.get('completion_kind', 'implementation') != 'implementation'
            or any(k in basis for k in ('validation_commands_policy_sha256', 'task_profile', 'run_id', 'transfer'))
            or any('native_red' in c for c in basis['checks'])):
        raise ValueError('unsupported_policy')
    # Preserve all historical governance files, not just the current approval.
    for folder_name in ('state/sessions', 'verification-bases', 'verification', 'completion-reapprovals', 'state/pipeline-history'):
        folder = root / '.omc' / folder_name
        files.extend(str(p.relative_to(root)) for p in folder.rglob('*') if p.is_file())
    # Pin the historical RED/output/evidence files referenced by preserved records.
    def evidence_refs(item):
        if isinstance(item, dict):
            for key, value in item.items():
                if key in {'path', 'file', 'output_file', 'verification_file'} and isinstance(value, str):
                    candidate = Path(value)
                    candidate = candidate if candidate.is_absolute() else root / candidate
                    try:
                        name = str(candidate.relative_to(root))
                    except ValueError:
                        raise ValueError('external_historical_evidence')
                    if name.startswith('.omc/') and candidate.is_file():
                        yield name
                yield from evidence_refs(value)
        elif isinstance(item, list):
            for value in item:
                yield from evidence_refs(value)
    queue = list(files)
    seen = set()
    while queue:
        name = queue.pop()
        if name in seen:
            continue
        seen.add(name)
        file = local(root, name)
        if file.suffix == '.json':
            try:
                references = list(evidence_refs(json.loads(file.read_bytes())))
            except json.JSONDecodeError:
                raise ValueError('historical_record_unreadable')
            files.extend(references)
            queue.extend(references)
    pins = {name: digest(local(root, name).read_bytes()) for name in sorted(set(files))}
    return {'root': str(root), 'git_dir': str(Path(git(root, 'rev-parse', '--absolute-git-dir').decode().strip()).resolve()),
            'head': head, 'work_id': pending['work_id'], 'root_session': pending['root_session_id'],
            'session_id': sid, 'protected': pins, 'checks': [c['id'] for c in basis['checks']]}


def _require_approved_snapshot(root, request, binding):
    """Never replace a human-approved baseline with a later lock-time snapshot."""
    if ownership(root) != binding:
        raise ValueError('issuance_binding_changed')
    values = (
        ('expected_binding_sha256', digest(json.dumps(binding, sort_keys=True).encode())),
        ('expected_code_sha256', digest(json.dumps(inventory(root, []), sort_keys=True).encode())),
        ('expected_index_sha256', digest(git(root, 'ls-files', '--stage'))),
    )
    for key, actual in values:
        if request.get(key) != actual:
            raise ValueError('approval_snapshot_mismatch:' + key)


def issue(root, proposal):
    root = Path(root).resolve()
    request = read(root, proposal)
    for key in ('approval_reference', 'purpose', 'grant_id'):
        if not isinstance(request.get(key), str) or not request[key].strip():
            raise ValueError('explicit_approval_required')
    allowed = request.get('allowed_files')
    if not isinstance(allowed, list) or not allowed or len(set(allowed)) != len(allowed):
        raise ValueError('exact_existing_scope_required')
    for name in allowed:
        if not isinstance(name, str) or Path(name).is_absolute() or str(Path(name)) != name or name.startswith(('.omc/', '.git/')):
            raise ValueError('invalid_scope')
        local(root, name)
        names = git(root, 'ls-files', '-co', '--exclude-standard', '-z').decode().split('\0')
        if name not in names:
            raise ValueError('ignored_scope_unsupported')
    binding = ownership(root)
    if request.get('required_checks') != binding['checks']:
        raise ValueError('approval_checks_mismatch')
    _require_approved_snapshot(root, request, binding)
    with lock(root, binding['work_id']):
        slot = slot_path(root, binding['work_id'])
        local(root, slot, exists=False)
        if slot.exists():
            raise ValueError('work_slot_already_consumed')
        _require_approved_snapshot(root, request, binding)
        snapshot = {'inventory': inventory(root, allowed), 'index': index(root, allowed)}
        full = inventory(root, [])
        gen = root / '.omc/edit-resumptions/generations' / uuid.uuid4().hex
        state = {'started_at': time.time(), 'session_id': binding['session_id'],
                 'owner_session_id': binding['session_id'], 'owner_work_id': binding['work_id'],
                 'contract_confirmed': False, 'red_done_tests': [], 'allowed_impl_files': []}
        write_new(gen / 'pipeline.json', state)
        if ownership(root) != binding or inventory(root, []) != full or index(root, allowed) != snapshot['index']:
            raise ValueError('issuance_binding_changed')
        boot, start = clock()
        value = {'schema_version': 1, 'binding': binding, 'grant_id': request['grant_id'],
                 'generation': str(gen.relative_to(root)), 'allowed_files': allowed,
                 'approval_reference': request['approval_reference'], 'purpose': request['purpose'],
                 'boot': boot, 't0': start, 'deadline': start + TTL, **snapshot}
        temp = gen / 'slot.json'
        write_new(temp, value)
        slot.parent.mkdir(parents=True, exist_ok=True)
        # Check the original approval again after all preparation/fsync work.
        _require_approved_snapshot(root, request, binding)
        publish_boot, publish_now = clock()
        if publish_boot != boot or publish_now < start or publish_now >= start + TTL:
            raise ValueError('issuance_expired')
        os.link(temp, slot)  # Exclusive atomic publication; never replace a work slot.
        durable_generation(root, gen, slot)
        boot_now, now = clock()
        if boot_now != boot or now < start or now >= start + TTL:
            raise ValueError('issuance_expired')
        return {'path': str(slot), 'sha256': digest(slot.read_bytes())}


@contextlib.contextmanager
def _loaded_context(root, *, durable=True):
    """Keep the work lock through validation and the caller's bounded file commit."""
    ref = json.loads(os.environ.get(ENV, 'null'))
    if not isinstance(ref, dict) or set(ref) != {'path', 'sha256'}:
        raise ValueError('explicit_resume_context_required')
    p = local(root, ref['path'])
    raw = p.read_bytes()
    if digest(raw) != ref['sha256']:
        raise ValueError('resume_context_sha_mismatch')
    value = json.loads(raw)
    if p != slot_path(Path(root).resolve(), value['binding']['work_id']):
        raise ValueError('wrong_work_slot')
    # The issuer holds the same lock through publication and directory fsync.
    with lock(root, value['binding']['work_id']):
        if ownership(root, require_expired=False) != value['binding']:
            raise ValueError('resume_binding_changed')
        if inventory(root, value['allowed_files']) != value['inventory'] or index(root, value['allowed_files']) != value['index']:
            raise ValueError('resume_scope_or_index_changed')
        boot, now = clock()
        if boot != value['boot'] or now < value['t0'] or now >= value['deadline']:
            raise ValueError('resume_expired_or_clock_changed')
        gen = local(root, Path(value['generation']) / 'pipeline.json')
        if durable:
            durable_generation(root, gen.parent, p)
        # Recheck after potentially slow durability work; no 30 minute extension.
        boot, now = clock()
        if boot != value['boot'] or now < value['t0'] or now >= value['deadline']:
            raise ValueError('resume_expired_or_clock_changed')
        yield value


def load(root, *, durable=True):
    with _loaded_context(root, durable=durable) as value:
        return value


def pipeline_path(root):
    value = load(root)
    return local(root, Path(value['generation']) / 'pipeline.json')


def inspect(root, work_id):
    p = local(root, slot_path(root, work_id))
    return {'path': str(p), 'sha256': digest(p.read_bytes()), 'slot': json.loads(p.read_bytes()), 'activated': False}


def apply(root, name, content):
    value = load(root)
    path = local(root, name)
    relative = str(path.relative_to(Path(root).resolve()))
    if relative not in value['allowed_files'] or len(content) > 8 * 1024 * 1024:
        raise ValueError('edit_scope_not_approved')
    from omc_pipeline_guard import cmd_check_edit
    if cmd_check_edit(Path(root).resolve(), relative) != 0:
        raise ValueError('edit_pipeline_gate_blocked')
    _, now = clock()
    if value['deadline'] - now <= 5:
        raise ValueError('termination_reserve_exhausted')
    temp = Path(root) / value['generation'] / ('edit-' + uuid.uuid4().hex)
    with temp.open('xb') as stream:
        stream.write(content); stream.flush(); os.fsync(stream.fileno())
    os.chmod(temp, stat.S_IMODE(path.stat().st_mode))
    # Retirement takes this same lock. It cannot detach the work between the
    # final validation and its file commit, even if the process is suspended.
    with _loaded_context(root) as current:
        if current['generation'] != value['generation']:
            raise ValueError('resume_generation_changed')
        _, now = clock()
        if current['deadline'] - now <= 5:
            raise ValueError('termination_reserve_exhausted')
        os.replace(temp, path)
    load(root)


_execution_trace = contextvars.ContextVar('omc_execution_trace', default=None)


class _ExecutionTrace:
    """Bounded diagnostic samples; never evidence of unmeasured member identity."""
    def __init__(self, pgid=None):
        self.pgid = pgid
        self.events = []
        self.bytes = 0
        self.truncated = 0
        self.record_errors = 0

    def append(self, operation, **fields):
        try:
            now = time.monotonic()
            if not math.isfinite(now):
                raise ValueError('diagnostic_clock_unavailable')
            row = {'operation': operation, 'monotonic': now, **fields}
            size = len(json.dumps(row).encode())
            # Reserve space for envelope/counters; no diagnostics can grow unbounded.
            if len(self.events) >= 32 or self.bytes + size > 12000:
                self.truncated += 1
                return
            self.events.append(row)
            self.bytes += size
        except Exception:
            self.record_errors += 1

    def payload(self):
        return {'clock_domain': 'monotonic', 'owned_pid': self.pgid, 'owned_pgid': self.pgid,
                'member_states': 'unknown', 'events': self.events,
                'truncated': self.truncated, 'record_errors': self.record_errors}


def _trace_event(operation, **fields):
    trace = _execution_trace.get()
    if trace is not None:
        trace.append(operation, **fields)


def _execution_audit(path, value):
    trace = _execution_trace.get()
    if trace is not None:
        value = {**value, 'cleanup_trace': trace.payload()}
    write_new(path, value)


def _group_exists(pgid):
    try:
        os.killpg(pgid, 0)
        _trace_event('killpg_query', pgid=pgid, signal=0, exists=True, errno=None)
        return True
    except ProcessLookupError as exc:
        _trace_event('killpg_query', pgid=pgid, signal=0, exists=False, errno=exc.errno)
        return False
    except OSError as exc:
        _trace_event('killpg_query', pgid=pgid, signal=0, exists='unknown', errno=exc.errno)
        raise


def _wait_group_exit(child, seconds):
    end = time.monotonic() + seconds
    while True:
        code = child.poll()  # Existing reap, no additional process queries.
        _trace_event('direct_child_poll', pid=child.pid, returncode=code)
        try:
            if not _group_exists(child.pid):
                return True
        except OSError as exc:
            if exc.errno != errno.EPERM:
                raise
            # Darwin can report EPERM while only unreaped zombies remain.
            # Keep the observation unknown, reap/recheck within this same deadline.
        remaining = end - time.monotonic()
        if remaining <= 0:
            return False
        time.sleep(min(0.01, remaining))


def _stop_group(child):
    """Bounded group cleanup; a parent exit alone never proves termination."""
    try:
        for sig in (signal.SIGTERM, signal.SIGKILL):
            try:
                os.killpg(child.pid, sig)
                _trace_event('killpg_signal', pgid=child.pid, signal=int(sig), errno=None)
            except ProcessLookupError as exc:
                _trace_event('killpg_signal', pgid=child.pid, signal=int(sig), errno=exc.errno)
                child.poll()
                return not _group_exists(child.pid)
            except OSError as exc:
                _trace_event('killpg_signal', pgid=child.pid, signal=int(sig), errno=exc.errno)
                if exc.errno != errno.EPERM:
                    raise
                # A zombie-only Darwin group can reject the signal as well.
                # Only the subsequent absence query can confirm cleanup.
            if _wait_group_exit(child, 2):
                return True
    except OSError:
        return False
    return False


def supervise(root, argv, *, profile):
    trace = _ExecutionTrace()
    token = _execution_trace.set(trace)
    try:
        return _supervise(root, argv, profile=profile)
    finally:
        _execution_trace.reset(token)


def _supervise(root, argv, *, profile):
    value = load(root)
    state = read(root, Path(value['generation']) / 'pipeline.json')
    if not state.get('contract_confirmed'):
        raise ValueError('new_contract_required')
    # No arbitrary shell, editor or detached process is an approved profile.
    allowed = {
        'tests': [sys.executable, '-m', 'pytest'],
        'tdd': [sys.executable, 'scripts/omc_tdd_check.py', '--staged'],
    }
    prefix = allowed.get(profile)
    if prefix is None or argv[:len(prefix)] != prefix:
        raise ValueError('unsupported_execution_profile')
    if profile == 'tests' and any(x.startswith('-') or x not in value['allowed_files'] for x in argv[len(prefix):]):
        raise ValueError('test_scope_not_approved')
    if profile == 'tests' and len(argv) == len(prefix):
        raise ValueError('explicit_test_files_required')
    _, now = clock()
    if value['deadline'] - now <= 5:
        raise ValueError('termination_reserve_exhausted')
    timeout = min(120, value['deadline'] - now - 5)
    child = subprocess.Popen(argv, cwd=root, start_new_session=True)
    trace = _execution_trace.get()
    if trace is not None:
        trace.pgid = child.pid  # start_new_session makes the owned child's PID its PGID.
    _trace_event('owned_child_started', pid=child.pid, pgid=child.pid)
    audit = Path(root) / value['generation'] / 'executions' / (uuid.uuid4().hex + '.json')
    try:
        boot, now = clock()
        if boot != value['boot'] or now < value['t0']:
            raise subprocess.TimeoutExpired(argv, 0)
        timeout = min(timeout, value['deadline'] - now - 5)
        if timeout <= 0:
            raise subprocess.TimeoutExpired(argv, 0)
        code = child.wait(timeout=timeout)
    except subprocess.TimeoutExpired:
        stopped = _stop_group(child)
        _execution_audit(audit, {'status':'incomplete', 'termination':'observed' if stopped else 'unknown',
                          'argv':argv, 'returncode':child.returncode, 'reason':'timeout'})
        raise ValueError('supervised_execution_timeout' if stopped else 'termination_unconfirmed')
    except (OSError, ValueError, subprocess.SubprocessError):
        stopped = _stop_group(child)
        _execution_audit(audit, {'status':'incomplete', 'termination':'observed' if stopped else 'unknown',
                          'argv':argv, 'returncode':child.returncode, 'reason':'execution_unverifiable'})
        raise
    try:
        descendants = _group_exists(child.pid)
    except OSError as exc:
        stopped = _stop_group(child)
        _execution_audit(audit, {'status':'incomplete', 'termination':'observed' if stopped else 'unknown',
                          'argv':argv, 'returncode':code, 'reason':'process_group_unverifiable'})
        raise ValueError('process_group_unverifiable') from exc
    if descendants:
        stopped = _stop_group(child)
        _execution_audit(audit, {'status':'incomplete', 'termination':'observed' if stopped else 'unknown',
                          'argv':argv, 'returncode':code, 'reason':'leftover_descendants'})
        raise ValueError('leftover_descendants_terminated' if stopped else 'termination_unconfirmed')
    try:
        load(root)
    except (OSError, ValueError):
        _execution_audit(audit, {'status':'incomplete', 'argv':argv, 'returncode':code})
        raise
    _execution_audit(audit, {'status':'passed' if code==0 else 'failed', 'argv':argv, 'returncode':code})
    return code


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--target', type=Path, default=Path.cwd())
    sub = parser.add_subparsers(dest='cmd', required=True)
    sub.add_parser('capabilities')
    p = sub.add_parser('approve-edit-resume'); p.add_argument('--file', type=Path, required=True)
    p = sub.add_parser('inspect-edit-resume'); p.add_argument('--work-id', required=True)
    p = sub.add_parser('retire-expired-work', help='Archive expired unfinished work before an independent new work.')
    p.add_argument('--work-id', required=True)
    p.add_argument('--approval-reference', required=True)
    sub.add_parser('validate')
    p = sub.add_parser('apply'); p.add_argument('--file', required=True)
    p = sub.add_parser('run'); p.add_argument('--profile', choices=['tests', 'tdd'], required=True); p.add_argument('argv', nargs=argparse.REMAINDER)
    args = parser.parse_args()
    try:
        if args.cmd == 'capabilities':
            print(json.dumps({'single_slot_per_work': True, 'ttl_seconds': TTL, 'response_delivery_required': False}))
        elif args.cmd == 'approve-edit-resume':
            print(json.dumps(issue(args.target, args.file)))
        elif args.cmd == 'inspect-edit-resume':
            print(json.dumps(inspect(args.target.resolve(), args.work_id)))
        elif args.cmd == 'retire-expired-work':
            print(json.dumps(retire_expired_work(args.target, args.work_id,
                                                 approval_reference=args.approval_reference)))
        elif args.cmd == 'validate':
            print(json.dumps(load(args.target.resolve())))
        elif args.cmd == 'apply':
            apply(args.target.resolve(), args.file, sys.stdin.buffer.read(8 * 1024 * 1024 + 1))
        elif args.cmd == 'run':
            return supervise(args.target.resolve(), args.argv, profile=args.profile)
        return 0
    except (OSError, ValueError, TypeError, KeyError, AttributeError, subprocess.SubprocessError) as exc:
        print('edit_resume_blocked: ' + str(exc))
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
