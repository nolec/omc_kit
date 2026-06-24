"""
omc-qa skill contract regression tests.

QA is a manual verification checklist skill. It must stay scoped to
post-implementation user checks without turning into planning, code review,
or ship gating.
"""
from __future__ import annotations

import re
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
MAX_NON_EMPTY_LINES = 42

REQUIRED_QA_SKILL_PATHS = [
    ROOT / ".agents" / "skills" / "omc-qa" / "SKILL.md",
    ROOT / "templates" / ".agents" / "skills" / "omc-qa" / "SKILL.md",
    ROOT / "templates" / ".agent" / "skills" / "omc-qa" / "SKILL.md",
]
OPTIONAL_QA_SKILL_PATHS = [
    ROOT / ".agent" / "skills" / "omc-qa" / "SKILL.md",
]
REQUIRED_CLAUDE_QA_COMMAND_PATHS = [
    ROOT / ".claude" / "commands" / "qa.md",
    ROOT / "templates" / ".claude" / "commands" / "qa.md",
]
STANDARD_CORE_SEQUENCE_AGENTS = "/plan      →    /task      →    /review    →    /ship"
STANDARD_CORE_SEQUENCE_OVERLAY = "/plan` → `/task` → `/review` → `/ship"

REQUIRED_SEQUENCE = [
    "git diff --stat HEAD",
    "python3 scripts/omc.py state status --target .",
    "변경 기능",
    "영향 화면/흐름",
    "사용자 유형",
    "확인 환경",
    "정상 흐름",
    "예외 흐름",
    "회귀 포인트",
    "우선순위 높은 QA 5개",
    "실행 결과 아님",
    "다음 추천",
]

REQUIRED_BEHAVIOR_MARKERS = [
    "수동 QA 체크리스트",
    "구현 후",
    "diff 또는 plan",
    "입력 부족",
    "고정 QA 생성 불가",
    "omc-plan",
    "omc-review",
    "omc-ship",
    "코드 리뷰를 하지 않습니다",
    "배포 게이트를 대신하지 않습니다",
    "주추천 1개",
    "자동으로 진행하지는 않습니다.",
]

REQUIRED_FOCUS_MARKERS = [
    "사용자에게 보여줄 것",
    "시스템이 암묵적으로 처리",
]

VALID_QA_SAMPLE = """
변경 기능: 쿠폰 적용 UX 개선
영향 화면/흐름: 장바구니 > 쿠폰 입력
사용자 유형: 로그인 구매자
확인 환경: desktop, mobile
정상 흐름:
1. 유효 쿠폰 적용 성공
예외 흐름:
1. 만료 쿠폰 에러 노출
회귀 포인트:
1. 배송비 재계산
우선순위 높은 QA 5개:
1. 모바일 쿠폰 적용
실행 결과 아님: 아직 체크리스트 생성만 완료
추천 다음 스킬: $omc-review
"""

INVALID_QA_SAMPLE = """
체크리스트:
1. 로그인 확인
2. 에러 확인
다음 액션: 알아서
"""


def _read(path: Path) -> str:
    assert path.exists(), f"missing qa skill path: {path.relative_to(ROOT)}"
    return path.read_text(encoding="utf-8")


def _collect_qa_skill_texts(
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


def _validate_qa_output(sample: str) -> list[str]:
    required_patterns = {
        "feature": r"변경 기능:\s*\S",
        "flow": r"영향 화면/흐름:\s*\S",
        "user": r"사용자 유형:\s*\S",
        "env": r"확인 환경:\s*\S",
        "normal": r"정상 흐름:.*1\.",
        "exception": r"예외 흐름:.*1\.",
        "regression": r"회귀 포인트:.*1\.",
        "top5": r"우선순위 높은 QA 5개:.*1\.",
        "not_executed": r"실행 결과 아님:\s*\S",
        "next_skill": r"추천 다음 스킬:\s*\$omc-(review|ship|plan)",
    }
    return [
        name
        for name, pattern in required_patterns.items()
        if not re.search(pattern, sample, re.S)
    ]


def test_qa_skill_paths_are_identical():
    texts = _collect_qa_skill_texts(
        root=ROOT,
        required_paths=REQUIRED_QA_SKILL_PATHS,
        optional_paths=OPTIONAL_QA_SKILL_PATHS,
    )
    canonical = texts[".agents/skills/omc-qa/SKILL.md"]
    mismatched = [name for name, text in texts.items() if text != canonical]
    assert not mismatched, f"omc-qa skill copies differ: {mismatched}"


def test_ignored_live_agent_qa_path_is_optional(tmp_path: Path):
    canonical = tmp_path / ".agents" / "skills" / "omc-qa" / "SKILL.md"
    template_codex = tmp_path / "templates" / ".agents" / "skills" / "omc-qa" / "SKILL.md"
    template_agent = tmp_path / "templates" / ".agent" / "skills" / "omc-qa" / "SKILL.md"
    ignored_live_agent = tmp_path / ".agent" / "skills" / "omc-qa" / "SKILL.md"

    for path in (canonical, template_codex, template_agent):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("same", encoding="utf-8")

    texts = _collect_qa_skill_texts(
        root=tmp_path,
        required_paths=(canonical, template_codex, template_agent),
        optional_paths=(ignored_live_agent,),
    )
    assert texts == {
        ".agents/skills/omc-qa/SKILL.md": "same",
        "templates/.agents/skills/omc-qa/SKILL.md": "same",
        "templates/.agent/skills/omc-qa/SKILL.md": "same",
    }


def test_claude_qa_command_live_and_template_stay_identical():
    texts = {
        path.relative_to(ROOT).as_posix(): _read(path)
        for path in REQUIRED_CLAUDE_QA_COMMAND_PATHS
    }
    canonical = texts["templates/.claude/commands/qa.md"]
    mismatched = [name for name, text in texts.items() if text != canonical]
    assert not mismatched, f"qa Claude command copies differ: {mismatched}"


def test_qa_is_not_reclassified_into_standard_core_pipeline():
    assert STANDARD_CORE_SEQUENCE_AGENTS in _read(ROOT / "AGENTS.md")
    for path in (ROOT / "templates" / "CLAUDE.md", ROOT / "templates" / "GEMINI.md"):
        text = _read(path)
        assert STANDARD_CORE_SEQUENCE_OVERLAY in text, (
            f"standard core sequence drifted in {path.relative_to(ROOT)}"
        )


def test_qa_skill_stays_short_enough_to_scan():
    text = _read(REQUIRED_QA_SKILL_PATHS[0])
    non_empty_lines = [line for line in text.splitlines() if line.strip()]
    assert len(non_empty_lines) <= MAX_NON_EMPTY_LINES, (
        f"omc-qa has {len(non_empty_lines)} non-empty lines"
    )


def test_qa_skill_preserves_required_execution_order():
    text = _read(REQUIRED_QA_SKILL_PATHS[0])
    cursor = -1
    missing_or_reordered: list[str] = []

    for marker in REQUIRED_SEQUENCE:
        next_pos = text.find(marker, cursor + 1)
        if next_pos == -1:
            missing_or_reordered.append(marker)
        else:
            cursor = next_pos

    assert not missing_or_reordered, f"missing or reordered markers: {missing_or_reordered}"


def test_qa_skill_preserves_required_behavior_markers():
    text = _read(REQUIRED_QA_SKILL_PATHS[0])
    missing = [marker for marker in REQUIRED_BEHAVIOR_MARKERS if marker not in text]
    assert not missing, f"missing behavior markers: {missing}"


def test_qa_skill_explains_visible_vs_implicit_work():
    text = _read(REQUIRED_QA_SKILL_PATHS[0])
    missing = [marker for marker in REQUIRED_FOCUS_MARKERS if marker not in text]
    assert not missing, f"missing focus markers: {missing}"


def test_valid_qa_output_fixture_has_required_structure():
    assert _validate_qa_output(VALID_QA_SAMPLE) == []


def test_invalid_qa_output_fixture_exposes_weak_checklist():
    failures = _validate_qa_output(INVALID_QA_SAMPLE)
    assert {"feature", "flow", "user", "env", "normal", "exception", "regression", "top5", "not_executed", "next_skill"}.issubset(set(failures))
