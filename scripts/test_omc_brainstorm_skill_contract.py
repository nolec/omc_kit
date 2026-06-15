"""
omc-brainstorm skill contract regression tests.

Brainstorm is an interaction skill. Shortening it must preserve sequential
Socratic questioning, convergence criteria, and the no-code-before-decision gate.
"""
from __future__ import annotations

import re
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
MAX_NON_EMPTY_LINES = 44

REQUIRED_BRAINSTORM_SKILL_PATHS = [
    ROOT / ".agents" / "skills" / "omc-brainstorm" / "SKILL.md",
    ROOT / "templates" / ".agents" / "skills" / "omc-brainstorm" / "SKILL.md",
    ROOT / "templates" / ".agent" / "skills" / "omc-brainstorm" / "SKILL.md",
]
OPTIONAL_BRAINSTORM_SKILL_PATHS = [
    ROOT / ".agent" / "skills" / "omc-brainstorm" / "SKILL.md",
]

REQUIRED_SEQUENCE = [
    "python3 scripts/omc.py state status --target .",
    "Phase 4 전 코드 작성 금지",
    "What",
    "현상",
    "Why",
    "원인 가설",
    "How",
    "해결 옵션",
    "Decide",
    "핵심 문제",
    "옵션 A",
    "옵션 B",
    "권장 옵션",
    "사용자 확인",
]

REQUIRED_BEHAVIOR_MARKERS = [
    "순서대로 하나씩",
    "사용자가 답한 후 다음 질문",
    "현상 한 문장",
    "원인 가설 1개",
    "해결 옵션 2개",
    "주추천 1개",
    "구현 안 함",
    "보류",
    "$omc-office-hours",
    "$omc-plan",
    "사용자 확인 완료",
    "자동 진입 금지",
    "모든 LLM 공통 출력 형식",
    "입력 부족 시 중단",
]

REQUIRED_FOCUS_MARKERS = [
    "사용자에게 보여줄 것",
    "시스템이 암묵적으로 처리",
]

VALID_BRAINSTORM_SAMPLE = """
핵심 문제: 스킬 수정 전에 요구사항이 섞여 구현 범위가 흔들린다.
현상 한 문장: 계획 없이 여러 스킬을 동시에 고치려 한다.
원인 가설 1개: 스킬별 역할 경계가 문서에 길게 흩어져 있다.
해결 옵션 2개:
  옵션 A: 한 스킬씩 계약 테스트로 줄인다.
  옵션 B: 모든 제품 스킬을 한 번에 재설계한다.
권장 옵션: 옵션 A
사용자 확인: 완료
다음 액션: $omc-office-hours
"""

INVALID_BRAINSTORM_SAMPLE = """
핵심 문제: 뭔가 모호하다.
권장 옵션: 옵션 A
"""


def _read(path: Path) -> str:
    assert path.exists(), f"missing brainstorm skill path: {path.relative_to(ROOT)}"
    return path.read_text(encoding="utf-8")


def _collect_brainstorm_skill_texts(
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


def _validate_brainstorm_output(sample: str) -> list[str]:
    required_patterns = {
        "problem": r"핵심 문제:\s*\S",
        "phenomenon": r"현상 한 문장:\s*\S",
        "cause": r"원인 가설 1개:\s*\S",
        "options": r"해결 옵션 2개:.*옵션 A:.*옵션 B:",
        "recommendation": r"권장 옵션:\s*\S",
        "confirmed": r"사용자 확인:\s*완료",
        "next_action": r"다음 액션:\s*\$omc-(office-hours|plan)",
    }
    return [
        name
        for name, pattern in required_patterns.items()
        if not re.search(pattern, sample, re.S)
    ]


def test_brainstorm_skill_paths_are_identical():
    texts = _collect_brainstorm_skill_texts(
        root=ROOT,
        required_paths=REQUIRED_BRAINSTORM_SKILL_PATHS,
        optional_paths=OPTIONAL_BRAINSTORM_SKILL_PATHS,
    )
    canonical = texts[".agents/skills/omc-brainstorm/SKILL.md"]
    mismatched = [name for name, text in texts.items() if text != canonical]
    assert not mismatched, f"omc-brainstorm skill copies differ: {mismatched}"


def test_ignored_live_agent_brainstorm_path_is_optional(tmp_path: Path):
    canonical = tmp_path / ".agents" / "skills" / "omc-brainstorm" / "SKILL.md"
    template_codex = tmp_path / "templates" / ".agents" / "skills" / "omc-brainstorm" / "SKILL.md"
    template_agent = tmp_path / "templates" / ".agent" / "skills" / "omc-brainstorm" / "SKILL.md"
    ignored_live_agent = tmp_path / ".agent" / "skills" / "omc-brainstorm" / "SKILL.md"

    for path in (canonical, template_codex, template_agent):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("same", encoding="utf-8")

    texts = _collect_brainstorm_skill_texts(
        root=tmp_path,
        required_paths=(canonical, template_codex, template_agent),
        optional_paths=(ignored_live_agent,),
    )
    assert texts == {
        ".agents/skills/omc-brainstorm/SKILL.md": "same",
        "templates/.agents/skills/omc-brainstorm/SKILL.md": "same",
        "templates/.agent/skills/omc-brainstorm/SKILL.md": "same",
    }


def test_brainstorm_skill_stays_short_enough_to_scan():
    text = _read(REQUIRED_BRAINSTORM_SKILL_PATHS[0])
    non_empty_lines = [line for line in text.splitlines() if line.strip()]
    assert len(non_empty_lines) <= MAX_NON_EMPTY_LINES, (
        f"omc-brainstorm has {len(non_empty_lines)} non-empty lines"
    )


def test_brainstorm_skill_preserves_required_execution_order():
    text = _read(REQUIRED_BRAINSTORM_SKILL_PATHS[0])
    cursor = -1
    missing_or_reordered: list[str] = []

    for marker in REQUIRED_SEQUENCE:
        next_pos = text.find(marker, cursor + 1)
        if next_pos == -1:
            missing_or_reordered.append(marker)
        else:
            cursor = next_pos

    assert not missing_or_reordered, f"missing or reordered markers: {missing_or_reordered}"


def test_brainstorm_skill_preserves_required_behavior_markers():
    text = _read(REQUIRED_BRAINSTORM_SKILL_PATHS[0])
    missing = [marker for marker in REQUIRED_BEHAVIOR_MARKERS if marker not in text]
    assert not missing, f"missing behavior markers: {missing}"


def test_brainstorm_skill_explains_visible_vs_implicit_work():
    text = _read(REQUIRED_BRAINSTORM_SKILL_PATHS[0])
    missing = [marker for marker in REQUIRED_FOCUS_MARKERS if marker not in text]
    assert not missing, f"missing focus markers: {missing}"


def test_valid_brainstorm_output_fixture_has_required_structure():
    assert _validate_brainstorm_output(VALID_BRAINSTORM_SAMPLE) == []


def test_invalid_brainstorm_output_fixture_exposes_weak_convergence():
    failures = _validate_brainstorm_output(INVALID_BRAINSTORM_SAMPLE)
    assert {"phenomenon", "cause", "options", "confirmed", "next_action"}.issubset(
        set(failures)
    )
