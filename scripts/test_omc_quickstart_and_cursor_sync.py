from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent


def test_quickstart_keeps_overview_and_guard_section() -> None:
    text = (ROOT / "docs" / "omc_quickstart.md").read_text(encoding="utf-8")
    assert "python3 scripts/omc_autopilot.py overview" in text
    assert "## 명시적 검증 & CI" in text
    assert "python3 scripts/omc_tdd_check.py --staged" in text


def test_installed_quickstart_matches_original(installed_omc_target) -> None:
    relative = Path("docs/omc_quickstart.md")
    assert (installed_omc_target / relative).read_bytes() == (ROOT / relative).read_bytes()
