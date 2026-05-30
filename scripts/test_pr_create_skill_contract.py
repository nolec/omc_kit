"""
pr-create skill contract regression tests.

This is a non-omc external effect skill. It must separate read-only checks
from side-effect commands and require explicit user approval before push/PR.
"""
from __future__ import annotations

import re
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
MAX_NON_EMPTY_LINES = 90

REQUIRED_PR_SKILL_PATHS = [
    ROOT / ".agents" / "skills" / "pr-create" / "SKILL.md",
    ROOT / "templates" / ".agents" / "skills" / "pr-create" / "SKILL.md",
]
OPTIONAL_PR_SKILL_PATHS = [
    ROOT / ".agent" / "skills" / "pr-create" / "SKILL.md",
    ROOT / "templates" / ".agent" / "skills" / "pr-create" / "SKILL.md",
]

REQUIRED_SEQUENCE = [
    "non-omc",
    "omc_skill_check",
    "대상이 아님",
    "$omc-ship",
    "python3 scripts/omc_guard.py require --target . --for \"ship\"",
    "python3 scripts/omc_tdd_check.py --staged",
    "필수 체크",
    "ship gate",
    "승인 상태",
    "쓰기 명령 차단",
    "git status -sb",
    "git log",
    "git diff --stat",
    "gh auth status",
    "gh pr list",
    ".github/PULL_REQUEST_TEMPLATE.md",
    "읽기 전용 확인",
    "쓰기/부작용 명령",
    "main/master/trunk",
    "중복 PR",
    "push 필요",
    "assignee 후보",
    "사용자 승인 전",
    "git push",
    "gh pr create",
    "gh label create",
    "실행 금지",
    "fallback",
    "작업 사항",
    "검증",
    "리스크/롤백",
    "스크린샷 또는 N/A",
]

REQUIRED_BEHAVIOR_MARKERS = [
    "필수 체크",
    "승인 상태",
    "미승인",
    "승인",
    "N/A",
    "이유",
    "brew install gh",
    "예시",
]

VALID_PR_SAMPLE = """
사전 확인(읽기 전용 확인):
- ship gate: python3 scripts/omc_guard.py require --target . --for "ship"
- tdd gate: python3 scripts/omc_tdd_check.py --staged
- gh auth status
- gh pr list
- .github/PULL_REQUEST_TEMPLATE.md

차단/주의:
- 현재 브랜치: main/master/trunk 아님
- 중복 PR: 없음
- push 필요: yes
- 승인 상태: 미승인

쓰기/부작용 명령:
- git push (사용자 승인 전 실행 금지)
- gh pr create (사용자 승인 전 실행 금지)
- gh label create (사용자 승인 전 실행 금지)

fallback:
- 작업 사항
- 검증
- 리스크/롤백
- 스크린샷 또는 N/A
"""

INVALID_PR_SAMPLE = """
PR 생성:
gh pr create --title "quick"
"""


def _read(path: Path) -> str:
    assert path.exists(), f"missing pr-create skill path: {path.relative_to(ROOT)}"
    return path.read_text(encoding="utf-8")


def _collect_pr_skill_texts(
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


def _validate_pr_output(sample: str) -> list[str]:
    required_patterns = {
        "readonly": r"읽기 전용 확인.*gh auth status.*gh pr list",
        "gate": r"ship gate:.*tdd gate:",
        "approval": r"승인 상태:\s*(미승인|승인)",
        "side_effect_block": r"git push.*실행 금지.*gh pr create.*실행 금지",
        "fallback": r"fallback:.*작업 사항.*검증.*리스크/롤백.*스크린샷 또는 N/A",
    }
    return [
        name
        for name, pattern in required_patterns.items()
        if not re.search(pattern, sample, re.S)
    ]


def test_pr_create_skill_paths_are_identical():
    texts = _collect_pr_skill_texts(
        root=ROOT,
        required_paths=REQUIRED_PR_SKILL_PATHS,
        optional_paths=OPTIONAL_PR_SKILL_PATHS,
    )
    canonical = texts[".agents/skills/pr-create/SKILL.md"]
    mismatched = [name for name, text in texts.items() if text != canonical]
    assert not mismatched, f"pr-create skill copies differ: {mismatched}"


def test_pr_create_skill_stays_short_enough_to_scan():
    text = _read(REQUIRED_PR_SKILL_PATHS[0])
    non_empty_lines = [line for line in text.splitlines() if line.strip()]
    assert len(non_empty_lines) <= MAX_NON_EMPTY_LINES, (
        f"pr-create has {len(non_empty_lines)} non-empty lines"
    )


def test_pr_create_skill_preserves_required_execution_order():
    text = _read(REQUIRED_PR_SKILL_PATHS[0])
    cursor = -1
    missing_or_reordered: list[str] = []

    for marker in REQUIRED_SEQUENCE:
        next_pos = text.find(marker, cursor + 1)
        if next_pos == -1:
            missing_or_reordered.append(marker)
        else:
            cursor = next_pos

    assert not missing_or_reordered, f"missing or reordered markers: {missing_or_reordered}"


def test_pr_create_skill_preserves_required_behavior_markers():
    text = _read(REQUIRED_PR_SKILL_PATHS[0])
    missing = [marker for marker in REQUIRED_BEHAVIOR_MARKERS if marker not in text]
    assert not missing, f"missing behavior markers: {missing}"


def test_pr_create_skill_does_not_auto_execute_side_effects():
    text = _read(REQUIRED_PR_SKILL_PATHS[0])
    forbidden = [r"즉시 실행", r"자동 실행", r"바로 gh pr create"]
    found = [pattern for pattern in forbidden if re.search(pattern, text)]
    assert not found, f"pr-create should not auto-run side effects: {found}"


def test_valid_pr_output_fixture_has_required_structure():
    assert _validate_pr_output(VALID_PR_SAMPLE) == []


def test_invalid_pr_output_fixture_exposes_weak_pr_gate():
    failures = _validate_pr_output(INVALID_PR_SAMPLE)
    assert {"readonly", "gate", "approval", "side_effect_block", "fallback"}.issubset(
        set(failures)
    )
