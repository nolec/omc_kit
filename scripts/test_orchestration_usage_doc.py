from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
DOC = ROOT / "docs" / "orchestration_usage.md"
AGENTS_TEMPLATE = ROOT / "templates" / "AGENTS.md"


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




def test_agents_template_points_to_installed_workflow_for_explicit_orchestration() -> None:
    agents = AGENTS_TEMPLATE.read_text(encoding="utf-8")
    workflow = (ROOT / "docs/omc_explicit_workflow.md").read_text(encoding="utf-8")
    assert "OMC 오케스트레이션을 명시적으로 요청했다면" in agents
    assert "docs/omc_explicit_workflow.md#명시적으로-선택한-omc-작업" in agents
    explicit_ops = workflow.split("## 명시적으로 선택한 OMC 작업", 1)[1]
    assert "### 오케스트레이션 사용 원칙" in explicit_ops
    assert "(omc_quickstart.md)" in explicit_ops


def test_orchestration_usage_doc_keeps_codex_input_explicit() -> None:
    text = DOC.read_text(encoding="utf-8")
    assert "| Codex | `$omc-plan`, `$omc-task`, `$omc-review` | 자연어 키워드 요청 |" in text
    assert "| Claude Code |" not in text


def test_orchestration_usage_doc_uses_single_supported_input_style() -> None:
    text = DOC.read_text(encoding="utf-8")
    assert "command entry" not in text
    assert "| Gemini CLI |" not in text
    assert "`python3 scripts/omc.py ...`" in text


def test_orchestration_usage_doc_separates_big_plan_from_small_autopilot_units() -> None:
    text = DOC.read_text(encoding="utf-8")

    required_markers = [
        "autopilot 작업 단위 기준",
        "spec이 고정된 한 태스크씩만",
        "task file 기준",
        "autopilot은 실행 보조용이지",
    ]
    missing = [marker for marker in required_markers if marker not in text]
    assert not missing, f"missing autopilot scope markers: {missing}"
