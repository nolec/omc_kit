#!/usr/bin/env python3
"""Pure, fail-closed decisions for identity-bound work-unit closure."""
from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from typing import Any

import omc_closure


INPUT_SCHEMA_VERSION = "omc-closure-decision-input/v1"
OUTPUT_SCHEMA_VERSION = "omc-closure-decision/v1"
RECEIPT_SCHEMA_VERSION = "omc-closure-acceptance-receipt/v1"
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_IDENTITY_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
_ISSUE_FIELDS = {
    "issue_id",
    "surface",
    "invariant",
    "failure_mode",
    "severity",
    "status",
    "revision",
    "validation_round",
    "previous_fingerprint",
    "change_reason",
}


class ClosureDecisionError(ValueError):
    """Raised when decision input is not structurally trustworthy."""


_ACCEPTANCE_TOKEN = object()


class ConsumedClosureAcceptance:
    """Opaque acceptance loaded from OMC's consumed decision state."""

    __slots__ = ("_receipt",)

    def __init__(self, receipt: dict[str, Any], *, _token: object) -> None:
        if _token is not _ACCEPTANCE_TOKEN:
            raise ClosureDecisionError("acceptance_must_be_loaded_from_state")
        self._receipt = receipt

    @property
    def receipt(self) -> dict[str, Any]:
        return json.loads(json.dumps(self._receipt, ensure_ascii=False))


def _canonical_json(value: Any) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def canonical_sha256(value: Any) -> str:
    return hashlib.sha256(_canonical_json(value)).hexdigest()


def load_consumed_acceptance(
    project_root: Path,
    *,
    decision_id: str,
) -> ConsumedClosureAcceptance:
    """Load an acceptance only after the state machine consumed it."""
    root = Path(project_root)
    path = root / ".omc" / "state" / "latest.json"
    current = root
    for segment in path.relative_to(root).parts:
        current /= segment
        if current.is_symlink():
            raise ClosureDecisionError("acceptance_state_symlink")
    try:
        latest = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ClosureDecisionError("acceptance_state_invalid") from exc
    decision = latest.get("pending_decision") if isinstance(latest, dict) else None
    if (
        not isinstance(decision, dict)
        or decision.get("decision_id") != decision_id
        or decision.get("action") != "closure_accept"
        or decision.get("status") != "consumed"
    ):
        raise ClosureDecisionError("acceptance_not_consumed")
    receipt = decision.get("acceptance_receipt")
    if not isinstance(receipt, dict):
        raise ClosureDecisionError("acceptance_receipt_missing")
    return ConsumedClosureAcceptance(receipt, _token=_ACCEPTANCE_TOKEN)


def _exact(value: Any, keys: set[str], field: str) -> dict[str, Any]:
    if not isinstance(value, dict) or set(value) != keys:
        raise ClosureDecisionError(f"{field}_fields_invalid")
    return value


def _sha256(value: Any, field: str) -> str:
    if not isinstance(value, str) or not _SHA256_RE.fullmatch(value):
        raise ClosureDecisionError(f"{field}_invalid")
    return value


def _identity(value: Any, field: str) -> str:
    if not isinstance(value, str) or not _IDENTITY_RE.fullmatch(value):
        raise ClosureDecisionError(f"{field}_invalid")
    return value


def issue_fingerprint(issue: dict[str, Any]) -> str:
    return canonical_sha256(
        {
            "issue_id": issue.get("issue_id"),
            "surface": issue.get("surface"),
            "invariant": issue.get("invariant"),
            "failure_mode": issue.get("failure_mode"),
            "severity": issue.get("severity"),
            "status": issue.get("status"),
            "revision": issue.get("revision"),
            "validation_round": issue.get("validation_round"),
        }
    )


def issue_identity_fingerprint(issue: dict[str, Any]) -> str:
    """Identify one semantic issue independently from its caller-provided ID."""
    return canonical_sha256(
        {
            "surface": issue.get("surface"),
            "invariant": issue.get("invariant"),
        }
    )


def _validate_issues(
    issues: Any,
    *,
    max_total_rounds: int,
    max_revisions_per_issue: int,
) -> tuple[bool, list[dict[str, Any]]]:
    if not isinstance(issues, list):
        raise ClosureDecisionError("issues_invalid")
    histories: dict[str, list[dict[str, Any]]] = {}
    semantic_owners: dict[str, str] = {}
    for raw in issues:
        if not isinstance(raw, dict) or set(raw) != _ISSUE_FIELDS:
            return False, []
        try:
            issue_id = _identity(raw["issue_id"], "issue_id")
        except ClosureDecisionError:
            return False, []
        if any(
            not isinstance(raw[field], str) or not raw[field].strip()
            for field in ("surface", "invariant", "failure_mode")
        ):
            return False, []
        if raw["severity"] not in {"critical", "major", "minor"}:
            return False, []
        if raw["status"] not in {"open", "resolved", "accepted"}:
            return False, []
        if raw["status"] == "accepted" and raw["severity"] != "minor":
            return False, []
        revision = raw["revision"]
        if (
            isinstance(revision, bool)
            or not isinstance(revision, int)
            or not 1 <= revision <= max_revisions_per_issue
        ):
            return False, []
        validation_round = raw["validation_round"]
        if (
            isinstance(validation_round, bool)
            or not isinstance(validation_round, int)
            or not 1 <= validation_round <= max_total_rounds
        ):
            return False, []
        semantic_identity = issue_identity_fingerprint(raw)
        owner = semantic_owners.setdefault(semantic_identity, issue_id)
        if owner != issue_id:
            return False, []
        histories.setdefault(issue_id, []).append(raw)

    final: list[dict[str, Any]] = []
    for issue_id, history in histories.items():
        previous: dict[str, Any] | None = None
        semantic_identity: str | None = None
        for event in history:
            current_identity = issue_identity_fingerprint(event)
            if semantic_identity is None:
                semantic_identity = current_identity
            elif current_identity != semantic_identity:
                return False, []
            if previous is None:
                if (
                    event["revision"] != 1
                    or event["previous_fingerprint"] is not None
                    or event["change_reason"] is not None
                ):
                    return False, []
            else:
                if event["revision"] != previous["revision"] + 1:
                    return False, []
                if event["validation_round"] <= previous["validation_round"]:
                    return False, []
                if event["previous_fingerprint"] != issue_fingerprint(previous):
                    return False, []
                if (
                    not isinstance(event["change_reason"], str)
                    or not event["change_reason"].strip()
                ):
                    return False, []
            previous = event
        if previous is not None:
            final.append(previous)
    return True, sorted(final, key=lambda item: item["issue_id"])


def _validate_input(value: Any, contract: dict[str, Any]) -> dict[str, Any]:
    value = _exact(
        value,
        {
            "schema_version",
            "work_unit",
            "contract_sha256",
            "scope",
            "validation",
            "verification",
            "issues",
            "acceptance_decision_id",
        },
        "decision_input",
    )
    if value["schema_version"] != INPUT_SCHEMA_VERSION:
        raise ClosureDecisionError("decision_input_schema_invalid")
    work_unit = _exact(
        value["work_unit"],
        {"session_id", "task_id", "request_digest"},
        "work_unit",
    )
    _identity(work_unit["session_id"], "session_id")
    _identity(work_unit["task_id"], "task_id")
    _sha256(work_unit["request_digest"], "request_digest")
    if work_unit != contract["work_unit"]:
        raise ClosureDecisionError("work_unit_identity_mismatch")
    if _sha256(value["contract_sha256"], "contract_sha256") != contract["contract_sha256"]:
        raise ClosureDecisionError("contract_digest_mismatch")

    scope = _exact(value["scope"], {"change_required", "change_approved"}, "scope")
    if any(not isinstance(scope[field], bool) for field in scope):
        raise ClosureDecisionError("scope_value_invalid")
    validation = _exact(value["validation"], {"rounds_consumed"}, "validation")
    rounds = validation["rounds_consumed"]
    if isinstance(rounds, bool) or not isinstance(rounds, int) or rounds < 0:
        raise ClosureDecisionError("rounds_consumed_invalid")
    verification = _exact(
        value["verification"], {"status", "receipt_sha256"}, "verification"
    )
    status = verification["status"]
    if status not in {"pending", "passed", "failed"}:
        raise ClosureDecisionError("verification_status_invalid")
    receipt = verification["receipt_sha256"]
    if status == "pending" and receipt is not None:
        raise ClosureDecisionError("verification_receipt_forbidden")
    if status in {"passed", "failed"}:
        _sha256(receipt, "verification_receipt_required")
    decision_id = value["acceptance_decision_id"]
    if decision_id is not None:
        _identity(decision_id, "acceptance_decision_id")
    return value


def acceptance_binding(
    decision_input: dict[str, Any],
    *,
    accepted_residual_issue_ids: list[str],
) -> dict[str, Any]:
    verification = decision_input["verification"]
    issue_ids = sorted(
        {
            _identity(item, "residual_issue_id")
            for item in accepted_residual_issue_ids
        }
    )
    if len(issue_ids) != len(accepted_residual_issue_ids):
        raise ClosureDecisionError("residual_issue_ids_invalid")
    issues = decision_input.get("issues")
    if not isinstance(issues, list):
        raise ClosureDecisionError("issues_invalid")
    latest_by_id: dict[str, dict[str, Any]] = {}
    for issue in issues:
        if not isinstance(issue, dict) or issue.get("issue_id") not in issue_ids:
            continue
        revision = issue.get("revision")
        if isinstance(revision, bool) or not isinstance(revision, int):
            raise ClosureDecisionError("residual_issue_revision_invalid")
        current = latest_by_id.get(issue["issue_id"])
        if current is None or revision > current["revision"]:
            latest_by_id[issue["issue_id"]] = issue
    if set(latest_by_id) != set(issue_ids) or any(
        issue.get("status") != "accepted" for issue in latest_by_id.values()
    ):
        raise ClosureDecisionError("residual_issue_ids_invalid")
    accepted_residual_issues = [latest_by_id[issue_id] for issue_id in issue_ids]
    return {
        "session_id": decision_input["work_unit"]["session_id"],
        "task_id": decision_input["work_unit"]["task_id"],
        "request_digest": decision_input["work_unit"]["request_digest"],
        "contract_sha256": decision_input["contract_sha256"],
        "scope_sha256": canonical_sha256(decision_input["scope"]),
        "verification_receipt_sha256": verification["receipt_sha256"],
        "accepted_residual_issue_ids": issue_ids,
        "accepted_residual_issues_sha256": canonical_sha256(
            accepted_residual_issues
        ),
    }


def _receipt_valid(
    receipt: Any,
    *,
    decision_input: dict[str, Any],
    accepted_issue_ids: list[str],
) -> bool:
    expected_keys = {
        "schema_version",
        "decision_id",
        "session_id",
        "action",
        "status",
        "binding",
        "binding_sha256",
    }
    if not isinstance(receipt, dict) or set(receipt) != expected_keys:
        return False
    if (
        receipt["schema_version"] != RECEIPT_SCHEMA_VERSION
        or receipt["action"] != "closure_accept"
        or receipt["status"] != "consumed"
        or receipt["decision_id"] != decision_input["acceptance_decision_id"]
        or receipt["session_id"] != decision_input["work_unit"]["session_id"]
    ):
        return False
    binding = acceptance_binding(
        decision_input,
        accepted_residual_issue_ids=accepted_issue_ids,
    )
    return receipt["binding"] == binding and receipt["binding_sha256"] == canonical_sha256(binding)


def decision_sha256(decision: dict[str, Any]) -> str:
    return canonical_sha256(
        {key: value for key, value in decision.items() if key != "decision_sha256"}
    )


def _result(
    decision_input: dict[str, Any],
    *,
    state: str,
    terminal: bool,
    usable: bool,
    reason: str,
    remaining: list[str],
    residual: list[str],
) -> dict[str, Any]:
    result = {
        "schema_version": OUTPUT_SCHEMA_VERSION,
        "closure_state": state,
        "terminal": terminal,
        "usable_now": usable,
        "reason_code": reason,
        "remaining_conditions": remaining,
        "residual_issue_ids": residual,
        "evidence_state": "UNMEASURED",
        "contract_sha256": decision_input["contract_sha256"],
        "decision_input_sha256": canonical_sha256(decision_input),
    }
    result["decision_sha256"] = decision_sha256(result)
    return result


def decide_closure(
    contract: Any,
    decision_input: Any,
    *,
    acceptance_receipt: Any = None,
) -> dict[str, Any]:
    try:
        contract = omc_closure.validate_envelope(contract, require_digest=True)
    except omc_closure.ClosureContractError as exc:
        raise ClosureDecisionError(f"closure_contract_invalid:{exc}") from exc
    decision_input = _validate_input(decision_input, contract)
    valid_history, final_issues = _validate_issues(
        decision_input["issues"],
        max_total_rounds=contract["validation"]["max_total_rounds"],
        max_revisions_per_issue=contract["validation"]["max_revisions_per_issue"],
    )
    if not valid_history:
        return _result(
            decision_input,
            state="BLOCKED",
            terminal=True,
            usable=False,
            reason="issue_history_invalid",
            remaining=["valid_issue_history"],
            residual=[],
        )
    highest_reported_round = max(
        (issue["validation_round"] for issue in decision_input["issues"]),
        default=0,
    )
    if decision_input["validation"]["rounds_consumed"] < highest_reported_round:
        raise ClosureDecisionError("validation_rounds_underreported")

    residual = [item["issue_id"] for item in final_issues if item["status"] != "resolved"]
    accepted_issue_ids = sorted(
        item["issue_id"] for item in final_issues if item["status"] == "accepted"
    )
    if acceptance_receipt is not None and not isinstance(
        acceptance_receipt, ConsumedClosureAcceptance
    ):
        return _result(
            decision_input,
            state="BLOCKED",
            terminal=True,
            usable=False,
            reason="acceptance_receipt_invalid",
            remaining=["valid_user_acceptance"],
            residual=residual,
        )
    receipt = acceptance_receipt.receipt if acceptance_receipt is not None else None
    if receipt is not None and not _receipt_valid(
        receipt,
        decision_input=decision_input,
        accepted_issue_ids=accepted_issue_ids,
    ):
        return _result(
            decision_input,
            state="BLOCKED",
            terminal=True,
            usable=False,
            reason="acceptance_receipt_invalid",
            remaining=["valid_user_acceptance"],
            residual=residual,
        )
    severe = [
        item["issue_id"]
        for item in final_issues
        if item["status"] != "resolved" and item["severity"] in {"critical", "major"}
    ]
    if severe:
        return _result(
            decision_input,
            state="BLOCKED",
            terminal=True,
            usable=False,
            reason="severe_issue_open",
            remaining=["resolve_severe_issues"],
            residual=severe,
        )
    if (
        decision_input["scope"]["change_required"]
        and not decision_input["scope"]["change_approved"]
    ):
        return _result(
            decision_input,
            state="BLOCKED",
            terminal=True,
            usable=False,
            reason="scope_change_unapproved",
            remaining=["scope_change_approval"],
            residual=residual,
        )
    verification = decision_input["verification"]["status"]
    if verification == "failed":
        return _result(
            decision_input,
            state="BLOCKED",
            terminal=True,
            usable=False,
            reason="verification_failed",
            remaining=["verification_pass"],
            residual=residual,
        )
    exhausted_issue_ids = sorted(
        item["issue_id"]
        for item in final_issues
        if item["status"] == "open"
        and item["revision"] >= contract["validation"]["max_revisions_per_issue"]
    )
    if exhausted_issue_ids:
        return _result(
            decision_input,
            state="INCONCLUSIVE",
            terminal=True,
            usable=False,
            reason="issue_revision_budget_exhausted",
            remaining=[],
            residual=exhausted_issue_ids,
        )
    if verification == "pending":
        exhausted = (
            decision_input["validation"]["rounds_consumed"]
            >= contract["validation"]["max_total_rounds"]
        )
        return _result(
            decision_input,
            state="INCONCLUSIVE" if exhausted else "IN_PROGRESS",
            terminal=exhausted,
            usable=False,
            reason="validation_budget_exhausted" if exhausted else "verification_pending",
            remaining=[] if exhausted else ["verification"],
            residual=residual,
        )

    open_minor_ids = sorted(
        item["issue_id"] for item in final_issues if item["status"] == "open"
    )
    if open_minor_ids or receipt is None:
        return _result(
            decision_input,
            state="AWAITING_ACCEPTANCE",
            terminal=False,
            usable=False,
            reason="acceptance_required",
            remaining=["user_acceptance"],
            residual=residual,
        )
    state = "DONE_WITH_RISK" if accepted_issue_ids else "DONE"
    return _result(
        decision_input,
        state=state,
        terminal=True,
        usable=True,
        reason="accepted_with_residual_risk" if accepted_issue_ids else "closure_complete",
        remaining=[],
        residual=accepted_issue_ids,
    )
