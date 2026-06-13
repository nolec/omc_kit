"""
Regression tests for the next OMC skill optimization comparison note.

The note is intentionally lightweight, but it must keep the comparison
criteria explicit and recommend exactly one next target.
"""
from __future__ import annotations

import re
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
DOC_PATH = ROOT / "docs" / "omc_next_skill_candidates.md"

REQUIRED_MARKERS = [
    "비교 대상",
    "omc-status",
    "omc-critique",
    "사용 빈도",
    "현재 길이",
    "반복 설명량",
    "안전 리스크",
    "기준 상태",
    ".omc/lessons",
    "추천 후보",
    "다음 액션",
]


def _read_doc() -> str:
    assert DOC_PATH.exists(), f"missing comparison note: {DOC_PATH.relative_to(ROOT)}"
    return DOC_PATH.read_text(encoding="utf-8")


def test_next_skill_candidates_doc_has_required_markers():
    text = _read_doc()
    missing = [marker for marker in REQUIRED_MARKERS if marker not in text]
    assert not missing, f"missing comparison markers: {missing}"


def test_next_skill_candidates_doc_recommends_exactly_one_candidate():
    text = _read_doc()
    matches = re.findall(r"추천 후보:\s*(omc-status|omc-critique)", text)
    assert len(matches) == 1, f"expected exactly one recommended candidate, got: {matches}"


def test_next_skill_candidates_doc_explains_why_other_candidate_waits():
    text = _read_doc()
    assert "보류 이유" in text, "comparison note must explain why the other candidate waits"
