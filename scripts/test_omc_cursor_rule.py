from pathlib import Path


def test_omc_always_rule_keeps_no_auto_progress_guard():
    rule_path = Path(".cursor/rules/omc-always.md")
    text = rule_path.read_text(encoding="utf-8")
    assert "스킬 완료 후 자동 진행 금지" in text
    assert "다음 스킬로 자동 진입하지 않는다" in text
    assert "| 완료된 스킬 | 금지 동작 |" in text
    assert "| omc-task | 완료 후 자동으로 omc-review/omc-ship 실행 금지 |" in text
    assert "**올바른 동작:**" in text
    assert "**금지 동작:**" in text
