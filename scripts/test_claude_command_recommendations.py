from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent


def test_codex_review_documents_single_primary_recommendation() -> None:
    skill = ROOT / "templates/.agents/skills/omc-review/SKILL.md"
    text = skill.read_text(encoding="utf-8")
    assert "주추천 1개" in text
    assert "자동으로 진행하지는 않습니다." in text
    assert "배포 준비 명시 + ship_intent_explicit=yes면 `$omc-ship`" in text
