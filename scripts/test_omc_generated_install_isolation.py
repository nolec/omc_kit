"""Exercise generated-file checks without a pre-installed developer checkout."""
import hashlib
import importlib.util
import os
from pathlib import Path
import shutil
import subprocess
import sys
from types import SimpleNamespace

import pytest


ROOT = Path(__file__).resolve().parent.parent
SUBJECTS = [
    'scripts/test_llm_autopilot_commands.py::test_deployed_claude_plan_matches_template',
    'scripts/test_omc_native_command.py::test_task_templates_describe_diagnostics_and_official_report',
    'scripts/test_omc_state_record_confirm.py::test_core_omc_skills_document_use_session_sync_step',
    'scripts/test_omc_state_record_confirm.py::test_core_omc_skill_mirrors_stay_in_sync',
]


def uninstalled_checkout(tmp_path):
    target = tmp_path / 'checkout'
    subprocess.run(['git', 'clone', '--shared', '--quiet', str(ROOT), str(target)],
                   check=True, capture_output=True, timeout=30)
    for name in ['scripts/conftest.py', *dict.fromkeys(n.split('::')[0] for n in SUBJECTS)]:
        shutil.copy2(ROOT / name, target / name)
    assert not (target / '.claude/commands/plan.md').exists()
    assert not (target / '.agent/skills/omc-plan/SKILL.md').exists()
    return target


def run_checks(root, nodes, *, plugin=None):
    env = os.environ.copy()
    env.pop('PYTEST_ADDOPTS', None)
    argv = [sys.executable, '-m', 'pytest', *nodes, '-q', '-ra']
    if plugin:
        directory = root / '.omc'
        directory.mkdir(exist_ok=True)
        (directory / 'install_fault.py').write_text(plugin)
        env['PYTHONPATH'] = str(directory) + os.pathsep + env.get('PYTHONPATH', '')
        argv += ['-p', 'install_fault']
    return subprocess.run(argv, cwd=root, env=env, capture_output=True, text=True, timeout=120)


def protected_bytes(root):
    names = ['.git/index', '.omc/pipeline_session.json', '.omc/state/latest.json',
             '.omc/install-receipt.json', '.omc/quality-gates.json',
             '.claude/commands/plan.md', '.claude/commands/task.md']
    names += [str(p.relative_to(root)) for base in ['.agent/skills', '.agents/skills']
              for p in (root / base).rglob('*') if p.is_file()]
    return {n: hashlib.sha256((root / n).read_bytes()).hexdigest()
            if (root / n).is_file() else None for n in names}


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
