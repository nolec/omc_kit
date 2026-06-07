"""
omc-investigate skill contract regression tests.

Investigate is a root-cause gate. Shortening it must preserve evidence-first
debugging, hypothesis rejection loops, and handoff to task for implementation.
"""
from __future__ import annotations

import re
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
MAX_NON_EMPTY_LINES = 41

REQUIRED_INVESTIGATE_SKILL_PATHS = [
    ROOT / ".agents" / "skills" / "omc-investigate" / "SKILL.md",
    ROOT / "templates" / ".agents" / "skills" / "omc-investigate" / "SKILL.md",
    ROOT / "templates" / ".agent" / "skills" / "omc-investigate" / "SKILL.md",
]
OPTIONAL_INVESTIGATE_SKILL_PATHS = [
    ROOT / ".agent" / "skills" / "omc-investigate" / "SKILL.md",
]

REQUIRED_SEQUENCE = [
    "python3 scripts/omc.py state sync-session --target . --mode autopilot --title \"omc-investigate\" --request \"<현재 작업 한 줄 요약>\" --roles analysis",
    "git diff --stat HEAD",
    "git log --oneline -5",
    "git ls-files --others --exclude-standard",
    "python3 scripts/omc.py state status --target .",
    "ROOT CAUSE",
    "현상",
    "재현 조건",
    "기대 동작",
    "재현 불가",
    "PATTERN ANALYSIS",
    "가설",
    "우선순위",
    "HYPOTHESIS TESTING",
    "검증 커맨드",
    "기각",
    "FIX PLAN",
    "근본 원인",
    "$omc-task",
]

REQUIRED_BEHAVIOR_MARKERS = [
    "수정 전 근본 원인",
    "증상 패치 금지",
    "추측 금지",
    "데이터",
    "로그",
    "코드 근거",
    "로그 추가",
    "간헐적 발생 패턴",
    "가설이 모두 기각",
    "PHASE 2로 돌아간다",
    "3회 연속 기각",
    "$omc-plan",
    "$omc-review",
    "$omc-ceo-review",
    "근본 원인 확정 전 구현 금지",
    "모든 LLM 공통 출력 형식",
    "입력 부족 시 중단",
]

REQUIRED_FOCUS_MARKERS = [
    "사용자에게 보여줄 것",
    "시스템이 암묵적으로 처리",
]

VALID_INVESTIGATE_SAMPLE = """
현상: 로그인 API가 500을 반환한다.
재현 조건: staging에서 expired token 요청 시 100% 재현
기대 동작: 401과 재로그인 안내를 반환한다.
재현 불가: N/A — 현재 100% 재현됨
원인 가설:
  1. [높음] expired token 예외 매핑 누락
검증 커맨드: python3 -m pytest scripts/test_auth.py
결과: 확인됨
근본 원인: expired token 예외가 generic 500으로 매핑된다.
FIX PLAN: 재현 테스트를 추가하고 $omc-task로 수정한다.
"""

INVALID_INVESTIGATE_SAMPLE = """
현상: 로그인 안 됨
FIX PLAN: auth.py 수정
"""


def _read(path: Path) -> str:
    assert path.exists(), f"missing investigate skill path: {path.relative_to(ROOT)}"
    return path.read_text(encoding="utf-8")


def _collect_investigate_skill_texts(
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


def _validate_investigate_output(sample: str) -> list[str]:
    required_patterns = {
        "symptom": r"현상:\s*\S",
        "repro": r"재현 조건:\s*\S",
        "expected": r"기대 동작:\s*\S",
        "hypothesis": r"원인 가설:.*\[높음\]",
        "verification": r"검증 커맨드:\s*\S",
        "result": r"결과:\s*(확인됨|기각됨)",
        "root_cause": r"근본 원인:\s*\S",
        "fix_plan": r"FIX PLAN:\s*\S",
        "task_handoff": r"\$omc-task",
    }
    return [
        name
        for name, pattern in required_patterns.items()
        if not re.search(pattern, sample, re.S)
    ]


def test_investigate_skill_paths_are_identical():
    texts = _collect_investigate_skill_texts(
        root=ROOT,
        required_paths=REQUIRED_INVESTIGATE_SKILL_PATHS,
        optional_paths=OPTIONAL_INVESTIGATE_SKILL_PATHS,
    )
    canonical = texts[".agents/skills/omc-investigate/SKILL.md"]
    mismatched = [name for name, text in texts.items() if text != canonical]
    assert not mismatched, f"omc-investigate skill copies differ: {mismatched}"


def test_ignored_live_agent_investigate_path_is_optional(tmp_path: Path):
    canonical = tmp_path / ".agents" / "skills" / "omc-investigate" / "SKILL.md"
    template_codex = tmp_path / "templates" / ".agents" / "skills" / "omc-investigate" / "SKILL.md"
    template_agent = tmp_path / "templates" / ".agent" / "skills" / "omc-investigate" / "SKILL.md"
    ignored_live_agent = tmp_path / ".agent" / "skills" / "omc-investigate" / "SKILL.md"

    for path in (canonical, template_codex, template_agent):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("same", encoding="utf-8")

    texts = _collect_investigate_skill_texts(
        root=tmp_path,
        required_paths=(canonical, template_codex, template_agent),
        optional_paths=(ignored_live_agent,),
    )
    assert texts == {
        ".agents/skills/omc-investigate/SKILL.md": "same",
        "templates/.agents/skills/omc-investigate/SKILL.md": "same",
        "templates/.agent/skills/omc-investigate/SKILL.md": "same",
    }


def test_investigate_skill_stays_short_enough_to_scan():
    text = _read(REQUIRED_INVESTIGATE_SKILL_PATHS[0])
    non_empty_lines = [line for line in text.splitlines() if line.strip()]
    assert len(non_empty_lines) <= MAX_NON_EMPTY_LINES, (
        f"omc-investigate has {len(non_empty_lines)} non-empty lines"
    )


def test_investigate_skill_preserves_required_execution_order():
    text = _read(REQUIRED_INVESTIGATE_SKILL_PATHS[0])
    cursor = -1
    missing_or_reordered: list[str] = []

    for marker in REQUIRED_SEQUENCE:
        next_pos = text.find(marker, cursor + 1)
        if next_pos == -1:
            missing_or_reordered.append(marker)
        else:
            cursor = next_pos

    assert not missing_or_reordered, f"missing or reordered markers: {missing_or_reordered}"


def test_investigate_skill_preserves_required_behavior_markers():
    text = _read(REQUIRED_INVESTIGATE_SKILL_PATHS[0])
    missing = [marker for marker in REQUIRED_BEHAVIOR_MARKERS if marker not in text]
    assert not missing, f"missing behavior markers: {missing}"


def test_investigate_skill_explains_visible_vs_implicit_work():
    text = _read(REQUIRED_INVESTIGATE_SKILL_PATHS[0])
    missing = [marker for marker in REQUIRED_FOCUS_MARKERS if marker not in text]
    assert not missing, f"missing focus markers: {missing}"


def test_valid_investigate_output_fixture_has_required_structure():
    assert _validate_investigate_output(VALID_INVESTIGATE_SAMPLE) == []


def test_invalid_investigate_output_fixture_exposes_weak_root_cause_gate():
    failures = _validate_investigate_output(INVALID_INVESTIGATE_SAMPLE)
    assert {"repro", "expected", "hypothesis", "root_cause", "task_handoff"}.issubset(
        set(failures)
    )
