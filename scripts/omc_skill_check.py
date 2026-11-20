#!/usr/bin/env python3
"""Read-only Codex skill registration checks (single skill or --all)."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import re

BASE = Path(__file__).resolve().parent.parent
_TEMPLATES = BASE / 'templates'
ROOT = _TEMPLATES if (not (BASE/'.omc/install-source.json').exists()
    and not (BASE/'.omc/install-source.json').is_symlink()
    and (_TEMPLATES/'.agents/skills').is_dir()
    and (_TEMPLATES/'AGENTS.md').is_file()
    and (BASE/'scripts/install.py').is_file()) else BASE


def _rel(path: Path, root: Path) -> Path:
    try:
        return path.relative_to(root)
    except ValueError:
        return path


def get_omc_kit_path() -> Path | None:
    """Prefer current registration; invalid explicit metadata never falls back."""
    if ROOT == _TEMPLATES:
        return None  # Already checking the canonical templates, not an installed mirror.
    metadata = BASE/'.omc/install-source.json'
    legacy = BASE/'.omc/hub.path'
    if metadata.exists() or metadata.is_symlink():
        data = json.loads(metadata.read_text(encoding='utf-8'))
        raw = data.get('source_path') if isinstance(data, dict) else None
        if not isinstance(raw, str) or not raw.strip():
            raise ValueError('invalid install-source.json source_path')
    elif legacy.exists() or legacy.is_symlink():
        raw = legacy.read_text(encoding='utf-8').strip()
        if not raw:
            raise ValueError('invalid legacy source registration')
    else:
        return None
    try:
        source = Path(raw).expanduser()
    except RuntimeError as error:
        raise ValueError('declared source path cannot expand user') from error
    if not source.is_absolute():
        source = BASE/source
    if not source.is_dir():
        raise ValueError('declared source directory is missing or unreadable')
    return source


def skill_name_to_command_name(skill: str) -> str:
    return skill.removeprefix('omc-')


def _check_file(num: int, label: str, path: Path) -> tuple[dict, bytes | None]:
    try:
        data = path.read_bytes()
        if not data.strip():
            raise ValueError('empty file')
    except (OSError, ValueError) as error:
        return dict(num=num, label=label, path=path, ok=False, reason=f'{_rel(path, ROOT)}: {error}'), None
    return dict(num=num, label=label, path=path, ok=True, reason=''), data


def check_skill(skill: str, verbose: bool = True) -> list[dict]:
    """Check actual Codex discovery surfaces, plus any explicitly declared SSOT."""
    if not re.fullmatch(r'omc-[a-z0-9][a-z0-9-]*', skill):
        return [dict(num=1, label='Codex skill name', path=None, ok=False, reason='invalid skill name')]
    canonical = ROOT/'.agents/skills'/skill/'SKILL.md'
    skill_result, skill_bytes = _check_file(1, 'Codex 스킬', canonical)
    if skill_bytes is not None:
        try:
            import yaml
            text = skill_bytes.decode('utf-8')
            lines = text.splitlines()
            if not lines or lines[0] != '---':
                raise ValueError('missing YAML frontmatter')
            end = lines.index('---', 1)
            metadata = yaml.safe_load('\n'.join(lines[1:end]))
            if (not isinstance(metadata, dict) or metadata.get('name') != skill
                or not isinstance(metadata.get('description'), str)
                or not metadata['description'].strip()):
                raise ValueError('frontmatter requires matching name and nonempty description')
        except ImportError:
            skill_result.update(ok=False, reason='PyYAML is required for frontmatter validation')
        except (ValueError, yaml.YAMLError) as error:
            skill_result.update(ok=False, reason=f'invalid Codex frontmatter: {error}')
    results = [skill_result,
        _check_file(2, 'Codex CLI 공통 참조', ROOT/'.codex/commands/omc-commands.md')[0],
        _check_file(3, 'Codex 공통 지침', ROOT/'AGENTS.md')[0],
    ]
    # Static reference tables need not enumerate every discoverable skill.
    try:
        source = get_omc_kit_path()
        if source is None:
            results.append(dict(num=4, label='source SSOT (선택)', path=None,
                                ok=None, skip=True, reason='canonical source 또는 외부 source 미등록'))
        else:
            path = source/'templates/.agents/skills'/skill/'SKILL.md'
            result, source_bytes = _check_file(4, 'source SSOT', path)
            if result['ok'] and results[0]['ok'] and source_bytes != skill_bytes:
                result.update(ok=False, reason='deployed Codex skill differs from registered source')
            results.append(result)
    except (OSError, ValueError) as error:
        results.append(dict(num=4, label='source SSOT', path=None, ok=False, reason=str(error)))
    return results


def print_report(skill: str, results: list[dict], fix_hint: bool = False):
    passed = sum(r['ok'] is True for r in results)
    failed = sum(r['ok'] is False for r in results)
    skipped = sum(r['ok'] is None for r in results)
    print(f'\nSKILL CHECK: {skill}')
    for result in results:
        icon = '[x]' if result['ok'] is True else '[-]' if result['ok'] is None else '[ ]'
        print(f"  {icon} {result['num']}. {result['label']}")
        if result['reason']:
            print(f"       → {result['reason']}")
    print(f'결과: {passed}/{len(results)} 통과  {failed}개 누락  {skipped}개 건너뜀')
    if failed and fix_hint:
        print('등록 경로·선언된 source를 확인하세요. 자동 설치·초기화는 수행하지 않습니다.')
        print('  참고: .agents/skills/SKILL_CHECKLIST.md')


def list_all_skills() -> list[str]:
    def names(folder: Path) -> set[str]:
        if not folder.is_dir():
            return set()
        return {p.name for p in folder.iterdir()
                if p.is_dir() and p.name.startswith('omc-')
                and ((p / 'SKILL.md').exists() or (p / 'SKILL.md').is_symlink())}
    skills = names(ROOT/'.agents/skills')
    source = get_omc_kit_path()
    if source is not None:
        folder = source/'templates/.agents/skills'
        if not folder.is_dir():
            raise ValueError('declared source skill inventory is missing')
        skills |= names(folder)
    receipt = BASE/'.omc/install-receipt.json'
    if ROOT != _TEMPLATES and (receipt.exists() or receipt.is_symlink()):
        data = json.loads(receipt.read_text(encoding='utf-8'))
        entries = data.get('entries') if isinstance(data, dict) else None
        if not isinstance(entries, dict):
            raise ValueError('invalid install receipt inventory')
        for path in entries:
            match = re.fullmatch(r'\.agents/skills/(omc-[a-z0-9][a-z0-9-]*)/SKILL\.md', path)
            if match:
                skills.add(match.group(1))
    return sorted(skills)


def main() -> int:
    parser = argparse.ArgumentParser(description='Codex 스킬 등록 검증 (읽기 전용)')
    parser.add_argument('skill', nargs='?')
    parser.add_argument('--all', action='store_true')
    parser.add_argument('--fix-hint', action='store_true')
    args = parser.parse_args()
    if args.all:
        try:
            skills = list_all_skills()
        except (OSError, ValueError) as error:
            print(f'스킬 목록 검증 실패: {error}')
            return 1
        if not skills:
            print('스킬 없음 (.agents/skills/ 에 omc-* 폴더 없음)')
            return 1
    elif args.skill == 'pr-create':
        print('pr-create는 공용 보조 Git 스킬이며 OMC 검사 대상이 아닙니다.')
        return 1
    elif args.skill:
        skills = [args.skill if args.skill.startswith('omc-') else f'omc-{args.skill}']
    else:
        parser.print_help()
        return 0
    failures = 0
    for skill in skills:
        results = check_skill(skill)
        print_report(skill, results, args.fix_hint)
        failures += sum(r['ok'] is False for r in results)
    return 1 if failures else 0


if __name__ == '__main__':
    raise SystemExit(main())
