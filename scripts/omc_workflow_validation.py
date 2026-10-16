#!/usr/bin/env python3
"""Collect a native task final verbatim and assess a controlled workflow run.

Observations are not attestations. Known verification arguments are checked;
this collector is not a sandbox or a general shell/filesystem auditor.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import shlex
from pathlib import Path

from omc_output_contract import OutputContractError, parse_envelope
from omc_pipeline_guard import _load_approved_basis, evaluate_task_completion

STATUSES = {
    '필수 검증 통과·작업 완료', '구현됨·검증 미완료',
    '구현 여부 확인 불가·검증 미완료', '검증 실패·수정 필요', '증거 불일치·재검증 필요',
}


def collect(root: Path, events_file: Path, sid: str, verification: Path,
            expected: str, output: Path) -> dict:
    source = events_file.read_bytes()
    rows = [json.loads(line) for line in source.decode('utf-8').splitlines() if line.strip()]
    for row in rows:
        if not isinstance(row, dict):
            raise ValueError('native event must be an object')
        message = row.get('message', {})
        if not isinstance(message, dict):
            raise ValueError('native event message must be an object')
        content = message.get('content', [])
        if not isinstance(content, list):
            raise ValueError('native message content must be a list')
        for block in content:
            if not isinstance(block, dict):
                raise ValueError('native content block must be an object')
            if block.get('type') == 'tool_use' and not isinstance(block.get('input', {}), dict):
                raise ValueError('native tool input must be an object')
    finals = [r for r in rows if r.get('type') == 'result']
    if len(finals) != 1 or not isinstance(finals[0].get('result'), str):
        raise ValueError('native result must contain exactly one final response')
    final = finals[0]['result']
    # Preserve the complete response; never normalize or repair explicit output.
    evaluation = evaluate_task_completion(root, verification, expected_session_id=sid)
    output.mkdir(parents=True, exist_ok=True)
    (output / 'final.txt').write_text(final, encoding='utf-8')
    issues = []
    try:
        envelope = parse_envelope(final)
        semantic = 'passed' if envelope['stage'] == 'task' and envelope['outcome'] == expected else 'failed'
    except OutputContractError as exc:
        envelope = None
        semantic = 'unknown'
        issues.append(str(exc))
    lines = [line.strip().strip('#*` ').strip() for line in final.splitlines() if line.strip()]
    # Declared body states are semantic evidence in both outcome directions.
    body_states = [line for line in lines if line in STATUSES]
    expected_states = {evaluation['first']}
    if any(status not in expected_states for status in body_states):
        semantic = 'failed'
        issues.append('본문 상태와 필수 검증 판정이 충돌합니다')
    substantive = [line for line in lines if line not in {'---', '***', '___'}]
    if semantic == 'passed':
        if not substantive or substantive[0] not in expected_states:
            semantic = 'unknown'
            issues.append('본문 상태를 확인할 수 없어 기계 판정만으로 통과시키지 않습니다')
        elif expected == 'blocked':
            # A named test passing is compatible with another required check
            # remaining unrun. Only whole-task claims require reconciliation.
            overall_claim = re.compile(
                r'(?:작업|전체|구현)\s*(?:을|이|은|는|이\s*)?\s*완료'
                r'|(?:모든|전체)\s*(?:필수\s*)?검증.*?통과'
            )
            body = [line for line in lines if line not in STATUSES
                    and not line.startswith(('<!-- OMC_OUTPUT:', 'OMC_OUTPUT:', 'VERDICT:'))]
            if any(overall_claim.search(line) for line in body):
                semantic = 'unknown'
                issues.append('전체 완료 주장은 별도 의미 확인이 필요합니다')
    format_quality = {
        'first_line': bool(lines) and final.splitlines()[0] in STATUSES,
        'unique_status': sum(line in STATUSES for line in lines) == 1,
        'single_next_action': sum(bool(re.match(r'^(?:[-+*]\s+|[0-9]+[.)]\s+)?(?:\*\*|__)?(?:다음 행동|다음 액션)(?:\*\*|__)?\s*:', line)) for line in lines) == 1,
    }
    registered = evaluation['registered']
    calls = {}
    evidence = []
    verification_paths = []
    known_writes = []
    for index, row in enumerate(rows):
        content = row.get('message', {}).get('content', [])
        if not isinstance(content, list):
            continue
        for block in content:
            if not isinstance(block, dict):
                continue
            if block.get('type') == 'tool_use':
                calls[block.get('id')] = (index, block)
                if block.get('name') in {'Write', 'Edit'}:
                    value = block.get('input', {}).get('file_path')
                    if isinstance(value, str):
                        known_writes.append(str((root / value).resolve()))
                if block.get('name') == 'Bash':
                    command = block.get('input', {}).get('command', '')
                    if 'complete-task' in command:
                        try:
                            args = shlex.split(command)
                            positions = [i for i, arg in enumerate(args) if arg == '--verification-file']
                            verification_paths.extend(args[i + 1] for i in positions if i + 1 < len(args))
                        except ValueError:
                            verification_paths.append('UNPARSEABLE')
            elif block.get('type') == 'tool_result' and block.get('tool_use_id') in calls:
                call_index, call = calls[block['tool_use_id']]
                command = call.get('input', {}).get('command', '')
                text = block.get('content', '')
                if not isinstance(text, str):
                    text = json.dumps(text, ensure_ascii=False)
                for token, marker in [('contract-done', 'CONTRACT 확인 완료'), ('red-done', 'RED 완료 등록')]:
                    if call.get('name') == 'Bash' and token in command and marker in text and not block.get('is_error'):
                        evidence.append((token, call_index))
                if call.get('name') == 'Bash' and 'complete-task' in command:
                    try:
                        gate = parse_envelope(text)
                        gate_states = [line.strip().strip('#*` ').strip() for line in text.splitlines()
                                       if line.strip().strip('#*` ').strip() in STATUSES]
                        if (gate['stage'] == 'task' and gate['outcome'] == expected == evaluation['outcome']
                                and gate_states and all(state == evaluation['first'] for state in gate_states)):
                            evidence.append(('complete-task', call_index))
                    except OutputContractError:
                        pass
    order = [next((i for name, i in evidence if name == token), None)
             for token in ['contract-done', 'red-done', 'complete-task']]
    procedure = all(i is not None for i in order) and order == sorted(order, key=lambda x: -1 if x is None else x)
    path_state = 'unknown'
    observed = []
    if verification_paths:
        path_state = 'passed'
        for value in verification_paths:
            candidate = (root / value).resolve()
            observed.append(str(candidate))
            try:
                candidate.relative_to(root.resolve())
                internal = True
            except ValueError:
                internal = False
            if value == 'UNPARSEABLE' or not internal or candidate != verification.resolve():
                path_state = 'failed'
        if not verification.is_file():
            path_state = 'unknown' if path_state == 'passed' else path_state
    for value in known_writes:
        try:
            Path(value).relative_to(root.resolve())
        except ValueError:
            path_state = 'failed'
    approved_scope = 'unknown'
    try:
        _load_approved_basis(root, sid)
        approved_scope = 'passed'
        if expected != evaluation['outcome'] or (envelope and envelope.get('outcome') != evaluation['outcome']):
            semantic = 'failed'
            issues.append('승인된 검증 기준과 최종 완료 주장이 충돌합니다')
        issues.extend(evaluation['problems'])
    except (OSError, ValueError, TypeError, AttributeError) as exc:
        issues.append(str(exc))
    required = {
        'approved_scope': approved_scope,
        'registration': 'passed' if registered else 'unknown' if registered is None else 'failed',
        'procedure_calls': 'passed' if procedure else 'unknown',
        'truthful_final': semantic,
        'verification_scope': path_state,
        'native_exit': 'passed' if finals[0].get('subtype') == 'success' and not finals[0].get('is_error') else 'failed',
    }
    assessment = {
        'schema_version': 'omc-workflow-validation/v1',
        'final_sha256': hashlib.sha256(final.encode('utf-8')).hexdigest(),
        'events_sha256': hashlib.sha256(source).hexdigest(),
        'required': required, 'format_quality': format_quality,
        'recompare_entry': 'allow' if all(value == 'passed' for value in required.values()) else 'hold',
        'issues': issues, 'observed_verification_paths': observed, 'observed_direct_write_paths': known_writes,
        'boundary': 'Known CLI arguments and returned outputs only; not filesystem containment or evidence authentication.',
    }
    (output / 'assessment.json').write_text(json.dumps(assessment, ensure_ascii=False, indent=2) + '\n')
    return assessment


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--target', type=Path, required=True)
    parser.add_argument('--events-file', type=Path, required=True)
    parser.add_argument('--session-id', required=True)
    parser.add_argument('--verification-file', type=Path, required=True)
    parser.add_argument('--expected-outcome', choices=['done', 'blocked'], required=True)
    parser.add_argument('--output-dir', type=Path, required=True)
    args = parser.parse_args()
    try:
        result = collect(args.target.resolve(), args.events_file, args.session_id,
                         args.verification_file, args.expected_outcome, args.output_dir)
    except (OSError, ValueError, TypeError) as exc:
        failure = {'schema_version': 'omc-workflow-validation/v1', 'recompare_entry': 'hold', 'error': str(exc)}
        args.output_dir.mkdir(parents=True, exist_ok=True)
        (args.output_dir / 'assessment.json').write_text(json.dumps(failure, ensure_ascii=False, indent=2) + '\n')
        print(json.dumps(failure, ensure_ascii=False))
        return 1
    print(json.dumps(result, ensure_ascii=False))
    return 0 if result['recompare_entry'] == 'allow' else 1


if __name__ == '__main__':
    raise SystemExit(main())
