"""
omc-ceo-review skill contract regression tests.

CEO review is a scope decision gate. Shortening it must preserve mode selection,
evidence requirements, and user-owned business judgment.
"""
from __future__ import annotations

import re
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
MAX_NON_EMPTY_LINES = 42

REQUIRED_CEO_REVIEW_SKILL_PATHS = [
    ROOT / ".agents" / "skills" / "omc-ceo-review" / "SKILL.md",
    ROOT / "templates" / ".agents" / "skills" / "omc-ceo-review" / "SKILL.md",
    ROOT / "templates" / ".agent" / "skills" / "omc-ceo-review" / "SKILL.md",
]
OPTIONAL_CEO_REVIEW_SKILL_PATHS = [
    ROOT / ".agent" / "skills" / "omc-ceo-review" / "SKILL.md",
]

REQUIRED_SEQUENCE = [
    "python3 scripts/omc.py state status --target .",
    "모드",
    "EXPAND",
    "SELECTIVE",
    "HOLD",
    "REDUCE",
    "공통 5개",
    "이탈",
    "핵심 가치",
    "80%",
    "성공 지표",
    "시한",
    "선택 모드 질문 2개",
    "결론",
    "APPROVED",
    "REDUCE",
    "REJECT",
]

REQUIRED_BEHAVIOR_MARKERS = [
    "모드와 결론",
    "자동 HOLD 금지",
    "사용자 선택",
    "예/아니오",
    "수치",
    "근거",
    "근거 없는 결론 금지",
    "사용자 판단",
    "$omc-plan",
    "보류 사유",
    ".omc/notepad.md",
    "자동 진입 금지",
    "모든 LLM 공통 출력 형식",
    "입력 부족 시 중단",
]

REQUIRED_RECOMMENDATION_MARKERS = [
    "APPROVED",
    "주추천 1개",
    "$omc-plan",
    "REDUCE",
    "다시 실행",
    "REJECT",
    ".omc/notepad.md",
    "자동으로 진행하지는 않습니다.",
]

REQUIRED_FOCUS_MARKERS = [
    "사용자에게 보여줄 것",
    "시스템이 암묵적으로 처리",
]

VALID_CEO_REVIEW_SAMPLE = """
모드: REDUCE
공통 5개:
1. 이탈: 12명 중 5명이 해당 기능 부재를 이탈 이유로 말했다.
2. 핵심 가치: 핵심 워크플로 완료 시간을 30% 줄인다.
3. 80% 단순 버전: 자동화 없이 체크리스트만 제공한다.
4. 성공 지표: 7일 내 설정 완료율 60%
5. 시한/위험: 사용자가 2일 내 가능하다고 판단했다.
선택 모드 질문 2개:
R1. 포기하는 것: 자동 추천
R2. 측정 가능: 설정 완료율로 가능
사용자 판단: 완료
결론: APPROVED
이유: 근거 있는 수치가 있고 축소 버전으로도 측정 가능하다.
"""

INVALID_CEO_REVIEW_SAMPLE = """
모드: HOLD
공통 5개:
1. 이탈: 예
결론: APPROVED
"""


def _read(path: Path) -> str:
    assert path.exists(), f"missing ceo-review skill path: {path.relative_to(ROOT)}"
    return path.read_text(encoding="utf-8")


def _collect_ceo_review_skill_texts(
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


def _validate_ceo_review_output(sample: str) -> list[str]:
    failures: list[str] = []
    required_patterns = {
        "mode": r"모드:\s*(EXPAND|SELECTIVE|HOLD|REDUCE)",
        "common_five": r"공통 5개:",
        "churn_evidence": r"이탈:.*\d",
        "core_value_evidence": r"핵심 가치:.*\d|%",
        "simple_version": r"80%.*:",
        "success_metric": r"성공 지표:.*\d|%",
        "timeline_or_risk": r"시한/위험:.*사용자.*판단",
        "mode_questions": r"선택 모드 질문 2개:",
        "user_judgment": r"사용자 판단:\s*완료",
        "verdict": r"결론:\s*(APPROVED|REDUCE|REJECT)",
        "reason": r"이유:\s*\S",
    }
    failures.extend(
        name
        for name, pattern in required_patterns.items()
        if not re.search(pattern, sample)
    )
    if re.search(r":\s*(예|아니오)\s*$", sample, re.M):
        failures.append("yes_no_only")
    return failures


def test_ceo_review_skill_paths_are_identical():
    texts = _collect_ceo_review_skill_texts(
        root=ROOT,
        required_paths=REQUIRED_CEO_REVIEW_SKILL_PATHS,
        optional_paths=OPTIONAL_CEO_REVIEW_SKILL_PATHS,
    )
    canonical = texts[".agents/skills/omc-ceo-review/SKILL.md"]
    mismatched = [name for name, text in texts.items() if text != canonical]
    assert not mismatched, f"omc-ceo-review skill copies differ: {mismatched}"


def test_ignored_live_agent_ceo_review_path_is_optional(tmp_path: Path):
    canonical = tmp_path / ".agents" / "skills" / "omc-ceo-review" / "SKILL.md"
    template_codex = tmp_path / "templates" / ".agents" / "skills" / "omc-ceo-review" / "SKILL.md"
    template_agent = tmp_path / "templates" / ".agent" / "skills" / "omc-ceo-review" / "SKILL.md"
    ignored_live_agent = tmp_path / ".agent" / "skills" / "omc-ceo-review" / "SKILL.md"

    for path in (canonical, template_codex, template_agent):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("same", encoding="utf-8")

    texts = _collect_ceo_review_skill_texts(
        root=tmp_path,
        required_paths=(canonical, template_codex, template_agent),
        optional_paths=(ignored_live_agent,),
    )
    assert texts == {
        ".agents/skills/omc-ceo-review/SKILL.md": "same",
        "templates/.agents/skills/omc-ceo-review/SKILL.md": "same",
        "templates/.agent/skills/omc-ceo-review/SKILL.md": "same",
    }


def test_ceo_review_skill_stays_short_enough_to_scan():
    text = _read(REQUIRED_CEO_REVIEW_SKILL_PATHS[0])
    non_empty_lines = [line for line in text.splitlines() if line.strip()]
    assert len(non_empty_lines) <= MAX_NON_EMPTY_LINES, (
        f"omc-ceo-review has {len(non_empty_lines)} non-empty lines"
    )


def test_ceo_review_skill_preserves_required_execution_order():
    text = _read(REQUIRED_CEO_REVIEW_SKILL_PATHS[0])
    cursor = -1
    missing_or_reordered: list[str] = []

    for marker in REQUIRED_SEQUENCE:
        next_pos = text.find(marker, cursor + 1)
        if next_pos == -1:
            missing_or_reordered.append(marker)
        else:
            cursor = next_pos

    assert not missing_or_reordered, f"missing or reordered markers: {missing_or_reordered}"


def test_ceo_review_skill_preserves_required_behavior_markers():
    text = _read(REQUIRED_CEO_REVIEW_SKILL_PATHS[0])
    missing = [marker for marker in REQUIRED_BEHAVIOR_MARKERS if marker not in text]
    assert not missing, f"missing behavior markers: {missing}"


def test_ceo_review_skill_explains_visible_vs_implicit_work():
    text = _read(REQUIRED_CEO_REVIEW_SKILL_PATHS[0])
    missing = [marker for marker in REQUIRED_FOCUS_MARKERS if marker not in text]
    assert not missing, f"missing focus markers: {missing}"


def test_ceo_review_skill_preserves_next_step_recommendation_rules():
    text = _read(REQUIRED_CEO_REVIEW_SKILL_PATHS[0])
    missing = [marker for marker in REQUIRED_RECOMMENDATION_MARKERS if marker not in text]
    assert not missing, f"missing recommendation markers: {missing}"


def test_valid_ceo_review_output_fixture_has_required_structure():
    assert _validate_ceo_review_output(VALID_CEO_REVIEW_SAMPLE) == []


def test_invalid_ceo_review_output_fixture_exposes_weak_scope_gate():
    failures = _validate_ceo_review_output(INVALID_CEO_REVIEW_SAMPLE)
    assert {"churn_evidence", "core_value_evidence", "user_judgment"}.issubset(
        set(failures)
    )
    assert "yes_no_only" in failures
