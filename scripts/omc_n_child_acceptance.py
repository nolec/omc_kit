from __future__ import annotations

import argparse
from copy import deepcopy
import hashlib
import json
import math
from pathlib import Path
import re
import subprocess
import tempfile
from typing import Any

if __package__:
    from .omc_executor_shadow import build_n_child_dag_proposal, build_n_child_dag_v2_grant
    from .omc_n_child_scheduler import build_process_provider_runner, execute_n_child_dag_grant_file
    from .omc_scope import canonical_scope_sha256
else:
    from omc_executor_shadow import build_n_child_dag_proposal, build_n_child_dag_v2_grant
    from omc_n_child_scheduler import build_process_provider_runner, execute_n_child_dag_grant_file
    from omc_scope import canonical_scope_sha256


SCHEMA_VERSION = "omc-n-child-acceptance/v1"
CASE_KINDS = {"success", "failure", "timeout", "policy_violation"}
EXPECTED_KINDS = {
    "success": 2,
    "failure": 1,
    "timeout": 1,
    "policy_violation": 1,
}
EXPECTED_CASES = (
    {
        "case_id": "success-3-child",
        "kind": "success",
        "child_count": 3,
        "expected_status": "completed",
    },
    {
        "case_id": "success-5-child",
        "kind": "success",
        "child_count": 5,
        "expected_status": "completed",
    },
    {
        "case_id": "provider-failure-3-child",
        "kind": "failure",
        "child_count": 3,
        "expected_status": "parent_review",
    },
    {
        "case_id": "provider-timeout-3-child",
        "kind": "timeout",
        "child_count": 3,
        "expected_status": "parent_review",
    },
    {
        "case_id": "scope-policy-violation-3-child",
        "kind": "policy_violation",
        "child_count": 3,
        "expected_status": "parent_review",
    },
)
EXPECTED_THRESHOLDS = {
    "required_case_count": 5,
    "required_success_count": 2,
    "max_duplicate_executions": 0,
    "max_applied_scope_violations": 0,
    "max_accepted_budget_violations": 0,
    "max_applied_failed_patches": 0,
    "max_missing_receipts": 0,
}
CASE_SCHEMA_VERSION = "omc-n-child-acceptance-case/v1"


def write_acceptance_fixture_packets(source_root: Path, packet_root: Path) -> None:
    """Write the deterministic five-case packet catalog used by the E2E acceptance test."""
    source_root = source_root.resolve()
    packet_root.mkdir(parents=True, exist_ok=True)
    for case in EXPECTED_CASES:
        children = []
        child_grants = []
        prompts = {}
        for index in range(1, case["child_count"] + 1):
            child_id = f"child-{index}"
            relative_path = f"src/{case['case_id']}/{child_id}.txt"
            path = source_root / relative_path
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text("baseline\n", encoding="utf-8")
            scope_paths = [relative_path]
            scope_hash = canonical_scope_sha256(scope_paths)
            children.append({
                "child_id": child_id,
                "depends_on": [],
                "scope_paths": scope_paths,
                "scope_hash": scope_hash,
            })
            child_grants.append({
                "mode": "single_child_execution_grant",
                "parent_id": case["case_id"],
                "child_id": child_id,
                "executor": "codex",
                "execution_allowed": True,
                "retry_count": 0,
                "cost_recorded": False,
                "sandbox_status": "not_started",
                "usage_status": "unavailable",
                "status": "ready",
                "approval_status": "validated",
                "approval_id": f"{case['case_id']}-{child_id}",
                "session_id": f"fixture-{case['case_id']}",
                "timeout_sec": 1,
                "budget_usd": 0.01,
                "retry_limit": 0,
                "gate_status": "allowed",
                "shadow_recorded": True,
                "fallback_action": "parent_review",
                "plan_fingerprint": "acceptance-fixture-v1",
                "idempotency_key": f"{case['case_id']}-{child_id}",
                "budget": {"max_attempts": 1, "max_total_elapsed_sec": 1, "max_output_chars": 1000},
                "max_attempts": 1,
                "max_total_elapsed_sec": 1,
                "max_output_chars": 1000,
                "max_total_tokens": 100,
                "scope_hash": scope_hash,
                "approval_expires_at": "2099-01-01T00:00:00Z",
            })
            prompts[child_id] = f"fixture:{case['kind']}:{relative_path}"
        request = {
            "schema_version": "omc-n-child-dag/v2",
            "dag_id": case["case_id"],
            "execution_mode": "n_child_dag_opt_in",
            "execution_requested": True,
            "children": children,
            "child_grants": child_grants,
            "child_prompts": prompts,
            "aggregate_budget": {
                "max_external_calls": case["child_count"],
                "max_parallelism": case["child_count"],
                "max_total_elapsed_sec": 5,
                "max_output_chars": case["child_count"] * 1000,
                "max_total_tokens": case["child_count"] * 100,
            },
        }
        packet = {
            "schema_version": CASE_SCHEMA_VERSION,
            "case_id": case["case_id"],
            "request": request,
            "approval": {
                "approval_id": f"approval-{case['case_id']}",
                "dag_id": case["case_id"],
                "operator_confirmed": True,
                "expires_at": "2099-01-01T00:00:00Z",
            },
        }
        (packet_root / f"{case['case_id']}.json").write_text(
            json.dumps(packet, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )


def canonical_sha256(value: Any) -> str:
    payload = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def _is_sha256(value: Any) -> bool:
    return isinstance(value, str) and re.fullmatch(r"[0-9a-f]{64}", value) is not None


def _case_outcome_matches(case: dict[str, Any], receipt: dict[str, Any]) -> bool:
    raw = receipt.get("raw_result")
    if not isinstance(raw, dict):
        return False
    evidence = receipt.get("metric_evidence")
    if not isinstance(evidence, dict) or evidence.get("status") != "verified":
        return False
    child_statuses = evidence.get("child_statuses")
    external_call_count = evidence.get("external_call_count")
    if not isinstance(child_statuses, list) or not isinstance(external_call_count, int):
        return False
    raw_children = raw.get("children")
    if isinstance(raw_children, list):
        raw_terminal_statuses = [
            child.get("status")
            for child in raw_children
            if isinstance(child, dict)
            and child.get("status") in {"succeeded", "failed", "timeout", "blocked", "indeterminate"}
        ]
        if raw_terminal_statuses and raw_terminal_statuses != child_statuses:
            return False
    if case["kind"] == "success":
        return (
            raw.get("status") == "completed"
            and external_call_count == case["child_count"]
            and child_statuses == ["succeeded"] * case["child_count"]
        )
    if raw.get("status") not in {"review_required", "indeterminate", "blocked"}:
        return False
    child_status_set = set(child_statuses)
    reason = str(raw.get("reason_code", "")).lower()
    if case["kind"] == "failure":
        return external_call_count >= 1 and "failed" in child_status_set
    if case["kind"] == "timeout":
        return external_call_count >= 1 and "timeout" in child_status_set
    return evidence.get("scope_violations_detected", 0) >= 1 and (
        "scope" in reason or "policy" in reason or "failed" in child_status_set
    )


def validate_acceptance_manifest(payload: Any) -> dict[str, Any]:
    if not isinstance(payload, dict) or payload.get("schema_version") != SCHEMA_VERSION:
        raise ValueError("acceptance_schema_invalid")
    expected_hash = payload.get("manifest_sha256")
    unsigned = deepcopy(payload)
    unsigned.pop("manifest_sha256", None)
    if not _is_sha256(expected_hash) or canonical_sha256(unsigned) != expected_hash:
        raise ValueError("manifest_hash_mismatch")
    if not isinstance(payload.get("acceptance_id"), str) or not payload["acceptance_id"].strip():
        raise ValueError("acceptance_id_invalid")
    if not isinstance(payload.get("source_commit"), str) or re.fullmatch(r"[0-9a-f]{40}", payload["source_commit"]) is None:
        raise ValueError("source_commit_invalid")
    cases = payload.get("cases")
    if not isinstance(cases, list) or len(cases) != 5:
        raise ValueError("acceptance_case_count_invalid")
    case_ids: list[str] = []
    kind_counts = {kind: 0 for kind in CASE_KINDS}
    for case in cases:
        if not isinstance(case, dict):
            raise ValueError("acceptance_case_invalid")
        case_id = case.get("case_id")
        kind = case.get("kind")
        child_count = case.get("child_count")
        if not isinstance(case_id, str) or not case_id.strip() or case_id in case_ids:
            raise ValueError("acceptance_case_id_invalid")
        if kind not in CASE_KINDS:
            raise ValueError("acceptance_case_kind_invalid")
        if not isinstance(child_count, int) or isinstance(child_count, bool) or not 3 <= child_count <= 5:
            raise ValueError("acceptance_child_count_invalid")
        if case.get("expected_status") not in {"completed", "parent_review"}:
            raise ValueError("acceptance_expected_status_invalid")
        if not _is_sha256(case.get("request_sha256")):
            raise ValueError("acceptance_request_hash_invalid")
        case_ids.append(case_id)
        kind_counts[kind] += 1
    if kind_counts != EXPECTED_KINDS:
        raise ValueError("acceptance_case_mix_invalid")
    catalog = [
        {
            "case_id": case["case_id"],
            "kind": case["kind"],
            "child_count": case["child_count"],
            "expected_status": case["expected_status"],
        }
        for case in cases
    ]
    if catalog != list(EXPECTED_CASES):
        raise ValueError("acceptance_case_catalog_invalid")
    if payload.get("thresholds") != EXPECTED_THRESHOLDS:
        raise ValueError("acceptance_thresholds_invalid")
    return deepcopy(payload)


def _build_acceptance_report_from_results(
    manifest: dict[str, Any], results: list[dict[str, Any]]
) -> dict[str, Any]:
    manifest = validate_acceptance_manifest(manifest)
    if not isinstance(results, list):
        raise ValueError("acceptance_results_invalid")
    by_id: dict[str, dict[str, Any]] = {}
    failures: list[str] = []
    for result in results:
        if not isinstance(result, dict) or not isinstance(result.get("case_id"), str):
            raise ValueError("acceptance_result_invalid")
        case_id = result["case_id"]
        if case_id not in {case["case_id"] for case in manifest["cases"]}:
            failures.append(f"unexpected_result:{case_id}")
            continue
        if case_id in by_id:
            failures.append(f"duplicate_result:{case_id}")
        by_id[case_id] = result
    totals = {
        "duplicate_executions": 0,
        "applied_scope_violations": 0,
        "accepted_budget_violations": 0,
        "applied_failed_patches": 0,
        "missing_receipts": 0,
    }
    success_case_count = 0
    for case in manifest["cases"]:
        case_id = case["case_id"]
        result = by_id.get(case_id)
        if result is None:
            failures.append(f"missing_result:{case_id}")
            totals["missing_receipts"] += 1
            continue
        if result.get("status") != case["expected_status"]:
            failures.append(f"unexpected_status:{case_id}")
        if case["kind"] == "success" and result.get("status") == "completed":
            success_case_count += 1
        receipt = result.get("receipt")
        if not _is_sha256(result.get("receipt_sha256")) or not isinstance(receipt, dict):
            failures.append(f"missing_receipt:{case_id}")
            totals["missing_receipts"] += 1
        elif canonical_sha256(receipt) != result["receipt_sha256"]:
            failures.append(f"receipt_hash_mismatch:{case_id}")
        elif (
            receipt.get("schema_version") != SCHEMA_VERSION
            or receipt.get("acceptance_id") != manifest["acceptance_id"]
            or receipt.get("manifest_sha256") != manifest["manifest_sha256"]
            or receipt.get("case_id") != case_id
            or receipt.get("kind") != case["kind"]
            or receipt.get("request_sha256") != case["request_sha256"]
            or receipt.get("source_commit") != manifest["source_commit"]
        ):
            failures.append(f"receipt_binding_mismatch:{case_id}")
        elif not _case_outcome_matches(case, receipt):
            failures.append(f"case_semantic_mismatch:{case_id}")
        metrics = receipt.get("metrics") if isinstance(receipt, dict) else None
        evidence = receipt.get("metric_evidence") if isinstance(receipt, dict) else None
        if not isinstance(evidence, dict) or evidence.get("status") != "verified":
            failures.append(f"metric_evidence_unverified:{case_id}")
        for field in (
            "duplicate_executions",
            "applied_scope_violations",
            "accepted_budget_violations",
            "applied_failed_patches",
        ):
            value = metrics.get(field) if isinstance(metrics, dict) else None
            if not isinstance(value, int) or isinstance(value, bool) or value < 0:
                failures.append(f"invalid_metric:{case_id}:{field}")
            else:
                totals[field] += value
    thresholds = manifest["thresholds"]
    if success_case_count < thresholds["required_success_count"]:
        failures.append("insufficient_success_cases")
    threshold_fields = {
        "duplicate_executions": "max_duplicate_executions",
        "applied_scope_violations": "max_applied_scope_violations",
        "accepted_budget_violations": "max_accepted_budget_violations",
        "applied_failed_patches": "max_applied_failed_patches",
        "missing_receipts": "max_missing_receipts",
    }
    for field, threshold in threshold_fields.items():
        if totals[field] > thresholds[threshold]:
            failures.append(f"threshold_exceeded:{field}")
    failures = list(dict.fromkeys(failures))
    report = {
        "schema_version": SCHEMA_VERSION,
        "acceptance_id": manifest["acceptance_id"],
        "manifest_sha256": manifest["manifest_sha256"],
        "case_count": len(manifest["cases"]),
        "success_case_count": success_case_count,
        "totals": totals,
        "failures": failures,
        "verdict": "PASS" if not failures else "FAIL",
    }
    report["report_sha256"] = canonical_sha256(report)
    return report


def _git(*args: str, cwd: Path | None = None) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", *args],
        cwd=str(cwd) if cwd is not None else None,
        text=True,
        capture_output=True,
        check=False,
    )


def _default_case_executor(
    packet: dict[str, Any],
    workspace: Path,
    case_artifact: Path,
    provider_adapter: Path,
) -> dict[str, Any]:
    request = packet.get("request")
    approval = packet.get("approval")
    if not isinstance(request, dict) or not isinstance(approval, dict):
        return {"status": "review_required", "reason_code": "acceptance_packet_invalid"}
    proposal = build_n_child_dag_proposal(workspace, request)
    if proposal.get("status") != "ready":
        return {
            "status": "review_required",
            "reason_code": proposal.get("reason_code", "dag_proposal_failed"),
        }
    bound_approval = deepcopy(approval)
    bound_approval["proposal_sha256"] = proposal["proposal_sha256"]
    grant = build_n_child_dag_v2_grant(workspace, proposal, bound_approval)
    if grant.get("status") != "ready":
        return {
            "status": "review_required",
            "reason_code": grant.get("reason_code", "dag_grant_failed"),
        }
    try:
        runner = build_process_provider_runner(provider_adapter)
    except ValueError as exc:
        return {"status": "review_required", "reason_code": str(exc)}
    return execute_n_child_dag_grant_file(
        grant,
        case_artifact / "dag-ledger.json",
        case_artifact / "child-ledger.json",
        trusted_target=workspace,
        prompts=proposal["child_prompts"],
        project_root=workspace,
        runner=runner,
    )


def _metric_result(
    manifest: dict[str, Any],
    case: dict[str, Any],
    packet: dict[str, Any],
    raw_result: dict[str, Any],
    case_artifact: Path,
) -> dict[str, Any]:
    raw_status = raw_result.get("status")
    status = "completed" if raw_status == "completed" else "parent_review"
    metrics, metric_evidence = _ledger_metrics(case_artifact)
    receipt = {
        "schema_version": SCHEMA_VERSION,
        "acceptance_id": manifest["acceptance_id"],
        "manifest_sha256": manifest["manifest_sha256"],
        "case_id": case["case_id"],
        "kind": case["kind"],
        "request_sha256": canonical_sha256(packet),
        "source_commit": manifest["source_commit"],
        "raw_result": deepcopy(raw_result),
        "metrics": metrics,
        "metric_evidence": metric_evidence,
    }
    receipt_sha256 = canonical_sha256(receipt)
    return {
        "case_id": case["case_id"],
        "status": status,
        "receipt_sha256": receipt_sha256,
        "receipt": receipt,
    }


def _ledger_metrics(case_artifact: Path) -> tuple[dict[str, int], dict[str, Any]]:
    empty = {
        "duplicate_executions": 0,
        "applied_scope_violations": 0,
        "accepted_budget_violations": 0,
        "applied_failed_patches": 0,
    }
    dag_path = case_artifact / "dag-ledger.json"
    child_path = case_artifact / "child-ledger.json"
    try:
        dag_ledger = _load_json(dag_path)
        child_ledger = _load_json(child_path)
    except ValueError:
        return empty, {"status": "unverified", "reason_code": "execution_ledger_missing"}
    entries = child_ledger.get("entries") if isinstance(child_ledger, dict) else None
    dag = dag_ledger.get("dag") if isinstance(dag_ledger, dict) else None
    invalid = {"status": "unverified", "reason_code": "execution_ledger_invalid"}
    if (
        not isinstance(dag_ledger, dict)
        or dag_ledger.get("schema_version") != 1
        or not isinstance(dag_ledger.get("revision"), int)
        or isinstance(dag_ledger.get("revision"), bool)
        or dag_ledger["revision"] < 1
        or not isinstance(child_ledger, dict)
        or child_ledger.get("schema_version") != 1
        or not isinstance(child_ledger.get("revision"), int)
        or isinstance(child_ledger.get("revision"), bool)
        or child_ledger["revision"] < 1
        or not isinstance(entries, list)
        or not entries
        or not isinstance(dag, dict)
        or not isinstance(dag.get("status"), str)
        or not isinstance(dag.get("reason_code"), str)
        or not isinstance(dag.get("external_call_count"), int)
        or isinstance(dag.get("external_call_count"), bool)
        or dag["external_call_count"] < 0
        or not isinstance(dag.get("children"), list)
    ):
        return empty, invalid

    terminal_statuses = {"succeeded", "failed", "timeout"}
    keys: list[str] = []
    outcomes: list[dict[str, Any]] = []
    for entry in entries:
        if not isinstance(entry, dict):
            return empty, invalid
        child_id = entry.get("child_id")
        key = entry.get("idempotency_key")
        attempt_count = entry.get("attempt_count")
        status = entry.get("status")
        max_elapsed = entry.get("max_total_elapsed_sec")
        max_output = entry.get("max_output_chars")
        max_tokens = entry.get("max_total_tokens")
        outcome = entry.get("outcome")
        if (
            not isinstance(child_id, str)
            or not child_id.strip()
            or not isinstance(key, str)
            or not key.strip()
            or not isinstance(attempt_count, int)
            or isinstance(attempt_count, bool)
            or attempt_count < 1
            or status not in terminal_statuses
            or not isinstance(max_elapsed, (int, float))
            or isinstance(max_elapsed, bool)
            or not math.isfinite(float(max_elapsed))
            or max_elapsed <= 0
            or not isinstance(max_output, int)
            or isinstance(max_output, bool)
            or max_output <= 0
            or not isinstance(max_tokens, int)
            or isinstance(max_tokens, bool)
            or max_tokens <= 0
            or not isinstance(outcome, dict)
            or outcome.get("status") != status
            or not isinstance(outcome.get("reason_code"), str)
            or not outcome["reason_code"].strip()
            or not isinstance(outcome.get("elapsed_sec"), (int, float))
            or isinstance(outcome.get("elapsed_sec"), bool)
            or not math.isfinite(float(outcome["elapsed_sec"]))
            or outcome["elapsed_sec"] < 0
            or not isinstance(outcome.get("output_chars"), int)
            or isinstance(outcome.get("output_chars"), bool)
            or outcome["output_chars"] < 0
            or not isinstance(outcome.get("patch_applied"), bool)
            or not isinstance(outcome.get("scope_violation_detected"), bool)
        ):
            return empty, invalid
        token_usage = outcome.get("token_usage")
        if token_usage is not None and (
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
            return empty, invalid
        if status == "succeeded" and token_usage is None:
            return empty, invalid
        keys.append(key)
        outcomes.append(outcome)

    dag_children = dag["children"]
    if any(
        not isinstance(child, dict)
        or not isinstance(child.get("child_id"), str)
        or child.get("status") not in terminal_statuses
        for child in dag_children
    ):
        return empty, invalid
    dag_statuses = {child["child_id"]: child["status"] for child in dag_children}
    entry_statuses = {entry["child_id"]: entry["status"] for entry in entries}
    if (
        len(dag_statuses) != len(dag_children)
        or len(entry_statuses) != len(entries)
        or dag_statuses != entry_statuses
        or dag["external_call_count"] != sum(entry["attempt_count"] for entry in entries)
    ):
        return empty, invalid
    duplicate_keys = len(keys) - len(set(keys))
    duplicate_attempts = sum(entry["attempt_count"] - 1 for entry in entries)
    scope_detected = sum(outcome["scope_violation_detected"] for outcome in outcomes)
    metrics = {
        "duplicate_executions": duplicate_keys + duplicate_attempts,
        "applied_scope_violations": sum(
            outcome["scope_violation_detected"] and outcome["patch_applied"]
            for outcome in outcomes
        ),
        "accepted_budget_violations": sum(
            outcome["status"] == "succeeded"
            and (
                outcome["elapsed_sec"] > entry["max_total_elapsed_sec"]
                or outcome["output_chars"] > entry["max_output_chars"]
                or (
                    isinstance(outcome.get("token_usage"), dict)
                    and outcome["token_usage"]["total_tokens"] > entry["max_total_tokens"]
                )
            )
            for entry, outcome in zip(entries, outcomes)
        ),
        "applied_failed_patches": sum(
            outcome["status"] != "succeeded" and outcome["patch_applied"]
            for outcome in outcomes
        ),
    }
    return metrics, {
        "status": "verified",
        "external_call_count": sum(entry["attempt_count"] for entry in entries),
        "child_statuses": [entry["status"] for entry in entries],
        "scope_violations_detected": scope_detected,
        "ledger_sha256s": {
            "dag": canonical_sha256(dag_ledger),
            "children": canonical_sha256(child_ledger),
        },
    }


def _raw_result_matches_dag(raw_result: Any, dag: Any) -> bool:
    if not isinstance(raw_result, dict) or not isinstance(dag, dict):
        return False
    children = dag.get("children")
    required = (
        "status",
        "reason_code",
        "dag_id",
        "external_call_count",
        "total_elapsed_sec",
        "total_output_chars",
        "input_tokens",
        "output_tokens",
        "total_tokens",
    )
    if not isinstance(children, list) or any(field not in dag for field in required):
        return False
    expected = {
        field: deepcopy(dag[field])
        for field in required
    }
    expected["children"] = deepcopy(children)
    expected["completed_child_ids"] = [
        child["child_id"]
        for child in children
        if isinstance(child, dict) and child.get("status") == "succeeded"
    ]
    expected["pending_child_ids"] = [
        child["child_id"]
        for child in children
        if isinstance(child, dict) and child.get("status") == "not_started"
    ]
    expected["failed_child_ids"] = [
        child["child_id"]
        for child in children
        if isinstance(child, dict)
        and child.get("status") in {"failed", "timeout", "blocked", "indeterminate"}
    ]
    if dag.get("parent_review") is not None:
        expected["parent_review"] = deepcopy(dag["parent_review"])
    return raw_result == expected


def load_authoritative_acceptance_results(
    manifest: dict[str, Any], artifact_root: Path
) -> list[dict[str, Any]]:
    """Reload receipts and ledgers, rejecting self-consistent but unsupported results."""
    manifest = validate_acceptance_manifest(manifest)
    artifact_root = artifact_root.resolve()
    results = _load_json(artifact_root / "results.json")
    if not isinstance(results, list):
        raise ValueError("acceptance_results_invalid")
    expected_ids = [case["case_id"] for case in manifest["cases"]]
    result_ids = [result.get("case_id") if isinstance(result, dict) else None for result in results]
    if result_ids != expected_ids:
        raise ValueError("acceptance_results_catalog_invalid")

    authoritative: list[dict[str, Any]] = []
    for result in results:
        case_id = result["case_id"]
        case_root = artifact_root / case_id
        envelope = _load_json(case_root / "receipt.json")
        receipt = envelope.get("receipt") if isinstance(envelope, dict) else None
        receipt_sha256 = envelope.get("receipt_sha256") if isinstance(envelope, dict) else None
        if (
            not isinstance(receipt, dict)
            or not _is_sha256(receipt_sha256)
            or canonical_sha256(receipt) != receipt_sha256
            or result.get("receipt_sha256") != receipt_sha256
            or result.get("receipt") != receipt
        ):
            raise ValueError(f"acceptance_receipt_artifact_mismatch:{case_id}")
        metrics, evidence = _ledger_metrics(case_root)
        try:
            dag = _load_json(case_root / "dag-ledger.json")["dag"]
        except (KeyError, TypeError, ValueError) as exc:
            raise ValueError(f"acceptance_ledger_evidence_invalid:{case_id}") from exc
        if (
            evidence.get("status") != "verified"
            or receipt.get("metrics") != metrics
            or receipt.get("metric_evidence") != evidence
            or not _raw_result_matches_dag(receipt.get("raw_result"), dag)
        ):
            raise ValueError(f"acceptance_ledger_evidence_invalid:{case_id}")
        authoritative.append(deepcopy(result))
    return authoritative


def build_acceptance_report(
    manifest: dict[str, Any], artifact_root: str | Path
) -> dict[str, Any]:
    """Build a verdict only from receipts revalidated against persisted ledgers."""
    if not isinstance(artifact_root, (str, Path)):
        raise ValueError("acceptance_artifact_root_invalid")
    root = Path(artifact_root)
    return _build_acceptance_report_from_results(
        manifest,
        load_authoritative_acceptance_results(manifest, root),
    )


def run_acceptance(
    manifest: dict[str, Any],
    *,
    packet_root: Path,
    source_root: Path,
    artifact_root: Path,
    provider_adapter: Path,
    case_executor: Any = _default_case_executor,
) -> list[dict[str, Any]]:
    manifest = validate_acceptance_manifest(manifest)
    packet_root = packet_root.resolve()
    source_root = source_root.resolve()
    artifact_root = artifact_root.resolve()
    head = _git("rev-parse", "HEAD", cwd=source_root)
    if head.returncode != 0 or head.stdout.strip() != manifest["source_commit"]:
        raise ValueError("acceptance_source_commit_mismatch")
    artifact_root.mkdir(parents=True, exist_ok=True)
    results: list[dict[str, Any]] = []
    for case in manifest["cases"]:
        packet_path = packet_root / f"{case['case_id']}.json"
        packet = _load_json(packet_path)
        if (
            not isinstance(packet, dict)
            or packet.get("schema_version") != CASE_SCHEMA_VERSION
            or packet.get("case_id") != case["case_id"]
            or canonical_sha256(packet) != case["request_sha256"]
        ):
            raise ValueError(f"acceptance_packet_mismatch:{case['case_id']}")
        case_artifact = artifact_root / case["case_id"]
        case_artifact.mkdir(parents=True, exist_ok=False)
        with tempfile.TemporaryDirectory(
            prefix=f"omc-{case['case_id']}-", dir=artifact_root
        ) as raw_temp:
            workspace = Path(raw_temp) / "workspace"
            clone = _git("clone", "--no-local", "--quiet", str(source_root), str(workspace))
            if clone.returncode != 0:
                raise ValueError(f"acceptance_clone_failed:{case['case_id']}")
            checkout = _git("checkout", "--quiet", "--detach", manifest["source_commit"], cwd=workspace)
            if checkout.returncode != 0:
                raise ValueError(f"acceptance_checkout_failed:{case['case_id']}")
            try:
                raw_result = case_executor(
                    packet,
                    workspace,
                    case_artifact,
                    provider_adapter.resolve(strict=False),
                )
            except Exception as exc:
                raw_result = {
                    "status": "review_required",
                    "reason_code": "case_executor_exception",
                    "exception_type": type(exc).__name__,
                }
        if not isinstance(raw_result, dict):
            raw_result = {"status": "review_required", "reason_code": "case_result_invalid"}
        result = _metric_result(manifest, case, packet, raw_result, case_artifact)
        (case_artifact / "receipt.json").write_text(
            json.dumps(
                {
                    "receipt_sha256": result["receipt_sha256"],
                    "receipt": result["receipt"],
                },
                ensure_ascii=False,
                indent=2,
            ) + "\n",
            encoding="utf-8",
        )
        results.append(result)
    (artifact_root / "results.json").write_text(
        json.dumps(results, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    return results


def _load_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ValueError("acceptance_input_unavailable") from exc


def build_acceptance_manifest(
    *,
    source_root: Path,
    packet_root: Path,
    acceptance_id: str,
) -> dict[str, Any]:
    source_root = source_root.resolve()
    packet_root = packet_root.resolve()
    head = _git("rev-parse", "HEAD", cwd=source_root)
    if head.returncode != 0 or re.fullmatch(r"[0-9a-f]{40}", head.stdout.strip()) is None:
        raise ValueError("acceptance_source_commit_invalid")
    cases: list[dict[str, Any]] = []
    for expected in EXPECTED_CASES:
        packet = _load_json(packet_root / f"{expected['case_id']}.json")
        if (
            not isinstance(packet, dict)
            or packet.get("schema_version") != CASE_SCHEMA_VERSION
            or packet.get("case_id") != expected["case_id"]
        ):
            raise ValueError(f"acceptance_packet_invalid:{expected['case_id']}")
        cases.append({**expected, "request_sha256": canonical_sha256(packet)})
    manifest = {
        "schema_version": SCHEMA_VERSION,
        "acceptance_id": acceptance_id,
        "source_commit": head.stdout.strip(),
        "cases": cases,
        "thresholds": deepcopy(EXPECTED_THRESHOLDS),
    }
    manifest["manifest_sha256"] = canonical_sha256(manifest)
    return validate_acceptance_manifest(manifest)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run and evaluate bounded N-child acceptance evidence.")
    sub = parser.add_subparsers(dest="command", required=True)
    validate = sub.add_parser("validate")
    validate.add_argument("--manifest", type=Path, required=True)
    prepare = sub.add_parser("prepare")
    prepare.add_argument("--source-root", type=Path, required=True)
    prepare.add_argument("--packet-root", type=Path, required=True)
    prepare.add_argument("--acceptance-id", required=True)
    prepare.add_argument("--out", type=Path, required=True)
    run = sub.add_parser("run")
    run.add_argument("--manifest", type=Path, required=True)
    run.add_argument("--packet-root", type=Path, required=True)
    run.add_argument("--source-root", type=Path, required=True)
    run.add_argument("--artifact-root", type=Path, required=True)
    run.add_argument("--provider-adapter", type=Path, required=True)
    finalize = sub.add_parser("finalize")
    finalize.add_argument("--manifest", type=Path, required=True)
    finalize.add_argument("--artifact-root", type=Path, required=True)
    finalize.add_argument("--out", type=Path)
    args = parser.parse_args(argv)
    try:
        if args.command == "prepare":
            manifest = build_acceptance_manifest(
                source_root=args.source_root,
                packet_root=args.packet_root,
                acceptance_id=args.acceptance_id,
            )
            args.out.parent.mkdir(parents=True, exist_ok=True)
            args.out.write_text(
                json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
                encoding="utf-8",
            )
            result = {"status": "ready", "manifest_sha256": manifest["manifest_sha256"]}
        else:
            manifest = validate_acceptance_manifest(_load_json(args.manifest))
        if args.command == "prepare":
            pass
        elif args.command == "validate":
            result = {"status": "ready", "manifest_sha256": manifest["manifest_sha256"]}
        elif args.command == "run":
            results = run_acceptance(
                manifest,
                packet_root=args.packet_root,
                source_root=args.source_root,
                artifact_root=args.artifact_root,
                provider_adapter=args.provider_adapter,
            )
            result = _build_acceptance_report_from_results(manifest, results)
            (args.artifact_root / "report.json").write_text(
                json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
            )
        else:
            result = build_acceptance_report(manifest, args.artifact_root)
            if args.out is not None:
                args.out.parent.mkdir(parents=True, exist_ok=True)
                args.out.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    except ValueError as exc:
        print(json.dumps({"status": "blocked", "reason_code": str(exc)}, sort_keys=True))
        return 2
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return 0 if result.get("verdict", "PASS") == "PASS" else 2


if __name__ == "__main__":
    raise SystemExit(main())
