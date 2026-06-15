from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent


def test_quickstart_keeps_overview_and_guard_section() -> None:
    text = (ROOT / "docs" / "omc_quickstart.md").read_text(encoding="utf-8")
    assert "python3 scripts/omc_autopilot.py overview" in text
    assert "## 자동 가드 & CI" in text
    assert "python3 scripts/omc_tdd_check.py --staged" in text


def test_cursor_pipeline_hook_matches_template() -> None:
    deployed = (ROOT / ".cursor" / "hooks" / "omc-pipeline-check.sh").read_text(encoding="utf-8")
    template = (ROOT / "templates" / ".cursor" / "hooks" / "omc-pipeline-check.sh").read_text(encoding="utf-8")
    assert deployed == template
