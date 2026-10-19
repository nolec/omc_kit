#!/usr/bin/env python3
"""Read-only peer trace observations, not a shell auditor or execution guard.

Tool returns are observations, not attestations. Verification claims in prose
are never promoted to passed. No OMC completion protocol is imposed on peers.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import shlex
from pathlib import Path

CHECKS = ('tests', 'lint', 'browser-controlled', 'browser-home', 'browser-visual-controlled')
SKILL = 'oh-my-claudecode:execute'


def state(status, reason, events=()):
    return {'status': status, 'reason': reason, 'events': list(events)}


def destination(value, root, paths):
    if not isinstance(value, str) or not value or any(c in value for c in '$`*?\n'):
        return 'unknown'
    # Lexical paths only: consulting today's symlinks cannot prove past targets.
    p = Path(value)
    if '..' in p.parts:
        return 'unknown'
    try:
        rel = p.relative_to(root) if p.is_absolute() else p
    except ValueError:
        return 'unknown'
    if any(rel == base or base in rel.parents for base in paths):
        return 'code'
    # Do not assume an artifact path could not have been a symlink at execution.
    # Current filesystem evidence alone cannot settle historical writes.
    return 'unknown'


def bash_kind(command, root, paths):
    if not isinstance(command, str) or any(c in command for c in '$`;&|\n\\()<>'):
        # A narrow literal redirect is supported; nested/shell expansion is not.
        if isinstance(command, str) and command.count('>') == 1 and not any(
                c in command for c in '$`;&|\n\\()<>'.replace('>', '')):
            left, right = command.split('>')
            try:
                args, targets = shlex.split(left), shlex.split(right)
            except ValueError:
                return 'unknown'
            if args and args[0] in {'echo', 'printf'} and len(targets) == 1:
                return destination(targets[0], root, paths)
        return 'unknown'
    try:
        args = shlex.split(command)
    except ValueError:
        return 'unknown'
    if not args:
        return 'read'
    if args[0] == 'touch' and len(args) > 1 and all(not a.startswith('-') for a in args[1:]):
        kinds = [destination(a, root, paths) for a in args[1:]]
        return 'code' if 'code' in kinds else 'unknown'
    # Exact invocations only. No arbitrary git/read flags, aliases or scripts.
    if args in [['pwd'], ['git', 'status', '--short'], ['git', 'diff', 'HEAD'],
                ['git', 'rev-parse', 'HEAD']]:
        return 'read'
    return 'unknown'


def analyze(source, root, paths, provenance):
    calls, returns, problems = {}, {}, []
    try:
        rows = [json.loads(line) for line in source.decode('utf-8').splitlines() if line.strip()]
        if not rows or any(not isinstance(r, dict) for r in rows):
            raise ValueError('non_object_event')
        inits = [i for i, r in enumerate(rows) if r.get('type') == 'system' and r.get('subtype') == 'init']
        complete = (len(inits) == 1 and all(r.get('type') == 'system' for r in rows[:inits[0]])
                    and rows[-1].get('type') == 'result'
                    and isinstance(rows[-1].get('result'), str)
                    and sum(r.get('type') == 'result' for r in rows) == 1)
        if not complete:
            problems.append('incomplete_trace')
        for index, row in enumerate(rows):
            msg = row.get('message', {})
            if not isinstance(msg, dict) or not isinstance(msg.get('content', []), list):
                raise ValueError('invalid_message')
            for block in msg.get('content', []):
                if not isinstance(block, dict):
                    raise ValueError('invalid_content')
                if block.get('type') == 'tool_use':
                    ident = block.get('id')
                    if (row.get('type') != 'assistant' or not isinstance(ident, str) or not ident
                            or ident in calls or not isinstance(block.get('input'), dict)
                            or not isinstance(block.get('name'), str)):
                        raise ValueError('invalid_or_duplicate_call')
                    calls[ident] = (index, block)
                elif block.get('type') == 'tool_result':
                    ident = block.get('tool_use_id')
                    if (row.get('type') != 'user' or not isinstance(ident, str)
                            or ident not in calls or ident in returns
                            or not isinstance(block.get('content'), (str, list))
                            or type(block.get('is_error', False)) is not bool):
                        raise ValueError('invalid_or_unmatched_return')
                    returns[ident] = (index, not block.get('is_error', False))
        if calls.keys() != returns.keys():
            problems.append('missing_tool_return')
    except (ValueError, UnicodeError, TypeError) as exc:
        problems.append(str(exc))

    entry_calls = {i: (n, b) for i, (n, b) in calls.items()
                   if b.get('name') == 'Skill' and b['input'].get('skill') == SKILL}
    successes = [(returns[i][0], i) for i in entry_calls if i in returns and returns[i][1]]
    if problems:
        entry = state('unknown', 'trace_integrity_unconfirmed')
    elif successes:
        entry = state('passed', 'skill_return_observed', [min(successes)[0]])
    else:
        entry = state('failed', 'skill_error' if entry_calls else 'skill_not_called')

    writes, uncertain = [], []
    for ident, (index, block) in calls.items():
        name, args = block.get('name'), block['input']
        kind = 'read'
        if name in {'Write', 'Edit'}:
            kind = destination(args.get('file_path'), root, paths)
        elif name == 'Bash':
            kind = bash_kind(args.get('command'), root, paths)
        elif name not in {'Read', 'Glob', 'Grep', 'Skill'}:
            kind = 'unknown'
        if kind == 'code':
            # An error may still have partial effects; it is never proof of no write.
            if ident in returns and returns[ident][1]:
                writes.append(index)
            else:
                uncertain.append(index)
        elif kind == 'unknown':
            uncertain.append(index)
    returned = min(successes)[0] if successes else None
    if problems:
        order = state('unknown', 'trace_integrity_unconfirmed')
    elif writes and (returned is None or min(writes) < returned):
        order = state('failed', 'code_write_before_successful_entry', writes)
    elif returned is None or not writes:
        order = state('unknown', 'no_orderable_entry_and_code_write')
    elif min(writes) == returned or any(i <= returned for i in uncertain):
        order = state('unknown', 'possible_earlier_write', uncertain)
    else:
        order = state('passed', 'observed_code_write_after_entry', writes)

    # Raw tool output/final prose does not establish five semantic quality checks.
    # Keep all five present for a separate candidate-bound verification assessor.
    checks = {i: state('unknown', 'separate_native_verification_assessment_required') for i in CHECKS}
    data = {'schema_version': 'omc-peer-trace/v1', 'provenance': provenance,
            'source_sha256': hashlib.sha256(source).hexdigest(), 'event_count': len(rows) if 'rows' in locals() else 0,
            'entry_call_count': None if problems else len(entry_calls), 'entry': entry, 'write_order': order,
            'checks': checks, 'comparison_eligible': False, 'trace_issues': problems,
            'limits': ['Read-only observations, not a write-prevention mechanism.',
                       'Tool success does not prove instruction compliance or external effects.',
                       'Final diff is not chronological evidence.',
                       'Current aliases, executables and historical symlinks are not authenticated.']}
    # Without authenticated historical target/executable identities, syntactic
    # passing order is an observation only, never eligibility or an attestation.
    data['report'] = render(data)
    return data


def render(data):
    labels = {'passed': '관찰 통과', 'failed': '미충족', 'unknown': '확인 불가'}
    count = ('확정 불가' if data['entry_call_count'] is None
             else str(data['entry_call_count']) + '회')
    return '\n'.join([
        '기록 종류: ' + ('합성 기록 — 실제 진입 성공 증거 아님' if data['provenance'] == 'synthetic'
                       else '실제 실행 기록 — 진위 인증 아님'),
        '도구 진입: ' + labels[data['entry']['status']] + ' — execute 호출 ' + count,
        '수정 순서: ' + labels[data['write_order']['status']],
        '필수 검증: 확인 불가 — 5개 항목의 별도 증거 평가 필요',
        '운영자 사후 검증은 실행 중 검증으로 대체하지 않습니다.',
        '비교 표본 채택: 보류',
    ])


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--events', type=Path, required=True)
    parser.add_argument('--root', type=Path, required=True)
    parser.add_argument('--work-path', action='append', required=True)
    parser.add_argument('--provenance', choices=['synthetic', 'native'], required=True)
    args = parser.parse_args()
    paths = [Path(p) for p in args.work_path]
    if any(p.is_absolute() or '..' in p.parts or p == Path('.') for p in paths):
        parser.error('work paths must be nonempty relative subpaths')
    data = analyze(args.events.read_bytes(), args.root.resolve(), paths, args.provenance)
    print(json.dumps(data, ensure_ascii=False, indent=2))
    return 2 if data['trace_issues'] else 0


if __name__ == '__main__':
    raise SystemExit(main())
