"""No-op executor shadow contract.

This module validates a future child execution request without invoking a
process, network client, filesystem mutation, or external LLM.
"""

from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone
import fcntl
import hashlib
import json
import math
import os
from pathlib import Path
import tempfile
import time
from typing import Any, Callable

if __package__:
    from .omc_scope import canonicalize_child_scopes
else:
    from omc_scope import canonicalize_child_scopes


def _is_finite_number(value: object) -> bool:
    try:
        return math.isfinite(float(value))
    except (OverflowError, TypeError, ValueError):
        return False


def build_parent_review_recovery(execution_result: Any) -> dict[str, Any]:
    """Map a terminal child failure to one bounded parent-review action."""
    if not isinstance(execution_result, dict):
        return {
            "status": "blocked",
            "reason_code": "parent_review_input_invalid",
        }
    execution_status = execution_result.get("status")
    execution_reason_code = execution_result.get("reason_code")
    recovery_actions = {
        "failed": "inspect_child_failure",
        "timeout": "inspect_timeout_and_partial_output",
        "indeterminate": "reconcile_execution_ledger",
        "blocked": "reconcile_execution_ledger",
    }
    if (
        execution_status not in recovery_actions
        or not isinstance(execution_reason_code, str)
        or not execution_reason_code.strip()
    ):
        return {
            "status": "blocked",
            "reason_code": "parent_review_input_invalid",
        }
    reported_execution_status = execution_status
    reported_execution_reason_code = execution_reason_code
    if execution_status in {"indeterminate", "blocked"}:
        terminal_execution_status = execution_result.get("execution_status")
        terminal_execution_reason_code = execution_result.get(
            "execution_reason_code"
        )
        if terminal_execution_status is not None:
            if (
                terminal_execution_status not in {"succeeded", "failed", "timeout"}
                or not isinstance(terminal_execution_reason_code, str)
                or not terminal_execution_reason_code.strip()
            ):
                return {
                    "status": "blocked",
                    "reason_code": "parent_review_input_invalid",
                }
            reported_execution_status = terminal_execution_status
            reported_execution_reason_code = terminal_execution_reason_code
        elif (
            terminal_execution_reason_code is not None
            or execution_status == "blocked"
        ):
            return {
                "status": "blocked",
                "reason_code": "parent_review_input_invalid",
            }
    return {
        "status": "review_required",
        "action": "parent_review",
        "execution_status": reported_execution_status,
        "execution_reason_code": reported_execution_reason_code,
        "recovery_reason_code": execution_reason_code,
        "recovery_action": recovery_actions[execution_status],
        "automatic_retry_allowed": False,
        "automatic_redistribution_allowed": False,
    }


def _with_parent_review_recovery(result: dict[str, Any]) -> dict[str, Any]:
    recovery = build_parent_review_recovery(result)
    if recovery["status"] != "review_required":
        return result
    return {**result, "parent_review": recovery}


def _base_record(request: dict[str, Any]) -> dict[str, Any]:
    return {
        "mode": "noop_shadow",
        "parent_id": str(request.get("parent_id") or ""),
        "child_id": str(request.get("child_id") or ""),
        "executor": str(request.get("executor") or ""),
        "execution_allowed": False,
        "retry_count": 0,
        "cost_recorded": False,
        "sandbox_status": "not_started",
        "usage_status": "unavailable",
    }


def _rejected(
    request: dict[str, Any],
    *,
    status: str,
    reason_code: str,
) -> dict[str, Any]:
    record = _base_record(request)
    record.update({"status": status, "reason_code": reason_code})
    return record


def _single_child_pilot_rejection(
    request: dict[str, Any],
    approval: dict[str, Any],
) -> dict[str, Any] | None:
    """Return a gate rejection for the bounded single-child pilot, if any."""
    child_count = request.get("child_count")
    if not isinstance(child_count, int) or isinstance(child_count, bool) or child_count != 1:
        return _rejected(request, status="blocked", reason_code="single_child_required")

    if request.get("child_status") != "ready":
        return _rejected(request, status="hold", reason_code="child_not_ready")

    if "sensitive_paths" not in request:
        return _rejected(request, status="blocked", reason_code="scope_metadata_missing")
    sensitive_paths = request["sensitive_paths"]
    if not isinstance(sensitive_paths, list):
        return _rejected(request, status="blocked", reason_code="scope_metadata_missing")
    if sensitive_paths:
        return _rejected(request, status="blocked", reason_code="sensitive_scope")

    if "depends_on" not in request or "dependency_statuses" not in request:
        return _rejected(
            request,
            status="blocked",
            reason_code="dependency_metadata_missing",
        )
    depends_on = request["depends_on"]
    dependency_statuses = request["dependency_statuses"]
    if not isinstance(depends_on, list) or not isinstance(dependency_statuses, dict):
        return _rejected(
            request,
            status="blocked",
            reason_code="dependency_metadata_missing",
        )
    if any(dependency_statuses.get(dependency) != "completed" for dependency in depends_on):
        return _rejected(request, status="hold", reason_code="dependency_not_ready")

    plan_fingerprint = request.get("plan_fingerprint")
    idempotency_key = request.get("idempotency_key")
    if not isinstance(plan_fingerprint, str) or not plan_fingerprint.strip():
        return _rejected(request, status="blocked", reason_code="plan_scope_missing")
    if not isinstance(idempotency_key, str) or not idempotency_key.strip():
        return _rejected(request, status="blocked", reason_code="idempotency_key_missing")

    seen_idempotency_keys = request.get("seen_idempotency_keys", [])
    if not isinstance(seen_idempotency_keys, list):
        return _rejected(request, status="blocked", reason_code="idempotency_key_invalid")
    if idempotency_key in seen_idempotency_keys:
        return _rejected(request, status="blocked", reason_code="duplicate_idempotency_key")

    budget = request.get("budget")
    if not isinstance(budget, dict):
        return _rejected(request, status="blocked", reason_code="budget_invalid")
    max_attempts = budget.get("max_attempts")
    max_elapsed = budget.get("max_total_elapsed_sec")
    max_output_chars = budget.get("max_output_chars")
    if (
        max_attempts != 1
        or not isinstance(max_elapsed, (int, float))
        or isinstance(max_elapsed, bool)
        or not _is_finite_number(max_elapsed)
        or max_elapsed <= 0
        or max_elapsed > 120
        or not isinstance(max_output_chars, int)
        or isinstance(max_output_chars, bool)
        or max_output_chars <= 0
    ):
        return _rejected(request, status="blocked", reason_code="budget_invalid")

    if approval.get("operator_confirmed") is not True or approval.get("approval_status") != "approved":
        return _rejected(
            request,
            status="blocked",
            reason_code="operator_confirmation_missing",
        )
    if approval.get("plan_fingerprint") != plan_fingerprint:
        return _rejected(request, status="blocked", reason_code="plan_scope_mismatch")
    if approval.get("idempotency_key") != idempotency_key:
        return _rejected(
            request,
            status="blocked",
            reason_code="approval_binding_mismatch",
        )
    return None


def build_noop_shadow_record(request: dict[str, Any]) -> dict[str, Any]:
    """Validate one child request and return a non-executing shadow record."""
    record = _base_record(request)
    approval = request.get("approval")
    policy = request.get("policy")
    single_child_pilot = request.get("pilot_mode") == "single_child"

    if any(
        not isinstance(request.get(key), str) or not request.get(key).strip()
        for key in ("parent_id", "child_id", "executor", "scope_hash")
    ):
        return _rejected(request, status="rejected", reason_code="identifier_missing")

    if not isinstance(approval, dict):
        return _rejected(request, status="blocked", reason_code="approval_missing")
    if not isinstance(policy, dict):
        return _rejected(
            request,
            status="rejected",
            reason_code="guard_metadata_missing",
        )

    if single_child_pilot:
        pilot_rejection = _single_child_pilot_rejection(request, approval)
        if pilot_rejection is not None:
            return pilot_rejection

    required_approval = {
        "approval_id",
        "session_id",
        "child_id",
        "scope_hash",
        "expires_at",
    }
    if any(not approval.get(key) for key in required_approval):
        return _rejected(
            request,
            status="rejected",
            reason_code="approval_metadata_missing",
        )

    if approval.get("child_id") != request.get("child_id") or approval.get("scope_hash") != request.get("scope_hash"):
        return _rejected(request, status="blocked", reason_code="scope_mismatch")

    try:
        expires_at = datetime.fromisoformat(str(approval["expires_at"]).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return _rejected(
            request,
            status="rejected",
            reason_code="approval_expiry_invalid",
        )
    if expires_at.tzinfo is None or expires_at.utcoffset() is None:
        return _rejected(
            request,
            status="rejected",
            reason_code="approval_expiry_invalid",
        )
    if expires_at <= datetime.now(timezone.utc):
        return _rejected(request, status="blocked", reason_code="approval_expired")

    allowed_executors = policy.get("allowed_executors")
    timeout_sec = policy.get("timeout_sec")
    budget_usd = policy.get("budget_usd")
    retry_limit = policy.get("retry_limit")
    if (
        not isinstance(allowed_executors, list)
        or not allowed_executors
        or any(not isinstance(executor, str) or not executor.strip() for executor in allowed_executors)
        or not isinstance(timeout_sec, (int, float))
        or isinstance(timeout_sec, bool)
        or timeout_sec <= 0
        or not _is_finite_number(timeout_sec)
        or not isinstance(budget_usd, (int, float))
        or isinstance(budget_usd, bool)
        or budget_usd < 0
        or not _is_finite_number(budget_usd)
        or not isinstance(retry_limit, int)
        or isinstance(retry_limit, bool)
        or retry_limit < 0
    ):
        return _rejected(
            request,
            status="rejected",
            reason_code="guard_metadata_invalid",
        )

    if request.get("executor") not in allowed_executors:
        return _rejected(
            request,
            status="rejected",
            reason_code="executor_not_allowed",
        )
    execution_requested = request.get("execution_requested", False)
    if not isinstance(execution_requested, bool):
        return _rejected(
            request,
            status="rejected",
            reason_code="execution_flag_invalid",
        )
    if execution_requested:
        return _rejected(
            request,
            status="rejected",
            reason_code="real_execution_disabled",
        )

    record.update(
        {
            "status": "simulated",
            "approval_status": "validated",
            "approval_id": approval["approval_id"],
            "session_id": approval["session_id"],
            "timeout_sec": timeout_sec,
            "budget_usd": budget_usd,
            "retry_limit": retry_limit,
        }
    )
    if single_child_pilot:
        record.update(
            {
                "gate_status": "allowed",
                "shadow_recorded": True,
                "fallback_action": "parent_review",
                "plan_fingerprint": request["plan_fingerprint"],
                "idempotency_key": request["idempotency_key"],
                "budget": request["budget"],
            }
        )
    return record


def build_single_child_execution_grant(request: dict[str, Any]) -> dict[str, Any]:
    """Issue a bounded grant without invoking the selected executor.

    The existing shadow gate remains the single source of truth for approval,
    scope, dependency, budget, and idempotency validation. The caller must
    consume the grant separately; this function never starts a process.
    """
    if request.get("execution_requested") is not True or request.get("execution_mode") != "single_child_opt_in":
        return _rejected(
            request,
            status="blocked",
            reason_code="execution_opt_in_missing",
        )
    if request.get("pilot_mode") != "single_child":
        return _rejected(
            request,
            status="blocked",
            reason_code="single_child_required",
        )

    shadow_request = dict(request)
    shadow_request["execution_requested"] = False
    shadow = build_noop_shadow_record(shadow_request)
    if shadow.get("status") != "simulated" or shadow.get("gate_status") != "allowed":
        return shadow

    budget = request["budget"]
    return {
        **shadow,
        "mode": "single_child_execution_grant",
        "status": "ready",
        "execution_allowed": True,
        "max_attempts": budget["max_attempts"],
        "max_total_elapsed_sec": budget["max_total_elapsed_sec"],
        "max_output_chars": budget["max_output_chars"],
        "scope_hash": request["scope_hash"],
        "approval_expires_at": request["approval"]["expires_at"],
        "shadow_recorded": True,
        "fallback_action": "parent_review",
    }


def _canonical_sha256(value: Any) -> str:
    payload = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def _valid_sequence_child_grant(grant: Any, child_id: str) -> bool:
    return (
        isinstance(grant, dict)
        and grant.get("mode") == "single_child_execution_grant"
        and grant.get("status") == "ready"
        and grant.get("execution_allowed") is True
        and grant.get("child_id") == child_id
        and grant.get("max_attempts") == 1
        and grant.get("fallback_action") == "parent_review"
        and _is_finite_number(grant.get("max_total_elapsed_sec"))
        and float(grant["max_total_elapsed_sec"]) > 0
        and isinstance(grant.get("max_output_chars"), int)
        and not isinstance(grant.get("max_output_chars"), bool)
        and grant["max_output_chars"] > 0
    )


def _dag_blocked(reason_code: str) -> dict[str, Any]:
    return {
        "mode": "n_child_dag_grant",
        "status": "blocked",
        "reason_code": reason_code,
        "execution_allowed": False,
    }


def _dag_proposal_blocked(reason_code: str) -> dict[str, Any]:
    return {
        "mode": "n_child_dag_proposal",
        "status": "blocked",
        "reason_code": reason_code,
        "execution_allowed": False,
        "scheduler_eligible": False,
    }


def _dag_graph_is_valid(children: list[dict[str, Any]]) -> bool:
    child_ids = [child.get("child_id") for child in children]
    if (
        not all(isinstance(child_id, str) and child_id.strip() for child_id in child_ids)
        or len(set(child_ids)) != len(child_ids)
    ):
        return False
    child_id_set = set(child_ids)
    dependency_map: dict[str, list[str]] = {}
    for child, child_id in zip(children, child_ids):
        dependencies = child.get("depends_on")
        if (
            not isinstance(dependencies, list)
            or any(
                not isinstance(dependency, str) or not dependency.strip()
                for dependency in dependencies
            )
            or len(set(dependencies)) != len(dependencies)
            or child_id in dependencies
            or not set(dependencies).issubset(child_id_set)
        ):
            return False
        dependency_map[child_id] = dependencies

    remaining = {
        child_id: set(dependencies)
        for child_id, dependencies in dependency_map.items()
    }
    ready = [
        child_id for child_id, dependencies in remaining.items() if not dependencies
    ]
    visited = 0
    while ready:
        completed = ready.pop()
        visited += 1
        for child_id, dependencies in remaining.items():
            if completed not in dependencies:
                continue
            dependencies.remove(completed)
            if not dependencies:
                ready.append(child_id)
    return visited == len(children)


def _scope_parts(value: Any) -> tuple[str, ...] | None:
    if not isinstance(value, str) or not value.strip() or "\\" in value:
        return None
    normalized = value.strip().rstrip("/")
    parts = tuple(normalized.split("/"))
    if normalized.startswith("/") or any(part in {"", ".", ".."} for part in parts):
        return None
    return parts


def _dag_scopes_are_disjoint(children: list[dict[str, Any]]) -> bool:
    scopes: list[tuple[str, ...]] = []
    for child in children:
        scope_paths = child.get("scope_paths")
        scope_hash = child.get("scope_hash")
        if (
            not isinstance(scope_paths, list)
            or not scope_paths
            or not isinstance(scope_hash, str)
            or not scope_hash.strip()
        ):
            return False
        for scope_path in scope_paths:
            parts = _scope_parts(scope_path)
            if parts is None:
                return False
            if any(
                parts[: len(existing)] == existing
                or existing[: len(parts)] == parts
                for existing in scopes
            ):
                return False
            scopes.append(parts)
    return True


def _dag_critical_path_elapsed(
    children: list[dict[str, Any]],
    child_grants: list[dict[str, Any]],
) -> float:
    dependencies = {
        child["child_id"]: child["depends_on"]
        for child in children
    }
    durations = {
        child["child_id"]: float(grant["max_total_elapsed_sec"])
        for child, grant in zip(children, child_grants)
    }
    elapsed_by_child: dict[str, float] = {}

    def elapsed(child_id: str) -> float:
        if child_id not in elapsed_by_child:
            dependency_elapsed = max(
                (elapsed(dependency) for dependency in dependencies[child_id]),
                default=0.0,
            )
            elapsed_by_child[child_id] = dependency_elapsed + durations[child_id]
        return elapsed_by_child[child_id]

    return max(elapsed(child_id) for child_id in dependencies)


def _valid_future_timestamp(value: Any) -> bool:
    parsed = _parse_timestamp(value)
    return (
        parsed is not None
        and parsed.tzinfo is not None
        and parsed.utcoffset() is not None
        and parsed > datetime.now(timezone.utc)
    )


def _parse_timestamp(value: Any) -> datetime | None:
    if not isinstance(value, str):
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        return None
    return parsed


def _valid_n_child_execution_grant(grant: Any, child_id: str) -> bool:
    required_text_fields = (
        "parent_id",
        "executor",
        "approval_id",
        "session_id",
        "idempotency_key",
        "scope_hash",
        "plan_fingerprint",
    )
    return (
        _valid_sequence_child_grant(grant, child_id)
        and all(
            isinstance(grant.get(field), str) and grant[field].strip()
            for field in required_text_fields
        )
        and grant.get("approval_status") == "validated"
        and grant.get("gate_status") == "allowed"
        and grant.get("shadow_recorded") is True
        and isinstance(grant.get("max_attempts"), int)
        and not isinstance(grant.get("max_attempts"), bool)
        and isinstance(grant.get("max_total_elapsed_sec"), (int, float))
        and not isinstance(grant.get("max_total_elapsed_sec"), bool)
        and _valid_future_timestamp(grant.get("approval_expires_at"))
    )


def _valid_n_child_budget(
    children: list[dict[str, Any]],
    child_grants: list[dict[str, Any]],
    budget: Any,
) -> bool:
    if not isinstance(budget, dict):
        return False
    max_external_calls = budget.get("max_external_calls")
    max_parallelism = budget.get("max_parallelism")
    max_elapsed = budget.get("max_total_elapsed_sec")
    max_output = budget.get("max_output_chars")
    max_tokens = budget.get("max_total_tokens")
    child_token_limits = [grant.get("max_total_tokens") for grant in child_grants]
    return (
        isinstance(max_external_calls, int)
        and not isinstance(max_external_calls, bool)
        and max_external_calls == len(children)
        and isinstance(max_parallelism, int)
        and not isinstance(max_parallelism, bool)
        and 1 <= max_parallelism <= len(children)
        and not isinstance(max_elapsed, bool)
        and _is_finite_number(max_elapsed)
        and float(max_elapsed) >= _dag_critical_path_elapsed(children, child_grants)
        and isinstance(max_output, int)
        and not isinstance(max_output, bool)
        and max_output >= sum(grant["max_output_chars"] for grant in child_grants)
        and isinstance(max_tokens, int)
        and not isinstance(max_tokens, bool)
        and all(
            isinstance(limit, int)
            and not isinstance(limit, bool)
            and limit > 0
            for limit in child_token_limits
        )
        and max_tokens >= sum(child_token_limits)
    )


def _proposal_hash_payload(proposal: dict[str, Any]) -> dict[str, Any]:
    keys = (
        "schema_version",
        "mode",
        "status",
        "reason_code",
        "dag_id",
        "execution_mode",
        "execution_requested",
        "execution_allowed",
        "scheduler_eligible",
        "children",
        "child_grants",
        "child_prompts",
        "aggregate_budget",
        "scope_policy_version",
        "target_identity_sha256",
        "graph_sha256",
        "child_grant_sha256s",
        "prompt_sha256s",
        "aggregate_budget_sha256",
    )
    payload = {key: deepcopy(proposal.get(key)) for key in keys}
    if "target_binding" in proposal:
        payload["target_binding"] = deepcopy(proposal["target_binding"])
    return payload


def build_n_child_dag_proposal(
    trusted_target: str | Path,
    request: dict[str, Any],
) -> dict[str, Any]:
    """Build a canonical v2 DAG proposal before operator approval."""
    if (
        not isinstance(request, dict)
        or request.get("execution_requested") is not True
        or request.get("execution_mode") != "n_child_dag_opt_in"
    ):
        return _dag_proposal_blocked("dag_opt_in_missing")
    dag_id = request.get("dag_id")
    children = request.get("children")
    child_grants = request.get("child_grants")
    child_prompts = request.get("child_prompts")
    budget = request.get("aggregate_budget")
    target_binding = request.get("target_binding")
    if (
        request.get("schema_version") != "omc-n-child-dag/v2"
        or not isinstance(dag_id, str)
        or not dag_id.strip()
    ):
        return _dag_proposal_blocked("dag_input_invalid")
    if (
        not isinstance(children, list)
        or not 3 <= len(children) <= 5
        or not all(isinstance(child, dict) for child in children)
    ):
        return _dag_proposal_blocked("n_child_count_invalid")
    if not _dag_graph_is_valid(children):
        return _dag_proposal_blocked("dag_graph_invalid")

    scope_result = canonicalize_child_scopes(
        trusted_target,
        children,
        target_binding=target_binding,
    )
    if scope_result.get("status") != "ready":
        return _dag_proposal_blocked(str(scope_result.get("reason_code")))
    normalized_children = scope_result["children"]
    child_ids = [child["child_id"] for child in normalized_children]
    if (
        not isinstance(child_grants, list)
        or len(child_grants) != len(normalized_children)
        or not all(
            _valid_n_child_execution_grant(grant, child_id)
            and child.get("scope_hash") == normalized.get("scope_hash")
            and grant.get("scope_hash") == normalized.get("scope_hash")
            for grant, child, normalized, child_id in zip(
                child_grants, children, normalized_children, child_ids
            )
        )
        or len({grant["idempotency_key"] for grant in child_grants})
        != len(child_grants)
    ):
        return _dag_proposal_blocked("child_execution_grant_invalid")
    approval_ids = [grant["approval_id"] for grant in child_grants]
    if (
        any(approval_id != approval_id.strip() for approval_id in approval_ids)
        or len(set(approval_ids)) != len(approval_ids)
    ):
        return _dag_proposal_blocked("child_approval_id_invalid")
    if (
        not isinstance(child_prompts, dict)
        or set(child_prompts) != set(child_ids)
        or any(
            not isinstance(child_prompts[child_id], str)
            or not child_prompts[child_id].strip()
            for child_id in child_ids
        )
    ):
        return _dag_proposal_blocked("dag_prompt_invalid")
    if not _valid_n_child_budget(normalized_children, child_grants, budget):
        return _dag_proposal_blocked("dag_budget_invalid")

    proposal = {
        "schema_version": "omc-n-child-dag/v2",
        "mode": "n_child_dag_proposal",
        "status": "ready",
        "reason_code": "dag_proposal_ready",
        "execution_mode": "n_child_dag_opt_in",
        "execution_requested": True,
        "execution_allowed": False,
        "scheduler_eligible": False,
        "dag_id": dag_id,
        "children": deepcopy(normalized_children),
        "child_grants": deepcopy(child_grants),
        "child_prompts": deepcopy(child_prompts),
        "aggregate_budget": deepcopy(budget),
        "scope_policy_version": scope_result["scope_policy_version"],
        "target_identity_sha256": scope_result["target_identity_sha256"],
        "graph_sha256": _canonical_sha256(normalized_children),
        "child_grant_sha256s": [
            _canonical_sha256(grant) for grant in child_grants
        ],
        "prompt_sha256s": {
            child_id: _canonical_sha256(child_prompts[child_id])
            for child_id in child_ids
        },
        "aggregate_budget_sha256": _canonical_sha256(budget),
    }
    if target_binding is not None:
        proposal["target_binding"] = deepcopy(target_binding)
    proposal["proposal_sha256"] = _canonical_sha256(
        _proposal_hash_payload(proposal)
    )
    return proposal


def build_n_child_dag_v2_grant(
    trusted_target: str | Path,
    proposal: dict[str, Any],
    approval: dict[str, Any],
) -> dict[str, Any]:
    """Issue scheduler eligibility only for an intact, target-bound proposal."""
    if (
        not isinstance(proposal, dict)
        or proposal.get("schema_version") != "omc-n-child-dag/v2"
        or proposal.get("mode") != "n_child_dag_proposal"
        or proposal.get("status") != "ready"
        or proposal.get("reason_code") != "dag_proposal_ready"
        or proposal.get("execution_mode") != "n_child_dag_opt_in"
        or proposal.get("execution_requested") is not True
        or proposal.get("execution_allowed") is not False
        or proposal.get("scheduler_eligible") is not False
    ):
        return _dag_blocked("dag_proposal_invalid")
    scope_result = canonicalize_child_scopes(
        trusted_target,
        proposal.get("children"),
        target_binding=proposal.get("target_binding"),
    )
    if scope_result.get("status") != "ready":
        return _dag_blocked(str(scope_result.get("reason_code")))
    if scope_result.get("target_identity_sha256") != proposal.get(
        "target_identity_sha256"
    ):
        return _dag_blocked("dag_target_mismatch")
    if scope_result.get("children") != proposal.get("children"):
        return _dag_blocked("dag_proposal_invalid")
    expected_proposal_hash = _canonical_sha256(_proposal_hash_payload(proposal))
    if proposal.get("proposal_sha256") != expected_proposal_hash:
        return _dag_blocked("dag_proposal_invalid")
    child_grants = proposal.get("child_grants")
    if (
        isinstance(child_grants, list)
        and child_grants
        and all(isinstance(grant, dict) for grant in child_grants)
        and any(
            not _valid_future_timestamp(grant.get("approval_expires_at"))
            for grant in child_grants
        )
    ):
        return _dag_blocked("child_execution_grant_expired")
    rebuilt_proposal = build_n_child_dag_proposal(
        trusted_target,
        {
            "schema_version": proposal.get("schema_version"),
            "dag_id": proposal.get("dag_id"),
            "execution_mode": proposal.get("execution_mode"),
            "execution_requested": proposal.get("execution_requested"),
            "children": deepcopy(proposal.get("children")),
            "child_grants": deepcopy(child_grants),
            "child_prompts": deepcopy(proposal.get("child_prompts")),
            "aggregate_budget": deepcopy(proposal.get("aggregate_budget")),
            **(
                {"target_binding": deepcopy(proposal["target_binding"])}
                if "target_binding" in proposal
                else {}
            ),
        },
    )
    if rebuilt_proposal != proposal:
        return _dag_blocked("dag_proposal_invalid")
    if (
        not isinstance(approval, dict)
        or approval.get("operator_confirmed") is not True
        or not isinstance(approval.get("approval_id"), str)
        or not approval["approval_id"].strip()
        or approval["approval_id"] != approval["approval_id"].strip()
        or not _valid_future_timestamp(approval.get("expires_at"))
    ):
        return _dag_blocked("dag_approval_invalid")
    if approval["approval_id"] in {
        grant["approval_id"] for grant in proposal["child_grants"]
    }:
        return _dag_blocked("approval_id_collision")
    if (
        approval.get("dag_id") != proposal.get("dag_id")
        or approval.get("proposal_sha256") != expected_proposal_hash
    ):
        return _dag_blocked("dag_approval_binding_mismatch")

    children = proposal["children"]
    approval_expiry = _parse_timestamp(approval["expires_at"])
    child_expiries = [
        _parse_timestamp(grant["approval_expires_at"])
        for grant in child_grants
    ]
    if approval_expiry is None or any(expiry is None for expiry in child_expiries):
        return _dag_blocked("dag_approval_invalid")
    if approval_expiry > min(child_expiries):
        return _dag_blocked("dag_approval_exceeds_child_grant")

    budget = proposal["aggregate_budget"]
    return {
        **deepcopy(proposal),
        "mode": "n_child_dag_grant",
        "status": "ready",
        "reason_code": "dag_ready",
        "execution_allowed": True,
        "scheduler_eligible": True,
        "approval_id": approval["approval_id"],
        "approval_expires_at": approval["expires_at"],
        "child_ids": [child["child_id"] for child in children],
        "ready_child_ids": [
            child["child_id"] for child in children if not child["depends_on"]
        ],
        "max_external_calls": budget["max_external_calls"],
        "max_parallelism": budget["max_parallelism"],
        "max_total_elapsed_sec": float(budget["max_total_elapsed_sec"]),
        "max_output_chars": budget["max_output_chars"],
        "max_total_tokens": budget["max_total_tokens"],
        "fallback_action": "parent_review",
        "automatic_retry_allowed": False,
        "automatic_redistribution_allowed": False,
        "automatic_fallback_allowed": False,
        "automatic_resume_allowed": False,
        "replay_check_required": True,
    }
def build_n_child_dag_grant(request: dict[str, Any]) -> dict[str, Any]:
    """Build an approval-bound contract for a bounded three-to-five child DAG.

    This validates the future scheduler input only. It does not reserve budget,
    mutate a ledger, or invoke an executor.
    """
    if (
        not isinstance(request, dict)
        or request.get("execution_requested") is not True
        or request.get("execution_mode") != "n_child_dag_opt_in"
    ):
        return _dag_blocked("dag_opt_in_missing")

    dag_id = request.get("dag_id")
    children = request.get("children")
    child_grants = request.get("child_grants")
    child_prompts = request.get("child_prompts")
    budget = request.get("aggregate_budget")
    approval = request.get("dag_approval")
    if (
        request.get("schema_version") != "omc-n-child-dag/v1"
        or not isinstance(dag_id, str)
        or not dag_id.strip()
    ):
        return _dag_blocked("dag_input_invalid")
    if (
        not isinstance(children, list)
        or not 3 <= len(children) <= 5
        or not all(isinstance(child, dict) for child in children)
    ):
        return _dag_blocked("n_child_count_invalid")
    if not _dag_graph_is_valid(children):
        return _dag_blocked("dag_graph_invalid")
    if not _dag_scopes_are_disjoint(children):
        return _dag_blocked("dag_scope_overlap")

    child_ids = [child["child_id"] for child in children]
    if (
        not isinstance(child_grants, list)
        or len(child_grants) != len(children)
        or not all(
            _valid_n_child_execution_grant(grant, child_id)
            and grant.get("scope_hash") == child.get("scope_hash")
            for grant, child, child_id in zip(child_grants, children, child_ids)
        )
        or len({grant["idempotency_key"] for grant in child_grants})
        != len(child_grants)
    ):
        return _dag_blocked("child_execution_grant_invalid")
    if (
        not isinstance(child_prompts, dict)
        or set(child_prompts) != set(child_ids)
        or any(
            not isinstance(child_prompts[child_id], str)
            or not child_prompts[child_id].strip()
            for child_id in child_ids
        )
    ):
        return _dag_blocked("dag_prompt_invalid")

    if not isinstance(budget, dict):
        return _dag_blocked("dag_budget_invalid")
    max_external_calls = budget.get("max_external_calls")
    max_parallelism = budget.get("max_parallelism")
    max_elapsed = budget.get("max_total_elapsed_sec")
    max_output = budget.get("max_output_chars")
    child_elapsed_floor = _dag_critical_path_elapsed(children, child_grants)
    child_output_limit = sum(grant["max_output_chars"] for grant in child_grants)
    if (
        not isinstance(max_external_calls, int)
        or isinstance(max_external_calls, bool)
        or max_external_calls != len(children)
        or not isinstance(max_parallelism, int)
        or isinstance(max_parallelism, bool)
        or not 1 <= max_parallelism <= len(children)
        or isinstance(max_elapsed, bool)
        or not _is_finite_number(max_elapsed)
        or float(max_elapsed) < child_elapsed_floor
        or not isinstance(max_output, int)
        or isinstance(max_output, bool)
        or max_output < child_output_limit
    ):
        return _dag_blocked("dag_budget_invalid")

    graph_sha256 = _canonical_sha256(children)
    child_grant_sha256s = [_canonical_sha256(grant) for grant in child_grants]
    prompt_sha256s = {
        child_id: _canonical_sha256(child_prompts[child_id])
        for child_id in child_ids
    }
    budget_sha256 = _canonical_sha256(budget)
    if (
        not isinstance(approval, dict)
        or approval.get("operator_confirmed") is not True
        or not isinstance(approval.get("approval_id"), str)
        or not approval["approval_id"].strip()
        or not _valid_future_timestamp(approval.get("expires_at"))
    ):
        return _dag_blocked("dag_approval_invalid")
    if (
        approval.get("dag_id") != dag_id
        or approval.get("graph_sha256") != graph_sha256
        or approval.get("child_grant_sha256s") != child_grant_sha256s
        or approval.get("prompt_sha256s") != prompt_sha256s
        or approval.get("aggregate_budget_sha256") != budget_sha256
    ):
        return _dag_blocked("dag_approval_binding_mismatch")

    return {
        "schema_version": "omc-n-child-dag/v1",
        "mode": "n_child_dag_grant",
        "status": "ready",
        "reason_code": "dag_ready",
        "execution_allowed": True,
        "scheduler_eligible": False,
        "dag_id": dag_id,
        "approval_id": approval["approval_id"],
        "approval_expires_at": approval["expires_at"],
        "children": deepcopy(children),
        "child_ids": child_ids,
        "ready_child_ids": [
            child["child_id"] for child in children if not child["depends_on"]
        ],
        "child_grants": deepcopy(child_grants),
        "child_prompts": deepcopy(child_prompts),
        "graph_sha256": graph_sha256,
        "child_grant_sha256s": child_grant_sha256s,
        "prompt_sha256s": prompt_sha256s,
        "aggregate_budget_sha256": budget_sha256,
        "max_external_calls": max_external_calls,
        "max_parallelism": max_parallelism,
        "max_total_elapsed_sec": float(max_elapsed),
        "max_output_chars": max_output,
        "fallback_action": "parent_review",
        "automatic_retry_allowed": False,
        "automatic_redistribution_allowed": False,
        "automatic_fallback_allowed": False,
        "automatic_resume_allowed": False,
    }


def build_two_child_sequence_grant(request: dict[str, Any]) -> dict[str, Any]:
    """Build an explicit, approval-bound grant for exactly two children."""

    def blocked(reason_code: str) -> dict[str, Any]:
        return {
            "mode": "two_child_sequence_grant",
            "status": "blocked",
            "reason_code": reason_code,
            "execution_allowed": False,
        }

    if (
        not isinstance(request, dict)
        or request.get("execution_requested") is not True
        or request.get("execution_mode") != "two_child_sequential_opt_in"
    ):
        return blocked("sequence_opt_in_missing")
    sequence_id = request.get("sequence_id")
    children = request.get("children")
    ordered_child_ids = request.get("ordered_child_ids")
    child_grants = request.get("child_grants")
    child_prompts = request.get("child_prompts")
    approval = request.get("sequence_approval")
    budget = request.get("aggregate_budget")
    if not isinstance(sequence_id, str) or not sequence_id.strip():
        return blocked("sequence_input_invalid")
    if (
        not isinstance(children, list)
        or len(children) != 2
        or not all(isinstance(child, dict) for child in children)
    ):
        return blocked("exactly_two_children_required")
    child_ids = [child.get("child_id") for child in children]
    if (
        not all(isinstance(child_id, str) and child_id.strip() for child_id in child_ids)
        or len(set(child_ids)) != 2
        or ordered_child_ids != child_ids
        or children[0].get("depends_on") != []
        or children[1].get("depends_on") != [child_ids[0]]
    ):
        return blocked("sequence_graph_invalid")
    if (
        not isinstance(child_grants, list)
        or len(child_grants) != 2
        or not all(
            _valid_sequence_child_grant(grant, child_id)
            for grant, child_id in zip(child_grants, child_ids)
        )
    ):
        return blocked("child_execution_grant_invalid")
    if (
        not isinstance(child_prompts, dict)
        or set(child_prompts) != set(child_ids)
        or any(
            not isinstance(child_prompts[child_id], str)
            or not child_prompts[child_id].strip()
            for child_id in child_ids
        )
    ):
        return blocked("sequence_prompt_invalid")
    if not isinstance(budget, dict) or budget.get("max_external_calls") != 2:
        return blocked("sequence_budget_invalid")
    max_elapsed = budget.get("max_total_elapsed_sec")
    max_output = budget.get("max_output_chars")
    child_elapsed_limit = sum(float(grant["max_total_elapsed_sec"]) for grant in child_grants)
    child_output_limit = sum(grant["max_output_chars"] for grant in child_grants)
    if (
        not _is_finite_number(max_elapsed)
        or float(max_elapsed) < child_elapsed_limit
        or not isinstance(max_output, int)
        or isinstance(max_output, bool)
        or max_output < child_output_limit
    ):
        return blocked("sequence_budget_invalid")

    graph_sha256 = _canonical_sha256(children)
    order_sha256 = _canonical_sha256(ordered_child_ids)
    child_grant_sha256s = [_canonical_sha256(grant) for grant in child_grants]
    prompt_sha256s = {
        child_id: _canonical_sha256(child_prompts[child_id])
        for child_id in child_ids
    }
    if (
        not isinstance(approval, dict)
        or approval.get("operator_confirmed") is not True
        or approval.get("sequence_id") != sequence_id
        or not isinstance(approval.get("approval_id"), str)
        or not approval["approval_id"].strip()
        or not isinstance(approval.get("expires_at"), str)
        or approval.get("graph_sha256") != graph_sha256
        or approval.get("execution_order_sha256") != order_sha256
        or approval.get("child_grant_sha256s") != child_grant_sha256s
        or approval.get("prompt_sha256s") != prompt_sha256s
    ):
        return blocked("sequence_approval_binding_mismatch")

    return {
        "mode": "two_child_sequence_grant",
        "status": "ready",
        "reason_code": "sequence_ready",
        "execution_allowed": True,
        "sequence_id": sequence_id,
        "approval_id": approval["approval_id"],
        "approval_expires_at": approval["expires_at"],
        "children": deepcopy(children),
        "ordered_child_ids": list(ordered_child_ids),
        "child_grants": deepcopy(child_grants),
        "prompt_sha256s": prompt_sha256s,
        "graph_sha256": graph_sha256,
        "execution_order_sha256": order_sha256,
        "child_grant_sha256s": child_grant_sha256s,
        "max_external_calls": 2,
        "max_total_elapsed_sec": float(max_elapsed),
        "max_output_chars": max_output,
        "automatic_retry_allowed": False,
        "automatic_redistribution_allowed": False,
        "automatic_fallback_allowed": False,
        "automatic_resume_allowed": False,
    }


def reserve_single_child_execution_grant(
    grant: dict[str, Any],
    ledger: dict[str, Any],
    *,
    expected_scope_hash: str,
    expected_ledger_revision: int,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Return a CAS-ready ledger transition for one validated grant."""
    ledger_copy = deepcopy(ledger)

    def blocked(reason_code: str) -> dict[str, Any]:
        return {
            "status": "blocked",
            "reason_code": reason_code,
            "reservation": None,
            "ledger": ledger_copy,
        }

    entries = ledger_copy.get("entries") if isinstance(ledger_copy, dict) else None
    if (
        not isinstance(ledger_copy, dict)
        or ledger_copy.get("schema_version") != 1
        or not isinstance(ledger_copy.get("revision"), int)
        or isinstance(ledger_copy.get("revision"), bool)
        or ledger_copy["revision"] < 0
        or not isinstance(entries, list)
        or any(
            not isinstance(entry, dict)
            or not isinstance(entry.get("idempotency_key"), str)
            or not entry["idempotency_key"].strip()
            for entry in entries
        )
    ):
        return blocked("consumption_ledger_invalid")
    if (
        not isinstance(expected_ledger_revision, int)
        or isinstance(expected_ledger_revision, bool)
        or expected_ledger_revision < 0
    ):
        return blocked("expected_ledger_revision_invalid")
    if ledger_copy["revision"] != expected_ledger_revision:
        return blocked("consumption_ledger_stale")
    if not isinstance(expected_scope_hash, str) or not expected_scope_hash.strip():
        return blocked("expected_scope_missing")
    if (
        not isinstance(grant, dict)
        or grant.get("mode") != "single_child_execution_grant"
        or grant.get("status") != "ready"
        or grant.get("execution_allowed") is not True
        or grant.get("max_attempts") != 1
        or not isinstance(grant.get("max_total_elapsed_sec"), (int, float))
        or isinstance(grant.get("max_total_elapsed_sec"), bool)
        or not _is_finite_number(grant.get("max_total_elapsed_sec"))
        or grant["max_total_elapsed_sec"] <= 0
        or not isinstance(grant.get("max_output_chars"), int)
        or isinstance(grant.get("max_output_chars"), bool)
        or grant["max_output_chars"] <= 0
        or grant.get("fallback_action") != "parent_review"
    ):
        return blocked("execution_grant_invalid")

    required_text = (
        "parent_id",
        "child_id",
        "executor",
        "approval_id",
        "session_id",
        "idempotency_key",
        "scope_hash",
        "approval_expires_at",
    )
    if any(not isinstance(grant.get(field), str) or not grant[field].strip() for field in required_text):
        return blocked("execution_grant_invalid")
    if grant["scope_hash"] != expected_scope_hash:
        return blocked("grant_scope_mismatch")

    current_time = now or datetime.now(timezone.utc)
    if current_time.tzinfo is None or current_time.utcoffset() is None:
        return blocked("reservation_time_invalid")
    try:
        expires_at = datetime.fromisoformat(grant["approval_expires_at"].replace("Z", "+00:00"))
    except ValueError:
        return blocked("execution_grant_invalid")
    if expires_at.tzinfo is None or expires_at.utcoffset() is None:
        return blocked("execution_grant_invalid")
    if expires_at <= current_time:
        return blocked("grant_expired")
    if any(entry["idempotency_key"] == grant["idempotency_key"] for entry in entries):
        return blocked("duplicate_grant_consumption")

    reservation = {
        "parent_id": grant["parent_id"],
        "child_id": grant["child_id"],
        "executor": grant["executor"],
        "approval_id": grant["approval_id"],
        "session_id": grant["session_id"],
        "idempotency_key": grant["idempotency_key"],
        "scope_hash": grant["scope_hash"],
        "approval_expires_at": grant["approval_expires_at"],
        "status": "reserved",
        "reserved_at": current_time.astimezone(timezone.utc).isoformat().replace("+00:00", "Z"),
        "max_attempts": 1,
        "max_total_elapsed_sec": grant["max_total_elapsed_sec"],
        "max_output_chars": grant["max_output_chars"],
        "fallback_action": grant["fallback_action"],
    }
    max_total_tokens = grant.get("max_total_tokens")
    if max_total_tokens is not None:
        if (
            not isinstance(max_total_tokens, int)
            or isinstance(max_total_tokens, bool)
            or max_total_tokens <= 0
        ):
            return blocked("execution_grant_invalid")
        reservation["max_total_tokens"] = max_total_tokens
    entries.append(reservation)
    ledger_copy["revision"] += 1
    return {
        "status": "reserved",
        "reason_code": "grant_reserved",
        "reservation": reservation,
        "ledger": ledger_copy,
    }


def finalize_single_child_execution_reservation(
    ledger: dict[str, Any],
    *,
    idempotency_key: str,
    outcome: dict[str, Any],
    expected_ledger_revision: int,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Return a terminal CAS transition for one reserved child execution."""
    ledger_copy = deepcopy(ledger)

    def blocked(reason_code: str) -> dict[str, Any]:
        return {
            "status": "blocked",
            "reason_code": reason_code,
            "entry": None,
            "ledger": ledger_copy,
        }

    entries = ledger_copy.get("entries") if isinstance(ledger_copy, dict) else None
    if (
        not isinstance(ledger_copy, dict)
        or ledger_copy.get("schema_version") != 1
        or not isinstance(ledger_copy.get("revision"), int)
        or isinstance(ledger_copy.get("revision"), bool)
        or ledger_copy["revision"] < 0
        or not isinstance(entries, list)
        or any(
            not isinstance(entry, dict)
            or not isinstance(entry.get("idempotency_key"), str)
            or not entry["idempotency_key"].strip()
            for entry in entries
        )
    ):
        return blocked("consumption_ledger_invalid")
    if (
        not isinstance(expected_ledger_revision, int)
        or isinstance(expected_ledger_revision, bool)
        or expected_ledger_revision < 0
    ):
        return blocked("expected_ledger_revision_invalid")
    if ledger_copy["revision"] != expected_ledger_revision:
        return blocked("consumption_ledger_stale")
    if not isinstance(idempotency_key, str) or not idempotency_key.strip():
        return blocked("idempotency_key_invalid")
    if not isinstance(outcome, dict):
        return blocked("execution_outcome_invalid")

    terminal_status = outcome.get("status")
    reason_code = outcome.get("reason_code")
    elapsed_sec = outcome.get("elapsed_sec")
    output_chars = outcome.get("output_chars")
    token_usage = outcome.get("token_usage")
    if (
        not isinstance(terminal_status, str)
        or terminal_status not in {"succeeded", "failed", "timeout"}
        or not isinstance(reason_code, str)
        or not reason_code.strip()
        or not isinstance(elapsed_sec, (int, float))
        or isinstance(elapsed_sec, bool)
        or not _is_finite_number(elapsed_sec)
        or elapsed_sec < 0
        or not isinstance(output_chars, int)
        or isinstance(output_chars, bool)
        or output_chars < 0
    ):
        return blocked("execution_outcome_invalid")

    matches = [entry for entry in entries if entry["idempotency_key"] == idempotency_key]
    if len(matches) != 1:
        return blocked("execution_reservation_missing" if not matches else "consumption_ledger_invalid")
    entry = matches[0]
    # Ledgers written before status was explicit represent reserved entries.
    if entry.get("status", "reserved") not in {"reserved", "running"}:
        return blocked("execution_already_finalized")
    max_elapsed = entry.get("max_total_elapsed_sec")
    max_output = entry.get("max_output_chars")
    if (
        not isinstance(max_elapsed, (int, float))
        or isinstance(max_elapsed, bool)
        or not _is_finite_number(max_elapsed)
        or max_elapsed <= 0
        or not isinstance(max_output, int)
        or isinstance(max_output, bool)
        or max_output <= 0
    ):
        return blocked("consumption_ledger_invalid")
    if elapsed_sec > max_elapsed or output_chars > max_output:
        return blocked("execution_budget_exceeded")

    current_time = now or datetime.now(timezone.utc)
    if current_time.tzinfo is None or current_time.utcoffset() is None:
        return blocked("completion_time_invalid")
    reserved_at_text = entry.get("reserved_at")
    if not isinstance(reserved_at_text, str) or not reserved_at_text.strip():
        return blocked("consumption_ledger_invalid")
    try:
        reserved_at = datetime.fromisoformat(reserved_at_text.replace("Z", "+00:00"))
    except ValueError:
        return blocked("consumption_ledger_invalid")
    if reserved_at.tzinfo is None or reserved_at.utcoffset() is None:
        return blocked("consumption_ledger_invalid")
    if current_time < reserved_at:
        return blocked("completion_time_before_reservation")
    normalized_outcome = {
        "status": terminal_status,
        "reason_code": reason_code,
        "elapsed_sec": elapsed_sec,
        "output_chars": output_chars,
    }
    for evidence_field in ("patch_applied", "scope_violation_detected"):
        evidence_value = outcome.get(evidence_field, False)
        if not isinstance(evidence_value, bool):
            return blocked("execution_outcome_invalid")
        normalized_outcome[evidence_field] = evidence_value
    if token_usage is not None:
        if (
            not isinstance(token_usage, dict)
            or any(
                not isinstance(token_usage.get(field), int)
                or isinstance(token_usage.get(field), bool)
                or token_usage[field] < 0
                for field in ("input_tokens", "output_tokens", "total_tokens")
            )
            or token_usage["total_tokens"]
            != token_usage["input_tokens"] + token_usage["output_tokens"]
        ):
            return blocked("execution_outcome_invalid")
        normalized_outcome["token_usage"] = deepcopy(token_usage)
    parent_review = build_parent_review_recovery(
        {"status": terminal_status, "reason_code": reason_code}
    )
    if parent_review["status"] == "review_required":
        normalized_outcome["parent_review"] = parent_review
    entry.update(
        {
            "status": terminal_status,
            "completed_at": current_time.astimezone(timezone.utc).isoformat().replace("+00:00", "Z"),
            "outcome": normalized_outcome,
        }
    )
    ledger_copy["revision"] += 1
    return {
        "status": "finalized",
        "reason_code": "execution_outcome_recorded",
        "entry": entry,
        "ledger": ledger_copy,
    }


def record_single_child_parent_review_decision(
    ledger: dict[str, Any],
    *,
    idempotency_key: str,
    approval: dict[str, Any],
    expected_ledger_revision: int,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Record one operator-bound parent judgment without executing recovery."""
    ledger_copy = deepcopy(ledger)

    def blocked(reason_code: str) -> dict[str, Any]:
        return {
            "status": "blocked",
            "reason_code": reason_code,
            "decision": None,
            "entry": None,
            "ledger": ledger_copy,
        }

    entries = ledger_copy.get("entries") if isinstance(ledger_copy, dict) else None
    if (
        not isinstance(ledger_copy, dict)
        or ledger_copy.get("schema_version") != 1
        or not isinstance(ledger_copy.get("revision"), int)
        or isinstance(ledger_copy.get("revision"), bool)
        or ledger_copy["revision"] < 0
        or not isinstance(entries, list)
        or any(not isinstance(entry, dict) for entry in entries)
    ):
        return blocked("consumption_ledger_invalid")
    if (
        not isinstance(expected_ledger_revision, int)
        or isinstance(expected_ledger_revision, bool)
        or expected_ledger_revision < 0
    ):
        return blocked("expected_ledger_revision_invalid")
    if ledger_copy["revision"] != expected_ledger_revision:
        return blocked("consumption_ledger_stale")
    if not isinstance(idempotency_key, str) or not idempotency_key.strip():
        return blocked("idempotency_key_invalid")
    if not isinstance(approval, dict):
        return blocked("parent_review_approval_invalid")

    matches = [
        entry
        for entry in entries
        if entry.get("idempotency_key") == idempotency_key
    ]
    if len(matches) != 1:
        return blocked(
            "execution_reservation_missing"
            if not matches
            else "consumption_ledger_invalid"
        )
    entry = matches[0]
    if "parent_review_decision" in entry:
        return blocked("parent_review_already_decided")
    outcome = entry.get("outcome")
    if (
        entry.get("status") not in {"failed", "timeout"}
        or not isinstance(outcome, dict)
        or outcome.get("status") != entry.get("status")
    ):
        return blocked("parent_review_not_required")
    expected_review = build_parent_review_recovery(
        {"status": outcome.get("status"), "reason_code": outcome.get("reason_code")}
    )
    if (
        expected_review.get("status") != "review_required"
        or outcome.get("parent_review") != expected_review
    ):
        return blocked("parent_review_not_required")

    decision = approval.get("decision")
    if not isinstance(decision, str) or decision not in {"acknowledge", "hold"}:
        return blocked("parent_review_decision_invalid")
    required_text = (
        "approval_id",
        "parent_id",
        "child_id",
        "scope_hash",
        "idempotency_key",
        "recovery_action",
        "expires_at",
    )
    if (
        approval.get("operator_confirmed") is not True
        or approval.get("approval_status") != "approved"
        or any(
            not isinstance(approval.get(field), str)
            or not approval[field].strip()
            for field in required_text
        )
    ):
        return blocked("parent_review_approval_invalid")
    if (
        approval["parent_id"] != entry.get("parent_id")
        or approval["child_id"] != entry.get("child_id")
        or approval["scope_hash"] != entry.get("scope_hash")
        or approval["idempotency_key"] != idempotency_key
        or approval["recovery_action"] != expected_review["recovery_action"]
    ):
        return blocked("parent_review_approval_binding_mismatch")

    current_time = now or datetime.now(timezone.utc)
    if current_time.tzinfo is None or current_time.utcoffset() is None:
        return blocked("parent_review_decision_time_invalid")
    try:
        expires_at = datetime.fromisoformat(
            approval["expires_at"].replace("Z", "+00:00")
        )
        completed_at = datetime.fromisoformat(
            str(entry.get("completed_at", "")).replace("Z", "+00:00")
        )
    except ValueError:
        return blocked("parent_review_approval_invalid")
    if (
        expires_at.tzinfo is None
        or expires_at.utcoffset() is None
        or completed_at.tzinfo is None
        or completed_at.utcoffset() is None
    ):
        return blocked("parent_review_approval_invalid")
    if current_time >= expires_at:
        return blocked("parent_review_approval_expired")
    if current_time < completed_at:
        return blocked("parent_review_decision_time_invalid")

    decision_record = {
        "status": "recorded",
        "decision": decision,
        "approval_id": approval["approval_id"],
        "decided_at": current_time.astimezone(timezone.utc)
        .isoformat()
        .replace("+00:00", "Z"),
        "recovery_action": expected_review["recovery_action"],
        "automatic_retry_allowed": False,
        "automatic_redistribution_allowed": False,
    }
    entry["parent_review_decision"] = decision_record
    ledger_copy["revision"] += 1
    return {
        "status": "recorded",
        "reason_code": "parent_review_decision_recorded",
        "decision": decision_record,
        "entry": entry,
        "ledger": ledger_copy,
    }


def record_single_child_parent_review_followup(
    ledger: dict[str, Any],
    *,
    idempotency_key: str,
    followup: dict[str, Any],
    expected_ledger_revision: int,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Record one operator-observed outcome after a parent-review decision."""
    ledger_copy = deepcopy(ledger)

    def blocked(reason_code: str) -> dict[str, Any]:
        return {
            "status": "blocked",
            "reason_code": reason_code,
            "followup": None,
            "entry": None,
            "ledger": ledger_copy,
        }

    entries = ledger_copy.get("entries") if isinstance(ledger_copy, dict) else None
    if (
        not isinstance(ledger_copy, dict)
        or ledger_copy.get("schema_version") != 1
        or not isinstance(ledger_copy.get("revision"), int)
        or isinstance(ledger_copy.get("revision"), bool)
        or ledger_copy["revision"] < 0
        or not isinstance(entries, list)
        or any(not isinstance(entry, dict) for entry in entries)
    ):
        return blocked("consumption_ledger_invalid")
    if (
        not isinstance(expected_ledger_revision, int)
        or isinstance(expected_ledger_revision, bool)
        or expected_ledger_revision < 0
    ):
        return blocked("expected_ledger_revision_invalid")
    if ledger_copy["revision"] != expected_ledger_revision:
        return blocked("consumption_ledger_stale")
    if not isinstance(idempotency_key, str) or not idempotency_key.strip():
        return blocked("idempotency_key_invalid")
    if not isinstance(followup, dict):
        return blocked("parent_review_followup_invalid")

    matches = [
        entry
        for entry in entries
        if entry.get("idempotency_key") == idempotency_key
    ]
    if len(matches) != 1:
        return blocked(
            "execution_reservation_missing"
            if not matches
            else "consumption_ledger_invalid"
        )
    entry = matches[0]
    if "parent_review_followup" in entry:
        return blocked("parent_review_followup_already_recorded")
    decision = entry.get("parent_review_decision")
    outcome = entry.get("outcome")
    expected_review = (
        build_parent_review_recovery(
            {
                "status": outcome.get("status"),
                "reason_code": outcome.get("reason_code"),
            }
        )
        if isinstance(outcome, dict)
        else {}
    )
    decision_value = decision.get("decision") if isinstance(decision, dict) else None
    if (
        not isinstance(decision, dict)
        or decision.get("status") != "recorded"
        or not isinstance(decision_value, str)
        or decision_value not in {"acknowledge", "hold"}
        or not isinstance(decision.get("approval_id"), str)
        or not decision["approval_id"].strip()
        or entry.get("status") not in {"failed", "timeout"}
        or not isinstance(outcome, dict)
        or outcome.get("status") != entry.get("status")
        or expected_review.get("status") != "review_required"
        or outcome.get("parent_review") != expected_review
        or decision.get("recovery_action") != expected_review["recovery_action"]
        or decision.get("automatic_retry_allowed") is not False
        or decision.get("automatic_redistribution_allowed") is not False
    ):
        return blocked("parent_review_decision_missing")

    required_text = (
        "followup_id",
        "approval_id",
        "parent_id",
        "child_id",
        "scope_hash",
        "idempotency_key",
        "reason_code",
    )
    followup_outcome = followup.get("outcome")
    if (
        followup.get("operator_confirmed") is not True
        or any(
            not isinstance(followup.get(field), str)
            or not followup[field].strip()
            for field in required_text
        )
        or not isinstance(followup_outcome, str)
        or followup_outcome not in {"resolved", "still_blocked", "escalated"}
    ):
        return blocked("parent_review_followup_invalid")
    if (
        followup.get("automatic_retry_performed") is not False
        or followup.get("automatic_redistribution_performed") is not False
    ):
        return blocked("parent_review_followup_forbidden_automation")
    if (
        followup["approval_id"] != decision["approval_id"]
        or followup["parent_id"] != entry.get("parent_id")
        or followup["child_id"] != entry.get("child_id")
        or followup["scope_hash"] != entry.get("scope_hash")
        or followup["idempotency_key"] != idempotency_key
    ):
        return blocked("parent_review_followup_binding_mismatch")
    if any(
        candidate.get("parent_review_followup", {}).get("followup_id")
        == followup["followup_id"]
        for candidate in entries
        if isinstance(candidate.get("parent_review_followup"), dict)
    ):
        return blocked("parent_review_followup_id_reused")

    current_time = now or datetime.now(timezone.utc)
    if (
        not isinstance(current_time, datetime)
        or current_time.tzinfo is None
        or current_time.utcoffset() is None
    ):
        return blocked("parent_review_followup_time_invalid")
    try:
        decided_at = datetime.fromisoformat(
            str(decision.get("decided_at", "")).replace("Z", "+00:00")
        )
    except ValueError:
        return blocked("parent_review_decision_missing")
    if decided_at.tzinfo is None or decided_at.utcoffset() is None:
        return blocked("parent_review_decision_missing")
    if current_time < decided_at:
        return blocked("parent_review_followup_time_invalid")

    followup_record = {
        "status": "recorded",
        "followup_id": followup["followup_id"],
        "approval_id": followup["approval_id"],
        "outcome": followup_outcome,
        "reason_code": followup["reason_code"],
        "observed_at": current_time.astimezone(timezone.utc)
        .isoformat()
        .replace("+00:00", "Z"),
        "automatic_retry_performed": False,
        "automatic_redistribution_performed": False,
    }
    entry["parent_review_followup"] = followup_record
    ledger_copy["revision"] += 1
    return {
        "status": "recorded",
        "reason_code": "parent_review_followup_recorded",
        "followup": followup_record,
        "entry": entry,
        "ledger": ledger_copy,
    }


def _claim_single_child_execution_reservation_file(
    grant: dict[str, Any],
    ledger_path: str | Path,
    *,
    now: Callable[[], datetime],
    sequence_expires_at: datetime | None = None,
) -> dict[str, Any]:
    """Atomically claim a reserved grant before any executor side effect."""
    path = Path(ledger_path)
    lock_path = path.with_name(f"{path.name}.lock")

    def blocked(reason_code: str) -> dict[str, Any]:
        return {
            "status": "blocked",
            "reason_code": reason_code,
            "entry": None,
            "ledger": None,
        }

    if path.is_symlink() or lock_path.is_symlink():
        return blocked("consumption_ledger_path_invalid")
    if not path.exists():
        return blocked("consumption_ledger_read_failed")

    lock_flags = os.O_CREAT | os.O_RDWR
    if hasattr(os, "O_NOFOLLOW"):
        lock_flags |= os.O_NOFOLLOW
    try:
        lock_fd = os.open(lock_path, lock_flags, 0o600)
    except OSError:
        return blocked("consumption_ledger_lock_failed")

    temp_path: Path | None = None
    replace_completed = False
    try:
        with os.fdopen(lock_fd, "r+", encoding="utf-8") as lock_file:
            fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX)
            try:
                current_time = now()
            except Exception:
                return blocked("claim_time_invalid")
            if (
                not isinstance(current_time, datetime)
                or current_time.tzinfo is None
                or current_time.utcoffset() is None
            ):
                return blocked("claim_time_invalid")
            if sequence_expires_at is not None:
                if (
                    not isinstance(sequence_expires_at, datetime)
                    or sequence_expires_at.tzinfo is None
                    or sequence_expires_at.utcoffset() is None
                ):
                    return blocked("sequence_grant_invalid")
                if sequence_expires_at <= current_time:
                    return blocked("sequence_grant_expired")
            if path.is_symlink():
                return blocked("consumption_ledger_path_invalid")
            try:
                ledger = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, UnicodeError, json.JSONDecodeError):
                return blocked("consumption_ledger_read_failed")
            entries = ledger.get("entries") if isinstance(ledger, dict) else None
            revision = ledger.get("revision") if isinstance(ledger, dict) else None
            if (
                not isinstance(ledger, dict)
                or ledger.get("schema_version") != 1
                or not isinstance(revision, int)
                or isinstance(revision, bool)
                or not isinstance(entries, list)
                or any(
                    not isinstance(candidate, dict)
                    or not isinstance(candidate.get("idempotency_key"), str)
                    or not candidate["idempotency_key"].strip()
                    for candidate in entries
                )
            ):
                return blocked("consumption_ledger_invalid")
            if (
                not isinstance(grant, dict)
                or grant.get("mode") != "single_child_execution_grant"
                or grant.get("status") != "ready"
                or grant.get("execution_allowed") is not True
                or grant.get("max_attempts") != 1
                or not isinstance(grant.get("max_total_elapsed_sec"), (int, float))
                or isinstance(grant.get("max_total_elapsed_sec"), bool)
                or not _is_finite_number(grant.get("max_total_elapsed_sec"))
                or grant["max_total_elapsed_sec"] <= 0
                or not isinstance(grant.get("max_output_chars"), int)
                or isinstance(grant.get("max_output_chars"), bool)
                or grant["max_output_chars"] <= 0
            ):
                return blocked("execution_grant_invalid")
            idempotency_key = grant.get("idempotency_key")
            matches = [
                entry
                for entry in entries
                if isinstance(entry, dict) and entry.get("idempotency_key") == idempotency_key
            ]
            if len(matches) != 1:
                return blocked("execution_reservation_missing")
            entry = matches[0]
            if entry.get("status", "reserved") != "reserved":
                return blocked("execution_already_claimed")
            binding_fields = (
                "parent_id",
                "child_id",
                "executor",
                "approval_id",
                "session_id",
                "idempotency_key",
                "scope_hash",
                "approval_expires_at",
                "max_attempts",
                "max_total_elapsed_sec",
                "max_output_chars",
                "fallback_action",
            )
            if any(entry.get(field) != grant.get(field) for field in binding_fields):
                return blocked("execution_reservation_mismatch")
            if "max_total_tokens" in grant and entry.get("max_total_tokens") != grant.get(
                "max_total_tokens"
            ):
                return blocked("execution_reservation_mismatch")
            try:
                expires_at = datetime.fromisoformat(grant["approval_expires_at"].replace("Z", "+00:00"))
            except (AttributeError, ValueError):
                return blocked("execution_grant_invalid")
            if expires_at.tzinfo is None or expires_at.utcoffset() is None:
                return blocked("execution_grant_invalid")
            if expires_at <= current_time:
                return blocked("grant_expired")

            entry.update(
                {
                    "status": "running",
                    "started_at": current_time.astimezone(timezone.utc).isoformat().replace("+00:00", "Z"),
                    "attempt_count": 1,
                }
            )
            ledger["revision"] = revision + 1
            fd, raw_temp_path = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
            temp_path = Path(raw_temp_path)
            try:
                os.fchmod(fd, 0o600)
                with os.fdopen(fd, "w", encoding="utf-8") as temp_file:
                    json.dump(
                        ledger,
                        temp_file,
                        ensure_ascii=False,
                        sort_keys=True,
                        separators=(",", ":"),
                    )
                    temp_file.write("\n")
                    temp_file.flush()
                    os.fsync(temp_file.fileno())
                os.replace(temp_path, path)
                replace_completed = True
                temp_path = None
                directory_fd = os.open(path.parent, os.O_RDONLY)
                try:
                    os.fsync(directory_fd)
                finally:
                    os.close(directory_fd)
            except OSError:
                if replace_completed:
                    try:
                        persisted_ledger = json.loads(path.read_text(encoding="utf-8"))
                    except (OSError, UnicodeError, json.JSONDecodeError):
                        persisted_ledger = None
                    persisted_entry = None
                    if isinstance(persisted_ledger, dict) and isinstance(persisted_ledger.get("entries"), list):
                        persisted_entry = next(
                            (
                                candidate
                                for candidate in persisted_ledger["entries"]
                                if isinstance(candidate, dict)
                                and candidate.get("idempotency_key") == grant.get("idempotency_key")
                            ),
                            None,
                        )
                    return {
                        "status": "indeterminate",
                        "reason_code": "execution_claim_durability_unknown",
                        "entry": persisted_entry,
                        "ledger": persisted_ledger,
                    }
                return blocked("consumption_ledger_write_failed")
            return {
                "status": "claimed",
                "reason_code": "execution_claimed",
                "entry": deepcopy(entry),
                "ledger": ledger,
            }
    finally:
        if temp_path is not None:
            try:
                temp_path.unlink()
            except FileNotFoundError:
                pass


def execute_reserved_single_child_grant_file(
    grant: dict[str, Any],
    ledger_path: str | Path,
    *,
    prompt: str,
    project_root: str | Path,
    runner: Callable[..., dict[str, Any]] | None = None,
    monotonic: Callable[[], float] = time.monotonic,
    now: Callable[[], datetime] = lambda: datetime.now(timezone.utc),
    sequence_expires_at: datetime | None = None,
    require_token_usage: bool = False,
) -> dict[str, Any]:
    """Claim, execute once, and terminalize one bounded child grant.

    The injected runner is the provider boundary. This adapter never selects a
    fallback executor and never retries after a claim.
    """
    if not isinstance(prompt, str) or not prompt.strip():
        return {
            "status": "blocked",
            "reason_code": "execution_input_invalid",
            "external_call_performed": False,
        }
    if runner is None:
        from omc_exec import run_headless_executor_once

        runner = run_headless_executor_once
    if not callable(runner):
        return {
            "status": "blocked",
            "reason_code": "execution_input_invalid",
            "external_call_performed": False,
        }
    claim = _claim_single_child_execution_reservation_file(
        grant,
        ledger_path,
        now=now,
        sequence_expires_at=sequence_expires_at,
    )
    if claim["status"] != "claimed":
        return _with_parent_review_recovery(
            {**claim, "external_call_performed": False}
        )

    max_elapsed = float(grant["max_total_elapsed_sec"])
    max_output = int(grant["max_output_chars"])
    max_total_tokens = grant.get("max_total_tokens") if require_token_usage else None
    started = monotonic()
    output = ""
    raw_output = ""
    terminal_status = "failed"
    reason_code = "executor_result_invalid"
    external_call_performed = False
    token_usage: dict[str, int] | None = None
    patch_applied = False
    scope_violation_detected = False
    try:
        external_call_performed = True
        runner_kwargs = {
            "executor": grant["executor"],
            "prompt": prompt,
            "project_root": Path(project_root),
            "timeout_sec": grant["max_total_elapsed_sec"],
        }
        if require_token_usage:
            runner_kwargs["max_total_tokens"] = max_total_tokens
            runner_kwargs["max_output_chars"] = max_output
        runner_result = runner(**runner_kwargs)
        if not isinstance(runner_result, dict):
            raise TypeError("runner result must be a mapping")
        returncode = runner_result.get("returncode")
        candidate_output = runner_result.get("output", "")
        if not isinstance(returncode, int) or isinstance(returncode, bool) or not isinstance(candidate_output, str):
            raise TypeError("runner result contract invalid")
        raw_output = candidate_output
        output = raw_output[:max_output]
        candidate_usage = runner_result.get("token_usage")
        patch_applied = runner_result.get("patch_applied") is True
        scope_violation_detected = runner_result.get("scope_violation_detected") is True
        if isinstance(candidate_usage, dict) and all(
            isinstance(candidate_usage.get(field), int)
            and not isinstance(candidate_usage.get(field), bool)
            and candidate_usage[field] >= 0
            for field in ("input_tokens", "output_tokens", "total_tokens")
        ) and candidate_usage["total_tokens"] == (
            candidate_usage["input_tokens"] + candidate_usage["output_tokens"]
        ):
            token_usage = {
                field: candidate_usage[field]
                for field in ("input_tokens", "output_tokens", "total_tokens")
            }
        if returncode == 124:
            terminal_status = "timeout"
            reason_code = "executor_timeout"
        elif returncode != 0:
            terminal_status = "failed"
            candidate_reason = runner_result.get("reason_code")
            reason_code = (
                candidate_reason
                if candidate_reason in {
                    "scope_policy_violation",
                    "scope_patch_conflict",
                    "scope_patch_apply_failed",
                }
                else "executor_failed"
            )
        elif require_token_usage and token_usage is None:
            terminal_status = "failed"
            reason_code = "token_usage_unavailable"
        elif require_token_usage and token_usage["total_tokens"] > max_total_tokens:
            terminal_status = "failed"
            reason_code = "provider_token_limit_violated"
        else:
            terminal_status = "succeeded"
            reason_code = "executor_completed"
    except TimeoutError:
        terminal_status = "timeout"
        reason_code = "executor_timeout"
    except Exception:
        terminal_status = "failed"
        reason_code = "executor_exception"
    observed_elapsed = max(0.0, monotonic() - started)
    if observed_elapsed > max_elapsed:
        terminal_status = "timeout"
        reason_code = "executor_timeout"
    recorded_elapsed = min(observed_elapsed, max_elapsed)
    finalized = finalize_single_child_execution_reservation_file(
        ledger_path,
        idempotency_key=grant["idempotency_key"],
        outcome={
            "status": terminal_status,
            "reason_code": reason_code,
            "elapsed_sec": recorded_elapsed,
            "output_chars": len(output),
            "patch_applied": patch_applied,
            "scope_violation_detected": scope_violation_detected,
            **({"token_usage": token_usage} if token_usage is not None else {}),
        },
        now=now(),
    )
    if finalized["status"] not in {"finalized", "indeterminate"}:
        return _with_parent_review_recovery({
            **finalized,
            "execution_status": terminal_status,
            "execution_reason_code": reason_code,
            "output": output,
            "output_truncated": len(output) < len(raw_output),
            "observed_elapsed_sec": observed_elapsed,
            "recorded_elapsed_sec": recorded_elapsed,
            "output_chars": len(output),
            "external_call_performed": external_call_performed,
            "token_usage": token_usage,
        })
    if finalized["status"] == "indeterminate":
        return _with_parent_review_recovery({
            "status": "indeterminate",
            "reason_code": finalized["reason_code"],
            "execution_status": terminal_status,
            "execution_reason_code": reason_code,
            "output": output,
            "output_truncated": len(output) < len(raw_output),
            "observed_elapsed_sec": observed_elapsed,
            "recorded_elapsed_sec": recorded_elapsed,
            "output_chars": len(output),
            "external_call_performed": external_call_performed,
            "token_usage": token_usage,
            "ledger_status": finalized["status"],
            "entry": finalized["entry"],
        })
    return _with_parent_review_recovery({
        "status": terminal_status,
        "reason_code": reason_code,
        "output": output,
        "output_truncated": len(output) < len(raw_output),
        "observed_elapsed_sec": observed_elapsed,
        "recorded_elapsed_sec": recorded_elapsed,
        "output_chars": len(output),
        "external_call_performed": external_call_performed,
        "token_usage": token_usage,
        "ledger_status": finalized["status"],
        "entry": finalized["entry"],
    })


def _persist_two_child_sequence_state(
    ledger_path: str | Path,
    state: dict[str, Any],
    *,
    create: bool,
) -> dict[str, Any]:
    path = Path(ledger_path)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
    except OSError:
        return {"status": "indeterminate", "reason_code": "sequence_ledger_write_failed"}
    lock_path = path.with_name(f"{path.name}.lock")
    if path.is_symlink() or lock_path.is_symlink():
        return {"status": "blocked", "reason_code": "sequence_ledger_path_invalid"}
    lock_flags = os.O_CREAT | os.O_RDWR
    if hasattr(os, "O_NOFOLLOW"):
        lock_flags |= os.O_NOFOLLOW
    try:
        lock_fd = os.open(lock_path, lock_flags, 0o600)
    except OSError:
        return {"status": "blocked", "reason_code": "sequence_ledger_lock_failed"}

    temp_path: Path | None = None
    try:
        with os.fdopen(lock_fd, "r+", encoding="utf-8") as lock_file:
            fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX)
            if path.is_symlink():
                return {"status": "blocked", "reason_code": "sequence_ledger_path_invalid"}
            if create and path.exists():
                return {"status": "blocked", "reason_code": "sequence_already_started"}
            if not create:
                try:
                    current = json.loads(path.read_text(encoding="utf-8"))
                except (OSError, UnicodeError, json.JSONDecodeError):
                    return {"status": "indeterminate", "reason_code": "sequence_ledger_read_failed"}
                current_sequence = current.get("sequence") if isinstance(current, dict) else None
                if (
                    not isinstance(current, dict)
                    or current.get("schema_version") != 1
                    or not isinstance(current.get("revision"), int)
                    or isinstance(current.get("revision"), bool)
                    or not isinstance(current_sequence, dict)
                    or current_sequence.get("sequence_id") != state.get("sequence_id")
                ):
                    return {"status": "indeterminate", "reason_code": "sequence_ledger_invalid"}
                revision = current["revision"] + 1
            else:
                revision = 1
            ledger = {
                "schema_version": 1,
                "revision": revision,
                "sequence": deepcopy(state),
            }
            try:
                fd, raw_temp_path = tempfile.mkstemp(
                    prefix=f".{path.name}.", suffix=".tmp", dir=path.parent
                )
                temp_path = Path(raw_temp_path)
                os.fchmod(fd, 0o600)
                with os.fdopen(fd, "w", encoding="utf-8") as temp_file:
                    json.dump(
                        ledger,
                        temp_file,
                        ensure_ascii=False,
                        sort_keys=True,
                        separators=(",", ":"),
                    )
                    temp_file.write("\n")
                    temp_file.flush()
                    os.fsync(temp_file.fileno())
                os.replace(temp_path, path)
                temp_path = None
                directory_fd = os.open(path.parent, os.O_RDONLY)
                try:
                    os.fsync(directory_fd)
                finally:
                    os.close(directory_fd)
            except OSError:
                return {"status": "indeterminate", "reason_code": "sequence_ledger_write_failed"}
            return {"status": "persisted", "reason_code": "sequence_state_persisted", "ledger": ledger}
    except OSError:
        return {"status": "indeterminate", "reason_code": "sequence_ledger_lock_failed"}
    finally:
        if temp_path is not None:
            try:
                temp_path.unlink()
            except FileNotFoundError:
                pass


def _with_sequence_parent_review(result: dict[str, Any]) -> dict[str, Any]:
    if result.get("status") not in {"blocked", "indeterminate"}:
        return result
    return {
        **result,
        "parent_review": {
            "status": "review_required",
            "reason_code": "sequence_state_requires_review",
            "sequence_reason_code": result.get("reason_code", "sequence_state_unknown"),
        },
    }


def _two_child_sequence_result(state: dict[str, Any]) -> dict[str, Any]:
    children = state["children"]
    completed = [child["child_id"] for child in children if child["status"] == "succeeded"]
    pending = [child["child_id"] for child in children if child["status"] == "not_started"]
    failed = next(
        (child["child_id"] for child in children if child["status"] in {"failed", "timeout", "blocked", "indeterminate"}),
        None,
    )
    result = {
        "status": state["status"],
        "reason_code": state["reason_code"],
        "sequence_id": state["sequence_id"],
        "children": deepcopy(children),
        "completed_child_ids": completed,
        "pending_child_ids": pending,
        "failed_child_id": failed,
        "external_call_count": state["external_call_count"],
        "total_elapsed_sec": state["total_elapsed_sec"],
        "total_output_chars": state["total_output_chars"],
    }
    if state.get("parent_review") is not None:
        result["parent_review"] = deepcopy(state["parent_review"])
    return result


def execute_two_child_sequence_grant_file(
    grant: dict[str, Any],
    sequence_ledger_path: str | Path,
    single_child_ledger_path: str | Path,
    *,
    prompts: dict[str, str],
    project_root: str | Path,
    runner: Callable[..., dict[str, Any]] | None = None,
    monotonic: Callable[[], float] = time.monotonic,
    now: Callable[[], datetime] = lambda: datetime.now(timezone.utc),
) -> dict[str, Any]:
    """Execute an approved two-child sequence without retry or auto-resume."""
    if (
        not isinstance(grant, dict)
        or grant.get("mode") != "two_child_sequence_grant"
        or grant.get("status") != "ready"
        or grant.get("execution_allowed") is not True
        or grant.get("max_external_calls") != 2
        or any(
            grant.get(flag) is not False
            for flag in (
                "automatic_retry_allowed",
                "automatic_redistribution_allowed",
                "automatic_fallback_allowed",
                "automatic_resume_allowed",
            )
        )
    ):
        return {"status": "blocked", "reason_code": "sequence_grant_invalid"}
    if Path(sequence_ledger_path) == Path(single_child_ledger_path):
        return {"status": "blocked", "reason_code": "separate_sequence_ledger_required"}
    child_ids = grant.get("ordered_child_ids")
    child_grants = grant.get("child_grants")
    if (
        not isinstance(child_ids, list)
        or len(child_ids) != 2
        or not isinstance(child_grants, list)
        or len(child_grants) != 2
        or not isinstance(prompts, dict)
        or set(prompts) != set(child_ids)
        or any(
            not isinstance(prompts[child_id], str)
            or not prompts[child_id].strip()
            or grant.get("prompt_sha256s", {}).get(child_id) != _canonical_sha256(prompts[child_id])
            for child_id in child_ids
        )
    ):
        return {"status": "blocked", "reason_code": "sequence_input_invalid"}
    if (
        grant.get("graph_sha256") != _canonical_sha256(grant.get("children"))
        or grant.get("execution_order_sha256") != _canonical_sha256(child_ids)
        or grant.get("child_grant_sha256s")
        != [_canonical_sha256(child_grant) for child_grant in child_grants]
        or any(
            not _valid_sequence_child_grant(child_grant, child_id)
            for child_grant, child_id in zip(child_grants, child_ids)
        )
    ):
        return {"status": "blocked", "reason_code": "sequence_grant_binding_mismatch"}
    try:
        current_time = now()
    except Exception:
        return {"status": "blocked", "reason_code": "sequence_time_invalid"}
    try:
        expires_at = datetime.fromisoformat(grant["approval_expires_at"].replace("Z", "+00:00"))
    except (AttributeError, ValueError):
        return {"status": "blocked", "reason_code": "sequence_grant_invalid"}
    if (
        not isinstance(current_time, datetime)
        or current_time.tzinfo is None
        or current_time.utcoffset() is None
        or expires_at.tzinfo is None
        or expires_at <= current_time
    ):
        return {"status": "blocked", "reason_code": "sequence_grant_expired"}

    state = {
        "sequence_id": grant["sequence_id"],
        "status": "reserved",
        "reason_code": "sequence_reserved",
        "graph_sha256": grant["graph_sha256"],
        "execution_order_sha256": grant["execution_order_sha256"],
        "external_call_count": 0,
        "total_elapsed_sec": 0.0,
        "total_output_chars": 0,
        "parent_review": None,
        "children": [
            {"child_id": child_id, "status": "not_started", "reason_code": "awaiting_dependency" if index else "ready"}
            for index, child_id in enumerate(child_ids)
        ],
    }
    persisted = _persist_two_child_sequence_state(sequence_ledger_path, state, create=True)
    if persisted["status"] != "persisted":
        return _with_sequence_parent_review(persisted)

    for index, (child_id, child_grant) in enumerate(zip(child_ids, child_grants)):
        try:
            child_start_time = current_time if index == 0 else now()
        except Exception:
            child_start_time = None
        if (
            not isinstance(child_start_time, datetime)
            or child_start_time.tzinfo is None
            or child_start_time.utcoffset() is None
            or expires_at <= child_start_time
        ):
            state["status"] = "review_required"
            state["reason_code"] = (
                "sequence_grant_expired"
                if isinstance(child_start_time, datetime)
                and child_start_time.tzinfo is not None
                and child_start_time.utcoffset() is not None
                else "sequence_time_invalid"
            )
            state["children"][index]["reason_code"] = state["reason_code"]
            state["parent_review"] = {
                "status": "review_required",
                "reason_code": "sequence_child_requires_review",
                "failed_child_id": child_id,
            }
            persisted = _persist_two_child_sequence_state(
                sequence_ledger_path, state, create=False
            )
            return (
                _with_sequence_parent_review(persisted)
                if persisted["status"] != "persisted"
                else _two_child_sequence_result(state)
            )
        state["status"] = "running"
        state["reason_code"] = "child_running"
        state["children"][index].update({"status": "running", "reason_code": "execution_claim_pending"})
        persisted = _persist_two_child_sequence_state(sequence_ledger_path, state, create=False)
        if persisted["status"] != "persisted":
            return _with_sequence_parent_review(persisted)
        reservation = reserve_single_child_execution_grant_file(
            child_grant,
            single_child_ledger_path,
            expected_scope_hash=child_grant["scope_hash"],
            now=child_start_time,
        )
        if reservation["status"] != "reserved":
            child_result = {
                "status": "indeterminate" if reservation["status"] == "indeterminate" else "blocked",
                "reason_code": reservation["reason_code"],
                "external_call_performed": False,
            }
        else:
            child_result = execute_reserved_single_child_grant_file(
                child_grant,
                single_child_ledger_path,
                prompt=prompts[child_id],
                project_root=project_root,
                runner=runner,
                monotonic=monotonic,
                now=now,
                sequence_expires_at=expires_at,
            )
        if child_result.get("external_call_performed") is True:
            state["external_call_count"] += 1
        entry = child_result.get("entry") if isinstance(child_result, dict) else None
        outcome = entry.get("outcome") if isinstance(entry, dict) else None
        if isinstance(outcome, dict):
            elapsed = outcome.get("elapsed_sec", 0.0)
            output_chars = outcome.get("output_chars", 0)
            usage_durability = (
                "durable"
                if child_result.get("ledger_status") == "finalized"
                else "durability_unknown"
            )
        elif child_result.get("external_call_performed") is True:
            elapsed = child_result.get("recorded_elapsed_sec", 0.0)
            output_chars = child_result.get("output_chars", 0)
            usage_durability = "observed_only"
        else:
            elapsed = 0.0
            output_chars = 0
            usage_durability = "not_applicable"
        state["total_elapsed_sec"] += float(elapsed) if _is_finite_number(elapsed) else 0.0
        state["total_output_chars"] += output_chars if isinstance(output_chars, int) else 0
        state["children"][index].update(
            {
                "status": child_result.get("status", "indeterminate"),
                "reason_code": child_result.get("reason_code", "execution_result_invalid"),
                "elapsed_sec": elapsed,
                "output_chars": output_chars,
                "usage_durability": usage_durability,
            }
        )
        if child_result.get("status") != "succeeded":
            state["status"] = "indeterminate" if child_result.get("status") == "indeterminate" else "review_required"
            state["reason_code"] = "child_not_succeeded"
            state["parent_review"] = child_result.get("parent_review") or {
                "status": "review_required",
                "reason_code": "sequence_child_requires_review",
                "failed_child_id": child_id,
            }
            if index == 0:
                state["children"][1]["reason_code"] = "dependency_not_succeeded"
            persisted = _persist_two_child_sequence_state(sequence_ledger_path, state, create=False)
            return (
                _with_sequence_parent_review(persisted)
                if persisted["status"] != "persisted"
                else _two_child_sequence_result(state)
            )

        persisted = _persist_two_child_sequence_state(sequence_ledger_path, state, create=False)
        if persisted["status"] != "persisted":
            return _with_sequence_parent_review(persisted)

    state["status"] = "completed"
    state["reason_code"] = "sequence_completed"
    persisted = _persist_two_child_sequence_state(sequence_ledger_path, state, create=False)
    return (
        _with_sequence_parent_review(persisted)
        if persisted["status"] != "persisted"
        else _two_child_sequence_result(state)
    )


def finalize_single_child_execution_reservation_file(
    ledger_path: str | Path,
    *,
    idempotency_key: str,
    outcome: dict[str, Any],
    now: datetime | None = None,
) -> dict[str, Any]:
    """Persist one terminal execution outcome under an exclusive file lock."""
    path = Path(ledger_path)
    lock_path = path.with_name(f"{path.name}.lock")

    def blocked(reason_code: str) -> dict[str, Any]:
        return {
            "status": "blocked",
            "reason_code": reason_code,
            "entry": None,
            "ledger": None,
        }

    if path.is_symlink() or lock_path.is_symlink():
        return blocked("consumption_ledger_path_invalid")
    if not path.exists():
        return blocked("consumption_ledger_read_failed")

    lock_flags = os.O_CREAT | os.O_RDWR
    if hasattr(os, "O_NOFOLLOW"):
        lock_flags |= os.O_NOFOLLOW
    try:
        lock_fd = os.open(lock_path, lock_flags, 0o600)
    except OSError:
        return blocked("consumption_ledger_lock_failed")

    temp_path: Path | None = None
    replace_completed = False
    try:
        with os.fdopen(lock_fd, "r+", encoding="utf-8") as lock_file:
            fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX)
            if path.is_symlink():
                return blocked("consumption_ledger_path_invalid")
            try:
                ledger = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, UnicodeError, json.JSONDecodeError):
                return blocked("consumption_ledger_read_failed")

            revision = ledger.get("revision") if isinstance(ledger, dict) else None
            if not isinstance(revision, int) or isinstance(revision, bool):
                return blocked("consumption_ledger_invalid")
            result = finalize_single_child_execution_reservation(
                ledger,
                idempotency_key=idempotency_key,
                outcome=outcome,
                expected_ledger_revision=revision,
                now=now,
            )
            if result["status"] != "finalized":
                return result

            fd, raw_temp_path = tempfile.mkstemp(
                prefix=f".{path.name}.",
                suffix=".tmp",
                dir=path.parent,
            )
            temp_path = Path(raw_temp_path)
            try:
                os.fchmod(fd, 0o600)
                with os.fdopen(fd, "w", encoding="utf-8") as temp_file:
                    json.dump(
                        result["ledger"],
                        temp_file,
                        ensure_ascii=False,
                        sort_keys=True,
                        separators=(",", ":"),
                    )
                    temp_file.write("\n")
                    temp_file.flush()
                    os.fsync(temp_file.fileno())
                os.replace(temp_path, path)
                replace_completed = True
                temp_path = None
                directory_fd = os.open(path.parent, os.O_RDONLY)
                try:
                    os.fsync(directory_fd)
                finally:
                    os.close(directory_fd)
            except OSError:
                if replace_completed:
                    try:
                        persisted_ledger = json.loads(path.read_text(encoding="utf-8"))
                    except (OSError, UnicodeError, json.JSONDecodeError):
                        persisted_ledger = None
                    persisted_entry = None
                    if isinstance(persisted_ledger, dict) and isinstance(persisted_ledger.get("entries"), list):
                        persisted_entry = next(
                            (
                                candidate
                                for candidate in persisted_ledger["entries"]
                                if isinstance(candidate, dict) and candidate.get("idempotency_key") == idempotency_key
                            ),
                            None,
                        )
                    return {
                        "status": "indeterminate",
                        "reason_code": "consumption_ledger_durability_unknown",
                        "entry": persisted_entry,
                        "ledger": persisted_ledger,
                    }
                return blocked("consumption_ledger_write_failed")
            return result
    finally:
        if temp_path is not None:
            try:
                temp_path.unlink()
            except FileNotFoundError:
                pass


def record_single_child_parent_review_decision_file(
    ledger_path: str | Path,
    *,
    idempotency_key: str,
    approval: dict[str, Any],
    now: datetime | None = None,
) -> dict[str, Any]:
    """Persist one parent judgment under the execution ledger lock."""
    path = Path(ledger_path)
    lock_path = path.with_name(f"{path.name}.lock")

    def blocked(reason_code: str) -> dict[str, Any]:
        return {
            "status": "blocked",
            "reason_code": reason_code,
            "decision": None,
            "entry": None,
            "ledger": None,
        }

    if path.is_symlink() or lock_path.is_symlink():
        return blocked("consumption_ledger_path_invalid")
    if not path.exists():
        return blocked("consumption_ledger_read_failed")

    lock_flags = os.O_CREAT | os.O_RDWR
    if hasattr(os, "O_NOFOLLOW"):
        lock_flags |= os.O_NOFOLLOW
    try:
        lock_fd = os.open(lock_path, lock_flags, 0o600)
    except OSError:
        return blocked("consumption_ledger_lock_failed")

    temp_path: Path | None = None
    replace_completed = False
    try:
        with os.fdopen(lock_fd, "r+", encoding="utf-8") as lock_file:
            fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX)
            if path.is_symlink():
                return blocked("consumption_ledger_path_invalid")
            try:
                ledger = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, UnicodeError, json.JSONDecodeError):
                return blocked("consumption_ledger_read_failed")
            revision = ledger.get("revision") if isinstance(ledger, dict) else None
            if not isinstance(revision, int) or isinstance(revision, bool):
                return blocked("consumption_ledger_invalid")
            result = record_single_child_parent_review_decision(
                ledger,
                idempotency_key=idempotency_key,
                approval=approval,
                expected_ledger_revision=revision,
                now=now,
            )
            if result["status"] != "recorded":
                return result

            raw_fd: int | None = None
            try:
                raw_fd, raw_temp_path = tempfile.mkstemp(
                    prefix=f".{path.name}.",
                    suffix=".tmp",
                    dir=path.parent,
                )
                temp_path = Path(raw_temp_path)
                os.fchmod(raw_fd, 0o600)
                temp_file = os.fdopen(raw_fd, "w", encoding="utf-8")
                raw_fd = None
                with temp_file:
                    json.dump(
                        result["ledger"],
                        temp_file,
                        ensure_ascii=False,
                        sort_keys=True,
                        separators=(",", ":"),
                    )
                    temp_file.write("\n")
                    temp_file.flush()
                    os.fsync(temp_file.fileno())
                os.replace(temp_path, path)
                replace_completed = True
                temp_path = None
                directory_fd = os.open(path.parent, os.O_RDONLY)
                try:
                    os.fsync(directory_fd)
                finally:
                    os.close(directory_fd)
            except OSError:
                if replace_completed:
                    try:
                        persisted_ledger = json.loads(
                            path.read_text(encoding="utf-8")
                        )
                    except (OSError, UnicodeError, json.JSONDecodeError):
                        persisted_ledger = None
                    return {
                        "status": "indeterminate",
                        "reason_code": "consumption_ledger_durability_unknown",
                        "decision": None,
                        "entry": None,
                        "ledger": persisted_ledger,
                    }
                return blocked("consumption_ledger_write_failed")
            finally:
                if raw_fd is not None:
                    try:
                        os.close(raw_fd)
                    except OSError:
                        pass
            return result
    finally:
        if temp_path is not None:
            try:
                temp_path.unlink()
            except FileNotFoundError:
                pass


def record_single_child_parent_review_followup_file(
    ledger_path: str | Path,
    *,
    idempotency_key: str,
    followup: dict[str, Any],
    now: datetime | None = None,
) -> dict[str, Any]:
    """Persist one parent-review follow-up under the execution ledger lock."""
    path = Path(ledger_path)
    lock_path = path.with_name(f"{path.name}.lock")

    def blocked(reason_code: str) -> dict[str, Any]:
        return {
            "status": "blocked",
            "reason_code": reason_code,
            "followup": None,
            "entry": None,
            "ledger": None,
        }

    if path.is_symlink() or lock_path.is_symlink():
        return blocked("consumption_ledger_path_invalid")
    if not path.exists():
        return blocked("consumption_ledger_read_failed")

    lock_flags = os.O_CREAT | os.O_RDWR
    if hasattr(os, "O_NOFOLLOW"):
        lock_flags |= os.O_NOFOLLOW
    try:
        lock_fd = os.open(lock_path, lock_flags, 0o600)
    except OSError:
        return blocked("consumption_ledger_lock_failed")

    temp_path: Path | None = None
    replace_completed = False
    try:
        with os.fdopen(lock_fd, "r+", encoding="utf-8") as lock_file:
            try:
                fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX)
            except OSError:
                return blocked("consumption_ledger_lock_failed")
            if path.is_symlink():
                return blocked("consumption_ledger_path_invalid")
            try:
                ledger = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, UnicodeError, json.JSONDecodeError):
                return blocked("consumption_ledger_read_failed")
            revision = ledger.get("revision") if isinstance(ledger, dict) else None
            if not isinstance(revision, int) or isinstance(revision, bool):
                return blocked("consumption_ledger_invalid")
            result = record_single_child_parent_review_followup(
                ledger,
                idempotency_key=idempotency_key,
                followup=followup,
                expected_ledger_revision=revision,
                now=now,
            )
            if result["status"] != "recorded":
                return result

            raw_fd: int | None = None
            try:
                raw_fd, raw_temp_path = tempfile.mkstemp(
                    prefix=f".{path.name}.",
                    suffix=".tmp",
                    dir=path.parent,
                )
                temp_path = Path(raw_temp_path)
                os.fchmod(raw_fd, 0o600)
                temp_file = os.fdopen(raw_fd, "w", encoding="utf-8")
                raw_fd = None
                with temp_file:
                    json.dump(
                        result["ledger"],
                        temp_file,
                        ensure_ascii=False,
                        sort_keys=True,
                        separators=(",", ":"),
                    )
                    temp_file.write("\n")
                    temp_file.flush()
                    os.fsync(temp_file.fileno())
                os.replace(temp_path, path)
                replace_completed = True
                temp_path = None
                directory_fd = os.open(path.parent, os.O_RDONLY)
                try:
                    os.fsync(directory_fd)
                finally:
                    os.close(directory_fd)
            except OSError:
                if replace_completed:
                    try:
                        persisted_ledger = json.loads(
                            path.read_text(encoding="utf-8")
                        )
                    except (OSError, UnicodeError, json.JSONDecodeError):
                        persisted_ledger = None
                    return {
                        "status": "indeterminate",
                        "reason_code": "consumption_ledger_durability_unknown",
                        "followup": None,
                        "entry": None,
                        "ledger": persisted_ledger,
                    }
                return blocked("consumption_ledger_write_failed")
            finally:
                if raw_fd is not None:
                    try:
                        os.close(raw_fd)
                    except OSError:
                        pass
            return result
    finally:
        if temp_path is not None:
            try:
                temp_path.unlink()
            except FileNotFoundError:
                pass


def reserve_single_child_execution_grant_file(
    grant: dict[str, Any],
    ledger_path: str | Path,
    *,
    expected_scope_hash: str,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Persist one grant reservation under an exclusive filesystem lock."""
    path = Path(ledger_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    lock_path = path.with_name(f"{path.name}.lock")

    def blocked(reason_code: str) -> dict[str, Any]:
        return {
            "status": "blocked",
            "reason_code": reason_code,
            "reservation": None,
            "ledger": None,
        }

    if path.is_symlink() or lock_path.is_symlink():
        return blocked("consumption_ledger_path_invalid")

    lock_flags = os.O_CREAT | os.O_RDWR
    if hasattr(os, "O_NOFOLLOW"):
        lock_flags |= os.O_NOFOLLOW
    try:
        lock_fd = os.open(lock_path, lock_flags, 0o600)
    except OSError:
        return blocked("consumption_ledger_lock_failed")

    temp_path: Path | None = None
    try:
        with os.fdopen(lock_fd, "r+", encoding="utf-8") as lock_file:
            fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX)
            if path.is_symlink():
                return blocked("consumption_ledger_path_invalid")
            if path.exists():
                try:
                    ledger = json.loads(path.read_text(encoding="utf-8"))
                except (OSError, UnicodeError, json.JSONDecodeError):
                    return blocked("consumption_ledger_read_failed")
            else:
                ledger = {"schema_version": 1, "revision": 0, "entries": []}

            revision = ledger.get("revision") if isinstance(ledger, dict) else None
            if not isinstance(revision, int) or isinstance(revision, bool):
                return blocked("consumption_ledger_invalid")
            result = reserve_single_child_execution_grant(
                grant,
                ledger,
                expected_scope_hash=expected_scope_hash,
                expected_ledger_revision=revision,
                now=now,
            )
            if result["status"] != "reserved":
                return result

            fd, raw_temp_path = tempfile.mkstemp(
                prefix=f".{path.name}.",
                suffix=".tmp",
                dir=path.parent,
            )
            temp_path = Path(raw_temp_path)
            try:
                os.fchmod(fd, 0o600)
                with os.fdopen(fd, "w", encoding="utf-8") as temp_file:
                    json.dump(
                        result["ledger"],
                        temp_file,
                        ensure_ascii=False,
                        sort_keys=True,
                        separators=(",", ":"),
                    )
                    temp_file.write("\n")
                    temp_file.flush()
                    os.fsync(temp_file.fileno())
                os.replace(temp_path, path)
                temp_path = None
                directory_fd = os.open(path.parent, os.O_RDONLY)
                try:
                    os.fsync(directory_fd)
                finally:
                    os.close(directory_fd)
            except OSError:
                return blocked("consumption_ledger_write_failed")
            return result
    finally:
        if temp_path is not None:
            try:
                temp_path.unlink()
            except FileNotFoundError:
                pass
