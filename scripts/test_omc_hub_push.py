import subprocess
import sys
from pathlib import Path
import pytest
import install

ROOT = Path(__file__).resolve().parent.parent


@pytest.mark.parametrize('case', ['managed', 'modified', 'project_owned', 'other_drift', 'symlink'])
def test_codex_force_install_preserves_foreign_receipt_files(tmp_path, case):
    """Synthetic previous receipt: other IDE files are outside Codex ownership."""
    import json
    target = tmp_path / 'consumer'
    target.mkdir()
    command = [sys.executable, str(ROOT / 'scripts/install.py'), '--target', str(target)]
    initial = subprocess.run(command, text=True, capture_output=True)
    assert initial.returncode == 0, initial.stdout + initial.stderr
    name = 'omc-retired-other.mdc' if case == 'other_drift' else 'omc-hub-sync.mdc'
    rule = target / '.cursor/rules' / name
    rule.parent.mkdir(parents=True)
    rule.write_text('synthetic previous IDE rule\n')
    relative = str(rule.relative_to(target))
    receipt_path = target / '.omc/install-receipt.json'
    previous = json.loads(receipt_path.read_text())
    previous['entries'][relative] = {
        'policy': 'preserve' if case == 'project_owned' else 'managed_exact',
        'ownership': 'preserved' if case == 'project_owned' else 'exclusive_managed',
        'source_sha256': install._sha256_file(rule),
        'target_sha256': install._sha256_file(rule),
        'status': 'preserved' if case == 'project_owned' else 'updated',
        'setup_created': case != 'project_owned',
    }
    receipt_path.write_text(json.dumps(previous))
    original_receipt = receipt_path.read_bytes()
    preserved_receipt = tmp_path / 'synthetic-previous-receipt.json'
    preserved_receipt.write_bytes(original_receipt)
    if case in {'modified', 'other_drift'}:
        rule.write_text('synthetic user modification\n')
    if case == 'symlink':
        external = tmp_path / 'external-rule'
        external.write_bytes(rule.read_bytes())
        rule.unlink()
        rule.symlink_to(external)
    raw = rule.read_bytes()
    for _ in range(2):
        updated = subprocess.run(command + ['--force'], text=True, capture_output=True)
        assert updated.returncode == 0, updated.stdout + updated.stderr
        assert rule.read_bytes() == raw
        if case == 'symlink':
            assert rule.is_symlink() and external.read_bytes() == raw
        current = json.loads(receipt_path.read_text())
        assert not any(n.startswith('.cursor/') for n in current['entries'])
        assert preserved_receipt.read_bytes() == original_receipt
        audit = subprocess.run([sys.executable, str(ROOT / 'scripts/omc_install_audit.py'), str(target), '--strict'], text=True, capture_output=True)
        assert audit.returncode == 0, audit.stdout + audit.stderr

@pytest.mark.parametrize('condition', ['source_present', 'ownership_unknown', 'hash_missing'])
def test_retired_hub_preservation_requires_verified_scope(tmp_path, condition):
    rule = tmp_path / '.cursor/rules/omc-hub-sync.mdc'
    rule.parent.mkdir(parents=True)
    rule.write_text('original')
    previous_hash = install._sha256_file(rule)
    rule.write_text('modified')
    entry = {
        'policy': 'managed_exact', 'previously_managed': True,
        'ownership': 'exclusive_managed', 'previous_receipt_schema_version': 3,
        'previous_target_sha256': previous_hash, 'registered_current_install': False,
    }
    if condition == 'source_present':
        entry['registered_current_install'] = True
    elif condition == 'ownership_unknown':
        entry['ownership'] = 'manual_review'
    else:
        entry['previous_target_sha256'] = ''
    assert install._prune_stale_managed_outputs(tmp_path, {'.cursor/rules/omc-hub-sync.mdc': entry}, force=True) == 0
    assert entry['policy'] == 'managed_exact'
    assert rule.read_text() == 'modified'

@pytest.mark.parametrize('force', [False, True])
def test_enabled_legacy_codex_hooks_block_install_without_mutation(tmp_path, force):
    target = tmp_path / 'consumer'
    target.mkdir()
    for name, content in {
        'scripts/omc_hub_push.py': 'print("legacy")\n',
        '.cursor/rules/omc-hub-sync.mdc': 'user-owned legacy instruction\n',
        '.claude/settings.json': '{"user":"preserve"}',
        '.codex/hooks.json': '{"hooks":{"PostToolUse":[{"hooks":[{"command":".agent-hooks/omc-hub-push.sh"}]}]}}',
        '.agent-hooks/omc-hub-push.sh': '#!/bin/sh\necho user-original\n',
    }.items():
        p = target / name
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(content)
    before = {str(p.relative_to(target)): p.read_bytes() for p in target.rglob('*') if p.is_file()}
    command = [sys.executable, str(ROOT / 'scripts/install.py'), '--target', str(target)]
    result = subprocess.run(command + (['--force'] if force else []), text=True, capture_output=True)
    assert result.returncode != 0
    assert 'automatic hooks must be disabled' in result.stdout + result.stderr
    assert before == {str(p.relative_to(target)): p.read_bytes() for p in target.rglob('*') if p.is_file()}
    assert not (target / '.omc').exists()

@pytest.mark.parametrize('args', [[], ['--push'], ['--dry-run'], ['-m', 'legacy']])
def test_retired_cli(tmp_path, args):
    script = tmp_path / 'scripts/omc_hub_push.py'
    script.parent.mkdir()
    script.write_bytes((ROOT / 'scripts/omc_hub_push.py').read_bytes())
    hub = tmp_path / 'hub'
    hub.mkdir()
    subprocess.run(['git', 'init', '-q', str(hub)], check=True)
    (hub / 'sentinel').write_text('dirty user data')
    config = tmp_path / '.omc'
    config.mkdir()
    (config / 'hub.path').write_text(str(hub))
    before = {str(p.relative_to(tmp_path)): p.read_bytes() for p in tmp_path.rglob('*') if p.is_file()}
    result = subprocess.run([sys.executable, str(script), *args], input='y\n', text=True, capture_output=True, cwd=tmp_path)
    assert result.returncode == 2
    assert 'retired' in result.stderr.lower()
    assert before == {str(p.relative_to(tmp_path)): p.read_bytes() for p in tmp_path.rglob('*') if p.is_file()}

@pytest.mark.parametrize('ownership,changed,removed', [('exclusive_managed', False, 1), ('exclusive_managed', True, 0), ('project_owned', False, 0)])
def test_hub_rule_pruning_respects_ownership(tmp_path, ownership, changed, removed):
    rule = tmp_path / '.cursor/rules/omc-hub-sync.mdc'
    rule.parent.mkdir(parents=True)
    rule.write_text('old hub rule')
    entry = {'policy': 'managed_exact', 'previously_managed': True, 'ownership': ownership, 'previous_receipt_schema_version': 3, 'previous_target_sha256': install._sha256_file(rule), 'registered_current_install': False}
    if changed:
        rule.write_text('user edited hub rule')
    assert install._prune_stale_managed_outputs(tmp_path, {'.cursor/rules/omc-hub-sync.mdc': entry}, force=True) == removed
    assert rule.exists() == (not removed)

def test_shell_no_external_calls(tmp_path):
    import os
    log = tmp_path / 'calls'
    for name in ('git', 'python3', 'cp', 'basename'):
        tool = tmp_path / name
        tool.write_text('#!/bin/sh\necho called >> "$CALL_LOG"\nexit 99\n')
        tool.chmod(0o755)
    result = subprocess.run(['/bin/bash', str(ROOT / 'templates/.agent-hooks/omc-hub-push.sh'), '--push'], input='bad', text=True, capture_output=True, env={**os.environ, 'PATH': str(tmp_path), 'CALL_LOG': str(log), 'INPUT_JSON': '{"params":{"path":"omc_example.py"}}'})
    assert result.returncode == 0
    assert result.stdout == result.stderr == ''
    assert not log.exists()

def test_templates_no_hub_automation():
    import json
    # Removing payload may leave empty containers; files and links are still forbidden.
    for name in ('.claude', '.cursor'):
        foreign = ROOT / 'templates' / name
        assert not foreign.is_symlink() and not foreign.is_file(), foreign
        assert not any(p.is_file() or p.is_symlink() for p in foreign.rglob('*')), foreign
    for path in ('.codex/hooks.json',):
        content = (ROOT / 'templates' / path).read_text()
        json.loads(content)
        assert 'omc-hub-push' not in content
    rule = ROOT / 'templates/.cursor/rules/omc-hub-sync.mdc'
    assert not rule.exists() or '--push' not in rule.read_text()

@pytest.mark.parametrize('force', [False, True])
def test_legacy_copy(tmp_path, force):
    dst = tmp_path / 'omc_hub_push.py'
    dst.write_text('print("legacy")\n')
    install._copy(ROOT / 'scripts/omc_hub_push.py', dst, force=force)
    result = subprocess.run([sys.executable, str(dst), '--push'], capture_output=True, text=True)
    assert result.returncode == (2 if force else 0)
    assert ('retired' in result.stderr.lower()) if force else result.stdout == 'legacy\n'

SOURCE_ONLY_HOOK_NAMES = (
    'omc-hub-push.sh',
    'omc-before-agent.sh',
    'omc-post-file-check.sh',
    'omc-session-end.sh',
    'omc-prompt-inject.sh',
    'omc-session-start.sh',
    'omc-pipeline-check.sh',
)


def test_new_install_and_strict_audit(tmp_path):
    result = subprocess.run([sys.executable, str(ROOT / 'scripts/install.py'), '--target', str(tmp_path)], cwd=ROOT, text=True, capture_output=True)
    assert result.returncode == 0, result.stdout + result.stderr
    assert not (tmp_path / '.cursor/rules/omc-hub-sync.mdc').exists()
    expected = {p.name for p in (ROOT / 'templates/.agent-hooks').glob('*.sh')} - set(SOURCE_ONLY_HOOK_NAMES)
    assert {p.name for p in (tmp_path / '.agent-hooks').glob('*.sh')} == expected
    doctor = subprocess.run(
        [sys.executable, str(tmp_path / 'scripts/omc_doctor.py'), '--target', str(tmp_path)],
        cwd=tmp_path, text=True, capture_output=True,
    )
    assert doctor.returncode == 0, doctor.stdout + doctor.stderr
    assert 'omc-session-start.sh' not in doctor.stdout
    assert 'omc-pipeline-check.sh' not in doctor.stdout
    for label in (
        'scripts/omc_pipeline_guard.py',
        '훅 파일 실행권한 (chmod +x)',
        'Codex automatic hooks disabled',
    ):
        assert any('[OK]' in line and label in line for line in doctor.stdout.splitlines()), doctor.stdout
    result = subprocess.run([sys.executable, str(ROOT / 'scripts/omc_install_audit.py'), str(tmp_path), '--strict'], cwd=ROOT, text=True, capture_output=True)
    assert result.returncode == 0, result.stdout + result.stderr


@pytest.mark.parametrize('name', SOURCE_ONLY_HOOK_NAMES)
@pytest.mark.parametrize('force', [False, True])
@pytest.mark.parametrize('modified', [False, True])
def test_reinstall_preserves_previous_legacy_hook(tmp_path, name, force, modified):
    import hashlib
    import json

    command = [sys.executable, '-B', str(ROOT / 'scripts/install.py'), '--target', str(tmp_path)]
    initial = subprocess.run(command, cwd=ROOT, text=True, capture_output=True)
    assert initial.returncode == 0, initial.stdout + initial.stderr
    relative = f'.agent-hooks/{name}'
    original = (ROOT / 'templates' / relative).read_bytes()
    content = original + b'\n# local change\n' if modified else original
    hook = tmp_path / relative
    hook.parent.mkdir(parents=True, exist_ok=True)
    hook.write_bytes(content)
    hook.chmod(0o644)
    receipt_path = tmp_path / '.omc/install-receipt.json'
    receipt = json.loads(receipt_path.read_text())
    digest = hashlib.sha256(original).hexdigest()
    receipt['entries'][relative] = {
        'policy': 'managed_exact', 'status': 'updated', 'ownership': 'exclusive_managed',
        'source_sha256': digest, 'target_sha256': digest,
    }
    receipt_path.write_text(json.dumps(receipt))

    reinstall = subprocess.run(command + (['--force'] if force else []),
                               cwd=ROOT, text=True, capture_output=True)
    assert reinstall.returncode == 0, reinstall.stdout + reinstall.stderr
    assert hook.read_bytes() == content
    assert hook.stat().st_mode & 0o777 == 0o644
    receipt = json.loads(receipt_path.read_text())
    assert receipt['entries'][relative]['target_sha256'] == hashlib.sha256(content).hexdigest()
    if modified:
        if name == 'omc-hub-push.sh':
            assert 'preserved non-stub' in reinstall.stdout
        assert receipt['entries'][relative]['policy'] == 'preserve'


@pytest.mark.parametrize('surface', ['.claude', '.cursor'])
@pytest.mark.parametrize('shape', ['absent', 'empty', 'top_file', 'nested_file',
                                  'root_link', 'broken_root_link', 'nested_link', 'broken_nested_link'])
def test_foreign_template_payload_contract(tmp_path, monkeypatch, surface, shape):
    # Empty containers are not deployable payload. Files and links remain forbidden.
    root = tmp_path / 'synthetic-kit'
    hooks = root / 'templates/.codex/hooks.json'
    hooks.parent.mkdir(parents=True)
    hooks.write_text('{"hooks":{}}')
    foreign = root / 'templates' / surface
    outside = tmp_path / 'external-evidence'
    outside.mkdir()
    (outside / 'raw.txt').write_text('synthetic external raw evidence\n')
    if shape == 'empty':
        (foreign / 'commands').mkdir(parents=True)
    elif shape == 'top_file':
        foreign.write_text('unexpected payload\n')
    elif shape == 'nested_file':
        (foreign / 'commands').mkdir(parents=True)
        (foreign / 'commands/local.md').write_text('unexpected payload\n')
    elif shape in {'root_link', 'broken_root_link'}:
        foreign.symlink_to(outside if shape == 'root_link' else tmp_path / 'missing', target_is_directory=True)
    elif shape in {'nested_link', 'broken_nested_link'}:
        foreign.mkdir()
        (foreign / 'linked').symlink_to(outside if shape == 'nested_link' else tmp_path / 'missing', target_is_directory=True)
    monkeypatch.setattr(sys.modules[__name__], 'ROOT', root)
    before = (outside / 'raw.txt').read_bytes()
    if shape in {'absent', 'empty'}:
        test_templates_no_hub_automation()
    else:
        with pytest.raises(AssertionError):
            test_templates_no_hub_automation()
    assert (outside / 'raw.txt').read_bytes() == before
