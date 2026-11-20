from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
TEMPLATES = ROOT / "templates"
CLAUDE_REVIEW = TEMPLATES / ".claude" / "commands" / "review.md"
GEMINI_COMMANDS = TEMPLATES / ".gemini" / "commands" / "omc-commands.md"
CODEX_COMMANDS = TEMPLATES / ".codex" / "commands" / "omc-commands.md"

COMMAND_FILES = [
    "benchmark",
    "ceo-review",
    "critique",
    "investigate",
    "office-hours",
    "plan",
    "review",
    "ship",
    "status",
    "task",
]

REQUIRED_MARKERS = [
    "다음 추천",
    "주추천 1개",
    "자동으로 진행하지는 않습니다.",
]


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def _assert_recommendation_contract(path: Path) -> None:
    text = _read(path)
    for marker in REQUIRED_MARKERS:
        assert marker in text, f"{path} missing marker: {marker}"


def _slice_between(text: str, start: str, end: str | None = None) -> str:
    start_idx = text.index(start)
    if end is None:
        return text[start_idx:]
    end_idx = text.index(end, start_idx)
    return text[start_idx:end_idx]








def test_codex_review_documents_single_primary_recommendation() -> None:
    text = _slice_between(_read(CODEX_COMMANDS), "### `$omc-review`", "### `$omc-investigate")
    assert "주추천 1개" in text
    assert "자동으로 진행하지는 않습니다." in text
    assert "APPROVE/APPROVE WITH NOTES + 배포 준비" in text
