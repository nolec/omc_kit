"""
Regression tests for OMC skill optimization guidance notes.

The notes are intentionally lightweight, but they must keep the comparison
criteria explicit, explain when to stop optimizing, and preserve the ranked
next candidates for later re-entry.
"""
from __future__ import annotations

import re
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
DOC_PATH = ROOT / "docs" / "omc_next_skill_candidates.md"
STOP_RULES_PATH = ROOT / "docs" / "omc_skill_optimization_stop_rules.md"

REQUIRED_MARKERS = [
    "비교 대상",
    "후보 1",
    "후보 2",
    "사용 빈도",
    "현재 길이",
    "반복 설명량",
    "안전 리스크",
    "기준 상태",
    ".omc/lessons",
    "지금 판단",
    "다음 액션",
]

STOP_RULES_MARKERS = [
    "OMC Skill Optimization Stop Rules",
    "지금 멈춰야 하는 신호",
    "다시 시작해도 되는 신호",
    "반복 재현",
    "REVISE",
    "토큰",
]


def _read_doc() -> str:
    assert DOC_PATH.exists(), f"missing comparison note: {DOC_PATH.relative_to(ROOT)}"
    return DOC_PATH.read_text(encoding="utf-8")


def test_next_skill_candidates_doc_has_required_markers():
    text = _read_doc()
    missing = [marker for marker in REQUIRED_MARKERS if marker not in text]
    assert not missing, f"missing comparison markers: {missing}"


def test_next_skill_candidates_doc_lists_two_ranked_candidates():
    text = _read_doc()
    matches = re.findall(r"후보 [12]:\s*(omc-[a-z-]+)", text)
    assert matches == ["omc-critique", "omc-retro"], (
        f"expected ranked candidates omc-critique then omc-retro, got: {matches}"
    )


def test_next_skill_candidates_doc_explains_why_optimization_stops_now():
    text = _read_doc()
    assert "지금은 바로 진행하지 않는 이유" in text, (
        "comparison note must explain why optimization stops for now"
    )


def test_stop_rules_doc_has_required_markers():
    assert STOP_RULES_PATH.exists(), f"missing stop rules note: {STOP_RULES_PATH.relative_to(ROOT)}"
    text = STOP_RULES_PATH.read_text(encoding="utf-8")
    missing = [marker for marker in STOP_RULES_MARKERS if marker not in text]
    assert not missing, f"missing stop-rules markers: {missing}"
