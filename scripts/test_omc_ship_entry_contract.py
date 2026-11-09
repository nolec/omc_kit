"""Execute the documented ship flow only against disposable, synthetic repositories.

This interpreter is a regression surface, not a production ship runner or an
authorization service. No real project or consumer is executed or installed.
"""
import copy
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import time

import pytest


ROOT = Path(__file__).resolve().parents[1]
TEMPLATES = [Path('templates/.agents/skills/omc-ship/SKILL.md'),
             Path('templates/.claude/commands/ship.md')]
INPUTS = [*TEMPLATES, Path('docs/omc_quality_gates.md'),
          Path('scripts/test_omc_ship_entry_contract.py'),
          Path('scripts/test_omc_generated_install_isolation.py')]
ORDER = ['control_init', 'control_guard', 'source_review', 'source_quality',
         'prepare', 'capture', 'seal', 'record', 'approve', 'check',
         'candidate_review', 'candidate_quality', 'tdd', 'run']
COMMANDS = {
    'control_init': ('omc.py', ['state', 'init'], 'control'),
    'control_guard': ('omc_guard.py', ['sync-require'], 'control'),
    'source_review': ('omc_review_snapshot.py', ['validate-ship'], 'source'),
    'source_quality': ('omc_quality_gate.py', ['status'], 'source'),
    'prepare': ('omc_ship_preflight.py', ['prepare'], 'candidate'),
    'capture': ('omc_review_snapshot.py', ['capture-review'], 'candidate'),
    'seal': ('omc_review_snapshot.py', ['seal-review-output'], 'candidate'),
    'record': ('omc_review_snapshot.py', ['record-review'], 'candidate'),
    'approve': ('omc_quality_gate.py', ['approve'], 'candidate'),
    'check': ('omc_ship_preflight.py', ['check'], 'candidate'),
    'candidate_review': ('omc_review_snapshot.py', ['validate-ship'], 'candidate'),
    'candidate_quality': ('omc_quality_gate.py', ['status'], 'candidate'),
    'tdd': ('omc_tdd_check.py', [], 'candidate'),
    'run': ('omc_quality_gate.py', ['run'], 'candidate'),
}


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def read_contract(path, *, raw=None):
    matches = re.findall(r'<!-- OMC_SHIP_ENTRY_CONTRACT:BEGIN -->\s*```json\s*(.*?)\s*```\s*<!-- OMC_SHIP_ENTRY_CONTRACT:END -->',
                         path.read_text() if raw is None else raw.decode('utf-8'), re.S)
    assert len(matches) == 1, 'ship entry contract missing or duplicated'
    data = json.loads(matches[0])
    assert data['schema_version'] == 'omc-ship-entry/v1'
    assert data['budget_seconds'] == {'input': 40, 'prepare': 240, 'bind': 40, 'check': 90, 'finalize': 70}
    assert data['total_prepare_seconds'] == 480
    assert [s['id'] for s in data['steps']] == ORDER, 'missing or reordered ship step'
    assert [s['stage'] for s in data['steps']] == (
        ['input'] * 4 + ['prepare'] + ['bind'] * 4 + ['check'] * 4 + ['run'])
    for step in data['steps']:
        script, commands, target = COMMANDS[step['id']]
        argv = step['argv']
        assert argv[:2] == ['{python}', '{kit}/scripts/' + script]
        assert argv.count('--target') == 1 and argv[argv.index('--target') + 1] == '{' + target + '}'
        assert all(cmd in argv for cmd in commands)
        assert step['cwd'] == '{control}', 'working directory must not default to source'
    return data


def git(root, *args):
    return subprocess.check_output(['git', *args], cwd=root,
        env={**os.environ, 'GIT_OPTIONAL_LOCKS': '0'}, stderr=subprocess.PIPE).decode().strip()


def public(script, args, cwd=ROOT, *, codes=(0,)):
    result = subprocess.run([sys.executable, str(ROOT / 'scripts' / script), *args],
                            cwd=cwd, capture_output=True, text=True, timeout=30)
    assert result.returncode in codes, result.stdout + result.stderr
    return json.loads(result.stdout)


@pytest.fixture
def subject(tmp_path):
    source = tmp_path / 'source'
    source.mkdir()
    git(source, 'init', '-b', 'main')
    git(source, 'config', 'user.name', 'Synthetic')
    git(source, 'config', 'user.email', 'synthetic@example.invalid')
    (source / '.gitignore').write_text('.omc/\n')
    (source / 'probe.txt').write_text('synthetic quality evidence\n')
    git(source, 'add', '.')
    git(source, 'commit', '-m', 'baseline')
    base = git(source, 'rev-parse', 'HEAD')
    (source / 'result.md').write_text('candidate\n')
    git(source, 'add', 'result.md')
    git(source, 'commit', '-m', 'candidate')
    head = git(source, 'rev-parse', 'HEAD')
    git(source, 'update-ref', 'refs/remotes/origin/main', base)
    metadata = source / '.omc'
    metadata.mkdir()
    (metadata / 'pipeline_session.json').write_text('{"preserved":"original session"}')
    marker = tmp_path / 'quality-calls.txt'
    config = {'schema_version': 'omc-quality-gates/v1', 'base_ref': 'origin/main',
        'evidence': [{'path': 'probe.txt', 'sha256': digest(source / 'probe.txt')}],
        'gates': [{'id': 'test', 'purpose': 'test', 'scope': 'full', 'required': True,
                   'argv': [sys.executable, '-c',
                       "from pathlib import Path; p=Path(%r); p.write_text('called'); raise SystemExit(0)" % str(marker)],
                   'timeout_sec': 10}]}
    config_file = metadata / 'quality-gates.json'
    config_file.write_text(json.dumps(config))
    status = public('omc_quality_gate.py', ['--target', str(source), 'status'], codes=(1,))
    canonical_sha = status['config_sha256']
    public('omc_quality_gate.py', ['--target', str(source), 'approve', '--config-sha256', canonical_sha, '--allow-full'])
    review = tmp_path / 'approved-review.txt'
    review.write_text('APPROVE: synthetic result.md only; not real ship approval.')
    snap = public('omc_review_snapshot.py', ['capture-review', '--target', str(source),
        '--base-commit', base, '--candidate-commit', head])
    sealed = public('omc_review_snapshot.py', ['seal-review-output', '--target', str(source),
        '--review-snapshot', snap['snapshot_path'], '--review-snapshot-sha256', snap['snapshot_sha256'],
        '--review-output', str(review)])
    public('omc_review_snapshot.py', ['record-review', '--target', str(source),
        '--review-snapshot', snap['snapshot_path'], '--review-snapshot-sha256', snap['snapshot_sha256'],
        '--review-evidence', sealed['evidence_path'], '--review-evidence-sha256', sealed['evidence_sha256'],
        '--verdict', 'APPROVE', '--expected-path', 'result.md'])
    contract = tmp_path / 'preflight.json'
    contract.write_text(json.dumps({'schema_version': 'omc-ship-preflight/v1',
        'source_root': str(source), 'expected_head_oid': head, 'base_ref': 'origin/main',
        'expected_base_oid': base, 'quality_config_sha256': digest(config_file),
        'expected_changed_paths': ['result.md']}))
    control = tmp_path / 'control'
    control.mkdir()
    return {'source': str(source), 'control': str(control), 'candidate': str(tmp_path / 'candidate'),
        'prepare_record': str(tmp_path / 'prepare-record'), 'check_record': str(tmp_path / 'check-record'),
        'preflight': str(contract), 'review_output': str(review), 'review_output_sha256': digest(review),
        'head': head, 'base': base, 'config_sha256': canonical_sha,
        'expected_path_args': ['--expected-path', 'result.md'], 'quality_scope_args': ['--allow-full'],
        'marker': str(marker), 'authorized': True, 'max_attempts': 1, 'run_timeout': 30,
        'run_finalize_seconds': 70,
        'python': sys.executable, 'kit': str(ROOT)}


def expand(value, context):
    match = re.fullmatch(r'\{([^{}]+)\}', value)
    if match:
        key = match[1]
        if '.' in key:
            a, b = key.split('.', 1)
            return context[a][b]
        return context[key]
    for key in ['kit']:
        value = value.replace('{' + key + '}', context[key])
    assert '{' not in value and '}' not in value, 'unknown placeholder'
    return value


def preserve(root):
    files = [root / '.git/index', root / '.git/config', root / '.git/HEAD',
             root / '.omc/pipeline_session.json', root / 'probe.txt', root / 'result.md']
    files += list((root / '.git/refs').rglob('*'))
    files = [f for f in files if f.is_file()]
    files += list((root / '.omc/state').rglob('*.json'))
    return {str(f): (digest(f), f.stat().st_mode & 0o777) for f in files if f.is_file()}


def verify_preflight(result, operation, context, elapsed):
    """Consume CLI completion and immutable raw files, never disk status alone."""
    assert result.returncode == 0
    payload = json.loads(result.stdout)
    assert payload['status'] == 'baseline_verified' and payload['operation'] == operation
    assert payload['candidate'] == context['head'] and payload['base_oid'] == context['base']
    assert payload['target'] == context['candidate']
    assert payload['contract_sha256'] == digest(Path(context['preflight']))
    contract = json.loads(Path(context['preflight']).read_text())
    assert payload['config_sha256'] == contract['quality_config_sha256']
    assert payload['cleanup_confirmed'] and payload['evidence_complete']
    assert elapsed <= {'prepare': 240, 'check': 90}[operation]
    directory = Path(context[operation + '_record'])
    assert payload['record_dir'] == str(directory)
    for item in payload['raw_evidence']:
        name = Path(item['name'])
        assert name.name == str(name) and not name.is_absolute()
        file = directory / name
        assert not file.is_symlink() and digest(file) == item['sha256']
        assert file.stat().st_size == item['size']
    disk = json.loads((directory / 'final.json').read_text())
    assert disk['record_kind'] == 'evidence_only'
    assert disk['reason_code'] == 'requires_cli_completion'


def expanded_invocation(step, context):
    argv = []
    for token in step['argv']:
        value = expand(token, context)
        argv.extend(str(item) for item in value) if isinstance(value, list) else argv.append(str(value))
    return tuple(argv), str(expand(step['cwd'], context))


def execute_flow(path, context, *, mutate=None, fault=None, clock=None, mutate_invocation=None):
    """Consume the template block with argv arrays; never a shell or real project."""
    clock = clock or time.monotonic
    start = clock()
    contract_raw = path.read_bytes()
    contract_sha256 = hashlib.sha256(contract_raw).hexdigest()
    data = read_contract(path, raw=contract_raw)
    if mutate:
        data = mutate(copy.deepcopy(data))
        # Validate a mutated document through the same loader, not an alternate flow.
        temp = Path(context['control']) / 'mutated.md'
        temp.write_text('<!-- OMC_SHIP_ENTRY_CONTRACT:BEGIN -->\n```json\n' + json.dumps(data)
            + '\n```\n<!-- OMC_SHIP_ENTRY_CONTRACT:END -->')
        data = read_contract(temp)
    source = Path(context['source'])
    before = preserve(source)
    logs, audits = [], []
    expected_context = copy.deepcopy(context)
    binding = tuple((role, str(expected_context[role]))
                    for role in ('source', 'candidate', 'control', 'kit'))
    report_id = 'ship-entry-' + hashlib.sha256(str(context['control']).encode()).hexdigest()[:16]
    report = {'status': 'passed', 'category': None, 'calls': logs,
              'invocation_audits': audits, 'binding_context': binding,
              'contract_path': str(path), 'contract_sha256': contract_sha256}
    stage_start = start
    last_stage = None
    try:
        if (not context['authorized'] or context['max_attempts'] != 1
                or context.get('run_timeout', 0) <= 0 or context.get('run_finalize_seconds', 0) <= 0):
            report.update(status='blocked', category='approval_mismatch')
            return report
        assert all(Path(value).is_absolute() for _, value in binding)
        assert len({value for _, value in binding}) == len(binding)
        assert not (Path(expected_context['control']) / '.git').exists()
        assert context['quality_scope_args'] in ([], ['--allow-full'])
        assert digest(Path(context['review_output'])) == context['review_output_sha256']
        for step in data['steps']:
            ident = step['id']
            stage = step['stage']
            if stage != last_stage:
                stage_start, last_stage = (start if last_stage is None else clock()), stage
            if ident == 'run':
                assert all(row['passed'] for row in logs)
                timeout = context['run_timeout']
            else:
                index = ORDER.index(ident)
                future_stages = list(dict.fromkeys(s['stage'] for s in data['steps'][index + 1:]
                                                   if s['stage'] not in (stage, 'run')))
                reserved = 70 + sum(data['budget_seconds'][s] for s in future_stages)
                remaining = 480 - reserved - (clock() - start)
                stage_remaining = data['budget_seconds'][stage] - (clock() - stage_start)
                assert remaining >= stage_remaining > 0, 'preparation deadline exceeded'
                timeout = stage_remaining
            # Expected values use the separately frozen binding, not mutated actual argv.
            expected_argv, expected_cwd = expanded_invocation(step, expected_context)
            argv, cwd = expanded_invocation(step, context)
            if mutate_invocation:
                argv, cwd = mutate_invocation(ident, list(argv), cwd)
            argv = tuple(argv)
            matched = argv == expected_argv and cwd == expected_cwd
            audits.append({'report_id': report_id, 'step_id': ident,
                'contract_sha256': contract_sha256,
                'expected_argv': list(expected_argv), 'actual_argv': list(argv),
                'expected_cwd': expected_cwd, 'actual_cwd': cwd,
                'decision': 'matched' if matched else 'blocked'})
            if not matched:
                report.update(status='blocked', category='invocation_mismatch')
                break
            # Dispatch exactly the immutable values just checked, without rebuilding them.
            stamp = clock()
            result = subprocess.run(argv, cwd=cwd, capture_output=True, text=True, timeout=timeout)
            row = {'id': ident, 'argv': argv, 'returncode': result.returncode,
                   'stdout': result.stdout, 'stderr': result.stderr,
                   'started': stamp, 'ended': clock(), 'passed': False}
            logs.append(row)
            if fault:
                fault(ident, context, row)
            if result.returncode:
                report.update(status='blocked', category=('code_failure' if ident == 'run'
                    else 'approval_mismatch' if ident in ['source_review', 'source_quality', 'record', 'approve', 'candidate_review', 'candidate_quality']
                    else 'preparation_unavailable'))
                if ident == 'run':
                    failed = json.loads(result.stdout)
                    report['category'] = ('preparation_unavailable'
                        if failed.get('reason') == 'environment_not_ready'
                        else 'code_failure' if any(g.get('status') == 'failed' for g in failed.get('gates', []))
                        else 'evidence_unconfirmed')
                break
            payload = None
            if step.get('json_status') or ident in ['capture', 'seal', 'record', 'approve']:
                payload = json.loads(result.stdout)
            if step.get('json_status'):
                assert payload['status'] == step['json_status'], (ident, payload)
            if ident in ['prepare', 'check']:
                verify_preflight(result, ident, context, row['ended'] - row['started'])
            if ident in ['source_quality', 'candidate_quality', 'approve']:
                assert payload['config_sha256'] == context['config_sha256']
            if ident in ['capture', 'seal']:
                context[ident] = json.loads(result.stdout)
                expected_context[ident] = copy.deepcopy(context[ident])
            row['passed'] = True
    except (AssertionError, ValueError, KeyError, subprocess.TimeoutExpired):
        report.update(status='blocked', category='evidence_unconfirmed')
    finally:
        assert preserve(source) == before, 'source state changed'
        report['source_preserved'] = True
        terminal = clock()
        report['elapsed_seconds'] = terminal - start
        report['actual_home'] = 'NOT_VERIFIED'
        report['comparison'] = 'HOLD'
        records = Path(context['control']) / 'calls.json'
        run = next((r for r in logs if r['id'] == 'run'), None)
        deadline = (run['ended'] + context['run_finalize_seconds'] if run
                    else min(terminal + 70, start + 480))
        raw = json.dumps({**report, 'status': 'blocked', 'observed_status': report['status'],
                          'record_kind': 'evidence_only'}, indent=2).encode()
        records.write_bytes(raw)
        report['archive_sha256'] = hashlib.sha256(raw).hexdigest()
        report['archive_confirmed'] = records.read_bytes() == raw and clock() <= deadline
        if not report['archive_confirmed']:
            report.update(status='blocked', category='evidence_unconfirmed')
    return report


@pytest.mark.parametrize('template', TEMPLATES)
def test_documented_public_flow_executes_only_the_fixed_candidate(subject, template):
    report = execute_flow(ROOT / template, subject)
    assert report['status'] == 'passed', report
    assert [r['id'] for r in report['calls']] == ORDER
    assert Path(subject['marker']).read_text() == 'called'
    assert report['source_preserved']


@pytest.mark.parametrize('mutation', ['remove_prepare', 'reorder', 'source_write'])
def test_invalid_documented_flow_cannot_run_quality(subject, mutation):
    def mutate(data):
        if mutation == 'remove_prepare': data['steps'] = [s for s in data['steps'] if s['id'] != 'prepare']
        elif mutation == 'reorder': data['steps'][4], data['steps'][-1] = data['steps'][-1], data['steps'][4]
        else:
            s = next(s for s in data['steps'] if s['id'] == 'approve')
            s['argv'][s['argv'].index('--target') + 1] = '{source}'
        return data
    with pytest.raises(AssertionError):
        execute_flow(ROOT / TEMPLATES[0], subject, mutate=mutate)
    assert not Path(subject['marker']).exists()


@pytest.mark.parametrize('defect', ['missing_ref', 'wrong_ref', 'missing_review'])
def test_documented_prerequisite_failure_never_runs_quality(subject, defect):
    source = Path(subject['source'])
    if defect == 'missing_ref': git(source, 'update-ref', '-d', 'refs/remotes/origin/main')
    elif defect == 'wrong_ref': git(source, 'update-ref', 'refs/remotes/origin/main', subject['head'])
    else: (source / '.omc/state/review-snapshots/current.json').unlink()
    report = execute_flow(ROOT / TEMPLATES[0], subject)
    assert report['status'] == 'blocked'
    assert not any(r['id'] == 'run' for r in report['calls'])
    assert not Path(subject['marker']).exists()


def test_documented_candidate_drift_cannot_reuse_source_approval(subject):
    def drift(ident, context, row):
        if ident == 'prepare':
            target = Path(context['candidate'])
            (target / 'unexpected.txt').write_text('unapproved')
            git(target, 'add', 'unexpected.txt')
    report = execute_flow(ROOT / TEMPLATES[0], subject, fault=drift)
    assert report['status'] == 'blocked'
    assert not Path(subject['marker']).exists()


def test_documented_real_gate_failure_is_separate_from_preparation(subject):
    # Change and explicitly approve the synthetic command before freezing inputs.
    source = Path(subject['source'])
    path = source / '.omc/quality-gates.json'
    config = json.loads(path.read_text())
    config['gates'][0]['argv'][-1] = config['gates'][0]['argv'][-1].replace('SystemExit(0)', 'SystemExit(7)')
    path.write_text(json.dumps(config))
    state = public('omc_quality_gate.py', ['--target', str(source), 'status'], codes=(1,))
    subject['config_sha256'] = state['config_sha256']
    public('omc_quality_gate.py', ['--target', str(source), 'approve', '--config-sha256', subject['config_sha256'], '--allow-full'])
    preflight = Path(subject['preflight'])
    doc = json.loads(preflight.read_text()); doc['quality_config_sha256'] = digest(path)
    preflight.write_text(json.dumps(doc))
    report = execute_flow(ROOT / TEMPLATES[0], subject)
    assert report['status'] == 'blocked' and report['category'] == 'code_failure', report
    assert Path(subject['marker']).exists()


def test_missing_runtime_is_preparation_failure_not_code_failure(subject):
    source = Path(subject['source'])
    path = source / '.omc/quality-gates.json'
    config = json.loads(path.read_text())
    config['gates'][0]['argv'][0] = 'omc-synthetic-nonexistent-executable'
    path.write_text(json.dumps(config))
    state = public('omc_quality_gate.py', ['--target', str(source), 'status'], codes=(1,))
    subject['config_sha256'] = state['config_sha256']
    public('omc_quality_gate.py', ['--target', str(source), 'approve', '--config-sha256', subject['config_sha256'], '--allow-full'])
    preflight = Path(subject['preflight'])
    doc = json.loads(preflight.read_text()); doc['quality_config_sha256'] = digest(path)
    preflight.write_text(json.dumps(doc))
    report = execute_flow(ROOT / TEMPLATES[0], subject)
    assert report['status'] == 'blocked' and report['category'] == 'preparation_unavailable', report
    assert not Path(subject['marker']).exists()


def test_ship_templates_share_one_contract():
    assert read_contract(ROOT / TEMPLATES[0]) == read_contract(ROOT / TEMPLATES[1])


@pytest.mark.parametrize('defect', ['exit', 'status', 'operation', 'target', 'cleanup', 'evidence', 'deadline', 'raw_hash'])
def test_preflight_consumer_rejects_uncertain_success_evidence(subject, defect):
    args = ['prepare', '--contract', subject['preflight'], '--target', subject['candidate'],
            '--record-dir', subject['prepare_record']]
    payload = public('omc_ship_preflight.py', args)
    original = json.dumps(payload)
    assert json.loads((Path(subject['prepare_record']) / 'final.json').read_text())['status'] == 'blocked'
    code, elapsed = 0, 1
    if defect == 'exit': code = 1
    elif defect == 'status': payload['status'] = 'blocked'
    elif defect == 'operation': payload['operation'] = 'check'
    elif defect == 'target': payload['target'] = 'different candidate'
    elif defect == 'cleanup': payload['cleanup_confirmed'] = False
    elif defect == 'evidence': payload['evidence_complete'] = False
    elif defect == 'deadline': elapsed = 241
    else:
        file = Path(subject['prepare_record']) / payload['raw_evidence'][0]['name']
        file.write_bytes(file.read_bytes() + b'changed')
    with pytest.raises(AssertionError):
        verify_preflight(subprocess.CompletedProcess(args, code, json.dumps(payload), ''),
                         'prepare', subject, elapsed)
    # Original CLI completion is retained as a distinct test artifact.
    (Path(subject['control']) / 'original-prepare-stdout.json').write_text(original)


@pytest.mark.parametrize('defect', ['approval', 'run_budget', 'finalize_budget'])
def test_missing_execution_authorization_or_budget_never_runs_quality(subject, defect):
    if defect == 'approval': subject['authorized'] = False
    elif defect == 'run_budget': subject.pop('run_timeout')
    else: subject.pop('run_finalize_seconds')
    report = execute_flow(ROOT / TEMPLATES[0], subject)
    assert report['status'] == 'blocked' and report['category'] == 'approval_mismatch'
    assert not report['calls'] and not Path(subject['marker']).exists()


def test_preparation_deadline_prevents_later_commands(subject):
    clock = SimpleClock()
    def jump(ident, context, row):
        if ident == 'control_guard': clock.value = 500
    report = execute_flow(ROOT / TEMPLATES[0], subject, fault=jump, clock=clock)
    assert report['status'] == 'blocked' and report['category'] == 'evidence_unconfirmed'
    assert [r['id'] for r in report['calls']] == ['control_init', 'control_guard']
    assert not Path(subject['marker']).exists()


class SimpleClock:
    value = 0
    def __call__(self):
        return self.value


def test_installed_ship_entry_contract_matches_templates(installed_omc_target):
    for template, output in zip(TEMPLATES, [Path('.agents/skills/omc-ship/SKILL.md'), Path('.claude/commands/ship.md')]):
        generated = installed_omc_target / output
        assert generated.read_bytes() == (ROOT / template).read_bytes(), 'ship generated output mismatch'
        assert read_contract(generated) == read_contract(ROOT / template)
    assert (installed_omc_target / '.agents/skills/omc-task/SKILL.md').read_bytes() == (ROOT / 'templates/.agents/skills/omc-task/SKILL.md').read_bytes()


@pytest.mark.parametrize('template', TEMPLATES)
def test_control_seal_dispatch_is_audited_against_frozen_contract(subject, template, monkeypatch):
    # Fixture setup is deliberately outside the dispatched-call counter.
    source = Path(subject['source'])
    control = Path(subject['control'])
    before, raw_review = preserve(source), Path(subject['review_output']).read_bytes()
    actual_calls = []
    original_run = subprocess.run
    def observed(argv, **kwargs):
        actual_calls.append((tuple(argv), str(kwargs['cwd'])))
        return original_run(argv, **kwargs)
    monkeypatch.setattr(subprocess, 'run', observed)
    report = execute_flow(ROOT / template, subject)
    assert report['status'] == 'passed', report
    assert 'invocation_audits' in report, 'actual dispatch argv/cwd must have independent frozen-contract evidence'
    assert len(report['invocation_audits']) == len(actual_calls) == len(ORDER)
    assert control.is_absolute() and not (control / '.git').exists()
    for audit, call in zip(report['invocation_audits'], actual_calls):
        assert audit['decision'] == 'matched'
        assert tuple(audit['expected_argv']) == tuple(audit['actual_argv']) == call[0]
        assert audit['expected_cwd'] == audit['actual_cwd'] == call[1] == str(control)
        assert audit['contract_sha256'] == digest(ROOT / template)
        assert audit['report_id'] and audit['step_id'] in ORDER
    seal = next(row for row in report['calls'] if row['id'] == 'seal')
    target = Path(subject['candidate'])
    assert seal['argv'].count('--target') == 1
    assert seal['argv'][seal['argv'].index('--target') + 1] == str(target)
    payload = json.loads(seal['stdout'])
    evidence = Path(payload['evidence_path'])
    assert evidence.is_relative_to(target) and digest(evidence) == payload['evidence_sha256']
    assert next(row for row in report['calls'] if row['id'] == 'candidate_review')['passed']
    assert preserve(source) == before and Path(subject['review_output']).read_bytes() == raw_review
    archived = json.loads((control / 'calls.json').read_bytes())
    assert archived['invocation_audits'] == report['invocation_audits']


@pytest.mark.parametrize('template', TEMPLATES)
@pytest.mark.parametrize('defect', ['missing', 'duplicate', 'source', 'control'])
def test_seal_document_target_defects_dispatch_no_children(subject, template, defect, monkeypatch):
    source, before = Path(subject['source']), preserve(Path(subject['source']))
    calls = []
    def unexpected(*args, **kwargs):
        calls.append(args)
        raise AssertionError('invalid contract reached child dispatch')
    monkeypatch.setattr(subprocess, 'run', unexpected)
    def mutate(data):
        argv = next(s for s in data['steps'] if s['id'] == 'seal')['argv']
        index = argv.index('--target')
        if defect == 'missing': del argv[index:index + 2]
        elif defect == 'duplicate': argv.extend(['--target', '{candidate}'])
        else: argv[index + 1] = '{' + defect + '}'
        return data
    with pytest.raises(AssertionError):
        execute_flow(ROOT / template, subject, mutate=mutate)
    assert calls == [] and not Path(subject['marker']).exists()
    assert not Path(subject['candidate']).exists() and preserve(source) == before


@pytest.mark.parametrize('template', TEMPLATES)
@pytest.mark.parametrize('defect', ['missing', 'duplicate', 'source', 'control', 'cwd', 'context'])
def test_post_expansion_seal_drift_is_rejected_before_spawn(subject, template, defect, monkeypatch):
    original_candidate = subject['candidate']
    original_control = subject['control']
    source = Path(subject['source'])
    before = preserve(source)
    dispatched = []
    original_run = subprocess.run
    def observed(argv, **kwargs):
        dispatched.append(tuple(argv))
        return original_run(argv, **kwargs)
    monkeypatch.setattr(subprocess, 'run', observed)
    def mutate(ident, argv, cwd):
        if ident != 'seal': return argv, cwd
        index = argv.index('--target')
        if defect == 'missing': del argv[index:index + 2]
        elif defect == 'duplicate': argv.extend(['--target', original_candidate])
        elif defect in ('source', 'control'): argv[index + 1] = subject[defect]
        elif defect == 'cwd': cwd = subject['source']
        else:
            # Poisoning mutable context must not redefine the expected target.
            subject['candidate'] = subject['source']
            argv[index + 1] = subject['source']
        return argv, cwd
    report = execute_flow(ROOT / template, subject, mutate_invocation=mutate)
    assert report['status'] == 'blocked' and report['category'] == 'invocation_mismatch', report
    assert [row['id'] for row in report['calls']] == ORDER[:ORDER.index('seal')]
    assert len(dispatched) == ORDER.index('seal')
    assert not any('seal-review-output' in argv or 'record-review' in argv or argv[-1:] == ('run',)
                   for argv in dispatched)
    audit = report['invocation_audits'][-1]
    assert audit['step_id'] == 'seal' and audit['decision'] == 'blocked'
    assert audit['expected_argv'][audit['expected_argv'].index('--target') + 1] == original_candidate
    assert audit['expected_cwd'] == original_control
    assert not Path(subject['marker']).exists() and preserve(source) == before
    target = Path(original_candidate)
    assert not (target / '.omc/state/review-snapshots/current.json').exists()
    assert not list((target / '.omc/state/review-snapshots/review-evidence').glob('*'))
    archived = json.loads((Path(original_control) / 'calls.json').read_bytes())
    assert archived['invocation_audits'][-1] == audit


@pytest.mark.parametrize('template', TEMPLATES)
def test_missing_seal_target_public_cli_fails_from_non_git_control(subject, template):
    # Intentional CLI failure reproduction, not a product RED or ship invocation.
    source, control, target = map(Path, (subject['source'], subject['control'], subject['candidate']))
    before = preserve(source)
    subprocess.run(['git', 'clone', '--no-hardlinks', str(source), str(target)],
                   check=True, capture_output=True, timeout=30)
    snap = public('omc_review_snapshot.py', ['capture-review', '--target', str(target),
        '--base-commit', subject['base'], '--candidate-commit', subject['head']], cwd=control)
    context = {**subject, 'capture': snap}
    step = next(s for s in read_contract(ROOT / template)['steps'] if s['id'] == 'seal')
    argv = [str(expand(token, context)) for token in step['argv']]
    index = argv.index('--target')
    del argv[index:index + 2]
    result = subprocess.run(argv, cwd=control, capture_output=True, text=True, timeout=30)
    assert result.returncode == 2 and json.loads(result.stdout)['reason_code'] == 'git_command_failed:rev-parse'
    assert not result.stderr and not (control / '.git').exists()
    assert not (target / '.omc/state/review-snapshots/current.json').exists()
    assert not list((target / '.omc/state/review-snapshots/review-evidence').glob('*'))
    assert not Path(subject['marker']).exists() and preserve(source) == before


@pytest.mark.parametrize('defect', ['missing', 'stale'])
def test_source_not_ready_never_gets_resealed_or_copied(subject, defect):
    source = Path(subject['source'])
    if defect == 'missing': (source / '.omc/state/review-snapshots/current.json').unlink()
    else: (source / 'result.md').write_text('unapproved fixture drift\n')
    before = preserve(source)
    report = execute_flow(ROOT / TEMPLATES[0], subject)
    assert report['status'] == 'blocked'
    assert [row['id'] for row in report['calls']] == ORDER[:3]
    assert not Path(subject['candidate']).exists() and not Path(subject['marker']).exists()
    assert preserve(source) == before


def test_installed_preparation_diagnosis_and_guidance_are_read_only(installed_omc_target, tmp_path):
    from test_omc_ship_preflight import fixture as preflight_fixture
    folder = tmp_path/'diagnosis-fixture'; folder.mkdir()
    source, contract, candidate = preflight_fixture.__wrapped__(folder)
    before = preserve(source)
    for name in ['omc-task','omc-review','omc-ship']:
        text = (installed_omc_target/f'.agents/skills/{name}/SKILL.md').read_text()
        assert '최종 후보 준비' in text
        assert 'diagnose' in text
    r = subprocess.run([sys.executable,str(installed_omc_target/'scripts/omc_ship_preflight.py'),
        'diagnose','--target',str(source),'--contract',str(contract)],capture_output=True,text=True)
    assert r.returncode == 1, r.stdout+r.stderr
    report = json.loads(r.stdout)
    assert report['review']['status'] == 'BLOCKED'
    assert report['quality']['status'] == 'approval_required'
    assert preserve(source) == before and not candidate.exists()
