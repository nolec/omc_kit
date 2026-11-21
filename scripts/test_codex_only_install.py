"""Codex-only installation: no other IDE output or implicit execution."""
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
import omc_doctor


def install(target, force=False, *, with_pr_create=False):
    result = subprocess.run(
        [sys.executable, '-B', str(ROOT / 'scripts/install.py'), '--target', str(target)]
        + (['--force'] if force else []) + (['--with-pr-create'] if with_pr_create else []),
        cwd=ROOT, capture_output=True, text=True,
        env={**os.environ, 'GIT_OPTIONAL_LOCKS': '0'},
    )
    assert result.returncode == 0, result.stdout + result.stderr


@pytest.fixture(scope='module')
def fresh(tmp_path_factory):
    target = tmp_path_factory.mktemp('codex-only')
    install(target)
    return target


def test_fresh_install_is_codex_only(fresh):
    assert (fresh / '.agents/skills/omc-task/SKILL.md').is_file()
    assert (fresh / 'scripts/omc.py').is_file()
    for name in ['.claude', '.gemini', '.cursor', '.agent', '.cursorignore', 'CLAUDE.md', 'GEMINI.md']:
        assert not (fresh / name).exists(), name


PR_CREATE = Path('.agents/skills/pr-create')
PR_CREATE_FILES = ('SKILL.md', 'agents/openai.yaml')


def test_pr_create_is_not_installed_by_default(fresh):
    assert not (fresh / PR_CREATE).exists()
    receipt = json.loads((fresh / '.omc/install-receipt.json').read_text())
    assert not any(path.startswith(f'{PR_CREATE}/') for path in receipt['entries'])
    result = subprocess.run(
        [sys.executable, '-B', str(fresh / 'scripts/omc_skill_check.py'), 'omc-ship'],
        cwd=fresh, capture_output=True, text=True,
    )
    assert result.returncode == 0, result.stdout + result.stderr


@pytest.mark.parametrize('entrypoint', ['installer', 'setup'])
def test_pr_create_can_be_installed_explicitly(tmp_path, entrypoint):
    if entrypoint == 'installer':
        install(tmp_path, with_pr_create=True)
    else:
        result = subprocess.run(
            [sys.executable, '-B', str(ROOT / 'scripts/omc.py'), 'setup', '--target',
             str(tmp_path), '--with-pr-create'],
            cwd=ROOT, capture_output=True, text=True,
            env={**os.environ, 'OMC_INSTALLATION_REGISTRY_DIR': str(tmp_path / 'registry')},
        )
        assert result.returncode == 0, result.stdout + result.stderr
    for name in PR_CREATE_FILES:
        relative = PR_CREATE / name
        assert (tmp_path / relative).read_bytes() == (ROOT / 'templates' / relative).read_bytes()
    receipt = json.loads((tmp_path / '.omc/install-receipt.json').read_text())
    assert all(receipt['entries'][str(PR_CREATE / name)]['policy'] == 'managed_exact'
               for name in PR_CREATE_FILES)


@pytest.mark.parametrize('force, with_pr_create', [(False, False), (True, False), (True, True)])
def test_reinstall_keeps_existing_pr_create_files(tmp_path, force, with_pr_create):
    install(tmp_path, with_pr_create=True)
    skill = tmp_path / PR_CREATE / 'SKILL.md'
    skill.write_bytes(skill.read_bytes() + b'\n# Project instructions\n')
    expected = {name: (tmp_path / PR_CREATE / name).read_bytes() for name in PR_CREATE_FILES}

    install(tmp_path, force=force, with_pr_create=with_pr_create)

    for name, data in expected.items():
        assert (tmp_path / PR_CREATE / name).read_bytes() == data
    receipt = json.loads((tmp_path / '.omc/install-receipt.json').read_text())
    assert receipt['entries'][str(PR_CREATE / 'SKILL.md')]['policy'] == 'preserve'
    assert receipt['entries'][str(PR_CREATE / 'agents/openai.yaml')]['policy'] == 'managed_exact'


def test_automatic_hooks_stay_disabled(fresh):
    for name in ['.codex/hooks.json', '.omc/hooks.json']:
        data = json.loads((fresh / name).read_text())
        assert not any(data['hooks'].values()), name
    assert not (fresh / 'scripts/pre-commit.sample').exists()
    assert not (fresh / 'scripts/post-commit.sample').exists()


def test_setup_and_force_reinstall_leave_hooks_disabled(tmp_path):
    subprocess.run(['git', 'init', '-q', str(tmp_path)], check=True)
    sentinel = b'#!/bin/sh\n# user-owned hook\nexit 0\n'
    hooks = [tmp_path / '.git/hooks' / n for n in ['pre-commit', 'post-commit']]
    for p in hooks:
        p.write_bytes(sentinel)
        p.chmod(0o644)
    install(tmp_path)
    install(tmp_path, force=True)
    for p in hooks:
        assert p.read_bytes() == sentinel
        assert p.stat().st_mode & 0o777 == 0o644
    data = json.loads((tmp_path / '.codex/hooks.json').read_text())
    assert not any(data['hooks'].values())


@pytest.mark.parametrize('force', [False, True])
def test_reinstall_preserves_non_codex_user_files(tmp_path, force):
    sentinels = {}
    for name in ['.claude/settings.json', '.gemini/settings.json', '.cursor/hooks.json', '.agent/rules/local.md']:
        p = tmp_path / name
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text('{"user": true}\n')
        sentinels[p] = p.read_bytes()
    install(tmp_path)
    install(tmp_path, force=force)
    assert all(p.read_bytes() == raw for p, raw in sentinels.items())


def test_doctor_accepts_absent_non_codex_integrations(fresh):
    checks = omc_doctor.run_checks(fresh)
    unsupported = [c for c in checks if any(n in c.label for n in ['.claude', '.gemini', '.cursor', '.agent/', 'Antigravity'])]
    assert not unsupported, [c.label for c in unsupported]
    policy = [c for c in checks if c.label == 'Codex automatic hooks disabled']
    assert len(policy) == 1 and policy[0].ok


def test_disabled_hooks_do_not_imply_completed_work(fresh):
    before = {str(p.relative_to(fresh)): p.read_bytes() for p in (fresh / '.omc').rglob('*') if p.is_file()}
    omc_doctor.run_checks(fresh)
    after = {str(p.relative_to(fresh)): p.read_bytes() for p in (fresh / '.omc').rglob('*') if p.is_file()}
    assert after == before
    assert not (fresh / '.omc/state/sessions').exists()


def test_receipt_and_source_hash_follow_codex_manifest(fresh):
    receipt = json.loads((fresh / '.omc/install-receipt.json').read_text())
    assert not any(n.startswith(('.claude/', '.gemini/', '.cursor/', '.agent/')) for n in receipt['entries'])
    result = subprocess.run([sys.executable, '-B', str(ROOT / 'scripts/omc_install_audit.py'), '--strict', str(fresh)], capture_output=True, text=True)
    assert result.returncode == 0, result.stdout + result.stderr


def test_setup_does_not_create_a_session(tmp_path):
    target = tmp_path / 'target'
    target.mkdir()
    marker = tmp_path / 'unexpected-hook-execution'
    (target / '.omc').mkdir()
    hook_config = {'version': 1, 'hooks': {'session_start': [{'type': 'shell', 'command': 'touch ' + str(marker)}]}}
    (target / '.omc/hooks.json').write_text(json.dumps(hook_config))
    result = subprocess.run(
        [sys.executable, '-B', str(ROOT / 'scripts/omc.py'), 'setup', '--target', str(target)],
        cwd=ROOT, capture_output=True, text=True,
        env={**os.environ, 'OMC_INSTALLATION_REGISTRY_DIR': str(tmp_path / 'registry'), 'GIT_OPTIONAL_LOCKS': '0'},
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert not marker.exists(), 'setup invoked an existing lifecycle command'
    assert json.loads((target / '.omc/hooks.json').read_text()) == hook_config
    sessions = target / '.omc/state/sessions'
    assert not sessions.exists() or not any(sessions.iterdir())
    assert not any(json.loads((target / '.codex/hooks.json').read_text())['hooks'].values())


def test_doctor_cli_does_not_initialize_state(tmp_path):
    install(tmp_path)
    before = {str(p.relative_to(tmp_path)): p.read_bytes() for p in (tmp_path / '.omc').rglob('*') if p.is_file()}
    result = subprocess.run([sys.executable, '-B', str(ROOT / 'scripts/omc_doctor.py'), '--target', str(tmp_path)], capture_output=True, text=True)
    assert result.returncode in [0, 1], result.stdout + result.stderr
    after = {str(p.relative_to(tmp_path)): p.read_bytes() for p in (tmp_path / '.omc').rglob('*') if p.is_file()}
    assert after == before, 'read-only doctor initialized or rewrote OMC state'


@pytest.mark.parametrize('force', [False, True])
def test_enabled_codex_target_is_blocked_without_mutation(tmp_path, force):
    p = tmp_path / '.codex/hooks.json'
    p.parent.mkdir()
    original = b'{"hooks":{"SessionStart":[{"hooks":[{"command":"echo user-hook"}]}]}}'
    p.write_bytes(original)
    result = subprocess.run([sys.executable, '-B', str(ROOT / 'scripts/install.py'), '--target', str(tmp_path)] + (['--force'] if force else []), capture_output=True, text=True)
    assert result.returncode != 0
    assert p.read_bytes() == original
    assert sorted(str(q.relative_to(tmp_path)) for q in tmp_path.rglob('*')) == ['.codex', '.codex/hooks.json']


def test_audit_rejects_enabled_hooks_even_when_preserved_by_receipt(tmp_path):
    install(tmp_path)
    p = tmp_path / '.codex/hooks.json'
    p.write_text('{"hooks":{"SessionStart":[{"hooks":[{"command":"echo bad"}]}]}}')
    receipt_path = tmp_path / '.omc/install-receipt.json'
    receipt = json.loads(receipt_path.read_text())
    receipt['entries']['.codex/hooks.json'].update(policy='preserve', ownership='preserved', status='preserved')
    receipt_path.write_text(json.dumps(receipt))
    result = subprocess.run([sys.executable, '-B', str(ROOT / 'scripts/omc_install_audit.py'), '--strict', str(tmp_path)], capture_output=True, text=True)
    assert result.returncode != 0, result.stdout + result.stderr
    assert 'hooks:automatic-execution-enabled-or-invalid' in result.stdout


def test_doctor_does_not_warn_about_disabled_git_hooks(tmp_path):
    subprocess.run(['git', 'init', '-q', str(tmp_path)], check=True)
    hook = tmp_path / '.git/hooks/pre-commit'
    hook.write_text('#!/bin/sh\nexit 0\n')
    hook.chmod(0o644)
    install(tmp_path)
    check = next(c for c in omc_doctor.run_checks(tmp_path) if c.label == '훅 파일 실행권한 (chmod +x)')
    assert check.ok, check.detail
    assert hook.stat().st_mode & 0o777 == 0o644
