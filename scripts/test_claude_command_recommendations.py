from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
TEMPLATES = ROOT / "templates"
CLAUDE_REVIEW = TEMPLATES / ".claude" / "commands" / "review.md"
GEMINI_COMMANDS = TEMPLATES / ".gemini" / "commands" / "omc-commands.md"
CODEX_COMMANDS = TEMPLATES / ".codex" / "commands" / "omc-commands.md"

COMMAND_FILES = [
    "benchmark",
    "brainstorm",
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


def test_template_claude_commands_include_recommendation_contract() -> None:
    for name in COMMAND_FILES:
        _assert_recommendation_contract(TEMPLATES / ".claude" / "commands" / f"{name}.md")


def test_claude_review_recommendation_matches_skill_intent() -> None:
    full_text = _read(CLAUDE_REVIEW)
    review_section = _slice_between(full_text, "# /review", "$ARGUMENTS가 있으면 해당 파일/범위만 리뷰합니다.")
    text = _slice_between(full_text, "## 다음 추천")
    assert "판정" in review_section or "VERDICT" in review_section
    assert "APPROVE/APPROVE WITH NOTES + 배포 준비" in text
    assert "그 외" in text
    assert "판정만으로 리뷰를 끝내지 않습니다." in text


def test_gemini_review_documents_single_primary_recommendation() -> None:
    text = _slice_between(_read(GEMINI_COMMANDS), "## /review", "## /investigate")
    assert "주추천 1개" in text
    assert "자동으로 진행하지는 않습니다." in text
    assert "APPROVE/APPROVE WITH NOTES + 배포 준비" in text


def test_codex_review_documents_single_primary_recommendation() -> None:
    text = _slice_between(_read(CODEX_COMMANDS), "### `$omc-review`", "### `$omc-investigate")
    assert "주추천 1개" in text
    assert "자동으로 진행하지는 않습니다." in text
    assert "APPROVE/APPROVE WITH NOTES + 배포 준비" in text


def test_gemini_critique_keeps_change_cost_checkpoint() -> None:
    text = _slice_between(_read(GEMINI_COMMANDS), "### `/critique [계획/코드]`")
    assert "변경 비용 추정" in text
    assert "실질 효과 LOW + MINOR만" in text
    assert "실질 효과 MED/HIGH" in text
