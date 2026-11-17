"""Public Codex registration checks: real files/CLI, no runtime-state mutation."""
from pathlib import Path
import json
import shutil
import subprocess
import sys

import pytest

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture(scope='module')
def installed(tmp_path_factory):
    target = tmp_path_factory.mktemp('skill-check-installed')
    result = subprocess.run([sys.executable, '-B', str(ROOT/'scripts/install.py'), '--target', str(target)], capture_output=True, text=True)
    assert result.returncode == 0, result.stdout + result.stderr
    return target


@pytest.fixture
def target(installed, tmp_path):
    return Path(shutil.copytree(installed, tmp_path/'target'))


def run(target, *args):
    before = {str(p.relative_to(target)): p.read_bytes() for p in target.rglob('*') if p.is_file()}
    result = subprocess.run([sys.executable, '-B', str(target/'scripts/omc_skill_check.py'), *args], cwd=target, capture_output=True, text=True)
    assert {str(p.relative_to(target)): p.read_bytes() for p in target.rglob('*') if p.is_file()} == before
    return result


def test_fresh_codex_registration_passes_without_other_ide_files(target):
    for name in ['.agent', '.claude', '.gemini', '.cursor']:
        assert not (target/name).exists()
    result = run(target, 'omc-review')
    assert result.returncode == 0, result.stdout + result.stderr


def test_all_installed_omc_skills_are_discovered(target):
    result = run(target, '--all')
    assert result.returncode == 0, result.stdout + result.stderr
    expected = {'autopilot', 'benchmark', 'brainstorm', 'ceo-review', 'critique',
                'dashboard', 'investigate', 'lesson', 'office-hours', 'plan', 'qa',
                'reentry', 'retro', 'review', 'ship', 'status', 'task'}
    assert {line.removeprefix('SKILL CHECK: omc-') for line in result.stdout.splitlines()
            if line.startswith('SKILL CHECK: ')} == expected


@pytest.mark.parametrize('relative', ['.agents/skills/omc-review/SKILL.md', '.codex/commands/omc-commands.md', 'AGENTS.md'])
def test_missing_required_codex_surface_fails(target, relative):
    (target/relative).unlink()
    assert run(target, 'omc-review').returncode == 1


def test_discovery_does_not_require_every_skill_in_static_reference_tables(target):
    (target/'.codex/commands/omc-commands.md').write_text('# Codex reference\n')
    (target/'AGENTS.md').write_text('# Codex instructions\n')
    assert run(target, 'omc-review').returncode == 0


def test_absent_optional_source_registration_does_not_block_discovery(target):
    (target/'.omc/install-source.json').unlink()
    assert run(target, 'review').returncode == 0


@pytest.mark.parametrize('metadata', ['{broken', '[]', '{}', '{"source_path":null}', '{"source_path":""}'])
def test_explicit_invalid_source_is_not_treated_as_absent(target, tmp_path, metadata):
    (target/'.omc/install-source.json').write_text(metadata)
    (target/'.omc/hub.path').write_text(str(ROOT))  # Must not hide invalid current registration.
    assert run(target, 'omc-review').returncode == 1


def test_declared_missing_source_fails(target, tmp_path):
    (target/'.omc/install-source.json').write_text(json.dumps({'source_path': str(tmp_path/'missing')}))
    assert run(target, 'omc-review').returncode == 1


def test_changed_deployed_skill_does_not_match_source(target):
    p = target/'.agents/skills/omc-review/SKILL.md'
    p.write_bytes(p.read_bytes()+b'\nchanged deployed skill\n')
    assert run(target, 'omc-review').returncode == 1


def test_legacy_source_registration_uses_plural_codex_skill_path(target, tmp_path):
    (target/'.omc/install-source.json').unlink()
    source = tmp_path/'legacy-source'
    skill = source/'templates/.agents/skills/omc-review/SKILL.md'
    skill.parent.mkdir(parents=True)
    skill.write_bytes((target/'.agents/skills/omc-review/SKILL.md').read_bytes())
    (target/'.omc/hub.path').write_text(str(source))
    assert run(target, 'omc-review').returncode == 0


def test_empty_canonical_skill_is_not_a_registered_skill(target):
    (target/'.omc/install-source.json').unlink()
    (target/'.agents/skills/omc-review/SKILL.md').write_bytes(b'')
    assert run(target, 'omc-review').returncode == 1


def test_empty_all_discovery_is_not_success(target):
    for p in (target/'.agents/skills').glob('omc-*'):
        shutil.rmtree(p)
    assert run(target, '--all').returncode == 1


def test_application_templates_do_not_change_installed_registration_root(target):
    folder=target/'templates';folder.mkdir();sentinel=folder/'application.html';sentinel.write_text('application-owned template')
    assert run(target,'omc-review').returncode == 0
    assert sentinel.read_text() == 'application-owned template'


def test_installed_reinstaller_uses_declared_source_with_app_templates(target):
    folder=target/'templates';folder.mkdir();sentinel=folder/'application.html';sentinel.write_text('application-owned template')
    result=subprocess.run([sys.executable,'-B',str(target/'scripts/install.py'),'--target',str(target)],cwd=target,capture_output=True,text=True)
    assert result.returncode == 0,result.stdout+result.stderr
    assert sentinel.read_text() == 'application-owned template'
    assert run(target,'omc-review').returncode == 0


@pytest.mark.parametrize('declared_source',[True,False])
def test_all_detects_a_whole_missing_registered_skill_directory(target,declared_source):
    if not declared_source:(target/'.omc/install-source.json').unlink()
    shutil.rmtree(target/'.agents/skills/omc-review')
    result=run(target,'--all')
    assert result.returncode == 1,result.stdout
    assert 'omc-review' in result.stdout


@pytest.mark.parametrize('payload',[
    b'# A guide without frontmatter\n',
    b'---\nskill_name: omc-review\ndescription: example\n---\n',
    b'---\nname: omc-other\ndescription: example\n---\n',
    b'---\nname: omc-review\ndescription: []\n---\n',
    b'---\nname: omc-review\n---\n',
    b'---\nname: [\ndescription: bad YAML\n---\n',
    b'\xff\xfeunreadable skill',
])
def test_invalid_frontmatter_is_not_registered_without_optional_ssot(target,payload):
    (target/'.omc/install-source.json').unlink()
    (target/'.agents/skills/omc-review/SKILL.md').write_bytes(payload)
    assert run(target,'omc-review').returncode == 1


@pytest.mark.parametrize('payload',[
    "---\nname: 'omc-review'\ndescription: >-\n  A folded\n  description\nmetadata:\n  short-description: review\n---\n# Body\n",
    '---\n{name: omc-review, description: "Quoted: description"}\n---\n# Body\n',
])
def test_standard_yaml_frontmatter_stays_supported(target,payload):
    (target/'.omc/install-source.json').unlink()
    (target/'.agents/skills/omc-review/SKILL.md').write_text(payload)
    assert run(target,'omc-review').returncode == 0


def test_recommended_skill_template_has_real_codex_name_and_registers(target,tmp_path):
    import yaml
    text=(ROOT/'templates/.agents/skills/SKILL_TEMPLATE.md').read_text().replace('[NAME]','example')
    metadata=yaml.safe_load(text.split('---',2)[1])
    assert metadata.get('name') == 'omc-example'
    source=tmp_path/'source';canonical=source/'templates/.agents/skills/omc-example/SKILL.md';canonical.parent.mkdir(parents=True);canonical.write_text(text)
    deployed=target/'.agents/skills/omc-example/SKILL.md';deployed.parent.mkdir();deployed.write_text(text)
    (target/'.omc/install-source.json').write_text(json.dumps({'source_path':str(source)}))
    assert run(target,'omc-example').returncode == 0


def test_pr_create_remains_explicitly_outside_omc_only_checker(target):
    result=run(target,'pr-create')
    assert result.returncode == 1
    assert '검사 대상' in result.stdout
    assert 'omc-pr-create' not in result.stdout


def test_source_kit_checks_canonical_templates_without_runtime_mutation():
    before={str(p.relative_to(ROOT/'.omc')):p.read_bytes() for p in (ROOT/'.omc').rglob('*') if p.is_file()}
    result=subprocess.run([sys.executable,'-B',str(ROOT/'scripts/omc_skill_check.py'),'--all'],cwd=ROOT,capture_output=True,text=True)
    assert result.returncode == 0,result.stdout+result.stderr
    assert {str(p.relative_to(ROOT/'.omc')):p.read_bytes() for p in (ROOT/'.omc').rglob('*') if p.is_file()}==before


@pytest.mark.parametrize('metadata', ['{}', '[]', '{broken'])
def test_installed_reinstaller_does_not_escape_bad_registration_via_templates(target, metadata):
    (target/'templates').mkdir()
    (target/'.omc/install-source.json').write_text(metadata)
    result = subprocess.run([sys.executable, '-B', str(target/'scripts/install.py'), '--target', str(target)], capture_output=True, text=True)
    assert result.returncode != 0
    assert 'metadata' in result.stdout + result.stderr


def test_all_rejects_invalid_explicit_receipt_inventory(target):
    (target/'.omc/install-receipt.json').write_text('{"entries": []}')
    assert run(target, '--all').returncode == 1


@pytest.mark.parametrize('registration', ['current', 'legacy'])
@pytest.mark.parametrize('args', [('omc-review',), ('--all',)])
def test_unknown_user_source_reports_failure_without_traceback(target, registration, args):
    import pwd
    unknown_user = 'omc_missing_user_529ab8525367490eb5b761c2'
    with pytest.raises(KeyError):
        pwd.getpwnam(unknown_user)
    raw = '~' + unknown_user + '/omc_kit'
    metadata = target/'.omc/install-source.json'
    if registration == 'current':
        metadata.write_text(json.dumps({'source_path': raw}))
    else:
        metadata.unlink()
        (target/'.omc/hub.path').write_text(raw)
    result = run(target, *args)
    assert result.returncode == 1
    assert 'declared source path cannot expand user' in result.stdout
    assert 'Traceback' not in result.stdout + result.stderr
    assert result.stderr == ''
