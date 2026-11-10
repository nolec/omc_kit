#!/usr/bin/env python3
"""Exact fast/rest selection and an opt-in, bounded two-worker experiment.

No approval cache is produced. pytest hooks write process-local native reports;
CLI stdout is one final JSON object and progress goes to stderr.
"""
from __future__ import annotations

import argparse
from contextlib import contextmanager
from concurrent.futures import ThreadPoolExecutor
import hashlib
import json
import os
from pathlib import Path
import shlex
import shutil
import signal
import subprocess
import sys
import time
import threading
import uuid

SELECTOR = 'not slow and not historical_evidence and not red_scaffold'
FAST_FILES = ('scripts/test_omc_review_runner.py', 'scripts/test_omc_review_skill_contract.py',
              'scripts/test_omc_task_skill_contract.py', 'scripts/test_omc_plan_skill_contract.py')
_REPORTS = []
_ACTIVE = {}
_CANCELLED = None
_SPAWNING = 0
_DEFERRED_DEADLINE = None


def pytest_runtest_logreport(report):
    _REPORTS.append({'nodeid': report.nodeid, 'when': report.when,
                     'outcome': report.outcome, 'xfail': hasattr(report, 'wasxfail')})


def pytest_sessionfinish(session, exitstatus):
    dest = os.environ.get('OMC_SUITE_REPORT')
    if dest:
        Path(dest).write_text(json.dumps({'selected': [i.nodeid for i in session.items],
            'reports': _REPORTS, 'exit_code': int(exitstatus)}), encoding='utf-8')


def progress(phase, started, prefix='OMC_SUITE_PROGRESS'):
    # Only owned metadata is displayed, never arbitrary child output or env values.
    print(prefix + ' ' + json.dumps({'phase': phase,
        'elapsed_seconds': round(time.monotonic() - started, 2)}), file=sys.stderr, flush=True)


def _stop_group(pid):
    for sig in (signal.SIGTERM, signal.SIGKILL):
        try:
            os.killpg(pid, sig)
        except ProcessLookupError:
            return
        except PermissionError:
            # On macOS a group containing only an unreaped zombie may return
            # EPERM for SIGKILL. Reap our parent, then require ESRCH; a real
            # permission failure must still block, never become cleanup success.
            process = _ACTIVE.get(pid)
            if process is not None:
                process.poll()
            try:
                os.killpg(pid, 0)
            except ProcessLookupError:
                return
            raise
        if sig == signal.SIGTERM:
            time.sleep(.05)


def execute(argv, cwd, timeout, *, env=None, phase='test', prefix='OMC_SUITE_PROGRESS'):
    """Drain both pipes, retain raw decoded output, and reap owned group on all exits."""
    start = time.monotonic()
    progress(phase + ':started', start, prefix)
    global _SPAWNING
    p = None
    deadline = start + timeout
    heartbeat = start + 5
    try:
        # Defer cancellation while Popen transfers ownership. Blocking signals
        # here would also block TERM in the exec'd child and prevent its cleanup.
        if _CANCELLED:
            raise SystemExit(128 + _CANCELLED)
        if _DEFERRED_DEADLINE:
            raise ValueError(_DEFERRED_DEADLINE + '_deadline_exceeded')
        _SPAWNING += 1
        try:
            p = subprocess.Popen(argv, cwd=cwd, env=env, stdin=subprocess.DEVNULL,
                                 stdout=subprocess.PIPE, stderr=subprocess.PIPE, start_new_session=True)
            _ACTIVE[p.pid] = p
        finally:
            _SPAWNING -= 1
        if _CANCELLED:
            raise SystemExit(128 + _CANCELLED)
        if _DEFERRED_DEADLINE:
            raise ValueError(_DEFERRED_DEADLINE + '_deadline_exceeded')
        while True:
            try:
                out, err = p.communicate(timeout=max(.001, min(.25, deadline - time.monotonic())))
                break
            except subprocess.TimeoutExpired:
                now = time.monotonic()
                if now >= deadline:
                    _stop_group(p.pid)
                    out, err = p.communicate(timeout=3)
                    raise subprocess.TimeoutExpired(argv, timeout, output=out, stderr=err)
                if now >= heartbeat:
                    progress(phase + ':running', start, prefix)
                    heartbeat = now + 5
        return subprocess.CompletedProcess(argv, p.returncode,
            out.decode('utf-8', errors='replace'), err.decode('utf-8', errors='replace'))
    finally:
        if p is not None:
            _stop_group(p.pid)
            if p.poll() is None:
                p.wait(timeout=3)
            _ACTIVE.pop(p.pid, None)
        progress(phase + ':finished', start, prefix)


def _interrupt(signum, frame):
    global _CANCELLED
    _CANCELLED = signum
    for pid in tuple(_ACTIVE):
        _stop_group(pid)
    if not _SPAWNING:
        raise SystemExit(128 + signum)


@contextmanager
def owned_process_signals():
    """The CLI that borrows execute owns its cancellation handlers as well."""
    if threading.current_thread() is not threading.main_thread():
        yield
        return
    global _CANCELLED
    old_cancelled = _CANCELLED
    previous = {sig: signal.getsignal(sig) for sig in (signal.SIGTERM, signal.SIGINT)}
    try:
        for sig in previous:
            signal.signal(sig, _interrupt)
        yield
    finally:
        for sig, handler in previous.items():
            signal.signal(sig, handler)
        _CANCELLED = old_cancelled


def remaining(deadline, phase):
    seconds = deadline - time.monotonic()
    if seconds <= 0:
        raise ValueError(phase + '_deadline_exceeded')
    return seconds


@contextmanager
def deadline_guard(deadline, phase):
    """Preempt blocking POSIX preparation I/O and verify monotonic time after it.

    Do not take over a caller's active alarm. The CLI owns a fresh main thread;
    worker commands have their independent subprocess timeouts.
    """
    seconds = remaining(deadline, phase)
    if threading.current_thread() is not threading.main_thread():
        raise ValueError('deadline_guard_requires_main_thread')
    if signal.getitimer(signal.ITIMER_REAL) != (0.0, 0.0):
        raise ValueError('deadline_guard_existing_alarm')
    global _DEFERRED_DEADLINE
    old_deferred = _DEFERRED_DEADLINE
    previous = signal.getsignal(signal.SIGALRM)
    def expired(signum, frame):
        global _DEFERRED_DEADLINE
        if _SPAWNING:
            _DEFERRED_DEADLINE = phase
            return
        raise ValueError(phase + '_deadline_exceeded')
    signal.signal(signal.SIGALRM, expired)
    try:
        signal.setitimer(signal.ITIMER_REAL, seconds)
        yield
        if _DEFERRED_DEADLINE:
            raise ValueError(_DEFERRED_DEADLINE + '_deadline_exceeded')
        remaining(deadline, phase)
    finally:
        signal.setitimer(signal.ITIMER_REAL, 0)
        signal.signal(signal.SIGALRM, previous)
        _DEFERRED_DEADLINE = old_deferred


def _hash(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def snapshot(root):
    """Code/input identity; exclude only Git metadata and local execution caches."""
    excluded = {'.git', '.omc', 'node_modules', '__pycache__', '.pytest_cache'}
    result = {}
    for base, dirs, files in os.walk(root, followlinks=False):
        dirs[:] = sorted(d for d in dirs if d not in excluded)
        for d in dirs:
            if (Path(base) / d).is_symlink():
                raise ValueError('linked_directory_requires_explicit_review')
        for name in sorted(files):
            p = Path(base) / name
            if p.is_symlink():
                raise ValueError('linked_file_requires_explicit_review')
            result[str(p.relative_to(root))] = [_hash(p), p.stat().st_mode & 0o777]
    return result


def _environment(report):
    env = os.environ.copy()
    # A top-level JUnit path must never be inherited by this child or nested pytest.
    opts = shlex.split(env.get('PYTEST_ADDOPTS', ''))
    clean = []
    i = 0
    while i < len(opts):
        token = opts[i]
        if token in ('--junitxml', '--junit-xml'):
            i += 2
            continue
        if token.startswith(('--junitxml=', '--junit-xml=')):
            i += 1
            continue
        clean.append(token)
        i += 1
    env['PYTEST_ADDOPTS'] = shlex.join(clean)
    env['PYTHONPATH'] = str(Path(__file__).resolve().parent) + os.pathsep + env.get('PYTHONPATH', '')
    env['OMC_SUITE_REPORT'] = str(report)
    env['GIT_OPTIONAL_LOCKS'] = '0'
    return env


def call_pytest(root, folder, nodes, *, timeout, collect=False):
    folder.mkdir(parents=True, exist_ok=False)
    report = folder / 'native.json'
    argv = [sys.executable, '-m', 'pytest', '-p', 'omc_test_suite', '-q', '-m', SELECTOR,
            '--rootdir', str(root), '--basetemp', str(folder / 'temp')]
    if collect:
        argv.append('--collect-only')
    else:
        argv.append('--junitxml=' + str(folder / 'junit.xml'))
    # Node arguments live in a bounded JSON file, avoiding ARG_MAX for a full suite.
    selection = folder / 'nodes.json'
    selection.write_text(json.dumps(nodes))
    env = _environment(report)
    env['OMC_SUITE_NODES'] = str(selection)
    start = time.monotonic()
    entry = {'argv': argv, 'cwd': str(root), 'started_monotonic': start,
             'collect_only': collect, 'nodes': nodes}
    try:
        result = execute(argv, root, timeout, env=env, phase=folder.name)
        entry.update(exit_code=result.returncode)
        (folder / 'stdout.txt').write_text(result.stdout)
        (folder / 'stderr.txt').write_text(result.stderr)
    except subprocess.TimeoutExpired as exc:
        entry.update(exit_code=None, timeout=True)
        (folder / 'stdout.txt').write_bytes(exc.output or b'')
        (folder / 'stderr.txt').write_bytes(exc.stderr or b'')
    entry['ended_monotonic'] = time.monotonic()
    (folder / 'invocation.json').write_text(json.dumps(entry, indent=2))
    if entry['exit_code'] != 0 or not report.is_file():
        raise ValueError('pytest_failed_or_timeout:' + str(folder))
    native = json.loads(report.read_text())
    selected = native['selected']
    if not selected or len(set(selected)) != len(selected):
        raise ValueError('empty_or_duplicate_selection')
    if not collect:
        if selected != nodes:
            raise ValueError('execution_selection_changed')
        reports = native['reports']
        calls = [r['nodeid'] for r in reports if r['when'] == 'call']
        if (len(calls) != len(selected) or set(calls) != set(selected)
                or any(r['outcome'] != 'passed' or r['xfail'] for r in reports)):
            raise ValueError('incomplete_or_unapproved_results')
    return native


def pytest_configure(config):
    nodes = os.environ.get('OMC_SUITE_NODES')
    if nodes:
        config.args[:] = json.loads(Path(nodes).read_text())


def partition(selected, fast_files):
    if len(set(selected)) != len(selected) or not selected:
        raise ValueError('empty_or_duplicate_selection')
    if len(set(fast_files)) != len(fast_files):
        raise ValueError('duplicate_fast_file')
    for file in fast_files:
        if file not in {n.split('::')[0] for n in selected}:
            raise ValueError('fast_file_not_collected:' + file)
    fast = [n for n in selected if n.split('::')[0] in fast_files]
    rest = [n for n in selected if n not in set(fast)]
    if not fast:
        raise ValueError('empty_fast_selection')
    return fast, rest


def _inside(child, parent):
    return child == parent or parent in child.parents


def pilot(args, state):
    start = time.monotonic()
    deadline = start + min(args.timeout_sec, 840)
    # All input and clone preparation comes before the two comparison runs.
    # Reserve both 300-second runs and the final 60 seconds; never clip an
    # expired duration to a tiny positive timeout and accidentally launch work.
    prepare_deadline = min(start + 180, deadline - 660)
    state.update(comparison_calls=0, clock_start=start, deadline_monotonic=deadline,
                 prepare_deadline_monotonic=prepare_deadline)
    prepared = {}
    preparation_seconds = {}
    with deadline_guard(prepare_deadline, 'pilot_prepare'):
        before = snapshot(args.target)
        spec = json.loads(args.pilot_file.read_text())
        cases = spec['cases']
        nodes = [c['nodeid'] for c in cases]
        if len(set(nodes)) != len(nodes) or not nodes:
            raise ValueError('invalid_pilot_nodes')
        groups = {}
        for c in cases:
            groups.setdefault(c['group'], []).append(c['nodeid'])
        if len(groups) != 2:
            raise ValueError('pilot_requires_two_groups')
        # External-browser sharing cannot be inferred from content hashes alone.
        if any('test_omc_ad_validation_runtime.py' in n for n in nodes):
            raise ValueError('external_browser_dependency_isolation_not_certified')
        selected = call_pytest(args.target, args.record_dir / 'pilot-collect', ['scripts'],
            timeout=min(60, remaining(prepare_deadline, 'pilot_prepare')), collect=True)['selected']
        if not set(nodes) <= set(selected):
            raise ValueError('pilot_node_not_collected')
        state['selected'] = nodes
        for mode in ('serial', 'parallel'):
            prep_start = time.monotonic()
            stage = args.record_dir / mode
            stage.mkdir()
            jobs = []
            for i, group in enumerate(groups.values()):
                remaining(prepare_deadline, 'pilot_prepare')
                root = stage / ('root-' + str(i))
                shutil.copytree(args.target, root, ignore=shutil.ignore_patterns(
                    '.git', '.omc', '__pycache__', '.pytest_cache', 'node_modules'))
                remaining(prepare_deadline, 'pilot_prepare')
                if snapshot(root) != before:
                    raise ValueError('pilot_clone_identity_mismatch')
                remaining(prepare_deadline, 'pilot_prepare')
                jobs.append((root, stage / ('job-' + str(i)), group))
            prepared[mode] = jobs
            preparation_seconds[mode] = time.monotonic() - prep_start
    state['preparation_seconds'] = time.monotonic() - start
    for mode in ('serial', 'parallel'):
        jobs = prepared[mode]
        reserve = 360 if mode == 'serial' else 60
        run_start = time.monotonic()
        run_deadline = min(run_start + 300, deadline - reserve)
        remaining(run_deadline, 'pilot_' + mode)
        state['comparison_calls'] += 1
        def call(job):
            return call_pytest(*job, timeout=remaining(run_deadline, 'pilot_' + mode))
        with deadline_guard(run_deadline, 'pilot_' + mode):
            if mode == 'parallel':
                with ThreadPoolExecutor(max_workers=2) as pool:
                    native = list(pool.map(call, jobs))
            else:
                native = [call(job) for job in jobs]
        ended = time.monotonic()
        entries = [json.loads((j[1]/'invocation.json').read_text()) for j in jobs]
        overlap = max(e['started_monotonic'] for e in entries) < min(e['ended_monotonic'] for e in entries)
        state[mode] = {'executed': sorted(n for v in native for n in v['selected']),
                       'work_seconds': ended-run_start, 'preparation_seconds': preparation_seconds[mode],
                       'started_monotonic': run_start, 'ended_monotonic': ended, 'overlap': overlap}
        if state[mode]['executed'] != sorted(nodes):
            raise ValueError('pilot_execution_set_mismatch')
    with deadline_guard(deadline, 'pilot_finalize'):
        for jobs in prepared.values():
            for root, _, _ in jobs:
                if snapshot(root) != before:
                    raise ValueError('pilot_inputs_changed')
        state['source_preserved'] = snapshot(args.target) == before
        if not state['source_preserved']:
            raise ValueError('source_inputs_changed')
    state['status'] = 'measured'
    state['improvement_ratio'] = 1-state['parallel']['work_seconds']/state['serial']['work_seconds']
    state['scope'] = 'pilot_only_no_full_suite_performance_claim'


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('operation', choices=('run', 'inventory', 'pilot'))
    parser.add_argument('--target', type=Path, required=True)
    parser.add_argument('--record-dir', type=Path)
    parser.add_argument('--fast-file', action='append')
    parser.add_argument('--fast-timeout-sec', type=float, default=60)
    parser.add_argument('--timeout-sec', type=float, default=3600)
    parser.add_argument('--pilot-file', type=Path)
    args = parser.parse_args(argv)
    args.target = args.target.resolve()
    args.record_dir = (args.record_dir or args.target/'.omc/test-suite'/uuid.uuid4().hex).resolve()
    if (not args.target.is_dir() or args.record_dir.exists()
            or (_inside(args.record_dir, args.target) and '.omc' not in args.record_dir.relative_to(args.target).parts)
            or _inside(args.target, args.record_dir)
            or not 0 < args.timeout_sec <= 3600 or not 0 < args.fast_timeout_sec <= 60):
        parser.error('fresh external or internal .omc record path and bounded timeout required')
    args.record_dir.mkdir(parents=True, exist_ok=False)
    signal.signal(signal.SIGTERM, _interrupt)
    signal.signal(signal.SIGINT, _interrupt)
    start = time.monotonic()
    state = {'status': 'blocked', 'operation': args.operation, 'rest_calls': 0, 'executed': [], 'record_dir': str(args.record_dir)}
    try:
        if args.operation == 'pilot':
            if not args.pilot_file:
                raise ValueError('pilot_file_required')
            pilot(args, state)
        else:
            deadline = start + args.timeout_sec
            fast_deadline = min(deadline, start + args.fast_timeout_sec)
            selected = call_pytest(args.target, args.record_dir/'collect', ['scripts'],
                timeout=max(.001, fast_deadline-time.monotonic()), collect=True)['selected']
            fast, rest = partition(selected, args.fast_file or FAST_FILES)
            state.update(selected=selected, fast=fast, rest=rest)
            if args.operation == 'inventory':
                state['status'] = 'inventoried'
            else:
                call_pytest(args.target, args.record_dir/'fast', fast,
                            timeout=max(.001, fast_deadline-time.monotonic()))
                state['executed'].extend(fast)
                if rest:
                    state['rest_calls'] += 1
                    call_pytest(args.target, args.record_dir/'rest', rest,
                                timeout=max(.001, deadline-time.monotonic()))
                    state['executed'].extend(rest)
                if sorted(state['executed']) != sorted(selected):
                    raise ValueError('executed_selection_mismatch')
                state['status'] = 'passed'
    except (OSError, ValueError, KeyError, TypeError, subprocess.SubprocessError) as exc:
        state['reason'] = str(exc)
    finally:
        cleanup_errors = []
        for pid in tuple(_ACTIVE):
            try:
                _stop_group(pid)
            except OSError as exc:
                cleanup_errors.append(str(exc))
        if cleanup_errors:
            state.update(status='blocked', cleanup_confirmed=False, cleanup_errors=cleanup_errors)
        state['elapsed_seconds'] = time.monotonic() - start
        (args.record_dir/'final.json').write_text(json.dumps(state, indent=2))
    print(json.dumps(state))
    return 0 if state['status'] in ('passed', 'measured', 'inventoried') else 1


if __name__ == '__main__':
    raise SystemExit(main())
