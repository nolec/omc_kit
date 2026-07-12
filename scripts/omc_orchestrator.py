"""Read-only orchestration plan generator.

This module creates a stage graph and model recommendations. It never runs an
executor; execution remains an explicit follow-up concern for autopilot.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import subprocess
import sys
import tempfile
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


def can_auto_execute_simple(
    plan: dict[str, object],
    *,
    user_opt_in: bool,
    sensitive_paths: list[str] | None = None,
    new_files: bool = False,
    api_change: bool = False,
    deletion: bool = False,
    dirty_scope_conflict: bool = False,
    git_status_unavailable: bool = False,
) -> bool:
    if user_opt_in is not True or plan.get("classification") != "single_task":
        return False
    if sensitive_paths or new_files or api_change or deletion or dirty_scope_conflict or git_status_unavailable:
        return False
    stages = plan.get("stages")
    if not isinstance(stages, list) or [stage.get("id") for stage in stages] != ["task", "review"]:
        return False
    routing = resolve_task_routing(
        task_kind="task",
        request_text=str(plan.get("request") or ""),
        complexity=str(plan.get("complexity") or ""),
        risk=str(plan.get("risk") or ""),
        ambiguity_level="low",
        failure_cost="low",
        operator_goal="speed",
        scope_fixed=True,
    )
    return bool(routing.get("simple_auto_execute_allowed") is True)


def detect_simple_scope_risks(target: str | Path, request: str) -> dict[str, object]:
    target_path = Path(target).resolve()
    status = subprocess.run(
        ["git", "status", "--porcelain"],
        cwd=str(target_path),
        capture_output=True,
        text=True,
        check=False,
    )
    raw_lines = (status.stdout or "").splitlines()
    paths = [line[3:].strip() for line in raw_lines if len(line) >= 4]
    normalized = _normalize_request(request).lower()
    sensitive = [
        path for path in paths
        if any(token in path.lower() for token in ("secret", "token", "password", ".env", "key"))
    ]
    return {
        "sensitive_paths": sensitive,
        "new_files": any(line.startswith("??") or line.startswith(" A") for line in raw_lines),
        "api_change": "api" in normalized,
        "deletion": any(line.startswith("D") or line.startswith(" D") for line in raw_lines) or "삭제" in normalized,
        "dirty_scope_conflict": bool(paths) or status.returncode != 0,
        "git_status_unavailable": status.returncode != 0,
    }


def build_simple_autopilot_task(plan: dict[str, object]) -> dict[str, object] | None:
    if plan.get("classification") != "single_task":
        return None
    stages = plan.get("stages")
    if not isinstance(stages, list) or [stage.get("id") for stage in stages] != ["task", "review"]:
        return None
    fingerprint = hashlib.sha256(str(plan.get("request") or "").encode()).hexdigest()[:12]
    return {
        "id": f"simple-auto-{fingerprint}",
        "title": "Simple OMC auto execution",
        "executor": "auto",
        "max_retries": 0,
        "steps": [
            {
                "id": "task",
                "title": "Execute task",
                "task_kind": "task",
                "prompt": (
                    f"{plan.get('request') or ''}\n\n"
                    "작업 결과를 확인한 뒤 마지막 줄에 `VERDICT: PROCEED` 또는 "
                    "`VERDICT: BLOCK` 중 하나를 출력하세요."
                ),
                "depends_on": [],
                "verdict_required": ["PROCEED"],
            },
            {
                "id": "review",
                "title": "Review task result",
                "task_kind": "review",
                "prompt": (
                    "Review the task result. 마지막 줄에 `VERDICT: APPROVE`, "
                    "`VERDICT: REVISE`, 또는 `VERDICT: BLOCK` 중 하나를 출력하세요. "
                    "REVISE/BLOCK이면 자동 실행을 중단합니다."
                ),
                "depends_on": ["task"],
                "verdict_required": ["APPROVE"],
            },
        ],
    }


def run_simple_autopilot(plan: dict[str, object], *, target: str | Path) -> int:
    scope_risks = detect_simple_scope_risks(target, str(plan.get("request") or ""))
    if not can_auto_execute_simple(plan, user_opt_in=True, **scope_risks):
        raise ValueError("simple auto-execution gate rejected the request")
    task = build_simple_autopilot_task(plan)
    if task is None:
        raise ValueError("simple autopilot task could not be built")
    target_path = Path(target).resolve()
    autopilot = Path(__file__).with_name("omc_autopilot.py")
    with tempfile.NamedTemporaryFile(mode="w", suffix=".json", encoding="utf-8") as handle:
        json.dump(task, handle, ensure_ascii=False, indent=2)
        handle.flush()
        return subprocess.run(
            [
                sys.executable,
                str(autopilot),
                "--target",
                str(target_path),
                "run",
                "--task",
                handle.name,
            ],
            cwd=str(target_path),
            check=False,
        ).returncode


def main() -> int:
    parser = argparse.ArgumentParser(description="Create a read-only OMC orchestration plan.")
    parser.add_argument("--request", required=True)
    parser.add_argument("--target", default=".")
    parser.add_argument("--dry-run", action="store_true", help="Print the plan without execution.")
    parser.add_argument(
        "--execute-simple",
        action="store_true",
        help="Opt in to existing autopilot only when the simple-task gate passes.",
    )
    args = parser.parse_args()
    if not args.dry_run and not args.execute_simple:
        parser.error("choose --dry-run or explicit --execute-simple")
    plan = build_orchestration_plan(args.request, target=args.target)
    if args.execute_simple:
        scope_risks = detect_simple_scope_risks(args.target, args.request)
        if not can_auto_execute_simple(plan, user_opt_in=True, **scope_risks):
            print(json.dumps({**plan, "blocked_reason": "simple_auto_execution_gate"}, ensure_ascii=False, indent=2))
            return 2
        return run_simple_autopilot(plan, target=args.target)
    print(json.dumps(plan, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
