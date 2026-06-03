"""
omc-task skill contract regression tests.

The task skill is intentionally short, but it must still preserve the
execution gates that keep OMC from becoming a loose prompt template.
"""
from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
MAX_NON_EMPTY_LINES = 75

REQUIRED_TASK_SKILL_PATHS = [
    ROOT / ".agents" / "skills" / "omc-task" / "SKILL.md",
    ROOT / "templates" / ".agents" / "skills" / "omc-task" / "SKILL.md",
    ROOT / "templates" / ".agent" / "skills" / "omc-task" / "SKILL.md",
]
OPTIONAL_TASK_SKILL_PATHS = [
    ROOT / ".agent" / "skills" / "omc-task" / "SKILL.md",
]

REQUIRED_SEQUENCE = [
    "python3 scripts/omc_guard.py require --for \"task\" --target .",
    "필수 체크",
    "CONTRACT 등록",
    "RED 등록",
    "TDD 게이트",
    "PHASE 1",
    "CONTRACT",
    "PHASE 2",
    "DESIGN",
    "PHASE 3",
    "RED",
    "python3 scripts/omc_pipeline_guard.py red-done <테스트파일>",
    "PHASE 4",
    "GREEN",
    "PHASE 5",
    "REFACTOR",
    "PHASE 6",
    "python3 scripts/omc_tdd_check.py --staged",
    "PHASE 7",
    "COMPOUND ENGINEERING",
    "$omc-review",
]

REQUIRED_FOCUS_MARKERS = [
    "사용자에게 보여줄 단계",
    "시스템이 암묵적으로 처리",
]


def _read(path: Path) -> str:
    assert path.exists(), f"missing task skill path: {path.relative_to(ROOT)}"
    return path.read_text(encoding="utf-8")


def _collect_task_skill_texts(
    *,
    root: Path,
    required_paths: tuple[Path, ...] | list[Path],
    optional_paths: tuple[Path, ...] | list[Path],
) -> dict[str, str]:
    texts = {path.relative_to(root).as_posix(): _read(path) for path in required_paths}
    texts.update(
        {
            path.relative_to(root).as_posix(): path.read_text(encoding="utf-8")
            for path in optional_paths
            if path.exists()
        }
    )
    return texts


def test_task_skill_paths_are_identical():
    """Live, mirror, and template copies must stay in lockstep."""
    texts = _collect_task_skill_texts(
        root=ROOT,
        required_paths=REQUIRED_TASK_SKILL_PATHS,
        optional_paths=OPTIONAL_TASK_SKILL_PATHS,
    )
    canonical = texts[".agents/skills/omc-task/SKILL.md"]
    mismatched = [name for name, text in texts.items() if text != canonical]
    assert not mismatched, f"omc-task skill copies differ: {mismatched}"


def test_ignored_live_agent_skill_path_is_optional(tmp_path: Path):
    """Clean checkouts may not have the ignored .agent live mirror."""
    canonical = tmp_path / ".agents" / "skills" / "omc-task" / "SKILL.md"
    template_codex = tmp_path / "templates" / ".agents" / "skills" / "omc-task" / "SKILL.md"
    template_agent = tmp_path / "templates" / ".agent" / "skills" / "omc-task" / "SKILL.md"
    ignored_live_agent = tmp_path / ".agent" / "skills" / "omc-task" / "SKILL.md"

    for path in (canonical, template_codex, template_agent):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("same", encoding="utf-8")

    texts = _collect_task_skill_texts(
        root=tmp_path,
        required_paths=(canonical, template_codex, template_agent),
        optional_paths=(ignored_live_agent,),
    )
    assert texts == {
        ".agents/skills/omc-task/SKILL.md": "same",
        "templates/.agents/skills/omc-task/SKILL.md": "same",
        "templates/.agent/skills/omc-task/SKILL.md": "same",
    }


def test_task_skill_stays_short_enough_to_scan():
    """The executable skill should fit in a single quick reading pass."""
    text = _read(REQUIRED_TASK_SKILL_PATHS[0])
    non_empty_lines = [line for line in text.splitlines() if line.strip()]
    assert len(non_empty_lines) <= MAX_NON_EMPTY_LINES, (
        f"omc-task has {len(non_empty_lines)} non-empty lines"
    )


def test_task_skill_preserves_required_execution_order():
    """Shortening the skill must not reorder or drop safety gates."""
    text = _read(REQUIRED_TASK_SKILL_PATHS[0])
    cursor = -1
    missing_or_reordered: list[str] = []

    for marker in REQUIRED_SEQUENCE:
        next_pos = text.find(marker, cursor + 1)
        if next_pos == -1:
            missing_or_reordered.append(marker)
        else:
            cursor = next_pos

    assert not missing_or_reordered, f"missing or reordered markers: {missing_or_reordered}"


def test_task_skill_explains_visible_vs_implicit_steps():
    """The shortened skill should distinguish user-visible gates from implicit work."""
    text = _read(REQUIRED_TASK_SKILL_PATHS[0])
    missing = [marker for marker in REQUIRED_FOCUS_MARKERS if marker not in text]
    assert not missing, f"missing focus markers: {missing}"
