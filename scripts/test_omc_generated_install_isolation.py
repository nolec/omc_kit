"""Exercise generated-file checks without a pre-installed developer checkout."""
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
from types import SimpleNamespace

import pytest


ROOT = Path(__file__).resolve().parent.parent
SUBJECTS = [
    'scripts/test_llm_autopilot_commands.py::test_deployed_claude_plan_matches_template',
    'scripts/test_omc_native_command.py::test_task_templates_describe_diagnostics_and_official_report',
    'scripts/test_omc_state_record_confirm.py::test_core_omc_skills_document_use_session_sync_step',
    'scripts/test_omc_state_record_confirm.py::test_core_omc_skill_mirrors_stay_in_sync',
]
SHIP_INPUTS = [
    'templates/.agents/skills/omc-ship/SKILL.md',
    'templates/.claude/commands/ship.md',
    'docs/omc_quality_gates.md',
    'scripts/test_omc_ship_entry_contract.py',
    'scripts/test_omc_generated_install_isolation.py',
]

OUTPUT_INPUTS = ['docs/omc_quality_gates.md', 'docs/orchestration_usage.md', 'scripts/omc_autopilot.py', 'scripts/omc_output_contract.py', 'scripts/omc_pipeline_guard.py', 'scripts/omc_workflow_validation.py', 'scripts/test_llm_autopilot_commands.py', 'scripts/test_omc_codex_output_readability.py', 'scripts/test_omc_completion_reporting_contract.py', 'scripts/test_omc_generated_install_isolation.py', 'scripts/test_omc_output_contract.py', 'scripts/test_omc_ship_entry_contract.py', 'scripts/test_omc_task_completion_surface.py', 'scripts/test_omc_workflow_validation.py', 'templates/.agents/skills/omc-critique/SKILL.md', 'templates/.agents/skills/omc-investigate/SKILL.md', 'templates/.agents/skills/omc-plan/references/workflow.md', 'templates/.agents/skills/omc-review/SKILL.md', 'templates/.agents/skills/omc-ship/SKILL.md', 'templates/.agents/skills/omc-task/SKILL.md', 'templates/.agents/skills/omc-task/references/verification.md', 'templates/.claude/commands/critique.md', 'templates/.claude/commands/investigate.md', 'templates/.claude/commands/plan.md', 'templates/.claude/commands/review.md', 'templates/.claude/commands/ship.md', 'templates/.claude/commands/task.md']

def uninstalled_checkout(tmp_path):
    target = tmp_path / 'checkout'
    subprocess.run(['git', 'clone', '--shared', '--quiet', str(ROOT), str(target)],
                   check=True, capture_output=True, timeout=30)
    names = dict.fromkeys(['scripts/conftest.py',
                          *(n.split('::')[0] for n in SUBJECTS), *SHIP_INPUTS, *OUTPUT_INPUTS])
    for name in names:
        source = ROOT / name
        destination = target / name
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, destination)
        assert source.read_bytes() == destination.read_bytes(), name
        assert source.stat().st_mode & 0o777 == destination.stat().st_mode & 0o777, name
    assert not (target / '.claude/commands/plan.md').exists()
    assert not (target / '.agent/skills/omc-plan/SKILL.md').exists()
    return target


def run_checks(root, nodes, *, plugin=None):
    env = os.environ.copy()
    env.pop('PYTEST_ADDOPTS', None)
    # Benchmark metadata invokes `git describe --dirty`, which writes stat caches.
    argv = [sys.executable, '-m', 'pytest', *nodes, '-q', '-ra', '-p', 'no:benchmark']
    if plugin:
        directory = root / '.omc'
        directory.mkdir(exist_ok=True)
        (directory / 'install_fault.py').write_text(plugin)
        env['PYTHONPATH'] = str(directory) + os.pathsep + env.get('PYTHONPATH', '')
        argv += ['-p', 'install_fault']
    # Keep custody outside pytest's disposable nested checkout.
    evidence_root = ROOT / '.omc/isolation-index'
    evidence_root.mkdir(parents=True, exist_ok=True)
    evidence = Path(tempfile.mkdtemp(prefix='run-', dir=evidence_root))
    metadata = {'target': str(root.resolve()), 'command': argv}

    def capture(label):
        index = root / '.git/index'
        try:
            raw = index.read_bytes()
        except FileNotFoundError:
            metadata[label] = None
        else:
            (evidence / f'index-{label}.bin').write_bytes(raw)
            metadata[label] = hashlib.sha256(raw).hexdigest()
        (evidence / 'index.json').write_text(json.dumps(metadata, indent=2))

    capture('before')
    try:
        return subprocess.run(argv, cwd=root, env=env, capture_output=True, text=True, timeout=120)
    finally:
        capture('after')


def protected_bytes(root):
    names = ['.git/index', '.omc/pipeline_session.json', '.omc/state/latest.json',
             '.omc/install-receipt.json', '.omc/quality-gates.json',
             '.claude/commands/plan.md', '.claude/commands/task.md']
    names += [str(p.relative_to(root)) for base in ['.agent/skills', '.agents/skills']
              for p in (root / base).rglob('*') if p.is_file()]
    return {n: hashlib.sha256((root / n).read_bytes()).hexdigest()
            if (root / n).is_file() else None for n in names}


def test_uninstalled_checkout_copies_exact_ship_candidate_inputs(tmp_path, monkeypatch):
    source = tmp_path / 'source'
    subprocess.run(['git', 'clone', '--no-hardlinks', '--quiet', str(ROOT), str(source)],
                   check=True, capture_output=True, timeout=30)
    inputs = ['templates/.agents/skills/omc-ship/SKILL.md',
              'templates/.claude/commands/ship.md', 'docs/omc_quality_gates.md',
              'scripts/test_omc_ship_entry_contract.py',
              'scripts/test_omc_generated_install_isolation.py']
    for name in OUTPUT_INPUTS:
        (source/name).parent.mkdir(parents=True,exist_ok=True)
        shutil.copy2(ROOT / name, source / name)
    template = source / inputs[0]
    template.write_text(template.read_text() + '\n<!-- uncommitted candidate sentinel -->\n')
    before = {n: (hashlib.sha256((source / n).read_bytes()).hexdigest(),
                  (source / n).stat().st_mode & 0o777) for n in inputs}
    index = (source / '.git/index').read_bytes()
    monkeypatch.setattr(sys.modules[__name__], 'ROOT', source)
    generation = tmp_path / 'generation'
    generation.mkdir()
    copied = uninstalled_checkout(generation)
    for name, expected in before.items():
        assert (hashlib.sha256((copied / name).read_bytes()).hexdigest(),
                (copied / name).stat().st_mode & 0o777) == expected, name
    assert (source / '.git/index').read_bytes() == index


@pytest.mark.parametrize('defect', ['missing', 'drift'])
def test_generated_ship_contract_rejects_corrupt_install(tmp_path, defect):
    root = uninstalled_checkout(tmp_path)
    node = 'scripts/test_omc_ship_entry_contract.py::test_installed_ship_entry_contract_matches_templates'
    plugin = f'''def pytest_runtest_call(item):
    target = item.funcargs.get('installed_omc_target')
    assert target is not None
    path = target / '.agents/skills/omc-ship/SKILL.md'
    path.unlink() if {defect!r} == 'missing' else path.write_text('corrupted ship contract')
'''
    result = run_checks(root, [node], plugin=plugin)
    assert result.returncode == 1, result.stdout + result.stderr
    assert ('ship generated output mismatch' in result.stdout
            or 'FileNotFoundError' in result.stdout)


def test_generated_checks_pass_without_installing_the_checkout(tmp_path):
    root = uninstalled_checkout(tmp_path)
    before = protected_bytes(ROOT)
    checkout_before = protected_bytes(root)
    result = run_checks(root, SUBJECTS)
    assert protected_bytes(ROOT) == before, result.stdout + result.stderr
    assert protected_bytes(root) == checkout_before, result.stdout + result.stderr
    assert result.returncode == 0, result.stdout + result.stderr
    assert '4 passed' in result.stdout


@pytest.mark.parametrize('defect,node,path', [
    ('missing', SUBJECTS[0], '.claude/commands/plan.md'),
    ('drift', SUBJECTS[0], '.claude/commands/plan.md'),
    ('missing', SUBJECTS[3], '.agent/skills/omc-plan/SKILL.md'),
])
def test_generated_checks_reject_missing_or_changed_install_output(tmp_path, defect, node, path):
    root = uninstalled_checkout(tmp_path)
    plugin = f'''def pytest_runtest_call(item):
    target = item.funcargs.get('installed_omc_target')
    assert target is not None, 'real installed target fixture required'
    path = target / {path!r}
    path.unlink() if {defect!r} == 'missing' else path.write_text('corrupted install')
'''
    result = run_checks(root, [node], plugin=plugin)
    assert result.returncode == 1, result.stdout + result.stderr
    assert 'real installed target fixture required' not in result.stdout
    assert ('FileNotFoundError' in result.stdout or '파일 없음' in result.stdout
            or 'deployed .claude/commands/plan.md가 template와 다름' in result.stdout)


def test_install_fixture_surfaces_installer_failure(tmp_path, monkeypatch):
    spec = importlib.util.spec_from_file_location('install_fixture_subject', ROOT / 'scripts/conftest.py')
    subject = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(subject)
    monkeypatch.setattr(subject, 'subprocess', SimpleNamespace(run=lambda argv, **kw:
                        subprocess.CompletedProcess(argv, 1, 'installer refused', 'failure details')),
                        raising=False)
    with pytest.raises(AssertionError, match='installer refused.*failure details'):
        subject.installed_omc_target.__wrapped__(tmp_path)


def test_install_fixture_does_not_run_for_unrelated_tests(tmp_path):
    root = uninstalled_checkout(tmp_path)
    probe = root / 'scripts/test_unrelated_probe.py'
    probe.write_text('def test_unrelated():\n    assert True\n')
    plugin = '''import subprocess
def pytest_configure(config):
    original = subprocess.run
    def observed(argv, *args, **kwargs):
        if isinstance(argv, (list, tuple)) and any(str(a).endswith('/install.py') for a in argv):
            raise AssertionError('unexpected installer for unrelated test')
        return original(argv, *args, **kwargs)
    subprocess.run = observed
'''
    result = run_checks(root, ['scripts/test_unrelated_probe.py'], plugin=plugin)
    assert result.returncode == 0, result.stdout + result.stderr


@pytest.mark.parametrize('outcome', ['passed', 'failed', 'timeout', 'deleted', 'missing'])
def test_run_checks_preserves_index_evidence_and_blocks_benchmark(tmp_path, monkeypatch, outcome):
    root = tmp_path / 'checkout'
    (root / '.git').mkdir(parents=True)
    monkeypatch.setattr(sys.modules[__name__], 'ROOT', root)
    index = root / '.git/index'
    if outcome != 'missing':
        index.write_bytes(b'original index')
    before = protected_bytes(root)
    observed = []

    def execute(argv, **kwargs):
        observed.append(argv)
        if outcome == 'deleted':
            index.unlink()
        elif outcome != 'missing':
            index.write_bytes(b'changed index')
        if outcome == 'timeout':
            raise subprocess.TimeoutExpired(argv, 120)
        return subprocess.CompletedProcess(argv, int(outcome == 'failed'), 'output', '')

    monkeypatch.setattr(subprocess, 'run', execute)
    if outcome == 'timeout':
        with pytest.raises(subprocess.TimeoutExpired):
            run_checks(root, ['scripts/test_probe.py'])
    else:
        result = run_checks(root, ['scripts/test_probe.py'])
        assert result.returncode == int(outcome == 'failed')
    assert ['-p', 'no:benchmark'] == observed[0][-2:]
    records = list((root / '.omc/isolation-index').glob('run-*'))
    assert len(records) == 1
    record = records[0]
    metadata = json.loads((record / 'index.json').read_text())
    for label, expected in [('before', None if outcome == 'missing' else b'original index'),
                            ('after', None if outcome in {'missing', 'deleted'} else b'changed index')]:
        path = record / f'index-{label}.bin'
        assert path.exists() == (expected is not None)
        assert metadata[label] == (None if expected is None else hashlib.sha256(expected).hexdigest())
        if expected is not None:
            assert path.read_bytes() == expected
    if outcome != 'missing':
        with pytest.raises(AssertionError):
            assert protected_bytes(root) == before


def test_run_checks_keeps_each_invocation_evidence(tmp_path, monkeypatch):
    monkeypatch.setattr(sys.modules[__name__], 'ROOT', tmp_path)
    (tmp_path / '.git').mkdir()
    index = tmp_path / '.git/index'
    index.write_bytes(b'first')
    monkeypatch.setattr(subprocess, 'run', lambda argv, **kwargs:
                        subprocess.CompletedProcess(argv, 0, '', ''))
    run_checks(tmp_path, [])
    index.write_bytes(b'second')
    run_checks(tmp_path, [])
    records = list((tmp_path / '.omc/isolation-index').glob('run-*'))
    assert len(records) == 2
    assert {p.joinpath('index-before.bin').read_bytes() for p in records} == {b'first', b'second'}
    assert all(p.joinpath('index-before.bin').read_bytes() == p.joinpath('index-after.bin').read_bytes()
               for p in records)


def test_run_checks_evidence_survives_checkout_removal(tmp_path, monkeypatch):
    collector = tmp_path / 'collector'
    root = tmp_path / 'temporary-checkout'
    (root / '.git').mkdir(parents=True)
    (root / '.git/index').write_bytes(b'preserved')
    monkeypatch.setattr(sys.modules[__name__], 'ROOT', collector)
    monkeypatch.setattr(subprocess, 'run', lambda argv, **kwargs:
                        subprocess.CompletedProcess(argv, 0, '', ''))
    run_checks(root, ['scripts/test_probe.py'])
    shutil.rmtree(root)
    records = list((collector / '.omc/isolation-index').glob('run-*'))
    assert len(records) == 1
    assert (records[0] / 'index-before.bin').read_bytes() == b'preserved'
    assert (records[0] / 'index-after.bin').read_bytes() == b'preserved'
    metadata = json.loads((records[0] / 'index.json').read_text())
    assert metadata['target'] == str(root.resolve())
    assert 'scripts/test_probe.py' in metadata['command']


def test_generated_output_separation_uses_all_uncommitted_inputs(tmp_path):
    root=uninstalled_checkout(tmp_path)
    for name in OUTPUT_INPUTS:
        assert (root/name).read_bytes()==(ROOT/name).read_bytes(),name
        assert (root/name).stat().st_mode & 0o777 == (ROOT/name).stat().st_mode & 0o777,name
    result=run_checks(root,['scripts/test_omc_completion_reporting_contract.py::test_twelve_installed_surfaces_use_detached_output'])
    assert result.returncode==0,result.stdout+result.stderr


def test_installed_completion_resume_keeps_evidence_and_reset_boundary(installed_omc_target):
    import json
    from test_omc_task_completion_surface import preserved_work
    import omc_state
    target = installed_omc_target
    # Git fixtures must ignore runtime bytecode before binding candidate bytes.
    (target/'.gitignore').write_text('.omc/\n__pycache__/\n')
    latest = target/'.omc/state/latest.json'
    latest.parent.mkdir(parents=True, exist_ok=True)
    latest.write_text(json.dumps({'latest_session_id':'current', 'latest_confirmed_session_id':'current'}))
    preserved_work(target)
    omc_state.record_session(target, mode='autopilot', title='omc-task',
        request='resume approved completion', role_ids=['directive'], confirmed=True,
        completion_action='preserve')
    text = (target/'.agents/skills/omc-task/SKILL.md').read_text()
    assert '--roles directive --completion-action preserve' in text
    assert '인계 없는 session_start' in text
    paths = [target/'verification.json', *sorted((target/'.omc/verification-bases').rglob('*.json'))]
    before = {p: p.read_bytes() for p in paths}
    command = [sys.executable, str(target/'scripts/omc_pipeline_guard.py'), '--target', str(target),
               'complete-task', '--verification-file', str(target/'verification.json')]
    result = subprocess.run(command, cwd=target, capture_output=True, text=True)
    assert result.returncode == 0, result.stdout + result.stderr
    hook = subprocess.run([sys.executable, str(target/'scripts/omc.py'), 'hook', 'session_start',
                           '--target', str(target)], cwd=target, capture_output=True, text=True)
    assert hook.returncode == 0, hook.stdout + hook.stderr
    result = subprocess.run(command, cwd=target, capture_output=True, text=True)
    assert result.returncode == 1
    assert '시작 훅에서 현재 CONTRACT 연결이 초기화됐습니다' in result.stdout
    assert {p: p.read_bytes() for p in paths} == before


@pytest.mark.parametrize('extra_conflict', [False, True])
def test_reinstall_preserves_committed_retirement_archive(tmp_path, monkeypatch, extra_conflict):
    import install
    from omc_install_audit import audit_target
    monkeypatch.setenv('OMC_INSTALLATION_REGISTRY_DIR', str(tmp_path / 'registry'))
    monkeypatch.setenv('XDG_CONFIG_HOME', str(tmp_path / 'config'))
    from test_omc_edit_resume import retiring, _commit_completed_work, _retire
    target, session = retiring.__wrapped__(tmp_path)
    _commit_completed_work(target, session)
    receipt = _retire(target, session)
    archive = target / receipt['archive']
    manifest = json.loads((archive / 'manifest.json').read_text())
    assert receipt['previous_work_status'] == 'EXPIRED_COMMITTED'
    env = {**os.environ, 'GIT_OPTIONAL_LOCKS': '0'}
    command = [sys.executable, str(ROOT / 'scripts/install.py'), '--target', str(target)]
    def run(*args):
        result = subprocess.run([*command, *args], cwd=ROOT, env=env,
                                capture_output=True, text=True, timeout=60)
        return result
    business = target / 'business.txt'
    business.write_text('preserve business work')
    protected = [business, target / '.git/index',
                 *[p for p in archive.rglob('*') if p.is_file()],
                 *[p for p in (target / '.omc/state/sessions').rglob('*.json')]]
    before = {p: (p.read_bytes(), p.stat().st_mode & 0o777) for p in protected}
    head = subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=target)
    first = run()
    assert first.returncode == 0, first.stdout + first.stderr
    local = target / 'scripts/omc_edit_resume.py'
    local.write_bytes(local.read_bytes() + b'\n# consumer local retirement change\n')
    backup = tmp_path / 'consumer-local-backup.py'
    shutil.copy2(local, backup)
    if extra_conflict:
        (target / 'scripts/omc_guard.py').write_text('# unrelated local change\n')
    classification = install._classify_auto_update(ROOT, target)
    expected = {'scripts/omc_edit_resume.py'}
    if extra_conflict:
        expected.add('scripts/omc_guard.py')
    assert classification['status'] == 'local_conflict'
    assert set(classification['conflicts']) == expected
    blocked = run('--auto-update')
    assert blocked.returncode != 0
    assert local.read_bytes() == backup.read_bytes()
    if not extra_conflict:
        # Explicit fixture authorization covers this exact single conflict only.
        updated = run('--force')
        assert updated.returncode == 0, updated.stdout + updated.stderr
        assert local.read_bytes() == (ROOT / 'scripts/omc_edit_resume.py').read_bytes()
        audit = audit_target(target, trusted_source_root=ROOT)
        assert audit['verification_status'] == 'ok', audit
    assert {p: (p.read_bytes(), p.stat().st_mode & 0o777) for p in protected} == before
    assert json.loads((archive / 'completed.json').read_text()) == receipt
    assert json.loads((archive / 'manifest.json').read_text()) == manifest
    assert subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=target) == head
