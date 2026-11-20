"""Codex source contracts, independent of any project installation or old state."""
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


def test_codex_agents_template_keeps_no_auto_progress_guard():
    agents = (ROOT / "templates/AGENTS.md").read_text(encoding="utf-8")
    workflow = (ROOT / "docs/omc_explicit_workflow.md").read_text(encoding="utf-8")
    assert "스킬 완료 후 자동 진행 금지" in agents
    assert "다음 스킬로 자동 진입하지 않는다" in agents
    for marker in (
        "#### 예외: 승인된 단일 작업 V0 내부 단계",
        "#### 예외: 이미 제시한 local commit 선택의 단일 승인 상속",
        "| omc-task | 완료 후 자동으로 omc-review/omc-ship 실행 금지 |",
    ):
        assert marker in workflow


def test_omc_workflow_requires_only_applicable_red_checkpoints():
    workflow = (ROOT / "docs/omc_explicit_workflow.md").read_text(encoding="utf-8")
    assert "적용되는 체크포인트를 완료한 뒤 다음 단계로 진행합니다" in workflow
    assert "기존 정책상 RED가 필요한 작업에만 적용" in workflow
    assert "버그 재현과 신규 함수 등 기존 RED 의무" in workflow
    assert "체크박스를 채우지 않으면 다음 단계로 진행하지 않습니다" not in workflow


@pytest.mark.parametrize("base", [".agents", "templates/.agents"])
@pytest.mark.parametrize("skill", ["omc-task", "omc-review"])
def test_codex_skills_keep_manual_progress_boundary(base, skill):
    text = (ROOT / base / "skills" / skill / "SKILL.md").read_text(encoding="utf-8")
    assert "자동으로 진행하지는 않습니다" in text
    assert "일반 대화의 자동 진입 금지는 유지한다" in text
