"""No-op executor shadow contract.

This module validates a future child execution request without invoking a
process, network client, filesystem mutation, or external LLM.
"""

from __future__ import annotations

from datetime import datetime, timezone
import math
from typing import Any


def _is_finite_number(value: object) -> bool:
    try:
        return math.isfinite(float(value))
    except (OverflowError, TypeError, ValueError):
        return False


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


def build_noop_shadow_record(request: dict[str, Any]) -> dict[str, Any]:
    """Validate one child request and return a non-executing shadow record."""
    record = _base_record(request)
    approval = request.get("approval")
    policy = request.get("policy")

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

    if approval.get("child_id") != request.get("child_id") or approval.get(
        "scope_hash"
    ) != request.get("scope_hash"):
        return _rejected(request, status="blocked", reason_code="scope_mismatch")

    try:
        expires_at = datetime.fromisoformat(
            str(approval["expires_at"]).replace("Z", "+00:00")
        )
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
        or any(
            not isinstance(executor, str) or not executor.strip()
            for executor in allowed_executors
        )
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
    return record
