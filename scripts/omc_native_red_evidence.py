#!/usr/bin/env python3
"""Opt-in Ad/Jest RED execution and native-call custody (local trust only).

Execution failures are unknown, never RED. Historical receipts survive GREEN;
registration checks the current candidate, completion checks the saved history.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import shlex
import subprocess
import sys
import tempfile
import uuid

TEST = 'libs/sellerhub-store/src/lib/components/Ad/Ad.test.tsx'
CONFIG = 'libs/sellerhub-store/jest.config.ts'
ARGV = ['./node_modules/.bin/jest', '--config', CONFIG, '--runInBand', '--runTestsByPath', TEST]
COMMAND = 'python3 scripts/omc_native_red_evidence.py observe --target .'
PREFIX = '.omc/native-red'
SCHEMA = 'omc-native-red-result/v1'


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def local(root, name):
    from omc_home_observation import local as runtime_path
    return runtime_path(root, name)


def source(root, name):
    if not isinstance(name, str) or not name or Path(name).is_absolute() or '..' in Path(name).parts:
        raise ValueError('native_red_source_path_invalid')
    path = root / name
    path.resolve().relative_to(root.resolve())
    if any(p.is_symlink() for p in [path, *path.parents] if p == root or root in p.parents):
        raise ValueError('native_red_source_symlink')
    return path


def write_new(root, name, value):
    path = local(root, name)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(dir=path.parent, prefix='.publish-')
    try:
        with os.fdopen(fd, 'w') as stream:
            json.dump(value, stream, ensure_ascii=False, indent=2)
            stream.flush()
            os.fsync(stream.fileno())
        os.link(temporary, path)  # Publish complete bytes without replacement.
    finally:
        os.unlink(temporary)
    return {'path': name, 'sha256': sha(path.read_bytes())}


def selected(root, ref):
    raw = local(root, ref['path']).read_bytes()
    if sha(raw) != ref.get('sha256'):
        raise ValueError('native_red_digest_mismatch')
    return json.loads(raw)


def session(root):
    latest = json.loads((root / '.omc/state/latest.json').read_bytes())
    sid = latest.get('latest_confirmed_session_id')
    if not isinstance(sid, str) or not sid or latest.get('latest_session_id', sid) != sid:
        raise ValueError('native_red_session_required')
    return sid


def policy(root, basis=None):
    from omc_pipeline_guard import _load_approved_basis
    basis = basis or _load_approved_basis(root, session(root))
    checks = [c for c in basis['checks'] if 'native_red' in c]
    if not checks:
        return None
    if len(checks) != 1 or checks[0]['id'] != 'tests':
        raise ValueError('native_red_check_invalid')
    pin = checks[0]['native_red']
    if (pin.get('schema_version') != 'omc-native-red-policy/v1' or pin.get('argv') != ARGV
            or pin.get('test_file') != TEST or not pin.get('run_id')
            or pin['run_id'] != basis.get('run_id') or basis['session_id'] != session(root)):
        raise ValueError('native_red_policy_invalid')
    local(root, pin['events_path'])
    paths = pin.get('product_paths')
    if not isinstance(paths, list) or not paths:
        raise ValueError('native_red_product_scope_required')
    for name in paths:
        source(root, name)
    baseline = validate_execution(root, pin['baseline'])
    if baseline['kind'] != 'baseline' or baseline['session_id'] != basis['session_id'] or baseline['run_id'] != pin['run_id']:
        raise ValueError('native_red_baseline_binding_mismatch')
    return pin


def jest_cases(value, exit_code):
    if not isinstance(value, dict) or type(exit_code) is not int or exit_code not in (0, 1):
        raise ValueError('native_red_execution_unknown')
    suites = value.get('testResults')
    if not isinstance(suites, list) or not suites or value.get('numRuntimeErrorTestSuites', 0):
        raise ValueError('native_red_jest_runtime_error')
    assertions = []
    for suite in suites:
        cases = suite.get('assertionResults')
        if suite.get('testExecError') or not isinstance(cases, list) or not cases:
            raise ValueError('native_red_jest_incomplete')
        if Path(suite.get('name', '')).name != Path(TEST).name:
            raise ValueError('native_red_wrong_suite')
        assertions.extend(cases)
    names = [c.get('fullName') for c in assertions]
    if any(not isinstance(n, str) or not n for n in names) or len(set(names)) != len(names):
        raise ValueError('native_red_case_identity_invalid')
    if any(c.get('status') not in {'passed', 'failed'} for c in assertions):
        raise ValueError('native_red_jest_incomplete')
    failed = sorted(c['fullName'] for c in assertions if c['status'] == 'failed')
    if (value.get('numTotalTests') != len(assertions) or value.get('numFailedTests') != len(failed)
            or value.get('numPassedTests') != len(assertions)-len(failed)
            or value.get('success') is not (not failed) or exit_code != int(bool(failed))):
        raise ValueError('native_red_jest_summary_mismatch')
    return failed


def execute(root, kind, run_id):
    from omc_pipeline_guard import _code_snapshot_digest, _load_state, _basis_digest, _load_approved_basis
    sid = session(root)
    pin = None if kind == 'baseline' else policy(root)
    if kind != 'baseline':
        state = _load_state(root)
        if not pin or not state.get('contract_confirmed') or state.get('session_id') != sid:
            raise ValueError('native_red_current_contract_required')
        run_id = pin['run_id']
    if not isinstance(run_id, str) or not run_id:
        raise ValueError('native_red_run_required')
    source(root, TEST); source(root, CONFIG)
    directory = f'{PREFIX}/executions/{uuid.uuid4().hex}'
    local(root, directory).mkdir(parents=True)
    output = directory + '/jest.json'
    before = _code_snapshot_digest(root)
    test_hash = sha(source(root, TEST).read_bytes())
    argv = ARGV + ['--json', '--outputFile', str(local(root, output))]
    record = {'schema_version': 'omc-native-red-execution/v1', 'kind': kind, 'session_id': sid,
              'run_id': run_id, 'argv': ARGV, 'test_file': TEST, 'test_sha256': test_hash,
              'before_snapshot': before, 'jest_path': output, 'status': 'unknown'}
    if pin:
        record.update(baseline=pin['baseline'], check_id='tests', basis_sha256=_basis_digest(_load_approved_basis(root, sid)))
    try:
        result = subprocess.run(argv, cwd=root, capture_output=True, timeout=120,
                                env={**os.environ, 'NX_DAEMON': 'false'})
        stdout, stderr, exit_code = result.stdout, result.stderr, result.returncode
    except subprocess.TimeoutExpired as exc:
        stdout, stderr, exit_code = exc.stdout or b'', exc.stderr or b'', None
    except OSError as exc:
        stdout, stderr, exit_code = b'', str(exc).encode(), None
    for key, raw in [('stdout', stdout), ('stderr', stderr)]:
        name = directory + '/' + key + '.txt'
        local(root, name).write_bytes(raw)
        record[key + '_path'] = name
        record[key + '_sha256'] = sha(raw)
    record.update(test_exit_code=exit_code, after_snapshot=_code_snapshot_digest(root),
                  test_after_sha256=sha(source(root, TEST).read_bytes()))
    try:
        raw = local(root, output).read_bytes()
        record['jest_sha256'] = sha(raw)
        record['failed_cases'] = jest_cases(json.loads(raw), exit_code)
        if record['before_snapshot'] != record['after_snapshot'] or record['test_sha256'] != record['test_after_sha256']:
            raise ValueError('native_red_candidate_changed_during_execution')
        record['status'] = 'observed'
    except (OSError, ValueError, TypeError, KeyError, AttributeError) as exc:
        record['reason'] = str(exc)
    ref = write_new(root, directory + '/execution.json', record)
    if record['status'] != 'observed':
        raise ValueError('native_red_execution_unknown:' + ref['path'])
    if kind == 'baseline':
        return ref
    return {'schema_version': SCHEMA, 'execution': ref, 'test_exit_code': exit_code,
            'failed_cases': record['failed_cases']}


def validate_execution(root, ref):
    record = selected(root, ref)
    if record.get('schema_version') != 'omc-native-red-execution/v1' or record.get('status') != 'observed' or record.get('argv') != ARGV:
        raise ValueError('native_red_execution_unknown')
    for key in ['stdout', 'stderr', 'jest']:
        raw = local(root, record[key + '_path']).read_bytes()
        if sha(raw) != record[key + '_sha256']:
            raise ValueError('native_red_raw_digest_mismatch')
    failed = jest_cases(json.loads(local(root, record['jest_path']).read_bytes()), record['test_exit_code'])
    if (record['failed_cases'] != failed or record['before_snapshot'] != record['after_snapshot']
            or record['test_sha256'] != record['test_after_sha256'] or record['test_file'] != TEST):
        raise ValueError('native_red_execution_binding_mismatch')
    return record


def exact_observe(command):
    try:
        return shlex.split(command) == shlex.split(COMMAND)
    except (ValueError, TypeError):
        return False


def extract(root, raw):
    calls, results, receipts = {}, set(), []
    initialized, offset = False, 0
    for line in raw.splitlines(keepends=True):
        if not line.endswith(b'\n'):
            break
        offset += len(line)
        row = json.loads(line)
        if row.get('type') == 'system' and row.get('subtype') == 'init':
            if initialized:
                raise ValueError('native_red_duplicate_init')
            initialized = True
        for block in row.get('message', {}).get('content', []):
            if block.get('type') == 'tool_use':
                ident = block['id']
                if not initialized or row.get('type') != 'assistant' or ident in calls:
                    raise ValueError('native_red_call_invalid')
                calls[ident] = block
            elif block.get('type') == 'tool_result':
                ident = block['tool_use_id']
                if ident not in calls or ident in results or row.get('type') != 'user':
                    raise ValueError('native_red_result_invalid')
                results.add(ident)
                call = calls[ident]
                if call.get('name') != 'Bash' or not exact_observe(call.get('input', {}).get('command')) or block.get('is_error'):
                    continue
                content = block.get('content')
                if isinstance(content, list):
                    content = ''.join(c.get('text', '') for c in content if c.get('type') == 'text')
                try:
                    value = json.loads(content)
                except (TypeError, ValueError):
                    continue
                if value.get('schema_version') != SCHEMA:
                    continue
                record = validate_execution(root, value['execution'])
                if value.get('test_exit_code') != record['test_exit_code'] or value.get('failed_cases') != record['failed_cases']:
                    raise ValueError('native_red_return_mismatch')
                receipts.append({'schema_version': 'omc-native-red-receipt/v1', 'call_id': ident,
                                 'prefix_bytes': offset, 'prefix_sha256': sha(raw[:offset]), 'observation': value})
    return receipts


def receipt_name(value):
    return f"{PREFIX}/receipts/{sha(json.dumps([value['call_id'], value['prefix_sha256']]).encode())}.json"


def publish(root):
    pin = policy(root)
    if pin is None:
        return False
    changed = False
    for receipt in extract(root, local(root, pin['events_path']).read_bytes()):
        name = receipt_name(receipt)
        if not local(root, name).exists():
            write_new(root, name, receipt)
            changed = True
    return changed


def references(root):
    directory = local(root, PREFIX + '/receipts')
    return [{'path': str(p.relative_to(root)), 'sha256': sha(p.read_bytes())}
            for p in sorted(directory.glob('*.json'))]


def validate_receipt(root, ref, historical=False, events_file=None):
    from omc_pipeline_guard import _code_snapshot_digest, _basis_digest, _load_approved_basis
    pin = policy(root)
    if pin is None:
        raise ValueError('native_red_policy_required')
    receipt = selected(root, ref)
    if ref['path'] != receipt_name(receipt):
        raise ValueError('native_red_receipt_path_mismatch')
    raw = local(root, pin['events_path']).read_bytes()
    size = receipt['prefix_bytes']
    if type(size) is not int or size <= 0 or size > len(raw) or sha(raw[:size]) != receipt['prefix_sha256']:
        raise ValueError('native_red_prefix_mismatch')
    if events_file is not None and events_file.read_bytes()[:size] != raw[:size]:
        raise ValueError('native_red_collected_prefix_mismatch')
    if receipt not in extract(root, raw[:size]):
        raise ValueError('native_red_call_binding_mismatch')
    record = validate_execution(root, receipt['observation']['execution'])
    basis = _load_approved_basis(root, session(root))
    if (record['session_id'] != basis['session_id'] or record['run_id'] != pin['run_id']
            or record.get('basis_sha256') != _basis_digest(basis) or record.get('baseline') != pin['baseline']
            or record.get('check_id') != 'tests' or record['test_exit_code'] != 1):
        raise ValueError('native_red_observation_binding_mismatch')
    if not historical and (record['after_snapshot'] != _code_snapshot_digest(root)
                           or record['test_sha256'] != sha(source(root, TEST).read_bytes())):
        raise ValueError('native_red_registration_snapshot_mismatch')
    return record


def make_evidence(root, ref, case):
    record = validate_receipt(root, ref)
    baseline = validate_execution(root, record['baseline'])
    if case not in set(record['failed_cases']) - set(baseline['failed_cases']):
        raise ValueError('native_red_case_not_new_failure')
    output = 'stderr' if local(root, record['stderr_path']).read_bytes().strip() else 'stdout'
    value = {'session_id': record['session_id'], 'basis_sha256': record['basis_sha256'], 'test_file': TEST,
             'test_sha256': record['test_sha256'], 'check_id': 'tests', 'case_id': case,
             'expected_failure': case, 'command': shlex.join(ARGV), 'exit_code': 1,
             'baseline_failed_cases': baseline['failed_cases'], 'failed_cases': record['failed_cases'],
             'output_file': record[output + '_path'], 'output_sha256': record[output + '_sha256'], 'native_receipt': ref}
    return write_new(root, f'{PREFIX}/evidence/{uuid.uuid4().hex}.json', value)


def validate_evidence(root, doc, historical=False, events_file=None):
    record = validate_receipt(root, doc['native_receipt'], historical, events_file)
    baseline = validate_execution(root, record['baseline'])
    output = 'stderr' if local(root, record['stderr_path']).read_bytes().strip() else 'stdout'
    expected = {'test_file': TEST, 'test_sha256': record['test_sha256'], 'check_id': 'tests',
                'command': shlex.join(ARGV), 'failed_cases': record['failed_cases'],
                'baseline_failed_cases': baseline['failed_cases'], 'output_file': record[output + '_path'],
                'output_sha256': record[output + '_sha256'], 'exit_code': 1}
    if any(doc.get(k) != v for k, v in expected.items()) or doc.get('case_id') not in set(record['failed_cases'])-set(baseline['failed_cases']):
        raise ValueError('native_red_evidence_mismatch')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=['baseline', 'observe', 'receipts', 'evidence', 'run'])
    parser.add_argument('--target', type=Path, default=Path.cwd())
    parser.add_argument('--run-id')
    parser.add_argument('--receipt')
    parser.add_argument('--receipt-sha256')
    parser.add_argument('--case-id')
    parser.add_argument('--launch', nargs=argparse.REMAINDER)
    args = parser.parse_args()
    root = args.target.resolve()
    try:
        if args.action == 'run':
            from omc_home_observation import tee_native
            pin = policy(root)
            if not pin or not args.launch:
                raise ValueError('native_red_policy_and_launch_required')
            from omc_home_observation import REQUIREMENT, run_native
            if (root / REQUIREMENT).exists():
                return run_native(root, args.launch)  # Publish both selected contracts.
            return tee_native(root, args.launch, local(root, pin['events_path']), [lambda: publish(root)])
        if args.action in {'baseline', 'observe'}:
            result = execute(root, args.action, args.run_id)
        elif args.action == 'receipts':
            result = references(root)
        else:
            result = make_evidence(root, {'path': args.receipt, 'sha256': args.receipt_sha256}, args.case_id)
        print(json.dumps(result, ensure_ascii=False))
        return 0
    except (OSError, ValueError, KeyError, TypeError, AttributeError) as exc:
        print(json.dumps({'status': 'unknown', 'reason': str(exc)}, ensure_ascii=False))
        return 1


if __name__ == '__main__':
    sys.exit(main())
