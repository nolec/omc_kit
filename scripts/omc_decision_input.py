#!/usr/bin/env python3
from __future__ import annotations


def build_next_priority_input(
    *,
    blocker: str,
    observed_reason_signals_present: bool,
    baseline_comparison_status: str,
    extension: dict[str, object] | None = None,
) -> dict[str, object]:
    return {
        "core": {
            "blocker": blocker,
            "observed_reason_signals_present": observed_reason_signals_present,
            "baseline_comparison_status": baseline_comparison_status,
        },
        "extension": dict(extension or {}),
    }


def build_next_priority_surface_input(
    *,
    blocker: str,
    observed_reason_signals_present: bool,
    baseline_comparison_status: str,
    source_surface: str,
    extension: dict[str, object] | None = None,
) -> dict[str, object]:
    merged_extension = {"source_surface": source_surface}
    if extension:
        merged_extension.update(dict(extension))
    return build_next_priority_input(
        blocker=blocker,
        observed_reason_signals_present=observed_reason_signals_present,
        baseline_comparison_status=baseline_comparison_status,
        extension=merged_extension,
    )


def resolve_next_priority(
    *,
    blocker: str,
    observed_reason_signals_present: bool,
    baseline_comparison_status: str,
) -> tuple[str, str]:
    if blocker == "insufficient_observed_samples":
        return "collect_more_observed_runs", "need more observed samples"
    if blocker == "insufficient_same_surface_evidence":
        return "add_same_surface_observed_evidence", "need more same-surface evidence"
    if blocker == "insufficient_policy_pairs":
        return "expand_policy_pair_coverage", "need more policy pair coverage"
    if blocker == "baseline_comparison_not_ready":
        return "stabilize_baseline_comparison_inputs", "baseline comparison input is not ready"
    if baseline_comparison_status == "ready" and observed_reason_signals_present:
        return (
            "validate_operator_bottlenecks_from_observed_runs",
            "reason signals observed in ready dataset",
        )
    return "maintain_policy_comparison_confidence", "readiness requirements are currently satisfied"


def resolve_next_priority_from_input(decision_input: dict[str, object]) -> tuple[str, str]:
    core = decision_input.get("core")
    if not isinstance(core, dict):
        core = decision_input
    return resolve_next_priority(
        blocker=str(core.get("blocker") or ""),
        observed_reason_signals_present=bool(core.get("observed_reason_signals_present")),
        baseline_comparison_status=str(core.get("baseline_comparison_status") or ""),
    )


def build_status_followup_input(
    *,
    request_kind: str,
    returncode: int | None = None,
    extension: dict[str, object] | None = None,
) -> dict[str, object]:
    return {
        "core": {
            "request_kind": str(request_kind or "build"),
            "returncode": returncode,
        },
        "extension": dict(extension or {}),
    }


def resolve_status_followup_from_input(decision_input: dict[str, object]) -> tuple[str, str]:
    core = decision_input.get("core")
    if not isinstance(core, dict):
        core = decision_input

    returncode = core.get("returncode")
    if returncode is not None:
        reason = f"exit_code={returncode}"
    else:
        reason = "failed"

    next_steps = {
        "debug": "실패 지점 로그와 재현 경로를 먼저 다시 확인",
        "review": "검토 범위와 실패한 체크 포인트를 먼저 다시 고정",
        "design": "가정한 계약과 실제 구현/입력 조건 차이를 먼저 점검",
        "domain": "비용 가정, 검증 구간, 실행 금지 조건부터 다시 점검",
        "research": "입력 범위와 필요한 근거 형식을 먼저 다시 고정",
        "build": "실패 지점과 완료 조건 차이를 먼저 다시 확인",
    }
    request_kind = str(core.get("request_kind") or "build")
    return reason, next_steps.get(request_kind, next_steps["build"])


def build_plan_followup_input(
    *,
    request_text: str,
    extension: dict[str, object] | None = None,
) -> dict[str, object]:
    request_text = str(request_text or "").strip()
    lowered = request_text.lower()
    return {
        "core": {
            "request_text": request_text,
            "contains_question": "?" in request_text,
            "contains_plan_wording": "plan" in lowered or "플랜" in request_text,
            "roadmap_sync_intent_present": (
                "로드맵" in request_text
                and any(keyword in request_text for keyword in ("최신화", "싱크", "동기화", "업데이트"))
            ),
            "progress_check_intent_present": any(
                keyword in request_text for keyword in ("어디까지", "진행된", "진행 상태", "작업 체크")
            ),
            "explanation_request_present": any(
                keyword in request_text
                for keyword in ("왜", "맞아", "해야하나", "해도 되나", "추천", "정리해줘", "쪼개서")
            ),
        },
        "extension": dict(extension or {}),
    }


def resolve_plan_followup_from_input(decision_input: dict[str, object]) -> tuple[str, str]:
    core = decision_input.get("core")
    if not isinstance(core, dict):
        core = decision_input

    roadmap_sync_intent_present = bool(core.get("roadmap_sync_intent_present"))
    progress_check_intent_present = bool(core.get("progress_check_intent_present"))
    contains_question = bool(core.get("contains_question"))
    contains_plan_wording = bool(core.get("contains_plan_wording"))
    explanation_request_present = bool(core.get("explanation_request_present"))

    if roadmap_sync_intent_present:
        return "$omc-plan", "roadmap sync should align before the next implementation step"
    if progress_check_intent_present and not contains_question:
        return "$omc-plan", "progress check should refresh plan state before implementation"
    if contains_plan_wording or contains_question or explanation_request_present:
        return "사용자 선택 대기", "plan wording or explanation intent should pause for user selection"
    return "$omc-task", "implementation progression is still the default follow-up"
