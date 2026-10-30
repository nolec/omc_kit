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


def read_contract(path):
    matches = re.findall(r'<!-- OMC_SHIP_ENTRY_CONTRACT:BEGIN -->\s*```json\s*(.*?)\s*```\s*<!-- OMC_SHIP_ENTRY_CONTRACT:END -->',
                         path.read_text(), re.S)
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
    files = [root / '.git/index', root / '.git/config', root / '.omc/pipeline_session.json']
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


def execute_flow(path, context, *, mutate=None, fault=None, clock=None):
    """Consume the template block with argv arrays; never a shell or real project."""
    clock = clock or time.monotonic
    start = clock()
    data = read_contract(path)
    if mutate:
        data = mutate(copy.deepcopy(data))
        # Validate a mutated document through the same loader, not an alternate flow.
        temp = Path(context['control']) / 'mutated.md'
        temp.write_text('<!-- OMC_SHIP_ENTRY_CONTRACT:BEGIN -->\n```json\n' + json.dumps(data)
            + '\n```\n<!-- OMC_SHIP_ENTRY_CONTRACT:END -->')
        data = read_contract(temp)
    source = Path(context['source'])
    before = preserve(source)
    logs = []
    report = {'status': 'passed', 'category': None, 'calls': logs}
    stage_start = start
    last_stage = None
    try:
        if (not context['authorized'] or context['max_attempts'] != 1
                or context.get('run_timeout', 0) <= 0 or context.get('run_finalize_seconds', 0) <= 0):
            report.update(status='blocked', category='approval_mismatch')
            return report
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
            argv = []
            for token in step['argv']:
                value = expand(token, context)
                argv.extend(value if isinstance(value, list) else [str(value)])
            cwd = expand(step['cwd'], context)
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
