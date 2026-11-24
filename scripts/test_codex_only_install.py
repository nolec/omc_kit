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


def test_doctor_treats_missing_cost_log_and_ethos_context_as_optional(fresh):
    result = subprocess.run(
        [sys.executable, '-B', str(fresh / 'scripts/omc_doctor.py'), '--target', str(fresh)],
        cwd=fresh, capture_output=True, text=True,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    lines = [line.strip() for line in result.stdout.splitlines()]
    assert any(line.startswith('[INFO] .omc/cost_log.jsonl') for line in lines)
    assert any(line.startswith('[INFO] ETHOS.md 섹션 5') for line in lines)
    assert not any(line.startswith('[WARN]') and ('cost_log.jsonl' in line or '섹션 5' in line)
                   for line in lines)
    assert 'omc_cost.py record' not in result.stdout
    assert not (fresh / '.omc/cost_log.jsonl').exists()
    assert any(line.startswith('[OK]   ETHOS.md 존재') for line in lines)


@pytest.mark.parametrize('entrypoint', ['installer', 'setup'])
def test_fresh_git_install_excludes_new_prompts_without_changing_user_rules(tmp_path, entrypoint):
    target = tmp_path / 'project'
    subprocess.run(['git', 'init', '-q', str(target)], check=True)
    gitignore = target / '.gitignore'
    gitignore.write_bytes(b'# Project rules\n/project-cache/\n')
    exclude = target / '.git/info/exclude'
    local_rules = b'# Local rules\n/local-note.txt\n'
    exclude.write_bytes(local_rules)

    args = ['install.py'] if entrypoint == 'installer' else ['omc.py', 'setup']
    env = {**os.environ, 'OMC_INSTALLATION_REGISTRY_DIR': str(tmp_path / 'registry')}
    result = subprocess.run(
        [sys.executable, '-B', str(ROOT / 'scripts' / args[0]), *args[1:], '--target', str(target)],
        cwd=ROOT, env=env, capture_output=True, text=True,
    )
    assert result.returncode == 0, result.stdout + result.stderr

    receipt = json.loads((target / '.omc/install-receipt.json').read_text())
    for name in ('PROMPT_COMMON.md', 'PROMPT_COMMON_LEAN.md'):
        assert (target / name).read_bytes() == (ROOT / 'templates' / name).read_bytes()
        entry = receipt['entries'][name]
        assert entry['setup_created'] is True
        assert entry['ownership'] == 'exclusive_managed'
        assert entry['policy'] == 'managed_exact'
        ignored = subprocess.run(
            ['git', 'check-ignore', '-v', '--', name], cwd=target, capture_output=True, text=True,
        )
        assert ignored.returncode == 0 and ignored.stdout.startswith('.git/info/exclude:')
    assert gitignore.read_bytes() == b'# Project rules\n/project-cache/\n'
    assert exclude.read_bytes().startswith(local_rules)
    assert exclude.read_text().count('# OMC-KIT:BEGIN') == 1

    audit = subprocess.run(
        [sys.executable, '-B', str(ROOT / 'scripts/omc_install_audit.py'), '--strict', str(target)],
        cwd=ROOT, capture_output=True, text=True,
    )
    assert audit.returncode == 0, audit.stdout + audit.stderr


@pytest.mark.parametrize('force', [False, True])
def test_reinstall_preserves_existing_ethos_and_forgets_missing_retired_entries(tmp_path, force):
    subprocess.run(['git', 'init', '-q', str(tmp_path)], check=True)
    install(tmp_path)

    ethos = tmp_path / 'ETHOS.md'
    original = '# Project rules\r\n\r\n## 5. 이 프로젝트 맥락\r\n\r\n___\r\n'.encode()
    ethos.write_bytes(original)
    ethos.chmod(0o640)
    prior_stat = ethos.stat()
    receipt_file = tmp_path / '.omc/install-receipt.json'
    receipt = json.loads(receipt_file.read_text())
    retired = '.agents/skills/omc-brainstorm/SKILL.md'
    retired_template = ROOT / 'templates' / retired
    assert not retired_template.exists()
    assert not (tmp_path / retired).exists()
    receipt['entries'][retired] = {
        'policy': 'managed_exact', 'ownership': 'exclusive_managed', 'status': 'updated',
        'source_sha256': 'a' * 64, 'target_sha256': 'a' * 64,
    }
    receipt_file.write_text(json.dumps(receipt))

    cli = tmp_path / 'scripts/omc.py'
    cli.unlink()
    result = subprocess.run(
        [sys.executable, '-B', str(ROOT / 'scripts/install.py'), '--target', str(tmp_path)]
        + (['--force'] if force else []),
        cwd=ROOT, input='Y\n', capture_output=True, text=True,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert ethos.read_bytes() == original
    assert ethos.stat().st_mode == prior_stat.st_mode
    assert ethos.stat().st_mtime_ns == prior_stat.st_mtime_ns
    receipt = json.loads(receipt_file.read_text())
    assert receipt['entries']['ETHOS.md']['policy'] == 'preserve'
    assert retired not in receipt['entries']
    assert cli.read_bytes() == (ROOT / 'scripts/omc.py').read_bytes()
    audit = subprocess.run(
        [sys.executable, '-B', str(ROOT / 'scripts/omc_install_audit.py'), '--strict', str(tmp_path)],
        cwd=ROOT, capture_output=True, text=True,
    )
    assert audit.returncode == 0, audit.stdout + audit.stderr


@pytest.mark.parametrize('flags', [(), ('--force',)])
def test_reinstall_preserves_agents_and_existing_git_excludes(tmp_path, flags):
    subprocess.run(['git', 'init', '-q', str(tmp_path)], check=True)
    install(tmp_path)
    agents = tmp_path / 'AGENTS.md'
    gitignore = tmp_path / '.gitignore'
    exclude = tmp_path / '.git/info/exclude'
    agents.write_bytes(b'# Project-only rules\r\nNo implicit workflows.\r\n')
    gitignore.write_bytes(b'# Team rules\r\n/local-cache/\r\n')
    exclude.write_bytes(exclude.read_bytes().replace(
        b'# OMC-KIT:END', b'/local-only.txt\n# OMC-KIT:END',
    ).replace(b'\n', b'\r\n'))
    paths = (agents, gitignore, exclude)
    before = [(path.read_bytes(), path.stat().st_mtime_ns) for path in paths]

    _install_seeds(tmp_path, *flags)

    assert [(path.read_bytes(), path.stat().st_mtime_ns) for path in paths] == before
    receipt = json.loads((tmp_path / '.omc/install-receipt.json').read_text())
    assert receipt['entries']['AGENTS.md']['policy'] == 'preserve'
    audit = subprocess.run(
        [sys.executable, '-B', str(ROOT / 'scripts/omc_install_audit.py'), '--strict', str(tmp_path)],
        cwd=ROOT, capture_output=True, text=True,
    )
    assert audit.returncode == 0, audit.stdout + audit.stderr


@pytest.mark.parametrize('staged', [False, True])
def test_reinstall_preserves_git_deleted_host_docs(tmp_path, staged):
    def git(*args):
        return subprocess.check_output(
            ['git', '--no-optional-locks', *args], cwd=tmp_path,
        )

    git('init', '-q')
    install(tmp_path)
    names = ('AGENTS.md', 'CODEX.md')
    git('add', '-f', '--', *names)
    git('-c', 'user.name=OMC Test', '-c', 'user.email=omc@example.com',
        '-c', 'core.hooksPath=/dev/null', 'commit', '-qm', 'Track project rules')
    for name in names:
        (tmp_path / name).unlink()
    if staged:
        git('add', '-u', '--', *names)
    status = git('status', '--porcelain=v1', '-z', '--', *names)
    assert b'D' in status
    index_before = (tmp_path / '.git/index').read_bytes()

    install(tmp_path)

    assert all(not (tmp_path / name).exists() for name in names)
    assert git('status', '--porcelain=v1', '-z', '--', *names) == status
    assert (tmp_path / '.git/index').read_bytes() == index_before
    receipt = json.loads((tmp_path / '.omc/install-receipt.json').read_text())
    assert all(name not in receipt['entries'] for name in names)
    result = subprocess.run(
        [sys.executable, '-B', str(ROOT / 'scripts/omc_install_audit.py'), '--strict', str(tmp_path)],
        cwd=ROOT, capture_output=True, text=True,
    )
    assert result.returncode == 0, result.stdout + result.stderr


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


SEED_OPTIONS = {
    '--with-research-tasks': ('shared_tasks', '.omc/tasks', '*.json'),
    '--with-shared-lessons': ('shared_lessons', '.omc/lessons', '*.md'),
}


def _seed_files(option):
    template, destination, pattern = SEED_OPTIONS[option]
    return {Path(destination) / p.name: p.read_bytes()
            for p in (ROOT / 'templates' / template).glob(pattern)}


def _install_seeds(target, *flags, setup=False):
    command = ['scripts/omc.py', 'setup'] if setup else ['scripts/install.py']
    result = subprocess.run(
        [sys.executable, '-B', *command, '--target', str(target), *flags],
        cwd=ROOT, capture_output=True, text=True,
        env={**os.environ, 'OMC_INSTALLATION_REGISTRY_DIR': str(target.parent / 'registry')},
    )
    assert result.returncode == 0, result.stdout + result.stderr


def test_default_install_excludes_bundled_tasks_and_lessons(fresh):
    assert not list((fresh / '.omc/tasks').glob('*.json'))
    assert not list((fresh / '.omc/lessons').glob('*.md'))
    receipt = json.loads((fresh / '.omc/install-receipt.json').read_text())
    assert all(str(path) not in receipt['entries'] for option in SEED_OPTIONS for path in _seed_files(option))


@pytest.mark.parametrize('setup', [False, True])
@pytest.mark.parametrize('option', SEED_OPTIONS)
def test_seed_options_are_independent(tmp_path, setup, option):
    target = tmp_path / 'project'
    _install_seeds(target, option, setup=setup)
    expected = _seed_files(option)
    deployed = {p.relative_to(target): p.read_bytes()
                for _, destination, pattern in SEED_OPTIONS.values()
                for p in (target / destination).glob(pattern)}
    assert deployed == expected
    receipt = json.loads((target / '.omc/install-receipt.json').read_text())
    assert all(receipt['entries'][str(path)]['policy'] == 'managed_exact' for path in expected)


@pytest.mark.parametrize('flags', [(), ('--force',), ('--force', *SEED_OPTIONS)])
def test_reinstall_preserves_seed_files_and_receipt_entries(tmp_path, flags):
    _install_seeds(tmp_path, *SEED_OPTIONS)
    paths = [path for option in SEED_OPTIONS for path in _seed_files(option)]
    task = tmp_path / '.omc/tasks/observed-collect.json'
    task.write_bytes(task.read_bytes() + b'\n')
    before = {path: (tmp_path / path).read_bytes() for path in paths}

    for _ in range(2):
        _install_seeds(tmp_path, *flags)
        receipt = json.loads((tmp_path / '.omc/install-receipt.json').read_text())
        assert all((tmp_path / path).read_bytes() == content for path, content in before.items())
        assert all(receipt['entries'][str(path)]['status'] == 'preserved' for path in paths)


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
    policy = [c for c in checks if c.label == 'Codex OMC WIP notice']
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
def test_wip_notice_preflight_leaves_target_unchanged(tmp_path, force):
    p = tmp_path / '.codex/hooks.json'
    p.parent.mkdir()
    original = b'{"hooks":{"SessionStart":[{"hooks":[{"command":"echo user-hook"}]}]}}'
    p.write_bytes(original)
    result = subprocess.run([sys.executable, '-B', str(ROOT / 'scripts/install.py'), '--target', str(tmp_path)] + (['--force'] if force else []), capture_output=True, text=True)
    assert result.returncode != 0
    assert p.read_bytes() == original
    assert sorted(str(q.relative_to(tmp_path)) for q in tmp_path.rglob('*')) == ['.codex', '.codex/hooks.json']


def test_audit_rejects_malformed_hooks_even_when_preserved_by_receipt(tmp_path):
    install(tmp_path)
    p = tmp_path / '.codex/hooks.json'
    p.write_text('{"hooks":{"SessionStart":[{"hooks":[{"command":"echo bad"}]}]}}')
    receipt_path = tmp_path / '.omc/install-receipt.json'
    receipt = json.loads(receipt_path.read_text())
    receipt['entries']['.codex/hooks.json'].update(policy='preserve', ownership='preserved', status='preserved')
    receipt_path.write_text(json.dumps(receipt))
    result = subprocess.run([sys.executable, '-B', str(ROOT / 'scripts/omc_install_audit.py'), '--strict', str(tmp_path)], capture_output=True, text=True)
    assert result.returncode != 0, result.stdout + result.stderr
    assert 'hooks:invalid-project-config-or-unsupported-omc-hook' in result.stdout


def test_doctor_does_not_warn_about_disabled_git_hooks(tmp_path):
    subprocess.run(['git', 'init', '-q', str(tmp_path)], check=True)
    hook = tmp_path / '.git/hooks/pre-commit'
    hook.write_text('#!/bin/sh\nexit 0\n')
    hook.chmod(0o644)
    install(tmp_path)
    check = next(c for c in omc_doctor.run_checks(tmp_path) if c.label == '훅 파일 실행권한 (chmod +x)')
    assert check.ok, check.detail
    assert hook.stat().st_mode & 0o777 == 0o644


def test_wip_notice_reinstall_lifecycle(tmp_path):
    target = tmp_path / "project"
    target.mkdir()
    subprocess.run(["git", "init", "-q", str(target)], check=True)
    env = {**os.environ, "OMC_INSTALLATION_REGISTRY_DIR": str(tmp_path / "registry")}
    setup = [sys.executable, "-B", str(ROOT / "scripts/omc.py"), "setup", "--target", str(target)]
    installer = [sys.executable, "-B", str(ROOT / "scripts/install.py"), "--target", str(target)]

    def run(command):
        result = subprocess.run(command, capture_output=True, text=True, env=env)
        assert result.returncode == 0, result.stdout + result.stderr

    run(installer)
    path = target / ".codex/hooks.json"
    original = json.loads(path.read_text())
    assert not any(original["hooks"].values())
    original["hooks"]["SessionStart"] = [{"matcher": "startup", "hooks": [
        {"type": "command", "command": "echo user-hook"},
    ]}]
    path.write_text(json.dumps(original))
    run(setup + ["--with-wip-notice"])
    enabled = path.read_bytes()
    assert len(json.loads(enabled)["hooks"]["SessionStart"]) == 2
    assert (target / "scripts/omc_wip_notice.py").is_file()
    for command in [installer + ["--with-wip-notice"], installer, installer + ["--force"], setup]:
        run(command)
        assert path.read_bytes() == enabled
    assert any(c.ok and "enabled" in c.detail for c in omc_doctor.run_checks(target)
               if c.label == "Codex OMC WIP notice")
    run([sys.executable, "-B", str(ROOT / "scripts/omc_install_audit.py"), "--strict", str(target)])
    run(installer + ["--without-wip-notice"])
    assert json.loads(path.read_bytes()) == original
    disabled = path.read_bytes()
    run(setup + ["--without-wip-notice"])
    assert path.read_bytes() == disabled



@pytest.mark.parametrize("opaque_config", ["non-directory", "malformed-hook-option"])
def test_wip_notice_opaque_preflight_writes_nothing(tmp_path, opaque_config):
    target = tmp_path / "project"
    target.mkdir()
    config_dir = target / ".codex"
    if opaque_config == "non-directory":
        config_dir.write_text("project-owned file")
    else:
        config_dir.mkdir()
        (config_dir / "hooks.json").write_text(
            '{"hooks":{"Stop":[{"hooks":[{"type":"command","command":"true","timeout":{}}]}]}}'
        )
    before = {p.relative_to(target): p.read_bytes() for p in target.rglob("*") if p.is_file()}
    result = subprocess.run(
        [sys.executable, "-B", str(ROOT / "scripts/install.py"), "--target", str(target), "--with-wip-notice"],
        capture_output=True, text=True,
    )
    assert result.returncode != 0
    assert {p.relative_to(target): p.read_bytes() for p in target.rglob("*") if p.is_file()} == before
