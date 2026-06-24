"""
omc-critique skill contract regression tests.

Critique is a pre-mortem skill. Shortening it must not weaken mode detection,
skeptical output quality, or verdict discipline.
"""
from __future__ import annotations

import re
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
MAX_NON_EMPTY_LINES = 79

REQUIRED_CRITIQUE_SKILL_PATHS = [
    ROOT / ".agents" / "skills" / "omc-critique" / "SKILL.md",
    ROOT / "templates" / ".agents" / "skills" / "omc-critique" / "SKILL.md",
    ROOT / "templates" / ".agent" / "skills" / "omc-critique" / "SKILL.md",
]
OPTIONAL_CRITIQUE_SKILL_PATHS = [
    ROOT / ".agent" / "skills" / "omc-critique" / "SKILL.md",
]

REQUIRED_SEQUENCE = [
    "git diff --stat HEAD",
    "git ls-files --others --exclude-standard",
    "find . -newer .git/index",
    "python3 scripts/omc.py state status --target .",
    ".omc/runs",
    ".omc/lessons",
    "pipeline_run_result",
    "PLAN 모드",
    "CODE 모드",
    "Verdict",
    "$omc-plan",
    "$omc-review",
    "다음 추천",
    "주추천 1개",
    "자동으로 진행하지는 않습니다.",
]

REQUIRED_MARKERS = [
    "Pre-mortem",
    "칭찬 금지",
    "가정",
    "누락",
    "리스크",
    "실패 조건",
    "의존성",
    "CRITICAL",
    "WARNING",
    "MINOR",
    "HOLD",
    "REVISE",
    "PROCEED",
    "BLOCK",
    "APPROVE",
    "근거",
    "대안",
    "권고 조치",
    "변경 비용 추정",
    "같은 REVISE/HOLD 사유가 반복될 때만",
    "반복 근거가 없으면 여기서 중단",
    "사용자 선택 대기",
]

REQUIRED_FOCUS_MARKERS = [
    "사용자에게 보여줄 것",
    "시스템이 암묵적으로 처리",
]

VALID_CRITIQUE_SAMPLE = """
CRITICAL:
- 근거: 세션 request가 다르면 잘못된 CONTRACT로 task에 들어간다.
  대안: state init/session_start 후 CONTRACT를 다시 등록한다.

WARNING:
- 근거: untracked 파일을 보지 않으면 신규 파일 누락을 감지하지 못한다.
  대안: git ls-files --others --exclude-standard와 find . -newer .git/index를 모두 확인한다.

MINOR:
- 근거: 성공 지표가 없으면 완료 판단이 임의적이다.
  대안: 테스트 fixture로 verdict와 행동 원칙을 고정한다.

VERDICT: HOLD
"""

INVALID_CRITIQUE_SAMPLE = """
좋은 접근입니다.
CRITICAL:
- 세션이 좀 애매합니다.

VERDICT: PROCEED
"""


def _read(path: Path) -> str:
    assert path.exists(), f"missing critique skill path: {path.relative_to(ROOT)}"
    return path.read_text(encoding="utf-8")


def _collect_critique_skill_texts(
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


def _validate_critique_output(sample: str) -> list[str]:
    failures: list[str] = []

    praise_patterns = [
        r"좋은\s+접근",
        r"잘\s+설계",
        r"훌륭",
        r"칭찬",
    ]
    if any(re.search(pattern, sample) for pattern in praise_patterns):
        failures.append("praise")

    required_patterns = {
        "critical": r"CRITICAL:\s*\n\s*-",
        "warning": r"WARNING:\s*\n\s*-",
        "minor": r"MINOR:\s*\n\s*-",
        "evidence": r"근거:\s*\S",
        "alternative": r"대안:\s*\S",
        "verdict": r"VERDICT:\s*(HOLD|REVISE|PROCEED|BLOCK|APPROVE)",
    }
    failures.extend(
        name
        for name, pattern in required_patterns.items()
        if not re.search(pattern, sample)
    )
    return failures


def test_critique_skill_paths_are_identical():
    canonical = _read(REQUIRED_CRITIQUE_SKILL_PATHS[0])
    texts = {
        path.relative_to(ROOT).as_posix(): _read(path)
        for path in REQUIRED_CRITIQUE_SKILL_PATHS
    }
    mismatched = [name for name, text in texts.items() if text != canonical]
    assert not mismatched, f"omc-critique skill copies differ: {mismatched}"


def test_ignored_live_agent_critique_path_is_optional(tmp_path: Path):
    canonical = tmp_path / ".agents" / "skills" / "omc-critique" / "SKILL.md"
    template_codex = tmp_path / "templates" / ".agents" / "skills" / "omc-critique" / "SKILL.md"
    template_agent = tmp_path / "templates" / ".agent" / "skills" / "omc-critique" / "SKILL.md"
    ignored_live_agent = tmp_path / ".agent" / "skills" / "omc-critique" / "SKILL.md"

    for path in (canonical, template_codex, template_agent):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("same", encoding="utf-8")

    texts = _collect_critique_skill_texts(
        root=tmp_path,
        required_paths=(canonical, template_codex, template_agent),
        optional_paths=(ignored_live_agent,),
    )
    assert texts == {
        ".agents/skills/omc-critique/SKILL.md": "same",
        "templates/.agents/skills/omc-critique/SKILL.md": "same",
        "templates/.agent/skills/omc-critique/SKILL.md": "same",
    }


def test_critique_skill_stays_short_enough_to_scan():
    text = _read(REQUIRED_CRITIQUE_SKILL_PATHS[0])
    non_empty_lines = [line for line in text.splitlines() if line.strip()]
    assert len(non_empty_lines) <= MAX_NON_EMPTY_LINES, (
        f"omc-critique has {len(non_empty_lines)} non-empty lines"
    )


def test_critique_skill_avoids_duplicate_auto_progress_warnings():
    text = _read(REQUIRED_CRITIQUE_SKILL_PATHS[0])
    assert text.count("자동으로") <= 1, "duplicate auto-progress wording should be trimmed"


def test_critique_skill_limits_duplicate_change_cost_wording():
    text = _read(REQUIRED_CRITIQUE_SKILL_PATHS[0])
    assert text.count("변경 비용") <= 4, "change-cost wording should stay compact"


def test_critique_skill_preserves_required_execution_order():
    text = _read(REQUIRED_CRITIQUE_SKILL_PATHS[0])
    cursor = -1
    missing_or_reordered: list[str] = []

    for marker in REQUIRED_SEQUENCE:
        next_pos = text.find(marker, cursor + 1)
        if next_pos == -1:
            missing_or_reordered.append(marker)
        else:
            cursor = next_pos

    assert not missing_or_reordered, f"missing or reordered markers: {missing_or_reordered}"


def test_critique_skill_preserves_required_behavior_markers():
    text = _read(REQUIRED_CRITIQUE_SKILL_PATHS[0])
    missing = [marker for marker in REQUIRED_MARKERS if marker not in text]
    assert not missing, f"missing behavior markers: {missing}"


def test_critique_skill_explains_visible_vs_implicit_work():
    text = _read(REQUIRED_CRITIQUE_SKILL_PATHS[0])
    missing = [marker for marker in REQUIRED_FOCUS_MARKERS if marker not in text]
    assert not missing, f"missing focus markers: {missing}"


def test_critique_skill_recommendations_stay_conservative():
    text = _read(REQUIRED_CRITIQUE_SKILL_PATHS[0])
    required_markers = [
        "다음 추천",
        "우선순위",
        "HOLD/REVISE",
        "$omc-plan",
        "PROCEED",
        "PLAN 모드",
        "CODE 모드",
        "범위 고정",
        "$omc-task",
        "$omc-review",
        "자동으로 진행하지는 않습니다.",
    ]
    missing = [marker for marker in required_markers if marker not in text]
    assert not missing, f"missing critique recommendation markers: {missing}"


def test_valid_critique_output_fixture_has_required_structure():
    assert _validate_critique_output(VALID_CRITIQUE_SAMPLE) == []


def test_invalid_critique_output_fixture_exposes_weak_critique():
    failures = _validate_critique_output(INVALID_CRITIQUE_SAMPLE)
    assert {"praise", "warning", "minor", "evidence", "alternative"}.issubset(
        set(failures)
    )
