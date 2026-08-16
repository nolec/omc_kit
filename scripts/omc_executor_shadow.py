"""No-op executor shadow contract.

This module validates a future child execution request without invoking a
process, network client, filesystem mutation, or external LLM.
"""

from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone
import fcntl
import json
import math
import os
from pathlib import Path
import tempfile
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


def _single_child_pilot_rejection(
    request: dict[str, Any],
    approval: dict[str, Any],
) -> dict[str, Any] | None:
    """Return a gate rejection for the bounded single-child pilot, if any."""
    child_count = request.get("child_count")
    if (
        not isinstance(child_count, int)
        or isinstance(child_count, bool)
        or child_count != 1
    ):
        return _rejected(request, status="blocked", reason_code="single_child_required")

    if request.get("child_status") != "ready":
        return _rejected(request, status="hold", reason_code="child_not_ready")

    if "sensitive_paths" not in request:
        return _rejected(
            request, status="blocked", reason_code="scope_metadata_missing"
        )
    sensitive_paths = request["sensitive_paths"]
    if not isinstance(sensitive_paths, list):
        return _rejected(
            request, status="blocked", reason_code="scope_metadata_missing"
        )
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
    if any(
        dependency_statuses.get(dependency) != "completed" for dependency in depends_on
    ):
        return _rejected(request, status="hold", reason_code="dependency_not_ready")

    plan_fingerprint = request.get("plan_fingerprint")
    idempotency_key = request.get("idempotency_key")
    if not isinstance(plan_fingerprint, str) or not plan_fingerprint.strip():
        return _rejected(request, status="blocked", reason_code="plan_scope_missing")
    if not isinstance(idempotency_key, str) or not idempotency_key.strip():
        return _rejected(
            request, status="blocked", reason_code="idempotency_key_missing"
        )

    seen_idempotency_keys = request.get("seen_idempotency_keys", [])
    if not isinstance(seen_idempotency_keys, list):
        return _rejected(
            request, status="blocked", reason_code="idempotency_key_invalid"
        )
    if idempotency_key in seen_idempotency_keys:
        return _rejected(
            request, status="blocked", reason_code="duplicate_idempotency_key"
        )

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

    if (
        approval.get("operator_confirmed") is not True
        or approval.get("approval_status") != "approved"
    ):
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
    if (
        request.get("execution_requested") is not True
        or request.get("execution_mode") != "single_child_opt_in"
    ):
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
    if any(
        not isinstance(grant.get(field), str) or not grant[field].strip()
        for field in required_text
    ):
        return blocked("execution_grant_invalid")
    if grant["scope_hash"] != expected_scope_hash:
        return blocked("grant_scope_mismatch")

    current_time = now or datetime.now(timezone.utc)
    if current_time.tzinfo is None or current_time.utcoffset() is None:
        return blocked("reservation_time_invalid")
    try:
        expires_at = datetime.fromisoformat(
            grant["approval_expires_at"].replace("Z", "+00:00")
        )
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
        "status": "reserved",
        "reserved_at": current_time.astimezone(timezone.utc)
        .isoformat()
        .replace("+00:00", "Z"),
        "max_attempts": 1,
        "max_total_elapsed_sec": grant["max_total_elapsed_sec"],
        "max_output_chars": grant["max_output_chars"],
        "fallback_action": grant["fallback_action"],
    }
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

    matches = [
        entry for entry in entries if entry["idempotency_key"] == idempotency_key
    ]
    if len(matches) != 1:
        return blocked(
            "execution_reservation_missing"
            if not matches
            else "consumption_ledger_invalid"
        )
    entry = matches[0]
    # Ledgers written before status was explicit represent reserved entries.
    if entry.get("status", "reserved") != "reserved":
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
    entry.update(
        {
            "status": terminal_status,
            "completed_at": current_time.astimezone(timezone.utc)
            .isoformat()
            .replace("+00:00", "Z"),
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
                    if isinstance(persisted_ledger, dict) and isinstance(
                        persisted_ledger.get("entries"), list
                    ):
                        persisted_entry = next(
                            (
                                candidate
                                for candidate in persisted_ledger["entries"]
                                if isinstance(candidate, dict)
                                and candidate.get("idempotency_key") == idempotency_key
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
