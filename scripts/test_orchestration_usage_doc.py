from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
DOC = ROOT / "docs" / "orchestration_usage.md"
AGENTS = ROOT / "AGENTS.md"


def test_orchestration_usage_doc_exists_with_required_sections() -> None:
    assert DOC.exists(), "docs/orchestration_usage.md must exist"

    text = DOC.read_text(encoding="utf-8")

    required_markers = [
        "OMC Orchestration Usage",
        "기본 원칙",
        "입력 선정 기준",
        "기본 오케스트레이션",
        "확장 오케스트레이션",
        "플랫폼별 사용 모델",
        "시작-멈춤-다음 추천",
    ]
    missing = [marker for marker in required_markers if marker not in text]
    assert not missing, f"missing orchestration usage sections: {missing}"


def test_agents_references_orchestration_usage_doc() -> None:
    text = AGENTS.read_text(encoding="utf-8")

    required_markers = [
        "오케스트레이션 사용 원칙",
        "docs/orchestration_usage.md",
        "Codex",
        "Claude",
        "Gemini",
    ]
    missing = [marker for marker in required_markers if marker not in text]
    assert not missing, f"missing AGENTS orchestration markers: {missing}"


def test_orchestration_usage_doc_keeps_claude_input_conservative() -> None:
    text = DOC.read_text(encoding="utf-8")

    assert "| Claude Code | `/plan`, `/task`, `/review` | 자연어 요청 |" not in text
    assert "Claude Code | `/plan`, `/task`, `/review` |" in text


def test_orchestration_usage_doc_uses_single_gemini_input_style() -> None:
    text = DOC.read_text(encoding="utf-8")

    assert "command entry" not in text
    assert "| Gemini CLI | `/plan`, `/task`, `/review` |" in text
