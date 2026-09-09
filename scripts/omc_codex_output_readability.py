"""Deterministic readability checks for Codex OMC stage output."""
from __future__ import annotations

from collections import Counter
import re


LINE_BUDGETS = {"plan": 20, "task": 20, "review": 24}
ORDERED_PREFIXES = {
    "plan": ("결정:", "범위:", "태스크:", "승인 요청:"),
    "task": ("결과:", "변경:", "검증:", "다음 행동:"),
    "review": ("[", "검증:", "판정:", "다음 행동:"),
}
BLOCKED_ORDERED_PREFIXES = {
    "plan": ("결정:", "범위:", "decisions_required:", "승인 요청:"),
    "task": ("결과:", "원인:", "영향:", "수정 방향:", "다음 행동:"),
    "review": ("[", "원인:", "영향:", "수정 방향:", "검증:", "판정:", "다음 행동:"),
}
CONCLUSION_PREFIXES = {"plan": "결정:", "task": "결과:", "review": "["}
ACTION_PREFIXES = {"plan": "승인 요청:", "task": "다음 행동:", "review": "다음 행동:"}
OUTCOMES = {"ready", "blocked"}


def _nonempty_lines(output: str) -> list[str]:
    return [line.strip() for line in output.splitlines() if line.strip()]


def _content(line: str) -> str:
    return re.sub(r"^(?:(?:[-*+>]|→|#{1,6})\s+)+", "", line.strip())


def visible_lines(output: str) -> list[str]:
    """Return human-facing lines, excluding the complete two-line machine footer."""
    lines = _nonempty_lines(output)
    if (
        len(lines) >= 2
        and lines[-2].startswith("<!-- OMC_OUTPUT: ")
        and lines[-2].endswith(" -->")
        and lines[-1].startswith("VERDICT: ")
    ):
        return lines[:-2]
    return lines


def measure_output(output: str) -> dict[str, int]:
    lines = visible_lines(output)
    counts = Counter(_content(line) for line in lines)
    return {
        "visible_line_count": len(lines),
        "next_action_count": sum(
            _content(line).startswith(("다음 행동:", "승인 요청:")) for line in lines
        ),
        "duplicate_line_count": sum(count - 1 for count in counts.values() if count > 1),
    }


def _ordered(lines: list[str], prefixes: tuple[str, ...]) -> bool:
    cursor = -1
    for prefix in prefixes:
        try:
            cursor = next(
                i for i in range(cursor + 1, len(lines)) if _content(lines[i]).startswith(prefix)
            )
        except StopIteration:
            return False
    return True


def validate_output(output: str, *, stage: str, outcome: str) -> list[str]:
    if stage not in LINE_BUDGETS:
        return ["unknown_stage"]
    if outcome not in OUTCOMES:
        return ["unknown_outcome"]

    lines = visible_lines(output)
    metrics = measure_output(output)
    errors: list[str] = []
    if metrics["visible_line_count"] > LINE_BUDGETS[stage]:
        errors.append("line_budget_exceeded")
    if metrics["duplicate_line_count"]:
        errors.append("duplicate_visible_line")
    if metrics["next_action_count"] != 1:
        errors.append("next_action_count_invalid")
    if any("사용자 선택 대기" in _content(line) for line in lines):
        errors.append("generic_next_action")
    ordered_prefixes = BLOCKED_ORDERED_PREFIXES[stage] if outcome == "blocked" else ORDERED_PREFIXES[stage]
    if not _ordered(lines, ordered_prefixes):
        errors.append("stage_order_invalid")

    conclusion_prefix = CONCLUSION_PREFIXES[stage]
    if not any(_content(line).startswith(conclusion_prefix) for line in lines[:3]):
        errors.append("conclusion_not_in_first_three_lines")
    if not any(_content(line).startswith(ACTION_PREFIXES[stage]) for line in lines):
        errors.append("stage_action_missing")

    if outcome == "blocked":
        required = (
            ("decisions_required:",)
            if stage == "plan"
            else ("원인:", "영향:", "수정 방향:")
        )
        if any(
            not any(_content(line).startswith(prefix) for line in lines)
            for prefix in required
        ):
            errors.append("failure_detail_missing")
    return errors


def prompt_contract(stage: str) -> str:
    """Return the concise observable-output contract for a Codex pipeline stage."""
    if stage not in LINE_BUDGETS:
        raise ValueError(f"unsupported readability stage: {stage}")
    ready_prefixes = " ".join(f"`{prefix}`" for prefix in ORDERED_PREFIXES[stage])
    blocked_prefixes = " ".join(
        f"`{prefix}`" for prefix in BLOCKED_ORDERED_PREFIXES[stage]
    )
    return (
        f"각 항목 생략 금지. 정상 결과 첫 네 항목의 시작 문자열은 순서대로 {ready_prefixes}입니다. "
        f"차단 결과의 시작 문자열은 순서대로 {blocked_prefixes}입니다. "
        f"전체는 {LINE_BUDGETS[stage]}줄 이하로 작성하세요. "
        "결론은 첫 3줄 안, 동일 문장 반복 없음, 다음 행동은 정확히 1개입니다. "
        "차단 결과는 원인, 영향, 수정 방향을 유지하세요. 내부 상태인 `사용자 선택 대기`는 "
        "직접 쓰지 말고 사용자가 선택할 실제 대상을 요청하세요."
    )
