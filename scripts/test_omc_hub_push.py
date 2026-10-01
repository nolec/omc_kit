import subprocess
import sys
from pathlib import Path
import pytest
import install

ROOT = Path(__file__).resolve().parent.parent


@pytest.mark.parametrize('case', ['managed', 'modified', 'project_owned', 'other_drift', 'symlink'])
def test_force_retirement_from_previous_install_receipt(tmp_path, case):
    import json
    import shutil

    legacy_kit = tmp_path / 'legacy-kit'
    tracked = subprocess.run(
        ['git', 'ls-files', '-z'], cwd=ROOT, check=True, capture_output=True,
    ).stdout.decode().split('\0')
    # Build only from tracked source bytes, not credentials or local artifacts.
    for relative in filter(None, tracked):
        source = ROOT / relative
        assert not source.is_symlink(), relative
        if source.is_file():
            destination = legacy_kit / relative
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, destination)
    rule_name = 'omc-retired-other.mdc' if case == 'other_drift' else 'omc-hub-sync.mdc'
    legacy_rule = legacy_kit / 'templates/.cursor/rules' / rule_name
    legacy_rule.write_text('# Legacy Hub rule\npython3 scripts/omc_hub_push.py --push\n')
    target = tmp_path / 'consumer'
    target.mkdir()
    rule = target / '.cursor/rules' / rule_name
    if case == 'project_owned':
        rule.parent.mkdir(parents=True)
        rule.write_text('project-owned rule\n')
    initial = subprocess.run([sys.executable, str(legacy_kit / 'scripts/install.py'), '--target', str(target)], text=True, capture_output=True)
    assert initial.returncode == 0, initial.stdout + initial.stderr
    previous = json.loads((target / '.omc/install-receipt.json').read_text())
    relative_rule = str(rule.relative_to(target))
    entry = previous['entries'][relative_rule]
    assert entry['policy'] == ('preserve' if case == 'project_owned' else 'managed_exact')
    assert rule.exists()
    if case in {'modified', 'other_drift'}:
        rule.write_text('user-modified managed rule\n')
    if case == 'symlink':
        external = tmp_path / 'external-rule'
        external.write_bytes(rule.read_bytes())
        rule.unlink()
        rule.symlink_to(external)
    preserved = rule.read_bytes()
    updated = subprocess.run(
        [sys.executable, str(ROOT / 'scripts/install.py'), '--target', str(target), '--force'],
        text=True, capture_output=True,
    )
    assert updated.returncode == 0, updated.stdout + updated.stderr
    if case == 'managed':
        assert not rule.exists()
    else:
        assert rule.read_bytes() == preserved
        if case != 'other_drift':
            assert relative_rule in updated.stdout
    audit = subprocess.run(
        [sys.executable, str(ROOT / 'scripts/omc_install_audit.py'), str(target), '--strict'],
        text=True, capture_output=True,
    )
    if case in {'other_drift', 'symlink'}:
        assert audit.returncode != 0, audit.stdout + audit.stderr
        if case == 'symlink':
            assert rule.is_symlink()
            assert external.read_bytes() == preserved
        current = json.loads((target / '.omc/install-receipt.json').read_text())
        assert current['entries'][relative_rule]['policy'] != 'preserve'
        return
    assert audit.returncode == 0, audit.stdout + audit.stderr
    if case != 'managed':
        current = json.loads((target / '.omc/install-receipt.json').read_text())
        kept = current['entries'][relative_rule]
        assert kept['policy'] == 'preserve'
        assert kept['ownership'] == 'preserved'
        assert kept['source_sha256'] == ''
    repeated = subprocess.run(
        [sys.executable, str(ROOT / 'scripts/install.py'), '--target', str(target), '--force'],
        text=True, capture_output=True,
    )
    assert repeated.returncode == 0, repeated.stdout + repeated.stderr
    assert not rule.exists() if case == 'managed' else rule.read_bytes() == preserved
    if case != 'managed':
        current = json.loads((target / '.omc/install-receipt.json').read_text())
        assert current['entries'][relative_rule]['ownership'] == 'preserved'
        assert current['entries'][relative_rule]['policy'] == 'preserve'
    audit = subprocess.run(
        [sys.executable, str(ROOT / 'scripts/omc_install_audit.py'), str(target), '--strict'],
        text=True, capture_output=True,
    )
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
def test_install_surface_and_legacy_residue(tmp_path, force):
    target = tmp_path / 'consumer'
    target.mkdir()
    script = target / 'scripts/omc_hub_push.py'
    script.parent.mkdir()
    script.write_text('print("legacy")\n')
    rule = target / '.cursor/rules/omc-hub-sync.mdc'
    rule.parent.mkdir(parents=True)
    rule.write_text('project-owned legacy --push instruction\n')
    for path in ('.claude/settings.json', '.cursor/hooks.json', '.codex/hooks.json'):
        settings = target / path
        settings.parent.mkdir(parents=True, exist_ok=True)
        settings.write_text('{"hooks":{"PostToolUse":[{"hooks":[{"type":"command","command":".agent-hooks/omc-hub-push.sh"}]}]}}')
    shell = target / '.agent-hooks/omc-hub-push.sh'
    shell.parent.mkdir()
    shell.write_text('#!/bin/bash\necho legacy-hook\n')
    command = [sys.executable, str(ROOT / 'scripts/install.py'), '--target', str(target)]
    if force:
        command.append('--force')
    result = subprocess.run(command, cwd=ROOT, text=True, capture_output=True)
    assert result.returncode == 0, result.stdout + result.stderr
    assert 'Hub retirement' in result.stdout
    assert '.cursor/rules/omc-hub-sync.mdc' in result.stdout
    assert rule.read_text() == 'project-owned legacy --push instruction\n'
    invocation = subprocess.run([sys.executable, str(script), '--push'], text=True, capture_output=True)
    assert invocation.returncode == (2 if force else 0)
    hook_result = subprocess.run(['/bin/bash', str(shell)], text=True, capture_output=True)
    assert hook_result.returncode == 0
    assert hook_result.stdout == ('' if force else 'legacy-hook\n')
    if force:
        for path in ('.claude/settings.json', '.cursor/hooks.json', '.codex/hooks.json'):
            content = (target / path).read_text()
            assert 'omc-hub-push' not in content
            assert 'session' in content.lower()
        assert 'omc-post-file-check.sh' in (target / '.codex/hooks.json').read_text()
    else:
        for path in ('.claude/settings.json', '.cursor/hooks.json', '.codex/hooks.json'):
            assert 'omc-hub-push.sh' in (target / path).read_text()

def test_new_install_and_strict_audit(tmp_path):
    result = subprocess.run([sys.executable, str(ROOT / 'scripts/install.py'), '--target', str(tmp_path)], cwd=ROOT, text=True, capture_output=True)
    assert result.returncode == 0, result.stdout + result.stderr
    assert not (tmp_path / '.cursor/rules/omc-hub-sync.mdc').exists()
    result = subprocess.run([sys.executable, str(ROOT / 'scripts/omc_install_audit.py'), str(tmp_path), '--strict'], cwd=ROOT, text=True, capture_output=True)
    assert result.returncode == 0, result.stdout + result.stderr

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
    for path in ('.claude/settings.json', '.cursor/hooks.json', '.codex/hooks.json'):
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
