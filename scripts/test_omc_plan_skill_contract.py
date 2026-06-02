"""
omc-plan skill contract regression tests.

The plan skill is upstream of implementation, so shortening it must preserve
requirements quality, explicit user confirmation, and TDD task structure.
"""
from __future__ import annotations

import re
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
MAX_NON_EMPTY_LINES = 45

REQUIRED_PLAN_SKILL_PATHS = [
    ROOT / ".agents" / "skills" / "omc-plan" / "SKILL.md",
    ROOT / "templates" / ".agents" / "skills" / "omc-plan" / "SKILL.md",
    ROOT / "templates" / ".agent" / "skills" / "omc-plan" / "SKILL.md",
]
OPTIONAL_PLAN_SKILL_PATHS = [
    ROOT / ".agent" / "skills" / "omc-plan" / "SKILL.md",
]

REQUIRED_SEQUENCE = [
    "python3 scripts/omc.py state status --target .",
    "AGENTS.md Tier 1",
    "CONTRACT",
    "목표",
    "범위 (포함)",
    "범위 (제외",
    "DoD",
    "제약",
    "사용자 컨펌",
    "입력",
    "출력",
    "성공 지표",
    "실패",
    "영향받는 파일",
    "RED",
    "GREEN",
    "VERIFY",
    "사용자 컨펌 완료 전",
    "python3 scripts/omc.py state confirm --target .",
    "$omc-task",
]

REQUIRED_FOCUS_MARKERS = [
    "사용자에게 보여줄 단계",
    "시스템이 암묵적으로 처리",
]

VALID_PLAN_SAMPLE = """
목표: 로그인 실패 원인을 재현 가능한 테스트로 고정한다.
범위 (포함): API 에러 매핑과 실패 메시지 테스트
범위 (제외): UI 스타일 변경, OAuth 신규 플로우
DoD: 실패 테스트가 먼저 실패하고 수정 후 통과한다.
제약: 기존 public API 시그니처는 바꾸지 않는다.
사용자 컨펌: 완료
입력: email/password
출력: auth result
성공 지표: REVISE/BLOCK 비율 감소
실패 정책: 외부 API 실패는 명시적 에러로 반환
영향받는 파일: src/auth.ts
태스크 1: API 에러 매핑
  RED    : scripts/test_auth.py::test_error_mapping
  GREEN  : src/auth.ts 최소 수정
  VERIFY : python3 -m pytest scripts/test_auth.py
"""

INVALID_PLAN_SAMPLE = """
목표: 로그인 개선
범위 (포함): 로그인
DoD: 잘 된다
태스크 1: 구현
  RED    : 테스트
  GREEN  : 코드
"""


def _read(path: Path) -> str:
    assert path.exists(), f"missing plan skill path: {path.relative_to(ROOT)}"
    return path.read_text(encoding="utf-8")


def _collect_plan_skill_texts(
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


def _validate_plan_output(sample: str) -> list[str]:
    required_patterns = {
        "goal": r"목표:\s*\S",
        "included_scope": r"범위 \(포함\):\s*\S",
        "excluded_scope": r"범위 \(제외[^\)]*\):\s*\S",
        "dod": r"DoD:\s*\S",
        "constraints": r"제약:\s*\S",
        "confirmed": r"사용자 컨펌:\s*완료",
        "input": r"입력:\s*\S",
        "output": r"출력:\s*\S",
        "success_metrics": r"성공 지표:\s*\S",
        "failure_policy": r"실패 정책:\s*\S",
        "affected_files": r"영향받는 파일:\s*\S",
        "red": r"RED\s*:\s*\S",
        "green": r"GREEN\s*:\s*\S",
        "verify": r"VERIFY\s*:\s*\S",
    }
    return [
        name
        for name, pattern in required_patterns.items()
        if not re.search(pattern, sample)
    ]


def test_plan_skill_paths_are_identical():
    texts = _collect_plan_skill_texts(
        root=ROOT,
        required_paths=REQUIRED_PLAN_SKILL_PATHS,
        optional_paths=OPTIONAL_PLAN_SKILL_PATHS,
    )
    canonical = texts[".agents/skills/omc-plan/SKILL.md"]
    mismatched = [name for name, text in texts.items() if text != canonical]
    assert not mismatched, f"omc-plan skill copies differ: {mismatched}"


def test_ignored_live_agent_plan_path_is_optional(tmp_path: Path):
    canonical = tmp_path / ".agents" / "skills" / "omc-plan" / "SKILL.md"
    template_codex = tmp_path / "templates" / ".agents" / "skills" / "omc-plan" / "SKILL.md"
    template_agent = tmp_path / "templates" / ".agent" / "skills" / "omc-plan" / "SKILL.md"
    ignored_live_agent = tmp_path / ".agent" / "skills" / "omc-plan" / "SKILL.md"

    for path in (canonical, template_codex, template_agent):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("same", encoding="utf-8")

    texts = _collect_plan_skill_texts(
        root=tmp_path,
        required_paths=(canonical, template_codex, template_agent),
        optional_paths=(ignored_live_agent,),
    )
    assert texts == {
        ".agents/skills/omc-plan/SKILL.md": "same",
        "templates/.agents/skills/omc-plan/SKILL.md": "same",
        "templates/.agent/skills/omc-plan/SKILL.md": "same",
    }


def test_plan_skill_stays_short_enough_to_scan():
    text = _read(REQUIRED_PLAN_SKILL_PATHS[0])
    non_empty_lines = [line for line in text.splitlines() if line.strip()]
    assert len(non_empty_lines) <= MAX_NON_EMPTY_LINES, (
        f"omc-plan has {len(non_empty_lines)} non-empty lines"
    )


def test_plan_skill_preserves_required_execution_order():
    text = _read(REQUIRED_PLAN_SKILL_PATHS[0])
    cursor = -1
    missing_or_reordered: list[str] = []

    for marker in REQUIRED_SEQUENCE:
        next_pos = text.find(marker, cursor + 1)
        if next_pos == -1:
            missing_or_reordered.append(marker)
        else:
            cursor = next_pos

    assert not missing_or_reordered, f"missing or reordered markers: {missing_or_reordered}"


def test_plan_skill_explains_visible_vs_implicit_steps():
    text = _read(REQUIRED_PLAN_SKILL_PATHS[0])
    missing = [marker for marker in REQUIRED_FOCUS_MARKERS if marker not in text]
    assert not missing, f"missing focus markers: {missing}"


def test_valid_plan_output_fixture_has_required_structure():
    assert _validate_plan_output(VALID_PLAN_SAMPLE) == []


def test_invalid_plan_output_fixture_exposes_missing_structure():
    missing = _validate_plan_output(INVALID_PLAN_SAMPLE)
    assert {"excluded_scope", "confirmed", "verify"}.issubset(set(missing))
