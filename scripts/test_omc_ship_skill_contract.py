"""
omc-ship skill contract regression tests.

Ship is a release gate. Shortening it must preserve blocking checks and keep
push/deploy execution behind explicit user approval.
"""
from __future__ import annotations

import re
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
MAX_NON_EMPTY_LINES = 49

REQUIRED_SHIP_SKILL_PATHS = [
    ROOT / ".agents" / "skills" / "omc-ship" / "SKILL.md",
    ROOT / "templates" / ".agents" / "skills" / "omc-ship" / "SKILL.md",
    ROOT / "templates" / ".agent" / "skills" / "omc-ship" / "SKILL.md",
]
OPTIONAL_SHIP_SKILL_PATHS = [
    ROOT / ".agent" / "skills" / "omc-ship" / "SKILL.md",
]

REQUIRED_SEQUENCE = [
    "$omc-review",
    "치명/중대",
    "필수 체크",
    "게이트 통과",
    "비밀값 검사",
    "python3 scripts/omc_guard.py sync-require --target . --mode autopilot --title \"omc-ship\" --request \"<현재 작업 한 줄 요약>\" --roles directive --for \"ship\"",
    "python3 scripts/omc_tdd_check.py --run-tests",
    "git status -sb",
    "git diff HEAD",
    "git ls-files --others --exclude-standard",
    "package.json",
    "README",
    "ETHOS.md",
    "테스트",
    "타입",
    "린트",
    "SECRET",
    "KEY",
    "TOKEN",
    "PASSWORD",
    "모든 게이트 통과 전",
    "사용자 승인",
    "사용자 명시 승인 전",
    "git push",
    "deploy",
    "실제 배포 후",
    "다음 추천",
    "주추천 1개",
    "자동으로 진행하지는 않습니다.",
]

REQUIRED_BEHAVIOR_MARKERS = [
    "배포 차단",
    "명령은 예시",
    "Nx 미사용",
    "필수 체크",
    "현재 ship 대상 범위",
    "범위 밖 dirty 변경",
    "$omc-investigate",
    "$omc-task",
    "$pr-create",
    "$omc-retro",
    "교훈",
    "SHIP READY",
    "BLOCKED",
]

REQUIRED_FOCUS_MARKERS = [
    "사용자에게 보여줄 것",
    "시스템이 암묵적으로 처리",
]

VALID_SHIP_SAMPLE = """
OMC 가드: PASS
TDD 게이트: PASS
테스트: PASS
타입: PASS
린트: PASS
현재 ship 대상 범위: templates/.claude/commands/plan.md, scripts/test_llm_autopilot_commands.py
범위 밖 dirty 변경: docs/omc_quickstart.md
git status -sb: clean
git diff HEAD: SECRET/KEY/TOKEN/PASSWORD 없음
untracked: 없음
프로젝트별 명령 확인: package.json, README, ETHOS.md 확인
사용자 명시 승인: 완료
결론: SHIP READY
"""

INVALID_SHIP_SAMPLE = """
OMC 가드: PASS
테스트: PASS
결론: SHIP READY
"""


def _read(path: Path) -> str:
    assert path.exists(), f"missing ship skill path: {path.relative_to(ROOT)}"
    return path.read_text(encoding="utf-8")


def _collect_ship_skill_texts(
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


def _validate_ship_output(sample: str) -> list[str]:
    required_patterns = {
        "guard": r"OMC 가드:\s*PASS",
        "tdd": r"TDD 게이트:\s*PASS",
        "tests": r"테스트:\s*PASS",
        "types": r"타입:\s*PASS",
        "lint": r"린트:\s*PASS",
        "status": r"git status -sb:\s*\S",
        "secrets": r"SECRET/KEY/TOKEN/PASSWORD 없음",
        "untracked": r"untracked:\s*\S",
        "project_commands": r"package\.json.*README.*ETHOS\.md",
        "approval": r"사용자 명시 승인:\s*완료",
        "verdict": r"결론:\s*SHIP READY",
    }
    return [
        name
        for name, pattern in required_patterns.items()
        if not re.search(pattern, sample, re.S)
    ]


def test_ship_skill_paths_are_identical():
    texts = _collect_ship_skill_texts(
        root=ROOT,
        required_paths=REQUIRED_SHIP_SKILL_PATHS,
        optional_paths=OPTIONAL_SHIP_SKILL_PATHS,
    )
    canonical = texts[".agents/skills/omc-ship/SKILL.md"]
    mismatched = [name for name, text in texts.items() if text != canonical]
    assert not mismatched, f"omc-ship skill copies differ: {mismatched}"


def test_ignored_live_agent_ship_path_is_optional(tmp_path: Path):
    canonical = tmp_path / ".agents" / "skills" / "omc-ship" / "SKILL.md"
    template_codex = tmp_path / "templates" / ".agents" / "skills" / "omc-ship" / "SKILL.md"
    template_agent = tmp_path / "templates" / ".agent" / "skills" / "omc-ship" / "SKILL.md"
    ignored_live_agent = tmp_path / ".agent" / "skills" / "omc-ship" / "SKILL.md"

    for path in (canonical, template_codex, template_agent):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("same", encoding="utf-8")

    texts = _collect_ship_skill_texts(
        root=tmp_path,
        required_paths=(canonical, template_codex, template_agent),
        optional_paths=(ignored_live_agent,),
    )
    assert texts == {
        ".agents/skills/omc-ship/SKILL.md": "same",
        "templates/.agents/skills/omc-ship/SKILL.md": "same",
        "templates/.agent/skills/omc-ship/SKILL.md": "same",
    }


def test_ship_skill_stays_short_enough_to_scan():
    text = _read(REQUIRED_SHIP_SKILL_PATHS[0])
    non_empty_lines = [line for line in text.splitlines() if line.strip()]
    assert len(non_empty_lines) <= MAX_NON_EMPTY_LINES, (
        f"omc-ship has {len(non_empty_lines)} non-empty lines"
    )


def test_ship_skill_avoids_duplicate_approval_wording():
    text = _read(REQUIRED_SHIP_SKILL_PATHS[0])
    assert text.count("사용자 명시 승인") <= 2, "duplicate approval wording should be trimmed"


def test_ship_skill_preserves_required_execution_order():
    text = _read(REQUIRED_SHIP_SKILL_PATHS[0])
    cursor = -1
    missing_or_reordered: list[str] = []

    for marker in REQUIRED_SEQUENCE:
        next_pos = text.find(marker, cursor + 1)
        if next_pos == -1:
            missing_or_reordered.append(marker)
        else:
            cursor = next_pos

    assert not missing_or_reordered, f"missing or reordered markers: {missing_or_reordered}"


def test_ship_skill_preserves_required_behavior_markers():
    text = _read(REQUIRED_SHIP_SKILL_PATHS[0])
    missing = [marker for marker in REQUIRED_BEHAVIOR_MARKERS if marker not in text]
    assert not missing, f"missing behavior markers: {missing}"


def test_ship_skill_explains_visible_vs_implicit_work():
    text = _read(REQUIRED_SHIP_SKILL_PATHS[0])
    missing = [marker for marker in REQUIRED_FOCUS_MARKERS if marker not in text]
    assert not missing, f"missing focus markers: {missing}"


def test_ship_skill_recommendations_match_blocked_and_post_deploy_states():
    text = _read(REQUIRED_SHIP_SKILL_PATHS[0])
    required_markers = [
        "다음 추천",
        "SHIP READY",
        "사용자 선택 대기",
        "실제 배포 후",
        "$omc-retro",
        "BLOCKED",
        "$omc-investigate",
        "$omc-task",
        "자동으로 진행하지는 않습니다.",
    ]
    missing = [marker for marker in required_markers if marker not in text]
    assert not missing, f"missing ship recommendation markers: {missing}"


def test_ship_skill_has_single_required_check_section():
    text = _read(REQUIRED_SHIP_SKILL_PATHS[0])
    assert text.count("## 필수 체크") == 1, "omc-ship must keep a single 필수 체크 section"


def test_valid_ship_output_fixture_has_required_structure():
    assert _validate_ship_output(VALID_SHIP_SAMPLE) == []


def test_invalid_ship_output_fixture_exposes_weak_release_gate():
    failures = _validate_ship_output(INVALID_SHIP_SAMPLE)
    assert {"tdd", "types", "lint", "secrets", "approval"}.issubset(set(failures))
