"""Codex source contracts, independent of any project installation or old state."""
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


def test_codex_agents_template_keeps_no_auto_progress_guard():
    text = (ROOT / "templates/AGENTS.md").read_text(encoding="utf-8")
    for marker in (
        "스킬 완료 후 자동 진행 금지",
        "다음 스킬로 자동 진입하지 않는다",
        "| 완료된 스킬 | 금지 동작 |",
        "| omc-task | 완료 후 자동으로 omc-review/omc-ship 실행 금지 |",
        "**올바른 동작:**",
        "**금지 동작:**",
    ):
        assert marker in text


@pytest.mark.parametrize("base", [".agents", "templates/.agents"])
@pytest.mark.parametrize("skill", ["omc-task", "omc-review"])
def test_codex_skills_keep_manual_progress_boundary(base, skill):
    text = (ROOT / base / "skills" / skill / "SKILL.md").read_text(encoding="utf-8")
    assert "자동으로 진행하지는 않습니다" in text
    assert "일반 대화의 자동 진입 금지는 유지한다" in text
