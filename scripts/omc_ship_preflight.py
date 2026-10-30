"""Pinned local-clone baseline preparation. Never runs a quality gate or a model."""
from __future__ import annotations
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import signal
import stat
import subprocess
import time

SCHEMA = 'omc-ship-preflight/v1'
BUDGETS = {'prepare': (240, 210), 'check': (90, 60)}

class Blocked(Exception):
    def __init__(self, reason, detail='', code=1):
        super().__init__(detail)
        self.reason, self.detail, self.code = reason, detail, code

def sha(raw):
    return hashlib.sha256(raw).hexdigest()

def absolute(value):
    p = Path(value)
    if not p.is_absolute() or '..' in p.parts:
        raise Blocked('unsafe_path', str(p))
    if any(x.is_symlink() for x in [p, *p.parents]):
        raise Blocked('symlink_path', str(p))
    return p

def identity(p):
    s = p.stat()
    return [s.st_dev, s.st_ino]

def overlap(a, b):
    return a == b or a in b.parents or b in a.parents

class Owned:
    """Exclusive reservation in a trusted parent, not a hostile-same-UID sandbox."""
    def __init__(self, path):
        self.path = absolute(path)
        self.parent = identity(self.path.parent)
        try:
            self.path.mkdir(mode=0o700)
        except FileExistsError as e:
            raise Blocked('reservation_conflict', str(path)) from e
        self.fd = os.open(self.path, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        self.token = identity(self.path)
    def verify(self):
        absolute(self.path)
        f = os.fstat(self.fd)
        if identity(self.path.parent) != self.parent or identity(self.path) != self.token or [f.st_dev, f.st_ino] != self.token:
            raise Blocked('directory_identity_changed', str(self.path))
    def file(self, name):
        self.verify()
        if '/' in name or name in {'.', '..'}:
            raise Blocked('unsafe_output_name')
        return os.fdopen(os.open(name, os.O_CREAT | os.O_EXCL | os.O_RDWR | os.O_NOFOLLOW,
                                0o600, dir_fd=self.fd), 'w+b')
    def write(self, name, raw):
        with self.file(name) as f:
            f.write(raw)
            f.flush()
    def close(self):
        os.close(self.fd)

class ClonedMetadata(Owned):
    """Pin only a directory restored inside this prepare's exclusively owned clone."""
    @classmethod
    def pin(cls, owner, run):
        run.work()
        owner.verify()
        try:
            first = os.stat('.omc', dir_fd=owner.fd, follow_symlinks=False)
        except FileNotFoundError:
            owner.verify()
            return None
        if stat.S_ISLNK(first.st_mode):
            raise Blocked('symlink_path', str(owner.path/'.omc'))
        if not stat.S_ISDIR(first.st_mode):
            raise Blocked('reservation_conflict', str(owner.path/'.omc'))
        fd = os.open('.omc', os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=owner.fd)
        directory = cls.__new__(cls)
        directory.path, directory.parent = owner.path/'.omc', owner.token
        directory.fd, directory.token, directory.owner = fd, [first.st_dev, first.st_ino], owner
        try:
            directory.verify()
            run.work()
        except BaseException:
            directory.close()
            raise
        return directory

    def verify(self):
        self.owner.verify()
        try:
            super().verify()
        except FileNotFoundError as ex:
            raise Blocked('directory_identity_changed', str(self.path)) from ex

    def file(self, name):
        try:
            return super().file(name)
        except FileExistsError as ex:
            raise Blocked('reservation_conflict', str(self.path/name)) from ex


def metadata_inventory(run, directory):
    """Walk through pinned directory fds, never following metadata links."""
    found = set()
    def walk(fd, prefix):
        run.work()
        before = os.fstat(fd)
        with os.scandir(fd) as entries:
            for entry in entries:
                run.work()
                relative = prefix+'/'+entry.name
                item = entry.stat(follow_symlinks=False)
                found.add(relative)
                if stat.S_ISLNK(item.st_mode):
                    raise Blocked('symlink_path', relative)
                if stat.S_ISDIR(item.st_mode):
                    child = os.open(entry.name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=fd)
                    try:
                        opened = os.fstat(child)
                        if (opened.st_dev, opened.st_ino) != (item.st_dev, item.st_ino):
                            raise Blocked('directory_identity_changed', relative)
                        walk(child, relative)
                        after = os.stat(entry.name, dir_fd=fd, follow_symlinks=False)
                        if (after.st_dev, after.st_ino) != (item.st_dev, item.st_ino):
                            raise Blocked('directory_identity_changed', relative)
                    finally:
                        os.close(child)
                elif not stat.S_ISREG(item.st_mode):
                    raise Blocked('unsupported_tracked_type', relative)
        after = os.fstat(fd)
        if (before.st_mtime_ns, before.st_ctime_ns) != (after.st_mtime_ns, after.st_ctime_ns):
            raise Blocked('directory_identity_changed', prefix)
    directory.verify()
    walk(directory.fd, '.omc')
    directory.verify()
    run.work()
    return found


def verify_cloned_metadata(run, target, directory, head, config_created=False):
    """Strict clone inventory; NOT reused by check after fresh approval issuance."""
    run.work()
    rows = run.git(target, 'ls-tree', '-r', '-z', '--full-tree', head, '--', '.omc')
    expected = set()
    for row in rows.split(b'\0'):
        if not row:
            continue
        run.work()
        name = os.fsdecode(row.split(b'\t', 1)[1])
        if name == '.omc' or not name.startswith('.omc/') or '..' in Path(name).parts:
            raise Blocked('unsafe_tracked_path', name)
        if name == '.omc/pipeline_session.json' or any(
                name == prefix or name.startswith(prefix+'/') for prefix in
                ('.omc/state', '.omc/verification', '.omc/verification-bases')):
            raise Blocked('cloned_runtime_metadata_conflict', name)
        expected.add(name)
        expected.update(str(parent) for parent in Path(name).parents if str(parent) not in ('.', '.omc'))
    if directory is None:
        if expected:
            raise Blocked('tracked_file_missing', '.omc')
        directory = Owned(target/'.omc')
    try:
        directory.verify()
        if not config_created and os.path.lexists(directory.path/'quality-gates.json'):
            raise Blocked('reservation_conflict', str(directory.path/'quality-gates.json'))
        actual = metadata_inventory(run, directory)
        if not expected and actual and not config_created:
            raise Blocked('cloned_metadata_untracked', '.omc')
        if config_created:
            expected.add('.omc/quality-gates.json')
        if actual != expected:
            raise Blocked('cloned_metadata_inventory_mismatch', repr(sorted(actual ^ expected)))
        verify_head_files(run, target, head)
        # HEAD checks run subprocesses after the first scan. Recheck the actual
        # inventory before returning permission to create the configuration.
        actual = metadata_inventory(run, directory)
        if actual != expected:
            raise Blocked('cloned_metadata_inventory_mismatch', repr(sorted(actual ^ expected)))
        directory.verify()
        run.work()
        return directory
    except BaseException:
        # The caller owns an already pinned fd. Only close a newly created one here.
        if not isinstance(directory, ClonedMetadata) and not config_created:
            directory.close()
        raise


class Run:
    def __init__(self, command, started):
        self.started = started
        self.total, work = BUDGETS[command]
        self.work_end = started + work
        self.record = None
        self.children, self.commands, self.raw_files, self.cleanup_errors = [], [], [], []
        self.phase = 'input'
        self.env = {k: v for k, v in os.environ.items() if not k.startswith('GIT_')}
        self.env.update(GIT_CONFIG_NOSYSTEM='1', GIT_CONFIG_GLOBAL=os.devnull,
                        GIT_CONFIG_SYSTEM=os.devnull, GIT_OPTIONAL_LOCKS='0', GIT_TERMINAL_PROMPT='0')
    def work(self):
        if time.monotonic() >= self.work_end:
            raise Blocked('work_deadline_exceeded', code=124)
    def exists(self, entry):
        try:
            os.killpg(entry['pgid'], 0)
            return True
        except ProcessLookupError:
            return False
        except OSError as e:
            self.cleanup_errors.append({'pid': entry['pid'], 'operation': 'probe', 'errno': e.errno, 'error': str(e)})
            return True
    def cleanup(self, deadline):
        for e in self.children:
            if e.get('confirmed'):
                continue
            p = e['process']
            try:
                if self.exists(e):
                    if p.poll() is None and os.getpgid(p.pid) != e['pgid']:
                        raise Blocked('process_identity_changed')
                    for sig in [signal.SIGTERM, signal.SIGKILL]:
                        if time.monotonic() >= deadline:
                            break
                        try:
                            os.killpg(e['pgid'], sig)
                        except ProcessLookupError:
                            break
                        except OSError as ex:
                            self.cleanup_errors.append({'pid': p.pid, 'operation': sig.name, 'errno': ex.errno, 'error': str(ex)})
                            break
                        end = min(deadline, time.monotonic()+0.2)
                        while time.monotonic() < end:
                            p.poll()
                            if not self.exists(e):
                                break
                            time.sleep(0.01)
                        if not self.exists(e):
                            break
                p.poll()
                e['confirmed'] = p.returncode is not None and not self.exists(e)
            except (OSError, Blocked) as ex:
                self.cleanup_errors.append({'pid': p.pid, 'operation': 'cleanup', 'error': str(ex)})
                e['confirmed'] = False
        return all(e.get('confirmed', False) for e in self.children)
    def git(self, root, *args, cap=30):
        self.work()
        names = [f'git-{len(self.commands):04d}.{s}' for s in ['stdout', 'stderr']]
        cmd = ['git', '-c', 'core.hooksPath=/dev/null', '-c', 'core.fsmonitor=false',
               '-c', 'protocol.file.allow=always', *map(str, args)]
        row = {'argv': cmd, 'cwd': str(root), 'phase': self.phase, 'stdout': names[0], 'stderr': names[1]}
        self.commands.append(row)
        with self.record.file(names[0]) as out, self.record.file(names[1]) as err:
            self.raw_files.extend(names)
            try:
                p = subprocess.Popen(cmd, cwd=root, env=self.env, stdin=subprocess.DEVNULL,
                                     stdout=out, stderr=err, start_new_session=True)
                e = {'process': p, 'pid': p.pid, 'pgid': p.pid, 'started': time.monotonic(), 'confirmed': False}
                self.children.append(e)
                row.update(pid=p.pid, pgid=p.pid, started_monotonic=e['started'])
                p.wait(timeout=max(0.001, min(cap, self.work_end-time.monotonic())))
                row['exit_code'] = p.returncode
                e['confirmed'] = not self.exists(e)
                if not e['confirmed']:
                    raise Blocked('child_group_remaining')
            except subprocess.TimeoutExpired as ex:
                row['timeout'] = True
                raise Blocked('git_timeout', repr(args), 124) from ex
            except OSError as ex:
                row.update(errno=ex.errno, error=str(ex))
                raise Blocked('git_execution_error', str(ex)) from ex
            out.flush()
            err.flush()
            if p.returncode:
                err.seek(0, 2)
                err.seek(max(0, err.tell()-8192))
                row['stderr_tail'] = err.read().decode(errors='replace')
                raise Blocked('git_failed', row['stderr_tail'])
            out.seek(0)
            raw = out.read()
        self.work()
        return raw

def canonical(run, ref, root):
    if not isinstance(ref, str):
        raise Blocked('invalid_base_ref')
    if ref.startswith('origin/'):
        value = 'refs/remotes/' + ref
    elif ref.startswith('refs/remotes/origin/'):
        value = ref
    else:
        raise Blocked('invalid_base_ref', ref)
    run.git(root, 'check-ref-format', value)
    return value

def independent(run, root):
    absolute(root)
    if not (root/'.git').is_dir() or (root/'.git').is_symlink():
        raise Blocked('not_independent_repository', str(root))
    common = Path(run.git(root, 'rev-parse', '--path-format=absolute', '--git-common-dir').decode().strip())
    top = Path(run.git(root, 'rev-parse', '--show-toplevel').decode().strip())
    if common.resolve() != root/'.git' or top.resolve() != root:
        raise Blocked('shared_git_directory')


def verify_head_files(run, root, head):
    """Compare actual bytes/type/execute mode to HEAD, never to index hints.

    Git diff may deliberately skip assume-unchanged/skip-worktree entries.
    Hash the working bytes as a Git blob without filters or index mutation.
    Contracts use full 40-character OIDs, so this surface requires SHA-1 Git.
    Submodule contents and checkout transformations are not certified here.
    """
    if run.git(root, 'rev-parse', '--show-object-format').strip() != b'sha1':
        raise Blocked('unsupported_object_format')
    tree = run.git(root, 'ls-tree', '-r', '-z', '--full-tree', head)
    for row in tree.split(b'\0'):
        if not row:
            continue
        run.work()
        meta, name = row.split(b'\t', 1)
        mode, kind, expected_oid = meta.split()
        relative = Path(os.fsdecode(name))
        if relative.is_absolute() or '..' in relative.parts or relative.parts[0] == '.git':
            raise Blocked('unsafe_tracked_path', os.fsdecode(name))
        path = root/relative
        # Never follow a substituted ancestor directory outside the candidate.
        absolute(path.parent)
        try:
            before = path.lstat()
        except FileNotFoundError as ex:
            raise Blocked('tracked_file_missing', str(relative)) from ex
        if mode == b'120000' and kind == b'blob':
            if not stat.S_ISLNK(before.st_mode):
                raise Blocked('tracked_type_mismatch', str(relative))
            raw = os.fsencode(os.readlink(path))
            digest = hashlib.sha1(b'blob '+str(len(raw)).encode()+b'\0'+raw).hexdigest()
        elif mode in {b'100644', b'100755'} and kind == b'blob':
            if not stat.S_ISREG(before.st_mode):
                raise Blocked('tracked_type_mismatch', str(relative))
            actual_mode = b'100755' if before.st_mode & stat.S_IXUSR else b'100644'
            if actual_mode != mode:
                raise Blocked('tracked_mode_mismatch', str(relative))
            digest_state = hashlib.sha1(b'blob '+str(before.st_size).encode()+b'\0')
            fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
            with os.fdopen(fd, 'rb') as stream:
                opened = os.fstat(stream.fileno())
                if (opened.st_dev, opened.st_ino) != (before.st_dev, before.st_ino):
                    raise Blocked('tracked_file_changed_during_check', str(relative))
                while True:
                    run.work()
                    block = stream.read(1024*1024)
                    if not block:
                        break
                    digest_state.update(block)
            digest = digest_state.hexdigest()
        else:
            raise Blocked('unsupported_tracked_type', str(relative))
        run.work()
        after = path.lstat()
        attrs = ('st_dev', 'st_ino', 'st_mode', 'st_size', 'st_mtime_ns', 'st_ctime_ns')
        if any(getattr(before, n) != getattr(after, n) for n in attrs):
            raise Blocked('tracked_file_changed_during_check', str(relative))
        if digest.encode('ascii') != expected_oid:
            raise Blocked('tracked_content_mismatch', str(relative))


def changed(run, root, doc, ref):
    if run.git(root, 'rev-parse', 'HEAD').decode().strip() != doc['expected_head_oid']:
        raise Blocked('candidate_mismatch')
    if run.git(root, 'rev-parse', '--verify', ref+'^{commit}').decode().strip() != doc['expected_base_oid']:
        raise Blocked('base_oid_mismatch')
    run.git(root, 'merge-base', '--is-ancestor', doc['expected_base_oid'], doc['expected_head_oid'])
    paths = sorted(p for p in run.git(root, 'diff', '--name-only', '-z', ref+'...HEAD', '--').decode().split('\0') if p)
    if paths != sorted(doc['expected_changed_paths']):
        raise Blocked('changed_paths_mismatch', repr(paths))
    verify_head_files(run, root, doc['expected_head_oid'])
    dirty = run.git(root, 'diff', '--name-only', '-z', 'HEAD', '--')
    untracked = [p for p in run.git(root, 'ls-files', '--others', '--exclude-standard', '-z').decode().split('\0')
                 if p and p != '.omc' and not p.startswith('.omc/')]
    if dirty or untracked:
        raise Blocked('local_changes', repr(untracked))
    return paths

def config_bytes(run, root, doc, ref):
    run.work()
    p = absolute(root/'.omc/quality-gates.json')
    raw = p.read_bytes()
    if sha(raw) != doc['quality_config_sha256']:
        raise Blocked('quality_config_mismatch')
    if canonical(run, json.loads(raw)['base_ref'], root) != ref:
        raise Blocked('config_base_ref_mismatch')
    return raw

def freeze(run, root):
    files = {}
    names = [os.fsdecode(n) for n in run.git(root, 'ls-files', '-z').split(b'\0') if n]
    names += ['.git/index', '.omc/quality-gates.json']
    state = root/'.omc/state'
    if state.is_dir():
        names += [str(p.relative_to(root)) for p in state.rglob('*') if p.is_file()]
    for name in sorted(set(names)):
        run.work()
        p = root/name
        if not p.exists() and not p.is_symlink():
            files[name] = None
        elif p.is_symlink():
            files[name] = {'link': os.readlink(p), 'mode': stat.S_IMODE(p.lstat().st_mode)}
        else:
            files[name] = {'sha256': sha(p.read_bytes()), 'mode': stat.S_IMODE(p.stat().st_mode)}
    return {'head': run.git(root, 'rev-parse', 'HEAD').decode().strip(), 'files': files,
            'root_identity': identity(root), 'git_identity': identity(root/'.git'),
            'refs': run.git(root, 'for-each-ref', '--format=%(refname) %(objectname)').decode()}

def load_contract(path):
    absolute(path)
    with path.open('rb') as f:
        raw = f.read(1024*1024+1)
    if len(raw) > 1024*1024:
        raise Blocked('contract_too_large')
    doc = json.loads(raw)
    fields = {'schema_version', 'source_root', 'expected_head_oid', 'base_ref',
              'expected_base_oid', 'quality_config_sha256', 'expected_changed_paths'}
    if not isinstance(doc, dict) or set(doc) != fields or doc['schema_version'] != SCHEMA:
        raise Blocked('invalid_contract')
    for k in ['expected_head_oid', 'expected_base_oid']:
        if not isinstance(doc[k], str) or not re.fullmatch('[0-9a-f]{40}', doc[k]):
            raise Blocked('invalid_oid', k)
    if not isinstance(doc['quality_config_sha256'], str) or not re.fullmatch('[0-9a-f]{64}', doc['quality_config_sha256']):
        raise Blocked('invalid_config_digest')
    names = doc['expected_changed_paths']
    if not isinstance(names, list) or not names or not all(isinstance(n, str) for n in names) or len(set(names)) != len(names):
        raise Blocked('invalid_changed_paths')
    for n in names:
        p = Path(n)
        if p.is_absolute() or '..' in p.parts or p.as_posix() != n or n in {'.', '.git', '.omc'} or n.startswith(('.git/', '.omc/')):
            raise Blocked('invalid_changed_paths', n)
    return doc, raw

def execute(run, args, result):
    doc, raw = load_contract(Path(args.contract))
    source, target, record = absolute(doc['source_root']), absolute(args.target), absolute(args.record_dir)
    if any(overlap(a, b) for a, b in [(source, target), (source, record), (target, record)]):
        raise Blocked('path_overlap')
    run.record = Owned(record)
    run.record.write('contract.json', raw)
    result.update(contract_sha256=sha(raw), source=str(source), target=str(target), record_dir=str(record),
                  candidate=doc['expected_head_oid'], base_oid=doc['expected_base_oid'], config_sha256=doc['quality_config_sha256'])
    if args.command == 'prepare':
        run.phase = 'source'
        independent(run, source)
        ref = canonical(run, doc['base_ref'], source)
        result['canonical_ref'] = ref
        result['changed_paths'] = changed(run, source, doc, ref)
        config = config_bytes(run, source, doc, ref)
        before = freeze(run, source)
        run.record.write('source-before.json', json.dumps(before, ensure_ascii=False).encode())
        changed(run, source, doc, ref)
        config_bytes(run, source, doc, ref)
        run.phase = 'reservation'
        owned = Owned(target)
        directory = None
        try:
            run.phase = 'clone'
            owned.verify()
            run.git(source.parent, 'clone', '--no-hardlinks', '--template=', '--origin', 'omc-source', '--', source, target, cap=120)
            owned.verify()
            directory = ClonedMetadata.pin(owned, run)
            independent(run, target)
            if run.git(target, 'rev-parse', 'HEAD').decode().strip() != doc['expected_head_oid']:
                raise Blocked('candidate_mismatch')
            run.git(target, 'merge-base', '--is-ancestor', doc['expected_base_oid'], 'HEAD')
            run.phase = 'bind_ref'
            owned.verify()
            existing = run.git(target, 'for-each-ref', '--format=%(objectname)', ref).decode().strip()
            if existing and existing != doc['expected_base_oid']:
                raise Blocked('existing_base_ref_mismatch')
            if not existing:
                owned.verify()
                run.git(target, 'update-ref', ref, doc['expected_base_oid'], '0'*40)
            owned.verify()
            run.phase = 'config'
            if directory is not None and not run.git(target, 'ls-tree', '-r', '--name-only', doc['expected_head_oid'], '--', '.omc').strip():
                raise Blocked('cloned_metadata_untracked', '.omc')
            directory = verify_cloned_metadata(run, target, directory, doc['expected_head_oid'])
            owned.verify()
            directory.write('quality-gates.json', config)
            verify_cloned_metadata(run, target, directory, doc['expected_head_oid'], config_created=True)
            if freeze(run, source) != before:
                raise Blocked('source_preservation_mismatch')
            changed(run, source, doc, ref)
            config_bytes(run, source, doc, ref)
            result['source_preserved'] = True
            run.phase = 'verify'
            owned.verify()
            changed(run, target, doc, ref)
            config_bytes(run, target, doc, ref)
            owned.verify()
        finally:
            if directory is not None:
                directory.close()
            owned.close()
    else:
        run.phase = 'check'
        independent(run, target)
        ref = canonical(run, doc['base_ref'], target)
        result['canonical_ref'] = ref
        before = freeze(run, target)
        run.record.write('target-before.json', json.dumps(before, ensure_ascii=False).encode())
        result['changed_paths'] = changed(run, target, doc, ref)
        config_bytes(run, target, doc, ref)
        if before != freeze(run, target):
            raise Blocked('target_preservation_mismatch')
        result['target_preserved'] = True
    run.work()

def finish(run, result, error):
    terminal = time.monotonic()
    cleanup_end = min(terminal+20, run.started+run.total-10)
    record_end = min(cleanup_end+10, run.started+run.total)
    result.update(phase=run.phase, work_terminal_monotonic=terminal, cleanup_deadline_monotonic=cleanup_end,
                  record_deadline_monotonic=record_end, commands=run.commands)
    result['cleanup_confirmed'] = run.cleanup(cleanup_end)
    result['cleanup_errors'] = run.cleanup_errors
    result['processes'] = [{k: v for k, v in e.items() if k != 'process'} for e in run.children]
    result.update(status='blocked' if error else 'baseline_verified', reason_code=error.reason if error else None,
                  exit_code=error.code if error else 0, initial_reason=error.reason if error else None)
    if error:
        result['detail'] = error.detail
    if not result['cleanup_confirmed']:
        result.update(status='blocked', reason_code='cleanup_unconfirmed', exit_code=125)
    try:
        if time.monotonic() >= record_end:
            raise Blocked('record_deadline_exceeded')
        result['raw_evidence'] = []
        if run.record:
            run.record.verify()
            for name in run.raw_files:
                if time.monotonic() >= record_end:
                    raise Blocked('record_deadline_exceeded')
                p = run.record.path/name
                result['raw_evidence'].append({'name': name, 'size': p.stat().st_size, 'sha256': sha(p.read_bytes())})
        result['evidence_complete'] = bool(run.record) and result['cleanup_confirmed']
        result['elapsed_seconds'] = time.monotonic()-run.started
        if time.monotonic() >= record_end:
            raise Blocked('record_deadline_exceeded')
        # Disk evidence alone cannot certify a successful CLI exit, especially if a
        # write completes after the deadline. Consumers must use stdout + exit code.
        disk = {**result, 'status': 'blocked', 'reason_code': 'requires_cli_completion',
                'record_kind': 'evidence_only', 'observed_check_status': result['status']}
        if run.record:
            name = 'final.json' if result['evidence_complete'] else 'partial.json'
            run.record.write(name, json.dumps(disk, ensure_ascii=False, sort_keys=True).encode())
        if time.monotonic() >= record_end:
            raise Blocked('record_deadline_exceeded')
    except (OSError, Blocked) as ex:
        result.update(status='blocked', reason_code='cleanup_unconfirmed' if not result['cleanup_confirmed'] else 'evidence_incomplete',
                      evidence_complete=False, recording_error=str(ex), exit_code=125 if not result['cleanup_confirmed'] else 1)
        if run.record and time.monotonic() < record_end:
            try:
                run.record.write('partial.json', json.dumps(result, ensure_ascii=False).encode())
            except (OSError, Blocked):
                pass
    try:
        print(json.dumps(result, ensure_ascii=False, sort_keys=True), flush=True)
    except (OSError, BrokenPipeError):
        return 1
    finally:
        if run.record:
            run.record.close()
    return result['exit_code']

def main():
    started = time.monotonic()
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('command', choices=['prepare', 'check'])
    parser.add_argument('--contract', required=True)
    parser.add_argument('--target', required=True)
    parser.add_argument('--record-dir', required=True)
    args = parser.parse_args()
    run = Run(args.command, started)
    result = {'status': 'blocked', 'reason_code': 'not_started', 'operation': args.command,
              'scope': 'baseline_only_not_ship_approval', 'started_monotonic': started}
    error = None
    try:
        execute(run, args, result)
    except Blocked as ex:
        error = ex
    except (OSError, ValueError, KeyError, TypeError) as ex:
        error = Blocked('input_or_operation_error', str(ex))
    except KeyboardInterrupt:
        error = Blocked('interrupted')
    return finish(run, result, error)

if __name__ == '__main__':
    raise SystemExit(main())
