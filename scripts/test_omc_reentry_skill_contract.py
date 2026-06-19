"""
omc-reentry skill contract regression tests.

Reentry is a return-to-project skill. It must restore project context quickly
without degenerating into status dumping or implementation planning.
"""
from __future__ import annotations

import re
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
MAX_NON_EMPTY_LINES = 44

REQUIRED_REENTRY_SKILL_PATHS = [
    ROOT / ".agents" / "skills" / "omc-reentry" / "SKILL.md",
    ROOT / "templates" / ".agents" / "skills" / "omc-reentry" / "SKILL.md",
    ROOT / "templates" / ".agent" / "skills" / "omc-reentry" / "SKILL.md",
]
OPTIONAL_REENTRY_SKILL_PATHS = [
    ROOT / ".agent" / "skills" / "omc-reentry" / "SKILL.md",
]

REQUIRED_SEQUENCE = [
    "README",
    "ETHOS.md",
    "AGENTS.md",
    "git status -sb",
    "git log --oneline -5",
    "python3 scripts/omc.py state status --target .",
    "프로젝트 한 줄 요약",
    "핵심 구조",
    "실행/검증 진입점",
    "주의할 SSOT/금지 경로",
    "최근 작업 흔적",
    "다음 읽을 파일 3개",
    "추천 다음 스킬",
]

REQUIRED_BEHAVIOR_MARKERS = [
    "문서 기준",
    "현재 상태 기준",
    "문서 우선",
    "정확히 3개",
    "정확히 1개",
    "tree dump 금지",
    "README 재요약 금지",
    "$omc-status",
    "$omc-plan",
    "자동으로 진행하지는 않습니다.",
]

REQUIRED_BOUNDARY_MARKERS = [
    "omc-status",
    "현재 세션 상태",
    "프로젝트 복귀 맥락 복원",
    "구현 태스크 분해를 하지 않습니다",
]

REQUIRED_FOCUS_MARKERS = [
    "사용자에게 보여줄 것",
    "시스템이 암묵적으로 처리",
]

VALID_REENTRY_SAMPLE = """
프로젝트 한 줄 요약: OMC 멀티 LLM 개발 운영 킷이다.
핵심 구조: scripts/, templates/, .omc/, docs/
실행/검증 진입점: python3 scripts/omc.py state status --target .
주의할 SSOT/금지 경로: templates/.agents/skills/ 가 SSOT다.
최근 작업 흔적: 마지막 커밋은 status run visibility 개선이다.
다음 읽을 파일 3개:
1. README.md
2. scripts/omc.py
3. .omc/notepad.md
추천 다음 스킬: $omc-plan
"""

INVALID_REENTRY_SAMPLE = """
이 프로젝트는 대충 OMC 같다.
다음 액션은 알아서 고르자.
"""


def _read(path: Path) -> str:
    assert path.exists(), f"missing reentry skill path: {path.relative_to(ROOT)}"
    return path.read_text(encoding="utf-8")


def _collect_reentry_skill_texts(
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


def _validate_reentry_output(sample: str) -> list[str]:
    required_patterns = {
        "summary": r"프로젝트 한 줄 요약:\s*\S",
        "structure": r"핵심 구조:\s*\S",
        "entry": r"실행/검증 진입점:\s*\S",
        "ssot": r"주의할 SSOT/금지 경로:\s*\S",
        "recent": r"최근 작업 흔적:\s*\S",
        "reading_list": r"다음 읽을 파일 3개:.*1\..*2\..*3\.",
        "next_skill": r"추천 다음 스킬:\s*\$omc-(status|plan|review|investigate)",
    }
    return [
        name
        for name, pattern in required_patterns.items()
        if not re.search(pattern, sample, re.S)
    ]


def test_reentry_skill_paths_are_identical():
    texts = _collect_reentry_skill_texts(
        root=ROOT,
        required_paths=REQUIRED_REENTRY_SKILL_PATHS,
        optional_paths=OPTIONAL_REENTRY_SKILL_PATHS,
    )
    canonical = texts[".agents/skills/omc-reentry/SKILL.md"]
    mismatched = [name for name, text in texts.items() if text != canonical]
    assert not mismatched, f"omc-reentry skill copies differ: {mismatched}"


def test_ignored_live_agent_reentry_path_is_optional(tmp_path: Path):
    canonical = tmp_path / ".agents" / "skills" / "omc-reentry" / "SKILL.md"
    template_codex = tmp_path / "templates" / ".agents" / "skills" / "omc-reentry" / "SKILL.md"
    template_agent = tmp_path / "templates" / ".agent" / "skills" / "omc-reentry" / "SKILL.md"
    ignored_live_agent = tmp_path / ".agent" / "skills" / "omc-reentry" / "SKILL.md"

    for path in (canonical, template_codex, template_agent):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("same", encoding="utf-8")

    texts = _collect_reentry_skill_texts(
        root=tmp_path,
        required_paths=(canonical, template_codex, template_agent),
        optional_paths=(ignored_live_agent,),
    )
    assert texts == {
        ".agents/skills/omc-reentry/SKILL.md": "same",
        "templates/.agents/skills/omc-reentry/SKILL.md": "same",
        "templates/.agent/skills/omc-reentry/SKILL.md": "same",
    }


def test_reentry_skill_stays_short_enough_to_scan():
    text = _read(REQUIRED_REENTRY_SKILL_PATHS[0])
    non_empty_lines = [line for line in text.splitlines() if line.strip()]
    assert len(non_empty_lines) <= MAX_NON_EMPTY_LINES, (
        f"omc-reentry has {len(non_empty_lines)} non-empty lines"
    )


def test_reentry_skill_preserves_required_execution_order():
    text = _read(REQUIRED_REENTRY_SKILL_PATHS[0])
    cursor = -1
    missing_or_reordered: list[str] = []

    for marker in REQUIRED_SEQUENCE:
        next_pos = text.find(marker, cursor + 1)
        if next_pos == -1:
            missing_or_reordered.append(marker)
        else:
            cursor = next_pos

    assert not missing_or_reordered, f"missing or reordered markers: {missing_or_reordered}"


def test_reentry_skill_preserves_required_behavior_markers():
    text = _read(REQUIRED_REENTRY_SKILL_PATHS[0])
    missing = [marker for marker in REQUIRED_BEHAVIOR_MARKERS if marker not in text]
    assert not missing, f"missing behavior markers: {missing}"


def test_reentry_skill_declares_boundary_vs_status():
    text = _read(REQUIRED_REENTRY_SKILL_PATHS[0])
    missing = [marker for marker in REQUIRED_BOUNDARY_MARKERS if marker not in text]
    assert not missing, f"missing boundary markers: {missing}"


def test_reentry_skill_explains_visible_vs_implicit_work():
    text = _read(REQUIRED_REENTRY_SKILL_PATHS[0])
    missing = [marker for marker in REQUIRED_FOCUS_MARKERS if marker not in text]
    assert not missing, f"missing focus markers: {missing}"


def test_valid_reentry_output_fixture_has_required_structure():
    assert _validate_reentry_output(VALID_REENTRY_SAMPLE) == []


def test_invalid_reentry_output_fixture_exposes_weak_reentry_report():
    failures = _validate_reentry_output(INVALID_REENTRY_SAMPLE)
    assert {"summary", "structure", "entry", "ssot", "recent", "reading_list", "next_skill"}.issubset(
        set(failures)
    )
