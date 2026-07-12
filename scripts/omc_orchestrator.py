"""Read-only orchestration plan generator.

This module creates a stage graph and model recommendations. It never runs an
executor; execution remains an explicit follow-up concern for autopilot.
"""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
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
_DECOMPOSITION_DOMAINS = (
    ("backend", ("api", "결제", "백엔드", "권한", "마이그레이션", "개인정보", "배포", "삭제")),
    ("frontend", ("프론트", "frontend")),
    ("verification", ("테스트", "test", "통합")),
)
_ALLOWED_EXECUTORS = {"codex", "claude", "gemini"}
_CAPABILITY_SOURCE_TYPES = {"fixture", "observed"}


def normalize_capability_evidence(evidence: dict[str, object]) -> dict[str, object]:
    """Normalize capability observations without granting execution permission."""
    source_type = evidence.get("source_type")
    executor = evidence.get("executor")
    sample_count = evidence.get("sample_count")
    observed_at = evidence.get("observed_at")
    environment_fingerprint = evidence.get("environment_fingerprint")
    reason_codes: list[str] = []

    if source_type not in _CAPABILITY_SOURCE_TYPES:
        reason_codes.append("invalid_source_type")
        source_type = "observed"
    if executor not in _ALLOWED_EXECUTORS:
        reason_codes.append("invalid_executor")
    if not isinstance(observed_at, str) or not observed_at.strip():
        reason_codes.append("missing_observed_at")
    else:
        try:
            parsed_observed_at = datetime.fromisoformat(observed_at.replace("Z", "+00:00"))
            if parsed_observed_at.tzinfo is None or parsed_observed_at.utcoffset() is None:
                reason_codes.append("invalid_observed_at_timezone")
        except ValueError:
            reason_codes.append("invalid_observed_at")
    if not isinstance(sample_count, int) or isinstance(sample_count, bool) or sample_count < 0:
        reason_codes.append("invalid_sample_count")
    if not isinstance(environment_fingerprint, str) or not environment_fingerprint.strip():
        reason_codes.append("missing_environment_fingerprint")

    normalized = dict(evidence)
    normalized.update(
        {
            "source_type": source_type,
            "executor": executor,
            "sample_count": sample_count if isinstance(sample_count, int) else 0,
            "reason_codes": reason_codes,
            "execution_allowed": False,
        }
    )

    if any(code.startswith("invalid_") for code in reason_codes):
        status = "rejected"
    elif (
        not observed_at
        or not isinstance(sample_count, int)
        or sample_count == 0
        or "missing_environment_fingerprint" in reason_codes
    ):
        status = "insufficient"
    elif source_type == "fixture":
        status = "unverified"
    else:
        status = "observed"
    normalized["evidence_status"] = status
    return normalized


def _parse_capability_timestamp(value: object) -> datetime | None:
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def build_capability_evidence_from_runs(
    runs: list[dict[str, object]],
    *,
    current_environment_fingerprint: str | None = None,
    now: str | None = None,
    freshness_hours: int | None = None,
) -> list[dict[str, object]]:
    """Aggregate real run records for observation only; never enable execution."""
    if freshness_hours is not None and (
        not isinstance(freshness_hours, int) or isinstance(freshness_hours, bool) or freshness_hours < 0
    ):
        raise ValueError("freshness_hours must be a non-negative integer")
    aggregates: dict[tuple[str, str, str, str], dict[str, object]] = {}
    now_dt = _parse_capability_timestamp(now) if now else datetime.now(timezone.utc)

    for run in runs:
        status = str(run.get("status") or "unknown").lower()
        task_kind = str(run.get("task_kind") or "unknown")
        domain = str(run.get("domain") or "unknown")
        policy_profile = str(run.get("policy_profile") or "balanced")
        key = (str(run.get("executor")), task_kind, domain, policy_profile)
        if status in {"running", "pending", "in_progress"}:
            aggregate = aggregates.setdefault(
                key,
                {
                    "executor": run.get("executor"),
                    "task_kind": task_kind,
                    "domain": domain,
                    "policy_profile": policy_profile,
                    "source_type": "observed",
                    "observed_at": run.get("started_at"),
                    "sample_count": 0,
                    "success_count": 0,
                    "failure_count": 0,
                    "availability_attempts": 0,
                    "availability_successes": 0,
                    "in_progress_count": 0,
                    "fresh_sample_count": 0,
                    "stale_sample_count": 0,
                    "current_environment_sample_count": 0,
                    "mismatched_environment_sample_count": 0,
                    "current_environment_fresh_sample_count": 0,
                    "current_environment_stale_sample_count": 0,
                    "reason_codes": set(),
                    "environment_fingerprint": run.get("environment_fingerprint"),
                    "execution_allowed": False,
                },
            )
            aggregate["in_progress_count"] = int(aggregate.get("in_progress_count", 0)) + 1
            aggregate["reason_codes"].add("in_progress")
            continue
        observed_at = run.get("finished_at") or run.get("started_at")
        environment = run.get("environment_fingerprint")
        evidence = normalize_capability_evidence(
            {
                "executor": run.get("executor"),
                "task_kind": task_kind,
                "domain": domain,
                "policy_profile": policy_profile,
                "source_type": "observed",
                "observed_at": observed_at,
                "sample_count": 1,
                "environment_fingerprint": environment,
                "success_count": 1 if status in {"completed", "success", "succeeded"} else 0,
                "failure_count": 0 if status in {"completed", "success", "succeeded"} else 1,
                "availability_attempts": 1,
                "availability_successes": 1 if status != "unavailable" else 0,
            }
        )
        reason_codes = set(str(code) for code in evidence.get("reason_codes", []))
        observed_dt = _parse_capability_timestamp(observed_at)
        is_stale = False
        if freshness_hours is not None and observed_dt and now_dt:
            age_hours = (now_dt - observed_dt).total_seconds() / 3600
            if age_hours > freshness_hours:
                reason_codes.add("stale")
                is_stale = True
        if (
            current_environment_fingerprint
            and environment
            and environment != current_environment_fingerprint
        ):
            reason_codes.add("environment_mismatch")
        is_current_environment = bool(
            current_environment_fingerprint
            and environment == current_environment_fingerprint
        )

        aggregate = aggregates.setdefault(
            key,
            {
                **evidence,
                "sample_count": 0,
                "success_count": 0,
                "failure_count": 0,
                "availability_attempts": 0,
                "availability_successes": 0,
                "in_progress_count": 0,
                "fresh_sample_count": 0,
                "stale_sample_count": 0,
                "current_environment_sample_count": 0,
                "mismatched_environment_sample_count": 0,
                "current_environment_fresh_sample_count": 0,
                "current_environment_stale_sample_count": 0,
                "reason_codes": set(),
            },
        )
        for field in ("sample_count", "success_count", "failure_count", "availability_attempts", "availability_successes"):
            aggregate[field] = int(aggregate.get(field, 0)) + int(evidence.get(field, 0))
        aggregate["reason_codes"].update(reason_codes)
        aggregate["fresh_sample_count"] = int(aggregate.get("fresh_sample_count", 0)) + (0 if is_stale else 1)
        aggregate["stale_sample_count"] = int(aggregate.get("stale_sample_count", 0)) + (1 if is_stale else 0)
        if current_environment_fingerprint:
            if is_current_environment:
                aggregate["current_environment_sample_count"] = int(aggregate.get("current_environment_sample_count", 0)) + 1
                aggregate["current_environment_fresh_sample_count"] = int(aggregate.get("current_environment_fresh_sample_count", 0)) + (0 if is_stale else 1)
                aggregate["current_environment_stale_sample_count"] = int(aggregate.get("current_environment_stale_sample_count", 0)) + (1 if is_stale else 0)
            elif environment:
                aggregate["mismatched_environment_sample_count"] = int(aggregate.get("mismatched_environment_sample_count", 0)) + 1
        if observed_dt and (
            not _parse_capability_timestamp(aggregate.get("observed_at"))
            or observed_dt > _parse_capability_timestamp(aggregate.get("observed_at"))
        ):
            aggregate["observed_at"] = observed_at

    results: list[dict[str, object]] = []
    for aggregate in aggregates.values():
        reason_codes = sorted(str(code) for code in aggregate.pop("reason_codes", set()))
        if int(aggregate.get("sample_count", 0)) == 0 and int(aggregate.get("in_progress_count", 0)) > 0:
            status = "insufficient"
        elif (
            "environment_mismatch" in reason_codes
            and int(aggregate.get("current_environment_sample_count", 0)) == 0
        ):
            status = "environment_mismatch"
        elif (
            int(aggregate.get("current_environment_sample_count", 0)) > 0
            and int(aggregate.get("current_environment_fresh_sample_count", 0)) == 0
            and "stale" in reason_codes
        ) or (
            not current_environment_fingerprint
            and int(aggregate.get("fresh_sample_count", 0)) == 0
            and "stale" in reason_codes
        ):
            status = "stale"
        elif any(code.startswith("invalid_") for code in reason_codes):
            status = "rejected"
        elif "missing_observed_at" in reason_codes or "missing_environment_fingerprint" in reason_codes:
            status = "insufficient"
        else:
            status = "observed"
        aggregate["reason_codes"] = reason_codes
        aggregate["evidence_status"] = status
        aggregate["execution_allowed"] = False
        results.append(aggregate)
    return sorted(results, key=lambda item: tuple(str(item.get(field) or "") for field in ("executor", "task_kind", "domain", "policy_profile")))


def load_capability_evidence_from_runs(
    target: str | Path,
    *,
    current_environment_fingerprint: str | None = None,
    now: str | None = None,
    freshness_hours: int | None = None,
) -> list[dict[str, object]]:
    """Load only persisted run results and return observation-only aggregates."""
    return load_capability_evidence_report_from_runs(
        target,
        current_environment_fingerprint=current_environment_fingerprint,
        now=now,
        freshness_hours=freshness_hours,
    )["evidence"]


def load_capability_evidence_report_from_runs(
    target: str | Path,
    *,
    current_environment_fingerprint: str | None = None,
    now: str | None = None,
    freshness_hours: int | None = None,
) -> dict[str, object]:
    """Load run results and retain rejection metadata for operators."""
    runs: list[dict[str, object]] = []
    rejected_run_count = 0
    rejected_run_reasons: dict[str, int] = {}
    runs_dir = Path(target) / ".omc" / "runs"
    for result_path in sorted(runs_dir.glob("*/result.json")):
        try:
            payload = json.loads(result_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            rejected_run_count += 1
            rejected_run_reasons["invalid_json"] = rejected_run_reasons.get("invalid_json", 0) + 1
            continue
        except OSError:
            rejected_run_count += 1
            rejected_run_reasons["read_error"] = rejected_run_reasons.get("read_error", 0) + 1
            continue
        if isinstance(payload, dict):
            runs.append(payload)
        else:
            rejected_run_count += 1
            rejected_run_reasons["non_object"] = rejected_run_reasons.get("non_object", 0) + 1
    return {
        "evidence": build_capability_evidence_from_runs(
            runs,
            current_environment_fingerprint=current_environment_fingerprint,
            now=now,
            freshness_hours=freshness_hours,
        ),
        "rejected_run_count": rejected_run_count,
        "rejected_run_reasons": rejected_run_reasons,
    }


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
        "capability_evidence_status": "unverified",
        "capability_evidence_source": "none",
        "capability_evidence_sample_count": 0,
        "capability_evidence_reason_codes": ["no_capability_evidence"],
        "execution_allowed": False,
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


def validate_decomposition_result(result: dict[str, object]) -> list[str]:
    errors: list[str] = []
    required = {"classification", "children", "execution_allowed", "decomposition_confidence"}
    errors.extend(f"missing_{key}" for key in sorted(required - set(result)))
    classification = result.get("classification")
    confidence = result.get("decomposition_confidence")
    if classification not in {"single_task", "needs_plan", "needs_delegation"}:
        errors.append("invalid_classification")
    if confidence not in {"low", "medium", "high"}:
        errors.append("invalid_confidence")
    if classification == "needs_delegation":
        parent_required = {"recommendation_only", "evidence_status", "user_selection_needed"}
        errors.extend(f"missing_{key}" for key in sorted(parent_required - set(result)))
        if result.get("recommendation_only") is not True:
            errors.append("recommendation_must_be_only")
        if result.get("evidence_status") != "unverified":
            errors.append("invalid_evidence_status")
        if not isinstance(result.get("user_selection_needed"), bool):
            errors.append("invalid_user_selection_needed")
    children = result.get("children")
    if not isinstance(children, list):
        return [*errors, "children_not_list"]

    child_ids = [str(child.get("id") or "") for child in children if isinstance(child, dict)]
    if len(child_ids) != len(set(child_ids)):
        errors.append("duplicate_child_id")
    child_id_set = set(child_ids)
    graph: list[dict[str, object]] = []
    child_required = {
        "id", "goal", "scope", "depends_on", "task_kind", "risk", "expected_output", "handoff_contract",
        "recommended_executor", "executor_reason_code", "executor_reason_summary", "executor_fallback",
        "recommendation_only", "evidence_status", "recommended_policy_profile", "policy_confidence",
    }
    for child in children:
        if not isinstance(child, dict):
            errors.append("child_not_object")
            continue
        errors.extend(f"missing_child_{key}" for key in sorted(child_required - set(child)))
        if not isinstance(child.get("id"), str) or not child.get("id", "").strip():
            errors.append("invalid_child_id")
        if not isinstance(child.get("goal"), str) or not child.get("goal", "").strip():
            errors.append("invalid_child_goal")
        if not isinstance(child.get("expected_output"), str) or not child.get("expected_output", "").strip():
            errors.append("invalid_expected_output")
        scope = child.get("scope")
        if not isinstance(scope, list) or not all(isinstance(item, str) and item.strip() for item in scope):
            errors.append("invalid_child_scope")
        if child.get("task_kind") not in {"task", "review"}:
            errors.append("invalid_task_kind")
        if child.get("risk") not in {"low", "medium", "high"}:
            errors.append("invalid_risk")
        handoff_contract = child.get("handoff_contract")
        required_fields = handoff_contract.get("required_fields") if isinstance(handoff_contract, dict) else None
        if not isinstance(required_fields, list) or not required_fields or not all(
            isinstance(field, str) and field.strip() for field in required_fields
        ):
            errors.append("invalid_handoff_contract")
        if child.get("recommended_executor") not in _ALLOWED_EXECUTORS:
            errors.append("invalid_recommended_executor")
        if not isinstance(child.get("executor_reason_code"), str) or not child.get("executor_reason_code", "").strip():
            errors.append("invalid_executor_reason_code")
        if not isinstance(child.get("executor_reason_summary"), str) or not child.get("executor_reason_summary", "").strip():
            errors.append("invalid_executor_reason_summary")
        if child.get("executor_fallback") not in _ALLOWED_EXECUTORS or child.get("executor_fallback") == child.get("recommended_executor"):
            errors.append("invalid_executor_fallback")
        if child.get("recommendation_only") is not True:
            errors.append("recommendation_must_be_only")
        if child.get("evidence_status") != "unverified":
            errors.append("invalid_evidence_status")
        if child.get("recommended_policy_profile") not in {"cost_saver", "balanced", "quality_first"}:
            errors.append("invalid_recommended_policy_profile")
        if child.get("policy_confidence") not in {"low", "high"}:
            errors.append("invalid_policy_confidence")
        dependencies = child.get("depends_on")
        if not isinstance(dependencies, list):
            errors.append("child_dependencies_not_list")
            dependencies = []
        if any(str(dep) not in child_id_set for dep in dependencies):
            errors.append("missing_child_dependency")
        graph.append({"id": child.get("id"), "depends_on": dependencies})
    if "cycle" in validate_stage_graph(graph):
        errors.append("dependency_cycle")
    if classification == "needs_delegation":
        unresolved_questions = result.get("unresolved_questions")
        if confidence == "low" and children:
            errors.append("confidence_children_mismatch")
        if confidence == "low" and not isinstance(unresolved_questions, list):
            errors.append("confidence_questions_missing")
        if confidence in {"medium", "high"} and not children:
            errors.append("confidence_children_mismatch")
    if result.get("execution_allowed") is not False:
        errors.append("execution_must_remain_disabled")
    return sorted(set(errors))


def _child_executor_recommendation(plan: dict[str, object], child: dict[str, object]) -> dict[str, object]:
    risk = str(child.get("risk") or plan.get("risk") or "low")
    routing = resolve_task_routing(
        task_kind=str(child.get("task_kind") or "task"),
        request_text=str(plan.get("request") or ""),
        risk=risk,
        ambiguity_level="medium" if risk == "high" else "low",
        failure_cost="high" if risk == "high" else "low",
        operator_goal="balance",
        scope_fixed=True,
    )
    recommended = str(routing.get("recommended_executor") or "codex")
    fallback = str(routing.get("executor_fallback") or "gemini")
    if fallback == recommended:
        fallback = "gemini" if recommended != "gemini" else "codex"
    return {
        "recommended_executor": recommended,
        "executor_reason_code": str(routing.get("executor_reason_code") or "default_executor"),
        "executor_reason_summary": str(routing.get("executor_reason_summary") or "executor recommendation is unverified"),
        "executor_fallback": fallback,
        "capability_evidence_status": "unverified",
        "capability_evidence_source": "none",
        "capability_evidence_sample_count": 0,
        "capability_evidence_reason_codes": ["no_capability_evidence"],
        "execution_allowed": False,
        "recommendation_only": True,
        "evidence_status": "unverified",
        "recommended_policy_profile": str(routing.get("recommended_policy_profile") or "balanced"),
        "policy_confidence": str(routing.get("policy_confidence") or "low"),
    }


def build_decomposition_result(plan: dict[str, object]) -> dict[str, object]:
    stages = plan.get("stages")
    if plan.get("classification") != "needs_delegation" or not isinstance(stages, list):
        return {
            "request": plan.get("request", ""),
            "classification": plan.get("classification", "single_task"),
            "decomposition_confidence": "high",
            "children": [],
            "unresolved_questions": [],
            "merge_strategy": "none",
            "execution_allowed": False,
            "recommendation_only": True,
            "evidence_status": "unverified",
            "user_selection_needed": False,
        }
    normalized = _normalize_request(str(plan.get("request") or "")).lower()
    domains = [
        domain
        for domain, markers in _DECOMPOSITION_DOMAINS
        if any(marker in normalized for marker in markers)
    ]
    if len(domains) < 2:
        return {
            "request": plan.get("request", ""),
            "classification": "needs_delegation",
            "decomposition_confidence": "low",
            "children": [],
            "unresolved_questions": ["decomposition_domains_unclear"],
            "merge_strategy": "sequential_summary",
            "execution_allowed": False,
            "recommendation_only": True,
            "evidence_status": "unverified",
            "user_selection_needed": True,
        }
    children = []
    for domain in domains:
        children.append(
            {
                "id": f"child-{domain}",
                "goal": f"{domain} scope for requested change",
                "scope": [domain],
                "depends_on": [],
                "task_kind": "task",
                "risk": str(plan.get("risk") or "low"),
                "expected_output": f"{domain} result summary and handoff metadata",
                "handoff_contract": {
                    "required_fields": ["summary", "changed_files", "open_questions"],
                },
            }
        )
    children.append(
        {
            "id": "child-integration-review",
            "goal": "integrate child summaries and review cross-scope impact",
            "scope": domains,
            "depends_on": [f"child-{domain}" for domain in domains],
            "task_kind": "review",
            "risk": str(plan.get("risk") or "low"),
            "expected_output": "integration review summary and open questions",
            "handoff_contract": {
                "required_fields": ["summary", "changed_files", "open_questions"],
            },
        }
    )
    for child in children:
        child.update(_child_executor_recommendation(plan, child))
    return {
        "request": plan.get("request", ""),
        "classification": "needs_delegation",
        "decomposition_confidence": "high" if len(domains) >= 3 else "medium",
        "children": children,
        "unresolved_questions": [],
        "merge_strategy": "sequential_summary",
        "execution_allowed": False,
        "recommendation_only": True,
        "evidence_status": "unverified",
        "user_selection_needed": any(bool(stage.get("user_selection_needed")) for stage in stages),
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
