"""
omc-benchmark skill contract regression tests.

Benchmark is a strategy skill. It must avoid inventing competitors or current
product state, label evidence quality, and stop before implementation.
"""
from __future__ import annotations

import re
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
MAX_NON_EMPTY_LINES = 42

REQUIRED_BENCHMARK_SKILL_PATHS = [
    ROOT / ".agents" / "skills" / "omc-benchmark" / "SKILL.md",
    ROOT / "templates" / ".agents" / "skills" / "omc-benchmark" / "SKILL.md",
    ROOT / "templates" / ".agent" / "skills" / "omc-benchmark" / "SKILL.md",
]
OPTIONAL_BENCHMARK_SKILL_PATHS = [
    ROOT / ".agent" / "skills" / "omc-benchmark" / "SKILL.md",
]

REQUIRED_SEQUENCE = [
    "python3 scripts/omc.py state status --target .",
    "분석 기능",
    "비교 대상",
    "비교 관점",
    "우리 현재 수준 근거",
    "비교 대상 미지정",
    "후보 3개",
    "사용자 선택",
    "임의 확정 금지",
    "검증 상태",
    "미검증",
    "직접 검증됨",
    "공식 출처 확인됨",
    "갭 분석",
    "최소 3개",
    "1등 수준",
    "우리 수준",
    "갭 판정",
    "갭 크기",
    "근거 수준",
    "높음 / 중간 / 낮음",
    "직접 검증 필요",
    "차별화 포인트",
    "우선순위 TOP 1",
    "낮음 다수",
    "실제 제품 검증",
    "$omc-office-hours",
    "사용자 채택 의사",
    "$omc-plan",
    "자동 구현 진입 금지",
]

REQUIRED_BEHAVIOR_MARKERS = [
    "세계 1등",
    "N/A",
    "현재 제품 상태 근거 없음",
    "사용자가 직접 확정",
    "주추천 1개",
    "사용자 선택 대기",
    "선택지",
    "차별화 가설",
    "근거 충분",
    "TOP 1 확정",
    "사용자 채택 의사",
]

REQUIRED_FOCUS_MARKERS = [
    "사용자에게 보여줄 것",
    "시스템이 암묵적으로 처리",
]

VALID_BENCHMARK_SAMPLE = """
BENCHMARK 설정
분석 기능: 주문 관리 화면
비교 대상: Shopify
비교 관점: UX 흐름
우리 현재 수준 근거: README와 사용자 설명
검증 상태: 미검증
갭 분석 — 최소 3개
항목 1: 필터
  1등 수준: 고급 필터
  우리 수준: 기본 필터
  갭 판정: 열위
  갭 크기: 중간
  근거 수준: 낮음 — 직접 검증 필요
항목 2: 일괄 처리
  1등 수준: 지원
  우리 수준: N/A — 현재 제품 상태 근거 없음
  갭 판정: N/A
  갭 크기: N/A
  근거 수준: 낮음 — 직접 검증 필요
항목 3: 모바일
  1등 수준: 최적화
  우리 수준: 미확인
  갭 판정: N/A
  갭 크기: N/A
  근거 수준: 낮음 — 직접 검증 필요
차별화 포인트: 도메인 특화 알림
우선순위 TOP 1: 필터
다음 액션: 낮음 다수 → 실제 제품 검증
"""

INVALID_BENCHMARK_SAMPLE = """
BENCHMARK RESULT
비교 대상: 세계 1등 아무거나
갭 분석: 우리가 뒤처짐
다음 액션: 바로 구현
"""


def _read(path: Path) -> str:
    assert path.exists(), f"missing benchmark skill path: {path.relative_to(ROOT)}"
    return path.read_text(encoding="utf-8")


def _collect_benchmark_skill_texts(
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


def _validate_benchmark_output(sample: str) -> list[str]:
    required_patterns = {
        "setup": r"분석 기능:.*비교 대상:.*비교 관점:.*우리 현재 수준 근거:",
        "verification": r"검증 상태:\s*(미검증|직접 검증됨|공식 출처 확인됨)",
        "gaps": r"갭 분석.*항목 1:.*항목 2:.*항목 3:",
        "evidence": r"근거 수준:\s*(높음|중간|낮음).*직접 검증 필요",
        "differentiation": r"차별화 포인트:\s*\S",
        "priority": r"우선순위 TOP 1:\s*\S",
        "next_action": r"다음 액션:.*(실제 제품 검증|\$omc-office-hours|\$omc-plan)",
    }
    return [
        name
        for name, pattern in required_patterns.items()
        if not re.search(pattern, sample, re.S)
    ]


def _stale_guidance_line(text: str) -> str:
    for line in text.splitlines():
        if "stale" in line:
            return line
    raise AssertionError("missing stale guidance line")


def test_benchmark_skill_paths_are_identical():
    texts = _collect_benchmark_skill_texts(
        root=ROOT,
        required_paths=REQUIRED_BENCHMARK_SKILL_PATHS,
        optional_paths=OPTIONAL_BENCHMARK_SKILL_PATHS,
    )
    canonical = texts[".agents/skills/omc-benchmark/SKILL.md"]
    mismatched = [name for name, text in texts.items() if text != canonical]
    assert not mismatched, f"omc-benchmark skill copies differ: {mismatched}"


def test_benchmark_skill_stays_short_enough_to_scan():
    text = _read(REQUIRED_BENCHMARK_SKILL_PATHS[0])
    non_empty_lines = [line for line in text.splitlines() if line.strip()]
    assert len(non_empty_lines) <= MAX_NON_EMPTY_LINES, (
        f"omc-benchmark has {len(non_empty_lines)} non-empty lines"
    )


def test_benchmark_skill_keeps_stale_guidance_compact():
    stale_line = _stale_guidance_line(_read(REQUIRED_BENCHMARK_SKILL_PATHS[0]))
    for marker in ("OMC 세션", "stale", "사용자 요청", "N/A — 이유"):
        assert marker in stale_line, f"missing stale marker: {marker}"
    assert len(stale_line) <= 58, "stale guidance should stay compact"


def test_benchmark_skill_preserves_required_execution_order():
    text = _read(REQUIRED_BENCHMARK_SKILL_PATHS[0])
    cursor = -1
    missing_or_reordered: list[str] = []

    for marker in REQUIRED_SEQUENCE:
        next_pos = text.find(marker, cursor + 1)
        if next_pos == -1:
            missing_or_reordered.append(marker)
        else:
            cursor = next_pos

    assert not missing_or_reordered, f"missing or reordered markers: {missing_or_reordered}"


def test_benchmark_skill_preserves_required_behavior_markers():
    text = _read(REQUIRED_BENCHMARK_SKILL_PATHS[0])
    missing = [marker for marker in REQUIRED_BEHAVIOR_MARKERS if marker not in text]
    assert not missing, f"missing behavior markers: {missing}"


def test_benchmark_skill_explains_visible_vs_implicit_work():
    text = _read(REQUIRED_BENCHMARK_SKILL_PATHS[0])
    missing = [marker for marker in REQUIRED_FOCUS_MARKERS if marker not in text]
    assert not missing, f"missing focus markers: {missing}"


def test_benchmark_skill_does_not_force_implementation():
    text = _read(REQUIRED_BENCHMARK_SKILL_PATHS[0])
    forbidden = [r"\$omc-task", r"구현 시작", r"바로 구현"]
    found = [pattern for pattern in forbidden if re.search(pattern, text)]
    assert not found, f"benchmark must stop before implementation, found: {found}"


def test_valid_benchmark_output_fixture_has_required_structure():
    assert _validate_benchmark_output(VALID_BENCHMARK_SAMPLE) == []


def test_invalid_benchmark_output_fixture_exposes_weak_strategy_gate():
    failures = _validate_benchmark_output(INVALID_BENCHMARK_SAMPLE)
    assert {
        "setup",
        "verification",
        "gaps",
        "evidence",
        "differentiation",
        "priority",
        "next_action",
    }.issubset(set(failures))
