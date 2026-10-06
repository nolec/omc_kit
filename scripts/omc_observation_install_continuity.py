#!/usr/bin/env python3
"""Explicit one-hop installation continuity; never rewrite an observation policy."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
from pathlib import Path

import omc_install_audit
import omc_state

PROOF_FILE = '.omc/observations/install-continuity.json'
SCHEMA = 'omc-observation-install-continuity/v1'


class ContinuityError(ValueError):
    pass


def _hash(data):
    return hashlib.sha256(json.dumps(data, sort_keys=True, separators=(',', ':')).encode()).hexdigest()


def _read(path):
    if path.is_symlink() or not path.is_file():
        raise ContinuityError('continuity_file_invalid')
    value = json.loads(path.read_bytes())
    if not isinstance(value, dict):
        raise ContinuityError('continuity_file_invalid')
    return value


def _publish(path, value):
    if path.exists() or path.is_symlink():
        if _read(path) != value:
            raise ContinuityError('continuity_conflict')
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open('x', encoding='utf-8') as handle:
        json.dump(value, handle, ensure_ascii=False, sort_keys=True)
        handle.flush()
        os.fsync(handle.fileno())


def _registration_files(root):
    paths = [root / '.omc' / name for name in (
        'observation-policy.json', 'skill-effectiveness-cohort-v3.json',
        'skill-effectiveness-cohort-v3-transition.json', 'observations/transition.json')]
    paths.extend((root / '.omc/observations/studies').glob('*/policy.json'))
    return {str(p.relative_to(root)): hashlib.sha256(p.read_bytes()).hexdigest()
            for p in sorted(paths) if p.is_file() and not p.is_symlink()}


def _identity(root):
    path = root / '.omc/install-receipt.json'
    if path.is_symlink() or not path.is_file():
        raise ContinuityError('continuity_install_invalid')
    raw = path.read_bytes()
    receipt = json.loads(raw)
    if (not isinstance(receipt, dict)
            or re.fullmatch('[0-9a-f]{64}', str(receipt.get('source_sha256'))) is None
            or not isinstance(receipt.get('source_revision'), str) or not receipt['source_revision'].strip()
            or not isinstance(receipt.get('omc_version'), str) or not receipt['omc_version'].strip()):
        raise ContinuityError('continuity_install_invalid')
    audit = omc_install_audit.audit_target(root, install_receipt_bytes=raw)
    if (audit.get('installed_integrity_status') != 'ok'
            or receipt.get('target') != str(root) or path.read_bytes() != raw):
        raise ContinuityError('continuity_install_invalid')
    return {'install_receipt_sha256': hashlib.sha256(raw).hexdigest(),
            'source_sha256': receipt['source_sha256'], 'source_revision': receipt['source_revision'],
            'source_version': receipt['omc_version']}


def _valid_identity(value, *, receipt):
    keys = {'source_sha256', 'source_revision', 'source_version'}
    if receipt:
        keys.add('install_receipt_sha256')
    return (isinstance(value, dict) and set(value) == keys
            and all(isinstance(v, str) and v.strip() for v in value.values())
            and re.fullmatch('[0-9a-f]{64}', value['source_sha256']) is not None
            and re.fullmatch(r'\d+\.\d+\.\d+', value['source_version']) is not None
            and (not receipt or re.fullmatch('[0-9a-f]{64}', value['install_receipt_sha256']) is not None))


def _checked(value, kind):
    if (not isinstance(value, dict) or value.get('schema_version') != SCHEMA or value.get('kind') != kind
            or value.get('sha256') != _hash({k:v for k,v in value.items() if k != 'sha256'})):
        raise ContinuityError('continuity_proof_invalid')
    if kind == 'plan':
        keys = {'schema_version', 'kind', 'root', 'before', 'expected', 'registration_files', 'sha256'}
        files = value.get('registration_files')
        if (set(value) != keys or not isinstance(value.get('root'), str)
                or not Path(value['root']).is_absolute()
                or not _valid_identity(value.get('before'), receipt=True)
                or not _valid_identity(value.get('expected'), receipt=False)
                or not isinstance(files, dict) or not files
                or any(not isinstance(path, str) or not path.startswith('.omc/')
                       or '..' in Path(path).parts or not isinstance(digest, str)
                       or re.fullmatch('[0-9a-f]{64}', digest) is None
                       for path, digest in files.items())):
            raise ContinuityError('continuity_plan_invalid')
    elif kind == 'proof':
        if (set(value) != {'schema_version', 'kind', 'plan', 'after', 'sha256'}
                or not _valid_identity(value.get('after'), receipt=True)):
            raise ContinuityError('continuity_proof_invalid')
        plan = _checked(value['plan'], 'plan')
        if any(value['after'][key] != item for key, item in plan['expected'].items()):
            raise ContinuityError('continuity_plan_mismatch')
    else:
        raise ContinuityError('continuity_proof_invalid')
    return value


def prepare(root, *, expected_source_sha256, expected_source_revision, expected_version, output):
    root = root.resolve()
    if output.is_symlink() or output.resolve().is_relative_to(root):
        raise ContinuityError('continuity_external_plan_required')
    if (re.fullmatch('[0-9a-f]{64}', expected_source_sha256) is None
            or not expected_source_revision.strip() or not expected_version.strip()):
        raise ContinuityError('continuity_target_invalid')
    with omc_state._omc_lock(root):
        if (root / PROOF_FILE).exists() or (root / PROOF_FILE).is_symlink():
            raise ContinuityError('continuity_already_sealed')
        value = {'schema_version': SCHEMA, 'kind': 'plan', 'root': str(root),
                 'before': _identity(root), 'registration_files': _registration_files(root),
                 'expected': {'source_sha256': expected_source_sha256,
                              'source_revision': expected_source_revision, 'source_version': expected_version}}
        if not value['registration_files']:
            raise ContinuityError('continuity_registration_required')
        value['sha256'] = _hash(value)
        _checked(value, 'plan')
        _publish(output, value)
        return value


def seal(root, *, plan_path):
    root = root.resolve()
    if plan_path.resolve().is_relative_to(root):
        raise ContinuityError('continuity_external_plan_required')
    with omc_state._omc_lock(root):
        plan = _checked(_read(plan_path), 'plan')
        current = _identity(root)
        if (plan['root'] != str(root) or plan['registration_files'] != _registration_files(root)
                or any(current.get(k) != v for k,v in plan['expected'].items())):
            raise ContinuityError('continuity_plan_mismatch')
        value = {'schema_version': SCHEMA, 'kind': 'proof', 'plan': plan, 'after': current}
        value['sha256'] = _hash(value)
        _publish(root / PROOF_FILE, value)
        return value


def installation_matches(root, expected, actual):
    """Called only after the consumer has independently audited the live install."""
    if expected == actual:
        return True
    try:
        root = root.resolve()
        proof = _checked(_read(root / PROOF_FILE), 'proof')
        plan = _checked(proof['plan'], 'plan')
        if (plan['root'] != str(root) or plan['registration_files'] != _registration_files(root)
                or proof['after'] != _identity(root)
                or any(proof['after'].get(k) != v for k,v in plan['expected'].items())):
            return False
        aliases = {'installed_source_sha256':'source_sha256', 'installed_source_revision':'source_revision',
                   'installed_omc_version':'source_version'}
        return (set(expected) == set(actual) and bool(expected)
                and all(plan['before'].get(aliases.get(k,k)) == v for k,v in expected.items())
                and all(proof['after'].get(aliases.get(k,k)) == v for k,v in actual.items()))
    except (ContinuityError, OSError, ValueError, KeyError, TypeError):
        return False


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest='command', required=True)
    before = sub.add_parser('prepare')
    before.add_argument('--expected-source-sha256', required=True)
    before.add_argument('--expected-source-revision', required=True)
    before.add_argument('--expected-version', required=True)
    before.add_argument('--out', type=Path, required=True)
    after = sub.add_parser('seal')
    after.add_argument('--plan', type=Path, required=True)
    for command in (before, after):
        command.add_argument('--target', type=Path, required=True)
    args = parser.parse_args()
    try:
        if args.command == 'prepare':
            result = prepare(args.target, expected_source_sha256=args.expected_source_sha256,
                expected_source_revision=args.expected_source_revision, expected_version=args.expected_version,
                output=args.out)
        else:
            result = seal(args.target, plan_path=args.plan)
        print(json.dumps(result, ensure_ascii=False, sort_keys=True))
        return 0
    except (ContinuityError, OSError, ValueError, KeyError, TypeError) as error:
        print(json.dumps({'status':'BLOCKED', 'reason':str(error)}))
        return 2


if __name__ == '__main__':
    raise SystemExit(main())
