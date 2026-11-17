from __future__ import annotations

from pathlib import Path

import omc_sync_ssot


def test_sync_map_does_not_recreate_singular_skill_templates():
    destinations = {destination for _, destination in omc_sync_ssot.SYNC_MAP}

    assert not any(path.startswith("templates/.agent/skills/") for path in destinations)


def test_sync_map_publishes_canonical_agent_skills():
    assert (
        ".agents/skills/omc-task/SKILL.md",
        "templates/.agents/skills/omc-task/SKILL.md",
    ) in omc_sync_ssot.SYNC_MAP


def test_sync_map_only_accepts_kit_managed_agent_skills():
    for path in (
        Path("omc-task/SKILL.md"),
        Path("omc-plan/references/workflow.md"),
        Path("pr-create/SKILL.md"),
        Path("SKILL_CHECKLIST.md"),
        Path("SKILL_TEMPLATE.md"),
    ):
        assert omc_sync_ssot._is_managed_agent_skill(path)

    assert not omc_sync_ssot._is_managed_agent_skill(Path("project-local/SKILL.md"))


import json
import os
import shutil
import subprocess
import sys

import pytest

SOURCE = Path(__file__).resolve().parents[1] / 'scripts/omc_sync_ssot.py'
MODES = [[], ['--check'], ['--dry-run']]
FOREIGN = (
    '.cursor/rules/omc-local.mdc', '.cursor/rules/bm-local.mdc',
    '.gemini/settings.json', '.gemini/commands/local.md',
    '.claude/commands/local.md', '.agent/skills/omc-local/SKILL.md',
    'CLAUDE.md', 'GEMINI.md',
)


def _write(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)


def _snapshot(root):
    result = {}
    for p in root.rglob('*'):
        if p.is_symlink():
            result[str(p.relative_to(root))] = ('link', os.readlink(p))
        elif p.is_file():
            result[str(p.relative_to(root))] = (p.read_bytes(), p.stat().st_mtime_ns)
    return result


@pytest.fixture
def synthetic_project(tmp_path):
    root = tmp_path / 'project'
    kit = root / 'omc_kit'
    supported = {
        'scripts/omc_sync_ssot.py': SOURCE.read_bytes(),
        'scripts/omc_engine.py': b'# synthetic common engine\n',
        'scripts/omc_claude_provider.py': b'# synthetic common model provider\n',
        'AGENTS.md': b'# synthetic shared instructions\n',
        'CODEX.md': b'# synthetic Codex instructions\n',
        '.agents/skills/omc-example/SKILL.md': b'# synthetic managed skill\n',
        '.agents/skills/omc-example/references/workflow.md': b'# synthetic workflow\n',
        '.agents/skills/pr-create/SKILL.md': b'# synthetic Git helper\n',
        '.agents/skills/SKILL_CHECKLIST.md': b'# synthetic checklist\n',
        '.agents/skills/SKILL_TEMPLATE.md': b'# synthetic skill template\n',
        '.codex/commands/omc-commands.md': b'# synthetic Codex reference\n',
    }
    expected = set()
    for relative, data in supported.items():
        destination = relative if relative.startswith('scripts/') else 'templates/' + relative
        _write(root / relative, data)
        _write(kit / destination, data)
        expected.add((relative, destination))
    for base in (root, kit):
        _write(base / '.omc/record.json', b'{"synthetic":"preserve-original"}\n')
        _write(base / '.codex/hooks.json', b'{"hooks":{}}\n')
        _write(base / 'backups/raw-failure.txt', b'synthetic raw failure\n')
    return root, kit, expected


def _cli(root, args):
    return subprocess.run([sys.executable, '-B', str(root / 'scripts/omc_sync_ssot.py'), *args],
                          cwd=root, capture_output=True, text=True)


def _map(root):
    # Import the actual copied module after seeding, without altering its discovery.
    result = subprocess.run([sys.executable, '-B', '-c',
        'import json,runpy,sys; print(json.dumps(runpy.run_path(sys.argv[1])["SYNC_MAP"]))',
        str(root / 'scripts/omc_sync_ssot.py')], cwd=root, capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    return [tuple(p) for p in json.loads(result.stdout)]


@pytest.mark.parametrize('args', MODES)
def test_non_codex_surfaces_are_never_sync_targets(synthetic_project, args):
    root, kit, expected = synthetic_project
    for relative in FOREIGN:
        _write(root / relative, b'synthetic foreign live data\n')
        _write(kit / 'templates' / relative, b'synthetic preserved foreign destination\n')
    before = _snapshot(root)
    result = _cli(root, args)
    assert result.returncode == 0, result.stdout + result.stderr
    assert not any(p in result.stdout for p in FOREIGN)
    assert _snapshot(root) == before
    assert set(_map(root)) == expected


def test_supported_codex_and_common_pairs_remain(synthetic_project):
    root, kit, expected = synthetic_project
    _write(kit / 'scripts/omc_engine.py', b'different previous engine\n')
    (kit / 'templates/.agents/skills/omc-example/references/workflow.md').unlink()
    pairs = _map(root)
    assert len(pairs) == len(set(pairs))
    assert set(pairs) == expected
    result = _cli(root, [])
    assert result.returncode == 0, result.stderr
    assert all((root / live).read_bytes() == (kit / dest).read_bytes() for live, dest in expected)


def test_project_owned_skills_are_not_adopted(synthetic_project):
    root, kit, expected = synthetic_project
    for relative in ['.agents/skills/project-local/SKILL.md', '.agents/skills/NOTES.md']:
        _write(root / relative, b'synthetic user-owned source\n')
        _write(kit / 'templates' / relative, b'synthetic user-owned destination\n')
    before = _snapshot(root)
    assert set(_map(root)) == expected
    result = _cli(root, [])
    assert result.returncode == 0, result.stderr
    assert 'project-local' not in result.stdout and 'NOTES.md' not in result.stdout
    assert _snapshot(root) == before


@pytest.mark.parametrize('args', MODES)
@pytest.mark.parametrize('case', ['equal', 'changed', 'missing'])
def test_mode_exit_codes_and_write_boundaries(synthetic_project, args, case):
    root, kit, expected = synthetic_project
    destination = kit / 'templates/CODEX.md'
    if case == 'changed':
        _write(destination, b'synthetic previous Codex doc\n')
    elif case == 'missing':
        destination.unlink()
    before = _snapshot(root)
    result = _cli(root, args)
    assert result.returncode == (int(case != 'equal') if args == ['--check'] else 0), result.stderr
    expected_path_line = '  CODEX.md  →  templates/CODEX.md'
    if args and case != 'equal':
        assert result.stdout.splitlines().count(expected_path_line) == 1, result.stdout
    after = _snapshot(root)
    if args or case == 'equal':
        assert after == before
    else:
        relative = str(destination.relative_to(root))
        assert set(after) - set(before) <= {relative}
        assert not (set(before) - set(after))
        assert {p for p in before if before[p] != after[p]} <= {relative}
        assert destination.read_bytes() == (root / 'CODEX.md').read_bytes()
    # Source bytes, unrelated evidence, and no-op retry metadata all remain intact.
    second_before = _snapshot(root)
    second = _cli(root, args)
    assert second.returncode == (result.returncode if args else 0)
    if args and case != 'equal':
        assert second.stdout.splitlines().count(expected_path_line) == 1, second.stdout
    assert _snapshot(root) == second_before


@pytest.mark.parametrize('args', MODES)
def test_foreign_symlink_trees_and_evidence_are_untouched(synthetic_project, tmp_path, args):
    root, kit, expected = synthetic_project
    external = tmp_path / 'external-evidence'
    _write(external / 'gemini/settings.json', b'synthetic external Gemini settings\n')
    _write(external / 'cursor/omc-local.mdc', b'synthetic external Cursor rule\n')
    (root / '.gemini').symlink_to(external / 'gemini', target_is_directory=True)
    (root / '.cursor').mkdir()
    (root / '.cursor/rules').symlink_to(external / 'cursor', target_is_directory=True)
    before = _snapshot(tmp_path)
    result = _cli(root, args)
    assert result.returncode == 0, result.stdout + result.stderr
    assert '.gemini/' not in result.stdout and '.cursor/' not in result.stdout
    assert set(_map(root)) == expected
    assert _snapshot(tmp_path) == before
