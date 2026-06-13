"""
omc-office-hours skill contract regression tests.

Office hours is a product gate. Shortening it must preserve the six-question
check, user-owned proceed decision, and handoff discipline before coding.
"""
from __future__ import annotations

import re
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
MAX_NON_EMPTY_LINES = 39

REQUIRED_OFFICE_HOURS_SKILL_PATHS = [
    ROOT / ".agents" / "skills" / "omc-office-hours" / "SKILL.md",
    ROOT / "templates" / ".agents" / "skills" / "omc-office-hours" / "SKILL.md",
    ROOT / "templates" / ".agent" / "skills" / "omc-office-hours" / "SKILL.md",
]
OPTIONAL_OFFICE_HOURS_SKILL_PATHS = [
    ROOT / ".agent" / "skills" / "omc-office-hours" / "SKILL.md",
]

REQUIRED_SEQUENCE = [
    "python3 scripts/omc.py state status --target .",
    "6개 질문",
    "Q1",
    "정확히 누구",
    "Q2",
    "실제 고통",
    "Q3",
    "측정",
    "Q4",
    "MVP",
    "Q5",
    "제외",
    "Q6",
    "10점",
    "PROCEED",
    "RETHINK",
    "HOLD",
]

REQUIRED_BEHAVIOR_MARKERS = [
    "$omc-brainstorm",
    "$omc-ceo-review",
    "$omc-plan",
    "lite",
    "full",
    "6개 답변 완료 전",
    "AI가 임의로 PROCEED",
    "사용자가",
    "자동 진입 금지",
    "모든 LLM 공통 출력 형식",
    "입력 부족 시 중단",
]

REQUIRED_FOCUS_MARKERS = [
    "사용자에게 보여줄 것",
    "시스템이 암묵적으로 처리",
]

VALID_OFFICE_HOURS_SAMPLE = """
Q1. 정확히 누구: 매일 OMC 스킬을 수정하는 1인 개발자
Q2. 실제 고통: 구현 전에 사용자 가치 검증 없이 바로 코딩한다.
Q3. 성공 기준: 스킬 수정 전 6개 답변이 100% 채워지고 RETHINK/HOLD 사유가 기록된다.
Q4. MVP: 1시간 안에 6문답과 판정만 강제한다.
Q5. 제외: 시장 조사 자동화, 디자인 시안, 구현 태스크 생성
Q6. 10점짜리 버전: 과거 세션 데이터를 보고 제품 리스크를 자동 추천한다.
사용자가 완료 시한을 판단: 1시간
판정: PROCEED
"""

INVALID_OFFICE_HOURS_SAMPLE = """
Q1. 누구: 모든 사용자
Q2. 고통: 불편함
Q3. 성공 기준: 더 편하다
판정: PROCEED
"""


def _read(path: Path) -> str:
    assert path.exists(), f"missing office-hours skill path: {path.relative_to(ROOT)}"
    return path.read_text(encoding="utf-8")


def _collect_office_hours_skill_texts(
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


def _validate_office_hours_output(sample: str) -> list[str]:
    failures: list[str] = []
    required_patterns = {
        "specific_user": r"Q1\..*정확히 누구:\s*(?!모든 사용자)\S",
        "pain": r"Q2\..*실제 고통:\s*\S",
        "measurable_success": r"Q3\..*성공 기준:.*\d|100%|%",
        "mvp": r"Q4\..*MVP:\s*\S",
        "excluded_scope": r"Q5\..*제외:\s*\S",
        "ten_out_of_ten": r"Q6\..*10점.*:\s*\S",
        "user_decision": r"사용자가.*판단:\s*\S",
        "verdict": r"판정:\s*(PROCEED|RETHINK|HOLD)",
    }
    failures.extend(
        name
        for name, pattern in required_patterns.items()
        if not re.search(pattern, sample)
    )
    return failures


def test_office_hours_skill_paths_are_identical():
    texts = _collect_office_hours_skill_texts(
        root=ROOT,
        required_paths=REQUIRED_OFFICE_HOURS_SKILL_PATHS,
        optional_paths=OPTIONAL_OFFICE_HOURS_SKILL_PATHS,
    )
    canonical = texts[".agents/skills/omc-office-hours/SKILL.md"]
    mismatched = [name for name, text in texts.items() if text != canonical]
    assert not mismatched, f"omc-office-hours skill copies differ: {mismatched}"


def test_ignored_live_agent_office_hours_path_is_optional(tmp_path: Path):
    canonical = tmp_path / ".agents" / "skills" / "omc-office-hours" / "SKILL.md"
    template_codex = tmp_path / "templates" / ".agents" / "skills" / "omc-office-hours" / "SKILL.md"
    template_agent = tmp_path / "templates" / ".agent" / "skills" / "omc-office-hours" / "SKILL.md"
    ignored_live_agent = tmp_path / ".agent" / "skills" / "omc-office-hours" / "SKILL.md"

    for path in (canonical, template_codex, template_agent):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("same", encoding="utf-8")

    texts = _collect_office_hours_skill_texts(
        root=tmp_path,
        required_paths=(canonical, template_codex, template_agent),
        optional_paths=(ignored_live_agent,),
    )
    assert texts == {
        ".agents/skills/omc-office-hours/SKILL.md": "same",
        "templates/.agents/skills/omc-office-hours/SKILL.md": "same",
        "templates/.agent/skills/omc-office-hours/SKILL.md": "same",
    }


def test_office_hours_skill_stays_short_enough_to_scan():
    text = _read(REQUIRED_OFFICE_HOURS_SKILL_PATHS[0])
    non_empty_lines = [line for line in text.splitlines() if line.strip()]
    assert len(non_empty_lines) <= MAX_NON_EMPTY_LINES, (
        f"omc-office-hours has {len(non_empty_lines)} non-empty lines"
    )


def test_office_hours_skill_preserves_required_execution_order():
    text = _read(REQUIRED_OFFICE_HOURS_SKILL_PATHS[0])
    cursor = -1
    missing_or_reordered: list[str] = []

    for marker in REQUIRED_SEQUENCE:
        next_pos = text.find(marker, cursor + 1)
        if next_pos == -1:
            missing_or_reordered.append(marker)
        else:
            cursor = next_pos

    assert not missing_or_reordered, f"missing or reordered markers: {missing_or_reordered}"


def test_office_hours_skill_preserves_required_behavior_markers():
    text = _read(REQUIRED_OFFICE_HOURS_SKILL_PATHS[0])
    missing = [marker for marker in REQUIRED_BEHAVIOR_MARKERS if marker not in text]
    assert not missing, f"missing behavior markers: {missing}"


def test_office_hours_skill_explains_visible_vs_implicit_work():
    text = _read(REQUIRED_OFFICE_HOURS_SKILL_PATHS[0])
    missing = [marker for marker in REQUIRED_FOCUS_MARKERS if marker not in text]
    assert not missing, f"missing focus markers: {missing}"


def test_valid_office_hours_output_fixture_has_required_structure():
    assert _validate_office_hours_output(VALID_OFFICE_HOURS_SAMPLE) == []


def test_invalid_office_hours_output_fixture_exposes_weak_product_gate():
    failures = _validate_office_hours_output(INVALID_OFFICE_HOURS_SAMPLE)
    assert {"specific_user", "measurable_success", "mvp", "excluded_scope"}.issubset(
        set(failures)
    )


def test_office_hours_skill_declares_risk_based_full_triggers():
    text = _read(REQUIRED_OFFICE_HOURS_SKILL_PATHS[0])
    required = [
        "권한",
        "돈",
        "운영",
        "정책",
        "범위 불명확",
        "full",
    ]
    missing = [marker for marker in required if marker not in text]
    assert not missing, f"missing full trigger markers: {missing}"


def test_office_hours_skill_keeps_q3_q4_q5_in_lite_mode():
    text = _read(REQUIRED_OFFICE_HOURS_SKILL_PATHS[0])
    required = [
        "lite",
        "Q3",
        "Q4",
        "Q5",
    ]
    missing = [marker for marker in required if marker not in text]
    assert not missing, f"missing lite safety markers: {missing}"


def test_office_hours_skill_only_allows_q6_to_be_omitted_in_lite_mode():
    text = _read(REQUIRED_OFFICE_HOURS_SKILL_PATHS[0])
    assert "Q6" in text and "생략" in text, "lite mode must explain Q6 omission"


def test_office_hours_skill_declares_lite_to_full_escalation_rule():
    text = _read(REQUIRED_OFFICE_HOURS_SKILL_PATHS[0])
    required = [
        "lite",
        "full 재질문",
        "Q1",
        "Q2",
        "Q3",
        "Q4",
        "Q5",
    ]
    missing = [marker for marker in required if marker not in text]
    assert not missing, f"missing lite->full escalation markers: {missing}"
