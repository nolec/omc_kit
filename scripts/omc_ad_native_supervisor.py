#!/usr/bin/env python3
"""POSIX single-attempt operator supervisor; not an advertising task evaluator.

Local records bind evidence, not hostile-tamper resistance. Never signals the
detached core child group: that group is observed and cleaned by the core.
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


def stamp():
    now = datetime.now(timezone.utc)
    return {'utc': now.isoformat(), 'kst': now.astimezone(timezone(timedelta(hours=9))).isoformat(),
            'monotonic': time.monotonic()}


def digest(path):
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
    prompt = args.prompt.resolve(strict=True)
    if not prompt.is_file():
        raise ValueError('prompt must be a file')
    argv = args.launch[1:] if args.launch[:1] == ['--'] else args.launch
    if not argv or not argv[0]:
        raise ValueError('explicit launch argv required')
    output = Path(os.path.abspath(args.output))
    if any(p.is_symlink() for p in (output, *output.parents)):
        raise ValueError('output symlink')
    if output in (pid_path, exit_path, prompt) or any(output in p.parents for p in (pid_path, exit_path, prompt)):
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
    launch_files = {}
    if argv and Path(argv[0]).is_file():
        launch_files[argv[0]] = digest(Path(argv[0]))
    if len(argv) > 1:
        script = cwd/argv[1]
        if script.is_file():
            launch_files[str(script.resolve())] = digest(script)
    invocation['launch_file_sha256'] = launch_files
    (output/'invocation.json').write_text(json.dumps(invocation, ensure_ascii=False, indent=2)+'\n')
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
    capture_complete = capture_complete and not storage_errors and all(s['eof'] for s in streams.values())
    input_complete = (input_eof and input_sent == prompt_bytes
                      and input_hash.hexdigest() == prompt_hash.hexdigest() and not input_errors)
    normal = (wrapper_code == child_code == 0 and not timed_out and not interrupted and not forced
              and parser.normal == parser.results == 1 and parser.complete
              and capture_complete and input_complete and cleanup == 'confirmed' and not termination_errors)
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
    parser.add_argument('launch', nargs=argparse.REMAINDER)
    args = parser.parse_args()
    try:
        with ExitStack() as resources:
            return supervise(args, resources)
    except (ValueError, OSError) as exc:
        print('supervisor_input_or_setup_error: '+str(exc), file=sys.stderr)
        return 2


if __name__ == '__main__':
    raise SystemExit(main())
