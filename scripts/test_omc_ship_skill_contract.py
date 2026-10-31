"""
omc-ship skill contract regression tests.

Ship is a release gate. Shortening it must preserve blocking checks and keep
push/deploy execution behind explicit user approval.
"""
from __future__ import annotations

import re

import pytest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
MAX_NON_EMPTY_LINES = 49

REQUIRED_SHIP_SKILL_PATHS = [
    ROOT / ".agents" / "skills" / "omc-ship" / "SKILL.md",
    ROOT / "templates" / ".agents" / "skills" / "omc-ship" / "SKILL.md",
]
OPTIONAL_SHIP_SKILL_PATHS = [
    ROOT / ".agent" / "skills" / "omc-ship" / "SKILL.md",
]

REQUIRED_SEQUENCE = ['## 입력과 실행 범위', '## 승인 연결', '## 공통 명령 계약', '## 시간·증거와 중단', '## 보고와 권한 경계', '## 다음 추천', '## Machine output contract']

REQUIRED_BEHAVIOR_MARKERS = ['읽기 전용', '원문을 보존', '자동 재시도', 'cleanup_confirmed=true', 'evidence_complete=true', '품질 명령0회', '비밀값 검사', '명시적 승인', '실패·불확실성을 성공으로 완화하지 않는다', 'detached_v1', '사용자 최종 답변에 붙이지 않는다']

REQUIRED_FOCUS_MARKERS = ['설정 승인됨', '실행 준비됨', '검사 통과', '배포 완료']

VALID_SHIP_SAMPLE = """
OMC 가드: PASS
TDD 게이트: PASS
테스트: PASS
타입: PASS
린트: PASS
품질 게이트 상태: ready
품질 게이트 실행: PASS
현재 ship 대상 범위: templates/.claude/commands/plan.md, scripts/test_llm_autopilot_commands.py
범위 밖 dirty 변경: docs/omc_quickstart.md
git status -sb: clean
git diff HEAD: SECRET/KEY/TOKEN/PASSWORD 없음
untracked: 없음
사용자 명시 승인: 완료
결론: SHIP READY
"""

INVALID_SHIP_SAMPLE = """
OMC 가드: PASS
테스트: PASS
결론: SHIP READY
"""

VALID_READY_SHIP_RECOMMENDATION_SAMPLE = """
결론: SHIP READY
다음 추천:
- 주추천 1개만 제시, 우선순위: 현재 결론에 맞는 1개를 먼저 말합니다.
- SHIP READY → 사용자 선택 대기
- 실제 배포 후 → `$omc-retro`
- BLOCKED + 테스트/회귀 실패 → `$omc-investigate`
- BLOCKED + 신규 테스트 누락/TDD 위반 → `$omc-task`
- 자동으로 진행하지는 않습니다.
"""


def _read(path: Path) -> str:
    assert path.exists(), f"missing ship skill path: {path.relative_to(ROOT)}"
    return path.read_text(encoding="utf-8")


def _collect_ship_skill_texts(
    *,
    root: Path,
    required_paths: tuple[Path, ...] | list[Path],
    optional_paths: tuple[Path, ...] | list[Path],
) -> dict[str, str]:
    texts = {path.relative_to(root).as_posix(): _read(path) for path in required_paths}
    texts.update(
        {
            path.relative_to(root).as_posix(): path.read_text(encoding="utf-8")
            for path in optional_paths
            if path.exists()
        }
    )
    return texts


def _extract_primary_recommendation(sample: str) -> str:
    in_section = False
    for raw_line in sample.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        if line.startswith("다음 추천"):
            in_section = True
            continue
        if not in_section or not line.startswith("-"):
            continue
        if "사용자 선택 대기" in line:
            return "사용자 선택 대기"
        match = re.search(r"(\$omc-[a-z-]+)", line)
        if match:
            return match.group(1)
    raise AssertionError("primary recommendation not found")


def _validate_ship_output(sample: str) -> list[str]:
    required_patterns = {
        "guard": r"OMC 가드:\s*PASS",
        "tdd": r"TDD 게이트:\s*PASS",
        "tests": r"테스트:\s*PASS",
        "types": r"타입:\s*PASS",
        "lint": r"린트:\s*PASS",
        "quality_status": r"품질 게이트 상태:\s*ready",
        "quality_run": r"품질 게이트 실행:\s*PASS",
        "status": r"git status -sb:\s*\S",
        "secrets": r"SECRET/KEY/TOKEN/PASSWORD 없음",
        "untracked": r"untracked:\s*\S",
        "approval": r"사용자 명시 승인:\s*완료",
        "verdict": r"결론:\s*SHIP READY",
    }
    return [
        name
        for name, pattern in required_patterns.items()
        if not re.search(pattern, sample, re.S)
    ]


def test_ship_skill_paths_are_identical():
    texts = _collect_ship_skill_texts(
        root=ROOT,
        required_paths=REQUIRED_SHIP_SKILL_PATHS,
        optional_paths=OPTIONAL_SHIP_SKILL_PATHS,
    )
    canonical = texts[".agents/skills/omc-ship/SKILL.md"]
    mismatched = [name for name, text in texts.items() if text != canonical]
    assert not mismatched, f"omc-ship skill copies differ: {mismatched}"


def test_ignored_live_agent_ship_path_is_optional(tmp_path: Path):
    canonical = tmp_path / ".agents" / "skills" / "omc-ship" / "SKILL.md"
    template_codex = tmp_path / "templates" / ".agents" / "skills" / "omc-ship" / "SKILL.md"
    ignored_live_agent = tmp_path / ".agent" / "skills" / "omc-ship" / "SKILL.md"

    for path in (canonical, template_codex):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("same", encoding="utf-8")

    texts = _collect_ship_skill_texts(
        root=tmp_path,
        required_paths=(canonical, template_codex),
        optional_paths=(ignored_live_agent,),
    )
    assert texts == {
        ".agents/skills/omc-ship/SKILL.md": "same",
        "templates/.agents/skills/omc-ship/SKILL.md": "same",
    }


def test_ship_skill_stays_short_enough_to_scan():
    text = _workflow_without_output_protocol(_read(REQUIRED_SHIP_SKILL_PATHS[0]))
    body, marker, remainder = text.partition('<!-- OMC_SHIP_ENTRY_CONTRACT:BEGIN -->')
    contract, marker2, tail = remainder.partition('<!-- OMC_SHIP_ENTRY_CONTRACT:END -->')
    assert marker and marker2
    assert len([line for line in contract.splitlines() if line.strip()]) <= 27
    assert len([line for line in (body + tail).splitlines() if line.strip()]) <= MAX_NON_EMPTY_LINES


def test_ship_skill_avoids_duplicate_approval_wording():
    text = _read(REQUIRED_SHIP_SKILL_PATHS[0])
    assert text.count("사용자 명시 승인") <= 2, "duplicate approval wording should be trimmed"


def test_ship_skill_revalidates_the_reviewed_candidate_before_ready():
    from test_omc_ship_entry_contract import read_contract
    data = read_contract(REQUIRED_SHIP_SKILL_PATHS[0])
    ids = [step['id'] for step in data['steps']]
    assert ids.index('candidate_review') < ids.index('run')
    step = next(step for step in data['steps'] if step['id'] == 'candidate_review')
    assert 'validate-ship' in step['argv'] and step['json_status'] == 'READY'


def test_ship_skill_preserves_required_execution_order():
    from test_omc_ship_entry_contract import read_contract
    for path in REQUIRED_SHIP_SKILL_PATHS:
        data = read_contract(path)
        assert data['steps'][-1]['id'] == 'run'
    text = _read(REQUIRED_SHIP_SKILL_PATHS[0])
    assert [text.index(marker) for marker in REQUIRED_SEQUENCE] == sorted(text.index(marker) for marker in REQUIRED_SEQUENCE)


def test_ship_skill_preserves_required_behavior_markers():
    text = _read(REQUIRED_SHIP_SKILL_PATHS[0])
    missing = [marker for marker in REQUIRED_BEHAVIOR_MARKERS if marker not in text]
    assert not missing, f"missing behavior markers: {missing}"


def test_ship_skill_explains_visible_vs_implicit_work():
    text = _read(REQUIRED_SHIP_SKILL_PATHS[0])
    missing = [marker for marker in REQUIRED_FOCUS_MARKERS if marker not in text]
    assert not missing, f"missing focus markers: {missing}"


def test_ship_skill_recommendations_match_blocked_and_post_deploy_states():
    text = _read(REQUIRED_SHIP_SKILL_PATHS[0])
    for marker in ('다음 추천', '주추천 1개', '우선순위', 'BLOCKED', '$omc-investigate',
                   '$omc-task', 'SHIP READY', '승인된 다음 행동', '실제 배포 후',
                   '$omc-retro', '자동으로 진행하지는 않습니다.'):
        assert marker in text


def test_ship_recommendation_fixture_holds_on_ready_state():
    assert _extract_primary_recommendation(VALID_READY_SHIP_RECOMMENDATION_SAMPLE) == "사용자 선택 대기"


def test_ship_skill_has_single_required_check_section():
    text = _read(REQUIRED_SHIP_SKILL_PATHS[0])
    assert text.count('<!-- OMC_SHIP_ENTRY_CONTRACT:BEGIN -->') == 1
    assert text.count('<!-- OMC_SHIP_ENTRY_CONTRACT:END -->') == 1


def test_valid_ship_output_fixture_has_required_structure():
    assert _validate_ship_output(VALID_SHIP_SAMPLE) == []


def test_invalid_ship_output_fixture_exposes_weak_release_gate():
    failures = _validate_ship_output(INVALID_SHIP_SAMPLE)
    assert {"tdd", "types", "lint", "secrets", "approval"}.issubset(set(failures))


def test_ship_template_declares_one_next_action_and_environment_stop():
    for path in (ROOT / 'templates/.agents/skills/omc-ship/SKILL.md',
                 ROOT / 'templates/.claude/commands/ship.md'):
        text = path.read_text()
        assert '주추천 1개' in text
        assert 'environment_not_ready' in text
        assert '품질 명령0회' in text
        assert '자동으로 진행하지는 않습니다.' in text


def _workflow_without_output_protocol(text):
    body, separator, protocol = text.partition('\n## Machine output contract\n')
    assert separator and text.count('\n## Machine output contract\n') == 1
    assert len([line for line in protocol.splitlines() if line.strip()]) <= 9
    for marker in ('detached_v1', 'omc-output/v1', 'publish', 'complete-task',
                   '사용자 최종 답변에 붙이지 않는다', 'legacy_inline',
                   '출력 실패', '구형으로 우회하지 않는다'):
        assert marker in protocol, marker
    return '\n'.join(line for line in body.splitlines() if not line.startswith('기계 기록의 단계:'))


def test_fresh_install_uses_uncommitted_ship_and_skill_templates(tmp_path):
    import os
    import subprocess
    import sys
    env = {**os.environ, 'GIT_OPTIONAL_LOCKS': '0', 'OMC_AUTO_UPDATE': '0'}
    index = Path(subprocess.check_output(
        ['git', 'rev-parse', '--path-format=absolute', '--git-path', 'index'],
        cwd=ROOT, env=env, text=True).strip())
    assert index.is_absolute() and index.is_file()
    before = index.read_bytes()
    before_mode = index.stat().st_mode & 0o777
    target = tmp_path / 'installed'
    target.mkdir()
    result = subprocess.run([sys.executable, str(ROOT / 'scripts/install.py'), '--target', str(target)],
        cwd=ROOT, env=env,
        capture_output=True, text=True, timeout=60)
    assert result.returncode == 0, result.stdout + result.stderr
    pairs = [('critique', 'SKILL.md'), ('investigate', 'SKILL.md'),
             ('plan', 'references/workflow.md'), ('review', 'SKILL.md'),
             ('ship', 'SKILL.md'), ('task', 'SKILL.md')]
    for name, file in pairs:
        rel = Path('.agents/skills') / ('omc-' + name) / file
        template = ROOT / 'templates' / rel
        for prefix in ('.agents', '.agent'):
            generated = target / prefix / 'skills' / ('omc-' + name) / file
            assert generated.read_bytes() == template.read_bytes(), generated
            assert generated.stat().st_mode & 0o777 == template.stat().st_mode & 0o777, generated
    audit = [sys.executable, str(ROOT / 'scripts/omc_install_audit.py'), '--json', '--strict', str(target)]
    clean = subprocess.run(audit, cwd=ROOT, env={**os.environ, 'GIT_OPTIONAL_LOCKS': '0'},
                           capture_output=True, text=True, timeout=60)
    assert clean.returncode == 0, clean.stdout + clean.stderr
    corrupted = target / '.agent/skills/omc-review/SKILL.md'
    corrupted.write_text(corrupted.read_text() + '\nlocal corruption\n')
    bad = subprocess.run(audit, cwd=ROOT, env={**os.environ, 'GIT_OPTIONAL_LOCKS': '0'},
                         capture_output=True, text=True, timeout=60)
    assert bad.returncode != 0
    assert 'omc-review/SKILL.md' in bad.stdout
    assert index.read_bytes() == before
    assert index.stat().st_mode & 0o777 == before_mode


def test_skill_shared_record_protocol_is_identical_and_fail_closed():
    import pytest
    shared = _read(ROOT / 'templates/.agents/skills/omc-ship/SKILL.md').split('\n## Machine output contract\n', 1)[1]
    for name, file in [('critique', 'SKILL.md'), ('investigate', 'SKILL.md'),
                       ('plan', 'references/workflow.md'), ('review', 'SKILL.md'),
                       ('task', 'SKILL.md'), ('ship', 'SKILL.md')]:
        text = _read(ROOT / 'templates/.agents/skills' / ('omc-' + name) / file)
        assert text.split('\n## Machine output contract\n', 1)[1] == shared
        _workflow_without_output_protocol(text)
        for marker in ('detached_v1', 'publish', 'legacy_inline', '구형으로 우회하지 않는다'):
            with pytest.raises(AssertionError):
                _workflow_without_output_protocol(text.replace(marker, 'MISSING'))


@pytest.mark.parametrize('layout', ['clone', 'linked-worktree'])
def test_install_check_preserves_resolved_index_in_git_layouts(tmp_path, monkeypatch, layout):
    import os
    import shutil
    import subprocess
    env = {**os.environ, 'GIT_OPTIONAL_LOCKS': '0', 'OMC_AUTO_UPDATE': '0'}
    original = ROOT
    original_index = Path(subprocess.check_output(
        ['git', 'rev-parse', '--path-format=absolute', '--git-path', 'index'],
        cwd=original, env=env, text=True).strip())
    original_bytes = original_index.read_bytes()
    original_mode = original_index.stat().st_mode & 0o777
    seed = tmp_path / 'seed'
    subprocess.run(['git', 'clone', '--template=', '--quiet', '--no-hardlinks', str(original), str(seed)],
                   env=env, check=True, capture_output=True, timeout=30)
    candidate = seed
    if layout == 'linked-worktree':
        candidate = tmp_path / 'linked'
        subprocess.run(['git', '-C', str(seed), 'worktree', 'add', '--detach', str(candidate), 'HEAD'],
                       env=env, check=True, capture_output=True, timeout=30)
        assert (candidate / '.git').is_file()
    else:
        assert (candidate / '.git').is_dir()
    changed = subprocess.check_output(['git', 'diff', '--name-only', '--diff-filter=ACMRT', 'HEAD'],
                                      cwd=original, env=env, text=True).splitlines()
    for name in changed:
        if Path(name).parts[0] == '.omc':
            continue
        destination = candidate / name
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(original / name, destination)
    actual_index = Path(subprocess.check_output(
        ['git', 'rev-parse', '--path-format=absolute', '--git-path', 'index'],
        cwd=candidate, env=env, text=True).strip())
    donor_index = Path(subprocess.check_output(
        ['git', 'rev-parse', '--path-format=absolute', '--git-path', 'index'],
        cwd=seed, env=env, text=True).strip())
    assert actual_index.is_absolute() and actual_index.is_file()
    if layout == 'linked-worktree':
        assert actual_index != donor_index
    pins = {p: (p.read_bytes(), p.stat().st_mode & 0o777)
            for p in {actual_index, donor_index, original_index}}
    monkeypatch.setitem(globals(), 'ROOT', candidate)
    exercise = tmp_path / 'exercise'
    exercise.mkdir()
    test_fresh_install_uses_uncommitted_ship_and_skill_templates(exercise)
    for index, (content, mode) in pins.items():
        assert index.read_bytes() == content
        assert index.stat().st_mode & 0o777 == mode
    assert original_index.read_bytes() == original_bytes
    assert original_index.stat().st_mode & 0o777 == original_mode
