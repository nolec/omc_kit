"""
omc-review skill contract regression tests.

The review skill can be short, but it must still preserve scope collection,
evidence, severity buckets, and the verdict contract.
"""
from __future__ import annotations

from pathlib import Path
import re


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

REQUIRED_DECISION_OUTPUT_MARKERS = [
    "decision",
    "risk",
    "next_action",
    "판정 결과",
    "리스크 요약",
    "다음 스킬 1개",
]

REQUIRED_DECISION_TABLE_MARKERS = [
    "공통 결정표",
    "stage",
    "outcome",
    "user_selection_needed",
    "ship_intent_explicit",
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

REQUIRED_COMPLETION_MARKERS = [
    "출력이 길어져도",
    "검증 커맨드",
    "판정",
    "VERDICT",
    "다음 추천",
    "생략하지 않습니다.",
]

REQUIRED_REVISE_MARKERS = [
    "REVISE/BLOCK",
    "REVISE/BLOCK면 수정 방향 포함",
]

VALID_REVISE_REVIEW_RECOMMENDATION_SAMPLE = """
판정: REVISE
VERDICT: REVISE
다음 추천:
- 주추천 1개, 우선순위: REVISE/BLOCK면 `$omc-task`
- APPROVE/APPROVE WITH NOTES + 배포 준비 명시 + ship_intent_explicit=yes면 `$omc-ship`
- APPROVE/APPROVE WITH NOTES + 배포 준비 미명시 또는 user_selection_needed=yes면 사용자 선택 대기
- 자동으로 진행하지는 않습니다.
"""

VALID_REVIEW_DECISION_OUTPUT_SAMPLE = """
decision: REVISE / APPROVE (판정 결과)
risk: HIGH / MED / LOW (리스크 요약)
next_action: $omc-task / $omc-ship / 사용자 선택 대기 (다음 스킬 1개)
"""

VALID_REVIEW_DECISION_TABLE_SAMPLE = """
공통 결정표:
- stage: review
- outcome: revise / done
- user_selection_needed: yes / no
- ship_intent_explicit: yes / no
"""


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


def _extract_primary_recommendation(sample: str) -> str:
    in_section = False
    for raw_line in sample.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        if line.startswith("다음 추천"):
            in_section = True
            continue
        if not in_section or not line.startswith("-"):
            continue
        if "사용자 선택 대기" in line:
            return "사용자 선택 대기"
        match = re.search(r"(\$omc-[a-z-]+)", line)
        if match:
            return match.group(1)
    raise AssertionError("primary recommendation not found")


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


def test_review_skill_declares_decision_risk_next_action_contract():
    text = _read(REQUIRED_REVIEW_SKILL_PATHS[0])
    missing = [marker for marker in REQUIRED_DECISION_OUTPUT_MARKERS if marker not in text]
    assert not missing, f"missing review decision markers: {missing}"


def test_review_skill_declares_common_decision_table_axes():
    text = _read(REQUIRED_REVIEW_SKILL_PATHS[0])
    missing = [marker for marker in REQUIRED_DECISION_TABLE_MARKERS if marker not in text]
    assert not missing, f"missing review decision table markers: {missing}"


def test_review_skill_declares_non_negotiable_review_contract():
    text = _read(REQUIRED_REVIEW_SKILL_PATHS[0])
    missing = [marker for marker in REQUIRED_SAFETY_MARKERS if marker not in text]
    assert not missing, f"missing review safety markers: {missing}"


def test_review_skill_forces_completion_section_even_for_long_output():
    text = _read(REQUIRED_REVIEW_SKILL_PATHS[0])
    missing = [marker for marker in REQUIRED_COMPLETION_MARKERS if marker not in text]
    assert not missing, f"missing completion markers: {missing}"


def test_review_skill_requires_fix_direction_for_revise_and_block():
    text = _read(REQUIRED_REVIEW_SKILL_PATHS[0])
    missing = [marker for marker in REQUIRED_REVISE_MARKERS if marker not in text]
    assert not missing, f"missing revise markers: {missing}"


def test_review_skill_recommendations_match_verdict_buckets():
    text = _read(REQUIRED_REVIEW_SKILL_PATHS[0])
    required_markers = [
        "다음 추천",
        "우선순위",
        "REVISE/BLOCK",
        "$omc-task",
        "APPROVE/APPROVE WITH NOTES",
        "배포 준비 명시",
        "$omc-ship",
        "사용자 선택 대기",
        "배포 준비 미명시",
        "자동으로 진행하지는 않습니다.",
    ]
    missing = [marker for marker in required_markers if marker not in text]
    assert not missing, f"missing review recommendation markers: {missing}"


def test_review_skill_next_recommendation_lines_each_resolve_to_one_action():
    text = _read(REQUIRED_REVIEW_SKILL_PATHS[0])
    lines = []
    in_section = False
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if line.startswith("## 다음 추천"):
            in_section = True
            continue
        if in_section and line.startswith("## "):
            break
        if in_section and (
            "REVISE/BLOCK" in line or "APPROVE/APPROVE WITH NOTES" in line
        ):
            lines.append(line)
    assert lines, "expected review recommendation lines"
    for line in lines:
        actions = re.findall(r"(\$omc-[a-z-]+|사용자 선택 대기)", line)
        assert len(actions) == 1, f"line must resolve to one action: {line}"


def test_review_recommendation_fixture_routes_revise_to_task():
    assert _extract_primary_recommendation(VALID_REVISE_REVIEW_RECOMMENDATION_SAMPLE) == "$omc-task"


def test_valid_review_decision_output_fixture_declares_review_specific_meaning():
    for marker in REQUIRED_DECISION_OUTPUT_MARKERS:
        assert marker in VALID_REVIEW_DECISION_OUTPUT_SAMPLE


def test_valid_review_decision_table_fixture_declares_common_axes():
    for marker in REQUIRED_DECISION_TABLE_MARKERS:
        assert marker in VALID_REVIEW_DECISION_TABLE_SAMPLE


def test_review_recommendation_fixture_keeps_single_next_action_per_state():
    sample = """
다음 추천:
- REVISE/BLOCK면 `$omc-task`
- APPROVE/APPROVE WITH NOTES + 배포 준비 명시 + ship_intent_explicit=yes면 `$omc-ship`
- APPROVE/APPROVE WITH NOTES + 배포 준비 미명시 또는 user_selection_needed=yes면 사용자 선택 대기
"""
    lines = [line.strip() for line in sample.splitlines() if line.strip().startswith("-")]
    for line in lines:
        actions = re.findall(r"(\$omc-[a-z-]+|사용자 선택 대기)", line)
        assert len(actions) == 1, f"line must resolve to one action: {line}"


def test_review_skill_prioritizes_current_bottleneck_over_default_pipeline():
    text = _read(REQUIRED_REVIEW_SKILL_PATHS[0])
    for marker in [
        "현재 병목 > 기본 파이프라인",
        "REVISE/BLOCK면 `$omc-task`",
        "배포 준비 명시 + ship_intent_explicit=yes면 `$omc-ship`",
        "배포 준비 미명시 또는 user_selection_needed=yes면 사용자 선택 대기",
    ]:
        assert marker in text
