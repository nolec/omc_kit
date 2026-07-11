"""Read-only orchestration plan generator.

This module creates a stage graph and model recommendations. It never runs an
executor; execution remains an explicit follow-up concern for autopilot.
"""

from __future__ import annotations

import argparse
import json
import re
from pathlib import Path

from omc_exec import resolve_task_routing


_COMPLEX_MARKERS = (
    "api",
    "결제",
    "마이그레이션",
    "교체",
    "프론트",
    "백엔드",
    "테스트",
    "여러",
    "통합",
    "위임",
    "역할",
    "autopilot",
    "실행 기록",
)
_HIGH_RISK_MARKERS = (
    "결제",
    "삭제",
    "마이그레이션",
    "권한",
    "개인정보",
    "배포",
    "setup --force",
    "설치",
)


def _normalize_request(request: str) -> str:
    return re.sub(r"\s+", " ", request).strip()


def _classify_request(request: str) -> tuple[str, str, str]:
    normalized = _normalize_request(request).lower()
    complexity = "high" if sum(marker in normalized for marker in _COMPLEX_MARKERS) >= 2 else "low"
    risk = "high" if any(marker in normalized for marker in _HIGH_RISK_MARKERS) else "low"
    if complexity == "high" and risk == "high":
        return "needs_delegation", complexity, risk
    if complexity == "high":
        return "needs_plan", complexity, "medium"
    return "single_task", complexity, risk


def validate_stage_graph(stages: list[dict[str, object]]) -> list[str]:
    ids = {str(stage.get("id") or "") for stage in stages}
    errors: list[str] = []
    graph: dict[str, list[str]] = {}
    for stage in stages:
        stage_id = str(stage.get("id") or "")
        dependencies = stage.get("depends_on")
        if not stage_id or not isinstance(dependencies, list):
            errors.append("invalid_stage")
            continue
        if any(str(dep) not in ids for dep in dependencies):
            errors.append("missing_dependency")
        graph[stage_id] = [str(dep) for dep in dependencies]

    visiting: set[str] = set()
    visited: set[str] = set()

    def visit(stage_id: str) -> None:
        if stage_id in visiting:
            errors.append("cycle")
            return
        if stage_id in visited:
            return
        visiting.add(stage_id)
        for dependency in graph.get(stage_id, []):
            visit(dependency)
        visiting.remove(stage_id)
        visited.add(stage_id)

    for stage_id in graph:
        visit(stage_id)
    return sorted(set(errors))


def _stage(
    stage_id: str,
    skill: str,
    dependencies: list[str],
    reason: str,
    *,
    request: str,
    complexity: str,
    risk: str,
) -> dict[str, object]:
    routing = resolve_task_routing(
        task_kind=stage_id,
        request_text=request,
        complexity=complexity,
        risk=risk,
        ambiguity_level="medium" if complexity == "high" else "low",
        failure_cost="high" if risk == "high" else "low",
        operator_goal="complete_requested_change",
        scope_fixed=True,
    )
    return {
        "id": stage_id,
        "skill": skill,
        "model_profile": routing["model_profile"],
        "depends_on": dependencies,
        "reason_summary": routing.get("routing_reason_summary") or reason,
        "recommended_policy_profile": routing.get("recommended_policy_profile", "balanced"),
        "policy_reason_summary": routing.get("policy_reason_summary", "shared policy resolver unavailable"),
        "policy_confidence": routing.get("policy_confidence", "low"),
        "recommended_executor": routing.get("recommended_executor", "codex"),
        "executor_reason_summary": routing.get("executor_reason_summary", "default executor fallback"),
        "executor_fallback": routing.get("executor_fallback", "codex"),
        "user_selection_needed": bool(routing.get("user_selection_needed", True)),
        "recommended_next_skill": routing.get("recommended_next_skill", "omc-plan"),
        "auto_execution_allowed": bool(routing.get("auto_execution_allowed", False)),
    }


def build_orchestration_plan(request: str, *, target: str | Path = ".") -> dict[str, object]:
    normalized = _normalize_request(request)
    if not normalized:
        raise ValueError("request must not be empty")
    classification, complexity, risk = _classify_request(normalized)
    stage_kwargs = {"request": normalized, "complexity": complexity, "risk": risk}
    if classification == "single_task":
        stages = [
            _stage("task", "omc-task", [], "동작 범위가 작고 실패 비용이 낮음", **stage_kwargs),
            _stage("review", "omc-review", ["task"], "작은 변경의 회귀 여부 확인", **stage_kwargs),
        ]
    elif classification == "needs_plan":
        stages = [
            _stage("plan", "omc-plan", [], "다중 파일 또는 통합 영향 범위 분석 필요", **stage_kwargs),
            _stage("task", "omc-task", ["plan"], "계획 결과를 기준으로 구현", **stage_kwargs),
            _stage("review", "omc-review", ["task"], "구현 결과의 회귀 검증", **stage_kwargs),
        ]
    else:
        stages = [
            _stage("plan", "omc-plan", [], "고위험·다중 영역 작업의 영향 분석", **stage_kwargs),
            _stage("task", "omc-task", ["plan"], "분해된 구현 태스크 실행 계획", **stage_kwargs),
            _stage("critique", "omc-critique", ["task"], "고위험 변경의 실패 가능성 사전 검토", **stage_kwargs),
            _stage("review", "omc-review", ["critique"], "최종 diff와 테스트 검증", **stage_kwargs),
        ]
    graph_errors = validate_stage_graph(stages)
    return {
        "request": normalized,
        "target": str(Path(target)),
        "mode": "dry-run",
        "classification": classification,
        "complexity": complexity,
        "risk": risk,
        "stages": stages,
        "invalid_dependency_rate": 1 if graph_errors else 0,
        "graph_errors": graph_errors,
        "execution_allowed": False,
        "user_selection_needed": any(stage["user_selection_needed"] for stage in stages),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Create a read-only OMC orchestration plan.")
    parser.add_argument("--request", required=True)
    parser.add_argument("--target", default=".")
    parser.add_argument("--dry-run", action="store_true", help="Required safety marker; no executor is called.")
    args = parser.parse_args()
    if not args.dry_run:
        parser.error("--dry-run is required; this command never executes stages")
    print(json.dumps(build_orchestration_plan(args.request, target=args.target), ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
