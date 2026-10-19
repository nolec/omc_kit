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
    """Same query and server-binding machinery as operator preparation, new output."""
    from omc_pipeline_guard import _code_snapshot_digest
    from omc_workflow_preflight import browser_observation, server_binding
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
    observed = browser_observation(browser)
    server_binding(root, binding['config'], browser, observed=observed,
                   previous=binding['evidence'], initial=False)
    if snapshot != _code_snapshot_digest(root):
        raise ValueError('home_candidate_changed_during_observation')
    return {**observed, 'schema_version': SCHEMA, 'session_id': req['session_id'],
            'run_id': req['run_id'], 'code_snapshot_sha256': snapshot, 'server_binding': 'passed'}


def _is_observe(command: str, root: Path) -> bool:
    try:
        tokens = shlex.split(command)
    except (ValueError, TypeError):
        return False
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


def run_native(root: Path, argv: list[str]) -> int:
    """Tee a single native invocation; publish tool results while it is running."""
    req = requirement(root)
    from omc_pipeline_guard import _load_approved_basis
    enforce_requirement(root, _load_approved_basis(root, req['session_id']))
    events = local(root, req['events_path'])
    events.parent.mkdir(parents=True, exist_ok=True)
    if receipt_references(root):
        raise ValueError('home_receipt_already_exists')
    from omc_native_red_evidence import policy, publish as publish_red
    pin = policy(root)
    if pin is not None and local(root, pin['events_path']) != events:
        raise ValueError('native_red_home_stream_mismatch')
    publishers = [lambda: publish(root)]
    if pin is not None:
        publishers.append(lambda: publish_red(root))
    return tee_native(root, argv, events, publishers)


def tee_native(root: Path, argv: list[str], events: Path, publishers: list) -> int:
    """Shared complete-line custody for explicitly selected native publishers."""
    events.parent.mkdir(parents=True, exist_ok=True)
    with events.open('xb') as output:
        with subprocess.Popen(argv, cwd=root, stdout=subprocess.PIPE) as proc:
            try:
                for line in proc.stdout:
                    output.write(line)
                    output.flush()
                    if line.endswith(b'\n'):
                        for publisher in publishers:
                            publisher()
                    sys.stdout.buffer.write(line)
                    sys.stdout.buffer.flush()
            except BaseException:
                proc.terminate()
                proc.wait(timeout=10)
                raise
            return proc.wait()


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
        print(json.dumps({'status': 'unknown', 'reason': str(exc)}))
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
