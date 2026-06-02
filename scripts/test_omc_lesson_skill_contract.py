"""
omc-lesson skill contract regression tests.

Lesson capture is allowed to create real .omc/lessons files only when the user
is intentionally recording a lesson. The skill document must keep duplicate
search, required fields, supported CLI flags, and verification clear.
"""
from __future__ import annotations

import re
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
MAX_NON_EMPTY_LINES = 52

REQUIRED_LESSON_SKILL_PATHS = [
    ROOT / ".agents" / "skills" / "omc-lesson" / "SKILL.md",
    ROOT / "templates" / ".agents" / "skills" / "omc-lesson" / "SKILL.md",
    ROOT / "templates" / ".agent" / "skills" / "omc-lesson" / "SKILL.md",
]
OPTIONAL_LESSON_SKILL_PATHS = [
    ROOT / ".agent" / "skills" / "omc-lesson" / "SKILL.md",
]

REQUIRED_SEQUENCE = [
    "단순 메모",
    ".omc/notepad.md",
    "python3 scripts/omc_lesson.py search \"키워드\" --top 3",
    "유사 교훈",
    "기존 교훈 확인",
    "수동 편집",
    "신규 기록",
    "제목",
    "증상",
    "원인",
    "규칙",
    "태그",
    "verify",
    "행동 지침",
    "기록 금지",
    "python3 scripts/omc_lesson.py add -i",
    "python3 scripts/omc_lesson.py add",
    "--title",
    "--symptom",
    "--cause",
    "--rule",
    "--verify",
    "--tags",
    "python3 scripts/omc_lesson.py list",
    "python3 scripts/omc_lesson.py show",
    "교훈 없음",
    "기존 교훈 후보 발견",
    "한 파일 = 한 교훈",
    "BM25 자동 주입",
    "$omc-retro",
]

REQUIRED_BEHAVIOR_MARKERS = [
    "이번 task",
    "실제 `.omc/lessons/` 파일 생성·수정 금지",
    "update 명령 없음",
    "N/A",
    "general",
    "제목/증상/원인/규칙",
    "불명확하면",
]

REQUIRED_FOCUS_MARKERS = [
    "사용자에게 보여줄 것",
    "시스템이 암묵적으로 처리",
]

VALID_LESSON_SAMPLE = """
중복 검색: python3 scripts/omc_lesson.py search "git add" --top 3
판단: 기존 교훈 후보 발견
조치: update 명령 없음 — 기존 교훈 확인 후 수동 편집 또는 신규 기록 여부 확인
필수 필드: 제목/증상/원인/규칙 명확
태그: general
verify: python3 -m pytest
확인: python3 scripts/omc_lesson.py list
다음 액션: $omc-retro
"""

INVALID_LESSON_SAMPLE = """
교훈 추가:
python3 scripts/omc_lesson.py add --title "대충" --rule "N/A"
"""


def _read(path: Path) -> str:
    assert path.exists(), f"missing lesson skill path: {path.relative_to(ROOT)}"
    return path.read_text(encoding="utf-8")


def _collect_lesson_skill_texts(
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


def _validate_lesson_output(sample: str) -> list[str]:
    required_patterns = {
        "search": r"search\s+\"[^\"]+\"\s+--top 3",
        "decision": r"(교훈 없음|신규 기록|기존 교훈 후보 발견)",
        "duplicate_policy": r"update 명령 없음.*(수동 편집|신규 기록)",
        "required_fields": r"제목/증상/원인/규칙\s+명확",
        "tags": r"태그:\s*(general|[a-z0-9_, -]+)",
        "verify": r"verify:\s*\S",
        "confirm": r"python3 scripts/omc_lesson.py (list|search|show)",
        "next_action": r"\$omc-retro|세션 계속",
    }
    return [
        name
        for name, pattern in required_patterns.items()
        if not re.search(pattern, sample, re.S)
    ]


def test_lesson_skill_paths_are_identical():
    texts = _collect_lesson_skill_texts(
        root=ROOT,
        required_paths=REQUIRED_LESSON_SKILL_PATHS,
        optional_paths=OPTIONAL_LESSON_SKILL_PATHS,
    )
    canonical = texts[".agents/skills/omc-lesson/SKILL.md"]
    mismatched = [name for name, text in texts.items() if text != canonical]
    assert not mismatched, f"omc-lesson skill copies differ: {mismatched}"


def test_lesson_skill_stays_short_enough_to_scan():
    text = _read(REQUIRED_LESSON_SKILL_PATHS[0])
    non_empty_lines = [line for line in text.splitlines() if line.strip()]
    assert len(non_empty_lines) <= MAX_NON_EMPTY_LINES, (
        f"omc-lesson has {len(non_empty_lines)} non-empty lines"
    )


def test_lesson_skill_preserves_required_execution_order():
    text = _read(REQUIRED_LESSON_SKILL_PATHS[0])
    cursor = -1
    missing_or_reordered: list[str] = []

    for marker in REQUIRED_SEQUENCE:
        next_pos = text.find(marker, cursor + 1)
        if next_pos == -1:
            missing_or_reordered.append(marker)
        else:
            cursor = next_pos

    assert not missing_or_reordered, f"missing or reordered markers: {missing_or_reordered}"


def test_lesson_skill_preserves_required_behavior_markers():
    text = _read(REQUIRED_LESSON_SKILL_PATHS[0])
    missing = [marker for marker in REQUIRED_BEHAVIOR_MARKERS if marker not in text]
    assert not missing, f"missing behavior markers: {missing}"


def test_lesson_skill_explains_visible_vs_implicit_work():
    text = _read(REQUIRED_LESSON_SKILL_PATHS[0])
    missing = [marker for marker in REQUIRED_FOCUS_MARKERS if marker not in text]
    assert not missing, f"missing focus markers: {missing}"


def test_lesson_skill_does_not_document_unsupported_update_command():
    text = _read(REQUIRED_LESSON_SKILL_PATHS[0])
    assert "omc_lesson.py update" not in text


def test_valid_lesson_output_fixture_has_required_structure():
    assert _validate_lesson_output(VALID_LESSON_SAMPLE) == []


def test_invalid_lesson_output_fixture_exposes_weak_capture_gate():
    failures = _validate_lesson_output(INVALID_LESSON_SAMPLE)
    assert {
        "search",
        "decision",
        "duplicate_policy",
        "required_fields",
        "verify",
        "confirm",
        "next_action",
    }.issubset(set(failures))
