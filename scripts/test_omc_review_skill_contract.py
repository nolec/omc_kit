"""
omc-review skill contract regression tests.

The review skill can be short, but it must still preserve scope collection,
evidence, severity buckets, and the verdict contract.
"""
from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
MAX_NON_EMPTY_LINES = 60

REQUIRED_REVIEW_SKILL_PATHS = [
    ROOT / ".agents" / "skills" / "omc-review" / "SKILL.md",
    ROOT / "templates" / ".agents" / "skills" / "omc-review" / "SKILL.md",
    ROOT / "templates" / ".agent" / "skills" / "omc-review" / "SKILL.md",
]
OPTIONAL_REVIEW_SKILL_PATHS = [
    ROOT / ".agent" / "skills" / "omc-review" / "SKILL.md",
]

REQUIRED_SEQUENCE = [
    "python3 scripts/omc.py state sync-session --target . --mode autopilot --title \"omc-review\" --request \"<현재 작업 한 줄 요약>\" --roles code_review",
    "git status -sb",
    "git diff HEAD",
    "git ls-files --others --exclude-standard",
    "find . -newer .git/index",
    "python3 scripts/omc.py state status --target .",
    "필수 체크",
    "범위 확정",
    "파일:라인 근거",
    "검증 커맨드",
    "리뷰 범위",
    "파일을 직접 읽고",
    ".omc/runs",
    ".omc/lessons",
    "pipeline_run_result",
    "C1",
    "C2",
    "C3",
    "C4",
    "C5",
    "C6",
    "C7",
    "REVIEW RESULT",
    "[치명]",
    "[중대]",
    "[경미]",
    "[제안]",
    "파일:라인",
    "BLOCK",
    "REVISE",
    "APPROVE WITH NOTES",
    "APPROVE",
    "다음 추천",
    "주추천 1개",
    "자동으로 진행하지는 않습니다.",
]

REQUIRED_FOCUS_MARKERS = [
    "리뷰어가 사용자에게 바로 보여줄 것",
    "시스템이 암묵적으로 처리",
]

REQUIRED_SAFETY_MARKERS = [
    "안전 필수 항목",
    "파일:라인",
    "VERDICT",
    "[치명]",
    "[중대]",
    "[경미]",
    "[제안]",
]


def _read(path: Path) -> str:
    assert path.exists(), f"missing review skill path: {path.relative_to(ROOT)}"
    return path.read_text(encoding="utf-8")


def _collect_review_skill_texts(
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


def test_review_skill_paths_are_identical():
    texts = _collect_review_skill_texts(
        root=ROOT,
        required_paths=REQUIRED_REVIEW_SKILL_PATHS,
        optional_paths=OPTIONAL_REVIEW_SKILL_PATHS,
    )
    canonical = texts[".agents/skills/omc-review/SKILL.md"]
    mismatched = [name for name, text in texts.items() if text != canonical]
    assert not mismatched, f"omc-review skill copies differ: {mismatched}"


def test_ignored_live_agent_review_path_is_optional(tmp_path: Path):
    canonical = tmp_path / ".agents" / "skills" / "omc-review" / "SKILL.md"
    template_codex = tmp_path / "templates" / ".agents" / "skills" / "omc-review" / "SKILL.md"
    template_agent = tmp_path / "templates" / ".agent" / "skills" / "omc-review" / "SKILL.md"
    ignored_live_agent = tmp_path / ".agent" / "skills" / "omc-review" / "SKILL.md"

    for path in (canonical, template_codex, template_agent):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("same", encoding="utf-8")

    texts = _collect_review_skill_texts(
        root=tmp_path,
        required_paths=(canonical, template_codex, template_agent),
        optional_paths=(ignored_live_agent,),
    )
    assert texts == {
        ".agents/skills/omc-review/SKILL.md": "same",
        "templates/.agents/skills/omc-review/SKILL.md": "same",
        "templates/.agent/skills/omc-review/SKILL.md": "same",
    }


def test_review_skill_stays_short_enough_to_scan():
    text = _read(REQUIRED_REVIEW_SKILL_PATHS[0])
    non_empty_lines = [line for line in text.splitlines() if line.strip()]
    assert len(non_empty_lines) <= MAX_NON_EMPTY_LINES, (
        f"omc-review has {len(non_empty_lines)} non-empty lines"
    )


def test_review_skill_preserves_required_execution_order():
    text = _read(REQUIRED_REVIEW_SKILL_PATHS[0])
    cursor = -1
    missing_or_reordered: list[str] = []

    for marker in REQUIRED_SEQUENCE:
        next_pos = text.find(marker, cursor + 1)
        if next_pos == -1:
            missing_or_reordered.append(marker)
        else:
            cursor = next_pos

    assert not missing_or_reordered, f"missing or reordered markers: {missing_or_reordered}"


def test_review_skill_explains_visible_vs_implicit_work():
    text = _read(REQUIRED_REVIEW_SKILL_PATHS[0])
    missing = [marker for marker in REQUIRED_FOCUS_MARKERS if marker not in text]
    assert not missing, f"missing focus markers: {missing}"


def test_review_skill_declares_non_negotiable_review_contract():
    text = _read(REQUIRED_REVIEW_SKILL_PATHS[0])
    missing = [marker for marker in REQUIRED_SAFETY_MARKERS if marker not in text]
    assert not missing, f"missing review safety markers: {missing}"


def test_review_skill_recommendations_match_verdict_buckets():
    text = _read(REQUIRED_REVIEW_SKILL_PATHS[0])
    required_markers = [
        "다음 추천",
        "REVISE/BLOCK",
        "$omc-task",
        "APPROVE/APPROVE WITH NOTES",
        "배포 준비",
        "$omc-ship",
        "사용자 선택 대기",
        "그 외",
        "종료/후속 작업 선택",
        "자동으로 진행하지는 않습니다.",
    ]
    missing = [marker for marker in required_markers if marker not in text]
    assert not missing, f"missing review recommendation markers: {missing}"
