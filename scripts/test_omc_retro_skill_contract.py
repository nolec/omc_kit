"""
omc-retro skill contract regression tests.

Retro summarizes history. It must separate evidence sources, flag stale OMC
metadata, and avoid writing notepad or lesson files without explicit approval.
"""
from __future__ import annotations

import re
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
MAX_NON_EMPTY_LINES = 90

REQUIRED_RETRO_SKILL_PATHS = [
    ROOT / ".agents" / "skills" / "omc-retro" / "SKILL.md",
    ROOT / "templates" / ".agents" / "skills" / "omc-retro" / "SKILL.md",
    ROOT / "templates" / ".agent" / "skills" / "omc-retro" / "SKILL.md",
]
OPTIONAL_RETRO_SKILL_PATHS = [
    ROOT / ".agent" / "skills" / "omc-retro" / "SKILL.md",
]

REQUIRED_SEQUENCE = [
    "기간 미지정",
    "최근 7일",
    "이번 주",
    "Asia/Seoul",
    "날짜 범위",
    "python3 scripts/omc.py state status --target .",
    "cat .omc/notepad.md",
    "python3 scripts/omc_lesson.py list",
    "git log --oneline --since=\"7 days ago\"",
    "git log 기준",
    "omc state 기준",
    "notepad 기준",
    "lesson list 기준",
    "세션 불일치",
    "stale",
    "완료된 작업",
    "반복되는 문제 패턴",
    "완료되지 못한 작업",
    "다음 우선순위",
    "최대 3개",
    "교훈 필요 여부",
    "교훈 없음",
    "$omc-lesson",
    "기존 교훈 업데이트 후보",
    "사용자 명시 승인 전",
    ".omc/notepad.md",
    ".omc/lessons/",
    "직접 수정하지 않음",
    "미완료/차단 먼저",
    "반복 패턴 개선",
    "가치 큰 다음 스킬",
    "리스크 큰 자동화는 뒤",
    "없음",
]

REQUIRED_BEHAVIOR_MARKERS = [
    "같은 실패/차단/재시도 2회 이상",
    "같은 스킬에서 반복 수정",
    "테스트/가드 실패 반복",
    "사용자가 같은 질문을 반복",
    "N/A",
    "이유",
    "$omc-status",
]

VALID_RETRO_SAMPLE = """
RETRO — 2026-05-25..2026-05-31
출처/충돌:
  git log 기준: 스킬 슬림화 커밋 3개
  omc state 기준: resume hang retry
  notepad 기준: stale current_request
  lesson list 기준: 반복 패턴 2개
  세션 불일치: stale
완료된 작업: git log 기준 omc-lesson
반복되는 문제 패턴: 같은 스킬에서 반복 수정 2회 이상 → $omc-lesson 필요
완료되지 못한 작업: 없음
다음 우선순위 최대 3개: 미완료/차단 먼저, 반복 패턴 개선, 가치 큰 다음 스킬
교훈 필요 여부: 기존 교훈 업데이트 후보
"""

INVALID_RETRO_SAMPLE = """
RETRO
완료: 많이 함
다음: 알아서 notepad 업데이트
"""


def _read(path: Path) -> str:
    assert path.exists(), f"missing retro skill path: {path.relative_to(ROOT)}"
    return path.read_text(encoding="utf-8")


def _collect_retro_skill_texts(
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


def _validate_retro_output(sample: str) -> list[str]:
    required_patterns = {
        "range": r"RETRO\s+—\s+\d{4}-\d{2}-\d{2}.*\d{4}-\d{2}-\d{2}",
        "sources": r"git log 기준:.*omc state 기준:.*notepad 기준:.*lesson list 기준:",
        "stale": r"세션 불일치:\s*stale",
        "completed": r"완료된 작업:\s*\S",
        "patterns": r"반복되는 문제 패턴:.*\$omc-lesson",
        "unfinished": r"완료되지 못한 작업:\s*\S",
        "priorities": r"다음 우선순위 최대 3개:.*미완료/차단 먼저.*반복 패턴 개선",
        "lesson": r"교훈 필요 여부:\s*(교훈 없음|\$omc-lesson 필요|기존 교훈 업데이트 후보)",
    }
    return [
        name
        for name, pattern in required_patterns.items()
        if not re.search(pattern, sample, re.S)
    ]


def test_retro_skill_paths_are_identical():
    texts = _collect_retro_skill_texts(
        root=ROOT,
        required_paths=REQUIRED_RETRO_SKILL_PATHS,
        optional_paths=OPTIONAL_RETRO_SKILL_PATHS,
    )
    canonical = texts[".agents/skills/omc-retro/SKILL.md"]
    mismatched = [name for name, text in texts.items() if text != canonical]
    assert not mismatched, f"omc-retro skill copies differ: {mismatched}"


def test_retro_skill_stays_short_enough_to_scan():
    text = _read(REQUIRED_RETRO_SKILL_PATHS[0])
    non_empty_lines = [line for line in text.splitlines() if line.strip()]
    assert len(non_empty_lines) <= MAX_NON_EMPTY_LINES, (
        f"omc-retro has {len(non_empty_lines)} non-empty lines"
    )


def test_retro_skill_preserves_required_execution_order():
    text = _read(REQUIRED_RETRO_SKILL_PATHS[0])
    cursor = -1
    missing_or_reordered: list[str] = []

    for marker in REQUIRED_SEQUENCE:
        next_pos = text.find(marker, cursor + 1)
        if next_pos == -1:
            missing_or_reordered.append(marker)
        else:
            cursor = next_pos

    assert not missing_or_reordered, f"missing or reordered markers: {missing_or_reordered}"


def test_retro_skill_preserves_required_behavior_markers():
    text = _read(REQUIRED_RETRO_SKILL_PATHS[0])
    missing = [marker for marker in REQUIRED_BEHAVIOR_MARKERS if marker not in text]
    assert not missing, f"missing behavior markers: {missing}"


def test_retro_skill_does_not_document_direct_writes():
    text = _read(REQUIRED_RETRO_SKILL_PATHS[0])
    forbidden = [r"omc_lesson.py add -i", r">>\s*\.omc/notepad\.md"]
    found = [pattern for pattern in forbidden if re.search(pattern, text)]
    assert not found, f"retro should propose writes, not perform them: {found}"


def test_valid_retro_output_fixture_has_required_structure():
    assert _validate_retro_output(VALID_RETRO_SAMPLE) == []


def test_invalid_retro_output_fixture_exposes_weak_retrospective():
    failures = _validate_retro_output(INVALID_RETRO_SAMPLE)
    assert {
        "range",
        "sources",
        "stale",
        "patterns",
        "priorities",
        "lesson",
    }.issubset(set(failures))
