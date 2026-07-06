from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
DOC = ROOT / "docs" / "fugu_benchmark.md"


def test_fugu_benchmark_doc_exists_with_required_sections() -> None:
    assert DOC.exists(), "docs/fugu_benchmark.md must exist"

    text = DOC.read_text(encoding="utf-8")

    required_markers = [
        "Fugu Benchmark",
        "현재 OMC 수준",
        "부분 반영",
        "채택한 항목",
        "아직 미채택 항목",
        "도입 가치",
        "Codex / Claude / Gemini 적용",
        "1단계 범위",
    ]
    missing = [marker for marker in required_markers if marker not in text]
    assert not missing, f"missing fugu benchmark sections: {missing}"


def test_fugu_benchmark_doc_mentions_rule_based_gap_and_next_focus() -> None:
    text = DOC.read_text(encoding="utf-8")

    required_markers = [
        "rule-based",
        "learned orchestrator",
        "response mode",
        "reroute",
        "task start delay",
        "single-entry",
        "오케스트레이션 품질",
    ]
    missing = [marker for marker in required_markers if marker not in text]
    assert not missing, f"missing fugu benchmark concepts: {missing}"


def test_fugu_benchmark_doc_includes_evidence_and_confidence_mapping() -> None:
    text = DOC.read_text(encoding="utf-8")

    required_markers = [
        "## 반영 검증 기준",
        "로드맵 항목",
        "실제 반영 증거",
        "Fugu 비교 축",
        "신뢰도",
        "문서만 있으면 `부분 반영`",
        "테스트/benchmark/autopilot 상태까지 있으면 `반영 확인`",
        "비교 판단은 `현재 상태 참조`와 `반영 검증 완료`를 구분한다.",
    ]
    missing = [marker for marker in required_markers if marker not in text]
    assert not missing, f"missing fugu benchmark evidence mapping: {missing}"
