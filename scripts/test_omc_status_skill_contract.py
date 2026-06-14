"""
omc-status skill contract regression tests.

Status is a read-only diagnostic skill. It must detect stale OMC context,
separate execution artifacts from source changes, and recommend the next
workflow step without mutating session or git state.
"""
from __future__ import annotations

import re
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
MAX_NON_EMPTY_LINES = 44

REQUIRED_STATUS_SKILL_PATHS = [
    ROOT / ".agents" / "skills" / "omc-status" / "SKILL.md",
    ROOT / "templates" / ".agents" / "skills" / "omc-status" / "SKILL.md",
    ROOT / "templates" / ".agent" / "skills" / "omc-status" / "SKILL.md",
]
OPTIONAL_STATUS_SKILL_PATHS = [
    ROOT / ".agent" / "skills" / "omc-status" / "SKILL.md",
]

REQUIRED_SEQUENCE = [
    "조회 전용",
    "python3 scripts/omc.py state status --target .",
    "cat .omc/notepad.md",
    "git status -sb",
    "git diff --stat HEAD",
    "git ls-files --others --exclude-standard",
    "git log --oneline -5",
    "세션 불일치",
    "stale",
    "소스/스킬 변경",
    ".omc 실행 아티팩트",
    "untracked",
    "OMC 세션",
    "Git 상태",
    "변경 분류",
    "차단/주의",
    "다음 액션",
    "$omc-plan",
    "$omc-task",
    "$omc-review",
    "$omc-ship",
    "$omc-retro",
]

REQUIRED_BEHAVIOR_MARKERS = [
    "현재 사용자 요청",
    "latest request",
    "confirmed_request",
    "pending_request",
    "N/A",
    "이유",
    "커밋 대상 아님",
    "남은 스킬 후보",
]

REQUIRED_FOCUS_MARKERS = [
    "사용자에게 보여줄 것",
    "시스템이 암묵적으로 처리",
]

REQUIRED_SAFETY_MARKERS = [
    "조회 전용 안전 항목",
    "read-only",
    "커밋 대상 아님",
    "다음 액션",
]

MUTATING_COMMAND_PATTERNS = [
    r"python3 scripts/omc\.py state confirm",
    r"python3 scripts/omc\.py state init",
    r"python3 scripts/omc\.py hook session_start",
    r"\bgit add\b",
    r"\bgit commit\b",
    r"\bgit push\b",
    r"\bdeploy\b",
]

VALID_STATUS_SAMPLE = """
OMC 세션: latest request와 현재 사용자 요청 불일치 — stale 세션 불일치
Git 상태: main...origin/main [ahead 8]
변경 분류: 소스/스킬 변경 없음, .omc 실행 아티팩트만 있음, untracked 있음
차단/주의: .omc 실행 아티팩트는 커밋 대상 아님
다음 액션: $omc-plan으로 현재 요청 재정렬
"""

INVALID_STATUS_SAMPLE = """
현재 확정된 작업: resume hang retry
다음 1액션: 계속 진행
"""


def _read(path: Path) -> str:
    assert path.exists(), f"missing status skill path: {path.relative_to(ROOT)}"
    return path.read_text(encoding="utf-8")


def _collect_status_skill_texts(
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


def _validate_status_output(sample: str) -> list[str]:
    required_patterns = {
        "session": r"OMC 세션:.*(세션 불일치|stale)",
        "git": r"Git 상태:\s*\S",
        "classification": r"변경 분류:.*소스/스킬 변경.*\.omc 실행 아티팩트.*untracked",
        "warning": r"차단/주의:.*커밋 대상 아님",
        "next_action": r"다음 액션:.*\$omc-(plan|task|review|ship|retro)",
    }
    return [
        name
        for name, pattern in required_patterns.items()
        if not re.search(pattern, sample, re.S)
    ]


def test_status_skill_paths_are_identical():
    texts = _collect_status_skill_texts(
        root=ROOT,
        required_paths=REQUIRED_STATUS_SKILL_PATHS,
        optional_paths=OPTIONAL_STATUS_SKILL_PATHS,
    )
    canonical = texts[".agents/skills/omc-status/SKILL.md"]
    mismatched = [name for name, text in texts.items() if text != canonical]
    assert not mismatched, f"omc-status skill copies differ: {mismatched}"


def test_ignored_live_agent_status_path_is_optional(tmp_path: Path):
    canonical = tmp_path / ".agents" / "skills" / "omc-status" / "SKILL.md"
    template_codex = tmp_path / "templates" / ".agents" / "skills" / "omc-status" / "SKILL.md"
    template_agent = tmp_path / "templates" / ".agent" / "skills" / "omc-status" / "SKILL.md"
    ignored_live_agent = tmp_path / ".agent" / "skills" / "omc-status" / "SKILL.md"

    for path in (canonical, template_codex, template_agent):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("same", encoding="utf-8")

    texts = _collect_status_skill_texts(
        root=tmp_path,
        required_paths=(canonical, template_codex, template_agent),
        optional_paths=(ignored_live_agent,),
    )
    assert texts == {
        ".agents/skills/omc-status/SKILL.md": "same",
        "templates/.agents/skills/omc-status/SKILL.md": "same",
        "templates/.agent/skills/omc-status/SKILL.md": "same",
    }


def test_status_skill_stays_short_enough_to_scan():
    text = _read(REQUIRED_STATUS_SKILL_PATHS[0])
    non_empty_lines = [line for line in text.splitlines() if line.strip()]
    assert len(non_empty_lines) <= MAX_NON_EMPTY_LINES, (
        f"omc-status has {len(non_empty_lines)} non-empty lines"
    )


def test_status_skill_avoids_duplicate_stale_wording():
    text = _read(REQUIRED_STATUS_SKILL_PATHS[0])
    assert text.count("세션 불일치") <= 2, "duplicate stale wording should be trimmed"


def test_status_skill_preserves_required_execution_order():
    text = _read(REQUIRED_STATUS_SKILL_PATHS[0])
    cursor = -1
    missing_or_reordered: list[str] = []

    for marker in REQUIRED_SEQUENCE:
        next_pos = text.find(marker, cursor + 1)
        if next_pos == -1:
            missing_or_reordered.append(marker)
        else:
            cursor = next_pos

    assert not missing_or_reordered, f"missing or reordered markers: {missing_or_reordered}"


def test_status_skill_preserves_required_behavior_markers():
    text = _read(REQUIRED_STATUS_SKILL_PATHS[0])
    missing = [marker for marker in REQUIRED_BEHAVIOR_MARKERS if marker not in text]
    assert not missing, f"missing behavior markers: {missing}"


def test_status_skill_explains_visible_vs_implicit_work():
    text = _read(REQUIRED_STATUS_SKILL_PATHS[0])
    missing = [marker for marker in REQUIRED_FOCUS_MARKERS if marker not in text]
    assert not missing, f"missing focus markers: {missing}"


def test_status_skill_declares_read_only_safety_markers():
    text = _read(REQUIRED_STATUS_SKILL_PATHS[0])
    missing = [marker for marker in REQUIRED_SAFETY_MARKERS if marker not in text]
    assert not missing, f"missing status safety markers: {missing}"


def test_status_skill_does_not_suggest_mutating_commands():
    text = _read(REQUIRED_STATUS_SKILL_PATHS[0])
    forbidden = [
        pattern for pattern in MUTATING_COMMAND_PATTERNS if re.search(pattern, text)
    ]
    assert not forbidden, f"status skill must stay read-only, found: {forbidden}"


def test_valid_status_output_fixture_has_required_structure():
    assert _validate_status_output(VALID_STATUS_SAMPLE) == []


def test_invalid_status_output_fixture_exposes_weak_status_report():
    failures = _validate_status_output(INVALID_STATUS_SAMPLE)
    assert {"session", "git", "classification", "warning", "next_action"}.issubset(
        set(failures)
    )
