"""
omc-plan skill contract regression tests.

The plan skill is upstream of implementation, so shortening it must preserve
requirements quality, explicit user confirmation, and TDD task structure.
"""
from __future__ import annotations

import re
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
MAX_NON_EMPTY_LINES = 40

REQUIRED_PLAN_SKILL_PATHS = [
    ROOT / ".agents" / "skills" / "omc-plan" / "SKILL.md",
    ROOT / "templates" / ".agents" / "skills" / "omc-plan" / "SKILL.md",
    ROOT / "templates" / ".agent" / "skills" / "omc-plan" / "SKILL.md",
]
OPTIONAL_PLAN_SKILL_PATHS = [
    ROOT / ".agent" / "skills" / "omc-plan" / "SKILL.md",
]

REQUIRED_SEQUENCE = [
    "python3 scripts/omc.py state sync-session --target . --mode autopilot --title \"omc-plan\" --request \"<현재 작업 한 줄 요약>\" --roles analysis",
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
    "plan full",
    "plan lite",
    "lite",
    "full 재계획",
    "RED",
    "GREEN",
    "VERIFY",
    "사용자 컨펌 완료 전",
    "python3 scripts/omc.py state confirm --target .",
    "$omc-task",
    "다음 추천",
    "주추천 1개",
    "자동으로 진행하지는 않습니다.",
]

REQUIRED_FOCUS_MARKERS = [
    "사용자에게 보여줄 단계",
    "시스템이 암묵적으로 처리",
]

REQUIRED_DECISION_OUTPUT_MARKERS = [
    "decision",
    "risk",
    "next_action",
    "진행 가능 여부",
    "변경 위험도",
    "다음 스킬 1개",
]

REQUIRED_DECISION_TABLE_MARKERS = [
    "공통 결정표",
    "stage",
    "outcome",
    "user_selection_needed",
]

REQUIRED_RISK_CONCEPTS = [
    ("새 파일", "신규 파일"),
    ("API", "시그니처"),
    ("3개 이상", "세 개 이상"),
    ("검증 명령", "VERIFY"),
    ("범위", "불명확"),
    ("애매", "full"),
    ("2개 이하", "1~2"),
    ("분리", "범위"),
]

REQUIRED_HIGH_RISK_RECOMMENDATION_MARKERS = [
    "고위험",
    "$omc-critique",
    "고위험이면 먼저",
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

VALID_LITE_PLAN_SAMPLE = """
목표: 문구 한 줄 수정이 테스트와 함께 반영된다.
범위 (포함): 스킬 문구와 단일 계약 테스트 갱신
범위 (제외): 새 파일 생성, 다른 스킬 수정
DoD: lite 계획으로 1개 태스크만 제시되고 검증까지 통과한다.
제약: 기존 API/signature는 바꾸지 않는다.
사용자 컨펌: 완료
입력: 기존 omc-plan 스킬 텍스트
출력: 더 짧은 lite 계획
성공 지표: 작은 작업에서 plan 출력이 더 짧다.
실패 정책: 조건이 애매하면 full 재계획으로 승격한다.
영향받는 파일: .agents/skills/omc-plan/SKILL.md
태스크 1: lite 문구 정리
  RED    : scripts/test_omc_plan_skill_contract.py -k lite
  GREEN  : .agents/skills/omc-plan/SKILL.md 최소 수정
  VERIFY : python3 -m pytest -q scripts/test_omc_plan_skill_contract.py -k lite
"""

INVALID_LITE_PLAN_SAMPLE = """
목표: 작은 수정
범위 (포함): 스킬
범위 (제외): 없음
DoD: 된다
제약: 없음
사용자 컨펌: 완료
입력: 기존 파일
출력: 수정
성공 지표: 짧다
실패 정책: 실패하면 수정
영향받는 파일: skill.md
태스크 1: 구현
  RED    : test
  GREEN  : skill
  VERIFY : pytest
태스크 2: 추가 구현
  RED    : test2
  GREEN  : skill2
  VERIFY : pytest2
태스크 3: 과한 구현
  RED    : test3
  GREEN  : skill3
  VERIFY : pytest3
"""

INVALID_PLAN_SAMPLE = """
목표: 로그인 개선
범위 (포함): 로그인
DoD: 잘 된다
태스크 1: 구현
  RED    : 테스트
  GREEN  : 코드
"""

VALID_HIGH_RISK_PLAN_RECOMMENDATION_SAMPLE = """
다음 추천:
- 주추천 1개만 제시, 우선순위: 새 파일/API 변경/3개 이상 파일 같은 고위험이면 먼저 `$omc-critique`
- outcome=ready + user_selection_needed=no + 범위 고정 + 컨펌 완료면 `$omc-task`
- outcome=unresolved + risk=high면 `$omc-critique`
- outcome=unresolved + risk=low + user_selection_needed=yes면 사용자 선택 대기
- 사용자가 설계만 확인 중이거나 다음 단계를 아직 고르지 않음 → 사용자 선택 대기
- 자동으로 진행하지는 않습니다.
"""

VALID_PLAN_DECISION_OUTPUT_SAMPLE = """
decision: PROCEED / HOLD (진행 가능 여부)
risk: LOW / MED / HIGH (변경 위험도)
next_action: $omc-task / $omc-critique / 사용자 선택 대기 (다음 스킬 1개)
"""

VALID_PLAN_DECISION_TABLE_SAMPLE = """
공통 결정표:
- stage: plan
- outcome: unresolved / ready
- user_selection_needed: yes / no
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


def _extract_task_count(sample: str) -> int:
    return len(re.findall(r"^태스크\s+\d+:", sample, flags=re.MULTILINE))


def _missing_concepts(text: str, concepts: list[tuple[str, ...]]) -> list[tuple[str, ...]]:
    return [concept for concept in concepts if not any(token in text for token in concept)]


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


def test_plan_skill_recommendations_are_state_based_and_guarded():
    text = _read(REQUIRED_PLAN_SKILL_PATHS[0])
    required_markers = [
        "다음 추천",
        "우선순위",
        "범위 고정 + 컨펌 완료",
        "$omc-task",
        "범위 불명확",
        "$omc-critique",
        "$omc-office-hours",
        "사용자 선택 대기",
        "자동으로 진행하지는 않습니다.",
    ]
    missing = [marker for marker in required_markers if marker not in text]
    assert not missing, f"missing plan recommendation markers: {missing}"


def test_plan_skill_next_recommendation_lines_each_resolve_to_one_action():
    text = _read(REQUIRED_PLAN_SKILL_PATHS[0])
    lines = []
    in_section = False
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if line.startswith("## 다음 추천"):
            in_section = True
            continue
        if in_section and line.startswith("## "):
            break
        if in_section and line.startswith("- outcome="):
            lines.append(line)
    assert lines, "expected outcome-based recommendation lines"
    for line in lines:
        actions = re.findall(r"(\$omc-[a-z-]+|사용자 선택 대기)", line)
        assert len(actions) == 1, f"line must resolve to one action: {line}"


def test_plan_skill_recommends_critique_for_high_risk_even_when_scope_is_clear():
    text = _read(REQUIRED_PLAN_SKILL_PATHS[0])
    missing = [marker for marker in REQUIRED_HIGH_RISK_RECOMMENDATION_MARKERS if marker not in text]
    assert not missing, f"missing high-risk recommendation markers: {missing}"


def test_plan_recommendation_fixture_prefers_critique_for_high_risk():
    assert _extract_primary_recommendation(VALID_HIGH_RISK_PLAN_RECOMMENDATION_SAMPLE) == "$omc-critique"


def test_plan_skill_explains_visible_vs_implicit_steps():
    text = _read(REQUIRED_PLAN_SKILL_PATHS[0])
    missing = [marker for marker in REQUIRED_FOCUS_MARKERS if marker not in text]
    assert not missing, f"missing focus markers: {missing}"


def test_plan_skill_declares_decision_risk_next_action_contract():
    text = _read(REQUIRED_PLAN_SKILL_PATHS[0])
    missing = [marker for marker in REQUIRED_DECISION_OUTPUT_MARKERS if marker not in text]
    assert not missing, f"missing decision output markers: {missing}"


def test_plan_skill_declares_common_decision_table_axes():
    text = _read(REQUIRED_PLAN_SKILL_PATHS[0])
    missing = [marker for marker in REQUIRED_DECISION_TABLE_MARKERS if marker not in text]
    assert not missing, f"missing decision table markers: {missing}"


def test_plan_skill_declares_lite_full_risk_concepts():
    text = _read(REQUIRED_PLAN_SKILL_PATHS[0])
    missing = _missing_concepts(text, REQUIRED_RISK_CONCEPTS)
    assert not missing, f"missing lite/full risk concepts: {missing}"


def test_valid_plan_output_fixture_has_required_structure():
    assert _validate_plan_output(VALID_PLAN_SAMPLE) == []


def test_valid_lite_plan_output_fixture_limits_task_count():
    assert _validate_plan_output(VALID_LITE_PLAN_SAMPLE) == []
    assert _extract_task_count(VALID_LITE_PLAN_SAMPLE) <= 2


def test_valid_plan_decision_output_fixture_declares_plan_specific_meaning():
    for marker in REQUIRED_DECISION_OUTPUT_MARKERS:
        assert marker in VALID_PLAN_DECISION_OUTPUT_SAMPLE


def test_valid_plan_decision_table_fixture_declares_common_axes():
    for marker in REQUIRED_DECISION_TABLE_MARKERS:
        assert marker in VALID_PLAN_DECISION_TABLE_SAMPLE


def test_plan_recommendation_fixture_keeps_single_next_action_per_state():
    sample = """
다음 추천:
- outcome=ready + user_selection_needed=no + 범위 고정 + 컨펌 완료면 `$omc-task`
- outcome=unresolved + risk=high면 `$omc-critique`
- outcome=unresolved + risk=low + user_selection_needed=yes면 사용자 선택 대기
"""
    lines = [line.strip() for line in sample.splitlines() if line.strip().startswith("-")]
    for line in lines:
        actions = re.findall(r"(\$omc-[a-z-]+|사용자 선택 대기)", line)
        assert len(actions) == 1, f"line must resolve to one action: {line}"


def test_plan_skill_prioritizes_current_bottleneck_over_default_pipeline():
    text = _read(REQUIRED_PLAN_SKILL_PATHS[0])
    for marker in [
        "현재 병목 > 기본 파이프라인",
        "고위험이면 먼저 `$omc-critique`",
        "outcome=ready + user_selection_needed=no + 범위 고정 + 컨펌 완료면 `$omc-task`",
        "사용자가 설계만 확인 중이거나 다음 단계를 아직 고르지 않음 → 사용자 선택 대기",
    ]:
        assert marker in text


def test_invalid_plan_output_fixture_exposes_missing_structure():
    missing = _validate_plan_output(INVALID_PLAN_SAMPLE)
    assert {"excluded_scope", "confirmed", "verify"}.issubset(set(missing))


def test_invalid_lite_plan_fixture_rejects_more_than_two_tasks():
    assert _extract_task_count(INVALID_LITE_PLAN_SAMPLE) > 2
